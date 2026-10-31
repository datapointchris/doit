"""The day in two lists — what it still wants, and what it has had.

The third density, beside `doit dashboard` and `doit next`. The dashboard reads
every lane and ranks across none of them; `next` draws one thing from the
weights. Neither says what the day has already had, because an outstanding
count can only climb and a lane of hundreds of unread articles renders exactly
like a lane of two overdue chores.

**Above both lists, what today asks to hold the pace.** The timed pursuits'
minutes and the counted pursuits' checkoffs, each in its own unit, with the
habits, review and Labs as checklists under them. Then what is behind beyond
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
A group that could not be read is said once, in the due list, where its absence
would read as nothing owed. A group with nothing in it prints nothing.

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
habits done and habits still to do.

**Color finds the structure and marks a state, and never fills a whole row.**
The two list headings are cyan and each group's name is blue, so the eye
reaches a group before it reads a row. A done entry's hour is green, the color
this screen says done with. A due row's note is yellow or red by how late it
is, and a group that could not be read is red. The text, the labels and the
handles stay plain.
"""

import datetime as dt
import json
from itertools import chain
from typing import Annotated
from typing import NamedTuple

import typer
from rich.text import Text

from doit import dashboard
from doit import happened
from doit import lanes
from doit import pursuits
from doit import quota
from doit import sources
from doit import window
from doit.happened import GROUP_INDENT
from doit.happened import HOUR_STYLE
from doit.happened import ITEM_INDENT
from doit.happened import group_heading
from doit.happened import has_something
from doit.happened import render_unavailable
from doit.lanes import Lane
from doit.lanes import Row
from doit.lanes import Urgency
from doit.render import column_width
from doit.render import console
from doit.render import error_console
from doit.render import fitted
from doit.render import handle_column
from doit.render import print_handle_below
from doit.render import span_text
from doit.render import terminal_width
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
ROUTINE_GROUPS = (*DAY_SET_LANES, 'pursuits')
DUE_PER_ROUTINE_GROUP = 10

# A review slug or a Lab id is the longest label here and fits. A longer one is
# clipped rather than pushing every description in its group off the line.
DUE_LABEL_MAX = 20

# What a row's text keeps after the widest label, before a handle may sit beside
# it. A handle that would take more goes on the line under its row.
DUE_TEXT_MIN = 16

LIST_STYLE = 'cyan'


