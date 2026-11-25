"""The text helpers that write a row's cells: a joined context, a note's first sentence, and a span of days."""

import re

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
