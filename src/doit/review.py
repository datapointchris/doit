"""The temporal half of doit: a terminal-native cadence register.

Answers "what should I revisit or run on a cadence?". Invoked as `doit review`.

Two records, by design, both kept by doit's service:
  - a register            declarative config you hand-edit with `doit review
                          edit` (description, cadence, optional command). It is
                          kept as written, so your comments and layout are never
                          disturbed.
  - the done dates        the days `done` recorded per item.

The due date is derived, never stored: next_due = last_done + cadence. A new item
with no recorded done simply shows as never done. Storing only last_done means
there is no date to keep in sync and nothing to drift.

The register holds two blocks: `items` for maintenance and `practice` for the
Labs you work through. The Labs live in terminal-library, which is shared
reference. When you practice one is yours, so its schedule is kept here.
"""

import contextlib
import datetime as dt
import json
import re
import subprocess
import sys
from typing import Annotated

import typer
from rich.text import Text

from doit import service
from doit.cadence import is_due
from doit.cadence import overdue_days
from doit.cadence import parse_cadence
from doit.cadence import status_label
from doit.paths import xdg_cache_home
from doit.render import console
from doit.render import nudge_header
from doit.render import nudge_row
from doit.render import nudge_width

TEMPLATE = """\
# Terminal-native review / cadence register, read by `doit review`.
#
# This is declarative config — edit it freely with `doit review edit`. The done
# dates are recorded by `doit review done <id>`, and on no other evidence.
#
#   description  what to revisit or run
#   cadence      how often: <n>d / <n>w / <n>mo / <n>y  (e.g. 2w, 1mo)
#   command      optional; shown so you know what to run (not auto-run)
#   show         optional command the nudge runs to print a card inline, where
#                the item's name alone does not say enough to act on
#
# `practice:` holds the Labs you work through on a schedule, with the same
# fields, keyed by the Lab's id. An id names one item across both blocks.

items:
  prune-docker:
    description: Remove stopped containers and dangling images
    cadence: 1mo
    command: docker system prune

practice: {}
"""

# A nudge you have to scroll is a nudge you learn to ignore, so the roster is
# bounded and the overflow points at the browse view instead of printing itself.
NUDGE_MAX_ITEMS = 8

# How long the nudge waits between showings where nobody set an interval. Four
# hours means at most twice a working day, which is the point of a nudge rather
# than a banner. The shell gate waits this long too, until a nudge has answered.
DEFAULT_NUDGE_MINUTES = 240

# The shell gate's files, read with no Python and no request. The interval is the
# service's, as plain minutes. The marker's mtime is when this machine last asked.
# The showing is what the last nudge printed, waiting for the next shell.
NUDGE_INTERVAL = xdg_cache_home() / 'doit' / 'nudge-interval-minutes'
NUDGE_MARKER = xdg_cache_home() / 'doit' / 'nudge'
NUDGE_SHOWING = xdg_cache_home() / 'doit' / 'nudge-showing.txt'


# Fields a register item may declare: the ones the TEMPLATE header documents.
# Anything else is a typo, and a typo here is silent damage. A misspelled
# `cadence:` is ignored, so the item loses its schedule with nothing printed.
ITEM_FIELDS = {'description', 'cadence', 'command', 'show'}

# The register's blocks: maintenance, then the Labs practiced on a schedule.
BLOCKS = ('items', 'practice')


class RegisterError(ValueError):
    """A review register that cannot be read as written.

    A ValueError, so the dashboard and `doit today` degrade the review lane to
    this message rather than dropping it or failing the whole view.
    """


