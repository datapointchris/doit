"""The `doit pursuits` commands and `doit next`, each asking the service and rendering its answer."""

import datetime as dt
import json
import shlex
from collections.abc import Callable
from typing import Annotated

import typer
from rich.text import Text

from doit import service
from doit.journal import new_id
from doit.paths import machine_name
from doit.pursuits import DRIFT_SINCE
from doit.pursuits import GUTTER
from doit.pursuits import TEMPLATE
from doit.pursuits import Abandoned
from doit.pursuits import ask
from doit.pursuits import chosen_item
from doit.pursuits import due_in_days
from doit.pursuits import due_style
from doit.pursuits import format_due
from doit.pursuits import format_elapsed
from doit.pursuits import goal_text
from doit.pursuits import match_pursuit
from doit.pursuits import minutes_refusal
from doit.pursuits import offer
from doit.pursuits import offered_order
from doit.pursuits import paced
from doit.pursuits import print_reason
from doit.pursuits import prompt_for_minutes
from doit.pursuits import prompt_for_pursuit
from doit.pursuits import render_drift
from doit.pursuits import render_explain
from doit.pursuits import render_offers
from doit.pursuits import render_stranded_counts
from doit.pursuits import run_on_log
from doit.pursuits import standing_from
from doit.pursuits import unoffered
from doit.pursuits import which_choice
from doit.pursuits import write_names_cache
from doit.render import can_prompt
from doit.render import console
from doit.render import error_console
from doit.render import terminal_width
from doit.window import span
from doit.window import window


def cmd_next(explain: bool, as_json: bool, reroll: bool) -> int:
    """The service's draw: the one standing for this window, or a fresh one where none stands or ``reroll`` asks."""
    selection = service.client().get('/next', machine=machine_name(), reroll=reroll or None)
    if as_json:
        # Plain print, never the rich console: a Console soft-wraps at terminal
        # width, which would put newlines inside JSON strings.
        print(json.dumps(selection, indent=2))
        return 0

    now = dt.datetime.now().astimezone()
    write_names_cache(selection['pursuits'])
    state = standing_from(selection['state'], selection['pursuits'], now)
    names = selection['offered']
    if explain:
        return render_explain(state, selection)

    width = terminal_width()
    console.rule(f'[cyan]What now[/] · {now:%a %d %b}', align='left')
    # One per line. Joined on one line, two dates read as one event.
    for event in selection.get('context') or []:
        console.print(Text(f'{" " * GUTTER}{event}', style='magenta'), no_wrap=True, overflow='ellipsis')
    if selection.get('context_error'):
        # Said rather than left blank, because a blank reads as a quiet day.
        console.print(Text(f'{" " * GUTTER}events unavailable — {selection["context_error"]}', style='yellow'), soft_wrap=True)

    resolved = selection.get('resolved') or {}
    # The offered rows keep the order the draw gave them, never re-sorted here.
    # Every other pursuit follows, so the screen answers for the whole register.
    rest = offered_order(state, [name for name in state['active'] if name not in names])
    console.print()
    render_offers([offer(name, state, resolved) for name in names], [unoffered(name, state) for name in rest], width)
    return 0


def unrecorded(name: str, words: list[str], minutes: int | None, occurred: dt.datetime, downstream: dict) -> None:
    """Say that on_log ran and the entry did not land, and the log that records it without running on_log again."""
    again = ['doit', 'pursuits', 'log', name, '--no-write', *(['--minutes', str(minutes)] if minutes else []), *words]
    error_console.print(Text(f'  on_log already ran: {downstream.get("command")}', style='yellow'), soft_wrap=True)
    error_console.print(
        Text(f'  Record it without running on_log again, adding --ago for the time since {occurred:%H:%M}:  {shlex.join(again)}'),
        soft_wrap=True,
    )


