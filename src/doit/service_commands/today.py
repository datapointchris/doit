"""`doit today`, rendering the day the service answers."""

import datetime as dt
import json
from typing import Annotated

import typer

from doit import lanes
from doit import quota
from doit import service
from doit.render import error_console
from doit.today import Day
from doit.today import render


def cmd_today(as_json: bool) -> int:
    answered = service.client().get('/today')
    for problem in answered.get('problems') or []:
        error_console.print(f'sources: {problem}')
    if as_json:
        # Plain print, never the rich console: a Console soft-wraps at terminal
        # width, which would put newlines inside JSON strings.
        print(json.dumps(answered, indent=2))
    else:
        figures = answered.get('quota')
        day = Day(
            [lanes.lane_from(lane) for lane in answered['done']],
            [lanes.lane_from(lane) for lane in answered['due']],
            None if figures is None else quota.from_dict(figures),
        )
        render(day, dt.datetime.fromisoformat(answered['generated_at']).date())
    # A read-only glance always succeeds. Degradation is shown per group, and a
    # consumer of --json checks each lane's status rather than the exit code.
    return 0


def today_command(
    as_json: Annotated[bool, typer.Option('--json', help='Output the day as JSON to stdout.')] = False,
) -> None:
    """What today still wants, and what it has had."""
    raise typer.Exit(cmd_today(as_json))
