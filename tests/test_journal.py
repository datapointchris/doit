"""Tests for doit.journal — per-machine append-only files, merged on read.

The per-machine split is the point of the module, so most of what is asserted here
is that two machines' files coexist and combine rather than overwrite: that is the
Syncthing failure the design exists to avoid, and a regression would look like
working code right up until the day a second machine logged something.
"""

import datetime as dt
import json
import random
from pathlib import Path

from doit import journal

NOW = dt.datetime.fromisoformat('2026-08-04T12:00:00-04:00')


def write_entries(directory: Path, machine: str, entries: list[dict]) -> Path:
    path = journal.journal_path(directory, machine)
    for entry in entries:
        journal.append(path, entry)
    return path


def entry(pursuit: str, event: str = 'done', days_ago: float = 0.0, **extra) -> dict:
    return {
        'pursuit': pursuit,
        'event': event,
        'occurred_at': (NOW - dt.timedelta(days=days_ago)).isoformat(),
        **extra,
    }


def test_journal_path_is_named_for_the_machine(tmp_path):
    assert journal.journal_path(tmp_path, 'macmini').name == 'next-log-macmini.jsonl'


def test_append_stamps_the_schema_version(tmp_path):
    written = journal.append(journal.journal_path(tmp_path, 'mbp'), entry('chores'))
    assert written['schema_version'] == journal.SCHEMA_VERSION


def test_append_writes_one_line_per_record(tmp_path):
    path = write_entries(tmp_path, 'mbp', [entry('chores'), entry('read')])
    assert len(path.read_text().strip().splitlines()) == 2


def test_read_all_merges_every_machine(tmp_path):
    # The whole reason for per-machine files: both must survive and combine.
    write_entries(tmp_path, 'mbp', [entry('chores', days_ago=2)])
    write_entries(tmp_path, 'archlinux', [entry('read', days_ago=1)])
    records = journal.read_all(tmp_path)
    assert [record['pursuit'] for record in records] == ['chores', 'read']


def test_read_all_orders_by_when_it_happened_not_which_file(tmp_path):
    write_entries(tmp_path, 'zzz-machine', [entry('first', days_ago=5)])
    write_entries(tmp_path, 'aaa-machine', [entry('second', days_ago=1)])
    assert [record['pursuit'] for record in journal.read_all(tmp_path)] == ['first', 'second']


def test_read_all_skips_a_malformed_line(tmp_path):
    path = write_entries(tmp_path, 'mbp', [entry('chores')])
    with path.open('a') as handle:
        handle.write('{not json at all\n')
    write_entries(tmp_path, 'mbp', [entry('read')])
    # A half-synced line must not make the rest of the history unreadable.
    assert len(journal.read_all(tmp_path)) == 2


def test_read_all_on_an_empty_directory(tmp_path):
    assert journal.read_all(tmp_path) == []


def test_latest_occurrence_takes_the_most_recent_per_pursuit(tmp_path):
    write_entries(tmp_path, 'mbp', [entry('chores', days_ago=9), entry('chores', days_ago=2)])
    latest = journal.latest_occurrence(journal.read_all(tmp_path), 'done')
    assert latest['chores'] == NOW - dt.timedelta(days=2)


def test_latest_occurrence_separates_event_kinds(tmp_path):
    write_entries(tmp_path, 'mbp', [entry('cs', 'done', 9), entry('cs', 'skip', 1)])
    records = journal.read_all(tmp_path)
    assert journal.latest_occurrence(records, 'done')['cs'] == NOW - dt.timedelta(days=9)
    assert journal.latest_occurrence(records, 'skip')['cs'] == NOW - dt.timedelta(days=1)


def test_days_since_reports_none_for_a_pursuit_never_logged(tmp_path):
    write_entries(tmp_path, 'mbp', [entry('chores', days_ago=3)])
    latest = journal.latest_occurrence(journal.read_all(tmp_path), 'done')
    elapsed = journal.days_since(latest, ['chores', 'brand-new'], NOW)
    assert round(elapsed['chores']) == 3
    assert elapsed['brand-new'] is None


def test_checkoff_equivalent_is_the_fraction_of_a_checkoff_the_entry_was():
    assert journal.checkoff_equivalent({'duration_minutes': 20}, 45.0) == 20 / 45
    assert journal.checkoff_equivalent({'duration_minutes': 90}, 45.0) == 2.0
    assert journal.checkoff_equivalent({'duration_minutes': 20}, None) == 1.0
    assert journal.checkoff_equivalent({'duration_minutes': 0}, 45.0) == 1.0
    assert journal.checkoff_equivalent({}, 45.0) == 1.0, 'a timed entry with no duration states only that it happened'
    assert journal.checkoff_equivalent({'duration_minutes': True}, 45.0) == 1.0


def test_counts_are_summed_across_machines(tmp_path):
    journal.bump_counts(journal.counts_path(tmp_path, 'mbp'), ['chores', 'chores'])
    journal.bump_counts(journal.counts_path(tmp_path, 'archlinux'), ['chores'])
    assert journal.load_counts(tmp_path)['chores'] == 3


def test_counts_of_an_empty_directory(tmp_path):
    assert journal.load_counts(tmp_path) == {}


def test_new_id_is_time_ordered():
    rng = random.Random(3)
    earlier = journal.new_id(NOW, rng)
    later = journal.new_id(NOW + dt.timedelta(seconds=1), rng)
    assert earlier < later


def test_new_id_carries_the_uuid7_version_and_variant():
    identifier = journal.new_id(NOW, random.Random(3))
    assert identifier[14] == '7'
    assert identifier[19] in '89ab'


def test_parse_time_tolerates_a_missing_or_bad_value():
    assert journal.parse_time(None) is None
    assert journal.parse_time('not a timestamp') is None
    assert journal.parse_time('2026-08-04T12:00:00-04:00') == NOW


def test_appended_records_are_valid_json_lines(tmp_path):
    path = write_entries(tmp_path, 'mbp', [entry('chores', note='has "quotes" and, commas')])
    parsed = json.loads(path.read_text().strip())
    assert parsed['note'] == 'has "quotes" and, commas'


def done_at(pursuit: str, when: dt.datetime) -> dict:
    return {'pursuit': pursuit, 'event': 'done', 'occurred_at': when.isoformat()}


def test_counts_by_machine_keeps_each_box_separate(tmp_path):
    """`load_counts` sums, which cannot say where a record sits — and where it
    sits is what decides whether this box may edit it."""
    (tmp_path / 'next-offers-archlinux.json').write_text(json.dumps({'chores': 4, 'gone': 8}))
    (tmp_path / 'next-offers-macmini.json').write_text(json.dumps({'chores': 3}))

    assert journal.counts_by_machine(tmp_path) == {'archlinux': {'chores': 4, 'gone': 8}, 'macmini': {'chores': 3}}
    assert journal.load_counts(tmp_path) == {'chores': 7, 'gone': 8}


def test_counts_by_machine_is_empty_without_any_files(tmp_path):
    assert journal.counts_by_machine(tmp_path) == {}
