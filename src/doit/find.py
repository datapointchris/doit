"""Search the federated index, then hand you off to whoever owns the answer.

`doit find` is one motion: fuzzy-match a line, press Enter, get everything known
about that subject assembled from every collection that has it. `doit show` is
that same composite when you already know the name, and it is a command rather
than a picker callback because knowing the name is the common case. `doit launch`
is the other direction: your areas and your own tools, for when the question is
"what can I even run here".

`doit choose` is the same picker ending somewhere else: it prints the row's
invocation and nothing else, so the thing you pointed at can be run rather than
only read. Landing it on the prompt is the shell's half — see `doit.shell` — and
has to be, because the line editor belongs to the parent process this one cannot
reach into.

fzf is shelled out to rather than reimplemented. It is already the picker
everywhere else on this machine, and a Python reimplementation would be a worse
one that also had to be maintained.

Nothing here renders a subject itself. Each lens hands off to whatever owns it —
`doit.tools` for anything out of the registry or the shell files, `doit workflows
show` for a card, `bat` over a skill file — because a second renderer is a second
thing to keep in step with the first. Whether that hand-off is an in-process call
or a subprocess follows from where the owner lives, and is not a distinction this
module makes.
"""

import json
import shutil
import subprocess
from collections.abc import Callable
from datetime import date
from typing import Annotated

import typer
from rich.text import Text

from doit import digest
from doit import index
from doit import rotate
from doit import skills
from doit import tools
from doit import usage
from doit.render import console
from doit.render import error_console

# What bare `doit launch` offers above your own tools: the areas of doit itself.
AREAS = [
    ('next', 'What to do now, drawn from your weighted pursuits'),
    ('dashboard', 'Everything outstanding across your apps, in lanes'),
    ('find', 'Search across tools, cards, skills, funcs, aliases, git'),
    ('review due', 'What is due to revisit'),
    ('labs due', 'Hands-on practice that is due now'),
]

FZF_COMMON = [
    '--delimiter=\t',
    '--with-nth=1',
    '--tiebreak=begin,length',
    '--no-hscroll',
    '--preview-window=right:62%',
]

# The header is the only thing on screen naming which picker you are in, so it
# is named here rather than quoted at the call site — two commands share one
# picker, and a copy-paste header is the drift that makes them indistinguishable.
FIND_HEADER = 'Enter opens everything known about it'
CHOOSE_HEADER = 'Enter puts it on your command line'
LAUNCH_HEADER = 'Your areas and tools · Enter shows it'


def run_fzf(lines: list[str], query: str, preview: str, header: str) -> str | None:
    """Pick one line with fzf: the row, '' when the pick was cancelled, None when fzf is missing.

    Cancelling and having no picker are different answers and every caller has
    to tell them apart. `choose` is read back through `$(...)`, where exit status
    is the caller's only signal — a missing binary reported as success is an
    empty command line with no explanation of why.

    A missing fzf is reported rather than raised: the index is still usable
    through `--json`, and a stack trace would say less than one sentence does.
    """
    if not shutil.which('fzf'):
        error_console.print('fzf is not installed on this machine.')
        return None
    command = [
        'fzf',
        *FZF_COMMON,
        f'--query={query}',
        '--prompt=doit ❯ ',
        f'--preview={preview}',
        f'--header={header}',
    ]
    result = subprocess.run(command, input='\n'.join(lines), text=True, capture_output=True, check=False)
    return result.stdout.strip()


def index_lines(entries: list[index.Entry]) -> list[str]:
    """`display <TAB> source <TAB> name <TAB> invocation` — field one is shown and searched.

    The invocation rides along rather than being looked up again after the pick.
    `--with-nth=1` means fzf neither displays nor matches it, and re-resolving a
    name through a second `build_index()` is a second answer that can disagree
    with the row the human actually pointed at.
    """
    return [f'{entry.display()}\t{entry.source}\t{entry.name}\t{entry.invocation}' for entry in entries]


def cmd_find(term: str, sources: list[str] | None) -> int:
    entries = index.build_index(sources)
    if not entries:
        error_console.print('Nothing indexed. Fetch the library with [cyan]doit content sync[/].')
        return 1
    selected = run_fzf(index_lines(entries), term, 'doit __preview {2} {3}', FIND_HEADER)
    if selected is None:
        return 1
    if not selected:
        return 0
    return cmd_show(selected.split('\t')[2])


def cmd_choose(term: str, sources: list[str] | None) -> int:
    """The same pick, ending on the command line instead of in a card.

    `find` answers "tell me about this" and `choose` answers "give me this to
    run" — two terminal acts over one picker, so neither has to be a mode of the
    other. Plain `print` rather than the console: this string is the answer a
    caller reads back through `$(...)`, and a style code or a wrap in it would
    land on the prompt verbatim.
    """
    entries = index.build_index(sources)
    if not entries:
        error_console.print('Nothing indexed. Fetch the library with [cyan]doit content sync[/].')
        return 1
    selected = run_fzf(index_lines(entries), term, 'doit __preview {2} {3}', CHOOSE_HEADER)
    if selected is None:
        return 1
    if not selected:
        return 0
    print(selected.split('\t')[3])
    return 0


