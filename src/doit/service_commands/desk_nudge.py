"""`doit desk-nudge`: the watch that counts present time and fires the nudge, and the commands that answer, pause and report it.

Every rule is `doit.desk_nudge`'s. This module holds the effects around them: the
state file and its lock, the record file, the service's reads and writes, and the
programs that the watch starts. Each request and program runs outside the state
lock, because inside it one would hold every other writer as long.
"""

import contextlib
import datetime as dt
import fcntl
import json
import logging
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from collections.abc import Iterator
from dataclasses import dataclass
from dataclasses import field
from enum import StrEnum
from pathlib import Path
from typing import IO
from typing import Annotated
from typing import Any

import typer
from pyclilogin import LoginError
from rich.text import Text

from doit import __version__
from doit import desk_nudge
from doit import paths
from doit import service
from doit import tool_version
from doit.desk_nudge import ANSWER_LABELS
from doit.desk_nudge import Answer
from doit.desk_nudge import Event
from doit.desk_nudge import NothingToAnswer
from doit.desk_nudge import Phase
from doit.desk_nudge import SuggestionError
from doit.journal import new_id
from doit.render import console
from doit.render import error_console
from doit.render import examples

log = logging.getLogger('doit.desk_nudge')

# How long `claude-sessions`, `hyprctl`, `gdbus` and a passing `notify-send` may each take.
PROGRAM_SECONDS = 5.0
# Outlasts the service's 10-second read of an app plus its read of the day's context.
NEXT_BUDGET_SECONDS = 30.0
INTERVAL_BUDGET_SECONDS = 10.0
ANSWER_BUDGET_SECONDS = 10.0
# Another desk's answers move the interval too, so the watch reads it again this often.
INTERVAL_REFRESH_SECONDS = 300.0
PROC = Path('/proc')
NO_ANSWER_EXIT = 4

NOTIFICATIONS = ['--dest', 'org.freedesktop.Notifications', '--object-path', '/org/freedesktop/Notifications']
BUS = ['--dest', 'org.freedesktop.DBus', '--object-path', '/org/freedesktop/DBus']

app = typer.Typer(
    name='desk-nudge',
    no_args_is_help=True,
    help=(
        'A nudge when a long stretch at the desk runs past the interval, suggesting what `doit next` offers first.\n\n'
        'The watch counts the time you are present and fires a notification at the interval. Your answers to it tune the '
        'interval, and every firing, answer and absence is recorded for the warehouse.'
    ),
    epilog=examples(
        ('doit desk-nudge status', 'where the clock stands, and what the nudge suggests'),
        ('doit desk-nudge answer too_soon', 'answer the nudge that is showing'),
        ('doit desk-nudge pause', 'hold the clock until resumed'),
    ),
)


def now() -> dt.datetime:
    """The desk's clock, which the scenarios replace."""
    return dt.datetime.now().astimezone()


# --- the state file -----------------------------------------------------------


def read_state() -> dict:
    """The state file, or a fresh desk's state where there is none. A file that is not JSON starts a fresh clock."""
    path = paths.desk_nudge_state_file()
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return desk_nudge.fresh_state()
    try:
        return desk_nudge.loaded_state(json.loads(text))
    except ValueError as error:
        log.error(f'{path} is not JSON ({error}), so the desk nudge starts from a fresh clock')
        return desk_nudge.fresh_state()


def write_state(state: dict) -> None:
    """Replace the state file whole, through a temporary file beside it, so no reader sees half of one."""
    path = paths.desk_nudge_state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix='.desk-nudge-', suffix='.json')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as file:
            json.dump(state, file, indent=2, ensure_ascii=False)
            file.write('\n')
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


@contextlib.contextmanager
def changed_state() -> Iterator[dict]:
    """The state, read under `fcntl.lockf` and written back atomically with the pill redrawn.

    The watch, `record-presence`, `answer`, `pause` and `resume` each change the
    file this way. Hold it around the read, the change and the write alone. A
    block that raises writes nothing.
    """
    lock_path = paths.desk_nudge_state_lock_file()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        fcntl.lockf(lock, fcntl.LOCK_EX)
        state = read_state()
        yield state
        state['pill'].update(desk_nudge.pill_face(state))
        write_state(state)