def cmd_log(name: str | None, words: list[str], ago: str | None, minutes: int | None, assume_yes: bool, no_write: bool) -> int:
    """Record having done one, asking for whatever was not passed as a flag.

    Anything given on the command line is never asked about, so the fast path
    stays one line. What is asked for is the pursuit, the note and — for a pursuit
    measured in time — the duration, which is what the entry is made of there
    rather than an extra nobody discovers from a help screen they never open.
    """
    client = service.client()
    # The register and the standing draw rather than the whole state: this asks
    # no app, so the question appears at typing speed.
    standing = client.get('/pursuits/register')
    pursuits, offered = standing['pursuits'], standing['offered']

    try:
        if name is None:
            if not can_prompt():
                error_console.print('Which pursuit? Pass it as the argument:  [cyan]doit pursuits log <pursuit>[/]')
                return 1
            matched = prompt_for_pursuit(pursuits, offered)
        else:
            matched = match_pursuit(name, pursuits) or ''
            if not matched:
                error_console.print(f'No pursuit named {name}. See:  [cyan]doit pursuits list[/]')
                return 1
        if minutes is not None and (refusal := minutes_refusal(matched, pursuits[matched], minutes)):
            raise typer.BadParameter(refusal)
        if not words and can_prompt():
            answer = ask('  note, or Enter to skip: ')
            words = answer.split() if answer else []
        if minutes is None and (refusal := minutes_refusal(matched, pursuits[matched], None)):
            if not can_prompt():
                raise typer.BadParameter(f'{refusal}: pass --minutes <n>')
            minutes = prompt_for_minutes()
    except Abandoned:
        error_console.print('  Nothing logged.')
        return 1

    occurred = dt.datetime.now().astimezone()
    if ago:
        delta = span(ago)
        if delta is None:
            raise typer.BadParameter(f'--ago takes a span such as 30s, 90m, 3h, 2d or 1w, not {ago!r}.')
        occurred -= delta

    # The standing draw's entry is what was on screen, so the service answers with
    # the concrete item this pursuit meant, and resolves afresh only where the draw
    # failed, matched nothing or never offered it.
    item = client.get(f'/pursuits/{matched}/item')['resolved'] if pursuits[matched].get('resolve') else {}
    chosen = chosen_item(item, which_choice(item.get('choices') or [], bool(pursuits[matched].get('on_log'))))

    downstream = None
    if not no_write:
        downstream = run_on_log(pursuits[matched], chosen, ' '.join(words), minutes, assume_yes)

    # Logging a pursuit says the pursuit happened. It does not say which of its
    # candidates did unless you said so, and the note is prose. So the item is
    # only named where naming it is a fact: the backend matched one row, or the
    # write-through completed the one you picked.
    acted = bool((downstream or {}).get('ran'))
    named = chosen if chosen and (int(item.get('candidates') or 1) == 1 or acted) else {}

    # The service marks the pursuit logged on the standing draw, so it is not
    # offered again inside the window, and says when it next comes due.
    body = {
        'id': new_id(dt.datetime.now().astimezone()),
        'pursuit': matched,
        'occurred_at': occurred.isoformat(),
        'note': ' '.join(words) or None,
        'duration_minutes': minutes,
        'item': named or None,
        'downstream': downstream,
    }
    try:
        days = client.post('/journal/done', service.signed(body), repeatable=True)['due_in_days']
    except service.ServiceFailed:
        if acted:
            unrecorded(matched, words, minutes, occurred, downstream or {})
        raise
    label = f' — {named["label"]}' if named.get('label') else ''
    logged = Text.from_markup('[green]Logged[/] ')
    logged.append(f'{matched}{label}' + ('' if days is None else f' · {format_due(days)}'))
    console.print(logged)
    if downstream and downstream.get('ran'):
        ran = Text.from_markup('[green]Ran[/] ')
        ran.append(downstream['command'])
        console.print(ran)
    return 0


def cmd_skip(name: str | None, duration: str | None) -> int:
    """Pass on a pursuit for a span. The service refuses one outside the draw, which drift would still count as a pass."""
    client = service.client()
    standing = client.get('/pursuits/register')
    pursuits = standing['pursuits']
    try:
        if name is None:
            if not can_prompt():
                error_console.print('Which pursuit? Pass it as the argument:  [cyan]doit pursuits skip <pursuit>[/]')
                return 1
            drawn = {name: pursuits[name] for name in standing['active']}
            if not drawn:
                error_console.print('Nothing is in the draw to pass.')
                return 1
            matched = prompt_for_pursuit(drawn, standing['offered'])
        else:
            matched = match_pursuit(name, pursuits) or ''
            if not matched:
                error_console.print(f'No pursuit named {name}.')
                return 1
    except Abandoned:
        error_console.print('  Nothing skipped.')
        return 1

    passed_for = client.post(
        '/journal/skip',
        service.signed({'id': new_id(dt.datetime.now().astimezone()), 'pursuit': matched, 'for': duration}),
        repeatable=True,
    )
    until = dt.datetime.fromisoformat(passed_for['until'])
    passed = Text.from_markup('[yellow]Passed[/] ')
    passed.append(
        f'{matched} — out of the draw for {passed_for["days"]}d, until {until:%d %b %Y}. Owes nothing meanwhile, weight untouched.'
    )
    console.print(passed)
    console.print(f'  Back sooner:  [cyan]doit pursuits resume {matched}[/]')
    return 0


