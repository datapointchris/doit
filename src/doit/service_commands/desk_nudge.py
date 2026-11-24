"""`doit desk-nudge`: the watch that counts and fires the nudge, the commands that answer, pause and report it, and its panel.

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
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO
from typing import Annotated
from typing import Any
from typing import assert_never
from urllib.parse import quote

import typer
from pyclilogin import LoginError
from rich.text import Text

from doit import __version__
from doit import desk_nudge
from doit import paths
from doit import service
from doit import tool_version
from doit.dashboard import habit_completion_argv
from doit.desk_nudge import ANSWER_LABELS
from doit.desk_nudge import Answer
from doit.desk_nudge import Event
from doit.desk_nudge import NothingToAnswer
from doit.desk_nudge import PanelRowKind
from doit.desk_nudge import PanelStep
from doit.desk_nudge import Phase
from doit.desk_nudge import SuggestionError
from doit.journal import new_id
from doit.lanes import CompletionKind
from doit.render import console
from doit.render import error_console
from doit.render import examples
from doit.service_commands.pursuits import LogRefused
from doit.service_commands.pursuits import Unrecorded
from doit.service_commands.pursuits import log_pursuit
from doit.service_commands.review import mark_done

log = logging.getLogger('doit.desk_nudge')

# How long `claude-sessions`, `hyprctl`, `loginctl`, the chime and a passing `notify-send` may each take.
PROGRAM_SECONDS = 5.0
# The XDG sound theme's event that a firing plays, read by name so the desk's own theme chooses the sound.
CHIME_SOUND = 'message-new-instant'
# Outlasts the service's read of each app plus its read of the day's context, as `/next` makes them.
NEXT_BUDGET_SECONDS = 30.0
# `/today` asks every app that `/next` asks, and `/pursuits` builds the same state.
PANEL_BUDGET_SECONDS = 30.0
INTERVAL_BUDGET_SECONDS = 10.0
ANSWER_BUDGET_SECONDS = 10.0
# How long `life habits complete` may take. It is the one panel program with a budget other than `PROGRAM_SECONDS`.
HABIT_SECONDS = 30.0
# Another desk's answers move the interval too, so the watch reads it again this often.
INTERVAL_REFRESH_SECONDS = 300.0
# While logind holds the session active, rows completed elsewhere leave the panel this often, beside each firing and each pick.
PANEL_REFRESH_SECONDS = 300.0
PICK_REFUSED_EXIT = 2
# `fcntl.lockf` excludes other processes and never this one's threads, so the watch's threads take this first.
STATE_THREAD_LOCK = threading.Lock()

app = typer.Typer(
    name='desk-nudge',
    no_args_is_help=True,
    help=(
        'A nudge every interval at the desk, suggesting what `doit next` offers first.\n\n'
        'The watch counts the time since the last nudge ended, then flashes its pill and chimes at the interval. Your answers to it '
        'tune the interval, and every firing, answer and pick is recorded for the warehouse.'
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


def parsed_state(path: Path) -> dict:
    """The state file's state, or a fresh desk's where there is none. Raises UnreadableState."""
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return desk_nudge.fresh_state()
    try:
        document = json.loads(text)
    except ValueError as error:
        raise desk_nudge.UnreadableState(f'it is not JSON ({error})') from None
    return desk_nudge.loaded_state(document)


def read_state() -> dict:
    """The state, for a reader that changes nothing. A file that cannot be read reads as a fresh desk's."""
    path = paths.desk_nudge_state_file()
    try:
        return parsed_state(path)
    except desk_nudge.UnreadableState as error:
        log.error(f'{path} cannot be read, so it reads as a fresh clock: {error}')
        return desk_nudge.fresh_state()


def set_aside(path: Path) -> Path:
    """Move an unreadable state file beside itself, so the answers it kept stay on disk for a person."""
    aside = path.with_name(f'{path.name}.unreadable-{now():%Y%m%dT%H%M%S}')
    os.replace(path, aside)
    return aside


