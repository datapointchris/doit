"""The day in two lists — what today has had, and what it still wants.

The third density, beside `doit dashboard` and `doit next`. The dashboard reads
every lane and ranks across none of them; `next` draws one thing from the
weights. Neither says what the day has already had, because an outstanding
count can only climb and a lane of hundreds of unread articles renders exactly
like a lane of two overdue chores.

**What is done is named, not counted.** Each group is one record: the habits,
the pursuits, review, Labs, then each completion source. Inside a group the
entries run in the order they happened, with the hour wherever the record kept
one. A count would leave the reader to recall what it stood for.

**What is not done is said once, in the due list.** Each group there holds what
one record owes, in that record's own order, and shows the first three. The
rest are one command away, on the row saying how many there are.

A group with nothing in it prints nothing. An empty heading spends a line on
what its absence already says. A group that could not be read always prints,
because a missing group reads as nothing to do.

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

**Color marks what differs between rows, never what every row has.** A label,
a handle and an hour are on every line, so coloring them spends the whole
palette before anything has been said. Three things earn it here: how late a
row is, a group that could not be read, and the two headings that find the
lists. Everything else is plain, which is also what leaves the late rows
visible at a glance.
"""

import json
from collections.abc import Callable
from datetime import date
from datetime import datetime
from typing import Annotated
from typing import NamedTuple

import typer
from rich.text import Text

from doit import dashboard
from doit import journal
from doit import labs
from doit import lanes
from doit import pursuits
from doit import review
from doit import sources
from doit.lanes import GridCell
from doit.lanes import Lane
from doit.lanes import Row
from doit.lanes import Urgency
from doit.render import column_width
from doit.render import console
from doit.render import error_console
from doit.render import fitted
from doit.render import span_text
from doit.render import terminal_width

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
# drop a failed habits lane from both lists without a word.
DAY_SET_LANES = ('habits',)

# Past three a group is a backlog. The row after them says how many more there
# are and what to type to see them.
DUE_PER_GROUP = 3

# A review slug or a Lab id is the longest label here and fits. A longer one is
# clipped rather than pushing every description in its group off the line.
DUE_LABEL_MAX = 20

GROUP_INDENT = '  '
ITEM_INDENT = '    '
CLOCK_WIDTH = len('00:00')


def local_date(value: object, now: datetime) -> date | None:
    """The local calendar day a backend's timestamp landed on.

    Both shapes, because these fields are not consistent across apps and never
    will be: `read_finish_date` is a plain day and `complete_date` is an
    instant. One function, so both land on the same day.

    The instant is converted before its day is read. The apps stamp in UTC, so
    the first ten characters of `2026-09-21T01:00:00Z` say the 21st while the
    work happened at nine in the evening of the 20th in `-04:00`. Truncating
    first moves every evening onto tomorrow.

    Truncation is the fallback, for a plain day with no time to convert and for
    a stamp whose day is readable while its time is not.
    """
    if not value:
        return None
    text = str(value)
    stamp = journal.parse_time(text)
    if stamp is not None:
        return stamp.astimezone(now.tzinfo).date() if stamp.tzinfo else stamp.date()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def local_instant(value: object, now: datetime) -> datetime | None:
    """When a record says something happened, in local time, if it says an hour.

    A plain day parses as naive midnight. Shown, that is `00:00`, an hour nobody
    did anything at, and it sorts ahead of the whole morning. So only a stamp
    carrying an offset is a time here.
    """
    if isinstance(value, datetime):
        stamp: datetime | None = value
    else:
        stamp = journal.parse_time(str(value)) if value else None
    if stamp is None or stamp.tzinfo is None:
        return None
    return stamp.astimezone(now.tzinfo)


def instant_text(value: object, now: datetime) -> str:
    """`GridCell.done_at` for a record's stamp: local, with its offset, or blank."""
    stamp = local_instant(value, now)
    return '' if stamp is None else stamp.isoformat(timespec='seconds')


def rows_done_today(payload: object, date_field: str, now: datetime) -> list[dict]:
    """The rows in a completions payload whose date is today.

    Filtered here even where the command already asked the backend for one day.
    `{today}` in the argv is a transfer cost saved, not the thing that makes the
    answer right — an entry edited to drop it would otherwise report a whole
    backlog as this morning's work, and report it silently.
    """
    if not isinstance(payload, list):
        return []
    today = now.date()
    return [row for row in payload if isinstance(row, dict) and local_date(row.get(date_field), now) == today]


