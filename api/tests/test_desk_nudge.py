"""The desk nudge against the real service, in process.

The watch runs on an injected clock, with `claude-sessions`, `hyprctl`,
`canberra-gtk-play` and `notify-send` stand-ins on PATH that answer from recorded output.
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
from doit import journal
from doit import pursuits
from doit import service
from doit.cli import app as cli_app
from doit.desk_nudge import Answer
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

# Each stand-in logs its call, then answers as the real program does, from the recorded output.
STAND_IN = """#!PYTHON
import json, os, sys
from pathlib import Path

here = Path(os.environ['DESK_NUDGE_STAND_INS'])
fixtures = Path(os.environ['DESK_NUDGE_FIXTURES'])
name, args = Path(sys.argv[0]).name, sys.argv[1:]
with (here / 'calls.jsonl').open('a') as calls:
    calls.write(json.dumps({'program': name, 'argv': args}) + '\\n')
if name == 'claude-sessions':
    print((fixtures / 'claude-sessions.json').read_text())
elif name == 'hyprctl':
    instances = (fixtures / 'hyprctl-instances.json').read_text()
    if args == ['instances', '-j']:
        print(instances)
    elif args == ['-i', json.loads(instances)[0]['instance'], 'activewindow', '-j']:
        print((fixtures / 'hyprctl-activewindow.json').read_text())
    elif args == ['-i', json.loads(instances)[0]['instance'], 'monitors', '-j']:
        print(json.dumps([{'name': 'DP-1', 'dpmsStatus': True}]))
    else:
        sys.exit('HYPRLAND_INSTANCE_SIGNATURE not set! (is hyprland running?)')
elif name == 'loginctl':
    print('no')
elif name == 'life':
    failing = here / 'life-exit'
    if failing.exists():
        sys.exit('the habit store is down')
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
        return [call['argv'] for call in logged if call['program'] == program]

    def status(self) -> dict:
        shown = CliRunner().invoke(cli_app, ['desk-nudge', 'status', '--json'])
        assert shown.exit_code == 0, shown.output
        document = json.loads(shown.output)
        Draft7Validator(json.loads((SCHEMAS / desk_nudge.STATUS_SCHEMA_FILE).read_text())).validate(document)
        return document

    def panel(self, *args: str, exit_code: int = 0) -> dict:
        """One `panel` command's step, checked against the committed schema, or {} where it exits non-zero and prints none."""
        ran = CliRunner().invoke(cli_app, ['desk-nudge', 'panel', *args, '--json'])
        assert ran.exit_code == exit_code, ran.output
        if exit_code:
            assert ran.stdout == ''
            return {}
        step = json.loads(ran.stdout)
        Draft7Validator(json.loads((SCHEMAS / desk_nudge.PANEL_SCHEMA_FILE).read_text())).validate(step)
        return step


def until(condition: Callable[[], object], seconds: float = 15) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, 'the desk never reached the expected state'
        time.sleep(0.02)


@pytest.fixture
def desk(tmp_path, monkeypatch):
    built = {
        desk_nudge.RECORD_SCHEMA_FILE: desk_nudge.record_schema(),
        desk_nudge.STATUS_SCHEMA_FILE: desk_nudge.status_schema(),
        desk_nudge.PANEL_SCHEMA_FILE: desk_nudge.panel_schema(),
    }
    for name, schema in built.items():
        assert json.loads((SCHEMAS / name).read_text()) == schema, f'schemas/{name} is stale: run `task schemas`'
    root = tmp_path / 'desk'
    programs = root / 'bin'
    programs.mkdir(parents=True)
    for program in ('claude-sessions', 'hyprctl', 'canberra-gtk-play', 'notify-send', 'loginctl', 'life'):
        script = programs / program
        script.write_text(STAND_IN.replace('PYTHON', sys.executable))
        script.chmod(0o755)
    (root / 'run' / 'hypr' / INSTANCE).mkdir(parents=True)
    (root / 'run' / 'hypr' / INSTANCE / '.socket.sock').touch()
    monkeypatch.setenv('PATH', f'{programs}{os.pathsep}{os.environ["PATH"]}')
    monkeypatch.setenv('DESK_NUDGE_STAND_INS', str(root))
    monkeypatch.setenv('DESK_NUDGE_FIXTURES', str(FIXTURES))
    monkeypatch.setenv('XDG_STATE_HOME', str(root / 'state'))
    monkeypatch.setenv('XDG_CACHE_HOME', str(root / 'cache'))
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(root / 'run'))
    monkeypatch.delenv('HYPRLAND_INSTANCE_SIGNATURE', raising=False)
    monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
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
    """A watch run for 30 minutes, until its nudge is ready and has chimed."""
    watch = desk.watch()
    desk.run(watch, 30 * 60 - 15)
    assert desk.state()['phase'] == Phase.COUNTING
    desk.run(watch, 15)
    until(lambda: desk.calls('canberra-gtk-play'))
    return watch


