"""Append-only event journal, one file per machine, merged on read.

The counterpart to :mod:`doit.state`, and the reason it exists separately:
``state.py`` holds a small map that is rewritten whole, so losing a version costs
one date that the next ``done`` restores. A journal is the record that cannot be
reconstructed, and it is written from every machine, so it cannot share that
exposure.

The exposure is Syncthing's, and it is worth stating precisely because the fix
looks arbitrary otherwise. Syncthing resolves conflicts **per file, not per line** —
it mirrors files, it does not merge text the way git does. When two machines each
hold a version of one file the other has not seen, newest-modification wins and the
loser is set aside whole as ``.sync-conflict-<date>-<device>``, unread. For an
append-only log that divergence is not a rare race but the ordinary shape of a day:
log something on the laptop, close the lid before it syncs, log something at the
desktop, reopen the laptop, and one side loses its entire tail.

So every machine appends only to its own file, named for its bare hostname. No two
devices ever hold divergent versions of the same file, Syncthing has nothing to
resolve, and reads take the union. At a handful of entries a day the merge is
microseconds and the log stays greppable, which is why this is JSONL rather than
the SQLite the fleet standard would otherwise call for.

Records are self-describing: each carries its own ``schema_version``, because files
written by machines running different versions of the tool end up merged in one
read.
"""

import json
import random
import uuid
from datetime import date
from datetime import datetime
from pathlib import Path

from doit.state import load_state
from doit.state import save_state

SCHEMA_VERSION = 1

# Trailing window for the measured logging rate. Long enough that a quiet week
# does not swing the implied intervals, short enough to follow a real change of
# pace within a month.
RATE_WINDOW_DAYS = 30


def journal_path(directory: Path, machine: str) -> Path:
    """This machine's journal file. One writer per file is the whole sync story."""
    return directory / f'next-log-{machine}.jsonl'


def counts_path(directory: Path, machine: str) -> Path:
    """This machine's offered-counts file, summed with its siblings on read."""
    return directory / f'next-offers-{machine}.json'


def new_id(now: datetime, rng: random.Random | None = None) -> str:
    """A UUIDv7: 48-bit millisecond timestamp, then randomness.

    Time-ordered, so ids sort into the order the events happened even after files
    from several machines are merged, and collision-safe without those machines
    coordinating. Hand-built because :mod:`uuid` only grew ``uuid7`` in 3.14.
    """
    rng = rng or random.SystemRandom()
    timestamp_ms = int(now.timestamp() * 1000) & 0xFFFFFFFFFFFF
    value = (timestamp_ms << 80) | (0x7 << 76) | (rng.getrandbits(12) << 64) | (0b10 << 62) | rng.getrandbits(62)
    return str(uuid.UUID(int=value))


def append(path: Path, record: dict) -> dict:
    """Append one record as a JSON line, stamping the schema version.

    A lone ``write`` on a file opened for append is a single small write on every
    platform this runs on, so a record cannot interleave with another process's.
    There is no read-modify-write here — that is the point of an append-only file.
    """
    stamped = {'schema_version': SCHEMA_VERSION, **record}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(stamped, separators=(',', ':')) + '\n')
    return stamped


def read_all(directory: Path) -> list[dict]:
    """Every machine's records, merged and ordered by when the event happened.

    A malformed line is skipped rather than fatal: the journal is the historical
    record, and one bad line from a half-written sync must not make the rest of it
    unreadable.
    """
    records = []
    for path in sorted(directory.glob('next-log-*.jsonl')):
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    records.sort(key=lambda record: record.get('occurred_at') or record.get('logged_at') or '')
    return records


