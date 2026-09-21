"""The day as a scoreboard — what today has had, and what it still wants.

The third density, beside `doit dashboard` and `doit next`. The dashboard reads
every lane and ranks across none of them; `next` draws one thing from the
weights. Neither says how much of the day is done, because an outstanding count
can only climb and a lane of hundreds of unread articles renders exactly like a
lane of two overdue chores.

**Only things a day can finish appear here.** Habits, review items and Labs each
have a floor a day can reach, so they carry a ratio. Everything else carries a
count or nothing — there is no honest denominator for articles read, and
inventing one would put a target on the screen that nobody set.

Nothing here recomputes due-ness. The dashboard mirrors the `overdue` a backend
emits rather than deriving it again, and that holds just as hard at this
density. So the due rows are taken from statuses and lanes already built. The
pursuits go through `build_state` instead, because doit owns that model and
there is no backend to mirror.

The model is `doit.lanes`, unchanged. A scoreboard row is a Lane whose `meta`
holds the ratio and whose `grid` holds the ticks, so `--json` emits the same
document `doit dashboard --json` does and no consumer learns a second schema.
Only the renderer is new, and one sibling key naming which lanes are counts.

**Color marks what differs between rows, never what every row has.** A label,
a handle and a title are on every line, so coloring them spends the whole
palette before anything has been said. Four things earn it here: how late a
row is, a tick that is done, a lane that could not be answered, and the two
headings that find the blocks. Everything else is plain, which is also what
leaves the late rows visible at a glance.
"""

import json
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

# The titles are there so a number you did not expect can be recognized without
# another command. The row is a count first.
NAMED_PER_ROW = 3

# Past this the day is not the problem.
DUE_ROWS = 12

# The due list's groups, sorted on before lateness is. An appointment keeps its
# hour whatever else is owed; a set you are part-way through is the one thing
# here you can finish in a minute, so it sits at the bottom where it does not
# push a commitment off the screen.
APPOINTMENTS, OWED, SETS = 0, 1, 2

SCOREBOARD_LABEL_WIDTH = 12
# Wide enough for the longest meta built here, `N touched · N behind`.
META_WIDTH = 22
DUE_LABEL_WIDTH = 11
TICK_DONE = '✓'
TICK_OPEN = '○'

# Beyond this a tick strip is a wall rather than a glance, and the ratio beside
# it already carries the number. A register in the tens is a row of circles
# nobody counts.
MAX_TICKS = 10


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


def completion_lane(name: str, title: str, texts: list[str], hint: str) -> Lane:
    """One count row: how many of a thing were finished today, and which.

    A grid of finished cells rather than rows, so `sum(cell.done)` is the count
    on every lane in this document — the habits lane arrives that way from the
    dashboard and a second convention here would make the two disagree.
    """
    return Lane(
        name=name,
        title=title,
        meta=f'{len(texts)} done',
        grid=[GridCell(text, True) for text in texts],
        total=len(texts),
        hints=[hint] if hint else [],
    )


def row_text(row: dict, label_field: str) -> str:
    return str(row.get(label_field) or '').strip() or '—'


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
        return [completion_lane(name, title, [row_text(row, label_field) for row in rows], hint)]

    return adapter


# Which completion source answers which row. The field names came from live
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
    """`meso review --since 1d` as one training row.

    One call covering sessions, log entries and measurements. `--since` takes a
    window rather than a date, so `1d` reaches back further than midnight and
    the day filter here is what narrows it.
    """
    payload = result.payload
    if not isinstance(payload, dict):
        return [lanes.unavailable('training', 'TRAINING', sources.reason('meso-review', result))]
    now = datetime.now().astimezone()
    texts = []
    for key, label_field, date_field in MESO_KINDS:
        for row in rows_done_today(payload.get(key), date_field, now):
            texts.append(row_text(row, label_field))
    return [completion_lane('training', 'TRAINING', texts, 'meso review --since 1d')]


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


def ratio_meta(done: int, total: int) -> str:
    return f'{done} of {total}'


def grid_lanes(built: list[dashboard.LaneView]) -> list[Lane]:
    """Every lane that arrived as a complete set rather than a ranked excerpt.

    A grid is the day by construction — `GridCell` exists to say "today's
    habits, not the top three". So this needs no list of names and picks up a
    future source that answers the same way.

    The meta is rewritten rather than carried through. A source phrases its own
    summary for its own lane, and on the dashboard that is right; in a column
    where every row is a ratio, one row reading "1 of 6 done today" beside
    another reading "0 of 11" makes the reader parse each row instead of
    scanning the column.
    """
    found = []
    for lane in built:
        if not lane.grid:
            continue
        done = sum(1 for cell in lane.grid if cell.done)
        found.append(
            Lane(
                name=lane.name,
                title=lane.title,
                meta=ratio_meta(done, len(lane.grid)),
                grid=list(lane.grid),
                total=len(lane.grid),
                hints=list(lane.hints),
                reason=lane.reason,
            )
        )
    return found