def append_records(records: list[dict]) -> None:
    """Append each `doit.desk_nudge` line. A failed append logs at error, and whatever wrote it carries on."""
    if not records:
        return
    path = paths.desk_nudge_records_file()
    lines = ''.join(json.dumps(line, ensure_ascii=False, separators=(',', ':')) + '\n' for line in records)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as file:
            file.write(lines)
    except OSError as error:
        log.error(f'could not append {len(records)} desk nudge record(s) to {path}: {error}')


def carry_out(change: desk_nudge.Change) -> None:
    """Write a change's records, and close the notification of the nudge it ended."""
    append_records(change.records)
    if change.closes and change.closes.get('notification_id'):
        close_notification(change.closes['notification_id'])


# --- the programs that the watch starts ----------------------------------------


class ProgramFailed(Exception):
    """A program was missing, timed out or exited non-zero."""


def run_program(argv: list[str], seconds: float = PROGRAM_SECONDS) -> str:
    """A program's stdout, or ProgramFailed naming what went wrong."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=seconds, check=False, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise ProgramFailed(f'{argv[0]} is not installed') from None
    except subprocess.TimeoutExpired:
        raise ProgramFailed(f'{argv[0]} gave no answer within {seconds:g} s') from None
    except OSError as error:
        raise ProgramFailed(f'{argv[0]} could not start: {error}') from None
    if done.returncode != 0:
        said = (done.stderr.strip().splitlines() or [''])[-1]
        raise ProgramFailed(f'{shlex.join(argv)} exited {done.returncode}' + (f': {said}' if said else ''))
    return done.stdout


def claude_sessions() -> list | dict:
    """Every row of `claude-sessions --json`, whole, or why it could not be read."""
    try:
        rows = json.loads(run_program(['claude-sessions', '--json']))
    except (ProgramFailed, ValueError) as error:
        return {'error': str(error)}
    return rows if isinstance(rows, list) else {'error': 'claude-sessions --json printed no list'}


def focused_window() -> dict:
    """The focused window's class and title, read through `hyprctl -i` from the instance `hyprctl instances` lists.

    A unit started at login never has HYPRLAND_INSTANCE_SIGNATURE, so the instance
    is always named.
    """
    try:
        instance = desk_nudge.chosen_instance(json.loads(run_program(['hyprctl', 'instances', '-j'])), os.environ.get('WAYLAND_DISPLAY'))
        if instance is None:
            return {'error': 'hyprctl instances -j lists no Hyprland instance'}
        return desk_nudge.focused_window_from(json.loads(run_program(['hyprctl', '-i', instance, 'activewindow', '-j'])))
    except (ProgramFailed, ValueError) as error:
        return {'error': str(error)}


def snapshot() -> dict:
    """What the desk showed at a moment: every Claude session, and the focused window."""
    return {'sessions': claude_sessions(), 'focused_window': focused_window()}


def close_notification(notification_id: int) -> None:
    """Close one notification by its id. A failure logs at warning, and the notification may stay up."""
    method = ['--method', 'org.freedesktop.Notifications.CloseNotification', str(notification_id)]
    try:
        run_program(['gdbus', 'call', '--session', *NOTIFICATIONS, *method])
    except ProgramFailed as error:
        log.warning(f'could not close notification {notification_id}, so it may stay up: {error}')


def notification_bus_owner() -> str | None:
    """Which connection owns org.freedesktop.Notifications. It changes when dunst restarts."""
    method = ['--method', 'org.freedesktop.DBus.GetNameOwner', 'org.freedesktop.Notifications']
    try:
        return desk_nudge.bus_owner_from(run_program(['gdbus', 'call', '--session', *BUS, *method]))
    except ProgramFailed as error:
        log.warning(f'could not read who owns org.freedesktop.Notifications: {error}')
        return None


def notify(summary: str, body: str) -> None:
    """A passing notification, for what the person should know now."""
    try:
        run_program(['notify-send', '-a', 'doit', summary, body])
    except ProgramFailed as error:
        log.warning(f'could not notify "{summary}": {error}')


def hyprland_socket_exists() -> bool:
    """Whether a Hyprland instance's socket is under $XDG_RUNTIME_DIR/hypr/, read with no program."""
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    return runtime is not None and any((Path(runtime) / 'hypr').glob('*/.socket.sock'))


