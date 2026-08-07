"""The human single pane of glass. Invoked as `doit dashboard`.

Answers "what is outstanding across everything?" in one glance: open tasks,
habits still due, what you're learning, what you're reading, maintenance due, the
next project items, and what's approaching.

Lanes, not a merged list. Each lane is independently ordered by its own backend
and you pick one by time and energy — there is no cross-source ranking, no
scoring, and no metrics, because a single ordering over unlike things would be
invented rather than meaningful.

`doit next` is the sibling that does rank across everything, and it is allowed to
because it is not inventing the ordering — it draws on a weight you declared per
pursuit. Nothing here should grow toward that: this is the read of what is
outstanding, that is the decision about the next hour.

Every row names a thing you could act on. A lane that can only report structure
(a track's current unit, a maintenance entry's slug) states it as context or
resolves it to the underlying resource instead of spending a row on it.

This is a renderer, nothing more. Every backend speaks `--json` and owns its own
domain rules (which book is next, which habit is due today, when a Lab is
overdue); the dashboard only lays out what they return. That is why it knows no app's
semantics. It reads doit's own review and labs lanes in-process rather than
shelling back out to itself — those return the same rows their `--json` prints,
so the backend still owns due-ness and nothing here re-derives a schedule.

`is_due_row` below therefore mirrors `doit.cadence.is_due` rather than importing
it. Inside one package that reads as removable duplication. It is not: backends
own due-ness, and the day this file imports the cadence module is the day the
dashboard starts re-deriving a schedule a backend already computed.

Not to be confused with `forge brief`, which is the dev brief across *repos* for
a coding session. Same plumbing, different audience.
"""

import math
import shutil
from collections.abc import Callable
from datetime import date
from datetime import datetime
from typing import Annotated
from urllib.parse import urlsplit

import typer
from rich.text import Text

from doit import labs
from doit import lanes as lanemodel
from doit import review
from doit import sources
from doit.lanes import GridCell
from doit.lanes import Lane as LaneView
from doit.lanes import Row
from doit.lanes import Urgency
from doit.lanes import unavailable
from doit.render import console
from doit.render import first_sentence
from doit.render import join_context

NOTE_STYLES = {Urgency.NONE: None, Urgency.DUE: 'yellow', Urgency.OVERDUE: 'red'}

# doit's own lanes, read in-process. These are not sources: the source registry
# exists so doit need not know which *other* apps are installed, and doit does
# know its own modules.
#
# They were subprocesses — `doit review list --json` — on the grounds that
# uniformity over `--json` was the contract. It cost two full interpreter starts
# per dashboard (0.73s of 0.98s CPU on the maintenance lane) to read a local YAML
# file and a directory of markdown already reachable from here. The contract that
# actually matters is unaffected: these functions return the exact rows the
# `--json` output prints, so the backend still owns due-ness and the dashboard
# still never re-derives a cadence.
LOCAL_BACKENDS = {
    'review': review.statuses,
    'labs': labs.statuses,
}

# The dashboard is a glance: three rows a lane, or ten when you asked for that
# one lane specifically. Grids are exempt — see LaneView.
ROW_CAP = 3
FOCUSED_ROW_CAP = 10

# Lanes whose useful unit is bigger than the default. Tasks is the list you
# actually pick from; learning carries one row per stream (the current section
# plus each track), so three would silently drop a whole stream.
LANE_ROW_CAPS = {'tasks': 5, 'learning': 5}

# The `icb overview` schema this build understands. A newer one is reported
# rather than half-read: dotfiles and ichrisbirch install independently.
ICB_SCHEMA_VERSION = 2

# The status ids `learning` assigns; the names come from `learning statuses`.
LEARNING_NOT_STARTED = 1
LEARNING_IN_PROGRESS = 2

# A cap, not a target — it stops the note column flying off to the right of an
# ultrawide terminal. It was 100, which on any normal terminal threw away width a
# row needed: a project name arrived as "CLI machine contract …" while thirty
# columns sat unused to the right of it.
MAX_WIDTH = 140
LABEL_WIDTH = 8

