"""One app the service reads, reached with a client-credentials token of the service's own.

Every read goes through `App.get`, so a refusal reads the same whichever app
gave it: one sentence naming the app, what it was asked, and what it answered.
That sentence becomes the reason on the lane the read was for, and the service
logs it at error as `source_failed` with the source's name, so an alert can be
filed on the line.

A write goes through `App.post`, and its refusal is the same sentence, said to
the person whose request the service passed on.

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


class AppFailed(RuntimeError):
    """An app could not answer a request, for a reason worth one sentence."""

    def __init__(
        self, reason: str, *, refused: bool = False, status: int | None = None, transient: bool = False, outcome_unknown: bool = False
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        # The app turned the service's credential away: a 401, a 403, or a token it would not mint.
        self.refused = refused
        # The app's HTTP status, where it answered at all.
        self.status = status
        # The app was down, slow or broken, which clears with nobody acting.
        self.transient = transient
        # The request reached the app and its answer was lost, so a write may have been recorded.
        self.outcome_unknown = outcome_unknown


class ReadFailed(AppFailed):
    """An app could not answer a read, for a reason worth one sentence on the lane."""


class WriteFailed(AppFailed):
    """An app refused a write or never answered it, for a reason worth one sentence to the person who asked for it."""


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
        params = {key: value_text(value) for key, value in (query or {}).items() if value is not None}
        return self.exchange('GET', path, f'GET {path}', ReadFailed, timeout, params=params)

    def post(self, path: str, body: dict[str, object], asked: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> object:
        """The JSON body of `POST <app>/api/v1<path>` sending `body`, or a `WriteFailed` saying why there is none.

        `asked` names the request in that sentence as a person would type it,
        because `path` carries the escapes of each segment it fills.
        """
        return self.exchange('POST', path, asked, WriteFailed, timeout, json=body)

    def exchange(self, method: str, path: str, asked: str, failed: type[AppFailed], timeout: float, **request: Any) -> object:
        """One request with the service's token, its JSON answer, or `failed` saying why there is none."""
        if self.settings.missing:
            raise failed(f'{self.name} is not configured on the service: {self.settings.missing} unset')
        try:
            token = self.tokens.token()
        except LoginError as error:
            raise failed(f'{self.name}: {error}', refused=True) from error
        try:
            response = self.client.request(
                method,
                f'{self.settings.url}{API_PREFIX}{path}',
                headers={'Authorization': f'Bearer {token.access_token}', 'Accept': 'application/json'},
                timeout=timeout,
                **request,
            )
        except (httpx2.ConnectError, httpx2.ConnectTimeout) as error:
            raise failed(f'{self.name} could not be reached for {asked}: {error}', transient=True) from error
        except httpx2.TimeoutException as error:
            raise failed(f'{self.name} did not answer {asked} within {timeout:g}s', transient=True, outcome_unknown=True) from error
        except httpx2.HTTPError as error:
            raise failed(f'{self.name} broke off its answer to {asked}: {error}', transient=True, outcome_unknown=True) from error
        if response.status_code >= 400:
            said = answer_message(response)
            raise failed(
                f'{self.name} answered {response.status_code} to {asked}' + (f': {said}' if said else ''),
                refused=response.status_code in (401, 403),
                status=response.status_code,
                transient=response.status_code >= 500,
            )
        try:
            return response.json()
        except ValueError as error:
            raise failed(f'{self.name} answered {asked} with something other than JSON', outcome_unknown=True) from error


def value_text(value: object) -> str:
    """A query value as the apps read it: booleans lowercase, everything else as written."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)