def parse_time(value: str | None) -> datetime | None:
    """An ISO 8601 timestamp from a record, or None if absent or unparsable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def latest_occurrence(records: list[dict], event: str) -> dict[str, datetime]:
    """Most recent time per pursuit for one event kind (``done``, ``skip``, …)."""
    latest: dict[str, datetime] = {}
    for record in records:
        if record.get('event') != event:
            continue
        pursuit = record.get('pursuit')
        when = parse_time(record.get('occurred_at') or record.get('logged_at'))
        if not pursuit or when is None:
            continue
        if pursuit not in latest or when > latest[pursuit]:
            latest[pursuit] = when
    return latest


def earliest_occurrence(records: list[dict], event: str) -> dict[str, datetime]:
    """Oldest time per pursuit for one event kind.

    The counterpart to :func:`latest_occurrence`, and what a balance falls back to
    for its origin. A pursuit nobody has zeroed is still answerable from the first
    thing it ever recorded, which is the only honest starting point available
    without asking.
    """
    earliest: dict[str, datetime] = {}
    for record in records:
        if record.get('event') != event:
            continue
        pursuit = record.get('pursuit')
        when = parse_time(record.get('occurred_at') or record.get('logged_at'))
        if not pursuit or when is None:
            continue
        if pursuit not in earliest or when < earliest[pursuit]:
            earliest[pursuit] = when
    return earliest


def local_day(record: dict, now: datetime) -> date | None:
    """The local date a record landed on, by ``now``'s offset.

    The unit an app's answer can also be expressed in, so anything lining a typed
    entry up against a date a backend reported reads the date through this.
    Matching :func:`doit.evidence.dates_of`, or the same act would land on two
    different days depending which record carried it.
    """
    when = parse_time(record.get('occurred_at') or record.get('logged_at'))
    return None if when is None else when.astimezone(now.tzinfo).date()


def days(records: list[dict], pursuit: str, event: str, now: datetime) -> list[date]:
    """The distinct local dates one pursuit has a matching record on."""
    found = set()
    for record in records:
        if record.get('event') != event or record.get('pursuit') != pursuit:
            continue
        day = local_day(record, now)
        if day is not None:
            found.add(day)
    return sorted(found)


def days_since(latest: dict[str, datetime], names: list[str], now: datetime) -> dict[str, float | None]:
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
    which is what it claimed when it was typed.
    """
    if not size:
        return 1.0
    minutes = record.get('duration_minutes')
    if not isinstance(minutes, int | float) or isinstance(minutes, bool) or minutes <= 0:
        return 1.0
    return float(minutes) / size


def rate_per_day(records: list[dict], now: datetime, sizes: dict[str, float]) -> float | None:
    """Measured checkoff-equivalents per day, or ``None`` when there is nothing to measure.

    Equivalents rather than entries, because this one number divides every
    pursuit's implied interval. An hour of reading typed as four fragments would
    otherwise count four times what the same hour counts as one sitting, and every
    interval in the register would halve on the strength of how the typing went.
    A 20-minute read against a 45-minute checkoff contributes 0.44.

    ``sizes`` maps each pursuit measured in time to its checkoff size in minutes.
    Anything absent from it counts one per entry, which is what a pursuit
    satisfied in occurrences means by a log.

    The divisor is how long the journal has actually been running, capped at the
    window — dividing a young journal's entries by a full 30 days would report a
    pace far below the real one and stretch every implied interval to match.
    """
    within = []
    for record in records:
        if record.get('event') != 'done':
            continue
        when = parse_time(record.get('occurred_at') or record.get('logged_at'))
        if when is None or (now - when).days >= RATE_WINDOW_DAYS:
            continue
        within.append((when, checkoff_equivalent(record, sizes.get(str(record.get('pursuit'))))))
    if not within:
        return None
    span_days = (now - min(when for when, _ in within)).total_seconds() / 86400.0
    return sum(amount for _, amount in within) / max(min(span_days, float(RATE_WINDOW_DAYS)), 1.0)


def load_counts(directory: Path) -> dict[str, int]:
    """Offered counts summed across every machine's file.

    Counts are per-machine for the same reason the journal is: a shared counter
    file would have two writers and would silently lose increments, and a statistic
    that quietly undercounts is worse than none.
    """
    totals: dict[str, int] = {}
    for path in sorted(directory.glob('next-offers-*.json')):
        for name, count in load_state(path).items():
            totals[name] = totals.get(name, 0) + int(count)
    return totals


def counts_by_machine(directory: Path) -> dict[str, dict[str, int]]:
    """Offered counts kept per machine rather than summed.

    :func:`load_counts` answers how often something came up. This answers where
    the record sits, which is what a reader needs when the repair is to edit a
    file and the file belongs to another box.
    """
    found: dict[str, dict[str, int]] = {}
    for path in sorted(directory.glob('next-offers-*.json')):
        found[path.stem.removeprefix('next-offers-')] = {name: int(count) for name, count in load_state(path).items()}
    return found


def bump_counts(path: Path, names: list[str]) -> dict[str, int]:
    """Increment this machine's offered count for each named pursuit."""
    counts = {name: int(count) for name, count in load_state(path).items()}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    save_state(path, counts)
    return counts