def done_lane(name: str, title: str, cells: list[GridCell], hint: str) -> Lane:
    """One group of the done list, in the order its entries happened.

    Sorted here rather than by the renderer, so `--json` hands a consumer the
    order the screen shows. An entry whose record kept only the day has no place
    in that order, so it follows the timed ones in the order it arrived.
    """
    timed = sorted((cell for cell in cells if cell.done_at), key=lambda cell: datetime.fromisoformat(cell.done_at))
    ordered = timed + [cell for cell in cells if not cell.done_at]
    return Lane(name=name, title=title, meta=f'{len(ordered)} done', grid=ordered, total=len(ordered), hints=[hint] if hint else [])


def row_text(row: dict, label_field: str) -> str:
    return str(row.get(label_field) or '').strip() or '—'


def completion_cell(row: dict, label_field: str, date_field: str, now: datetime) -> GridCell:
    return GridCell(row_text(row, label_field), True, done_at=instant_text(row.get(date_field), now))


def flat_adapter(source_id: str, name: str, title: str, label_field: str, date_field: str, hint: str) -> sources.Adapter:
    """An adapter for a backend answering with a flat array of finished things.

    Most of the completion sources are this shape and differ only in which
    field is the title and which is the date. A closure here rather than two
    more keys in `sources.yml`, because `lanes.py` rejects field mappings in
    that file by name.

    The failure reason is asked for under the source id, never the lane name.
    `reason` looks the configured argv back up by that id to say which command
    an old binary rejected, so a lane name here would lose the command and name
    a thing that was never a source.
    """

    def adapter(result: sources.Result) -> list[Lane]:
        if result.payload is None or not isinstance(result.payload, list):
            return [lanes.unavailable(name, title, sources.reason(source_id, result))]
        now = datetime.now().astimezone()
        rows = rows_done_today(result.payload, date_field, now)
        return [done_lane(name, title, [completion_cell(row, label_field, date_field, now) for row in rows], hint)]

    return adapter


# Which completion source answers which group. The field names came from live
# payloads, so check one before changing a row here.
FLAT_COMPLETIONS = (
    ('tasks', 'TASKS', 'icb-tasks', 'name', 'complete_date', 'icb tasks list --status completed'),
    ('projects', 'PROJECTS', 'icb-projects', 'title', 'completed_at', 'icb projects items list --status completed'),
    ('articles', 'ARTICLES', 'icb-articles', 'title', 'last_read_date', 'icb articles list'),
    ('books', 'BOOKS', 'icb-books', 'title', 'read_finish_date', 'icb books list'),
    ('learning', 'LEARNING', 'learning-completed', 'title', 'completed_at', 'learning completed list --all'),
)

# meso answers three kinds in one document and names each kind's rows
# differently, which is why it cannot use the flat adapter above.
MESO_KINDS = (
    ('sessions', 'workout_name', 'performed_on'),
    ('log_entries', 'mood', 'entry_date'),
    ('measurements', 'metric', 'measured_on'),
)


def meso_adapter(result: sources.Result) -> list[Lane]:
    """`meso review --since 1d` as one training group.

    One call covering sessions, log entries and measurements. `--since` takes a
    window rather than a date, so `1d` reaches back further than midnight and
    the day filter here is what narrows it. meso records days, not hours, so
    every entry here is untimed.
    """
    payload = result.payload
    if not isinstance(payload, dict):
        return [lanes.unavailable('training', 'TRAINING', sources.reason('meso-review', result))]
    now = datetime.now().astimezone()
    cells = []
    for key, label_field, date_field in MESO_KINDS:
        for row in rows_done_today(payload.get(key), date_field, now):
            cells.append(completion_cell(row, label_field, date_field, now))
    return [done_lane('training', 'TRAINING', cells, 'meso review --since 1d')]


# Every completion adapter doit ships, with the lane each one produces. One
# table rather than a list beside a loop, for the reason `SHIPPED_ADAPTERS` is
# one: a registration nothing enumerates is a row no test can assert about, and
# these share a global registry with the dashboard's.
COMPLETION_ADAPTERS: tuple[tuple[str, str], ...] = tuple(
    (adapter_id, lane_name) for lane_name, _, adapter_id, _, _, _ in FLAT_COMPLETIONS
) + (('meso-review', 'training'),)

