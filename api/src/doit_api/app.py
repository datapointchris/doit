"""The HTTP surface: routes, and which caller each one admits.

Every route parses its request, calls a view or a write, and returns what that
returned. Nothing here computes a view, reads an app or opens the store itself.

`/health`, `/ready` and the RFC 9728 metadata answer anyone. Everything under
`/api/v1` answers a person's login, because every route reads or writes one
person's register and journal. A write records the caller as `recorded_by`,
beside the machine and actor the client names.

`create_app` takes every collaborator as an argument. Production passes the ones
`main` builds from the environment. A test passes a temporary store, stand-in
apps and a verifier that knows one token, and exercises the whole surface with
no network.
"""

import logging
import uuid
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Annotated
from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import FastAPI
from fastapi import Query
from fastapi import Response
from pyapiauth import Caller
from pyapiauth.fastapi_dependencies import person_dependency
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from doit import journal

from . import documents
from .computed_views import Views
from .computed_views import instant
from .errors import NotFound
from .errors import install_error_handlers
from .forecasting import ForecastLoop
from .settings import Settings
from .store import VERSIONED
from .store import Store

log = logging.getLogger(__name__)

# Where RFC 9728 puts the metadata of an API served at the root of its host.
PROTECTED_RESOURCE_PATH = '/.well-known/oauth-protected-resource'


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)


class Written(Strict):
    """Who a journal write is from: the machine and the actor the client names."""

    machine: str | None = Field(None, description='The machine the write was made on. The service names itself when omitted.')
    actor: str | None = Field(None, description='Who or what made the write. The caller when omitted.')


class DocumentBody(Strict):
    text: str = Field(description='The whole document as YAML, comments included.')


class Keyed(Written):
    """A journal write the client can send again: one id names it however many times it arrives."""

    id: uuid.UUID | None = Field(None, description='A UUIDv7 the client makes once per write. A write sent again with it is stored once.')


class DoneBody(Keyed):
    pursuit: str = Field(description='A pursuit name, or a prefix only one name starts with.')
    note: str | None = None
    duration_minutes: int | None = Field(
        None, gt=0, description='Required for a pursuit measured in minutes, refused for one counted in completions.'
    )
    occurred_at: str | None = Field(None, description='When it happened, ISO 8601 with an offset. Now when omitted.')
    item: dict[str, Any] | None = Field(None, description='The resolved choice the entry is about, where the entry names one.')
    downstream: dict[str, Any] | None = Field(None, description="What the pursuit's on_log command did on the desk.")


class SkipBody(Keyed):
    pursuit: str
    span: str | None = Field(
        None, alias='for', description='How long to pass, in days or weeks such as 3d or 2w. One interval when omitted.'
    )


class ResumeBody(Written):
    pursuit: str | None = Field(None, description='The pursuit to bring back. Every skipped one when omitted.')


class ResetBody(Written):
    pursuits: list[str] | None = Field(None, description='The pursuits to reset. Every one when omitted.')


class CompletionBody(Strict):
    day: str | None = Field(None, description='The day it was done, YYYY-MM-DD. Today when omitted.')


class NudgeBody(Strict):
    interval_minutes: int | None = Field(None, gt=0)
    last_nudged_at: str | None = Field(None, description='When the nudge last showed, ISO 8601 with an offset. Only moves later.')


class ForecastBody(Strict):
    budget_minutes: int | None = Field(None, gt=0, description="Minutes a day to spend. The register's budget when omitted.")


