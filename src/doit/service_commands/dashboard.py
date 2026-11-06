"""`doit dashboard`, rendering the lanes the service answers."""

import datetime as dt
from typing import Annotated

import typer
from rich.text import Text

from doit import lanes as lanemodel
from doit import service
from doit.dashboard import FOCUSED_ROW_CAP
from doit.dashboard import LANE_NAMES
from doit.dashboard import ROW_CAP
from doit.dashboard import render_lanes
from doit.render import console
from doit.render import error_console
from doit.render import examples

DASHBOARD_EXAMPLES = examples(
    ('doit dashboard', 'every lane at a glance'),
    ('doit dashboard --lane tasks', 'one lane, with all its rows'),
    ('doit dashboard --json', 'the lanes as data, where each says whether it was read'),
)


def cmd_dashboard(lane: list[str] | None, as_json: bool) -> int:
    answered = service.client().get('/dashboard')
    for problem in answered.get('problems') or []:
        error_console.print(Text(f'sources: {problem}', style='yellow'))
    if not answered.get('configured'):
        error_console.print('No sources configured. Write them with [cyan]doit sources edit[/].')

    offered = lanemodel.from_document(answered)
    collected = [found for found in offered if found.name in lane] if lane else offered
    # `--lane` is not checked against a closed set: a conforming source
    # contributes lanes doit has never heard of, so the only honest check is
    # whether the name matched anything that actually came back.
    unmatched = [name for name in lane or [] if name not in {found.name for found in offered}]
    if unmatched:
        error_console.print(Text(f'No lane named {", ".join(unmatched)}.', style='yellow'))
        error_console.print(f'  Configured sources offered: {", ".join(found.name for found in offered) or "nothing"}')

    # Asking for one lane means you want to see it, not glance at it.
    row_cap = FOCUSED_ROW_CAP if lane else ROW_CAP

    if as_json:
        # Plain print, never the rich console: a Console soft-wraps at terminal
        # width, which would put newlines inside JSON strings.
        print(lanemodel.dumps(collected, dt.datetime.fromisoformat(answered['generated_at'])))
    else:
        render_lanes(collected, dt.date.today(), row_cap)
        # Which pursuits are behind, by name. A register that would not build
        # costs this one line rather than the glance, said in red, because a
        # missing line reads as nothing behind.
        standing = answered.get('pursuit_standing')
        if standing:
            # Soft-wrapped, because the line ends in a command to type.
            console.print(Text(f'  {standing["text"]}\n', style='red' if standing['unavailable'] else 'yellow'), soft_wrap=True)
    # A read-only glance always succeeds: degradation is shown per lane, and a
    # consumer of --json checks lanes[].status rather than the exit code.
    return 0


def dashboard_command(
    lane: Annotated[
        list[str] | None,
        typer.Option(
            '--lane',
            help=f'Show only this lane (repeatable): {", ".join(LANE_NAMES)}. A source can offer lanes beyond these.',
        ),
    ] = None,
    as_json: Annotated[bool, typer.Option('--json', help='Output the lane model as JSON to stdout.')] = False,
) -> None:
    """Every lane: what is outstanding across your apps.

    A lane is one list on this screen, what one app says is outstanding. Nothing
    ranks across lanes, so `doit next` is the command that picks one thing. A lane
    that could not be read says why, and the last line names any pursuit that is behind.
    """
    raise typer.Exit(cmd_dashboard(lane, as_json))
