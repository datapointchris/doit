"""Tests for doit.review — register loading, derived schedule status, and --json.

The register and state paths are module constants read at call time, so an
autouse fixture repoints them rather than the env-before-import dance the
dotfiles version needed when this was a uv single-file script loaded by path.

Tests that need recorded done-dates write a state file under tmp_path, because
overdue days are derived from today and a committed state file would rot.
"""

import json
import re
from datetime import date
from datetime import timedelta
from pathlib import Path

import pytest

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


def test_list_json_emits_every_item_with_its_status(capsys):
    assert review.cmd_list(as_json=True) == 0

    rows = json.loads(capsys.readouterr().out)

    assert len(rows) == 3, '--json emits the whole register, not just what is due'
    assert set(rows[0]) == {'id', 'cadence', 'desc', 'command', 'show', 'last', 'overdue'}


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

    rows = [re.sub(r'\033\[[0-9;]*m', '', line) for line in nudge_lines(capsys)]
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


def test_bare_review_shows_help_rather_than_running(capsys):
    """cli-design.md: no args shows help, at every level of the tree."""
    assert review.main([]) == 0

    out = capsys.readouterr().out
    assert 'doit review due' in out
    assert 'overdue-item' not in out, 'help lists the verbs; it never reads the register'


def test_unknown_verb_is_a_usage_error(capsys):
    """Exit 2 distinguishes "you typed it wrong" from "it ran and failed"."""
    assert review.main(['nonsense']) == 2
    assert 'Unknown' in capsys.readouterr().err


def test_done_without_an_id_is_a_usage_error(capsys):
    assert review.main(['done']) == 2
    assert 'Usage' in capsys.readouterr().err
