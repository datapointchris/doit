"""The desk nudge against the real service, in process.

The watch runs on an injected clock, with `claude-sessions`, `hyprctl`,
`notify-send` and `gdbus` stand-ins on PATH that answer from recorded output.
"""

import datetime as dt
import json
import os
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

import httpx2
import pytest
from conftest import connected
from conftest import stand_in
from jsonschema import Draft7Validator
from typer.testing import CliRunner

from doit import desk_nudge
from doit.cli import app as cli_app
from doit.desk_nudge import Event
from doit.desk_nudge import Phase
from doit.desk_nudge import SuggestionError
from doit.service_commands import desk_nudge as commands

FIRED_AT = '2026-11-22T14:00:00-05:00'
REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / 'tests' / 'fixtures' / 'desk-nudge'
SCHEMAS = REPO / 'schemas'
INSTANCE = json.loads((FIXTURES / 'hyprctl-instances.json').read_text())[0]['instance']
DESK_START = dt.datetime(2026, 11, 22, 13, 0, tzinfo=dt.timezone(dt.timedelta(hours=-5)))
BOOKS = [{'title': 'A Neutral Book', 'id': 7}, {'title': 'Another Book', 'id': 9}]
READ = 'pursuits:\n  read:\n    description: Read what is on the shelf\n    weight: 30\n    cadence: 3d\n'
RESOLVED_READ = READ + '    resolve: {app: life, path: /books}\n    label: title\n    id: id\n'
ONLY_PAUSED = READ + '    paused: true\n'
NOTIFIED = 4711

# Each stand-in logs its call, then answers as the real program does, from the recorded output.
STAND_IN = """#!PYTHON
import json, os, sys, time
from pathlib import Path

here = Path(os.environ['DESK_NUDGE_STAND_INS'])
fixtures = Path(os.environ['DESK_NUDGE_FIXTURES'])
name, args = Path(sys.argv[0]).name, sys.argv[1:]
with (here / 'calls.jsonl').open('a') as calls:
    calls.write(json.dumps({'program': name, 'argv': args, 'signature': os.environ.get('HYPRLAND_INSTANCE_SIGNATURE')}) + '\\n')
if name == 'claude-sessions':
    print((fixtures / 'claude-sessions.json').read_text())
elif name == 'hyprctl':
    instances = (fixtures / 'hyprctl-instances.json').read_text()
    if args == ['instances', '-j']:
        print(instances)
    elif args == ['-i', json.loads(instances)[0]['instance'], 'activewindow', '-j']:
        print((fixtures / 'hyprctl-activewindow.json').read_text())
    else:
        sys.exit('HYPRLAND_INSTANCE_SIGNATURE not set! (is hyprland running?)')
elif name == 'gdbus':
    if 'org.freedesktop.DBus.GetNameOwner' in args:
        print((fixtures / 'gdbus-get-name-owner.txt').read_text().strip())
    else:
        (here / f'closed-{args[-1]}').touch()
        print('()')
elif name == 'notify-send' and '-A' in args:
    shown = args[args.index('-r') + 1] if '-r' in args else 'NOTIFIED'
    print(shown, flush=True)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if (here / 'action').exists():
            chosen = (here / 'action').read_text()
            (here / 'action').unlink()
            if chosen:
                print(chosen, flush=True)
            sys.exit(0)
        if (here / f'closed-{shown}').exists():
            (here / f'closed-{shown}').unlink()
            sys.exit(0)
        time.sleep(0.02)
"""


@dataclass
class Clock:
    moment: dt.datetime

    def __call__(self) -> dt.datetime:
        return self.moment


