"""Tests for doit.labs — Lab loading, the deck listing, flashcards and scaffolding.

An autouse fixture points the deck at a committed fixture directory.
"""

import json
from pathlib import Path

import pytest

from doit import labs
from doit import render
from doit import tools

FIXTURE_DIR = Path(__file__).resolve().parent / 'fixtures'
LABS_FIXTURE = FIXTURE_DIR / 'labs'


@pytest.fixture(autouse=True)
def deck(monkeypatch):
    monkeypatch.setattr(labs, 'LABS_DIR', LABS_FIXTURE)
    monkeypatch.setattr(tools, 'REGISTRY', FIXTURE_DIR / 'labs-registry.yml')


def test_load_labs_reads_frontmatter():
    deck = labs.load_labs()
    assert set(deck) == {'tagged-lab', 'plain-lab'}
    # Title comes from the body's first H1, not a frontmatter field.
    assert deck['tagged-lab']['title'] == 'A Tagged Lab'
    assert deck['tagged-lab']['tags'] == ['fd', 'demo']


@pytest.fixture
def big_deck(tmp_path, monkeypatch):
    """Seven Labs, more than a bounded listing shows."""
    for i in range(7):
        (tmp_path / f'lab-{i}.md').write_text(f'---\ntags: []\n---\n\n# Lab {i}\n')
    monkeypatch.setattr(labs, 'LABS_DIR', tmp_path)
    return tmp_path


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
    monkeypatch.setattr(render, 'no_input', True)

    assert labs.cmd_flash() == 1
    assert 'nobody to answer' in capsys.readouterr().err


def test_new_writes_straight_into_the_deck(tmp_path, monkeypatch):
    """One directory, not a source and an installed copy, so a Lab created here is immediately loadable."""
    monkeypatch.setattr(labs, 'LABS_DIR', tmp_path)
    monkeypatch.setattr(labs, 'open_in_editor', lambda path: None)

    assert labs.cmd_new('Find Files Fast') == 0

    created = tmp_path / 'find-files-fast.md'
    assert created.exists() and not created.is_symlink()
    assert 'find-files-fast' in labs.load_labs()