for lane_name, lane_title, adapter_id, label, stamp, command in FLAT_COMPLETIONS:
    sources.register_adapter(adapter_id, flat_adapter(adapter_id, lane_name, lane_title, label, stamp, command))
sources.register_adapter('meso-review', meso_adapter)


def filled(template: str, handle: str) -> str:
    """A cell's handle as a command, where the lane's hint says how to make one.

    The dashboard prints a habit's bare id beside a hint reading `icb habits
    complete <id>`, and the reader puts the two together. A due row has one
    handle column, so they are put together here.
    """
    if handle and '<id>' in template:
        return template.replace('<id>', handle)
    return handle


def set_lanes(built: list[dashboard.LaneView], now: datetime) -> tuple[list[Lane], list[Lane]]:
    """Each day set split in two: the done group, and the group still to do.

    A set that could not be read goes to the due list alone. That is where its
    absence would read as nothing left to do.
    """
    done: list[Lane] = []
    due: list[Lane] = []
    for lane in built:
        if lane.name not in DAY_SET_LANES:
            continue
        if not lane.available:
            due.append(lane)
            continue
        finished = [GridCell(cell.text, True, done_at=instant_text(cell.done_at, now)) for cell in lane.grid if cell.done]
        done.append(done_lane(lane.name, lane.title, finished, ''))
        template = lane.hints[0] if lane.hints else ''
        left = [Row(cell.text, '', handle=filled(template, cell.handle)) for cell in lane.grid if not cell.done]
        due.append(Lane(name=lane.name, title=lane.title, rows=left, total=len(left), hints=[f'doit dashboard --lane {lane.name}']))
    return done, due


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


def item_name(row: dict) -> str:
    return str(row.get('id') or row.get('title') or '')


def register_lanes(
    name: str,
    title: str,
    rows: list[dict],
    is_due: Callable[[dict], bool],
    today: date,
    describe: Callable[[dict], str],
    act: Callable[[dict], str],
    hint: str,
) -> tuple[Lane, Lane]:
    """A register's done group, and its group of what is still owed.

    `is_due` is the register's own predicate. Review is due when never done or
    past its date; a Lab is due only when it is also scheduled, so an on-demand
    Lab never is. One rule written here would agree with `doit review due` and
    disagree with `doit labs due`.

    The owed group is ranked by `dashboard.maintenance_row`, the order the
    dashboard's MAINTENANCE lane already gives these.
    """
    stamp = today.isoformat()
    done = [row for row in rows if row.get('last') == stamp]
    # Done is subtracted from due rather than counted alongside it. An item on a
    # daily cadence done this morning still reports `overdue: 0`, because zero
    # means "wanted today" and it was — so without this it is cleared and owed
    # at once.
    finished = {id(row) for row in done}
    ranked = sorted(
        (
            dashboard.maintenance_row(item_name(row), describe(row), row, act(row))
            for row in rows
            if id(row) not in finished and is_due(row)
        ),
        key=lambda entry: entry[0],
    )
    owed = [row for _, row in ranked]
    return (
        done_lane(name, title, [GridCell(describe(row), True) for row in done], hint),
        Lane(name=name, title=title, rows=owed, total=len(owed), hints=[hint]),
    )


def maintenance_lanes(today: date) -> tuple[list[Lane], list[Lane]]:
    """The two registers doit keeps itself, each split into done and owed.

    Read as statuses rather than through the dashboard's `maintenance` lane,
    which interleaves them into one ranked excerpt and drops the done half.

    A register's slug names a row to the tooling, and what it is for exists only
    in its description or title, so that is the text. A review item's own
    `command` is its handle. A Lab has none, because it is a document you open,
    and the verb that opens it is doit's.
    """
    done: list[Lane] = []
    due: list[Lane] = []
    for name, title, read, is_due, describe, act, hint in (
        (
            'review',
            'REVIEW',
            review.statuses,
            lambda row: review.is_due(row.get('overdue')),
            lambda row: str(row.get('desc') or item_name(row)),
            lambda row: str(row.get('command') or ''),
            'doit review due',
        ),
        (
            'labs',
            'LABS',
            labs.statuses,
            labs.is_due_row,
            lambda row: str(row.get('title') or item_name(row)),
            lambda row: f'doit labs show {item_name(row)}',
            'doit labs due',
        ),
    ):
        try:
            cleared, owed = register_lanes(name, title, read(), is_due, today, describe, act, hint)
        except (OSError, ValueError) as error:
            due.append(lanes.unavailable(name, title, str(error)))
            continue
        done.append(cleared)
        due.append(owed)
    return done, due


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
        if state['balance'][name] < state['checkoff_size'][name]:
            continue
        behind.append((name, pursuits.due_in_days(state, name)))
    return sorted(behind, key=lambda row: (row[1] is None, row[1] if row[1] is not None else 0))


