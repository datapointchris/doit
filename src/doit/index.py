"""The federated index: everything you own, in one searchable list.

Eight collections, one row shape. The tools registry, the workflow cards, Claude
skills, annotated shell functions, aliases, git aliases, forgit's fzf shortcuts,
and the tmux keybindings parsed out of their own card. doit owns none of them —
each row names where it came from and what to type, and rendering a subject is
delegated back to whatever owns it.

**Every row carries its invocation, not just its name.** A registry key is a
package name as often as a command — `ripgrep` installs `rg`, `git-delta`
installs `delta`, forgit's shortcuts are keyed `git-forgit-*` — so a row keyed by
name names something you cannot type. The command is in each entry's `usage`
field: `rg [pattern] [path]`, `git forgit log`.

Rot is reported rather than rendered. `unresolved()` is what turns "this row goes
nowhere" from a thing you rediscover every time you search into a list you can
fix, which is the difference between an index that decays and one that does not.
What survives it is shell integrations defined at runtime (`br`, `z`, `nvm`),
which are not rot, plus entries that genuinely are.
"""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from doit.cards import first_heading
from doit.cards import split_frontmatter
from doit.paths import library_dir
from doit.skills import load_skills
from doit.tools import LEADING_KEYWORDS
from doit.tools import invocation_head
from doit.tools import load_registry

SHELL_DIR = Path(os.environ.get('SHELL_DIR') or Path.home() / '.local' / 'shell')
WORKFLOWS_DIR = Path(os.environ.get('DOIT_WORKFLOWS_DIR') or library_dir() / 'workflows')
FORGIT_PLUGIN = Path(os.environ.get('FORGIT_PLUGIN') or Path.home() / '.config' / 'zsh' / 'plugins' / 'forgit' / 'forgit.plugin.zsh')

# The card that owns the tmux keybindings, so the in-tmux popup and this index
# read one source rather than drifting apart.
TMUX_CARD = 'tmux-commands'


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
    # Shell functions only: the definition itself, which is the refresher a
    # one-line description cannot be.
    body: str = ''
    # What the row expands to, where that is a different string from its name —
    # an alias, a git alias, a forgit shortcut. `invocation` is what you type;
    # this is what happens when you do, and a card that omits it is a card that
    # cannot answer why the alias is worth remembering.
    command: str = ''

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
    aliases too, so reading only the shared files silently drops every
    platform-specific shortcut from the index.
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
    entries = []
    for name, meta in load_registry().items():
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
    and inflate every fuzzy match. The first sentence is what identifies it, and
    `doit skills list --full` is where the rest of it is readable.
    """
    return [Entry(source='skill', name=skill.name, invocation=f'/{skill.name}', description=skill.summary) for skill in load_skills()]


def index_functions() -> list[Entry]:
    """Functions carrying the `#@name` / `#--> description` annotation.

    The annotation is the opt-in: an unannotated function is an internal helper
    and stays out of the index.

    The definition that follows is captured too. A description says what a
    function is for; the body is the only thing that says what it does, and
    reading it is why anyone opens the file. Definitions are top-level, so the
    brace in column one closes them — a nested brace is always indented. A
    function whose block never closes still yields its row, because an entry
    that vanishes on a syntax error is worse than one with a ragged body.
    """
    entries: list[Entry] = []
    pending: tuple[str, str] | None = None
    body: list[str] | None = None
    name = ''

    def flush() -> None:
        nonlocal pending, body
        if pending:
            entries.append(Entry(source='func', name=pending[0], invocation=pending[0], description=pending[1], body='\n'.join(body or [])))
        pending, body = None, None

    for line in shell_text().splitlines():
        if body is not None:
            if line.startswith('}'):
                flush()
            else:
                body.append(line)
        elif line.startswith('#@'):
            flush()
            name = line[2:].strip()
        elif line.startswith('#-->') and name:
            pending, name = (name, line[4:].strip()), ''
        elif line and not line.startswith('#'):
            # The definition line itself; everything up to the closing brace is
            # the body.
            body = [] if pending else None
            name = ''
    flush()
    return entries


def index_aliases() -> list[Entry]:
    """Aliases, with the immediately preceding comment as the description."""
    entries = []
    description = ''
    for line in shell_text().splitlines():
        if line.startswith('#'):
            description = line.lstrip('#').strip()
        elif line.startswith('alias '):
            name, _, expansion = line[6:].partition('=')
            entries.append(
                Entry(source='alias', name=name, invocation=name, description=description, command=expansion.strip().strip('\'"'))
            )
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
            # description and command are the same string here: the expansion is
            # both what identifies a git alias in a search line and what its card
            # prints under it.
            entries.append(Entry(source='git', name=name, invocation=f'git {name}', description=expansion, command=expansion))
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
        Entry(
            source='forgit',
            name=name,
            invocation=name,
            description=f'forgit: {action.replace("_", " ")}',
            command=action.replace('_', ' '),
        )
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


def unresolved(entries: list[Entry] | None = None) -> list[Entry]:
    """Rows naming something this machine cannot run.

    Only the lenses whose rows are commands are checked. A workflow card, a
    skill or a tmux keybinding is not a command and cannot be dead in this sense.

    A row that goes nowhere is registry rot, and rot you cannot list is rot you
    re-encounter forever.
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
