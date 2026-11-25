"""Tests for doit.render's text helpers, which write the cells of each view's rows."""

from doit import render


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


def test_a_span_is_reported_in_the_coarsest_unit_that_still_says_something():
    assert render.span_text(3) == '3d'
    assert render.span_text(13.9) == '14d'
    assert render.span_text(30) == '4w'
    assert render.span_text(200) == '7mo'


def test_a_span_rounds_rather_than_truncating():
    """Truncating loses most of a unit at the top of every bucket, and the whole
    value below the smallest one — where a reader takes `0` for none."""
    assert render.span_text(20) == '3w', '20 days is nearer three weeks than two'
    assert render.span_text(119) == '4mo'


def test_a_span_below_its_smallest_unit_is_one_of_it_rather_than_none():
    """A weight-implied interval goes sub-day at ordinary logging rates, and
    `every 0d` reads as a schedule that asks for nothing."""
    assert render.span_text(0.67) == '1d'
    assert render.span_text(0.04) == '1d'


def test_a_span_ignores_the_direction_it_was_measured_in():
    # A due date says overdue or in; the number beside it is a magnitude.
    assert render.span_text(-21) == render.span_text(21) == '3w'
