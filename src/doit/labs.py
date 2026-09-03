"""Hands-on practice Labs, the drill half of doit. Invoked as `doit labs`.

A Lab is a single markdown file under the Labs dir that teaches a tool the way a
senior engineer walks a junior through it: what to do, what to expect, why it
works, alternatives. Any environment setup is a copy-pasteable code block inside
the Lab — you run it yourself in a second pane and work through the steps at your
own keyboard. Nothing is graded; the point is the reps.

Two concerns, kept apart like the review register:
  - the Labs themselves   markdown, hand-authored, only ever read by this tool
  - labs-state.json       the last-practiced date per Lab, written by `done`

Scheduling reuses the review register's model: next_due = last_done + cadence,
derived not stored. A Lab with no `cadence:` is practice-on-demand and never
shows as due.

There is one Labs directory, not a source and an installed copy. It is a git
checkout that `doit content sync` pulls, so authoring writes straight to the file
the reader reads — which is why nothing here resolves a second path or links one
into the other.
"""

import json
import os
import random
import subprocess
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from rich.text import Text

from doit import tools
from doit.cadence import is_due
from doit.cadence import overdue_days
from doit.cadence import parse_cadence
from doit.cadence import status_label
from doit.cards import first_heading
from doit.cards import render_body
from doit.cards import slugify
from doit.cards import split_frontmatter
from doit.paths import library_dir
from doit.paths import xdg_state_home
from doit.render import can_prompt
from doit.render import console
from doit.render import error_console
from doit.state import load_state
from doit.state import save_state

LABS_DIR = Path(os.environ.get('DOIT_LABS_DIR') or library_dir() / 'labs')
STATE = Path(os.environ.get('DOIT_LABS_STATE') or xdg_state_home() / 'doit' / 'labs-state.json')

# A flashcard session stays short — the research says a few minutes / <=5 new/day
# beats long cramming — so a session samples at most this many cards.
FLASH_SESSION = 10

TEMPLATE = """\
---
tags: []
cadence: 1mo
---

# {title}

> One line: what this Lab drills and when you'd reach for it.

## Setup

Copy this into your other pane to stage a scratch dir to play in:

```bash
LAB=$(mktemp -d) && cd "$LAB"
# ... create fixture files here ...
```

## Steps

1. **Do the thing.** `some command`
   - Expect: what you should see.
   - Why: the reason it works.
   - Alternative: another way to reach the same result.
"""


def load_labs() -> dict:
    """{lab_id: {title, tags, cadence, path}} for every Lab markdown file."""
    deck: dict[str, dict] = {}
    if not LABS_DIR.exists():
        return deck
    for path in sorted(LABS_DIR.glob('*.md')):
        meta, body = split_frontmatter(path.read_text())
        lab_id = path.stem
        deck[lab_id] = {
            'title': first_heading(body) or lab_id,
            'tags': meta.get('tags') or [],
            'cadence': str(meta.get('cadence') or ''),
            'path': path,
        }
    return deck


def statuses() -> list[dict]:
    """Every Lab with its derived schedule state, most urgent first.

    Scheduled Labs (those with a cadence) sort ahead of on-demand ones; within
    the scheduled group, never-practiced sorts first, then most overdue.
    """
    labs = load_labs()
    state = load_state(STATE)
    today = date.today()
    rows = []
    for lab_id, meta in labs.items():
        cadence = meta['cadence']
        last = state.get(lab_id)
        # No cadence → practice-on-demand: never scheduled, so never "due".
        overdue = overdue_days(last, cadence, today) if cadence else None
        rows.append(
            {
                'id': lab_id,
                'title': meta['title'],
                'tags': meta['tags'],
                'cadence': cadence,
                'cadence_days': parse_cadence(cadence) if cadence else 0,
                'last': last,
                'overdue': overdue,
                'scheduled': bool(cadence),
            }
        )
    rows.sort(key=lambda r: (0 if r['scheduled'] else 1, -(float('inf') if r['overdue'] is None else r['overdue'])))
    return rows


def is_due_row(row: dict) -> bool:
    """A row is due only if it is scheduled and its cadence has elapsed."""
    return row['scheduled'] and is_due(row['overdue'])


def render_row(row: dict) -> None:
    if row['scheduled']:
        meta = f'every {row["cadence"]}'
        if row['last']:
            meta += f' · last {row["last"]}'
        status = status_label(row['overdue'])
    else:
        meta = 'on demand'
        status = '—'
    line = Text('  ')
    line.append(f'{row["id"]:<24}', style='yellow')
    line.append(f'  {status}  ·  {meta}')
    console.print(line)
    console.print(Text(f'      {row["title"]}'))
    if row['tags']:
        tags = Text('      ')
        tags.append(f'#{" #".join(row["tags"])}', style='cyan')
        console.print(tags)
    console.print()


