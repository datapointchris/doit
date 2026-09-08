"""Weighted attention allocation — the probabilistic half of the family's scheduling model.

``cadence`` is the deterministic half: a declared interval, a derived due date, an
item that is either due or not. This module is for pursuits whose weight states a
*share of attention* rather than a deadline. Three consequences follow, and they
are the whole design:

**How often something should come up is derived, not declared.** A pursuit's share
of the total weight, against how many checkoffs actually get done per day, gives
the interval at which it would come up if you were living exactly as you said. So
a weight is the only number to hand-maintain; the schedule falls out of it. An
explicit cadence overrides the implied interval for the things that genuinely are
weekly.

**Standing is a running balance, in the pursuit's own unit.** The schedule asks
for one checkoff per interval; what a checkoff *is* is a fixed number of minutes
where the pursuit declares one, and a single completion where it does not. The
difference between what has been asked for and what has been done is the balance,
and it is unbounded in both directions. So a burst counts for exactly what it was,
a fortnight away is owed in full, and no fragment of time can strand.

**What to do next is drawn, not ranked.** A ranked list is a queue: the same five
items every run until one is cleared. A weighted draw makes a heavy pursuit likely
rather than certain, so the list moves, and rarely-picked pursuits still surface.
Sampling is Efraimidis–Spirakis: give each candidate the key ``-ln(U)/w`` and take
the smallest ``k``. That draws without replacement with probability proportional to
weight in one pass, with no rejection loop and no renormalizing after each pick.

Nothing here reads a file or a clock — every function takes numbers and returns
numbers, so the model is testable without a journal or a register.
"""

import math
import random
from collections.abc import Iterable

# How sharply urgency climbs once a pursuit owes more than one checkoff.
# Superlinear, so something well behind outruns a merely heavier pursuit that is
# current.
DEFAULT_CATCHUP_EXPONENT = 1.5

# Guard for the interval divisor. A brand-new or long-idle journal reports a rate
# near zero, and 1/(share × rate) would blow the implied interval up to years.
MIN_LOGS_PER_DAY = 0.25

# Assumed rate when the journal has nothing to measure yet, so a fresh install
# still produces sane intervals on its first run.
FALLBACK_LOGS_PER_DAY = 2.0

# Days a period covers when a balance is judged against what the schedule asks
# for over one. A week is the shortest span a weekly cadence can express itself
# in, so it is the shortest one a surplus or a debt can be read against.
PERIOD_DAYS = 7.0


def implied_shares(weights: dict[str, float]) -> dict[str, float]:
    """Each pursuit's fraction of the total weight.

    Weights are relative magnitudes and are never normalized on disk — 35/30/70 is
    a legitimate register. This is what turns them into the shares to display, so a
    number that is more dominant than it looked is visible without doing the
    arithmetic by hand.
    """
    total = sum(w for w in weights.values() if w > 0)
    if total <= 0:
        return {name: 0.0 for name in weights}
    return {name: max(w, 0.0) / total for name, w in weights.items()}


def implied_interval(share: float, logs_per_day: float) -> float:
    """Days between appearances for a pursuit holding ``share`` of the attention.

    At ``logs_per_day`` checkoffs a day, a pursuit owed ``share`` of them comes up
    every ``1 / (share × rate)`` days. The rate is measured from the journal rather
    than configured, so the whole register retunes itself as the real pace changes.
    """
    if share <= 0:
        return math.inf
    return 1.0 / (share * max(logs_per_day, MIN_LOGS_PER_DAY))


def implied_intervals(weights: dict[str, float], logs_per_day: float) -> dict[str, float]:
    """:func:`implied_interval` for every pursuit in one call."""
    return {name: implied_interval(share, logs_per_day) for name, share in implied_shares(weights).items()}


def balance(elapsed: float, interval: float, size: float, done: float) -> float:
    """What the schedule has asked for over ``elapsed`` days, less what was done.

    Positive is behind and negative is ahead, in whatever unit ``size`` counts in:
    minutes for a pursuit that declares a checkoff size, whole checkoffs for one
    that does not. A pursuit whose weight implies no interval at all is owed
    nothing, since there is no schedule to fall behind.

    Nothing is clamped, dropped or forgiven at either end. A balance far enough
    from zero to look wrong is the register saying its weight is wrong, and that
    is the one reading that has to survive to be acted on.
    """
    if interval <= 0 or math.isinf(interval):
        return 0.0
    return (elapsed / interval) * size - done


