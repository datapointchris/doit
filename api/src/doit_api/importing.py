"""Bringing a desk's files into the store, as often as it takes.

A desk keeps its configuration in `~/.config/doit/` and its records in
`~/.local/state/doit/`. An import posts both folders' files in one request, each
with its path under `config/` or `state/`, its text and when it was last
modified, and merges them into the store in one transaction.

**Running it twice adds nothing twice.** Each kind of record merges on what
identifies it: a journal entry on its id, a review completion on its entry and
day, a forecast reading on its machine and moment. An offered count only rises,
because a count never falls, so an older copy cannot undo a newer one. A nudge
time only moves later. So the import is run once per desk and again whenever
in doubt.

**A document is replaced only while an import owns it.** A document no person
has edited on the service since the last import is replaced by the desk's text.
One edited on the service is kept, and the summary says so, so a late import of
a stale desk cannot undo an edit made on the service.

**A pursuit's commands become reads.** The service reads apps over HTTP, so a
`resolve` or `evidence` written as an app's CLI command is rewritten as the
request that command makes: the app, the path and the query. The rewrite edits
those values in place and keeps the document's comments. A command with no known
request is left as written and listed in the summary, and its pursuit's row says
the command cannot run until it is rewritten by hand.

Files the store has no place for are listed as skipped with the reason: a CLI's
own update stamp, a sync log, a backup copy.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import re
import shlex
from collections.abc import Callable

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from doit import journal

from . import documents
from .errors import BadRequest
from .store import Store
from .store import stamp

# The prefix `edited_by` carries on a document an import wrote.
IMPORT_AUTHOR = 'import:'

JOURNAL = re.compile(r'state/next-log-(?P<machine>.+)\.jsonl')
OFFERS = re.compile(r'state/next-offers-(?P<machine>.+)\.json')
FORECASTS = re.compile(r'state/forecast-(?P<machine>.+)\.jsonl')
DOCUMENT_FILES = {'config/pursuits.yml': 'pursuits', 'config/register.yml': 'review', 'config/sources.yml': 'sources'}

# Why a file the store has no place for is skipped.
SKIPPED = (
    (re.compile(r'state/autoupdate(-.+)?\.json'), "the installed CLI's own update stamp, which stays on each desk"),
    (re.compile(r'state/content-sync-.+\.log'), 'a log of syncing the content library, which stays on each desk'),
    (re.compile(r'.+\.bak.*'), 'a backup copy'),
)

EVENTS = {event.value for event in journal.Event}

# icb list commands and the collection each reads. Each flag becomes the query
# parameter of the same name with `_` for `-`, which is how icb's CLI builds it.
ICB_COLLECTIONS: dict[tuple[str, ...], str] = {
    ('tasks', 'list'): '/tasks',
    ('books', 'list'): '/books',
    ('issues', 'list'): '/issues',
    ('articles', 'list'): '/articles',
    ('projects', 'items', 'list'): '/project-items',
    ('countdowns', 'list'): '/countdowns',
    ('events', 'list'): '/events',
}
SHORT_FLAGS = {'-n': '--limit'}


def flag_query(arguments: list[str]) -> dict[str, object] | None:
    """`--flag value` pairs and bare `--flag` switches as a query, or None where an argument is not a flag."""
    query: dict[str, object] = {}
    position = 0
    while position < len(arguments):
        argument = SHORT_FLAGS.get(arguments[position], arguments[position])
        if not argument.startswith('--'):
            return None
        name, _, inline = argument[2:].partition('=')
        if inline:
            value: object = inline
            position += 1
        elif position + 1 < len(arguments) and not arguments[position + 1].startswith('-'):
            value = arguments[position + 1]
            position += 2
        else:
            value = True
            position += 1
        query[name.replace('-', '_')] = int(value) if isinstance(value, str) and value.isdigit() else value
    return query


def translated(command: str) -> tuple[dict, str | None] | None:
    """The read a CLI command makes, and the `items` path its answer moved to where it moved, or None for a command with no known read."""
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    words = [word for word in words if word != '--json']
    if not words:
        return None
    program, rest = words[0], words[1:]
    if program == 'icb':
        for verbs, path in ICB_COLLECTIONS.items():
            if tuple(rest[: len(verbs)]) == verbs:
                query = flag_query(rest[len(verbs) :])
                return None if query is None else (read_of('icb', path, query), None)
        return None
    if program == 'learning' and rest[:1] == ['plan']:
        # The plan's head is the overview's `plan_head`. The service's client
        # holds the overview scope, not the plan's.
        return read_of('learning', '/overview', {}), 'plan_head'
    if program == 'learning' and rest[:1] == ['overview']:
        return read_of('learning', '/overview', {}), None
    if program == 'meso' and rest[:2] == ['cycles', 'list']:
        return read_of('meso', '/cycles', {}), None
    return None


def read_of(app: str, path: str, query: dict) -> dict:
    found = CommentedMap([('app', app), ('path', path)])
    if query:
        shown = CommentedMap(query)
        shown.fa.set_flow_style()
        found['query'] = shown
    return found


def round_trip() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def translated_pursuits(text: str) -> tuple[str, list[dict], list[dict]]:
    """The pursuits document with every known command rewritten as a read, what was rewritten, and what was not."""
    yaml = round_trip()
    document = yaml.load(text)
    rewritten: list[dict] = []
    untranslated: list[dict] = []
    for name, settings in ((document or {}).get('pursuits') or {}).items():
        if not isinstance(settings, dict):
            continue
        for field in ('resolve', 'evidence'):
            command = settings.get(field)
            if not isinstance(command, str):
                continue
            found = translated(command)
            if found is None:
                untranslated.append({'pursuit': name, 'field': field, 'command': command})
                continue
            read, items = found
            settings[field] = read
            if items and field == 'resolve' and settings.get('items') == 'sequence':
                settings['items'] = items
            rewritten.append({'pursuit': name, 'field': field, 'command': command, 'read': json.loads(json.dumps(read))})
    if not rewritten:
        return text, rewritten, untranslated
    written = io.StringIO()
    yaml.dump(document, written)
    return written.getvalue(), rewritten, untranslated


def json_lines(text: str) -> tuple[list[dict], int]:
    """Each line that parses as an object, and how many did not."""
    found, malformed = [], 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(record, dict):
            found.append(record)
        else:
            malformed += 1
    return found, malformed


def modified(entry: dict) -> str | None:
    text = entry.get('modified_at')
    if not text:
        return None
    try:
        return stamp(dt.datetime.fromisoformat(str(text).replace('Z', '+00:00')))
    except ValueError:
        raise BadRequest(f'{entry.get("path")}: modified_at={text!r} is not an ISO 8601 instant') from None


def run(store: Store, files: list[dict], imported_by: str, now: dt.datetime) -> dict:
    """Merge a desk's files into the store, and say what each file added."""
    at = stamp(now)
    summary: dict = {
        'documents': {},
        'journal': {'read': 0, 'added': 0, 'malformed': 0},
        'offers': {'raised': 0},
        'review': {'read': 0, 'added': 0},
        'forecasts': {'read': 0, 'added': 0, 'malformed': 0},
        'nudge': {},
        'rewritten': [],
        'untranslated': [],
        'skipped': [],
    }
    seen = set()
    for entry in files:
        path = str(entry.get('path') or '')
        if not path or not isinstance(entry.get('text'), str):
            raise BadRequest('every file needs a path and its text')
        if path in seen:
            raise BadRequest(f'{path} is in the request twice')
        seen.add(path)

    with store.write() as connection:
        for entry in files:
            merge(connection, store, entry, imported_by, at, summary)
        summary['import_id'] = store.record_import(connection, imported_by, summary, at)
    return summary


