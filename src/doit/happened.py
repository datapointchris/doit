"""What happened over a window, read once from every record that keeps it.

The fold for one question, "what happened". `doit happened` renders it over the
window asked, and `doit today` renders it over the day, so the two cannot
disagree about what a day held.

**Every record that keeps a completion is asked.** doit's own three are the
pursuit journal with each app's evidence, review, and Labs. The rest are every
`completions:` entry in `sources.yml`, each asked about the same window.

**Each row carries its source and its actor.** The source is the group it is
listed under. The actor is whatever the record wrote, an issue's `closed_by` or
a journal entry's `actor`, and is blank where the record keeps none. Three
actors write to the records: a person by hand, a session that person drives,
and an unattended job. The screen says which of the three, and `--json` carries
the identity as written.

**What is done is named, not counted.** Inside a group the entries run in the
order they happened, with the hour wherever the record kept one. A record
keeping only the day lists that day's entries after its timed ones, never at a
guessed midnight.

**The verdict line names every record asked.** A group that answered with
nothing prints nothing, so the verdict is what says it was asked. A group that
could not be read always prints, because a missing group reads as nothing done.

Review and Labs keep one date per item, the latest. An item done twice in a
window is listed once, on the later day.
"""

import datetime as dt
import json
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from typing import Annotated
from typing import NamedTuple

import typer
from rich.text import Text

from doit import actor
from doit import journal
from doit import labs
from doit import lanes
from doit import pursuits
from doit import review
from doit import sources
from doit import window
from doit.lanes import GridCell
from doit.lanes import Lane
from doit.render import console
from doit.render import error_console
from doit.render import fitted
from doit.render import terminal_width
from doit.window import Bounds

DEFAULT_SINCE = '7d'

GROUP_STYLE = 'bold blue'
HOUR_STYLE = 'green'

GROUP_INDENT = '  '
ITEM_INDENT = '    '
CLOCK_WIDTH = len('00:00')
DATE_FORMAT = '%a %d %b'
DATE_WIDTH = len('Mon 26 Oct')
ACTOR_WIDTH = max(len(actor.UNATTENDED), len('unrecorded'))
COLUMN_GAP = 2


# --- reading a record's date ---------------------------------------------------


def local_date(value: object, now: dt.datetime) -> dt.date | None:
    """The local calendar day a record's stamp landed on.

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
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def local_instant(value: object, now: dt.datetime) -> dt.datetime | None:
    """When a record says something happened, in local time, if it says an hour.

    A plain day parses as naive midnight. Shown, that is `00:00`, an hour nobody
    did anything at, and it sorts ahead of the whole morning. So only a stamp
    carrying an offset is a time here.
    """
    if isinstance(value, dt.datetime):
        stamp: dt.datetime | None = value
    else:
        stamp = journal.parse_time(str(value)) if value else None
    if stamp is None or stamp.tzinfo is None:
        return None
    return stamp.astimezone(now.tzinfo)


def instant_text(value: object, now: dt.datetime) -> str:
    """`GridCell.done_at` for a record's stamp: local, with its offset, or blank."""
    stamp = local_instant(value, now)
    return '' if stamp is None else stamp.isoformat(timespec='seconds')


def lands_within(value: object, bounds: Bounds, now: dt.datetime) -> bool:
    """Whether a record's stamp falls inside the window.

    An instant is inside or not. A plain day is inside when any of it is, the
    reading a backend gives its own day columns against a window of instants.
    So a book finished on the window's first day counts, though the window
    opens at three in the afternoon.
    """
    stamp = local_instant(value, now)
    if stamp is not None:
        return bounds.holds(stamp)
    day = local_date(value, now)
    return day is not None and bounds.touches(day)


