import pytest

from doit import __version__
from doit.cli import main


def test_version_flag_prints_the_version(capsys, monkeypatch):
    for flag in ('-V', '--version'):
        monkeypatch.setattr('sys.argv', ['doit', flag])
        assert main() == 0
        assert capsys.readouterr().out.strip() == f'doit {__version__}'


@pytest.mark.parametrize('argv', [[], ['nonsense']])
def test_bare_and_unknown_invocations_print_usage(capsys, monkeypatch, argv):
    monkeypatch.setattr('sys.argv', ['doit', *argv])
    assert main() == 0
    assert 'doit' in capsys.readouterr().out