# A trailing column takes a share of the line rather than a fixed count. A flat
# cap is wrong at both ends — too tight to name a repo and a project on a wide
# terminal, and wide enough to crowd out the thing itself on a narrow one. Both
# only ever bind when the content is genuinely long, because a column is sized to
# what is actually on screen first.
NOTE_WIDTH_SHARE = 0.4
NOTE_WIDTH_MIN = 22
HANDLE_WIDTH_SHARE = 0.3
HANDLE_WIDTH_MIN = 20
TEXT_WIDTH_MIN = 20

# Marks the handle as something to type rather than something to read. The same
# arrow the nudge and `doit review due` use for the same field.
HANDLE_MARK = '↳'

# Two columns of habits, because a dozen single-name rows would swamp every
# other lane while a single run-on line hides which ones are left.
GRID_COLUMNS = 2
GRID_GUTTER = 3
GRID_MIN_COLUMN_WIDTH = 24

# How many of a queue to list; the rest of the pile is a count in the heading.
QUEUE_ROWS = 3

# Past a season out an exact day count stops meaning anything, so the relative
# label switches unit rather than switching format.
RELATIVE_DAYS_LIMIT = 90


def icb_context(results: dict[str, sources.Result], sections: tuple[str, ...]) -> tuple[dict | None, str]:
    """The icb payload plus any reason the given sections are degraded.

    A payload is refused outright only when the call failed or its schema is
    newer than this build understands. A section-keyed warning still returns the
    payload: that lane degrades, the rest of the snapshot does not.
    """
    result = results.get('icb')
    if result is None or result.payload is None:
        return None, sources.reason('icb', result)

    payload = result.payload
    if not isinstance(payload, dict):
        return None, 'icb returned unreadable --json output'

    version = payload.get('schema_version', 0)
    if version > ICB_SCHEMA_VERSION:
        return None, f'icb overview schema v{version} is newer than this dashboard — update doit'

    for warning in payload.get('warnings') or []:
        if warning.get('section') in sections:
            return payload, warning.get('message', '')
    return payload, ''


def plural(count: int, noun: str) -> str:
    return f'{count} {noun}' if count == 1 else f'{count} {noun}s'


def clean(text: str) -> str:
    """Collapse a stored string onto one line.

    Scraped article titles arrive wrapped in the source page's newlines and
    tabs, which would otherwise break the row's alignment and its clip width.
    """
    return ' '.join((text or '').split())


def view_handle(command: str, item: dict) -> str:
    """What to type to see one row in full.

    Every backend here has a `view` verb, and it is the right one for a glance:
    it is the read, so a dashboard never puts a write in front of you, and it is
    uniform across the namespaces so the column means one thing everywhere. Empty
    when the backend gave no id, because a handle that would fail is worse than
    no handle — it reads as something you can run.
    """
    identifier = item.get('id')
    return f'{command} {identifier}' if identifier else ''


def source_host(url: str) -> str:
    """The site an article came from.

    Three hundred unread titles say what to read and nothing about where it is or
    who wrote it, and the host is the one field that separates a vendor blog post
    from a paper without opening either.
    """
    host = urlsplit(url or '').netloc
    return host.removeprefix('www.')


def describe(title: str, detail: str) -> str:
    """A row's own words plus the gist of whatever note the backend carries,
    because a bare title like "Glove 80" names a thing without saying what to do
    with it.

    The gist, not the note: an item's notes hold the reasoning, the rejected
    alternatives and the pre-flight checks, and flattening all of that into a row
    that then gets clipped ends the line in the middle of the second paragraph.
    """
    detail = first_sentence(detail)
    return f'{title} — {detail}' if detail else title


def build_tasks_lane(results: dict[str, sources.Result], today: date) -> LaneView:
    payload, reason = icb_context(results, ('tasks',))
    if payload is None:
        return unavailable('tasks', 'TASKS', reason)

    section = payload.get('tasks') or {}
    items = section.get('items') or []
    total = section.get('total', len(items))
    # `p3` rather than a bare `3`: in a gutter that elsewhere holds words, a lone
    # numeral reads as a row number. The priority is not the id, so without the
    # handle there is nothing on the row you could act on.
    rows = [
        Row(
            f'p{task.get("priority", "")}',
            describe(task.get('name', ''), task.get('notes', '')),
            task.get('category', ''),
            handle=view_handle('icb tasks view', task),
        )
        for task in items
    ]
    return LaneView(
        name='tasks',
        title='TASKS',
        meta=f'{total} open',
        rows=rows,
        total=total,
        hints=['icb tasks todo'],
        reason=reason,
    )