def process_name(pid: str) -> str:
    try:
        return (PROC / pid / 'comm').read_text(encoding='utf-8').strip()
    except OSError:
        return ''


# --- the service ----------------------------------------------------------------


def drawn_suggestion() -> dict:
    """`/next`'s first offered pursuit, or why there is none. Reading it counts as an offer from this desk."""
    try:
        selection = service.client().get('/next', machine=paths.machine_name(), budget_seconds=NEXT_BUDGET_SECONDS)
    except LoginError as refused:
        return desk_nudge.failed_suggestion(SuggestionError.LOGIN, str(refused))
    except service.NoAnswer as silent:
        return desk_nudge.failed_suggestion(SuggestionError.UNREACHABLE, str(silent))
    except service.ServiceFailed as failed:
        if failed.status in (404, 409):
            return desk_nudge.failed_suggestion(SuggestionError.NOTHING_TO_DRAW, str(failed))
        if failed.status in (None, 401):
            return desk_nudge.failed_suggestion(SuggestionError.LOGIN, str(failed))
        return desk_nudge.failed_suggestion(SuggestionError.UNREACHABLE, str(failed))
    return desk_nudge.suggestion_from(selection)


def refresh_interval() -> None:
    """The interval and the answer counts as the service computes them now. Where it gives none, the last ones stand."""
    try:
        answered = service.client().get('/desk-nudge', budget_seconds=INTERVAL_BUDGET_SECONDS)
    except (service.ServiceFailed, LoginError) as error:
        log.warning(f'kept the interval this desk last read, because the service gave none: {error}')
        return
    with changed_state() as state:
        desk_nudge.took_interval(state, answered)


def send_answer(row: dict) -> dict:
    """Store one answer. Repeatable, because the service stores a `(nudge_id, answer)` once and answers the first row again."""
    return service.client().post('/desk-nudge/answers', row, repeatable=True, budget_seconds=ANSWER_BUDGET_SECONDS)


def resending_can_store(error: Exception) -> bool:
    """Whether the same answer sent later could be stored: no answer came, the login failed, or the service broke.

    A refusal of the answer itself would refuse it again on every tick.
    """
    if isinstance(error, LoginError | service.NoAnswer):
        return True
    status = getattr(error, 'status', None)
    return status is None or status == 401 or status >= 500


@dataclass(frozen=True)
class Answered:
    """What became of one answer: taken on the desk, then stored by the service or not."""

    taken: desk_nudge.Taken
    saved: bool
    queued: bool = False
    failure: Exception | None = None


def answer_nudge(answer: Answer, nudge_id: str | None = None, *, from_notification: bool = False) -> Answered:
    """Take one answer, end the nudge it answers, and store it, queueing it where the service does not.

    The one path for every answer: the notification's action, the panel's rows and
    `doit desk-nudge answer`. With no id it answers the ready nudge. Raises
    NothingToAnswer where none is ready and the id names none that this desk fired.
    """
    with changed_state() as state:
        taken = desk_nudge.take_answer(state, now(), answer, nudge_id, paths.machine_name())
    if taken.closes and taken.closes.get('notification_id') and not from_notification:
        close_notification(taken.closes['notification_id'])
    seen = snapshot()
    try:
        answered = send_answer(taken.row)
    except (service.ServiceFailed, LoginError) as error:
        queued = resending_can_store(error)
        if queued:
            with changed_state() as state:
                desk_nudge.queue_answer(state, taken)
        append_records([desk_nudge.answered_record(now(), taken.row, taken.late, saved=False, snapshot=seen)])
        label = ANSWER_LABELS[answer]
        if queued:
            notify('doit: answer not saved yet', f'"{label}" is kept on this desk, and the watch sends it again.')
        else:
            notify('doit: answer refused', f'"{label}": {error}')
        return Answered(taken, saved=False, queued=queued, failure=error)
    with changed_state() as state:
        desk_nudge.took_interval(state, answered)
    append_records([desk_nudge.answered_record(now(), taken.row, taken.late, saved=True, snapshot=seen)])
    return Answered(taken, saved=True)


