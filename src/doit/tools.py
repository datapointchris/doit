"""The tool registry: what each thing you own is, and what to type.

Invoked as `doit tools`. The third collection in the terminal library, beside
`workflows/` and `labs/`, and the one that answers "what was that command
again" rather than "how do I do this task".

This module owns the registry — its path, its loading, and every card shape
drawn from it — because three other modules read it and a constant defined in
three places is three places a move has to be chased.

The card shapes take plain arguments rather than index rows. A function, an
alias, a git alias and a forgit shortcut are not in the registry at all; they
are parsed out of shell files by the index. Keeping the renderers ignorant of
where their arguments came from is what lets `doit show` compose them and the
reminder rotation reuse them without this module importing either.

Every card comes in two densities. The full one is a browse view and carries
every field. The brief one is an interrupt — a startup nudge, a preview pane —
and carries identity, what the thing is, and one command, clipped rather than
wrapped, because a wrapped row is two rows.
"""

import json
import os
import shutil
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.padding import Padding
from rich.text import Text

from doit.paths import library_dir
from doit.render import console
from doit.render import error_console

REGISTRY = Path(os.environ.get('DOIT_TOOLS_REGISTRY') or library_dir() / 'tools' / 'registry.yml')

# Shell keywords that can lead a `usage` string without being the command.
LEADING_KEYWORDS = {'source', '.', 'eval', 'exec'}

# Caps each line of a brief card. A fixed width rather than the real terminal
# size: a brief card's whole job is to fit inside someone else's output — a
# startup nudge, an fzf preview pane — and the terminal it is measured against
# is not the space it is given. Narrow enough for any usable pane.
BRIEF_WIDTH = 72

# What a brief tool card is allowed: the identity line, the description, and one
# example. More than that and it is the detail view with fields missing.
BRIEF_EXAMPLES = 1


def load_registry() -> dict:
    """`{name: {category, description, usage, examples, ...}}`, or empty.

    A missing registry is not an error here. The draw, the dashboard and the
    review register need no tools, and a machine that has not synced content yet
    should still run everything else.
    """
    if not REGISTRY.exists():
        return {}
    return (yaml.safe_load(REGISTRY.read_text()) or {}).get('tools') or {}


def invocation_head(invocation: str) -> str:
    """The word a `usage` string actually asks you to run."""
    tokens = invocation.split()
    if not tokens:
        return ''
    if tokens[0] in LEADING_KEYWORDS and len(tokens) > 1:
        return tokens[1]
    return tokens[0]


def clip_brief(text: str, indent: int) -> str:
    """`text` shortened to fit BRIEF_WIDTH alongside `indent` columns of prefix.

    Without this a long example wraps, and the card silently costs the extra
    line that brief exists to save.
    """
    room = BRIEF_WIDTH - indent
    if len(text) <= room or room < 2:
        return text
    return text[: room - 1] + '…'


def card_title(name: str, kind: str) -> None:
    """The identity line every card shape opens with, whatever it is drawing."""
    line = Text('  ')
    line.append(name, style='cyan')
    line.append('  ')
    line.append(f'({kind})', style='green')
    console.print(line, no_wrap=True, overflow='ellipsis')


def card_detail(text: str, brief: bool, indent: int = 2) -> None:
    if not text:
        return
    console.print(Text(f'  {clip_brief(text, indent) if brief else text}'))


def card_arrow(text: str, brief: bool) -> None:
    """The `↳ <command>` line: what to type, on the row under what it is."""
    line = Text('  ')
    line.append('↳ ', style='yellow')
    line.append(clip_brief(text, 4) if brief else text)
    console.print(line, no_wrap=True, overflow='ellipsis')


def field(label: str, value: str) -> None:
    """A short value on the label's own line: `Category: file-viewer`."""
    line = Text()
    line.append(f'{label}: ', style='yellow')
    line.append(value)
    console.print(line)


def block(label: str, value: str) -> None:
    """A prose value under its label, indented.

    Inline would put a wrapped sentence under a column of its own label, which
    is where a detail card starts reading as a paragraph rather than a form.

    Padding rather than a literal two spaces, so the second line of a wrapped
    sentence keeps the indent instead of starting back at column zero.
    """
    console.print(Text(f'{label}:', style='yellow'))
    console.print(Padding(Text(value), (0, 0, 0, 2)))
    console.print()