def period_amount(interval: float, size: float, period_days: float = PERIOD_DAYS) -> float:
    """How much of its own unit a pursuit's schedule asks for over ``period_days``.

    The scale a balance is read against. A heavy strand and a light one are both
    judged by how many periods of their own schedule they have drifted, so one
    threshold covers a register whose pursuits ask for wildly different amounts.
    """
    if interval <= 0 or math.isinf(interval):
        return 0.0
    return period_days / interval * size


def urgency(owed: float, size: float, catchup_exponent: float = DEFAULT_CATCHUP_EXPONENT) -> float:
    """How much a pursuit's weight is multiplied by, given what it owes.

    Zero for anything current or ahead, 1.0 at exactly one checkoff behind, and
    ``ratio ^ exponent`` past that. Unbounded above: a pursuit left long enough to
    dominate every draw is a weight nobody has revisited, and a ceiling there
    suppresses the one signal saying the register needs editing.

    The zero at current is what a cooldown floor used to buy. Doing a pursuit that
    was on schedule takes its balance to zero or below, so the thing just logged
    cannot be the heaviest candidate a minute later — and one that was three
    checkoffs behind still is, which is correct.
    """
    if size <= 0:
        return 0.0
    ratio = owed / size
    if ratio <= 0:
        return 0.0
    return ratio**catchup_exponent


def effective_weights(
    weights: dict[str, float],
    balances: dict[str, float],
    sizes: dict[str, float],
    exponents: dict[str, float],
    suppressed: Iterable[str],
) -> dict[str, float]:
    """The weights the draw actually runs on: stated weight × urgency, minus skips.

    A skip is a hard zero for as long as it runs rather than a factor that decays,
    because a pass with an expiry is a decision about a span of time and a
    suppression multiplier is a guess about one draw. Skips never touch the stated
    weight — revealed and stated preference stay separate signals, and divergence
    surfaces in `drift`.
    """
    passed = set(suppressed)
    effective = {}
    for name, weight in weights.items():
        if name in passed:
            effective[name] = 0.0
            continue
        effective[name] = max(weight, 0.0) * urgency(balances[name], sizes[name], exponents[name])
    return effective


def candidates(effective: dict[str, float], weights: dict[str, float], suppressed: Iterable[str]) -> dict[str, float]:
    """What to sample from, falling back to stated weight when nothing is owed.

    Urgency is zero for everything current, so a register with no debt anywhere
    would offer nothing at all — and a blank screen reads as the tool having
    broken rather than as being caught up. Falling back keeps five things on
    offer while every row's balance says none of them is owed, which is the
    reading that lets an evening be spent deliberately rather than dutifully.

    A skip is excluded from the fallback too. It is the one statement about a
    pursuit that is not about being behind, so it has to survive a state where
    nothing is.
    """
    if any(value > 0 for value in effective.values()):
        return effective
    passed = set(suppressed)
    return {name: max(weight, 0.0) for name, weight in weights.items() if name not in passed}


def draw(effective: dict[str, float], size: int, rng: random.Random | None = None) -> list[str]:
    """Draw up to ``size`` distinct pursuits with probability proportional to weight.

    Efraimidis–Spirakis: the smallest ``k`` of the keys ``-ln(U_i)/w_i`` is exactly a
    weighted sample without replacement, so one pass over the candidates does it —
    no rejection loop, and no renormalizing the remaining weights after each pick.
    Anything at or below zero (ahead, skipped, paused, weightless) is not a candidate.
    """
    rng = rng or random.Random()
    keys = []
    for name, weight in effective.items():
        if weight <= 0:
            continue
        # random() can return exactly 0.0, and log(0) is undefined; redraw rather
        # than nudge the value, which would bias that pursuit's key downward.
        u = rng.random()
        while u <= 0.0:
            u = rng.random()
        keys.append((-math.log(u) / weight, name))
    keys.sort()
    return [name for _, name in keys[:size]]


def first_draw_probabilities(effective: dict[str, float]) -> dict[str, float]:
    """Each pursuit's chance of being the *first* name drawn.

    Deliberately not the probability of appearing anywhere in a draw of five: that
    quantity has no closed form for sampling without replacement and estimating it
    would put a number on screen nobody could check. This one is exact, and it
    ranks the candidates identically.
    """
    total = sum(w for w in effective.values() if w > 0)
    if total <= 0:
        return {name: 0.0 for name in effective}
    return {name: (w / total if w > 0 else 0.0) for name, w in effective.items()}