def statuses_lane(name: str, title: str, rows: list[dict], today: date, hint: str) -> Lane:
    """One maintenance row: of the things due today, how many are done.

    `review` and `labs` both emit `overdue` and `last`, so due-ness and done-ness
    are read rather than derived. `overdue >= 0` is the backend's own answer to
    "is this wanted today", and `None` is an item never done, which ranks above
    any number of days late.
    """
    stamp = today.isoformat()
    done = [row for row in rows if row.get('last') == stamp]
    # Done is subtracted from due rather than counted alongside it. An item on a
    # daily cadence done this morning still reports `overdue: 0`, because zero
    # means "wanted today" and it was — so without this it lands in both halves
    # and a register of one item reads `1 of 2`.
    finished = {id(row) for row in done}
    due = [row for row in rows if id(row) not in finished and (row.get('overdue') is None or row.get('overdue', -1) >= 0)]
    cells = [GridCell(str(row.get('id') or row.get('title') or ''), True) for row in done]
    cells += [GridCell(str(row.get('id') or row.get('title') or ''), False) for row in due]
    return Lane(
        name=name,
        title=title,
        meta=ratio_meta(len(done), len(done) + len(due)),
        grid=cells,
        total=len(done) + len(due),
        hints=[hint],
    )


def touched_today(state: dict, today: date) -> list[str]:
    """Pursuits with evidence of happening today, from either record.

    The journal answers what got typed and `evidence_days` answers what the apps
    saw, and a pursuit satisfied inside its own CLI appears only in the second.
    Both are true reports of the same act, which is why the union is the answer
    rather than either one.
    """
    stamp = today.isoformat()
    seen = {name for name, days in (state.get('evidence_days') or {}).items() if stamp in set(days)}
    now = state['now']
    for record in state.get('records') or []:
        if record.get('event') != journal.Event.DONE:
            continue
        if journal.local_day(record, now) == today:
            seen.add(str(record.get('pursuit')))
    return sorted(name for name in seen if name in state['active'])


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


def pursuits_lane(state: dict, today: date) -> Lane:
    """One row saying how much of the register today has moved.

    No ratio, deliberately. The weights imply intervals of days and weeks, so
    the active set is not a daily target and a ratio against it would put a
    goal on the screen the register never claimed. Two counts claim nothing.
    """
    touched = touched_today(state, today)
    behind = owing(state)
    meta = f'{len(touched)} touched · {len(behind)} behind' if behind else f'{len(touched)} touched'
    return Lane(
        name='pursuits',
        title='PURSUITS',
        meta=meta,
        grid=[GridCell(name, True) for name in touched],
        # The grid's own length, not the active set's. A total above the grid
        # renders this as a ratio row and ticks a box per pursuit.
        total=len(touched),
        hints=['doit next'],
    )


def logged_lane(state: dict, today: date) -> Lane:
    """What you typed into the journal today, and how long it came to.

    Minutes are summed only over the entries that carry one. A timed pursuit
    logged without `--minutes` records no duration on purpose, so counting it as
    zero would be as wrong as counting it as a whole checkoff.
    """
    now = state['now']
    entries = [
        record
        for record in state.get('records') or []
        if record.get('event') == journal.Event.DONE and journal.local_day(record, now) == today
    ]
    minutes = sum(
        float(record['duration_minutes'])
        for record in entries
        if isinstance(record.get('duration_minutes'), int | float) and not isinstance(record.get('duration_minutes'), bool)
    )
    plural = '' if len(entries) == 1 else 'ies'
    meta = f'{len(entries)} entr{plural or "y"}'
    if minutes:
        meta += f' · {round(minutes)} min'
    return Lane(
        name='logged',
        title='LOGGED',
        meta=meta,
        grid=[GridCell(str(record.get('pursuit') or ''), True) for record in entries],
        total=len(entries),
        hints=['doit log'],
    )