def resend_queued() -> None:
    """Send each queued answer again. The send that stores one writes `answered` with `saved: true`."""
    with changed_state() as state:
        queued = list(state['queued_answers'])
    for entry in queued:
        try:
            answered = send_answer(entry['row'])
        except (service.ServiceFailed, LoginError) as error:
            log.warning(f'{len(queued)} desk nudge answer(s) still not saved: {error}')
            return
        with changed_state() as state:
            dropped = desk_nudge.unqueue_answer(state, entry['row'])
            desk_nudge.took_interval(state, answered)
        if dropped:
            append_records([desk_nudge.answered_record(now(), entry['row'], entry['late'], saved=True)])


# --- the watch --------------------------------------------------------------------


@dataclass
class Shown:
    """One `notify-send` child and the nudge it shows."""

    process: subprocess.Popen
    nudge_id: str
    # Set where a later showing replaced this one, whose exit is then no dismissal.
    replaced: threading.Event = field(default_factory=threading.Event)


def alive(thread: threading.Thread | None) -> bool:
    return thread is not None and thread.is_alive()


class Watch:
    """A tick every 15 seconds, and a thread for everything that waits on a request or a program."""

    def __init__(self) -> None:
        self.version = __version__
        self.firing: threading.Thread | None = None
        self.reading: threading.Thread | None = None
        self.resending: threading.Thread | None = None
        self.refreshing: threading.Thread | None = None
        self.interval_read_at: float | None = None
        self.shown: Shown | None = None
        self.hypridle_pid: str | None = None
        self.showing = threading.Lock()

    def background(self, target: Callable[..., None], *args: Any) -> threading.Thread:
        thread = threading.Thread(target=target, args=args, daemon=True)
        thread.start()
        return thread

    def start(self) -> None:
        """Write `started`, then finish a nudge that a stopped watch left firing, or show a ready one again."""
        moment = now()
        with changed_state() as state:
            # A restarted watch keeps the last tick, so its first tick counts the time it was stopped as absence.
            state['last_tick'] = state['last_tick'] or desk_nudge.stamp(moment)
            nudge = dict(state['nudge']) if state['nudge'] else None
            phase = Phase(state['phase'])
            started = desk_nudge.record(
                Event.STARTED,
                moment,
                doit_version=self.version,
                phase=phase.value,
                present_minutes=round(state['present_seconds'] / 60, 2),
                interval_minutes=state['interval_minutes'],
            )
        append_records([started])
        if nudge and phase is Phase.FIRING:
            self.firing = self.background(self.fire, nudge)
        elif nudge and phase is Phase.READY:
            self.show(nudge, replaces=nudge['notification_id'])
        self.refresh_interval_when_due()

    def tick(self) -> None:
        """Count, expire, and start whatever the tick calls for off the loop."""
        moment = now()
        live = self.live_desk()
        with changed_state() as state:
            change = desk_nudge.tick(state, moment, live)
            firing = desk_nudge.begin_firing(state, moment, new_id(moment)) if change.fire else None
            ready = dict(state['nudge']) if state['phase'] == Phase.READY else None
            queued = bool(state['queued_answers'])
        carry_out(change)
        if firing:
            self.firing = self.background(self.fire, firing)
        if ready and ready['bus_owner']:
            owner = notification_bus_owner()
            if owner and owner != ready['bus_owner']:
                log.info(f'the notification server is now {owner}, so the nudge is shown again')
                self.show(ready, replaces=ready['notification_id'])
        if queued and not alive(self.resending):
            self.resending = self.background(resend_queued)
        self.refresh_interval_when_due()

    def refresh_interval_when_due(self) -> None:
        due = self.interval_read_at is None or time.monotonic() - self.interval_read_at >= INTERVAL_REFRESH_SECONDS
        if due and not alive(self.refreshing):
            self.interval_read_at = time.monotonic()
            self.refreshing = self.background(refresh_interval)

    def fire(self, nudge: dict) -> None:
        """Steps two to six: read `/next`, take the snapshot, write `fired`, make it ready, and show it."""
        suggestion = drawn_suggestion()
        seen = snapshot()
        fired = desk_nudge.record(
            Event.FIRED,
            desk_nudge.instant(nudge['fired_at']),
            nudge_id=nudge['nudge_id'],
            interval_minutes=nudge['interval_minutes'],
            present_minutes=nudge['present_minutes'],
            suggestion=suggestion,
            **seen,
        )
        append_records([fired])
        with changed_state() as state:
            ready = desk_nudge.make_ready(state, now(), nudge['nudge_id'], suggestion)
        if ready:
            self.show(ready)

    def show(self, nudge: dict, replaces: int | None = None) -> None:
        """Start `notify-send` as a child that the tick loop never waits on, with a thread that reads its id and its action."""
        argv = desk_nudge.notification_argv(nudge, replaces)
        with self.showing:
            try:
                process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True)
            except OSError as error:
                log.warning(f'could not show the nudge, so the pill and the panel carry it alone: {error}')
                return
            earlier, self.shown = self.shown, Shown(process, nudge['nudge_id'])
            if earlier and earlier.process.poll() is None:
                earlier.replaced.set()
                earlier.process.terminate()
            self.reading = self.background(self.read_notification, self.shown)

    def read_notification(self, shown: Shown) -> None:
        """Keep the notification's id, then take its action: an answer, or `dismissed` where it closed with none."""
        assert shown.process.stdout is not None
        first = shown.process.stdout.readline().strip()
        if not first.isdigit():
            shown.process.wait()
            if not shown.replaced.is_set():
                log.warning(
                    f'notify-send printed no id and exited {shown.process.returncode}, so the pill and the panel carry the nudge alone'
                )
            return
        owner = notification_bus_owner()
        with changed_state() as state:
            desk_nudge.attach_notification(state, shown.nudge_id, int(first), owner)
        action = shown.process.stdout.readline().strip()
        shown.process.wait()
        if shown.replaced.is_set():
            return
        if action in set(Answer):
            try:
                answer_nudge(Answer(action), shown.nudge_id, from_notification=True)
            except NothingToAnswer as nothing:
                log.warning(f'the notification answered {action}, and {nothing}')
            return
        if owner and notification_bus_owner() != owner:
            return
        with changed_state() as state:
            records = desk_nudge.dismissal(state, now(), shown.nudge_id)
        append_records(records)

    def live_desk(self) -> bool:
        """A Hyprland socket and a running hypridle, both read with no program."""
        return hyprland_socket_exists() and self.hypridle_running()

    def hypridle_running(self) -> bool:
        """Whether hypridle runs, checking the pid found last before reading every process's name."""
        if self.hypridle_pid and process_name(self.hypridle_pid) == 'hypridle':
            return True
        try:
            pids = [entry.name for entry in PROC.iterdir() if entry.name.isdigit()]
        except OSError:
            pids = []
        self.hypridle_pid = next((pid for pid in pids if process_name(pid) == 'hypridle'), None)
        return self.hypridle_pid is not None

    def run(self) -> int:
        """Tick until the installed doit is another release, then exit so that the unit starts it."""
        self.start()
        while True:
            began = time.monotonic()
            self.tick()
            installed = tool_version()
            if installed != self.version:
                log.info(f'doit {installed} is installed and this watch runs {self.version}, so it exits for the unit to start the new one')
                return 0
            time.sleep(max(desk_nudge.TICK_SECONDS - (time.monotonic() - began), 0.0))

    def close(self) -> None:
        """Stop the notification child and wait on the threads, for a watch that ends inside a process that goes on."""
        if self.shown and self.shown.process.poll() is None:
            self.shown.replaced.set()
            self.shown.process.terminate()
        for thread in (self.firing, self.reading, self.resending, self.refreshing):
            if thread is not None:
                thread.join(timeout=10)


