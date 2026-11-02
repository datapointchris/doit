"""One app the service reads, reached with a client-credentials token of the service's own.

Every read goes through `App.get`, so a refusal reads the same whichever app
gave it: one sentence naming the app, what it was asked, and what it answered.
That sentence becomes the reason on the lane the read was for, and the service
logs it at error as `source_failed` with the source's name, so an alert can be
filed on the line.

An app whose URL or secret the deployment has not set is not an error at
startup. Its reads refuse with the variable to set, so the service runs while a
credential is on its way and the lane says what it is waiting for.
"""

from __future__ import annotations

import os
from typing import Any

import httpx2
from pyclilogin import LoginError
from pyclilogin import Provider
from pyclilogin import ServiceTokenSource

from ..settings import USER_AGENT
from ..settings import AppSettings

# Each app serves its API under this prefix.
API_PREFIX = '/api/v1'

DEFAULT_TIMEOUT_SECONDS = 10.0


class ReadFailed(RuntimeError):
    """An app could not answer a read, for a reason worth one sentence on the lane."""

    def __init__(self, reason: str, *, timed_out: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.timed_out = timed_out


def local_zone() -> str:
    """The IANA zone the service computes in, for an app that reads calendar days in one.

    The deployment names it with `TZ`, which also sets the process's clock.
    """
    return os.environ.get('TZ', '').lstrip(':') or 'UTC'


def answer_message(response: httpx2.Response) -> str:
    """The sentence an error body carries, under whichever key the app used."""
    try:
        body = response.json()
    except ValueError:
        return response.text.strip().splitlines()[0] if response.text.strip() else ''
    if isinstance(body, dict):
        for key in ('message', 'detail', 'error'):
            if isinstance(body.get(key), str) and body[key]:
                return body[key]
    return ''


class App:
    """The app `settings` names, read as its service client."""

    def __init__(self, settings: AppSettings, issuer: str, client: httpx2.Client | None = None, tokens: Any = None) -> None:
        self.settings = settings
        self.name = settings.name
        self.client = client or httpx2.Client(headers={'User-Agent': USER_AGENT}, follow_redirects=False)
        self.tokens = tokens
        if self.tokens is None and not settings.missing:
            provider = Provider(issuer, settings.client_id, scopes=settings.scopes, user_agent=USER_AGENT)
            self.tokens = ServiceTokenSource(provider, settings.secret, repair=f'Check {settings.secret_variable}.')

    def get(self, path: str, query: dict[str, object] | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> object:
        """The JSON body of `GET <app>/api/v1<path>`, or a `ReadFailed` saying why there is none."""
        asked = f'GET {path}'
        if self.settings.missing:
            raise ReadFailed(f'{self.name} is not configured on the service: {self.settings.missing} unset')
        try:
            token = self.tokens.token()
        except LoginError as error:
            raise ReadFailed(f'{self.name}: {error}') from error
        params = {key: value_text(value) for key, value in (query or {}).items() if value is not None}
        try:
            response = self.client.get(
                f'{self.settings.url}{API_PREFIX}{path}',
                params=params,
                headers={'Authorization': f'Bearer {token.access_token}', 'Accept': 'application/json'},
                timeout=timeout,
            )
        except httpx2.TimeoutException as error:
            raise ReadFailed(f'{self.name} did not answer {asked} within {timeout:g}s', timed_out=True) from error
        except httpx2.HTTPError as error:
            raise ReadFailed(f'{self.name} could not be reached for {asked}: {error}') from error
        if response.status_code >= 400:
            said = answer_message(response)
            raise ReadFailed(f'{self.name} answered {response.status_code} to {asked}' + (f': {said}' if said else ''))
        try:
            return response.json()
        except ValueError as error:
            raise ReadFailed(f'{self.name} answered {asked} with something other than JSON') from error


def value_text(value: object) -> str:
    """A query value as the apps read it: booleans lowercase, everything else as written."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)
