"""Cadence parsing and due-date math shared by the review register and labs.

A cadence token is ``<n><unit>`` — ``2w`` / ``1mo`` / ``10d`` / ``1y`` (a bare
number means days). The due date is always derived, never stored:
``next_due = last_done + cadence``. Storing only ``last_done`` means there is no
second date to keep in sync and nothing to drift.
"""

from datetime import date
from datetime import timedelta

CADENCE_UNITS = {'d': 1, 'w': 7, 'mo': 30, 'y': 365}


def parse_cadence(token: str) -> int:
    """Days for a cadence token like 2w / 1mo / 10d / 1y (bare number = days)."""
    token = str(token).strip()
    num = ''.join(c for c in token if c.isdigit())
    unit = ''.join(c for c in token if c.isalpha())
    if not num:
        return 0
    return int(num) * CADENCE_UNITS.get(unit, 1)


def overdue_days(last: str | None, cadence: str, today: date) -> int | None:
    """Days past due for an item, or None if it has never been done.

    Negative means not yet due. ``None`` (never done) is treated by callers as the
    most urgent state, sorting above everything with a real due date.
    """
    if not last:
        return None
    next_due = date.fromisoformat(last) + timedelta(days=parse_cadence(cadence))
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
