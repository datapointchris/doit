"""Tests for the command tree itself, not for what any command does.

What is asserted here is the machine contract: the version line, help on a bare
invocation at every level, and exit 2 for a usage error. Those come from typer
rather than from code in this repo, so these are the tests that would catch the
tree being wired up in a way that loses them.
"""

import pytest
from typer.testing import CliRunner

from doit import __version__
from doit.cli import app

runner = CliRunner()


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
