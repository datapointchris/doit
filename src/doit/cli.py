"""doit-python's command tree, which runs `doit tui`.

The Go doit runs every other command line, and execs `doit-python tui` with the
arguments typed after it. A usage error exits 2, from typer.
"""

import os
from typing import Annotated

import typer
from pyclilogin import LoginError

from doit import __version__
from doit import tui
from doit.render import error_console
from doit.render import set_no_input

# No typer completion: the Go doit's `doit shell completion` completes every verb.
app = typer.Typer(
    name='doit',
    no_args_is_help=True,
    add_completion=False,
    help='Runs `doit tui`. The Go doit runs every other command line, and `doit --help` lists them.',
    rich_markup_mode='markdown',
)


app.command('tui', epilog=tui.TUI_EXAMPLES)(tui.tui_command)


def version_callback(asked: bool) -> None:
    """`doit-python --version`, in the one line every CLI here answers it with."""
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
    no_input: Annotated[
        bool,
        typer.Option('--no-input', help='Never prompt; fail naming the flag that would have answered.'),
    ] = False,
) -> None:
    """Root callback, hosting the app-level options."""
    set_no_input(no_input)


# Each chose the files doit read and wrote. The service holds that data. A
# variable still set names a file that is never read, so it is refused rather
# than ignored.
RETIRED_VARIABLES = ('DOIT_JOURNAL_DIR', 'DOIT_REVIEW_STATE', 'DOIT_PURSUITS', 'DOIT_REGISTER', 'DOIT_SOURCES')


def retired_variable_set() -> str:
    """The refusal for the first retired variable still set, or '' where none is."""
    for variable in RETIRED_VARIABLES:
        if os.environ.get(variable):
            return f'${variable} is set, and doit reads its records from its service. Unset it.'
    return ''


def main() -> None:
    if refusal := retired_variable_set():
        error_console.print(f'doit: {refusal}', soft_wrap=True, markup=False)
        raise SystemExit(2)
    # The console script is doit-python, and a person typed doit, so every
    # usage line and refusal names doit.
    try:
        app(prog_name='doit')
    except LoginError as failure:
        error_console.print(f'doit: {failure}', soft_wrap=True, markup=False)
        raise SystemExit(1) from None