def cmd_resume(name: str | None) -> int:
    """End a standing skip now, so the pursuit returns to the next draw.

    The verb that retires the mark `skip` writes. Without one a mistyped span is
    unreachable by any command the tool ships, in a journal nothing rewrites.

    Written as a skip that has already expired rather than as a deletion, because
    the journal is append-only and a pass that was made is still a fact about the
    week it was made in.
    """
    chosen = service.client().post('/journal/resume', service.signed({'pursuit': name}))['resumed']
    if not chosen:
        console.print(f'{name} is not skipped, and is already in the draw.' if name else 'Nothing is skipped.')
        return 0
    back = Text.from_markup('[green]Resumed[/] ')
    back.append(f'{", ".join(chosen)} — back in the next draw.')
    console.print(back)
    return 0


def cmd_reset(name: str | None, assume_yes: bool) -> int:
    """Start a balance again from now — one pursuit, or every one when none is named.

    The journal stays append-only: a reset is a boundary marker written into it,
    not a rewrite of what happened. Everything before the marker still reads as
    history and stops counting toward what is owed.

    The whole-register form is confirmed, because it discards every accrued
    balance at once and nothing the tool ships puts one back. A single pursuit
    goes through unasked — that is one number, and the register says what it
    should be.
    """
    client = service.client()
    if name is None:
        everything = sorted(client.get('/pursuits/register')['pursuits'])
        if not assume_yes and not confirm_reset(everything):
            error_console.print('  Nothing reset.')
            return 1
    chosen = client.post('/journal/reset', service.signed({'pursuits': None if name is None else [name]}))['reset']
    done = Text.from_markup('[green]Reset[/] ')
    done.append(f'{", ".join(chosen)} — {"each balance" if len(chosen) > 1 else "the balance"} starts again from now.')
    console.print(done)
    return 0


def confirm_reset(chosen: list[str]) -> bool:
    """Ask before discarding every accrued balance, refusing where nobody can answer."""
    if not can_prompt():
        error_console.print(f'Resetting all {len(chosen)} pursuits discards every balance. Pass [cyan]--yes[/] to do it unattended.')
        return False
    prompt = Text('  ')
    prompt.append(f'Reset all {len(chosen)} pursuits', style='yellow')
    prompt.append(' — every accrued balance is discarded. [y/N] ')
    try:
        return console.input(prompt).strip().lower() in ('y', 'yes')
    except (KeyboardInterrupt, EOFError):
        return False


def cmd_list(as_json: bool) -> int:
    client = service.client()
    listed = client.get('/pursuits')
    if as_json:
        # See cmd_next: a Console would soft-wrap this into invalid JSON.
        print(json.dumps(listed, indent=2))
        return 0

    pursuits = listed['pursuits']
    write_names_cache(pursuits)
    state = standing_from(listed['state'], pursuits, dt.datetime.now().astimezone())

    console.rule('[cyan]Pursuits', align='left')
    ordered = sorted(pursuits.items(), key=lambda row: -row[1].get('weight', 0))
    names = max(len(name) for name in pursuits)
    goals = max(len(goal_text(config)) for _, config in ordered)
    lasts = max(len(format_elapsed(state['days_since'].get(name))) for name, _ in ordered)
    for name, config in ordered:
        line = Text('  ')
        line.append(name.ljust(names), style='white')
        line.append(f'  {int(config.get("weight", 0)):>3}' if paced(config) else '     ')
        line.append(f'  {goal_text(config).ljust(goals)}')
        line.append(f'  last {format_elapsed(state["days_since"].get(name)).ljust(lasts)}')
        if paced(config):
            days = due_in_days(state, name)
            line.append('  ')
            line.append(format_due(days), style=due_style(days))
        if config.get('paused'):
            line.append('  paused', style='yellow')
        elif name in state['term_ended']:
            line.append(f'  term ended {config["until"]}', style='yellow')
        elif name in state['suppressed']:
            line.append('  skipped', style='yellow')
        console.print(line, no_wrap=True, overflow='ellipsis')
        print_reason(config.get('reason'))
    tracking = ' · tracked is logged and never due' if not all(paced(config) for config in pursuits.values()) else ''
    console.print(f'\n  weight orders what is owed and draws the rest{tracking} · [cyan]doit pursuits edit[/]')
    render_stranded_counts(client.get('/pursuits/offers'))
    return 0