def build_habits_lane(results: dict[str, sources.Result], today: date) -> LaneView:
    payload, reason = icb_context(results, ('habits',))
    if payload is None:
        return unavailable('habits', 'HABITS', reason)

    section = payload.get('habits') or {}
    due = section.get('due_today') or []
    done = section.get('completed_today') or []
    current_total = section.get('current_total', len(due) + len(done))

    # Every current habit is due every day, so the useful view is the whole set
    # with the finished ones ticked off — not a ranked excerpt. Ordering by
    # category and name keeps a habit in the same place all day, so ticking one
    # off never shuffles the rest.
    #
    # Only the outstanding ones carry an id: a completion record identifies the
    # completion, not the habit, and a finished habit needs no handle anyway.
    entries = [(habit, False) for habit in due] + [(habit, True) for habit in done]
    entries.sort(key=lambda entry: (habit_category(entry[0]), entry[0].get('name', '')))
    grid = [GridCell(habit.get('name', ''), completed, '' if completed else str(habit.get('id', ''))) for habit, completed in entries]

    return LaneView(
        name='habits',
        title='HABITS',
        meta=f'{len(done)} of {current_total} done today',
        grid=grid,
        hints=['icb habits complete <id>'],
        reason=reason,
    )


def habit_category(habit: dict) -> str:
    return (habit.get('category') or {}).get('name', '')


def build_books_lane(results: dict[str, sources.Result], today: date) -> LaneView:
    payload, reason = icb_context(results, ('books',))
    if payload is None:
        return unavailable('books', 'BOOKS', reason)

    section = payload.get('books') or {}
    reading = section.get('reading') or []
    next_up = section.get('next_up') or []

    rows = round_robin(
        [book_row('reading', book) for book in reading],
        [book_row('next', book) for book in next_up[:QUEUE_ROWS]],
    )
    # The queue size comes from the backend's own total, never from the handful
    # of rows built here — icb's --limit would otherwise decide what the heading
    # claims the pile is.
    queued = section.get('next_up_total', len(next_up))
    return LaneView(
        name='books',
        title='BOOKS',
        meta=f'{len(reading)} reading · {queued} queued',
        rows=rows,
        total=len(reading) + queued,
        hints=['icb books list --ownership owned'],
        reason=reason,
    )


def book_row(label: str, book: dict) -> Row:
    return Row(label, clean(book.get('title', '')), clean(book.get('author', '')), handle=view_handle('icb books view', book))


def build_articles_lane(results: dict[str, sources.Result], today: date) -> LaneView:
    payload, reason = icb_context(results, ('articles',))
    if payload is None:
        return unavailable('articles', 'ARTICLES', reason)

    section = payload.get('articles') or {}
    current = section.get('current')
    unread = section.get('unread') or []
    unread_total = section.get('unread_total', len(unread))

    rows = [article_row('reading', current)] if current else []
    rows += [article_row('next', article) for article in unread[:QUEUE_ROWS]]
    return LaneView(
        name='articles',
        title='ARTICLES',
        meta=f'{unread_total} unread',
        rows=rows,
        total=unread_total + (1 if current else 0),
        hints=['icb articles list'],
        reason=reason,
    )


def article_row(label: str, article: dict) -> Row:
    return Row(
        label,
        clean(article.get('title', '')),
        source_host(article.get('url', '')),
        handle=view_handle('icb articles view', article),
    )


def build_projects_lane(results: dict[str, sources.Result], today: date) -> LaneView:
    payload, reason = icb_context(results, ('project_items',))
    if payload is None:
        return unavailable('projects', 'PROJECTS', reason)

    section = payload.get('project_items') or {}
    next_items = section.get('next') or []
    blocked = section.get('blocked') or []
    next_total = section.get('next_total', len(next_items))
    blocked_total = section.get('blocked_total', len(blocked))

    rows = [project_item_row('next', item) for item in next_items]
    rows += [project_item_row('blocked', item) for item in blocked]
    return LaneView(
        name='projects',
        title='PROJECTS',
        meta=f'{next_total} next · {blocked_total} blocked',
        rows=rows,
        total=next_total + blocked_total,
        # The only lane whose rows carry no handle. An item's id is a UUID, so
        # `icb projects items view <id>` runs to sixty columns and would be
        # ellipsised into something that looks copyable and is not — leaving the
        # title too narrow to read into the bargain. The verb goes in the hint
        # instead, and `doit next` prints it in full for the item it draws,
        # having a line to spend on one.
        hints=['icb projects items list', 'icb projects items view <id>'],
        reason=reason,
    )