def rows_within(payload: object, date_field: str, bounds: Bounds, now: dt.datetime) -> list[dict]:
    """The rows in a completions payload stamped inside the window.

    Filtered here even where the command already asked the backend for the
    window. The bounds in the argv save transfer; they are not what makes the
    answer right. An entry edited to drop them would otherwise report a whole
    backlog as this window's work, and report it silently.
    """
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict) and lands_within(row.get(date_field), bounds, now)]


# --- a done group --------------------------------------------------------------


def done_cell(text: str, stamp: object, now: dt.datetime, recorded_by: str = '') -> GridCell:
    """One finished thing, with its local hour where the record kept one and its local day wherever it kept that."""
    day = local_date(stamp, now)
    return GridCell(text, True, done_at=instant_text(stamp, now), done_on=day.isoformat() if day else '', actor=recorded_by)


def when_done(cell: GridCell) -> tuple[bool, str, bool, float]:
    """A day's timed entries in the order they happened, then its untimed ones; a cell whose day is unknown goes last.

    Ordered by instant rather than by text. A day crossing a clock change
    carries two offsets, and `10:00-04:00` sorts as text ahead of `13:00+00:00`
    though it happened an hour later.
    """
    day = cell.done_on or cell.done_at[:10]
    stamp = dt.datetime.fromisoformat(cell.done_at).timestamp() if cell.done_at else 0.0
    return (not day, day, not cell.done_at, stamp)


def done_lane(name: str, title: str, cells: list[GridCell], hint: str) -> Lane:
    """One group, in the order its entries happened.

    Sorted here rather than by the renderer, so `--json` hands a consumer the
    order the screen shows. The sort is stable, so untimed entries of one day
    keep the order their record gave them.
    """
    ordered = sorted(cells, key=when_done)
    return Lane(name=name, title=title, meta=f'{len(ordered)} done', grid=ordered, total=len(ordered), hints=[hint] if hint else [])


def row_text(row: dict, label_field: str) -> str:
    return str(row.get(label_field) or '').strip() or '—'


def hint_for(command: tuple[str, ...], bounds: Bounds) -> str:
    """The command that lists what a group read, over the same window, as a person would type it."""
    return shlex.join(part for part in sources.resolved_command(list(command), bounds) if part != '--json')


# --- the completion adapters ---------------------------------------------------

SINCE = sources.SINCE_PLACEHOLDER
UNTIL = sources.UNTIL_PLACEHOLDER


def flat_adapter(
    source_id: str, name: str, title: str, label_field: str, date_field: str, actor_field: str, command: tuple[str, ...]
) -> Callable[[sources.Result], list[Lane]]:
    """An adapter for a backend answering with a flat array of finished things.

    Most of the completion sources are this shape and differ only in which
    field is the title, which the date and which the actor. A closure here
    rather than more keys in `sources.yml`, because `lanes.py` rejects field
    mappings in that file by name.

    The failure reason is asked for under the source id, never the lane name.
    `reason` looks the argv back up by that id to name the program that
    rejected it, so a lane name here would lose the command and name a thing
    that was never a source.
    """

    def adapter(result: sources.Result) -> list[Lane]:
        if not isinstance(result.payload, list):
            return [lanes.unavailable(name, title, sources.reason(source_id, result))]
        now = dt.datetime.now().astimezone()
        bounds = result.bounds or window.day_of(now)
        cells = [
            done_cell(row_text(row, label_field), row.get(date_field), now, str(row.get(actor_field) or '') if actor_field else '')
            for row in rows_within(result.payload, date_field, bounds, now)
        ]
        return [done_lane(name, title, cells, hint_for(command, bounds))]

    return adapter