def replace_file(path: Path, document: dict) -> None:
    """Replace a JSON file whole, through a temporary file beside it, so no reader sees half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix='.desk-nudge-', suffix='.json')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as file:
            json.dump(document, file, indent=2, ensure_ascii=False)
            file.write('\n')
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def write_state(state: dict) -> None:
    replace_file(paths.desk_nudge_state_file(), state)


@contextlib.contextmanager
def changed_state() -> Iterator[dict]:
    """The state, read under `STATE_THREAD_LOCK` and `fcntl.lockf` and written back atomically with the pill redrawn.

    The watch, `answer`, `pause`, `resume` and `panel pick` each
    change the file this way. Hold it around the read, the change and the write alone. A
    block that raises writes nothing. A file that cannot be read is set aside, and
    the change starts from a fresh clock.
    """
    lock_path = paths.desk_nudge_state_lock_file()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with STATE_THREAD_LOCK, lock_path.open('a') as lock:
        fcntl.lockf(lock, fcntl.LOCK_EX)
        path = paths.desk_nudge_state_file()
        try:
            state = parsed_state(path)
        except desk_nudge.UnreadableState as error:
            log.error(f'{path} cannot be read, so it is moved to {set_aside(path)} and the clock starts fresh: {error}')
            state = desk_nudge.fresh_state()
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
    """Write a change's records."""
    append_records(change.records)


# --- the programs that the watch starts ----------------------------------------


