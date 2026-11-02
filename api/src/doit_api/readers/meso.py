"""What the service reads from meso: the cycles its lane is drawn from, and the training done over a window."""

from __future__ import annotations

from doit.window import Bounds

from .apps import App


def cycles(meso: App) -> object:
    return meso.get('/cycles')


def review(meso: App, bounds: Bounds) -> object:
    return meso.get('/review', {'since': bounds.start.isoformat(), 'until': bounds.stop.isoformat()})