def hold_watch_lock() -> IO | None:
    """The watch lock, held until the returned file closes, or None where another watch holds it."""
    path = paths.desk_nudge_watch_lock_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a')
    try:
        fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


# journald reads a `<N>` priority at the start of each line that a unit writes to stderr.
SYSLOG_PRIORITY = {logging.DEBUG: 7, logging.INFO: 6, logging.WARNING: 4, logging.ERROR: 3, logging.CRITICAL: 2}


class JournalPriority(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return f'<{SYSLOG_PRIORITY.get(record.levelno, 6)}>{super().format(record)}'


def to_journal() -> bool:
    """Whether stderr is the journal stream systemd handed this process, as $JOURNAL_STREAM names it."""
    named = os.environ.get('JOURNAL_STREAM', '')
    try:
        stat = os.fstat(sys.stderr.fileno())
    except (OSError, ValueError):
        return False
    return named == f'{stat.st_dev}:{stat.st_ino}'


def log_to_stderr() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JournalPriority('%(message)s') if to_journal() else logging.Formatter('desk-nudge %(levelname)s: %(message)s'))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False


# --- the commands ---------------------------------------------------------------


@app.command(
    'watch',
    epilog=examples(('doit desk-nudge watch', 'run the clock in this terminal, where the desk has no user service running it')),
)
def watch_command() -> None:
    """Count present time and fire the nudge at the interval, until stopped.

    The desk's user service runs this. It exits 1 where another watch already
    runs on this desk, and 0 once a newer doit is installed, so that the unit
    starts the new release.
    """
    log_to_stderr()
    held = hold_watch_lock()
    if held is None:
        error_console.print('doit desk-nudge: another watch is running on this desk, so this one exits.')
        raise typer.Exit(1)
    with held:
        raise typer.Exit(Watch().run())


