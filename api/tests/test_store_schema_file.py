"""`api/store-schema.sql` is what the store's migrations build, so a reader never has to run them to learn the tables."""

import os
import sqlite3
from pathlib import Path

from doit_api.store import Store

SCHEMA_FILE = Path(__file__).parent.parent / 'store-schema.sql'
TABLES_FIRST = "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'view' THEN 1 ELSE 2 END, name"


def test_the_published_schema_is_what_the_migrations_build(tmp_path: Path) -> None:
    Store(tmp_path / 'store.db')
    connection = sqlite3.connect(tmp_path / 'store.db')
    rows = connection.execute(f"SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' {TABLES_FIRST}").fetchall()
    connection.close()
    built = ''.join(line.rstrip() + '\n' for (statement,) in rows for line in f'{statement};'.splitlines())
    if os.environ.get('WRITE_STORE_SCHEMA'):
        SCHEMA_FILE.write_text(built)
    assert SCHEMA_FILE.read_text() == built, 'a migration changed the tables: rerun this test with WRITE_STORE_SCHEMA=1'
