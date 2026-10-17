import datetime as dt

from doit import journal
from doit import quota

NOW = dt.datetime(2026, 10, 17, 18, 0, tzinfo=dt.timezone(dt.timedelta(hours=-4)))


def test_a_day_counts_the_pace_what_today_paid_and_only_what_is_overdue():
    """Reading three weeks ahead pays nothing toward a chore two days behind, and
    the chore's first owed checkoff is today's, not the backlog's."""
    state = {
        'now': NOW,
        'today': NOW.date(),
        'active': {'chore': {}, 'read': {}},
        'suppressed': [],
        'intervals': {'chore': 1.0, 'read': 7.0},
        'checkoff_size': {'chore': 1.0, 'read': 70.0},
        'weekly_minutes': {'read': 70.0},
        'balance': {'chore': 3.0, 'read': -210.0},
        'evidence_days': {},
        'records': [{'event': journal.Event.DONE, 'pursuit': 'read', 'occurred_at': NOW.isoformat(), 'duration_minutes': 25}],
    }
    review = quota.Recurring(cadence_days=7.0, due=True, done_today=False)

    figures = quota.build(state, [review])

    assert figures.daily == 45.0 + 10.0 + 45.0 / 7.0, 'a chore sitting a day, ten minutes of reading, a weekly review'
    assert figures.done == 25.0
    assert figures.backlog == 45.0 * 2 + 45.0


def test_a_timescale_shows_between_a_tenth_and_twice_the_daily_and_one_always_shows():
    def shown(backlog: float, daily: float) -> list[str]:
        return [entry.label for entry in quota.timescales(backlog, daily) if entry.shown]

    assert shown(4800.0, 300.0) == ['2 weeks', '1 month', '3 months']
    assert shown(1e6, 10.0) == ['3 months'], 'a backlog no timescale fits is still said'
    assert shown(5.0, 300.0) == ['3 days']
    assert shown(0.0, 300.0) == []
