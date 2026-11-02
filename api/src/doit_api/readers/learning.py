"""What the service reads from learning: the overview its lane is drawn from, and what was finished over a window."""

from __future__ import annotations

from doit.window import Bounds

from .apps import App

# How many finished items one read returns, matching what `learning completed` shows, so a lane reads the same from either.
COMPLETED_LIMIT = 100


def overview(learning: App) -> object:
    return learning.get('/overview')


def completed(learning: App, bounds: Bounds) -> tuple[object, str]:
    """Everything finished inside the window, newest first, and the sentence the CLI writes when its limit cut the list.

    One row past the limit is asked for, as the CLI asks, so a list the limit
    cut can say so. The sentence is what marks the group partial.
    """
    rows = learning.get(
        '/completed',
        {'include_resources': True, 'since': bounds.start.isoformat(), 'until': bounds.stop.isoformat(), 'limit': COMPLETED_LIMIT + 1},
    )
    if isinstance(rows, list) and len(rows) > COMPLETED_LIMIT:
        return rows[:COMPLETED_LIMIT], f'{COMPLETED_LIMIT} newest completed item(s), cut by --limit; older ones exist.'
    return rows, ''
