"""doit's store: one SQLite database holding the config, the journal and what the service computed.

Three kinds of table live here.

**Config, versioned.** The three documents a person edits (`pursuits`,
`review`, `sources`) are kept as their text in `documents`, comments and
layout included, and each entry in them is a row of its own table. Every one of
those tables follows the SQL:2011 system-versioned temporal-table pattern: a row
carries `valid_from`, and a trigger copies the row an update or a delete replaced
into `<table>_history` with the `valid_to` that closed it. SQLite has no native
system versioning, so the triggers are the mechanism. The table can then be read
as of any instant, and two reads diff.

**Records, appended.** The journal and forecast readings are events. Each is keyed
on what identifies it, a journal entry's UUIDv7 and a reading's machine and moment,
so a write sent again adds nothing.

**Review completions, appended and versioned.** A completion is an event a person
can get wrong, so one can be taken back. Its table carries the same triggers as the
config tables, and a completion is valid from the moment it was stored. A delete
leaves the row in `review_completions_history`. `done_on` is the day it says the
thing happened, which an entry recorded late sets apart from when it was stored.

**State the service keeps.** The offered counts, the standing draw, the evidence
cache and the nudge are small rows the service rewrites. Offers are counted twice,
as a lifetime total and as a row per day, because a report over a window can only
read the second.

A timestamp the service writes is ISO 8601 UTC text with its `Z`, to the
millisecond, so a column sorts as it reads. A record's own times keep the offset
they were written with, because the local day an entry landed on is part of what
it says.

Every connection waits up to `BUSY_TIMEOUT_MS` for a writer, and the database is
in WAL mode, so a read never waits on the forecast loop's write.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import sqlite3
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

BUSY_TIMEOUT_MS = 10_000

# Each versioned table, its key column, and the columns an edit compares. The
# history table holds the same columns plus `valid_to`.
VERSIONED: dict[str, tuple[str, tuple[str, ...]]] = {
    'documents': ('name', ('text', 'edited_by')),
    'pursuits': ('name', ('settings',)),
    'pursuit_cycles': ('name', ('settings',)),
    'forecast_settings': ('singleton', ('budget_minutes',)),
    'review_entries': ('id', ('block', 'description', 'cadence', 'command', 'show')),
    'sources': ('id', ('block', 'adapter', 'command', 'timeout', 'lanes')),
    # Read by entry, which holds one row a day, so nothing writes it through `put`.
    'review_completions': ('entry', ('done_on', 'recorded_by', 'stored_at')),
}

FIRST_VERSIONED_TABLES = ('documents', 'pursuits', 'pursuit_cycles', 'forecast_settings', 'review_entries', 'sources')

# A pursuit's settings are the mapping the pursuits document declares, held as JSON so a
# pursuit reads back exactly as it was written. The facts a question about the
# pursuits over time asks of are columns derived from it.
PURSUIT_COLUMNS = """
    settings TEXT NOT NULL CHECK (json_valid(settings)),
    description TEXT GENERATED ALWAYS AS (json_extract(settings, '$.description')) VIRTUAL,
    weight NUMERIC GENERATED ALWAYS AS (json_extract(settings, '$.weight')) VIRTUAL,
    cadence TEXT GENERATED ALWAYS AS (json_extract(settings, '$.cadence')) VIRTUAL,
    weekly_minutes INTEGER GENERATED ALWAYS AS (json_extract(settings, '$.weekly_minutes')) VIRTUAL,
    until TEXT GENERATED ALWAYS AS (json_extract(settings, '$.until')) VIRTUAL,
    paused INTEGER GENERATED ALWAYS AS (json_extract(settings, '$.paused')) VIRTUAL,
"""

CYCLE_COLUMNS = """
    settings TEXT NOT NULL CHECK (json_valid(settings)),
    until TEXT GENERATED ALWAYS AS (json_extract(settings, '$.until')) VIRTUAL,
