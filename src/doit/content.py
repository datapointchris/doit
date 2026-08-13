"""The cards and Labs, and keeping them current.

Content is authored far more often than doit is released, so it does not ship
inside doit. It is a git checkout of the terminal library under
`$XDG_DATA_HOME/terminal-library/`, which means writing a card and having a card
are the same act on every machine — the tldr model, where pages live upstream
and the client fetches them.

Not `doit update`: that verb is reserved for a tool updating *itself*, one
spelling everywhere. The content and the binary are two different things to
bring up to date, and giving them one word would make "did you update?" an
ambiguous question.

doit clones it on first use, so a new machine needs no setup step that can be
forgotten — which is the whole reason the remote is named here rather than left
to the checkout. `sync` afterwards asks git where it came from, so a fork or a
moved remote keeps working without this constant being right forever.

The clone happens once; the pull happens on every run, detached and never waited
on. A tool that *waited* on the network every command would be one you stopped
running, but nothing here waits: the command returns at its own speed and the
cards are current by the next one, which is what makes `sync` a verb nobody has
to remember.
"""

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.text import Text

from doit.paths import library_dir
from doit.paths import library_source
from doit.paths import xdg_state_home
from doit.render import console
from doit.render import error_console

CONTENT_DIR = Path(os.environ.get('DOIT_CONTENT_DIR') or library_dir())

# State, not config: this is one machine's record of its own last pull.
SYNC_LOG = Path(os.environ.get('DOIT_CONTENT_SYNC_LOG') or xdg_state_home() / 'doit' / 'content-sync.log')
SYNC_LOCK = SYNC_LOG.with_name(SYNC_LOG.name + '.lock')

# Long enough that no real pull is mistaken for a dead one, short enough that a
# machine killed mid-pull is syncing again the same session.
STALE_LOCK_SECONDS = 5 * 60

# Commands another program drives, where "once per run" is not once per command:
# an fzf preview pane redraws on every keystroke and completion fires on every
# TAB. Everything else is a person typing, which is exactly when a pull is free.
MACHINE_DRIVEN = frozenset({'__preview', '__render', 'names'})

# Where the cards come from on a machine that has none yet. Overridable so a
# fork, a mirror, or a test can point somewhere else without patching code.
CONTENT_REMOTE = os.environ.get('DOIT_CONTENT_REMOTE') or 'https://github.com/datapointchris/terminal-library.git'


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, check=False)


def is_checkout(path: Path) -> bool:
    return (path / '.git').exists()


def remote_url(path: Path) -> str:
    result = git('remote', 'get-url', 'origin', cwd=path)
    return result.stdout.strip() if result.returncode == 0 else ''


def content_source() -> str:
    """Which layer named this checkout, for `content status` to print."""
    return '$DOIT_CONTENT_DIR' if os.environ.get('DOIT_CONTENT_DIR') else library_source()


def ensure_cloned() -> bool:
    """Clone the content on a machine that has none. True if it is there now.

    Called on every invocation, so the common path is one `exists()` and no
    process spawn. A failure is a warning rather than an error: doit's other
    halves — the draw, the dashboard, the register — do not need cards, and
    refusing to run because a clone failed would be the tail wagging the dog.
    """
    if is_checkout(CONTENT_DIR):
        return True
    if CONTENT_DIR.exists() and any(CONTENT_DIR.iterdir()):
        # Something is already there that git did not put there. Adopting it
        # would mean deciding whose files win, which is not doit's call.
        return False
    error_console.print(Text(f'Fetching cards and Labs into {CONTENT_DIR}…'), soft_wrap=True)
    CONTENT_DIR.parent.mkdir(parents=True, exist_ok=True)
    result = git('clone', '--quiet', CONTENT_REMOTE, str(CONTENT_DIR))
    if result.returncode != 0:
        error_console.print(Text(f'  could not clone {CONTENT_REMOTE}:'), soft_wrap=True)
        error_console.print(Text(f'  {(result.stderr or result.stdout).strip().splitlines()[0]}'), soft_wrap=True)
        return False
    return True


def record_sync() -> None:
    """Mark the content as synced now, with no failure outstanding."""
    SYNC_LOG.parent.mkdir(parents=True, exist_ok=True)
    SYNC_LOG.write_text('')


def driven_by_a_person(argv: list[str]) -> bool:
    return MACHINE_DRIVEN.isdisjoint(argv)


def claim_sync() -> bool:
    """True if no pull is already in flight, having claimed the right to start one.

    An atomic mkdir rather than a flag file anyone checks then writes: two doit
    commands started together would both find it absent and both pull, and the
    loser dies on git's own index.lock with a message that reads like a real
    failure of the remote.
    """
    try:
        SYNC_LOCK.mkdir(parents=True)
        return True
    except FileExistsError:
        pass
    try:
        held_for = time.time() - SYNC_LOCK.stat().st_mtime
    except OSError:
        return False
    if held_for < STALE_LOCK_SECONDS:
        return False
    # A pull killed mid-flight leaves the lock behind, and without this nothing
    # would ever sync again on this machine.
    try:
        SYNC_LOCK.rmdir()
        SYNC_LOCK.mkdir()
    except OSError:
        return False
    return True


