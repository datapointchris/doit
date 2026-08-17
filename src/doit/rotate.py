"""Which forgotten thing to surface next, and the cursor that stops it repeating.

Deciding what to put in front of you is an attention question, which is the one
thing doit exists to answer. It sat in a display tool until now only by history.

**One cursor per lens, never one across everything.** A single rotation makes a
row's recurrence a function of the whole population rather than of what that row
is worth: a weekly reminder over 127 registry tools surfaces any given tool once
every two and a half years, and adding a lens of fifty rows tomorrow pushes every
existing one further out again. Per lens, recurrence is cadence times that lens's
size and nothing else, so a drill over 29 forgit shortcuts and a reminder over the
whole registry can each be set to what that set is worth. The cadence itself is
not here — it is the register item that runs this, one per lens.

**Coldness is only asked of the lenses history can see.** A tmux binding is
pressed and a workflow card is read, so neither reaches a shell prompt;
:mod:`doit.usage` excludes them deliberately, and filtering those lenses on
coldness would find every row equally cold and be no filter at all. They rotate
over their whole set instead, which is the right behaviour for a drill anyway.

The cursor is a ``{typed: last_shown}`` map per lens, in the shape
:mod:`doit.state` already writes and :mod:`doit.observe` already reads — so a
register item observes a lens through ``newest-date-in`` with no new observer.
It is deliberately not merged into ``review-state.json``: removing a register
item orphans a stamp and has to warn, while dropping a tool from the catalogue
should drop its cursor entry in silence. One operation, opposite correct
behaviours.
"""

from datetime import date
from pathlib import Path

from doit import usage
from doit.index import Entry
from doit.index import build_index
from doit.paths import xdg_state_home
from doit.state import load_state
from doit.state import save_state

# One file per lens rather than one file keyed by lens. `newest-date-in` answers
# "when did this last happen at all" over a whole file, so a shared file would
# report every lens as freshly done the moment any one of them was.
ROTATION_DIR = xdg_state_home() / 'doit' / 'rotation'

# Every lens a rotation can draw from. The measurable ones are filtered to what
# has gone cold; the rest rotate whole.
LENSES = ('tool', 'func', 'alias', 'git', 'forgit', 'workflow', 'tmux', 'zsh')


def cursor_path(lens: str) -> Path:
    return ROTATION_DIR / f'{lens}.json'


def candidates(lens: str, entries: list[Entry] | None = None, rows: list[usage.Row] | None = None) -> list[Entry]:
    """The rows of one lens worth putting in front of you.

    A measurable lens is narrowed to what :func:`usage.unused` calls the tail,
    because something you reached for last week is not forgotten and spending the
    rotation on it wastes the slot. An unmeasurable one is not narrowed at all.
    """
    entries = build_index() if entries is None else entries
    mine = [entry for entry in entries if entry.source == lens]
    if lens not in usage.MEASURABLE:
        return mine
    rows = usage.measure(entries) if rows is None else rows
    cold = {row.typed for row in usage.unused(rows)}
    return [entry for entry in mine if usage.typed_form(entry) in cold]


def next_up(lens: str, entries: list[Entry] | None = None, rows: list[usage.Row] | None = None) -> Entry | None:
    """The candidate shown longest ago, or never.

    Never-shown leads, and within it the name decides, so the order is stable
    across runs and across machines. Nothing here reads the clock — the cursor
    holds the only dates involved, and a candidate absent from it has no date at
    all rather than an old one.
    """
    pool = candidates(lens, entries, rows)
    if not pool:
        return None
    shown = load_state(cursor_path(lens))
    return min(pool, key=lambda entry: (shown.get(usage.typed_form(entry) or entry.name, ''), entry.name))


def record(lens: str, entry: Entry, today: date | None = None) -> None:
    """Stamp this row as shown, advancing the lens's rotation by one.

    Keyed on the typed form so the cursor survives a row being recatalogued —
    a registry key can change while what you type does not.
    """
    path = cursor_path(lens)
    shown = load_state(path)
    shown[usage.typed_form(entry) or entry.name] = (today or date.today()).isoformat()
    save_state(path, shown)
