"""Tests for doit.tools — the registry and the card shapes drawn from it.

The cards are asserted through the rendered text rather than through a returned
structure, because the rendering is the whole product here: a card that composes
correctly and prints the wrong field is not a passing case.
"""

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from doit import tools
from doit.cli import app

runner = CliRunner()

REGISTRY_YAML = """\
tools:
  ripgrep:
    category: search
    description: Ultra-fast recursive grep
    installed_via: brew
    usage: "rg [pattern] [path]"
    why_use: Respects gitignore and is faster than grep
    notes: Add -uu to reach ignored files
    examples:
      - cmd: "rg -n TODO"
        desc: Line-numbered hits
      - cmd: "rg -t py class"
        desc: Only Python files
    see_also: [fd, grep]
    tags: [cli, search]
    docs_url: https://github.com/BurntSushi/ripgrep
  long-gone:
    category: search
    description: Removed two years ago
    usage: "long-gone --flags"
"""


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    path = tmp_path / 'registry.yml'
    path.write_text(REGISTRY_YAML)
    monkeypatch.setattr(tools, 'REGISTRY', path)

    # The PATH check resolves through `shutil.which`, which reads the real PATH,
    # so without a stub the card's verdict depends on what the machine running
    # the tests happens to have installed. Prepended rather than replacing PATH,
    # so `long-gone` still resolves to nothing.
    stubs = tmp_path / 'bin'
    stubs.mkdir()
    stub = stubs / 'rg'
    stub.write_text('#!/bin/sh\n')
    stub.chmod(0o755)
    monkeypatch.setenv('PATH', f'{stubs}{os.pathsep}{os.environ["PATH"]}')
    return path


def render(capsys, *args, **kwargs) -> str:
    tools.render_tool(*args, **kwargs)
    return capsys.readouterr().out


def test_a_missing_registry_is_empty_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, 'REGISTRY', tmp_path / 'nope.yml')

    assert tools.load_registry() == {}


def test_the_full_card_carries_every_authored_field(capsys):
    out = render(capsys, 'ripgrep', tools.load_registry()['ripgrep'])

    for expected in ('Ultra-fast recursive grep', 'Respects gitignore', 'brew', 'rg [pattern] [path]'):
        assert expected in out
    assert 'Add -uu to reach ignored files' in out
    assert 'fd, grep' in out
    assert 'https://github.com/BurntSushi/ripgrep' in out
    assert 'rg -n TODO' in out and 'rg -t py class' in out, 'every example, not just the first'


def test_the_path_check_reads_the_usage_not_the_key(capsys):
    """`ripgrep` is never on PATH; `rg` is. Checking the key calls the row dead."""
    out = render(capsys, 'ripgrep', tools.load_registry()['ripgrep'])

    assert 'rg is on PATH' in out
    assert 'not on PATH' not in out


def test_a_path_miss_is_reported_without_a_verdict(capsys):
    """Shell functions are not on PATH and are not rot; only the index report can tell."""
    out = render(capsys, 'long-gone', tools.load_registry()['long-gone'])

    assert 'not on PATH' in out
    assert 'doit kit unresolved' in out
    assert 'stale entry' not in out, 'the card cannot tell a function from rot, so it does not claim to'


def test_the_brief_card_keeps_identity_description_and_one_command(capsys):
    out = render(capsys, 'ripgrep', tools.load_registry()['ripgrep'], brief=True)

    assert 'ripgrep' in out
    assert 'Ultra-fast recursive grep' in out
    assert 'rg -n TODO' in out
    assert 'rg -t py class' not in out, 'a brief card carries one example, not the set'
    assert 'brew' not in out, 'browse-time fields are dropped, not shortened'


def test_the_brief_card_clips_rather_than_wraps(capsys):
    long_description = 'x' * 200
    out = render(capsys, 'wide', {'description': long_description}, brief=True)

    for line in out.splitlines():
        assert len(line) <= tools.BRIEF_WIDTH, 'a wrapped row is two rows, which is what brief exists to avoid'
    assert '…' in out


