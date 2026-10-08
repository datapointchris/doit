"""Each machine's usage table, written where a digest on another box can read it.

The digest runs on a box with no history of yours: a scheduler has no atuin login,
and atuin has no service accounts. Its own zsh history would answer instead, and
the reading would describe the scheduler. So every machine that runs the export
measures its own history and writes the result into the shared directory, and the
digest reads those files instead of any history at all.

**An export holds every host its history holds**, each measured on its own. atuin
syncs, so one desk's history carries the other desks' commands, each tagged with
the host that ran it. A zsh fallback knows only its own box, and
:func:`doit.observe.zsh_invocations` tags every row with this machine. Two exports
can then hold the same host, so :func:`freshest` takes each host from exactly
one of them, and no host is counted twice however many boxes export.

**Rows, never command lines.** A host's table is :func:`doit.usage.measure` over
that host's invocations: catalog strings, a count, a date. Raw history in a synced
folder would put every command line you typed, secrets included, on every box the
folder reaches.

A host is measured against the exporting machine's kit. A shell function defined
only on a Mac is invisible in archlinux's export of that Mac's history.

One file per exporting machine, rewritten whole by temp file and rename. One
writer per file is the whole sync story, for the reason :mod:`doit.journal` gives.
"""

import dataclasses
import datetime as dt
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from doit import usage
from doit.index import Entry
from doit.observe import Invocation
from doit.observe import ShellHistory

SCHEMA_VERSION = 1

# The machine is everything between the prefix and the extension, and a bare
# hostname holds no dot. A Syncthing conflict copy does
# (`usage-table-mbp.sync-conflict-….json`), so it is never read as an export.
TABLE_NAME = re.compile(r'usage-table-([^.]+)\.json')

ISO_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')


@dataclass(frozen=True)
class HostUsage:
    """One host's history, measured against the exporting machine's kit.

    ``through`` is the newest day a command ran on this host, as this history
    holds it. It is what :func:`freshest` compares, so a box whose sync stalled
    loses to one whose history for that host runs later.
    """

    host: str
    through: str
    commands: int
    rows: tuple[usage.Row, ...]


@dataclass(frozen=True)
class UsageTable:
    """What one machine exported: who wrote it, when, from which history, and per host."""

    machine: str
    generated: str
    history: str
    hosts: tuple[HostUsage, ...]


class Unreadable(NamedTuple):
    """A file named as an export that could not be read as one."""

    name: str
    reason: str

    def __str__(self) -> str:
        return f'{self.name} {self.reason}'


class Found(NamedTuple):
    """Every export in a directory, and every file named as one that would not read."""

    tables: list[UsageTable]
    unreadable: list[Unreadable]


class Malformed(ValueError):
    """A document that parses as JSON and is not a usage table."""


def table_path(directory: Path, machine: str) -> Path:
    """This machine's export. Its name carries the machine, and so does the document."""
    return directory / f'usage-table-{machine}.json'


def by_host(invocations: tuple[Invocation, ...]) -> dict[str, list[Invocation]]:
    """History split by the machine that ran each command."""
    hosts: dict[str, list[Invocation]] = defaultdict(list)
    for invocation in invocations:
        hosts[invocation.host].append(invocation)
    return hosts


def build(history: ShellHistory, kit: list[Entry], machine: str, generated: str) -> UsageTable:
    """Measure every host the history holds, each against this machine's kit."""
    hosts = tuple(
        HostUsage(
            host=host,
            through=max(invocation.date for invocation in ran),
            commands=len(ran),
            rows=tuple(usage.measure(kit, tuple(ran))),
        )
        for host, ran in sorted(by_host(history.entries).items())
    )
    return UsageTable(machine=machine, generated=generated, history=history.source, hosts=hosts)


def to_document(table: UsageTable) -> dict:
    """The table as written: hosts keyed by name, each row whole."""
    return {
        'schema_version': SCHEMA_VERSION,
        'machine': table.machine,
        'generated': table.generated,
        'history': table.history,
        'hosts': {
            host.host: {
                'through': host.through,
                'commands': host.commands,
                'rows': [dataclasses.asdict(row) for row in host.rows],
            }
            for host in table.hosts
        },
    }


def write(directory: Path, table: UsageTable) -> Path:
    """Replace this machine's export whole, so a reader never meets half of one."""
    path = table_path(directory, table.machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(to_document(table), indent=1) + '\n', encoding='utf-8')
    os.replace(temporary, path)
    return path


