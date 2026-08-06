"""Tests for doit.cadence — the cadence tokens and derived due-date math shared
by review and labs."""

from datetime import date

from doit import cadence


def test_parse_cadence_units():
    assert cadence.parse_cadence('10d') == 10
    assert cadence.parse_cadence('2w') == 14
    assert cadence.parse_cadence('1mo') == 30
    assert cadence.parse_cadence('1y') == 365


def test_parse_cadence_bare_number_is_days():
    assert cadence.parse_cadence('5') == 5


def test_parse_cadence_empty_is_zero():
    assert cadence.parse_cadence('') == 0
    assert cadence.parse_cadence('   ') == 0


def test_parse_cadence_unknown_unit_defaults_to_days():
    # get(unit, 1): an unrecognized unit multiplies by 1 (days) rather than crash.
    assert cadence.parse_cadence('3x') == 3


def test_overdue_days_never_done_is_none():
    assert cadence.overdue_days(None, '1w', date(2026, 7, 17)) is None
    assert cadence.overdue_days('', '1w', date(2026, 7, 17)) is None


def test_overdue_days_due_today_is_zero():
    # last + 1w == today → exactly due.
    assert cadence.overdue_days('2026-07-10', '1w', date(2026, 7, 17)) == 0


def test_overdue_days_positive_when_past_due():
    # next_due = 2026-07-08; today is 9 days later.
    assert cadence.overdue_days('2026-07-01', '1w', date(2026, 7, 17)) == 9


def test_overdue_days_negative_when_not_yet_due():
    # next_due = 2026-07-31; today is 14 days before.
    assert cadence.overdue_days('2026-07-17', '2w', date(2026, 7, 17)) == -14


def test_is_due():
    assert cadence.is_due(None) is True  # never done → always due
    assert cadence.is_due(0) is True  # due today
    assert cadence.is_due(5) is True  # overdue
    assert cadence.is_due(-3) is False  # not yet due


def test_status_label():
    assert cadence.status_label(None) == 'never done'
    assert cadence.status_label(5) == 'overdue 5d'
    assert cadence.status_label(0) == 'due today'
    assert cadence.status_label(-3) == 'in 3d'
