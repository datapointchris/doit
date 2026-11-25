"""Tests for the version doit-python reports."""

from importlib.metadata import PackageNotFoundError

import doit


def test_version_is_unknown_without_installed_metadata(monkeypatch):
    """A dev checkout says so rather than inventing a number."""

    def missing(_name):
        raise PackageNotFoundError

    monkeypatch.setattr(doit, 'installed_version', missing)

    assert doit.tool_version() == 'unknown'
