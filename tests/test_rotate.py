"""Tests for doit.rotate — which forgotten row a lens surfaces next.

Two things carry the module: the candidate set, which differs between a lens
history can see and one it cannot, and the cursor, which is what stops the same
row coming round twice while others have never been shown at all. Everything
below is one of those.

No test reads the real state dir; each points ROTATION_DIR at a tmp_path.
"""

from datetime import date

import pytest

from doit import rotate
from doit import usage
from doit.index import Entry
from doit.observe import Invocation


@pytest.fixture(autouse=True)
def cursors(tmp_path, monkeypatch):
    monkeypatch.setattr(rotate, 'ROTATION_DIR', tmp_path / 'rotation')


def tool(name: str, invocation: str = '') -> Entry:
    return Entry(source='tool', name=name, invocation=invocation or name)


def binding(name: str) -> Entry:
    return Entry(source='tmux', name=name, invocation=name)


def ran(command: str, when: str) -> Invocation:
    return Invocation(when, 'archlinux', command)


TODAY = date(2026, 8, 17)
RECENT = '2026-08-16'
LONG_AGO = '2020-01-01'


def test_a_lens_history_can_see_is_narrowed_to_what_has_gone_cold():
    """Something you ran yesterday is not forgotten, and the slot is worth more."""
    entries = [tool('warm'), tool('cold')]
    rows = usage.measure(entries, (ran('warm', RECENT), ran('cold', LONG_AGO)))

    names = [entry.name for entry in rotate.candidates('tool', entries, rows)]

    assert names == ['cold']


def test_a_lens_history_cannot_see_rotates_over_all_of_it():
    """A tmux binding is pressed, never typed, so every row would read as cold."""
    entries = [binding('split-window'), binding('choose-tree')]

    names = sorted(entry.name for entry in rotate.candidates('tmux', entries, []))

    assert names == ['choose-tree', 'split-window']


def test_never_shown_leads_and_the_name_breaks_the_tie():
    """Stable across runs and machines, because nothing here reads a clock."""
    entries = [binding('zulu'), binding('alpha')]

    assert rotate.next_up('tmux', entries, []).name == 'alpha'


def test_the_row_shown_longest_ago_comes_round_before_a_recent_one():
    entries = [binding('alpha'), binding('zulu')]
    rotate.record('tmux', entries[0], date(2026, 8, 16))
    rotate.record('tmux', entries[1], date(2020, 1, 1))

    assert rotate.next_up('tmux', entries, []).name == 'zulu'


def test_a_lens_with_no_candidates_surfaces_nothing_rather_than_failing():
    assert rotate.next_up('tmux', [], []) is None


def test_the_cursor_is_keyed_on_what_you_type_not_on_the_catalogue_name():
    """A registry key can be recatalogued while the command stays the same."""
    rotate.record('tool', tool('ripgrep', 'rg [pattern]'), TODAY)

    assert rotate.cursor_path('tool').exists()
    assert 'rg' in rotate.cursor_path('tool').read_text()
    assert 'ripgrep' not in rotate.cursor_path('tool').read_text()


def test_each_lens_advances_alone():
    """A shared cursor would report every lens done the moment any one was.

    `newest-date-in` answers over a whole file, so this is what lets one register
    item per lens observe its own rotation.
    """
    rotate.record('tool', tool('rg'), TODAY)

    assert rotate.cursor_path('tool').exists()
    assert not rotate.cursor_path('forgit').exists()
