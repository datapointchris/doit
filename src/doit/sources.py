"""Which apps doit asks, and what to do when one of them cannot answer.

doit must not know which sources a person asks. The sources document is the only
place that set is written down, so turning one on or off is an edit, never a
release, and nothing here imports a backend by name.

Two kinds of source, and the difference is whose job the shape is:

**Adapted** — the service reads the app and a named adapter turns its model into
lanes. The adapter is Python and lives with the dashboard, which is where it
belongs: what a lane looks like is doit's question, so doit is what answers it.
A tool that answers "what is open" should not also carry doit's row shape,
because its other consumers render the same data completely differently.

The read and the shape are one contract with the app, so both live in code, where
a sweep for a renamed route finds them. An entry in the sources document names the
adapter and says nothing of how it reads; one that declares a `command` beside an
adapter this doit has is told to delete the line.

**Conforming** — the source emits a lane document (see `doit.lanes`) directly and
doit renders it with no adapter. The service reads apps over HTTP and runs no
command, so it refuses a conforming source declared as one. Each refusal is the
source's lane, unavailable, saying so.

Two blocks, and the difference is which question the entry answers. `sources:`
answers what is outstanding and feeds `doit dashboard`. `completions:` answers
what happened over a window and feeds `doit happened` and `doit today`. An
entry in either is the same `Source`, so one parser and one failure policy serve
both — the block an entry sits in is the whole of what distinguishes it.

Failure policy is code, not config, because it must not vary by source. A source
absent from the file is silent — it is not configured, so it does not exist. One
that is configured but missing gets a single line naming it. One that runs and
fails shows its error. A configured lane is never silently dropped.
"""

import shlex
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from enum import StrEnum

from rich.text import Text

from doit import lanes
from doit.render import console
from doit.window import Bounds

TEMPLATE = """\
# Which apps doit asks, and what it asks them.
#
# This is the only place that says which sources doit asks, so turning one on
# or off is an edit here, never a release.
#
#   adapter   name an adapter in doit; the service reads its app over HTTP and
#             the adapter shapes the answer into lanes
#   command   argv printing a lane document, for a source with no adapter; the
#             service runs no command, so it shows the source as failed
#   lanes     optional; restricts which of a source's lanes are shown
#
# `doit sources contract` prints the lane document every source becomes, and
# `doit dashboard --json` a worked example.

# What is outstanding. Read by `doit dashboard`.
sources:
  icb:
    adapter: icb

  learning:
    adapter: learning

# What happened over a window. Read by `doit happened`, and by `doit today` for
# the day. Same three settings, and each adapter asks its app for the window.
completions:
  icb-habits:
    adapter: icb-habits

  icb-tasks:
    adapter: icb-tasks
"""


class Failure(StrEnum):
    FAILED = 'failed'


@dataclass(frozen=True)
class Source:
    """One configured backend, as the adapter and the command its entry declares."""

    id: str
    command: list[str] = field(default_factory=list)
    adapter: str = ''
    lanes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Result:
    """What one source call produced — facts only, no interpretation."""

    source: str
    payload: object | None = None
    exit_code: int | None = None
    stderr: str = ''
    failure: Failure | None = None
    # The window the call was asked about, so an adapter filters to the same one.
    bounds: Bounds | None = None


@dataclass
class Registry:
    """The configured sources, plus whatever the file itself got wrong.

    `sources` answers what is outstanding, `completions` answers what happened
    over a window. Two maps rather than one with a flag, because every caller wants one
    question or the other and none wants them interleaved — the dashboard would
    otherwise have to filter out the completion entries on every render.
    """

    sources: dict[str, Source] = field(default_factory=dict)
    completions: dict[str, Source] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def parse_block(declared: object, block: str, problems: list[str]) -> dict[str, Source]:
    """One block of the file as sources, collecting what it got wrong.

    Shared by both blocks so an entry means the same thing in either. A
    malformed entry is reported and skipped rather than raised: one bad source
    must not cost you the dashboard, and the message names the entry so it can
    be fixed.
    """
    if declared is None:
        return {}
    if not isinstance(declared, dict):
        problems.append(f'`{block}` must be a mapping of id to settings')
        return {}

    found = {}
    for name, config in declared.items():
        if not isinstance(config, dict):
            problems.append(f'{name}: must be a mapping of settings')
            continue
        adapter_name = str(config.get('adapter') or '')
        command = config.get('command')
        if adapter_name in ADAPTERS:
            # Reported rather than skipped, so the lane keeps answering while the
            # stale line waits to be deleted.
            if command is not None:
                problems.append(f'{name}: the {adapter_name} adapter has its own read — delete `command`')
            command = []
        else:
            if isinstance(command, str):
                command = shlex.split(command)
            if not isinstance(command, list) or not command:
                if adapter_name:
                    problems.append(f'{name}: this doit has no adapter named {adapter_name} — update doit, or correct `adapter:`')
                else:
                    problems.append(f'{name}: needs an `adapter` or a `command` to run')
                continue
        found[name] = Source(
            id=name,
            command=[str(part) for part in command],
            adapter=adapter_name,
            lanes=tuple(config.get('lanes') or ()),
        )
    return found