# Which completion source answers which group, with the field holding its title,
# its date and its actor, and the command it runs. The field names came from
# live payloads, so check one before changing a row here. Only issues record an
# actor.
#
# learning takes no window, so its whole history comes back and the filter does
# all of the narrowing.
FLAT_COMPLETIONS: tuple[tuple[str, str, str, str, str, str, tuple[str, ...]], ...] = (
    (
        'habits',
        'HABITS',
        'icb-habits',
        'name',
        'complete_date',
        '',
        ('icb', 'habits', 'completed', '--since', SINCE, '--until', UNTIL, '--json'),
    ),
    (
        'tasks',
        'TASKS',
        'icb-tasks',
        'name',
        'complete_date',
        '',
        ('icb', 'tasks', 'list', '--status', 'completed', '--since', SINCE, '--until', UNTIL, '--json'),
    ),
    (
        'projects',
        'PROJECTS',
        'icb-projects',
        'title',
        'completed_at',
        '',
        ('icb', 'projects', 'items', 'list', '--status', 'completed', '--since', SINCE, '--until', UNTIL, '--json'),
    ),
    (
        'issues',
        'ISSUES',
        'icb-issues',
        'title',
        'closed_ts',
        'closed_by',
        ('icb', 'issues', 'list', '--status', 'completed', '--since', SINCE, '--until', UNTIL, '--json'),
    ),
    (
        'articles',
        'ARTICLES',
        'icb-articles',
        'title',
        'last_read_date',
        '',
        ('icb', 'articles', 'list', '--since', SINCE, '--until', UNTIL, '--json'),
    ),
    (
        'books',
        'BOOKS',
        'icb-books',
        'title',
        'read_finish_date',
        '',
        ('icb', 'books', 'list', '--since', SINCE, '--until', UNTIL, '--json'),
    ),
    (
        'learning',
        'LEARNING',
        'learning-completed',
        'title',
        'completed_at',
        '',
        ('learning', 'completed', 'list', '--all', '--json'),
    ),
)

# meso answers three kinds in one document and names each kind's rows
# differently, which is why it cannot use the flat adapter above.
MESO_KINDS = (
    ('sessions', 'workout_name', 'performed_on'),
    ('log_entries', 'mood', 'entry_date'),
    ('measurements', 'metric', 'measured_on'),
)
MESO_COMMAND = ('meso', 'review', '--since', SINCE, '--until', UNTIL, '--json')


def meso_adapter(result: sources.Result) -> list[Lane]:
    """`meso review` over the window as one training group: sessions, log entries and measurements.

    meso records days, not hours, so every entry here is untimed.
    """
    payload = result.payload
    if not isinstance(payload, dict):
        return [lanes.unavailable('training', 'TRAINING', sources.reason('meso-review', result))]
    now = dt.datetime.now().astimezone()
    bounds = result.bounds or window.day_of(now)
    cells = [
        done_cell(row_text(row, label_field), row.get(date_field), now)
        for key, label_field, date_field in MESO_KINDS
        for row in rows_within(payload.get(key), date_field, bounds, now)
    ]
    return [done_lane('training', 'TRAINING', cells, hint_for(MESO_COMMAND, bounds))]


# Every completion adapter doit ships, with the group each one produces. One
# table rather than a list beside a loop: a registration nothing enumerates is a
# row no test can assert about, and these share a global registry with the
# dashboard's.
COMPLETION_ADAPTERS: tuple[tuple[str, str], ...] = tuple((adapter_id, lane_name) for lane_name, _, adapter_id, *_ in FLAT_COMPLETIONS) + (
    ('meso-review', 'training'),
)

for lane_name, lane_title, adapter_id, label, stamp, recorded_by, command in FLAT_COMPLETIONS:
    sources.register_adapter(adapter_id, command, flat_adapter(adapter_id, lane_name, lane_title, label, stamp, recorded_by, command))
sources.register_adapter('meso-review', MESO_COMMAND, meso_adapter)


# --- doit's own records --------------------------------------------------------


def journal_entry(record: dict) -> str:
    """A journal entry as the pursuit it was for, and its minutes if it kept any.

    A timed pursuit logged without `--minutes` records no duration on purpose,
    so it gets no number rather than a zero.
    """
    name = str(record.get('pursuit') or '')
    minutes = journal.recorded_minutes(record)
    if minutes is not None:
        return f'{name} · {round(minutes)} min'
    return name


