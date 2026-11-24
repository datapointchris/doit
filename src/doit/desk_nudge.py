"""The desk nudge's rules: the clock that counts toward the interval, the answers that tune it, and the records it writes.

The service keeps every answer and computes the interval from all of them on each
read, so a changed rule reprices the same answers. The desk keeps the clock in a
state file. Each function here takes the state or the data it decides on and
changes or returns it. Nothing here asks the service or starts a program, and
`write_schemas` is the one function that writes a file.
"""

import datetime as dt
import json
import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field
from enum import StrEnum
from pathlib import Path
from typing import Any

from doit.lanes import PURSUITS
from doit.lanes import CompletionKind
from doit.pursuits import choice_lines
from doit.pursuits import is_timed
from doit.pursuits import settled_choice
from doit.pursuits import writes_through
from doit.today import CHECKLIST_GROUPS
from doit.today import due_row_cap

STARTING_INTERVAL_MINUTES = 30

# Each too soon multiplies the interval by this, and each too late divides it.
ANSWER_STEP = 1.1

TICK_SECONDS = 15
# The most time one tick counts. A longer gap between ticks, such as a suspend or
# a stopped watch, goes uncounted.
TICK_CAP_SECONDS = 2 * TICK_SECONDS
READY_SECONDS = 10 * 60
# The pill reads `nudge stopped` once this passes with no tick, so a watch that
# died is told apart from a clock that holds.
STALE_AFTER_SECONDS = 4 * TICK_SECONDS
# How many ended nudges the state remembers, so a late answer can name its firing.
REMEMBERED_NUDGES = 20

STATE_VERSION = 2
RECORD_SCHEMA_VERSION = 3
STATUS_SCHEMA_VERSION = 2
PANEL_SCHEMA_VERSION = 2
PANEL_CACHE_VERSION = 2
RECORD_SCHEMA_FILE = 'desk-nudge-record.schema.json'
STATUS_SCHEMA_FILE = 'desk-nudge-status.schema.json'
PANEL_SCHEMA_FILE = 'desk-nudge-panel.schema.json'


class Answer(StrEnum):
    """What a person says of one nudge. Timing and bother are separate axes, so one nudge can take two.

    `good_timing` and `bothered` move no interval. They are stored with the
    others, for a rule that weighs when to interrupt.
    """

    GOOD_TIMING = 'good_timing'
    TOO_SOON = 'too_soon'
    TOO_LATE = 'too_late'
    BOTHERED = 'bothered'


ANSWER_LABELS = {
    Answer.GOOD_TIMING: 'good timing',
    Answer.TOO_SOON: 'too soon',
    Answer.TOO_LATE: 'too late',
    Answer.BOTHERED: 'bothered me',
}


class Event(StrEnum):
    """Each moment the nudge records, one `doit.desk_nudge` line each."""

    STARTED = 'started'
    FIRED = 'fired'
    ANSWERED = 'answered'
    EXPIRED = 'expired'
    PICKED = 'picked'
    GAP = 'gap'
    PAUSED = 'paused'
    RESUMED = 'resumed'


class Phase(StrEnum):
    """Where the clock stands. Only `counting` adds time."""

    COUNTING = 'counting'
    FIRING = 'firing'
    READY = 'ready'
    PAUSED = 'paused'
    NO_DESK = 'no_desk'

    @property
    def holds_nudge(self) -> bool:
        """Whether a nudge is in hand, which `begin_firing` starts and `end_ready` ends."""
        return self in (Phase.FIRING, Phase.READY)


class SuggestionError(StrEnum):
    """Why a firing has no suggestion, each with its own message."""

    UNREACHABLE = 'unreachable'
    LOGIN = 'login'
    NOTHING_TO_DRAW = 'nothing_to_draw'
    UNREADABLE = 'unreadable'


SUGGESTION_MESSAGES = {
    SuggestionError.UNREACHABLE: 'The doit service could not be reached, so take a break.',
    SuggestionError.LOGIN: "doit refused this desk's login. Run `doit auth login`, and take a break.",
    SuggestionError.NOTHING_TO_DRAW: 'Nothing is due, so take a break.',
    SuggestionError.UNREADABLE: "The service's draw could not be read, so take a break.",
}

PILL_TEXT = {Phase.PAUSED: 'paused', Phase.NO_DESK: 'no desk'}


class NothingToAnswer(Exception):
    """No nudge to answer: none is ready, or the id names none that this desk fired lately."""


def answer_counts(answers: Iterable[str]) -> dict[str, int]:
    """How many of each answer were given, every answer named, zero included."""
    counted = Counter(answers)
    return {answer.value: counted[answer] for answer in Answer}


def interval_minutes(answers: Iterable[str]) -> float:
    """`STARTING_INTERVAL_MINUTES`, times `ANSWER_STEP` for each too soon and divided by it for each too late, to the hundredth of a minute.

    It has no floor or ceiling. The order of the answers does not matter, so a too
    soon and a too late cancel.
    """
    counted = Counter(answers)
    return round(STARTING_INTERVAL_MINUTES * ANSWER_STEP ** (counted[Answer.TOO_SOON] - counted[Answer.TOO_LATE]), 2)


# --- time ---------------------------------------------------------------------


def stamp(moment: dt.datetime) -> str:
    """An instant as the state file holds it: ISO 8601 with its offset."""
    return moment.isoformat(timespec='milliseconds')


def instant(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text)


def maybe_instant(text: str | None) -> dt.datetime | None:
    return instant(text) if text else None


def record_stamp(moment: dt.datetime) -> str:
    """An instant as a record holds it, in UTC to the millisecond."""
    return moment.astimezone(dt.UTC).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


# --- the state ----------------------------------------------------------------


def fresh_state() -> dict[str, Any]:
    """The state of a desk that has never run the watch: counting from zero at the starting interval."""
    state: dict[str, Any] = {
        'version': STATE_VERSION,
        'phase': Phase.COUNTING.value,
        'counted_seconds': 0.0,
        'last_tick': None,
        'interval_minutes': float(STARTING_INTERVAL_MINUTES),
        'answer_counts': answer_counts([]),
        'nudge': None,
        'queued_answers': [],
        'recent_nudges': [],
        # Each panel key picked, with when its completion ended, and whether a pick asks the watch to read the rows again.
        'panel': {'picked': [], 'rebuild_wanted': False},
        'pill': {'text': '', 'class': Phase.COUNTING.value, 'stale_after': None},
    }
    state['pill'].update(pill_face(state))
    return state


