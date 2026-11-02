"""Which machines' checks are failing, read from the copy table of desk reports in ClickHouse.

Each desk's upkeep runs its checks on a timer and ships each result as a log
record: the check's key, `pass` or `fail`, a one-line summary, and the machine as
the resource's `host.name`. A materialized view copies those records into
`otel.desk_reports`, and the service's account is granted `SELECT` on that table
alone.

The lane is each machine's newest result for each check over the last
`LOOKBACK_HOURS`. A machine with a failing check is a row naming the check and
its summary. A machine whose checks all pass is counted and not listed, so the
lane names only what needs a look.

The answer is a lane document, which the dashboard draws with no adapter.
"""

from __future__ import annotations

import datetime as dt
import json

import httpx2

from doit import lanes
from doit.lanes import Lane
from doit.lanes import Row
from doit.lanes import Urgency

from ..settings import USER_AGENT
from ..settings import ClickHouseSettings
from .apps import ReadFailed

TIMEOUT_SECONDS = 10.0

# Long enough that a machine asleep overnight still reports what its last
# checks said, short enough that one retired a while ago drops out.
LOOKBACK_HOURS = 48

# The newest outcome and summary per machine and check. Times cross as integer
# milliseconds cast to text, because a DateTime64 in JSON carries no zone.
QUERY = """
SELECT
    ResourceAttributes['host.name'] AS machine,
    LogAttributes['fleet.check.key'] AS check_key,
    argMax(LogAttributes['fleet.check.outcome'], Timestamp) AS outcome,
    argMax(LogAttributes['fleet.check.summary'], Timestamp) AS summary,
    toString(toUnixTimestamp64Milli(max(Timestamp))) AS reported_ms
FROM otel.desk_reports
WHERE Kind = 'desk.check' AND InsertedAt > now64(6) - toIntervalHour({hours:UInt32})
GROUP BY machine, check_key
ORDER BY machine, check_key
FORMAT JSONEachRow
"""


def checks(settings: ClickHouseSettings, client: httpx2.Client | None = None) -> list[dict]:
    """Each machine's newest result per check, one dict per machine and check."""
    if settings.missing:
        raise ReadFailed(f'ClickHouse is not configured on the service: {settings.missing} unset')
    http = client or httpx2.Client(timeout=TIMEOUT_SECONDS)
    try:
        response = http.post(
            settings.url + '/',
            params={'param_hours': str(LOOKBACK_HOURS)},
            content=QUERY.encode(),
            headers={'X-ClickHouse-User': settings.user, 'X-ClickHouse-Key': settings.password, 'User-Agent': USER_AGENT},
            timeout=TIMEOUT_SECONDS,
        )
    except httpx2.TimeoutException as error:
        raise ReadFailed(f'ClickHouse did not answer the desk check read within {TIMEOUT_SECONDS:g}s', timed_out=True) from error
    except httpx2.HTTPError as error:
        raise ReadFailed(f'ClickHouse could not be reached for the desk check read: {error}') from error
    if response.status_code >= 400:
        said = response.text.strip().splitlines()[0] if response.text.strip() else 'no message'
        raise ReadFailed(f'ClickHouse answered {response.status_code} to the desk check read: {said}')
    try:
        return [json.loads(line) for line in response.text.splitlines() if line.strip()]
    except ValueError as error:
        raise ReadFailed('ClickHouse answered the desk check read with lines that are not JSON') from error


def reported_ago(reported_ms: str, now: dt.datetime) -> str:
    try:
        reported = dt.datetime.fromtimestamp(int(reported_ms) / 1000, dt.UTC)
    except (TypeError, ValueError):
        return ''
    hours = max(int((now - reported).total_seconds() // 3600), 0)
    return f'{hours}h ago' if hours < 48 else f'{hours // 24}d ago'


def desk_checks_lane(rows: list[dict], now: dt.datetime) -> Lane:
    """The lane: one row per failing check, grouped by machine."""
    machines = sorted({row.get('machine', '') for row in rows if row.get('machine')})
    failing = [row for row in rows if row.get('machine') and row.get('outcome') != 'pass']
    broken = sorted({row['machine'] for row in failing})
    return Lane(
        name='dotfiles',
        title='DOTFILES',
        meta=f'{len(machines)} machine(s) reporting' if not broken else f'{len(broken)} of {len(machines)} need a look',
        rows=[
            Row(
                row['machine'],
                f'{row.get("check_key", "")}: {row.get("summary") or row.get("outcome", "")}',
                reported_ago(row.get('reported_ms', ''), now),
                Urgency.DUE,
            )
            for row in failing
        ],
        total=len(failing),
        hints=['dotfiles check'] if broken else [f'{len(machines)} machine(s) clean'],
    )


def desk_checks_document(settings: ClickHouseSettings, now: dt.datetime, client: httpx2.Client | None = None) -> dict:
    return lanes.to_document([desk_checks_lane(checks(settings, client), now)], now)
