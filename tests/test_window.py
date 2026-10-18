import datetime as dt
import time

import pytest

from doit.window import window

EDT = dt.timezone(dt.timedelta(hours=-4))
EST = dt.timezone(dt.timedelta(hours=-5))
NOW = dt.datetime(2026, 10, 18, 9, 0, tzinfo=EDT)


@pytest.fixture
def new_york(monkeypatch):
    monkeypatch.setenv('TZ', 'America/New_York')
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.mark.parametrize(
    ('since', 'until', 'expected'),
    [
        ('30s', None, (NOW - dt.timedelta(seconds=30), None)),
        ('30m', None, (NOW - dt.timedelta(minutes=30), None)),
        ('12h', None, (NOW - dt.timedelta(hours=12), None)),
        ('7d', None, (NOW - dt.timedelta(days=7), None)),
        ('2w', None, (NOW - dt.timedelta(weeks=2), None)),
        ('2026-10-01', None, (dt.datetime(2026, 10, 1, tzinfo=EDT), None)),
        (None, '2026-10-01', (None, dt.datetime(2026, 10, 2, tzinfo=EDT))),
        (None, '2026-11-01', (None, dt.datetime(2026, 11, 2, tzinfo=EST))),
        (
            '2026-10-01T14:00:00-04:00',
            '2026-10-01T19:00:00Z',
            (dt.datetime(2026, 10, 1, 14, tzinfo=EDT), dt.datetime(2026, 10, 1, 19, tzinfo=dt.UTC)),
        ),
        ('6mo', None, ValueError),
        ('1y', None, ValueError),
        ('7', None, ValueError),
        ('2026-10-01T14:00:00', None, ValueError),
        ('1d', '2d', ValueError),
    ],
)
def test_each_form_resolves_to_an_instant_and_a_month_is_refused(new_york, since, until, expected):
    if expected is ValueError:
        with pytest.raises(ValueError):
            window(since, until, NOW)
    else:
        assert window(since, until, NOW) == expected
