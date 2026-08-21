"""Tests for the command tree itself, not for what any command does.

What is asserted here is the machine contract: the version line, help on a bare
invocation at every level, and exit 2 for a usage error. Those come from typer
rather than from code in this repo, so these are the tests that would catch the
tree being wired up in a way that loses them.
"""

from importlib.metadata import PackageNotFoundError

import pytest
from typer.testing import CliRunner

import doit
from doit import __version__
from doit.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def never_check_for_updates(monkeypatch):
    """Keep the releases API out of the suite.

    pyselfupdate's own gates already decline here — a captured stream is not a
    TTY and a checkout is not an installed tool — but a test that reaches the
    network only when two of someone else's gates both hold is a test that fails
    on a machine, not in CI.
    """
    monkeypatch.setattr('doit.cli.notify', lambda config: None)


@pytest.mark.parametrize('flag', ['-V', '--version'])
def test_version_flag_prints_one_line(flag):
    result = runner.invoke(app, [flag])

    assert result.exit_code == 0
    assert result.output.strip() == f'doit {__version__}'


@pytest.mark.parametrize('argv', [[], ['review'], ['labs']])
def test_every_node_shows_help_bare(argv):
    """cli-design.md: no args shows help, at every level of the tree."""
    result = runner.invoke(app, argv)

    assert 'Usage:' in result.output
    assert 'Options' in result.output


@pytest.mark.parametrize('command', ['next', 'log', 'skip', 'dashboard', 'pursuits', 'review', 'labs'])
def test_root_help_lists_every_command(command):
    result = runner.invoke(app, ['--help'])

    assert result.exit_code == 0
    assert command in result.output


@pytest.mark.parametrize('argv', [['nonsense'], ['review', 'nonsense'], ['labs', 'nonsense']])
def test_unknown_command_exits_2(argv):
    """A caller has to tell "you typed it wrong" from "it ran and failed"."""
    result = runner.invoke(app, argv)

    assert result.exit_code == 2


@pytest.mark.parametrize('argv', [['review', 'done'], ['labs', 'show'], ['labs', 'done']])
def test_missing_required_argument_exits_2(argv):
    """The hand-rolled `needs_argument` helper this replaced returned 2 by hand."""
    result = runner.invoke(app, argv)

    assert result.exit_code == 2


def test_update_is_a_root_command():
    """The fleet updater and the notice both name `doit update`; it has to exist."""
    result = runner.invoke(app, ['--help'])

    assert result.exit_code == 0
    assert 'update' in result.output


def test_update_path_does_not_also_notify(monkeypatch):
    """`update` runs its own check, so notifying would ask the API twice."""
    calls = []
    monkeypatch.setattr('doit.cli.notify', calls.append)
    monkeypatch.setattr('pyselfupdate.typercmd.run_update', lambda *args, **kwargs: None)

    runner.invoke(app, ['update', '--check'])

    assert calls == []


def test_every_other_path_notifies(monkeypatch):
    """The notice is gated inside pyselfupdate, so it must be reached to gate."""
    calls = []
    monkeypatch.setattr('doit.cli.notify', calls.append)

    runner.invoke(app, ['sources', 'list'])

    assert calls == [doit.cli.UPDATE_CONFIG]


def test_the_credential_is_left_to_pyselfupdate():
    """pyselfupdate runs `gh auth token` itself, and `$GITHUB_TOKEN_COMMAND` is
    what redirects or empties it — a lever that belongs to whoever runs doit.

    A helper here would be a second implementation of that, and the reason the
    library grew one is that eleven tools never pasted it.
    """
    assert doit.cli.UPDATE_CONFIG.token == ''
    assert doit.cli.UPDATE_CONFIG.token_func is None


def test_version_is_unknown_without_installed_metadata(monkeypatch):
    """A dev checkout says so rather than inventing a number."""

    def missing(_name):
        raise PackageNotFoundError

    monkeypatch.setattr(doit, 'installed_version', missing)

    assert doit._tool_version() == 'unknown'