def registry_from_document(document: dict) -> Registry:
    """Both blocks of a parsed sources document.

    A block that is absent is an empty map rather than an error. A register
    with no `completions:` block gets `doit today` without the groups those
    sources would list, rather than a warning nobody can act on.

    An id used in both blocks is a problem rather than two entries. A round's
    results are keyed by id, so the two reads collide on one result and the
    lane the loser would have built disappears with nothing said. `icb` and
    `meso` are plausible in either block, which is what makes the collision
    reachable rather than theoretical.
    """
    registry = Registry()
    registry.sources = parse_block(document.get('sources'), 'sources', registry.problems)
    registry.completions = parse_block(document.get('completions'), 'completions', registry.problems)
    for shared in sorted(set(registry.sources) & set(registry.completions)):
        registry.problems.append(f'{shared} is declared in both sources and completions; an id names one source')
        del registry.completions[shared]
    return registry


def first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else ''


def reason(name: str, result: Result | None) -> str:
    """Turn a failed read into one actionable line."""
    if result is None:
        return f'{name} was not queried'
    if result.failure is Failure.FAILED:
        return first_line(result.stderr) or f'{name} failed'
    return ''


# What turns each non-conforming app's answer into lanes, by adapter name. Filled by
# the module that owns the knowledge, so this file imports no backend.
ADAPTERS: dict[str, Callable[[Result], list[lanes.Lane]]] = {}


def register_adapter(name: str, build: Callable[[Result], list[lanes.Lane]]) -> None:
    ADAPTERS[name] = build


def lanes_from(source: Source, result: Result) -> list[lanes.Lane]:
    """This source's lanes, however it chose to speak.

    The contract is tried first even when an adapter is registered, so an app
    that starts emitting lane documents is picked up the moment it does — its
    adapter stops being reached without anything here changing.
    """
    built = lanes.from_document(result.payload)
    missing = ''
    if not built and source.adapter:
        build = ADAPTERS.get(source.adapter)
        if build is None:
            missing = f'this doit has no adapter named {source.adapter} — update doit, or correct `adapter:` with doit sources edit'
        else:
            built = build(result)
    if source.lanes:
        # A conforming source names its lanes only in its payload, so a call that
        # failed produces no payload and the lane disappears — the silent drop
        # this module exists to prevent, and invisible precisely when a backend
        # is broken. `lanes` is the only declaration of what should have been
        # there, so it doubles as the fallback rather than staying a filter.
        if not built:
            why = missing or reason(source.id, result)
            return [lanes.unavailable(name, name.upper(), why) for name in source.lanes]
        built = [lane for lane in built if lane.name in source.lanes]
    return built


def answer(block: str, source: Source, result: Result) -> dict[str, object]:
    """One source's verdict: whether it answered, and with how many lanes in which dialect.

    `via` is `contract` for a lane document and `adapter:<name>` otherwise, and
    is null when there are no lanes to describe. `adapter` is what the entry
    declares, and `command` is empty for an entry whose adapter this doit has.
    `failure` and `reason` are null for a source that answered.
    """
    built = [] if result.failure else lanes_from(source, result)
    via = None
    if built:
        via = 'contract' if lanes.is_lane_document(result.payload) else f'adapter:{source.adapter}'
    return {
        'id': source.id,
        'block': block,
        'adapter': source.adapter or None,
        'command': list(source.command),
        'answered': result.failure is None,
        'failure': str(result.failure) if result.failure else None,
        'reason': reason(source.id, result) if result.failure else None,
        'lanes': len(built),
        'via': via,
    }


def report_block(title: str, answers: list[dict[str, object]], width: int) -> None:
    """One block's sources, each with whether it answered. Silent when empty.

    An empty block is a question nobody configured, which is not a problem to
    report — a heading over nothing would read as something missing.
    """
    if not answers:
        return
    console.print(Text(title, style='cyan'))
    for row in answers:
        line = Text('  ')
        line.append(str(row['id']).ljust(width), style='yellow')
        if not row['answered']:
            line.append(f'  {row["reason"]}', style='red')
        elif row['lanes']:
            line.append(f'  {row["lanes"]} lanes via {row["via"]}')
        else:
            line.append('  answered, but offered no lanes', style='yellow')
        console.print(line, no_wrap=True, overflow='ellipsis')
