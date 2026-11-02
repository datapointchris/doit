"""The CLI's commands against the real service, in process.

Each command decodes what a route answers, so a shape changed on one side and
not the other fails here rather than on a desk. The service runs on the real
clock, because the CLI stamps a log with the desk's.
"""

import datetime as dt
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from conftest import TOKEN
from conftest import client_for
from conftest import stand_in
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from doit import cli
from doit import happened
from doit import journal
from doit import lanes
from doit import pursuits
from doit import render
from doit import review
from doit import service
from doit.cli import app as cli_app
from doit.service_commands import dashboard as dashboard_commands
from doit.service_commands import forecast as forecast_commands
from doit.service_commands import happened as happened_commands
from doit.service_commands import pursuits as pursuit_commands
from doit.service_commands import review as review_commands
from doit.service_commands import sources as sources_commands
from doit.service_commands import today as today_commands

FIXTURES = Path(__file__).resolve().parents[2] / 'tests' / 'fixtures'
TASKS = [{'name': 'Face Mud', 'id': 501}, {'name': 'Return hoodies', 'id': 512}, {'name': 'Journal', 'id': 474}]


@pytest.fixture
def api(store, monkeypatch, tmp_path) -> TestClient:
    return connected(store, monkeypatch, tmp_path)


Forward = Callable[[httpx2.Request], httpx2.Response]