def register_from_document(data: dict) -> dict[str, dict]:
    """A parsed register's blocks, each item by id.

    Raises RegisterError on an unknown block, an item that is not a mapping or
    declares a field outside ITEM_FIELDS, or an id in both blocks. The done
    dates are keyed by id alone, so one id in two blocks would share a stamp.
    """
    if not isinstance(data, dict):
        raise RegisterError(f'must be a mapping of blocks, not {type(data).__name__}')
    # A misspelled block is ignored by every reader, so its items silently never
    # come due. Refused here, it is one line naming the blocks that exist.
    unknown_blocks = set(data) - set(BLOCKS)
    if unknown_blocks:
        raise RegisterError(f'unknown block(s) {", ".join(sorted(unknown_blocks))}. Accepted: {", ".join(BLOCKS)}')
    register = {block: data.get(block) or {} for block in BLOCKS}
    owner: dict[str, str] = {}
    for block, items in register.items():
        for item_id, meta in items.items():
            if item_id in owner:
                raise RegisterError(f'{item_id}: in both {owner[item_id]} and {block}; an id names one item')
            owner[item_id] = block
            check_item(item_id, meta)
    return register


def check_item(item_id: str, meta: object) -> None:
    if meta is None:
        return
    if not isinstance(meta, dict):
        raise RegisterError(f'{item_id}: must be a mapping of fields, not {type(meta).__name__}')
    unknown = set(meta) - ITEM_FIELDS
    if unknown:
        # The accepted set is named so a reader can see `cadence` beside a
        # misspelled `cadance` and fix it without leaving the terminal.
        raise RegisterError(f'{item_id}: unknown field(s) {", ".join(sorted(unknown))}. Accepted: {", ".join(sorted(ITEM_FIELDS))}')


def statuses_of(register: dict[str, dict], state: dict[str, str], today: dt.date, block: str | None = None) -> list[dict]:
    """Every item in one block, or in every block, with its derived overdue days, most urgent first.

    ``state`` is each item's last done day by id. overdue is the number of days
    past due (negative = not yet due); None means the item has never been done,
    which sorts to the very top as most urgent.
    """
    rows = []
    for name in [block] if block else list(BLOCKS):
        for item_id, meta in register.get(name, {}).items():
            meta = meta or {}
            last = state.get(item_id)
            overdue = overdue_days(last, meta.get('cadence', ''), today)
            rows.append(
                {
                    'id': item_id,
                    'block': name,
                    'cadence': str(meta.get('cadence', '')),
                    'cadence_days': parse_cadence(meta.get('cadence', '')),
                    'desc': meta.get('description', '') or '',
                    'command': meta.get('command', '') or '',
                    'show': meta.get('show', '') or '',
                    'last': last,
                    'overdue': overdue,
                    'due': is_due(overdue),
                }
            )
    rows.sort(key=lambda r: float('inf') if r['overdue'] is None else r['overdue'], reverse=True)
    return rows


def render_item(row: dict) -> None:
    meta = f'every {row["cadence"]}'
    if row['last']:
        meta += f' · last {row["last"]}'
    line = Text('  ')
    line.append(f'{row["id"]:<20}', style='yellow')
    line.append(f'  {status_label(row["overdue"])}  ·  {meta}')
    console.print(line)
    console.print(Text(f'      {row["desc"]}'))
    if row['command']:
        command = Text('      ')
        command.append(f'↳ {row["command"]}', style='cyan')
        console.print(command)
    console.print()


def warn_empty() -> None:
    console.print('The review register has no items yet. Add some with [cyan]doit review edit[/].')


# Each block's heading in the browse views. Practice prints after maintenance,
# because a deck of never-practiced Labs would otherwise push it off the screen.
BLOCK_TITLES = {'items': 'Review', 'practice': 'Practice'}


def cmd_due() -> int:
    rows = service.client().get('/review')['entries']
    if not rows:
        warn_empty()
        return 0

    due = [row for row in rows if is_due(row['overdue'])]
    if due:
        for block, title in BLOCK_TITLES.items():
            in_block = [row for row in due if row['block'] == block]
            if in_block:
                console.rule(f"[cyan]{title} — what's due", align='left')
                for row in in_block:
                    render_item(row)
        console.print('Mark one done:  [cyan]doit review done <id>[/]')
        return 0

    console.rule("[cyan]Review — what's due", align='left')
    console.print('[green]✓[/] All caught up.')
    soonest = rows[0]  # sorted desc, so the least-overdue (soonest) is first
    up_next = Text('  Next up: ')
    up_next.append(soonest['id'], style='yellow')
    up_next.append(f' in {-soonest["overdue"]}d.')
    console.print(up_next)
    return 0