def test_clip_brief_leaves_short_text_alone():
    assert tools.clip_brief('short', 2) == 'short'


def test_heading_off_does_not_repeat_a_name_the_caller_already_ruled(capsys):
    with_heading = render(capsys, 'ripgrep', tools.load_registry()['ripgrep'])
    without = render(capsys, 'ripgrep', tools.load_registry()['ripgrep'], heading=False)

    assert with_heading.count('ripgrep') > without.count('ripgrep')


def test_the_function_card_shows_the_body_because_that_is_the_refresher(capsys):
    tools.render_function('mkd', 'Create a directory and enter it', 'mkdir -p "$1" && cd "$1"')
    out = capsys.readouterr().out

    assert 'shell function' in out
    assert 'mkdir -p' in out


def test_the_function_body_is_dropped_by_brief_rather_than_clipped(capsys):
    tools.render_function('mkd', 'Create a directory and enter it', 'mkdir -p "$1"', brief=True)
    out = capsys.readouterr().out

    assert 'Create a directory' in out
    assert 'mkdir -p' not in out


def test_the_alias_card_shows_what_it_expands_to(capsys):
    tools.render_alias('ll', 'eza -l --git', 'list files')
    out = capsys.readouterr().out

    assert 'list files' in out
    assert 'eza -l --git' in out


def test_the_git_alias_card_names_it_the_way_you_type_it(capsys):
    tools.render_git_alias('co', 'checkout')
    out = capsys.readouterr().out

    assert 'git co' in out
    assert 'git checkout' in out


def test_the_forgit_card_says_it_is_interactive(capsys):
    tools.render_forgit('glo', 'log')
    out = capsys.readouterr().out

    assert 'forgit' in out
    assert 'interactive git log' in out


def test_list_groups_by_category():
    result = runner.invoke(app, ['tools', 'list'])

    assert result.exit_code == 0
    assert 'search' in result.stdout
    assert 'ripgrep' in result.stdout


def test_list_can_be_scoped_to_one_category():
    result = runner.invoke(app, ['tools', 'list', '--category', 'search'])

    assert result.exit_code == 0
    assert 'ripgrep' in result.stdout


def test_an_unknown_category_is_an_error_naming_the_real_ones():
    result = runner.invoke(app, ['tools', 'list', '--category', 'nope'])

    assert result.exit_code == 1


def test_list_json_is_parsable():
    result = runner.invoke(app, ['tools', 'list', '--json'])

    rows = json.loads(result.stdout)
    assert {row['name'] for row in rows} == {'ripgrep', 'long-gone'}


def test_show_points_wider_when_the_name_is_not_in_the_registry():
    result = runner.invoke(app, ['tools', 'show', 'gwip'])

    assert result.exit_code == 1


def test_categories_list_counts_each_group():
    result = runner.invoke(app, ['tools', 'categories', 'list'])

    assert result.exit_code == 0
    assert 'search' in result.stdout


def test_categories_list_json_is_parsable():
    result = runner.invoke(app, ['tools', 'categories', 'list', '--json'])

    assert json.loads(result.stdout) == [{'category': 'search', 'tools': 2}]


def test_bare_categories_prints_help_rather_than_listing():
    """Every node in the tree shows help bare, so walking down never runs something."""
    result = runner.invoke(app, ['tools', 'categories'])

    assert result.exit_code != 0, 'a bare namespace is a usage error, not a read'
    assert 'list' in result.stdout


def test_bare_tools_prints_help_rather_than_listing():
    result = runner.invoke(app, ['tools'])

    assert result.exit_code != 0
    assert 'show' in result.stdout and 'list' in result.stdout


def test_fixture_registry_is_the_one_under_test(registry: Path):
    assert registry == tools.REGISTRY