class ProgramFailed(Exception):
    """A program was missing, timed out or exited non-zero. One that timed out may have done its work."""

    def __init__(self, message: str, *, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out


def run_program(argv: list[str], seconds: float = PROGRAM_SECONDS) -> str:
    """A program's stdout, or ProgramFailed naming what went wrong."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=seconds, check=False, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise ProgramFailed(f'{argv[0]} is not installed') from None
    except subprocess.TimeoutExpired:
        raise ProgramFailed(f'{argv[0]} gave no answer within {seconds:g} s', timed_out=True) from None
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


def hyprctl(query: str):
    """`hyprctl -i <instance> <query> -j`, parsed, on the instance `hyprctl instances` lists.

    A unit started at login never has HYPRLAND_INSTANCE_SIGNATURE, so the instance
    is always named. Raises ProgramFailed, or ValueError on output that is not JSON.
    """
    instance = desk_nudge.chosen_instance(json.loads(run_program(['hyprctl', 'instances', '-j'])), os.environ.get('WAYLAND_DISPLAY'))
    if instance is None:
        raise ProgramFailed('hyprctl instances -j lists no Hyprland instance')
    return json.loads(run_program(['hyprctl', '-i', instance, query, '-j']))


def focused_window() -> dict:
    """The focused window's class and title, or why it could not be read."""
    try:
        return desk_nudge.focused_window_from(hyprctl('activewindow'))
    except (ProgramFailed, ValueError) as error:
        return {'error': str(error)}


def screens_off() -> bool:
    """Whether every display is off, as hypridle leaves them overnight. A failed read says on."""
    try:
        monitors = hyprctl('monitors')
    except (ProgramFailed, ValueError):
        return False
    return isinstance(monitors, list) and bool(monitors) and not any(m.get('dpmsStatus', True) for m in monitors)


def snapshot() -> dict:
    """What the desk showed at a moment: every Claude session, and the focused window."""
    return {'sessions': claude_sessions(), 'focused_window': focused_window()}


def chime() -> None:
    """Play `CHIME_SOUND` through `canberra-gtk-play`, unless every display is off.

    A failure logs at warning, and the flashing pill still shows the nudge.
    """
    if screens_off():
        log.info('every display is off, so the nudge fires without its chime')
        return
    try:
        run_program(['canberra-gtk-play', '--id', CHIME_SOUND, '--description', 'desk nudge'])
    except ProgramFailed as error:
        log.warning(f'could not play the chime, so only the pill shows the nudge: {error}')


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


def session_idle() -> bool:
    """Whether logind marks the display session idle, which an idle listener such as hypridle's sets. A failed read reads as active."""
    try:
        return run_program(['loginctl', 'show-session', 'auto', '-p', 'IdleHint', '--value']).strip() == 'yes'
    except ProgramFailed as error:
        log.warning(f"could not read logind's idle hint, so the panel's rows are read as for a desk in use: {error}")
        return False


# --- the service ----------------------------------------------------------------


def read_failure(error: Exception) -> SuggestionError:
    """Why a read of the service failed: no answer came, or this desk's login was refused or is missing."""
    if isinstance(error, service.NoAnswer):
        return SuggestionError.UNREACHABLE
    if isinstance(error, LoginError) or getattr(error, 'status', None) in (None, 401):
        return SuggestionError.LOGIN
    return SuggestionError.UNREACHABLE


def drawn_suggestion() -> dict:
    """`/next`'s first offered pursuit, or why there is none. Reading it counts as an offer from this desk."""
    try:
        selection = service.client().get('/next', machine=paths.machine_name(), budget_seconds=NEXT_BUDGET_SECONDS)
    except (service.ServiceFailed, LoginError) as failed:
        if getattr(failed, 'status', None) in (404, 409):
            return desk_nudge.failed_suggestion(SuggestionError.NOTHING_TO_DRAW, str(failed))
        return desk_nudge.failed_suggestion(read_failure(failed), str(failed))
    return desk_nudge.suggestion_from(selection)


# --- the panel's rows -------------------------------------------------------------


def read_panel_cache() -> dict | None:
    """The rows that the watch last wrote, or None where it has written none that this release can read."""
    path = paths.desk_nudge_panel_cache_file()
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        log.warning(f'{path} cannot be read, so the panel has no rows until the watch writes it again: {error}')
        return None
    return document if isinstance(document, dict) and document.get('version') == desk_nudge.PANEL_CACHE_VERSION else None


def listed_pursuits(client: service.Service) -> dict:
    """`/pursuits`, for each pursuit's declaration and usual minutes. An empty register lists none."""
    try:
        return client.get('/pursuits', budget_seconds=PANEL_BUDGET_SECONDS)
    except service.ServiceFailed as failed:
        if failed.status == 404:
            return {'pursuits': {}, 'usual_minutes': {}}
        raise


def pursuit_item(client: service.Service, name: str) -> dict:
    """What a pursuit means now, with its choices. A failed read lists the pursuit as a note, which completes nothing."""
    try:
        return client.get(f'/pursuits/{quote(name, safe="")}/item', budget_seconds=PANEL_BUDGET_SECONDS).get('resolved') or {}
    except service.ServiceFailed as failed:
        return {'error': str(failed)}


def rebuild_panel_cache(suggestion: dict | None = None, *, read_next: bool = False) -> None:
    """Read the panel's rows again from `/today` and `/pursuits`, and write them for `panel show`.

    The suggestion is the one handed in, a fresh `/next` where `read_next` asks,
    or else the one the rows last held while today's lane still lists it. A
    failed read keeps the last rows and names why, so the panel reads as old
    rather than as nothing owed.
    """
    previous = read_panel_cache()
    carried = not read_next and suggestion is None
    if read_next:
        suggestion = drawn_suggestion()
    elif carried:
        suggestion = (previous or {}).get('suggestion')
    read_at = now()
    try:
        client = service.client()
        today = client.get('/today', budget_seconds=PANEL_BUDGET_SECONDS)
        listed = listed_pursuits(client)
        items = {name: pursuit_item(client, name) for name in desk_nudge.choice_pursuits(today, listed.get('pursuits') or {})}
    except (service.ServiceFailed, LoginError) as error:
        log.warning(f'kept the panel rows this desk last read, because the service gave none: {error}')
        cache = desk_nudge.failed_panel_cache(previous, read_failure(error), str(error), suggestion, now())
    else:
        kept = desk_nudge.still_owed(suggestion, today) if carried else suggestion
        cache = desk_nudge.panel_cache(today, listed, items, kept, read_at)
    replace_file(paths.desk_nudge_panel_cache_file(), cache)


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


@dataclass(frozen=True)
class Answered:
    """What became of one answer: taken on the desk, then stored by the service or not."""

    taken: desk_nudge.Taken
    saved: bool
    queued: bool = False
    failure: Exception | None = None


def answer_outcome(answered: Answered) -> tuple[int, str]:
    """The exit code and the sentence for one answer, which `doit desk-nudge answer` and a panel pick both give."""
    label = ANSWER_LABELS[Answer(answered.taken.row['answer'])]
    if answered.saved:
        late = ' It answers a nudge that had already ended, so the clock is left alone.' if answered.taken.late else ''
        return 0, f'"{label}" is saved. The interval is {read_state()["interval_minutes"]:g} minutes.{late}'
    kept = 'It is kept on this desk, and the watch sends it again.' if answered.queued else 'It was refused, so nothing sends it again.'
    return service.exit_code(answered.failure) if answered.failure else 1, f'"{label}" is not saved: {answered.failure}. {kept}'


def answer_nudge(answer: Answer, nudge_id: str | None = None) -> Answered:
    """Take one answer, end the nudge it answers, and store it, keeping it to send again where a later send could store it.

    The one path for every answer to a nudge. With no id it answers the ready
    nudge. Raises NothingToAnswer where none is ready and the id names none that
    this desk fired. An answer already kept on this desk is sent as it was kept,
    because that row is the one the service stores. One `saved: true` line is
    written per answer, by the writer that drops it from the queue or whose send
    the service added.
    """
    with changed_state() as state:
        taken = desk_nudge.take_answer(state, now(), answer, nudge_id, paths.machine_name())
        kept = desk_nudge.queued_entry(state, taken.row)
    if kept:
        taken = desk_nudge.Taken(kept['row'], kept['late'], taken.closes)
    seen = None if kept else snapshot()
    try:
        answered = send_answer(taken.row)
    except (service.ServiceFailed, LoginError) as error:
        queued = service.later_send_can_store(error)
        with changed_state() as state:
            if queued:
                desk_nudge.queue_answer(state, taken)
            ended = not queued and desk_nudge.unqueue_answer(state, taken.row)
        if not kept or ended:
            append_records([desk_nudge.answered_record(now(), taken.row, taken.late, saved=False, queued=queued, snapshot=seen)])
        label = ANSWER_LABELS[answer]
        if queued:
            notify('doit: answer not saved yet', f'"{label}" is kept on this desk, and the watch sends it again.')
        else:
            notify('doit: answer refused', f'"{label}": {error}')
        return Answered(taken, saved=False, queued=queued, failure=error)
    with changed_state() as state:
        ended = desk_nudge.unqueue_answer(state, taken.row)
        desk_nudge.took_interval(state, answered)
    # An answer that the service already held has its line from whoever stored it.
    if ended or (not kept and answered.get('added', True)):
        append_records([desk_nudge.answered_record(now(), taken.row, taken.late, saved=True, queued=False, snapshot=seen)])
    return Answered(taken, saved=True)


def resend_queued() -> None:
    """Send each queued answer again, in order, until a send fails in a way that a later send could outlast.

    The send that stores one writes `answered` with `saved: true`. One that the
    service refuses is dropped and written with `saved: false`, so it holds up
    none of the answers behind it.
    """
    with changed_state() as state:
        queued = list(state['queued_answers'])
    for entry in queued:
        try:
            answered = send_answer(entry['row'])
        except (service.ServiceFailed, LoginError) as error:
            if service.later_send_can_store(error):
                log.warning(f'{len(queued)} desk nudge answer(s) still not saved: {error}')
                return
            with changed_state() as state:
                dropped = desk_nudge.unqueue_answer(state, entry['row'])
            if dropped:
                log.error(f'the service refused a kept {entry["row"]["answer"]} answer, so it is dropped: {error}')
                append_records([desk_nudge.answered_record(now(), entry['row'], entry['late'], saved=False, queued=False)])
            continue
        with changed_state() as state:
            dropped = desk_nudge.unqueue_answer(state, entry['row'])
            desk_nudge.took_interval(state, answered)
        if dropped:
            append_records([desk_nudge.answered_record(now(), entry['row'], entry['late'], saved=True, queued=False)])


# --- the watch --------------------------------------------------------------------


def alive(thread: threading.Thread | None) -> bool:
    return thread is not None and thread.is_alive()


class Watch:
    """A tick every `TICK_SECONDS`, and a thread for everything that waits on a request or a program."""

    def __init__(self) -> None:
        self.version = __version__
        self.firing: threading.Thread | None = None
        self.resending: threading.Thread | None = None
        self.refreshing: threading.Thread | None = None
        self.rebuilding: threading.Thread | None = None
        self.interval_read_at: float | None = None
        self.panel_read_at: float | None = None
        # One rebuild of the panel's rows at a time, so a slower read never writes over a newer one.
        self.panel_lock = threading.Lock()

    def background(self, target: Callable[..., None], *args: Any) -> threading.Thread:
        thread = threading.Thread(target=target, args=args, daemon=True)
        thread.start()
        return thread

    def start(self) -> None:
        """Write `started`, then finish a nudge that a stopped watch left firing. A ready one stays on the pill, unchimed."""
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
                counted_minutes=round(state['counted_seconds'] / 60, 2),
                interval_minutes=state['interval_minutes'],
            )
        append_records([started])
        if nudge and phase is Phase.FIRING:
            self.firing = self.background(self.fire, nudge)
        self.refresh_interval_when_due()
        self.rebuild_panel_when_due(picked=False)

    def tick(self) -> None:
        """Count, expire, and start whatever the tick calls for off the loop."""
        moment = now()
        live = hyprland_socket_exists()
        with changed_state() as state:
            change = desk_nudge.tick(state, moment, live)
            firing = desk_nudge.begin_firing(state, moment, new_id(moment)) if change.fire else None
            queued = bool(state['queued_answers'])
            picked = not alive(self.rebuilding) and desk_nudge.take_panel_rebuild(state)
        carry_out(change)
        if firing:
            self.firing = self.background(self.fire, firing)
        if queued and not alive(self.resending):
            self.resending = self.background(resend_queued)
        self.refresh_interval_when_due()
        self.rebuild_panel_when_due(picked)

    def refresh_interval_when_due(self) -> None:
        due = self.interval_read_at is None or time.monotonic() - self.interval_read_at >= INTERVAL_REFRESH_SECONDS
        if due and not alive(self.refreshing):
            self.interval_read_at = time.monotonic()
            self.refreshing = self.background(refresh_interval)

    def rebuild_panel_when_due(self, picked: bool) -> None:
        """Read the panel's rows again after a pick, with `/next`, which leaves out what was logged, and every `PANEL_REFRESH_SECONDS`."""
        due = self.panel_read_at is None or time.monotonic() - self.panel_read_at >= PANEL_REFRESH_SECONDS
        if (picked or due) and not alive(self.rebuilding):
            self.panel_read_at = time.monotonic()
            self.rebuilding = self.background(self.rebuild_panel, None, picked, not picked)

    def rebuild_panel(self, suggestion: dict | None, read_next: bool, unless_idle: bool = False) -> None:
        """A failure that the read does not name keeps the last rows, because a stopped thread would leave them old with no word.

        A timed read waits while logind marks the session idle, because `/today`
        asks every app. It is due again at the next tick, so the first tick back
        at the desk reads the rows.
        """
        if unless_idle and session_idle():
            self.panel_read_at = None
            return
        with self.panel_lock:
            try:
                rebuild_panel_cache(suggestion, read_next=read_next)
            except Exception:
                log.exception("could not read the panel's rows again, so the panel keeps the last ones")

    def fire(self, nudge: dict) -> None:
        """Read `/next`, take the snapshot, write `fired`, make the nudge ready, chime, and read the panel's rows under its suggestion.

        Ready is the pill's class that flashes, so the chime and the flash begin together.

        A failure that the read does not name still makes the nudge ready, because a
        nudge left firing holds the clock until the watch restarts.
        """
        try:
            suggestion = drawn_suggestion()
        except Exception as error:
            log.exception('could not read what /next drew, so the nudge suggests a break')
            suggestion = desk_nudge.failed_suggestion(SuggestionError.UNREADABLE, f'{type(error).__name__}: {error}')
        seen = snapshot()
        fired = desk_nudge.record(
            Event.FIRED,
            desk_nudge.instant(nudge['fired_at']),
            nudge_id=nudge['nudge_id'],
            interval_minutes=nudge['interval_minutes'],
            counted_minutes=nudge['counted_minutes'],
            suggestion=suggestion,
            **seen,
        )
        append_records([fired])
        with changed_state() as state:
            ready = desk_nudge.make_ready(state, now(), nudge['nudge_id'], suggestion)
        if ready:
            chime()
        self.panel_read_at = time.monotonic()
        self.rebuild_panel(suggestion, read_next=False)

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
        """Wait on the threads, for a watch that ends inside a process that goes on."""
        for thread in (self.firing, self.resending, self.refreshing, self.rebuilding):
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
    """Count the time since the last nudge ended and fire the nudge at the interval, until stopped.

    The desk's user service runs this. It exits 1 where another watch already
    runs on this desk, and 0 once another doit release is installed, so that the
    unit starts it.
    """
    log_to_stderr()
    held = hold_watch_lock()
    if held is None:
        error_console.print('doit desk-nudge: another watch is running on this desk, so this one exits.')
        raise typer.Exit(1)
    with held:
        raise typer.Exit(Watch().run())


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

    Exits 0 once the service stores the answer. Exits 4 where no answer came, and
    1 where the login or the service failed: the answer is then kept on this desk,
    and the watch sends it again. Exits 1 where the service refuses the answer
    itself, which keeps nothing. Exits 2 where there is no nudge to answer.
    """
    try:
        answered = answer_nudge(answer, nudge)
    except NothingToAnswer as nothing:
        raise typer.BadParameter(f'{nothing}.', param_hint='--nudge') from None
    code, said = answer_outcome(answered)
    if code:
        error_console.print(f'doit desk-nudge: {said}', soft_wrap=True, markup=False)
    else:
        console.print(said, soft_wrap=True, markup=False)
    raise typer.Exit(code)


@app.command('pause', epilog=examples(('doit desk-nudge pause', 'hold the clock until resume')))
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
        rows.append(('counted', f'{document["counted_minutes"]:g} of {document["interval_minutes"]:g} minutes'))
    if document['ready_until']:
        rows.append(('ready until', f'{desk_nudge.instant(document["ready_until"]):%H:%M}'))
    suggestion = document['suggestion']
    if suggestion:
        rows.append(('suggestion', suggestion['message'] if 'error' in suggestion else f'{suggestion["pursuit"]} · {suggestion["label"]}'))
    rows.append(('answers', ' · '.join(f'{ANSWER_LABELS[Answer(name)]} {count}' for name, count in document['answer_counts'].items())))
    if document['queued_answer_count']:
        rows.append(('not saved', f'{document["queued_answer_count"]}, which the watch sends again'))
    width = max(len(name) for name, _ in rows)
    for name, value in rows:
        line = Text('  ')
        line.append(f'{name:<{width}}  ', style='dim')
        line.append(value)
        console.print(line, soft_wrap=True)
    if not document['watch_running']:
        console.print(Text('  The watch is not running, so the clock stands still.', style='yellow'), soft_wrap=True)


# --- the panel ----------------------------------------------------------------------


class PickRefused(Exception):
    """A key that the panel will not act on, such as one naming no row now or a row picked already. Nothing ran."""


@dataclass(frozen=True)
class Completion:
    """How one completion through its owner ended, as its notification says it."""

    ok: bool
    summary: str
    body: str
    exit_code: int = 0
    # Where the owner is known to have recorded nothing, so the row can be picked again at once.
    recorded_nothing: bool = False


class CompletionFailed(Exception):
    """A completion that did not go through. Its notification is already shown, and its `picked` line written."""

    def __init__(self, completion: Completion) -> None:
        super().__init__(completion.body)
        self.completion = completion


def listed_row(cache: dict | None, state: dict, key: str) -> dict:
    """The completion row that `key` names among the panel's rows now. Raises PickRefused where none does, or where it was picked."""
    rows = desk_nudge.panel_today(cache, state)
    row = next((row for row in rows if row['key'] == key and row['kind'] in set(CompletionKind)), None)
    if row is None:
        raise PickRefused(f'the panel lists no row {key} now')
    if key in desk_nudge.picked_keys(state, cache):
        raise PickRefused(f'{row["text"]} is picked already, and is taken again once the watch reads the rows again')
    if refusal := completion_refusal(row):
        raise PickRefused(refusal)
    return row


