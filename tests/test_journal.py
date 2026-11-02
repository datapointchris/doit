"""Tests for doit.journal — what a record says, read the same by the service and the CLI."""

import datetime as dt
import random

from doit import journal

NOW = dt.datetime.fromisoformat('2026-08-04T12:00:00-04:00')


def entry(pursuit: str, event: str = 'done', days_ago: float = 0.0, **extra) -> dict:
    return {
        'pursuit': pursuit,
        'event': event,
        'occurred_at': (NOW - dt.timedelta(days=days_ago)).isoformat(),
        **extra,
    }


def test_latest_occurrence_takes_the_most_recent_per_pursuit():
    latest = journal.latest_occurrence([entry('chores', days_ago=9), entry('chores', days_ago=2)], 'done')
    assert latest['chores'] == NOW - dt.timedelta(days=2)


def test_latest_occurrence_separates_event_kinds():
    records = [entry('cs', 'done', 9), entry('cs', 'skip', 1)]
    assert journal.latest_occurrence(records, 'done')['cs'] == NOW - dt.timedelta(days=9)
    assert journal.latest_occurrence(records, 'skip')['cs'] == NOW - dt.timedelta(days=1)


def test_days_since_reports_none_for_a_pursuit_never_logged():
    latest = journal.latest_occurrence([entry('chores', days_ago=3)], 'done')
    elapsed = journal.days_since(latest, ['chores', 'brand-new'], NOW)
    chores = elapsed['chores']
    assert chores is not None and round(chores) == 3
    assert elapsed['brand-new'] is None


def test_checkoff_equivalent_is_the_fraction_of_a_checkoff_the_entry_was():
    assert journal.checkoff_equivalent({'duration_minutes': 20}, 45.0) == 20 / 45
    assert journal.checkoff_equivalent({'duration_minutes': 90}, 45.0) == 2.0
    assert journal.checkoff_equivalent({'duration_minutes': 20}, None) == 1.0
    assert journal.checkoff_equivalent({'duration_minutes': 0}, 45.0) == 1.0
    assert journal.checkoff_equivalent({}, 45.0) == 1.0, 'a timed entry with no duration states only that it happened'
    assert journal.checkoff_equivalent({'duration_minutes': True}, 45.0) == 1.0


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
