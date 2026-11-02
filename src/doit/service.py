"""The one module that makes a request to doit's service.

`~/.config/doit/config.toml` names the service, its issuer and this machine's
client, and `doit auth login <url>` fills it in. With `$DOIT_CLIENT_SECRET` set
the CLI authenticates as that client with no login, as a scheduled job does.

A refusal reaches a command as `ServiceFailed`, carrying the service's own
sentence, or the login that would fix a refused token.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import httpx2
from pyclilogin import DEFAULT_SCOPES
from pyclilogin import Product
from pyclilogin import TokenRefused

from doit import actor
from doit import paths

USER_AGENT = 'doit-cli (+https://github.com/datapointchris/doit)'
PREFIX = '/api/v1'

# A view that asks every app waits on the slowest of them, which the service caps
# below this.
TIMEOUT = httpx2.Timeout(60.0, connect=10.0)
IMPORT_TIMEOUT = httpx2.Timeout(300.0, connect=10.0)

# The keychain service is a deployed identifier: renaming it orphans every stored token.
LOGIN = Product(
    name='doit',
    keyring_service='doit-cli',
    scopes=DEFAULT_SCOPES,
    secret_variable='DOIT_CLIENT_SECRET',
    user_agent=USER_AGENT,
    product_keys=frozenset(),
)


class ServiceFailed(Exception):
    """The service refused a request, or did not answer it."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def repair() -> str:
    """What fixes a refused token: a login for a person, the client id and secret for a service."""
    return LOGIN.service_repair() if LOGIN.is_service() else 'Run `doit auth login`.'


class BearerAuth(httpx2.Auth):
    """Sends this machine's access token, refreshing it first when it has lapsed."""

    def __init__(self, source: Any) -> None:
        self.source = source

    def auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response]:
        try:
            token = self.source.token()
        except TokenRefused as error:
            raise ServiceFailed(f"the identity provider no longer honors this machine's login ({error.error}). {repair()}") from error
        request.headers['Authorization'] = f'Bearer {token.access_token}'
        yield request


def said(response: httpx2.Response) -> str:
    """The service's sentence for a failure, which every refusal carries as `message`."""
    try:
        body = response.json()
    except ValueError:
        return ''
    return str(body.get('message') or '') if isinstance(body, dict) else ''


class Service:
    """One authenticated client for the service at `base`."""

    def __init__(self, base: str, source: Any, transport: httpx2.BaseTransport | None = None) -> None:
        self.base = base.rstrip('/')
        self.http = httpx2.Client(
            base_url=self.base + PREFIX,
            auth=BearerAuth(source),
            timeout=TIMEOUT,
            headers={'User-Agent': USER_AGENT},
            follow_redirects=False,
            transport=transport,
        )

    def send(self, method: str, path: str, **keywords: Any) -> Any:
        try:
            response = self.http.request(method, path, **keywords)
        except httpx2.HTTPError as error:
            raise ServiceFailed(f'cannot reach the doit service at {self.base}: {error}') from error
        if response.is_success:
            return response.json()
        if response.status_code == 401:
            raise ServiceFailed(f"the doit service refused this machine's token. {repair()}", 401)
        sentence = said(response)
        if sentence:
            raise ServiceFailed(sentence, response.status_code)
        raise ServiceFailed(f'the doit service at {self.base} answered HTTP {response.status_code}', response.status_code)

    def get(self, path: str, **query: Any) -> Any:
        return self.send('GET', path, params={key: value for key, value in query.items() if value is not None})

    def post(self, path: str, body: dict, timeout: httpx2.Timeout = TIMEOUT) -> Any:
        return self.send('POST', path, json=body, timeout=timeout)

    def put(self, path: str, body: dict) -> Any:
        return self.send('PUT', path, json=body)


def client() -> Service:
    """The service this machine's config file and login name."""
    deployment = LOGIN.load()
    return Service(deployment.api.value, LOGIN.token_source(deployment))


def signed(body: dict) -> dict:
    """A journal write naming this machine and whoever is running the command."""
    return {**body, 'machine': paths.machine_name(), 'actor': actor.identity()}