def answer(api, nudge_id: uuid.UUID, given: str) -> dict:
    sent = api.post(
        '/api/v1/desk-nudge/answers', json={'nudge_id': str(nudge_id), 'answer': given, 'fired_at': FIRED_AT, 'machine': 'desk'}
    )
    assert sent.status_code == 201, sent.text
    return sent.json()


def test_an_answer_moves_the_interval_and_too_soon_then_too_late_cancel(api):
    first, second = uuid.uuid4(), uuid.uuid4()
    assert api.get('/api/v1/desk-nudge').json()['interval_minutes'] == 30

    soon = answer(api, first, 'too_soon')
    resent = answer(api, first, 'too_soon')
    late = answer(api, second, 'too_late')

    assert soon['interval_minutes'] == 33
    assert (soon['added'], resent['added']) == (True, False)
    assert resent['stored'] == soon['stored'], 'a resend answers the row first stored'
    assert resent['interval_minutes'] == 33, 'a resend is stored once'
    assert late['interval_minutes'] == 30
    assert api.get('/api/v1/desk-nudge').json()['answer_counts'] == {'good_timing': 0, 'too_soon': 1, 'too_late': 1, 'bothered': 0}


def test_the_interval_fires_the_nudge_with_the_draws_first_item(store, monkeypatch, tmp_path, desk):
    registered(store, monkeypatch, tmp_path, RESOLVED_READ)
    fired(desk)

    [record] = desk.records(Event.FIRED)
    assert record['suggestion'] == {'pursuit': 'read', 'item_id': None, 'label': 'A Neutral Book\nAnother Book'}
    assert (record['counted_minutes'], record['interval_minutes']) == (30, 30)
    assert record['sessions'] == json.loads((FIXTURES / 'claude-sessions.json').read_text())
    assert record['focused_window'] == {'class': 'com.mitchellh.ghostty', 'title': 'Terminal — project-1'}
    window, monitors = ['-i', INSTANCE, 'activewindow', '-j'], ['-i', INSTANCE, 'monitors', '-j']
    assert desk.calls('hyprctl') == [['instances', '-j'], window, ['instances', '-j'], monitors]
    assert desk.calls('canberra-gtk-play') == [['--id', commands.CHIME_SOUND, '--description', 'desk nudge']]
    assert desk.calls('notify-send') == [], 'a firing shows no notification'
    status = desk.status()
    assert (status['phase'], status['pill']['text'], status['nudge_id']) == ('ready', 'ready · read', record['nudge_id'])

    assert CliRunner().invoke(cli_app, ['desk-nudge', 'answer', 'too_soon']).exit_code == 0

    [answered] = desk.records(Event.ANSWERED)
    assert (answered['answer'], answered['saved'], answered['late'], answered['nudge_id']) == ('too_soon', True, False, record['nudge_id'])
    status = desk.status()
    assert (status['phase'], status['counted_minutes'], status['interval_minutes'], status['minutes_left']) == ('counting', 0, 33, 33)


