"""The `doit forecast` commands, each asking the service and rendering its answer."""

import json
from typing import Annotated

import typer
from rich.text import Text

from doit import service
from doit.forecast import Verdict
from doit.forecast import emit
from doit.forecast import emit_trend
from doit.forecast import reading_from
from doit.forecast import source_summary
from doit.render import console
from doit.render import error_console
from doit.render import examples
from doit.service_commands.pursuits import run


def cmd_run(as_json: bool, budget: int | None) -> int:
    """Take a forecast and store it."""
    taken = service.client().post('/forecasts', {'budget_minutes': budget})
    if as_json:
        print(json.dumps(taken, indent=2))
    else:
        emit(reading_from(taken))
    return 0


def cmd_show(handle: str, as_json: bool) -> int:
    """The newest stored forecast, or the one a timestamp prefix names."""
    held = service.client().get(f'/forecasts/{handle or "latest"}')
    if as_json:
        print(json.dumps(held, indent=2))
    else:
        emit(reading_from(held))
    return 0


def cmd_list(as_json: bool) -> int:
    """Every stored forecast, oldest first."""
    held = service.client().get('/forecasts')
    if not held:
        error_console.print('No forecast stored yet:  [cyan]doit forecast run[/]')
        return 1
    if as_json:
        print(json.dumps(held, indent=2))
        return 0
    stored = [reading_from(reading) for reading in held]
    console.rule('[cyan]Forecasts', align='left')
    for reading in stored:
        row = Text('  ')
        row.append(f'{reading.generated[:16].replace("T", " ")}  ', style='white')
        row.append(f'{reading.machine:<10} {reading.budget_minutes:>4}m/day  ', style='dim')
        row.append(source_summary(reading), style='dim')
        console.print(row)
    plural = '' if len(stored) == 1 else 's'
    console.print(f'\n  {len(stored)} reading{plural} · [cyan]doit forecast trend[/] grades the matured ones')
    return 0


def cmd_trend(days: int, as_json: bool) -> int:
    """What the forecasts predicted against what the journal went on to record."""
    graded = service.client().get('/forecasts/trend', days=days)
    if not graded['verdicts']:
        error_console.print(f'No reading is {days} days old yet, so nothing can be graded:  [cyan]doit forecast run[/]')
        return 1
    if as_json:
        print(json.dumps(graded, indent=2))
        return 0
    emit_trend([Verdict(**verdict) for verdict in graded['verdicts']], days, graded['readings'])
    return 0


app = typer.Typer(
    name='forecast',
    no_args_is_help=True,
    help=(
        'Your pursuits run forward, to show whether their paces fit your days.\n\n'
        'A forecast runs the register, the document `doit pursuits edit` opens, through the real draw, and stores '
        'what it predicted. Once enough time has passed, a trend grades that against what you logged.'
    ),
    epilog=examples(
        ('doit forecast run', 'take a forecast now and store it'),
        ('doit forecast show', 'read the newest one back'),
        ('doit forecast trend', 'how earlier forecasts held up'),
    ),
)


JsonOption = Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')]


@app.command(
    'run',
    epilog=examples(
        ('doit forecast run', 'at the daily budget the register declares'),
        ('doit forecast run --budget 120', 'what two hours a day would get done'),
        ('doit forecast run --json', 'the same reading as data'),
    ),
)
def run_command(
    budget: Annotated[int | None, typer.Option('--budget', help='Minutes a day to spend, overriding the register.')] = None,
    as_json: JsonOption = False,
) -> None:
    """Take a forecast from the register as it stands, and store it."""
    run(lambda: cmd_run(as_json, budget))


@app.command(
    'show',
    epilog=examples(
        ('doit forecast show', 'the newest forecast'),
        ('doit forecast show 2026-11', 'the one a timestamp starting that way names'),
        ('doit forecast show --json', 'the newest as data'),
    ),
)
def show_command(
    handle: Annotated[str, typer.Argument(help='Timestamp prefix; the newest when omitted.')] = '',
    as_json: JsonOption = False,
) -> None:
    """Read a stored forecast back."""
    run(lambda: cmd_show(handle, as_json))


@app.command(
    'list',
    epilog=examples(
        ('doit forecast list', 'every stored forecast and its timestamp, to pass to show'),
        ('doit forecast list --json', 'the same forecasts as data'),
    ),
)
def list_command(as_json: JsonOption = False) -> None:
    """Every forecast taken, oldest first."""
    run(lambda: cmd_list(as_json))


@app.command(
    'trend',
    epilog=examples(
        ('doit forecast trend', 'how the week ahead held up'),
        ('doit forecast trend --days 30', 'how the month ahead held up'),
        ('doit forecast trend --json', 'the same verdicts as data'),
    ),
)
def trend_command(
    days: Annotated[int, typer.Option('--days', help='Which horizon to grade: 7, 14 or 30.')] = 7,
    as_json: JsonOption = False,
) -> None:
    """Grade the forecasts whose window has closed against the journal."""
    run(lambda: cmd_trend(days, as_json))
