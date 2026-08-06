"""doit's shared console and its startup-nudge renderers.

A nudge is an interrupt you did not ask for, so it trades every browse-time field
(description, tags, cadence, last-done) for one line per item, and every line is
clipped to the terminal rather than wrapped. A wrapped row is two rows, which is
how the nudge grew the first time.

Clipping and column widths are rich's: `no_wrap` plus `overflow='ellipsis'`
replaces arithmetic against `shutil.get_terminal_size`, and rich measures
printable width, so styling cannot shift a later column.

Lines carrying content from a register, a Lab or a command are built as `Text`
rather than markup strings — `Text.append` does not parse `[...]`, so a bracket
in a description cannot be swallowed as a style tag.
"""

from rich.console import Console
from rich.text import Text

# highlight=False: rich's automatic highlighter colours anything that looks like
# a number, path or URL, which turns a register id into a surprise.
console = Console(highlight=False)

# Everything that is not data. stdout carries the answer a caller parses; a
# diagnostic written there is what turns `doit review list --json` into a broken
# parse rather than a readable warning.
error_console = Console(stderr=True, highlight=False)

# Wide enough for "overdue 999d", the longest status_label, plus a space.
STATUS_WIDTH = 13


def nudge_header(title: str, count: int) -> None:
    """Print a one-line nudge heading (``Review · 6 due``)."""
    console.print(f'[cyan]{title} · {count} due[/]')


def nudge_width(names: list[str]) -> int:
    """The name-column width for a set of nudge rows."""
    return max((len(name) for name in names), default=0) + 2


def nudge_row(name: str, status: str, command: str, width: int) -> None:
    """Print one due item as a single line, clipping rather than wrapping.

    Only the command is at risk of being clipped — the name and status come
    first, so a narrow pane eats the trailing command rather than the identity of
    the row.
    """
    line = Text('  ')
    line.append(name.ljust(width), style='yellow')
    if command:
        line.append(status.ljust(STATUS_WIDTH))
        line.append(f'↳ {command}', style='cyan')
    else:
        line.append(status)
    console.print(line, no_wrap=True, overflow='ellipsis')
