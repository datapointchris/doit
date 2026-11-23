"""The desk nudge's rules: the clock that counts present time, the answers that tune its interval, and the records it writes.

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

from doit.pursuits import choice_lines
from doit.pursuits import settled_choice

STARTING_INTERVAL_MINUTES = 60

# Each too soon multiplies the interval by this, and each too late divides it.
ANSWER_STEP = 1.1

TICK_SECONDS = 15
# The most present time one tick adds. A longer gap between ticks, such as a
# suspend or a stopped watch, counts as absence, because no listener reports it.
TICK_CAP_SECONDS = 2 * TICK_SECONDS
# How long after the last input hypridle's listener reports the desk away.
IDLE_AFTER_SECONDS = 120
# An absence this long is a break, and restarts the clock at zero. A shorter one pauses it.
BREAK_SECONDS = 5 * 60
READY_SECONDS = 10 * 60
# The pill reads `nudge stopped` once this passes with no tick, so a watch that
# died is told apart from a clock that holds.
STALE_AFTER_SECONDS = 4 * TICK_SECONDS
# How many ended nudges the state remembers, so a late answer can name its firing.
REMEMBERED_NUDGES = 20

STATE_VERSION = 1
RECORD_SCHEMA_VERSION = 1
STATUS_SCHEMA_VERSION = 1
RECORD_SCHEMA_FILE = 'desk-nudge-record.schema.json'
STATUS_SCHEMA_FILE = 'desk-nudge-status.schema.json'


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
    DISMISSED = 'dismissed'
    EXPIRED = 'expired'
    PICKED = 'picked'
    AWAY = 'away'
    BACK = 'back'
    GAP = 'gap'
    PAUSED = 'paused'
    RESUMED = 'resumed'


class Phase(StrEnum):
    """Where the clock stands. Only `counting` adds present time."""

    COUNTING = 'counting'
    FIRING = 'firing'
    READY = 'ready'
    AWAY = 'away'
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

PILL_TEXT = {Phase.AWAY: 'away', Phase.PAUSED: 'paused', Phase.NO_DESK: 'no desk'}


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
        'present_seconds': 0.0,
        'last_tick': None,
        'interval_minutes': float(STARTING_INTERVAL_MINUTES),
        'answer_counts': answer_counts([]),
        'nudge': None,
        'away': None,
        'no_desk_since': None,
        'queued_answers': [],
        'recent_nudges': [],
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
    # The nudge that this change ended, whose notification is closed by its id.
    closes: dict | None = None
    # Present time reached the interval, so a firing begins.
    fire: bool = False


def end_ready(state: dict) -> dict:
    """End the nudge in hand, restart the clock at zero, and return the nudge."""
    nudge = state['nudge']
    state['nudge'] = None
    state['present_seconds'] = 0.0
    state['phase'] = Phase.COUNTING.value
    remembered = {'nudge_id': nudge['nudge_id'], 'fired_at': nudge['fired_at']}
    state['recent_nudges'] = [*state['recent_nudges'], remembered][-REMEMBERED_NUDGES:]
    return nudge


def away_outlived(state: dict, hypridle_pid: str | None) -> bool:
    """Whether the hypridle that reported the away has gone, so that no `back` will come for it."""
    away = state['away']
    return bool(away and away.get('hypridle_pid') and hypridle_pid and hypridle_pid != away['hypridle_pid'])


def tick(state: dict, now: dt.datetime, live_desk: bool, hypridle_pid: str | None = None) -> Change:
    """One tick of the watch: present time added, a ready nudge expired, the desk checked, and a firing called.

    A tick adds the seconds since the last one, capped at `TICK_CAP_SECONDS`. A
    longer gap writes `gap`, and one of `BREAK_SECONDS` or more restarts the clock.
    A desk that has been gone that long restarts it too when it returns. An away
    whose hypridle has gone, as after a logout or a reboot, ends as a `back` would.
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
        if phase is not Phase.NO_DESK:
            state['no_desk_since'] = stamp(last or now)
            state['phase'] = Phase.NO_DESK.value
        return change
    returned = phase is Phase.NO_DESK
    if returned:
        began = min(
            (moment for moment in (maybe_instant(state['no_desk_since']), maybe_instant((state['away'] or {}).get('since'))) if moment),
            default=now,
        )
        state['no_desk_since'] = None
        if (now - began).total_seconds() >= BREAK_SECONDS:
            state['present_seconds'] = 0.0
        state['phase'] = (Phase.AWAY if state['away'] else Phase.COUNTING).value
        phase = Phase(state['phase'])
    if phase is Phase.AWAY and away_outlived(state, hypridle_pid):
        change.records += came_back(state, now, state['away']['idle_after_seconds']).records
        return change
    if returned or phase is Phase.AWAY:
        return change
    if gap >= BREAK_SECONDS:
        state['present_seconds'] = 0.0
    elif not change.closes:
        state['present_seconds'] += min(gap, TICK_CAP_SECONDS)
    change.fire = state['present_seconds'] >= state['interval_minutes'] * 60
    return change


