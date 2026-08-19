"""Tests for doit.machine — what this box declares it installs.

Nothing here runs the real `dotfiles`. The subprocess is stubbed at
`query_dotfiles`, because what is under test is the fold from its JSON to a
`Declaration` and the fallback when there is no JSON to fold.

The distinction asserted hardest is unknown against empty. They are one value in
Python and two facts here, and reading the first as the second marks every
package-installed row as another machine's and empties the report.
"""

import json
import os
import time

import pytest

from doit import machine

RESOLVED = {
    'machine': 'archlinux-personal-workstation',
    'platform': 'archlinux',
    'coordinates': {'package_manager': 'pacman', 'os_family': 'linux'},
    'items': [
        {'section': 'go_tools', 'name': 'ripgrep', 'executable': 'rg'},
        {'section': 'go_tools', 'name': 'forge', 'executable': 'forge'},
        {'section': 'system_packages', 'name': 'aerc'},
    ],
}


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """A cache per test, so one test's write is never another's answer."""
    monkeypatch.setattr(machine, 'CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setattr(machine, 'MANIFEST_CACHE', tmp_path / 'cache' / 'machine-manifest.json')


def answering(payload, calls=None):
    def query():
        if calls is not None:
            calls.append(1)
        return payload

    return query


def test_the_command_is_taken_over_the_package_name(monkeypatch):
    """A registry row names `rg`, and the package it comes from is called ripgrep."""
    monkeypatch.setattr(machine, 'query_dotfiles', answering(RESOLVED))

    declared = machine.resolve_declaration()

    assert declared.declares('rg')
    assert not declared.declares('ripgrep')


def test_an_item_with_no_executable_falls_back_to_its_name(monkeypatch):
    monkeypatch.setattr(machine, 'query_dotfiles', answering(RESOLVED))

    assert machine.resolve_declaration().declares('aerc')


def test_the_package_manager_counts_as_declared(monkeypatch):
    """It is a coordinate rather than an item, so `brew` is on no manifest's list."""
    monkeypatch.setattr(machine, 'query_dotfiles', answering(RESOLVED))

    declared = machine.resolve_declaration()

    assert declared.declares('pacman')
    assert not declared.declares('brew')


def test_an_empty_name_declares_nothing(monkeypatch):
    """Otherwise a row whose invocation parsed to nothing would resolve against it."""
    monkeypatch.setattr(machine, 'query_dotfiles', answering(RESOLVED))

    assert not machine.resolve_declaration().declares('')


def test_a_box_that_cannot_be_asked_is_unknown_rather_than_empty(monkeypatch):
    """The one failure worse than the noise: every row filed as another machine's."""
    monkeypatch.setattr(machine, 'query_dotfiles', answering(None))

    declared = machine.resolve_declaration()

    assert declared == machine.UNKNOWN
    assert not declared.known


def test_a_machine_declaring_nothing_is_still_known(monkeypatch):
    """An empty manifest is an answer, and it is not the same as no answer."""
    monkeypatch.setattr(machine, 'query_dotfiles', answering({'items': [], 'coordinates': {}}))

    declared = machine.resolve_declaration()

    assert declared.known
    assert declared.executables == frozenset()


def test_a_fresh_cache_answers_without_asking_dotfiles(monkeypatch):
    calls = []
    monkeypatch.setattr(machine, 'query_dotfiles', answering(RESOLVED, calls))

    machine.resolve_declaration()
    machine.resolve_declaration()

    assert calls == [1], 'the second read came from the cache'


def test_a_stale_cache_is_re_read(monkeypatch):
    calls = []
    monkeypatch.setattr(machine, 'query_dotfiles', answering(RESOLVED, calls))
    machine.resolve_declaration()
    aged = time.time() - (machine.CACHE_HOURS + 1) * 3600
    os.utime(machine.MANIFEST_CACHE, (aged, aged))

    machine.resolve_declaration()

    assert calls == [1, 1]


def test_a_damaged_cache_reads_as_a_miss(monkeypatch):
    """It is derivable from one subprocess, so there is nothing worth failing over."""
    machine.CACHE_DIR.mkdir(parents=True)
    machine.MANIFEST_CACHE.write_text('{ not json')
    monkeypatch.setattr(machine, 'query_dotfiles', answering(RESOLVED))

    assert machine.resolve_declaration().declares('rg')


def test_a_query_that_fails_is_not_cached(monkeypatch):
    """Otherwise one offline moment would answer for a day."""
    monkeypatch.setattr(machine, 'query_dotfiles', answering(None))

    machine.resolve_declaration()

    assert not machine.MANIFEST_CACHE.exists()


def test_a_non_object_payload_is_no_answer(monkeypatch):
    monkeypatch.setattr(machine, 'query_dotfiles', answering(None))
    machine.CACHE_DIR.mkdir(parents=True)
    machine.MANIFEST_CACHE.write_text(json.dumps(['not', 'an', 'object']))

    assert machine.resolve_declaration() == machine.UNKNOWN
