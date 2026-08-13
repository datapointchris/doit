"""Tests for doit.index — the federated index and the rot report.

The lens parsers are covered against small fixtures rather than the real
collections, which change under you. What is asserted hardest is the invocation,
because a registry key is a package name as often as a command and a row keyed
by name names something you cannot type.
"""

import os
from pathlib import Path

import pytest

from doit import index
from doit import skills
from doit import tools

FIXTURE_DIR = Path(__file__).resolve().parent / 'fixtures'

REGISTRY_YAML = """\
tools:
  ripgrep:
    category: search
    description: Ultra-fast recursive grep
    usage: "rg [pattern] [path]"
    tags: [cli, search]
  git-forgit-log:
    category: version-control
    description: Interactive git log viewer
    usage: "git forgit log"
    tags: [git]
  forge:
    category: custom-tools
    description: The fleet orchestrator
    usage: "forge [command]"
    tags: [custom]
  long-gone:
    category: search
    description: Removed two years ago
    usage: "long-gone --flags"
    tags: []
"""


# The leading greeting is deliberate: a zshrc that prints on startup is ordinary,
# and the parser has to survive one. So are the range and the key-to-key alias,
# which are the two shapes `bindkey` emits that name no widget at all.
LIVE_BINDKEYS = """\
 ✓ zsh loaded
#keymap main
"^A"-"^C" self-insert
"^G" gwip
"^H" fzf-man-widget
"^I" expand-or-complete
"^R" atuin-search-viins
"^S" self-insert
"gUU" "gUgU"
#keymap emacs
"^A" beginning-of-line
"^T" fzf-file-widget
#keymap viins
"^A"-"^C" self-insert
"^G" gwip
"^H" fzf-man-widget
"^I" expand-or-complete
"^R" atuin-search-viins
"^S" self-insert
"gUU" "gUgU"
#keymap vicmd
"^R" fzf-history-widget
"j" down-line-or-history
"""

STOCK_BINDKEYS = """\
#keymap main
"^A" beginning-of-line
#keymap emacs
"^A" beginning-of-line
#keymap viins
"^A"-"^C" self-insert
"^H" vi-backward-delete-char
"^I" expand-or-complete
"^R" redisplay
#keymap vicmd
"^R" redo
"j" down-line-or-history
"""


@pytest.fixture(autouse=True)
def collections(tmp_path, monkeypatch):
    """Point every lens at a fixture, so no test reads the real machine."""
    monkeypatch.setattr(index, 'zsh_bindkeys', lambda interactive: LIVE_BINDKEYS if interactive else STOCK_BINDKEYS)
    registry = tmp_path / 'registry.yml'
    registry.write_text(REGISTRY_YAML)
    monkeypatch.setattr(tools, 'REGISTRY', registry)
    monkeypatch.setattr(index, 'WORKFLOWS_DIR', FIXTURE_DIR / 'workflows')
    monkeypatch.setattr(skills, 'SKILLS_DIR', tmp_path / 'no-skills')
    monkeypatch.setattr(index, 'FORGIT_PLUGIN', tmp_path / 'no-forgit')
    monkeypatch.setattr(index, 'SHELL_DIR', tmp_path / 'shell')
    (tmp_path / 'shell').mkdir()
    (tmp_path / 'shell' / 'functions.sh').write_text('#@gwip\n#--> commit work in progress\ngwip() {\n  :\n}\n')
    (tmp_path / 'shell' / 'aliases.sh').write_text('# list files\nalias ll="eza -l"\n')

    # Pointing the lenses at fixtures was not enough: `unresolved` resolves through
    # `shutil.which`, which reads the real PATH, so the rot report depended on what
    # the machine running the tests happened to have installed. CI has none of it,
    # and called a fixture row dead. Prepended rather than replacing PATH, so
    # `long-gone` still resolves to nothing and git stays runnable.
    stubs = tmp_path / 'bin'
    stubs.mkdir()
    for command in ('rg', 'forge'):
        stub = stubs / command
        stub.write_text('#!/bin/sh\n')
        stub.chmod(0o755)
    monkeypatch.setenv('PATH', f'{stubs}{os.pathsep}{os.environ["PATH"]}')


