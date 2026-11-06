"""Tests for the generated shell blocks.

The assertion that earns its place is `zsh -n`: a generated script that does not
parse is the classic way this breaks, and it breaks at shell startup on every
machine at once rather than where it was written.
"""

import os
import re
import shutil
import subprocess
import time

import pytest
from typer.testing import CliRunner

from doit import shell
from doit.cli import app as cli_app
from doit.service_commands import review as review_commands

runner = CliRunner()
needs_zsh = pytest.mark.skipif(not shutil.which('zsh'), reason='zsh is not installed')


def parses(script: str) -> bool:
    return subprocess.run(['zsh', '-n'], input=script, text=True, capture_output=True, check=False).returncode == 0


@needs_zsh
@pytest.mark.parametrize('generate', [shell.zsh_init, shell.zsh_completion])
def test_the_generated_zsh_parses(generate):
    assert parses(generate())


@needs_zsh
def test_a_shell_never_waits_on_the_nudge_and_the_next_one_prints_what_it_found(tmp_path, monkeypatch):
    """The nudge asks the service, and a shell that waited on it would hang wherever the service is out of reach."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    (bin_dir / 'doit').write_text('#!/bin/sh\nsleep 1\necho "Review  prune-docker  never done"\n')
    (bin_dir / 'doit').chmod(0o755)
    for name, file in (('NUDGE_INTERVAL', 'nudge-interval-minutes'), ('NUDGE_MARKER', 'nudge'), ('NUDGE_SHOWING', 'nudge-showing.txt')):
        monkeypatch.setattr(review_commands, name, tmp_path / 'cache' / file)
    block = shell.zsh_init()
    environment = {**os.environ, 'PATH': f'{bin_dir}{os.pathsep}{os.environ["PATH"]}'}

    def open_shell() -> tuple[str, float]:
        started = time.monotonic()
        opened = subprocess.run(['zsh', '-f', '-c', block], env=environment, capture_output=True, text=True, check=True, timeout=10)
        return opened.stdout, time.monotonic() - started

    printed, took = open_shell()
    assert (printed, took < 0.5) == ('', True)
    deadline = time.monotonic() + 5
    while not review_commands.NUDGE_SHOWING.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert 'prune-docker' in open_shell()[0]
    assert open_shell()[0] == '', 'a showing prints once, and the marker holds the next check off for the interval'


def test_completion_reads_the_cache_rather_than_calling_doit():
    """A TAB must not wait on a Python start."""
    script = shell.zsh_completion()

    assert 'next-names.txt' in script
    assert 'doit ' not in script.split('_doit_pursuits')[1].split('}')[0]


def test_completion_offers_a_pursuit_where_one_is_expected():
    script = shell.zsh_completion()

    assert "'pursuits log'|'pursuits skip')" in script, 'the two verbs that take a pursuit'


def test_the_completion_offers_no_verb_the_cli_does_not_have():
    """Nothing else joins this hand-written list to the command tree."""
    offered = set(re.findall(r"^\s+'([a-z-]+):", shell.zsh_completion(), re.M))
    registered = {command.name for command in cli_app.registered_commands}
    registered |= {group.name for group in cli_app.registered_groups}

    assert offered <= registered, f'completion offers {sorted(offered - registered)}'


@pytest.mark.parametrize('verb', ['init', 'completion'])
def test_an_unsupported_shell_is_a_usage_error(verb):
    result = runner.invoke(cli_app, ['shell', verb, 'fish'])

    assert result.exit_code == 2
    assert 'zsh' in result.output