def cmd_show(lab_id: str) -> int:
    labs = load_labs()
    if lab_id not in labs:
        return unknown_lab(lab_id)
    render_body(labs[lab_id]['path'], for_preview=False)
    console.print()
    hint = Text()
    hint.append('Work through it in your other pane, then:', style='cyan')
    hint.append(f'  doit labs done {lab_id}')
    console.print(hint)
    return 0


def cmd_render_preview(lab_id: str) -> int:
    labs = load_labs()
    if lab_id not in labs:
        return 1
    render_body(labs[lab_id]['path'], for_preview=True)
    return 0


def bounded(rows: list[dict], limit: int | None) -> list[dict]:
    """The first `limit` rows, or all of them when no limit was asked for."""
    return rows[:limit] if limit else rows


def more_line(shown: int, total: int, browse: str) -> None:
    """Say what the limit held back, and name the command that shows it."""
    if shown < total:
        console.print(f'  +{total - shown} more  ·  [cyan]{browse}[/]')


def cmd_due(limit: int | None = None) -> int:
    """What's due to practice, bounded to the first `limit` rows when given.

    The bound is what lets this same view serve a review item's `show:`. That
    slot runs at shell startup, where an unbounded deck is a catalog rather
    than a prompt, and the deck comes due in clumps.
    """
    if not load_labs():
        return warn_no_labs()
    console.rule('[cyan]Labs — due to practice', align='left')
    due = [r for r in statuses() if is_due_row(r)]
    if not due:
        console.print('[green]✓[/] No Labs due. Browse them all with [cyan]doit labs list[/].')
        return 0
    shown = bounded(due, limit)
    for row in shown:
        render_row(row)
    more_line(len(shown), len(due), 'doit labs due')
    console.print('Practice one:  [cyan]doit labs show <id>[/]   ·   mark done:  [cyan]doit labs done <id>[/]')
    return 0


def cmd_list(as_json: bool = False, limit: int | None = None) -> int:
    # --json emits the whole deck with each Lab's derived status, so a consumer
    # (the dashboard) filters on `overdue`/`scheduled` itself. An empty deck is
    # an empty list, not the prose hint: --json must always be parsable.
    if as_json:
        # Plain print, never the rich console: a Console soft-wraps at terminal
        # width, which would put newlines inside JSON strings and hand a consumer
        # a parse error instead of data.
        print(json.dumps(bounded(statuses(), limit), indent=2))
        return 0
    if not load_labs():
        return warn_no_labs()
    console.rule('[cyan]Labs — full deck', align='left')
    deck = statuses()
    shown = bounded(deck, limit)
    for row in shown:
        render_row(row)
    more_line(len(shown), len(deck), 'doit labs list')
    console.print('Practice one:  [cyan]doit labs show <id>[/]   ·   choose interactively:  [cyan]doit labs choose[/]')
    return 0


def cmd_done(lab_id: str) -> int:
    labs = load_labs()
    if lab_id not in labs:
        return unknown_lab(lab_id)
    today = date.today().isoformat()
    state = load_state(STATE)
    state[lab_id] = today
    save_state(STATE, state)
    cadence = labs[lab_id]['cadence']
    done = Text.from_markup('[green]✓[/] Marked ')
    done.append(lab_id, style='yellow')
    due_again = f' Due again in {parse_cadence(cadence)}d.' if cadence else ''
    done.append(f' practiced ({today}).{due_again}')
    console.print(done)
    return 0


