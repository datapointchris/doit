"""The desk panel view: each part of the desk nudge's panel, computed from what the service read.

The service reads the store and the apps and hands this module what it read.
The root package's own functions decide every rule of the panel here, so a
reader of the view needs no copy of any of them.
`schemas/desk-panel-view.schema.json` describes the document.

Every entry that a pick completes carries its `key` and what it completes, as
`{kind, ref, item_id}`. An entry that completes nothing carries null in both.
A habit's checklist item carries two keys, `key` to tick one set and
`untick_key` to take the latest back, beside the one `completes` they share.
So no reader parses a key or lists a kind to learn what a pick does.

No part holds minutes away from the desk. A timed pursuit's Log starts from its
`usual_minutes`, because an amount inferred from time away was weighed and
rejected.
"""

import dataclasses as dc
import datetime as dt

from doit import dashboard
from doit import happened
from doit import lanes
from doit import quota
from doit import sources
from doit import today
from doit.desk_nudge import ANSWER_LABELS
from doit.desk_nudge import Answer
from doit.lanes import CompletionKind
from doit.lanes import Lane
from doit.lanes import Row
from doit.pursuits import choice_lines
from doit.pursuits import is_timed
from doit.pursuits import settled_choice
from doit.pursuits import writes_through
from doit.today import due_row_cap

SCHEMA_VERSION = 1

KEY_SEPARATOR = ':'

# The kinds of a checklist item's two keys: one ticks a set, one takes the latest set back.
TICK = 'tick'
UNTICK = 'untick'

# The due lanes carried under the lane contract. Pursuits and habits are parts of their own.
LANES = (*(register.name for register in happened.registers()), *today.DAY_SHAPED_ROW_LANES)

NOTHING_OFFERED = 'the draw offers nothing that is not already logged'

NO_COMPLETION: dict = {'key': None, 'completes': None}


def completion_key(kind: str, ref: str, item_id: str | None = None) -> str:
    """What an entry completes, as its key: `pursuit:tasks:532`, `habit:17`, `review:prune-docker`."""
    return KEY_SEPARATOR.join([kind, ref, *([item_id] if item_id else [])])


def completion(kind: CompletionKind, ref: str, item_id: str | None = None) -> dict:
    """What picking an entry completes: its key, and the kind, ref and item id that make it."""
    return {'key': completion_key(kind, ref, item_id), 'completes': {'kind': str(kind), 'ref': ref, 'item_id': item_id}}


def unit_of(config: dict) -> str:
    """A pursuit's unit, minutes or checkoffs, as `doit today --json` names it."""
    return 'minutes' if is_timed(config) else 'checkoffs'


def pursuit_completion(name: str, config: dict) -> dict:
    """A pursuit that writes through is completed by one of its choices, so its own entry completes nothing."""
    return NO_COMPLETION if writes_through(config) else completion(CompletionKind.PURSUIT, name)


def choice(name: str, config: dict, found: dict) -> dict:
    """One resolved choice: its title, its author or place, and what picking it completes.

    Only a pursuit that writes through completes a choice, and only a choice
    carrying an id, because its log completes that id in the app that owns it.
    """
    item_id = found.get('id')
    completes = completion(CompletionKind.PURSUIT, name, item_id) if writes_through(config) and item_id else NO_COMPLETION
    return {'id': item_id, 'title': found.get('label') or '', 'by': found.get('context') or '', **completes}


def choices_of(name: str, config: dict, entry: dict | None) -> tuple[list[dict], str | None]:
    """A pursuit's choices from its resolve, or why they could not be read."""
    if not entry:
        return [], None
    if entry.get('error'):
        return [], str(entry['error'])
    return [choice(name, config, found) for found in entry.get('choices') or [] if isinstance(found, dict)], None


def figures(pursuit: dict | None) -> dict:
    """What today asks of a pursuit, what it paid and what is behind, from today's figures. Null where the figures do not count it."""
    found = pursuit or {}
    return {'need': found.get('need'), 'done': found.get('done'), 'behind': found.get('behind')}


