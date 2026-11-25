"""The lane contract — the one shape doit renders, and the one shape it emits.

A lane is a titled, independently-ordered list of things you could act on. This
module defines it, parses it, and serializes it, and it is deliberately the only
place that knows what a lane is.

**The contract is symmetric, and that is the point.** What a source emits is the
document doit builds from every adapter, which `doit sources contract` prints, so a
tool that wants a lane on the dashboard has one shape to copy. Conform to it and doit
needs no code to render you — no mapping, no adapter, no release. That is the whole
interface.

An app that has not conformed yet gets an adapter, which is ordinary Python and
lives with the dashboard rather than in config. Adapters are the migration path,
not the design: every one of them is a source that has not yet been asked to
speak the contract.

Rejected: expressing the mapping in the sources document as paths and format strings.
That is a template language rebuilt in YAML, it could not express a label chosen
by a boolean without growing conditionals, and it puts the burden on the consumer
forever rather than on the producer once.
"""

import datetime as dt
from dataclasses import dataclass
from dataclasses import field
from enum import StrEnum
from typing import TypeGuard

from doit import actor

# Bump only for a breaking reshape. Additive fields are free, because every
# reader below treats an absent field as its empty value.
SCHEMA_VERSION = 1

# The day set's lane, under one name for the grid of the whole set and for the
# group of those done, because `doit today` matches the two by it.
HABITS = 'habits'
# The due pursuits' lane.
PURSUITS = 'pursuits'


class Urgency(StrEnum):
    """How hard a row's note should push.

    Color is derived from this, never from matching the note text, so wording
    and emphasis stay independent — a source can reword "3d overdue" without
    silently changing what color it renders.
    """

    NONE = 'none'
    DUE = 'due'
    OVERDUE = 'overdue'


class CompletionKind(StrEnum):
    """Which owner completes a row, and so which code builds the completion."""

    PURSUIT = 'pursuit'
    HABIT = 'habit'
    REVIEW = 'review'
    PRACTICE = 'practice'


@dataclass(frozen=True)
class CompletionTarget:
    """What picking a row completes, as an identity rather than a command.

    `ref` is the pursuit's name, the habit's id, or the register entry's id. doit
    builds the command from the kind and the ref when the row is picked, so no
    argv is ever read back from a document or a cache.
    """

    kind: CompletionKind
    ref: str


@dataclass(frozen=True)
class Row:
    """One thing you could act on.

    `label` is the gutter word, `text` the thing itself, `note` the trailing
    qualifier. A source decides all of them: doit never invents a label, because a
    label it invented would describe doit's model rather than the source's.

    `handle` is what you would type to act on this row, and it is the difference
    between a row you can read and a row you can do. A grid cell has carried one
    since the beginning; a row not having one is why a lane could name a thing to
    revisit while withholding the command that revisits it.

    `completes` is the one field that a source never sets. Only doit's own builders of
    today's rows fill it, and `row_from` never reads it, so a source's document
    cannot choose what a pick completes.
    """

    label: str
    text: str
    note: str = ''
    urgency: Urgency = Urgency.NONE
    handle: str = ''
    completes: CompletionTarget | None = None


@dataclass(frozen=True)
class GridCell:
    """One member of a complete set — today's habits, not a ranked excerpt."""

    text: str
    done: bool
    # What you would type to act on this cell. Blank when there is nothing left
    # to do, so a finished cell does not offer a handle it does not need.
    handle: str = ''
    # When it was done, as an ISO 8601 instant. Blank where the source records
    # only the day, because a midnight standing in for an unknown hour sorts
    # first and prints as a time nobody did anything at.
    done_at: str = ''
    # The local day it was done, ISO 8601, set whether or not the hour is known.
    done_on: str = ''
    # Who did it, as the record wrote it; `doit.actor.kind` reads the spelling.
    # Blank where the record keeps no actor.
    actor: str = ''


@dataclass
class Lane:
    """One lane's model.

    `rows` are the ranked things to act on and are capped by the renderer.
    `grid` is exempt from the cap: it is a set that is only useful whole.

    `total` is the pre-cap size, so a capped lane never lies about the pile it
    sits above. `hints` are what you would run to see the rest — a remainder
    count says a lane was truncated without giving you anything to type.

    `in_flight` counts open things the lane does not list, because something
    other than you is already acting on them. It is never part of `total`: a
    reader subtracting the rows it shows from `total` would otherwise report
    them as rows the cap cut.

    `alert` says the lane carries problems rather than work. It draws first and
    in red, and it is omitted entirely while nothing is open, because a standing
    row reading "nothing wrong" is what teaches you to stop looking at the one
    place that says something is. Work in flight is open, so an alert lane with
    no rows and a nonzero `in_flight` is still drawn. An alert lane that could
    not be built is still drawn too: the omission is for a lane that answered
    and had nothing, never for one that failed to answer.

    `omit_when_empty` gives a work lane that same omission without the red and
    without drawing first: open feedback is empty most days, and a standing `—`
    says nothing while it is.
    """

    name: str
    title: str
    meta: str = ''
    rows: list[Row] = field(default_factory=list)
    grid: list[GridCell] = field(default_factory=list)
    total: int = 0
    in_flight: int = 0
    hints: list[str] = field(default_factory=list)
    reason: str = ''
    available: bool = True
    alert: bool = False
    omit_when_empty: bool = False


