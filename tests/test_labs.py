"""Tests for doit.labs — Lab loading, frontmatter parsing, and schedule status.

An autouse fixture points the deck at a committed fixture directory and the state
at a file that does not exist, so every Lab reads as never-practiced.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from doit import labs
from doit import render
from doit import tools

FIXTURE_DIR = Path(__file__).resolve().parent / 'fixtures'
LABS_FIXTURE = FIXTURE_DIR / 'labs'


@pytest.fixture(autouse=True)
def deck(monkeypatch):
    monkeypatch.setattr(labs, 'LABS_DIR', LABS_FIXTURE)
    monkeypatch.setattr(labs, 'STATE', LABS_FIXTURE / 'does-not-exist-state.json')
    monkeypatch.setattr(tools, 'REGISTRY', FIXTURE_DIR / 'labs-registry.yml')


def test_load_labs_reads_frontmatter():
    deck = labs.load_labs()
    assert set(deck) == {'scheduled-lab', 'ondemand-lab'}
    # Title comes from the body's first H1, not a frontmatter field.
    assert deck['scheduled-lab']['title'] == 'A Scheduled Lab'
    assert deck['scheduled-lab']['tags'] == ['fd', 'demo']
    assert deck['scheduled-lab']['cadence'] == '2w'
    # No cadence in the frontmatter → empty string (practice-on-demand).
    assert deck['ondemand-lab']['cadence'] == ''


def test_statuses_scheduled_sorts_before_ondemand():
    rows = labs.statuses()
    assert rows[0]['id'] == 'scheduled-lab'
    assert rows[-1]['id'] == 'ondemand-lab'
    assert rows[-1]['scheduled'] is False


def test_is_due_row():
    rows = {r['id']: r for r in labs.statuses()}
    # Scheduled + never practiced → due; on-demand → never due.
    assert labs.is_due_row(rows['scheduled-lab']) is True
    assert labs.is_due_row(rows['ondemand-lab']) is False


@pytest.fixture
def big_deck(tmp_path, monkeypatch):
    """Seven scheduled Labs, none practiced, so all seven are due."""
    for i in range(7):
        (tmp_path / f'lab-{i}.md').write_text(f'---\ntags: []\ncadence: 1w\n---\n\n# Lab {i}\n')
    monkeypatch.setattr(labs, 'LABS_DIR', tmp_path)
    return tmp_path


def test_due_bounded_renders_that_many_and_counts_the_rest(big_deck, capsys):
    """The bound is what makes this view affordable in a startup nudge.

    Anything unbounded there is a catalog rather than a prompt, and the deck
    comes due in clumps.
    """
    assert labs.cmd_due(limit=3) == 0

    out = capsys.readouterr().out
    assert [line.split()[0] for line in out.splitlines() if line.startswith('  lab-')] == ['lab-0', 'lab-1', 'lab-2']
    assert '+4 more' in out
    assert 'doit labs due' in out


def test_due_unbounded_renders_the_whole_deck(big_deck, capsys):
    """A browse view you asked for carries every row, and says nothing was held back."""
    assert labs.cmd_due() == 0

    out = capsys.readouterr().out
    assert len([line for line in out.splitlines() if line.startswith('  lab-')]) == 7
    assert 'more' not in out


def test_due_says_so_when_nothing_is_due(tmp_path, monkeypatch, capsys):
    """On-demand Labs are never due, so a deck of them has nothing to practice."""
    (tmp_path / 'ondemand.md').write_text('---\ntags: []\n---\n\n# On Demand\n')
    monkeypatch.setattr(labs, 'LABS_DIR', tmp_path)

    assert labs.cmd_due(limit=3) == 0
    assert 'No Labs due' in capsys.readouterr().out


def test_limit_bounds_json_as_well_as_the_render(big_deck, capsys):
    """A flag that silently does nothing under --json is a knob that lies."""
    assert labs.cmd_list(as_json=True, limit=2) == 0

    rows = json.loads(capsys.readouterr().out)
    assert [row['id'] for row in rows] == ['lab-0', 'lab-1']


def test_load_flashcards_all():
    cards = labs.load_flashcards()
    # fd (2 examples) + rg (1); the no-examples tool contributes nothing.
    assert len(cards) == 3
    card = next(c for c in cards if c['answer'] == 'fd -e go')
    assert card['tool'] == 'fd'
    assert card['prompt'] == 'find Go files'


def test_load_flashcards_filter_by_tool():
    cards = labs.load_flashcards('fd')
    assert len(cards) == 2
    assert {c['tool'] for c in cards} == {'fd'}


def test_load_flashcards_filter_by_tag():
    # Both fd and rg carry the `search` tag.
    cards = labs.load_flashcards('search')
    assert {c['tool'] for c in cards} == {'fd', 'rg'}


def test_load_flashcards_unknown_subject_empty():
    assert labs.load_flashcards('nonexistent') == []


def test_flash_refuses_a_run_with_nobody_to_answer_it(capsys):
    """Every card waits on a keystroke. Off a terminal that is a stdin which never
    closes — no output, no exit code. pytest's stdin is exactly that caller."""
    assert labs.cmd_flash() == 1
    assert 'nobody to answer' in capsys.readouterr().err


def test_no_input_refuses_flash_even_on_a_terminal(monkeypatch, capsys):
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    monkeypatch.setattr(render, '_no_input', True)

    assert labs.cmd_flash() == 1
    assert 'nobody to answer' in capsys.readouterr().err


def test_new_writes_straight_into_the_deck(tmp_path, monkeypatch):
    """One directory, not a source and an installed copy.

    The dotfiles version wrote to a repo path and symlinked it into the installed
    dir. The Labs dir is a git checkout now, so authoring and reading are the same
    file — a Lab created here is immediately loadable.
    """
    monkeypatch.setattr(labs, 'LABS_DIR', tmp_path)
    monkeypatch.setattr(labs, 'open_editor', lambda path: None)

    assert labs.cmd_new('Find Files Fast') == 0

    created = tmp_path / 'find-files-fast.md'
    assert created.exists() and not created.is_symlink()
    assert 'find-files-fast' in labs.load_labs()


def test_a_bare_argument_is_no_longer_a_lab_id():
    """`menu labs <arg>` meant "a Lab id, or failing that a tool name".

    That overload is gone: a Lab is `show <id>`, and an unrecognized word is a
    typo rather than a tool subject to federate on.
    """
    result = CliRunner().invoke(labs.app, ['scheduled-lab'])

    assert result.exit_code == 2
