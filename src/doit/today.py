"""The day in two lists — what it still wants, and what it has had.

The third density, beside `doit dashboard` and `doit next`. The dashboard reads
every lane and ranks across none of them; `next` draws one thing from the
weights. Neither says what the day has already had, because an outstanding
count can only climb and a lane of hundreds of unread articles renders exactly
like a lane of two overdue chores.

**Above both lists, what today asks to hold the pace.** The timed pursuits'
minutes and the counted pursuits' checkoffs, each in its own unit, with the
habits, review and practice as checklists under them. Then what is behind beyond
today, and each timescale worth showing with how much more a day would clear it.
`doit.quota` holds the arithmetic. Where the pursuits could not be read, the line
names them instead of a figure.

**What is not done is said once, in the due list, and it prints first.** It is
what the reader acts on, and the done list is the record beneath it. Each group
holds what one record owes, in that record's own order. Habits and pursuits show
their first ten. Each is a short list the day works through, so its rows are the
day's plan rather than a backlog. Every other group shows three. The rest are
one command away, on the row saying how many there are.

**Anything done today is off the due list, even where its record still owes.**
A pursuit logged this morning can still be weeks behind its pace. The day has
had it, so it is listed as done and only as done.

**The done list is `doit.happened`'s fold over the day.** `doit happened` renders
the same fold over any window, so the two cannot disagree about what a day held.
Closed issues are the one difference. They are engineering work agents did, which
the issue tracker reports, so only `doit happened` asks for them. A group that
could not be read is said once, in the due list, where its absence would read as
nothing owed. A group with nothing in it prints nothing.

Nothing here recomputes due-ness. The dashboard mirrors the `overdue` a backend
emits rather than deriving it again, and that holds just as hard at this
density. So the due rows are taken from statuses and lanes already built, and a
register is asked its own `is_due` rather than having one written for it here.
The pursuits go through `build_state` instead, because doit owns that model and
there is no backend to mirror.

The model is `doit.lanes`. Every group in either list is a Lane. A done group
is a grid of finished cells carrying `done_at`, and a due group is ranked rows.
`--json` emits the two lists side by side, each lane in the contract's shape.
Two lists rather than one, because a record appears in both under one name:
habits done and habits still to do. The service answers them, and the Go doit's
`cli/internal/screens` draws them.
"""

import dataclasses as dc
import datetime as dt
from typing import NamedTuple

from doit import dashboard
from doit import happened
from doit import lanes
from doit import pursuits
from doit import quota
from doit import sources
from doit.lanes import CompletionKind
from doit.lanes import CompletionTarget
from doit.lanes import Lane
from doit.lanes import Row
from doit.lanes import Urgency
from doit.render import span_text
from doit.window import Bounds

# The one row-based lane whose urgency means today. Every other lane marks DUE
# on a window rather than on the day, so `urgency != NONE` reads as "wants
# attention" and filtering on it pulls next week onto a screen about this
# afternoon.
#
# A lane name rather than an app name, so a conforming source supplying
# `upcoming` lands here with nothing to change.
DAY_SHAPED_ROW_LANES = ('upcoming',)

# The complete sets whose every member is due every day. Named rather than found
# by having a grid, for two reasons. A grid says "the whole set", not "due
# today". And a lane that failed has no grid, so finding sets that way would
# drop a failed habits lane from the due list without a word.
DAY_SET_LANES = (lanes.HABITS,)

# Past three a group is a backlog. The row after them says how many more there
# are and what to type to see them.
DUE_PER_GROUP = 3

# The groups whose rows are the day's plan rather than a backlog. Ten shows a
# routine whole without letting it push every register off the screen.
ROUTINE_GROUPS = (*DAY_SET_LANES, lanes.PURSUITS)
DUE_PER_ROUTINE_GROUP = 10

OFF_THE_DAY_ADAPTERS = ('slate-issues',)


def day_registry(registry: sources.Registry) -> sources.Registry:
    """The sources a day asks: every one but the completion sources whose records stay off it."""
    asked = {name: source for name, source in registry.completions.items() if source.adapter not in OFF_THE_DAY_ADAPTERS}
    return dc.replace(registry, completions=asked)


def filled(template: str, handle: str) -> str:
    """A cell's handle as a command, where the lane's hint says how to make one.

    The dashboard prints a habit's bare id beside a hint reading `life habits
    complete <id>`, and the reader puts the two together. A due row has one
    handle column, so they are put together here.
    """
    if handle and '<id>' in template:
        return template.replace('<id>', handle)
    return handle


def habit_target(habit_id: str) -> CompletionTarget | None:
    """What picking a habit's row completes, or None where its cell names no habit."""
    return CompletionTarget(CompletionKind.HABIT, habit_id) if habit_id else None


