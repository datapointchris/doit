"""The cards and Labs, and keeping them current.

Content is authored far more often than doit is released, so it does not ship
inside doit. It is a git checkout under `$XDG_DATA_HOME/doit/`, which means
writing a card and having a card are the same act on every machine — the tldr
model, where pages live upstream and the client fetches them.

Not `doit update`: [cli-design.md](~/dev/standards/cli-design.md) reserves that
verb for a tool updating *itself*, one spelling everywhere. The content and the
binary are two different things to bring up to date, and giving them one word
would make "did you update?" an ambiguous question.

Nothing here knows the remote. The checkout does, because it is a checkout —
asking git where it came from is one call, and a URL written into this file is a
URL that goes stale in a repo nobody thought to grep.
"""

import os
import subprocess
from pathlib import Path
from typing import Annotated

import typer
from rich.text import Text

from doit.paths import xdg_data_home
from doit.render import console
from doit.render import error_console

CONTENT_DIR = Path(os.environ.get('DOIT_CONTENT_DIR') or xdg_data_home() / 'doit')


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, check=False)


def is_checkout(path: Path) -> bool:
    return (path / '.git').exists()


def remote_url(path: Path) -> str:
    result = git('remote', 'get-url', 'origin', cwd=path)
    return result.stdout.strip() if result.returncode == 0 else ''


def explain_missing() -> int:
    """Say what to clone and where, rather than guessing a remote.

    A default URL baked in here would be one more place a repo rename has to be
    chased, and it would fail confusingly on a machine that wants a fork.
    """
    # soft_wrap: a path rich has wrapped mid-way is a path you cannot copy, and
    # the whole point of this message is the command underneath it.
    error_console.print(Text(f'No content checkout at {CONTENT_DIR}.'), soft_wrap=True)
    error_console.print('Clone it there once:')
    error_console.print(Text(f'  git clone <your-doit-content-repo> {CONTENT_DIR}', style='cyan'), soft_wrap=True)
    error_console.print('  Cards land in `workflows/`, Labs in `labs/`.')
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
    console.print(Text.from_markup('[green]✓[/] ') + Text(result.stdout.strip() or 'Already up to date.'))
    return 0


def cmd_status() -> int:
    """Where the content is, where it came from, and whether it is dirty.

    Authoring happens in this checkout, so "have I committed the card I wrote"
    is a question with a real answer that is otherwise a `cd` away.
    """
    if not is_checkout(CONTENT_DIR):
        return explain_missing()
    console.print(Text(f'path    {CONTENT_DIR}'), soft_wrap=True)
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


app = typer.Typer(name='content', no_args_is_help=True, help='The cards and Labs checkout.')


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
