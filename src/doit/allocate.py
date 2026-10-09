"""Standing and the draw — the arithmetic behind `doit next`.

``cadence`` is the deterministic half of the family's scheduling: a declared
interval, a derived due date, an item that is either due or not. This module is
the half `doit next` runs on, and it has two jobs that never touch.

**Standing is a running balance, in the pursuit's own unit.** Every pursuit
declares its pace — one occurrence every so many days, or so many minutes a
week — and the schedule asks for one checkoff per interval: one occurrence, or
one week's minutes. What has been asked for over a recent window less what was
done in it is the balance. The pace is declared rather than derived, so nothing
else in the register can move it: a pursuit's goal says the same thing whatever
the other pursuits weigh and however much got logged last week.

**Weight decides order and nothing else.** Whatever is owed is shown, heaviest
first. Whatever room is left on the screen is drawn, not ranked: a ranked list
is a queue, the same rows every run until one clears, and a weighted draw makes
a heavy pursuit likely rather than certain. Sampling is Efraimidis–Spirakis:
give each candidate the key ``-ln(U)/w`` and take the smallest ``k``. That draws
without replacement with probability proportional to weight in one pass, with
no rejection loop and no renormalizing after each pick.

Nothing here reads a file or a clock — every function takes numbers and returns
numbers, so the model is testable without a journal or a register.
"""

import math
import random
from collections.abc import Iterable

# The interval of a pursuit measured in time. Its pace is declared as minutes a
# week, so one checkoff is one week's minutes and the schedule asks for it weekly.
WEEK_DAYS = 7.0


def balance(elapsed: float, interval: float, size: float, done: float) -> float:
    """What the schedule has asked for over ``elapsed`` days, less what was done.

    Positive is behind and negative is ahead, in whatever unit ``size`` counts in:
    minutes for a pursuit measured in time, whole checkoffs for one counted in
    occurrences. A pursuit with no interval is owed nothing, since there is no
    schedule to fall behind.
    """
    if interval <= 0 or math.isinf(interval):
        return 0.0
    return (elapsed / interval) * size - done


def days_until_due(owed: float, interval: float, size: float) -> float | None:
    """Days until one more checkoff is owed. Negative is already overdue.

    The balance restated on the one axis every pursuit shares. A balance counts
    minutes for one pursuit and checkoffs for another, so two rows cannot be
    compared without converting by hand; dividing by the size gives checkoffs, and
    the interval turns those into days. A pursuit owing nothing is due one
    interval from now, and each checkoff owed past the first is one interval late.

    Ordering by this deliberately re-orders against checkoffs owed, because the
    interval differs per pursuit. Two checkoffs owed on a daily schedule is one
    day late; one and a half on a ten-day schedule is five. The second is the one
    that has been waiting, and that is the question the screen asks.
    """
    if interval <= 0 or math.isinf(interval) or size <= 0:
        return None
    return interval - owed / size * interval


def projected_days_until_due(
    owed: float,
    interval: float,
    size: float,
    fills_in: float,
    departures: Iterable[tuple[float, float]],
    returning: Iterable[tuple[float, float]] = (),
    holding: Iterable[tuple[float, float]] = (),
) -> float | None:
    """Days until one checkoff is owed, for a balance kept over a sliding window.

    Standing counts only what the window holds, so a balance grows three ways.
    Until the window is full, the schedule asks for more every day, at one
    checkoff per interval. Once it is full, what is asked for stays level, except
    while the window's start passes a skipped span, which the schedule asks for
    again at the same rate. And it steps up as old payments leave. ``fills_in`` is
    the days until the window is full, ``departures`` is each payment as ``(days
    until it leaves, amount)``, and ``returning`` is each skipped span as ``(days
    until the window's start reaches it, days until it has passed)``.

    A skip still running stops the clock, so ``holding`` is each span from now
    that it covers, ``(days until it starts, days until it ends)``. Inside one,
    the schedule asks for nothing new, and a full window sheds the days the skip
    is taking out of it.

    Projected rather than read off the formula. A pursuit paid well ahead by a burst
    a fortnight ago comes due the day that burst leaves the window, which no
    rescaling of today's balance can say.

    Overdue keeps :func:`days_until_due`, which says how late: a whole checkoff
    owed is due now, and each one past it is one interval later. A window shorter
    than one interval never asks for a whole checkoff, so it is never due and
    answers None.
    """
    if interval <= 0 or math.isinf(interval) or size <= 0:
        return None
    if owed >= size:
        return days_until_due(owed, interval, size)
    rate = size / interval
    # Each moment the balance's growth changes: (day, step in owed, change in how many
    # sources accrue). Filling is one source until fills_in, each skipped span
    # passing out of the window is one more while it does, and a running skip is
    # one fewer, so a full window under a skip sheds what it asked for.
    moments = [(fills_in, 0.0, -1)]
    moments += [(days, amount, 0) for days, amount in departures]
    for start, finish in returning:
        moments += [(start, 0.0, 1), (finish, 0.0, -1)]
    for start, finish in holding:
        moments += [(start, 0.0, -1), (finish, 0.0, 1)]
    day, accruing = 0.0, 1
    for when, step, change in sorted(moments, key=lambda moment: (moment[0], -moment[2])):
        when = max(when, day)
        if accruing > 0 and day + (size - owed) / (rate * accruing) <= when:
            return day + (size - owed) / (rate * accruing)
        owed += rate * accruing * (when - day)
        day, accruing = when, accruing + change
        owed += step
        if owed >= size:
            return day
    return day + (size - owed) / (rate * accruing) if accruing > 0 else None


def candidates(weights: dict[str, float], ratios: dict[str, float], suppressed: Iterable[str]) -> dict[str, float]:
    """What the draw fills the screen from, at each pursuit's stated weight.

    ``ratios`` is each pursuit's balance in checkoffs. Anything a whole checkoff
    behind is shown outright rather than left to chance, so it is never sampled
    here — the draw only decides what else to put beside it.

    **A pursuit a whole checkoff or more ahead is not in it.** That is the thing
    just done, and offering it back reads as the log having gone nowhere. Where
    holding that line would empty the pool, everything not owed is offered
    instead — a blank screen says the tool broke rather than that you are done.

    A skip is in neither. It is the one statement about a pursuit that is not
    about its balance, so it has to survive a state where nothing is owed.
    """
    passed = set(suppressed)
    open_rows = {name: weight for name, weight in weights.items() if weight > 0 and name not in passed and ratios[name] < 1.0}
    resting = {name: weight for name, weight in open_rows.items() if ratios[name] > -1.0}
    return resting or open_rows


def draw(effective: dict[str, float], size: int, rng: random.Random | None = None) -> list[str]:
    """Draw up to ``size`` distinct pursuits with probability proportional to weight.

    Efraimidis–Spirakis: the smallest ``k`` of the keys ``-ln(U_i)/w_i`` is exactly a
    weighted sample without replacement, so one pass over the candidates does it —
    no rejection loop, and no renormalizing the remaining weights after each pick.
    Anything at or below zero is not a candidate.
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
