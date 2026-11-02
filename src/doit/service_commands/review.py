"""The `doit review` commands, each asking the service and rendering its answer, and the files the shell gate reads."""

import datetime as dt
import json
from typing import Annotated

import typer
from rich.text import Text

from doit import service
from doit.cadence import is_due
from doit.cadence import status_label
from doit.paths import xdg_cache_home
from doit.render import console
from doit.render import nudge_header
from doit.render import nudge_row
from doit.render import nudge_width
from doit.review import BLOCK_TITLES
from doit.review import TEMPLATE
from doit.review import parse_duration_minutes
from doit.review import render_item
from doit.review import render_stranded
from doit.review import run_show
from doit.review import warn_empty

# The shell gate's files, read with no Python and no request. The interval is the
# service's, as plain minutes. The marker's mtime is when this machine last asked.
# The showing is what the last nudge printed, waiting for the next shell.
NUDGE_INTERVAL = xdg_cache_home() / 'doit' / 'nudge-interval-minutes'


NUDGE_MARKER = xdg_cache_home() / 'doit' / 'nudge'


NUDGE_SHOWING = xdg_cache_home() / 'doit' / 'nudge-showing.txt'


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