class UnreadableState(ValueError):
    """A state file that is not this release's state: not JSON, not an object, or another `version`."""


def loaded_state(document: object) -> dict[str, Any]:
    """A state file's document over a fresh state, so a field it lacks reads as a fresh desk's. Raises UnreadableState."""
    if not isinstance(document, dict) or document.get('version') != STATE_VERSION:
        raise UnreadableState(f'it holds no version {STATE_VERSION} state')
    return {**fresh_state(), **document}


def in_hand(state: dict) -> dict | None:
    """The nudge that is firing or ready, or None where the phase holds none."""
    return state['nudge'] if Phase(state['phase']).holds_nudge else None


@dataclass
class Change:
    """What one change to the state asks of the effects around it."""

    records: list[dict] = field(default_factory=list)
    # The nudge that this change ended.
    closes: dict | None = None
    # Present time reached the interval, so a firing begins.
    fire: bool = False


def end_ready(state: dict) -> dict:
    """End the nudge in hand, restart the clock at zero, and return the nudge."""
    nudge = state['nudge']
    state['nudge'] = None
    state['counted_seconds'] = 0.0
    state['phase'] = Phase.COUNTING.value
    remembered = {'nudge_id': nudge['nudge_id'], 'fired_at': nudge['fired_at']}
    state['recent_nudges'] = [*state['recent_nudges'], remembered][-REMEMBERED_NUDGES:]
    return nudge


def tick(state: dict, now: dt.datetime, live_desk: bool) -> Change:
    """One tick of the watch: time counted, a ready nudge expired, the desk checked, and a firing called.

    A tick adds the seconds since the last one, capped at `TICK_CAP_SECONDS`, and a
    longer gap writes `gap`. The clock holds while no desk session exists. Nothing
    but a nudge ending restarts it, so leaving the desk neither pauses nor resets it.
    """
    change = Change()
    last = maybe_instant(state['last_tick'])
    gap = max((now - last).total_seconds(), 0.0) if last else 0.0
    state['last_tick'] = stamp(now)
    state['pill']['stale_after'] = stamp(now + dt.timedelta(seconds=STALE_AFTER_SECONDS))
    if gap > TICK_CAP_SECONDS:
        change.records.append(record(Event.GAP, now, gap_seconds=round(gap, 3)))

    phase = Phase(state['phase'])
    if phase is Phase.READY and now >= instant(state['nudge']['ready_until']):
        change.closes = end_ready(state)
        change.records.append(record(Event.EXPIRED, now, nudge_id=change.closes['nudge_id']))
        phase = Phase.COUNTING
    if phase is Phase.PAUSED or phase.holds_nudge:
        return change
    if not live_desk:
        state['phase'] = Phase.NO_DESK.value
        return change
    if phase is Phase.NO_DESK:
        state['phase'] = Phase.COUNTING.value
        return change
    if not change.closes:
        state['counted_seconds'] += min(gap, TICK_CAP_SECONDS)
    change.fire = state['counted_seconds'] >= state['interval_minutes'] * 60
    return change


def begin_firing(state: dict, now: dt.datetime, nudge_id: str) -> dict:
    """Step one of a firing: the nudge minted and the phase `firing`, so a restarted watch finishes it rather than firing again."""
    state['phase'] = Phase.FIRING.value
    state['nudge'] = {
        'nudge_id': nudge_id,
        'fired_at': stamp(now),
        'counted_minutes': round(state['counted_seconds'] / 60, 2),
        'interval_minutes': state['interval_minutes'],
        'suggestion': None,
        'ready_until': None,
    }
    return dict(state['nudge'])


def make_ready(state: dict, now: dt.datetime, nudge_id: str, suggestion: dict) -> dict | None:
    """The nudge ready for `READY_SECONDS`, where it is still the one firing. Returns it, or None where it ended meanwhile."""
    nudge = state['nudge']
    if state['phase'] != Phase.FIRING or not nudge or nudge['nudge_id'] != nudge_id:
        return None
    nudge['suggestion'] = suggestion
    nudge['ready_until'] = stamp(now + dt.timedelta(seconds=READY_SECONDS))
    state['phase'] = Phase.READY.value
    return dict(nudge)


def pause(state: dict, now: dt.datetime) -> Change:
    """Hold the clock where it stands, ending a nudge in hand. A clock already paused is left alone."""
    if state['phase'] == Phase.PAUSED:
        return Change()
    change = Change()
    if in_hand(state):
        change.closes = end_ready(state)
    state['phase'] = Phase.PAUSED.value
    change.records.append(record(Event.PAUSED, now, nudge_id=change.closes['nudge_id'] if change.closes else None))
    return change


def resume(state: dict, now: dt.datetime) -> Change:
    """Carry on the count from where `pause` held it."""
    if state['phase'] != Phase.PAUSED:
        return Change()
    state['phase'] = Phase.COUNTING.value
    return Change(records=[record(Event.RESUMED, now)])


@dataclass(frozen=True)
class Taken:
    """An answer taken on the desk: the row the service stores, whether its nudge had already ended, and the nudge it ends."""

    row: dict
    late: bool
    closes: dict | None


def take_answer(state: dict, now: dt.datetime, answer: Answer, nudge_id: str | None, machine: str) -> Taken:
    """Take an answer to the nudge in hand, or to a nudge that ended lately, which is late and leaves the clock alone.

    With no id it answers the nudge in hand. Raises NothingToAnswer where there is none.
    """
    nudge = in_hand(state)
    closes = None
    if nudge and nudge_id in (None, nudge['nudge_id']):
        answered_id, fired_at, late = nudge['nudge_id'], nudge['fired_at'], False
        closes = end_ready(state)
    elif nudge_id is None:
        raise NothingToAnswer('no nudge is ready, so name one with --nudge')
    else:
        remembered = {entry['nudge_id']: entry['fired_at'] for entry in state['recent_nudges']}
        if nudge_id not in remembered:
            raise NothingToAnswer(f'this desk fired no nudge {nudge_id} lately')
        answered_id, fired_at, late = nudge_id, remembered[nudge_id], True
    row = {
        'nudge_id': answered_id,
        'answer': answer.value,
        'fired_at': fired_at,
        'answered_at': stamp(now),
        'machine': machine,
    }
    return Taken(row, late, closes)


