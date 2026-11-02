"""The reads the documents declare, made over HTTP: each source's answer, and each pursuit's `resolve` and `evidence`.

On a desk, a source names an adapter and the adapter runs a CLI. Here the same
adapter name selects an HTTP read that returns the body the CLI would have
printed, so the adapter that draws the lane runs unchanged. A source declaring a
bare `command` names a program on a desk, and is answered with a refusal that
says so.

A pursuit's `resolve` and `evidence` declare an app, a path under its API and a
query, which this module asks:

    resolve:
      app: icb
      path: /tasks
      query: {limit: 3}

A `resolve` or `evidence` still written as a command was never imported into
that form, and its row says so rather than going quiet.

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
from .apps import App
from .apps import ReadFailed
from .github import GitHub

log = logging.getLogger(__name__)

READ_KEYS = {'app', 'path', 'query'}


def declared_read(declaration: object, field: str) -> tuple[str, str, dict]:
    """A `resolve` or `evidence` declaration's app, path and query, or a `ReadFailed` naming what is wrong with it."""
    if isinstance(declaration, str):
        raise ReadFailed(f'{field} is a command, which the service cannot run; declare the app, path and query it reads')
    if not isinstance(declaration, dict) or not declaration.get('app') or not str(declaration.get('path', '')).startswith('/'):
        raise ReadFailed(f'{field} must name an app and a path starting with /')
    unknown = set(declaration) - READ_KEYS
    if unknown:
        raise ReadFailed(f'{field} has unknown key(s) {", ".join(sorted(unknown))}; it takes app, path and query')
    query = declaration.get('query') or {}
    if not isinstance(query, dict):
        raise ReadFailed(f'{field}.query must be a mapping of parameter to value')
    return str(declaration['app']), str(declaration['path']), query


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
            'prs': lambda bounds, now: (self.github.open_pull_requests(now), ''),
            'dotfiles': lambda bounds, now: (clickhouse.desk_checks_document(self.clickhouse_settings, now, self.clickhouse_client), ''),
            'learning-completed': lambda bounds, now: learning.completed(self.app('learning'), bounds),
            'meso-review': lambda bounds, now: (meso.review(self.app('meso'), bounds), ''),
            **{adapter: self.icb_completions(read) for adapter, read in icb.COMPLETIONS.items()},
        }

    def icb_completions(self, read: Callable[[App, Bounds], object]) -> Callable[[Bounds, dt.datetime], tuple[object, str]]:
        return lambda bounds, now: (read(self.app('icb'), bounds), '')

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
            log.error('source_failed', extra={'source': source.id, 'adapter': source.adapter, 'reason': error.reason})
            return Result(source=source.id, stderr=error.reason, failure=Failure.FAILED, bounds=bounds)
        return Result(source=source.id, payload=payload, exit_code=0, stderr=said, bounds=bounds)

    def results(self, sources: list[Source], bounds: Bounds, now: dt.datetime) -> dict[str, Result]:
        """Every source asked at once, keyed by id."""
        if not sources:
            return {}
        with ThreadPoolExecutor(max_workers=len(sources)) as pool:
            return {result.source: result for result in pool.map(lambda source: self.result(source, bounds, now), sources)}

    def declared(self, declaration: object, field: str) -> object:
        app, path, query = declared_read(declaration, field)
        return self.app(app).get(path, query)

    def resolve(self, name: str, config: dict) -> dict | None:
        """What a pursuit means right now, read from its app: the same entry the desk's resolve builds."""
        if not config.get('resolve'):
            return None
        backend = config['resolve'].get('app', 'resolve') if isinstance(config['resolve'], dict) else 'resolve'
        try:
            if not config.get('label'):
                raise ReadFailed('resolve over HTTP reads JSON, so it needs a label field')
            document = self.declared(config['resolve'], 'resolve')
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
        """When a pursuit last happened, by its app: the same entry the desk's evidence read builds."""
        entry: dict = {'checked_at': now.isoformat()}
        try:
            if config.get('evidence_files'):
                raise ReadFailed('evidence_files names a directory on a desk, which the service cannot read')
            document = self.declared(config.get('evidence'), 'evidence')
        except ReadFailed as error:
            log.error('source_failed', extra={'source': f'{name}.evidence', 'reason': error.reason})
            entry['error'] = error.reason
            return name, entry
        stamps = evidence_reads.stamps_in(document, config, now)
        entry['last'] = max(stamps).isoformat() if stamps else None
        entry['dates'] = evidence_reads.dates_of(stamps, now)
        return name, entry
