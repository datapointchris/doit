"""Tests for doit.review — register loading, derived schedule status, and --json.

The register and state paths are module constants read at call time, so an
autouse fixture repoints them rather than the env-before-import dance the
dotfiles version needed when this was a uv single-file script loaded by path.

Tests that need recorded done-dates write a state file under tmp_path, because
overdue days are derived from today and a committed state file would rot.
"""

import json
from datetime import date
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from doit import machine
from doit import observe
from doit import review

FIXTURE_DIR = Path(__file__).resolve().parent / 'fixtures' / 'review'


@pytest.fixture(autouse=True)
def register(monkeypatch):
    """Point at the committed fixture register and a state file that does not exist."""
    monkeypatch.setattr(review, 'REGISTER', FIXTURE_DIR / 'register.yml')
    monkeypatch.setattr(review, 'STATE', FIXTURE_DIR / 'does-not-exist-state.json')


def write_state(tmp_path, monkeypatch, done_dates: dict) -> None:
    """Point the module at a state file recording the given last-done dates."""
    state_file = tmp_path / 'review-state.json'
    state_file.write_text(json.dumps(done_dates))
    monkeypatch.setattr(review, 'STATE', state_file)


def test_load_items_reads_the_register():
    items = review.load_items()
    assert set(items) == {'never-done', 'overdue-item', 'fresh-item'}
    assert items['overdue-item']['cadence'] == '1w'
    assert items['fresh-item']['show'] == 'echo fresh'


def test_load_items_none_without_a_register(monkeypatch):
    """None distinguishes "no register at all" from "a register with no items"."""
    monkeypatch.setattr(review, 'REGISTER', FIXTURE_DIR / 'nope.yml')
    assert review.load_items() is None


def test_statuses_never_done_reads_as_most_urgent():
    rows = {row['id']: row for row in review.statuses()}
    assert rows['never-done']['overdue'] is None
    assert review.statuses()[0]['overdue'] is None


def test_statuses_orders_most_overdue_first(tmp_path, monkeypatch):
    today = date.today()
    write_state(
        tmp_path,
        monkeypatch,
        {
            'overdue-item': (today - timedelta(days=30)).isoformat(),
            'fresh-item': today.isoformat(),
        },
    )

    rows = review.statuses()

    assert [row['id'] for row in rows] == ['never-done', 'overdue-item', 'fresh-item']
    assert rows[1]['overdue'] == 23  # 30 days since done, 7-day cadence
    assert rows[2]['overdue'] == -28  # done today, 28-day cadence
    assert review.is_due(rows[1]['overdue']) is True
    assert review.is_due(rows[2]['overdue']) is False


def observe_history(tmp_path, monkeypatch, entries: dict) -> None:
    """A shell history recording each command as last run on the given date."""
    lines = [f': {int(datetime.combine(when, datetime.min.time()).timestamp())}:0;{command}' for command, when in entries.items()]
    history = tmp_path / 'history'
    history.write_text('\n'.join(lines) + '\n')
    monkeypatch.setattr(observe, 'HISTORY', history)
    observe.history_entries.cache_clear()


def test_running_the_command_clears_an_item_a_stale_stamp_left_overdue(tmp_path, monkeypatch):
    """Doing the thing is what marks it done. An item whose only record is one
    you have to type reads as weeks overdue hours after it ran."""
    today = date.today()
    write_state(tmp_path, monkeypatch, {'overdue-item': (today - timedelta(days=30)).isoformat()})
    observe_history(tmp_path, monkeypatch, {'echo overdue': today})

    row = {row['id']: row for row in review.statuses()}['overdue-item']

    assert row['last'] == today.isoformat()
    assert row['overdue'] == -7, 'a 7-day cadence, done today'
    assert row['observed'] is True, 'a date you never typed must not read as one you did'


def test_a_stamp_still_wins_when_it_is_the_later_evidence(tmp_path, monkeypatch):
    """Observation may only ever improve a date. A command run long ago cannot
    undo `done`, or adding an observer would lose history."""
    today = date.today()
    write_state(tmp_path, monkeypatch, {'overdue-item': today.isoformat()})
    observe_history(tmp_path, monkeypatch, {'echo overdue': today - timedelta(days=200)})

    row = {row['id']: row for row in review.statuses()}['overdue-item']

    assert row['last'] == today.isoformat()
    assert row['observed'] is False


def test_orphaned_state_ids_names_history_a_rename_stranded(tmp_path, monkeypatch):
    """Renaming an item strands its done-date under the old key, and the item
    then reads as never done — the state that looks most like it needs doing."""
    write_state(tmp_path, monkeypatch, {'overdue-item': '2026-01-01', 'old-name': '2026-01-01'})

    assert review.orphaned_state_ids() == ['old-name']


def test_list_json_emits_every_item_with_its_status(capsys):
    assert review.cmd_list(as_json=True) == 0

    rows = json.loads(capsys.readouterr().out)

    assert len(rows) == 3, '--json emits the whole register, not just what is due'
    assert set(rows[0]) == {
        'id',
        'cadence',
        'cadence_days',
        'desc',
        'command',
        'show',
        'last',
        'overdue',
        'observed',
        'problem',
    }


def test_list_json_is_parsable_without_a_register(monkeypatch, capsys):
    """--json must always emit JSON; the prose hint would break a consumer."""
    monkeypatch.setattr(review, 'REGISTER', FIXTURE_DIR / 'nope.yml')

    assert review.cmd_list(as_json=True) == 0
    assert json.loads(capsys.readouterr().out) == []


