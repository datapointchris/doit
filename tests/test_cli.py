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
from jsonschema import Draft7Validator
from typer._click.exceptions import NoSuchOption
from typer.core import TyperCommand
from typer.core import TyperGroup
from typer.main import get_command
from typer.models import CommandInfo
from typer.testing import CliRunner

import doit
from doit import __version__
from doit.cli import ROOT_ORDER
from doit.cli import accepted_placements
from doit.cli import app
from doit.service_commands import pursuits as pursuit_commands

runner = CliRunner()
SCHEMAS = Path(__file__).resolve().parents[1] / 'schemas'


def command_name(command: CommandInfo) -> str:
    """The name typer shows for a registered command: its own, or its function's."""
    assert command.callback is not None
    return command.name or command.callback.__name__


@pytest.mark.parametrize('flag', ['-V', '--version'])
def test_version_flag_prints_one_line(flag):
    result = runner.invoke(app, [flag])

    assert result.exit_code == 0
    assert result.output.strip() == f'doit {__version__}'


@pytest.mark.parametrize('argv', [[], ['review'], ['forecast']])
def test_every_node_shows_help_bare(argv):
    """cli-design.md: a node with no bare default shows help."""
    result = runner.invoke(app, argv)

    assert 'Usage:' in result.output
    assert 'Options' in result.output


def test_the_pursuits_help_lists_every_verb_registered_under_it():
    """Derived from the tree rather than listed, so a verb added next month is
    covered by construction and one that never reaches the help is a failure.

    A hidden command is deliberately absent from the help, so the population is
    the visible ones — `pursuits names` feeds the shell completion and would be
    noise on a screen a person reads.
    """
    registered = {command_name(command) for command in pursuit_commands.app.registered_commands if not command.hidden}
    result = runner.invoke(app, ['pursuits', '--help'])

    assert registered
    assert not [name for name in registered if name not in result.output]


def visible_nodes(command, path=()):
    """Every command a person can reach, with the words that reach it."""
    yield path, command
    for name, child in getattr(command, 'commands', {}).items():
        if not child.hidden:
            yield from visible_nodes(child, (*path, name))


def test_every_root_command_sits_in_a_named_panel_at_its_place():
    """A command left out falls into a panel named Commands, at the foot of the screen."""
    root = get_command(app)
    assert isinstance(root, TyperGroup)
    commands = root.commands

    assert set(commands) == set(ROOT_ORDER)
    assert not [
        name for name, command in commands.items() if not (isinstance(command, TyperCommand | TyperGroup) and command.rich_help_panel)
    ]


def test_every_screen_carries_examples_that_parse():
    """An example is copied whole, so one naming a verb or flag doit lacks is the reader's first error.

    A trailing --help answers only once the line parses, so an unknown command or flag still fails.
    """
    runnable = []
    for path, command in visible_nodes(get_command(app)):
        assert command.epilog, f'doit {" ".join(path)} shows no example'
        runnable += re.findall(r'`(?:eval "\$\()?(doit [^`)"]+)', command.epilog)

    assert runner.invoke(app, ['review', 'done', 'x', '--onn', 'y', '--help']).exit_code == 2, 'a bad flag must fail through --help'
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
# Text that a screen shows and nobody runs: the zsh completion's heading over the
# verb list, and the desk nudge panel's banner over rows that could not be read again.
NOT_A_COMMAND = {('shell.py', ' command'), ('desk_nudge.py', ' unreachable')}


def printed_commands():
    for source in sorted((REPO / 'src' / 'doit').rglob('*.py')):
        for match in PRINTED_IN_SOURCE.finditer(source.read_text()):
            if (source.name, match.group(1)) not in NOT_A_COMMAND:
                yield source.name, match.group(1).split()
    readme = (REPO / 'README.md').read_text()
    for fence in SHELL_FENCE.finditer(readme):
        for match in PRINTED_IN_FENCE.finditer(fence.group(1)):
            yield 'README.md', match.group(1).split()
    for match in PRINTED_IN_SOURCE.finditer(readme):
        yield 'README.md', match.group(1).split()


def test_every_command_it_prints_is_in_the_command_tree():
    """A hint naming a command that was renamed sends its reader to an error.

    The walk stops at a leaf, so the words after it are that command's arguments.
    """
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


def test_config_show_says_where_each_setting_came_from(tmp_path, monkeypatch):
    """A plausible value is the failure this read exists to expose, so the layer sits beside it."""
    (tmp_path / 'doit').mkdir()
    (tmp_path / 'doit' / 'config.toml').write_text('api = "https://doit.example.com"\nissuer = "https://id.example.com"\nclient_id = "c"\n')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    monkeypatch.delenv('XDG_STATE_HOME', raising=False)
    monkeypatch.setenv('DOIT_CLIENT_SECRET', 'never-print-this')

    result = runner.invoke(app, ['config', 'show', '--json'])

    document = json.loads(result.output)
    Draft7Validator(json.loads((SCHEMAS / 'config-show.schema.json').read_text())).validate(document)
    settings = document['settings']
    assert (settings['api']['value'], settings['api']['source']) == ('https://doit.example.com', 'file')
    assert (settings['config_file']['source'], settings['config_file']['from']) == ('environment', '$XDG_CONFIG_HOME')
    assert settings['state_dir']['source'] == 'default'
    assert settings['client_secret']['value'] == 'set'
    assert 'never-print-this' not in result.output


def test_config_show_answers_on_a_machine_with_no_config_and_names_the_login(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

    result = runner.invoke(app, ['config', 'show'])

    assert result.exit_code == 0
    assert 'doit auth login' in result.output


@pytest.mark.parametrize('argv', [['nonsense'], ['review', 'nonsense']])
def test_unknown_command_exits_2(argv):
    """A caller has to tell "you typed it wrong" from "it ran and failed"."""
    result = runner.invoke(app, argv)

    assert result.exit_code == 2


def test_a_root_option_refused_by_a_subcommand_names_the_root_placement():
    """`doit next --no-input` is refused by `next`; the root is where it goes."""
    with pytest.raises(NoSuchOption) as refused:
        get_command(app).main(['next', '--no-input'], prog_name='doit', standalone_mode=False)

    assert accepted_placements(refused.value) == ['doit --no-input next']


def test_a_line_the_go_doit_passed_on_names_its_help_and_runs_nothing():
    """The Go doit hands on a flag it does not take, and Python has no copy of the verb to run it."""
    result = runner.invoke(app, ['update', '--chek'])

    assert result.exit_code == 2
    assert 'doit update --help' in result.output


def test_version_is_unknown_without_installed_metadata(monkeypatch):
    """A dev checkout says so rather than inventing a number."""

    def missing(_name):
        raise PackageNotFoundError

    monkeypatch.setattr(doit, 'installed_version', missing)

    assert doit.tool_version() == 'unknown'
