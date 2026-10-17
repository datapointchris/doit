"""What a day at pace asks for, what today has paid of it, and what catching up adds.

Shown at the top of `doit today`. Every figure is minutes of effort, the one unit a
timed pursuit, a counted one, a review item and a Lab share. A timed pursuit is
already counted in minutes. A counted pursuit's checkoff is worth one sitting, as
`forecast.durations` measures it from the journal, so its balance, today's minutes
and the daily ask convert at one rate. Neither register records how long an item
takes, so each one is `forecast.FALLBACK_MINUTES`, the figure forecast already
assumes for a pursuit it cannot measure.

**The daily is the pace, never the backlog.** It is what holding every pace costs
a day, so a day that pays it leaves every balance where it was. What is overdue is
said apart from it, as extra minutes a day over a few timescales. A ratio against
the backlog reads as a target the day was set, the way `0 of 23` Labs would.

**Catching up is the backlog spread over days.** Standing has no window, so a pace
pays exactly what accrues and only the overdue part needs more. Overdue is the bar
the due list uses: a pursuit owing more than one checkoff, a register item that is
due. A pursuit ahead of its pace offsets nothing, because reading more does not
paint.

A timescale is shown where its extra is at most `MOST_EXTRA` dailies, past which it
is not a day anyone keeps, and at least `LEAST_EXTRA` of one, below which it adds
nothing the shorter timescales did not. Where none qualifies, the longest is shown
when all ask too much and the shortest when all ask too little, so a backlog is
never left unsaid.

Habits are a checklist beside the number and never part of it. They are due every
day as a set and carry no duration.
"""

import datetime as dt
import math
from dataclasses import dataclass
from dataclasses import field
from typing import NamedTuple

from doit import forecast
from doit.pursuits import completed_since
from doit.pursuits import records_by_pursuit

TIMESCALES = ((3, '3 days'), (7, '1 week'), (14, '2 weeks'), (30, '1 month'), (90, '3 months'))
MOST_EXTRA = 2.0
LEAST_EXTRA = 0.1


class Recurring(NamedTuple):
    """One review item or scheduled Lab, as the daily and the backlog count it."""

    cadence_days: float
    due: bool
    done_today: bool


@dataclass(frozen=True)
class CatchUp:
    days: int
    label: str
    extra_per_day: float
    shown: bool


@dataclass(frozen=True)
class Quota:
    daily: float = 0.0
    done: float = 0.0
    backlog: float = 0.0
    catch_up: list[CatchUp] = field(default_factory=list)
    habits_done: int | None = None
    habits_total: int | None = None
    habits_unavailable: str = ''
    unavailable: str = ''


def to_dict(figures: Quota) -> dict:
    """The `minutes` object of `doit today --json`, every timescale with whether it was shown."""
    habits = None
    if figures.habits_total is not None:
        habits = {'done': figures.habits_done, 'total': figures.habits_total}
    return {
        'daily': round(figures.daily, 1),
        'done': round(figures.done, 1),
        'backlog': round(figures.backlog, 1),
        'catch_up': [
            {'days': entry.days, 'label': entry.label, 'extra_per_day': round(entry.extra_per_day, 1), 'shown': entry.shown}
            for entry in figures.catch_up
        ],
        'habits': habits,
        'habits_unavailable': figures.habits_unavailable,
        'unavailable': figures.unavailable,
    }


def paced(state: dict) -> list[str]:
    """The pursuits every figure counts: active, scheduled, and not skipped."""
    passed = set(state['suppressed'])
    return [name for name in state['active'] if name not in passed and math.isfinite(state['intervals'].get(name, math.inf))]


def minutes_per_unit(state: dict, names: list[str]) -> dict[str, float]:
    """What one unit of each pursuit's balance is worth in minutes."""
    sittings = forecast.durations({name: state['active'][name] for name in names}, state['records'])
    return {name: 1.0 if state['weekly_minutes'].get(name) else sittings[name].minutes for name in names}


class Figures(NamedTuple):
    daily: float
    done: float
    backlog: float


def pursuit_figures(state: dict) -> Figures:
    """The pursuits' share of each figure, paid by the rules standing pays by."""
    value = minutes_per_unit(state, paced(state))
    now = state['now']
    midnight = dt.datetime.combine(state['today'], dt.time(), tzinfo=now.tzinfo)
    mine = records_by_pursuit(state['records'])
    paid_today = {
        name: completed_since(mine.get(name, []), state['evidence_days'].get(name, []), now, midnight, state['weekly_minutes'].get(name))
        for name in value
    }
    return Figures(
        daily=sum(value[name] * state['checkoff_size'][name] / state['intervals'][name] for name in value),
        done=sum(value[name] * paid_today[name] for name in value),
        backlog=sum(value[name] * max(0.0, state['balance'][name] - state['checkoff_size'][name]) for name in value),
    )


def register_figures(recurring: list[Recurring]) -> Figures:
    """The review items' and scheduled Labs' share of each figure."""
    minutes = forecast.FALLBACK_MINUTES
    return Figures(
        daily=sum(minutes / item.cadence_days for item in recurring if item.cadence_days > 0),
        done=minutes * sum(1 for item in recurring if item.done_today),
        backlog=minutes * sum(1 for item in recurring if item.due),
    )


def timescales(backlog: float, daily: float) -> list[CatchUp]:
    """Each timescale's extra minutes a day, marked with whether it is worth showing."""
    extras = [(days, label, backlog / days) for days, label in TIMESCALES]
    if backlog <= 0:
        return [CatchUp(days, label, extra, False) for days, label, extra in extras]
    fits = [LEAST_EXTRA * daily <= extra <= MOST_EXTRA * daily for _, _, extra in extras]
    if not any(fits):
        pick = len(extras) - 1 if all(extra > MOST_EXTRA * daily for _, _, extra in extras) else 0
        fits = [index == pick for index in range(len(extras))]
    return [CatchUp(days, label, extra, shown) for (days, label, extra), shown in zip(extras, fits, strict=True)]


def build(
    state: dict | None,
    recurring: list[Recurring],
    habits: tuple[int, int] | None = None,
    habits_unavailable: str = '',
) -> Quota:
    """The day's figures. ``state`` is None where the register declares no pursuits."""
    parts = [register_figures(recurring)] + ([] if state is None else [pursuit_figures(state)])
    daily = sum(part.daily for part in parts)
    backlog = sum(part.backlog for part in parts)
    return Quota(
        daily=daily,
        done=sum(part.done for part in parts),
        backlog=backlog,
        catch_up=timescales(backlog, daily),
        habits_done=None if habits is None else habits[0],
        habits_total=None if habits is None else habits[1],
        habits_unavailable=habits_unavailable,
    )