def pursuit_facts(cache: dict | None, row: dict) -> dict:
    """What the rows know of a pursuit row's pursuit, and nothing for any other row."""
    if row['kind'] != CompletionKind.PURSUIT:
        return {}
    return ((cache or {}).get('pursuits') or {}).get(row['ref']) or {}


def timed_facts(cache: dict | None, row: dict) -> dict:
    """The facts of a pursuit counted in minutes. Raises PickRefused for any other row, whose log carries no minutes."""
    facts = pursuit_facts(cache, row)
    if not facts.get('timed'):
        raise PickRefused(f'{row["text"]} is not counted in minutes')
    return facts


def logged_pursuit(row: dict, minutes: int | None) -> Completion:
    """`doit pursuits log`, in-process, naming the row's choice. Its on_log runs unasked, because the pick is the asking."""
    name = row['ref']
    try:
        logged = log_pursuit(name, row.get('item_id'), minutes, '', assume_yes=True)
    except Unrecorded as lost:
        return Completion(False, f'doit: {name} not recorded', '\n'.join(lost.lines()), service.exit_code(lost.failure))
    except (LogRefused, service.ServiceFailed, LoginError) as failed:
        # A send with no answer may have landed, so only a refusal frees the row.
        refused = not isinstance(failed, service.NoAnswer)
        return Completion(False, f'doit: {name} not logged', str(failed), service.exit_code(failed), recorded_nothing=refused)
    entry = logged.lines()[0]
    if logged.exit_code:
        return Completion(False, f'doit: {name} logged, and its on_log failed', f'{entry}\n{logged.ran} exited {logged.exit_code}', 1)
    return Completion(True, 'doit: logged', '\n'.join(logged.lines()))


