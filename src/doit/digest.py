"""What your usage table says about you, read back as prose.

``kit usage`` ranks what you reach for and ``kit unused`` is its tail. Both
answer a question about one row at a time; neither answers the question about
the shape of the whole table — what you learned and then dropped, what you
catalogd and never ran, where two rows are one habit spelled twice. That is a
reading task rather than a counting one, so it is handed to a model.

**The payload is aggregate by construction, and that is what makes sending it
acceptable.** A :class:`doit.usage.Row` is your catalog joined to history:
``typed``, ``sources`` and ``names`` are built from the registry and the shell
files, and history contributes a count and a date and nothing else. No recorded
command line ever reaches a Row, so none can reach a prompt built from Rows.
:data:`PAYLOAD_FIELDS` narrows it again to the four fields the reading needs,
and it filters the row *after* serializing it, so a field added to ``Row``
upstream is dropped rather than silently joining the payload.

The session is denied every tool as well, so it cannot go and open the history
file those counts were derived from.

**The payload is not the whole of what gets sent, and bounding the rest takes a
flag.** ``claude -p`` injects the machine's own configuration ahead of the
prompt, and the user-level ``CLAUDE.md`` loads out of the home directory whatever
the working directory is — so running somewhere neutral bounds the *project*
memory and nothing else. Measured against a loopback endpoint on Claude Code
2.1.229, the same call made twice and differing only in that flag: the first user
message was 64,551 characters without it and 373 with. What survives the flag is
the account email and the date, which the CLI injects either way. ``--bare``
reads as the stronger form of the same flag and is not one — it breaks the OAuth
session a scheduled run authenticates with.

``claude -p`` is a network call, so it happens only when asked: ``run``, or a
schedule that invokes ``run``. ``show`` and ``list`` read what a run wrote and
never reach the network, which is why they are separate verbs rather than one
verb and a flag.

Readings are kept under the state directory rather than the cache directory
because a recompute cannot rebuild one — a second call costs another request
and reads a table that has moved since. One append-only file per machine, for
the reason :mod:`doit.journal` gives at length: Syncthing resolves conflicts per
file, so two machines appending to one file lose a tail.
"""

import dataclasses
import datetime as dt
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from doit import usage
from doit.paths import machine_name
from doit.paths import xdg_state_home
from doit.render import console
from doit.render import error_console

SCHEMA_VERSION = 1

# The only fields that may reach the prompt. Applied to a serialized row rather
# than written out by hand, so a field added to `usage.Row` has to be added here
# too before it can travel. Every string here originates in the catalog and
# every number in the counts, which is the property that makes the payload safe
# to send at all — `last` is excluded because a date says when you were at a
# keyboard and `days_since` answers the same question about the row.
PAYLOAD_FIELDS = ('typed', 'sources', 'count', 'days_since')

# What the session may not do. `--allowed-tools` pre-approves rather than
# confines, so the deny list is the only flag that restricts anything —
# `--allowed-tools "Read,Glob,Grep"` left Bash in the tool set on Claude Code
# 2.1.x. Everything is denied because the reading needs nothing: the table is in
# the prompt, and a session that can read a file could open the shell history
# this payload exists to stay clear of.
DENIED_TOOLS = ('Bash', 'Read', 'Write', 'Edit', 'NotebookEdit', 'Glob', 'Grep', 'WebFetch', 'WebSearch', 'Task')

# Replaces the session's output style for the call. Without it the active style
# wraps the answer in headings and commentary, which is what gets stored and
# read back months later.
SYSTEM_PROMPT = (
    'You read one table of aggregated command statistics and reply with short plain-text prose. '
    'No preamble, no headings, no bullet lists, no markdown, no code fences. '
    'You have no tools and no access to the machine: the table in the message is everything you know.'
)

# Generous, because one reading is one request and a wedged call should fail
# rather than hang a scheduled run forever.
DEFAULT_TIMEOUT_SECONDS = 300

# Short, because it only stamps a reading. A CLI too slow to say its own version
# is one this call gives up on rather than one that delays the reading itself.
VERSION_TIMEOUT_SECONDS = 10

# Empty because the call names no model and the CLI's default is whatever the
# session resolves. Recording a guess would be worse than recording nothing —
# a wrong attribution is read as fact and cannot be told from a right one.
MODEL = ''

# How many handles a miss names before it points at `list` instead. An error is
# read at a glance, and readings accumulate for as long as the schedule runs, so
# printing the whole record buries the sentence that says what went wrong.
HANDLES_ON_MISS = 5

STATE_DIR = xdg_state_home() / 'doit'