def took_interval(state: dict, answered: dict) -> None:
    """The interval and the counts the service answered, which the clock fires on from now. A field that the reply lacks keeps its value."""
    state['interval_minutes'] = float(answered.get('interval_minutes', state['interval_minutes']))
    state['answer_counts'] = dict(answered.get('answer_counts', state['answer_counts']))


def answer_key(row: dict) -> tuple[str, str]:
    """What the service stores an answer under, once."""
    return row['nudge_id'], row['answer']


def queued_entry(state: dict, row: dict) -> dict | None:
    """The queued answer that the service would store in place of `row`, which is the first one kept under its key."""
    return next((queued for queued in state['queued_answers'] if answer_key(queued['row']) == answer_key(row)), None)


def queue_answer(state: dict, taken: Taken) -> None:
    """Keep an answer that the service did not store, so each tick sends it again."""
    if queued_entry(state, taken.row) is None:
        state['queued_answers'] = [*state['queued_answers'], {'row': taken.row, 'late': taken.late}]


def unqueue_answer(state: dict, row: dict) -> bool:
    """Drop a queued answer once stored or refused. False where another writer already dropped it, so only one writes its line."""
    kept = [queued for queued in state['queued_answers'] if answer_key(queued['row']) != answer_key(row)]
    dropped = len(kept) != len(state['queued_answers'])
    state['queued_answers'] = kept
    return dropped


def answered_record(now: dt.datetime, row: dict, late: bool, saved: bool, queued: bool, snapshot: dict | None = None) -> dict:
    """The `answered` line. A resend that stores or drops a queued answer carries no snapshot, because its moment has passed."""
    since_fired = (instant(row['answered_at']) - instant(row['fired_at'])).total_seconds()
    return record(
        Event.ANSWERED,
        now,
        nudge_id=row['nudge_id'],
        answer=row['answer'],
        seconds_since_fired=round(since_fired, 3),
        saved=saved,
        queued=queued,
        late=late,
        **(snapshot or {}),
    )


# --- what the firing reads and shows -------------------------------------------


def suggestion_from(selection: dict) -> dict:
    """The first pursuit `/next` offers, with the item it stands for where `settled_choice` settles one.

    The label is what `doit next` shows for it: each resolved choice on its own
    line, else the pursuit's description.
    """
    offered = selection.get('offered') or []
    if not offered:
        return failed_suggestion(SuggestionError.NOTHING_TO_DRAW, 'the draw offered nothing')
    name = offered[0]
    entry = (selection.get('resolved') or {}).get(name) or {}
    choices = [] if entry.get('error') else entry.get('choices') or []
    settled = settled_choice(choices)
    description = ((selection.get('pursuits') or {}).get(name) or {}).get('description') or ''
    item_id = settled.get('id') if settled else None
    return {'pursuit': name, 'item_id': item_id, 'label': '\n'.join(choice_lines(choices)) or description}


def failed_suggestion(kind: SuggestionError, detail: str) -> dict:
    return {'error': kind.value, 'message': SUGGESTION_MESSAGES[kind], 'detail': detail}


def topic(suggestion: dict | None) -> str:
    """The pill's word for a ready nudge: its pursuit, or `break` where none was drawn."""
    return suggestion['pursuit'] if suggestion and 'pursuit' in suggestion else 'break'


def chosen_instance(instances: object, wayland_display: str | None) -> str | None:
    """The Hyprland instance that `hyprctl instances -j` lists on this desk's display, else the newest one."""
    rows = [row for row in instances if isinstance(row, dict) and row.get('instance')] if isinstance(instances, list) else []
    on_display = [row for row in rows if wayland_display and row.get('wl_socket') == wayland_display]
    pool = on_display or rows
    return str(max(pool, key=lambda row: row.get('time') or 0)['instance']) if pool else None


def focused_window_from(window: object) -> dict:
    """`hyprctl activewindow -j` cut to what the record keeps. With nothing focused, hyprctl prints `{}`."""
    if not isinstance(window, dict):
        return {'error': 'hyprctl activewindow -j printed no object'}
    return {'class': window.get('class') or None, 'title': window.get('title') or None}


# --- the pill and the status -----------------------------------------------------


def minutes_left(state: dict) -> int:
    return max(math.ceil((state['interval_minutes'] * 60 - state['counted_seconds']) / 60), 0)


def pill_face(state: dict) -> dict[str, str]:
    """The pill's text and class. The watch's tick sets `stale_after` beside them."""
    phase = Phase(state['phase'])
    if phase is Phase.READY:
        text = f'ready · {topic(state["nudge"]["suggestion"])}'
    elif phase in (Phase.COUNTING, Phase.FIRING):
        text = f'⏲ {minutes_left(state)}m'
    else:
        text = PILL_TEXT[phase]
    return {'text': text, 'class': phase.value}


def status_document(state: dict, now: dt.datetime) -> dict:
    """`doit desk-nudge status --json`, which `schemas/desk-nudge-status.schema.json` describes."""
    nudge = in_hand(state) or {}
    stale_after = maybe_instant(state['pill']['stale_after'])
    return {
        'schema_version': STATUS_SCHEMA_VERSION,
        'phase': state['phase'],
        'watch_running': stale_after is not None and now < stale_after,
        'counted_minutes': round(state['counted_seconds'] / 60, 2),
        'interval_minutes': state['interval_minutes'],
        'minutes_left': None if nudge else minutes_left(state),
        'nudge_id': nudge.get('nudge_id'),
        'fired_at': nudge.get('fired_at'),
        'ready_until': nudge.get('ready_until'),
        'suggestion': nudge.get('suggestion'),
        'answer_counts': state['answer_counts'],
        'queued_answer_count': len(state['queued_answers']),
        'pill': state['pill'],
    }