def cmd_pick() -> int:
    labs = load_labs()
    if not labs:
        return warn_no_labs()
    rows = '\n'.join(f'{lab_id}\t{meta["title"]}' for lab_id, meta in sorted(labs.items()))
    try:
        result = subprocess.run(
            [
                'fzf',
                '--delimiter=\t',
                '--with-nth=2',
                '--prompt=lab ❯ ',
                '--preview',
                'doit labs __render {1}',
                '--preview-window=right:62%',
            ],
            input=rows,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        error_console.print('fzf not found. Use [cyan]doit labs list[/] then [cyan]doit labs show <id>[/].')
        return 1
    selected = result.stdout.strip()
    if not selected:
        return 0
    return cmd_show(selected.split('\t', 1)[0])


def cmd_new(name: str) -> int:
    slug = slugify(name)
    if not slug:
        error_console.print('A Lab needs a name with at least one letter or digit.')
        return 2
    target = LABS_DIR / f'{slug}.md'
    if target.exists():
        error_console.print(Text(f'Lab already exists: {target}'))
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(TEMPLATE.format(title=name))
    console.print(Text.from_markup('[green]✓[/] Created ') + Text(str(target)))
    open_editor(target)
    return 0


def cmd_edit(lab_id: str) -> int:
    path = LABS_DIR / f'{lab_id}.md'
    if not path.exists():
        return unknown_lab(lab_id)
    open_editor(path)
    return 0


def open_editor(path: Path) -> None:
    subprocess.run([os.environ.get('EDITOR', 'nvim'), str(path)], check=False)


def unknown_lab(lab_id: str) -> int:
    error_console.print(Text(f'No Lab {lab_id!r}.'))
    hint = Text('See what is available with ')
    hint.append('doit labs list', style='cyan')
    hint.append(', or create it with ')
    hint.append(f'doit labs new {lab_id}', style='cyan')
    error_console.print(hint)
    return 1


def warn_no_labs() -> int:
    console.print(Text(f'No Labs yet in {LABS_DIR}.'))
    console.print('Create your first with [cyan]doit labs new <name>[/].')
    return 0


def load_flashcards(subject: str | None = None) -> list[dict]:
    """Recall cards from the registry: prompt = an example's description, answer =
    its command. Zero authoring — every tool's examples become a deck.

    A subject filters to one tool by name or by tag membership.

    The registry backs this deck (recall the command from an example's
    description). `doit.tools` owns it; this only reads it.
    """
    cards = []
    for name, tool in tools.load_registry().items():
        tool = tool or {}
        if subject and name != subject and subject not in (tool.get('tags') or []):
            continue
        for example in tool.get('examples') or []:
            example = example or {}
            cmd = example.get('cmd')
            desc = example.get('desc')
            if cmd and desc:
                cards.append({'tool': name, 'prompt': desc, 'answer': cmd})
    return cards


def cmd_flash(subject: str | None = None) -> int:
    """A quick recall pass: show a description, you recall the command, reveal,
    self-mark. Ephemeral (no schedule) — the daily warm-up next to the Labs."""
    cards = load_flashcards(subject)
    if not cards:
        message = f'No examples to drill for {subject!r}.' if subject else f'No registry examples found at {tools.REGISTRY}.'
        console.print(Text(message))
        return 0
    if not can_prompt():
        error_console.print('flash is answered card by card, and this run has nobody to answer it.')
        return 1
    random.shuffle(cards)
    cards = cards[:FLASH_SESSION]
    console.rule('[cyan]Flashcards — recall the command', align='left')
    correct = 0
    answered = 0
    for i, card in enumerate(cards, 1):
        # Text, not markup: a tool name sits inside square brackets here, which
        # rich would otherwise read as a style tag and swallow.
        counter = Text('  ')
        counter.append(f'{i}/{len(cards)}', style='yellow')
        counter.append(f'  [{card["tool"]}]')
        console.print(counter)
        console.print(Text(f'  {card["prompt"]}'))
        try:
            if console.input('  [cyan]Enter to reveal · q to quit ❯ [/]').strip().lower() == 'q':
                break
            answer = Text('  ')
            answer.append(f'$ {card["answer"]}', style='green')
            console.print(answer)
            got = console.input(r'  got it? \[y/N] ❯ ').strip().lower()
        except EOFError:
            break
        answered += 1
        if got == 'y':
            correct += 1
        console.print()
    if answered:
        console.print(f'[green]✓[/] Recalled {correct}/{answered}.')
    return 0


app = typer.Typer(name='labs', no_args_is_help=True, help='Hands-on practice Labs.')


LimitOption = Annotated[int | None, typer.Option('--limit', '-n', help='Show at most this many, then say how many are left.')]


@app.command('due')
def due_command(limit: LimitOption = None) -> None:
    """What's due to practice now."""
    raise typer.Exit(cmd_due(limit))


@app.command('list')
def list_command(
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
    limit: LimitOption = None,
) -> None:
    """Every Lab and its schedule status."""
    raise typer.Exit(cmd_list(as_json, limit))


@app.command('show')
def show_command(lab_id: Annotated[str, typer.Argument(help='The Lab to open.')]) -> None:
    """Open a Lab to read while you work in another pane."""
    raise typer.Exit(cmd_show(lab_id))


@app.command('choose')
def choose_command() -> None:
    """Choose a Lab interactively (fzf)."""
    raise typer.Exit(cmd_pick())


@app.command('flash')
def flash_command(
    subject: Annotated[str | None, typer.Argument(help='One tool or tag; omit to drill everything.')] = None,
) -> None:
    """Quick recall drill from tool examples."""
    raise typer.Exit(cmd_flash(subject))


@app.command('done')
def done_command(lab_id: Annotated[str, typer.Argument(help='The Lab to mark practiced.')]) -> None:
    """Mark a Lab practiced, advancing its schedule."""
    raise typer.Exit(cmd_done(lab_id))


@app.command('new')
def new_command(name: Annotated[list[str], typer.Argument(help='The new Lab name.')]) -> None:
    """Scaffold a new Lab and open it in $EDITOR."""
    raise typer.Exit(cmd_new(' '.join(name)))


@app.command('edit')
def edit_command(lab_id: Annotated[str, typer.Argument(help='The Lab to edit.')]) -> None:
    """Edit an existing Lab in $EDITOR."""
    raise typer.Exit(cmd_edit(lab_id))


@app.command('__render', hidden=True)
def render_command(lab_id: Annotated[str, typer.Argument()]) -> None:
    """Render one Lab body, for the fzf preview pane in `pick`."""
    raise typer.Exit(cmd_render_preview(lab_id))