def cmd_list(as_json: bool = False) -> int:
    register = service.client().get('/review')
    rows = register['entries']
    # --json emits every item with its derived status, due or not, so a consumer
    # filters on `due` or `overdue` itself and can say "2 due of 9". Each row
    # names its `block`.
    if as_json:
        # Plain print, never the rich console: a Console soft-wraps at terminal
        # width, which would put newlines inside JSON strings and hand a consumer
        # a parse error instead of data.
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        warn_empty()
    for block, title in BLOCK_TITLES.items():
        in_block = [row for row in rows if row['block'] == block]
        if in_block or block == 'items':
            console.rule(f'[cyan]{title} — full register', align='left')
        for row in in_block:
            render_item(row)
    render_stranded(register['stranded'])
    return 0


def render_stranded(stranded: list[str]) -> None:
    """Name any recorded done dates whose item the register no longer holds.

    Said in the one view that is about the register itself rather than about
    what to do next. The nudge and `due` stay silent — a misfiled record is not
    a task, and an interrupt that reports bookkeeping is the kind you stop
    reading.
    """
    if not stranded:
        return
    console.print(Text(f'Recorded done dates with no register item: {", ".join(stranded)}', style='yellow'))
    console.print('  Renaming an item strands its history. Naming it back in the register picks the history up again.')


def cmd_done(item_id: str) -> int:
    # The desk's own day, because the service's clock may sit in another zone.
    # One completion per item and day, so sending it again records nothing new.
    done = service.client().post(f'/review/{item_id}/completions', {'day': dt.date.today().isoformat()}, repeatable=True)
    line = Text.from_markup('[green]✓[/] Marked ')
    line.append(item_id, style='yellow')
    days = (done.get('status') or {}).get('cadence_days')
    line.append(f' done ({done["done_on"]}).' + (f' Due again in {days}d.' if days else ''))
    console.print(line)
    return 0


def run_show(row: dict) -> None:
    """Run a due item's `show:` command and let its output print inline.

    Only the nudge calls this, so the manual `list`/`due` views only ever read.
    This is the one place a review item runs rather than being displayed, and it
    is opt-in per item: `command:` is shown, `show:` is run. Stdout is inherited
    so colored output survives, and a timeout keeps a slow command from wedging
    shell startup.

    A `show:` command must be a read. Anything it changes is a change nothing
    asked for. A `show:` needing a side effect to be useful is an argument for a
    read-only flag on that tool, not for relaxing this.

    A `show:` command must emit at most a couple of lines — it runs inside a
    nudge, so a command that prints its own full listing silently turns the nudge
    back into a wall of text. Rendering stays the child's job; this never captures
    or trims its output.
    """
    cmd = row.get('show')
    if not cmd:
        return
    # Flush first: our print() output is buffered, but the subprocess writes to
    # the inherited fd immediately, so without this its card jumps ahead of the
    # header and item list we just printed.
    sys.stdout.flush()
    with contextlib.suppress(subprocess.TimeoutExpired):
        # shell=True is the feature, not an oversight: a register entry is hand-
        # authored config and may hold a pipeline, so it has to reach a shell.
        subprocess.run(cmd, shell=True, timeout=10, check=False)  # nosec B602


def nudge_due(held: dict, now: dt.datetime) -> bool:
    """Whether the interval has passed since the nudge last showed on any machine."""
    last = held.get('last_nudged_at')
    if not last:
        return True
    interval = dt.timedelta(minutes=held.get('interval_minutes') or DEFAULT_NUDGE_MINUTES)
    return now - dt.datetime.fromisoformat(last.replace('Z', '+00:00')) >= interval


