"""Tests for doit.allocate — implied intervals, urgency, and the weighted draw.

Every function here is pure, so the draw is tested against a seeded Random rather
than by sampling: a statistical assertion on an unseeded generator either passes
by luck or fails the build for the same reason.
"""

import math
import random

from doit import allocate


def test_implied_shares_normalizes_relative_magnitudes():
    # 35/30/70 is a legitimate register — nothing has to add up to 100.
    shares = allocate.implied_shares({'cs': 35, 'read': 30, 'travel': 70})
    assert sum(shares.values()) == 1.0
    assert shares['travel'] > shares['cs'] > shares['read']


def test_implied_shares_survives_an_all_zero_register():
    assert allocate.implied_shares({'a': 0, 'b': 0}) == {'a': 0.0, 'b': 0.0}


def test_implied_interval_is_inverse_to_share_and_rate():
    # A quarter of the attention at two logs a day is one appearance every two days.
    assert allocate.implied_interval(0.25, 2.0) == 2.0
    assert allocate.implied_interval(0.5, 2.0) == 1.0


def test_implied_interval_floors_the_rate():
    # A near-idle journal would otherwise divide by ~0 and imply an interval of years.
    assert allocate.implied_interval(0.5, 0.0) == 1.0 / (0.5 * allocate.MIN_LOGS_PER_DAY)


def test_implied_interval_of_a_weightless_pursuit_is_infinite():
    assert math.isinf(allocate.implied_interval(0.0, 2.0))


def test_urgency_is_one_at_exactly_the_interval():
    assert allocate.urgency(10.0, 10.0) == 1.0


def test_urgency_is_zero_inside_the_cooldown():
    # Just logged: it must not be the heaviest candidate again minutes later.
    assert allocate.urgency(0.5, 10.0) == 0.0
    assert allocate.urgency(10.0 * allocate.COOLDOWN_FRACTION, 10.0) > 0.0


def test_urgency_climbs_superlinearly_past_the_interval():
    single = allocate.urgency(10.0, 10.0)
    double = allocate.urgency(20.0, 10.0)
    assert double > 2 * single


def test_urgency_is_capped():
    assert allocate.urgency(10_000.0, 1.0) == allocate.URGENCY_CEILING


def test_never_done_is_the_most_urgent_state():
    assert allocate.urgency(None, 10.0) == allocate.URGENCY_CEILING


def test_effective_weight_multiplies_stated_weight_by_urgency():
    effective = allocate.effective_weights({'a': 30}, {'a': 10.0}, {'a': 20.0})
    assert effective['a'] == 30 * allocate.urgency(20.0, 10.0)


def test_a_skip_suppresses_but_does_not_remove():
    plain = allocate.effective_weights({'a': 30}, {'a': 10.0}, {'a': 20.0})
    skipped = allocate.effective_weights({'a': 30}, {'a': 10.0}, {'a': 20.0}, {'a': 1.0})
    assert skipped['a'] == plain['a'] * allocate.SKIP_SUPPRESSION
    assert skipped['a'] > 0


def test_skip_suppression_lapses_after_one_interval():
    plain = allocate.effective_weights({'a': 30}, {'a': 10.0}, {'a': 20.0})
    stale = allocate.effective_weights({'a': 30}, {'a': 10.0}, {'a': 20.0}, {'a': 11.0})
    assert stale['a'] == plain['a']


def test_draw_returns_distinct_names_up_to_size():
    drawn = allocate.draw({'a': 1, 'b': 1, 'c': 1, 'd': 1}, 3, random.Random(1))
    assert len(drawn) == 3
    assert len(set(drawn)) == 3


def test_draw_never_offers_a_zero_weight_candidate():
    drawn = allocate.draw({'cooling': 0.0, 'ready': 5.0}, 5, random.Random(1))
    assert drawn == ['ready']


def test_draw_returns_fewer_than_asked_when_candidates_run_out():
    assert allocate.draw({'a': 1.0}, 5, random.Random(1)) == ['a']


def test_draw_is_reproducible_for_a_seed():
    weights = {'a': 10, 'b': 20, 'c': 30, 'd': 40}
    assert allocate.draw(weights, 3, random.Random(7)) == allocate.draw(weights, 3, random.Random(7))


