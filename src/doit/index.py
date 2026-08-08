"""The federated index: everything you own, in one searchable list.

Eight collections, one row shape. The tools registry, the workflow cards, Claude
skills, annotated shell functions, aliases, git aliases, forgit's fzf shortcuts,
and the tmux keybindings parsed out of their own card. doit owns none of them —
each row names where it came from and what to type, and rendering a subject is
delegated back to whatever owns it.

**Every row carries its invocation, and that is the fix this rewrite exists for.**
The bash version indexed the registry by its key, so `ripgrep` and `git-delta`
and twenty `git-forgit-*` entries rendered as rows naming things you cannot type
— 65 of 130 tool rows named something absent from PATH. The command was in the
registry the whole time, in each entry's `usage` field: `rg [pattern] [path]`,
`git forgit log`. Reading it drops the unresolvable count from 65 to 11, and the
survivors are shell integrations defined at runtime (`br`, `z`, `nvm`) plus
genuine registry rot.

Rot is reported rather than rendered. `unresolved()` is what turns "this row goes
nowhere" from a thing you rediscover every time you search into a list you can
fix, which is the difference between an index that decays and one that does not.
"""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from doit.cards import first_heading
from doit.cards import split_frontmatter
from doit.paths import library_dir

SHELL_DIR = Path(os.environ.get('SHELL_DIR') or Path.home() / '.local' / 'shell')
SKILLS_DIR = Path(os.environ.get('DOIT_SKILLS_DIR') or Path.home() / '.claude' / 'skills')
REGISTRY = Path(os.environ.get('DOIT_TOOLS_REGISTRY') or library_dir() / 'tools' / 'registry.yml')
WORKFLOWS_DIR = Path(os.environ.get('DOIT_WORKFLOWS_DIR') or library_dir() / 'workflows')
FORGIT_PLUGIN = Path(os.environ.get('FORGIT_PLUGIN') or Path.home() / '.config' / 'zsh' / 'plugins' / 'forgit' / 'forgit.plugin.zsh')

# The card that owns the tmux keybindings, so the in-tmux popup and this index
# read one source rather than drifting apart.
TMUX_CARD = 'tmux-commands'

# Shell keywords that can lead a `usage` string without being the command.
LEADING_KEYWORDS = {'source', '.', 'eval', 'exec'}


@dataclass(frozen=True)
class Entry:
    """One indexed thing.

    `name` is its identity in the collection that owns it; `invocation` is what
    you would type, which is not always the same string and is the whole point.
    """

    source: str
    name: str
    invocation: str
    description: str = ''
    tags: tuple[str, ...] = ()
    # Registry entries only: what `doit launch` uses to offer your own tools
    # ahead of the hundred third-party ones.
    category: str = ''

    def display(self) -> str:
        """The one line fzf shows and searches.

        Name, invocation, description and tags all ride in it, because fzf can
        only match what it displays — tags are the discovery contract, so a
        missing result means a missing tag rather than a broken search.
        """
        parts = [f'[{self.source}]'.ljust(10), self.name.ljust(22)]
        if self.invocation and self.invocation != self.name:
            parts.append(f'{self.invocation}  ')
        if self.description:
            parts.append(self.description)
        if self.tags:
            parts.append(' '.join(self.tags))
        return ' '.join(part for part in parts if part).rstrip()


def shell_files() -> list[Path]:
    """The shared shell files plus this platform's, which holds both.

    The platform file (macos.sh / archlinux.sh / wsl.sh) carries functions and
    aliases too. Reading only the shared files is what made platform-specific
    shortcuts findable in `toolbox funcs` but not here.
    """
    files = [SHELL_DIR / 'functions.sh', SHELL_DIR / 'aliases.sh']
    platform = os.environ.get('PLATFORM')
    if platform and (SHELL_DIR / f'{platform}.sh').exists():
        files.append(SHELL_DIR / f'{platform}.sh')
    return [path for path in files if path.exists()]