# --- the panel ---------------------------------------------------------------------
#
# The panel is rows that doit computes and `doit tui` draws. Its cache holds each row's
# identity, a kind and a ref, and never a command, so a file that anything else
# can write never chooses what a pick runs. A key names what a row completes, so
# one completion keeps one key wherever it is listed.

MINUTES_STEP = 5
# What a timed pursuit's minutes step offers where no entry ever kept minutes. None of them is labeled usual.
MINUTES_WITHOUT_USUAL = (15, 30, 45, 60)
# Where that step's ±5 rows count from.
MINUTES_ADJUSTED_FROM = 30
NOTHING_LEFT = 'nothing left today'
# The due lanes whose rows something completes, each named where it could not be read.
COMPLETED_LANES = (PURSUITS, *CHECKLIST_GROUPS)
KEY_SEPARATOR = ':'


class PanelStep(StrEnum):
    """Which step of the panel that a document holds: today's rows, a timed pursuit's minutes, or what a pick did."""

    TODAY = 'today'
    MINUTES = 'minutes'
    DONE = 'done'


class PanelRowKind(StrEnum):
    """A row that completes nothing in an owner: a note, an answer, or a step through a timed pursuit's minutes."""

    NOTE = 'note'
    ANSWER = 'answer'
    LOG_MINUTES = 'log_minutes'
    ADJUST_MINUTES = 'adjust_minutes'


PANEL_ROW_KINDS = (PanelRowKind.NOTE, PanelRowKind.ANSWER, *CompletionKind, PanelRowKind.LOG_MINUTES, PanelRowKind.ADJUST_MINUTES)

# A menu draws these as they are, so they carry no markup.
PANEL_BANNERS = {SuggestionError.LOGIN: 'run doit auth login'}
UNREACHABLE_BANNER = 'doit unreachable'


def completion_key(kind: str, ref: str, item_id: str | None = None) -> str:
    """What a row completes, as its key: `pursuit:tasks:532`, `habit:17`, `review:prune-docker`."""
    return KEY_SEPARATOR.join([kind, ref, *([item_id] if item_id else [])])


def panel_row(
    key: str | None,
    kind: str,
    text: str,
    ref: str | None = None,
    item_id: str | None = None,
    *,
    group: str | None = None,
    minutes: int | None = None,
    suggested: bool = False,
) -> dict:
    return {
        'key': key,
        'kind': str(kind),
        'text': text,
        'ref': ref,
        'item_id': item_id,
        'group': group,
        'minutes': minutes,
        'suggested': suggested,
    }


def completion_row(kind: str, ref: str, text: str, group: str, item_id: str | None = None) -> dict:
    """A row that completes something, listed under `group`, the due lane it came from."""
    return panel_row(completion_key(kind, ref, item_id), kind, text, ref, item_id, group=group)


def note_row(text: str) -> dict:
    """A row that says something and completes nothing, so it has no key."""
    return panel_row(None, PanelRowKind.NOTE, text)


def completable(today: dict) -> list[tuple[dict, dict]]:
    """Each due lane's rows that something completes, in the lane's own order, beside the lane.

    A backlog is capped as `doit today` caps it, so review entries do not bury
    the day's pursuits and habits, and a routine group is listed whole.
    """
    found = []
    for lane in today.get('due') or []:
        if lane.get('status') == 'unavailable':
            continue
        rows = [row for row in lane.get('rows') or [] if isinstance(row.get('completes'), dict)]
        found += [(lane, row) for row in rows[: due_row_cap(str(lane.get('name')), whole_routine=True)]]
    return found


def choice_pursuits(today: dict, register: dict) -> list[str]:
    """The due pursuits whose choices `/pursuits/{name}/item` is read for, because their log completes the one it names."""
    return [
        row['completes']['ref']
        for _, row in completable(today)
        if row['completes'].get('kind') == CompletionKind.PURSUIT and writes_through(register.get(row['completes']['ref']) or {})
    ]


def cached_rows(lane: dict, row: dict, register: dict, items: dict[str, dict]) -> list[dict]:
    """One of today's rows as the panel lists it, under its lane's name, which says what kind of row it is.

    A pursuit that writes through lists each choice that carries an id. Where
    its choices could not be read, or it has none, it is a note, because a log
    naming no choice completes nothing in the app that owns them.
    """
    kind, ref, group = str(row['completes'].get('kind')), str(row['completes'].get('ref')), str(lane.get('name'))
    if kind != CompletionKind.PURSUIT:
        return [completion_row(kind, ref, row.get('label') or ref, group)]
    if not writes_through(register.get(ref) or {}):
        return [completion_row(kind, ref, ref, group)]
    item = items.get(ref) or {}
    if item.get('error'):
        return [note_row(f'{ref} unavailable — {item["error"]}')]
    choices = [choice for choice in item.get('choices') or [] if choice.get('id')]
    if not choices:
        return [note_row(f'{ref} has no choice to complete')]
    return [completion_row(kind, ref, f'{ref} · {choice.get("label") or choice["id"]}', group, str(choice['id'])) for choice in choices]


def pursuit_facts(register: dict, usual: dict) -> dict[str, dict]:
    """What a pick needs to know of each pursuit, as `doit.pursuits` decides it when the rows are read."""
    return {
        name: {'timed': is_timed(config), 'writes_through': writes_through(config), 'usual_minutes': usual.get(name)}
        for name, config in register.items()
    }


def still_owed(suggestion: dict | None, today: dict) -> dict | None:
    """A suggestion carried into newer rows, kept only where today's pursuits lane still lists its pursuit.

    A pursuit logged at a terminal or on another desk leaves the lane, and a
    suggestion that outlived it would log it twice.
    """
    if not suggestion or 'pursuit' not in suggestion:
        return suggestion
    listed = {
        row['completes'].get('ref')
        for lane in today.get('due') or []
        for row in lane.get('rows') or []
        if isinstance(row.get('completes'), dict) and row['completes'].get('kind') == CompletionKind.PURSUIT
    }
    return suggestion if suggestion['pursuit'] in listed else None