class Presence(StrEnum):
    AWAY = 'away'
    BACK = 'back'


@app.command(
    'record-presence',
    epilog=examples(
        ('doit desk-nudge record-presence away 120', 'what the 120-second hypridle listener runs on timeout'),
        ('doit desk-nudge record-presence back 120', 'what it runs on the next input'),
    ),
)
def record_presence_command(
    change: Annotated[Presence, typer.Argument(help='away after the idle timeout, back on the next input.')],
    seconds: Annotated[
        int, typer.Argument(min=1, help="The listener's timeout, which an away counts back from.")
    ] = desk_nudge.IDLE_AFTER_SECONDS,
) -> None:
    """Record that the desk went idle or came back, which pauses, restarts or ends the nudge."""
    moment = now()
    with changed_state() as state:
        if change is Presence.AWAY:
            made = desk_nudge.went_away(state, moment, seconds)
        else:
            made = desk_nudge.came_back(state, moment, seconds)
    carry_out(made)
    raise typer.Exit(0)


@app.command(
    'answer',
    epilog=examples(
        ('doit desk-nudge answer too_soon', 'the ready nudge came too soon, so the interval grows'),
        ('doit desk-nudge answer bothered --nudge <id>', 'answer a nudge that has already ended'),
    ),
)
def answer_command(
    answer: Annotated[Answer, typer.Argument(help='What you say of the nudge.')],
    nudge: Annotated[str | None, typer.Option('--nudge', help='The nudge to answer, where it is not the ready one.')] = None,
) -> None:
    """Answer a nudge, which ends it and restarts the clock.

    Exits 0 once the service stores it. Where the service does not, the answer is
    kept on this desk and the watch sends it again: exit 4 where no answer came,
    and 1 where the login or the service failed. Exits 1 where there is no nudge
    to answer.
    """
    try:
        answered = answer_nudge(answer, nudge)
    except NothingToAnswer as nothing:
        error_console.print(f'doit desk-nudge: {nothing}.')
        raise typer.Exit(1) from None
    label = ANSWER_LABELS[answer]
    late = ' It answers a nudge that had already ended, so the clock is left alone.' if answered.taken.late else ''
    if answered.saved:
        state = read_state()
        console.print(f'"{label}" is saved. The interval is {state["interval_minutes"]:g} minutes.{late}', soft_wrap=True)
        raise typer.Exit(0)
    kept = 'It is kept on this desk, and the watch sends it again.' if answered.queued else 'It was refused, so nothing sends it again.'
    error_console.print(f'doit desk-nudge: "{label}" is not saved: {answered.failure}. {kept}', soft_wrap=True, markup=False)
    raise typer.Exit(NO_ANSWER_EXIT if isinstance(answered.failure, service.NoAnswer) else 1)