def test_draw_favors_weight_over_many_trials():
    # The one statistical assertion, made safe by a fixed seed: a 20x weight must
    # come up first far more often, or the keys are not proportional to weight.
    rng = random.Random(11)
    firsts = [allocate.draw({'heavy': 100.0, 'light': 5.0}, 1, rng)[0] for _ in range(400)]
    assert firsts.count('heavy') > firsts.count('light') * 5


def test_first_draw_probabilities_sum_to_one_and_exclude_zeros():
    probabilities = allocate.first_draw_probabilities({'a': 30.0, 'b': 10.0, 'cooling': 0.0})
    assert probabilities['cooling'] == 0.0
    assert abs(sum(probabilities.values()) - 1.0) < 1e-9
    assert probabilities['a'] == 0.75


def test_one_completion_banks_exactly_what_days_since_already_said():
    """The generalization has to leave the ordinary case untouched.

    A pursuit done once, with credit configured, must weigh identically to one
    without — otherwise turning credit on silently re-times everything else.
    """
    assert allocate.banked_days_since([3.0], interval=1.0, cap=7.0) == 3.0
    assert allocate.banked_days_since([0.0], interval=1.0, cap=7.0) == 0.0


def test_three_completions_in_one_evening_cover_three_days():
    """The whole point: a burst is credited, not collapsed to its last entry."""
    banked = allocate.banked_days_since([0.0, 0.0, 0.0], interval=1.0, cap=7.0)
    assert banked == -2.0, 'satisfied two days past today, so due again on the third'
    assert allocate.urgency(banked, interval=1.0) == 0.0, 'and cannot be drawn meanwhile'


def test_the_bank_runs_out_and_the_pursuit_comes_back():
    three_days_ago = [3.0, 3.0, 3.0]
    assert allocate.banked_days_since(three_days_ago, interval=1.0, cap=7.0) == 1.0
    assert allocate.urgency(1.0, interval=1.0) == 1.0, 'exactly due, not overdue'


def test_credit_cannot_be_hoarded_past_the_cap():
    """A spring clean must not silence a daily prompt for a month."""
    twenty = [0.0] * 20
    banked = allocate.banked_days_since(twenty, interval=1.0, cap=7.0)
    assert allocate.banked_position(banked, 1.0) == 7.0


def test_debt_is_forgiven_past_the_cap_too():
    """A fortnight away must not accrue a backlog no evening can clear."""
    banked = allocate.banked_days_since([30.0], interval=1.0, cap=7.0)
    assert allocate.banked_position(banked, 1.0) == -7.0
    assert allocate.urgency(banked, interval=1.0) == allocate.URGENCY_CEILING


def test_a_missed_stretch_is_owed_and_paid_down_one_at_a_time():
    """Debt carries. One chore clears one, not the whole backlog."""
    interval, cap = 1.0, 7.0
    owed = allocate.banked_position(allocate.banked_days_since([4.0], interval, cap), interval)
    assert owed == -3.0, 'one done four days ago covers that day, leaving three'

    paid = allocate.banked_position(allocate.banked_days_since([4.0, 0.0], interval, cap), interval)
    assert paid == -2.0, 'doing one now pays exactly one of them down'

    cleared = [4.0, 0.0, 0.0, 0.0]
    assert allocate.banked_position(allocate.banked_days_since(cleared, interval, cap), interval) == 0.0


def test_never_done_stays_none_rather_than_becoming_a_debt():
    assert allocate.banked_days_since([], interval=1.0, cap=7.0) is None
    assert allocate.banked_position(None, 1.0) is None


def test_an_occurrence_outside_the_window_does_not_drag_a_burst_backwards():
    """The carry starts at the oldest occurrence, so one from months ago set a
    ceiling that three tonight could not climb back from — and the pursuit read
    as maximally behind on the evening it was worked hardest."""
    interval, cap = 3.0, 7.0
    recent = allocate.banked_position(allocate.banked_days_since([1.0, 3.0, 5.0], interval, cap), interval)

    with_an_old_one = allocate.banked_position(allocate.banked_days_since([1.0, 3.0, 5.0, 60.0], interval, cap), interval)

    assert recent == 4.0
    assert with_an_old_one == recent


def test_the_most_recent_is_kept_when_none_are_inside_the_window():
    """Dropping every occurrence would read as never done, which is the state a
    pursuit idle for a year is furthest from."""
    banked = allocate.banked_days_since([30.0, 90.0], interval=1.0, cap=7.0)

    assert allocate.banked_position(banked, 1.0) == -7.0
    assert allocate.urgency(banked, interval=1.0) == allocate.URGENCY_CEILING
