"""Tests for the generated shell blocks and the content checkout.

The assertion that earns its place is `zsh -n`: a generated script that does not
parse is the classic way this breaks, and it breaks at shell startup on every
machine at once rather than where it was written.
"""

import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from doit import content
from doit import shell
from doit.cli import app as cli_app

runner = CliRunner()
needs_zsh = pytest.mark.skipif(not shutil.which('zsh'), reason='zsh is not installed')


def parses(script: str) -> bool:
    return subprocess.run(['zsh', '-n'], input=script, text=True, capture_output=True, check=False).returncode == 0


@needs_zsh
@pytest.mark.parametrize('generate', [shell.zsh_init, shell.zsh_completion])
def test_the_generated_zsh_parses(generate):
    assert parses(generate())


def test_the_init_block_gates_on_doit_being_installed():
    """dotfiles used to test `-x ~/.local/bin/menu-review`, which broke the day
    the binaries collapsed into one."""
    block = shell.zsh_init()

    assert '$+commands[doit]' in block
    assert 'menu-review' not in block


def test_the_init_block_reads_the_interval_from_shared_state():
    """The schedule is rolling and shared, not once per machine."""
    assert 'nudge-interval-minutes' in shell.zsh_init()
    assert str(shell.DEFAULT_NUDGE_MINUTES) in shell.zsh_init()


def test_completion_reads_the_cache_rather_than_calling_doit():
    """A TAB must not wait on a Python start."""
    script = shell.zsh_completion()

    assert 'next-names.txt' in script
    assert 'doit ' not in script.split('_doit_pursuits')[1].split('}')[0]


def test_completion_offers_a_pursuit_where_one_is_expected():
    script = shell.zsh_completion()

    assert 'log|skip)' in script, 'the two verbs that take a pursuit'


def test_an_unsupported_shell_is_a_usage_error():
    result = runner.invoke(cli_app, ['shell-init', 'fish'])

    assert result.exit_code == 2
    assert 'zsh' in result.output


def test_a_missing_content_checkout_says_what_to_clone(tmp_path, monkeypatch, capsys):
    """No remote is guessed: a URL written into doit is one more thing to chase."""
    monkeypatch.setattr(content, 'CONTENT_DIR', tmp_path / 'absent')

    assert content.cmd_status() == 1

    err = capsys.readouterr().err
    assert 'git clone' in err
    assert str(tmp_path / 'absent') in err


def test_content_path_prints_one_line_for_scripting(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(content, 'CONTENT_DIR', tmp_path / 'somewhere')

    assert content.cmd_path() == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / 'somewhere')


def test_content_status_reports_uncommitted_work(tmp_path, monkeypatch, capsys):
    """Authoring happens in the checkout, so "did I commit that card" is a real
    question with an answer that is otherwise a `cd` away."""
    monkeypatch.setattr(content, 'CONTENT_DIR', tmp_path)
    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, check=True)
    (tmp_path / 'workflows').mkdir()
    (tmp_path / 'workflows' / 'new-card.md').write_text('# New Card\n')

    assert content.cmd_status() == 0

    out = capsys.readouterr().out
    assert 'uncommitted' in out
    # git collapses an untracked directory to the directory itself.
    assert 'workflows/' in out


def test_content_is_cloned_on_first_use(tmp_path, monkeypatch):
    """A new machine needs no setup step anyone has to remember."""
    upstream = tmp_path / 'upstream'
    upstream.mkdir()
    subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=upstream, check=True)
    (upstream / 'workflows').mkdir()
    (upstream / 'workflows' / 'a-card.md').write_text('# A Card\n')
    subprocess.run(['git', 'add', '-A'], cwd=upstream, check=True)
    subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'seed'], cwd=upstream, check=True)

    target = tmp_path / 'content'
    monkeypatch.setattr(content, 'CONTENT_DIR', target)
    monkeypatch.setattr(content, 'CONTENT_REMOTE', str(upstream))

    assert content.ensure_cloned() is True
    assert (target / 'workflows' / 'a-card.md').exists()


def test_an_existing_checkout_is_not_re_cloned(tmp_path, monkeypatch):
    """The common path is one exists() and no process spawn."""
    target = tmp_path / 'content'
    (target / '.git').mkdir(parents=True)
    monkeypatch.setattr(content, 'CONTENT_DIR', target)
    monkeypatch.setattr(content, 'CONTENT_REMOTE', 'file:///nowhere-that-exists')

    assert content.ensure_cloned() is True


def test_a_non_empty_directory_is_left_alone(tmp_path, monkeypatch):
    """Files git did not put there are not doit's to adopt or overwrite."""
    target = tmp_path / 'content'
    target.mkdir()
    (target / 'mine.md').write_text('# Mine\n')
    monkeypatch.setattr(content, 'CONTENT_DIR', target)

    assert content.ensure_cloned() is False
    assert (target / 'mine.md').exists()


def test_a_failed_clone_warns_rather_than_stopping_doit(tmp_path, monkeypatch, capsys):
    """The draw, the dashboard and the register need no cards."""
    monkeypatch.setattr(content, 'CONTENT_DIR', tmp_path / 'content')
    monkeypatch.setattr(content, 'CONTENT_REMOTE', str(tmp_path / 'not-a-repo'))

    assert content.ensure_cloned() is False
    assert 'could not clone' in capsys.readouterr().err
