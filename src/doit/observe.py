"""Observed last-done: deriving when a register item was actually done.

A schedule needs one date per item: when it last happened. A date you declare by
typing ``doit review done <id>`` cannot go stale gracefully, because there is
nothing underneath it to re-check — an item you did but never reported reads
exactly like one you never did, and noticing what you have stopped thinking about
is the register's whole purpose.

An observer supplies evidence instead. Every kind answers one question — when did
this last actually happen — from a trace the act itself left behind: shell
history records the command you ran, another tool's state file records the work
it did. Nothing here writes; observation reads traces made elsewhere, for other
reasons.

Which trace to read is declared per item in the register rather than wired in
here, for the reason ``sources.yml`` exists: doit must not know which apps you
have. This module knows only the shapes a date can arrive in.

Shell history is the default observer because a register item already names the
command that does it, so "did I do this" and "did I run that" are one question.
It comes from atuin, which records the machine each command ran on and syncs
between them, falling back to this machine's zsh history when atuin cannot be
asked. The fallback still answers, but only about this box.

Watch out for two things. A command that *opens* work rather than doing it — a
picker, a review window, a ``claude /...`` session — must set ``observe: false``,
or abandoning it halfway counts as having done it. And work that is per-machine
rather than done once for everyone — updating this box's packages, checking its
PATH — must set ``scope: machine``, or the other desk doing it clears it here.
"""

import json
import os
import re
import subprocess
from datetime import date
from functools import cache
from pathlib import Path
from typing import NamedTuple

from doit.paths import canonical_host
from doit.paths import machine_name
from doit.paths import xdg_state_home

# zsh EXTENDED_HISTORY: `: <started>:<elapsed>;<command>`
HISTORY_LINE = re.compile(r'^: (\d+):\d+;(.*)$')

HISTORY = Path(os.environ.get('HISTFILE') or xdg_state_home() / 'zsh' / 'history')

ISO_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')

# Scope answers "did I do this *here*, or anywhere". Fleet is the default because
# most maintenance is done once for everyone — reindexing, an audit, relearning a
# tool. Per-machine work is the minority and says so.
FLEET = 'fleet'
MACHINE = 'machine'
SCOPES = (FLEET, MACHINE)

# `--include-duplicates` is required, not incidental: the default dedupes to the
# newest run of each distinct command across all hosts, which is exactly the row
# a machine-scoped question needs to still see for its own host.
ATUIN_QUERY = (
    'atuin',
    'search',
    '--search-mode',
    'prefix',
    '--include-duplicates',
    '--limit',
    '200000',
    '--format',
    '{time}|{host}|{command}',
    '',
)
ATUIN_TIMEOUT = 20


class Observation(NamedTuple):
    """A date an observer found, and any reason it could not look properly.

    A problem is carried rather than raised: the register is hand-edited config,
    so a typo here must degrade one item's date, never the dashboard that renders
    it. It is surfaced by ``doit review list`` — silence would leave a misspelled
    observer looking exactly like a task genuinely never done.
    """

    date: str | None = None
    problem: str = ''


class Invocation(NamedTuple):
    """One recorded command: the day it ran, the machine it ran on, and the text."""

    date: str
    host: str
    command: str


@cache
def history_entries() -> tuple[Invocation, ...]:
    """Every recorded shell invocation, from atuin if it answers and zsh if not.

    Read once per process and shared: both the dashboard and the review views ask
    about every register item, so a read per item would re-run the same query a
    dozen times.

    atuin is preferred because it is the only source that records *which machine*
    ran a command, which is what makes a machine-scoped item answerable at all —
    and because it is the store that syncs, so a command run at the other desk is
    visible here. The zsh file is the fallback rather than the poor relation: it
    is written unconditionally by `.zshrc`, so it answers on a box where atuin is
    not installed, not yet syncing, or simply broken.
    """
    return atuin_invocations() or zsh_invocations()


def atuin_invocations() -> tuple[Invocation, ...]:
    """Every command atuin holds, or nothing at all if it cannot be asked.

    Any failure is silence rather than an error: a machine without atuin, or one
    whose server is unreachable, still has the zsh history the caller falls back
    to, and a register that stopped rendering because a history tool was missing
    would be worse than one answering from the other source.
    """
    try:
        result = subprocess.run(ATUIN_QUERY, capture_output=True, text=True, timeout=ATUIN_TIMEOUT, check=False)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return ()
    if result.returncode != 0:
        return ()
    return parse_atuin_rows(result.stdout)


def parse_atuin_rows(stdout: str) -> tuple[Invocation, ...]:
    """`{time}|{host}|{command}` rows, as invocations."""
    entries = []
    for line in stdout.splitlines():
        # The command may itself contain the separator, so it takes the remainder
        # rather than a field of its own.
        parts = line.split('|', 2)
        if len(parts) != 3 or not ISO_DATE.match(parts[0]):
            continue
        entries.append(Invocation(parts[0][:10], canonical_host(parts[1]), parts[2].strip()))
    return tuple(entries)