def report_last_sync_failure() -> None:
    """Say that the last background pull failed, since nothing else can.

    A detached pull has nowhere to print. Cards that quietly stopped updating
    read as cards that stopped changing, which is the same failure as the
    dashboard dropping a lane: the reader concludes there is nothing new.
    """
    try:
        last = SYNC_LOG.read_text().strip()
    except OSError:
        return
    if not last:
        return
    error_console.print(Text(f'Cards last failed to sync: {last.splitlines()[0]}'), soft_wrap=True)
    error_console.print(Text('  retry with doit content sync', style='cyan'), soft_wrap=True)


def autosync(argv: list[str] | None = None) -> bool:
    """Pull the cards on every run, in the background. True if a pull started.

    Detached and never waited on: the cards are current by the time you read one
    without anything having waited for the network, and a pull against a dead
    route costs the command nothing. This is why it is every run rather than on
    a timer — nothing is being rationed, so there is nothing to ration.
    """
    if not is_checkout(CONTENT_DIR):
        return False
    report_last_sync_failure()
    if not driven_by_a_person(sys.argv if argv is None else argv):
        return False
    SYNC_LOG.parent.mkdir(parents=True, exist_ok=True)
    if not claim_sync():
        return False
    # The log is the only channel a detached child has: it keeps git's stderr on
    # failure and is emptied on success, so "non-empty" means "the last pull
    # failed" without this having to wait around for an exit status.
    #
    # `--ff-only` is what makes this safe to point at a checkout cards are
    # authored in. It refuses rather than merging, and git declines to overwrite
    # a modified file, so an unfinished card is never the price of a sync.
    subprocess.Popen(
        [
            'sh',
            '-c',
            'git pull --ff-only --quiet 2>"$1" && : >"$1"; rmdir "$2"',
            'sh',
            str(SYNC_LOG),
            str(SYNC_LOCK),
        ],
        cwd=CONTENT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return True


def explain_missing() -> int:
    """Say what to clone and where, rather than guessing a remote.

    A default URL baked in here would be one more place a repo rename has to be
    chased, and it would fail confusingly on a machine that wants a fork.
    """
    # soft_wrap: a path rich has wrapped mid-way is a path you cannot copy, and
    # the whole point of this message is the command underneath it.
    error_console.print(Text(f'No content checkout at {CONTENT_DIR}.'), soft_wrap=True)
    error_console.print('doit clones it on first run; if that failed, do it by hand:')
    error_console.print(Text(f'  git clone {CONTENT_REMOTE} {CONTENT_DIR}', style='cyan'), soft_wrap=True)
    return 1


def cmd_sync() -> int:
    """Fast-forward the content checkout."""
    if not is_checkout(CONTENT_DIR):
        return explain_missing()
    result = git('pull', '--ff-only', cwd=CONTENT_DIR)
    if result.returncode != 0:
        error_console.print(Text(f'git pull failed in {CONTENT_DIR}:'))
        error_console.print(Text(f'  {(result.stderr or result.stdout).strip().splitlines()[0]}'))
        return 1
    # Doing it by hand is a sync like any other: it resets the daily clock, and
    # it clears a past failure that would otherwise keep being reported at a
    # problem the person just fixed.
    record_sync()
    console.print(Text.from_markup('[green]✓[/] ') + Text(result.stdout.strip() or 'Already up to date.'))
    return 0


def cmd_status() -> int:
    """Where the content is, where it came from, and whether it is dirty.

    Authoring happens in this checkout, so "have I committed the card I wrote"
    is a question with a real answer that is otherwise a `cd` away.
    """
    if not is_checkout(CONTENT_DIR):
        return explain_missing()
    console.print(Text(f'path    {CONTENT_DIR}  (from {content_source()})'), soft_wrap=True)
    console.print(Text(f'remote  {remote_url(CONTENT_DIR) or "(none)"}'), soft_wrap=True)
    dirty = git('status', '--porcelain', cwd=CONTENT_DIR).stdout.strip()
    if not dirty:
        console.print(Text.from_markup('[green]✓[/] nothing uncommitted'))
        return 0
    console.print(Text(f'{len(dirty.splitlines())} uncommitted change(s)', style='yellow'))
    for line in dirty.splitlines():
        console.print(Text(f'  {line}'), no_wrap=True, overflow='ellipsis')
    return 0


def cmd_path() -> int:
    """The checkout path, for scripting. One line, nothing else."""
    print(CONTENT_DIR)
    return 0


app = typer.Typer(name='content', no_args_is_help=True, help='The terminal-library checkout.')


@app.command('sync')
def sync_command() -> None:
    """Fetch the latest cards and Labs."""
    raise typer.Exit(cmd_sync())


@app.command('status')
def status_command() -> None:
    """Where the content lives and whether anything is uncommitted."""
    raise typer.Exit(cmd_status())


@app.command('path')
def path_command() -> None:
    """Print the content checkout path."""
    raise typer.Exit(cmd_path())


@app.command('edit')
def edit_command(
    what: Annotated[str, typer.Argument(help='A card or Lab id.')],
) -> None:
    """Open a card or Lab in $EDITOR, wherever it lives."""
    for subdir in ('workflows', 'labs'):
        candidate = CONTENT_DIR / subdir / f'{what}.md'
        if candidate.exists():
            subprocess.run([os.environ.get('EDITOR', 'nvim'), str(candidate)], check=False)
            raise typer.Exit(0)
    error_console.print(Text(f'No card or Lab named {what!r} under {CONTENT_DIR}.'))
    raise typer.Exit(1)
