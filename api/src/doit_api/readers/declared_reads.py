"""The reads the documents declare, made over HTTP: each source's answer, and each pursuit's `resolve` and `evidence`.

A source names an adapter, and its name selects an HTTP read here and the builder
that draws the lane from what the read returns. A source declaring a bare
`command` names a program, and is answered with a refusal that says so.

A pursuit's `resolve` and `evidence` declare an app, a path under its API and a
query, which this module asks:

    resolve:
      app: icb
      path: /tasks
      query: {limit: 3}

The register's validator refuses any other shape, so a read reaching here is one
the service can make.

Every failed read is logged at error as `source_failed`, naming the source and
the reason, and the reason is what the lane or the row shows.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import httpx2

from doit import evidence as evidence_reads
from doit import pursuits
from doit.sources import Failure
from doit.sources import Result
from doit.sources import Source
from doit.window import Bounds

from ..settings import ClickHouseSettings
from . import clickhouse
from . import icb
from . import learning
from . import meso
from . import nomad
from . import slate
from .apps import App
from .apps import ReadFailed
from .github import GitHub

log = logging.getLogger(__name__)


def source_failed(source: str, adapter: str | None, reason: str, *, exc_info: bool = False) -> None:
    """The one line an alert is filed on: a read the service could not make, with the source's name."""
    log.error('source_failed', extra={'source': source, 'adapter': adapter, 'reason': reason}, exc_info=exc_info)


class Readers:
    """Every app the service reads, and the read each declared source and pursuit selects."""

    def __init__(
        self,
        apps: dict[str, App],
        github: GitHub,
        clickhouse_settings: ClickHouseSettings,
        clickhouse_client: httpx2.Client | None = None,
    ) -> None:
        self.apps = apps
        self.github = github
        self.clickhouse_settings = clickhouse_settings
        self.clickhouse_client = clickhouse_client
        self.adapter_reads: dict[str, Callable[[Bounds, dt.datetime], tuple[object, str]]] = {
            'icb': lambda bounds, now: (icb.overview(self.app('icb'), now), ''),
            'learning': lambda bounds, now: (learning.overview(self.app('learning')), ''),
            'meso': lambda bounds, now: (meso.cycles(self.app('meso')), ''),
            'meso-feedback': lambda bounds, now: (meso.open_feedback(self.app('meso')), ''),
            'nomad': lambda bounds, now: (nomad.overview(self.app('nomad')), ''),
            'prs': lambda bounds, now: self.github.open_pull_requests(now),
            'dotfiles': lambda bounds, now: (clickhouse.desk_checks_document(self.clickhouse_settings, now, self.clickhouse_client), ''),
            'learning-completed': lambda bounds, now: learning.completed(self.app('learning'), bounds),
            'meso-review': lambda bounds, now: (meso.review(self.app('meso'), bounds), ''),
            **{adapter: self.completions('icb', read) for adapter, read in icb.COMPLETIONS.items()},
            **{adapter: self.completions('slate', read) for adapter, read in slate.COMPLETIONS.items()},
        }

    def completions(self, app: str, read: Callable[[App, Bounds], object]) -> Callable[[Bounds, dt.datetime], tuple[object, str]]:
        return lambda bounds, now: (read(self.app(app), bounds), '')

    def app(self, name: str) -> App:
        if name not in self.apps:
            raise ReadFailed(f'the service reads no app named {name}; it reads {", ".join(sorted(self.apps))}')
        return self.apps[name]

    def result(self, source: Source, bounds: Bounds, now: dt.datetime) -> Result:
        """One source's answer, as the adapter that draws its lanes reads it."""
        try:
            if not source.adapter:
                raise ReadFailed(f'{source.id} runs `{" ".join(source.command)}`, and the service reads apps over HTTP only')
            read = self.adapter_reads.get(source.adapter)
            if read is None:
                raise ReadFailed(f'the service has no HTTP read for the {source.adapter} adapter')
            payload, said = read(bounds, now)
        except ReadFailed as error:
            source_failed(source.id, source.adapter, error.reason)
            return Result(source=source.id, stderr=error.reason, failure=Failure.FAILED, bounds=bounds)
        except Exception as error:
            # A reader's own fault costs its lane, never the view the other lanes are in.
            reason = f'the {source.adapter} read failed: {type(error).__name__}: {error}'
            source_failed(source.id, source.adapter, reason, exc_info=True)
            return Result(source=source.id, stderr=reason, failure=Failure.FAILED, bounds=bounds)
        return Result(source=source.id, payload=payload, exit_code=0, stderr=said, bounds=bounds)

    def results(self, sources: list[Source], bounds: Bounds, now: dt.datetime) -> dict[str, Result]:
        """Every source asked at once, keyed by id."""
        if not sources:
            return {}
        with ThreadPoolExecutor(max_workers=len(sources)) as pool:
            return {result.source: result for result in pool.map(lambda source: self.result(source, bounds, now), sources)}

    def declared(self, declaration: dict) -> object:
        return self.app(str(declaration['app'])).get(str(declaration['path']), declaration.get('query') or {})

    def resolve(self, name: str, config: dict) -> dict | None:
        """What a pursuit means right now, read from its app."""
        if not config.get('resolve'):
            return None
        backend = str(config['resolve']['app'])
        try:
            if not config.get('label'):
                raise ReadFailed('resolve over HTTP reads JSON, so it needs a label field')
            document = self.declared(config['resolve'])
        except ReadFailed as error:
            log.error('source_failed', extra={'source': f'{name}.resolve', 'reason': error.reason})
            return {'pursuit': name, 'error': error.reason, 'backend': backend}
        return pursuits.choices_from(name, config, document, backend)

    def resolve_all(self, names: list[str], register: dict) -> dict[str, dict]:
        wanted = [name for name in names if register.get(name, {}).get('resolve')]
        if not wanted:
            return {}
        with ThreadPoolExecutor(max_workers=len(wanted)) as pool:
            found = pool.map(lambda name: self.resolve(name, register[name]), wanted)
        return {name: entry for name, entry in zip(wanted, found, strict=True) if entry}

    def evidence(self, name: str, config: dict, now: dt.datetime) -> tuple[str, dict]:
        """When a pursuit last happened, by its app, as the entry `evidence.refreshed` caches."""
        try:
            document = self.declared(config['evidence'])
        except ReadFailed as error:
            log.error('source_failed', extra={'source': f'{name}.evidence', 'reason': error.reason})
            return name, {'checked_at': now.isoformat(), 'error': error.reason}
        return name, evidence_reads.answer_from(document, config, now)