def marked_review_done(row: dict) -> Completion:
    """`doit review done`, in-process. The service keeps one completion a day for an entry, so a failure frees the row."""
    try:
        done = mark_done(row['ref'])
    except (service.ServiceFailed, LoginError) as failed:
        return Completion(False, f'doit: {row["ref"]} not marked done', str(failed), service.exit_code(failed), recorded_nothing=True)
    days = (done.get('status') or {}).get('cadence_days')
    return Completion(True, 'doit: marked done', f'{row["ref"]} ({done["done_on"]})' + (f' · due again in {days}d' if days else ''))


def completed_habit(row: dict) -> Completion:
    """`life habits complete <id>`, stopped after `HABIT_SECONDS`. One that ran out of time may have landed, so it holds the row."""
    try:
        run_program(habit_completion_argv(row['ref']), HABIT_SECONDS)
    except ProgramFailed as failed:
        return Completion(False, 'doit: habit not completed', str(failed), 1, recorded_nothing=not failed.timed_out)
    return Completion(True, 'doit: habit completed', row['text'])


def completion_refusal(row: dict) -> str | None:
    """Why a row cannot be completed as it stands, checked before anything runs."""
    match CompletionKind(row['kind']):
        case CompletionKind.HABIT:
            # A source's lane names the habit, so its id reaches life as an argument only where it is one.
            return None if re.fullmatch('[0-9]+', row['ref']) else f'{row["text"]} names habit {row["ref"]!r}, and a habit id is all digits'
        case CompletionKind.PURSUIT | CompletionKind.REVIEW | CompletionKind.PRACTICE:
            return None
        case _ as unmatched:
            assert_never(unmatched)


