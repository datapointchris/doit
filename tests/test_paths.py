"""Tests for doit.paths — XDG resolution and this machine's identity.

These arrived from dotfiles untested. The empty-string case is the one worth
holding: the module reads ``os.environ.get(VAR) or default`` rather than
``os.environ.get(VAR, default)``, because a variable exported as empty is the
shape a partially-initialised shell actually produces, and the two-argument form
would resolve every path to the filesystem root.
"""

from pathlib import Path

import pytest

from doit import paths

RESOLVERS = [
    ('XDG_CONFIG_HOME', paths.xdg_config_home, Path('.config')),
    ('XDG_DATA_HOME', paths.xdg_data_home, Path('.local/share')),
    ('XDG_STATE_HOME', paths.xdg_state_home, Path('.local/state')),
    ('XDG_CACHE_HOME', paths.xdg_cache_home, Path('.cache')),
]


@pytest.mark.parametrize(('var', 'resolve', 'default'), RESOLVERS)
def test_env_var_wins_when_set(monkeypatch, tmp_path, var, resolve, default):
    monkeypatch.setenv(var, str(tmp_path / 'elsewhere'))
    assert resolve() == tmp_path / 'elsewhere'


@pytest.mark.parametrize(('var', 'resolve', 'default'), RESOLVERS)
def test_spec_default_when_unset(monkeypatch, var, resolve, default):
    monkeypatch.delenv(var, raising=False)
    assert resolve() == Path.home() / default


@pytest.mark.parametrize(('var', 'resolve', 'default'), RESOLVERS)
def test_spec_default_when_set_but_empty(monkeypatch, var, resolve, default):
    monkeypatch.setenv(var, '')
    assert resolve() == Path.home() / default


def write_config(config_home, body: str) -> None:
    directory = config_home / 'doit'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'config.toml').write_text(body)


# Asserted rung by rung, each winning over every rung below it. The rungs are
# indistinguishable from their result — every one yields a path — so a
# reordering is invisible to a test that only checks a path came back.
def test_the_variable_beats_the_config_and_the_default(monkeypatch, tmp_path):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    write_config(tmp_path / 'config', 'library_dir = "/from/config"\n')
    monkeypatch.setenv('DOIT_LIBRARY_DIR', '/from/the/shell')
    assert paths.library_dir() == Path('/from/the/shell')
    assert paths.library_source() == '$DOIT_LIBRARY_DIR'


def test_the_config_beats_the_default(monkeypatch, tmp_path):
    monkeypatch.delenv('DOIT_LIBRARY_DIR', raising=False)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    write_config(tmp_path / 'config', 'library_dir = "/from/config"\n')
    assert paths.library_dir() == Path('/from/config')
    assert paths.library_source() == str(tmp_path / 'config' / 'doit' / 'config.toml')


def test_the_default_answers_a_machine_that_says_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv('DOIT_LIBRARY_DIR', raising=False)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    assert paths.library_dir() == tmp_path / 'data' / 'terminal-library'
    assert paths.library_source() == 'default'


def test_a_variable_set_but_empty_is_not_an_answer(monkeypatch, tmp_path):
    monkeypatch.setenv('DOIT_LIBRARY_DIR', '')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    assert paths.library_dir() == tmp_path / 'data' / 'terminal-library'


def test_a_tilde_in_the_config_resolves(monkeypatch, tmp_path):
    monkeypatch.delenv('DOIT_LIBRARY_DIR', raising=False)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    write_config(tmp_path / 'config', 'library_dir = "~/elsewhere/library"\n')
    assert paths.library_dir() == Path.home() / 'elsewhere' / 'library'


def test_malformed_config_falls_through_rather_than_breaking_the_run(monkeypatch, tmp_path):
    """A machine keeping the library where doit expects it must still work."""
    monkeypatch.delenv('DOIT_LIBRARY_DIR', raising=False)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    write_config(tmp_path / 'config', 'library_dir = [this is not toml\n')
    assert paths.library_dir() == tmp_path / 'data' / 'terminal-library'


def test_machine_name_is_the_bare_lowercased_host(monkeypatch):
    monkeypatch.setattr(paths.socket, 'gethostname', lambda: 'Macmini.local')
    assert paths.machine_name() == 'macmini'


def test_machine_name_without_a_domain(monkeypatch):
    monkeypatch.setattr(paths.socket, 'gethostname', lambda: 'archlinux')
    assert paths.machine_name() == 'archlinux'
