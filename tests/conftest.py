"""Isolation from the machine running the suite.

The caches the CLI reads and writes go to the test's own directory. Autouse and
suite-wide rather than per-module: any test that ends up calling a command
inherits it whether or not its module knows it exists.
"""

import pytest

from doit import pursuits


@pytest.fixture(autouse=True)
def caches_in_the_test_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(pursuits, 'CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setattr(pursuits, 'NAMES_CACHE', tmp_path / 'cache' / 'next-names.txt')
