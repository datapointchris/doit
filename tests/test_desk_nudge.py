"""The desk nudge's absence rule, as a decision table over the rules alone."""

import datetime as dt

import pytest

from doit import desk_nudge
from doit.desk_nudge import Event
from doit.desk_nudge import Phase

LAST_INPUT = dt.datetime(2026, 11, 22, 14, 0, tzinfo=dt.timezone(dt.timedelta(hours=-5)))
NUDGE = '019aad00-0000-7000-8000-000000000001'
TICK = dt.timedelta(seconds=desk_nudge.TICK_SECONDS)


HYPRIDLE, NEW_HYPRIDLE = '100', '200'


def ticked(state: dict, start: dt.datetime, end: dt.datetime, live: bool = True) -> dt.datetime:
    """A tick every `TICK_SECONDS` after `start` through `end`, as the watch makes them."""
    moment = start
    while moment + TICK <= end:
        moment += TICK
        desk_nudge.tick(state, moment, live, HYPRIDLE if live else None)
    return moment


def outcome(away_seconds: int, gap_seconds: int, phase: str, live: bool, back: str) -> tuple[str, float, bool | None, bool]:
    """The clock after the last input at 40 present minutes: an away of `away_seconds` from that input, then one tick `gap_seconds` on.

    `back` is how the away ends: `input` reports it back, `gone desk` ends the
    session for the rest of the away, and `stopped watch` stops the watch. Both
    of those end with a new hypridle at the last tick, which never reports back.
    Returns the outcome, the present minutes, the `fired_while_idle` of the away
    where there was one, and whether a `gap` was written.
    """
    state = desk_nudge.fresh_state()
    state.update(present_seconds=40 * 60.0, last_tick=desk_nudge.stamp(LAST_INPUT))
    if phase != 'counting':
        fired = LAST_INPUT + dt.timedelta(seconds=60 if phase == 'ready, fired while idle' else -30)
        desk_nudge.begin_firing(state, fired, NUDGE)
        desk_nudge.make_ready(state, fired, NUDGE, {'pursuit': 'read', 'item_id': None, 'label': ''})
    records = []
    moment = LAST_INPUT
    if away_seconds:
        moment = ticked(state, moment, LAST_INPUT + dt.timedelta(seconds=desk_nudge.IDLE_AFTER_SECONDS))
        records += desk_nudge.went_away(state, moment, desk_nudge.IDLE_AFTER_SECONDS, HYPRIDLE).records
        if back != 'stopped watch':
            moment = ticked(state, moment, LAST_INPUT + dt.timedelta(seconds=away_seconds), live=back == 'input')
        if back == 'input':
            records += desk_nudge.came_back(state, moment, desk_nudge.IDLE_AFTER_SECONDS).records
    reporter = HYPRIDLE if back == 'input' else NEW_HYPRIDLE
    records += desk_nudge.tick(state, moment + dt.timedelta(seconds=gap_seconds), live, reporter).records

    away = next((line for line in records if line['event'] == Event.AWAY), None)
    if state['phase'] == Phase.NO_DESK:
        result = 'no desk'
    elif away and away['nudge_id']:
        result = 'ends ready'
    elif state['present_seconds'] < desk_nudge.TICK_SECONDS + 1:
        result = 'restarts'
    else:
        result = 'resumes'
    wrote_gap = any(line['event'] == Event.GAP for line in records)
    return result, round(state['present_seconds'] / 60, 2), away['fired_while_idle'] if away else None, wrote_gap


@pytest.mark.parametrize(
    ('away_seconds', 'gap_seconds', 'phase', 'live', 'back', 'expected', 'present_minutes', 'fired_while_idle'),
    [
        (0, 15, 'counting', True, 'input', 'resumes', 40.25, None),
        (0, 60, 'counting', True, 'input', 'resumes', 40.5, None),
        (0, 360, 'counting', True, 'input', 'restarts', 0, None),
        (0, 15, 'counting', False, 'input', 'no desk', 40, None),
        (180, 15, 'counting', True, 'input', 'resumes', 40.25, False),
        (300, 15, 'counting', True, 'input', 'restarts', 0.25, False),
        (600, 15, 'counting', True, 'input', 'restarts', 0.25, False),
        (180, 15, 'counting', False, 'input', 'no desk', 40, False),
        (180, 15, 'ready', True, 'input', 'ends ready', 0.25, False),
        (180, 15, 'ready, fired while idle', True, 'input', 'ends ready', 0.25, True),
        (180, 15, 'counting', True, 'gone desk', 'resumes', 40, False),
        (600, 15, 'counting', True, 'gone desk', 'restarts', 0, False),
        (120, 3600, 'counting', True, 'stopped watch', 'restarts', 0, False),
    ],
)
def test_an_absence_resumes_restarts_or_ends_ready_and_an_away_takes_its_idle_seconds_back(
    away_seconds, gap_seconds, phase, live, back, expected, present_minutes, fired_while_idle
):
    assert outcome(away_seconds, gap_seconds, phase, live, back) == (
        expected,
        present_minutes,
        fired_while_idle,
        gap_seconds > desk_nudge.TICK_CAP_SECONDS,
    )