def shell_text() -> str:
    return '\n'.join(path.read_text() for path in shell_files())


def index_tools() -> list[Entry]:
    """The tools registry, indexed by what you type rather than by its key.

    A registry key is a package name as often as a command — `ripgrep` installs
    `rg`, `git-delta` installs `delta` — so the key alone names a row you cannot
    act on. `usage` holds the real invocation and every entry has one.
    """
    if not REGISTRY.exists():
        return []
    tools = (yaml.safe_load(REGISTRY.read_text()) or {}).get('tools') or {}
    entries = []
    for name, meta in tools.items():
        meta = meta or {}
        entries.append(
            Entry(
                source='tool',
                name=name,
                invocation=(meta.get('usage') or name).strip(),
                description=meta.get('description') or '',
                tags=tuple(meta.get('tags') or ()),
                category=meta.get('category') or '',
            )
        )
    return entries


def index_workflows() -> list[Entry]:
    if not WORKFLOWS_DIR.exists():
        return []
    entries = []
    for path in sorted(WORKFLOWS_DIR.glob('*.md')):
        meta, body = split_frontmatter(path.read_text())
        entries.append(
            Entry(
                source='workflow',
                name=path.stem,
                invocation=f'doit workflows show {path.stem}',
                description=first_heading(body) or path.stem,
                tags=tuple(meta.get('tags') or ()),
            )
        )
    return entries


def index_skills() -> list[Entry]:
    """Claude skills, described by the first sentence of their description.

    A skill description carries its trigger and output rules too, which are long
    and inflate every fuzzy match. The first sentence is what identifies it.
    """
    if not SKILLS_DIR.exists():
        return []
    entries = []
    for path in sorted(SKILLS_DIR.glob('*/SKILL.md')):
        described = re.search(r'^description:\s*(.+)$', path.read_text(), re.M)
        summary = described.group(1).strip() if described else ''
        summary = re.split(r'(?<=[.!?])\s+(?=[A-Z])', summary)[0]
        entries.append(Entry(source='skill', name=path.parent.name, invocation=f'/{path.parent.name}', description=summary))
    return entries


def index_functions() -> list[Entry]:
    """Functions carrying the `#@name` / `#--> description` annotation.

    The annotation is the opt-in: an unannotated function is an internal helper
    and stays out of the index.
    """
    entries = []
    name = ''
    for line in shell_text().splitlines():
        if line.startswith('#@'):
            name = line[2:].strip()
        elif line.startswith('#-->') and name:
            entries.append(Entry(source='func', name=name, invocation=name, description=line[4:].strip()))
            name = ''
        elif line and not line.startswith('#'):
            name = ''
    return entries


def index_aliases() -> list[Entry]:
    """Aliases, with the immediately preceding comment as the description."""
    entries = []
    description = ''
    for line in shell_text().splitlines():
        if line.startswith('#'):
            description = line.lstrip('#').strip()
        elif line.startswith('alias '):
            name = line[6:].split('=', 1)[0]
            entries.append(Entry(source='alias', name=name, invocation=name, description=description))
            description = ''
        elif line.strip():
            description = ''
    return entries


