"""The federated index: everything you own, in one searchable list.

One row shape over every collection — `LENSES` is the roster. The tools
registry, the workflow cards, Claude skills, annotated shell functions, aliases,
git aliases, forgit's fzf shortcuts, the tmux keybindings parsed out of their own
card, and the zsh keys your config binds. doit owns none of them — each row names
where it came from and what to type, and rendering a subject is delegated back to
whatever owns it.

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

import functools
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
    `doit claude skills list --full` is where the rest of it is readable.
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


# Every keymap that could be the live one, plus `main` to identify which of them
# is. Both editing modes are asked for because which one a machine runs is its own
# choice, and only the one `main` points at ends up indexed.
ZSH_KEYMAPS = ('main', 'emacs', 'viins', 'vicmd')

# Bound in every zsh and never a discovery: literal insertion, the terminal's own
# escape sequences, and the digit prefix.
ZSH_NOISE = frozenset({'self-insert', 'undefined-key', 'bracketed-paste', 'digit-argument'})

# What zsh's own widgets do, in the words someone searching would use. Only the
# ones a config actually binds need an entry — a widget absent here falls back to
# its own name, which is already most of a description.
ZSH_WIDGETS = {
    '_bash_complete-word': "complete the word using bash's completion rules",
    '_bash_list-choices': "list matches using bash's completion rules",
    '_complete_debug': 'run completion again with tracing on, into a pager',
    '_complete_help': 'show which completion functions and tags apply here',
    '_complete_tag': 'complete restricted to one group of matches',
    '_correct_filename': 'spell-correct the filename under the cursor',
    '_correct_word': 'spell-correct the word under the cursor',
    '_expand_alias': 'expand the alias under the cursor in place',
    '_expand_word': 'expand the word under the cursor in place',
    '_history-complete-newer': 'complete from newer matches in history',
    '_history-complete-older': 'complete from older matches in history',
    '_list_expansions': 'list what the current word would expand to',
    '_most_recent_file': 'insert the most recently modified matching file',
    '_next_tags': 'cycle to the next group of completion matches',
    '_read_comp': 'read a completion specification at the prompt',
    'atuin-search': 'search shell history with atuin',
    'atuin-search-viins': 'search shell history with atuin',
    'fzf-cd-widget': 'cd into a directory picked with fzf',
    'fzf-completion': 'complete the current word through fzf',
    'fzf-file-widget': 'insert a file path picked with fzf',
    'fzf-history-widget': 'search shell history with fzf',
}

# `"^H" fzf-man-widget`. A range (`"^A"-"^C"`) fails it at the space, which is
# wanted — a range is only ever bulk self-insert. So does `"gUU" "gUgU"`, which
# aliases one key sequence to another rather than naming a widget.
ZSH_BINDING = re.compile(r'^"((?:[^"\\]|\\.)*)"\s+([^"\s]\S*)$')

# Control sequences with a key of their own on the keyboard. Naming the key is
# the whole point of the translation, and `Tab` says something `^I` does not.
CONTROL_KEYS = {'I': 'Tab', 'M': 'Enter', 'J': 'Enter', '?': 'Backspace'}


def readable_key(key: str) -> str:
    """`^X^R` as `Ctrl-X Ctrl-R` — the notation the tmux card already uses.

    Not cosmetic. `^` is fzf's prefix-anchor operator, so a row displaying `^H`
    is a row nobody can find by typing its key: the query anchors to the start of
    the line and matches nothing. Every control binding was unsearchable by name
    until this translation went in, which the recall harness is what caught.
    """
    parts = []
    position = 0
    while position < len(key):
        if key.startswith('^[', position):
            following = key[position + 2 : position + 3]
            parts.append(f'Alt-{following}' if following else 'Esc')
            position += 3 if following else 2
        elif key[position] == '^' and position + 1 < len(key):
            letter = key[position + 1].upper()
            parts.append(CONTROL_KEYS.get(letter) or f'Ctrl-{letter}')
            position += 2
        else:
            parts.append(key[position])
            position += 1
    return ' '.join(parts)


ZSH_DUMP = 'for keymap in {}; do print "#keymap $keymap"; bindkey -M $keymap; done'.format(' '.join(ZSH_KEYMAPS))


@functools.cache
def zsh_bindkeys(interactive: bool) -> str:
    """The keymaps as zsh itself reports them, or '' when it cannot be asked.

    Interactive is the live shell, with every plugin loaded; `-f` is stock zsh
    with no startup files at all. The pair is what makes the diff possible.

    Cached because a `doit find` that opens a card builds the index twice, and
    the interactive read is the most expensive thing in it — it sources the whole
    zshrc. Safe only because doit is a short-lived process.
    """
    flags = ['-i', '-c'] if interactive else ['-f', '-c']
    try:
        result = subprocess.run(
            ['zsh', *flags, ZSH_DUMP],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ''
    return result.stdout


def parse_bindkeys(text: str) -> dict[tuple[str, str], str]:
    """`{(keymap, key): widget}` from a `bindkey` dump.

    Anything that is not a `#keymap` marker or a binding is dropped, which is
    what makes a zshrc that greets you on startup harmless here.
    """
    bindings: dict[tuple[str, str], str] = {}
    keymap = ''
    for line in text.splitlines():
        if line.startswith('#keymap '):
            keymap = line.removeprefix('#keymap ').strip()
        elif keymap and (match := ZSH_BINDING.match(line)):
            bindings[keymap, match.group(1)] = match.group(2)
    return bindings


def active_keymaps(bindings: dict[tuple[str, str], str]) -> tuple[str, ...]:
    """The keymaps a key you press can actually reach.

    `main` is an alias for whichever editing mode is live, so the other mode is
    dormant — fzf binds its widgets into both, and indexing the dormant copy
    offers keys that do nothing when pressed. Command mode ships with vi
    insert mode and is unreachable without it.

    Reported under the real name rather than as `main`, because the name is what
    a `bindkey -M` goes on to use.
    """

    def keys_of(name: str) -> dict[str, str]:
        return {key: widget for (keymap, key), widget in bindings.items() if keymap == name}

    main = keys_of('main')
    for mode in ('viins', 'emacs'):
        if main and keys_of(mode) == main:
            return (mode, 'vicmd') if mode == 'viins' else (mode,)
    # A keymap of someone's own making, linked to main under a name this does not
    # know. Indexing it as `main` is worse than dropping it.
    return ('main',)


def index_zsh_keys() -> list[Entry]:
    """Keys your zsh binds that a stock zsh does not.

    Asked of zsh rather than read out of the config files, for the reason the git
    lens is asked of git: fzf and atuin bind their widgets when they load, so the
    keys most worth finding — `^R`, `^T`, `^[c` — appear in no file this repo or
    dotfiles could parse.

    Diffed against stock zsh rather than filtered by a denylist. A raw keymap is
    mostly vi motions, which are documented everywhere and discover nothing; what
    is left after the diff is exactly what this machine's config added. The diff
    also needs no maintenance, whereas a list of widgets to ignore would rot every
    time a plugin changed.

    Narrowed further to the keymaps you can reach, because a plugin binds into
    both editing modes and only one of them is live.

    A widget you wrote yourself gets its description from the function lens, so
    the annotation above the function is still the only place it is written down.
    """
    live = parse_bindkeys(zsh_bindkeys(interactive=True))
    if not live:
        return []
    stock = parse_bindkeys(zsh_bindkeys(interactive=False))
    reachable = active_keymaps(live)
    described = {entry.name: entry.description for entry in index_functions()}
    entries = []
    for (keymap, key), widget in sorted(live.items()):
        if keymap not in reachable or widget in ZSH_NOISE or stock.get((keymap, key)) == widget:
            continue
        description = described.get(widget) or ZSH_WIDGETS.get(widget) or widget.lstrip('_').replace('-', ' ').replace('_', ' ')
        pressed = readable_key(key)
        entries.append(
            Entry(
                source='zsh',
                # The keymap rides in the name because `^R` is atuin in insert
                # mode and fzf in command mode, and a row you cannot tell apart
                # from another row is one `doit show` cannot resolve.
                name=f'{keymap} {pressed}',
                invocation=pressed,
                # The raw sequence survives here because it is what `bindkey`
                # speaks, and rebinding the key is the one thing you would go on
                # to do. Off the display line, where `^` would be an fzf operator.
                command=f"bindkey -M {keymap} '{key}' {widget}",
                description=description,
            )
        )
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
    'zsh': index_zsh_keys,
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