@dataclass
class Desk:
    """One desk: its stand-ins, its clock, and the watches started on it."""

    root: Path
    clock: Clock
    watches: list[commands.Watch] = field(default_factory=list)

    def watch(self) -> commands.Watch:
        watch = commands.Watch()
        self.watches.append(watch)
        watch.start()
        return watch

    def run(self, watch: commands.Watch, seconds: int) -> None:
        """Tick every 15 seconds for `seconds`, as the loop would."""
        for _ in range(seconds // desk_nudge.TICK_SECONDS):
            self.clock.moment += dt.timedelta(seconds=desk_nudge.TICK_SECONDS)
            watch.tick()

    def state(self) -> dict:
        return commands.read_state()

    def records(self, event: Event | None = None) -> list[dict]:
        """Every record line written, each checked against the committed schema."""
        path = self.root / 'state' / 'doit' / 'desk-nudge-events.jsonl'
        lines = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        validator = Draft7Validator(json.loads((SCHEMAS / desk_nudge.RECORD_SCHEMA_FILE).read_text()))
        for line in lines:
            validator.validate(line)
        return [line for line in lines if event is None or line['event'] == event]

    def calls(self, program: str) -> list[list[str]]:
        path = self.root / 'calls.jsonl'
        logged = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        assert all(call['signature'] is None for call in logged), 'the watch never hands on HYPRLAND_INSTANCE_SIGNATURE'
        return [call['argv'] for call in logged if call['program'] == program]

    def click(self, action: str) -> None:
        """The person's click on the open notification: an answer's name, or '' for a left click that closes it."""
        pending = self.root / 'action.pending'
        pending.write_text(action)
        pending.rename(self.root / 'action')

    def status(self) -> dict:
        shown = CliRunner().invoke(cli_app, ['desk-nudge', 'status', '--json'])
        assert shown.exit_code == 0, shown.output
        document = json.loads(shown.output)
        Draft7Validator(json.loads((SCHEMAS / desk_nudge.STATUS_SCHEMA_FILE).read_text())).validate(document)
        return document


def until(condition: Callable[[], object], seconds: float = 15) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, 'the desk never reached the expected state'
        time.sleep(0.02)


@pytest.fixture
def desk(tmp_path, monkeypatch):
    for name in (desk_nudge.RECORD_SCHEMA_FILE, desk_nudge.STATUS_SCHEMA_FILE):
        built = desk_nudge.record_schema() if name == desk_nudge.RECORD_SCHEMA_FILE else desk_nudge.status_schema()
        assert json.loads((SCHEMAS / name).read_text()) == built, f'schemas/{name} is stale: run `task schemas`'
    root = tmp_path / 'desk'
    programs = root / 'bin'
    programs.mkdir(parents=True)
    for program in ('claude-sessions', 'hyprctl', 'gdbus', 'notify-send'):
        script = programs / program
        script.write_text(STAND_IN.replace('PYTHON', sys.executable).replace('NOTIFIED', str(NOTIFIED)))
        script.chmod(0o755)
    (root / 'run' / 'hypr' / INSTANCE).mkdir(parents=True)
    (root / 'run' / 'hypr' / INSTANCE / '.socket.sock').touch()
    (root / 'proc' / '4242').mkdir(parents=True)
    (root / 'proc' / '4242' / 'comm').write_text('hypridle\n')
    monkeypatch.setenv('PATH', f'{programs}{os.pathsep}{os.environ["PATH"]}')
    monkeypatch.setenv('DESK_NUDGE_STAND_INS', str(root))
    monkeypatch.setenv('DESK_NUDGE_FIXTURES', str(FIXTURES))
    monkeypatch.setenv('XDG_STATE_HOME', str(root / 'state'))
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(root / 'run'))
    monkeypatch.delenv('HYPRLAND_INSTANCE_SIGNATURE', raising=False)
    monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
    monkeypatch.setattr(commands, 'PROC', root / 'proc')
    monkeypatch.setattr('doit.cli.notify', lambda config: None)
    clock = Clock(DESK_START)
    monkeypatch.setattr(commands, 'now', clock)
    found = Desk(root, clock)
    yield found
    for watch in found.watches:
        watch.close()


def registered(store, monkeypatch, tmp_path, register: str, around=None):
    client = connected(store, monkeypatch, tmp_path, {'life': stand_in('life', {'/books': lambda request: BOOKS})}, around)
    client.put('/api/v1/documents/pursuits', json={'text': register}).raise_for_status()
    return client


