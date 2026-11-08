import json

from conftest import client_for

PURSUITS = """\
forecast:
  budget_minutes: 120

pursuits:
  tasks:
    description: Clear one task
    cadence: 2d
    weight: 3
    resolve: {app: life, path: /tasks, query: {limit: 3}}
    label: name
    id: id
"""


def test_an_edit_keeps_the_row_it_replaced_and_a_refused_document_writes_nothing(store):
    client = client_for(store)
    client.put('/api/v1/documents/pursuits', json={'text': PURSUITS}).raise_for_status()

    client.put('/api/v1/documents/pursuits', json={'text': PURSUITS.replace('weight: 3', 'weight: 5')})
    refused = client.put('/api/v1/documents/pursuits', json={'text': PURSUITS.replace('cadence: 2d', 'cadence: often')})

    versions = client.get('/api/v1/history/pursuits', params={'key': 'tasks'}).json()
    assert [(json.loads(row['settings'])['weight'], row['valid_to'] is None) for row in versions] == [(3, False), (5, True)]
    assert refused.status_code == 422
    assert len(client.get('/api/v1/documents/pursuits/versions').json()) == 2
