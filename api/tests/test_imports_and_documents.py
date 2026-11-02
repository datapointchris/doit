import json

from conftest import client_for

PURSUITS = """\
forecast:
  budget_minutes: 120

pursuits:
  # Errands from the task app.
  tasks:
    description: Clear one task
    cadence: 2d
    weight: 3
    resolve: icb tasks list --limit 3 --json
    label: name
    id: id
  study:
    description: Work the plan
    weekly_minutes: 240
    weight: 2
    resolve: learning plan -n 3 --json
    items: sequence
    label: title
  walk:
    description: Walk
    cadence: 1d
    weight: 1
    resolve: weather today --json
    label: summary
"""

REGISTER = 'items:\n  backups:\n    description: Check the backups restore\n    cadence: 1mo\n'

JOURNAL = [
    {'id': '0190a000-0000-7000-8000-000000000001', 'pursuit': 'tasks', 'event': 'done', 'occurred_at': '2026-11-01T09:00:00-05:00'},
    {'id': '0190a000-0000-7000-8000-000000000002', 'pursuit': 'walk', 'event': 'done', 'occurred_at': '2026-11-01T18:00:00-05:00'},
]


def desk_files(journal=JOURNAL, pursuits=PURSUITS):
    return [
        {'path': 'config/pursuits.yml', 'text': pursuits},
        {'path': 'config/register.yml', 'text': REGISTER},
        {'path': 'state/next-log-desk.jsonl', 'text': ''.join(json.dumps(record) + '\n' for record in journal)},
        {'path': 'state/next-offers-desk.json', 'text': json.dumps({'tasks': 4, 'walk': 2})},
        {'path': 'state/review-state.json', 'text': json.dumps({'backups': '2026-10-20'})},
        {'path': 'state/autoupdate-desk.json', 'text': '{}'},
    ]


def test_an_import_run_twice_adds_only_what_the_first_lacked(store):
    client = client_for(store)

    first = client.post('/api/v1/imports', json={'files': desk_files()}).json()
    newer = [
        *JOURNAL,
        {'id': '0190a000-0000-7000-8000-000000000003', 'pursuit': 'tasks', 'event': 'done', 'occurred_at': '2026-11-02T08:00:00-05:00'},
    ]
    second = client.post('/api/v1/imports', json={'files': desk_files(journal=newer)}).json()

    assert (first['journal']['added'], second['journal']['added']) == (2, 1)
    assert (first['review']['added'], second['review']['added']) == (1, 0)
    assert second['offers']['raised'] == 0
    assert not second['documents']['pursuits']['changed']
    assert second['skipped'] == [
        {'path': 'state/autoupdate-desk.json', 'reason': "the installed CLI's own update stamp, which stays on each desk"}
    ]
    assert len(client.get('/api/v1/journal').json()) == 3


def test_an_import_rewrites_each_known_command_as_a_read_and_keeps_the_comments(store):
    client = client_for(store)

    summary = client.post('/api/v1/imports', json={'files': desk_files()}).json()
    text = client.get('/api/v1/documents/pursuits').json()['text']

    assert '# Errands from the task app.' in text
    assert 'resolve:\n      app: icb\n      path: /tasks\n      query: {limit: 3}' in text
    assert 'resolve:\n      app: learning\n      path: /overview\n    items: plan_head' in text
    assert summary['untranslated'] == [{'pursuit': 'walk', 'field': 'resolve', 'command': 'weather today --json'}]


def test_an_import_keeps_a_document_edited_on_the_service(store):
    client = client_for(store)
    client.post('/api/v1/imports', json={'files': desk_files()})
    edited = client.get('/api/v1/documents/pursuits').json()['text'].replace('weight: 3', 'weight: 5')
    client.put('/api/v1/documents/pursuits', json={'text': edited})

    again = client.post('/api/v1/imports', json={'files': desk_files()}).json()

    assert again['documents']['pursuits'] == {'kept': 'edited on the service by chris since it was imported'}
    assert 'weight: 5' in client.get('/api/v1/documents/pursuits').json()['text']


def test_an_edit_keeps_the_row_it_replaced_and_a_refused_document_writes_nothing(store):
    client = client_for(store)
    client.post('/api/v1/imports', json={'files': desk_files()})
    text = client.get('/api/v1/documents/pursuits').json()['text']

    client.put('/api/v1/documents/pursuits', json={'text': text.replace('weight: 3', 'weight: 5')})
    refused = client.put('/api/v1/documents/pursuits', json={'text': text.replace('cadence: 2d', 'cadence: often')})

    versions = client.get('/api/v1/history/pursuits', params={'key': 'tasks'}).json()
    assert [(json.loads(row['settings'])['weight'], row['valid_to'] is None) for row in versions] == [(3, False), (5, True)]
    assert refused.status_code == 422
    assert refused.json()['message'] == 'pursuits: tasks: cadence is a whole number of days, like 3d or 9d'
    assert len(client.get('/api/v1/documents/pursuits/versions').json()) == 2