def filled(template: str, handle: str) -> str:
    """A cell's handle as a command, where the lane's hint says how to make one.

    The dashboard prints a habit's bare id beside a hint reading `icb habits
    complete <id>`, and the reader puts the two together. A due row has one
    handle column, so they are put together here.
    """
    if handle and '<id>' in template:
        return template.replace('<id>', handle)
    return handle


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
        finished = {cell.text for group in done if group.name == lane.name for cell in group.grid}
        template = lane.hints[0] if lane.hints else ''
        left = [
            Row(cell.text, '', handle=filled(template, cell.handle)) for cell in lane.grid if not cell.done and cell.text not in finished
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
        missing.append(lanes.unavailable(lane.name, lane.title, f'no {supplier} entry under completions in sources.yml'))
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
        found.append(Lane(name=lane.name, title=lane.title, rows=rows, total=len(rows), hints=list(lane.hints)))
    return found


def register_owed(register: happened.Register, rows: list[dict], bounds: Bounds, now: dt.datetime) -> Lane:
    """What a register still owes today, ranked the way the dashboard's MAINTENANCE lane ranks it.

    Each register is asked its own `is_due`. One rule written here would agree
    with `doit review due` and disagree with `doit labs due`.

    An item done today is left off. One on a daily cadence done this morning
    still reports `overdue: 0`, because zero means "wanted today" and it was —
    so without this it would be listed as done and owed at once.
    """
    ranked = sorted(
        (
            dashboard.maintenance_row(happened.item_name(row), register.describe(row), row, register.act(row))
            for row in rows
            if not happened.lands_within(row.get('last'), bounds, now) and register.is_due(row)
        ),
        key=lambda entry: entry[0],
    )
    owed = [row for _, row in ranked]
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
        # A skipped pursuit is one you chose to pass on, as `doit next` says.
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
        rows.append(Row(name, described, note, Urgency.OVERDUE, f'doit pursuits log {name}'))
    return Lane(name='pursuits', title='PURSUITS', rows=rows, total=len(rows), hints=['doit next --explain'])


def pursuit_lanes(kept: happened.Kept, bounds: Bounds) -> list[Lane]:
    """The pursuits' due group, or why the register would not load; nothing where it is empty."""
    if kept.state_error:
        return [lanes.unavailable('pursuits', 'PURSUITS', kept.state_error)]
    if kept.state is None:
        return []
    return [pursuits_due(kept.state, bounds)]


CHECKLIST_GROUPS = (*DAY_SET_LANES, *(register.name for register in happened.registers()))


def checklists(done: list[Lane], due: list[Lane]) -> list[quota.Checklist]:
    """Each checklist group as done today of done today and still due, or why either half could not be read."""
    found = []
    for lane in due:
        if lane.name not in CHECKLIST_GROUPS:
            continue
        cleared_groups = [cleared for cleared in done if cleared.name == lane.name]
        failed = lane if not lane.available else next((cleared for cleared in cleared_groups if not cleared.available), None)
        if failed is not None:
            found.append(quota.Checklist(lane.name, unavailable=failed.reason))
            continue
        finished = sum(len(cleared.grid) for cleared in cleared_groups)
        found.append(quota.Checklist(lane.name, finished, finished + len(lane.rows)))
    return sorted(found, key=lambda entry: CHECKLIST_GROUPS.index(entry.name))


def day_quota(state: dict | None, done: list[Lane], due: list[Lane]) -> quota.Quota:
    """The figures above both lists, with the pursuits named as unavailable where their group could not be read."""
    failed = next((lane.reason for lane in due if lane.name == 'pursuits' and not lane.available), '')
    return quota.build(state, checklists(done, due), failed)


class Day(NamedTuple):
    """Today's two lists, each holding its groups in the order they print, and the figures above them."""

    done: list[Lane]
    due: list[Lane]
    figures: quota.Quota | None = None


def build(registry: sources.Registry, now: dt.datetime) -> Day:
    """Both lists, from one round of every configured source.

    One `sources.fetch` over both blocks, so the command costs the slowest
    single backend rather than the sum of two rounds.

    The due groups run appointments, pursuits, registers, then sets. An
    appointment keeps its hour whatever else is owed. A set you are part-way
    through is the one thing here you can finish in a minute, so it sits at the
    bottom where it does not push a commitment off the screen.

    The done groups lead with the day sets, then run in the fold's order.
    """
    bounds = window.day_of(now)
    lane_sources = list(registry.sources.values())
    completion_sources = list(registry.completions.values())
    results = sources.fetch(lane_sources + completion_sources, bounds)
    built = dashboard.lanes_of(registry, results, None)
    kept = happened.read_kept(now)
    folded = happened.fold(registry, results, kept, bounds, now)
    folded += missing_done_halves(built, folded)

    due = appointment_lanes(built) + pursuit_lanes(kept, bounds) + maintenance_lanes(kept, bounds, now) + set_lanes(built, folded)
    failed = {lane.name for lane in due if not lane.available}
    shown = [lane for lane in folded if lane.available or lane.name not in failed]
    done = sorted(shown, key=lambda lane: lane.name not in DAY_SET_LANES)
    return Day(done, due, day_quota(kept.state, done, due))


def render(day: Day, today: dt.date) -> None:
    width = terminal_width()
    console.rule(f'[cyan]Today[/] · {today.strftime("%a %d %b")}', align='left')
    if day.figures is not None:
        render_quota(day.figures)
    due = [lane for lane in day.due if has_something(lane)]
    done = [lane for lane in day.done if has_something(lane)]
    if due:
        console.print('STILL DUE', style=LIST_STYLE)
        render_due(due, width)
    if done:
        if due:
            console.print()
        console.print('DONE', style=LIST_STYLE)
        happened.render_groups(done, width)


def checkoffs_text(count: float) -> str:
    whole = round(count)
    return f'{whole} checkoff' if whole == 1 else f'{whole} checkoffs'


def joined(parts: list[Text]) -> Text:
    line = Text(GROUP_INDENT)
    for index, part in enumerate(parts):
        if index:
            line.append(' · ')
        line.append_text(part)
    return line


def today_parts(figures: quota.Quota) -> list[Text]:
    """What today asks in each unit, green once today has paid it."""
    if figures.unavailable:
        return [Text('pursuits unavailable', style='red')]
    parts = []
    minutes, checkoffs = figures.minutes, figures.checkoffs
    if minutes is not None and minutes.need > 0:
        part = Text(pursuits.minutes_text(minutes.done), style=HOUR_STYLE if minutes.done >= minutes.need else '')
        noun = 'pursuit' if minutes.asked_of == 1 else 'pursuits'
        part.append(f' of {pursuits.minutes_text(minutes.need)} across {minutes.asked_of} {noun}')
        parts.append(part)
    if checkoffs is not None and checkoffs.need > 0:
        part = Text(f'{round(checkoffs.done)}', style=HOUR_STYLE if checkoffs.done >= checkoffs.need else '')
        part.append(f' of {checkoffs_text(checkoffs.need)}')
        parts.append(part)
    return parts


def checklist_parts(figures: quota.Quota) -> list[Text]:
    parts = []
    for entry in figures.checklists:
        if entry.unavailable:
            parts.append(Text(f'{entry.name} unavailable', style='red'))
        else:
            parts.append(Text(f'{entry.name} {entry.done} of {entry.total}'))
    return parts


def behind_lines(figures: quota.Quota) -> list[Text]:
    """What is behind beyond today in each unit, then each timescale worth showing."""
    minutes, checkoffs = figures.minutes, figures.checkoffs
    if minutes is None or checkoffs is None or not (minutes.behind or checkoffs.behind):
        return []

    def amounts(extra_minutes: float, extra_checkoffs: float) -> str:
        said = [pursuits.minutes_text(extra_minutes)] if round(extra_minutes) else []
        return ' and '.join(said + ([checkoffs_text(extra_checkoffs)] if extra_checkoffs else []))

    lines = [Text(f'{GROUP_INDENT}{amounts(minutes.behind, checkoffs.behind)} behind')]
    for entry in figures.catch_up:
        if entry.shown:
            extra = amounts(entry.minutes_per_day, entry.checkoffs_per_day)
            lines.append(Text(f'{GROUP_INDENT}{extra} more a day catches up in {entry.label}'))
    return lines


def quota_lines(figures: quota.Quota) -> list[Text]:
    """Today's ask, the checklists, what is behind, and each catch-up worth showing.

    A figure is green once today has paid it, the color this screen says done
    with. Nothing else is colored but a part that could not be read.
    """
    lines = [joined(parts) for parts in (today_parts(figures), checklist_parts(figures)) if parts]
    return lines + behind_lines(figures)


def render_quota(figures: quota.Quota) -> None:
    for line in quota_lines(figures):
        console.print(line, no_wrap=True, overflow='ellipsis')


def visible_rows(lane: Lane) -> list[Row]:
    """A group's first rows, then one saying how many more it holds and where."""
    rows = lane.rows[: DUE_PER_ROUTINE_GROUP if lane.name in ROUTINE_GROUPS else DUE_PER_GROUP]
    hidden = max(lane.total, len(lane.rows)) - len(rows)
    if hidden > 0:
        rows = [*rows, Row(f'{hidden} more', '', handle=' · '.join(lane.hints))]
    return rows


def render_due(groups: list[Lane], width: int) -> None:
    """One line per outstanding thing: what, how late, what to type.

    The note and handle columns are sized across every group, so they line up
    down the whole list. The label column is sized per group, so a group of long
    review slugs does not push a pursuit's description off the line.

    The label, text and note are clipped rather than wrapped, because a wrapped
    row is two rows and this screen is read at a glance. A handle is never
    clipped, since a clipped command fails when typed. One that would leave a
    row less than `DUE_TEXT_MIN` columns of text beside the widest label goes on
    the line under its row.
    """
    shown = [(lane, visible_rows(lane)) for lane in groups]
    every = list(chain.from_iterable(rows for _, rows in shown))
    note_width = column_width(width, [row.note for row in every], 0.2, 10)
    note_gap = note_width + 1 if note_width else 0
    widest_label = min(DUE_LABEL_MAX, max((len(row.label) for row in every), default=0))
    label_gap = widest_label + 1 if widest_label else 0
    handle_room = width - len(ITEM_INDENT) - label_gap - DUE_TEXT_MIN - note_gap - 1
    handle_width = handle_column([f'↳ {row.handle}' for row in every if row.handle], handle_room)
    what_width = max(DUE_TEXT_MIN, width - len(ITEM_INDENT) - note_gap - (handle_width + 1 if handle_width else 0))
    for lane, rows in shown:
        console.print(group_heading(lane))
        if not lane.available:
            render_unavailable(lane)
            continue
        label_width = min(DUE_LABEL_MAX, max((len(row.label) for row in rows), default=0))
        text_width = max(0, what_width - label_width - (1 if label_width else 0))
        for row in rows:
            line = Text(ITEM_INDENT)
            if label_width:
                line.append(fitted(row.label, label_width, pad=True))
                line.append(' ')
            line.append(fitted(row.text, text_width, pad=True))
            if note_width:
                line.append(' ')
                line.append(fitted(row.note, note_width, pad=True, style=dashboard.NOTE_STYLES[row.urgency] or ''))
            handle = f'↳ {row.handle}' if row.handle else ''
            inline = bool(handle) and len(handle) <= handle_width
            if inline:
                line.append(' ')
                line.append(handle)
            line.rstrip()
            console.print(line, no_wrap=True, overflow='ellipsis')
            if handle and not inline:
                print_handle_below(handle, len(ITEM_INDENT) + label_width + (1 if label_width else 0), width)


def cmd_today(as_json: bool) -> int:
    registry = sources.load()
    for problem in registry.problems:
        error_console.print(f'sources.yml: {problem}')
    now = dt.datetime.now().astimezone()
    day = build(registry, now)

    if as_json:
        document = {
            'schema_version': lanes.SCHEMA_VERSION,
            'generated_at': lanes.timestamp(now),
            'done': [lanes.lane_to_dict(lane) for lane in day.done],
            'due': [lanes.lane_to_dict(lane) for lane in day.due],
            'quota': None if day.figures is None else quota.to_dict(day.figures),
        }
        # Plain print, never the rich console: a Console soft-wraps at terminal
        # width, which would put newlines inside JSON strings.
        print(json.dumps(document, indent=2))
    else:
        render(day, now.date())
    # A read-only glance always succeeds. Degradation is shown per group, and a
    # consumer of --json checks each lane's status rather than the exit code.
    return 0


def today_command(
    as_json: Annotated[bool, typer.Option('--json', help='Output the day as JSON to stdout.')] = False,
) -> None:
    """What today still wants, and what it has had."""
    raise typer.Exit(cmd_today(as_json))
