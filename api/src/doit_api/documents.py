"""The three documents a person edits, and the configuration every view is computed from.

`pursuits`, `register` and `sources` are each kept as the text they were written
as, comments and layout included, because that text is what a person edits. The
text is the authority: every view parses it with doit's own validators, the same
ones a desk ran on its files, so the service and a desk cannot read one register
two ways.

Each entry is also a row of its own versioned table, written in the same
transaction as the text. Those tables are what a question about the register
over time reads: when a pursuit's weight moved, what the sources were last
month. A document that fails validation is refused whole, and nothing is
written.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from dataclasses import field

import yaml

from doit import pursuits
from doit import review
from doit import sources
from doit.pursuits import Cycle

from .errors import BadRequest
from .errors import NotFound
from .store import Store
from .store import stamp

NAMES = ('pursuits', 'register', 'sources')


@dataclass
class Config:
    """Everything the documents configure, parsed and validated."""

    pursuits: dict = field(default_factory=dict)
    forecast: dict = field(default_factory=dict)
    cycles: list[Cycle] = field(default_factory=list)
    register: dict[str, dict] = field(default_factory=dict)
    registry: sources.Registry = field(default_factory=sources.Registry)


def loaded(text: str, name: str) -> dict:
    try:
        document = yaml.safe_load(text) or {}
    except yaml.YAMLError as error:
        raise BadRequest(f'{name} is not YAML: {error}') from error
    if not isinstance(document, dict):
        raise BadRequest(f'{name} must be a mapping at the top, not {type(document).__name__}')
    return document


def parse_pursuits(text: str) -> tuple[dict, dict, dict, list[Cycle]]:
    """The pursuits document, its pursuits, its forecast settings and its cycles, or a refusal naming the first fault."""
    try:
        document = pursuits.parse_register(text, 'pursuits')
        found = pursuits.pursuits_from_document(document, 'pursuits')
        return (
            document,
            found,
            pursuits.settings_from_document(document, 'pursuits'),
            pursuits.cycles_from_document(document, found, 'pursuits'),
        )
    except pursuits.RegisterError as error:
        raise BadRequest(f'pursuits: {error}') from error


def parse_register(text: str) -> dict[str, dict]:
    try:
        return review.register_from_document(loaded(text, 'register'))
    except review.RegisterError as error:
        raise BadRequest(f'register: {error}') from error


def parse_sources(text: str) -> tuple[dict, sources.Registry]:
    document = loaded(text, 'sources')
    return document, sources.registry_from_document(document)


def as_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def projections(name: str, text: str) -> dict[str, dict[object, dict]]:
    """Each versioned table a document fills, with the rows it holds, keyed by the table's key."""
    if name == 'pursuits':
        document, found, forecast, _ = parse_pursuits(text)
        return {
            'pursuits': {pursuit: {'settings': as_json(config)} for pursuit, config in found.items()},
            'pursuit_cycles': {entry['name']: {'settings': as_json(entry)} for entry in document.get('cycles') or []},
            'forecast_settings': {1: {'budget_minutes': forecast.get('budget_minutes')}},
        }
    if name == 'register':
        return {
            'review_entries': {
                entry: {
                    'block': block,
                    'description': text_or_none((meta or {}).get('description')),
                    'cadence': text_or_none((meta or {}).get('cadence')),
                    'command': text_or_none((meta or {}).get('command')),
                    'show': text_or_none((meta or {}).get('show')),
                }
                for block, entries in parse_register(text).items()
                for entry, meta in entries.items()
            }
        }
    if name == 'sources':
        document, _ = parse_sources(text)
        rows = {}
        for block in ('sources', 'completions'):
            declared = document.get(block) or {}
            for source, settings in declared.items() if isinstance(declared, dict) else []:
                settings = settings if isinstance(settings, dict) else {}
                rows[source] = {
                    'block': block,
                    'adapter': text_or_none(settings.get('adapter')),
                    'command': None if settings.get('command') is None else as_json(settings['command']),
                    'timeout': settings.get('timeout'),
                    'lanes': None if settings.get('lanes') is None else as_json(settings['lanes']),
                }
        return {'sources': rows}
    raise NotFound(f'no document named {name}; the documents are {", ".join(NAMES)}')


def text_or_none(value: object) -> str | None:
    return None if value is None else str(value)


def write(connection, name: str, text: str, edited_by: str, at: str) -> dict:
    """Replace one document and its rows inside an open transaction, and say what moved.

    A document whose text is unchanged writes nothing, so its history gains no
    version that changed nothing.
    """
    tables = projections(name, text)
    held = connection.execute('SELECT text FROM documents WHERE name = ?', (name,)).fetchone()
    if held is not None and held['text'] == text:
        return {'document': name, 'changed': False}
    Store.put(connection, 'documents', name, {'text': text, 'edited_by': edited_by}, at)
    moved = {table: Store.replace(connection, table, rows, at) for table, rows in tables.items()}
    return {'document': name, 'changed': True, 'rows': moved}


def save(store: Store, name: str, text: str, edited_by: str, now: dt.datetime) -> dict:
    if name not in NAMES:
        raise NotFound(f'no document named {name}; the documents are {", ".join(NAMES)}')
    projections(name, text)
    with store.write() as connection:
        return write(connection, name, text, edited_by, stamp(now))


def config(store: Store) -> Config:
    """The configuration every view reads, from the documents as they stand."""
    found = Config()
    held = {name: store.document(name) for name in NAMES}
    if held['pursuits'] is not None:
        _, found.pursuits, found.forecast, found.cycles = parse_pursuits(held['pursuits'].text)
    if held['register'] is not None:
        found.register = parse_register(held['register'].text)
    if held['sources'] is not None:
        _, found.registry = parse_sources(held['sources'].text)
    return found
