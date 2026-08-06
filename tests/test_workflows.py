"""Tests for doit.workflows — card loading, listing, and authoring.

Rendering is bat's and is not asserted here; what matters is which file gets
picked and where a new one lands.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from doit import workflows
from doit.cli import app as cli_app

FIXTURE_DIR = Path(__file__).resolve().parent / 'fixtures' / 'workflows'


@pytest.fixture(autouse=True)
def cards(monkeypatch):
    monkeypatch.setattr(workflows, 'WORKFLOWS_DIR', FIXTURE_DIR)


def test_load_cards_reads_title_and_tags():
    loaded = workflows.load_cards()

    assert set(loaded) == {'tmux-commands', 'no-frontmatter'}
    assert loaded['tmux-commands']['title'] == 'tmux Commands'
    assert loaded['tmux-commands']['tags'] == ['tmux', 'terminal']


def test_a_card_without_frontmatter_still_loads():
    """Cards predate the tags convention, so the block is optional."""
    card = workflows.load_cards()['no-frontmatter']

    assert card['title'] == 'A Card Without Frontmatter'
    assert card['tags'] == []


def test_list_names_every_card(capsys):
    assert workflows.cmd_list() == 0

    out = capsys.readouterr().out
    assert 'tmux-commands' in out
    assert 'tmux Commands' in out


def test_show_accepts_the_filename_a_completion_offers(monkeypatch):
    """`.md` is stripped, because tab-completion offers the filename."""
    rendered = []
    monkeypatch.setattr(workflows, 'render_body', lambda path, **kwargs: rendered.append(path))

    assert workflows.cmd_show('tmux-commands.md') == 0

    assert rendered == [FIXTURE_DIR / 'tmux-commands.md']


def test_an_unknown_card_points_at_the_federated_search(capsys):
    assert workflows.cmd_show('nope') == 1

    err = capsys.readouterr().err
    assert 'doit workflows list' in err
    assert 'doit find nope' in err


def test_new_writes_straight_into_the_cards_directory(tmp_path, monkeypatch):
    """The bash version could not do this.

    It had no idea where its own content lived, so it found an existing symlink
    in the installed directory, resolved it backwards to the dotfiles source,
    wrote there, then symlinked forward again. The cards directory is a git
    checkout now, so authoring writes the file the reader reads.
    """
    monkeypatch.setattr(workflows, 'WORKFLOWS_DIR', tmp_path)
    monkeypatch.setattr(workflows, 'open_editor', lambda path: None)

    assert workflows.cmd_new('Review And Merge A PR') == 0

    created = tmp_path / 'review-and-merge-a-pr.md'
    assert created.exists() and not created.is_symlink()
    assert 'review-and-merge-a-pr' in workflows.load_cards()


def test_new_refuses_to_overwrite(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(workflows, 'WORKFLOWS_DIR', tmp_path)
    monkeypatch.setattr(workflows, 'open_editor', lambda path: None)
    (tmp_path / 'already-here.md').write_text('# Already Here\n')

    assert workflows.cmd_new('Already Here') == 1
    assert 'already exists' in capsys.readouterr().err


def test_random_renders_one_of_the_deck(monkeypatch):
    rendered = []
    monkeypatch.setattr(workflows, 'render_body', lambda path, **kwargs: rendered.append(path))

    assert workflows.cmd_random() == 0

    assert rendered[0].parent == FIXTURE_DIR


def test_an_empty_directory_is_not_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(workflows, 'WORKFLOWS_DIR', tmp_path)

    assert workflows.cmd_list() == 0
    assert 'doit workflows new' in capsys.readouterr().out


def test_search_is_not_a_verb_here():
    """`workflows <term>` and `workflows search` collapsed into `doit find`."""
    result = CliRunner().invoke(cli_app, ['workflows', 'search', 'git'])

    assert result.exit_code == 2
