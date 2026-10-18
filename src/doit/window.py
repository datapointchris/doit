"""The window a read covers: `--since` and `--until`, each a span, a day or an instant."""

import datetime as dt
import re

SPAN = re.compile(r'(\d+)([smhdw])')
DAY = re.compile(r'\d{4}-\d{2}-\d{2}')
SPAN_UNITS = {'s': 'seconds', 'm': 'minutes', 'h': 'hours', 'd': 'days', 'w': 'weeks'}
FORMS = 'a span (30m, 12h, 7d, 2w), a day (2026-10-01) or an RFC 3339 instant'


def first_instant(day: dt.date) -> dt.datetime:
    """The day's first instant, built in the machine's zone rather than reached by adding hours."""
    return dt.datetime(day.year, day.month, day.day).astimezone()


def bound(value: str, now: dt.datetime, *, end: bool) -> dt.datetime:
    """One flag's value as an instant in the machine's zone; `end` marks `--until`, where a day includes all of itself."""
    if span := SPAN.fullmatch(value):
        return (now - dt.timedelta(**{SPAN_UNITS[span[2]]: int(span[1])})).astimezone()
    if DAY.fullmatch(value):
        day = dt.date.fromisoformat(value)
        return first_instant(day + dt.timedelta(days=1) if end else day)
    try:
        instant = dt.datetime.fromisoformat(value)
    except ValueError:
        instant = None
    if instant is None or instant.tzinfo is None:
        raise ValueError(f'{value!r} is not {FORMS}. There is no month or year unit: write 26w, or a day.')
    return instant.astimezone()


def window(since: str | None, until: str | None, now: dt.datetime) -> tuple[dt.datetime | None, dt.datetime | None]:
    """Both flags as instants, None where a flag was not given; refuses a window that ends before it starts."""
    start = bound(since, now, end=False) if since else None
    stop = bound(until, now, end=True) if until else None
    if start and stop and start >= stop:
        raise ValueError(f'--since {since} is not before --until {until}.')
    return start, stop