def panel_cache(today: dict, listed: dict, items: dict[str, dict], suggestion: dict | None, read_at: dt.datetime) -> dict:
    """The panel's rows as identities, from `/today`, `/pursuits` and each write-through pursuit's `/pursuits/{name}/item`.

    `upcoming` is left out, because nothing completes a trip or a countdown. A
    lane that something completes and that could not be read is named in a note,
    because a lane dropped without a word reads as nothing owed. `read_at` is
    when the read began, so a pick made while it ran outlives it.
    """
    register = listed.get('pursuits') or {}
    rows: list[dict] = []
    for lane in today.get('due') or []:
        if lane.get('status') == 'unavailable' and lane.get('name') in COMPLETED_LANES:
            rows.append(note_row(f'{lane["name"]} unavailable — {lane.get("reason") or "no reason given"}'))
    for lane, row in completable(today):
        rows += cached_rows(lane, row, register, items)
    return {
        'version': PANEL_CACHE_VERSION,
        'read_at': stamp(read_at),
        'failed': None,
        'suggestion': suggestion,
        'pursuits': pursuit_facts(register, listed.get('usual_minutes') or {}),
        'rows': rows,
    }


def failed_panel_cache(previous: dict | None, kind: SuggestionError, detail: str, suggestion: dict | None, now: dt.datetime) -> dict:
    """The rows last read, kept, with why they could not be read again."""
    kept = previous or {'version': PANEL_CACHE_VERSION, 'read_at': None, 'pursuits': {}, 'rows': []}
    return {**kept, 'suggestion': suggestion, 'failed': {'error': kind.value, 'detail': detail, 'at': stamp(now)}}


def panel_banner(cache: dict | None) -> str | None:
    """Why the rows are old or missing: `rows from 14:05 — doit unreachable`. None where the last read succeeded."""
    if cache is None:
        return 'no rows yet — the desk nudge watch reads them'
    failed = cache.get('failed')
    if not failed:
        return None
    since = f'rows from {instant(cache["read_at"]):%H:%M}' if cache.get('read_at') else 'no rows yet'
    return f'{since} — {PANEL_BANNERS.get(SuggestionError(failed["error"]), UNREACHABLE_BANNER)}'


def suggestion_rows(suggestion: dict, cache: dict) -> list[dict]:
    """The suggestion as rows, keyed as the lanes key the same completion.

    A pursuit that writes through is listed by the choice it settled, else by
    each of its choices that the rows list, and by none where they list none.
    Any other pursuit is one row under its own key, because its log names no
    choice. A pursuit that the rows do not know is not listed.
    """
    name, item_id = suggestion['pursuit'], suggestion.get('item_id')
    facts = (cache.get('pursuits') or {}).get(name)
    if facts is None:
        return []
    if facts['writes_through']:
        rows = cache.get('rows') or []
        choices = [row for row in rows if row['kind'] == CompletionKind.PURSUIT and row['ref'] == name and row['item_id']]
        return [row for row in choices if row['item_id'] == item_id] or choices
    label = ' / '.join(line for line in (suggestion.get('label') or '').splitlines() if line)
    return [completion_row(CompletionKind.PURSUIT, name, f'{name} · {label}' if label else name, PURSUITS)]


def read_since(cache: dict | None, moment: str) -> bool:
    """Whether the rows were read at or after `moment`."""
    read_at = maybe_instant((cache or {}).get('read_at'))
    return read_at is not None and read_at >= instant(moment)


def once_each(rows: list[dict]) -> list[dict]:
    """Each key listed once, where it first appears, because one completion is one row."""
    seen: set[str] = set()
    kept = []
    for row in rows:
        if row['key'] is None or row['key'] not in seen:
            kept.append(row)
            seen.add(row['key'])
    return kept


def answer_row_key(answer: Answer, nudge_id: str) -> str:
    """An answer row's key names its nudge, so a pick after the nudge ended answers it late."""
    return KEY_SEPARATOR.join([PanelRowKind.ANSWER, answer.value, nudge_id])


def panel_today(cache: dict | None, state: dict) -> list[dict]:
    """The first step: why the rows are old, the suggestion, the answers while a nudge is ready, then every row that something completes.

    A ready nudge's suggestion is the one it showed, until the watch reads the
    rows after it fired. Otherwise the suggestion is the rows', which the watch
    read from `/next` at a firing or after a pick, and drops once its pursuit
    leaves today's lane. A row that the suggestion already lists is not listed
    again in its lane.
    """
    rows = []
    banner = panel_banner(cache)
    if banner:
        rows.append(note_row(banner))
    listed = (cache or {}).get('rows') or []
    ready = state['nudge'] if state['phase'] == Phase.READY else None
    suggestion = ready['suggestion'] if ready and not read_since(cache, ready['fired_at']) else (cache or {}).get('suggestion')
    if cache is not None and suggestion and 'pursuit' in suggestion:
        rows += [{**row, 'suggested': True} for row in suggestion_rows(suggestion, cache)]
    if ready:
        rows += [
            panel_row(answer_row_key(answer, ready['nudge_id']), PanelRowKind.ANSWER, ANSWER_LABELS[answer], answer.value)
            for answer in Answer
        ]
    rows += listed
    if cache is not None and cache.get('read_at') and not any(row['kind'] in set(CompletionKind) for row in listed):
        rows.append(note_row(NOTHING_LEFT))
    return once_each(rows)


def held_picks(state: dict, cache: dict | None) -> list[dict]:
    """The picks that these rows may still list: each one whose completion ended after the rows were read, or has not ended."""
    read_at = maybe_instant((cache or {}).get('read_at'))
    return [pick for pick in state['panel']['picked'] if read_at is None or instant(pick['at']) >= read_at]


def picked_keys(state: dict, cache: dict | None) -> set[str]:
    return {pick['key'] for pick in held_picks(state, cache)}


def unpicked(rows: list[dict], state: dict, cache: dict | None) -> list[dict]:
    picked = picked_keys(state, cache)
    return [row for row in rows if row['key'] not in picked]


