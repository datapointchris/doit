"""Cadence parsing and due-date math shared by the review register and labs.

A cadence token is ``<n><unit>`` — ``2w`` / ``1mo`` / ``10d`` / ``1y`` (a bare
number means days). The due date is always derived, never stored:
``next_due = last_done + cadence``. Storing only ``last_done`` means there is no
second date to keep in sync and nothing to drift.
"""

import datetime as dt
import re

CADENCE_UNITS = {'d': 1, 'w': 7, 'mo': 30, 'y': 365}
CADENCE_TOKEN = re.compile(r'(?P<count>\d+)(?P<unit>d|w|mo|y)?')


def parse_cadence(token: str) -> int:
    """Days for a cadence token like 2w / 1mo / 10d / 1y (bare number = days), 0 for anything else.

    The whole token has to match. Gathering its digits and letters separately
    read `3.5d` as 35 days and `1w2d` as 12, and a wrong number of days is
    indistinguishable from a right one everywhere downstream. Zero is what every
    caller already refuses or reads as having no cadence.
    """
    found = CADENCE_TOKEN.fullmatch(str(token).strip())
    if found is None:
        return 0
    return int(found['count']) * CADENCE_UNITS[found['unit'] or 'd']


def overdue_days(last: str | None, cadence: str, today: dt.date) -> int | None:
    """Days past due for an item, or None if it has never been done.

    Negative means not yet due. ``None`` (never done) is treated by callers as the
    most urgent state, sorting above everything with a real due date.
    """
    if not last:
        return None
    next_due = dt.date.fromisoformat(last) + dt.timedelta(days=parse_cadence(cadence))
    return (today - next_due).days


def is_due(overdue: int | None) -> bool:
    """Due when never done (None) or on/past the due date (overdue >= 0)."""
    return overdue is None or overdue >= 0


def status_label(overdue: int | None) -> str:
    """Human-readable status for an overdue value from :func:`overdue_days`."""
    if overdue is None:
        return 'never done'
    if overdue > 0:
        return f'overdue {overdue}d'
    if overdue == 0:
        return 'due today'
    return f'in {-overdue}d'
