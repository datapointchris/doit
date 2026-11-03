"""The one module that makes a request to doit's service.

`~/.config/doit/config.toml` names the service, its issuer and this machine's
client, and `doit auth login <url>` fills it in.

A refusal reaches a command as `ServiceFailed`, carrying the service's own
sentence, or the repair for a refused token. `NoAnswer` is the one failure the
same command can outlast by running again later.
"""

from __future__ import annotations

import random
import tempfile
import time
from collections.abc import Callable
from collections.abc import Generator
from pathlib import Path
from typing import Any

import httpx2
from pyclilogin import DEFAULT_SCOPES
from pyclilogin import Product
from pyclilogin import TokenRefused
from rich.text import Text

from doit import actor
from doit import paths
from doit.editor import open_in_editor
from doit.render import can_prompt
from doit.render import console
from doit.render import error_console

USER_AGENT = 'doit-cli (+https://github.com/datapointchris/doit)'
PREFIX = '/api/v1'

# Must outlast the longest read a view makes of an app, so a slow app costs the
# view one failed lane and not the whole answer.
TIMEOUT = httpx2.Timeout(60.0, connect=10.0)
IMPORT_TIMEOUT = httpx2.Timeout(300.0, connect=10.0)

# One request with its retries, bounded together. A command can wait on a person
# between requests, so the bound is per request rather than per command.
BUDGET_SECONDS = 90.0
ATTEMPTS = 3
LONGEST_PAUSE_SECONDS = 20.0
# Statuses a proxy or a busy service sends before the request is handled, so a
# later attempt can be answered.
RETRIED_STATUSES = frozenset({429, 502, 503, 504})

# The keychain service is a deployed identifier: renaming it orphans every stored token.
LOGIN = Product(
    name='doit',
    keyring_service='doit-cli',
    scopes=DEFAULT_SCOPES,
    user_agent=USER_AGENT,
    product_keys=frozenset(),
)


class ServiceFailed(Exception):
    """The service refused a request, or could not be asked."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class NoAnswer(ServiceFailed):
    """No answer came: the service was never reached, timed out, or was busy on every attempt."""


class BearerAuth(httpx2.Auth):
    """Sends this machine's access token, refreshing it first when it has lapsed."""

    def __init__(self, source: Any, repair: str) -> None:
        self.source = source
        self.repair = repair

    def auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response]:
        try:
            token = self.source.token()
        except TokenRefused as error:
            raise ServiceFailed(f"the identity provider no longer honors this machine's login ({error.error}). {self.repair}") from error
        request.headers['Authorization'] = f'Bearer {token.access_token}'
        yield request


def said(response: httpx2.Response) -> str:
    """The service's sentence for a failure, which every refusal carries as `message`."""
    try:
        body = response.json()
    except ValueError:
        return ''
    return str(body.get('message') or '') if isinstance(body, dict) else ''


def bounded(timeout: httpx2.Timeout, remaining: float) -> httpx2.Timeout:
    """`timeout` with no phase allowed past what is left of the budget."""
    return httpx2.Timeout(
        min(timeout.read or remaining, remaining),
        connect=min(timeout.connect or remaining, remaining),
        write=min(timeout.write or remaining, remaining),
        pool=min(timeout.pool or remaining, remaining),
    )


def pause_after(attempt: int, response: httpx2.Response | None) -> float:
    """How long to wait after a failed attempt: the service's Retry-After, else a random fraction of 2^(n-1) seconds."""
    if response is not None:
        try:
            return min(float(response.headers.get('Retry-After', '')), LONGEST_PAUSE_SECONDS)
        except ValueError:
            pass
    return random.SystemRandom().uniform(0, min(2 ** (attempt - 1), LONGEST_PAUSE_SECONDS))


