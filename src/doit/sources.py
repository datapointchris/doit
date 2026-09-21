"""Which apps doit asks, and what to do when one of them cannot answer.

doit must not know which apps exist. Adding one is an edit to `sources.yml`,
never a release — that file is the only place the set of backends is written
down, and nothing here imports a backend by name.

Two kinds of source, and the difference is whose job the shape is:

**Adapted** — the source prints its own model and a named adapter turns it into
lanes. The adapter is Python and lives with the dashboard, which is where it
belongs: what a lane looks like is doit's question, so doit is what answers it.
A tool that answers "what is open" should not also carry doit's row shape,
because its other consumers render the same data completely differently.

**Conforming** — the source emits a lane document (see `doit.lanes`) directly and
doit renders it with no adapter. Available, and the right choice only for a tool
whose entire reason to exist is feeding this dashboard.

Two blocks, and the difference is which question the entry answers. `sources:`
answers what is outstanding and feeds `doit dashboard`. `completions:` answers
what happened today and feeds `doit today`. An entry in either is the same
`Source`, so one parser, one runner and one failure policy serve both — the
block an entry sits in is the whole of what distinguishes it.

A completions command needs today's date, and `{today}` is the only token
substituted into one. `resolved_command` carries what is matched and why.

Failure policy is code, not config, because it must not vary by source. A source
absent from the file is silent — it is not configured, so it does not exist. One
that is configured but missing gets a single line naming it. One that runs and
fails shows its error. A configured lane is never silently dropped.
"""

import json
import os
import shlex
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import typer
import yaml
from rich.text import Text

from doit import lanes
from doit.paths import xdg_config_home
from doit.render import console
from doit.render import error_console

SOURCES = Path(os.environ.get('DOIT_SOURCES') or xdg_config_home() / 'doit' / 'sources.yml')

# Generous against a measured happy path of well under a second. The sources run
# concurrently, so this is the worst-case total rather than a per-source penalty.
DEFAULT_TIMEOUT_SECONDS = 5.0

# The one substitution a command may ask for. `resolved_command` says what it
# matches against.
TODAY_TOKEN = '{today}'

TEMPLATE = """\
# Which apps doit asks, and what it asks them.
#
# doit does not know which apps exist — this file is the only place that says.
# Adding one is an edit here, never a release.
#
#   command   argv to run; it must print a lane document on stdout
#   timeout   seconds to wait (default 5)
#   adapter   name a Python adapter in doit that shapes this source's own model
#   lanes     optional; restricts which of a source's lanes are shown
#
# A source emitting doit's own lane document needs no adapter. Run
# `doit sources contract` for the shape, or `doit dashboard --json` for a
# worked example.

# What is outstanding. Read by `doit dashboard`.
sources:
  icb:
    command: [icb, overview, --json]
    adapter: icb

  learning:
    command: [learning, overview, --json]
    adapter: learning

# What happened today. Read by `doit today`. Same four settings, and `{today}`
# in place of an argv part becomes the local ISO date — a whole part only, so a
# backend gets the date as its own argument.
completions:
  icb-tasks:
    command: [icb, tasks, list, --status, completed, --start, '{today}', --json]
    adapter: icb-tasks
"""


class Failure(StrEnum):
    NOT_CONFIGURED = 'not-configured'
    NOT_INSTALLED = 'not-installed'
    TIMED_OUT = 'timed-out'
    FAILED = 'failed'
    UNREADABLE = 'unreadable'


@dataclass(frozen=True)
class Source:
    """One configured backend."""

    id: str
    command: list[str]
    timeout: float = DEFAULT_TIMEOUT_SECONDS
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
    timeout: float | None = None


