"""Claude skills — what you own, and what each one is actually for.

Invoked as `doit claude skills`; see `doit.claude` for why it sits under a
namespace. A skill is a directory under `~/.claude/skills/` holding one
`SKILL.md`: YAML frontmatter naming it and describing when it should fire, and a
markdown body that is the instruction Claude follows.

This is a browse view, and it exists because the two other ways of seeing the
library both discard the part worth reading. `doit find --source skill` keeps only
the first sentence, because a full description carries trigger phrases and output
rules that inflate every fuzzy match. Claude Code's own listing shows what the
model sees, not what Chris wants to choose from. Here the whole description is the
point.

**Names group themselves.** A skill is `<verb>-<target>`, and the verb is the
closed vocabulary the library is governed by — `review-`, `audit-`, `brief-`,
`capture-`, plus the `learn-` namespace. Grouping splits on the name rather than
matching a list, so the vocabulary lives in one place (`review-fleet` § "Skill
health") and adding a verb needs no release here. A name that does not parse is
visible by landing in its own group rather than by a check that has to be kept
current.

This module owns `SKILLS_DIR` and the loader; `doit.index` reads through it, since
a path defined per reader is a path that drifts.
"""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.text import Text

from doit.cards import split_document
from doit.render import console
from doit.render import error_console
from doit.tools import block
from doit.tools import field

SKILLS_DIR = Path(os.environ.get('DOIT_SKILLS_DIR') or Path.home() / '.claude' / 'skills')

# A description ends its first sentence at punctuation followed by a capital.
# `doit.render.first_sentence` stops at any punctuation-then-space, which is right
# for a stored note and wrong here: descriptions are dense with `e.g.`, `~/notes/`
# and `/slash-commands`, each of which would end the sentence early.
SUMMARY_END = re.compile(r'(?<=[.!?])\s+(?=[A-Z])')

# Frontmatter keys read by the fallback below. Anything else is only reachable
# through YAML, which is the normal path.
LENIENT_KEYS = ('name', 'description', 'argument-hint', 'disable-model-invocation')


@dataclass(frozen=True)
class Skill:
    """One skill, as a browse view needs it rather than as the index does."""

    name: str
    description: str
    argument_hint: str
    # Whether Claude may invoke it on its own. `disable-model-invocation: true`
    # also drops the description from the context window, so a skill that is
    # manual-only is one whose trigger prose is never read.
    auto: bool
    # Whether the frontmatter parsed as strict YAML. See `read_frontmatter`.
    strict: bool
    path: Path
    sections: tuple[str, ...]

    @property
    def group(self) -> str:
        return self.name.split('-', 1)[0] if '-' in self.name else ''

    @property
    def summary(self) -> str:
        return SUMMARY_END.split(' '.join(self.description.split()), 1)[0]


def read_frontmatter(text: str) -> tuple[dict, str, bool]:
    """(frontmatter, body, whether it was valid YAML).

    A description is one long unquoted sentence, and several in the library
    contain a `: ` that makes the block invalid YAML — Claude Code's own loader
    is lenient enough not to notice. Parsing strictly and dropping those would
    hide the most-used skills from this listing, so a failure falls back to
    reading the keys line by line.

    The flag is the point of doing it this way. `unresolved()` in `doit.index`
    established the shape: report rot rather than rendering around it, or the
    listing quietly becomes the reason nobody fixes the file.

    The split comes from `doit.cards` and only the policy lives here. Restating
    the split is what let the two copies diverge on whether a scalar frontmatter
    is guarded against.
    """
    front, body = split_document(text)
    if not front:
        return {}, body, True
    try:
        meta = yaml.safe_load(front)
    except yaml.YAMLError:
        return lenient_frontmatter(front), body, False
    return (meta if isinstance(meta, dict) else {}), body, True


