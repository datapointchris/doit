"""What you own and never reach for.

The mirror of ``index.unresolved``, and the second of two folds with the same
shape: your kit joined against evidence, yielding a verdict per row.
``unresolved`` joins it against PATH, the shell files and this machine's manifest
and answers "you cannot run this"; this joins it against shell history and
answers "you can run it and you never do". Read together they separate rot from forgetting — a row that is
both unresolved and unused is a stale entry to delete, while one that resolves
fine and is unused is the thing worth resurfacing.

Counting folds ``observe.history_entries``, the read the review register already
makes and caches for the process. A second history parser is deliberately not
added: it would answer a subtly different question within a month, and then two
commands would disagree about whether you have used ``rg``.

**Rows are matched on what you type, not on what they are called.** A registry
key is a package name as often as a command — ``ripgrep`` installs ``rg`` — and
plenty of rows are subcommands, where the head word alone is actively wrong:
``git-forgit-add`` invokes ``git forgit add``, so keying it on ``git`` hands it
every git invocation on the machine and reports it as heavily used. Matching is
therefore ``observe.is_invocation_of`` against the row's literal invocation
prefix, which is the same word-boundary rule that stops a register item for
``claude /audit-repo`` claiming a ``claude /audit-repo-docs`` run.

Two limits worth knowing before trusting a number. A count is what you typed, so
an alias credits the alias and never the tool it expands to. And history records
only what you ran at a prompt — work driven through an agent or an editor leaves
no trace here, by design.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from datetime import timedelta

from doit.index import Entry
from doit.index import build_index
from doit.observe import Invocation
from doit.observe import history_entries
from doit.observe import is_invocation_of
from doit.tools import LEADING_KEYWORDS

# Lenses whose rows are commands you type, so shell history can answer for them.
#
# The others are excluded rather than reported as unused. A keybinding is
# pressed, whether at the zsh prompt or inside tmux; a workflow card is read; a
# Claude skill runs inside a session — none of them reaches a shell history, so
# counting them would report a permanent zero and call it disuse. An unmeasurable
# row is not a forgotten one.
MEASURABLE = ('tool', 'func', 'alias', 'git', 'forgit')

# Days without an invocation before something counts as unused. Long, because the
# question is whether you have stopped reaching for something and plenty of tools
# are genuinely seasonal — a report that fires at a fortnight is one you learn to
# ignore.
DEFAULT_DAYS = 90

# Words that carry a command rather than being one. `LEADING_KEYWORDS` covers the
# ones a registry `usage` string can carry; these appear only in recorded command
# lines, where crediting them would make `sudo` the most used thing on the
# machine and lose every privileged command underneath it.
COMMAND_CARRIERS = frozenset({'sudo', 'doas', 'command', 'time', 'nohup'})

# A usage string stops being literal at its first placeholder: `rg [pattern]`
# invokes `rg`, and matching the brackets would match nothing at all. A bare `...`
# or `|` ends it too, being an ellipsis or an alternation rather than a word.
#
# Matched on the opening bracket only, never on a leading `.`: the `..`, `...`
# and `....` aliases are real commands whose whole name is dots, and treating a
# dot as punctuation drops them from the report entirely.
PLACEHOLDER_STARTS = ('[', '<', '{')
PLACEHOLDER_TOKENS = frozenset({'...', '|'})


def is_placeholder(token: str) -> bool:
    """Whether a usage-string token stands for an argument rather than being one.

    Three spellings are in the registry at once: bracketed (`[pattern]`,
    `<name>`), an ellipsis or alternation, and bare uppercase — `bbkt pr VERB`,
    `dectl PIPELINE RESOURCE ACTION`, `jira issue VERB`. The last is the one that
    bites, because it looks like a literal word: left in the prefix it matches no
    command line that was ever typed, so those rows report as never used however
    much you use them.

    Length two is the floor so a genuinely uppercase single-letter command
    survives.
    """
    return token.startswith(PLACEHOLDER_STARTS) or token in PLACEHOLDER_TOKENS or (len(token) > 1 and token.isupper())


@dataclass(frozen=True)
class Row:
    """One thing you can type, beside how often you have.

    ``typed`` is both the handle and the join key. ``names`` and ``sources`` are
    plural because one typed command can be catalogued more than once — the
    registry documents several of your own shell functions, so they arrive as a
    ``tool`` row and a ``func`` row naming one thing.
    """

    typed: str
    sources: tuple[str, ...]
    names: tuple[str, ...]
    count: int
    last: str

    def days_since(self, today: date) -> int | None:
        """Days since it last ran, or None if it never has."""
        return None if not self.last else (today - date.fromisoformat(self.last)).days


def literal_prefix(invocation: str) -> str:
    """The part of a usage string that is a command rather than a placeholder.

    ``git forgit add`` keeps all three words, so it matches only itself;
    ``git [command] [options]`` keeps one, so it matches every git line. Getting
    this wrong in the other direction is what makes twenty-three registry rows
    inherit one count.
    """
    tokens = invocation.split()
    if tokens and tokens[0] in LEADING_KEYWORDS and len(tokens) > 1:
        tokens = tokens[1:]
    literal: list[str] = []
    for token in tokens:
        # The first token is taken as written: `...` is itself the name of a real
        # cd-up alias, and no usage string opens with a placeholder.
        if literal and is_placeholder(token):
            break
        literal.append(token)
    return ' '.join(literal)


def typed_form(entry: Entry) -> str:
    """What you would type to invoke this row.

    A git alias is spelled ``git co`` in history even though the collection calls
    it ``co``, and ``index`` already stores that in its invocation — so every
    lens answers through the same field rather than through a per-lens rule.
    """
    return literal_prefix(entry.invocation)


def normalized(command: str) -> str:
    """A recorded command line with any leading carrier or keyword removed."""
    tokens = command.split()
    while tokens and tokens[0] in COMMAND_CARRIERS:
        tokens = tokens[1:]
    if tokens and tokens[0] in LEADING_KEYWORDS and len(tokens) > 1:
        tokens = tokens[1:]
    return ' '.join(tokens)


def by_head(invocations: tuple[Invocation, ...]) -> dict[str, list[tuple[str, str]]]:
    """History bucketed by first word, as ``{head: [(command, date)]}``.

    Prefix matching cannot be a dict lookup, so without this every row would scan
    every invocation. A row's own first word bounds the scan to the lines that
    could possibly match it.
    """
    buckets: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for invocation in invocations:
        command = normalized(invocation.command)
        head, _, _ = command.partition(' ')
        if head:
            buckets[head].append((command, invocation.date))
    return buckets


def measure(entries: list[Entry] | None = None, invocations: tuple[Invocation, ...] | None = None) -> list[Row]:
    """Every measurable row of your kit, joined to what history says about it.

    Rows sharing a typed form are merged, because the unit of the answer is a
    thing you can type: showing ``checknode`` twice because it is catalogued
    twice would be reporting the catalogue, not your usage.

    A row absent from history is kept with a zero count rather than dropped —
    never having run it is the answer this exists to surface, so it has to be
    representable.
    """
    entries = build_index() if entries is None else entries
    invocations = history_entries() if invocations is None else invocations
    buckets = by_head(invocations)

    merged: dict[str, tuple[list[str], list[str]]] = {}
    for entry in entries:
        if entry.source not in MEASURABLE:
            continue
        typed = typed_form(entry)
        if not typed:
            continue
        sources, names = merged.setdefault(typed, ([], []))
        if entry.source not in sources:
            sources.append(entry.source)
        if entry.name not in names:
            names.append(entry.name)

    rows = []
    for typed, (sources, names) in merged.items():
        head, _, _ = typed.partition(' ')
        matches = [when for command, when in buckets.get(head, ()) if is_invocation_of(command, typed)]
        rows.append(
            Row(
                typed=typed,
                sources=tuple(sorted(sources)),
                names=tuple(sorted(names)),
                count=len(matches),
                last=max(matches) if matches else '',
            )
        )
    return rows


def unused(rows: list[Row], days: int = DEFAULT_DAYS, minimum: int = 1, today: date | None = None) -> list[Row]:
    """The tail: never run, run fewer than ``minimum`` times, or gone cold."""
    cutoff = ((today or date.today()) - timedelta(days=days)).isoformat()
    return [row for row in rows if row.count < minimum or row.last < cutoff]


def by_frequency(rows: list[Row]) -> list[Row]:
    """Most reached for first, so the tail sinks to the bottom on its own."""
    return sorted(rows, key=lambda row: (-row.count, row.typed))


def by_staleness(rows: list[Row]) -> list[Row]:
    """Longest neglected first. Never run outranks merely cold, whose lateness is bounded."""
    return sorted(rows, key=lambda row: (row.count > 0, row.last, row.typed))