def due_lane(state: dict | None, built: list[dashboard.LaneView], grids: list[Lane], today: date) -> Lane:
    """Everything still wanted today, most overdue first, each with a handle.

    Several contributors in one list rather than one short list each: the
    question is what to do next, and a reader deciding that does not care which
    subsystem owns the answer.

    `grids` is every complete set on the scoreboard, which is not the same as
    the grids in `built`. Review and Labs are read as statuses rather than
    through the dashboard, so taking them from `built` alone would print their
    ratios two lines above a due list that never mentions them.

    A register that would not load costs its own two rows and the owed group.
    The appointments and the sets have nothing to do with the weights, so
    withholding them would turn one subsystem's failure into a blank screen.
    """
    rows: list[tuple[int, float, Row]] = []

    # Grouped before ranked; `APPOINTMENTS, OWED, SETS` carries why.
    for lane in built:
        if lane.name not in DAY_SHAPED_ROW_LANES:
            continue
        for row in lane.rows:
            if row.urgency in (Urgency.DUE, Urgency.OVERDUE):
                rows.append((APPOINTMENTS, 0.0, row))

    if state is not None:
        for name, due_in in owing(state):
            note = 'due today' if due_in is None or -1 < due_in <= 0 else f'{span_text(due_in)} overdue'
            rank = 0.0 if due_in is None else due_in
            described = state['active'][name].get('description', '')
            rows.append((OWED, rank, Row(name, described, note, Urgency.OVERDUE, f'doit log {name}')))

    for lane in grids:
        left = [cell for cell in lane.grid if not cell.done]
        if not left:
            continue
        hint = lane.hints[0] if lane.hints else ''
        # The names go in the text and the count in the note. A row saying only
        # "5 left" beside a ratio the scoreboard already printed two lines up
        # gives the reader nothing they did not have.
        named = ' · '.join(cell.text for cell in left[:NAMED_PER_ROW])
        rows.append((SETS, -float(len(left)), Row(lane.name, named, f'{len(left)} left', Urgency.DUE, hint)))

    rows.sort(key=lambda entry: (entry[0], entry[1]))
    ordered = [row for _, _, row in rows]
    return Lane(name='due', title='STILL DUE', rows=ordered, total=len(ordered), hints=['doit dashboard'])


def maintenance_lanes(today: date) -> list[Lane]:
    """The two registers doit keeps itself.

    Read as statuses rather than through the dashboard's `maintenance` lane,
    which interleaves them into one ranked excerpt and drops the done half —
    exactly the half this screen is for.
    """
    found = []
    for name, title, read, hint in (
        ('review', 'REVIEW', review.statuses, 'doit review due'),
        ('labs', 'LABS', labs.statuses, 'doit labs due'),
    ):
        try:
            found.append(statuses_lane(name, title, read(), today, hint))
        except (OSError, ValueError) as error:
            found.append(lanes.unavailable(name, title, str(error)))
    return found


def pursuit_lanes(now: datetime, today: date) -> tuple[list[Lane], dict | None]:
    """The register's two rows, and the state the due list also needs.

    Guarded the way the dashboard guards its own standing line. A register that
    refuses to load is a real failure mode and it must cost these two rows
    rather than the whole screen — the lanes either side of it have nothing to
    do with the weights.
    """
    try:
        register = pursuits.load_pursuits()
        if not register:
            return [], None
        state = pursuits.build_state(register, now)
    except (pursuits.RegisterError, OSError, ValueError) as error:
        return [lanes.unavailable('pursuits', 'PURSUITS', str(error))], None
    return [pursuits_lane(state, today), logged_lane(state, today)], state


class Day(NamedTuple):
    """Today's lanes, and which of them carry no denominator.

    `counts` is recorded as the document is built rather than inferred from the
    lanes afterwards. A ratio row with everything done has a full grid and a
    matching total, which is exactly what a count row looks like. So a habits
    register finally cleared would render as an app completion.
    """

    lanes: list[Lane]
    counts: frozenset[str]


def build(registry: sources.Registry, now: datetime) -> Day:
    """Today's whole document — the scoreboard, then the due list.

    One `sources.fetch` over both blocks, so the command costs the slowest
    single backend rather than the sum of two rounds.
    """
    today = now.date()
    lane_sources = list(registry.sources.values())
    completion_sources = list(registry.completions.values())
    results = sources.fetch(lane_sources + completion_sources)

    built = dashboard.lanes_of(registry, results, None)
    # Every complete set on this screen, collected as it is added. The due list
    # turns each one's unfinished half into a row, and a set it is not handed is
    # a ratio printed with nothing under it saying what is left.
    grids: list[Lane] = list(grid_lanes(built))
    scoreboard: list[Lane] = list(grids)

    register_lanes, state = pursuit_lanes(now, today)
    counts = {lane.name for lane in register_lanes}
    scoreboard.extend(register_lanes)

    maintenance = maintenance_lanes(today)
    grids.extend(maintenance)
    scoreboard.extend(maintenance)

    for source in completion_sources:
        produced = sources.lanes_from(source, results[source.id])
        counts.update(lane.name for lane in produced)
        scoreboard.extend(produced)

    scoreboard.append(due_lane(state, built, grids, today))
    return Day(scoreboard, frozenset(counts))