def merge(connection, store: Store, entry: dict, imported_by: str, at: str, summary: dict) -> None:
    path, text = str(entry['path']), str(entry['text'])
    if path in DOCUMENT_FILES:
        name = DOCUMENT_FILES[path]
        if name == 'pursuits':
            text, rewritten, untranslated = translated_pursuits(text)
            summary['rewritten'].extend(rewritten)
            summary['untranslated'].extend(untranslated)
        held = connection.execute('SELECT edited_by FROM documents WHERE name = ?', (name,)).fetchone()
        if held is not None and not str(held['edited_by']).startswith(IMPORT_AUTHOR):
            summary['documents'][name] = {'kept': f'edited on the service by {held["edited_by"]} since it was imported'}
            return
        summary['documents'][name] = documents.write(connection, name, text, f'{IMPORT_AUTHOR}{imported_by}', at)
        return
    readers: list[tuple[re.Pattern, Callable[[re.Match], None]]] = [
        (JOURNAL, lambda match: merge_journal(connection, store, text, imported_by, at, summary)),
        (OFFERS, lambda match: merge_offers(connection, store, match['machine'], text, path, summary)),
        (FORECASTS, lambda match: merge_forecasts(connection, store, text, at, summary)),
    ]
    for pattern, merge_file in readers:
        match = pattern.fullmatch(path)
        if match:
            merge_file(match)
            return
    if path == 'state/review-state.json':
        merge_review(connection, store, text, path, imported_by, at, summary)
    elif path == 'state/nudge-interval-minutes':
        minutes = text.strip()
        if not minutes.isdigit() or int(minutes) <= 0:
            raise BadRequest(f'{path} holds {minutes!r}, not a positive whole number of minutes')
        store.set_nudge(connection, interval_minutes=int(minutes))
        summary['nudge']['interval_minutes'] = int(minutes)
    elif path == 'state/nudge':
        nudged = modified(entry)
        if nudged:
            store.set_nudge(connection, last_nudged_at=nudged)
            summary['nudge']['last_nudged_at'] = nudged
    else:
        reason = next((why for pattern, why in SKIPPED if pattern.fullmatch(path)), 'the store has no place for it')
        summary['skipped'].append({'path': path, 'reason': reason})