def project_item_row(label: str, item: dict) -> Row:
    """The item, what it means, and where the work lands.

    The repo comes first because it is the one word that says where you would go
    to do this; the projects follow because they say which effort it serves. An
    item can sit in several projects, so all of them are named — picking a first
    would be arbitrary. Either field can be absent: an errand has no repo, and an
    older `icb` build omits membership entirely.
    """
    projects = [project.get('name', '') for project in item.get('projects') or []]
    where = join_context([item.get('repo'), *projects])
    return Row(label, describe(item.get('title', ''), item.get('notes', '')), where)


def build_upcoming_lane(results: dict[str, sources.Result], today: date) -> LaneView:
    payload, reason = icb_context(results, ('countdowns', 'events'))
    if payload is None:
        return unavailable('upcoming', 'UPCOMING', reason)

    countdowns = payload.get('countdowns') or {}
    events = payload.get('events') or {}

    # An event's venue is where it is, which is most of what you want from a row
    # about somewhere you have to be; a countdown has only its notes.
    entries = []
    for countdown in countdowns.get('items') or []:
        text = describe(countdown.get('name', ''), countdown.get('notes', ''))
        entries.append((countdown.get('due_date', ''), text, view_handle('icb countdowns view', countdown)))
    for event in events.get('items') or []:
        text = describe(event.get('name', ''), event.get('venue') or event.get('notes', ''))
        entries.append((event_date(event.get('date', '')), text, view_handle('icb events view', event)))
    entries.sort(key=lambda entry: entry[0])

    # The gutter always answers "how far away", the note always answers "which
    # day". Which kind of entry it is says nothing about what you would do next,
    # so it does not earn the column — the handle names it anyway.
    rows = [Row(relative_day(due, today), text, calendar_day(due), day_urgency(due, today), handle) for due, text, handle in entries]
    countdown_total = countdowns.get('total', 0)
    event_total = events.get('total', 0)
    return LaneView(
        name='upcoming',
        title='UPCOMING',
        meta=f'{plural(countdown_total, "countdown")} · {plural(event_total, "event")}',
        rows=rows,
        total=countdown_total + event_total,
        hints=['icb countdowns list', 'icb events list'],
        reason=reason,
    )


def build_learning_lane(results: dict[str, sources.Result], today: date) -> LaneView:
    result = results.get('learning')
    if result is None or not isinstance(result.payload, dict):
        return unavailable('learning', 'LEARNING', sources.reason('learning', result))

    payload = result.payload
    section = payload.get('current_section') or {}
    progress = section.get('progress') or {}
    completed = progress.get('completed_items', 0)
    total_items = progress.get('total_items', 0)
    meta = f'{section.get("name", "")} · {completed} of {total_items} done' if section else ''

    # Every row is a resource you could open right now. The section supplies its
    # own next few; each track supplies exactly one, already chosen by `learning`
    # — a unit name is a position in a curriculum, not something you can sit down
    # and do.
    started = []
    unstarted = []
    seen = set()
    for resource in payload.get('in_progress_resources') or []:
        seen.add(resource.get('id'))
        started.append(resource_row('open', resource))
    for item in section.get('items') or []:
        resource = item.get('resource') or {}
        if resource.get('id') in seen:
            continue
        if resource.get('status_id') == LEARNING_IN_PROGRESS:
            seen.add(resource.get('id'))
            started.append(resource_row('open', resource))
        elif resource.get('status_id') == LEARNING_NOT_STARTED:
            seen.add(resource.get('id'))
            unstarted.append(resource_row('start', resource))

    # Interleaved, not appended: the tracks are the streams most easily crowded
    # out, and they are the reason the lane spans more than the current section.
    rows = round_robin(track_rows(payload.get('track_focus') or [], seen), started + unstarted)
    return LaneView(
        name='learning',
        title='LEARNING',
        meta=meta,
        rows=rows,
        total=len(rows),
        hints=['learning overview'],
    )


