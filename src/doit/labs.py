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
checkout that `doit update` pulls, so authoring writes straight to the file the
reader reads — which is why nothing here resolves a second path or links one
into the other.
"""

import json
import os
import random
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml
from pytermstyle import CYAN
from pytermstyle import GREEN
from pytermstyle import RESET
from pytermstyle import YELLOW
from pytermstyle import clip
from pytermstyle import header
from pytermstyle import help_end
from pytermstyle import help_header
from pytermstyle import help_row
from pytermstyle import help_section
from pytermstyle import help_usage

from doit.cadence import is_due
from doit.cadence import overdue_days
from doit.cadence import parse_cadence
from doit.cadence import status_label
from doit.paths import xdg_data_home
from doit.paths import xdg_state_home
from doit.state import load_state
from doit.state import save_state

LABS_DIR = Path(os.environ.get('DOIT_LABS_DIR') or xdg_data_home() / 'doit' / 'labs')
STATE = Path(os.environ.get('DOIT_LABS_STATE') or xdg_state_home() / 'doit' / 'labs-state.json')

# The tool registry backs the zero-authoring flashcard deck (recall the command
# from an example's description). Toolbox owns this file; doit only reads it.
REGISTRY = Path(os.environ.get('TOOLBOX_REGISTRY') or xdg_data_home() / 'toolbox' / 'registry.yml')

# A flashcard session stays short — the research says a few minutes / <=5 new/day
# beats long cramming — so a session samples at most this many cards.
FLASH_SESSION = 10

# How many Lab ids the nudge names before it stops. The deck comes due in clumps,
# and a startup nudge asking you to pick one of fifteen is a catalogue, not a
# prompt — three is enough to make the choice concrete.
NUDGE_SAMPLE = 3

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


def split_frontmatter(text: str) -> tuple[dict, str]:
    """(frontmatter dict, body) for a Lab file's text.

    The frontmatter carries only `tags` and an optional `cadence`; the Lab's
    title is its first `# ` heading, not a frontmatter field — same convention as
    the workflow cards, and it keeps markdownlint happy (no duplicate H1).
    """
    if not text.startswith('---'):
        return {}, text
    parts = text.split('---', 2)
    if len(parts) < 3:
        return {}, text
    return (yaml.safe_load(parts[1]) or {}), parts[2].lstrip('\n')


def parse_frontmatter(path: Path) -> dict:
    """The YAML frontmatter of a Lab file as a dict (empty if there is none)."""
    return split_frontmatter(path.read_text())[0]


def strip_frontmatter(text: str) -> str:
    """The Lab body with its leading YAML frontmatter block removed."""
    return split_frontmatter(text)[1]


def first_heading(body: str) -> str:
    """The first level-1 markdown heading (`# ...`) in a Lab body, or ''."""
    for line in body.splitlines():
        if line.startswith('# '):
            return line[2:].strip()
    return ''


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
    print(f'  {YELLOW}{row["id"]:<24}{RESET}  {status}  ·  {meta}')
    print(f'      {row["title"]}')
    if row['tags']:
        print(f'      {CYAN}#{" #".join(row["tags"])}{RESET}')
    print()


def render_body(path: Path, *, for_preview: bool) -> None:
    """Render a Lab's markdown body (frontmatter stripped) through bat."""
    body = strip_frontmatter(path.read_text()).lstrip('\n')
    args = ['bat', '--style=plain', '--language=markdown', '--color=always']
    if for_preview:
        args.append('--paging=never')
    try:
        subprocess.run(args, input=body, text=True, check=False)
    except FileNotFoundError:
        # bat absent: fall back to raw text so the Lab is still readable.
        print(body)


def cmd_show(lab_id: str) -> int:
    labs = load_labs()
    if lab_id not in labs:
        return unknown_lab(lab_id)
    render_body(labs[lab_id]['path'], for_preview=False)
    print(f'\n{CYAN}Work through it in your other pane, then:{RESET}  doit labs done {lab_id}')
    return 0


def cmd_render_preview(lab_id: str) -> int:
    labs = load_labs()
    if lab_id not in labs:
        return 1
    render_body(labs[lab_id]['path'], for_preview=True)
    return 0


def cmd_due() -> int:
    if not load_labs():
        return warn_no_labs()
    header('Labs — due to practice')
    due = [r for r in statuses() if is_due_row(r)]
    if due:
        for row in due:
            render_row(row)
        print(f'Practice one:  {CYAN}doit labs show <id>{RESET}   ·   mark done:  {CYAN}doit labs done <id>{RESET}')
        return 0
    print(f'{GREEN}✓{RESET} No Labs due. Browse them all with {CYAN}doit labs list{RESET}.')
    return 0


def cmd_list(as_json: bool = False) -> int:
    # --json emits the whole deck with each Lab's derived status, so a consumer
    # (the dashboard) filters on `overdue`/`scheduled` itself. An empty deck is
    # an empty list, not the prose hint: --json must always be parsable.
    if as_json:
        print(json.dumps(statuses(), indent=2))
        return 0
    if not load_labs():
        return warn_no_labs()
    header('Labs — full deck')
    for row in statuses():
        render_row(row)
    print(f'Practice one:  {CYAN}doit labs show <id>{RESET}   ·   pick interactively:  {CYAN}doit labs pick{RESET}')
    return 0


