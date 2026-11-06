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
from doit.render import error_console
from doit.render import examples
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


def reads_now(status: dict | None) -> str:
    """How an entry reads after a correction, which no longer sits one cadence from today."""
    if not status:
        return ''
    last = f', last {status["last"]}' if status.get('last') else ''
    return f' Now: {status_label(status.get("overdue"))}{last}.'


def cmd_done(item_id: str, on: dt.date | None = None) -> int:
    # The desk's own day, because the service's clock may sit in another zone.
    # One completion per item and day, so sending it again records nothing new.
    day = (on or dt.date.today()).isoformat()
    done = service.client().post(f'/review/{item_id}/completions', {'day': day}, repeatable=True)
    line = Text.from_markup('[green]✓[/] Marked ')
    line.append(item_id, style='yellow')
    days = (done.get('status') or {}).get('cadence_days')
    after = reads_now(done.get('status')) if on else f' Due again in {days}d.' if days else ''
    line.append(f' done ({done["done_on"]}).{after}')
    console.print(line)
    return 0


def cmd_undo(item_id: str) -> int:
    doit = service.client()
    held = doit.get(f'/review/{item_id}/completions')
    if not held:
        error_console.print(Text(f'{item_id} has no completion to take back.'))
        return 1
    # Named by its day, so a send repeated after a lost answer cannot take back the one before it.
    undone = doit.delete(f'/review/{item_id}/completions/{held[-1]["done_on"]}')
    line = Text.from_markup('[green]✓[/] Took back ')
    line.append(item_id, style='yellow')
    line.append(f' ({undone["done_on"]}).{reads_now(undone.get("status"))}')
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


app = typer.Typer(
    name='review',
    no_args_is_help=True,
    help=(
        "What's due to revisit, on a cadence.\n\n"
        'A review item is something to revisit on a cadence, whether maintenance or practice. '
        'The register is the document listing them, which `doit review edit` opens.'
    ),
    epilog=examples(
        ('doit review due', "what's due now, most overdue first"),
        ('doit review done prune-docker', 'mark one done, which sets when it comes due again'),
        ('doit review list', 'every item and its status, due or not'),
    ),
)


@app.command('due', epilog=examples(('doit review due', 'every item due, where the dashboard shows the first few')))
def due_command() -> None:
    """What's due now, most overdue first."""
    raise typer.Exit(cmd_due())


@app.command(
    'list',
    epilog=examples(
        ('doit review list', 'every item and its status, due or not'),
        ('doit review list --json', 'the same items as data, to filter on `due` or `overdue`'),
    ),
)
def list_command(
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
) -> None:
    """Every registered item and its status.

    In --json, `overdue` is days past due (negative is days until due) and null
    when the item was never done, which counts as due; `due` says so directly.
    """
    raise typer.Exit(cmd_list(as_json))


def past_day(text: str | None) -> dt.date | None:
    """`--on` as a date, a usage error where it is not one or has not happened yet."""
    if text is None:
        return None
    try:
        day = dt.date.fromisoformat(text)
    except ValueError:
        raise typer.BadParameter(f'{text!r} is not a day (YYYY-MM-DD)') from None
    if day > dt.date.today():
        raise typer.BadParameter(f'{text} is later than today, and a completion records what has happened')
    return day


@app.command(
    'done',
    epilog=examples(
        ('doit review done prune-docker', 'done today'),
        ('doit review done prune-docker --on 2026-11-04', 'record one you did earlier, and read its due date from that day'),
    ),
)
def done_command(
    item_id: Annotated[str, typer.Argument(help='The register id to mark done.')],
    on: Annotated[
        str | None, typer.Option('--on', help='The day it was done, YYYY-MM-DD, to record it late.', show_default='today')
    ] = None,
) -> None:
    """Mark an item done, advancing its due date."""
    raise typer.Exit(cmd_done(item_id, past_day(on)))


@app.command('undo', epilog=examples(('doit review undo prune-docker', 'take back a completion recorded by mistake')))
def undo_command(item_id: Annotated[str, typer.Argument(help='The register id to take a completion back from.')]) -> None:
    """Take back an item's newest completion, so it reads as it did before."""
    raise typer.Exit(cmd_undo(item_id))


@app.command('edit', epilog=examples(('doit review edit', 'add an item or change its cadence')))
def edit_command() -> None:
    """Edit the register in $EDITOR, the document listing every review item."""
    raise typer.Exit(0 if service.edit_document('review', TEMPLATE) else 1)


@app.command(
    'nudge-every',
    epilog=examples(
        ('doit review nudge-every 4h', 'at most every four hours, across every machine'),
        ('doit review nudge-every 90m', 'at most every ninety minutes'),
    ),
)
def nudge_every_command(
    duration: Annotated[str, typer.Argument(help='How often, e.g. 4h, 90m, or a bare number of hours.')],
) -> None:
    """Set how often the startup nudge may fire.

    The nudge is what a new shell prints about due items, once `doit shell init` is
    in your rc.
    """
    minutes = parse_duration_minutes(duration)
    if minutes is None or minutes <= 0:
        raise typer.BadParameter('expected a duration like 4h, 90m, or a bare number of hours')
    raise typer.Exit(cmd_nudge_every(minutes))


@app.command('nudge', hidden=True)
def nudge_command() -> None:
    """The startup nudge, called by the shell gate rather than by hand."""
    raise typer.Exit(cmd_nudge())