def moved_within(state: dict, bounds: Bounds) -> list[tuple[str, GridCell]]:
    """Every entry for a pursuit the window moved, from either record, beside its name.

    The journal answers what got typed, one entry per act at its own time. The
    evidence answers which days the apps saw it. A day a pursuit was satisfied
    inside its own CLI and never typed is added once, at the hour its app last
    saw it where that hour falls on the day.

    Every journal entry counts, whether or not the register still names its
    pursuit. Pausing a pursuit this afternoon does not undo this morning.
    """
    now = state['now']
    entries = []
    typed: set[tuple[str, dt.date]] = set()
    for record in state.get('records') or []:
        stamp = journal.parse_time(record.get('occurred_at') or record.get('logged_at'))
        if record.get('event') != journal.Event.DONE or stamp is None:
            continue
        stamp = stamp.astimezone(now.tzinfo)
        if not bounds.holds(stamp):
            continue
        name = str(record.get('pursuit') or '')
        typed.add((name, stamp.date()))
        entries.append((name, done_cell(journal_entry(record), stamp, now, str(record.get('actor') or ''))))
    observed = state.get('observed') or {}
    for name, days in (state.get('evidence_days') or {}).items():
        seen = local_instant(observed.get(name), now)
        for day in days:
            if (name, day) in typed or not bounds.touches(day):
                continue
            entries.append((name, done_cell(name, seen if seen is not None and seen.date() == day else day, now)))
    return entries


def pursuits_group(state: dict, bounds: Bounds) -> Lane:
    return done_lane('pursuits', 'PURSUITS', [cell for _, cell in moved_within(state, bounds)], 'doit next --explain')


def item_name(row: dict) -> str:
    return str(row.get('id') or row.get('title') or '')


class Register(NamedTuple):
    """One register doit keeps, and how an item in it is described.

    A slug names a row to the tooling, and what it is for exists only in its
    description or title, so that is the text.
    """

    name: str
    title: str
    read: Callable[[], list[dict]]
    describe: Callable[[dict], str]
    hint: str


def registers() -> tuple[Register, ...]:
    """Built per call rather than at import, so each read goes through the module's current `statuses`."""
    return (
        Register('review', 'REVIEW', review.statuses, lambda row: str(row.get('desc') or item_name(row)), 'doit review due'),
        Register('labs', 'LABS', labs.statuses, lambda row: str(row.get('title') or item_name(row)), 'doit labs due'),
    )


def register_group(register: Register, rows: list[dict], bounds: Bounds, now: dt.datetime) -> Lane:
    cells = [done_cell(register.describe(row), row.get('last'), now) for row in rows if lands_within(row.get('last'), bounds, now)]
    return done_lane(register.name, register.title, cells, register.hint)


@dataclass
class Kept:
    """What doit keeps itself, read once so a screen built from two lists reads it once.

    `state` is None where the register is empty or would not load, and
    `state_error` says which. A register that could not be read is absent from
    `registers`, and `register_errors` says why.
    """

    state: dict | None = None
    state_error: str = ''
    registers: dict[str, list[dict]] = field(default_factory=dict)
    register_errors: dict[str, str] = field(default_factory=dict)


def read_kept(now: dt.datetime) -> Kept:
    """The pursuit state and both registers, each failure kept to its own record.

    A register that refuses to load is a real failure mode, and it must cost
    that record's group rather than the whole screen.
    """
    kept = Kept()
    try:
        register = pursuits.load_pursuits()
        if register:
            kept.state = pursuits.build_state(register, now)
    except (pursuits.RegisterError, OSError, ValueError) as error:
        kept.state_error = str(error)
    for entry in registers():
        try:
            kept.registers[entry.name] = entry.read()
        except (OSError, ValueError) as error:
            kept.register_errors[entry.name] = str(error)
    return kept