def begin_firing(state: dict, now: dt.datetime, nudge_id: str) -> dict:
    """Step one of a firing: the nudge minted and the phase `firing`, so a restarted watch finishes it rather than firing again."""
    state['phase'] = Phase.FIRING.value
    state['nudge'] = {
        'nudge_id': nudge_id,
        'fired_at': stamp(now),
        'present_minutes': round(state['present_seconds'] / 60, 2),
        'interval_minutes': state['interval_minutes'],
        'suggestion': None,
        'ready_until': None,
        'notification_id': None,
        'bus_owner': None,
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


def held_nudge(state: dict, nudge_id: str) -> dict | None:
    """The nudge in hand, where it is the one named."""
    nudge = in_hand(state)
    return nudge if nudge and nudge['nudge_id'] == nudge_id else None


def went_away(state: dict, now: dt.datetime, idle_after_seconds: int, hypridle_pid: str | None = None) -> Change:
    """hypridle's listener saw no input for `idle_after_seconds`, so the desk has been away since the last input.

    Counting gives those seconds back. A nudge in hand ends, and the record says
    whether it fired inside the idle window. A paused clock or a gone desk only
    remembers when the absence began. The away keeps the pid of the hypridle that
    reported it, so a tick can end it once that hypridle is gone.
    """
    since = now - dt.timedelta(seconds=idle_after_seconds)
    fired_while_idle = False
    nudge_id = None
    change = Change()
    phase = Phase(state['phase'])
    if phase.holds_nudge:
        change.closes = end_ready(state)
        nudge_id = change.closes['nudge_id']
        fired_while_idle = instant(change.closes['fired_at']) >= since
        state['phase'] = Phase.AWAY.value
    elif phase is Phase.COUNTING:
        state['present_seconds'] = max(state['present_seconds'] - idle_after_seconds, 0.0)
        state['phase'] = Phase.AWAY.value
    earlier = state['away']
    state['away'] = {
        'since': earlier['since'] if earlier else stamp(since),
        'idle_after_seconds': idle_after_seconds,
        'fired_while_idle': fired_while_idle or bool(earlier and earlier['fired_while_idle']),
        'hypridle_pid': hypridle_pid or (earlier or {}).get('hypridle_pid'),
    }
    change.records.append(
        record(
            Event.AWAY,
            now,
            idle_after_seconds=idle_after_seconds,
            away_seconds=float(idle_after_seconds),
            fired_while_idle=fired_while_idle,
            nudge_id=nudge_id,
        )
    )
    return change


def came_back(state: dict, now: dt.datetime, idle_after_seconds: int) -> Change:
    """Input again. Under `BREAK_SECONDS` away resumes the count, and a break restarts it at zero."""
    away = state['away'] or {'since': stamp(now - dt.timedelta(seconds=idle_after_seconds)), 'fired_while_idle': False}
    away_seconds = max((now - instant(away['since'])).total_seconds(), 0.0)
    state['away'] = None
    if state['phase'] == Phase.AWAY:
        if away_seconds >= BREAK_SECONDS:
            state['present_seconds'] = 0.0
        state['phase'] = Phase.COUNTING.value
    return Change(
        records=[
            record(
                Event.BACK,
                now,
                idle_after_seconds=idle_after_seconds,
                away_seconds=round(away_seconds, 3),
                fired_while_idle=away['fired_while_idle'],
            )
        ]
    )


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
    state['phase'] = (Phase.AWAY if state['away'] else Phase.COUNTING).value
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


def attach_notification(state: dict, nudge_id: str, notification_id: int, bus_owner: str | None) -> bool:
    """The notification's id and the bus owner that showed it, kept on the nudge still in hand."""
    nudge = held_nudge(state, nudge_id)
    if nudge is None:
        return False
    nudge['notification_id'] = notification_id
    nudge['bus_owner'] = bus_owner
    return True


def dismissal(state: dict, now: dt.datetime, nudge_id: str) -> list[dict]:
    """A notification closed with no answer while its nudge is ready: `dismissed`, and the nudge stays ready."""
    if state['phase'] != Phase.READY or held_nudge(state, nudge_id) is None:
        return []
    return [record(Event.DISMISSED, now, nudge_id=nudge_id)]


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


def notification_text(nudge: dict) -> tuple[str, str]:
    """The notification's summary and body."""
    suggestion = nudge['suggestion'] or {}
    foot = f'{round(nudge["present_minutes"])} min at the desk'
    if 'pursuit' not in suggestion:
        return 'Time for a break', f'{suggestion.get("message") or SUGGESTION_MESSAGES[SuggestionError.NOTHING_TO_DRAW]}\n{foot}'
    label = suggestion.get('label')
    return f'Up for {suggestion["pursuit"]}', f'{label}\n{foot}' if label else foot


def notification_argv(nudge: dict, replaces: int | None = None) -> list[str]:
    """`notify-send`, printing its id and then the action chosen, open until clicked, with the four answers."""
    summary, body = notification_text(nudge)
    actions = [part for answer in Answer for part in ('-A', f'{answer.value}={ANSWER_LABELS[answer]}')]
    replacing = ['-r', str(replaces)] if replaces else []
    return ['notify-send', '-p', '-t', '0', '-a', 'doit', *replacing, *actions, summary, body]


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


def bus_owner_from(reply: str) -> str | None:
    """The unique name in gdbus's reply to GetNameOwner, such as `(':1.42',)`."""
    found = re.search(r"'([^']+)'", reply)
    return found.group(1) if found else None


# --- the pill and the status -----------------------------------------------------


def minutes_left(state: dict) -> int:
    return max(math.ceil((state['interval_minutes'] * 60 - state['present_seconds']) / 60), 0)


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
        'present_minutes': round(state['present_seconds'] / 60, 2),
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


# --- records and their schemas ----------------------------------------------------


def record(event: Event, now: dt.datetime, **fields: Any) -> dict:
    """One `doit.desk_nudge` line."""
    return {'ts': record_stamp(now), 'event': event.value, 'fleet.schema_version': RECORD_SCHEMA_VERSION, **fields}


# The fields that each event's line carries beyond `ts`, `event` and the schema version.
RECORD_FIELDS: dict[Event, tuple[str, ...]] = {
    Event.STARTED: ('doit_version', 'phase', 'present_minutes', 'interval_minutes'),
    Event.FIRED: ('nudge_id', 'interval_minutes', 'present_minutes', 'suggestion', 'sessions', 'focused_window'),
    Event.ANSWERED: ('nudge_id', 'answer', 'seconds_since_fired', 'saved', 'queued', 'late'),
    Event.DISMISSED: ('nudge_id',),
    Event.EXPIRED: ('nudge_id',),
    Event.PICKED: ('nudge_id', 'pick', 'sessions', 'focused_window'),
    Event.AWAY: ('idle_after_seconds', 'away_seconds', 'fired_while_idle', 'nudge_id'),
    Event.BACK: ('idle_after_seconds', 'away_seconds', 'fired_while_idle'),
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
                'message': {'type': 'string', 'description': 'What the notification says instead.'},
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
    'present_minutes': {'type': 'number', 'description': 'The present time counted.'},
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
        'required': ['kind', 'ref', 'ok'],
        'properties': {
            'kind': {'type': 'string', 'description': 'The row picked: a pursuit, task, habit, review or practice.'},
            'ref': {'type': 'string', 'description': "The row's identity within its kind."},
            'item_id': {'type': ['string', 'null']},
            'minutes': {'type': ['integer', 'null']},
            'ok': {'type': 'boolean', 'description': 'Whether the completion went through.'},
            'error': {'type': ['string', 'null']},
        },
    },
    'idle_after_seconds': {'type': 'integer', 'description': "The listener's timeout, which an away counts back from."},
    'away_seconds': {'type': 'number', 'description': 'How long the absence had run, from the last input.'},
    'fired_while_idle': {'type': 'boolean', 'description': 'Whether a nudge fired inside the idle window.'},
    'gap_seconds': {'type': 'number', 'description': 'The time between two ticks, counted as absence past two tick periods.'},
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
            'present_minutes',
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
            'present_minutes': {'type': 'number', 'description': 'The present time counted toward the interval.'},
            'interval_minutes': {
                'type': 'number',
                'description': f'The interval last read from the service, or {STARTING_INTERVAL_MINUTES} where none was.',
            },
            'minutes_left': {
                'type': ['integer', 'null'],
                'minimum': 0,
                'description': 'Present minutes still to count before a firing. Null while a nudge is firing or ready.',
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


def schema_text(schema: dict) -> str:
    return json.dumps(schema, indent=2, ensure_ascii=False) + '\n'


def write_schemas(directory: Path) -> None:
    """Write both schemas into `directory`, the repo's `schemas/`."""
    (directory / RECORD_SCHEMA_FILE).write_text(schema_text(record_schema()), encoding='utf-8')
    (directory / STATUS_SCHEMA_FILE).write_text(schema_text(status_schema()), encoding='utf-8')
