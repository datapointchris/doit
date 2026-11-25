"""The temporal half of doit: a terminal-native cadence register.

Answers "what should I revisit or run on a cadence?". Invoked as `doit review`.

Two records, by design, both kept by doit's service:
  - a register            declarative config you hand-edit with `doit review
                          edit` (description, cadence, optional command). It is
                          kept as written, so your comments and layout are never
                          disturbed.
  - the done dates        the days `done` recorded per item, less any `undo` took
                          back.

The due date is derived, never stored: next_due = last_done + cadence. A new item
with no recorded done simply shows as never done. Storing only last_done means
there is no date to keep in sync and nothing to drift.

The register holds two blocks: `items` for maintenance and `practice` for what
you work through on a schedule. howto carries the Labs. When you practice one is
yours, so its schedule is kept here.
"""

import datetime as dt
import re

from doit.cadence import is_due
from doit.cadence import overdue_days
from doit.cadence import parse_cadence
from doit.cadence import status_label

TEMPLATE = """\
# Terminal-native review / cadence register, read by `doit review`.
#
# This is declarative config — edit it freely with `doit review edit`. The done
# dates are recorded by `doit review done <id>`, and on no other evidence.
#
#   description  what to revisit or run
#   cadence      how often: <n>d / <n>w / <n>mo / <n>y  (e.g. 2w, 1mo)
#   command      optional; shown so you know what to run (not auto-run)
#   show         optional command the nudge runs to print a card inline, where
#                the item's name alone does not say enough to act on
#
# `practice:` holds what you work through on a schedule, with the same fields.
# For a howto Lab, key it by the Lab's id and set `command: howto labs show <id>`.
# An id names one item across both blocks.

items:
  prune-docker:
    description: Remove stopped containers and dangling images
    cadence: 1mo
    command: docker system prune

practice: {}
"""

# A nudge you have to scroll is a nudge you learn to ignore, so the roster is
# bounded and the overflow points at the browse view instead of printing itself.
NUDGE_MAX_ITEMS = 8

# How long the nudge waits between showings where nobody set an interval. Four
# hours means at most twice a working day, which is the point of a nudge rather
# than a banner. The shell gate waits this long too, until a nudge has answered.
DEFAULT_NUDGE_MINUTES = 240


# Fields a register item may declare: the ones the TEMPLATE header documents.
# Anything else is a typo, and a typo here fails silently. A misspelled
# `cadence:` is ignored, so the item loses its schedule with nothing printed.
ITEM_FIELDS = {'description', 'cadence', 'command', 'show'}

# The register's blocks: maintenance, then the Labs practiced on a schedule.
BLOCKS = ('items', 'practice')


class RegisterError(ValueError):
    """A review register that cannot be read as written.

    A ValueError, so the dashboard and `doit today` degrade the review lane to
    this message rather than dropping it or failing the whole view.
    """


def register_from_document(data: dict) -> dict[str, dict]:
    """A parsed register's blocks, each item by id.

    Raises RegisterError on an unknown block, an item that is not a mapping or
    declares a field outside ITEM_FIELDS, or an id in both blocks. The done
    dates are keyed by id alone, so one id in two blocks would share a stamp.
    """
    if not isinstance(data, dict):
        raise RegisterError(f'must be a mapping of blocks, not {type(data).__name__}')
    # A misspelled block is ignored by every reader, so its items silently never
    # come due. Refused here, it is one line naming the blocks that exist.
    unknown_blocks = set(data) - set(BLOCKS)
    if unknown_blocks:
        raise RegisterError(f'unknown block(s) {", ".join(sorted(unknown_blocks))}. Accepted: {", ".join(BLOCKS)}')
    register = {block: data.get(block) or {} for block in BLOCKS}
    owner: dict[str, str] = {}
    for block, items in register.items():
        for item_id, meta in items.items():
            if item_id in owner:
                raise RegisterError(f'{item_id}: in both {owner[item_id]} and {block}; an id names one item')
            owner[item_id] = block
            check_item(item_id, meta)
    return register


def check_item(item_id: str, meta: object) -> None:
    if meta is None:
        return
    if not isinstance(meta, dict):
        raise RegisterError(f'{item_id}: must be a mapping of fields, not {type(meta).__name__}')
    unknown = set(meta) - ITEM_FIELDS
    if unknown:
        # The accepted set is named so a reader can see `cadence` beside a
        # misspelled `cadance` and fix it without leaving the terminal.
        raise RegisterError(f'{item_id}: unknown field(s) {", ".join(sorted(unknown))}. Accepted: {", ".join(sorted(ITEM_FIELDS))}')


def statuses_of(register: dict[str, dict], state: dict[str, str], today: dt.date, block: str | None = None) -> list[dict]:
    """Every item in one block, or in every block, with its derived overdue days, most urgent first.

    ``state`` is each item's last done day by id. overdue is the number of days
    past due (negative = not yet due); None means the item has never been done,
    which sorts to the very top as most urgent.
    """
    rows = []
    for name in [block] if block else list(BLOCKS):
        for item_id, meta in register.get(name, {}).items():
            meta = meta or {}
            last = state.get(item_id)
            overdue = overdue_days(last, meta.get('cadence', ''), today)
            rows.append(
                {
                    'id': item_id,
                    'block': name,
                    'cadence': str(meta.get('cadence', '')),
                    'cadence_days': parse_cadence(meta.get('cadence', '')),
                    'desc': meta.get('description', '') or '',
                    'command': meta.get('command', '') or '',
                    'show': meta.get('show', '') or '',
                    'last': last,
                    'overdue': overdue,
                    'due': is_due(overdue),
                }
            )
    rows.sort(key=lambda r: float('inf') if r['overdue'] is None else r['overdue'], reverse=True)
    return rows


# Each block's heading in the browse views. Practice prints after maintenance,
# because a deck of never-practiced Labs would otherwise push it off the screen.
BLOCK_TITLES = {'items': 'Review', 'practice': 'Practice'}


def with_status(row: dict) -> dict:
    """An entry as the service answers it: its derived fields, and `status`, the label every screen prints for it."""
    return {**row, 'status': status_label(row['overdue'])}


def block_titles() -> list[dict]:
    """Each block with its heading, maintenance first, as the browse views print them."""
    return [{'name': block, 'title': BLOCK_TITLES[block]} for block in BLOCKS]


def nudge_due(held: dict, now: dt.datetime) -> bool:
    """Whether the interval has passed since the nudge last showed on any machine."""
    last = held.get('last_nudged_at')
    if not last:
        return True
    interval = dt.timedelta(minutes=held.get('interval_minutes') or DEFAULT_NUDGE_MINUTES)
    return now - dt.datetime.fromisoformat(last.replace('Z', '+00:00')) >= interval


def parse_duration_minutes(dur: str) -> int | None:
    """'4h' → 240, '90m' → 90, bare '4' → hours. None if unparsable."""
    match = re.fullmatch(r'(\d+)\s*([hm]?)', dur.strip())
    if not match:
        return None
    count = int(match.group(1))
    unit = match.group(2) or 'h'  # a bare number means hours
    return count * 60 if unit == 'h' else count
