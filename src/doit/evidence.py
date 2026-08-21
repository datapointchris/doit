"""What the owning apps say you already did, asked of the party that knows.

:mod:`doit.journal` records what you typed. This records what the apps observed.
Both answer one question — when did this pursuit last happen — and the later of
the two wins, because a pursuit satisfied in its own app is satisfied whether or
not you also told doit about it. Without this the ceiling on what the draw can
know is exactly what you remembered to retype, and a pursuit you do constantly
through its own CLI reads as never done.

A pursuit declares its evidence the same way it declares `resolve`, and for the
same reason: doit must not know which apps exist. `evidence` is the command,
`evidence_time` names the timestamp field on each row, `evidence_items` names the
key holding the rows when they are nested, and `evidence_where` filters rows to
the ones that count. That last one is what makes a habit usable — twenty of them
come back from one call and only one of them is the pursuit.

Every read is a subprocess against a remote API, so answers are cached and
refreshed on a TTL rather than gathered per render. A refresh that fails leaves
the previous answer standing: evidence an hour stale costs a slightly worse draw,
evidence missing costs a wrong one.

Each answer keeps the dates it was built from as well as the latest of them, so a
reader asking how often a pursuit happens has something to ask.
"""

import json
import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from datetime import datetime
from pathlib import Path

from doit.journal import parse_time

SCHEMA_VERSION = 1

# Long enough that a burst of commands makes one round of calls, short enough
# that something finished over lunch is accounted for by the afternoon.
REFRESH_TTL_SECONDS = 1800.0

# Higher than a lane's, because these are authenticated calls to remote APIs
# rather than local reads, and a pursuit dropping to "never" is worse than a
# pause.
TIMEOUT_SECONDS = 8.0

MAX_WORKERS = 6

# How far back an answer keeps its dates. `doit pursuits drift` measures ninety
# days, so it is the window a consumer would ask for, and bounding it is what
# stops one pursuit's cache growing for as long as its app has history.
OCCURRENCE_WINDOW_DAYS = 90


def cache_path(directory: Path) -> Path:
    """Derived, per-machine, and reproducible, so it lives in cache rather than state."""
    return directory / 'evidence.json'


def load(directory: Path) -> dict:
    """The last answers, or an empty document when there are none yet.

    A malformed or unreadable file reads as empty rather than fatal. This is an
    optimisation over asking the apps directly, so losing it must cost a refresh
    and nothing else.
    """
    path = cache_path(directory)
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {'schema_version': SCHEMA_VERSION, 'pursuits': {}}
    if not isinstance(payload, dict) or not isinstance(payload.get('pursuits'), dict):
        return {'schema_version': SCHEMA_VERSION, 'pursuits': {}}
    return payload


def save(directory: Path, payload: dict) -> None:
    path = cache_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def declared(pursuits: dict) -> dict[str, dict]:
    """The pursuits that name both a command and the field to read from it.

    A command without `evidence_time` cannot answer when, so it is not evidence
    and is skipped rather than guessed at.
    """
    return {name: config for name, config in pursuits.items() if config.get('evidence') and config.get('evidence_time')}


def matching(rows: list, where: dict | None) -> list:
    """Rows whose named fields all equal what the filter asks for.

    Compared as strings, because a config file has no types and an id that is 14
    in YAML and "14" in JSON is the same id.
    """
    if not where:
        return rows
    kept = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if all(str(row.get(field, '')) == str(value) for field, value in where.items()):
            kept.append(row)
    return kept


def rows_of(document, items_key: str | None) -> list:
    """The rows in whatever shape the backend answered with.

    A backend that already picked one thing returns an object rather than a list
    of one, the same way `resolve` handles it.
    """
    if items_key:
        document = document.get(items_key) if isinstance(document, dict) else None
    if isinstance(document, dict):
        return [document]
    return document if isinstance(document, list) else []


def aware(when: datetime | None, now: datetime) -> datetime | None:
    """A backend's timestamp in a comparable form.

    Some answer with an offset and some without, and a naive one compared against
    an aware `now` raises rather than sorting. An offset-less stamp is read as
    this machine's time, which is what a backend that omits one means.
    """
    if when is None:
        return None
    return when if when.tzinfo is not None else when.replace(tzinfo=now.tzinfo)


def stamps_in(document, config: dict, now: datetime) -> list[datetime]:
    """Every timestamp among the rows that count, comparable.

    One read of the rows feeds both fields an answer records. A second parser
    would drift from this one, and then the latest date and the set of dates
    would disagree about whether a pursuit happened.
    """
    rows = matching(rows_of(document, config.get('evidence_items')), config.get('evidence_where'))
    field = config['evidence_time']
    stamps = [aware(parse_time(str(row.get(field))), now) for row in rows if isinstance(row, dict) and row.get(field)]
    return [stamp for stamp in stamps if stamp is not None]


def latest_in(document, config: dict, now: datetime) -> datetime | None:
    """The most recent timestamp among the rows that count, or None."""
    found = stamps_in(document, config, now)
    return max(found) if found else None


