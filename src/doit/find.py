"""Search the federated index, then hand you off to whoever owns the answer.

`doit find` is one motion: fuzzy-match a line, press Enter, get everything known
about that subject assembled from every collection that has it. `doit launch`
is the other direction — your areas and your own tools, for when the question is
"what can I even run here" rather than "where is the thing I already have in
mind".

fzf is shelled out to rather than reimplemented. It is already the picker
everywhere else on this machine, and a Python reimplementation would be a worse
one that also had to be maintained.

Nothing here renders a subject itself. Each lens delegates to the tool that owns
it — `toolbox show`, `doit workflows show`, `bat` over a skill file — because a
second renderer is a second thing to keep in step with the first.
"""

import json
import shutil
import subprocess
from typing import Annotated

import typer
from rich.text import Text

from doit import index
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


def run_fzf(lines: list[str], query: str, preview: str, header: str) -> str:
    """Pick one line with fzf, or '' if nothing was chosen.

    A missing fzf is reported rather than raised: the index is still usable
    through `--json`, and a stack trace would say less than one sentence does.
    """
    if not shutil.which('fzf'):
        error_console.print('fzf is not installed on this machine.')
        return ''
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
    """`display <TAB> source <TAB> name` — field one is shown and searched."""
    return [f'{entry.display()}\t{entry.source}\t{entry.name}' for entry in entries]


def cmd_find(term: str, sources: list[str] | None) -> int:
    entries = index.build_index(sources)
    if not entries:
        error_console.print('Nothing indexed. Is the toolbox registry installed?')
        return 1
    selected = run_fzf(
        index_lines(entries),
        term,
        'doit __preview {2} {3}',
        'Enter opens everything known about it',
    )
    if not selected:
        return 0
    return cmd_show(selected.split('\t')[2])


def cmd_launch() -> int:
    """Areas first, then your own tools — the "what can I run here" question.

    Your own tools, not all 130: a launcher answering "what can I run here" with
    every third-party binary on the machine answers a different question.
    """
    own = [entry for entry in index.index_tools() if entry.category == 'custom-tools']
    rows = [f'{name:<18} {description}\t{name}' for name, description in AREAS]
    rows += [f'{entry.name:<18} {entry.description}\t{entry.name}' for entry in sorted(own, key=lambda e: e.name)]
    selected = run_fzf(rows, '', 'doit __preview tool {2}', 'Your areas and tools · Enter shows it')
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
        render_section(f'toolbox — {entry.invocation}', 'yellow')
        delegate(['toolbox', 'show', subject])
        render_section('tldr — common examples', 'blue')
        delegate(['tldr', index.invocation_head(entry.invocation)])
    if 'workflow' in found:
        render_section('workflow — your reference card', 'cyan')
        delegate(['doit', 'workflows', 'show', subject])
    if 'skill' in found:
        render_section('skill — Claude skill', 'green')
        delegate(['bat', '--style=plain', '--color=always', '--language=markdown', str(index.SKILLS_DIR / subject / 'SKILL.md')])
    for source in ('func', 'alias', 'git', 'forgit', 'tmux'):
        if source in found:
            entry = found[source]
            render_section(f'{source} — {entry.invocation}', 'cyan')
            console.print(Text(f'  {entry.description}'))
    return 0


def cmd_unresolved(as_json: bool) -> int:
    """List rows naming something this machine cannot run."""
    dead = index.unresolved()
    if as_json:
        print(json.dumps([{'source': e.source, 'name': e.name, 'invocation': e.invocation} for e in dead], indent=2))
        return 0
    if not dead:
        console.print('[green]✓[/] Every indexed row names something runnable.')
        return 0
    console.rule('[cyan]Unresolved index rows', align='left')
    width = max(len(entry.name) for entry in dead)
    for entry in dead:
        line = Text('  ')
        line.append(entry.name.ljust(width), style='yellow')
        line.append(f'  {entry.invocation}')
        console.print(line, no_wrap=True, overflow='ellipsis')
    console.print(f'\n  {len(dead)} rows name something not on PATH, nor a function, alias or forgit shortcut.')
    console.print('  Either the entry is stale, or its `usage` names a runtime shell integration.')
    return 0


index_app = typer.Typer(name='index', no_args_is_help=True, help='The federated index itself.')


def find_command(
    term: Annotated[list[str] | None, typer.Argument(help='What to search for.')] = None,
    source: Annotated[
        list[str] | None,
        typer.Option('--source', help=f'Limit to one collection (repeatable): {", ".join(index.LENSES)}'),
    ] = None,
) -> None:
    """Search across every collection you own."""
    unknown = [name for name in source or [] if name not in index.LENSES]
    if unknown:
        raise typer.BadParameter(f'unknown source(s) {", ".join(unknown)}; choose from {", ".join(index.LENSES)}')
    raise typer.Exit(cmd_find(' '.join(term or []), source))


def launch_command() -> None:
    """Your areas and every tool you own."""
    raise typer.Exit(cmd_launch())


def show_command(subject: Annotated[str, typer.Argument()]) -> None:
    """Everything known about one subject, from every collection that has it."""
    raise typer.Exit(cmd_show(subject))


def preview_command(source: Annotated[str, typer.Argument()], name: Annotated[str, typer.Argument()]) -> None:
    """Render one row for the fzf preview pane."""
    if source == 'tool':
        raise typer.Exit(0 if delegate(['toolbox', 'show', name]) else 1)
    if source == 'workflow':
        raise typer.Exit(0 if delegate(['doit', 'workflows', '__render', name]) else 1)
    if source == 'skill':
        path = index.SKILLS_DIR / name / 'SKILL.md'
        args = ['bat', '--style=plain', '--color=always', '--language=markdown', str(path)]
        raise typer.Exit(0 if delegate(args) else 1)
    entry = lens_sources(name).get(source)
    if entry:
        console.print(Text(f'{entry.invocation}\n\n{entry.description}'))
    raise typer.Exit(0)


@index_app.command('list')
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


@index_app.command('unresolved')
def index_unresolved_command(
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
) -> None:
    """Rows naming something this machine cannot run."""
    raise typer.Exit(cmd_unresolved(as_json))
