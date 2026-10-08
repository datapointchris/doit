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

**``run`` reads exported tables, never shell history.** It runs on a scheduler
whose own history would otherwise answer for the fleet. ``export`` measures
this machine's history into a :mod:`doit.usage_table` file, and ``run`` merges
every file present, taking each host once. A file that will not read fails the
run before any request. What it covered is named in the summary and kept on the
reading, so a host that stopped arriving shows as absent rather than idle.

Readings are kept in the journal directory, beside the tables they read, and not
in the cache directory: a recompute cannot rebuild one, because a second call
costs another request and reads a table that has moved since. One append-only
file per machine, for the reason :mod:`doit.journal` gives at length: Syncthing
resolves conflicts per file, so two machines appending to one file lose a tail.
"""

import dataclasses
import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from doit import observe
from doit import usage
from doit import usage_table
from doit.index import build_index
from doit.paths import NamedPathMissing
from doit.paths import journal_dir
from doit.paths import machine_name
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

# Where fleet's scheduler asks a job to say what came of its run. Set only on a
# scheduled run, to a file that does not exist yet.
RESULT_FILE_ENV = 'FLEET_RESULT_FILE'


class Failure(StrEnum):
    """Why a reading could not be taken, as a key rather than as a sentence.

    Each wants a different reaction — an absent binary is a machine to fix, a
    timeout is worth retrying, an empty answer is not, and a reply with no result
    frame is a CLI whose output format moved — so a caller has to be able to tell
    them apart, which a printable string does not allow. Wording lives once in
    :data:`FAILURE_TEXT` keyed by this, so a sentence can be rewritten without a
    test noticing.
    """

    NOT_INSTALLED = 'not-installed'
    TIMED_OUT = 'timed-out'
    FAILED = 'failed'
    EMPTY = 'empty'
    NO_RESULT = 'no-result'


FAILURE_TEXT: dict[Failure, str] = {
    Failure.NOT_INSTALLED: 'claude is not installed on this machine, so no reading can be taken.',
    Failure.TIMED_OUT: 'claude did not answer within {detail}.',
    Failure.FAILED: 'claude failed: {detail}',
    Failure.EMPTY: 'claude returned nothing.',
    Failure.NO_RESULT: 'claude replied without a result frame. The reply began {detail}',
}


@dataclass(frozen=True)
class Usage:
    """What one call spent, in tokens, each kind on its own.

    The keys are fleet's ``usage.Usage`` keys, because this is written into the
    run record fleet reads. Never one total: a total ranks a call by how much
    context it re-read rather than by what it did.
    """

    input: int = 0
    cache_creation: int = 0
    cache_read: int = 0
    output: int = 0


@dataclass(frozen=True)
class Reply:
    """The result frame of one ``claude -p`` call: what it answered and what it spent.

    A call that failed has one too. A refusal exits 1 with its diagnosis in
    ``text``, ``is_error`` set, and the session and tokens beside it.
    """

    text: str
    session_id: str
    usage: Usage
    is_error: bool = False


class DigestFailed(Exception):
    """The reading could not be taken.

    ``reason`` is the key a caller branches on and a test asserts; the sentence
    is derived from it, and ``detail`` carries whatever the runtime knew that the
    wording could not. ``reply`` is the result frame where the call got far
    enough to write one, so a failure still says what it spent.
    """

    def __init__(self, reason: Failure, detail: str = '', reply: Reply | None = None) -> None:
        self.reason = reason
        self.detail = detail
        self.reply = reply
        super().__init__(FAILURE_TEXT[reason].format(detail=detail))


@dataclass(frozen=True)
class ExportUsed:
    """One usage table a reading read: when it was written, from which history, and what it gave.

    ``hosts`` maps each host taken from this table to the newest day its history
    there reaches. A host whose history stops weeks back is a sync that stalled,
    so the summary, ``list`` and ``show`` print that day beside the host. A table
    whose every host was fresher in another is still listed, with no hosts,
    because it was read.
    """

    generated: str
    history: str
    hosts: dict[str, str]


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

    ``exports`` is keyed by the machine that wrote each table, and says which
    hosts the reading covers. ``machine`` is only the box that sent the request.
    A reading stored before exports existed read one machine's history and
    carries none.
    """

    generated: str
    machine: str
    rows: int
    days: int
    text: str
    model: str = ''
    claude_version: str = ''
    exports: dict[str, ExportUsed] = field(default_factory=dict)

    def histories(self) -> dict[str, str]:
        """Every host the reading covers, in name order, with the last day its history held."""
        return dict(sorted((host, through) for used in self.exports.values() for host, through in used.hosts.items()))


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


