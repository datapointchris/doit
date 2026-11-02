"""The event journal's vocabulary, and how a record is read.

The journal is the record that cannot be reconstructed: every done, skip, reset
and pace, in the order they happened. The service keeps it. This module is what
both the service and the CLI agree a record says.
"""

import datetime as dt
import random
import uuid
from enum import StrEnum


class Event(StrEnum):
    """What a journal record says happened. The whole vocabulary, in one place.

    Roughly fifteen functions across three modules select on this field and none
    of them is a dispatch, so nothing mechanical finds them. Naming the members
    here makes adding a kind a search on one name rather than an audit somebody
    has to remember to run.

    A PACE is written by doit rather than by anyone logging: the interval and
    checkoff size the register declared from that moment, or nulls where it
    asked for nothing.
    """

    DONE = 'done'
    SKIP = 'skip'
    RESET = 'reset'
    PACE = 'pace'


def new_id(now: dt.datetime, rng: random.Random | None = None) -> str:
    """A UUIDv7: 48-bit millisecond timestamp, then randomness.

    Time-ordered, so ids sort into the order the events happened, and
    collision-safe without the machines that log coordinating. Hand-built
    because :mod:`uuid` only grew ``uuid7`` in 3.14.
    """
    rng = rng or random.SystemRandom()
    timestamp_ms = int(now.timestamp() * 1000) & 0xFFFFFFFFFFFF
    value = (timestamp_ms << 80) | (0x7 << 76) | (rng.getrandbits(12) << 64) | (0b10 << 62) | rng.getrandbits(62)
    return str(uuid.UUID(int=value))


def parse_time(value: str | None) -> dt.datetime | None:
    """An ISO 8601 timestamp from a record, or None if absent or unparsable."""
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def occurred(record: dict) -> dt.datetime | None:
    """When a record says the thing happened, falling back to when it was logged; None where neither parses.

    Every reader of a record's time asks here, so a change to how that time is
    read reaches the typed entry and the day it is matched against alike.
    """
    return parse_time(record.get('occurred_at') or record.get('logged_at'))


def latest_occurrence(records: list[dict], event: str) -> dict[str, dt.datetime]:
    """Most recent time per pursuit for one event kind (``done``, ``skip``, …)."""
    latest: dict[str, dt.datetime] = {}
    for record in records:
        if record.get('event') != event:
            continue
        pursuit = record.get('pursuit')
        when = occurred(record)
        if not pursuit or when is None:
            continue
        if pursuit not in latest or when > latest[pursuit]:
            latest[pursuit] = when
    return latest


def local_day(record: dict, now: dt.datetime) -> dt.date | None:
    """The local date a record landed on, by ``now``'s offset.

    The unit an app's answer can also be expressed in, so anything lining a typed
    entry up against a date a backend reported reads the date through this.
    Matching :func:`doit.evidence.dates_of`, or the same act would land on two
    different days depending which record carried it.
    """
    when = occurred(record)
    return None if when is None else when.astimezone(now.tzinfo).date()


def days_since(latest: dict[str, dt.datetime], names: list[str], now: dt.datetime) -> dict[str, float | None]:
    """Days elapsed per pursuit, ``None`` for one with no matching event yet."""
    elapsed: dict[str, float | None] = {}
    for name in names:
        when = latest.get(name)
        elapsed[name] = None if when is None else max((now - when).total_seconds() / 86400.0, 0.0)
    return elapsed


def checkoff_equivalent(record: dict, size: float | None) -> float:
    """One ``done`` record's share of a checkoff of ``size`` minutes.

    ``None`` is a pursuit that declares no size, whose checkoff is the entry
    itself. A timed entry carrying no duration falls back to one whole checkoff,
    since it states that the thing happened and nothing about how long.
    """
    if not size:
        return 1.0
    minutes = recorded_minutes(record)
    return 1.0 if minutes is None else minutes / size


def recorded_minutes(record: dict) -> float | None:
    """The minutes a record kept, or None where it kept none a sitting could last.

    A bool is refused though Python counts it an int, and so is anything at or
    below zero. Every reader of a duration asks here, so a balance, a forecast,
    drift and the today view all count the same entry the same way.
    """
    minutes = record.get('duration_minutes')
    if not isinstance(minutes, int | float) or isinstance(minutes, bool) or minutes <= 0:
        return None
    return float(minutes)