def take_pick(state: dict, cache: dict | None, key: str, now: dt.datetime) -> bool:
    """Hold a key as picked. False where it is held already, so one completion runs once.

    A pick whose completion ended before the rows were read is let go, because
    those rows no longer list what it completed.
    """
    held = held_picks(state, cache)
    state['panel']['picked'] = held
    if any(pick['key'] == key for pick in held):
        return False
    state['panel']['picked'] = [*held, {'key': key, 'at': stamp(now)}]
    return True


def settle_pick(state: dict, key: str, now: dt.datetime, *, recorded_nothing: bool) -> None:
    """Hold a pick until a read that begins after its completion ended, or let it go where the owner recorded nothing."""
    others = [pick for pick in state['panel']['picked'] if pick['key'] != key]
    state['panel']['picked'] = others if recorded_nothing else [*others, {'key': key, 'at': stamp(now)}]


def end_on_pick(state: dict) -> dict | None:
    """A pick ends the nudge in hand and restarts the clock, and leaves a clock with no nudge alone. Returns the nudge it ended."""
    return end_ready(state) if in_hand(state) else None


def want_panel_rebuild(state: dict) -> None:
    state['panel']['rebuild_wanted'] = True


def take_panel_rebuild(state: dict) -> bool:
    """Whether a pick asked for the rows to be read again, clearing the ask."""
    wanted = bool(state['panel'].get('rebuild_wanted'))
    state['panel']['rebuild_wanted'] = False
    return wanted


def minutes_key(kind: PanelRowKind, minutes: int, row_key: str) -> str:
    """A minutes row's key names the row it logs last, so that row's own key keeps its separators."""
    return KEY_SEPARATOR.join([kind, str(minutes), row_key])


@dataclass(frozen=True)
class AnswerKey:
    answer: Answer
    nudge_id: str | None


@dataclass(frozen=True)
class MinutesKey:
    kind: PanelRowKind
    minutes: int
    row_key: str


@dataclass(frozen=True)
class CompletionKey:
    key: str


class UnreadableKey(ValueError):
    """A key that no panel row carries the shape of."""


def parse_key(key: str) -> AnswerKey | MinutesKey | CompletionKey:
    """A key, read as `answer_row_key`, `minutes_key` and `completion_key` write it.

    A completion key stays whole, because a pick matches it against the rows
    rather than reading its parts.
    """
    kind, _, rest = key.partition(KEY_SEPARATOR)
    if kind == PanelRowKind.ANSWER:
        value, _, nudge_id = rest.partition(KEY_SEPARATOR)
        if value not in set(Answer):
            raise UnreadableKey(f'{value!r} is not an answer')
        return AnswerKey(Answer(value), nudge_id or None)
    if kind in (PanelRowKind.LOG_MINUTES, PanelRowKind.ADJUST_MINUTES):
        amount, _, row_key = rest.partition(KEY_SEPARATOR)
        if not re.fullmatch('[1-9][0-9]*', amount):
            raise UnreadableKey(f'{key} names no whole number of minutes')
        return MinutesKey(PanelRowKind(kind), int(amount), row_key)
    return CompletionKey(key)


def minutes_rows(row: dict, minutes: int | None, usual: float | None) -> list[dict]:
    """A timed pursuit's minutes step: log `minutes`, or reopen 5 above or below it.

    Opened with no amount, it starts on the usual amount. With none usual, it
    offers `MINUTES_WITHOUT_USUAL`, and its ±5 rows count from
    `MINUTES_ADJUSTED_FROM`. No amount is ever taken from time away.
    """
    name = row['ref']
    usual_whole = round(usual) if usual else None
    center = minutes or usual_whole
    amounts = [center] if center else list(MINUTES_WITHOUT_USUAL)
    around = center or MINUTES_ADJUSTED_FROM
    rows = [
        panel_row(
            minutes_key(PanelRowKind.LOG_MINUTES, amount, row['key']),
            PanelRowKind.LOG_MINUTES,
            f'{name} · log {amount} min' + (' (usual)' if amount == usual_whole else ''),
            minutes=amount,
            suggested=amount == around,
        )
        for amount in amounts
    ]
    for sign, amount in (('+', around + MINUTES_STEP), ('−', around - MINUTES_STEP)):
        if amount > 0:
            rows.append(
                panel_row(
                    minutes_key(PanelRowKind.ADJUST_MINUTES, amount, row['key']),
                    PanelRowKind.ADJUST_MINUTES,
                    f'{name} · {sign}{MINUTES_STEP} → {amount} min',
                    minutes=amount,
                )
            )
    return rows


def panel_step(step: PanelStep, rows: list[dict], message: str | None = None) -> dict:
    """One step as `panel show --json` and `panel pick --json` print it, which `schemas/desk-nudge-panel.schema.json` describes."""
    return {
        'schema_version': PANEL_SCHEMA_VERSION,
        'step': step.value,
        'rows': [
            {
                'key': row['key'],
                'kind': row['kind'],
                'text': row['text'],
                'group': row.get('group'),
                'minutes': row.get('minutes'),
                'suggested': bool(row.get('suggested')),
            }
            for row in rows
        ],
        'message': message,
    }


def picked_record(now: dt.datetime, nudge_id: str | None, row: dict, minutes: int | None, error: str | None, snapshot: dict) -> dict:
    """The `picked` line: what was completed, whether it went through, and the nudge it ended."""
    pick = {'kind': row['kind'], 'ref': row['ref'], 'item_id': row.get('item_id'), 'minutes': minutes, 'ok': error is None, 'error': error}
    return record(Event.PICKED, now, nudge_id=nudge_id, pick=pick, **snapshot)


# --- records and their schemas ----------------------------------------------------


def record(event: Event, now: dt.datetime, **fields: Any) -> dict:
    """One `doit.desk_nudge` line."""
    return {'ts': record_stamp(now), 'event': event.value, 'fleet.schema_version': RECORD_SCHEMA_VERSION, **fields}


# The fields that each event's line carries beyond `ts`, `event` and the schema version.
RECORD_FIELDS: dict[Event, tuple[str, ...]] = {
    Event.STARTED: ('doit_version', 'phase', 'counted_minutes', 'interval_minutes'),
    Event.FIRED: ('nudge_id', 'interval_minutes', 'counted_minutes', 'suggestion', 'sessions', 'focused_window'),
    Event.ANSWERED: ('nudge_id', 'answer', 'seconds_since_fired', 'saved', 'queued', 'late'),
    Event.EXPIRED: ('nudge_id',),
    Event.PICKED: ('nudge_id', 'pick', 'sessions', 'focused_window'),
    Event.GAP: ('gap_seconds',),
    Event.PAUSED: ('nudge_id',),
    Event.RESUMED: (),
}

