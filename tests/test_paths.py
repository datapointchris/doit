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


def test_machine_name_is_the_bare_lowercased_host(monkeypatch):
    monkeypatch.setattr(paths.socket, 'gethostname', lambda: 'Macmini.local')
    assert paths.machine_name() == 'macmini'


def test_machine_name_without_a_domain(monkeypatch):
    monkeypatch.setattr(paths.socket, 'gethostname', lambda: 'archlinux')
    assert paths.machine_name() == 'archlinux'