def render_tool(name: str, meta: dict, brief: bool = False, heading: bool = True) -> None:
    """The registry detail card, or three lines of it.

    Brief drops every field a browse view earns and keeps identity, what it is,
    and one thing to type — the same trade the dashboard nudge makes.

    `heading` is off when a caller has already named the subject, so a composite
    view does not rule the same name twice.
    """
    meta = meta or {}
    usage = (meta.get('usage') or name).strip()
    examples = meta.get('examples') or []

    if brief:
        card_title(name, 'tool')
        card_detail(meta.get('description') or '', brief)
        for example in examples[:BRIEF_EXAMPLES]:
            card_arrow((example or {}).get('cmd') or '', brief)
        return

    if heading:
        console.rule(f'[cyan]{name}', align='left')
        console.print()
    if description := meta.get('description'):
        block('Description', description)
    if why_use := meta.get('why_use'):
        block('Why use', why_use)
    if category := meta.get('category'):
        field('Category', category)
    if installed_via := meta.get('installed_via'):
        field('Installed via', installed_via)
    console.print()
    block('Usage', usage)

    if examples:
        console.print(Text('Examples:', style='yellow'))
        for example in examples:
            example = example or {}
            if not example.get('cmd'):
                continue
            line = Text('  $ ')
            line.append(example['cmd'], style='cyan')
            console.print(line, no_wrap=True, overflow='ellipsis')
            if example.get('desc'):
                console.print(Text(f'    {example["desc"]}'))

    console.print()
    if notes := meta.get('notes'):
        block('Notes', notes)
    if see_also := meta.get('see_also'):
        field('See also', ', '.join(see_also))
    if tags := meta.get('tags'):
        field('Tags', ', '.join(tags))
    if docs_url := meta.get('docs_url'):
        field('Docs', docs_url)

    # The `usage` string, not the registry key: a key is a package name as often
    # as a command, so checking it reports `ripgrep` and `git-delta` as missing
    # while `rg` and `delta` sit on PATH.
    #
    # A miss is stated without a verdict attached. Shell functions and aliases
    # are never on PATH and are perfectly runnable, and they outnumber genuine
    # rot two to one here — so a card calling every miss a stale entry would be
    # wrong most of the times it spoke. `doit kit unresolved` separates them,
    # against the shell files rather than a guess, and is the one report that
    # should say it.
    head = invocation_head(usage)
    if head and shutil.which(head):
        console.print(Text.from_markup('[green]✓[/] ') + Text(f'{head} is on PATH'))
    else:
        console.print(Text.from_markup('[yellow]⚠[/] ') + Text(f'{head or name} is not on PATH — functions and aliases never are'))
        console.print(Text('  Sort the stale entries from those with doit kit unresolved', style='cyan'), no_wrap=True)


def render_function(name: str, description: str, body: str = '', brief: bool = False) -> None:
    """A shell function: what it does, then the function itself.

    The body is the refresher — it is how you remember what the thing actually
    does — and it is however long the function is, which is exactly what brief
    drops rather than clips.
    """
    card_title(name, 'shell function')
    card_detail(description, brief)
    if body and not brief:
        console.print()
        for line in body.splitlines():
            console.print(Text(f'    {line}'), no_wrap=True, overflow='ellipsis')


def render_alias(name: str, command: str, description: str = '', brief: bool = False) -> None:
    card_title(name, 'alias')
    card_detail(description, brief)
    card_arrow(command, brief)


def render_git_alias(name: str, command: str, brief: bool = False) -> None:
    card_title(f'git {name}', 'git alias')
    card_arrow(f'git {command}', brief)


def render_forgit(name: str, action: str, brief: bool = False) -> None:
    card_arrow_text = f'interactive git {action}, via fzf'
    card_title(name, 'forgit')
    card_arrow(card_arrow_text, brief)


def cmd_show(name: str, brief: bool) -> int:
    registry = load_registry()
    if name not in registry:
        return unknown_tool(name)
    render_tool(name, registry[name], brief=brief)
    return 0


def unknown_tool(name: str) -> int:
    """Point at the two commands that search wider, rather than just refusing.

    A name absent from the registry is usually a function or an alias, which
    `doit show` renders and `doit find` searches — so the miss is nearly always
    one command away from a hit.
    """
    error_console.print(Text(f'No registry entry for {name!r}.'))
    hint = Text('Every collection: ')
    hint.append(f'doit show {name}', style='cyan')
    hint.append(', or search with ')
    hint.append(f'doit find {name}', style='cyan')
    error_console.print(hint)
    return 1