def fired(desk: Desk) -> commands.Watch:
    """A watch run for 60 present minutes, until its nudge is ready and showing."""
    watch = desk.watch()
    desk.run(watch, 60 * 60 - 15)
    assert desk.state()['phase'] == Phase.COUNTING
    desk.run(watch, 15)
    until(lambda: (desk.state()['nudge'] or {}).get('notification_id'))
    return watch


def answer(api, nudge_id: uuid.UUID, given: str) -> dict:
    sent = api.post(
        '/api/v1/desk-nudge/answers', json={'nudge_id': str(nudge_id), 'answer': given, 'fired_at': FIRED_AT, 'machine': 'desk'}
    )
    assert sent.status_code == 201, sent.text
    return sent.json()


def test_an_answer_moves_the_interval_and_too_soon_then_too_late_cancel(api):
    first, second = uuid.uuid4(), uuid.uuid4()
    assert api.get('/api/v1/desk-nudge').json()['interval_minutes'] == 60

    soon = answer(api, first, 'too_soon')
    resent = answer(api, first, 'too_soon')
    late = answer(api, second, 'too_late')

    assert soon['interval_minutes'] == 66
    assert (soon['added'], resent['added']) == (True, False)
    assert resent['stored'] == soon['stored'], 'a resend answers the row first stored'
    assert resent['interval_minutes'] == 66, 'a resend is stored once'
    assert late['interval_minutes'] == 60
    assert api.get('/api/v1/desk-nudge').json()['answer_counts'] == {'good_timing': 0, 'too_soon': 1, 'too_late': 1, 'bothered': 0}


def test_a_present_interval_fires_the_nudge_with_the_draws_first_item(store, monkeypatch, tmp_path, desk):
    registered(store, monkeypatch, tmp_path, RESOLVED_READ)
    fired(desk)

    [record] = desk.records(Event.FIRED)
    assert record['suggestion'] == {'pursuit': 'read', 'item_id': '7', 'label': 'A Neutral Book'}
    assert (record['present_minutes'], record['interval_minutes']) == (60, 60)
    assert record['sessions'] == json.loads((FIXTURES / 'claude-sessions.json').read_text())
    assert record['focused_window'] == {'class': 'com.mitchellh.ghostty', 'title': 'Terminal — project-1'}
    assert desk.calls('hyprctl') == [['instances', '-j'], ['-i', INSTANCE, 'activewindow', '-j']]
    [argv] = desk.calls('notify-send')
    assert argv[:3] == ['-p', '-t', '0']
    assert [argv[place + 1] for place, part in enumerate(argv) if part == '-A'] == [
        'good_timing=good timing',
        'too_soon=too soon',
        'too_late=too late',
        'bothered=bothered me',
    ]
    status = desk.status()
    assert (status['phase'], status['pill']['text'], status['nudge_id']) == ('ready', 'ready · read', record['nudge_id'])

    desk.click('too_soon')
    until(lambda: desk.records(Event.ANSWERED))

    [answered] = desk.records(Event.ANSWERED)
    assert (answered['answer'], answered['saved'], answered['late'], answered['nudge_id']) == ('too_soon', True, False, record['nudge_id'])
    status = desk.status()
    assert (status['phase'], status['present_minutes'], status['interval_minutes'], status['minutes_left']) == ('counting', 0, 66, 66)


def test_an_unanswered_nudge_expires_after_ten_minutes(store, monkeypatch, tmp_path, desk):
    registered(store, monkeypatch, tmp_path, RESOLVED_READ)
    watch = fired(desk)

    desk.run(watch, 5 * 60)
    assert watch.shown is not None and watch.shown.process.poll() is None, 'the ticks ran while the notification stayed open'
    desk.click('')
    until(lambda: desk.records(Event.DISMISSED))
    assert desk.status()['pill']['text'] == 'ready · read'

    desk.run(watch, 5 * 60 - 15)
    assert desk.state()['phase'] == Phase.READY
    desk.run(watch, 15)

    [expired] = desk.records(Event.EXPIRED)
    assert expired['nudge_id'] == desk.records(Event.FIRED)[0]['nudge_id']
    assert desk.calls('gdbus')[-1][-2:] == ['org.freedesktop.Notifications.CloseNotification', str(NOTIFIED)]
    assert (desk.state()['phase'], desk.state()['present_seconds']) == (Phase.COUNTING, 0)