class Failure(StrEnum):
    """Why a reading could not be taken, as a key rather than as a sentence.

    The four want different reactions — an absent binary is a machine to fix, a
    timeout is worth retrying, an empty answer is not — so a caller has to be
    able to tell them apart, which a printable string does not allow. Wording
    lives once in :data:`FAILURE_TEXT` keyed by this, so a sentence can be
    rewritten without a test noticing.
    """

    NOT_INSTALLED = 'not-installed'
    TIMED_OUT = 'timed-out'
    FAILED = 'failed'
    EMPTY = 'empty'


FAILURE_TEXT: dict[Failure, str] = {
    Failure.NOT_INSTALLED: 'claude is not installed on this machine, so no reading can be taken.',
    Failure.TIMED_OUT: 'claude did not answer within {detail}.',
    Failure.FAILED: 'claude failed: {detail}',
    Failure.EMPTY: 'claude returned nothing.',
}


class DigestFailed(Exception):
    """The reading could not be taken.

    ``reason`` is the key a caller branches on and a test asserts; the sentence
    is derived from it, and ``detail`` carries whatever the runtime knew that the
    wording could not.
    """

    def __init__(self, reason: Failure, detail: str = '') -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(FAILURE_TEXT[reason].format(detail=detail))


@dataclass(frozen=True)
class Digest:
    """One stored reading, beside what it was taken from.

    ``rows`` and ``days`` are recorded because the reading is only interpretable
    against the table that produced it: a digest naming three cold tools means
    something different when the threshold was a fortnight than when it was a
    season.

    ``model`` and ``claude_version`` are the other half of the same question. A
    reading that moved because the model changed is otherwise indistinguishable
    from one that moved because your kit did, and that comparison is the whole
    reason readings are kept rather than printed and dropped.

    Both may be empty and every reader carries on regardless. ``model`` is empty
    wherever the call named none, which leaves the choice to the CLI and doit
    with nothing it can honestly record. ``claude_version`` is empty where that
    CLI would not answer. A digest stored before the fields carries neither.
    """

    generated: str
    machine: str
    rows: int
    days: int
    text: str
    model: str = ''
    claude_version: str = ''


def row_payload(row: usage.Row, today: dt.date) -> dict[str, object]:
    """One row reduced to the fields a reading is allowed to see.

    The row is serialized whole and then filtered, which is what makes this an
    allowlist rather than a hand-copied subset: a new field on ``Row`` arrives in
    ``derived`` and is dropped here, and a field that disappears raises a
    ``KeyError`` instead of silently narrowing the payload.
    """
    derived = dict(dataclasses.asdict(row))
    derived['days_since'] = row.days_since(today)
    return {name: derived[name] for name in PAYLOAD_FIELDS}


def payload(rows: list[usage.Row], today: dt.date) -> list[dict[str, object]]:
    """The whole table, most-reached-for first, as the model will receive it."""
    return [row_payload(row, today) for row in usage.by_frequency(rows)]


def build_prompt(rows: list[usage.Row], today: dt.date, days: int) -> str:
    """The message sent to the model: the table, what its fields mean, and the questions.

    The field glossary is here rather than left implicit because ``sources`` is
    this repo's own vocabulary — a reader who does not know that ``func`` means a
    shell function you wrote yourself cannot tell an unused tool from an unused
    habit, and those want opposite reactions.
    """
    table = json.dumps(payload(rows, today), separators=(',', ':'))
    return f"""Read one person's command-line toolkit and say what it shows.

Each row is something they have catalogd as theirs, joined to how often they
have typed it at a shell prompt. A row counts as cold after {days} days.

FIELDS
  typed       what they type to invoke it
  sources     which collections catalog it — tool is a registry entry, func and
              alias are shell definitions they wrote, git and forgit are git aliases
  count       times it was typed
  days_since  days since it last ran, or null if it never has

TABLE
{table}

WHAT TO WRITE

Answer these in order, as continuous prose:

1. What is genuinely being reached for, and what that pattern says about how they work.
2. What was learned and then dropped — rows with a real count that have since gone
   cold. These are the most interesting, because someone bothered to catalog and
   use them before stopping.
3. What is catalogd and has never run at all. Separate the ones worth resurfacing
   from the ones that look like stale entries to delete, and say which is which.
4. Where two rows look like one habit spelled twice — an alias beside the tool it
   wraps, or two rows doing the same job.

Rules:
- Name specific rows. A sentence that would be true of any table is not worth writing.
- Do not restate counts they can already read. Say what a count means.
- Only commands typed at a prompt are recorded here, so anything usually driven by an
  agent or an editor leaves no trace. Where a low count looks like that, say so instead
  of concluding disuse.
- At most six short paragraphs of plain text.
"""


