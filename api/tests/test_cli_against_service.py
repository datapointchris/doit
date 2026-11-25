"""What the service answers the CLI, in process.

The Go doit runs every verb that asks the service, and cli/internal/contract runs
each against this commit's service. These pin what the service does on its own:
what each read carries, what a log's check resolves, and what a taken-back
completion leaves in history.
"""

import datetime as dt
from collections.abc import Callable
from pathlib import Path

import httpx2
from conftest import client_for
from conftest import stand_in
from fastapi.testclient import TestClient

from doit import actor

FIXTURES = Path(__file__).resolve().parents[2] / 'tests' / 'fixtures'
TASKS = [{'name': 'Water the plants', 'id': 501}, {'name': 'Return the parcel', 'id': 512}, {'name': 'Sort the mail', 'id': 474}]


def write(client: TestClient, name: str, fixture: str) -> None:
    client.put(f'/api/v1/documents/{name}', json={'text': (FIXTURES / fixture).read_text()}).raise_for_status()


def tasks_from(store, answer: Callable[[], object]) -> TestClient:
    """`chores` resolved from a tasks app giving ``answer()``."""
    client = client_for(store, {'life': stand_in('life', {'/tasks': lambda request: answer()})}, clock=None)
    register = (
        'pursuits:\n  chores:\n    weight: 25\n    cadence: 3d\n    resolve: {app: life, path: /tasks}\n    label: name\n    id: id\n'
    )
    client.put('/api/v1/documents/pursuits', json={'text': register + '    on_log: tick {id}\n'}).raise_for_status()
    return client


def test_next_answers_a_row_for_every_active_pursuit_with_the_offered_ones_first(api):
    write(api, 'pursuits', 'pursuits/pursuits.yml')

    drawn = api.get('/api/v1/next', params={'machine': 'desk'}).json()

    assert drawn['offered'] and set(drawn['offered']) <= set(drawn['state']['weights'])
    offered_rows = [row['name'] for row in drawn['offer_rows'] if row['offered']]
    assert offered_rows == drawn['offered'] == [row['name'] for row in drawn['offer_rows']][: len(offered_rows)]
    assert {row['name'] for row in drawn['offer_rows']} == {row['name'] for row in drawn['explain_rows']} == set(drawn['state']['active'])
    assert 'paused-thing' not in drawn['offered']


def test_a_reason_reaches_every_read_of_a_pursuit_and_a_tracked_one_has_no_weight_or_due_date(api):
    reason = 'It keeps the [house] standing, per chores.md'
    register = f'pursuits:\n  build:\n    description: Engineering\n  chores:\n    weight: 25\n    cadence: 3d\n    reason: {reason}\n'
    api.put('/api/v1/documents/pursuits', json={'text': register}).raise_for_status()

    rows = {row['name']: row for row in api.get('/api/v1/pursuits').json()['rows']}
    assert (rows['chores']['reason'], rows['build']['reason']) == (reason, '')
    assert (rows['build']['paced'], rows['build']['weight'], rows['build']['due']) == (False, '', '')
    drift = api.get('/api/v1/pursuits/drift').json()
    assert [row['reason'] for row in drift['printed_rows'] if row['pursuit'] == 'chores'] == [reason]
    assert [row['reason'] for row in api.get('/api/v1/pursuits/dormant').json()['dormant'] if row['pursuit'] == 'chores'] == [reason]
    explained = api.get('/api/v1/next', params={'machine': 'desk'}).json()['explain_rows']
    assert [row['reason'] for row in explained if row['name'] == 'chores'] == [reason]


def test_a_log_check_resolves_again_past_a_draw_whose_read_failed(store):
    """A cached failure carries no id, so on_log would complete nothing and nothing on screen would say so."""
    down = [True]
    client = tasks_from(store, lambda: httpx2.Response(500, json={'message': 'down'}) if down[0] else TASKS[:1])
    client.get('/api/v1/next', params={'machine': actor.machine_name()}).raise_for_status()
    down[0] = False

    check = client.post('/api/v1/journal/done', json={'pursuit': 'chores', 'check': True}).json()

    assert (check['chosen']['id'], check['chosen']['label'], check['on_log']) == ('501', 'Water the plants', 'tick {id}')
    assert [record for record in client.get('/api/v1/journal').json() if record['event'] == 'done'] == [], 'a check logs nothing'


def test_a_completion_taken_back_leaves_the_entry_as_it_read_and_stays_in_history(api):
    write(api, 'review', 'review/register.yml')
    today = dt.date.today().isoformat()
    for day in ('2026-07-17', today):
        api.post('/api/v1/review/never-done/completions', json={'day': day}).raise_for_status()

    undone = api.delete(f'/api/v1/review/never-done/completions/{today}').json()
    assert undone['status']['last'] == '2026-07-17'
    assert undone['status']['status'].startswith('overdue')
    undone = api.delete('/api/v1/review/never-done/completions/2026-07-17').json()
    assert (undone['status']['last'], undone['status']['status']) == (None, 'never done')

    history = api.get('/api/v1/history/review_completions', params={'key': 'never-done'}).json()
    assert sorted(row['done_on'] for row in history) == ['2026-07-17', today]
    assert all(row['valid_to'] for row in history)


def test_trips_and_feedback_reach_their_lanes_and_a_failed_source_says_so(store):
    overview = {
        'schema_version': 1,
        'as_of_month': '2026-07',
        'scheduled': {
            'items': [{'id': 'trip-1', 'name': 'Portland', 'notes': '', 'planned_date': '2026-04', 'past_its_month': True}],
            'total': 1,
            'past_its_month_total': 1,
        },
        'warnings': [],
    }
    feedback = [{'id': 'f-1', 'body': 'The rest timer is too small', 'context_path': '/sessions/4', 'created_at': '2026-07-20T12:00:00Z'}]
    apps = {
        'nomad': stand_in('nomad', {'/overview': lambda request: overview}),
        'meso': stand_in('meso', {'/feedback': lambda request: feedback if request.url.params['status'] == 'open' else []}),
    }
    client = client_for(store, apps, clock=None)
    document = 'sources:\n  life:\n    adapter: life\n  nomad:\n    adapter: nomad\n  meso-feedback:\n    adapter: meso-feedback\n'
    client.put('/api/v1/documents/sources', json={'text': document}).raise_for_status()

    board = {lane['name']: lane for lane in client.get('/api/v1/dashboard').json()['lanes']}
    assert '3mo owed' in [row['label'] for row in board['upcoming']['rows']]
    assert 'life is not configured' in board['upcoming']['reason'], 'an life that cannot answer costs its rows, not the trips'
    assert (board['meso - feedback']['title'], board['meso - feedback']['meta']) == ('MESO - FEEDBACK', '1 open')
    assert board['meso - feedback']['rows'][0]['text'].startswith('The rest timer')

    upcoming = next(lane for lane in client.get('/api/v1/today').json()['due'] if lane['name'] == 'upcoming')
    assert 'Portland' in [row['label'] for row in upcoming['rows']] + [row['text'] for row in upcoming['rows']]
    assert 'life' in (upcoming['reason'] or ''), 'the due list says which source it could not ask'

    answers = client.get('/api/v1/sources').json()['answers']
    assert {row['id']: row['failure'] for row in answers} == {'life': 'failed', 'nomad': None, 'meso-feedback': None}
