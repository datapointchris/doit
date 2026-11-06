"""The `doit sources` commands, each asking the service or printing what an adapter declares."""

import datetime as dt
import json
from typing import Annotated

import typer
from rich.text import Text

from doit import lanes
from doit import service
from doit.render import console
from doit.render import error_console
from doit.render import examples
from doit.sources import TEMPLATE
from doit.sources import report_block

app = typer.Typer(
    name='sources',
    no_args_is_help=True,
    help=(
        'Which apps doit asks, and what it asks them.\n\n'
        'A source is one app doit reads, and it becomes one or more lanes on the dashboard. Which apps those are is '
        'yours to declare, in the document `doit sources edit` opens.'
    ),
    epilog=examples(
        ('doit sources list', 'every configured source, and whether it answers'),
        ('doit sources edit', 'turn one on or off'),
    ),
)


@app.command(
    'list',
    epilog=examples(
        ('doit sources list', 'every configured source, and whether it answers'),
        ('doit sources list --json', 'the same answers as data'),
    ),
)
def list_command(
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
) -> None:
    """Every configured source, and whether the service's read of it answers."""
    report = service.client().get('/sources')
    for problem in report['problems']:
        error_console.print(Text(f'sources: {problem}', style='yellow'))
    answers = report['answers']
    if as_json:
        print(json.dumps(answers, indent=2))
        raise typer.Exit(0)
    if not answers:
        console.print('No sources configured. Write them with [cyan]doit sources edit[/].')
        raise typer.Exit(0)
    width = max(len(str(row['id'])) for row in answers)
    report_block('outstanding', [row for row in answers if row['block'] == 'outstanding'], width)
    report_block('happened', [row for row in answers if row['block'] == 'happened'], width)
    raise typer.Exit(0)


@app.command('example', epilog=examples(('doit sources example', 'see what the document can say, before writing one')))
def example_command() -> None:
    """Print the annotated example of the sources, writing nothing."""
    print(TEMPLATE)
    raise typer.Exit(0)


@app.command('edit', epilog=examples(('doit sources edit', 'turn a source on or off')))
def edit_command() -> None:
    """Edit the sources in $EDITOR, starting from the example where none are written."""
    raise typer.Exit(0 if service.edit_document('sources', TEMPLATE) else 1)


@app.command('contract', epilog=examples(('doit sources contract', 'the shape every lane takes, for a source you are writing')))
def contract_command() -> None:
    """The lane document every source becomes, as an annotated example.

    This is the interface between a source and every view: each adapter's read
    is shaped into it, and `doit dashboard --json` prints it.
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
        dt.datetime(2026, 8, 6, 12, 0, 0),
    )
    print(json.dumps(example, indent=2))
    raise typer.Exit(0)