def category_of(meta: dict | None) -> str:
    """The category a row is grouped under, named for the ones that authored none."""
    return (meta or {}).get('category') or 'uncategorised'


def category_names(registry: dict) -> list[str]:
    """Every category present, in display order."""
    return sorted({category_of(meta) for meta in registry.values()})


def check_category(category: str | None, registry: dict) -> None:
    """Reject an unknown `--category` as a usage error, before anything renders.

    Ahead of the `--json` branch on purpose: a machine caller filtering on a
    category that does not exist was being handed `[]` and exit 0, which reads
    as "the category is empty" rather than "you named the wrong one".

    Silent on an empty registry, so a machine that has not synced content yet
    gets the `doit content sync` explanation rather than a usage error about a
    category that would have been valid anywhere else.
    """
    if category and registry and category not in category_names(registry):
        raise typer.BadParameter(
            f'unknown category {category!r}; choose from {", ".join(category_names(registry))}. '
            'Counts per category: doit tools categories list'
        )


def cmd_list(category: str | None, as_json: bool) -> int:
    registry = load_registry()
    check_category(category, registry)
    if as_json:
        rows = [
            {'name': name, 'category': (meta or {}).get('category') or '', 'description': (meta or {}).get('description') or ''}
            for name, meta in sorted(registry.items())
            if not category or category_of(meta) == category
        ]
        print(json.dumps(rows, indent=2))
        return 0
    if not registry:
        console.print(Text(f'No registry at {REGISTRY}.'))
        console.print('Fetch the library with [cyan]doit content sync[/].')
        return 0

    grouped: dict[str, list[tuple[str, str]]] = {}
    for name, meta in sorted(registry.items()):
        meta = meta or {}
        grouped.setdefault(category_of(meta), []).append((name, meta.get('description') or ''))
    if category:
        grouped = {category: grouped[category]}

    console.rule('[cyan]Tools', align='left')
    width = max(len(name) for rows in grouped.values() for name, _ in rows)
    for group in sorted(grouped):
        console.print()
        console.print(Text(group, style='yellow'))
        for name, description in grouped[group]:
            line = Text('  ')
            line.append(name.ljust(width), style='green')
            line.append(f'  {description}')
            console.print(line, no_wrap=True, overflow='ellipsis')
    shown = sum(len(rows) for rows in grouped.values())
    console.print(f'\n  {shown} tools · [cyan]doit tools show <name>[/]')
    return 0


def category_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for meta in load_registry().values():
        name = category_of(meta)
        counts[name] = counts.get(name, 0) + 1
    return counts


def cmd_categories_list(as_json: bool) -> int:
    counts = category_counts()
    if as_json:
        print(json.dumps([{'category': name, 'tools': counts[name]} for name in sorted(counts)], indent=2))
        return 0
    if not counts:
        console.print(Text(f'No registry at {REGISTRY}.'))
        console.print('Fetch the library with [cyan]doit content sync[/].')
        return 0
    console.rule('[cyan]Tool categories', align='left')
    width = max(len(name) for name in counts)
    for name in sorted(counts):
        line = Text('  ')
        line.append(name.ljust(width), style='green')
        line.append(f'  {counts[name]}')
        console.print(line)
    console.print('\n  [cyan]doit tools list --category <name>[/]')
    return 0


app = typer.Typer(name='tools', no_args_is_help=True, help='The tool registry.')


@app.command('list')
def list_command(
    category: Annotated[str | None, typer.Option('--category', help='Only this category.')] = None,
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
) -> None:
    """Every tool you own, grouped by category."""
    raise typer.Exit(cmd_list(category, as_json))


@app.command('show')
def show_command(
    name: Annotated[str, typer.Argument(help='The registry entry to render.')],
    brief: Annotated[bool, typer.Option('--brief', help='Three lines instead of the full card.')] = False,
) -> None:
    """What one tool is, why to reach for it, and what to type."""
    raise typer.Exit(cmd_show(name, brief))


# A namespace rather than a bare `categories` that lists: every node in the tree
# prints help when given no arguments, so walking down one token at a time never
# runs something you did not ask for. That the set is read-only and will never
# grow `create` does not buy it an exemption — predictability is the point.
categories_app = typer.Typer(name='categories', no_args_is_help=True, help='The categories tools are grouped under.')
app.add_typer(categories_app, name='categories')


@categories_app.command('list')
def categories_list_command(
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
) -> None:
    """Every category and how many tools it holds."""
    raise typer.Exit(cmd_categories_list(as_json))
