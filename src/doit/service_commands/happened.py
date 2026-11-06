"""`doit happened`, rendering the window the service answers."""

import datetime as dt
import json
from typing import Annotated

import typer

from doit import lanes
from doit import service
from doit import window
from doit.happened import DEFAULT_SINCE
from doit.happened import render
from doit.render import error_console
from doit.render import examples
from doit.window import Bounds

HAPPENED_EXAMPLES = examples(
    ('doit happened', 'the past week'),
    ('doit happened --since 30d', 'the past month'),
    ('doit happened --since 2026-11-01 --until 2026-11-03', 'three whole days, since a day includes all of itself'),
    ('doit happened --json', 'every group as data'),
)


def cmd_happened(since: str | None, until: str | None, as_json: bool) -> int:
    now = dt.datetime.now().astimezone()
    if since is None and until is None:
        since = DEFAULT_SINCE
    try:
        start, stop = window.window(since, until, now)
    except ValueError as error:
        error_console.print(f'doit happened: {error}')
        return 2
    if start is not None and start >= (stop or now):
        error_console.print(f'doit happened: --since {since} is not before {until or "now"}.')
        return 2
    answered = service.client().get('/happened', since=since, until=until)
    for problem in answered.get('problems') or []:
        error_console.print(f'sources: {problem}')
    groups = [lanes.lane_from(lane) for lane in answered['groups']]
    if as_json:
        # Plain print, never the rich console: a Console soft-wraps at terminal
        # width, which would put newlines inside JSON strings.
        print(json.dumps(answered, indent=2))
    else:
        bounds = Bounds(dt.datetime.fromisoformat(answered['since']), dt.datetime.fromisoformat(answered['until']))
        render(groups, bounds, open_ended=until is None)
    # A read that asked some records exits 0 and names the rest in its verdict.
    # One that could ask none exits 1, so a script can tell it from a quiet window.
    return 1 if all(not lane.available for lane in groups) else 0


def happened_command(
    since: Annotated[
        str | None,
        typer.Option(
            '--since',
            help=f'Start of the window: {window.FORMS}. {DEFAULT_SINCE} ago where neither end is given, '
            'the earliest record where only --until is.',
        ),
    ] = None,
    until: Annotated[
        str | None, typer.Option('--until', help='End of the window, in the same forms; a day includes all of itself. Now where omitted.')
    ] = None,
    as_json: Annotated[bool, typer.Option('--json', help='Output every group as JSON to stdout.')] = False,
) -> None:
    """What happened over a span of time, from every app and log, and who did it.

    `doit today` folds the same records over the one day, beside what the day still owes.
    """
    raise typer.Exit(cmd_happened(since, until, as_json))
