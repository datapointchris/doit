"""Tests for doit.render's nudge primitives — the one-line-per-item renderers
shared by review and labs.

The bound these hold is the whole point of a nudge: a row that wraps is two rows,
so a clip that is off by a column silently doubles what shell startup prints. The
callers' own tests cover their assembly; these cover the primitive.

Colour is rich's concern and is not asserted here.
"""

from doit import render


def test_nudge_width_sizes_from_the_longest_name():
    assert render.nudge_width(['a', 'abc', 'ab']) == 5, 'longest name plus a two-column gutter'
    assert render.nudge_width([]) == 2, 'an empty roster is the gutter alone, not a max() error'


def test_nudge_row_fits_the_terminal(monkeypatch, capsys):
    monkeypatch.setenv('COLUMNS', '50')
    render.nudge_row('an-item', 'never done', 'x' * 200, render.nudge_width(['an-item']))

    out = capsys.readouterr().out.rstrip('\n')
    assert len(out) <= 50
    assert out.startswith('  an-item'), "the name is the row's identity and is never clipped"
    assert out.endswith('…')


def test_nudge_row_columns_line_up_across_rows(monkeypatch, capsys):
    """A short name and a long one put their status in the same column."""
    monkeypatch.setenv('COLUMNS', '200')
    width = render.nudge_width(['short', 'a-much-longer-name'])
    render.nudge_row('short', 'never done', 'cmd', width)
    render.nudge_row('a-much-longer-name', 'overdue 3d', 'cmd', width)

    first, second = capsys.readouterr().out.splitlines()
    assert first.index('never done') == second.index('overdue 3d')
    assert first.index('↳') == second.index('↳')


def test_nudge_row_without_a_command(monkeypatch, capsys):
    monkeypatch.setenv('COLUMNS', '80')
    render.nudge_row('an-item', 'never done', '', render.nudge_width(['an-item']))

    out = capsys.readouterr().out.rstrip('\n')
    assert out == '  an-item  never done'
    assert '↳' not in out


def test_nudge_header_is_one_line(capsys):
    render.nudge_header('Review', 6)

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1, 'the boxed three-line header belongs to the browse views'
    assert lines[0] == 'Review · 6 due'