def dates_of(stamps: list[datetime], now: datetime, window: int = OCCURRENCE_WINDOW_DAYS) -> list[str]:
    """The distinct local dates inside the window, oldest first.

    Dates rather than a count of rows, because row counts are not commensurable
    across apps: a habits backend answers with hundreds and a books backend with
    one, so counting would call reading three hundred times rarer than habits. A
    date either carries evidence or does not, whichever app was asked.

    Local by the same reading `aware` gives an offset-less stamp, so one machine's
    notion of a day is used throughout. The window ends today, so a stamp the
    backend dated ahead of now is outside it.
    """
    today = now.date()
    days = {stamp.astimezone(now.tzinfo).date() for stamp in stamps}
    return sorted(day.isoformat() for day in days if 0 <= (today - day).days < window)


def read_one(name: str, config: dict, now: datetime) -> tuple[str, dict]:
    """Ask one app when this pursuit last happened.

    Never `shell=True`: the command comes from a config file, and a tool that
    hands config text to a shell has a class of bug you cannot audit away later.
    """
    command = config['evidence']
    entry: dict = {'checked_at': now.isoformat()}
    try:
        result = subprocess.run(
            shlex.split(command),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        entry['error'] = str(error)
        return name, entry
    if result.returncode != 0:
        said = (result.stderr or result.stdout).strip().splitlines()
        entry['error'] = said[0] if said else f'exited {result.returncode}'
        return name, entry
    try:
        document = json.loads(result.stdout or 'null')
    except json.JSONDecodeError:
        entry['error'] = 'evidence did not return JSON'
        return name, entry

    stamps = stamps_in(document, config, now)
    when = max(stamps) if stamps else None
    entry['last'] = None if when is None else when.isoformat()
    entry['dates'] = dates_of(stamps, now)
    return name, entry


def stale(entry: dict | None, now: datetime, ttl: float) -> bool:
    if not entry:
        return True
    checked = parse_time(entry.get('checked_at'))
    if checked is None:
        return True
    return (now - checked).total_seconds() >= ttl


def refresh(
    pursuits: dict,
    directory: Path,
    now: datetime,
    force: bool = False,
    ttl: float = REFRESH_TTL_SECONDS,
) -> dict:
    """Bring the cache up to date, asking only the apps whose answer has aged out.

    A failed read keeps the previous `last` and its dates and records why, so one
    logged-out CLI degrades that pursuit to its journal rather than emptying the
    whole document.
    """
    payload = load(directory)
    entries = payload.setdefault('pursuits', {})
    candidates = declared(pursuits)

    # A pursuit that stopped declaring evidence stops carrying an answer, or a
    # weight edit would silently keep an old app's verdict alive. Done before the
    # nothing-to-do exit, because dropping one is itself something to do.
    dropped = [name for name in entries if name not in candidates]
    for name in dropped:
        del entries[name]

    due = {name: config for name, config in candidates.items() if force or stale(entries.get(name), now, ttl)}
    if not due and not dropped:
        return payload

    if due:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            answers = list(pool.map(lambda pair: read_one(pair[0], pair[1], now), due.items()))
        for name, entry in answers:
            previous = entries.get(name) or {}
            if 'error' in entry and previous.get('last'):
                entry['last'] = previous['last']
            if 'error' in entry and previous.get('dates'):
                entry['dates'] = previous['dates']
            entries[name] = entry

    payload['schema_version'] = SCHEMA_VERSION
    save(directory, payload)
    return payload


def observed(payload: dict) -> dict[str, datetime]:
    """Pursuit to when its app last saw it happen."""
    found = {}
    for name, entry in (payload.get('pursuits') or {}).items():
        when = parse_time((entry or {}).get('last'))
        if when is not None:
            found[name] = when
    return found


def occurrences(payload: dict) -> dict[str, list[date]]:
    """Pursuit to the distinct local dates its app reported something on.

    An answer carrying no dates reads as an empty window rather than as damage,
    and a date that will not parse is dropped rather than raising. The cache is
    derived, so anything missing from it costs a refresh and never an answer.
    """
    found = {}
    for name, entry in (payload.get('pursuits') or {}).items():
        days = (entry or {}).get('dates')
        if not isinstance(days, list):
            continue
        parsed = []
        for value in days:
            try:
                parsed.append(date.fromisoformat(str(value)))
            except ValueError:
                continue
        if parsed:
            found[name] = sorted(parsed)
    return found


def merged(typed: dict[str, datetime], seen: dict[str, datetime]) -> dict[str, datetime]:
    """The later of what you logged and what the app observed, per pursuit.

    Later rather than either alone: logging by hand stays meaningful for the
    pursuits no app can see, and an app's record stays authoritative for the ones
    it owns.
    """
    combined = dict(typed)
    for name, when in seen.items():
        current = combined.get(name)
        if current is None or when > current:
            combined[name] = when
    return combined


def problems(payload: dict) -> dict[str, str]:
    """Pursuits whose last read failed, and what it said."""
    return {name: entry['error'] for name, entry in (payload.get('pursuits') or {}).items() if entry and entry.get('error')}
