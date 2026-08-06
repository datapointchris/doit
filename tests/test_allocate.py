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