def completed(row: dict, minutes: int | None) -> Completion:
    """Complete one row through its owner, with what doit builds from the row's kind and ref."""
    match CompletionKind(row['kind']):
        case CompletionKind.PURSUIT:
            return logged_pursuit(row, minutes)
        case CompletionKind.HABIT:
            return completed_habit(row)
        case CompletionKind.REVIEW | CompletionKind.PRACTICE:
            return marked_review_done(row)
        case _ as unmatched:
            assert_never(unmatched)


def complete(row: dict, minutes: int | None, cache: dict | None) -> dict:
    """Hold the pick, complete the row, then settle the pick, end a nudge in hand where it went through, and write `picked`.

    Raises PickRefused where the key is held already, and CompletionFailed
    where the completion did not go through. A completion that recorded nothing
    frees its row at once. Every other one holds it until a read that begins
    after it ended, which the watch starts within a tick.
    """
    moment = now()
    with changed_state() as state:
        taken = desk_nudge.take_pick(state, cache, row['key'], moment)
    if not taken:
        raise PickRefused(f'{row["text"]} is picked already, and is taken again once the watch reads the rows again')
    seen = snapshot()
    try:
        outcome = completed(row, minutes)
    except Exception as error:
        log.exception(f'completing {row["key"]} raised, so its row is held until the rows are read again')
        outcome = Completion(False, 'doit: not completed', f'{type(error).__name__}: {error}', 1)
    with changed_state() as state:
        desk_nudge.settle_pick(state, row['key'], now(), recorded_nothing=outcome.recorded_nothing)
        ended = desk_nudge.end_on_pick(state) if outcome.ok else None
        desk_nudge.want_panel_rebuild(state)
    carry_out(desk_nudge.Change(closes=ended))
    notify(outcome.summary, outcome.body)
    nudge_id = ended['nudge_id'] if ended else None
    append_records([desk_nudge.picked_record(moment, nudge_id, row, minutes, None if outcome.ok else outcome.body, seen)])
    if not outcome.ok:
        raise CompletionFailed(outcome)
    return desk_nudge.panel_step(PanelStep.DONE, [], outcome.body)