def nonempty_string(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise Malformed(f'{where} is not a non-empty string')
    return value


def whole_number(value: object, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise Malformed(f'{where} is not a whole number')
    return value


def iso_date(value: object, where: str) -> str:
    if not isinstance(value, str) or not ISO_DATE.fullmatch(value):
        raise Malformed(f'{where} is not a YYYY-MM-DD date')
    return value


def string_list(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise Malformed(f'{where} is not a list of strings')
    return tuple(value)


def utc_timestamp(value: object, where: str) -> str:
    stamp = nonempty_string(value, where)
    try:
        parsed = dt.datetime.fromisoformat(stamp)
    except ValueError:
        raise Malformed(f'{where} is not an ISO 8601 timestamp') from None
    # Compared across machines by :func:`rank`, where a naive time cannot be
    # ordered against an aware one at all.
    if parsed.tzinfo is None:
        raise Malformed(f'{where} carries no UTC offset')
    return stamp


def parse_row(value: object, where: str) -> usage.Row:
    if not isinstance(value, dict):
        raise Malformed(f'{where} is not a mapping')
    last = value.get('last')
    return usage.Row(
        typed=nonempty_string(value.get('typed'), f'{where}.typed'),
        sources=string_list(value.get('sources'), f'{where}.sources'),
        names=string_list(value.get('names'), f'{where}.names'),
        count=whole_number(value.get('count'), f'{where}.count'),
        # Empty is a row that never ran, which a table has to be able to say.
        last='' if last == '' else iso_date(last, f'{where}.last'),
    )


def parse_host(host: str, value: object) -> HostUsage:
    where = f'hosts.{host}'
    if not isinstance(value, dict):
        raise Malformed(f'{where} is not a mapping')
    rows = value.get('rows')
    if not isinstance(rows, list):
        raise Malformed(f'{where}.rows is not a list')
    return HostUsage(
        host=host,
        through=iso_date(value.get('through'), f'{where}.through'),
        commands=whole_number(value.get('commands'), f'{where}.commands'),
        rows=tuple(parse_row(row, f'{where}.rows[{index}]') for index, row in enumerate(rows)),
    )


def parse(document: object, machine: str) -> UsageTable:
    """A usage table, or :class:`Malformed` naming the first field that is wrong.

    Whole or not at all. A table missing one host or one row would still merge,
    and the reading would undercount with nothing to say so.

    ``machine`` is the name the file carries, and the document has to agree. A
    file copied under another machine's name would otherwise count one history
    twice.
    """
    if not isinstance(document, dict):
        raise Malformed('is not a JSON object')
    version = document.get('schema_version')
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise Malformed('carries no schema_version')
    if version > SCHEMA_VERSION:
        raise Malformed(f'is schema {version}, newer than this doit reads ({SCHEMA_VERSION})')
    named = nonempty_string(document.get('machine'), 'machine')
    if named != machine:
        raise Malformed(f'names machine {named!r}, not the {machine!r} its filename says')
    hosts = document.get('hosts')
    if not isinstance(hosts, dict) or not hosts:
        raise Malformed('holds no hosts')
    return UsageTable(
        machine=named,
        generated=utc_timestamp(document.get('generated'), 'generated'),
        history=nonempty_string(document.get('history'), 'history'),
        hosts=tuple(parse_host(host, value) for host, value in sorted(hosts.items())),
    )


def read_all(directory: Path) -> Found:
    """Every machine's export, and every file named as one that would not read.

    An unreadable export is returned rather than skipped. Skipping it would drop
    every host it held from the reading, and the reading would still succeed.
    """
    tables: list[UsageTable] = []
    unreadable: list[Unreadable] = []
    if not directory.is_dir():
        return Found(tables, unreadable)
    for path in sorted(directory.iterdir()):
        match = TABLE_NAME.fullmatch(path.name)
        if not match:
            continue
        try:
            document = json.loads(path.read_text(encoding='utf-8'))
        except OSError as failure:
            unreadable.append(Unreadable(path.name, f'cannot be read: {failure.strerror or failure}'))
            continue
        except ValueError:
            unreadable.append(Unreadable(path.name, 'is not JSON'))
            continue
        try:
            tables.append(parse(document, match.group(1)))
        except Malformed as fault:
            unreadable.append(Unreadable(path.name, str(fault)))
    return Found(tables, unreadable)


# Each host, beside the table it was taken from.
Chosen = dict[str, tuple[UsageTable, HostUsage]]


def freshest(tables: list[UsageTable]) -> Chosen:
    """Each host once, from the export whose history for it runs latest.

    A history only grows, so a box whose sync stalled holds an earlier stretch of
    what a current one holds. Comparing on ``through`` takes the current one. The
    export's own timestamp is the last tie-break and never the first, because a
    file written today can still carry a stalled history.
    """
    chosen: Chosen = {}
    for table in tables:
        for host in table.hosts:
            held = chosen.get(host.host)
            if held is None or rank(table, host) > rank(*held):
                chosen[host.host] = (table, host)
    return dict(sorted(chosen.items()))


def rank(table: UsageTable, host: HostUsage) -> tuple[str, int, dt.datetime]:
    """What :func:`freshest` compares, in order."""
    return (host.through, host.commands, dt.datetime.fromisoformat(table.generated))
