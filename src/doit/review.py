"""The temporal half of doit: a terminal-native cadence register.

Answers "what should I revisit or run on a cadence?". Invoked as `doit review`.

Two files, by design:
  - a register            declarative config you hand-edit (description, cadence,
                          optional command). This tool only ever reads it, so your
                          comments and layout are never disturbed. Under the XDG
                          config dir: it is personal, and doit's repos are public.
  - a state file          the last-done date per item, written by `done`. Under
                          the XDG state dir. Paths resolve via the constants below.

The due date is derived, never stored: next_due = last_done + cadence. A new item
with no recorded done simply shows as never done. Storing only last_done means
there is no date to keep in sync and nothing to drift.
"""

import contextlib
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.text import Text

from doit.cadence import is_due
from doit.cadence import overdue_days
from doit.cadence import parse_cadence
from doit.cadence import status_label
from doit.paths import xdg_config_home
from doit.paths import xdg_state_home
from doit.render import console
from doit.render import nudge_header
from doit.render import nudge_row
from doit.render import nudge_width
from doit.state import load_state
from doit.state import save_state

REGISTER = Path(os.environ.get('DOIT_REGISTER') or xdg_config_home() / 'doit' / 'register.yml')
STATE = Path(os.environ.get('DOIT_REVIEW_STATE') or xdg_state_home() / 'doit' / 'review-state.json')

TEMPLATE = """\
# Terminal-native review / cadence register, read by `doit review`.
#
# This file is declarative config — edit it freely; `doit review` only ever
# reads it. The done state lives in review-state.json, which the tool writes.
#
#   description  what to revisit or run
#   cadence      how often: <n>d / <n>w / <n>mo / <n>y  (e.g. 2w, 1mo)
#   command      optional; shown so you know what to run (not auto-run)

items:
  revisit-a-tool:
    description: Open doit launch, pick a tool you have not used lately, relearn it
    cadence: 1w
"""

# A nudge you have to scroll is a nudge you learn to ignore, so the roster is
# bounded and the overflow points at the browse view instead of printing itself.
NUDGE_MAX_ITEMS = 8

# The nudge interval is stored as plain minutes in the synced state dir so the
# shell gate can read it with a bare `cat` (no parsing) and the value is shared
# across machines. This is the same file `doit shell-init` emits a gate against.
NUDGE_INTERVAL = xdg_state_home() / 'doit' / 'nudge-interval-minutes'


def load_items() -> dict | None:
    """The register's items, or None if there is no register file yet."""
    if not REGISTER.exists():
        return None
    data = yaml.safe_load(REGISTER.read_text()) or {}
    return data.get('items') or {}