def history_ages(ends: list[str], today: dt.date) -> str:
    """How many days before ``today`` each merged history stops, nearest first.

    Ages, never the days themselves and never the host names, so the prompt
    gains no date and no string the catalog did not supply. The run's summary
    is where the hosts are named.
    """
    ages = sorted(max((today - dt.date.fromisoformat(end)).days, 0) for end in ends)
    return ', '.join(str(age) for age in ages)


def build_prompt(rows: list[usage.Row], today: dt.date, days: int, history_ends: list[str]) -> str:
    """The message sent to the model: the table, what its fields mean, and the questions.

    The field glossary is here rather than left implicit because ``sources`` is
    this repo's own vocabulary — a reader who does not know that ``func`` means a
    shell function you wrote yourself cannot tell an unused tool from an unused
    habit, and those want opposite reactions.

    ``history_ends`` is the last day each merged history holds. A row typed only
    on a desk whose sync stalled reads as cold as that stall is long, and the
    rows carry no host, so the model is told how far back each history stops.
    """
    table = json.dumps(payload(rows, today), separators=(',', ':'))
    return f"""Read one person's command-line toolkit and say what it shows.

Each row is something they have catalogd as theirs, joined to how often they
have typed it at a shell prompt. A row counts as cold after {days} days.

The counts merge one shell history per machine. Days before today that each
history stops: {history_ages(history_ends, today)}.
Nothing typed on a machine after its history stops is counted.

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
- A history that stops weeks back makes a row read colder than it is. Do not call a
  row dropped where that gap could explain its days_since.
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


def tokens(counts: object, key: str) -> int:
    """One count out of a usage block, or 0 where the block or the key is missing."""
    return int(counts.get(key) or 0) if isinstance(counts, dict) else 0


def spent(frame: dict) -> Usage:
    """What the call cost, summed over every model it reached.

    The frame's own ``usage`` counts the main model alone, so reading it
    undercounts every call that reached a second model, and nothing fails.
    ``modelUsage`` breaks the call down per model, and the envelope is read only
    where that breakdown is empty. fleet's ``usage.Report.Sum`` reads a frame the
    same way, and both land in one ledger.
    """
    per_model = frame.get('modelUsage')
    if isinstance(per_model, dict) and per_model:
        models = list(per_model.values())
        return Usage(
            input=sum(tokens(model, 'inputTokens') for model in models),
            cache_creation=sum(tokens(model, 'cacheCreationInputTokens') for model in models),
            cache_read=sum(tokens(model, 'cacheReadInputTokens') for model in models),
            output=sum(tokens(model, 'outputTokens') for model in models),
        )
    envelope = frame.get('usage')
    return Usage(
        input=tokens(envelope, 'input_tokens'),
        cache_creation=tokens(envelope, 'cache_creation_input_tokens'),
        cache_read=tokens(envelope, 'cache_read_input_tokens'),
        output=tokens(envelope, 'output_tokens'),
    )


def read_reply(stdout: str) -> Reply | None:
    """The result frame out of ``--output-format json``, or None where there is none.

    That format is an array of every frame in the session. It is searched from
    the end for the frame whose type is ``result`` rather than taken as the last
    element. The CLI interleaves other frames, ``rate_limit_event`` among them,
    and nothing promises the result comes last. A lone object is read as an
    array of one.
    """
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    frames = parsed if isinstance(parsed, list) else [parsed]
    for frame in reversed(frames):
        if isinstance(frame, dict) and frame.get('type') == 'result':
            return Reply(
                text=str(frame.get('result') or ''),
                session_id=str(frame.get('session_id') or ''),
                usage=spent(frame),
                is_error=bool(frame.get('is_error')),
            )
    return None


def ask(prompt: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Reply:
    """Send the prompt to `claude -p` and return its result frame.

    The prompt goes over stdin rather than argv: a two-hundred-row table is an
    argument list a shell refuses, and argv is readable from `ps` by anything on
    the machine.

    `--output-format json` is what carries the session id and the tokens back
    beside the answer. A call that fails carries them too, and puts its
    diagnosis in the frame's text: measured on 2.1.293, an unknown model exited
    1 with a sentence there and only an error code on stderr. So a failure reads
    the frame before stderr.

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
        '--output-format',
        'json',
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

    reply = read_reply(result.stdout)
    if result.returncode != 0 or (reply is not None and reply.is_error):
        detail = (reply.text.strip() if reply else '') or result.stderr.strip() or f'exit {result.returncode}'
        raise DigestFailed(Failure.FAILED, detail, reply)
    if not result.stdout.strip():
        raise DigestFailed(Failure.EMPTY)
    if reply is None:
        raise DigestFailed(Failure.NO_RESULT, repr(result.stdout.strip()[:80]))
    if not reply.text.strip():
        raise DigestFailed(Failure.EMPTY, reply=reply)
    return dataclasses.replace(reply, text=reply.text.strip())


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


