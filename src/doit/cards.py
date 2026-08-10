"""Markdown cards: the primitives Labs and workflow cards both need.

Labs and workflow cards are one file shape — YAML frontmatter, a `# ` title, a
body rendered through bat — and differ only in which directory they are read
from, which is not a difference worth two implementations.

Nothing here knows about either collection. A caller supplies the path.
"""

import subprocess
from pathlib import Path

import yaml


def split_document(text: str) -> tuple[str, str]:
    """(raw frontmatter block, body), with no YAML involved.

    Separate from `split_frontmatter` so a reader that has to survive invalid
    YAML can reuse the splitting rather than restate it — `doit.skills` is that
    reader, because Claude Code tolerates frontmatter `yaml.safe_load` rejects.
    Splitting is shared; what to do when the parse fails is each caller's own.
    """
    if not text.startswith('---'):
        return '', text
    parts = text.split('---', 2)
    if len(parts) < 3:
        return '', text
    return parts[1], parts[2].lstrip('\n')


def split_frontmatter(text: str) -> tuple[dict, str]:
    """(frontmatter dict, body) for a card's text.

    The frontmatter carries only `tags` and, for Labs, an optional `cadence`; a
    card's title is its first `# ` heading rather than a frontmatter field, which
    keeps markdownlint happy (no duplicate H1) and means the rendered card reads
    the same as the file.
    """
    front, body = split_document(text)
    meta = yaml.safe_load(front) if front else None
    # A block that parses to a scalar — `---\nfoo\n---` — would otherwise be
    # handed back for a caller to call .get() on.
    return (meta if isinstance(meta, dict) else {}), body


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