def cmd_nudge() -> int:
    """Startup nudge: one line naming a few due Labs, silent when caught up.

    Runs inside the review nudge (the `practice-a-lab` item's `show:`), so it has
    to hold to a line or two however much of the deck is due. `doit labs list` is
    the browse view for the rest — this only has to get you to open it.
    """
    if not load_labs():
        return 0
    due = [r for r in statuses() if is_due_row(r)]
    if not due:
        return 0
    sample = ', '.join(row['id'] for row in due[:NUDGE_SAMPLE])
    if len(due) > NUDGE_SAMPLE:
        sample += ', …'
    label = f'Labs · {len(due)} due'
    tail = '   ↳ doit labs show <id>'
    sample = clip(sample, len(f'  {label}   {tail}'))
    print(f'  {CYAN}{label}{RESET}   {sample}{CYAN}{tail}{RESET}')
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
    if cadence:
        print(f'{GREEN}✓{RESET} Marked {lab_id!r} practiced ({today}). Due again in {parse_cadence(cadence)}d.')
    else:
        print(f'{GREEN}✓{RESET} Marked {lab_id!r} practiced ({today}).')
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
        print(f'fzf not found. Use {CYAN}doit labs list{RESET} then {CYAN}doit labs show <id>{RESET}.')
        return 1
    selected = result.stdout.strip()
    if not selected:
        return 0
    return cmd_show(selected.split('\t', 1)[0])


def slugify(name: str) -> str:
    slug = name.strip().lower().replace(' ', '-')
    return ''.join(c for c in slug if c.isalnum() or c == '-')


def cmd_new(name: str) -> int:
    slug = slugify(name)
    if not slug:
        print('A Lab needs a name with at least one letter or digit.', file=sys.stderr)
        return 2
    target = LABS_DIR / f'{slug}.md'
    if target.exists():
        print(f'Lab already exists: {target}')
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(TEMPLATE.format(title=name))
    print(f'{GREEN}✓{RESET} Created {target}')
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
    print(f'No Lab {lab_id!r}.')
    print(f'See what is available with {CYAN}doit labs list{RESET}, or create it with {CYAN}doit labs new {lab_id}{RESET}.')
    return 1


def warn_no_labs() -> int:
    print(f'No Labs yet in {LABS_DIR}.')
    print(f'Create your first with {CYAN}doit labs new <name>{RESET}.')
    return 0


def load_flashcards(subject: str | None = None) -> list[dict]:
    """Recall cards from the registry: prompt = an example's description, answer =
    its command. Zero authoring — every tool's examples become a deck.

    A subject filters to one tool by name or by tag membership.
    """
    if not REGISTRY.exists():
        return []
    data = yaml.safe_load(REGISTRY.read_text()) or {}
    tools = data.get('tools') or {}
    cards = []
    for name, tool in tools.items():
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
        if subject:
            print(f'No examples to drill for {subject!r}.')
        else:
            print(f'No registry examples found at {REGISTRY}.')
        return 0
    random.shuffle(cards)
    cards = cards[:FLASH_SESSION]
    header('Flashcards — recall the command')
    correct = 0
    answered = 0
    for i, card in enumerate(cards, 1):
        print(f'  {YELLOW}{i}/{len(cards)}{RESET}  [{card["tool"]}]')
        print(f'  {card["prompt"]}')
        try:
            if input(f'  {CYAN}Enter to reveal · q to quit ❯ {RESET}').strip().lower() == 'q':
                break
            print(f'  {GREEN}$ {card["answer"]}{RESET}')
            got = input('  got it? [y/N] ❯ ').strip().lower()
        except EOFError:
            break
        answered += 1
        if got == 'y':
            correct += 1
        print()
    if answered:
        print(f'{GREEN}✓{RESET} Recalled {correct}/{answered}.')
    return 0


def show_help() -> int:
    help_header('doit labs', 'Hands-on practice Labs.')
    help_usage('doit labs <verb> [ARGS]')

    help_section('Commands')
    help_row('doit labs due', '', "What's due to practice now")
    help_row('doit labs list', '', 'Every Lab and its schedule status')
    help_row('doit labs list', '--json', 'The same, as JSON')
    help_row('doit labs show', '<id>', 'Open a Lab to read while you work in another pane')
    help_row('doit labs pick', '', 'Pick a Lab interactively (fzf)')
    help_row('doit labs flash', '[<x>]', 'Quick recall drill from tool examples (all, or one tool/tag)')
    help_row('doit labs done', '<id>', 'Mark a Lab practiced (advances its schedule)')
    help_row('doit labs new', '<name>', 'Scaffold a new Lab and open it in $EDITOR')
    help_row('doit labs edit', '<id>', 'Edit an existing Lab in $EDITOR')

    help_end()
    return 0


def needs_argument(usage: str) -> int:
    print(f'Usage: {usage}', file=sys.stderr)
    return 2


def main(args: list[str]) -> int:
    if not args:
        return show_help()
    verb, rest = args[0], args[1:]
    if verb in ('help', '-h', '--help'):
        return show_help()
    if verb == 'due':
        return cmd_due()
    if verb == 'nudge':
        return cmd_nudge()
    if verb == 'list':
        return cmd_list('--json' in rest)
    if verb == 'pick':
        return cmd_pick()
    if verb == 'flash':
        return cmd_flash(rest[0] if rest else None)
    if verb == '__render':
        return cmd_render_preview(rest[0]) if rest else 1
    if verb == 'show':
        return cmd_show(rest[0]) if rest else needs_argument('doit labs show <id>')
    if verb == 'done':
        return cmd_done(rest[0]) if rest else needs_argument('doit labs done <id>')
    if verb == 'new':
        return cmd_new(' '.join(rest)) if rest else needs_argument('doit labs new <name>')
    if verb == 'edit':
        return cmd_edit(rest[0]) if rest else needs_argument('doit labs edit <id>')
    print(f'Unknown: doit labs {verb}', file=sys.stderr)
    show_help()
    return 2
