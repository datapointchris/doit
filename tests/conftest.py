"""Isolation from the machine running the suite.

``doit.observe`` derives an item's last-done from real shell history, so a
fixture register whose ``command`` someone had actually typed would change the
schedule under test. That failure would be invisible here and reproducible only
on the machine that had typed it, which is the worst shape a test failure comes
in — so no test reads the real history file unless it writes one itself.

The zsh lens reaches out the same way, to the live keymap, and is isolated here
for the same reason.

Autouse and suite-wide rather than per-module: the reach is `observe.HISTORY` and
`index.zsh_bindkeys`, and any test that ends up calling `review.statuses` or
`build_index` inherits it whether or not its module knows those exist.
"""

import pytest

from doit import index
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