@app.command('pause', epilog=examples(('doit desk-nudge pause', 'hold the clock, as a running day block does')))
def pause_command() -> None:
    """Hold the clock where it stands, ending a nudge that is showing, until `resume`."""
    with changed_state() as state:
        change = desk_nudge.pause(state, now())
    carry_out(change)
    if change.records:
        console.print('The desk nudge is paused. [cyan]doit desk-nudge resume[/] carries on the count.')
    else:
        console.print('The desk nudge was already paused.')
    raise typer.Exit(0)


@app.command('resume', epilog=examples(('doit desk-nudge resume', 'carry on the count from where pause held it')))
def resume_command() -> None:
    """Carry on the count from where `pause` held it."""
    with changed_state() as state:
        change = desk_nudge.resume(state, now())
    carry_out(change)
    console.print('The desk nudge carries on the count.' if change.records else 'The desk nudge was not paused.')
    raise typer.Exit(0)


@app.command(
    'status',
    epilog=examples(
        ('doit desk-nudge status', 'where the clock stands, and what the nudge suggests'),
        ('doit desk-nudge status --json', 'the same as data'),
    ),
)
def status_command(
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
) -> None:
    """The phase, the time left, the suggestion, the interval and the answer counts, read from this desk with no request."""
    document = desk_nudge.status_document(read_state(), now())
    if as_json:
        print(json.dumps(document, indent=2, ensure_ascii=False))
        raise typer.Exit(0)
    render_status(document)
    raise typer.Exit(0)


def render_status(document: dict) -> None:
    rows: list[tuple[str, str]] = [('clock', document['pill']['text'])]
    if document['minutes_left'] is not None:
        rows.append(('present', f'{document["present_minutes"]:g} of {document["interval_minutes"]:g} minutes'))
    if document['ready_until']:
        rows.append(('ready until', f'{desk_nudge.instant(document["ready_until"]):%H:%M}'))
    suggestion = document['suggestion']
    if suggestion:
        rows.append(('suggestion', suggestion['message'] if 'error' in suggestion else f'{suggestion["pursuit"]} · {suggestion["label"]}'))
    rows.append(('answers', ' · '.join(f'{ANSWER_LABELS[Answer(name)]} {count}' for name, count in document['answer_counts'].items())))
    if document['queued_answers']:
        rows.append(('not saved', f'{document["queued_answers"]}, which the watch sends again'))
    width = max(len(name) for name, _ in rows)
    for name, value in rows:
        line = Text('  ')
        line.append(f'{name:<{width}}  ', style='dim')
        line.append(value)
        console.print(line, soft_wrap=True)
    if not document['watch_running']:
        console.print(Text('  The watch is not running, so the clock stands still.', style='yellow'), soft_wrap=True)