@dataclass
class Registry:
    """The configured sources, plus whatever the file itself got wrong.

    `sources` answers what is outstanding, `completions` answers what happened
    today. Two maps rather than one with a flag, because every caller wants one
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
        command = config.get('command')
        if isinstance(command, str):
            command = shlex.split(command)
        if not isinstance(command, list) or not command:
            problems.append(f'{name}: needs a `command` to run')
            continue
        found[name] = Source(
            id=name,
            command=[str(part) for part in command],
            timeout=float(config.get('timeout') or DEFAULT_TIMEOUT_SECONDS),
            adapter=str(config.get('adapter') or ''),
            lanes=tuple(config.get('lanes') or ()),
        )
    return found


def load(path: Path | None = None) -> Registry:
    """Read `sources.yml`, both blocks.

    A block that is absent is an empty map rather than an error. A machine with
    no `completions:` block gets `doit today` without the groups those sources
    would list, rather than a warning it cannot act on.

    An id used in both blocks is a problem rather than two entries. `fetch` keys
    its results by id, so the two commands collide on one result and the lane
    the loser would have built disappears with nothing said. `icb` and `meso`
    are plausible in either block, which is what makes the collision reachable
    rather than theoretical.
    """
    path = SOURCES if path is None else path
    registry = Registry()
    if not path.exists():
        return registry
    document = yaml.safe_load(path.read_text()) or {}
    registry.sources = parse_block(document.get('sources'), 'sources', registry.problems)
    registry.completions = parse_block(document.get('completions'), 'completions', registry.problems)
    for shared in sorted(set(registry.sources) & set(registry.completions)):
        registry.problems.append(f'{shared} is declared in both sources and completions; an id names one command')
        del registry.completions[shared]
    return registry


def resolved_command(command: list[str], today: date | None = None) -> list[str]:
    """The argv to run, with `{today}` replaced by the local ISO date.

    Equality rather than a substring replace. A backend takes the date as its
    own argument, so splicing it into a longer string has no caller — and
    refusing that case means a path or a title that happens to contain the
    token is passed through untouched rather than silently rewritten.
    """
    stamp = (today or date.today()).isoformat()
    return [stamp if part == TODAY_TOKEN else part for part in command]


def run(source: Source) -> Result:
    command = resolved_command(source.command)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=source.timeout, check=False)
    except FileNotFoundError:
        return Result(source=source.id, failure=Failure.NOT_INSTALLED)
    except subprocess.TimeoutExpired:
        return Result(source=source.id, failure=Failure.TIMED_OUT, timeout=source.timeout)

    stderr = completed.stderr.strip()
    if completed.returncode != 0:
        return Result(source=source.id, exit_code=completed.returncode, stderr=stderr, failure=Failure.FAILED)
    try:
        return Result(source=source.id, payload=json.loads(completed.stdout), exit_code=0, stderr=stderr)
    except json.JSONDecodeError:
        return Result(source=source.id, exit_code=0, stderr=stderr, failure=Failure.UNREADABLE)


def fetch(sources: list[Source]) -> dict[str, Result]:
    """Every source, concurrently. Only what was asked for is called."""
    if not sources:
        return {}
    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        return {result.source: result for result in pool.map(run, sources)}


def first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else ''


def reason(name: str, result: Result | None) -> str:
    """Turn a failed call into one actionable line."""
    if result is None:
        return f'{name} was not queried'
    if result.failure is Failure.NOT_CONFIGURED:
        return f'{name} is not in sources.yml'
    if result.failure is Failure.NOT_INSTALLED:
        return f'{name} is not installed on this machine'
    if result.failure is Failure.TIMED_OUT:
        return f'{name} timed out after {result.timeout:g}s'
    if result.failure is Failure.UNREADABLE:
        return f'{name} did not return JSON'
    if result.failure is Failure.FAILED:
        # Exit 2 is a usage error across these CLIs, so a rejected command means
        # the installed binary predates it.
        if result.exit_code == 2:
            return f'installed {name} does not understand `{" ".join(result_command(name))}` — reinstall it'
        return first_line(result.stderr) or f'{name} exited {result.exit_code}'
    return ''


def result_command(name: str) -> list[str]:
    """The configured command for a source id, for use in an error message.

    Both blocks, because an id is unique across the file and a caller holding a
    failed `Result` has no idea which block the entry came from.
    """
    registry = load()
    source = registry.sources.get(name) or registry.completions.get(name)
    return source.command[1:] if source else []


# An adapter turns a non-conforming source's own model into lanes. Registered by
# the module that owns the knowledge, so this file imports no backend.
Adapter = Callable[[Result], list[lanes.Lane]]
ADAPTERS: dict[str, Adapter] = {}


def register_adapter(name: str, adapter: Adapter) -> None:
    ADAPTERS[name] = adapter


def lanes_from(source: Source, result: Result) -> list[lanes.Lane]:
    """This source's lanes, however it chose to speak.

    The contract is tried first even when an adapter is registered, so an app
    that starts emitting lane documents is picked up the moment it does — its
    adapter stops being reached without anything here changing.
    """
    built = lanes.from_document(result.payload)
    if not built and source.adapter:
        built = ADAPTERS.get(source.adapter, lambda _: [])(result)
    if source.lanes:
        # A conforming source names its lanes only in its payload, so a call that
        # failed produces no payload and the lane disappears — the silent drop
        # this module exists to prevent, and invisible precisely when a backend
        # is broken. `lanes` is the only declaration of what should have been
        # there, so it doubles as the fallback rather than staying a filter.
        if not built:
            return [lanes.unavailable(name, name.upper(), reason(source.id, result)) for name in source.lanes]
        built = [lane for lane in built if lane.name in source.lanes]
    return built


app = typer.Typer(name='sources', no_args_is_help=True, help='Which apps doit asks, and what it asks them.')


@app.command('list')
def list_command() -> None:
    """Every configured source, and whether it answers."""
    registry = load()
    for problem in registry.problems:
        error_console.print(Text(f'sources.yml: {problem}', style='yellow'))
    if not registry.sources and not registry.completions:
        console.print(Text(f'No sources configured in {SOURCES}.'))
        console.print('Write one with [cyan]doit sources example[/].')
        raise typer.Exit(0)

    # One call for the whole file, so the two blocks cost what the slower of
    # them costs rather than the sum.
    everything = list(registry.sources.values()) + list(registry.completions.values())
    results = fetch(everything)
    width = max(len(source.id) for source in everything)
    report_block('outstanding', registry.sources, results, width)
    report_block('today', registry.completions, results, width)
    raise typer.Exit(0)


def report_block(title: str, block: dict[str, Source], results: dict[str, Result], width: int) -> None:
    """One block's sources, each with whether it answered. Silent when empty.

    An empty block is a question nobody configured, which is not a problem to
    report — a heading over nothing would read as something missing.
    """
    if not block:
        return
    console.print(Text(title, style='cyan'))
    for name, source in block.items():
        result = results[name]
        built = lanes_from(source, result)
        line = Text('  ')
        line.append(name.ljust(width), style='yellow')
        if result.failure:
            line.append(f'  {reason(name, result)}', style='red')
        elif built:
            speaks = 'contract' if lanes.is_lane_document(result.payload) else f'adapter:{source.adapter}'
            line.append(f'  {len(built)} lanes via {speaks}')
        else:
            line.append('  answered, but offered no lanes', style='yellow')
        console.print(line, no_wrap=True, overflow='ellipsis')


@app.command('example')
def example_command() -> None:
    """Print an annotated sources.yml without writing a file."""
    print(TEMPLATE)
    raise typer.Exit(0)


@app.command('edit')
def edit_command() -> None:
    """Edit sources.yml in $EDITOR, creating it from the example if absent."""
    if not SOURCES.exists():
        SOURCES.parent.mkdir(parents=True, exist_ok=True)
        SOURCES.write_text(TEMPLATE)
    subprocess.run([os.environ.get('EDITOR', 'vi'), str(SOURCES)], check=False)
    raise typer.Exit(0)


@app.command('contract')
def contract_command() -> None:
    """The lane document a source may emit, as an annotated example.

    This is the interface. A tool printing this shape needs no adapter, no
    mapping and no release of doit to appear on the dashboard.
    """
    example = lanes.to_document(
        [
            lanes.Lane(
                name='trips',
                title='TRIPS',
                meta='2 planned',
                rows=[lanes.Row(label='next', text='Lisbon', note='in 12d', urgency=lanes.Urgency.DUE)],
                total=2,
                hints=['nomad trips list'],
            )
        ],
        datetime(2026, 8, 6, 12, 0, 0),
    )
    print(json.dumps(example, indent=2))
    raise typer.Exit(0)