def journal_entry(record: dict) -> str:
    """A journal entry as the pursuit it was for, and its minutes if it kept any.

    A timed pursuit logged without `--minutes` records no duration on purpose,
    so it gets no number rather than a zero.
    """
    name = str(record.get('pursuit') or '')
    minutes = record.get('duration_minutes')
    if isinstance(minutes, int | float) and not isinstance(minutes, bool):
        return f'{name} · {round(minutes)} min'
    return name


def pursuits_done(state: dict, today: date) -> Lane:
    """Every pursuit today moved, from either record.

    The journal answers what got typed, one entry per act at its own time. The
    evidence answers what the apps saw. A pursuit satisfied inside its own CLI
    appears only there, so it is added once, at the last time its app saw it.
    One also typed today is already listed, and its evidence adds nothing.

    Only pursuits still in the register. A journal is history and the register
    is now, so commenting one out takes it off today.
    """
    now = state['now']
    active = state['active']
    cells = []
    typed = set()
    for record in state.get('records') or []:
        name = str(record.get('pursuit') or '')
        if record.get('event') != journal.Event.DONE or name not in active or journal.local_day(record, now) != today:
            continue
        typed.add(name)
        cells.append(GridCell(journal_entry(record), True, done_at=instant_text(record.get('occurred_at') or record.get('logged_at'), now)))
    observed = state.get('observed') or {}
    for name, days in (state.get('evidence_days') or {}).items():
        if name in typed or name not in active or today not in days:
            continue
        seen = local_instant(observed.get(name), now)
        when = seen.isoformat(timespec='seconds') if seen is not None and seen.date() == today else ''
        cells.append(GridCell(name, True, done_at=when))
    return done_lane('pursuits', 'PURSUITS', cells, 'doit next --explain')


def pursuits_due(state: dict) -> Lane:
    """Each pursuit owing a checkoff, furthest behind first, with the command that logs it."""
    rows = []
    for name, due_in in owing(state):
        note = 'due today' if due_in is None or -1 < due_in <= 0 else f'{span_text(due_in)} overdue'
        described = state['active'][name].get('description', '')
        rows.append(Row(name, described, note, Urgency.OVERDUE, f'doit log {name}'))
    return Lane(name='pursuits', title='PURSUITS', rows=rows, total=len(rows), hints=['doit next --explain'])


def pursuit_lanes(now: datetime, today: date) -> tuple[list[Lane], list[Lane]]:
    """The pursuits' group in each list.

    Guarded the way the dashboard guards its own standing line. A register that
    refuses to load is a real failure mode and it must cost these two groups
    rather than the whole screen — the rest has nothing to do with the weights.
    The failure is reported in the due list, where a missing group reads as
    nothing owed.
    """
    try:
        register = pursuits.load_pursuits()
        if not register:
            return [], []
        state = pursuits.build_state(register, now)
    except (pursuits.RegisterError, OSError, ValueError) as error:
        return [], [lanes.unavailable('pursuits', 'PURSUITS', str(error))]
    return [pursuits_done(state, today)], [pursuits_due(state)]


class Day(NamedTuple):
    """Today's two lists, each holding its groups in the order they print."""

    done: list[Lane]
    due: list[Lane]


def build(registry: sources.Registry, now: datetime) -> Day:
    """Both lists, from one round of every configured source.

    One `sources.fetch` over both blocks, so the command costs the slowest
    single backend rather than the sum of two rounds.

    The due groups run appointments, pursuits, registers, then sets. An
    appointment keeps its hour whatever else is owed. A set you are part-way
    through is the one thing here you can finish in a minute, so it sits at the
    bottom where it does not push a commitment off the screen.
    """
    today = now.date()
    lane_sources = list(registry.sources.values())
    completion_sources = list(registry.completions.values())
    results = sources.fetch(lane_sources + completion_sources)
    built = dashboard.lanes_of(registry, results, None)

    sets_done, sets_due = set_lanes(built, now)
    moved, owed = pursuit_lanes(now, today)
    cleared, backlog = maintenance_lanes(today)

    done = sets_done + moved + cleared
    for source in completion_sources:
        done.extend(sources.lanes_from(source, results[source.id]))
    due = appointment_lanes(built) + owed + backlog + sets_due
    return Day(done, due)


