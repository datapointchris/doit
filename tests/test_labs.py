"""Tests for doit.labs — Lab loading, frontmatter parsing, and schedule status.

An autouse fixture points the deck at a committed fixture directory and the state
at a file that does not exist, so every Lab reads as never-practiced. That
replaces the env-before-import dance the dotfiles version needed when this was a
uv single-file script loaded by path.
"""

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from doit import labs

FIXTURE_DIR = Path(__file__).resolve().parent / 'fixtures'
LABS_FIXTURE = FIXTURE_DIR / 'labs'


@pytest.fixture(autouse=True)
def deck(monkeypatch):
    monkeypatch.setattr(labs, 'LABS_DIR', LABS_FIXTURE)
    monkeypatch.setattr(labs, 'STATE', LABS_FIXTURE / 'does-not-exist-state.json')
    monkeypatch.setattr(labs, 'REGISTRY', FIXTURE_DIR / 'labs-registry.yml')


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


def test_nudge_is_a_single_line(capsys):
    """The nudge runs inside the review nudge, so it holds to one line.

    It used to reuse the browse renderer and print the whole due deck — the bulk
    of what shell startup emitted. `doit labs list` remains the view for the rest.
    """
    assert labs.cmd_nudge() == 0

    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1
    assert '1 due' in lines[0]
    assert 'scheduled-lab' in lines[0]


def test_nudge_samples_and_counts_a_large_deck(tmp_path, monkeypatch, capsys):
    """However much of the deck is due, the nudge names a few and counts the rest."""
    for i in range(labs.NUDGE_SAMPLE + 4):
        (tmp_path / f'lab-{i}.md').write_text(f'---\ntags: []\ncadence: 1w\n---\n\n# Lab {i}\n')
    monkeypatch.setattr(labs, 'LABS_DIR', tmp_path)

    assert labs.cmd_nudge() == 0

    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1
    assert f'{labs.NUDGE_SAMPLE + 4} due' in lines[0]
    assert lines[0].count('lab-') == labs.NUDGE_SAMPLE


def test_nudge_clips_rather_than_wraps_on_a_narrow_pane(tmp_path, monkeypatch, capsys):
    """One line has to mean one line, or the nudge silently grows back."""
    for i in range(6):
        (tmp_path / f'a-very-long-lab-name-{i}.md').write_text(f'---\ntags: []\ncadence: 1w\n---\n\n# Lab {i}\n')
    monkeypatch.setattr(labs, 'LABS_DIR', tmp_path)
    monkeypatch.setenv('COLUMNS', '60')

    assert labs.cmd_nudge() == 0

    plain = re.sub(r'\033\[[0-9;]*m', '', capsys.readouterr().out).rstrip('\n')
    assert len(plain) <= 60, plain


def test_nudge_is_silent_when_nothing_is_due(tmp_path, monkeypatch, capsys):
    """On-demand Labs are never due, so a deck of them nudges not at all."""
    (tmp_path / 'ondemand.md').write_text('---\ntags: []\n---\n\n# On Demand\n')
    monkeypatch.setattr(labs, 'LABS_DIR', tmp_path)

    assert labs.cmd_nudge() == 0
    assert capsys.readouterr().out == ''


def test_strip_frontmatter_removes_block():
    text = '---\ntitle: X\ntags: [a]\n---\n\n# Heading\nbody\n'
    assert labs.strip_frontmatter(text).startswith('# Heading')
    assert 'title: X' not in labs.strip_frontmatter(text)


def test_strip_frontmatter_passthrough_without_block():
    text = '# Heading\nbody\n'
    assert labs.strip_frontmatter(text) == text


def test_parse_frontmatter(tmp_path):
    f = tmp_path / 'x.md'
    f.write_text('---\ntags: [a, b]\ncadence: 1w\n---\n# H\n')
    meta = labs.parse_frontmatter(f)
    assert meta['tags'] == ['a', 'b']
    assert meta['cadence'] == '1w'


def test_parse_frontmatter_none_when_absent(tmp_path):
    f = tmp_path / 'x.md'
    f.write_text('# H\nno frontmatter\n')
    assert labs.parse_frontmatter(f) == {}


def test_first_heading_is_the_title():
    assert labs.first_heading('# The Title\n\ntext\n## Sub\n') == 'The Title'


def test_first_heading_empty_when_none():
    assert labs.first_heading('no heading here\n') == ''


def test_slugify():
    assert labs.slugify('Find Files Fast!') == 'find-files-fast'
    assert labs.slugify('  rg/search  ') == 'rgsearch'


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

    That overload is gone: a Lab is `show <id>`, and an unrecognised word is a
    typo rather than a tool subject to federate on.
    """
    result = CliRunner().invoke(labs.app, ['scheduled-lab'])

    assert result.exit_code == 2