def statuses() -> list[dict]:
    """Every item with its derived overdue days, most urgent first.

    overdue is the number of days past due (negative = not yet due); None means
    the item has never been done, which sorts to the very top as most urgent.
    """
    items = load_items() or {}
    state = load_state(STATE)
    today = date.today()
    rows = []
    for item_id, meta in items.items():
        meta = meta or {}
        last = state.get(item_id)
        overdue = overdue_days(last, meta.get('cadence', ''), today)
        rows.append(
            {
                'id': item_id,
                'cadence': str(meta.get('cadence', '')),
                'desc': meta.get('description', '') or '',
                'command': meta.get('command', '') or '',
                'show': meta.get('show', '') or '',
                'last': last,
                'overdue': overdue,
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


def warn_no_register() -> None:
    console.print(Text(f'No review register at {REGISTER}.'))
    console.print('Add items there, or create one with [cyan]doit review edit[/].')


def cmd_due() -> int:
    if load_items() is None:
        warn_no_register()
        return 0
    rows = statuses()
    if not rows:
        console.print('The review register has no items yet.')
        return 0

    console.rule("[cyan]Review — what's due", align='left')
    due = [row for row in rows if is_due(row['overdue'])]
    if due:
        for row in due:
            render_item(row)
        console.print('Mark one done:  [cyan]doit review done <id>[/]')
        return 0

    console.print('[green]✓[/] All caught up.')
    soonest = rows[0]  # sorted desc, so the least-overdue (soonest) is first
    up_next = Text('  Next up: ')
    up_next.append(soonest['id'], style='yellow')
    up_next.append(f' in {-soonest["overdue"]}d.')
    console.print(up_next)
    return 0


def cmd_list(as_json: bool = False) -> int:
    # --json emits every item with its derived status, due or not, so a consumer
    # (the dashboard) filters on `overdue` itself and can say "2 due of 9". A
    # missing register is an empty list, not the prose hint: --json must always
    # be parseable.
    if as_json:
        # Plain print, never the rich console: a Console soft-wraps at terminal
        # width, which would put newlines inside JSON strings and hand a consumer
        # a parse error instead of data.
        print(json.dumps(statuses(), indent=2))
        return 0
    if load_items() is None:
        warn_no_register()
        return 0
    console.rule('[cyan]Review — full register', align='left')
    for row in statuses():
        render_item(row)
    return 0


def cmd_done(item_id: str) -> int:
    items = load_items()
    if items is None:
        warn_no_register()
        return 1
    if item_id not in items:
        console.print(Text(f'No such item: {item_id}'))
        console.print('See what is registered with [cyan]doit review list[/].')
        return 1
    today = date.today().isoformat()
    state = load_state(STATE)
    state[item_id] = today
    save_state(STATE, state)
    days = parse_cadence(items[item_id].get('cadence', ''))
    done = Text.from_markup('[green]✓[/] Marked ')
    done.append(item_id, style='yellow')
    done.append(f' done ({today}). Due again in {days}d.')
    console.print(done)
    return 0


def run_show(row: dict) -> None:
    """Run a due item's `show:` command and let its output print inline.

    Only the nudge calls this — a `show:` command produces live content and can
    have side effects (e.g. `toolbox remind` advances its round-robin so it cycles
    to the next neglected tool), so the manual `list`/`due` views only ever read.
    This is the one place a review item runs rather than being displayed, and it
    is opt-in per item: `command:` is shown, `show:` is run. Stdout is inherited
    so colored output survives, and a timeout keeps a slow command from wedging
    shell startup.

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


def cmd_nudge() -> int:
    """Startup nudge: print only what's due, and nothing at all when caught up.

    Deliberately a different density from `due`, not just a quieter one. `due`
    is a browse view you asked for, so it carries every field; a nudge is an
    interrupt you didn't, so each item collapses to identity, urgency, and the
    command to run. The description is browse-time information — at first shell
    you already know what your own register items mean.

    The shell caller gates how often this fires (a cheap marker-mtime check
    against the configured interval), so this stays a pure renderer — it never
    throttles itself, it just shows due items or stays silent. Silence-when-
    caught-up is the whole point: a nudge you don't notice on a clear day is a
    nudge you'll still read on a busy one.
    """
    if load_items() is None:
        return 0
    due = [row for row in statuses() if is_due(row['overdue'])]
    if not due:
        return 0
    shown = due[:NUDGE_MAX_ITEMS]
    nudge_header('Review', len(due))
    width = nudge_width([row['id'] for row in shown])
    for row in shown:
        nudge_row(row['id'], status_label(row['overdue']), row['command'], width)
    if len(due) > len(shown):
        console.print(f'  +{len(due) - len(shown)} more  ·  [cyan]doit review due[/]')
    # After the roster, expand any items carrying live content (e.g. the
    # neglected tool `toolbox remind` picks) so the roster stays scannable.
    console.print()
    for row in shown:
        run_show(row)
    console.print('Done:  [cyan]doit review done <id>[/]')
    return 0


def cmd_edit() -> int:
    if not REGISTER.exists():
        REGISTER.parent.mkdir(parents=True, exist_ok=True)
        REGISTER.write_text(TEMPLATE)
    subprocess.run([os.environ.get('EDITOR', 'vi'), str(REGISTER)], check=False)
    return 0


def parse_duration_minutes(dur: str) -> int | None:
    """'4h' → 240, '90m' → 90, bare '4' → hours. None if unparsable."""
    match = re.fullmatch(r'(\d+)\s*([hm]?)', dur.strip())
    if not match:
        return None
    count = int(match.group(1))
    unit = match.group(2) or 'h'  # a bare number means hours
    return count * 60 if unit == 'h' else count


def write_nudge_interval(minutes: int) -> int:
    """Record how often the startup nudge may fire."""
    NUDGE_INTERVAL.parent.mkdir(parents=True, exist_ok=True)
    NUDGE_INTERVAL.write_text(f'{minutes}\n')
    console.print(f'Nudge interval set to {minutes} minutes.')
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
    """Every registered item and its status."""
    raise typer.Exit(cmd_list(as_json))


@app.command('done')
def done_command(item_id: Annotated[str, typer.Argument(help='The register id to mark done.')]) -> None:
    """Mark an item done, advancing its due date."""
    raise typer.Exit(cmd_done(item_id))


@app.command('edit')
def edit_command() -> None:
    """Edit the register in $EDITOR."""
    raise typer.Exit(cmd_edit())


@app.command('nudge-every')
def nudge_every_command(
    duration: Annotated[str, typer.Argument(help='How often, e.g. 4h, 90m, or a bare number of hours.')],
) -> None:
    """Set the startup-nudge interval."""
    minutes = parse_duration_minutes(duration)
    if minutes is None or minutes <= 0:
        raise typer.BadParameter('expected a duration like 4h, 90m, or a bare number of hours')
    raise typer.Exit(write_nudge_interval(minutes))


@app.command('nudge', hidden=True)
def nudge_command() -> None:
    """The startup nudge, called by the shell gate rather than by hand."""
    raise typer.Exit(cmd_nudge())