def suggestion(selection: dict, register: dict, usual: dict, days: dict[str, dict], items: dict[str, dict]) -> dict:
    """The draw's first pursuit, as `doit next` offers it, with its choices and what a Log starts from.

    A resolve in `items` stands in for the draw's own, so a choice read since
    the draw failed is the one shown, as the pursuits part shows it. The label
    is what `doit next` shows for it: each resolved choice on its own line, else
    the pursuit's description.
    """
    resolved = {**(selection.get('resolved') or {}), **items}
    name = selection['offered'][0]
    config = register.get(name) or {}
    entry = resolved.get(name) or {}
    drawn = [] if entry.get('error') else entry.get('choices') or []
    settled = settled_choice(drawn)
    choices, choices_error = choices_of(name, config, resolved.get(name))
    return {
        'draw_id': selection.get('draw_id'),
        'pursuit': name,
        'item_id': settled.get('id') if settled else None,
        'label': '\n'.join(choice_lines(drawn)) or config.get('description') or '',
        'description': config.get('description') or '',
        'unit': unit_of(config),
        'writes_through': writes_through(config),
        'usual_minutes': usual.get(name),
        **figures(days.get(name)),
        **pursuit_completion(name, config),
        'choices': choices,
        'choices_error': choices_error,
    }


def answers() -> list[dict]:
    """Each answer to a nudge, with the words on its button."""
    return [{'answer': answer.value, 'label': ANSWER_LABELS[answer]} for answer in Answer]


def due_lane(day: today.Day, name: str) -> Lane | None:
    return next((lane for lane in day.due if lane.name == name), None)


def listed_pursuits(day: today.Day) -> list[str]:
    """Each pursuit on today's due list, furthest behind first, then each other one that today's figures count."""
    lane = due_lane(day, lanes.PURSUITS)
    owed = [row.completes.ref for row in lane.rows if row.completes] if lane is not None and lane.available else []
    counted = [entry.name for entry in day.figures.pursuits] if day.figures is not None else []
    return list(dict.fromkeys([*owed, *counted]))


def lists_choices(config: dict) -> bool:
    """Whether the panel lists a pursuit's choices under it.

    A pursuit that writes through lists them, because a pick of one completes
    it. Any other lists them only where it declares `choices_shown`, such as a
    course whose next chapters say what a sitting would be on. A resolve kept
    only to place `doit next`'s row lists nothing.
    """
    return writes_through(config) or bool(config.get('resolve') and config.get('choices_shown'))


def item_pursuits(day: today.Day, register: dict) -> list[str]:
    """The listed pursuits whose choices are read, because the panel lists them."""
    return [name for name in listed_pursuits(day) if lists_choices(register.get(name) or {})]


def pursuits_part(day: today.Day, register: dict, usual: dict, days: dict[str, dict], items: dict[str, dict]) -> dict:
    """Each pursuit in its own unit, owed ones first: what today asks of it, what is behind, and the choices the panel lists."""
    lane = due_lane(day, lanes.PURSUITS)
    if lane is not None and not lane.available:
        return {'status': 'unavailable', 'reason': lane.reason or None, 'rows': []}
    notes = {row.completes.ref: row.note for row in lane.rows if row.completes} if lane is not None else {}
    rows = []
    for name in listed_pursuits(day):
        config = register.get(name) or {}
        through = writes_through(config)
        choices, choices_error = choices_of(name, config, items.get(name)) if lists_choices(config) else ([], None)
        rows.append(
            {
                'name': name,
                'description': config.get('description') or '',
                'unit': unit_of(config),
                'owed': name in notes,
                'note': notes.get(name),
                **figures(days.get(name)),
                'writes_through': through,
                'usual_minutes': usual.get(name),
                **pursuit_completion(name, config),
                'items': choices,
                'items_error': choices_error,
            }
        )
    return {'status': 'ok', 'reason': None, 'rows': rows}


def checklist_items(habit_id: int | None, checklist: dict | None) -> list[dict]:
    """A habit's checklist items as life counts them today.

    Each item's `key` ticks one set and its `untick_key` takes the latest set
    back. Both carry the same `completes`, naming the habit and the item.
    """
    found = []
    for item in (checklist or {}).get('items') or []:
        if not isinstance(item, dict):
            continue
        item_id = item.get('id')
        keyed = habit_id is not None and isinstance(item_id, int)
        found.append(
            {
                'id': item_id,
                'name': item.get('name') or '',
                'target': item.get('daily_target'),
                'ticks': len(item.get('ticks') or []),
                'done': bool(item.get('done')),
                'key': completion_key(TICK, str(item_id)) if keyed else None,
                'untick_key': completion_key(UNTICK, str(item_id)) if keyed else None,
                'completes': {'kind': str(CompletionKind.HABIT), 'ref': str(habit_id), 'item_id': str(item_id)} if keyed else None,
            }
        )
    return found


