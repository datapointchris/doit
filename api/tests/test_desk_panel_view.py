"""The desk panel view, read from the app on a temporary store seeded from recorded documents and life's recorded answers."""

import datetime as dt
import json
from pathlib import Path

from conftest import NOW
from conftest import client_for
from conftest import stand_in
from jsonschema import Draft7Validator

from doit import journal
from doit import pursuits
from doit.lanes import CompletionKind
from doit_api.computed_views import NOTHING_TO_DRAW

REPO = Path(__file__).resolve().parents[2]
RECORDED = REPO / 'tests' / 'fixtures' / 'desk-panel-view'
SCHEMA = json.loads((REPO / 'schemas' / 'desk-panel-view.schema.json').read_text())


def recorded(name: str):
    return json.loads((RECORDED / name).read_text())


def life():
    """life's API answering from what it recorded: fourteen habits, two done and one with a checklist, three tasks and two events."""
    return stand_in(
        'life',
        {
            '/habits/day': lambda request: recorded('life-habits-day.json'),
            '/habits/completed': lambda request: recorded('life-habits-completed.json'),
            '/tasks': lambda request: recorded('life-tasks.json'),
            '/events': lambda request: recorded('life-events.json'),
            '/books': lambda request: [],
            '/articles': lambda request: [],
            '/articles/current': lambda request: None,
            '/countdowns': lambda request: [],
        },
    )


def seeded(store):
    """The service at `NOW`, ten days after it first read the recorded documents, with `read` logged long before at 45, 30 and 45."""
    long_ago = NOW - dt.timedelta(days=60)
    logged = [
        pursuits.new_record(
            journal.Event.DONE, 'read', None, {'occurred_at': (long_ago + dt.timedelta(days=day)).isoformat(), 'duration_minutes': amount}
        )
        for day, amount in enumerate((45, 30, 45))
    ]
    store.append(logged, 'person-1', long_ago)
    first = client_for(store, {'life': life()}, clock=lambda: NOW - dt.timedelta(days=10))
    for document in ('pursuits', 'review', 'sources'):
        first.put(f'/api/v1/documents/{document}', json={'text': (RECORDED / f'{document}.yml').read_text()}).raise_for_status()
    first.get('/api/v1/pursuits').raise_for_status()
    return client_for(store, {'life': life()})


def panel(client, **query) -> dict:
    answered = client.get('/api/v1/desk-panel-view', params=query)
    assert answered.status_code == 200, answered.text
    Draft7Validator(SCHEMA).validate(answered.json())
    return answered.json()


def test_the_desk_panel_view_carries_each_part_from_a_recorded_store(store):
    client = seeded(store)
    view = panel(client, draw='true', machine='desk')

    suggestion = view['suggestion']
    assert (suggestion['pursuit'], suggestion['unit'], suggestion['key']) == ('tasks', 'checkoffs', None)
    assert [(choice['key'], choice['title'], choice['by']) for choice in suggestion['choices']] == [
        ('pursuit:tasks:532', 'File the receipts', 'Paperwork'),
        ('pursuit:tasks:533', 'Return the library books', 'Errands'),
        ('pursuit:tasks:534', 'Descale the kettle', 'Home'),
    ]

    totals = view['totals']
    assert totals == client.get('/api/v1/today').json()['quota'], 'the figures are the ones `doit today` prints'
    assert totals['minutes']['behind'] > 0 and totals['checkoffs']['behind'] == 10, 'behind is said in each unit'
    assert [(entry['name'], entry['done'], entry['total']) for entry in totals['checklists']] == [
        ('habits', 2, 14),
        ('review', 0, 4),
        ('practice', 0, 1),
    ], 'the habits meter counts all fourteen habits'

    rows = {row['name']: row for row in view['pursuits']['rows']}
    assert [(name, row['unit'], row['owed'], row['key'], row['usual_minutes']) for name, row in rows.items()] == [
        ('tasks', 'checkoffs', True, None, None),
        ('read', 'minutes', True, 'pursuit:read', 45),
    ]
    assert [item['key'] for item in rows['tasks']['items']] == ['pursuit:tasks:532', 'pursuit:tasks:533', 'pursuit:tasks:534']
    assert (rows['read']['need'], rows['read']['done']) == (totals['pursuits'][1]['need'], 0)

    habits = view['habits']['rows']
    assert len(habits) == 14
    assert [(habit['id'], habit['key']) for habit in habits if habit['done']] == [(2, None), (5, None)]
    exercise = next(habit for habit in habits if habit['id'] == 7)
    assert (exercise['key'], exercise['category']) == ('habit:7', 'Health')
    assert [(item['id'], item['name'], item['target'], item['ticks']) for item in exercise['items']] == [
        (1, 'Squat', 5, 2),
        (2, 'Lunge', 5, 0),
    ]

    lanes = {lane['name']: lane for lane in view['lanes']}
    assert set(lanes) == {'review', 'practice', 'upcoming'}
    assert ([row['key'] for row in lanes['review']['rows']], lanes['review']['total']) == (
        ['review:prune-images', 'review:check-disks', 'review:rotate-keys'],
        4,
    )
    assert [row['key'] for row in lanes['practice']['rows']] == ['practice:lab-regex']
    assert lanes['upcoming']['status'] == 'ok' and all(row['key'] is None for row in lanes['upcoming']['rows'])
    assert SCHEMA['definitions']['completion_kind']['enum'] == list(CompletionKind), 'the schema and the service name one set of kinds'


def test_a_read_without_draw_saves_no_draw_and_bumps_no_offer_and_nothing_to_draw_answers_a_null_suggestion_with_its_reason(store):
    client = seeded(store)

    quiet = panel(client)
    assert quiet['suggestion'] is None
    assert (store.newest_draw(), store.offer_counts()) == (None, {})

    drawn = panel(client, draw='true', machine='desk')['suggestion']
    assert store.offer_counts() == {'desk': {'read': 1, 'tasks': 1}}

    standing = panel(client, machine='desk')['suggestion']
    assert standing['draw_id'] == drawn['draw_id'] == store.newest_draw()['draw_id'], 'a read without draw shows the standing draw'
    assert store.offer_counts() == {'desk': {'read': 1, 'tasks': 1}}, 'and counts no offer'

    paused = (RECORDED / 'pursuits.yml').read_text().replace('    weight: 40\n', '    weight: 40\n    paused: true\n')
    paused = paused.replace('    weight: 30\n', '    weight: 30\n    paused: true\n')
    client.put('/api/v1/documents/pursuits', json={'text': paused}).raise_for_status()
    nothing = panel(client, draw='true', machine='desk')
    assert (nothing['suggestion'], nothing['suggestion_reason']) == (None, NOTHING_TO_DRAW)
    assert nothing['pursuits']['status'] == 'ok' and nothing['habits']['status'] == 'ok', 'the rest of the panel still answers'
