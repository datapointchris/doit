"""doit's shared console, its column arithmetic, and its startup-nudge renderers.

A nudge is an interrupt you did not ask for, so it trades every browse-time field
(description, tags, cadence, last-done) for one line per item, and every line is
clipped to the terminal rather than wrapped. A wrapped row is two rows, which is
how the nudge grew the first time.

Two layers do the clipping. A view that wants columns sizes them here, against
the terminal width, and `fitted` truncates each value to the width it was given.
`console.print` then clips the assembled line with `no_wrap` and
`overflow='ellipsis'` as the backstop. Rich measures printable width at both
layers, so styling cannot shift a later column.

Lines carrying content from a register, a Lab or a command are built as `Text`
rather than markup strings — `Text.append` does not parse `[...]`, so a bracket
in a description cannot be swallowed as a style tag.
"""

import re
import shutil
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
_no_input = False


def set_no_input(value: bool) -> None:
    global _no_input
    _no_input = value


def can_prompt() -> bool:
    """Whether the user can be asked anything: --no-input never allows it, and
    otherwise stdin has to be a terminal.

    Lives beside the consoles because it answers the same question they do —
    who, if anyone, is on the other end. A prompt written to a stdin that never
    closes leaves the caller with no output and no exit code."""
    return not _no_input and sys.stdin.isatty()


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


def first_sentence(text: str) -> str:
    """The opening sentence of a stored note, collapsed onto one line.

    Notes run to paragraphs — the reasoning, the alternatives that were rejected,
    what to check before starting. Clipping that at a column boundary spends the
    width on the middle of a word somewhere in the second paragraph. Taking the
    first sentence spends it on the gist and stops where the writer stopped.
    """
    collapsed = ' '.join((text or '').split())
    ended = SENTENCE_END.search(collapsed)
    return collapsed[: ended.end()] if ended else collapsed


# A cap on how wide a row may grow, not a target. It stops a trailing column
# flying off to the right of an ultrawide terminal, where the eye has to track
# back across the whole line to reach the next row.
MAX_WIDTH = 140


def terminal_width() -> int:
    """How much width a multi-column view may spend."""
    return min(shutil.get_terminal_size(fallback=(80, 24)).columns, MAX_WIDTH)


def fitted(text: str, width: int, *, pad: bool = False, style: str = '') -> Text:
    """`text` as a Text no wider than `width`, ellipsized and optionally padded.

    A column's width is arithmetic the layout owns, so this truncates to a
    computed width rather than to the terminal's — `console.print` still clips
    the assembled line as a backstop.

    The style is carried here rather than passed to `Text.append`, which refuses
    one alongside a Text instance.

    A column granted no width renders nothing. `Text.truncate(0)` treats zero as
    no limit and hands back the whole value, so a layout that computed its way
    down to an empty column would emit its widest row there.
    """
    if width <= 0:
        return Text('')
    fitted_text = Text(text, style=style)
    fitted_text.truncate(width, overflow='ellipsis', pad=pad)
    return fitted_text


def column_width(width: int, values: list[str], share: float, minimum: int) -> int:
    """How wide a column gets: what it needs, bounded by its share of the line.

    Sized to the values actually on screen first, so a column whose rows are all
    empty reserves nothing and a column of short values stops stealing width from
    the titles beside it.
    """
    needed = max((len(value) for value in values), default=0)
    return min(needed, max(minimum, int(width * share)))


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