def resource_row(label: str, resource: dict, note: str = '') -> Row:
    """One openable resource. The handle is the read verb rather than the URL the
    resource carries: a link is not something you type, and `view` prints it
    along with everything else the row had no room for."""
    return Row(label, resource.get('title', ''), note, handle=view_handle('learning resources view', resource))


def track_rows(focuses: list[dict], seen: set) -> list[Row]:
    """One openable resource per track, noted with the track it advances.

    The note carries the track's name and progress because the row's title says
    nothing about which stream it belongs to — and that, not the resource, is
    what you are choosing between.
    """
    rows = []
    for focus in focuses:
        resource = focus.get('resource')
        if not resource or resource.get('id') in seen:
            continue
        seen.add(resource.get('id'))
        progress = focus.get('progress') or {}
        note = f'{focus.get("display_name", "")} {progress.get("completed_items", 0)}/{progress.get("total_items", 0)}'
        rows.append(resource_row('open' if focus.get('started') else 'start', resource, note))
    return rows


def build_maintenance_lane(results: dict[str, sources.Result], today: date) -> LaneView:
    review = results.get('review')
    labs = results.get('labs')

    ranked_reviews = []
    ranked_labs = []
    reasons = []

    # The register slug (`indy-reindex`) identifies a row to the tooling; what it
    # is for only exists in the description, so that is what gets shown — and the
    # register's own `command` is the handle, because a row saying "Re-index indy"
    # without saying `indy index` names the intent and withholds the act.
    review_items = review.payload if review else None
    if isinstance(review_items, list):
        for item in review_items:
            if is_due_row(item):
                ranked_reviews.append(maintenance_row('review', item.get('desc') or item.get('id', ''), item, item.get('command', '')))
    else:
        reasons.append(sources.reason('doit review', review))

    # A Lab carries no command of its own — it is a document you open, and the
    # verb that opens it is doit's. Naming it here is not backend knowledge
    # leaking in: this is doit's own lane, reading doit's own module.
    labs_items = labs.payload if labs else None
    if isinstance(labs_items, list):
        for item in labs_items:
            if item.get('scheduled') and is_due_row(item):
                handle = f'doit labs show {item.get("id", "")}'
                ranked_labs.append(maintenance_row('lab', item.get('title') or item.get('id', ''), item, handle))
    else:
        reasons.append(sources.reason('doit labs', labs))

    if len(reasons) == 2:
        return unavailable('maintenance', 'MAINTENANCE', '; '.join(reasons))

    # Ranked within a kind, then interleaved across the two. A single sort put
    # every review above every lab, because a never-run row of either kind ranks
    # identically and reviews were collected first — so a heading reading
    # "14 reviews · 16 labs due" sat above ten reviews and no lab at all.
    ranked_reviews.sort(key=lambda entry: entry[0])
    ranked_labs.sort(key=lambda entry: entry[0])
    review_due, labs_due = len(ranked_reviews), len(ranked_labs)
    rows = round_robin([row for _, row in ranked_reviews], [row for _, row in ranked_labs])
    return LaneView(
        name='maintenance',
        title='MAINTENANCE',
        meta=f'{plural(review_due, "review")} · {plural(labs_due, "lab")} due',
        rows=rows,
        total=len(rows),
        hints=['doit review due', 'doit labs due'],
        reason='; '.join(reasons),
    )


def maintenance_row(label: str, text: str, item: dict, handle: str) -> tuple[tuple[int, int], Row]:
    """A row plus how late it is, paired so the lane can rank on the number the
    backend gave rather than on the sentence built from it. Sorting the note text
    would put "12d overdue" above "3d overdue", and never-run entries — the least
    urgent state there is — above everything genuinely due."""
    overdue = item.get('overdue')
    if overdue is None:
        return (2, 0), Row(label, text, 'never run', Urgency.NONE, handle)
    if overdue == 0:
        return (1, 0), Row(label, text, 'due today', Urgency.DUE, handle)
    return (0, -overdue), Row(label, text, f'{overdue}d overdue', Urgency.OVERDUE, handle)


