"""What the service reads from projects: the focus project with its open items, and what was completed over a window.

projects serves its items and which of them are blocked, and its CLI picks the
focus from them in `projects next`. This module picks it the same way, so the
lane shows what `projects next` prints: the highest-ranked active project
holding an open item, with that project's open items in the order they are
taken, split into next and blocked. A change to that rule in projects has to be
made here too.

The document is `projects next --json`'s shape. A failed read fails the whole
document, because the lane has nothing else to draw.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from doit.window import Bounds

from .apps import App

# Rows per list, matching what `projects next` shows.
FOCUS_LIMIT = 10


def instant(text: object) -> dt.datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def project_order(project: dict) -> tuple:
    """Projects by position, then creation, then id: the CLI's stable order."""
    position = project.get('position')
    return (
        position if isinstance(position, int) else 0,
        instant(project.get('created_at')) or dt.datetime.min.replace(tzinfo=dt.UTC),
        str(project.get('id', '')),
    )


def primary_project(item: dict) -> dict | None:
    """The highest-ranked active project an item is queued under, or None."""
    active = [project for project in item.get('projects') or [] if project.get('status') == 'active']
    return min(active, key=project_order) if active else None


def queue_position(item: dict, project_id: object) -> float:
    for membership in item.get('memberships') or []:
        if membership.get('project_id') == project_id:
            return membership.get('position', float('inf'))
    return float('inf')


def focus_queue(items: list[dict], blocked: list[dict]) -> dict:
    """The one project to work on and its open items split into next and blocked, as the CLI picks them."""
    blocked_ids = {item.get('id') for item in blocked}
    open_items = []
    focus = None
    for item in items:
        if item.get('completed') or item.get('archived'):
            continue
        project = primary_project(item)
        if project is None:
            continue
        if focus is None or project_order(project) < project_order(focus):
            focus = project
        open_items.append((item, project))
    if focus is None:
        return {'project': None, 'next': [], 'blocked': []}

    def taken(pair: tuple[dict, dict]) -> tuple:
        item, project = pair
        return (
            project_order(project),
            queue_position(item, project.get('id')),
            instant(item.get('created_at')) or dt.datetime.min.replace(tzinfo=dt.UTC),
            str(item.get('id', '')),
        )

    ordered = [item for item, project in sorted(open_items, key=taken) if project.get('id') == focus.get('id')]
    return {
        'project': focus,
        'next': [item for item in ordered if item.get('id') not in blocked_ids],
        'blocked': [item for item in ordered if item.get('id') in blocked_ids],
    }


def rows(answer: object) -> list[dict]:
    return [row for row in answer if isinstance(row, dict)] if isinstance(answer, list) else []


def focus(projects: App, limit: int = FOCUS_LIMIT) -> dict:
    """The focus project and its open items, as `projects next --json` answers."""
    queue = focus_queue(rows(projects.get('/project-items', {'status': 'open'})), rows(projects.get('/project-items/blocked')))
    return {
        'project': queue['project'],
        'next': queue['next'][:limit],
        'next_total': len(queue['next']),
        'blocked': queue['blocked'][:limit],
        'blocked_total': len(queue['blocked']),
    }


# What each projects completions source reads over a window: the items finished
# inside it. The CLI command it answers for is the adapter's own, in `doit.happened`.
COMPLETIONS: dict[str, Callable[[App, Bounds], object]] = {
    'projects-completed': lambda projects, bounds: projects.get(
        '/project-items', {'status': 'completed', 'since': bounds.start.isoformat(), 'until': bounds.stop.isoformat()}
    ),
}