def cmd_drift(since: str | None, until: str | None, as_json: bool) -> int:
    """What each goal asked for over the window against what got done.

    The report the whole thing is for. It never adjusts a goal — what you said
    and what you did are different signals, and blending them would destroy the
    only honest comparison available. Offered counts sit beside them because they
    separate the two failures that look identical from the outside: a pursuit
    that never comes up, and one that comes up and gets ignored.
    """
    # Checked here as well, so a mistyped window is a usage error before anything is asked.
    try:
        window(since or (None if until else DRIFT_SINCE), until, dt.datetime.now().astimezone())
    except ValueError as error:
        raise typer.BadParameter(str(error)) from None
    report = service.client().get('/pursuits/drift', since=since, until=until)
    if as_json:
        # See cmd_next: a Console would soft-wrap this into invalid JSON.
        print(json.dumps(report, indent=2))
        return 0

    rows = report['rows']
    start = report['since'] and dt.datetime.fromisoformat(report['since'])
    opened = 'the first record' if not start else f'{start:%d %b %Y %H:%M}'
    closed = 'now' if until is None else f'{dt.datetime.fromisoformat(report["until"]):%d %b %Y %H:%M}'
    console.rule(f'[cyan]Drift · {opened} to {closed}', align='left')
    if not any(row['done'] for row in rows):
        console.print('Nothing recorded in the window yet — drift needs history before it can say anything.\n')
        return 0

    render_drift(rows)
    console.print()
    return 0


def cmd_dormant() -> int:
    """Pursuits gone quiet for more than three of their own intervals."""
    stale = service.client().get('/pursuits/dormant')['dormant']
    console.rule('[cyan]Dormant', align='left')
    if not stale:
        console.print('Nothing is running cold.\n')
        return 0
    width = max(len(row['pursuit']) for row in stale)
    for row in stale:
        line = Text('  ')
        line.append(f'{row["pursuit"]:<{width}}', style='white')
        line.append(f'  {format_elapsed(row["days_since"])} · goal {row["goal"]}')
        console.print(line)
        print_reason(row.get('reason'))
    console.print('\n  Cold is the time since the last one.')
    console.print('  [cyan]doit pursuits list[/] shows it beside what each owes.\n')
    return 0


def cmd_evidence(as_json: bool = False) -> int:
    """What each app says you last did, and which of them could not be asked.

    Its own command because the draw consumes this silently. A pursuit whose
    backend is logged out falls back to the journal and simply reads as never
    done, which looks identical to genuinely never having done it — the two need
    to be told apart somewhere, and this is where.
    """
    client = service.client()
    answered = client.get('/pursuits/evidence')
    if as_json:
        print(json.dumps(answered, indent=2))
        return 0

    now = dt.datetime.now().astimezone()
    seen = {name: dt.datetime.fromisoformat(when) for name, when in answered['observed'].items()}
    failed = answered['errors']
    undeclared = set(answered['undeclared'])

    console.rule('[cyan]Evidence', align='left')
    width = max((len(name) for name in answered['watched']), default=0)
    for name in answered['watched']:
        line = Text('  ')
        line.append(f'{name:<{width}}  ', style='white')
        if name in undeclared:
            line.append('no backend — logged by hand', style='yellow')
        elif name in failed:
            line.append(failed[name], style='red')
        elif name in seen:
            elapsed = (now - seen[name]).total_seconds() / 86400.0
            line.append(f'{format_elapsed(elapsed)} · {seen[name]:%d %b %H:%M}')
        else:
            line.append('asked, nothing recorded yet')
        console.print(line)
    console.print('\n  The apps are asked on a timer; this asks them now.\n')
    return 0


def cmd_edit() -> int:
    """Edit the register in $EDITOR, starting from the template where none is written yet."""
    if not service.edit_document('pursuits', TEMPLATE):
        return 1
    write_names_cache(service.client().get('/pursuits/register')['pursuits'])
    return 0


def cmd_names() -> int:
    """Emit name<TAB>description for the shell completion, refreshing its cache."""
    pursuits = service.client().get('/pursuits/register')['pursuits']
    write_names_cache(pursuits)
    for name, config in sorted(pursuits.items()):
        print(f'{name}\t{config.get("description", "")}')
    return 0


