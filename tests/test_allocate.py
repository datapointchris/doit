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


def test_one_checkoff_an_interval_is_exactly_current():
    assert allocate.balance(elapsed=3.0, interval=3.0, size=1.0, done=1.0) == 0.0
    assert allocate.balance(elapsed=7.0, interval=7.0, size=45.0, done=45.0) == 0.0


def test_a_burst_counts_for_every_checkoff_it_was():
    # Three chores in one evening is three days of cover at a daily interval, and
    # nothing caps how far forward that reaches.
    owed = allocate.balance(elapsed=0.0, interval=1.0, size=1.0, done=3.0)
    assert owed == -3.0
    assert allocate.urgency(owed, 1.0) == 0.0


def test_partial_time_rolls_over_rather_than_stranding():
    # 20 minutes against a 45-minute checkoff pays 20 minutes off the balance.
    # The fragment is not rounded away and is not held aside as a remainder.
    assert allocate.balance(elapsed=1.0, interval=1.0, size=45.0, done=20.0) == 25.0
    assert allocate.balance(elapsed=1.0, interval=1.0, size=45.0, done=45.0) == 0.0


def test_four_fragments_and_one_sitting_pay_the_same_amount():
    fragments = allocate.balance(elapsed=1.0, interval=1.0, size=45.0, done=15.0 * 4)
    sitting = allocate.balance(elapsed=1.0, interval=1.0, size=45.0, done=60.0)
    assert fragments == sitting


def test_a_fortnight_away_is_owed_in_full():
    assert allocate.balance(elapsed=14.0, interval=1.0, size=1.0, done=0.0) == 14.0


def test_being_far_ahead_is_not_forgiven_either():
    assert allocate.balance(elapsed=1.0, interval=1.0, size=1.0, done=20.0) == -19.0


def test_a_weightless_pursuit_owes_nothing():
    assert allocate.balance(3.0, math.inf, 1.0, 0.0) == 0.0


def test_period_amount_is_what_a_week_of_the_schedule_asks_for():
    # A 45-minute checkoff every day and a half is 210 minutes a week.
    assert allocate.period_amount(1.5, 45.0, 7.0) == 210.0


def test_period_amount_of_a_weightless_pursuit_is_nothing():
    assert allocate.period_amount(math.inf, 45.0, 7.0) == 0.0


def test_urgency_is_one_at_exactly_one_checkoff_behind():
    assert allocate.urgency(1.0, 1.0) == 1.0
    assert allocate.urgency(45.0, 45.0) == 1.0


def test_urgency_is_zero_for_anything_current_or_ahead():
    # What a cooldown floor used to buy, without one: doing a pursuit that was on
    # schedule takes its balance to zero, so it cannot be the heaviest candidate
    # again a minute later.
    assert allocate.urgency(0.0, 45.0) == 0.0
    assert allocate.urgency(-90.0, 45.0) == 0.0


def test_a_pursuit_already_behind_stays_urgent_after_one_checkoff():
    behind = allocate.balance(elapsed=4.0, interval=1.0, size=1.0, done=1.0)
    assert allocate.urgency(behind, 1.0) > 1.0


def test_urgency_climbs_superlinearly_past_one_checkoff():
    single = allocate.urgency(45.0, 45.0)
    double = allocate.urgency(90.0, 45.0)
    assert double > 2 * single


def test_urgency_is_unbounded():
    # A ceiling would cap the one signal saying the register needs editing, so a
    # long-neglected pursuit is meant to dominate until someone edits it.
    assert allocate.urgency(10_000.0, 1.0) > 100_000


def test_a_pursuit_with_no_checkoff_to_owe_is_never_urgent():
    assert allocate.urgency(5.0, 0.0) == 0.0


def test_effective_weight_multiplies_stated_weight_by_urgency():
    effective = allocate.effective_weights({'a': 30}, {'a': 2.0}, {'a': 1.0}, {'a': 1.5}, ())
    assert effective['a'] == 30 * allocate.urgency(2.0, 1.0)


def test_a_skip_removes_a_pursuit_rather_than_suppressing_it():
    plain = allocate.effective_weights({'a': 30}, {'a': 2.0}, {'a': 1.0}, {'a': 1.5}, ())
    skipped = allocate.effective_weights({'a': 30}, {'a': 2.0}, {'a': 1.0}, {'a': 1.5}, ['a'])
    assert plain['a'] > 0
    assert skipped['a'] == 0.0


def test_a_steeper_catchup_exponent_makes_the_same_debt_weigh_more():
    steep = allocate.effective_weights({'a': 10}, {'a': 4.0}, {'a': 1.0}, {'a': 3.0}, ())
    flat = allocate.effective_weights({'a': 10}, {'a': 4.0}, {'a': 1.0}, {'a': 1.0}, ())
    assert steep['a'] > flat['a']


def test_the_draw_falls_back_to_stated_weight_when_nothing_is_owed():
    # A register with no debt anywhere would otherwise offer nothing at all, and
    # a blank screen reads as the tool having broken rather than as being current.
    assert allocate.candidates({'a': 0.0, 'b': 0.0}, {'a': 30.0, 'b': 10.0}, ()) == {'a': 30.0, 'b': 10.0}


def test_the_fallback_still_leaves_a_skipped_pursuit_out():
    assert allocate.candidates({'a': 0.0, 'b': 0.0}, {'a': 30.0, 'b': 10.0}, ['b']) == {'a': 30.0}


def test_anything_owed_at_all_wins_over_the_fallback():
    assert allocate.candidates({'a': 0.0, 'b': 4.0}, {'a': 30.0, 'b': 10.0}, ()) == {'a': 0.0, 'b': 4.0}


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
