import logging

import httpx2
import pytest
from conftest import NOW
from conftest import client_for
from conftest import served
from conftest import stand_in
from doit_api.readers import icb
from doit_api.readers.apps import ReadFailed
from doit_api.readers.declared_reads import UNREAD_ADAPTERS
from doit_api.readers.declared_reads import Readers
from doit_api.readers.github import GitHub
from doit_api.settings import ClickHouseSettings
from doit_api.settings import GitHubSettings

from doit import sources

SOURCES = 'sources:\n  icb:\n    adapter: icb\n  prs:\n    adapter: prs\n'


def project(id_, position, status='active'):
    return {'id': id_, 'name': id_.title(), 'status': status, 'position': position, 'created_at': '2026-01-01T00:00:00Z'}


def item(id_, *projects, position=0):
    return {
        'id': id_,
        'title': f'Item {id_}',
        'completed': False,
        'archived': False,
        'projects': list(projects),
        'memberships': [{'project_id': found['id'], 'position': position} for found in projects],
        'created_at': '2026-02-01T00:00:00Z',
    }


# Answers in the shapes icb's API publishes, one route per read the overview makes.
ICB_ROUTES = {
    '/tasks': lambda request: [
        {'id': 1, 'name': 'File taxes', 'category': 'Chores'},
        {'id': 2, 'name': 'Call the bank', 'category': 'Chores'},
    ],
    '/habits/day': lambda request: {'date': '2026-11-02', 'due': [{'id': 7, 'name': 'Stretch'}], 'completed': [], 'current_total': 1},
    '/books': lambda request: [
        {'id': 1, 'title': 'Dune', 'progress': 'reading'},
        {'id': 2, 'title': 'Emma', 'progress': 'unread'},
        {'id': 3, 'title': 'Ulysses', 'progress': 'read'},
    ],
    '/articles/current': lambda request: {'id': 10, 'title': 'Current', 'save_date': '2026-10-01T00:00:00Z'},
    '/articles': lambda request: (
        [{'id': 11, 'title': 'Read one', 'last_read_date': '2026-10-30T00:00:00Z'}]
        if request.url.params.get('archived') == 'true'
        else [
            {'id': 12, 'title': 'Newer', 'save_date': '2026-10-20T00:00:00Z'},
            {'id': 10, 'title': 'Current', 'save_date': '2026-10-01T00:00:00Z'},
            {'id': 13, 'title': 'Older', 'save_date': '2026-09-01T00:00:00Z'},
        ]
    ),
    '/project-items': lambda request: [
        item('b', project('second', 2)),
        item('a', project('first', 1), position=2),
        item('c', project('first', 1), position=1),
        item('d', project('paused', 0, status='paused')),
    ],
    '/project-items/blocked': lambda request: [item('a', project('first', 1))],
    '/countdowns': lambda request: [
        {'id': 1, 'name': 'Passport', 'due_date': '2026-11-10'},
        {'id': 2, 'name': 'Gone', 'due_date': '2026-10-01'},
    ],
    '/events': lambda request: [{'id': 1, 'name': 'Concert', 'date': '2026-11-02T20:00:00-05:00', 'venue': 'Hall'}],
}


def test_the_overview_is_composed_from_one_read_per_section_as_the_cli_composes_it():
    document = icb.overview(stand_in('icb', ICB_ROUTES), NOW)

    assert document['tasks']['total'] == 2
    assert [book['title'] for book in document['books']['reading']] == ['Dune']
    assert [book['title'] for book in document['books']['next_up']] == ['Emma']
    assert [article['title'] for article in document['articles']['unread']] == ['Older', 'Newer']
    assert document['articles']['read_last_30_days'] == 1
    assert document['project_items']['project']['id'] == 'first'
    assert [found['id'] for found in document['project_items']['next']] == ['c']
    assert [found['id'] for found in document['project_items']['blocked']] == ['a']
    assert [countdown['name'] for countdown in document['countdowns']['items']] == ['Passport']
    assert document['warnings'] == []

    forbidden = {**ICB_ROUTES, '/books': lambda request: httpx2.Response(403, json={'error': 'Forbidden', 'message': 'scope missing'})}
    with pytest.raises(ReadFailed) as refused:
        icb.overview(stand_in('icb', forbidden), NOW)
    assert refused.value.refused


