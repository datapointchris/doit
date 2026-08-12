"""Tests for doit.find — the picker's line format and where a pick ends up.

fzf is never launched. It needs a TTY, so a test that spawned it would hang on
the picker rather than fail; `run_fzf` is stubbed with the row a human would have
landed on, which is the only part of the interaction that matters here.

What is asserted hardest is the split between the two terminal acts. `find` hands
its pick to the composite renderer and `choose` prints one line to stdout, and a
`$(doit choose)` that captured a rule, a colour or a second line would put that
on the command line verbatim.
"""

import pytest
from typer.testing import CliRunner

from doit import find
from doit import index
from doit.cli import app as cli_app

runner = CliRunner()

ENTRIES = [
    index.Entry(source='tool', name='ripgrep', invocation='rg [pattern] [path]', description='Ultra-fast recursive grep'),
    index.Entry(source='alias', name='ll', invocation='ll', description='List files', command='eza -l'),
]


@pytest.fixture
def picks(monkeypatch):
    """Answer the picker with one canned row, and record how it was called."""
    calls = {}

    def choose(row: str | None):
        def stub(lines, query, preview, header):
            calls.update(lines=lines, query=query, preview=preview, header=header)
            return row

        monkeypatch.setattr(find, 'run_fzf', stub)
        return calls

    monkeypatch.setattr(index, 'build_index', lambda sources=None: list(ENTRIES))
    return choose


def test_a_row_carries_its_invocation_past_fzf():
    """`--with-nth=1` hides field four, so it costs nothing to send it through
    and saves resolving the name a second time on the other side."""
    line = find.index_lines(ENTRIES)[0]
    fields = line.split('\t')

    assert len(fields) == 4
    assert fields[0] == ENTRIES[0].display()
    assert (fields[1], fields[2], fields[3]) == ('tool', 'ripgrep', 'rg [pattern] [path]')


def test_only_the_first_field_is_shown_and_searched():
    """The invocation rides in field four precisely so it cannot skew a match."""
    assert '--with-nth=1' in find.FZF_COMMON


def test_choosing_prints_the_invocation_and_nothing_else(picks, capsys):
    picks(find.index_lines(ENTRIES)[0])

    assert find.cmd_choose('', None) == 0

    captured = capsys.readouterr()
    assert captured.out == 'rg [pattern] [path]\n'
    assert captured.err == ''


def test_the_invocation_is_printed_even_where_it_differs_from_the_name(picks, capsys):
    """A registry key is a package name as often as a command; printing the name
    would put `ripgrep` on the line, which is not a thing this machine runs."""
    picks(find.index_lines(ENTRIES)[0])
    find.cmd_choose('', None)

    assert capsys.readouterr().out.strip() != 'ripgrep'


def test_backing_out_of_the_picker_is_not_an_error(picks, capsys):
    """Esc leaves the line alone. A non-zero exit would have the shell function
    report a failure for the ordinary way of changing your mind."""
    picks('')

    assert find.cmd_choose('', None) == 0
    assert capsys.readouterr().out == ''


def test_the_choose_header_says_where_the_pick_lands(picks):
    """Two commands share one picker, so the header is the only thing on screen
    that says which one you are in."""
    calls = picks('')
    find.cmd_choose('', None)

    assert 'command line' in calls['header']


def test_finding_still_opens_the_composite(monkeypatch, picks):
    """Adding a fourth field must not shift the name `find` reads back."""
    picks(find.index_lines(ENTRIES)[1])
    shown = []
    monkeypatch.setattr(find, 'cmd_show', lambda subject: shown.append(subject) or 0)

    assert find.cmd_find('', None) == 0
    assert shown == ['ll']


def test_an_empty_index_says_how_to_fill_it(monkeypatch, capsys):
    """Nothing indexed is a fixable state, not an empty picker."""
    monkeypatch.setattr(index, 'build_index', lambda sources=None: [])

    assert find.cmd_choose('', None) == 1
    assert 'doit content sync' in capsys.readouterr().err


@pytest.mark.parametrize('verb', ['find', 'choose'])
def test_an_unknown_source_is_a_usage_error(verb):
    """Both pickers validate `--source` the same way, because they read one index."""
    result = runner.invoke(cli_app, [verb, '--source', 'nonsense'])

    assert result.exit_code == 2
    assert 'nonsense' in result.output
