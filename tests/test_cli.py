"""Tests for the command tree itself, not for what any command does.

What is asserted here is the machine contract: the version line, help on a bare
node with no default, and exit 2 for a usage error. Those come from typer
rather than from code in this repo, so these are the tests that would catch the
tree being wired up in a way that loses them.
"""

import json
import re
import shlex
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest
from typer._click.exceptions import NoSuchOption
from typer.core import TyperCommand
from typer.core import TyperGroup
from typer.main import get_command
from typer.testing import CliRunner

import doit
from doit import __version__
from doit.cli import GO_COMMANDS_VARIABLE
from doit.cli import ROOT_ORDER
from doit.cli import accepted_placements
from doit.cli import app
from doit.cli import list_go_commands

runner = CliRunner()


@pytest.mark.parametrize('flag', ['-V', '--version'])
def test_version_flag_prints_one_line(flag):
    result = runner.invoke(app, [flag])

    assert result.exit_code == 0
    assert result.output.strip() == f'doit {__version__}'


def test_the_bare_root_shows_help():
    """cli-design.md: a node with no bare default shows help."""
    result = runner.invoke(app, [])

    assert 'Usage:' in result.output
    assert 'Options' in result.output


def visible_nodes(command, path=()):
    """Every command a person can reach, with the words that reach it."""
    yield path, command
    for name, child in getattr(command, 'commands', {}).items():
        if not child.hidden:
            yield from visible_nodes(child, (*path, name))


def test_every_root_command_sits_in_a_named_panel_at_its_place(monkeypatch):
    """A command left out falls into a panel named Commands, at the foot of the screen.

    The Go doit's commands are listed as it hands them on, and ROOT_ORDER places
    those that lead a panel above Python's own.
    """
    python_root = get_command(app)
    assert isinstance(python_root, TyperGroup)
    own = set(python_root.commands)
    monkeypatch.setattr(app, 'registered_commands', list(app.registered_commands))
    list_go_commands({GO_COMMANDS_VARIABLE: HANDED_GO_TREE.read_text()})
    root = get_command(app)
    assert isinstance(root, TyperGroup)
    commands = root.commands

    assert own <= set(ROOT_ORDER) <= set(commands)
    assert not [
        name for name, command in commands.items() if not (isinstance(command, TyperCommand | TyperGroup) and command.rich_help_panel)
    ]


def test_every_screen_carries_examples_that_parse(monkeypatch):
    """An example is copied whole, so one naming a verb or flag doit lacks is the reader's first error.

    A trailing --help answers only once the line parses, so an unknown command or flag still fails.
    The Go doit's commands are listed as it hands them on, so an example naming one parses.
    """
    runnable = []
    for path, command in visible_nodes(get_command(app)):
        assert command.epilog, f'doit {" ".join(path)} shows no example'
        runnable += re.findall(r'`(?:eval "\$\()?(doit [^`)"]+)', command.epilog)
    monkeypatch.setattr(app, 'registered_commands', list(app.registered_commands))
    list_go_commands({GO_COMMANDS_VARIABLE: HANDED_GO_TREE.read_text()})

    assert runner.invoke(app, ['tui', '--tabb', '--help']).exit_code == 2, 'a bad flag must fail through --help'
    for example in runnable:
        result = runner.invoke(app, [*shlex.split(example)[1:], '--help'])
        assert result.exit_code == 0, f'{example}: {result.output}'


# A command printed for a person to run: after rich markup, in a code span or in
# single quotes in the source, or on a line of a shell fence in the README. A
# word running into a `/` or a `.` is a path argument, not a subcommand.
REPO = Path(__file__).resolve().parents[1]
WORDS = r'((?: [a-z][\w-]*(?![\w/.-]))+)'
PRINTED_IN_SOURCE = re.compile(rf"(?:\]|`|')doit{WORDS}")
SHELL_FENCE = re.compile(r'^```(?:bash|sh)\n(.*?)^```', re.MULTILINE | re.DOTALL)
PRINTED_IN_FENCE = re.compile(rf'^doit{WORDS}', re.MULTILINE)
HANDED_GO_TREE = REPO / 'cli' / 'cmd' / 'doit' / 'testdata' / 'tree-handed-to-doit-python.json'


def printed_commands():
    for source in sorted((REPO / 'src' / 'doit').rglob('*.py')):
        for match in PRINTED_IN_SOURCE.finditer(source.read_text()):
            yield source.name, match.group(1).split()
    readme = (REPO / 'README.md').read_text()
    for fence in SHELL_FENCE.finditer(readme):
        for match in PRINTED_IN_FENCE.finditer(fence.group(1)):
            yield 'README.md', match.group(1).split()
    for match in PRINTED_IN_SOURCE.finditer(readme):
        yield 'README.md', match.group(1).split()


def test_every_command_it_prints_is_in_the_command_tree(monkeypatch):
    """A hint naming a command that was renamed sends its reader to an error.

    The walk stops at a leaf, so the words after it are that command's arguments.
    The Go doit's commands are listed as it hands them to doit-python.
    """
    monkeypatch.setattr(app, 'registered_commands', list(app.registered_commands))
    list_go_commands({GO_COMMANDS_VARIABLE: HANDED_GO_TREE.read_text()})
    tree = get_command(app)
    missing = []
    walked = 0
    for source, words in printed_commands():
        walked += 1
        command = tree
        for word in words:
            subcommands = getattr(command, 'commands', None)
            if subcommands is None:
                break
            if word not in subcommands:
                missing.append(f'{source}: doit {" ".join(words)}')
                break
            command = subcommands[word]

    assert walked > 20
    assert missing == []


def test_unknown_command_exits_2():
    """A caller has to tell "you typed it wrong" from "it ran and failed"."""
    result = runner.invoke(app, ['nonsense'])

    assert result.exit_code == 2


def test_a_root_option_refused_by_a_subcommand_names_the_root_placement():
    """`doit-python tui --no-input` is refused by `tui`; the root is where it goes."""
    with pytest.raises(NoSuchOption) as refused:
        get_command(app).main(['tui', '--no-input'], prog_name='doit', standalone_mode=False)

    assert accepted_placements(refused.value) == ['doit --no-input tui']


def test_a_command_the_go_doit_hands_on_is_listed_and_runs_nothing(monkeypatch):
    """The Go doit hands its tree on as it execs `doit-python tui`, and Python has no copy of those verbs to run."""
    monkeypatch.setattr(app, 'registered_commands', list(app.registered_commands))
    handed = [{'path': ['update'], 'summary': 'Update doit', 'group': 'This machine'}]
    list_go_commands({GO_COMMANDS_VARIABLE: json.dumps(handed)})

    tree = get_command(app)
    assert isinstance(tree, TyperGroup)
    assert 'update' in tree.commands
    result = runner.invoke(app, ['update', '--chek'])
    assert result.exit_code == 2
    assert 'doit update --help' in result.output


def test_version_is_unknown_without_installed_metadata(monkeypatch):
    """A dev checkout says so rather than inventing a number."""

    def missing(_name):
        raise PackageNotFoundError

    monkeypatch.setattr(doit, 'installed_version', missing)

    assert doit.tool_version() == 'unknown'
