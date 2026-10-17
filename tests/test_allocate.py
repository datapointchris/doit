"""Tests for doit.allocate — the balance, the due date, and the weighted draw.

Every function here is pure, so the draw is tested against a seeded Random rather
than by sampling: a statistical assertion on an unseeded generator either passes
by luck or fails the build for the same reason.
"""

import math
import random

from doit import allocate


def test_one_checkoff_an_interval_is_exactly_current():
    assert allocate.balance(elapsed=3.0, interval=3.0, size=1.0, done=1.0) == 0.0
    assert allocate.balance(elapsed=7.0, interval=7.0, size=45.0, done=45.0) == 0.0


def test_a_burst_counts_for_every_checkoff_it_was():
    # Three chores in one evening is three days of cover at a daily interval, and
    # nothing caps how far forward that reaches.
    assert allocate.balance(elapsed=0.0, interval=1.0, size=1.0, done=3.0) == -3.0


def test_partial_time_rolls_over_rather_than_stranding():
    # 20 minutes against a 45-minute checkoff pays 20 minutes off the balance.
    # The fragment is not rounded away and is not held aside as a remainder.
    assert allocate.balance(elapsed=1.0, interval=1.0, size=45.0, done=20.0) == 25.0
    assert allocate.balance(elapsed=1.0, interval=1.0, size=45.0, done=45.0) == 0.0


def test_four_fragments_and_one_sitting_pay_the_same_amount():
    fragments = allocate.balance(elapsed=1.0, interval=1.0, size=45.0, done=15.0 * 4)
    sitting = allocate.balance(elapsed=1.0, interval=1.0, size=45.0, done=60.0)
    assert fragments == sitting


def test_a_week_of_minutes_accrues_evenly_across_the_week():
    # A goal in minutes a week asks for its weekly amount once per week, so two
    # days into one it has asked for two sevenths.
    assert allocate.balance(elapsed=2.0, interval=allocate.WEEK_DAYS, size=1200.0, done=0.0) == 2.0 / 7.0 * 1200.0


def test_a_fortnight_away_is_owed_in_full():
    assert allocate.balance(elapsed=14.0, interval=1.0, size=1.0, done=0.0) == 14.0


def test_being_far_ahead_is_not_forgiven_either():
    assert allocate.balance(elapsed=1.0, interval=1.0, size=1.0, done=20.0) == -19.0


def test_a_pursuit_with_no_schedule_owes_nothing():
    assert allocate.balance(3.0, math.inf, 1.0, 0.0) == 0.0


def test_the_due_date_is_the_balance_on_the_one_axis_both_units_share():
    """One checkoff owed is due now, and each further checkoff is one more interval.

    Minutes and whole checkoffs cannot be read against each other, so neither is
    ever the number on screen. Days are what both convert to.
    """
    assert allocate.days_until_due(1.0, 3.0, 1.0) == 0.0
    assert allocate.days_until_due(3.0, 3.0, 1.0) == -6.0
    assert allocate.days_until_due(0.0, 3.0, 1.0) == 3.0
    assert allocate.days_until_due(90.0, 4.0, 45.0) == -4.0


def test_the_due_date_keeps_the_order_the_balance_had():
    """A rescaling, so the pursuit further behind in its own unit is further behind in days."""
    worse = allocate.days_until_due(4.0, 3.0, 1.0)
    better = allocate.days_until_due(2.0, 3.0, 1.0)

    assert worse < better < 0


def test_a_pursuit_with_no_schedule_is_due_at_no_time():
    assert allocate.days_until_due(1.0, 0.0, 1.0) is None
    assert allocate.days_until_due(1.0, math.inf, 1.0) is None
    assert allocate.days_until_due(1.0, 3.0, 0.0) is None


def test_the_pool_holds_nothing_owed():
    # Anything a whole checkoff behind is shown outright, so sampling it as well
    # would spend a row of the screen on it twice.
    assert allocate.candidates({'a': 30.0, 'b': 10.0}, {'a': 1.5, 'b': 0.0}, ()) == {'b': 10.0}


def test_the_pool_draws_at_the_stated_weight_and_nothing_else():
    # A balance decides membership and never scales a weight, so a pursuit's odds
    # are what the register says whatever the others owe.
    pool = allocate.candidates({'a': 30.0, 'b': 10.0, 'c': 5.0}, {'a': 0.9, 'b': 0.0, 'c': -0.5}, ())

    assert pool == {'a': 30.0, 'b': 10.0, 'c': 5.0}


def test_the_pool_leaves_a_skipped_pursuit_out():
    assert allocate.candidates({'a': 30.0, 'b': 10.0}, {'a': 0.0, 'b': 0.0}, ['b']) == {'a': 30.0}


def test_a_weightless_pursuit_is_never_in_the_pool():
    assert allocate.candidates({'a': 0.0, 'b': 10.0}, {'a': 0.0, 'b': 0.0}, ()) == {'b': 10.0}


def test_a_pursuit_a_whole_checkoff_ahead_is_not_offered_back():
    """It is the thing just done, and offering it back reads as the log having
    gone nowhere. One merely level with its schedule still fills a row."""
    pool = allocate.candidates(
        {'owed': 10.0, 'ahead': 10.0, 'level': 10.0},
        {'owed': 2.0, 'ahead': -2.0, 'level': -0.1},
        (),
    )

    assert pool == {'level': 10.0}


def test_the_whole_register_is_offered_rather_than_a_blank_screen():
    """Holding that line where everything is ahead would leave nothing on offer,
    and a blank screen says the tool broke rather than that you are done."""
    pool = allocate.candidates({'a': 10.0, 'b': 20.0}, {'a': -3.0, 'b': -4.0}, ())

    assert pool == {'a': 10.0, 'b': 20.0}


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
