"""Tests for doit.usage_table — one machine's usage, written for a digest on another box.

Two things decide whether a reading built from these files is true. Each host
has to arrive exactly once, however many exports hold it and however stale one
of them is. And a file that cannot be read has to say so, because a digest that
skips it reports the hosts it held as idle.
"""

import json
from pathlib import Path

from doit import usage
from doit import usage_table
from doit.index import Entry
from doit.observe import Invocation
from doit.observe import ShellHistory


def row(typed: str, count: int = 3, last: str = '2026-08-10') -> usage.Row:
    return usage.Row(typed=typed, sources=('tool',), names=(typed,), count=count, last=last)


def host(name: str, through: str, count: int = 3, commands: int = 10) -> usage_table.HostUsage:
    return usage_table.HostUsage(host=name, through=through, commands=commands, rows=(row('rg', count=count, last=through),))


def table(machine: str, *hosts: usage_table.HostUsage, generated: str = '2026-08-12T09:00:00+00:00') -> usage_table.UsageTable:
    return usage_table.UsageTable(machine=machine, generated=generated, history='atuin', hosts=hosts)


def written(directory: Path, machine: str, document: object) -> Path:
    path = usage_table.table_path(directory, machine)
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document))
    return path


def test_each_host_in_a_history_is_measured_on_its_own():
    """atuin carries every desk's commands, so one export answers for each desk separately."""
    history = ShellHistory(
        'atuin',
        (
            Invocation('2026-08-01', 'archlinux', 'rg needle'),
            Invocation('2026-08-09', 'mbp', 'rg other'),
            Invocation('2026-08-10', 'mbp', 'rg third'),
        ),
    )
    kit = [Entry(source='tool', name='ripgrep', invocation='rg [pattern]')]

    built = usage_table.build(history, kit, 'archlinux', '2026-08-12T09:00:00+00:00')

    by_host = {entry.host: entry for entry in built.hosts}
    assert (by_host['archlinux'].rows[0].count, by_host['archlinux'].through) == (1, '2026-08-01')
    assert (by_host['mbp'].rows[0].count, by_host['mbp'].through, by_host['mbp'].commands) == (2, '2026-08-10', 2)


def test_an_export_reads_back_as_the_table_that_was_written(tmp_path):
    exported = table('archlinux', host('archlinux', '2026-08-10'), host('mbp', '2026-08-09'))

    usage_table.write(tmp_path, exported)

    assert usage_table.read_all(tmp_path) == usage_table.Found([exported], [])


def test_a_host_two_exports_hold_is_taken_from_the_history_that_runs_latest():
    """A desk whose sync stalled writes a fresh file around stale rows, so the file's date cannot decide."""
    stalled = table(
        'archlinux', host('archlinux', '2026-10-08'), host('mbp', '2026-09-01', count=50), generated='2026-10-08T09:00:00+00:00'
    )
    current = table('mbp', host('mbp', '2026-10-07', count=80), generated='2026-10-07T09:00:00+00:00')

    chosen = usage_table.freshest([stalled, current])

    assert {name: taken.machine for name, (taken, _) in chosen.items()} == {'archlinux': 'archlinux', 'mbp': 'mbp'}
    assert {row.typed: row.count for row in usage.combine(entry.rows for _, entry in chosen.values())} == {'rg': 83}


def test_a_host_two_exports_hold_alike_is_counted_once():
    first = table('archlinux', host('mbp', '2026-10-07'), generated='2026-10-07T09:00:00+00:00')
    second = table('scheduler-lxc', host('mbp', '2026-10-07'), generated='2026-10-08T09:00:00+00:00')

    chosen = usage_table.freshest([first, second])

    assert chosen['mbp'][0].machine == 'scheduler-lxc'
    assert usage.combine(entry.rows for _, entry in chosen.values())[0].count == 3


def test_a_file_naming_another_machine_is_unreadable(tmp_path):
    """A table copied under a second name would otherwise count one history twice."""
    document = usage_table.to_document(table('archlinux', host('archlinux', '2026-08-10')))
    written(tmp_path, 'mbp', document)

    found = usage_table.read_all(tmp_path)

    assert found.tables == []
    assert found.unreadable[0].name == 'usage-table-mbp.json'
    assert "'archlinux'" in found.unreadable[0].reason


def test_one_bad_row_makes_the_whole_table_unreadable(tmp_path):
    """Dropping the row would undercount the reading with nothing to say so."""
    document = usage_table.to_document(table('archlinux', host('archlinux', '2026-08-10')))
    document['hosts']['archlinux']['rows'][0]['count'] = 'many'
    written(tmp_path, 'archlinux', document)

    found = usage_table.read_all(tmp_path)

    assert found.tables == []
    assert 'hosts.archlinux.rows[0].count' in found.unreadable[0].reason


def test_a_table_from_a_newer_doit_is_unreadable(tmp_path):
    document = usage_table.to_document(table('archlinux', host('archlinux', '2026-08-10')))
    document['schema_version'] = usage_table.SCHEMA_VERSION + 1
    written(tmp_path, 'archlinux', document)

    assert 'newer' in usage_table.read_all(tmp_path).unreadable[0].reason


def test_text_that_is_not_json_is_unreadable(tmp_path):
    usage_table.table_path(tmp_path, 'archlinux').write_text('{"half": ')

    assert usage_table.read_all(tmp_path).unreadable == [usage_table.Unreadable('usage-table-archlinux.json', 'is not JSON')]


def test_a_sync_conflict_copy_is_not_read_as_an_export(tmp_path):
    """Syncthing names the loser of a collision with dots a bare hostname never holds."""
    usage_table.write(tmp_path, table('mbp', host('mbp', '2026-08-10')))
    (tmp_path / 'usage-table-mbp.sync-conflict-20261008-120000-ABCDEFG.json').write_text('{}')

    found = usage_table.read_all(tmp_path)

    assert [entry.machine for entry in found.tables] == ['mbp']
    assert found.unreadable == []