def next_command(
    explain: Annotated[bool, typer.Option('--explain', help='The same draw with every number behind it.')] = False,
    reroll: Annotated[bool, typer.Option('--reroll', help='Force a fresh draw before the standing one expires.')] = False,
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
) -> None:
    """What to do now, drawn from what you said matters."""
    run(lambda: cmd_next(explain, as_json, reroll))


def log_command(
    pursuit: Annotated[str | None, typer.Argument(help='The pursuit, by name or unambiguous prefix (asked for when omitted).')] = None,
    note: Annotated[list[str] | None, typer.Argument(help='Free text recorded with the entry (asked for when omitted).')] = None,
    ago: Annotated[str | None, typer.Option('--ago', help='Log something you did earlier: a span such as 30s, 90m, 3h, 2d or 1w.')] = None,
    minutes: Annotated[
        int | None,
        typer.Option('--minutes', min=1, help='How long it took, in whole minutes (asked for when a pursuit is measured in time).'),
    ] = None,
    assume_yes: Annotated[bool, typer.Option('-y', '--yes', help="Run the pursuit's on_log command without asking.")] = False,
    no_write: Annotated[bool, typer.Option('--no-write', help='Log only; never touch the owning app.')] = False,
) -> None:
    """Record having done one, writing through to the app that owns it.

    Pass every field to log in one line. Leave one out at a terminal and it is
    asked for, with the pursuits listed and the current draw marked. A field
    already passed is never asked about, and --no-input skips every question.
    """
    run(lambda: cmd_log(pursuit, note or [], ago, minutes, assume_yes, no_write))


def skip_command(
    pursuit: Annotated[str | None, typer.Argument(help='The pursuit, by name or unambiguous prefix (asked for when omitted).')] = None,
    duration: Annotated[
        str | None,
        typer.Option('--for', help='How long to pass for, in days or weeks: 3d or 2w. One interval when omitted.'),
    ] = None,
) -> None:
    """Pass on one — out of the draw until it expires, owing nothing meanwhile."""
    run(lambda: cmd_skip(pursuit, duration))


app = typer.Typer(name='pursuits', no_args_is_help=True, help='What `doit next` draws from: each pursuit, its goal and its weight.')


@app.command('list')
def list_command(
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
) -> None:
    """Every pursuit, its weight, goal and when it is next due."""
    run(lambda: cmd_list(as_json))


@app.command('drift')
def drift_command(
    since: Annotated[
        str | None,
        typer.Option(
            '--since',
            help=(
                'Where the window starts: a span back from now (30m, 12h, 7d, 2w), a day or an RFC 3339 instant. '
                'With only --until, the first record.'
            ),
            show_default=DRIFT_SINCE,
        ),
    ] = None,
    until: Annotated[
        str | None,
        typer.Option('--until', help='Where the window ends, in the same forms. A day includes all of itself.', show_default='now'),
    ] = None,
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
) -> None:
    """What each goal asked for against what you did."""
    run(lambda: cmd_drift(since, until, as_json))


@app.command('dormant')
def dormant_command() -> None:
    """Pursuits gone quiet for more than three of their own intervals."""
    run(cmd_dormant)


@app.command('reset')
def reset_command(
    pursuit: Annotated[str | None, typer.Argument(help='The pursuit to zero; every one when omitted.')] = None,
    assume_yes: Annotated[bool, typer.Option('-y', '--yes', help='Zero every pursuit without confirming.')] = False,
) -> None:
    """Start a balance again from now; the journal keeps its history."""
    run(lambda: cmd_reset(pursuit, assume_yes))


@app.command('resume')
def resume_command(
    pursuit: Annotated[str | None, typer.Argument(help='The pursuit to bring back; every skipped one when omitted.')] = None,
) -> None:
    """End a standing skip, so the pursuit returns to the next draw."""
    run(lambda: cmd_resume(pursuit))


@app.command('evidence')
def evidence_command(
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
) -> None:
    """What each app says you last did, asked now rather than on the timer."""
    run(lambda: cmd_evidence(as_json))


@app.command('edit')
def edit_command() -> None:
    """Edit the pursuits in $EDITOR, starting from an annotated template where none are written."""
    run(cmd_edit)


@app.command('names', hidden=True)
def names_command() -> None:
    """Emit name<TAB>description for the shell completion."""
    run(cmd_names)


def run(action: Callable[[], int]) -> None:
    raise typer.Exit(action())


app.command('log')(log_command)
app.command('skip')(skip_command)