FAILED_READ = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['error'],
    'properties': {'error': {'type': 'string', 'description': 'Why the read failed or timed out.'}},
}

SUGGESTION_SCHEMA = {
    'description': "What the nudge suggested: /next's first offered pursuit and what it stands for, or why none was drawn.",
    'oneOf': [
        {
            'type': 'object',
            'additionalProperties': False,
            'required': ['pursuit', 'item_id', 'label'],
            'properties': {
                'pursuit': {'type': 'string', 'description': 'The pursuit /next offered first.'},
                'item_id': {
                    'type': ['string', 'null'],
                    'description': (
                        'The id of its one resolved choice. Null where it resolved none, or several that only a person can choose between.'
                    ),
                },
                'label': {
                    'type': 'string',
                    'description': "What doit next shows for it: each resolved choice on its own line, else the pursuit's description.",
                },
            },
        },
        {
            'type': 'object',
            'additionalProperties': False,
            'required': ['error', 'message', 'detail'],
            'properties': {
                'error': {'enum': [kind.value for kind in SuggestionError], 'description': 'Why /next drew nothing.'},
                'message': {'type': 'string', 'description': 'What the panel says instead.'},
                'detail': {'type': 'string', 'description': "The service's or the client's own sentence."},
            },
        },
    ],
}

NUDGE_ID = {'type': 'string', 'pattern': '^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'}

RECORD_PROPERTIES: dict[str, dict] = {
    'ts': {'type': 'string', 'format': 'date-time', 'description': 'When it happened, in UTC.'},
    'event': {'enum': [event.value for event in Event], 'description': 'What happened.'},
    'fleet.schema_version': {'const': RECORD_SCHEMA_VERSION, 'description': "This line's shape."},
    'doit_version': {'type': 'string', 'description': 'The doit release the watch runs.'},
    'phase': {'enum': [phase.value for phase in Phase], 'description': 'Where the clock stood when the watch started.'},
    'nudge_id': {
        'oneOf': [NUDGE_ID, {'type': 'null'}],
        'description': "The firing's UUIDv7, which joins its answers and the service's row. Null where no nudge was in hand.",
    },
    'interval_minutes': {'type': 'number', 'description': 'The interval in force.'},
    'counted_minutes': {
        'type': 'number',
        'description': 'The minutes counted toward the interval: time with a desk session since the last nudge ended.',
    },
    'suggestion': SUGGESTION_SCHEMA,
    'sessions': {
        'description': 'Every row of `claude-sessions --json`, whole, or why it could not be read.',
        'oneOf': [{'type': 'array', 'items': {'type': 'object'}}, FAILED_READ],
    },
    'focused_window': {
        'description': 'The focused window, read through `hyprctl -i <instance> activewindow -j`, or why it could not be read.',
        'oneOf': [
            {
                'type': 'object',
                'additionalProperties': False,
                'required': ['class', 'title'],
                'properties': {'class': {'type': ['string', 'null']}, 'title': {'type': ['string', 'null']}},
            },
            FAILED_READ,
        ],
    },
    'answer': {'enum': [answer.value for answer in Answer], 'description': 'What the person said of the nudge.'},
    'seconds_since_fired': {'type': 'number', 'description': 'From the firing to the answer.'},
    'saved': {'type': 'boolean', 'description': 'Whether the service stored the answer.'},
    'queued': {
        'type': 'boolean',
        'description': (
            'Whether the answer is kept on the desk to send again. A kept answer is written again once, '
            'with saved true once stored, or with both false once the service refuses it.'
        ),
    },
    'late': {'type': 'boolean', 'description': 'Whether its nudge had already ended, which leaves the clock alone.'},
    'pick': {
        'type': 'object',
        'additionalProperties': False,
        'description': 'What a panel pick completed, and whether it went through.',
        'required': ['kind', 'ref', 'item_id', 'minutes', 'ok', 'error'],
        'properties': {
            'kind': {'enum': [kind.value for kind in CompletionKind], 'description': 'Which owner completes it.'},
            'ref': {'type': 'string', 'description': "A pursuit's name, a habit's id, or a review register entry's id."},
            'item_id': {'type': ['string', 'null'], 'description': "The pursuit's choice that its on_log completed, where it named one."},
            'minutes': {'type': ['integer', 'null'], 'description': 'The minutes logged for a timed pursuit.'},
            'ok': {'type': 'boolean', 'description': 'Whether the completion went through.'},
            'error': {'type': ['string', 'null'], 'description': "Why it did not: the owner's refusal, or the command and how it failed."},
        },
    },
    'gap_seconds': {'type': 'number', 'description': 'The time between two ticks, past two tick periods, which the clock does not count.'},
}


def record_schema() -> dict:
    """The JSON Schema of a record line, with every enum read from `Event`, `Answer`, `Phase` and `SuggestionError`."""
    return {
        '$schema': 'http://json-schema.org/draft-07/schema#',
        '$id': f'urn:doit:desk-nudge-record:v{RECORD_SCHEMA_VERSION}',
        'title': 'doit.desk_nudge',
        'description': (
            'One line of $XDG_STATE_HOME/doit/desk-nudge-events.jsonl, appended and never rotated, for a log shipper to read. '
            'Written by doit.desk_nudge.write_schemas from the module, so edit the module rather than this file.'
        ),
        'type': 'object',
        'additionalProperties': False,
        'required': ['ts', 'event', 'fleet.schema_version'],
        'properties': RECORD_PROPERTIES,
        'allOf': [
            {'if': {'properties': {'event': {'const': event.value}}}, 'then': {'required': list(fields)}}
            for event, fields in RECORD_FIELDS.items()
            if fields
        ],
    }