# --- the fold ------------------------------------------------------------------


def fold(registry: sources.Registry, results: dict[str, sources.Result], kept: Kept, bounds: Bounds, now: dt.datetime) -> list[Lane]:
    """Every record's group over the window: doit's own, then each completion source in the order `sources.yml` lists it.

    A completion source that produced no group at all still gets one under its
    id. That is a conforming source declaring no `lanes:`. Failed, its group is
    unavailable. Answered, it is empty, so the verdict counts it among the
    records asked rather than losing it.
    """
    groups: list[Lane] = []
    if kept.state_error:
        groups.append(lanes.unavailable('pursuits', 'PURSUITS', kept.state_error))
    elif kept.state is not None:
        groups.append(pursuits_group(kept.state, bounds))
    for entry in registers():
        if entry.name in kept.register_errors:
            groups.append(lanes.unavailable(entry.name, entry.title, kept.register_errors[entry.name]))
        else:
            groups.append(register_group(entry, kept.registers.get(entry.name, []), bounds, now))
    for source in registry.completions.values():
        result = results.get(source.id)
        built = sources.lanes_from(source, result) if result is not None else []
        if not built:
            title = source.id.upper()
            answered = result is not None and result.failure is None
            built = [
                Lane(name=source.id, title=title) if answered else lanes.unavailable(source.id, title, sources.reason(source.id, result))
            ]
        groups.extend(built)
    return groups


def read(registry: sources.Registry, bounds: Bounds, now: dt.datetime) -> list[Lane]:
    """The fold over `bounds`, from one round of every completion source."""
    results = sources.fetch(list(registry.completions.values()), bounds)
    return fold(registry, results, read_kept(now), bounds, now)


# --- the screen ----------------------------------------------------------------


def has_something(lane: Lane) -> bool:
    return not lane.available or bool(lane.grid or lane.rows)


def group_heading(lane: Lane) -> Text:
    return Text(f'{GROUP_INDENT}{lane.name}', style=GROUP_STYLE)


def render_unavailable(lane: Lane) -> None:
    line = Text(ITEM_INDENT)
    line.append(f'unavailable — {lane.reason}', style='red')
    console.print(line, no_wrap=True, overflow='ellipsis')


def clock(done_at: str) -> str:
    return dt.datetime.fromisoformat(done_at).strftime('%H:%M') if done_at else ''


def day_text(cell: GridCell) -> str:
    day = cell.done_on or cell.done_at[:10]
    return dt.date.fromisoformat(day).strftime(DATE_FORMAT) if day else ''


def done_line(cell: GridCell, text_width: int, date_width: int = 0, actor_width: int = 0) -> Text:
    """One thing done: its day where the window spans several, its hour, who did it where its group records that, then what it was.

    An entry whose record kept only the day leaves the hour blank rather than
    printing a guess, so its text still lines up with the timed ones.
    """
    line = Text(ITEM_INDENT)
    if date_width:
        line.append(day_text(cell).ljust(date_width + COLUMN_GAP))
    hour = clock(cell.done_at)
    line.append(hour, style=HOUR_STYLE)
    line.append(' ' * (CLOCK_WIDTH - len(hour) + COLUMN_GAP))
    if actor_width:
        who = actor.kind(cell.actor)
        line.append(fitted(who or 'unrecorded', actor_width, pad=True, style='' if who else 'dim'))
        line.append(' ' * COLUMN_GAP)
    line.append(fitted(cell.text, text_width))
    return line


def render_groups(groups: list[Lane], width: int, *, dated: bool = False) -> None:
    """Each group under its name, one line per thing done.

    The actor column appears in a group only where one of its entries has an
    actor, so a group whose record keeps none spends no width on it.
    """
    date_width = DATE_WIDTH if dated else 0
    for lane in groups:
        console.print(group_heading(lane))
        if not lane.available:
            render_unavailable(lane)
            continue
        actor_width = ACTOR_WIDTH if any(cell.actor for cell in lane.grid) else 0
        used = len(ITEM_INDENT) + CLOCK_WIDTH + COLUMN_GAP
        used += date_width + COLUMN_GAP if date_width else 0
        used += actor_width + COLUMN_GAP if actor_width else 0
        for cell in lane.grid:
            console.print(done_line(cell, max(10, width - used), date_width, actor_width), no_wrap=True, overflow='ellipsis')


