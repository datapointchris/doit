"""Isolation from the machine running the suite.

``doit.observe`` derives an item's last-done from real shell history, so a
fixture register whose ``command`` someone had actually typed would change the
schedule under test. That failure would be invisible here and reproducible only
on the machine that had typed it, which is the worst shape a test failure comes
in — so no test reads the real history file unless it writes one itself.

Autouse and suite-wide rather than per-module: the reach is `observe.HISTORY`,
and any test that ends up calling `review.statuses` inherits it whether or not
its module knows observation exists.
"""

import pytest

from doit import observe


@pytest.fixture(autouse=True)
def isolated_shell_history(monkeypatch, tmp_path):
    """Cut history observation off from both real sources.

    atuin is stubbed out rather than pointed somewhere harmless: it is a real
    binary holding this machine's real history, and `history_entries` prefers it,
    so without this every test asking about a fixture command silently answers
    from whoever is running the suite. A test that wants the atuin path calls
    `observe.atuin_invocations` directly.

    The read is cached per process, so the cache is cleared on both sides: a test
    writing its own history must neither inherit the previous test's nor leave
    its own behind.
    """
    monkeypatch.setattr(observe, 'HISTORY', tmp_path / 'no-shell-history')
    monkeypatch.setattr(observe, 'atuin_invocations', lambda: ())
    observe.history_entries.cache_clear()
    yield
    observe.history_entries.cache_clear()
