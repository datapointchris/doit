"""doit's shared console, its text helpers, and its startup-nudge renderers.

A nudge is an interrupt you did not ask for, so it trades every browse-time field
(description, tags, cadence, last-done) for one line per item. A wrapped row is
two rows, which is how the nudge grew the first time, so every nudge line stays
within the terminal.

A handle, the command a row says to type, is the one value never clipped. A
nudge row drops its status to fit one, and leaves off a command that still does
not fit.

Lines carrying content from a register, a Lab or a command are built as `Text`
rather than markup strings — `Text.append` does not parse `[...]`, so a bracket
in a description cannot be swallowed as a style tag.
"""

import re
import sys

from rich.console import Console
from rich.text import Text

# highlight=False: rich's automatic highlighter colors anything that looks like
# a number, path or URL, which turns a register id into a surprise.
console = Console(highlight=False)

# Everything that is not data. stdout carries the answer a caller parses; a
# diagnostic written there is what turns `doit review list --json` into a broken
# parse rather than a readable warning.
error_console = Console(stderr=True, highlight=False)

# Bound to the persistent --no-input flag by the root callback, which runs on
# every invocation and rewrites it — so an in-process run never inherits the last
# one's value.
no_input = False


def set_no_input(value: bool) -> None:
    global no_input
    no_input = value


def examples(*rows: tuple[str, str]) -> str:
    """A help screen's epilog: each command beside the situation it answers.

    A markdown list, because each row has to keep a line of its own. The root's
    markdown mode reaches every screen below it.
    """
    return '\n\n'.join(['**Examples**', *(f'- `{command}` — {purpose}' for command, purpose in rows)])


def can_prompt() -> bool:
    """Whether the user can be asked anything: --no-input never allows it, and
    otherwise stdin has to be a terminal.

    Lives beside the consoles because it answers the same question they do —
    who, if anyone, is on the other end. A prompt written to a stdin that never
    closes leaves the caller with no output and no exit code."""
    return not no_input and sys.stdin.isatty()


# Wide enough for "overdue 999d", the longest status_label, plus a space.
STATUS_WIDTH = 13

# A sentence ends at punctuation the next character does not continue. Matching
# the following space rather than the punctuation alone is what keeps `execute.go`
# and `os.Exit(1)` from being read as two sentences apiece.
SENTENCE_END = re.compile(r'[.!?](?=\s)')


def join_context(parts) -> str:
    """The facts that place an item, joined into one qualifier.

    An item is placed by more than one fact — the repo it lands in and the effort
    it serves — and which of them a backend carries varies row by row. Blanks drop
    out so a missing one leaves no dangling separator, and repeats drop out
    because a repo whose project shares its name is placed once, not twice.
    """
    seen = []
    for part in parts:
        text = str(part or '').strip()
        if text and text not in seen:
            seen.append(text)
    return ' · '.join(seen)


def first_sentence(text: str | None) -> str:
    """The opening sentence of a stored note, collapsed onto one line.

    Notes run to paragraphs — the reasoning, the alternatives that were rejected,
    what to check before starting. Clipping that at a column boundary spends the
    width on the middle of a word somewhere in the second paragraph. Taking the
    first sentence spends it on the gist and stops where the writer stopped.
    """
    collapsed = ' '.join((text or '').split())
    ended = SENTENCE_END.search(collapsed)
    return collapsed[: ended.end()] if ended else collapsed


def span_text(days: float) -> str:
    """A number of days in the coarsest unit that still says something.

    Days up to a fortnight, then weeks, then months. A span reported in the unit
    it was measured in reads as a measurement — `43d` invites arithmetic, where
    `6w` is the answer that arithmetic was for.

    Rounded, and floored at one of whatever unit it landed in. Truncating turns a
    real quantity into `0`, which a reader takes for none: two thirds of a day is
    a schedule this tool would have printed as `every 0d`, and nineteen days is
    two weeks rather than the three it nearly is.
    """
    days = abs(days)
    if days < 14:
        return f'{max(round(days), 1)}d'
    if days < 90:
        return f'{max(round(days / 7), 1)}w'
    return f'{max(round(days / 30), 1)}mo'


def nudge_header(title: str, count: int) -> None:
    """Print a one-line nudge heading (``Review · 6 due``)."""
    console.print(f'[cyan]{title} · {count} due[/]')


def nudge_width(names: list[str]) -> int:
    """The name-column width for a set of nudge rows."""
    return max((len(name) for name in names), default=0) + 2


def nudge_row(name: str, status: str, command: str, width: int) -> None:
    """Print one due item as a single line, never wrapped.

    A clipped command fails when typed, so the command is printed whole or not
    at all. The status gives way to it first: a row whose line cannot hold all
    three drops the status. A command too wide for the line even then is left
    off, and the row keeps its name and status. The browse view prints it whole.
    """
    line = Text('  ')
    line.append(name.ljust(width), style='yellow')
    handle = f'↳ {command}' if command else ''
    if handle and 2 + width + STATUS_WIDTH + len(handle) <= console.width:
        line.append(status.ljust(STATUS_WIDTH))
        line.append(handle, style='cyan')
    elif handle and 2 + width + len(handle) <= console.width:
        line.append(handle, style='cyan')
    else:
        line.append(status)
    console.print(line, no_wrap=True, overflow='ellipsis')