def merge_journal(connection, store: Store, text: str, imported_by: str, at: str, summary: dict) -> None:
    records, malformed = json_lines(text)
    usable = [record for record in records if record.get('id') and record.get('event') in EVENTS]
    summary['journal']['read'] += len(records)
    summary['journal']['malformed'] += malformed + len(records) - len(usable)
    summary['journal']['added'] += store.append_records(connection, usable, f'{IMPORT_AUTHOR}{imported_by}', at)


def merge_offers(connection, store: Store, machine: str, text: str, path: str, summary: dict) -> None:
    try:
        counts = json.loads(text or '{}')
    except json.JSONDecodeError:
        raise BadRequest(f'{path} is not JSON') from None
    if not isinstance(counts, dict) or not all(isinstance(count, int) and count >= 0 for count in counts.values()):
        raise BadRequest(f'{path} must map each pursuit to a count')
    summary['offers']['raised'] += store.raise_offers(connection, machine, counts)


def merge_forecasts(connection, store: Store, text: str, at: str, summary: dict) -> None:
    readings, malformed = json_lines(text)
    usable = [reading for reading in readings if reading.get('generated') and reading.get('machine')]
    summary['forecasts']['read'] += len(readings)
    summary['forecasts']['malformed'] += malformed + len(readings) - len(usable)
    summary['forecasts']['added'] += sum(store.add_reading(connection, reading, at) for reading in usable)


def merge_review(connection, store: Store, text: str, path: str, imported_by: str, at: str, summary: dict) -> None:
    try:
        stamps = json.loads(text or '{}')
    except json.JSONDecodeError:
        raise BadRequest(f'{path} is not JSON') from None
    if not isinstance(stamps, dict):
        raise BadRequest(f'{path} must map each review entry to the day it was last done')
    for entry, day in stamps.items():
        try:
            dt.date.fromisoformat(str(day))
        except ValueError:
            raise BadRequest(f'{path}: {entry} holds {day!r}, not a date') from None
        summary['review']['read'] += 1
        summary['review']['added'] += store.add_completion(connection, entry, str(day), f'{IMPORT_AUTHOR}{imported_by}', at)