def test_an_unanswered_nudge_expires_after_ten_minutes(store, monkeypatch, tmp_path, desk):
    registered(store, monkeypatch, tmp_path, RESOLVED_READ)
    watch = fired(desk)

    desk.run(watch, 10 * 60 - 15)
    assert desk.status()['pill']['text'] == 'ready · read'
    desk.run(watch, 15)

    [expired] = desk.records(Event.EXPIRED)
    assert expired['nudge_id'] == desk.records(Event.FIRED)[0]['nudge_id']
    assert (desk.state()['phase'], desk.state()['counted_seconds']) == (Phase.COUNTING, 0)


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
    assert record['suggestion']['error'] == kind.value
    assert desk.status()['pill']['text'] == 'ready · break'


def test_a_restarted_watch_finishes_a_firing_and_chimes_once(store, monkeypatch, tmp_path, desk):
    registered(store, monkeypatch, tmp_path, RESOLVED_READ)
    stopped_while_firing = desk_nudge.fresh_state()
    stopped_while_firing.update(counted_seconds=30 * 60.0, last_tick=desk_nudge.stamp(DESK_START))
    nudge = desk_nudge.begin_firing(stopped_while_firing, DESK_START, '019aad00-0000-7000-8000-00000000000a')
    commands.write_state(stopped_while_firing)

    first = desk.watch()
    until(lambda: desk.calls('canberra-gtk-play'))
    first.close()
    desk.clock.moment += dt.timedelta(seconds=30)
    desk.watch().close()

    assert [record['nudge_id'] for record in desk.records(Event.FIRED)] == [nudge['nudge_id']]
    assert len(desk.calls('canberra-gtk-play')) == 1
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
    assert taken.exit_code == service.NO_ANSWER_EXIT, taken.output
    assert [(line['saved'], line['answer']) for line in desk.records(Event.ANSWERED)] == [(False, 'too_soon')]
    status = desk.status()
    assert (status['phase'], status['queued_answer_count'], status['interval_minutes']) == ('counting', 1, 30)

    down[0] = False
    desk.run(watch, 15)
    until(lambda: len(desk.records(Event.ANSWERED)) == 2)

    assert [line['saved'] for line in desk.records(Event.ANSWERED)] == [False, True]
    status = desk.status()
    assert (status['queued_answer_count'], status['interval_minutes'], status['minutes_left']) == (0, 33, 33)


TASKS = [{'id': 532, 'name': 'Bathe Dingo'}, {'id': 533, 'name': 'Trim Dingo Nails'}, {'id': 534, 'name': 'Unprogram Alexas'}]
TASKS_AND_READ = (
    'pursuits:\n'
    '  tasks:\n    description: Open tasks\n    weight: 30\n    cadence: 1d\n'
    '    resolve: {app: life, path: /tasks}\n    label: name\n    id: id\n    on_log: life tasks complete {id}\n'
    '  read:\n    description: Read what is on the shelf\n    weight: 30\n    weekly_minutes: 180\n'
)
LIFE_SOURCE = 'sources:\n  life:\n    adapter: life\n'


def life_app(habits: list[dict]):
    """life's API in the shapes it publishes: three open tasks, today's habits, and a concert tonight for the upcoming lane."""
    tonight = dt.datetime.now().astimezone().replace(hour=23, minute=0, second=0, microsecond=0)
    return stand_in(
        'life',
        {
            '/tasks': lambda request: TASKS,
            '/habits/day': lambda request: {'date': f'{tonight:%Y-%m-%d}', 'due': habits, 'completed': [], 'current_total': len(habits)},
            '/events': lambda request: [{'id': 1, 'name': 'Concert', 'date': tonight.isoformat(), 'venue': 'Hall'}],
        },
    )


def read_before(store, *minutes: int) -> None:
    """`read` logged long before the register first saw it, so the entries set its usual minutes and pay none of what it owes."""
    long_ago = dt.datetime.now().astimezone() - dt.timedelta(days=60)
    entries = [
        pursuits.new_record(
            journal.Event.DONE, 'read', None, {'occurred_at': (long_ago + dt.timedelta(days=day)).isoformat(), 'duration_minutes': amount}
        )
        for day, amount in enumerate(minutes)
    ]
    store.append(entries, 'person-1', long_ago)