def status_schema() -> dict:
    """The JSON Schema of `doit desk-nudge status --json`, read from the same enums."""
    nullable_stamp = {'type': ['string', 'null'], 'format': 'date-time'}
    return {
        '$schema': 'http://json-schema.org/draft-07/schema#',
        '$id': f'urn:doit:desk-nudge-status:v{STATUS_SCHEMA_VERSION}',
        'title': 'doit desk-nudge status',
        'description': (
            "Where the desk nudge's clock stands, read from this desk's state file with no request. Its pill is the state file's "
            'own pill object, which a status bar reads from the file. '
            'Written by doit.desk_nudge.write_schemas from the module, so edit the module rather than this file.'
        ),
        'type': 'object',
        'additionalProperties': False,
        'required': [
            'schema_version',
            'phase',
            'watch_running',
            'counted_minutes',
            'interval_minutes',
            'minutes_left',
            'nudge_id',
            'fired_at',
            'ready_until',
            'suggestion',
            'answer_counts',
            'queued_answer_count',
            'pill',
        ],
        'properties': {
            'schema_version': {'const': STATUS_SCHEMA_VERSION},
            'phase': {'enum': [phase.value for phase in Phase], 'description': 'Where the clock stands.'},
            'watch_running': {'type': 'boolean', 'description': "Whether the watch ticked within the pill's stale horizon."},
            'counted_minutes': {
                'type': 'number',
                'description': 'The minutes counted toward the interval: time with a desk session since the last nudge ended.',
            },
            'interval_minutes': {
                'type': 'number',
                'description': f'The interval last read from the service, or {STARTING_INTERVAL_MINUTES} where none was.',
            },
            'minutes_left': {
                'type': ['integer', 'null'],
                'minimum': 0,
                'description': 'Minutes still to count before a firing. Null while a nudge is firing or ready.',
            },
            'nudge_id': {'oneOf': [NUDGE_ID, {'type': 'null'}], 'description': 'The nudge in hand.'},
            'fired_at': {**nullable_stamp, 'description': 'When the nudge in hand fired.'},
            'ready_until': {**nullable_stamp, 'description': 'When the ready nudge expires.'},
            'suggestion': {'oneOf': [*SUGGESTION_SCHEMA['oneOf'], {'type': 'null'}], 'description': SUGGESTION_SCHEMA['description']},
            'answer_counts': {
                'type': 'object',
                'additionalProperties': False,
                'required': [answer.value for answer in Answer],
                'properties': {answer.value: {'type': 'integer', 'minimum': 0} for answer in Answer},
                'description': 'How many of each answer the service holds, as last read.',
            },
            'queued_answer_count': {
                'type': 'integer',
                'minimum': 0,
                'description': 'How many answers the service has not stored yet, which the watch sends again.',
            },
            'pill': {
                'type': 'object',
                'additionalProperties': False,
                'required': ['text', 'class', 'stale_after'],
                'properties': {
                    'text': {'type': 'string', 'description': 'What a status bar prints.'},
                    'class': {'enum': [phase.value for phase in Phase], 'description': "The pill's CSS class."},
                    'stale_after': {**nullable_stamp, 'description': 'Past this, the pill reads `nudge stopped`.'},
                },
            },
        },
    }


def panel_schema() -> dict:
    """The JSON Schema of one panel step, as `panel show --json` and `panel pick --json` print it."""
    return {
        '$schema': 'http://json-schema.org/draft-07/schema#',
        '$id': f'urn:doit:desk-nudge-panel:v{PANEL_SCHEMA_VERSION}',
        'title': 'doit desk-nudge panel',
        'description': (
            "One step of the desk nudge's panel, read from the rows that the watch keeps in $XDG_CACHE_HOME/doit/desk-nudge-panel.json. "
            '`doit tui` draws each row and hands the picked key to `doit desk-nudge panel pick`, which prints the next step. '
            'Written by doit.desk_nudge.write_schemas from the module, so edit the module rather than this file.'
        ),
        'type': 'object',
        'additionalProperties': False,
        'required': ['schema_version', 'step', 'rows', 'message'],
        'properties': {
            'schema_version': {'const': PANEL_SCHEMA_VERSION},
            'step': {
                'enum': [step.value for step in PanelStep],
                'description': "today's rows, a timed pursuit's minutes, or done once a pick has acted.",
            },
            'rows': {
                'type': 'array',
                'description': 'What to draw, in order. Empty on a done step.',
                'items': {
                    'type': 'object',
                    'additionalProperties': False,
                    'required': ['key', 'kind', 'text', 'group', 'minutes', 'suggested'],
                    'properties': {
                        'key': {
                            'type': ['string', 'null'],
                            'description': 'What `panel pick` takes. Null on a note, which completes nothing.',
                        },
                        'kind': {
                            'enum': [str(kind) for kind in PANEL_ROW_KINDS],
                            'description': 'A note, an answer, what a completion completes, or a step through the minutes.',
                        },
                        'text': {'type': 'string', 'description': 'The line to draw.'},
                        'group': {
                            'type': ['string', 'null'],
                            'description': 'The due lane a completion row came from, which it is drawn under. Null on every other row.',
                        },
                        'minutes': {
                            'type': ['integer', 'null'],
                            'description': 'The minutes a minutes row logs or reopens at. Null on every other row.',
                        },
                        'suggested': {
                            'type': 'boolean',
                            'description': 'A completion row completes the suggestion, which `doit next` put first. '
                            'On a log_minutes row, the amount that the step offers first, and that its ±5 rows count from.',
                        },
                    },
                },
            },
            'message': {'type': ['string', 'null'], 'description': 'What a done step did. Null on every other step.'},
        },
    }


def schema_text(schema: dict) -> str:
    return json.dumps(schema, indent=2, ensure_ascii=False) + '\n'


def write_schemas(directory: Path) -> None:
    """Write the record, status and panel schemas into `directory`, the repo's `schemas/`."""
    (directory / RECORD_SCHEMA_FILE).write_text(schema_text(record_schema()), encoding='utf-8')
    (directory / STATUS_SCHEMA_FILE).write_text(schema_text(status_schema()), encoding='utf-8')
    (directory / PANEL_SCHEMA_FILE).write_text(schema_text(panel_schema()), encoding='utf-8')