def store(path: Path, digest: Digest) -> str:
    """Append the reading, returning why it could not be kept, or '' when it was.

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
        return f'Reading taken but not stored at {path} — {failure}'
    return ''


def exports_of(value: object) -> dict[str, ExportUsed]:
    """A stored ``exports`` map, or none where it is absent or not that shape.

    Forgiving where :func:`usage_table.parse` is strict. That one guards what a
    reading is built from, and this only labels a reading already taken.
    """
    if not isinstance(value, dict):
        return {}
    exports: dict[str, ExportUsed] = {}
    for machine, used in value.items():
        if not isinstance(used, dict) or not isinstance(used.get('hosts'), dict):
            continue
        exports[str(machine)] = ExportUsed(
            generated=str(used.get('generated') or ''),
            history=str(used.get('history') or ''),
            hosts={str(host): str(through) for host, through in used['hosts'].items()},
        )
    return exports


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
                        exports=exports_of(record.get('exports')),
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


@dataclass(frozen=True)
class RunOutcome:
    """What one ``run`` came to.

    ``summary`` is the line a scheduler shows for the run. ``reply`` is the
    call's result frame where the call got far enough to write one.
    """

    code: int
    summary: str
    reply: Reply | None = None


def exports_used(tables: list[usage_table.UsageTable], chosen: usage_table.Chosen) -> dict[str, ExportUsed]:
    """Every table read, with the hosts the reading took from it."""
    taken: dict[str, dict[str, str]] = {table.machine: {} for table in tables}
    for host, (table, usage_of_host) in chosen.items():
        taken[table.machine][host] = usage_of_host.through
    return {
        table.machine: ExportUsed(generated=table.generated, history=table.history, hosts=taken[table.machine])
        for table in sorted(tables, key=lambda table: table.machine)
    }


def reaches(histories: dict[str, str]) -> str:
    """Each host beside the last day its history holds."""
    return ', '.join(f'{host} through {through}' for host, through in sorted(histories.items()))


def coverage(exports: dict[str, ExportUsed]) -> str:
    """Which hosts came from which machine's table, and how far each one's history reaches.

    How far the history reaches, never when the table was written: a table
    written today can carry a host whose sync stalled weeks ago.
    """
    return '; '.join(f"{reaches(used.hosts)} from {machine}'s {used.history} export" for machine, used in exports.items() if used.hosts)


def refuse(message: str) -> RunOutcome:
    """A run that stopped before spending a request, said once to the person and once to the scheduler."""
    error_console.print(message)
    return RunOutcome(1, message)


def take_reading(days: int, directory: Path) -> RunOutcome:
    """Take a reading from every exported table and store it, saying what came of it at every exit.

    Everything that can refuse does so before the request, so a run that was
    never going to be kept costs nothing.
    """
    today = dt.date.today()
    found = usage_table.read_all(directory)
    if found.unreadable:
        return refuse('No reading taken. ' + '; '.join(str(fault) for fault in found.unreadable) + '.')
    if not found.tables:
        return refuse(f"No usage table in {directory}, so there is nothing to read. `doit kit digest export` writes this machine's.")

    chosen = usage_table.freshest(found.tables)
    rows = usage.combine(host.rows for _, host in chosen.values())
    if not rows:
        return refuse('Nothing measurable in any exported table, so there is nothing to read.')
    exports = exports_used(found.tables, chosen)

    error_console.print(f'Reading {len(rows)} rows over {", ".join(chosen)} with claude — this takes a minute.')
    try:
        reply = ask(build_prompt(rows, today, days, [host.through for _, host in chosen.values()]))
    except DigestFailed as failure:
        error_console.print(str(failure))
        return RunOutcome(1, str(failure).splitlines()[0], failure.reply)

    digest = Digest(
        generated=dt.datetime.now(dt.UTC).isoformat(timespec='seconds'),
        machine=machine_name(),
        rows=len(rows),
        days=days,
        text=reply.text,
        model=MODEL,
        claude_version=claude_version(),
        exports=exports,
    )
    print(digest.text)
    unstored = store(digest_path(directory, digest.machine), digest)
    if unstored:
        error_console.print(unstored)
        return RunOutcome(1, unstored, reply)
    return RunOutcome(0, f'digest of {digest.rows} rows stored as {digest.generated} · {coverage(exports)}', reply)


def take_export(directory: Path) -> RunOutcome:
    """Measure this machine's history, every host it holds, and replace this machine's table.

    A history that holds nothing refuses rather than writing a table of zeros,
    which a reading would take as every row gone unused.
    """
    history = observe.shell_history()
    if not history.entries:
        return refuse(f'No shell history to export: atuin answered nothing and {observe.HISTORY} holds no commands.')
    machine = machine_name()
    table = usage_table.build(history, build_index(), machine, dt.datetime.now(dt.UTC).isoformat(timespec='seconds'))
    if not any(host.rows for host in table.hosts):
        return refuse('Nothing measurable in your kit, so there is nothing to export.')
    try:
        path = usage_table.write(directory, table)
    except OSError as failure:
        return refuse(f'Usage table not written to {usage_table.table_path(directory, machine)} — {failure}')
    hosts = ', '.join(f'{host.host} through {host.through}' for host in table.hosts)
    summary = f'exported {hosts} from {table.history} to {path}'
    error_console.print(summary)
    return RunOutcome(0, summary)


def claim_result_file() -> str:
    """Take the scheduler's result path out of the environment, or '' on a run by hand.

    Every process started afterwards inherits nothing, so neither the claude
    session nor anything it runs can write this run's result in its place.
    fleet's own verbs claim the variable the same way, before they start
    anything.
    """
    return os.environ.pop(RESULT_FILE_ENV, '')


def result_record(outcome: RunOutcome) -> dict[str, object]:
    """The run's result as fleet's scheduler reads it.

    ``session_id`` and ``usage`` are what make the run an agent run in the
    ledger, so they ride along whenever the call wrote a frame, a failed call
    included. A run that never reached claude carries its summary alone.
    """
    record: dict[str, object] = {'summary': outcome.summary}
    if outcome.reply is not None:
        if outcome.reply.session_id:
            record['session_id'] = outcome.reply.session_id
        record['usage'] = dataclasses.asdict(outcome.reply.usage)
    return record


def write_result(path: Path, outcome: RunOutcome) -> str:
    """Hand the scheduler the run's result, returning why it could not, or '' when it did.

    Created exclusively, as fleet's own writer creates it. A file already there
    was written by something else under this job, and writing beside it would
    leave the scheduler two documents or the wrong one.
    """
    try:
        with path.open('x', encoding='utf-8') as handle:
            handle.write(json.dumps(result_record(outcome)) + '\n')
    except OSError as failure:
        return f'Result not written to {path} — {failure}'
    return ''


def reported(action: Callable[[Path], RunOutcome], directory: Path | None) -> int:
    """Run one scheduled verb against the shared directory, and say what came of it to a scheduler that asked.

    The result is claimed before anything starts, so no child sees the path. A
    result that cannot be written fails the run: the scheduler would otherwise
    record a clean run with nothing to show for it, which is the run the ledger
    hides.

    ``directory`` is resolved inside, after the claim. A `$DOIT_JOURNAL_DIR` naming
    a share that has not arrived then reaches the scheduler as this run's reason,
    rather than as a traceback with no result at all.
    """
    result_file = claim_result_file()
    try:
        outcome = action(directory or journal_dir())
    except NamedPathMissing as missing:
        outcome = refuse(str(missing))
    if not result_file:
        return outcome.code
    unwritten = write_result(Path(result_file), outcome)
    if unwritten:
        error_console.print(unwritten)
        return 1
    return outcome.code


def cmd_run(days: int, directory: Path | None = None) -> int:
    """Take a reading from the exported tables in ``directory``, or the journal directory."""
    return reported(lambda shared: take_reading(days, shared), directory)


def cmd_export(directory: Path | None = None) -> int:
    """Write this machine's table into ``directory``, or the journal directory."""
    return reported(take_export, directory)


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
    console.rule(f'[cyan]{chosen.generated} · {chosen.machine}{over(chosen)}', align='left')
    console.print(chosen.text)
    return 0


