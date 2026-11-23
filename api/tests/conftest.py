import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from fastapi.testclient import TestClient
from pyapiauth import Caller
from pyapiauth import Rejected

from doit import pursuits
from doit import service
from doit.service_commands import review as review_commands
from doit_api.app import create_app
from doit_api.computed_views import Views
from doit_api.readers.apps import App
from doit_api.readers.declared_reads import Readers
from doit_api.readers.github import GitHub
from doit_api.settings import AppSettings
from doit_api.settings import ClickHouseSettings
from doit_api.settings import GitHubSettings
from doit_api.settings import Settings
from doit_api.store import Store

NOW = dt.datetime(2026, 11, 2, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=-5)))
TOKEN = 'person-token'


class Verifier:
    """Knows one person's token."""

    def verify(self, token):
        if token != TOKEN:
            raise Rejected('unknown token')
        return Caller(subject='person-1', client_id='doit-cli-desk', name='chris')


class Tokens:
    def token(self):
        return SimpleNamespace(access_token='service-token')


Routes = dict[str, Callable[[httpx2.Request], object]]


def served(routes: Routes) -> httpx2.Client:
    """A client whose requests are answered by `routes`, keyed by path; an unknown path answers 404."""

    def answer(request: httpx2.Request) -> httpx2.Response:
        route = routes.get(request.url.path)
        if route is None:
            return httpx2.Response(404, json={'error': 'Not Found', 'message': f'no route {request.url.path}'})
        found = route(request)
        return found if isinstance(found, httpx2.Response) else httpx2.Response(200, content=json.dumps(found))

    return httpx2.Client(transport=httpx2.MockTransport(answer))


def stand_in(name: str, routes: Routes) -> App:
    """An app served by `routes` under its `/api/v1` prefix."""
    settings = AppSettings(name, f'https://{name}.test', f'{name}-svc-doit', 'secret', (), 'URL', 'SECRET')
    return App(settings, 'https://id.test', client=served({f'/api/v1{path}': route for path, route in routes.items()}), tokens=Tokens())


def unconfigured(name: str) -> App:
    return App(
        AppSettings(name, '', f'{name}-svc-doit', '', (), f'DOIT_API_{name.upper()}_URL', f'DOIT_API_{name.upper()}_CLIENT_SECRET'), 'x'
    )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / 'doit.db')


def client_for(
    store: Store,
    apps: dict[str, App] | None = None,
    github: GitHub | None = None,
    clock: Callable[[], dt.datetime] | None = lambda: NOW,
) -> TestClient:
    """The service over `store`, at `NOW` unless handed another clock; None is the real one."""
    apps = {name: unconfigured(name) for name in ('life', 'learning', 'meso', 'nomad', 'slate')} | (apps or {})
    readers = Readers(apps, github or GitHub(GitHubSettings()), ClickHouseSettings())
    views = Views(store, readers, clock=clock)
    settings = Settings(issuer='https://id.test', public_url='https://doit.test', database_path=store.path)
    client = TestClient(create_app(settings=settings, store=store, views=views, verifier=Verifier()))
    client.headers['Authorization'] = f'Bearer {TOKEN}'
    return client


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


@pytest.fixture
def api(store, monkeypatch, tmp_path) -> TestClient:
    return connected(store, monkeypatch, tmp_path)