def by_name(entries: list[index.Entry]) -> dict[str, index.Entry]:
    return {entry.name: entry for entry in entries}


def test_a_registry_key_is_not_the_invocation():
    """`ripgrep` installs `rg`. The key names the package, `usage` names the command."""
    indexed = by_name(index.index_tools())

    assert indexed['ripgrep'].invocation == 'rg [pattern] [path]'
    assert indexed['git-forgit-log'].invocation == 'git forgit log'


def test_the_display_line_carries_the_invocation():
    """fzf can only match what it shows, and a row you cannot type is half a row."""
    line = by_name(index.index_tools())['ripgrep'].display()

    assert 'ripgrep' in line, 'the registry identity stays searchable'
    assert 'rg [pattern] [path]' in line
    assert 'search' in line, 'tags ride in the display line — they are the discovery contract'


def test_the_invocation_is_omitted_when_it_repeats_the_name():
    entry = index.Entry(source='func', name='gwip', invocation='gwip', description='commit wip')

    assert entry.display().count('gwip') == 1


@pytest.mark.parametrize(
    ('usage', 'expected'),
    [('rg [pattern]', 'rg'), ('git forgit log', 'git'), ('source aws-profiles', 'aws-profiles'), ('', '')],
)
def test_invocation_head_is_the_word_you_actually_run(usage, expected):
    assert tools.invocation_head(usage) == expected


def test_unresolved_finds_rot_and_nothing_else():
    """`rg` and `git` resolve; `long-gone` does not, and that is the report."""
    dead = [entry.name for entry in index.unresolved()]

    assert 'long-gone' in dead
    assert 'ripgrep' not in dead, 'resolved through its usage, not its key'
    assert 'forge' not in dead
    assert 'git-forgit-log' not in dead


def test_unresolved_ignores_lenses_that_are_not_commands():
    """A card, a skill or a keybinding cannot be a dead command."""
    dead = index.unresolved()

    assert {entry.source for entry in dead} <= {'tool', 'func', 'alias', 'forgit'}


def test_a_shell_function_resolves_even_though_it_is_not_on_path():
    """`which` cannot see a shell function, so the files are read directly."""
    assert 'gwip' in index.resolvable_names()
    assert 'll' in index.resolvable_names()


def test_functions_need_the_annotation_to_be_indexed():
    """The `#@name` annotation is the opt-in; helpers stay out."""
    functions = by_name(index.index_functions())

    assert functions['gwip'].description == 'commit work in progress'


def test_a_function_carries_its_body_because_that_is_the_refresher():
    assert by_name(index.index_functions())['gwip'].body == '  :'


def test_an_annotated_function_with_no_definition_still_yields_a_row(monkeypatch, tmp_path):
    """An entry that vanishes on a syntax error is worse than a ragged body."""
    shell = tmp_path / 'shell-only'
    shell.mkdir()
    (shell / 'functions.sh').write_text('#@orphan\n#--> annotated but never defined\n#@second\n#--> and another\nsecond() {\n  :\n}\n')
    monkeypatch.setattr(index, 'SHELL_DIR', shell)

    functions = by_name(index.index_functions())

    assert functions['orphan'].description == 'annotated but never defined'
    assert functions['orphan'].body == ''
    assert functions['second'].body == '  :'


def test_aliases_take_the_preceding_comment_as_their_description():
    assert by_name(index.index_aliases())['ll'].description == 'list files'


def test_an_alias_carries_what_it_expands_to():
    """A card that omits the expansion cannot say why the alias is worth keeping."""
    assert by_name(index.index_aliases())['ll'].command == 'eza -l'