def lenient_frontmatter(front: str) -> dict:
    """The keys a listing needs, read a line at a time.

    Only reached once YAML has already refused the block, so this is not a second
    parser competing with the first — it is what keeps an invalid file listed
    long enough for the flag to get it fixed.
    """
    meta: dict[str, str | bool] = {}
    for line in front.splitlines():
        key, separator, value = line.partition(':')
        if separator and key.strip() in LENIENT_KEYS:
            meta[key.strip()] = value.strip().strip('"\'')
    return meta


def load_skills() -> list[Skill]:
    """Every skill in the library, sorted by name."""
    if not SKILLS_DIR.exists():
        return []
    skills = []
    for path in sorted(SKILLS_DIR.glob('*/SKILL.md')):
        meta, body, strict = read_frontmatter(path.read_text())
        skills.append(
            Skill(
                # The directory name is the identity: it is what `/name` reaches
                # and what Claude Code loads the skill under, whatever the
                # frontmatter claims.
                name=path.parent.name,
                description=str(meta.get('description') or ''),
                argument_hint=str(meta.get('argument-hint') or ''),
                auto=not str(meta.get('disable-model-invocation') or '').lower().startswith('t'),
                strict=strict,
                path=path,
                sections=tuple(line[3:].strip() for line in body.splitlines() if line.startswith('## ')),
            )
        )
    return skills


def warn_no_skills() -> int:
    console.print(Text(f'No skills in {SKILLS_DIR}.'))
    console.print('The library is Syncthing-synced under [cyan]~/.claude/[/], which the work box does not carry.')
    return 0


def unknown_skill(name: str) -> int:
    error_console.print(Text(f'No skill {name!r}.'))
    hint = Text('See what is available with ')
    hint.append('doit claude skills list', style='cyan')
    hint.append(', or search across everything with ')
    hint.append(f'doit find {name}', style='cyan')
    error_console.print(hint)
    return 1


def group_names(skills: list[Skill]) -> list[str]:
    """Every group present, in display order."""
    return sorted({skill.group or 'ungrouped' for skill in skills})


def check_group(group: str | None, skills: list[Skill]) -> None:
    """Reject an unknown `--group` as a usage error, before anything renders.

    Ahead of the `--json` branch on purpose: a machine caller filtering on a
    group that does not exist was being handed `[]` and exit 0, which reads as
    "the group is empty" rather than "you named the wrong one".

    Silent on an empty library, so the work box — which carries no `~/.claude`
    at all — gets the explanation in `warn_no_skills` rather than a usage error
    about a group that would have been valid anywhere else.
    """
    if group and skills and group not in group_names(skills):
        raise typer.BadParameter(f'unknown group {group!r}; choose from {", ".join(group_names(skills))}')


def cmd_list(group: str | None, *, as_json: bool = False, full: bool = False) -> int:
    skills = load_skills()
    check_group(group, skills)
    if as_json:
        rows = [
            {
                'name': skill.name,
                'group': skill.group,
                'summary': skill.summary,
                'description': skill.description,
                'argument_hint': skill.argument_hint,
                'auto_invocable': skill.auto,
                'valid_yaml': skill.strict,
                'path': str(skill.path),
            }
            for skill in skills
            if not group or skill.group == group
        ]
        print(json.dumps(rows, indent=2))
        return 0
    if not skills:
        return warn_no_skills()

    grouped: dict[str, list[Skill]] = {}
    for skill in skills:
        grouped.setdefault(skill.group or 'ungrouped', []).append(skill)
    if group:
        grouped = {group: grouped[group]}

    console.rule('[cyan]Skills', align='left')
    width = max(len(skill.name) for rows in grouped.values() for skill in rows)
    for name in sorted(grouped):
        console.print()
        console.print(Text(name, style='yellow'))
        for skill in grouped[name]:
            line = Text('  ')
            line.append(skill.name.ljust(width), style='green')
            line.append(f'  {skill.description if full else skill.summary}')
            # Full descriptions are paragraphs, so they wrap; a summary is one
            # line and is clipped rather than folded into a second row.
            console.print(line, no_wrap=not full, overflow=None if full else 'ellipsis')

    shown = sum(len(rows) for rows in grouped.values())
    console.print(f'\n  {shown} skills · [cyan]doit claude skills show <name>[/]')
    report_invalid(skills)
    return 0


