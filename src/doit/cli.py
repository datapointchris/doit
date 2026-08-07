"""doit's command tree.

Every node prints help when given no arguments, and a usage error exits 2 —
both from typer rather than hand-rolled here, which is the whole reason the
tree is built this way. Each subcommand module owns its own `app`; this file
only assembles them.
"""

from typing import Annotated

import typer
from pyselfupdate import Config
from pyselfupdate import notify
from pyselfupdate.typercmd import add_update_command

from doit import __version__
from doit import content
from doit import dashboard
from doit import find
from doit import labs
from doit import pursuits
from doit import review
from doit import shell
from doit import sources
from doit import workflows

TAGLINE = 'What to do now, and everything that decides it.'

app = typer.Typer(name='doit', no_args_is_help=True, help=TAGLINE)

# Notify-only by house rule: one check per 24h, one line to stderr, and
# `doit update` is the only thing that writes anything. The notice and the
# command share this one config so they cannot advertise a release the command
# then refuses.
UPDATE_CONFIG = Config(tool='doit', owner='datapointchris')

# The draw and the two things you do to what it just offered sit at the root:
# they act on the answer, while `pursuits` manages the file that produced it.
app.command('next')(pursuits.next_command)
app.command('log')(pursuits.log_command)
app.command('skip')(pursuits.skip_command)
app.command('dashboard')(dashboard.dashboard_command)
app.command('find')(find.find_command)
app.command('launch')(find.launch_command)

app.command('show')(find.show_command)
app.command('shell-init')(shell.shell_init_command)
app.command('completion')(shell.completion_command)
add_update_command(app, UPDATE_CONFIG)

# What the fzf preview pane calls, not what you type.
app.command('__preview', hidden=True)(find.preview_command)

app.add_typer(pursuits.app, name='pursuits')
app.add_typer(review.app, name='review')
app.add_typer(labs.app, name='labs')
app.add_typer(workflows.app, name='workflows')
app.add_typer(find.index_app, name='index')
app.add_typer(sources.app, name='sources')
app.add_typer(content.app, name='content')


def version_callback(asked: bool) -> None:
    """`doit --version`, in the one line every CLI here answers it with."""
    if not asked:
        return
    print(f'doit {__version__}')
    raise typer.Exit()


@app.callback()
def root(
    ctx: typer.Context,
    version: Annotated[
        bool | None,
        typer.Option('--version', '-V', callback=version_callback, is_eager=True, help='Show the installed version and exit.'),
    ] = None,
) -> None:
    """Root callback, hosting the app-level options.

    The content clone happens here so a new machine needs no setup step anyone
    has to remember. It is one `exists()` once the checkout is there — see
    `doit.content.ensure_cloned` for why a failure only warns.

    Keeping it current is the same argument on every later run, so that is not a
    verb anyone has to remember either — the pull is detached, so a command costs
    the same whether or not one was due. `content` is exempt: its own subcommands
    report on the checkout, and a pull racing them would have `status` describe a
    tree being rewritten underneath it.
    """
    content.ensure_cloned()
    if ctx.invoked_subcommand != 'content':
        content.autosync()

    # `update` runs its own check, so notifying too would ask the releases API
    # twice for one command and let the notice contradict what `--check` just
    # printed. Every other path is gated locally before it reaches the network.
    if ctx.invoked_subcommand != 'update':
        notify(UPDATE_CONFIG)


def main() -> None:
    app()
