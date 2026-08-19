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
import typer
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
    """The row is `ripgrep` and the answer is `rg`: a registry key is a package
    name as often as a command, and printing the name would put something this
    machine cannot run on the line."""
    picks(find.index_lines(ENTRIES)[0])

    assert find.cmd_choose('', None) == 0

    captured = capsys.readouterr()
    assert captured.out == 'rg [pattern] [path]\n'
    assert captured.err == ''


def test_backing_out_of_the_picker_is_not_an_error(picks, capsys):
    """Esc leaves the line alone. A non-zero exit would have the shell function
    report a failure for the ordinary way of changing your mind."""
    picks('')

    assert find.cmd_choose('', None) == 0
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('command', [find.cmd_choose, find.cmd_find])
def test_a_missing_picker_is_a_failure_rather_than_a_cancelled_pick(monkeypatch, capsys, command):
    """Esc and "fzf is not installed" both used to be ''. `choose` is read back
    through `$(...)`, where exit status is the only signal the caller gets, so
    a machine without fzf reported "it ran and you chose nothing"."""
    monkeypatch.setattr(index, 'build_index', lambda sources=None: list(ENTRIES))
    monkeypatch.setattr(find.shutil, 'which', lambda binary: None)

    assert command('', None) == 1

    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'fzf is not installed' in captured.err


def test_launching_without_a_picker_fails_the_same_way(monkeypatch, capsys):
    """`launch` reads one index of its own, so it does not inherit the guard."""
    monkeypatch.setattr(index, 'index_tools', lambda: [])
    monkeypatch.setattr(find.shutil, 'which', lambda binary: None)

    assert find.cmd_launch() == 1
    assert 'fzf is not installed' in capsys.readouterr().err


def test_each_picker_names_where_its_own_pick_lands(picks):
    """Two commands share one picker, so the header is the only thing on screen
    that says which one you are in. Compared against the constants rather than
    a phrase, so rewording a header is not a test failure."""
    calls = picks('')
    find.cmd_choose('', None)
    assert calls['header'] == find.CHOOSE_HEADER

    calls = picks('')
    find.cmd_find('', None)
    assert calls['header'] == find.FIND_HEADER

    assert find.CHOOSE_HEADER != find.FIND_HEADER


def test_finding_still_opens_the_composite(monkeypatch, picks):
    """Adding a fourth field must not shift the name `find` reads back."""
    picks(find.index_lines(ENTRIES)[1])
    shown = []
    monkeypatch.setattr(find, 'cmd_show', lambda subject: shown.append(subject) or 0)

    assert find.cmd_find('', None) == 0
    assert shown == ['ll']


def test_the_preview_builds_only_the_collection_it_was_given(monkeypatch):
    """It runs once per keystroke, and building every collection to render one
    row asks zsh for its keymaps each time. That subprocess is an interactive
    shell, which takes the terminal from the picker that spawned it."""
    asked = []

    def record(sources=None):
        asked.append(sources)
        return list(ENTRIES)

    monkeypatch.setattr(index, 'build_index', record)

    with pytest.raises(typer.Exit):
        find.preview_command('alias', 'll')

    assert asked == [['alias']]


def test_a_launcher_row_says_which_collection_it_came_from(monkeypatch):
    """Areas and tools share one picker and one preview string, so a row that
    does not name its collection is one the preview renders as registry rot."""
    monkeypatch.setattr(index, 'index_tools', lambda: [])
    calls = {}
    monkeypatch.setattr(find, 'run_fzf', lambda lines, query, preview, header: calls.update(lines=lines, preview=preview) or '')

    assert find.cmd_launch() == 0
    assert calls['preview'] == 'doit __preview {2} {3}'
    assert all(line.split('\t')[1] == find.AREA for line in calls['lines'])


def test_picking_an_area_lands_on_its_help_not_the_composite(monkeypatch):
    """Nothing in any collection is named `review due`, so the composite can
    only report that it knows nothing about doit's own areas."""
    monkeypatch.setattr(index, 'index_tools', lambda: [])
    ran = []
    monkeypatch.setattr(find, 'delegate', lambda command: ran.append(command) or True)
    monkeypatch.setattr(find, 'run_fzf', lambda *args: f'review due  what is due\t{find.AREA}\treview due')

    assert find.cmd_launch() == 0
    assert ran == [['doit', 'review', 'due', '--help']]


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


def test_peek_shows_the_card_without_advancing_the_rotation(monkeypatch, tmp_path):
    """The startup nudge draws this repeatedly; the rotation moves when you do."""
    monkeypatch.setattr(find.rotate, 'ROTATION_DIR', tmp_path / 'rotation')
    monkeypatch.setattr(find.rotate, 'next_up', lambda lens, *args: ENTRIES[1])

    assert find.cmd_remind('alias', brief=True, peek=True) == 0
    assert not find.rotate.cursor_path('alias').exists()

    assert find.cmd_remind('alias', brief=True, peek=False) == 0
    assert 'll' in find.rotate.cursor_path('alias').read_text()


def test_an_unknown_lens_is_a_usage_error():
    result = runner.invoke(cli_app, ['kit', 'remind', '--lens', 'nonsense'])

    assert result.exit_code == 2
    assert 'nonsense' in result.output


def test_a_lens_with_nothing_cold_says_so_rather_than_printing_a_blank(monkeypatch, capsys):
    monkeypatch.setattr(find.rotate, 'next_up', lambda lens, *args: None)

    assert find.cmd_remind('tool', brief=False, peek=False) == 0
    assert 'Nothing in the tool lens' in capsys.readouterr().out
