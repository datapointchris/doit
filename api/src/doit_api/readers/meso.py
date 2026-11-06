"""What the service reads from meso: the cycles and the open feedback its lanes are drawn from, and the training done over a window."""

from __future__ import annotations

from doit.window import Bounds

from .apps import App


def cycles(meso: App) -> object:
    return meso.get('/cycles')


def open_feedback(meso: App) -> object:
    return meso.get('/feedback', {'status': 'open'})


def review(meso: App, bounds: Bounds) -> object:
    return meso.get('/review', {'since': bounds.start.isoformat(), 'until': bounds.stop.isoformat()})