def test_a_section_whose_read_fails_degrades_only_its_lane_and_logs_the_source(store, caplog):
    routes = {
        **ICB_ROUTES,
        '/books': lambda request: httpx2.Response(503, json={'error': 'Unavailable', 'message': 'the book store is restarting'}),
    }
    client = client_for(store, apps={'icb': stand_in('icb', routes)})
    client.put('/api/v1/documents/sources', json={'text': 'sources:\n  icb:\n    adapter: icb\n  learning:\n    adapter: learning\n'})

    with caplog.at_level(logging.ERROR, logger='doit_api'):
        lanes = {lane['name']: lane for lane in client.get('/api/v1/dashboard').json()['lanes']}

    assert lanes['tasks']['status'] == 'ok'
    assert lanes['books']['reason'] == 'books: icb answered 503 to GET /books: the book store is restarting'
    assert lanes['learning']['status'] == 'unavailable'
    assert (
        lanes['learning']['reason']
        == 'learning is not configured on the service: DOIT_API_LEARNING_URL, DOIT_API_LEARNING_CLIENT_SECRET unset'
    )
    failed = [(record.message, record.source) for record in caplog.records if record.message == 'source_failed']
    assert failed == [('source_failed', 'learning')]


def github_routes(calls):
    def counted(answer):
        def route(request):
            calls.append(request.url.path)
            return answer(request)

        return route

    item = {
        'repository_url': 'https://api.github.com/repos/someone/tool',
        'number': 4,
        'title': 'Add a flag',
        'user': {'login': 'renovate[bot]', 'type': 'Bot'},
        'draft': True,
        'created_at': '2026-10-26T12:00:00Z',
        'html_url': 'https://github.com/someone/tool/pull/4',
    }
    return {
        '/app/installations': counted(lambda request: [{'id': 99, 'account': {'login': 'someone'}}]),
        '/app/installations/99/access_tokens': counted(lambda request: {'token': 'installation-token'}),
        '/search/issues': counted(lambda request: {'total_count': 1, 'items': [item]}),
    }


def test_open_pull_requests_come_from_one_search_per_installation_and_are_kept_five_minutes(store, tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    calls: list[str] = []
    clock = iter([1000.0, 1000.0, 1100.0, 1100.0])
    github = GitHub(GitHubSettings('Iv1.client', pem), client=served(github_routes(calls)), clock=lambda: next(clock))
    client = client_for(store, github=github)
    client.put('/api/v1/documents/sources', json={'text': SOURCES})

    first = {lane['name']: lane for lane in client.get('/api/v1/dashboard').json()['lanes']}['prs']
    client.get('/api/v1/dashboard')

    assert first['rows'] == [{'label': 'tool', 'text': 'Add a flag (draft)', 'note': '7d', 'urgency': 'overdue', 'handle': 'prs'}]
    assert calls == ['/app/installations', '/app/installations/99/access_tokens', '/search/issues']


def test_a_logged_pursuit_counts_toward_its_standing_and_names_who_logged_it(store):
    client = client_for(store)
    register = 'pursuits:\n  walk:\n    description: Walk\n    cadence: 1d\n    weight: 1\n'
    register += '  run:\n    description: Run\n    weekly_minutes: 60\n    weight: 1\n'
    client.put('/api/v1/documents/pursuits', json={'text': register})

    logged = client.post('/api/v1/journal/done', json={'pursuit': 'wa', 'note': 'around the block', 'machine': 'Desk.local'})
    unmeasured = client.post('/api/v1/journal/done', json={'pursuit': 'run'})
    ahead = client.post('/api/v1/journal/done', json={'pursuit': 'walk', 'occurred_at': '2030-01-01T00:00:00Z'})

    assert logged.status_code == 201
    entry = logged.json()['entry']
    assert (entry['pursuit'], entry['machine'], entry['actor'], entry['note']) == ('walk', 'desk', 'chris', 'around the block')
    assert client.get('/api/v1/pursuits').json()['state']['done']['walk'] == 1
    assert (unmeasured.status_code, ahead.status_code) == (422, 422)
    assert [record['event'] for record in store.journal()].count('done') == 1


def test_every_adapter_doit_registers_has_a_read_or_a_reason():
    readers = Readers({}, GitHub(GitHubSettings()), ClickHouseSettings())

    assert set(sources.ADAPTERS) == set(readers.adapter_reads) | set(UNREAD_ADAPTERS)