def refused_next(answer: Callable[[], httpx2.Response]):
    return lambda request, forward: answer() if request.url.path == '/api/v1/next' else forward(request)


def unreachable() -> httpx2.Response:
    raise httpx2.ConnectError('connection refused')


@pytest.mark.parametrize(
    ('register', 'around', 'kind'),
    [
        (RESOLVED_READ, refused_next(unreachable), SuggestionError.UNREACHABLE),
        (RESOLVED_READ, refused_next(lambda: httpx2.Response(401, json={'message': 'unknown token'})), SuggestionError.LOGIN),
        (ONLY_PAUSED, None, SuggestionError.NOTHING_TO_DRAW),
    ],
)
def test_a_failed_next_still_fires_and_records_the_error(store, monkeypatch, tmp_path, desk, register, around, kind):
    registered(store, monkeypatch, tmp_path, register, around)
    fired(desk)

    [record] = desk.records(Event.FIRED)
    assert (record['suggestion']['error'], record['suggestion']['message']) == (kind.value, desk_nudge.SUGGESTION_MESSAGES[kind])
    [argv] = desk.calls('notify-send')
    assert argv[-2] == 'Time for a break'
    assert argv[-1].startswith(desk_nudge.SUGGESTION_MESSAGES[kind])
    assert desk.status()['pill']['text'] == 'ready · break'


def test_a_restarted_watch_resumes_a_ready_nudge(store, monkeypatch, tmp_path, desk):
    registered(store, monkeypatch, tmp_path, RESOLVED_READ)
    stopped_while_firing = desk_nudge.fresh_state()
    stopped_while_firing.update(present_seconds=60 * 60.0, last_tick=desk_nudge.stamp(DESK_START))
    nudge = desk_nudge.begin_firing(stopped_while_firing, DESK_START, '019aad00-0000-7000-8000-00000000000a')
    commands.write_state(stopped_while_firing)

    first = desk.watch()
    until(lambda: (desk.state()['nudge'] or {}).get('notification_id'))
    first.close()
    desk.clock.moment += dt.timedelta(seconds=30)
    desk.watch()
    until(lambda: len(desk.calls('notify-send')) == 2)

    shown, shown_again = desk.calls('notify-send')
    assert [record['nudge_id'] for record in desk.records(Event.FIRED)] == [nudge['nudge_id']]
    assert '-r' not in shown
    assert shown_again[shown_again.index('-r') + 1] == str(NOTIFIED)
    assert desk.state()['phase'] == Phase.READY


def test_an_answer_that_fails_to_save_is_sent_again_until_stored(store, monkeypatch, tmp_path, desk):
    down = [True]

    def failing_answers(request, forward):
        if down[0] and request.url.path == '/api/v1/desk-nudge/answers':
            return httpx2.Response(503)
        return forward(request)

    registered(store, monkeypatch, tmp_path, RESOLVED_READ, failing_answers)
    watch = fired(desk)

    taken = CliRunner().invoke(cli_app, ['desk-nudge', 'answer', 'too_soon'])
    assert taken.exit_code == commands.NO_ANSWER_EXIT, taken.output
    assert [(line['saved'], line['answer']) for line in desk.records(Event.ANSWERED)] == [(False, 'too_soon')]
    status = desk.status()
    assert (status['phase'], status['queued_answers'], status['interval_minutes']) == ('counting', 1, 60)

    down[0] = False
    desk.run(watch, 15)
    until(lambda: len(desk.records(Event.ANSWERED)) == 2)

    assert [line['saved'] for line in desk.records(Event.ANSWERED)] == [False, True]
    status = desk.status()
    assert (status['queued_answers'], status['interval_minutes'], status['minutes_left']) == (0, 66, 66)