class Service:
    """One authenticated client for the service at `base`, which `chosen_by` says where to find."""

    def __init__(
        self,
        base: str,
        source: Any,
        transport: httpx2.BaseTransport | None = None,
        *,
        chosen_by: str = '',
        repair: str = 'Run `doit auth login`.',
        pause: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base = base.rstrip('/')
        self.chosen_by = chosen_by
        self.repair = repair
        self.pause = pause
        self.http = httpx2.Client(
            base_url=self.base + PREFIX,
            auth=BearerAuth(source, repair),
            timeout=TIMEOUT,
            headers={'User-Agent': USER_AGENT},
            follow_redirects=False,
            transport=transport,
        )

    def where(self) -> str:
        return f'the doit service at {self.base}' + (f' ({self.chosen_by})' if self.chosen_by else '')

    def send(self, method: str, path: str, *, repeatable: bool, timeout: httpx2.Timeout = TIMEOUT, **keywords: Any) -> Any:
        """One request, tried again where a later attempt can be answered and cannot do the write twice.

        A refused connection never reached the service, so anything is sent
        again. After a timeout, a reset or a busy answer, only a `repeatable`
        request is: a read, a PUT, or a write the service stores once by its key.
        """
        give_up = time.monotonic() + BUDGET_SECONDS
        reason, attempt = 'the budget ran out', 0
        while attempt < ATTEMPTS and (remaining := give_up - time.monotonic()) > 0:
            attempt += 1
            response = None
            try:
                response = self.http.request(method, path, timeout=bounded(timeout, remaining), **keywords)
            except (httpx2.ConnectError, httpx2.ConnectTimeout) as error:
                reason = str(error) or type(error).__name__
            except httpx2.TransportError as error:
                reason = str(error) or type(error).__name__
                if not repeatable:
                    raise NoAnswer(self.unknown(method, path, reason)) from error
            else:
                if response.status_code not in RETRIED_STATUSES:
                    return self.answered(response)
                reason = f'HTTP {response.status_code}'
                if not repeatable and response.status_code != 429:
                    raise NoAnswer(self.unknown(method, path, reason))
            if attempt < ATTEMPTS:
                self.pause(min(pause_after(attempt, response), max(give_up - time.monotonic(), 0.0)))
        raise NoAnswer(f'no answer from {self.where()}: {reason}, {attempt} attempt{"s" if attempt != 1 else ""}')

    def unknown(self, method: str, path: str, reason: str) -> str:
        return f'{self.where()} did not answer {method} {path} ({reason}). It may have been recorded, so check before running it again.'

    def answered(self, response: httpx2.Response) -> Any:
        status = response.status_code
        if status == 401:
            raise ServiceFailed(f"{self.where()} refused this machine's token. {self.repair}", 401)
        if not response.is_success:
            raise ServiceFailed(said(response) or f'{self.where()} answered HTTP {status}', status)
        try:
            return response.json()
        except ValueError:
            raise ServiceFailed(f'{self.where()} answered HTTP {status} with a body that is not JSON', status) from None

    def get(self, path: str, **query: Any) -> Any:
        return self.send('GET', path, repeatable=True, params={key: value for key, value in query.items() if value is not None})

    def post(self, path: str, body: dict, *, repeatable: bool = False, timeout: httpx2.Timeout = TIMEOUT) -> Any:
        """A write. `repeatable` where the service stores it once by a key the body carries."""
        return self.send('POST', path, repeatable=repeatable, json=body, timeout=timeout)

    def put(self, path: str, body: dict) -> Any:
        return self.send('PUT', path, repeatable=True, json=body)


def client() -> Service:
    """The service this machine's config file and token source name."""
    deployment = LOGIN.load()
    return Service(
        deployment.api.value,
        LOGIN.token_source(deployment),
        chosen_by=f'api in {deployment.path}',
        repair=LOGIN.repair(deployment),
    )


def signed(body: dict) -> dict:
    """A journal write naming this machine and whoever is running the command."""
    return {**body, 'machine': paths.machine_name(), 'actor': actor.identity()}


def kept_edit(name: str) -> Path:
    """Where an edit the service never took waits for the next `doit <name> edit`."""
    return paths.xdg_state_home() / 'doit' / f'{name}-unsaved.yml'


def edit_document(name: str, template: str) -> bool:
    """Edit one of the service's documents in $EDITOR, starting from ``template`` where none is written yet.

    The service validates the whole text and writes nothing it refuses. A refusal
    names the first fault and reopens the edit with the text as it was left. A
    save that fails any other way keeps the text on this machine, and the next
    edit starts from it. Returns whether a document now stands.
    """
    doit = client()
    stored = True
    try:
        text = doit.get(f'/documents/{name}')['text']
    except ServiceFailed as missing:
        if missing.status != 404:
            raise
        text, stored = template, False
    original = text
    kept = kept_edit(name)
    if kept.is_file():
        text = kept.read_text(encoding='utf-8')
        error_console.print(Text(f'{name}: starting from the edit kept at {kept}, which was never saved.', style='yellow'))
    with tempfile.TemporaryDirectory(prefix='doit-') as folder:
        path = Path(folder) / f'{name}.yml'
        while True:
            path.write_text(text, encoding='utf-8')
            open_in_editor(path)
            text = path.read_text(encoding='utf-8')
            if text == original and stored:
                kept.unlink(missing_ok=True)
                console.print(f'{name}: no change.')
                return True
            try:
                doit.put(f'/documents/{name}', {'text': text})
            except ServiceFailed as refused:
                if refused.status != 422:
                    kept.parent.mkdir(parents=True, exist_ok=True)
                    kept.write_text(text, encoding='utf-8')
                    raise type(refused)(
                        f'{refused} The edit is kept at {kept}, and `doit {name} edit` starts from it.', refused.status
                    ) from refused
                error_console.print(Text(f'{name}: {refused}', style='yellow'))
                if not can_prompt() or console.input('  Edit again? [Y/n] ').strip().lower() in ('n', 'no'):
                    return False
                continue
            kept.unlink(missing_ok=True)
            console.print(f'{name}: saved.')
            return True
