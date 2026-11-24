"""`doit tui`, which hands the terminal to the `doit-tui` binary that this repository's `cli/` module builds."""

import os
import shutil
from typing import Annotated

import typer

from doit.render import error_console
from doit.render import examples

TUI_BINARY = 'doit-tui'

TUI_EXAMPLES = examples(
    ('doit tui', "open doit's screens"),
    ('doit tui --tab now', 'open on what can be completed now'),
    ('COLUMNS=120 LINES=30 doit tui --once', 'print the screen it opens on, then exit'),
)


def tui_command(
    tab: Annotated[str | None, typer.Option('--tab', help='The tab to open on, by name. doit-tui --help lists them.')] = None,
    once: Annotated[bool, typer.Option('--once', help='Print the screen it opens on at COLUMNS by LINES, then exit.')] = False,
) -> None:
    """Open doit's screens, where a click or a key completes a row.

    The screens are the `doit-tui` binary, which reads and picks through
    `doit desk-nudge panel`. Exits 1 where `doit-tui` is not on PATH.
    """
    binary = shutil.which(TUI_BINARY)
    if binary is None:
        error_console.print(f"doit tui: {TUI_BINARY} is not on PATH. It is built from the cli/ module of doit's repository.")
        raise typer.Exit(1)
    os.execv(binary, [binary, *(['--tab', tab] if tab else []), *(['--once'] if once else [])])
