"""What today asks of each pursuit to hold its pace, and what is behind beyond that.

Shown at the top of `doit today`. Each pursuit is counted in its own unit, and
nothing converts one unit into the other: a timed pursuit in minutes, a counted
one in checkoffs. So the line carries two figures side by side. A checkoff has
no length, and any length given to it would be a guess shown as a measurement.

**Today is the pace, less any lead.** It is what the pace asks between midnight
and the next one. A pursuit already ahead at midnight spends its lead first, so
a pursuit far enough ahead asks nothing. A counted pursuit asks for the checkoffs
whose turn falls today, so a two-day chore asks for one every other day rather
than half of one each day.

**Behind is what the pace asked before today and nothing has paid.** A pursuit
ahead of its pace offsets nothing, because reading more does not paint. Whatever
today paid beyond its own ask comes off what is behind. A timed pursuit's minutes
count as behind from the first one, though the due list waits for a whole week
of them before it calls the pursuit owed.

**Catching up is what is behind, spread over days.** A timescale is shown where
each unit's extra a day is at most `MOST_EXTRA` of that unit's day at pace, past
which it is not a day anyone keeps. It must also be at least `LEAST_EXTRA` of
one, below which it adds nothing a shorter timescale did not. Where none
qualifies, the longest is shown when every one asks too much, and otherwise the
shortest that does not, so a backlog is never left unsaid. Checkoffs a day are
rounded up, because nobody can do a fraction of one.

Habits, review items and Labs are checklists beside the figures. Each item is
done or not, and its own record says when it is due.
"""

import datetime as dt
import math
from dataclasses import dataclass
from dataclasses import field
from typing import NamedTuple

from doit.pursuits import asked_since
from doit.pursuits import completed_since
from doit.pursuits import records_by_pursuit

TIMESCALES = ((3, '3 days'), (7, '1 week'), (14, '2 weeks'), (30, '1 month'), (90, '3 months'))
MOST_EXTRA = 2.0
LEAST_EXTRA = 0.1


class PursuitDay(NamedTuple):
    """One pursuit's day, in minutes when ``timed`` and in checkoffs otherwise."""

    name: str
    timed: bool
    pace: float
    need: float
    done: float
    behind: float


class Unit(NamedTuple):
    """The pursuits counted in one unit, summed. ``asked_of`` is how many ask something today."""

    pace: float
    need: float
    done: float
    behind: float
    asked_of: int


class Checklist(NamedTuple):
    """A checklist beside the figures, or why it could not be read."""

    name: str
    done: int | None = None
    total: int | None = None
    unavailable: str = ''


@dataclass(frozen=True)
class CatchUp:
    days: int
    label: str
    minutes_per_day: float
    checkoffs_per_day: int
    shown: bool


@dataclass(frozen=True)
class Quota:
    """The day's figures. Both units are None where no pursuits were read, and ``unavailable`` says why."""

    minutes: Unit | None = None
    checkoffs: Unit | None = None
    pursuits: list[PursuitDay] = field(default_factory=list)
    catch_up: list[CatchUp] = field(default_factory=list)
    checklists: list[Checklist] = field(default_factory=list)
    unavailable: str = ''


def paced(state: dict) -> list[str]:
    """The pursuits every figure counts: active, scheduled, and not skipped."""
    passed = set(state['suppressed'])
    return [name for name in state['active'] if name not in passed and math.isfinite(state['intervals'].get(name, math.inf))]


def turns(asked: float) -> float:
    """How many checkoffs' turns an ask has reached, clear of float error at each boundary."""
    return float(math.floor(asked + 1e-9))


def pursuit_day(state: dict, name: str, own: list[dict]) -> PursuitDay:
    """What today asks of one pursuit, what it paid toward that, and what stays behind."""
    now = state['now']
    midnight = dt.datetime.combine(state['today'], dt.time(), tzinfo=now.tzinfo)
    origin = state['origins'][name]
    pace_of = (state['intervals'][name], state['checkoff_size'][name])
    minutes = state['weekly_minutes'].get(name)
    asked_by_midnight = asked_since(own, origin, midnight, pace_of) if midnight > origin else 0.0
    asked_by_tonight = asked_since(own, origin, midnight + dt.timedelta(days=1), pace_of)
    if not minutes:
        asked_by_midnight, asked_by_tonight = turns(asked_by_midnight), turns(asked_by_tonight)
    paid_today = completed_since(own, state['evidence_days'].get(name, []), now, max(origin, midnight), minutes)
    owed_at_midnight = asked_by_midnight - (state['done'][name] - paid_today)
    need = max(0.0, asked_by_tonight - asked_by_midnight + min(0.0, owed_at_midnight))
    return PursuitDay(
        name=name,
        timed=bool(minutes),
        pace=pace_of[1] / pace_of[0],
        need=need,
        done=min(paid_today, need),
        behind=max(0.0, owed_at_midnight - max(0.0, paid_today - need)),
    )


