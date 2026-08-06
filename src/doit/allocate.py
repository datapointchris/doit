"""Weighted attention allocation — the probabilistic half of the family's scheduling model.

``cadence`` is the deterministic half: a declared interval, a derived due date, an
item that is either due or not. This module is for pursuits whose weight states a
*share of attention* rather than a deadline. Two consequences follow, and they are
the whole design:

**How often something should come up is derived, not declared.** A pursuit's share
of the total weight, against how many things actually get logged per day, gives the
interval at which it would come up if you were living exactly as you said. So a
weight is the only number to hand-maintain; the schedule falls out of it. An
explicit cadence overrides the implied interval for the things that genuinely are
weekly.

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

# How sharply urgency climbs once a pursuit is past its interval. Superlinear, so
# something well overdue outruns a merely heavier pursuit that was done recently.
DEFAULT_ALPHA = 1.5

# Below this fraction of its interval a pursuit cannot be drawn at all. Without it
# the thing just logged is still the heaviest candidate and gets offered again
# minutes later, which reads as the tool not having noticed.
COOLDOWN_FRACTION = 0.2

# Urgency cannot exceed this multiple of the base weight. A pursuit dormant for a
# year would otherwise dominate every draw forever, crowding out the whole
# register on the strength of one number nobody has revisited.
URGENCY_CEILING = 8.0

# What a skip does to the next draw: suppressed, not removed, and only until the
# pursuit's interval has passed. Skips never touch the stated weight — revealed and
# stated preference stay separate signals, and divergence surfaces in `drift`.
SKIP_SUPPRESSION = 0.25

# Guard for the interval divisor. A brand-new or long-idle journal reports a rate
# near zero, and 1/(share × rate) would blow the implied interval up to years.
MIN_LOGS_PER_DAY = 0.25

# Assumed rate when the journal has nothing to measure yet, so a fresh install
# still produces sane intervals on its first run.
FALLBACK_LOGS_PER_DAY = 2.0


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

    At ``logs_per_day`` entries a day, a pursuit owed ``share`` of them comes up
    every ``1 / (share × rate)`` days. The rate is measured from the journal rather
    than configured, so the whole register retunes itself as the real pace changes.
    """
    if share <= 0:
        return math.inf
    return 1.0 / (share * max(logs_per_day, MIN_LOGS_PER_DAY))


def implied_intervals(weights: dict[str, float], logs_per_day: float) -> dict[str, float]:
    """:func:`implied_interval` for every pursuit in one call."""
    return {name: implied_interval(share, logs_per_day) for name, share in implied_shares(weights).items()}


def urgency(days_since: float | None, interval: float, alpha: float = DEFAULT_ALPHA) -> float:
    """How much a pursuit's weight is multiplied by, given how long it has been.

    Zero inside the cooldown, 1.0 at exactly the interval, climbing as ``ratio ^
    alpha`` past it and clamped at :data:`URGENCY_CEILING`. Never logged is treated
    as the most urgent state rather than as an error, so a pursuit added today is
    drawn without needing a seeded date.
    """
    if days_since is None:
        return URGENCY_CEILING
    if interval <= 0 or math.isinf(interval):
        return 0.0
    ratio = days_since / interval
    if ratio < COOLDOWN_FRACTION:
        return 0.0
    return min(ratio**alpha, URGENCY_CEILING)


def effective_weights(
    weights: dict[str, float],
    intervals: dict[str, float],
    days_since: dict[str, float | None],
    days_since_skip: dict[str, float | None] | None = None,
    alpha: float = DEFAULT_ALPHA,
) -> dict[str, float]:
    """The weights the draw actually runs on: stated weight × urgency, minus skips.

    A skip suppresses rather than excludes, and only until the pursuit's interval
    has elapsed — long enough that a pass reads as a pass, short enough that one
    reflexive skip cannot bury something for a season.
    """
    skips = days_since_skip or {}
    effective = {}
    for name, weight in weights.items():
        interval = intervals.get(name, math.inf)
        value = max(weight, 0.0) * urgency(days_since.get(name), interval, alpha)
        skipped = skips.get(name)
        if skipped is not None and not math.isinf(interval) and skipped < interval:
            value *= SKIP_SUPPRESSION
        effective[name] = value
    return effective


def draw(effective: dict[str, float], size: int, rng: random.Random | None = None) -> list[str]:
    """Draw up to ``size`` distinct pursuits with probability proportional to weight.

    Efraimidis–Spirakis: the smallest ``k`` of the keys ``-ln(U_i)/w_i`` is exactly a
    weighted sample without replacement, so one pass over the candidates does it —
    no rejection loop, and no renormalizing the remaining weights after each pick.
    Anything at or below zero (cooling down, paused, weightless) is not a candidate.
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