def nudge_lines(capsys) -> list[str]:
    """The nudge's non-blank output lines."""
    return [line for line in capsys.readouterr().out.splitlines() if line.strip()]


def test_nudge_is_one_line_per_due_item(capsys):
    """The nudge is a different density from `due`, not just a quieter one.

    A line count is the regression guard that matters here: the nudge used to
    reuse `render_item`, so each item silently cost four lines at shell startup.
    """
    assert review.cmd_nudge() == 0

    lines = nudge_lines(capsys)
    due = [row for row in review.statuses() if review.is_due(row['overdue'])]

    assert len(lines) == len(due) + 2, 'one header, one line per due item, one trailer'
    assert 'never-done' in lines[1]
    assert 'echo overdue' in lines[2], 'the command a row carries is the point of the row'
    assert not any('Has never been marked done' in line for line in lines), (
        'descriptions are browse-time information and stay in `doit review due`'
    )


def test_nudge_caps_the_roster_and_says_so(tmp_path, monkeypatch, capsys):
    """Overflow points at the browse view rather than printing itself."""
    over_cap = review.NUDGE_MAX_ITEMS + 3
    register_file = tmp_path / 'register.yml'
    items = '\n'.join(f'  item-{i}:\n    description: d{i}\n    cadence: 1w' for i in range(over_cap))
    register_file.write_text(f'items:\n{items}\n')
    monkeypatch.setattr(review, 'REGISTER', register_file)

    assert review.cmd_nudge() == 0

    lines = nudge_lines(capsys)
    assert len(lines) == review.NUDGE_MAX_ITEMS + 3, 'header, capped rows, +N more, trailer'
    assert f'+{over_cap - review.NUDGE_MAX_ITEMS} more' in lines[-2]


def test_nudge_clips_long_commands_rather_than_wrapping(tmp_path, monkeypatch, capsys):
    """A wrapped row is two lines, which is how the nudge grew in the first place."""
    register_file = tmp_path / 'register.yml'
    register_file.write_text('items:\n  long-one:\n    description: d\n    cadence: 1w\n    command: ' + 'x' * 200 + '\n')
    monkeypatch.setattr(review, 'REGISTER', register_file)
    monkeypatch.setenv('COLUMNS', '60')

    assert review.cmd_nudge() == 0

    rows = nudge_lines(capsys)
    assert all(len(row) <= 60 for row in rows), rows


def test_nudge_is_silent_when_nothing_is_due(tmp_path, monkeypatch, capsys):
    """A nudge you don't notice on a clear day is one you'll read on a busy one."""
    today = date.today().isoformat()
    write_state(tmp_path, monkeypatch, {'never-done': today, 'overdue-item': today, 'fresh-item': today})

    assert review.cmd_nudge() == 0
    assert capsys.readouterr().out == ''


def test_parse_duration_minutes():
    assert review.parse_duration_minutes('4h') == 240
    assert review.parse_duration_minutes('90m') == 90
    assert review.parse_duration_minutes('4') == 240, 'a bare number means hours'
    assert review.parse_duration_minutes(' 30m ') == 30
    assert review.parse_duration_minutes('soon') is None
    assert review.parse_duration_minutes('') is None


def test_nudge_every_rejects_an_unparsable_duration():
    """A bad value is a usage error, which typer.BadParameter is what earns."""
    result = CliRunner().invoke(review.app, ['nudge-every', 'soon'])

    assert result.exit_code == 2
    assert 'expected a duration' in result.output


REQUIRES_REGISTER = """\
items:
  needs-missing:
    description: About software this box does not have
    cadence: 1w
    requires: long-gone-prerequisite
  needs-present:
    description: About software that is here
    cadence: 1w
    requires: sh
  needs-nothing:
    description: Applies everywhere
    cadence: 1w
"""


@pytest.fixture
def requires_register(tmp_path, monkeypatch):
    path = tmp_path / 'register.yml'
    path.write_text(REQUIRES_REGISTER)
    monkeypatch.setattr(review, 'REGISTER', path)
    return path


def declaring(*names: str) -> machine.Declaration:
    return machine.Declaration(frozenset(names), 'pacman', known=True)


def test_an_item_about_software_this_machine_never_declared_is_hidden(requires_register, monkeypatch):
    """`observe: {scope: machine}` means the other desk cannot clear it either, so
    left in it counts overdue days forever against work nobody can do here."""
    monkeypatch.setattr(machine, 'declaration', lambda: declaring('rg'))

    assert {row['id'] for row in review.statuses()} == {'needs-present', 'needs-nothing'}


def test_a_requirement_on_path_keeps_the_item(requires_register, monkeypatch):
    monkeypatch.setattr(machine, 'declaration', lambda: declaring())

    assert 'needs-present' in {row['id'] for row in review.statuses()}


def test_a_requirement_the_manifest_declares_keeps_the_item(requires_register, monkeypatch):
    """Declared but not installed is still this machine's item — that is the reminder."""
    monkeypatch.setattr(machine, 'declaration', lambda: declaring('long-gone-prerequisite'))

    assert 'needs-missing' in {row['id'] for row in review.statuses()}


def test_an_unreadable_manifest_keeps_every_item(requires_register):
    """A box that cannot say what it declares shows every item, never none."""
    assert len(review.statuses()) == 3


def test_an_item_with_no_requires_is_never_scoped_out(requires_register, monkeypatch):
    monkeypatch.setattr(machine, 'declaration', lambda: declaring())

    assert 'needs-nothing' in {row['id'] for row in review.statuses()}