def round_robin(*groups: list[Row]) -> list[Row]:
    """Order rows so every kind gets a slot before any kind gets a second one.

    The row cap is deliberately small, so a straight concatenation would let one
    kind fill the lane: three books in progress would hide what is next, which is
    the least useful ordering of exactly the same rows.
    """
    ordered = []
    for index in range(max((len(group) for group in groups), default=0)):
        for group in groups:
            if index < len(group):
                ordered.append(group[index])
    return ordered


def is_due_row(item: dict) -> bool:
    """A row is due when it has never been done, or is at/past its due date.

    Mirrors doit.cadence.is_due over the `overdue` field the backends already
    computed — the dashboard never re-derives a cadence itself. See the module
    docstring for why this is not the duplication it looks like.
    """
    overdue = item.get('overdue')
    return overdue is None or overdue >= 0


def event_date(timestamp: str) -> str:
    """The calendar date of an RFC3339 event timestamp, for sorting alongside
    countdowns, which carry a bare date."""
    try:
        return datetime.fromisoformat(timestamp).astimezone().date().isoformat()
    except ValueError:
        return timestamp[:10]


def days_away(due: str, today: date) -> int | None:
    try:
        return (date.fromisoformat(due) - today).days
    except ValueError:
        return None


def relative_day(due: str, today: date) -> str:
    """A gutter-width distance. Past a season out the label changes unit, never
    format, so every row in the lane still answers the same question."""
    delta = days_away(due, today)
    if delta is None:
        return due
    if delta == 0:
        return 'today'
    if delta < 0:
        return f'{-delta}d ago'
    if delta <= RELATIVE_DAYS_LIMIT:
        return f'in {delta}d'
    target = date.fromisoformat(due)
    months = (target.year - today.year) * 12 + (target.month - today.month)
    return f'in {months}mo'


def calendar_day(due: str) -> str:
    try:
        return date.fromisoformat(due).strftime('%d %b %Y')
    except ValueError:
        return due


def day_urgency(due: str, today: date) -> Urgency:
    delta = days_away(due, today)
    if delta is None:
        return Urgency.NONE
    if delta < 0:
        return Urgency.OVERDUE
    if delta == 0:
        return Urgency.DUE
    return Urgency.NONE


# Which builder answers which lane, and which source's model it reads. This is
# doit's knowledge of apps that predate the lane contract, and it shrinks by one
# entry every time an app starts emitting lanes itself.
ICB_LANES = (
    ('tasks', build_tasks_lane),
    ('habits', build_habits_lane),
    ('books', build_books_lane),
    ('articles', build_articles_lane),
    ('projects', build_projects_lane),
    ('upcoming', build_upcoming_lane),
)

LOCAL_LANES = ('maintenance',)

# Every lane doit can name today. A conforming source contributes lanes that are
# not in this list, which is the point — it is a convenience for `--lane`, not a
# closed set the renderer relies on.
LANE_NAMES = [name for name, _ in ICB_LANES] + ['learning', *LOCAL_LANES]


def icb_adapter(result: sources.Result) -> list[LaneView]:
    """icb's overview model, as lanes. Unregistered the day icb emits them."""
    today = date.today()
    return [build({'icb': result}, today) for _, build in ICB_LANES]


def learning_adapter(result: sources.Result) -> list[LaneView]:
    """learning's overview model, as one lane."""
    return [build_learning_lane({'learning': result}, date.today())]


sources.register_adapter('icb', icb_adapter)
sources.register_adapter('learning', learning_adapter)


def read_local(read: Callable[[], object]) -> sources.Result:
    """One of doit's own lanes, without a round trip through a subprocess.

    A raising backend degrades its lane rather than the snapshot, the same way a
    failing subprocess does — the caller cannot tell the two apart.
    """
    try:
        return sources.Result(source='local', payload=read(), exit_code=0)
    except (OSError, ValueError) as error:
        return sources.Result(source='local', exit_code=1, stderr=str(error), failure=sources.Failure.FAILED)


def local_lanes() -> list[LaneView]:
    """doit's own lanes. Always built: they read doit's own state, and a
    dashboard that needs configuring before it can show your own register would
    be configuration for its own sake."""
    results = {name: read_local(read) for name, read in LOCAL_BACKENDS.items()}
    return [build_maintenance_lane(results, date.today())]


