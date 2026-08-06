"""doit's startup-nudge renderers.

The house palette, section header and help grammar live in ``pytermstyle``
— shared with every Python app, and with the bash and Go CLIs through its
counterparts. What is left here is specific to the nudge: it is an interrupt you
did not ask for, so it trades every browse-time field (description, tags,
cadence, last-done) for one line per item, and every line is clipped to the
terminal rather than wrapped.
"""

from pytermstyle import CYAN
from pytermstyle import RESET
from pytermstyle import YELLOW
from pytermstyle import clip

# Wide enough for "overdue 999d", the longest status_label, plus a space.
STATUS_WIDTH = 13


def nudge_header(title: str, count: int) -> None:
    """Print a one-line nudge heading (``Review · 6 due``)."""
    print(f'{CYAN}{title} · {count} due{RESET}')


def nudge_width(names: list[str]) -> int:
    """The name-column width for a set of nudge rows.

    Measured on the uncolored names for the same reason ``flush_rows`` does it:
    a format-string field width counts escape bytes and would shove every later
    column right by the length of the color escape.
    """
    return max((len(name) for name in names), default=0) + 2


def nudge_row(name: str, status: str, command: str, width: int) -> None:
    """Print one due item as a single line, clipping rather than wrapping.

    Only the command is clipped — the name and status are the identity of the
    row, so losing them to a narrow pane would defeat the point.
    """
    pad = ' ' * max(width - len(name), 0)
    status = status.ljust(STATUS_WIDTH)
    if not command:
        return print(f'  {YELLOW}{name}{RESET}{pad}{status}'.rstrip())
    command = clip(command, len(f'  {name}{pad}{status}↳ '))
    return print(f'  {YELLOW}{name}{RESET}{pad}{status}{CYAN}↳ {command}{RESET}')
