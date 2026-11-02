"""What the service reads from slate: the issues closed over a window."""

from __future__ import annotations

from collections.abc import Callable

from doit.window import Bounds

from .apps import App


def issues_completed(slate: App, bounds: Bounds) -> object:
    """Every issue completed inside the window, as `slate issues list --status completed --json` prints it."""
    return slate.get('/issues', {'status': 'completed', 'since': bounds.start.isoformat(), 'until': bounds.stop.isoformat()})


# What each slate completions source reads over a window. The CLI command each
# twin answers for is the adapter's own, in `doit.happened`.
COMPLETIONS: dict[str, Callable[[App, Bounds], object]] = {
    'slate-issues': issues_completed,
}