def collect(registry: sources.Registry, wanted: list[str] | None) -> list[LaneView]:
    """Every lane, in the order sources.yml declares its sources.

    Order is config's, not a constant here — that is what makes it yours to
    change. doit's own lanes come last because nothing in the file governs them.
    """
    configured = list(registry.sources.values())
    results = sources.fetch(configured)
    collected: list[LaneView] = []
    for source in configured:
        collected.extend(sources.lanes_from(source, results[source.id]))
    collected.extend(local_lanes())
    if wanted:
        collected = [lane for lane in collected if lane.name in wanted]
    return collected


def terminal_width() -> int:
    return min(shutil.get_terminal_size(fallback=(80, 24)).columns, MAX_WIDTH)


def clip(text: str, width: int) -> str:
    if width <= 0:
        return ''
    return text if len(text) <= width else text[: width - 1] + '…'


def cap_for(lane_name: str, row_cap: int) -> int:
    """A lane's own cap, unless you asked for one lane — then you want to see it,
    and the focused cap wins outright."""
    if row_cap == FOCUSED_ROW_CAP:
        return row_cap
    return LANE_ROW_CAPS.get(lane_name, row_cap)


def fitted(text: str, width: int, *, pad: bool = False) -> Text:
    """`text` as a Text no wider than `width`, ellipsised and optionally padded.

    A column's width is arithmetic the layout owns, so this truncates to a
    computed width rather than to the terminal's — `console.print` still clips
    the assembled line as a backstop.
    """
    fitted_text = Text(text)
    fitted_text.truncate(width, overflow='ellipsis', pad=pad)
    return fitted_text


def render_lanes(lanes: list[LaneView], today: date, row_cap: int) -> None:
    width = terminal_width()
    console.rule(f'[cyan]Dashboard[/] · {today.strftime("%a %d %b")}', align='left')

    for lane in (lane for lane in lanes if lane.available):
        render_lane(lane, width, cap_for(lane.name, row_cap))

    degraded = [lane for lane in lanes if not lane.available]
    if degraded:
        console.print()
        for lane in degraded:
            line = Text('  ')
            line.append(lane.name.ljust(12), style='yellow')
            line.append(f'unavailable — {lane.reason}')
            console.print(line)


def render_lane(lane: LaneView, width: int, row_cap: int) -> None:
    console.print()
    heading = Text()
    heading.append(lane.title, style='cyan')
    if lane.meta:
        heading.append(f'  {lane.meta}')
    console.print(heading)

    if lane.grid:
        render_grid(lane.grid, width)
    elif lane.rows:
        render_rows(lane.rows[:row_cap], width)
    else:
        console.print('  —')

    render_trailer(lane)
    if lane.reason:
        console.print(Text(f'  partial — {lane.reason}', style='yellow'))


def column_width(width: int, values: list[str], share: float, minimum: int) -> int:
    """How wide a trailing column gets: what it needs, bounded by its share.

    Sized to the values actually on screen first, so a lane whose rows carry no
    command reserves nothing for one and a lane of short notes stops stealing
    width from its titles.
    """
    needed = max((len(value) for value in values), default=0)
    return min(needed, max(minimum, int(width * share)))


def handle_text(row: Row) -> str:
    return f'{HANDLE_MARK} {row.handle}' if row.handle else ''


def render_rows(rows: list[Row], width: int) -> None:
    handle_width = column_width(width, [handle_text(row) for row in rows], HANDLE_WIDTH_SHARE, HANDLE_WIDTH_MIN)
    note_width = column_width(width, [row.note for row in rows], NOTE_WIDTH_SHARE, NOTE_WIDTH_MIN)
    reserved = sum(size + 1 for size in (handle_width, note_width) if size)
    text_width = max(TEXT_WIDTH_MIN, width - 3 - LABEL_WIDTH - reserved)
    for row in rows:
        line = Text('  ')
        line.append(row.label.ljust(LABEL_WIDTH), style='yellow')
        line.append(' ')
        line.append(fitted(row.text, text_width, pad=True))
        # Note before handle, so a row reads what it is, how urgent it is, then
        # what to type — the order `nudge_row` and `doit review due` already use.
        # Between the two, the command split every title from its own qualifier.
        if note_width:
            line.append(' ')
            # Styled on the Text itself: rich refuses a style argument when what
            # is being appended is already a Text, and only a due or overdue note
            # carries one — so this raised for exactly the rows that matter most.
            note = fitted(row.note, note_width, pad=True)
            style = NOTE_STYLES[row.urgency]
            if style:
                note.stylize(style)
            line.append(note)
        if handle_width:
            line.append(' ')
            handle = fitted(handle_text(row), handle_width)
            handle.stylize('cyan')
            line.append(handle)
        # Padded up to the last column that has anything in it, then trimmed, so
        # a line ends where its content does rather than in trailing spaces.
        line.rstrip()
        console.print(line, no_wrap=True, overflow='ellipsis')


