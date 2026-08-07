"""Tests for the generated shell blocks and the content checkout.

The assertion that earns its place is `zsh -n`: a generated script that does not
parse is the classic way this breaks, and it breaks at shell startup on every
machine at once rather than where it was written.
"""

import shutil
import subprocess
import time

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


def git_in(cwd, *args):
    subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t', *args], cwd=cwd, check=True)


def settled(predicate, seconds=10.0):
    """Wait for the detached pull, which by design is never waited on."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def synced(tmp_path, monkeypatch):
    """An upstream, a checkout of it, and the stamp/log pointed somewhere safe."""
    upstream = tmp_path / 'upstream'
    upstream.mkdir()
    subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=upstream, check=True)
    (upstream / 'workflows').mkdir()
    (upstream / 'workflows' / 'a-card.md').write_text('# A Card\n')
    git_in(upstream, 'add', '-A')
    git_in(upstream, 'commit', '-qm', 'seed')

    checkout = tmp_path / 'content'
    subprocess.run(['git', 'clone', '-q', str(upstream), str(checkout)], check=True)

    monkeypatch.setattr(content, 'CONTENT_DIR', checkout)
    monkeypatch.setattr(content, 'SYNC_LOG', tmp_path / 'state' / 'content-sync.log')
    monkeypatch.setattr(content, 'SYNC_LOCK', tmp_path / 'state' / 'content-sync.log.lock')
    return upstream, checkout


def test_a_card_written_upstream_arrives_without_anyone_running_sync(synced):
    """The verb exists, but nobody has to remember it."""
    upstream, checkout = synced
    (upstream / 'workflows' / 'later.md').write_text('# Later\n')
    git_in(upstream, 'add', '-A')
    git_in(upstream, 'commit', '-qm', 'a second card')

    assert content.autosync() is True
    assert settled(lambda: (checkout / 'workflows' / 'later.md').exists())


def test_every_run_pulls_rather_than_waiting_for_a_timer(synced):
    """Nothing waits on the pull, so there is nothing to ration."""
    assert content.autosync() is True
    assert settled(lambda: not content.SYNC_LOCK.exists())

    assert content.autosync() is True


def test_a_pull_already_in_flight_is_not_started_twice(synced):
    """Two doit commands at once would otherwise collide on git's index.lock,
    and the loser's error reads like the remote being unreachable."""
    content.SYNC_LOCK.parent.mkdir(parents=True, exist_ok=True)
    content.SYNC_LOCK.mkdir()

    assert content.autosync() is False


def test_a_lock_left_by_a_killed_pull_does_not_stop_syncing_forever(synced, monkeypatch):
    content.SYNC_LOCK.parent.mkdir(parents=True, exist_ok=True)
    content.SYNC_LOCK.mkdir()
    monkeypatch.setattr(content, 'STALE_LOCK_SECONDS', 0)

    assert content.autosync() is True


def test_an_fzf_preview_does_not_pull_on_every_keystroke(synced):
    """`__preview` and `__render` redraw as the selection moves, and completion
    fires on every TAB — a pull behind each is a pull every few milliseconds."""
    assert content.autosync(argv=['doit', '__preview', 'a-card']) is False
    assert content.autosync(argv=['doit', 'workflows', '__render', 'a-card']) is False
    assert content.autosync(argv=['doit', 'pursuits', 'names']) is False

    assert content.autosync(argv=['doit', 'next']) is True


def test_an_unfinished_card_is_not_the_price_of_a_background_sync(synced):
    """`--ff-only` is what makes this safe to point at a checkout you author in.

    The installed path can be a symlink to the checkout cards are written in, so
    a sync that clobbered a modified file would lose work that was never
    committed anywhere.
    """
    upstream, checkout = synced
    (upstream / 'workflows' / 'a-card.md').write_text('# A Card, upstream\n')
    git_in(upstream, 'add', '-A')
    git_in(upstream, 'commit', '-qm', 'upstream edit')

    mine = checkout / 'workflows' / 'a-card.md'
    mine.write_text('# A Card, half written\n')

    assert content.autosync() is True
    assert settled(lambda: content.SYNC_LOG.exists() and content.SYNC_LOG.read_text().strip())
    assert mine.read_text() == '# A Card, half written\n'


def test_a_pull_that_failed_in_the_background_is_reported_next_run(synced, capsys):
    """A detached pull has nowhere to print, so the next command says it."""
    content.SYNC_LOG.parent.mkdir(parents=True, exist_ok=True)
    content.SYNC_LOG.write_text('fatal: could not read from remote repository\n')

    content.autosync()

    err = capsys.readouterr().err
    assert 'last failed to sync' in err
    assert 'doit content sync' in err


def test_a_successful_pull_reports_nothing(synced, capsys):
    assert content.autosync() is True
    assert settled(lambda: not content.SYNC_LOCK.exists())
    capsys.readouterr()

    content.autosync()

    assert capsys.readouterr().err == ''


def test_syncing_by_hand_clears_a_failure_already_fixed(synced, capsys):
    content.SYNC_LOG.parent.mkdir(parents=True, exist_ok=True)
    content.SYNC_LOG.write_text('fatal: could not read from remote repository\n')

    assert content.cmd_sync() == 0

    assert content.SYNC_LOG.read_text() == ''
    content.autosync()
    assert 'last failed to sync' not in capsys.readouterr().err
