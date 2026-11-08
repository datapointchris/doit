import datetime as dt
import logging
import subprocess
import sys

import httpx2
import pytest
from conftest import NOW
from conftest import client_for
from conftest import served
from conftest import stand_in
from doit_api.readers import clickhouse
from doit_api.readers import life
from doit_api.readers import projects
from doit_api.readers.apps import ReadFailed
from doit_api.readers.declared_reads import Readers
from doit_api.readers.github import GitHub
from doit_api.settings import ClickHouseSettings
from doit_api.settings import GitHubSettings

from doit import sources

SOURCES = 'sources:\n  life:\n    adapter: life\n  prs:\n    adapter: prs\n'


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


# Answers in the shapes life's API publishes, one route per read the overview makes.
LIFE_ROUTES = {
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
    '/countdowns': lambda request: [
        {'id': 1, 'name': 'Passport', 'due_date': '2026-11-10'},
        {'id': 2, 'name': 'Gone', 'due_date': '2026-10-01'},
    ],
    '/events': lambda request: [{'id': 1, 'name': 'Concert', 'date': '2026-11-02T20:00:00-05:00', 'venue': 'Hall'}],
}


def test_the_overview_is_composed_from_one_read_per_section_as_the_cli_composes_it():
    document = life.overview(stand_in('life', LIFE_ROUTES), NOW)

    assert document['tasks']['total'] == 2
    assert [book['title'] for book in document['books']['reading']] == ['Dune']
    assert [book['title'] for book in document['books']['next_up']] == ['Emma']
    assert [article['title'] for article in document['articles']['unread']] == ['Older', 'Newer']
    assert document['articles']['read_last_30_days'] == 1
    assert [countdown['name'] for countdown in document['countdowns']['items']] == ['Passport']
    assert document['warnings'] == []

    forbidden = {**LIFE_ROUTES, '/books': lambda request: httpx2.Response(403, json={'error': 'Forbidden', 'message': 'scope missing'})}
    with pytest.raises(ReadFailed) as refused:
        life.overview(stand_in('life', forbidden), NOW)
    assert refused.value.refused


# Answers in the shapes projects' API publishes, for the two reads its focus makes.
PROJECTS_ROUTES = {
    '/project-items': lambda request: [
        item('b', project('second', 2)),
        item('a', project('first', 1), position=2),
        item('c', project('first', 1), position=1),
        item('d', project('paused', 0, status='paused')),
    ],
    '/project-items/blocked': lambda request: [item('a', project('first', 1))],
}


def test_the_focus_is_the_highest_ranked_active_project_split_into_next_and_blocked():
    document = projects.focus(stand_in('projects', PROJECTS_ROUTES))

    assert document['project']['id'] == 'first'
    assert [found['id'] for found in document['next']] == ['c']
    assert [found['id'] for found in document['blocked']] == ['a']
    assert (document['next_total'], document['blocked_total']) == (1, 1)


def test_a_section_whose_read_fails_degrades_only_its_lane_and_logs_the_source(store, caplog):
    routes = {
        **LIFE_ROUTES,
        '/books': lambda request: httpx2.Response(503, json={'error': 'Unavailable', 'message': 'the book store is restarting'}),
    }
    client = client_for(store, apps={'life': stand_in('life', routes)})
    client.put('/api/v1/documents/sources', json={'text': 'sources:\n  life:\n    adapter: life\n  learning:\n    adapter: learning\n'})

    with caplog.at_level(logging.ERROR, logger='doit_api'):
        lanes = {lane['name']: lane for lane in client.get('/api/v1/dashboard').json()['lanes']}

    assert lanes['tasks']['status'] == 'ok'
    assert lanes['books']['reason'] == 'books: life answered 503 to GET /books: the book store is restarting'
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


def test_the_standing_draw_retries_a_failed_resolve_and_drops_each_logged_pursuit_until_none_is_left(store):
    """The draw holds still for its window, so a recovered app and a log both have to show through it."""
    down = [True]
    answer = [{'name': 'Trim Dingo Nails', 'id': 4}]
    tasks = stand_in('life', {'/tasks': lambda request: httpx2.Response(500, json={'message': 'down'}) if down[0] else answer})
    client = client_for(store, apps={'life': tasks})
    register = 'pursuits:\n  chores:\n    weight: 5\n    cadence: 1d\n    resolve: {app: life, path: /tasks}\n    label: name\n    id: id\n'
    register += '  walk:\n    weight: 1\n    cadence: 1d\n'
    client.put('/api/v1/documents/pursuits', json={'text': register})

    first = client.get('/api/v1/next').json()
    down[0] = False
    again = client.get('/api/v1/next').json()
    logged = client.post('/api/v1/journal/done', json={'pursuit': 'chores'}).json()['entry']
    after = client.get('/api/v1/next').json()
    client.post('/api/v1/journal/done', json={'pursuit': 'walk'})
    spent = client.get('/api/v1/next').json()

    assert first['resolved']['chores']['error']
    assert again['draw_id'] == first['draw_id']
    assert again['resolved']['chores']['choices'][0]['label'] == 'Trim Dingo Nails'
    assert (logged['draw_id'], logged['was_offered'], logged['rank_offered']) == (
        first['draw_id'],
        True,
        first['offered'].index('chores') + 1,
    )
    assert (after['draw_id'], after['offered']) == (first['draw_id'], ['walk'])
    assert spent['draw_id'] != first['draw_id'], 'a draw with every pursuit logged is drawn again rather than shown empty'


