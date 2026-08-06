"""Markdown cards: the primitives Labs and workflow cards both need.

Two browsers over the same file shape — YAML frontmatter, a `# ` title, a body
rendered through bat — had grown up separately, one in Python and one in bash.
They differed only in which directory they read, which is not a difference worth
two implementations.

Nothing here knows about either collection. A caller supplies the path.
"""

import subprocess
from pathlib import Path

import yaml


def split_frontmatter(text: str) -> tuple[dict, str]:
    """(frontmatter dict, body) for a card's text.

    The frontmatter carries only `tags` and, for Labs, an optional `cadence`; a
    card's title is its first `# ` heading rather than a frontmatter field, which
    keeps markdownlint happy (no duplicate H1) and means the rendered card reads
    the same as the file.
    """
    if not text.startswith('---'):
        return {}, text
    parts = text.split('---', 2)
    if len(parts) < 3:
        return {}, text
    return (yaml.safe_load(parts[1]) or {}), parts[2].lstrip('\n')


def parse_frontmatter(path: Path) -> dict:
    """The YAML frontmatter of a card as a dict (empty if there is none)."""
    return split_frontmatter(path.read_text())[0]


def strip_frontmatter(text: str) -> str:
    """The card body with its leading YAML frontmatter block removed."""
    return split_frontmatter(text)[1]


def first_heading(body: str) -> str:
    """The first level-1 markdown heading (`# ...`) in a body, or ''."""
    for line in body.splitlines():
        if line.startswith('# '):
            return line[2:].strip()
    return ''


def slugify(name: str) -> str:
    """A card's filename stem: lowercase, spaces to hyphens, nothing else kept."""
    slug = name.strip().lower().replace(' ', '-')
    return ''.join(character for character in slug if character.isalnum() or character == '-')


def render_body(path: Path, *, for_preview: bool = False) -> None:
    """Render a card's markdown body (frontmatter stripped) through bat.

    Colour is forced because the output is often piped — into a pager, into an
    fzf preview pane — and bat would otherwise strip it on seeing no terminal.
    A preview additionally suppresses paging, which would hang the pane.
    """
    body = strip_frontmatter(path.read_text()).lstrip('\n')
    args = ['bat', '--style=plain', '--language=markdown', '--color=always']
    if for_preview:
        args.append('--paging=never')
    try:
        subprocess.run(args, input=body, text=True, check=False)
    except FileNotFoundError:
        # bat absent: fall back to raw text so the card is still readable. Plain
        # print, because a card is markdown full of brackets and backticks that
        # a rich Console would read as markup and rewrap.
        print(body)