def test_workflow_rows_point_at_the_command_that_renders_them():
    cards = by_name(index.index_workflows())

    assert cards['tmux-commands'].invocation == 'doit workflows show tmux-commands'
    assert cards['tmux-commands'].description == 'tmux Commands'


def test_tmux_keys_come_from_the_card_so_the_popup_cannot_drift():
    keys = by_name(index.index_tmux_keys())

    # The card escapes the pipe so markdown does not read it as a column
    # boundary; the key you actually press is unescaped.
    assert 'prefix + |' in keys
    assert keys['prefix + |'].description == 'split vertical'


def test_zsh_keys_are_what_your_config_adds_to_a_stock_zsh():
    """A raw keymap is mostly vi motions. The diff is the part you did not know."""
    keys = by_name(index.index_zsh_keys())

    assert set(keys) == {'viins ^G', 'viins ^H', 'viins ^R', 'vicmd ^R'}
    assert 'viins ^I' not in keys, 'bound identically in a stock zsh'


def test_the_dormant_editing_mode_is_not_indexed():
    """fzf binds into both modes. Only the one `main` points at can be pressed."""
    keys = by_name(index.index_zsh_keys())

    assert 'emacs ^T' not in keys, 'main is viins here, so the emacs keymap is unreachable'
    assert not any(key.startswith('main ') for key in keys), 'reported under its real name'


def test_an_unrecognised_editing_mode_is_reported_as_main():
    """A keymap linked to main under some other name is still what you press."""
    bindings = {('main', '^G'): 'gwip', ('mine', '^G'): 'gwip'}

    assert index.active_keymaps(bindings) == ('main',)


def test_a_zsh_key_carries_the_widget_it_runs():
    assert by_name(index.index_zsh_keys())['viins ^R'].command == 'atuin-search-viins'
    assert by_name(index.index_zsh_keys())['viins ^R'].invocation == '^R', 'the invocation is what you press'


def test_the_keymap_is_in_the_name_because_one_key_is_two_bindings():
    """`^R` is atuin in insert mode and fzf in command mode."""
    keys = by_name(index.index_zsh_keys())

    assert keys['viins ^R'].command == 'atuin-search-viins'
    assert keys['vicmd ^R'].command == 'fzf-history-widget'


def test_a_widget_you_wrote_is_described_by_its_own_annotation():
    """The `#-->` line above the function stays the only place it is written down."""
    assert by_name(index.index_zsh_keys())['viins ^G'].description == 'commit work in progress'


def test_an_unknown_widget_falls_back_to_its_own_name():
    assert by_name(index.index_zsh_keys())['viins ^H'].description == 'fzf man widget'


def test_bulk_and_alias_bindings_never_become_rows():
    """A range is always self-insert, and `"gUU" "gUgU"` names no widget at all."""
    keys = by_name(index.index_zsh_keys())

    assert not any(key.endswith('^S') for key in keys), 'self-insert is not a discovery'
    assert not any('gUU' in key for key in keys)


def test_a_zsh_key_is_never_reported_as_rot():
    """A keybinding is pressed, not typed, so PATH has nothing to say about it."""
    assert not [entry for entry in index.unresolved() if entry.source == 'zsh']


def test_a_machine_without_zsh_still_gets_every_other_lens(monkeypatch):
    monkeypatch.setattr(index, 'zsh_bindkeys', lambda interactive: '')

    assert index.index_zsh_keys() == []
    assert index.build_index(), 'the other collections still index'


def test_build_index_can_be_scoped_to_one_collection():
    scoped = index.build_index(['tool'])

    assert {entry.source for entry in scoped} == {'tool'}


def test_a_missing_collection_is_silent_not_fatal(monkeypatch, tmp_path):
    """A machine without the tool registry still gets every other lens."""
    monkeypatch.setattr(tools, 'REGISTRY', tmp_path / 'nope.yml')

    assert index.index_tools() == []
    assert index.build_index(), 'the other collections still index'