def set_lanes(built: list[dashboard.LaneView], done: list[Lane]) -> list[Lane]:
    """What each day set still holds, or the set itself where it could not be read.

    A member is owed while neither the set's own tick nor the done group names
    it, so the two halves never list one habit in both or in neither.
    """
    due: list[Lane] = []
    for lane in built:
        if lane.name not in DAY_SET_LANES:
            continue
        if not lane.available:
            due.append(lane)
            continue
        finished: set[str] = set()
        for group in done:
            if group.name == lane.name:
                finished |= {cell.text for cell in group.grid}
        template = lane.hints[0] if lane.hints else ''
        # An open cell's handle is the habit's id, which is what a pick completes.
        left = [
            Row(cell.text, '', handle=filled(template, cell.handle), completes=habit_target(cell.handle))
            for cell in lane.grid
            if not cell.done and cell.text not in finished
        ]
        due.append(Lane(name=lane.name, title=lane.title, rows=left, total=len(left), hints=[f'doit dashboard --lane {lane.name}']))
    return due


def missing_done_halves(built: list[dashboard.LaneView], folded: list[Lane]) -> list[Lane]:
    """An unavailable done group for each day set that answered while no completion source supplied its done half.

    Without it a member ticked today is in neither list: the set no longer
    owes it, and nothing lists it done.
    """
    have = {lane.name for lane in folded}
    missing = []
    for lane in built:
        if lane.name not in DAY_SET_LANES or not lane.available or lane.name in have:
            continue
        supplier = next((adapter_id for adapter_id, name in happened.COMPLETION_ADAPTERS if name == lane.name), lane.name)
        missing.append(lanes.unavailable(lane.name, lane.title, f'no {supplier} entry under completions: add one with doit sources edit'))
    return missing


def appointment_lanes(built: list[dashboard.LaneView]) -> list[Lane]:
    """The row-based day lanes, cut to the rows that fall today or are past.

    The gutter word moves to the note. On the dashboard it answers "how far
    away" for a lane of mostly future rows. Here every row is today or late, and
    how late is what the note column is for.
    """
    found = []
    for lane in built:
        if lane.name not in DAY_SHAPED_ROW_LANES:
            continue
        if not lane.available:
            found.append(lane)
            continue
        rows = [Row('', row.text, row.label, row.urgency, row.handle) for row in lane.rows if row.urgency in (Urgency.DUE, Urgency.OVERDUE)]
        found.append(Lane(name=lane.name, title=lane.title, rows=rows, total=len(rows), hints=list(lane.hints), reason=lane.reason))
    return found


def register_target(register: happened.Register, row: dict) -> CompletionTarget | None:
    """What picking a register entry's row completes: the entry `doit review done` marks, by its id.

    Each register's name is its kind. An entry with no id has nothing that
    `review done` could name, so its row completes nothing.
    """
    return CompletionTarget(CompletionKind(register.name), str(row['id'])) if row.get('id') else None


def register_owed(register: happened.Register, rows: list[dict], bounds: Bounds, now: dt.datetime) -> Lane:
    """What a register still owes today, ranked the way the dashboard's MAINTENANCE lane ranks it.

    An item done today is left off. One on a daily cadence done this morning
    still reports `overdue: 0`, because zero means "wanted today" and it was —
    so without this it would be listed as done and owed at once.
    """
    ranked = sorted(
        (
            (
                *dashboard.maintenance_row(happened.item_name(row), register.describe(row), row, register.act(row)),
                register_target(register, row),
            )
            for row in rows
            if not happened.lands_within(row.get('last'), bounds, now) and register.is_due(row)
        ),
        key=lambda entry: entry[0],
    )
    owed = [dc.replace(row, completes=target) for _, row, target in ranked]
    return Lane(name=register.name, title=register.title, rows=owed, total=len(owed), hints=[register.hint])


def maintenance_lanes(kept: happened.Kept, bounds: Bounds, now: dt.datetime) -> list[Lane]:
    """What each register doit keeps still owes, or why it could not be read."""
    due: list[Lane] = []
    for register in happened.registers():
        if register.name in kept.register_errors:
            due.append(lanes.unavailable(register.name, register.title, kept.register_errors[register.name]))
            continue
        due.append(register_owed(register, kept.registers.get(register.name, []), bounds, now))
    return due


def owing(state: dict) -> list[tuple[str, float | None]]:
    """Pursuits owing at least one checkoff, furthest past due first.

    A whole checkoff rather than any positive balance, matching `standing_line`:
    a balance climbs continuously from zero, so every pursuit passes through the
    fraction just above it after every single checkoff.

    A pursuit with no declared interval has no date to be past, so it sorts last
    and reads as due today rather than being dropped from a list of what is owed.
    """
    behind = []
    for name in state['active']:
        # A pursuit under a running skip owes nothing, so it is never behind.
        if name in state['suppressed'] or state['balance'][name] < state['checkoff_size'][name]:
            continue
        behind.append((name, pursuits.due_in_days(state, name)))
    return sorted(behind, key=lambda row: (row[1] is None, row[1] if row[1] is not None else 0))


