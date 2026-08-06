import pytest

from doit import __version__
from doit.cli import main


def test_version_flag_prints_the_version(capsys, monkeypatch):
    for flag in ('-V', '--version'):
        monkeypatch.setattr('sys.argv', ['doit', flag])
        assert main() == 0
        assert capsys.readouterr().out.strip() == f'doit {__version__}'


@pytest.mark.parametrize('argv', [[], ['help'], ['-h'], ['--help']])
def test_bare_and_help_invocations_print_usage(capsys, monkeypatch, argv):
    monkeypatch.setattr('sys.argv', ['doit', *argv])
    assert main() == 0
    assert 'doit review' in capsys.readouterr().out


def test_unknown_command_is_a_usage_error(capsys, monkeypatch):
    """Exit 2, not 0: a caller has to tell "you typed it wrong" from a real run.

    Collapsing it into 0 is worse than collapsing it into 1 — it tells a script
    the command succeeded.
    """
    monkeypatch.setattr('sys.argv', ['doit', 'nonsense'])

    assert main() == 2

    captured = capsys.readouterr()
    assert 'Unknown command: nonsense' in captured.err
    assert 'doit review' in captured.out, 'the usage that follows is still data'


def test_review_dispatches_to_its_own_tree(capsys, monkeypatch):
    monkeypatch.setattr('sys.argv', ['doit', 'review'])
    assert main() == 0
    assert 'doit review done' in capsys.readouterr().out