def zsh_invocations() -> tuple[Invocation, ...]:
    """Every command in this machine's zsh history.

    Every row is attributed to this machine, because that is what the file is —
    which also means a machine-scoped question is answered correctly here, and a
    fleet-scoped one silently narrows to this box.
    """
    try:
        text = HISTORY.read_text(errors='replace')
    except OSError:
        return ()
    here = machine_name()
    entries = []
    for line in text.splitlines():
        match = HISTORY_LINE.match(line)
        if match:
            when = date.fromtimestamp(int(match.group(1))).isoformat()
            entries.append(Invocation(when, here, match.group(2).strip()))
    return tuple(entries)


def is_invocation_of(command: str, prefix: str) -> bool:
    """Whether ``command`` is ``prefix`` being run, not merely text starting with it.

    The boundary is load-bearing, not defensive tidiness: without it the item
    whose command is ``claude /audit-repo`` claims every ``claude
    /audit-repo-docs`` run and reports itself done on the strength of a different
    task entirely.
    """
    return command == prefix or command.startswith(prefix + ' ')


def last_run(prefix: str, scope: str = FLEET) -> Observation:
    """The date this command last ran, anywhere or only here.

    The newest matching date, not the last matching row: with several shells and
    several machines feeding one store, its order is write order, which is only
    approximately chronological.
    """
    if not prefix:
        return Observation()
    here = machine_name()
    dates = [
        entry.date for entry in history_entries() if is_invocation_of(entry.command, prefix) and (scope != MACHINE or entry.host == here)
    ]
    return Observation(max(dates)) if dates else Observation()


def newest_date_in(path: str) -> Observation:
    """The newest date among the values of a ``{name: date}`` JSON map.

    The shape other tools' round-robin state already has — ``toolbox`` stamps
    each tool with the day it last surfaced it — so "when did this last happen at
    all" is the newest value in the file. Reading a foreign state file is sound
    precisely because it is a read: the owning tool keeps writing it for its own
    reasons whether or not doit is looking.
    """
    resolved = Path(path).expanduser()
    if not resolved.exists():
        return Observation()
    try:
        payload = json.loads(resolved.read_text() or '{}')
    except OSError:
        return Observation(problem=f'cannot read {resolved}')
    except ValueError:
        return Observation(problem=f'{resolved} is not valid JSON')
    if not isinstance(payload, dict):
        return Observation(problem=f'{resolved} is not a map of name to date')
    dates = [value for value in payload.values() if isinstance(value, str) and ISO_DATE.fullmatch(value)]
    return Observation(max(dates)) if dates else Observation()


OBSERVERS = ('history', 'newest-date-in')


def observed(spec: object, command: str) -> Observation:
    """When this item was last actually done, from whatever trace it declares.

    ``spec`` is the item's ``observe:`` value:

    - absent — watch the item's own ``command`` in shell history, because that is
      already the item's own statement of what doing it looks like
    - ``false`` — observe nothing; the recorded ``doit review done`` date stands
      alone, for an item whose command is not the work (a picker, a browse view)
    - ``{kind: argument}`` — one named observer, for a trace that is not the
      command: ``{newest-date-in: ~/.local/state/toolbox/reminders.json}``
    - any of the above plus ``scope: machine`` — only runs on this machine count,
      for work that is per-box rather than done once for everyone. ``{scope:
      machine}`` on its own keeps the default observer and narrows it.
    """
    if spec is False:
        return Observation()
    if spec is None:
        return last_run(command)
    if not isinstance(spec, dict):
        return Observation(problem=f'expects false or a mapping, got {type(spec).__name__}')

    scope = spec.get('scope', FLEET)
    if scope not in SCOPES:
        return Observation(problem=f'unknown scope {scope!r} — expected one of {", ".join(SCOPES)}')

    kinds = {key: value for key, value in spec.items() if key != 'scope'}
    if not kinds:
        return last_run(command, scope)
    if len(kinds) != 1:
        return Observation(problem=f'expects one observer, got {len(kinds)}: {", ".join(sorted(kinds))}')

    ((kind, argument),) = kinds.items()
    if kind == 'history':
        return last_run(str(argument), scope)
    if kind == 'newest-date-in':
        # A state file records that something happened, never where, so narrowing
        # it to one machine would silently answer a different question.
        if scope != FLEET:
            return Observation(problem="newest-date-in records no machine, so it cannot take scope: 'machine'")
        return newest_date_in(str(argument))
    return Observation(problem=f'unknown observer {kind!r} — expected one of {", ".join(OBSERVERS)}')


def newest(*dates: str | None) -> str | None:
    """The latest of several ISO dates, ignoring the ones nobody supplied.

    Newest-wins rather than observation-replaces-record: a stamp and an
    invocation are both true evidence of the same act, so neither may drag the
    date backwards. It also means adding an observer to an item can only ever
    improve its date, never lose the history already recorded for it.
    """
    known = [value for value in dates if value]
    return max(known) if known else None
