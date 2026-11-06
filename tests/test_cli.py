"""Tests for the command tree itself, not for what any command does.

What is asserted here is the machine contract: the version line, help on a bare
invocation at every level, and exit 2 for a usage error. Those come from typer
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
from typer.main import get_command
from typer.models import CommandInfo
from typer.testing import CliRunner

import doit
import doit.cli
from doit import __version__
from doit.cli import ROOT_ORDER
from doit.cli import accepted_placements
from doit.cli import app
from doit.service_commands import pursuits as pursuit_commands

runner = CliRunner()
SCHEMAS = Path(__file__).resolve().parents[1] / 'schemas'


@pytest.fixture(autouse=True)
def never_check_for_updates(monkeypatch):
    """Keep the releases API out of the suite.

    pyselfupdate's own gates already decline here — a captured stream is not a
    TTY and a checkout is not an installed tool — but a test that reaches the
    network only when two of someone else's gates both hold is a test that fails
    on a machine, not in CI.
    """
    monkeypatch.setattr('doit.cli.notify', lambda config: None)


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
    """cli-design.md: no args shows help, at every level of the tree."""
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
    commands = get_command(app).commands

    assert set(commands) == set(ROOT_ORDER)
    assert not [name for name, command in commands.items() if not command.rich_help_panel]


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
    with pytest.raises(Exception) as refused:
        get_command(app).main(['next', '--no-input'], prog_name='doit', standalone_mode=False)

    assert accepted_placements(refused.value) == ['doit --no-input next']


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

    assert doit.tool_version() == 'unknown'