def cmd_launch() -> int:
    """Areas first, then your own tools — the "what can I run here" question.

    Your own tools, not all 130: a launcher answering "what can I run here" with
    every third-party binary on the machine answers a different question.
    """
    own = [entry for entry in index.index_tools() if entry.category == 'custom-tools']
    rows = [f'{name:<18} {description}\t{name}' for name, description in AREAS]
    rows += [f'{entry.name:<18} {entry.description}\t{entry.name}' for entry in sorted(own, key=lambda e: e.name)]
    selected = run_fzf(rows, '', 'doit __preview tool {2}', LAUNCH_HEADER)
    if selected is None:
        return 1
    if not selected:
        return 0
    return cmd_show(selected.split('\t')[-1])


def lens_sources(subject: str) -> dict[str, index.Entry]:
    """Every indexed row whose name is this subject, keyed by collection."""
    return {entry.source: entry for entry in index.build_index() if entry.name == subject}


def render_section(title: str, style: str) -> None:
    console.print()
    console.print(Text(title, style=style))


def delegate(command: list[str]) -> bool:
    """Run a lens's own renderer. False when the tool is not installed."""
    try:
        subprocess.run(command, check=False)
    except FileNotFoundError:
        return False
    return True


def cmd_show(subject: str) -> int:
    """Everything known about one subject, from every collection that has it.

    Assembled in a fixed order so the same subject reads the same way every
    time, and each section names the tool it came from — a composite that hides
    its sources is one you cannot go and correct.
    """
    found = lens_sources(subject)
    if not found:
        error_console.print(Text(f'Nothing known about {subject!r} in any collection.'))
        error_console.print(f'Collections searched: {", ".join(index.LENSES)}')
        return 1

    console.rule(f'[cyan]{subject}', align='left')
    console.print(Text(f'Found in: {", ".join(sorted(found))}', style='blue'))

    if 'tool' in found:
        entry = found['tool']
        render_section(f'registry — {entry.invocation}', 'yellow')
        tools.render_tool(subject, tools.load_registry().get(subject) or {}, heading=False)
        render_section('tldr — common examples', 'blue')
        delegate(['tldr', tools.invocation_head(entry.invocation)])
    if 'workflow' in found:
        render_section('workflow — your reference card', 'cyan')
        delegate(['doit', 'workflows', 'show', subject])
    if 'skill' in found:
        render_section('skill — Claude skill', 'green')
        if skill := skills.load_skill(subject):
            skills.render_skill(skill, heading=False)
    for source in ('func', 'alias', 'git', 'forgit', 'zsh', 'tmux'):
        if source in found:
            console.print()
            render_lens_card(found[source])
    return 0


def render_lens_card(entry: index.Entry, brief: bool = False) -> None:
    """The card shape belonging to whichever shell collection this row came from.

    The fallback is the shape for a row that is not a command — a tmux keybinding
    is a key, so there is nothing for a `↳` line to carry.
    """
    if entry.source == 'func':
        tools.render_function(entry.name, entry.description, entry.body, brief=brief)
    elif entry.source == 'alias':
        tools.render_alias(entry.name, entry.command, entry.description, brief=brief)
    elif entry.source == 'git':
        tools.render_git_alias(entry.name, entry.command, brief=brief)
    elif entry.source == 'forgit':
        tools.render_forgit(entry.name, entry.command, brief=brief)
    elif entry.source == 'zsh':
        tools.render_zsh_key(entry.name, entry.command, entry.description, brief=brief)
    else:
        tools.card_title(entry.invocation, entry.source)
        tools.card_detail(entry.description, brief)


def cmd_unresolved(as_json: bool) -> int:
    """List rows naming something this machine cannot run."""
    verdict = index.classify()
    if as_json:
        print(json.dumps(index.unresolved_rows(verdict.dead), indent=2))
        return 0
    if not verdict.dead and not verdict.absent:
        console.print('[green]✓[/] Every indexed row names something runnable.')
        return 0

    if verdict.dead:
        console.rule('[cyan]Rows naming nothing runnable', align='left')
        print_index_rows(verdict.dead, 'yellow')
        console.print(f'\n  {len(verdict.dead)} rows name something not on PATH, nor a function, alias or forgit shortcut.')
        console.print('  No `requires:` accounts for them, so either the entry is stale or it needs one.')

    # A separate section, not a footnote on the one above. These are correct
    # entries for software this box does not have, so counting them alongside
    # rot is what trains you to stop reading the number.
    if verdict.absent:
        console.print()
        console.rule('[cyan]Rows needing something this machine lacks', align='left')
        print_index_rows(verdict.absent, 'cyan', note=lambda entry: f'needs {entry.requires}')
        console.print(f'\n  {len(verdict.absent)} rows are fine and their prerequisite is not installed here.')
    return 0