def summed(days: list[PursuitDay]) -> Unit:
    return Unit(
        pace=sum(day.pace for day in days),
        need=sum(day.need for day in days),
        done=sum(day.done for day in days),
        behind=sum(day.behind for day in days),
        asked_of=sum(1 for day in days if day.need > 0),
    )


def timescales(minutes: Unit, checkoffs: Unit) -> list[CatchUp]:
    """Each timescale's extra a day in both units, marked with whether it is worth showing."""
    extras = [(days, label, minutes.behind / days, checkoffs.behind / days) for days, label in TIMESCALES]

    def ratios(extra_minutes: float, extra_checkoffs: float) -> list[float]:
        pairs = ((extra_minutes, minutes.pace), (extra_checkoffs, checkoffs.pace))
        return [extra / pace for extra, pace in pairs if extra > 0 and pace > 0]

    measured = [ratios(extra_minutes, extra_checkoffs) for _, _, extra_minutes, extra_checkoffs in extras]
    fits = [bool(found) and all(LEAST_EXTRA <= ratio <= MOST_EXTRA for ratio in found) for found in measured]
    if any(measured) and not any(fits):
        bearable = [index for index, found in enumerate(measured) if all(ratio <= MOST_EXTRA for ratio in found)]
        pick = bearable[0] if bearable else len(extras) - 1
        fits = [index == pick for index in range(len(extras))]
    return [
        CatchUp(days, label, extra_minutes, math.ceil(extra_checkoffs - 1e-9), shown)
        for (days, label, extra_minutes, extra_checkoffs), shown in zip(extras, fits, strict=True)
    ]


def build(state: dict | None, checklists: list[Checklist], unavailable: str = '') -> Quota:
    """The day's figures. ``state`` is None where the register declares no pursuits or could not be read."""
    if state is None:
        return Quota(checklists=checklists, unavailable=unavailable)
    mine = records_by_pursuit(state['records'])
    days = [pursuit_day(state, name, mine.get(name, [])) for name in paced(state)]
    minutes = summed([day for day in days if day.timed])
    checkoffs = summed([day for day in days if not day.timed])
    return Quota(
        minutes=minutes,
        checkoffs=checkoffs,
        pursuits=days,
        catch_up=timescales(minutes, checkoffs),
        checklists=checklists,
    )


def unit_dict(found: Unit | None) -> dict | None:
    if found is None:
        return None
    return {
        'pace': round(found.pace, 1),
        'need': round(found.need, 1),
        'done': round(found.done, 1),
        'behind': round(found.behind, 1),
        'asked_of': found.asked_of,
    }


def to_dict(figures: Quota) -> dict:
    """The `quota` object of `doit today --json`, every timescale with whether it was shown."""
    return {
        'minutes': unit_dict(figures.minutes),
        'checkoffs': unit_dict(figures.checkoffs),
        'pursuits': [
            {
                'name': day.name,
                'unit': 'minutes' if day.timed else 'checkoffs',
                'pace': round(day.pace, 2),
                'need': round(day.need, 1),
                'done': round(day.done, 1),
                'behind': round(day.behind, 1),
            }
            for day in figures.pursuits
        ],
        'catch_up': [
            {
                'days': entry.days,
                'label': entry.label,
                'minutes_per_day': round(entry.minutes_per_day, 1),
                'checkoffs_per_day': entry.checkoffs_per_day,
                'shown': entry.shown,
            }
            for entry in figures.catch_up
        ],
        'checklists': [
            {'name': entry.name, 'done': entry.done, 'total': entry.total, 'unavailable': entry.unavailable} for entry in figures.checklists
        ],
        'unavailable': figures.unavailable,
    }