def has_something(lane: Lane) -> bool:
    return not lane.available or bool(lane.grid or lane.rows)


def render(day: Day, today: date) -> None:
    width = terminal_width()
    console.rule(f'[cyan]Today[/] · {today.strftime("%a %d %b")}', align='left')
    done = [lane for lane in day.done if has_something(lane)]
    due = [lane for lane in day.due if has_something(lane)]
    if done:
        console.print('DONE', style='cyan')
        render_done(done, width)
    if due:
        if done:
            console.print()
        console.print('STILL DUE', style='cyan')
        render_due(due, width)


def render_heading(lane: Lane) -> None:
    console.print(Text(f'{GROUP_INDENT}{lane.name}', style='bold'))


def render_unavailable(lane: Lane) -> None:
    line = Text(ITEM_INDENT)
    line.append(f'unavailable — {lane.reason}', style='red')
    console.print(line, no_wrap=True, overflow='ellipsis')


def clock(done_at: str) -> str:
    return datetime.fromisoformat(done_at).strftime('%H:%M') if done_at else ''


def render_done(groups: list[Lane], width: int) -> None:
    """One line per thing done: the hour it happened, then what it was.

    An entry whose record kept only the day leaves the hour blank rather than
    printing a guess, so its text still lines up with the timed ones.
    """
    text_width = max(10, width - len(ITEM_INDENT) - CLOCK_WIDTH - 2)
    for lane in groups:
        render_heading(lane)
        if not lane.available:
            render_unavailable(lane)
            continue
        for cell in lane.grid:
            line = Text(ITEM_INDENT)
            line.append(clock(cell.done_at).ljust(CLOCK_WIDTH))
            line.append('  ')
            line.append(fitted(cell.text, text_width))
            console.print(line, no_wrap=True, overflow='ellipsis')


def visible_rows(lane: Lane) -> list[Row]:
    """A group's first rows, then one saying how many more it holds and where."""
    rows = lane.rows[:DUE_PER_GROUP]
    hidden = max(lane.total, len(lane.rows)) - len(rows)
    if hidden > 0:
        rows = [*rows, Row(f'{hidden} more', '', handle=' · '.join(lane.hints))]
    return rows


def render_due(groups: list[Lane], width: int) -> None:
    """One line per outstanding thing: what, how late, what to type.

    The note and handle columns are sized across every group, so they line up
    down the whole list. The label column is sized per group, so a group of long
    review slugs does not push a pursuit's description off the line.

    Clipped rather than wrapped, because a wrapped row is two rows and this
    screen is read at a glance.
    """
    shown = [(lane, visible_rows(lane)) for lane in groups]
    every = [row for _, rows in shown for row in rows]
    note_width = column_width(width, [row.note for row in every], 0.2, 10)
    handle_width = column_width(width, [f'↳ {row.handle}' for row in every if row.handle], 0.35, 14)
    gaps = (1 if note_width else 0) + (1 if handle_width else 0)
    what_width = max(16, width - len(ITEM_INDENT) - note_width - handle_width - gaps)
    for lane, rows in shown:
        render_heading(lane)
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
            if handle_width and row.handle:
                line.append(' ')
                line.append(fitted(f'↳ {row.handle}', handle_width))
            line.rstrip()
            console.print(line, no_wrap=True, overflow='ellipsis')


def cmd_today(as_json: bool) -> int:
    registry = sources.load()
    for problem in registry.problems:
        error_console.print(f'sources.yml: {problem}')
    now = datetime.now().astimezone()
    day = build(registry, now)

    if as_json:
        document = {
            'schema_version': lanes.SCHEMA_VERSION,
            'generated_at': lanes.timestamp(now),
            'done': [lanes.lane_to_dict(lane) for lane in day.done],
            'due': [lanes.lane_to_dict(lane) for lane in day.due],
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
    as_json: Annotated[bool, typer.Option('--json', help='Output both lists as JSON to stdout.')] = False,
) -> None:
    """What today has had, and what it still wants."""
    raise typer.Exit(cmd_today(as_json))
