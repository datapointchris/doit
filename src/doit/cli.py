"""doit's command tree.

Every node prints help when given no arguments, and a usage error exits 2 —
both from typer rather than hand-rolled here, which is the whole reason the
tree is built this way. Each subcommand module owns its own `app`; this file
only assembles them.
"""

from typing import Annotated

import typer

from doit import __version__
from doit import dashboard
from doit import find
from doit import labs
from doit import pursuits
from doit import review
from doit import workflows

TAGLINE = 'What to do now, and everything that decides it.'

app = typer.Typer(name='doit', no_args_is_help=True, help=TAGLINE)

# The draw and the two things you do to what it just offered sit at the root:
# they act on the answer, while `pursuits` manages the file that produced it.
app.command('next')(pursuits.next_command)
app.command('log')(pursuits.log_command)
app.command('skip')(pursuits.skip_command)
app.command('dashboard')(dashboard.dashboard_command)
app.command('find')(find.find_command)
app.command('launch')(find.launch_command)

app.command('show')(find.show_command)

# What the fzf preview pane calls, not what you type.
app.command('__preview', hidden=True)(find.preview_command)

app.add_typer(pursuits.app, name='pursuits')
app.add_typer(review.app, name='review')
app.add_typer(labs.app, name='labs')
app.add_typer(workflows.app, name='workflows')
app.add_typer(find.index_app, name='index')


def version_callback(asked: bool) -> None:
    """`doit --version`, in the one line every CLI here answers it with."""
    if not asked:
        return
    print(f'doit {__version__}')
    raise typer.Exit()


@app.callback()
def root(
    version: Annotated[
        bool | None,
        typer.Option('--version', '-V', callback=version_callback, is_eager=True, help='Show the installed version and exit.'),
    ] = None,
) -> None:
    """Root callback, hosting the app-level options."""


def main() -> None:
    app()