def tick_strip(lane: Lane) -> Text:
    """The set as ticks, green only on the cells that are done.

    Styled per cell rather than per strip: green is what the screen says `done`
    with, so an open circle carrying it reads as the opposite of what it means.
    """
    strip = Text()
    if len(lane.grid) > MAX_TICKS:
        return strip
    for cell in lane.grid:
        strip.append(TICK_DONE if cell.done else TICK_OPEN, style='green' if cell.done else '')
    return strip


def render(day: Day, today: date) -> None:
    width = terminal_width()
    console.rule(f'[cyan]Today[/] · {today.strftime("%a %d %b")}', align='left')
    console.print()

    scoreboard = [lane for lane in day.lanes if lane.name != 'due']
    for lane in scoreboard:
        if lane.name not in day.counts:
            render_scoreboard_row(lane, width, ticks=True)

    # A count of zero is still a fact about the day, but eight rows of zero is
    # a screen that stops being read. The ones that happened keep their row and
    # the rest are named on one line, so nothing is hidden and nothing repeats.
    counts = [lane for lane in scoreboard if lane.name in day.counts]
    spoken = [lane for lane in counts if lane.total or not lane.available]
    silent = [lane for lane in counts if lane.available and not lane.total]
    if spoken or silent:
        console.print()
    for lane in spoken:
        render_scoreboard_row(lane, width, ticks=False)
    if silent:
        line = fitted('nothing yet', SCOREBOARD_LABEL_WIDTH, pad=True)
        line.append('  ')
        line.append(fitted(' · '.join(lane.name for lane in silent), max(10, width - 16)))
        console.print(line, no_wrap=True, overflow='ellipsis')

    due = next((lane for lane in day.lanes if lane.name == 'due'), None)
    if due and due.rows:
        console.print()
        console.print(due.title, style='cyan')
        render_due(due.rows[:DUE_ROWS], width)


def render_scoreboard_row(lane: Lane, width: int, ticks: bool) -> None:
    line = fitted(lane.name, SCOREBOARD_LABEL_WIDTH, pad=True)
    if not lane.available:
        line.append('  ')
        line.append(f'unavailable — {lane.reason}', style='red')
        console.print(line, no_wrap=True, overflow='ellipsis')
        return
    line.append('  ')
    line.append(fitted(lane.meta, META_WIDTH, pad=True))
    strip = tick_strip(lane) if ticks else Text()
    named = [cell.text for cell in lane.grid if cell.done][:NAMED_PER_ROW]
    if strip:
        line.append('  ')
        line.append(strip)
    elif named:
        line.append('  ')
        line.append(fitted(' · '.join(named), max(10, width - 40)))
    line.rstrip()
    console.print(line, no_wrap=True, overflow='ellipsis')


def render_due(rows: list[Row], width: int) -> None:
    """One line per outstanding thing: what, how late, what to type.

    Clipped rather than wrapped, because a wrapped row is two rows and this
    screen is read at a glance.
    """
    note_width = column_width(width, [row.note for row in rows], 0.2, 10)
    handle_width = column_width(width, [f'↳ {row.handle}' for row in rows], 0.35, 14)
    text_width = max(12, width - 4 - DUE_LABEL_WIDTH - note_width - handle_width)
    for row in rows:
        line = fitted(row.label, DUE_LABEL_WIDTH, pad=True)
        line.append(' ')
        line.append(fitted(row.text, text_width, pad=True))
        if note_width:
            line.append(' ')
            note = fitted(row.note, note_width, pad=True)
            note.stylize(dashboard.NOTE_STYLES[row.urgency] or '')
            line.append(note)
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
        document = lanes.to_document(day.lanes, now)
        # A sibling key, not a lane field: `lanes.py` is the contract a source
        # conforms to and no source declares this. A second renderer cannot
        # recover the split from the lanes, for the reason `Day` gives.
        document['count_lanes'] = sorted(day.counts)
        # Plain print, never the rich console: a Console soft-wraps at terminal
        # width, which would put newlines inside JSON strings.
        print(json.dumps(document, indent=2))
    else:
        render(day, now.date())
    # A read-only glance always succeeds. Degradation is shown per row, and a
    # consumer of --json checks lanes[].status rather than the exit code.
    return 0


def today_command(
    as_json: Annotated[bool, typer.Option('--json', help='Output the lane model as JSON to stdout.')] = False,
) -> None:
    """What today has had, and what it still wants."""
    raise typer.Exit(cmd_today(as_json))