def index_git_aliases() -> list[Entry]:
    """Asked of git itself, so every config file in the chain resolves."""
    try:
        result = subprocess.run(['git', 'config', '--get-regexp', r'^alias\.'], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return []
    entries = []
    for line in result.stdout.splitlines():
        key, _, expansion = line.partition(' ')
        name = key.removeprefix('alias.')
        if name:
            entries.append(Entry(source='git', name=name, invocation=f'git {name}', description=expansion))
    return entries


def index_forgit() -> list[Entry]:
    """forgit's fzf-interactive git shortcuts, parsed from the plugin.

    They are sourced after aliases.sh, so they live in no shell file the alias
    lens reads.
    """
    if not FORGIT_PLUGIN.exists():
        return []
    pattern = re.compile(r'forgit_([a-z_]+)="\$\{forgit_[a-z_]+:-([a-zA-Z-]+)\}"')
    return [
        Entry(source='forgit', name=name, invocation=name, description=f'forgit: {action.replace("_", " ")}')
        for action, name in pattern.findall(FORGIT_PLUGIN.read_text())
    ]


def index_tmux_keys() -> list[Entry]:
    """Keybindings read out of the tmux card, so the popup and this agree.

    The card is markdown tables of key | description, so a row qualifies when its
    key cell starts with a binding token. An escaped pipe inside a key cell
    (`prefix + \\|`) would otherwise split as a column boundary, so it is swapped
    out before the split and back afterwards.
    """
    card = WORKFLOWS_DIR / f'{TMUX_CARD}.md'
    if not card.exists():
        return []
    entries = []
    for line in card.read_text().splitlines():
        if not line.strip().startswith('|'):
            continue
        cells = line.replace(r'\|', '\x01').split('|')
        if len(cells) < 3:
            continue
        key, description = (cell.replace('\x01', '|').strip() for cell in cells[1:3])
        if re.match(r'^(prefix|Ctrl|Alt)', key):
            entries.append(Entry(source='tmux', name=key, invocation=key, description=description))
    return entries


LENSES = {
    'tool': index_tools,
    'workflow': index_workflows,
    'skill': index_skills,
    'func': index_functions,
    'alias': index_aliases,
    'git': index_git_aliases,
    'forgit': index_forgit,
    'tmux': index_tmux_keys,
}


def build_index(sources: list[str] | None = None) -> list[Entry]:
    """Every collection's rows, sorted for a stable dump.

    fzf re-ranks by score regardless, so the sort only makes the raw index
    readable — and diffable, which is how you see a collection disappear.
    """
    wanted = sources or list(LENSES)
    entries: list[Entry] = []
    for source in wanted:
        entries.extend(LENSES[source]())
    return sorted(entries, key=lambda entry: (entry.source, entry.name))


def resolvable_names() -> set[str]:
    """Every word that names something runnable on this machine.

    Built once and reused, because `unresolved` would otherwise shell out per
    row. Shell functions and aliases are read from their files rather than asked
    of the shell: this runs in a subprocess that has sourced nothing.
    """
    text = shell_text()
    names = set(re.findall(r'^#@(\S+)$', text, re.M))
    names |= set(re.findall(r'^alias\s+([^=\s]+)=', text, re.M))
    names |= {entry.name for entry in index_forgit()}
    return names


def invocation_head(invocation: str) -> str:
    """The word a `usage` string actually asks you to run."""
    tokens = invocation.split()
    if not tokens:
        return ''
    if tokens[0] in LEADING_KEYWORDS and len(tokens) > 1:
        return tokens[1]
    return tokens[0]


def unresolved(entries: list[Entry] | None = None) -> list[Entry]:
    """Rows naming something this machine cannot run.

    Only the lenses whose rows are commands are checked. A workflow card, a
    skill or a tmux keybinding is not a command and cannot be dead in this sense.

    This is the report the bash version never had. A row that goes nowhere is
    registry rot, and rot you cannot list is rot you re-encounter forever.
    """
    checkable = {'tool', 'func', 'alias', 'forgit'}
    entries = build_index() if entries is None else entries
    known = resolvable_names()
    git_aliases = {entry.name for entry in index_git_aliases()}
    dead = []
    for entry in entries:
        if entry.source not in checkable:
            continue
        head = invocation_head(entry.invocation)
        if not head or head in known or head in LEADING_KEYWORDS:
            continue
        tokens = entry.invocation.split()
        if head == 'git' and len(tokens) > 1 and tokens[1] in git_aliases | {'forgit'}:
            continue
        if shutil.which(head):
            continue
        dead.append(entry)
    return dead
