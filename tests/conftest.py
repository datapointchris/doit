"""Isolation from the machine running the suite.

A command that asks the service would call whatever deployment this machine is
logged in to, so every test is refused one unless it hands its own in. The caches
the CLI writes go to the test's own directory.

Autouse and suite-wide rather than per-module: any test that ends up calling a
command inherits the reach whether or not its module knows it exists.
"""

import pytest

from doit import pursuits
from doit import service
from doit.service_commands import review as review_commands


@pytest.fixture(autouse=True)
def no_service(monkeypatch, tmp_path):
    """Refuse the service unless a test hands one in, and cache into this test's own directory."""

    def refused() -> service.Service:
        raise AssertionError('this test asked the doit service without handing one in')

    monkeypatch.setattr(service, 'client', refused)
    monkeypatch.setattr(pursuits, 'CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setattr(pursuits, 'NAMES_CACHE', tmp_path / 'cache' / 'next-names.txt')
    monkeypatch.setattr(review_commands, 'NUDGE_INTERVAL', tmp_path / 'cache' / 'nudge-interval-minutes')