def pursuits_due(state: dict, bounds: Bounds) -> Lane:
    """Each pursuit owing a checkoff, furthest behind first, with the command that logs it.

    A pursuit moved today is left off however far behind it still is. Doing it
    checks it off for the day, so the done list names it and this does not.
    """
    moved = {name for name, _ in happened.moved_within(state, bounds)}
    rows = []
    for name, due_in in owing(state):
        if name in moved:
            continue
        note = 'due today' if due_in is None or -1 < due_in <= 0 else f'{span_text(due_in)} overdue'
        described = state['active'][name].get('description', '')
        rows.append(
            Row(name, described, note, Urgency.OVERDUE, f'doit pursuits log {name}', CompletionTarget(CompletionKind.PURSUIT, name))
        )
    return Lane(name=lanes.PURSUITS, title='PURSUITS', rows=rows, total=len(rows), hints=['doit next --explain'])


def pursuit_lanes(kept: happened.Kept, bounds: Bounds) -> list[Lane]:
    """The pursuits' due group, or why the register would not load; nothing where it is empty."""
    if kept.state_error:
        return [lanes.unavailable(lanes.PURSUITS, 'PURSUITS', kept.state_error)]
    if kept.state is None:
        return []
    return [pursuits_due(kept.state, bounds)]


CHECKLIST_GROUPS = (*DAY_SET_LANES, *(register.name for register in happened.registers()))


def checklists(done: list[Lane], due: list[Lane]) -> list[quota.Checklist]:
    """Each checklist group as what it finished today out of what it finished and still has due, or why either half could not be read."""
    found = []
    for lane in due:
        if lane.name not in CHECKLIST_GROUPS:
            continue
        cleared_groups = [cleared for cleared in done if cleared.name == lane.name]
        failed = lane if not lane.available else next((cleared for cleared in cleared_groups if not cleared.available), None)
        if failed is not None:
            found.append(quota.Checklist(lane.name, unavailable=failed.reason))
            continue
        # A member done twice today counts once, as `set_lanes` matches the done group by name.
        finished = len({cell.text for cleared in cleared_groups for cell in cleared.grid})
        found.append(quota.Checklist(lane.name, finished, finished + len(lane.rows)))
    return sorted(found, key=lambda entry: CHECKLIST_GROUPS.index(entry.name))


def day_quota(state: dict | None, done: list[Lane], due: list[Lane]) -> quota.Quota:
    """The figures above both lists, with the pursuits named as unavailable where their group could not be read."""
    failed = next((lane.reason for lane in due if lane.name == lanes.PURSUITS and not lane.available), '')
    return quota.build(state, checklists(done, due), failed)


class Day(NamedTuple):
    """Today's two lists, each holding its groups in the order they print, and the figures above them."""

    done: list[Lane]
    due: list[Lane]
    figures: quota.Quota | None = None


def assemble(
    registry: sources.Registry,
    results: dict[str, sources.Result],
    built: list[dashboard.LaneView],
    kept: happened.Kept,
    bounds: Bounds,
    now: dt.datetime,
) -> Day:
    """Both lists, from one round of every configured source, the lanes it built and what doit keeps.

    The due groups run appointments, pursuits, registers, then sets. An
    appointment keeps its hour whatever else is owed. A set you are part-way
    through is the one thing here you can finish in a minute, so it sits at the
    bottom where it does not push a commitment off the screen.

    The done groups lead with the day sets, then run in the fold's order.
    """
    folded = happened.fold(registry, results, kept, bounds, now)
    folded += missing_done_halves(built, folded)

    due = appointment_lanes(built) + pursuit_lanes(kept, bounds) + maintenance_lanes(kept, bounds, now) + set_lanes(built, folded)
    failed = {lane.name for lane in due if not lane.available}
    shown = [lane for lane in folded if lane.available or lane.name not in failed]
    done = sorted(shown, key=lambda lane: lane.name not in DAY_SET_LANES)
    return Day(done, due, day_quota(kept.state, done, due))


def due_row_cap(lane_name: str, *, whole_routine: bool = False) -> int | None:
    """How many of a due group's rows are shown, or None for all of them.

    `whole_routine` lists a routine group whole, for a reader that completes
    rows rather than reads them, and still caps every backlog.
    """
    if lane_name in ROUTINE_GROUPS:
        return None if whole_routine else DUE_PER_ROUTINE_GROUP
    return DUE_PER_GROUP
