"""What the service reads from nomad: the overview its planned trips are drawn from."""

from __future__ import annotations

from .apps import App


def overview(nomad: App) -> object:
    return nomad.get('/overview')