def claude_version(timeout: float = VERSION_TIMEOUT_SECONDS) -> str:
    """The version of the claude on PATH, or '' where it will not answer.

    The first field alone: `claude --version` says "2.1.238 (Claude Code)" and
    the rest of the line is the product name. A failure here is never fatal — a
    reading is worth more than its attribution, and one taken by a CLI that
    cannot say its own version is still a reading.
    """
    if not shutil.which('claude'):
        return ''
    try:
        result = subprocess.run(['claude', '--version'], capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ''
    if result.returncode != 0:
        return ''
    return next(iter(result.stdout.split()), '')


def ask(prompt: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> str:
    """Send the prompt to `claude -p` and return what it wrote.

    The prompt goes over stdin rather than argv: a two-hundred-row table is an
    argument list a shell refuses, and argv is readable from `ps` by anything on
    the machine.

    `--safe-mode` is what keeps the payload the payload. Without it the session
    loads the machine's own configuration and sends it ahead of the prompt, so a
    module that filters a table field by field ships an unbounded personal
    document beside it. Nothing about the answer shows that the flag is missing.

    The call also runs in a scratch directory, which bounds the working directory
    independently of what any one flag means: `doit` runs from wherever you
    happen to be standing, and a reading shaped by whichever repo that was is not
    reproducible.
    """
    if not shutil.which('claude'):
        raise DigestFailed(Failure.NOT_INSTALLED)

    command = [
        'claude',
        '-p',
        '--safe-mode',
        '--system-prompt',
        SYSTEM_PROMPT,
        '--disallowed-tools',
        ','.join(DENIED_TOOLS),
    ]
    with tempfile.TemporaryDirectory(prefix='doit-digest-') as scratch:
        try:
            result = subprocess.run(command, input=prompt, text=True, capture_output=True, cwd=scratch, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as expired:
            raise DigestFailed(Failure.TIMED_OUT, f'{timeout:.0f}s') from expired

    if result.returncode != 0:
        raise DigestFailed(Failure.FAILED, result.stderr.strip() or f'exit {result.returncode}')
    text = result.stdout.strip()
    if not text:
        raise DigestFailed(Failure.EMPTY)
    return text


def digest_path(directory: Path, machine: str) -> Path:
    """This machine's readings. One writer per file is the whole sync story."""
    return directory / f'usage-digest-{machine}.jsonl'


def append(path: Path, digest: Digest) -> Digest:
    """Store one reading as a JSON line, stamping the schema version.

    Append-only rather than rewritten whole: a reading costs a request and cannot
    be reconstructed, so the file has to survive a crash mid-write and a machine
    that syncs late.
    """
    record = {'schema_version': SCHEMA_VERSION, **dataclasses.asdict(digest)}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record) + '\n')
    return digest


def store(path: Path, digest: Digest) -> bool:
    """Append the reading, reporting a write failure instead of raising through it.

    Two things are at stake and only one of them can be recovered. The request is
    spent whether or not the file is writable, so a caller that lets the write
    take the process down loses the answer it already paid for; a caller that
    prints first loses a record another run can replace. The exit code still says
    something went wrong, because a schedule whose record silently stops arriving
    has no other way to find out.
    """
    try:
        append(path, digest)
    except OSError as failure:
        error_console.print(f'Reading taken but not stored at {path} — {failure}')
        return False
    return True


def read_all(directory: Path) -> list[Digest]:
    """Every machine's readings, merged and ordered oldest first.

    A malformed line is skipped rather than fatal, for the reason the journal
    gives: one half-synced line must not make the rest of the record unreadable.
    """
    stored: list[Digest] = []
    for path in sorted(directory.glob('usage-digest-*.jsonl')):
        for line in path.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            fields = {name: record.get(name) for name in ('generated', 'machine', 'rows', 'days', 'text')}
            if fields['generated'] and fields['text']:
                stored.append(
                    Digest(
                        generated=str(fields['generated']),
                        machine=str(fields['machine'] or ''),
                        rows=int(fields['rows'] or 0),
                        days=int(fields['days'] or 0),
                        text=str(fields['text']),
                        # Defaulted rather than required: a line written before
                        # these fields existed carries neither and must still load.
                        model=str(record.get('model') or ''),
                        claude_version=str(record.get('claude_version') or ''),
                    )
                )
    return sorted(stored, key=lambda digest: digest.generated)


def select(stored: list[Digest], handle: str = '') -> Digest | None:
    """The reading a handle names, or the newest one when no handle is given.

    A handle is any prefix of the stored timestamp, so the date printed above a
    digest is what you type to get it back. Several readings on one date resolve
    to the newest, which is the same answer the bare form gives.
    """
    matching = [digest for digest in stored if digest.generated.startswith(handle)] if handle else stored
    return matching[-1] if matching else None


def cmd_run(days: int, directory: Path) -> int:
    """Take a reading and store it."""
    today = dt.date.today()
    rows = usage.measure()
    if not rows:
        error_console.print('Nothing measurable in your kit, so there is nothing to read.')
        return 1

    error_console.print(f'Reading {len(rows)} rows with claude — this takes a minute.')
    try:
        text = ask(build_prompt(rows, today, days))
    except DigestFailed as failure:
        error_console.print(str(failure))
        return 1

    digest = Digest(
        generated=dt.datetime.now(dt.UTC).isoformat(timespec='seconds'),
        machine=machine_name(),
        rows=len(rows),
        days=days,
        text=text,
        model=MODEL,
        claude_version=claude_version(),
    )
    print(digest.text)
    return 0 if store(digest_path(directory, digest.machine), digest) else 1


def emit(digest: Digest) -> None:
    """The stored record as JSON on stdout, which is the only thing that goes there."""
    print(json.dumps(dataclasses.asdict(digest), indent=2))


def report_no_match(handle: str, kept: list[Digest]) -> int:
    """Say what to do next, which is a different thing in each of the two cases.

    With nothing stored, spending a request is the only move there is. With
    readings stored, the handle was mistyped — and `run` would then spend a
    request to recover from a typo, while the handles that do resolve fix it for
    nothing.
    """
    if not kept:
        error_console.print('No reading stored yet.')
        error_console.print('Take one with [cyan]doit kit digest run[/].')
        return 1
    error_console.print(f'No reading stored for {handle!r}. Stored readings, newest first:')
    for digest in reversed(kept[-HANDLES_ON_MISS:]):
        error_console.print(f'  [cyan]{digest.generated}[/] · {digest.machine}')
    if len(kept) > HANDLES_ON_MISS:
        error_console.print('See the rest with [cyan]doit kit digest list[/].')
    return 1


def cmd_show(handle: str, as_json: bool, directory: Path) -> int:
    """Print a stored reading without touching the network."""
    kept = read_all(directory)
    chosen = select(kept, handle)
    if chosen is None:
        return report_no_match(handle, kept)
    if as_json:
        emit(chosen)
        return 0
    console.rule(f'[cyan]{chosen.generated} · {chosen.machine}', align='left')
    console.print(chosen.text)
    return 0


def cmd_list(as_json: bool, directory: Path) -> int:
    """Name every stored reading, oldest first, so `show` has a handle to be given."""
    kept = read_all(directory)
    # An empty record is an empty array, not the prose hint: --json is parsed by
    # whatever asked for it, and a sentence on stdout is a parse error rather
    # than the readable line it looks like.
    if as_json:
        # Plain print, never the rich console: a Console soft-wraps at terminal
        # width, which would put newlines inside JSON strings and hand a consumer
        # a parse error instead of data.
        print(json.dumps([dataclasses.asdict(digest) for digest in kept], indent=2))
        return 0
    if not kept:
        console.print('No reading stored yet.')
        console.print('Take one with [cyan]doit kit digest run[/].')
        return 0
    console.rule('[cyan]Readings', align='left')
    for digest in kept:
        console.print(f'  [cyan]{digest.generated}[/] · {digest.machine} · {digest.rows} rows, cold after {digest.days}d')
    console.print('\nRead one:  [cyan]doit kit digest show <handle>[/]')
    return 0


app = typer.Typer(name='digest', no_args_is_help=True, help='What your usage table says, read back as prose.')


DaysOption = Annotated[int, typer.Option('--days', help='Days without a run before a row counts as cold.')]

JsonOption = Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')]


@app.command('run')
def digest_run_command(days: DaysOption = usage.DEFAULT_DAYS) -> None:
    """Read your usage table with claude and store what it says.

    The only command here that reaches the network, and one run is one request.
    It sends the aggregated table — what you type, how often, how long ago — and
    never a command line.

        doit kit digest run             read the table as it stands today
        doit kit digest run --days 30   count a row cold after a month, not a season
    """
    raise typer.Exit(cmd_run(days, STATE_DIR))


@app.command('show')
def digest_show_command(
    when: Annotated[str | None, typer.Argument(help='A stored timestamp, or any prefix of one. Omitted, the newest.')] = None,
    as_json: JsonOption = False,
) -> None:
    """Print a reading a run already took, without spending another.

    Handles come from `doit kit digest list`, and a date is a handle because any
    prefix of a stored timestamp resolves.

        doit kit digest show              the newest reading
        doit kit digest show 2026-08-12   the reading taken that day
    """
    raise typer.Exit(cmd_show(when or '', as_json, STATE_DIR))


@app.command('list')
def digest_list_command(as_json: JsonOption = False) -> None:
    """Name every reading stored, oldest first.

    The handles `show` takes, and how far back the record goes. Reads the stored
    file, so it answers on a machine that has no claude on it at all.

        doit kit digest list          what has been read, and when
        doit kit digest list --json   every reading whole, text included
    """
    raise typer.Exit(cmd_list(as_json, STATE_DIR))