def report_invalid(skills: list[Skill]) -> None:
    """Name any skill whose frontmatter is not valid YAML.

    Claude Code tolerates it; anything parsing the block strictly will not, and
    the fix is one pair of quotes around the description.
    """
    broken = [skill.name for skill in skills if not skill.strict]
    if broken:
        error_console.print(Text(f'\n  Frontmatter is not valid YAML in: {", ".join(broken)}', style='yellow'))
        error_console.print(Text('  Quote the description — an unquoted `: ` ends the mapping value.'))


def load_skill(name: str) -> Skill | None:
    """One skill by name, or None. `/name` is accepted — it is what gets pasted."""
    name = name.removeprefix('/')
    return next((skill for skill in load_skills() if skill.name == name), None)


def cmd_show(name: str) -> int:
    skill = load_skill(name)
    if skill is None:
        return unknown_skill(name.removeprefix('/'))
    render_skill(skill)
    return 0


def render_skill(skill: Skill, heading: bool = True) -> None:
    """The detail card.

    `heading` is off when a caller has already named the subject, so `doit show`
    does not rule the same name twice — the same contract `render_tool` carries.
    """
    if heading:
        console.rule(f'[cyan]{skill.name}', align='left')
        console.print()
    block('Description', skill.description)
    field('Invoke', f'/{skill.name} {skill.argument_hint}'.strip())
    field('Auto-invocable', 'yes' if skill.auto else f'no — only /{skill.name} reaches it')
    if not skill.strict:
        field('Frontmatter', 'not valid YAML — quote the description')
    field('File', str(skill.path))
    if skill.sections:
        console.print()
        console.print(Text('Steps:', style='yellow'))
        for section in skill.sections:
            console.print(Text(f'  {section}'), no_wrap=True, overflow='ellipsis')


def cmd_groups(*, as_json: bool = False) -> int:
    counts: dict[str, int] = {}
    for skill in load_skills():
        counts[skill.group or 'ungrouped'] = counts.get(skill.group or 'ungrouped', 0) + 1
    if as_json:
        print(json.dumps([{'group': name, 'skills': counts[name]} for name in sorted(counts)], indent=2))
        return 0
    if not counts:
        return warn_no_skills()
    console.rule('[cyan]Skill groups', align='left')
    width = max(len(name) for name in counts)
    for name in sorted(counts):
        line = Text('  ')
        line.append(name.ljust(width), style='green')
        line.append(f'  {counts[name]}')
        console.print(line)
    console.print('\n  [cyan]doit claude skills list --group <name>[/]')
    return 0


app = typer.Typer(name='skills', no_args_is_help=True, help='The Claude skills you own.')


@app.command('list')
def list_command(
    group: Annotated[str | None, typer.Option('--group', help='Limit to one verb group.')] = None,
    full: Annotated[bool, typer.Option('--full', help='The whole description, not its first sentence.')] = False,
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
) -> None:
    """Every skill and when it fires, grouped by verb."""
    raise typer.Exit(cmd_list(group, as_json=as_json, full=full))


@app.command('show')
def show_command(name: Annotated[str, typer.Argument(help='The skill to describe.')]) -> None:
    """One skill in full: what it is for, how to invoke it, and what it does."""
    raise typer.Exit(cmd_show(name))


# A namespace rather than a bare `groups` that lists, matching the call already
# made for `doit tools categories`: every node in the tree prints help bare, so
# walking down one token at a time never runs something you did not ask for.
groups_app = typer.Typer(name='groups', no_args_is_help=True, help='The verb groups skills are named under.')
app.add_typer(groups_app, name='groups')


@groups_app.command('list')
def groups_list_command(
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
) -> None:
    """Every group and how many skills it holds."""
    raise typer.Exit(cmd_groups(as_json=as_json))