def render_grid(cells: list[GridCell], width: int) -> None:
    available = width - 2
    columns = GRID_COLUMNS if available >= GRID_COLUMNS * GRID_MIN_COLUMN_WIDTH else 1
    column_width = (available - (columns - 1) * GRID_GUTTER) // columns
    handle_width = max((len(cell.handle) for cell in cells), default=0)
    text_width = column_width - 2 - (handle_width + 1 if handle_width else 0)
    # Column-major, so the set still reads top-to-bottom in its sorted order.
    per_column = math.ceil(len(cells) / columns)
    for offset in range(per_column):
        line = Text('  ')
        for column in range(columns):
            index = column * per_column + offset
            if index >= len(cells):
                continue
            if column:
                line.append(' ' * GRID_GUTTER)
            cell = cells[index]
            line.append('✓' if cell.done else '○', style='green' if cell.done else 'yellow')
            line.append(' ')
            if handle_width:
                line.append(cell.handle.rjust(handle_width), style='yellow')
                line.append(' ')
            line.append(fitted(cell.text, text_width, pad=True))
        line.rstrip()
        console.print(line, no_wrap=True, overflow='ellipsis')


def render_trailer(lane: LaneView) -> None:
    """Where to go for the rest, and nothing else.

    A remainder count told you a lane was truncated without telling you anything
    you could act on — the heading already states the lane's size, and the hint
    is what you would run to see the rest.
    """
    if not lane.hints:
        return
    line = Text('  ')
    for index, hint in enumerate(lane.hints):
        if index:
            line.append('   ')
        line.append(f'→ {hint}', style='blue')
    console.print(line)


def cmd_dashboard(lane: list[str] | None, as_json: bool) -> int:
    registry = sources.load()
    if registry.problems:
        for problem in registry.problems:
            console.print(Text(f'sources.yml: {problem}', style='yellow'))
    if not registry.sources:
        console.print(Text(f'No sources configured in {sources.SOURCES}.'))
        console.print('Write one with [cyan]doit sources example[/], then edit it.')

    offered = collect(registry, None)
    collected = [found for found in offered if found.name in lane] if lane else offered
    # `--lane` is not checked against a closed set: a conforming source
    # contributes lanes doit has never heard of, so the only honest check is
    # whether the name matched anything that actually came back.
    unmatched = [name for name in lane or [] if name not in {found.name for found in offered}]
    if unmatched:
        console.print(Text(f'No lane named {", ".join(unmatched)}.', style='yellow'))
        console.print(f'  Configured sources offered: {", ".join(found.name for found in offered) or "nothing"}')

    # Asking for one lane means you want to see it, not glance at it.
    row_cap = FOCUSED_ROW_CAP if lane else ROW_CAP

    if as_json:
        # Plain print, never the rich console: a Console soft-wraps at terminal
        # width, which would put newlines inside JSON strings.
        print(lanemodel.dumps(collected, datetime.now()))
    else:
        render_lanes(collected, date.today(), row_cap)
    # A read-only glance always succeeds: degradation is shown per lane, and a
    # consumer of --json checks lanes[].status rather than the exit code.
    return 0


def dashboard_command(
    lane: Annotated[
        list[str] | None,
        typer.Option('--lane', help=f'Show only this lane (repeatable): {", ".join(LANE_NAMES)}'),
    ] = None,
    as_json: Annotated[bool, typer.Option('--json', help='Output the lane model as JSON to stdout.')] = False,
) -> None:
    """Every lane — what is outstanding across everything."""
    raise typer.Exit(cmd_dashboard(lane, as_json))