def create_app(*, settings: Settings, store: Store, views: Views, verifier, forecast_loop: ForecastLoop | None = None) -> FastAPI:
    """The ASGI app, wired to the collaborators it is handed."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if forecast_loop is not None:
            forecast_loop.start()
        yield
        if forecast_loop is not None:
            forecast_loop.stop()

    app = FastAPI(
        title='doit-api',
        summary="doit's register, review register, sources and journal with their history, and every view computed from them.",
        version=version('doit-api'),
        lifespan=lifespan,
    )
    install_error_handlers(app)

    @app.get('/health', include_in_schema=False)
    def health():
        """Liveness. Answers while the process runs, with no auth and no dependency."""
        return {'status': 'ok'}

    @app.get(PROTECTED_RESOURCE_PATH, include_in_schema=False)
    def protected_resource():
        """RFC 9728 metadata: this API's public URL and the issuer whose tokens it takes.

        Unauthenticated, because a CLI logging in reads it before it holds a
        token, to learn which identity provider to log in through.
        """
        return {'resource': settings.public_url, 'authorization_servers': [settings.issuer]}

    @app.get('/ready', include_in_schema=False)
    def ready(response: Response):
        """Readiness: the store answers a query. The apps are not asked, because one being down costs its lanes and not the service."""
        try:
            store.ping()
        except Exception as error:
            log.error('unready', extra={'dependency': 'store', 'error': str(error)})
            response.status_code = 503
            return {'status': 'unready', 'failing': ['store']}
        return {'status': 'ready'}

    api = APIRouter(prefix='/api/v1')
    Person = Annotated[Caller, Depends(person_dependency(verifier))]

    @api.get('/documents')
    def document_list(caller: Person) -> list[dict]:
        """Each document's name, who last edited it and since when."""
        held = [store.document(name) for name in documents.NAMES]
        return [{'name': found.name, 'edited_by': found.edited_by, 'valid_from': found.valid_from} for found in held if found]

    @api.get('/documents/{name}')
    def document_read(name: str, caller: Person) -> dict:
        found = store.document(name) if name in documents.NAMES else None
        if found is None:
            raise NotFound(f'no document named {name}' if name not in documents.NAMES else f'{name} has not been written yet')
        return {'name': found.name, 'text': found.text, 'edited_by': found.edited_by, 'valid_from': found.valid_from}

    @api.put('/documents/{name}')
    def document_write(name: str, body: DocumentBody, caller: Person) -> dict:
        """Replace a document. It is validated whole first, and a refusal names the first fault and writes nothing."""
        return documents.save(store, name, body.text, caller.who, views.now())

    @api.get('/documents/{name}/versions')
    def document_versions(name: str, caller: Person) -> list[dict]:
        if name not in documents.NAMES:
            raise NotFound(f'no document named {name}')
        return store.document_versions(name)

    @api.get('/history/{table}')
    def history(
        table: str, caller: Person, key: Annotated[str | None, Query(description='One row by its key. Every row when omitted.')] = None
    ) -> list[dict]:
        """Every version of a versioned table's rows, oldest first, the current ones with no `valid_to`."""
        if table not in VERSIONED:
            raise NotFound(f'no versioned table named {table}; they are {", ".join(VERSIONED)}')
        return store.history(table, key)

    @api.get('/pursuits')
    def pursuit_list(caller: Person) -> dict:
        return views.pursuit_list()

    @api.get('/pursuits/drift')
    def pursuit_drift(
        caller: Person,
        since: Annotated[
            str | None, Query(description='Start of the window: a span such as 30d, or a date. 90d where neither end is given.')
        ] = None,
        until: Annotated[str | None, Query(description='End of the window, in the same forms. Now when omitted.')] = None,
    ) -> dict:
        return views.drift(since, until)

    @api.get('/pursuits/dormant')
    def pursuit_dormant(caller: Person) -> dict:
        return views.dormant()

    @api.get('/pursuits/evidence')
    def pursuit_evidence(caller: Person) -> dict:
        return views.evidence()

    @api.get('/pursuits/offers')
    def pursuit_offers(caller: Person) -> dict:
        return views.offer_counts()

    @api.get('/pursuits/register')
    def pursuit_register(caller: Person) -> dict:
        """The pursuits as declared and what the standing draw offers. Asks no app, so a prompt can read it."""
        return views.register()

    @api.get('/pursuits/{name}/item')
    def pursuit_item(name: str, caller: Person) -> dict:
        return views.item(name)

    @api.get('/next')
    def next_draw(
        caller: Person,
        machine: Annotated[
            str | None,
            Query(description='The machine asking, whose offered counts a new draw adds to. The service names itself when omitted.'),
        ] = None,
        reroll: Annotated[bool, Query(description='Draw again even while the last draw stands.')] = False,
    ) -> dict:
        return views.next(machine, reroll)

    @api.get('/journal')
    def journal_list(
        caller: Person,
        pursuit: Annotated[str | None, Query(description='One pursuit by its exact name.')] = None,
        since: Annotated[str | None, Query(description='The earliest occurred_at, ISO 8601 with an offset.')] = None,
    ) -> list[dict]:
        """Every journal entry, ordered by when it says the thing happened."""
        start = instant(since, 'since')
        found = []
        for record in store.journal():
            when = journal.occurred(record)
            if pursuit is not None and record.get('pursuit') != pursuit:
                continue
            if start is not None and (when is None or when < start):
                continue
            found.append(record)
        return found

    @api.post('/journal/done', status_code=201)
    def journal_done(body: DoneBody, caller: Person) -> dict:
        return views.log(body.model_dump(), caller.who)

    @api.post('/journal/skip', status_code=201)
    def journal_skip(body: SkipBody, caller: Person) -> dict:
        return views.skip({**body.model_dump(exclude={'span'}), 'for': body.span}, caller.who)

    @api.post('/journal/resume', status_code=201)
    def journal_resume(body: ResumeBody, caller: Person) -> dict:
        return views.resume(body.model_dump(), caller.who)

    @api.post('/journal/reset', status_code=201)
    def journal_reset(body: ResetBody, caller: Person) -> dict:
        return views.reset(body.model_dump(), caller.who)

    @api.get('/review')
    def review_list(
        caller: Person, block: Annotated[str | None, Query(description='items or practice. Both when omitted.')] = None
    ) -> dict:
        return views.review(block)

    @api.post('/review/{entry}/completions', status_code=201)
    def review_complete(entry: str, body: CompletionBody, caller: Person) -> dict:
        return views.complete_review(entry, body.day, caller.who)

    @api.get('/review/{entry}/completions')
    def review_completions(entry: str, caller: Person) -> list[dict]:
        """Every completion the entry holds, oldest day first."""
        return views.review_completions(entry)

    @api.delete('/review/{entry}/completions/{day}')
    def review_take_back(entry: str, day: str, caller: Person) -> dict:
        """Take back one day's completion. Its row stays at `/history/review_completions`, and sent again this removes nothing more."""
        return views.take_back_completion(entry, day, caller.who)

    @api.get('/review/nudge')
    def review_nudge(caller: Person) -> dict:
        return views.nudge()

    @api.put('/review/nudge')
    def review_nudge_set(body: NudgeBody, caller: Person) -> dict:
        return views.set_nudge(body.interval_minutes, body.last_nudged_at)

    @api.get('/dashboard')
    def dashboard(caller: Person, lane: Annotated[list[str] | None, Query(description='Only these lanes.')] = None) -> dict:
        return views.dashboard(lane)

    @api.get('/today')
    def today(caller: Person) -> dict:
        return views.today()

    @api.get('/happened')
    def happened(
        caller: Person,
        since: Annotated[
            str | None, Query(description='Start of the window: a span such as 7d, or a date. 7d where neither end is given.')
        ] = None,
        until: Annotated[str | None, Query(description='End of the window, in the same forms. Now when omitted.')] = None,
    ) -> dict:
        return views.happened(since, until)

    @api.get('/sources')
    def source_list(caller: Person) -> dict:
        return views.sources()

    @api.get('/forecasts')
    def forecast_list(caller: Person) -> list[dict]:
        """Every forecast taken, oldest first."""
        return views.forecast_list()

    @api.post('/forecasts', status_code=201)
    def forecast_take(body: ForecastBody, caller: Person) -> dict:
        return views.take_forecast(body.budget_minutes)

    @api.get('/forecasts/trend')
    def forecast_trend(caller: Person, days: Annotated[int, Query(description='Which horizon to grade: 7, 14 or 30.')] = 7) -> dict:
        return views.forecast_trend(days)

    @api.get('/forecasts/{generated}')
    def forecast_show(generated: str, caller: Person) -> dict:
        """The forecast taken at a moment, by any prefix of its timestamp; `latest` for the newest."""
        return views.forecast_show('' if generated == 'latest' else generated)

    app.include_router(api)
    return app