def verdict_lines(groups: list[Lane]) -> list[Text]:
    """Which records were asked, how many answered, and which recorded who did what."""
    asked = [lane.name for lane in groups]
    failed = [lane.name for lane in groups if not lane.available]
    recording = [lane.name for lane in groups if any(cell.actor for cell in lane.grid)]
    names = Text(f'{GROUP_INDENT}asked {", ".join(asked)}' if asked else f'{GROUP_INDENT}no record to ask')
    answered = Text(GROUP_INDENT)
    if failed:
        answered.append(f'{len(asked) - len(failed)} of {len(asked)} answered', style='red')
    else:
        answered.append(f'all {len(asked)} answered')
    answered.append(f' · actor recorded by {", ".join(recording)}' if recording else ' · no actor recorded')
    return [names, answered]


def window_title(bounds: Bounds, open_ended: bool) -> str:
    stop = 'now' if open_ended else f'{bounds.stop:%a %d %b %H:%M}'
    return f'{bounds.start:%a %d %b %H:%M} to {stop}'


def render(groups: list[Lane], bounds: Bounds, open_ended: bool) -> None:
    console.rule(f'[cyan]Happened[/] · {window_title(bounds, open_ended)}', align='left')
    for line in verdict_lines(groups):
        console.print(line, no_wrap=True, overflow='ellipsis')
    shown = [lane for lane in groups if has_something(lane)]
    if shown:
        console.print()
        render_groups(shown, terminal_width(), dated=bounds.days() > 1)


def cmd_happened(since: str, until: str | None, as_json: bool) -> int:
    now = dt.datetime.now().astimezone()
    try:
        start, stop = window.window(since, until, now)
    except ValueError as error:
        error_console.print(f'doit happened: {error}')
        return 2
    bounds = Bounds(start or now, stop or now)
    if bounds.start >= bounds.stop:
        error_console.print(f'doit happened: --since {since} is not before {until or "now"}.')
        return 2
    registry = sources.load()
    for problem in registry.problems:
        error_console.print(f'sources.yml: {problem}')
    groups = read(registry, bounds, now)
    if as_json:
        document = {
            'schema_version': lanes.SCHEMA_VERSION,
            'generated_at': lanes.timestamp(now),
            'since': bounds.start.isoformat(timespec='seconds'),
            'until': bounds.stop.isoformat(timespec='seconds'),
            'asked': [lane.name for lane in groups],
            'unavailable': [lane.name for lane in groups if not lane.available],
            'groups': [lanes.lane_to_dict(lane) for lane in groups],
        }
        # Plain print, never the rich console: a Console soft-wraps at terminal
        # width, which would put newlines inside JSON strings.
        print(json.dumps(document, indent=2))
    else:
        render(groups, bounds, open_ended=until is None)
    # A read-only glance always succeeds. Degradation is shown per group, and a
    # consumer of --json reads `unavailable` rather than the exit code.
    return 0


def happened_command(
    since: Annotated[str, typer.Option('--since', help=f'Start of the window: {window.FORMS}.')] = DEFAULT_SINCE,
    until: Annotated[
        str | None, typer.Option('--until', help='End of the window, in the same forms; a day includes all of itself. Now where omitted.')
    ] = None,
    as_json: Annotated[bool, typer.Option('--json', help='Output every group as JSON to stdout.')] = False,
) -> None:
    """What every record says happened over a window, and who did it where the record says."""
    raise typer.Exit(cmd_happened(since, until, as_json))
