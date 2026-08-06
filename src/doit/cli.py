"""doit's command tree.

Every node prints help when given no arguments, and a usage error exits 2 —
both from typer rather than hand-rolled here, which is the whole reason the
tree is built this way. Each subcommand module owns its own `app`; this file
only assembles them.
"""

from typing import Annotated

import typer

from doit import __version__
from doit import labs
from doit import review

TAGLINE = 'What to do now, and everything that decides it.'

app = typer.Typer(name='doit', no_args_is_help=True, help=TAGLINE)
app.add_typer(review.app, name='review')
app.add_typer(labs.app, name='labs')


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
