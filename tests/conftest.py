"""Isolation from the machine running the suite.

The zsh lens reaches out to the live keymap, so a fixture's rows would depend on
whoever is running the suite. That failure would be invisible here and
reproducible only on one machine, which is the worst shape a test failure comes
in.

The pursuits register, journal and evidence cache are the one reach that writes.
A live build journals first sightings and pace changes for whatever register it
reads, so a test drawing the dashboard would append to this desk's real journal.

Autouse and suite-wide rather than per-module: the reaches are
`index.zsh_bindkeys` and the pursuits paths, and any test that ends up calling
`build_index` or `build_state` inherits them whether or not its module knows
those exist.
"""

import pytest

from doit import index
from doit import paths
from doit import pursuits


@pytest.fixture(autouse=True)
def isolated_zsh_keymap(monkeypatch):
    """Answer the zsh lens with an empty keymap unless a test supplies its own.

    The lens asks the live shell, because that is the only place a plugin's
    bindings exist. Left alone, the rows would then be whatever the person
    running the suite happens to have bound — and on CI, which has no zsh config
    at all, the collection would empty without anything failing.

    The cache is cleared before the stub goes in rather than after: the stub
    replaces the cached function outright, so there is nothing left to clear on
    the way out.
    """
    index.zsh_bindkeys.cache_clear()
    monkeypatch.setattr(index, 'zsh_bindkeys', lambda interactive: '')


@pytest.fixture(autouse=True)
def empty_pursuits(monkeypatch, tmp_path):
    """Read no register, and journal and cache into this test's own directory.

    A missing register loads as empty, so nothing builds unless a test writes
    one. A test that does gets a journal nobody else reads.
    """
    monkeypatch.setattr(pursuits, 'REGISTER', tmp_path / 'no-pursuits.yml')
    monkeypatch.setattr(paths, 'JOURNAL_DIR', tmp_path / 'journal')
    monkeypatch.setattr(pursuits, 'CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setattr(pursuits, 'DRAW_CACHE', tmp_path / 'cache' / 'next-draw.json')
    monkeypatch.setattr(pursuits, 'NAMES_CACHE', tmp_path / 'cache' / 'next-names.txt')
