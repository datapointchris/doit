"""The lane contract — the one shape doit renders, and the one shape it emits.

A lane is a titled, independently-ordered list of things you could act on. This
module defines it, parses it, and serialises it, and it is deliberately the only
place that knows what a lane is.

**The contract is symmetric, and that is the point.** What `doit dashboard --json`
emits is exactly what a source may emit, so a tool that wants a lane on the
dashboard has one thing to read: doit's own output. Conform to it and doit needs
no code to render you — no mapping, no adapter, no release. That is the whole
interface.

An app that has not conformed yet gets an adapter, which is ordinary Python and
lives with the dashboard rather than in config. Adapters are the migration path,
not the design: every one of them is a source that has not yet been asked to
speak the contract.

Rejected: expressing the mapping in `sources.yml` as paths and format strings.
That is a template language rebuilt in YAML, it could not express a label chosen
by a boolean without growing conditionals, and it puts the burden on the consumer
forever rather than on the producer once.
"""

import json
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from enum import StrEnum
from typing import TypeGuard

# Bump only for a breaking reshape. Additive fields are free, because every
# reader below treats an absent field as its empty value.
SCHEMA_VERSION = 1


class Urgency(StrEnum):
    """How hard a row's note should push.

    Colour is derived from this, never from matching the note text, so wording
    and emphasis stay independent — a source can reword "3d overdue" without
    silently changing what colour it renders.
    """

    NONE = 'none'
    DUE = 'due'
    OVERDUE = 'overdue'


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
    """

    label: str
    text: str
    note: str = ''
    urgency: Urgency = Urgency.NONE
    handle: str = ''


@dataclass(frozen=True)
class GridCell:
    """One member of a complete set — today's habits, not a ranked excerpt."""

    text: str
    done: bool
    # What you would type to act on this cell. Blank when there is nothing left
    # to do, so a finished cell does not offer a handle it does not need.
    handle: str = ''


@dataclass
class Lane:
    """One lane's model.

    `rows` are the ranked things to act on and are capped by the renderer.
    `grid` is exempt from the cap: it is a set that is only useful whole.

    `total` is the pre-cap size, so a capped lane never lies about the pile it
    sits above. `hints` are what you would run to see the rest — a remainder
    count says a lane was truncated without giving you anything to type.

    `alert` says the lane carries problems rather than work. It draws first and
    in red, and it is omitted entirely while it is empty, because a standing row
    reading "nothing wrong" is what teaches you to stop looking at the one place
    that says something is. An alert lane that could not be built is still drawn:
    the omission is for a lane that answered and had nothing, never for one that
    failed to answer.
    """

    name: str
    title: str
    meta: str = ''
    rows: list[Row] = field(default_factory=list)
    grid: list[GridCell] = field(default_factory=list)
    total: int = 0
    hints: list[str] = field(default_factory=list)
    reason: str = ''
    available: bool = True
    alert: bool = False


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
        hints=[str(hint) for hint in payload.get('hints') or []],
        reason=str(payload.get('reason') or ''),
        available=payload.get('status', 'ok') != 'unavailable',
        alert=bool(payload.get('alert')),
    )


def from_document(payload: object) -> list[Lane]:
    """Every lane in a conforming source's output.

    A malformed lane is skipped rather than fatal. One bad entry from a source
    mid-upgrade must not cost you the lanes either side of it.
    """
    if not is_lane_document(payload):
        return []
    return [lane_from(lane) for lane in payload['lanes'] if isinstance(lane, dict) and lane.get('name')]


def to_document(lanes: list[Lane], generated_at: datetime) -> dict:
    """The contract, as doit emits it.

    This is also the worked example: a source wanting a lane here can run
    `doit dashboard --json` and copy the shape.
    """
    return {
        'schema_version': SCHEMA_VERSION,
        'generated_at': generated_at.isoformat(timespec='seconds'),
        'lanes': [
            {
                'name': lane.name,
                'title': lane.title,
                'meta': lane.meta,
                'status': 'ok' if lane.available else 'unavailable',
                'alert': lane.alert,
                'reason': lane.reason or None,
                'rows': [
                    {'label': row.label, 'text': row.text, 'note': row.note, 'urgency': str(row.urgency), 'handle': row.handle}
                    for row in lane.rows
                ],
                'grid': [{'text': cell.text, 'done': cell.done, 'handle': cell.handle} for cell in lane.grid],
                'total': lane.total,
                'hints': lane.hints,
            }
            for lane in lanes
        ],
    }


def dumps(lanes: list[Lane], generated_at: datetime) -> str:
    return json.dumps(to_document(lanes, generated_at), indent=2)
