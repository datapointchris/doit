"""Tests for doit.cards — the markdown-card primitives Labs and workflows share.

These moved out of the Labs tests when the two browsers became one. They are the
file-shape contract: frontmatter in, title from the first H1, everything else the
body.
"""

from doit import cards


def test_strip_frontmatter_removes_block():
    text = '---\ntitle: X\ntags: [a]\n---\n\n# Heading\nbody\n'
    assert cards.strip_frontmatter(text).startswith('# Heading')
    assert 'title: X' not in cards.strip_frontmatter(text)


def test_strip_frontmatter_passthrough_without_block():
    text = '# Heading\nbody\n'
    assert cards.strip_frontmatter(text) == text


def test_parse_frontmatter(tmp_path):
    f = tmp_path / 'x.md'
    f.write_text('---\ntags: [a, b]\ncadence: 1w\n---\n# H\n')
    meta = cards.parse_frontmatter(f)
    assert meta['tags'] == ['a', 'b']
    assert meta['cadence'] == '1w'


def test_parse_frontmatter_none_when_absent(tmp_path):
    f = tmp_path / 'x.md'
    f.write_text('# H\nno frontmatter\n')
    assert cards.parse_frontmatter(f) == {}


def test_first_heading_is_the_title():
    assert cards.first_heading('# The Title\n\ntext\n## Sub\n') == 'The Title'


def test_first_heading_empty_when_none():
    assert cards.first_heading('no heading here\n') == ''


def test_slugify():
    assert cards.slugify('Find Files Fast!') == 'find-files-fast'
    assert cards.slugify('  rg/search  ') == 'rgsearch'
