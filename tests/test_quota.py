import datetime as dt

from doit import journal
from doit import quota

NOW = dt.datetime(2026, 10, 17, 18, 0, tzinfo=dt.timezone(dt.timedelta(hours=-4)))
MIDNIGHT = dt.datetime.combine(NOW.date(), dt.time(), tzinfo=NOW.tzinfo)


def done(name: str, when: dt.datetime, minutes: float | None = None) -> dict:
    return {'event': journal.Event.DONE, 'pursuit': name, 'occurred_at': when.isoformat(), 'duration_minutes': minutes}


def test_today_asks_each_pursuit_its_turn_in_its_own_unit_and_the_rest_is_behind():
    """A daily chore three days behind asks for today's one, and the second done
    today comes off what is behind. Reading 210 minutes ahead asks for nothing
    and offsets none of work's 70, of which today's 25 pay its 10 and then 15."""
    records = [
        done('chore', NOW - dt.timedelta(hours=2)),
        done('chore', NOW - dt.timedelta(hours=1)),
        done('read', MIDNIGHT - dt.timedelta(days=6), 280),
        done('work', NOW - dt.timedelta(hours=3), 25),
    ]
    state = {
        'now': NOW,
        'today': NOW.date(),
        'active': {'chore': {}, 'read': {}, 'work': {}},
        'suppressed': [],
        'intervals': {'chore': 1.0, 'read': 7.0, 'work': 7.0},
        'checkoff_size': {'chore': 1.0, 'read': 70.0, 'work': 70.0},
        'weekly_minutes': {'read': 70.0, 'work': 70.0},
        'origins': {
            'chore': MIDNIGHT - dt.timedelta(days=3),
            'read': MIDNIGHT - dt.timedelta(days=7),
            'work': MIDNIGHT - dt.timedelta(days=7),
        },
        'done': {'chore': 2.0, 'read': 280.0, 'work': 25.0},
        'evidence_days': {},
        'records': records,
    }

    figures = quota.build(state, [])

    assert figures.minutes is not None and figures.checkoffs is not None
    assert (figures.minutes.need, figures.minutes.done, figures.minutes.behind, figures.minutes.asked_of) == (10.0, 10.0, 55.0, 1)
    assert (figures.checkoffs.need, figures.checkoffs.done, figures.checkoffs.behind) == (1.0, 1.0, 2.0)


def test_a_timescale_shows_where_each_unit_asks_between_a_tenth_and_twice_its_pace():
    def unit(pace: float, behind: float) -> quota.Unit:
        return quota.Unit(pace=pace, need=0.0, done=0.0, behind=behind, asked_of=0)

    def shown(minutes: quota.Unit, checkoffs: quota.Unit) -> list[tuple[str, int]]:
        return [(entry.label, entry.checkoffs_per_day) for entry in quota.timescales(minutes, checkoffs) if entry.shown]

    nothing = unit(0.0, 0.0)
    assert shown(unit(300.0, 4800.0), nothing) == [('2 weeks', 0), ('1 month', 0), ('3 months', 0)]
    assert shown(unit(300.0, 4800.0), unit(2.0, 60.0)) == [('1 month', 2), ('3 months', 1)], 'sixty checkoffs rule out two weeks'
    assert shown(unit(10.0, 1e6), nothing) == [('3 months', 0)], 'a backlog no timescale fits is still said'
    assert shown(unit(300.0, 5.0), nothing) == [('3 days', 0)]
    assert shown(unit(300.0, 0.0), nothing) == []