def print_index_rows(entries: list[index.Entry], style: str, note: Callable[[index.Entry], str] | None = None) -> None:
    width = max(len(entry.name) for entry in entries)
    for entry in entries:
        line = Text('  ')
        line.append(entry.name.ljust(width), style=style)
        line.append(f'  {note(entry) if note else entry.invocation}')
        console.print(line, no_wrap=True, overflow='ellipsis')


kit_app = typer.Typer(name='kit', no_args_is_help=True, help='Everything you own, and what you actually reach for.')

# The reading over the same table `usage` and `unused` count, so it belongs
# beside them rather than at the root. Its own module because it is the one part
# of `kit` that leaves the machine.
kit_app.add_typer(digest.app, name='digest')


TermArgument = Annotated[list[str] | None, typer.Argument(help='What to search for.')]

LensOption = Annotated[
    list[str] | None,
    typer.Option('--source', help=f'Limit to one collection (repeatable): {", ".join(index.LENSES)}'),
]


def lenses(source: list[str] | None) -> list[str] | None:
    """The `--source` list, or a usage error naming what it could have been."""
    unknown = [name for name in source or [] if name not in index.LENSES]
    if unknown:
        raise typer.BadParameter(f'unknown source(s) {", ".join(unknown)}; choose from {", ".join(index.LENSES)}')
    return source


def find_command(term: TermArgument = None, source: LensOption = None) -> None:
    """Search everything you own and open all that is known about the one you pick."""
    raise typer.Exit(cmd_find(' '.join(term or []), lenses(source)))


def choose_command(term: TermArgument = None, source: LensOption = None) -> None:
    """Pick one thing and print what you would type to run it — read, never opened.

    `$(doit choose)` in any shell. `doit shell widgets zsh` emits the zsh half
    that lands the pick on the line editor instead of on stdout.
    """
    raise typer.Exit(cmd_choose(' '.join(term or []), lenses(source)))


def launch_command() -> None:
    """Your areas and every tool you own."""
    raise typer.Exit(cmd_launch())


def show_command(subject: Annotated[str, typer.Argument()]) -> None:
    """Everything known about one subject, from every collection that has it."""
    raise typer.Exit(cmd_show(subject))


def preview_command(source: Annotated[str, typer.Argument()], name: Annotated[str, typer.Argument()]) -> None:
    """Render one row for the fzf preview pane."""
    if source == 'tool':
        tools.render_tool(name, tools.load_registry().get(name) or {})
        raise typer.Exit(0)
    if source == 'workflow':
        raise typer.Exit(0 if delegate(['doit', 'workflows', '__render', name]) else 1)
    if source == 'skill':
        skill = skills.load_skill(name)
        if skill:
            skills.render_skill(skill)
        raise typer.Exit(0 if skill else 1)
    entry = lens_sources(name).get(source)
    if entry:
        render_lens_card(entry)
    raise typer.Exit(0)


@kit_app.command('list')
def index_list_command(
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
) -> None:
    """Every indexed row, as the picker sees it."""
    entries = index.build_index()
    if as_json:
        print(
            json.dumps(
                [{'source': e.source, 'name': e.name, 'invocation': e.invocation, 'description': e.description} for e in entries],
                indent=2,
            )
        )
        raise typer.Exit(0)
    for entry in entries:
        console.print(Text(entry.display()), no_wrap=True, overflow='ellipsis')
    raise typer.Exit(0)


@kit_app.command('unresolved')
def index_unresolved_command(
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
) -> None:
    """Rows naming something this machine cannot run."""
    raise typer.Exit(cmd_unresolved(as_json))


# Caps the handle column so one long usage string cannot indent every other row.
HANDLE_WIDTH = 26

SourceOption = Annotated[
    list[str] | None,
    typer.Option('--source', help=f'Limit to one collection (repeatable): {", ".join(usage.MEASURABLE)}'),
]


def measured(source: list[str] | None) -> list[usage.Row]:
    """Every measurable row, optionally narrowed to one collection.

    Only the collections shell history can see are offered. Naming `tmux` here
    would return an empty list rather than an error, which reads as "you have
    forgotten all of it" instead of "this cannot be measured".
    """
    unknown = [name for name in source or [] if name not in usage.MEASURABLE]
    if unknown:
        raise typer.BadParameter(f'unknown source(s) {", ".join(unknown)}; measurable sources are {", ".join(usage.MEASURABLE)}')
    rows = usage.measure()
    if source:
        rows = [row for row in rows if set(row.sources) & set(source)]
    return rows