def connected(
    store, monkeypatch, tmp_path, apps=None, around: Callable[[httpx2.Request, Forward], httpx2.Response] | None = None
) -> TestClient:
    """The service, with every CLI command in this test asking it and caching into the test's own directory.

    ``around`` stands between the CLI and the service, to lose an answer or answer in its place.
    """
    client = client_for(store, apps, clock=None)

    def forward(request: httpx2.Request) -> httpx2.Response:
        answer = client.request(
            request.method,
            request.url.path,
            params=dict(request.url.params),
            content=request.content,
            headers={key: value for key, value in request.headers.items() if key.lower() != 'host'},
        )
        return httpx2.Response(answer.status_code, headers=dict(answer.headers), content=answer.content)

    def transport(request: httpx2.Request) -> httpx2.Response:
        return forward(request) if around is None else around(request, forward)

    tokens = SimpleNamespace(token=lambda: SimpleNamespace(access_token=TOKEN))
    monkeypatch.setattr(
        service,
        'client',
        lambda: service.Service('https://doit.test', tokens, transport=httpx2.MockTransport(transport), pause=lambda seconds: None),
    )
    monkeypatch.setattr(pursuits, 'CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setattr(pursuits, 'NAMES_CACHE', tmp_path / 'cache' / 'next-names.txt')
    monkeypatch.setattr(review_commands, 'NUDGE_INTERVAL', tmp_path / 'cache' / 'nudge-interval-minutes')
    return client


def at_terminal(monkeypatch, there: bool) -> None:
    """Whether someone is at the terminal, for the commands and for the prompts they call."""
    for module in (pursuits, pursuit_commands):
        monkeypatch.setattr(module, 'can_prompt', lambda: there)


def write(client: TestClient, name: str, fixture: str) -> None:
    client.put(f'/api/v1/documents/{name}', json={'text': (FIXTURES / fixture).read_text()}).raise_for_status()


def answers(monkeypatch, *replies: str) -> list[str]:
    """Answer the prompts from a script, recording what each one asked."""
    asked: list[str] = []
    queued = list(replies)

    def reply(prompt: str) -> str:
        asked.append(prompt.strip())
        return queued.pop(0)

    for module in (pursuits, pursuit_commands):
        monkeypatch.setattr(module, 'ask', reply)
    at_terminal(monkeypatch, True)
    return asked


def logged(store) -> list[dict]:
    return [record for record in store.journal() if record['event'] == journal.Event.DONE]


def write_through(store, monkeypatch, tmp_path, answer: Callable[[], object], around=None) -> Path:
    """`chores` resolved from a tasks app giving ``answer()``, completing the chosen item by writing its id to the file returned."""
    marker = tmp_path / 'completed'
    client = connected(store, monkeypatch, tmp_path, {'icb': stand_in('icb', {'/tasks': lambda request: answer()})}, around)
    register = 'pursuits:\n  chores:\n    weight: 25\n    cadence: 3d\n    resolve: {app: icb, path: /tasks}\n    label: name\n    id: id\n'
    client.put('/api/v1/documents/pursuits', json={'text': register + f'    on_log: sh -c "echo {{id}} > {marker}"\n'}).raise_for_status()
    return marker


def test_a_refusal_exits_1_with_the_services_sentence_and_a_service_that_never_answers_exits_4(store, monkeypatch, tmp_path, capsys):
    down = [False]
    client = connected(store, monkeypatch, tmp_path, around=lambda request, forward: httpx2.Response(503) if down[0] else forward(request))
    monkeypatch.setattr(sys, 'argv', ['doit', 'next'])

    with pytest.raises(SystemExit) as refused:
        cli.main()
    assert refused.value.code == 1
    assert capsys.readouterr().err.split() == f'doit: {client.get("/api/v1/next").json()["message"]}'.split()

    down[0] = True
    with pytest.raises(SystemExit) as unanswered:
        cli.main()
    assert unanswered.value.code == cli.NO_ANSWER_EXIT


def test_next_gives_every_active_pursuit_a_row_from_the_register_the_service_holds(api, capsys, monkeypatch):
    write(api, 'pursuits', 'pursuits/pursuits.yml')
    monkeypatch.setattr(render.console, '_width', 10_000)

    assert pursuit_commands.cmd_next(explain=False, as_json=True, reroll=False) == 0
    drawn = json.loads(capsys.readouterr().out)
    assert pursuit_commands.cmd_next(explain=False, as_json=False, reroll=False) == 0
    assert pursuit_commands.cmd_next(explain=True, as_json=False, reroll=False) == 0

    shown = capsys.readouterr().out
    assert drawn['offered'] and set(drawn['offered']) <= set(drawn['state']['weights'])
    assert all(name in shown for name in drawn['state']['weights']), 'a pursuit missing from the screen reads as one with nothing to say'
    assert 'paused-thing' not in drawn['offered']
    assert 'chores\t' in pursuits.NAMES_CACHE.read_text()


def test_a_log_is_counted_by_the_list_drift_and_dormant_views_that_follow(api, capsys):
    write(api, 'pursuits', 'pursuits/pursuits.yml')

    assert pursuit_commands.cmd_log('chores', ['swept'], None, None, assume_yes=True, no_write=True) == 0
    assert 'Logged chores' in capsys.readouterr().out

    assert pursuit_commands.cmd_list(as_json=True) == 0
    assert json.loads(capsys.readouterr().out)['state']['done']['chores'] == 1.0
    assert pursuit_commands.cmd_list(as_json=False) == 0
    assert pursuit_commands.cmd_drift(None, None, as_json=False) == 0
    assert any(line.startswith('chores') for line in capsys.readouterr().out.splitlines())

    assert pursuit_commands.cmd_dormant() == 0
    printed = capsys.readouterr().out
    cold = [line for line in printed.splitlines() if 'never' in line]
    assert len(cold) > 1 and len({line.index('never') for line in cold}) == 1, 'the rows align'


def test_the_list_gives_a_tracked_pursuit_no_weight_and_no_due_date(api):
    register = 'pursuits:\n  build:\n    description: Engineering\n  chores:\n    weight: 25\n    cadence: 3d\n'
    api.put('/api/v1/documents/pursuits', json={'text': register}).raise_for_status()

    ran = CliRunner().invoke(cli_app, ['pursuits', 'list'])

    line = next(line for line in ran.output.splitlines() if line.strip().startswith('build'))
    assert 'tracked' in line
    assert 'due' not in line
    assert not re.search(r'\d', line.split('last')[0]), 'a weight was printed'


def test_a_log_asks_for_what_was_not_passed_and_minutes_only_of_a_timed_pursuit(api, store, monkeypatch):
    write(api, 'pursuits', 'pursuits/pursuits.yml')
    asked = answers(monkeypatch, 'read-library', '', '45', '')

    assert pursuit_commands.cmd_log(None, [], None, None, assume_yes=True, no_write=True) == 0
    assert pursuit_commands.cmd_log('chores', [], None, None, assume_yes=True, no_write=True) == 0
    assert pursuit_commands.cmd_log('read-library', ['trimmed'], None, 20, assume_yes=True, no_write=True) == 0

    assert asked == ['pursuit:', 'note, or Enter to skip:', 'minutes:', 'note, or Enter to skip:']
    entries = [(record['pursuit'], record['duration_minutes'], record['note']) for record in logged(store)]
    assert entries == [('read-library', 45, None), ('chores', None, None), ('read-library', 20, 'trimmed')]


@pytest.mark.parametrize('arguments', [['chores', '--minutes', '20'], ['read-library', '--minutes', '-5'], ['read-library']])
def test_a_log_the_run_cannot_honor_is_a_usage_error_and_records_nothing(api, store, monkeypatch, arguments):
    """Minutes on a counted pursuit, a negative duration, and a timed pursuit with nobody to ask its minutes."""
    write(api, 'pursuits', 'pursuits/pursuits.yml')
    at_terminal(monkeypatch, False)

    ran = CliRunner().invoke(cli_app, ['pursuits', 'log', *arguments, '--yes', '--no-write'])

    assert ran.exit_code == 2
    assert logged(store) == []


@pytest.mark.parametrize('present', [True, False])
def test_a_log_naming_no_pursuit_records_nothing_until_one_is_chosen(api, store, monkeypatch, present):
    write(api, 'pursuits', 'pursuits/pursuits.yml')
    answers(monkeypatch, '')
    at_terminal(monkeypatch, present)

    assert pursuit_commands.cmd_log(None, [], None, None, assume_yes=True, no_write=True) == 1
    assert logged(store) == []


@pytest.mark.parametrize(('present', 'answer', 'completed'), [(True, '2', ('512', 'Return hoodies')), (True, '', None), (False, '1', None)])
def test_a_log_among_several_choices_completes_only_the_one_you_named(store, monkeypatch, tmp_path, present, answer, completed):
    """Three tasks on screen, so which one got done is a question only you can answer.
    Enter, or nobody there to ask, completes nothing: a guess is a wrong completion in another app's data."""
    marker = write_through(store, monkeypatch, tmp_path, lambda: TASKS)
    answers(monkeypatch, answer)
    at_terminal(monkeypatch, present)

    assert pursuit_commands.cmd_log('chores', ['done'], None, None, assume_yes=True, no_write=False) == 0

    item = logged(store)[0]['item']
    found = (marker.read_text().strip(), item['label']) if marker.exists() else None
    assert found == completed
    assert marker.exists() or item is None, 'the pursuit happened, and no one item is claimed'


def test_a_log_resolves_again_past_a_draw_whose_read_failed(store, monkeypatch, tmp_path):
    """A cached failure carries no id, so the write-through would complete nothing and nothing on screen would say so."""
    down = [True]
    marker = write_through(store, monkeypatch, tmp_path, lambda: httpx2.Response(500, json={'message': 'down'}) if down[0] else TASKS[:1])
    assert pursuit_commands.cmd_next(explain=False, as_json=False, reroll=False) == 0
    down[0] = False

    assert pursuit_commands.cmd_log('chores', [], None, None, assume_yes=True, no_write=False) == 0

    assert marker.read_text().strip() == '501'
    assert logged(store)[0]['item']['label'] == 'Face Mud'


def test_a_log_whose_answer_was_lost_is_stored_once_and_one_never_taken_names_the_repair(store, monkeypatch, tmp_path, capsys):
    """on_log has already run by the time the entry is sent, so the entry is sent again rather than lost,
    and where it never lands the log says how to record it without running on_log a second time."""
    outcomes = ['lost']

    def around(request: httpx2.Request, forward: Forward) -> httpx2.Response:
        if request.method != 'POST' or not request.url.path.endswith('/journal/done') or not outcomes:
            return forward(request)
        if outcomes.pop(0) == 'lost':
            forward(request)
        return httpx2.Response(504)

    marker = write_through(store, monkeypatch, tmp_path, lambda: TASKS[:1], around)
    assert pursuit_commands.cmd_log('chores', [], None, None, assume_yes=True, no_write=False) == 0
    assert (len(logged(store)), marker.read_text().strip()) == (1, '501')

    outcomes.extend(['down'] * service.ATTEMPTS)
    with pytest.raises(service.NoAnswer):
        pursuit_commands.cmd_log('chores', [], None, None, assume_yes=True, no_write=False)
    assert len(logged(store)) == 1
    assert 'doit pursuits log chores --no-write' in capsys.readouterr().err


def test_a_skip_says_when_the_pursuit_returns_and_how_to_bring_it_back_sooner(api, store, capsys):
    write(api, 'pursuits', 'pursuits/pursuits.yml')

    assert pursuit_commands.cmd_skip('read-library', '3d') == 0
    printed = capsys.readouterr().out
    assert pursuit_commands.cmd_resume('read-library') == 0

    # The date comes from the record, so a run straddling midnight compares one clock reading with itself.
    expires = journal.parse_time(next(record for record in store.journal() if record['event'] == 'skip')['expires_at'])
    assert expires is not None and f'{expires:%d %b %Y}' in printed
    assert 'doit pursuits resume read-library' in printed
    assert 'Resumed read-library' in capsys.readouterr().out


def test_zeroing_the_whole_register_is_refused_unattended_and_one_pursuit_goes_through(api, store, monkeypatch):
    """Every accrued balance goes at once and nothing the tool ships puts one back, so only the whole register is confirmed."""
    write(api, 'pursuits', 'pursuits/pursuits.yml')
    at_terminal(monkeypatch, False)

    assert pursuit_commands.cmd_reset(None, assume_yes=False) == 1
    assert pursuit_commands.cmd_reset('chores', assume_yes=False) == 0

    resets = [record['pursuit'] for record in store.journal() if record['event'] == 'reset' and record.get('note') != pursuits.FIRST_SEEN]
    assert resets == ['chores']


def test_the_review_nudge_shows_what_is_due_once_per_interval(api, capsys):
    write(api, 'review', 'review/register.yml')

    assert review_commands.cmd_nudge() == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert any('never-done' in line for line in lines)
    assert any('echo overdue' in line for line in lines), 'the command a row carries is the point of the row'

    assert review_commands.cmd_nudge() == 0
    assert capsys.readouterr().out == '', 'the service holds when it last showed, so a second shell stays quiet'
    assert review_commands.NUDGE_INTERVAL.read_text().strip() == str(review.DEFAULT_NUDGE_MINUTES)


def test_marking_an_item_done_takes_it_off_the_due_list(api, capsys):
    write(api, 'review', 'review/register.yml')

    assert review_commands.cmd_done('never-done') == 0
    capsys.readouterr()
    assert review_commands.cmd_list(as_json=True) == 0

    rows = {row['id']: row for row in json.loads(capsys.readouterr().out)}
    assert rows['never-done']['due'] is False
    assert rows['overdue-item']['due'] is True


def test_a_forecast_taken_is_read_back_and_not_graded_before_its_window_closes(api, capsys):
    write(api, 'pursuits', 'pursuits/pursuits.yml')

    assert forecast_commands.cmd_run(as_json=True, budget=45) == 0
    assert json.loads(capsys.readouterr().out)['budget_minutes'] == 45
    assert forecast_commands.cmd_show('', as_json=False) == 0
    assert forecast_commands.cmd_list(as_json=False) == 0
    assert 'chores' in capsys.readouterr().out

    assert forecast_commands.cmd_trend(7, as_json=False) == 1
    captured = capsys.readouterr()
    assert captured.out == ''


def test_the_day_and_the_dashboard_render_without_any_source(api, capsys):
    write(api, 'pursuits', 'pursuits/pursuits.yml')
    write(api, 'review', 'review/register.yml')

    assert today_commands.cmd_today(as_json=False) == 0
    assert 'never-done' in capsys.readouterr().out
    assert happened_commands.cmd_happened(None, None, as_json=False) == 0
    capsys.readouterr()

    assert dashboard_commands.cmd_dashboard(['nonsense'], as_json=True) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)['lanes'] == [], 'what the dashboard says about itself stays off the document'
    assert 'No sources configured' in captured.err
    assert 'No lane named nonsense' in captured.err

    listed = CliRunner().invoke(sources_commands.app, ['list', '--json'])
    assert listed.exit_code == 0
    assert json.loads(listed.output) == []


def test_happened_with_only_an_end_reads_from_the_earliest_record_through_that_whole_day(api, capsys):
    assert happened_commands.cmd_happened(None, '2026-07-20', as_json=True) == 0
    document = json.loads(capsys.readouterr().out)

    assert dt.datetime.fromisoformat(document['since']) == happened.EARLIEST
    assert document['until'][:10] == '2026-07-21'
    assert document['asked'] == ['review', 'practice']
    assert [lane.name for lane in lanes.from_document({'lanes': document['groups']})] == ['review', 'practice']