def unavailable(name: str, title: str, reason: str) -> Lane:
    """A lane that could not be built, carrying why.

    Never omitted: a dashboard that quietly drops a lane reads as "nothing
    outstanding", which is the worst thing it could say wrongly.
    """
    return Lane(name=name, title=title, reason=reason, available=False)


def is_lane_document(payload: object) -> TypeGuard[dict]:
    """Whether a source's output is already in the contract.

    The test is structural rather than a version check, because a source that
    emits lanes without a schema_version is still emitting lanes — refusing it
    would make the contract harder to adopt than to ignore.
    """
    return isinstance(payload, dict) and isinstance(payload.get('lanes'), list)


def row_from(payload: dict) -> Row:
    urgency = payload.get('urgency') or Urgency.NONE
    return Row(
        label=str(payload.get('label', '')),
        text=str(payload.get('text', '')),
        note=str(payload.get('note', '')),
        urgency=Urgency(urgency) if urgency in set(Urgency) else Urgency.NONE,
        handle=str(payload.get('handle', '')),
    )


def cell_from(payload: dict) -> GridCell:
    return GridCell(
        text=str(payload.get('text', '')),
        done=bool(payload.get('done')),
        handle=str(payload.get('handle', '')),
        done_at=str(payload.get('done_at') or ''),
        done_on=str(payload.get('done_on') or ''),
        actor=str(payload.get('actor') or ''),
    )


def lane_from(payload: dict) -> Lane:
    rows = [row_from(row) for row in payload.get('rows') or [] if isinstance(row, dict)]
    grid = [cell_from(cell) for cell in payload.get('grid') or [] if isinstance(cell, dict)]
    name = str(payload.get('name', ''))
    return Lane(
        name=name,
        title=str(payload.get('title') or name.upper()),
        meta=str(payload.get('meta', '')),
        rows=rows,
        grid=grid,
        total=int(payload.get('total') or len(rows) or len(grid)),
        in_flight=int(payload.get('in_flight') or 0),
        hints=[str(hint) for hint in payload.get('hints') or []],
        reason=str(payload.get('reason') or ''),
        available=payload.get('status', 'ok') != 'unavailable',
        alert=bool(payload.get('alert')),
        omit_when_empty=bool(payload.get('omit_when_empty')),
    )


def from_document(payload: object) -> list[Lane]:
    """Every lane in a conforming source's output.

    A malformed lane is skipped rather than fatal. One bad entry from a source
    mid-upgrade must not cost you the lanes either side of it.
    """
    if not is_lane_document(payload):
        return []
    return [lane_from(lane) for lane in payload['lanes'] if isinstance(lane, dict) and lane.get('name')]


def to_document(lanes: list[Lane], generated_at: dt.datetime) -> dict:
    """The contract, as doit emits it.

    This is also the worked example: a source wanting a lane here can run
    `doit sources contract` and copy the shape.
    """
    return {
        'schema_version': SCHEMA_VERSION,
        'generated_at': timestamp(generated_at),
        'lanes': [lane_to_dict(lane) for lane in lanes],
    }


def timestamp(generated_at: dt.datetime) -> str:
    """A document's timestamp, always with an offset.

    Coerced here rather than at each caller: a stamp with no offset cannot be
    compared across machines, and a naive one from a caller means the local
    time `datetime.now()` returns.
    """
    return generated_at.astimezone().isoformat(timespec='seconds')


def lane_to_dict(lane: Lane) -> dict:
    """One lane in the contract's shape, for a document that is not a flat list of lanes."""
    return {
        'name': lane.name,
        'title': lane.title,
        'meta': lane.meta,
        'status': 'ok' if lane.available else 'unavailable',
        'alert': lane.alert,
        'omit_when_empty': lane.omit_when_empty,
        'reason': lane.reason or None,
        'rows': [row_to_dict(row) for row in lane.rows],
        'grid': [
            {
                'text': cell.text,
                'done': cell.done,
                'handle': cell.handle,
                'done_at': cell.done_at,
                'done_on': cell.done_on,
                'actor': cell.actor,
                'actor_kind': actor.kind(cell.actor),
            }
            for cell in lane.grid
        ],
        'total': lane.total,
        'in_flight': lane.in_flight,
        'hints': lane.hints,
    }


def row_to_dict(row: Row) -> dict:
    """One row in the contract's shape, carrying `completes` only where a builder of today's rows set it."""
    shaped: dict[str, str | dict[str, str]] = {
        'label': row.label,
        'text': row.text,
        'note': row.note,
        'urgency': str(row.urgency),
        'handle': row.handle,
    }
    if row.completes is not None:
        shaped['completes'] = {'kind': str(row.completes.kind), 'ref': row.completes.ref}
    return shaped