def over(digest: Digest) -> str:
    """The hosts a reading covers and how far each reaches, as a clause, or nothing for one taken before exports."""
    histories = digest.histories()
    return f' · over {reaches(histories)}' if histories else ''


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
        console.print(f'  [cyan]{digest.generated}[/] · {digest.machine} · {digest.rows} rows, cold after {digest.days}d{over(digest)}')
    console.print('\nRead one:  [cyan]doit kit digest show <handle>[/]')
    return 0


def exit_after(action: Callable[[], int]) -> None:
    """Exit with what ``action`` returned, or with one line where `$DOIT_JOURNAL_DIR` names a missing directory."""
    try:
        code = action()
    except NamedPathMissing as missing:
        error_console.print(str(missing))
        raise typer.Exit(1) from None
    raise typer.Exit(code)


app = typer.Typer(name='digest', no_args_is_help=True, help='What your usage table says, read back as prose.')


DaysOption = Annotated[int, typer.Option('--days', help='Days without a run before a row counts as cold.')]

JsonOption = Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')]


@app.command('run')
def digest_run_command(days: DaysOption = usage.DEFAULT_DAYS) -> None:
    """Read every exported usage table with claude and store what it says.

    The only command here that reaches the network, and one run is one request.
    It sends the aggregated table — what you type, how often, how long ago — and
    never a command line.

    It reads the tables `export` wrote, taking each host from the table whose
    history for it runs latest. It reads no shell history of its own. A table
    that will not read stops the run before the request.

    Run by fleet's scheduler, it also writes the outcome, the hosts covered, the
    session id and the tokens spent to $FLEET_RESULT_FILE. Run by hand, it
    writes nothing there.

        doit kit digest run             read the tables as they stand today
        doit kit digest run --days 30   count a row cold after a month, not a season
    """
    raise typer.Exit(cmd_run(days))


@app.command('export')
def digest_export_command() -> None:
    """Write this machine's usage table where `run` reads it.

    Measures every host this machine's shell history holds, each against the kit
    here, and replaces this machine's file in $DOIT_JOURNAL_DIR. atuin syncs, so
    one desk's export can cover the others. The file holds counts and dates per
    row of your kit, and never a command line.

        doit kit digest export   measure history here and replace this machine's table
    """
    raise typer.Exit(cmd_export())


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
    exit_after(lambda: cmd_show(when or '', as_json, journal_dir()))


@app.command('list')
def digest_list_command(as_json: JsonOption = False) -> None:
    """Name every reading stored, oldest first.

    The handles `show` takes, and how far back the record goes. Reads the stored
    file, so it answers on a machine that has no claude on it at all.

        doit kit digest list          what has been read, and when
        doit kit digest list --json   every reading whole, text included
    """
    exit_after(lambda: cmd_list(as_json, journal_dir()))