"""

TABLE_COLUMNS = {
    'documents': """
        name TEXT PRIMARY KEY CHECK (name IN ('pursuits', 'review', 'sources')),
        text TEXT NOT NULL,
        edited_by TEXT NOT NULL,
    """,
    'pursuits': f'name TEXT PRIMARY KEY, {PURSUIT_COLUMNS}',
    'pursuit_cycles': f'name TEXT PRIMARY KEY, {CYCLE_COLUMNS}',
    'forecast_settings': """
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        budget_minutes INTEGER CHECK (budget_minutes IS NULL OR budget_minutes > 0),
    """,
    'review_entries': """
        id TEXT PRIMARY KEY,
        block TEXT NOT NULL CHECK (block IN ('items', 'practice')),
        description TEXT,
        cadence TEXT,
        command TEXT,
        show TEXT,
    """,
    'sources': """
        id TEXT PRIMARY KEY,
        block TEXT NOT NULL CHECK (block IN ('sources', 'completions')),
        adapter TEXT,
        command TEXT CHECK (command IS NULL OR json_valid(command)),
        timeout REAL,
        lanes TEXT CHECK (lanes IS NULL OR json_valid(lanes)),
    """,
    'review_completions': """
        entry TEXT NOT NULL,
        done_on TEXT NOT NULL CHECK (done_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
        recorded_by TEXT NOT NULL,
        stored_at TEXT NOT NULL,
    """,
}


def history_columns(table: str) -> str:
    """The history table's columns: the table's own, its key no longer unique."""
    key = VERSIONED[table][0]
    columns = TABLE_COLUMNS[table].replace(' PRIMARY KEY', ' NOT NULL', 1)
    return (
        f'{columns} valid_from TEXT NOT NULL, valid_to TEXT NOT NULL, CHECK (valid_from <= valid_to), UNIQUE ({key}, valid_from, valid_to)'
    )


def history_schema(table: str) -> str:
    """The history table a versioned table keeps its replaced rows in, and the triggers that fill it."""
    key, values = VERSIONED[table]
    copied = ', '.join((key, *values))
    old = ', '.join(f'OLD.{column}' for column in (key, *values))
    return f"""
    CREATE TABLE {table}_history ({history_columns(table)});
    CREATE INDEX {table}_history_by_key ON {table}_history ({key}, valid_from);
    CREATE TRIGGER {table}_keeps_what_an_update_replaced AFTER UPDATE ON {table}
    BEGIN
        INSERT INTO {table}_history ({copied}, valid_from, valid_to) VALUES ({old}, OLD.valid_from, NEW.valid_from);
    END;
    CREATE TRIGGER {table}_keeps_what_a_delete_removed AFTER DELETE ON {table}
    BEGIN
        INSERT INTO {table}_history ({copied}, valid_from, valid_to)
        VALUES ({old}, OLD.valid_from, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
    END;
    """


def versioned_schema(table: str) -> str:
    return f'CREATE TABLE {table} ({TABLE_COLUMNS[table]} valid_from TEXT NOT NULL);' + history_schema(table)


RECORDS_SCHEMA = """
CREATE TABLE journal_events (
    id TEXT PRIMARY KEY,
    pursuit TEXT NOT NULL,
    event TEXT NOT NULL CHECK (event IN ('done', 'skip', 'reset', 'pace')),
    occurred_at TEXT NOT NULL,
    machine TEXT NOT NULL,
    actor TEXT,
    record TEXT NOT NULL CHECK (json_valid(record)),
    recorded_by TEXT NOT NULL,
    stored_at TEXT NOT NULL
);
CREATE INDEX journal_events_by_time ON journal_events (occurred_at);
CREATE INDEX journal_events_by_pursuit ON journal_events (pursuit, occurred_at);

CREATE TABLE review_completions (
    id INTEGER PRIMARY KEY,
    entry TEXT NOT NULL,
    done_on TEXT NOT NULL CHECK (done_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    recorded_by TEXT NOT NULL,
    stored_at TEXT NOT NULL,
    UNIQUE (entry, done_on)
);

CREATE TABLE forecast_readings (
    machine TEXT NOT NULL,
    generated TEXT NOT NULL,
    generated_utc TEXT NOT NULL,
    reading TEXT NOT NULL CHECK (json_valid(reading)),
    stored_at TEXT NOT NULL,
    PRIMARY KEY (machine, generated)
);
CREATE INDEX forecast_readings_by_time ON forecast_readings (generated_utc);

CREATE TABLE offer_counts (
    machine TEXT NOT NULL,
    pursuit TEXT NOT NULL,
    offered INTEGER NOT NULL CHECK (offered >= 0),
    PRIMARY KEY (machine, pursuit)
);

CREATE TABLE draws (
    draw_id TEXT PRIMARY KEY,
    created_utc TEXT NOT NULL,
    selection TEXT NOT NULL CHECK (json_valid(selection))
);
CREATE INDEX draws_by_time ON draws (created_utc);

CREATE TABLE evidence_cache (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    payload TEXT NOT NULL CHECK (json_valid(payload)),
    updated_at TEXT NOT NULL
);

CREATE TABLE nudge (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    interval_minutes INTEGER CHECK (interval_minutes IS NULL OR interval_minutes > 0),
    last_nudged_at TEXT
);

CREATE TABLE imports (
    id INTEGER PRIMARY KEY,
    imported_at TEXT NOT NULL,
    imported_by TEXT NOT NULL,
    summary TEXT NOT NULL CHECK (json_valid(summary))
);
"""

# A generated `valid_from`, so the release still serving during a rollout can insert a completion.
COMPLETION_HISTORY_SCHEMA = """
ALTER TABLE review_completions ADD COLUMN valid_from TEXT GENERATED ALWAYS AS (stored_at) VIRTUAL;
""" + history_schema('review_completions')

OFFERS_BY_DAY_SCHEMA = """
CREATE TABLE offer_counts_by_day (
    day TEXT NOT NULL CHECK (day GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    machine TEXT NOT NULL,
    pursuit TEXT NOT NULL,
    offered INTEGER NOT NULL CHECK (offered > 0),
    PRIMARY KEY (day, machine, pursuit)
);
"""

# Applied in order, each once. `PRAGMA user_version` holds how many have run. A
# change to the schema is appended here, never made to an entry a database has run.
MIGRATIONS = (
    ''.join(versioned_schema(table) for table in FIRST_VERSIONED_TABLES) + RECORDS_SCHEMA,
    COMPLETION_HISTORY_SCHEMA,
    OFFERS_BY_DAY_SCHEMA,
)


def stamp(at: dt.datetime) -> str:
    """An instant as the store writes it: ISO 8601 UTC to the millisecond, with its `Z`."""
    return at.astimezone(dt.UTC).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def utc_of(text: str) -> str:
    """A record's own ISO 8601 time, offset and all, as the store's UTC text; the text itself where it does not parse."""
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return stamp(parsed)


@dataclass(frozen=True)
class Document:
    name: str
    text: str
    edited_by: str
    valid_from: str


class Store:
    """The database at `path`, migrated to the newest schema when it opens."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.writing = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute('PRAGMA journal_mode = WAL')
            ran = connection.execute('PRAGMA user_version').fetchone()[0]
            for number, migration in enumerate(MIGRATIONS[ran:], start=ran + 1):
                connection.executescript(f'BEGIN; {migration} PRAGMA user_version = {number}; COMMIT;')

    @contextlib.contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    @contextlib.contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """One write transaction, taken at once so two writers queue rather than deadlock on an upgrade."""
        with self.writing, self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                yield connection
            except BaseException:
                connection.execute('ROLLBACK')
                raise
            connection.execute('COMMIT')

    def ping(self) -> None:
        with self.connect() as connection:
            connection.execute('SELECT 1').fetchone()

    # --- config ---------------------------------------------------------------

    def document(self, name: str) -> Document | None:
        with self.connect() as connection:
            row = connection.execute('SELECT name, text, edited_by, valid_from FROM documents WHERE name = ?', (name,)).fetchone()
        return None if row is None else Document(**dict(row))

    def document_versions(self, name: str) -> list[dict]:
        """Every version of a document, oldest first, the current one last with no `valid_to`."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT text, edited_by, valid_from, valid_to FROM documents_history WHERE name = ?
                UNION ALL
                SELECT text, edited_by, valid_from, NULL FROM documents WHERE name = ?
                ORDER BY valid_from
                """,
                (name, name),
            ).fetchall()
        return [dict(row) for row in rows]

    def rows(self, table: str, at: str | None = None) -> list[dict]:
        """A versioned table's rows, as they stand or as they stood at the instant `at`."""
        key, values = VERSIONED[table]
        columns = ', '.join((key, *values, 'valid_from'))
        with self.connect() as connection:
            if at is None:
                found = connection.execute(f'SELECT {columns} FROM {table} ORDER BY rowid').fetchall()
            else:
                found = connection.execute(
                    f"""
                    SELECT {columns} FROM {table} WHERE valid_from <= :at
                    UNION ALL
                    SELECT {columns} FROM {table}_history WHERE valid_from <= :at AND :at < valid_to
                    """,
                    {'at': at},
                ).fetchall()
        return [dict(row) for row in found]

    def history(self, table: str, key_value: str | None = None) -> list[dict]:
        """Every version of a versioned table's rows, oldest first, the current ones with no `valid_to`."""
        key, values = VERSIONED[table]
        columns = ', '.join((key, *values, 'valid_from'))
        where = f'WHERE {key} = :key' if key_value is not None else ''
        with self.connect() as connection:
            found = connection.execute(
                f"""
                SELECT {columns}, valid_to FROM {table}_history {where}
                UNION ALL
                SELECT {columns}, NULL AS valid_to FROM {table} {where}
                ORDER BY valid_from, {key}
                """,
                {'key': key_value},
            ).fetchall()
        return [dict(row) for row in found]

    @staticmethod
    def replace(connection: sqlite3.Connection, table: str, wanted: dict[object, dict], at: str) -> dict[str, list]:
        """Make a versioned table hold exactly `wanted`, keyed by its key column, and say what moved."""
        key, _ = VERSIONED[table]
        moved: dict[str, list] = {'added': [], 'changed': [], 'removed': []}
        held = [row[key] for row in connection.execute(f'SELECT {key} FROM {table}')]
        for gone in [name for name in held if name not in wanted]:
            connection.execute(f'DELETE FROM {table} WHERE {key} = ?', (gone,))
            moved['removed'].append(gone)
        for name, row in wanted.items():
            outcome = Store.put(connection, table, name, row, at)
            if outcome != 'unchanged':
                moved[outcome].append(name)
        return moved

    @staticmethod
    def put(connection: sqlite3.Connection, table: str, key_value: object, row: dict, at: str) -> str:
        """Write one row of a versioned table, and say whether it was `added`, `changed` or `unchanged`.

        A row whose values are unchanged is left alone, so its history does not
        grow a version that changed nothing.
        """
        key, values = VERSIONED[table]
        ordered = [row.get(column) for column in values]
        held = connection.execute(f'SELECT {", ".join(values)} FROM {table} WHERE {key} = ?', (key_value,)).fetchone()
        if held is None:
            placeholders = ', '.join('?' for _ in range(len(values) + 2))
            columns = ', '.join((key, *values, 'valid_from'))
            connection.execute(f'INSERT INTO {table} ({columns}) VALUES ({placeholders})', (key_value, *ordered, at))
            return 'added'
        if [held[column] for column in values] == ordered:
            return 'unchanged'
        assignments = ', '.join(f'{column} = ?' for column in values)
        connection.execute(f'UPDATE {table} SET {assignments}, valid_from = ? WHERE {key} = ?', (*ordered, at, key_value))
        return 'changed'

    # --- the journal ----------------------------------------------------------

    def journal(self) -> list[dict]:
        """Every journal entry, ordered by the instant it says the thing happened."""
        with self.connect() as connection:
            rows = connection.execute('SELECT record FROM journal_events ORDER BY occurred_at, id').fetchall()
        return [json.loads(row['record']) for row in rows]

    @staticmethod
    def append_records(connection: sqlite3.Connection, records: list[dict], recorded_by: str, at: str) -> int:
        """Store each record not already held by its id, and say how many were new."""
        added = 0
        for record in records:
            cursor = connection.execute(
                """
                INSERT INTO journal_events (id, pursuit, event, occurred_at, machine, actor, record, recorded_by, stored_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    record['id'],
                    str(record.get('pursuit') or ''),
                    record['event'],
                    utc_of(str(record.get('occurred_at') or record.get('logged_at') or '')),
                    str(record.get('machine') or ''),
                    record.get('actor'),
                    json.dumps(record, separators=(',', ':'), default=str),
                    recorded_by,
                    at,
                ),
            )
            added += cursor.rowcount
        return added

    def append(self, records: list[dict], recorded_by: str, now: dt.datetime) -> int:
        if not records:
            return 0
        with self.write() as connection:
            return self.append_records(connection, records, recorded_by, stamp(now))

    # --- the review register's completions ------------------------------------

    def review_stamps(self) -> dict[str, str]:
        """Each entry's newest completion day, the map a review reads as `last`."""
        with self.connect() as connection:
            rows = connection.execute('SELECT entry, MAX(done_on) AS last FROM review_completions GROUP BY entry').fetchall()
        return {row['entry']: row['last'] for row in rows}

    @staticmethod
    def add_completion(connection: sqlite3.Connection, entry: str, day: str, recorded_by: str, at: str) -> int:
        cursor = connection.execute(
            'INSERT INTO review_completions (entry, done_on, recorded_by, stored_at) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING',
            (entry, day, recorded_by, at),
        )
        return cursor.rowcount

    @staticmethod
    def remove_completion(connection: sqlite3.Connection, entry: str, day: str) -> int:
        """Take back one day's completion, which the table's trigger keeps in history."""
        return connection.execute('DELETE FROM review_completions WHERE entry = ? AND done_on = ?', (entry, day)).rowcount

    def review_completions(self, entry: str) -> list[dict]:
        """Every completion an entry holds, oldest day first."""
        with self.connect() as connection:
            rows = connection.execute(
                'SELECT entry, done_on, recorded_by, stored_at FROM review_completions WHERE entry = ? ORDER BY done_on', (entry,)
            ).fetchall()
        return [dict(row) for row in rows]

    # --- forecast readings ----------------------------------------------------

    def readings(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute('SELECT reading FROM forecast_readings ORDER BY generated_utc').fetchall()
        return [json.loads(row['reading']) for row in rows]

    @staticmethod
    def add_reading(connection: sqlite3.Connection, reading: dict, at: str) -> int:
        cursor = connection.execute(
            """
            INSERT INTO forecast_readings (machine, generated, generated_utc, reading, stored_at) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            (reading['machine'], reading['generated'], utc_of(reading['generated']), json.dumps(reading), at),
        )
        return cursor.rowcount

    # --- offered counts -------------------------------------------------------

    def offer_counts(self) -> dict[str, dict[str, int]]:
        """Offered counts by machine, then pursuit."""
        found: dict[str, dict[str, int]] = {}
        with self.connect() as connection:
            for row in connection.execute('SELECT machine, pursuit, offered FROM offer_counts ORDER BY machine, pursuit'):
                found.setdefault(row['machine'], {})[row['pursuit']] = row['offered']
        return found

    def offers_by_day(self) -> dict[str, dict[dt.date, int]]:
        """Each pursuit's offers on each day, summed over every machine."""
        found: dict[str, dict[dt.date, int]] = {}
        with self.connect() as connection:
            for row in connection.execute('SELECT pursuit, day, SUM(offered) AS offered FROM offer_counts_by_day GROUP BY pursuit, day'):
                found.setdefault(row['pursuit'], {})[dt.date.fromisoformat(row['day'])] = row['offered']
        return found

    def offers_since(self, first_day: dt.date) -> dict[str, dict[str, int]]:
        """Offered counts from `first_day` on, by machine, then pursuit."""
        found: dict[str, dict[str, int]] = {}
        with self.connect() as connection:
            for row in connection.execute(
                """
                SELECT machine, pursuit, SUM(offered) AS offered FROM offer_counts_by_day WHERE day >= ?
                GROUP BY machine, pursuit ORDER BY machine, pursuit
                """,
                (first_day.isoformat(),),
            ):
                found.setdefault(row['machine'], {})[row['pursuit']] = row['offered']
        return found

    def bump_offers(self, machine: str, names: list[str], day: dt.date) -> None:
        with self.write() as connection:
            for name in names:
                connection.execute(
                    """
                    INSERT INTO offer_counts (machine, pursuit, offered) VALUES (?, ?, 1)
                    ON CONFLICT (machine, pursuit) DO UPDATE SET offered = offered + 1
                    """,
                    (machine, name),
                )
                connection.execute(
                    """
                    INSERT INTO offer_counts_by_day (day, machine, pursuit, offered) VALUES (?, ?, ?, 1)
                    ON CONFLICT (day, machine, pursuit) DO UPDATE SET offered = offered + 1
                    """,
                    (day.isoformat(), machine, name),
                )

    # --- the standing draw ----------------------------------------------------

    def newest_draw(self) -> dict | None:
        with self.connect() as connection:
            row = connection.execute('SELECT selection FROM draws ORDER BY created_utc DESC LIMIT 1').fetchone()
        return None if row is None else json.loads(row['selection'])

    def draw(self, draw_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute('SELECT selection FROM draws WHERE draw_id = ?', (draw_id,)).fetchone()
        return None if row is None else json.loads(row['selection'])

    def save_draw(self, selection: dict) -> None:
        with self.write() as connection:
            connection.execute(
                """
                INSERT INTO draws (draw_id, created_utc, selection) VALUES (?, ?, ?)
                ON CONFLICT (draw_id) DO UPDATE SET selection = excluded.selection
                """,
                (selection['draw_id'], utc_of(selection['created_at']), json.dumps(selection, default=str)),
            )

    # --- the evidence cache ---------------------------------------------------

    def evidence_cache(self) -> dict:
        with self.connect() as connection:
            row = connection.execute('SELECT payload FROM evidence_cache WHERE singleton = 1').fetchone()
        return {} if row is None else json.loads(row['payload'])

    def save_evidence_cache(self, payload: dict, now: dt.datetime) -> None:
        with self.write() as connection:
            connection.execute(
                """
                INSERT INTO evidence_cache (singleton, payload, updated_at) VALUES (1, ?, ?)
                ON CONFLICT (singleton) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at
                """,
                (json.dumps(payload), stamp(now)),
            )

    # --- the nudge ------------------------------------------------------------

    def nudge(self) -> dict:
        with self.connect() as connection:
            row = connection.execute('SELECT interval_minutes, last_nudged_at FROM nudge WHERE singleton = 1').fetchone()
        return {'interval_minutes': None, 'last_nudged_at': None} if row is None else dict(row)

    @staticmethod
    def set_nudge(connection: sqlite3.Connection, *, interval_minutes: int | None = None, last_nudged_at: str | None = None) -> None:
        """Set whichever half is given, keeping the other; a nudge time only moves later."""
        connection.execute('INSERT INTO nudge (singleton) VALUES (1) ON CONFLICT DO NOTHING')
        if interval_minutes is not None:
            connection.execute('UPDATE nudge SET interval_minutes = ? WHERE singleton = 1', (interval_minutes,))
        if last_nudged_at is not None:
            connection.execute(
                'UPDATE nudge SET last_nudged_at = ? WHERE singleton = 1 AND (last_nudged_at IS NULL OR last_nudged_at < ?)',
                (last_nudged_at, last_nudged_at),
            )