def picked_answer(key: desk_nudge.AnswerKey) -> dict:
    """Answer through `answer_nudge`, which notifies a failure, and exit as `doit desk-nudge answer` exits for it."""
    try:
        answered = answer_nudge(key.answer, key.nudge_id)
    except NothingToAnswer as nothing:
        raise PickRefused(str(nothing)) from None
    code, said = answer_outcome(answered)
    if code:
        raise CompletionFailed(Completion(False, 'doit: answer not saved', said, code))
    return desk_nudge.panel_step(PanelStep.DONE, [], said)


def minutes_step(row: dict, minutes: int | None, cache: dict | None) -> dict:
    """A timed pursuit's minutes step, centered on `minutes` or else on its usual amount."""
    usual = timed_facts(cache, row).get('usual_minutes')
    return desk_nudge.panel_step(PanelStep.MINUTES, desk_nudge.minutes_rows(row, minutes, usual))


def pick(key: str) -> dict:
    """Act on one panel row by its key, and return the next step.

    Raises PickRefused before anything runs, and CompletionFailed after a
    completion that did not go through.
    """
    try:
        parsed = desk_nudge.parse_key(key)
    except desk_nudge.UnreadableKey as unreadable:
        raise PickRefused(str(unreadable)) from None
    if isinstance(parsed, desk_nudge.AnswerKey):
        return picked_answer(parsed)
    cache, state = read_panel_cache(), read_state()
    match parsed:
        case desk_nudge.MinutesKey(kind=PanelRowKind.ADJUST_MINUTES, minutes=minutes, row_key=row_key):
            return minutes_step(listed_row(cache, state, row_key), minutes, cache)
        case desk_nudge.MinutesKey(minutes=minutes, row_key=row_key):
            row = listed_row(cache, state, row_key)
            timed_facts(cache, row)
            return complete(row, minutes, cache)
        case desk_nudge.CompletionKey(key=row_key):
            row = listed_row(cache, state, row_key)
            return minutes_step(row, None, cache) if pursuit_facts(cache, row).get('timed') else complete(row, None, cache)
        case _ as unmatched:
            assert_never(unmatched)


