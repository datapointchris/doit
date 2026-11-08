"""What the service reads from icb: the overview the dashboard's lanes are drawn from, and what was completed over a window.

icb serves no overview route. Its CLI composes `icb overview --json` from one
read per section, so the service composes the same document from the same reads,
for the sections doit's lanes use. The document keeps the CLI's shape and schema
version, so the lanes read it exactly as they read the CLI's.

This module is therefore a copy of the CLI's composition: the order of the
article queue and which countdowns show. A change to that
composition in icb has to be made here too, until icb serves the document itself
and this module becomes one read of it.

A section whose read fails keeps its empty shape and adds a warning naming it,
as the CLI does. Its lane degrades and the rest of the dashboard does not.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from doit.dashboard import ICB_SCHEMA_VERSION
from doit.window import Bounds

from .apps import App
from .apps import ReadFailed
from .apps import local_zone

# Rows per section, matching what `icb overview` shows, so a lane reads the same from either.
OVERVIEW_LIMIT = 10

ARTICLE_READ_WINDOW = dt.timedelta(days=30)


def instant(text: object) -> dt.datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def capped(rows: list, limit: int) -> list:
    return list(rows[:limit]) if limit > 0 else []


def article_read_activity(read: list[dict], now: dt.datetime) -> tuple[str | None, int]:
    stamps = [when for when in (instant(article.get('last_read_date')) for article in read) if when is not None]
    if not stamps:
        return None, 0
    cutoff = now - ARTICLE_READ_WINDOW
    return max(stamps).isoformat(), sum(1 for when in stamps if when > cutoff)


def articles_behind_current(unread: list[dict], current: dict | None) -> list[dict]:
    current_id = (current or {}).get('id')
    queued = [article for article in unread if current is None or article.get('id') != current_id]
    return sorted(queued, key=lambda article: instant(article.get('save_date')) or dt.datetime.min.replace(tzinfo=dt.UTC))


def upcoming_countdowns(countdowns: list[dict], now: dt.datetime) -> list[dict]:
    today = now.date().isoformat()
    upcoming = [countdown for countdown in countdowns if str(countdown.get('due_date') or '') >= today]
    return sorted(upcoming, key=lambda countdown: str(countdown.get('due_date') or ''))


# Each read the overview makes: its section, the label a warning names it by,
# and the request. The same table the CLI fans out over.
OVERVIEW_READS: tuple[tuple[str, str, str, Callable[[], dict]], ...] = (
    ('tasks', 'tasks', '/tasks', lambda: {'status': 'open'}),
    ('habits', 'habits', '/habits/day', lambda: {'timezone': local_zone()}),
    ('books', 'books', '/books', lambda: {'ownership': 'owned'}),
    ('articles', 'current article', '/articles/current', dict),
    ('articles', 'unread articles', '/articles', lambda: {'unread': True}),
    ('articles', 'read articles', '/articles', lambda: {'archived': True}),
    ('countdowns', 'countdowns', '/countdowns', dict),
    ('events', 'events', '/events', lambda: {'when': 'upcoming', 'timezone': local_zone()}),
)


def overview(icb: App, now: dt.datetime, limit: int = OVERVIEW_LIMIT) -> dict:
    """The overview document, composed from one read per section, with a warning for each read that failed.

    Two failures refuse the whole document, as they do from icb's CLI. One is
    every read failing, since an overview of nothing but warnings would draw every
    icb lane empty. The other is any section refusing the service's credential,
    since a missing scope would otherwise draw that section as if it held nothing.
    """

    def read(entry: tuple[str, str, str, Callable[[], dict]]) -> object:
        section, label, path, query = entry
        try:
            return icb.get(path, query())
        except ReadFailed as error:
            return error

    with ThreadPoolExecutor(max_workers=len(OVERVIEW_READS)) as pool:
        answers = list(pool.map(read, OVERVIEW_READS))
    failures = [(entry, answer) for entry, answer in zip(OVERVIEW_READS, answers, strict=True) if isinstance(answer, ReadFailed)]
    refused = [failure for _, failure in failures if failure.refused]
    if refused:
        raise refused[0]
    if len(failures) == len(OVERVIEW_READS):
        raise failures[0][1]
    by_label = {
        label: (None if isinstance(answer, ReadFailed) else answer)
        for (_, label, _, _), answer in zip(OVERVIEW_READS, answers, strict=True)
    }

    def rows(label: str) -> list[dict]:
        found = by_label.get(label)
        return [row for row in found if isinstance(row, dict)] if isinstance(found, list) else []

    tasks = rows('tasks')
    found_board, found_current = by_label.get('habits'), by_label.get('current article')
    board: dict = found_board if isinstance(found_board, dict) else {}
    owned = rows('books')
    current: dict | None = found_current if isinstance(found_current, dict) else None
    queued = articles_behind_current(rows('unread articles'), current)
    last_read, read_recently = article_read_activity(rows('read articles'), now)
    countdowns = upcoming_countdowns(rows('countdowns'), now)
    events = rows('events')
    next_up = [book for book in owned if book.get('progress') == 'unread']
    return {
        'schema_version': ICB_SCHEMA_VERSION,
        'generated_at': now.isoformat(),
        'tasks': {'items': capped(tasks, limit), 'total': len(tasks)},
        'habits': {
            'due_today': capped(board.get('due') or [], limit),
            'completed_today': capped(board.get('completed') or [], limit),
            'current_total': board.get('current_total') or 0,
        },
        'books': {
            'reading': capped([book for book in owned if book.get('progress') == 'reading'], limit),
            'next_up': capped(next_up, limit),
            'next_up_total': len(next_up),
        },
        'articles': {
            'current': current,
            'unread': capped(queued, limit),
            'unread_total': len(queued),
            'last_read_at': last_read,
            'read_last_30_days': read_recently,
        },
        'countdowns': {'items': capped(countdowns, limit), 'total': len(countdowns)},
        'events': {'items': capped(events, limit), 'total': len(events)},
        'warnings': [{'section': entry[0], 'message': f'{entry[1]}: {failure.reason}'} for entry, failure in failures],
    }


def window(bounds: Bounds, *, zoned: bool = False) -> dict[str, object]:
    """A completions window as icb reads one: two RFC 3339 instants, and the zone where it reads days."""
    query: dict[str, object] = {'since': bounds.start.isoformat(), 'until': bounds.stop.isoformat()}
    if zoned:
        query['timezone'] = local_zone()
    return query


# What each icb completions source reads over a window. The CLI command each
# twin answers for is the adapter's own, in `doit.happened`.
COMPLETIONS: dict[str, Callable[[App, Bounds], object]] = {
    'icb-habits': lambda icb, bounds: icb.get('/habits/completed', window(bounds, zoned=True)),
    'icb-tasks': lambda icb, bounds: icb.get('/tasks', {'status': 'completed', **window(bounds)}),
    'icb-articles': lambda icb, bounds: icb.get('/articles', window(bounds)),
    'icb-books': lambda icb, bounds: icb.get('/books', window(bounds, zoned=True)),
}
