"""The desk nudge's clock, as a decision table over the rules alone."""

import datetime as dt

import pytest

from doit import desk_nudge
from doit.desk_nudge import Event
from doit.desk_nudge import Phase

LAST_TICK = dt.datetime(2026, 11, 22, 14, 0, tzinfo=dt.timezone(dt.timedelta(hours=-5)))


@pytest.mark.parametrize(
    ('gap_seconds', 'live', 'phase', 'counted_minutes'),
    [
        (15, True, Phase.COUNTING, 20.25),
        (3600, True, Phase.COUNTING, 20.5),
        (15, False, Phase.NO_DESK, 20),
    ],
)
def test_a_tick_counts_at_most_two_periods_and_nothing_but_a_nudge_restarts_the_clock(gap_seconds, live, phase, counted_minutes):
    state = desk_nudge.fresh_state()
    state.update(counted_seconds=20 * 60.0, last_tick=desk_nudge.stamp(LAST_TICK))
    records = desk_nudge.tick(state, LAST_TICK + dt.timedelta(seconds=gap_seconds), live).records
    assert (state['phase'], round(state['counted_seconds'] / 60, 2)) == (phase, counted_minutes)
    assert any(line['event'] == Event.GAP for line in records) == (gap_seconds > desk_nudge.TICK_CAP_SECONDS)