def test_the_panel_logs_a_task_by_item_and_a_timed_pursuit_by_its_usual_minutes(store, monkeypatch, tmp_path, desk):
    client = connected(store, monkeypatch, tmp_path, {'life': life_app([])})
    client.put('/api/v1/documents/sources', json={'text': LIFE_SOURCE}).raise_for_status()
    client.put('/api/v1/documents/pursuits', json={'text': TASKS_AND_READ}).raise_for_status()
    read_before(store, 45, 30, 45)
    fired(desk)
    until(lambda: (commands.read_panel_cache() or {}).get('suggestion'))
    [nudge] = desk.records(Event.FIRED)

    answers = [f'answer:{answer.value}:{nudge["nudge_id"]}' for answer in Answer]
    tasks = ['pursuit:tasks:532', 'pursuit:tasks:533', 'pursuit:tasks:534']
    shown = desk.panel('show')['rows']
    assert [row['key'] for row in shown if row['kind'] == 'answer'] == answers
    assert {row['key'] for row in shown} == {*answers, *tasks, 'pursuit:read'}, 'each task its own row, and no upcoming row'

    assert desk.panel('pick', 'pursuit:tasks:532')['step'] == 'done'
    assert desk.calls('life') == [['tasks', 'complete', '532']]
    [picked] = desk.records(Event.PICKED)
    assert (picked['nudge_id'], picked['pick']['item_id'], picked['pick']['ok']) == (nudge['nudge_id'], '532', True)
    assert (desk.state()['phase'], desk.state()['counted_seconds']) == (Phase.COUNTING, 0)
    desk.panel('pick', 'pursuit:tasks:532', exit_code=2)
    assert desk.calls('life') == [['tasks', 'complete', '532']], 'a pick is taken once'

    usual = desk.panel('pick', 'pursuit:read')
    assert [row['key'] for row in usual['rows']] == [
        'log_minutes:45:pursuit:read',
        'adjust_minutes:50:pursuit:read',
        'adjust_minutes:40:pursuit:read',
    ]
    more = desk.panel('pick', 'adjust_minutes:50:pursuit:read')
    assert more['rows'][0]['key'] == 'log_minutes:50:pursuit:read'
    assert desk.panel('pick', 'log_minutes:50:pursuit:read')['step'] == 'done'
    newest = [record for record in store.journal() if record['event'] == journal.Event.DONE and record['pursuit'] == 'read'][-1]
    assert newest['duration_minutes'] == 50


def test_the_panel_completes_a_habit_and_a_review_and_names_a_failed_completion(store, monkeypatch, tmp_path, desk):
    down = [False]

    def unreachable_today(request, forward):
        return unreachable() if down[0] and request.url.path == '/api/v1/today' else forward(request)

    client = connected(
        store, monkeypatch, tmp_path, {'life': life_app([{'id': 7, 'name': 'Stretch'}, {'id': 8, 'name': 'Yoga'}])}, unreachable_today
    )
    client.put('/api/v1/documents/sources', json={'text': LIFE_SOURCE}).raise_for_status()
    client.put(
        '/api/v1/documents/review', json={'text': (REPO / 'tests' / 'fixtures' / 'review' / 'register.yml').read_text()}
    ).raise_for_status()
    commands.rebuild_panel_cache()

    assert desk.panel('pick', 'habit:7')['step'] == 'done'
    assert desk.calls('life') == [['habits', 'complete', '7']]
    assert desk.panel('pick', 'review:never-done')['step'] == 'done'
    assert client.get('/api/v1/review/never-done/completions').json(), 'the entry is recorded done'

    (desk.root / 'life-exit').touch()
    desk.panel('pick', 'habit:8', exit_code=1)
    failed = [record['pick'] for record in desk.records(Event.PICKED) if not record['pick']['ok']]
    assert [pick['ref'] for pick in failed] == ['8']
    assert 'life habits complete 8 exited 1' in failed[0]['error']
    assert any('life habits complete 8 exited 1' in argv[-1] for argv in desk.calls('notify-send'))

    down[0] = True
    commands.rebuild_panel_cache()
    shown = [(row['kind'], row['key']) for row in desk.panel('show')['rows']]
    # Kept from the read before, less what was done since, and with habit 8 free again because life refused it.
    assert shown == [('note', None), ('review', 'review:overdue-item'), ('review', 'review:fresh-item'), ('habit', 'habit:8')]
