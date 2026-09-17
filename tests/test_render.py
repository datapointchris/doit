"""Tests for doit.render's nudge primitives — the one-line-per-item renderers
shared by review and labs.

The bound these hold is the whole point of a nudge: a row that wraps is two rows,
so a clip that is off by a column silently doubles what shell startup prints. The
callers' own tests cover their assembly; these cover the primitive.

Color is rich's concern and is not asserted here.
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


def test_join_context_drops_blanks_and_repeats():
    assert render.join_context(['syncer', 'syncer']) == 'syncer', 'a repo whose project shares its name is placed once'
    assert render.join_context([None, 'A Project']) == 'A Project', 'a missing fact leaves no dangling separator'
    assert render.join_context(['doit', 'A Project', 'Another']) == 'doit · A Project · Another'
    assert render.join_context([]) == ''


def test_first_sentence_stops_where_the_writer_stopped():
    note = 'The gist of it.\n\nThen the reasoning, and what was rejected on the way.'
    assert render.first_sentence(note) == 'The gist of it.'


def test_first_sentence_keeps_a_note_that_never_ends_a_sentence():
    assert render.first_sentence('Sell on the Discord, maybe Reddit') == 'Sell on the Discord, maybe Reddit'
    assert render.first_sentence('') == ''
    assert render.first_sentence(None) == ''


def test_first_sentence_does_not_break_on_a_period_inside_a_word():
    # `execute.go` and `os.Exit(1)` are one sentence, not three. The boundary is
    # punctuation the next character does not continue.
    note = 'The main() pattern in cobracmd/execute.go flattens every failure to os.Exit(1). Fix it once.'
    assert render.first_sentence(note) == 'The main() pattern in cobracmd/execute.go flattens every failure to os.Exit(1).'


def test_first_sentence_collapses_the_wrapping_a_stored_note_arrives_with():
    assert render.first_sentence('one\n\ttwo   three') == 'one two three'


def test_nudge_header_is_one_line(capsys):
    render.nudge_header('Review', 6)

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1, 'the boxed three-line header belongs to the browse views'
    assert lines[0] == 'Review · 6 due'


def test_a_column_takes_what_it_needs_bounded_by_its_share():
    assert render.column_width(140, ['', ''], 0.3, 20) == 0, 'a column nothing fills reserves nothing'
    assert render.column_width(140, ['↳ indy index'], 0.3, 20) == 12, 'what it needs, when that is under the share'
    assert render.column_width(140, ['x' * 90], 0.3, 20) == 42, 'the share, when the content runs longer'
    assert render.column_width(40, ['x' * 90], 0.3, 20) == 20, 'never below the minimum, however narrow the line'


def test_fitted_truncates_to_the_width_it_was_given():
    assert render.fitted('abcdefgh', 4).plain == 'abc…'
    assert render.fitted('ab', 6, pad=True).plain == 'ab    '
    assert render.fitted('ab', 6).plain == 'ab', 'unpadded, so a trailing column ends where its value does'


def test_a_span_is_reported_in_the_coarsest_unit_that_still_says_something():
    assert render.span_text(3) == '3d'
    assert render.span_text(13.9) == '13d'
    assert render.span_text(30) == '4w'
    assert render.span_text(200) == '6mo'


def test_a_span_ignores_the_direction_it_was_measured_in():
    # A due date says overdue or in; the number beside it is a magnitude.
    assert render.span_text(-21) == render.span_text(21) == '3w'