def cmd_nudge() -> int:
    """Startup nudge: print only what's due, and nothing at all when caught up.

    Deliberately a different density from `due`, not just a quieter one. `due`
    is a browse view you asked for, so it carries every field; a nudge is an
    interrupt you didn't, so each item collapses to identity, urgency, and the
    command to run. The description is browse-time information — at first shell
    you already know what your own register items mean.

    The shell gate starts this in the background at most once per interval on
    each machine. The service says whether the interval has passed since the
    nudge last showed anywhere, so the schedule is one rolling one. Maintenance
    only: a Lab is practice you sit down for, not something to be interrupted
    with at the first shell.
    """
    doit = service.client()
    held = doit.get('/review/nudge')
    NUDGE_INTERVAL.parent.mkdir(parents=True, exist_ok=True)
    NUDGE_INTERVAL.write_text(f'{held["interval_minutes"]}\n')
    if not held['show']:
        return 0
    doit.put('/review/nudge', {'last_nudged_at': dt.datetime.now().astimezone().isoformat()})
    due = held['due']
    if not due:
        return 0
    nudge_header('Review', held['due_total'])
    width = nudge_width([row['id'] for row in due])
    for row in due:
        nudge_row(row['id'], status_label(row['overdue']), row['command'], width)
    if held['due_total'] > len(due):
        console.print(f'  +{held["due_total"] - len(due)} more  ·  [cyan]doit review due[/]')
    # After the roster, expand any items carrying live content, so the roster
    # stays scannable.
    console.print()
    for row in due:
        run_show(row)
    console.print('Done:  [cyan]doit review done <id>[/]')
    return 0


def parse_duration_minutes(dur: str) -> int | None:
    """'4h' → 240, '90m' → 90, bare '4' → hours. None if unparsable."""
    match = re.fullmatch(r'(\d+)\s*([hm]?)', dur.strip())
    if not match:
        return None
    count = int(match.group(1))
    unit = match.group(2) or 'h'  # a bare number means hours
    return count * 60 if unit == 'h' else count


def cmd_nudge_every(minutes: int) -> int:
    """Record how often the startup nudge may fire, on every machine."""
    held = service.client().put('/review/nudge', {'interval_minutes': minutes})
    NUDGE_INTERVAL.parent.mkdir(parents=True, exist_ok=True)
    NUDGE_INTERVAL.write_text(f'{held["interval_minutes"]}\n')
    console.print(f'Nudge interval set to {held["interval_minutes"]} minutes.')
    return 0


app = typer.Typer(name='review', no_args_is_help=True, help="What's due to revisit, on a cadence.")


@app.command('due')
def due_command() -> None:
    """What's due now, most overdue first."""
    raise typer.Exit(cmd_due())


@app.command('list')
def list_command(
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
) -> None:
    """Every registered item and its status.

    In --json, `overdue` is days past due (negative is days until due) and null
    when the item was never done, which counts as due; `due` says so directly.
    """
    raise typer.Exit(cmd_list(as_json))


@app.command('done')
def done_command(item_id: Annotated[str, typer.Argument(help='The register id to mark done.')]) -> None:
    """Mark an item done, advancing its due date."""
    raise typer.Exit(cmd_done(item_id))


@app.command('edit')
def edit_command() -> None:
    """Edit the register in $EDITOR."""
    raise typer.Exit(0 if service.edit_document('review', TEMPLATE) else 1)


@app.command('nudge-every')
def nudge_every_command(
    duration: Annotated[str, typer.Argument(help='How often, e.g. 4h, 90m, or a bare number of hours.')],
) -> None:
    """Set the startup-nudge interval."""
    minutes = parse_duration_minutes(duration)
    if minutes is None or minutes <= 0:
        raise typer.BadParameter('expected a duration like 4h, 90m, or a bare number of hours')
    raise typer.Exit(cmd_nudge_every(minutes))


@app.command('nudge', hidden=True)
def nudge_command() -> None:
    """The startup nudge, called by the shell gate rather than by hand."""
    raise typer.Exit(cmd_nudge())