def emit_rows(rows: list[usage.Row], today: date) -> None:
    """The rows as JSON on stdout, which is the only thing that goes there."""
    print(
        json.dumps(
            [
                {
                    'typed': row.typed,
                    'sources': list(row.sources),
                    'names': list(row.names),
                    'count': row.count,
                    'last': row.last or None,
                    'days_since': row.days_since(today),
                }
                for row in rows
            ],
            indent=2,
        )
    )


def render_rows(rows: list[usage.Row], today: date) -> None:
    """One line per thing you can type, with the handle first.

    The handle column is capped rather than sized to the longest row: one
    `typescript-language-server --stdio` would otherwise indent every other line
    by fourteen columns of nothing.
    """
    width = min(max(len(row.typed) for row in rows), HANDLE_WIDTH)
    for row in rows:
        line = Text('  ')
        line.append(row.typed.ljust(width), style='cyan' if row.count else 'yellow')
        line.append(f'  {",".join(row.sources):14}')
        line.append(f'{row.count:>5}×  ', style='' if row.count else 'yellow')
        since = row.days_since(today)
        line.append('never' if since is None else f'{row.last}  ({since}d)')
        console.print(line, no_wrap=True, overflow='ellipsis')


def cmd_usage(as_json: bool, source: list[str] | None) -> int:
    """Rank everything you own by how often you reach for it."""
    today = date.today()
    rows = usage.by_frequency(measured(source))
    if as_json:
        emit_rows(rows, today)
        return 0
    if not rows:
        console.print('Nothing measurable in your kit.')
        return 0
    console.rule('[cyan]Kit usage', align='left')
    render_rows(rows, today)
    cold = len(usage.unused(rows, today=today))
    # The trailer is the command that narrows to the rest, never a bare count.
    if cold:
        console.print(f'\n  {len(rows)} rows, {cold} of them unused — see them alone with [cyan]doit kit unused')
    return 0


def cmd_unused(as_json: bool, source: list[str] | None, days: int, minimum: int) -> int:
    """List what you own, can run, and never do."""
    today = date.today()
    rows = usage.by_staleness(usage.unused(measured(source), days=days, minimum=minimum, today=today))
    if as_json:
        emit_rows(rows, today)
        return 0
    if not rows:
        console.print(f'[green]✓[/] Everything in your kit has run in the last {days} days.')
        return 0
    console.rule(f'[cyan]Unused for {days}+ days', align='left')
    render_rows(rows, today)
    console.print(f'\n  {len(rows)} rows you can run and do not.')
    console.print('  Also in [cyan]doit kit unresolved[/]? Then it is stale, not forgotten.')
    return 0


@kit_app.command('usage')
def kit_usage_command(
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
    source: SourceOption = None,
) -> None:
    """How often you reach for each thing you own."""
    raise typer.Exit(cmd_usage(as_json, source))


@kit_app.command('unused')
def kit_unused_command(
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
    source: SourceOption = None,
    days: Annotated[int, typer.Option('--days', help='Days without a run before a row counts as unused.')] = usage.DEFAULT_DAYS,
    minimum: Annotated[int, typer.Option('--min', help='Runs below which a row counts as unused.')] = 1,
) -> None:
    """What you own, can run, and never do."""
    raise typer.Exit(cmd_unused(as_json, source, days, minimum))


def cmd_remind(lens: str, brief: bool, peek: bool) -> int:
    """Surface one forgotten row of a lens, and advance that lens's rotation."""
    if lens not in rotate.LENSES:
        raise typer.BadParameter(f'unknown lens {lens}; rotations are kept for {", ".join(rotate.LENSES)}')
    entry = rotate.next_up(lens)
    if entry is None:
        console.print(f'[green]✓[/] Nothing in the {lens} lens is due to resurface.')
        return 0
    if entry.source == 'tool':
        tools.render_tool(entry.name, tools.load_registry().get(entry.name) or {}, brief=brief)
    else:
        render_lens_card(entry, brief=brief)
    if not peek:
        rotate.record(lens, entry)
    return 0


@kit_app.command('remind')
def kit_remind_command(
    lens: Annotated[str, typer.Option('--lens', help=f'Which collection to draw from: {", ".join(rotate.LENSES)}.')] = 'tool',
    brief: Annotated[bool, typer.Option('--brief', help='A few lines, for a startup nudge.')] = False,
    peek: Annotated[bool, typer.Option('--peek', help='Show it without advancing the rotation.')] = False,
) -> None:
    """Resurface something from one collection you have stopped reaching for.

    Each lens keeps its own rotation, so a drill over the git aliases and a
    reminder over the registry recur at whatever their own register item says
    rather than at a rate the largest collection sets for all of them.
    """
    raise typer.Exit(cmd_remind(lens, brief, peek))