def print_step(step: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(step, indent=2, ensure_ascii=False))
        return
    if step['message']:
        console.print(Text(step['message']), soft_wrap=True)
    width = max((len(row['key'] or '') for row in step['rows']), default=0)
    for row in step['rows']:
        line = Text('  ')
        line.append(f'{row["key"] or "":<{width}}  ', style='dim')
        line.append(row['text'], style='yellow' if row['kind'] == PanelRowKind.NOTE else '')
        console.print(line, soft_wrap=True)


panel_app = typer.Typer(
    name='panel',
    no_args_is_help=True,
    help=(
        "The desk nudge's panel, which completes any of today's rows in one pick.\n\n"
        '`doit tui` draws the rows that `panel show --json` prints, and hands the key picked to `panel pick`, '
        'which prints the next step. doit builds every completion from the row it names, so the screen holds no rule.'
    ),
    epilog=examples(
        ('doit desk-nudge panel show', "today's rows with the key that picks each"),
        ('doit desk-nudge panel pick habit:17', 'complete habit 17 through life'),
    ),
)
app.add_typer(panel_app, name='panel')


@panel_app.command(
    'show',
    epilog=examples(
        ('doit desk-nudge panel show', "today's rows with the key that picks each"),
        ('doit desk-nudge panel show --json', 'the same step as data, for a menu to draw'),
    ),
)
def panel_show_command(
    as_json: Annotated[bool, typer.Option('--json', help='Output the step as JSON to stdout.')] = False,
) -> None:
    """The suggestion, the four answers while a nudge is ready, and every row that something can complete.

    Read with no request from the rows that the watch keeps, so a row picked
    since they were read is left out. Exits 0.
    """
    cache, state = read_panel_cache(), read_state()
    rows = desk_nudge.unpicked(desk_nudge.panel_today(cache, state), state, cache)
    print_step(desk_nudge.panel_step(PanelStep.TODAY, rows), as_json)
    raise typer.Exit(0)


@panel_app.command(
    'pick',
    epilog=examples(
        ('doit desk-nudge panel pick pursuit:read', "open read's minutes, starting on the amount most often logged"),
        ('doit desk-nudge panel pick review:prune-docker --json', 'mark a review entry done, and print the done step as data'),
    ),
)
def panel_pick_command(
    key: Annotated[str, typer.Argument(help='A row key that `panel show` printed.')],
    as_json: Annotated[bool, typer.Option('--json', help='Output the next step as JSON to stdout.')] = False,
) -> None:
    """Act on one row: answer the nudge, open a timed pursuit's minutes, or complete the row through its owner.

    Prints the next step, or a done step once something is completed, and exits
    0 for either. Exits 2 where the panel will not act on the key, such as a row
    picked already, and nothing ran. Exits 1 where the completion did not go
    through, and 4 where the service gave no answer. A notification names the
    error in each case, because a menu shows no stderr.
    """
    try:
        step = pick(key)
    except PickRefused as refused:
        notify('doit: not picked', str(refused))
        error_console.print(Text(f'doit desk-nudge panel pick: {refused}'), soft_wrap=True)
        raise typer.Exit(PICK_REFUSED_EXIT) from None
    except CompletionFailed as failed:
        error_console.print(
            Text(f'doit desk-nudge panel pick: {failed.completion.summary.removeprefix("doit: ")}: {failed}'), soft_wrap=True
        )
        raise typer.Exit(failed.completion.exit_code) from None
    print_step(step, as_json)
    raise typer.Exit(0)
