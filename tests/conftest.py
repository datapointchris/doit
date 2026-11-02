"""Isolation from the machine running the suite.

The zsh lens reaches out to the live keymap, so a fixture's rows would depend on
whoever is running the suite. That failure would be invisible here and
reproducible only on one machine, which is the worst shape a test failure comes
in.

The service is the other reach. A command that asks it would call whatever
deployment this machine is logged in to, so every test is refused one unless it
hands its own in. The caches the CLI writes go to the test's own directory.

Autouse and suite-wide rather than per-module: any test that ends up calling
`build_index` or a command inherits the reach whether or not its module knows
it exists.
"""

import pytest

from doit import index
from doit import pursuits
from doit import service
from doit.service_commands import review as review_commands


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
def no_service(monkeypatch, tmp_path):
    """Refuse the service unless a test hands one in, and cache into this test's own directory."""

    def refused() -> service.Service:
        raise AssertionError('this test asked the doit service without handing one in')

    monkeypatch.setattr(service, 'client', refused)
    monkeypatch.setattr(pursuits, 'CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setattr(pursuits, 'NAMES_CACHE', tmp_path / 'cache' / 'next-names.txt')
    monkeypatch.setattr(review_commands, 'NUDGE_INTERVAL', tmp_path / 'cache' / 'nudge-interval-minutes')