def test_an_offer_count_whose_pursuit_left_the_register_is_stranded_until_it_ages_out(store):
    """Counts are keyed by name, so a rename strands counts that drift, which walks the register, never shows again."""
    moments = [NOW]
    client = client_for(store, clock=lambda: moments[0])
    client.put('/api/v1/documents/pursuits', json={'text': 'pursuits:\n  walk:\n    weight: 1\n    cadence: 1d\n'})
    client.get('/api/v1/next', params={'machine': 'desk'})
    client.put('/api/v1/documents/pursuits', json={'text': 'pursuits:\n  run:\n    weight: 1\n    cadence: 1d\n'})

    offers = client.get('/api/v1/pursuits/offers').json()
    assert (offers['counts'], offers['recent'], offers['stranded']) == ({'desk': {'walk': 1}}, {'desk': {'walk': 1}}, ['walk'])

    moments[0] = NOW + dt.timedelta(days=91)
    offers = client.get('/api/v1/pursuits/offers').json()
    assert (offers['counts'], offers['recent'], offers['stranded']) == ({'desk': {'walk': 1}}, {}, [])


def test_drift_counts_only_the_offers_inside_its_window(store):
    moments = [NOW - dt.timedelta(days=30)]
    client = client_for(store, clock=lambda: moments[0])
    client.put('/api/v1/documents/pursuits', json={'text': 'pursuits:\n  walk:\n    weight: 1\n    cadence: 1d\n'})
    client.get('/api/v1/next', params={'machine': 'desk'})
    moments[0] = NOW - dt.timedelta(days=1)
    client.get('/api/v1/next', params={'machine': 'laptop'})
    moments[0] = NOW

    def offered(**over: str) -> int:
        return client.get('/api/v1/pursuits/drift', params=over).json()['rows'][0]['offered']

    assert offered() == 2
    assert offered(since='7d') == 1
    assert offered(since='60d', until='7d') == 1


def test_only_happened_asks_for_closed_issues(store):
    asked = []

    def closed(request):
        asked.append(request.url.path)
        return [{'title': 'closed it', 'closed_ts': '2026-11-02T09:00:00-05:00', 'closed_by': 'ada@laptop'}]

    client = client_for(store, {'slate': stand_in('slate', {'/issues': closed})})
    client.put('/api/v1/documents/sources', json={'text': 'completions:\n  slate-issues:\n    adapter: slate-issues\n'}).raise_for_status()

    day = client.get('/api/v1/today').json()
    assert 'issues' not in [lane['name'] for lane in day['done']]
    assert asked == []

    held = client.get('/api/v1/happened').json()
    [issues] = [lane for lane in held['groups'] if lane['name'] == 'issues']
    assert [cell['text'] for cell in issues['grid']] == ['closed it']


def test_a_tracked_pursuit_cannot_be_passed_or_returned_to_and_a_paced_one_can(store):
    """A skip on a pursuit with no pace would read in drift as `passed 1` against a draw it never had."""
    client = client_for(store)
    register = 'pursuits:\n  build:\n    description: Engineering\n  chores:\n    weight: 25\n    cadence: 3d\n'
    client.put('/api/v1/documents/pursuits', json={'text': register}).raise_for_status()

    refused = [client.post(f'/api/v1/journal/{verb}', json={'pursuit': 'build'}).status_code for verb in ('skip', 'resume')]
    passed = client.post('/api/v1/journal/skip', json={'pursuit': 'chores'})

    assert (refused, passed.status_code) == ([409, 409], 201)
    assert [record['pursuit'] for record in store.journal() if record['event'] == 'skip'] == ['chores']


def test_every_adapter_doit_registers_has_a_read():
    readers = Readers({}, GitHub(GitHubSettings()), ClickHouseSettings())

    assert set(sources.ADAPTERS) == set(readers.adapter_reads)


def test_the_service_never_loads_the_client_that_calls_it():
    """The rule modules the service runs keep no command, so a change to the CLI's client never reaches the service."""
    loaded = subprocess.run(
        [sys.executable, '-c', 'import sys, doit_api.main; print(sorted(m for m in sys.modules if m.startswith("doit.service")))'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    assert loaded == '[]'


def test_the_desk_check_lane_names_only_the_machines_with_a_failing_check():
    """A lane where every row passes teaches you to stop reading it, so a clean machine is only counted."""
    rows = [
        {'machine': 'desk', 'check_key': 'packages', 'outcome': 'pass', 'summary': '', 'reported_ms': ''},
        {'machine': 'laptop', 'check_key': 'packages', 'outcome': 'fail', 'summary': '2 unconverged', 'reported_ms': ''},
    ]

    lane = clickhouse.desk_checks_lane(rows, NOW)

    assert lane.meta == '1 of 2 need a look'
    assert [(row.label, row.text) for row in lane.rows] == [('laptop', 'packages: 2 unconverged')]