def habits_part(day: today.Day, result: sources.Result | None) -> dict:
    """Every habit on life's board, in habit-id order, with its category and its checklist items.

    Whether a habit is still owed, and what picking it completes, come from
    today's habits lane, which the habits meter counts, so the rows and the
    meter agree. A habit that the lane no longer lists is done and carries no
    key, so no pick completes it twice. Where no source reads life it is
    silent, and where the read failed it says why.
    """
    lane = due_lane(day, lanes.HABITS)
    if lane is None or result is None:
        return {'status': 'ok', 'reason': None, 'rows': []}
    if not lane.available:
        return {'status': 'unavailable', 'reason': lane.reason or None, 'rows': []}
    payload, reason = dashboard.life_context({'life': result}, ('habits',))
    if payload is None or reason:
        return {'status': 'unavailable', 'reason': reason or None, 'rows': []}
    owed = {row.completes.ref: row.completes for row in lane.rows if row.completes}
    owed_names = {row.text for row in lane.rows}
    section = payload.get('habits') or {}
    checklists = {checklist.get('habit_id'): checklist for checklist in section.get('checklists') or [] if isinstance(checklist, dict)}
    entries = [(habit, False) for habit in section.get('due_today') or []] + [
        (habit, True) for habit in section.get('completed_today') or []
    ]
    entries.sort(key=lambda entry: dashboard.habit_sort_key(entry[0], entry[1]))
    rows: list[dict] = []
    listed: set[int] = set()
    for habit, completed in entries:
        habit_id = habit.get('habit_id') if completed else habit.get('id')
        if isinstance(habit_id, int):
            if habit_id in listed:
                continue
            listed.add(habit_id)
        else:
            habit_id = None
        name = habit.get('name') or ''
        target = owed.get(str(habit_id)) if habit_id is not None else None
        rows.append(
            {
                'id': habit_id,
                'name': name,
                'category': (habit.get('category') or {}).get('name') or '',
                'done': target is None and name not in owed_names,
                **(completion(target.kind, target.ref) if target else NO_COMPLETION),
                'items': checklist_items(habit_id, checklists.get(habit_id)),
            }
        )
    return {'status': 'ok', 'reason': None, 'rows': rows}


def lane_row(row: Row) -> dict:
    """A due row under the lane contract, with its key and what it completes."""
    target = row.completes
    return {**lanes.row_to_dict(row), **(completion(target.kind, target.ref) if target else NO_COMPLETION)}


def lanes_part(day: today.Day) -> list[dict]:
    """Each register lane and day-shaped lane under the lane contract, cut to the rows that `doit today` lists. `total` counts them all."""
    found = []
    for lane in day.due:
        if lane.name not in LANES:
            continue
        shown = dc.replace(lane, rows=lane.rows[: due_row_cap(lane.name, whole_routine=True)])
        found.append({**lanes.lane_to_dict(shown), 'rows': [lane_row(row) for row in shown.rows]})
    return found


def view(
    *,
    now: dt.datetime,
    register: dict,
    usual: dict,
    day: today.Day,
    selection: dict | None,
    suggestion_reason: str | None,
    items: dict[str, dict],
    life: sources.Result | None,
    problems: list[str],
) -> dict:
    """The desk panel view, from the service's reads.

    `selection` is the draw the suggestion comes from, or None with
    `suggestion_reason` saying why there is none. `items` holds the resolve of
    each write-through pursuit and of the suggested one. `life` is the life
    source's answer, or None where no source reads life.
    """
    totals = quota.to_dict(day.figures or quota.Quota())
    days = {entry['name']: entry for entry in totals['pursuits']}
    if selection is not None and not selection.get('offered'):
        selection, suggestion_reason = None, NOTHING_OFFERED
    return {
        'schema_version': SCHEMA_VERSION,
        'generated_at': lanes.timestamp(now),
        'suggestion': suggestion(selection, register, usual, days, items) if selection is not None else None,
        'suggestion_reason': None if selection is not None else suggestion_reason,
        'answers': answers(),
        'totals': totals,
        'pursuits': pursuits_part(day, register, usual, days, items),
        'habits': habits_part(day, life),
        'lanes': lanes_part(day),
        'problems': problems,
    }
