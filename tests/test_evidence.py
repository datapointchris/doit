"""Tests for the evidence channel.

The property that matters is the first one: a pursuit satisfied in its own app
counts as done with nothing typed into doit. Everything else is the failure
policy around it, and the failure policy is the part that decides whether the
draw degrades or lies — a backend that cannot be reached must fall back to the
journal, never silently report "never done".
"""

import json
import shlex
import sys
from datetime import datetime
from datetime import timedelta

from doit import evidence

NOW = datetime(2026, 8, 16, 12, 0, 0).astimezone()


def emitting(document) -> str:
    """A command printing one JSON document, with no shell involved."""
    code = f'import json,sys; sys.stdout.write(json.dumps({document!r}))'
    return f'{shlex.quote(sys.executable)} -c {shlex.quote(code)}'


def failing(message: str = 'not logged in') -> str:
    code = f'import sys; sys.stderr.write({message!r}); sys.exit(1)'
    return f'{shlex.quote(sys.executable)} -c {shlex.quote(code)}'


def test_an_app_that_saw_it_counts_as_done_with_nothing_typed(tmp_path):
    """The whole point, in one assertion.

    Nothing was logged by hand. The app answered, so the pursuit has a last-done.
    """
    yesterday = (NOW - timedelta(days=1)).isoformat()
    pursuits = {
        'tasks': {
            'weight': 25,
            'evidence': emitting([{'complete_date': yesterday}]),
            'evidence_time': 'complete_date',
        }
    }
    payload = evidence.refresh(pursuits, tmp_path, NOW)
    seen = evidence.observed(payload)
    assert 'tasks' in seen
    assert evidence.merged({}, seen)['tasks'] == seen['tasks']


def test_the_later_of_typed_and_observed_wins():
    typed = {'tasks': NOW - timedelta(days=5), 'connect': NOW - timedelta(days=2)}
    seen = {'tasks': NOW - timedelta(days=1)}
    merged = evidence.merged(typed, seen)
    assert merged['tasks'] == NOW - timedelta(days=1), 'the app saw it more recently'
    assert merged['connect'] == NOW - timedelta(days=2), 'no app sees connect, so the log stands'


def test_a_row_filter_picks_one_habit_out_of_the_call(tmp_path):
    """Twenty habits come back from one call and one of them is the pursuit."""
    rows = [
        {'name': 'Read', 'complete_date': (NOW - timedelta(days=1)).isoformat()},
        {'name': 'Yoga', 'complete_date': NOW.isoformat()},
    ]
    pursuits = {
        'read': {
            'weight': 25,
            'evidence': emitting(rows),
            'evidence_time': 'complete_date',
            'evidence_where': {'name': 'Read'},
        }
    }
    seen = evidence.observed(evidence.refresh(pursuits, tmp_path, NOW))
    assert seen['read'] == NOW - timedelta(days=1), 'Yoga today must not satisfy the reading pursuit'


def test_the_most_recent_row_wins_not_the_first():
    rows = [
        {'at': (NOW - timedelta(days=9)).isoformat()},
        {'at': (NOW - timedelta(days=2)).isoformat()},
        {'at': (NOW - timedelta(days=6)).isoformat()},
    ]
    found = evidence.latest_in(rows, {'evidence_time': 'at'}, NOW)
    assert found == NOW - timedelta(days=2)


def test_a_backend_answering_without_an_offset_is_still_comparable():
    """A naive stamp compared against an aware now raises rather than sorting."""
    rows = [{'at': '2026-08-15T09:30:00'}]
    found = evidence.latest_in(rows, {'evidence_time': 'at'}, NOW)
    assert found is not None
    assert (NOW - found).days == 1


def test_nested_rows_and_a_lone_object_both_read():
    nested = {'sessions': [{'at': NOW.isoformat()}]}
    assert evidence.latest_in(nested, {'evidence_time': 'at', 'evidence_items': 'sessions'}, NOW) == NOW
    assert evidence.latest_in({'at': NOW.isoformat()}, {'evidence_time': 'at'}, NOW) == NOW


def test_a_failed_read_keeps_the_previous_answer_and_says_why(tmp_path):
    """A logged-out CLI degrades that pursuit to its journal, not to a wrong zero."""
    yesterday = (NOW - timedelta(days=1)).isoformat()
    working = {'train': {'weight': 25, 'evidence': emitting([{'at': yesterday}]), 'evidence_time': 'at'}}
    evidence.refresh(working, tmp_path, NOW)

    broken = {'train': {'weight': 25, 'evidence': failing(), 'evidence_time': 'at'}}
    payload = evidence.refresh(broken, tmp_path, NOW, force=True)

    assert evidence.observed(payload)['train'] == datetime.fromisoformat(yesterday)
    assert 'not logged in' in evidence.problems(payload)['train']


def test_output_that_is_not_json_is_an_error_rather_than_a_crash(tmp_path):
    pursuits = {'pr': {'weight': 25, 'evidence': f'{shlex.quote(sys.executable)} --version', 'evidence_time': 'at'}}
    payload = evidence.refresh(pursuits, tmp_path, NOW, force=True)
    assert 'pr' in evidence.problems(payload)
    assert evidence.observed(payload) == {}


def test_a_fresh_answer_is_not_asked_for_again(tmp_path):
    pursuits = {'tasks': {'weight': 25, 'evidence': emitting([{'at': NOW.isoformat()}]), 'evidence_time': 'at'}}
    evidence.refresh(pursuits, tmp_path, NOW)

    pursuits['tasks']['evidence'] = failing('should not run')
    payload = evidence.refresh(pursuits, tmp_path, NOW + timedelta(seconds=60))
    assert evidence.problems(payload) == {}, 'inside the TTL the app is left alone'


def test_an_aged_out_answer_is_asked_for_again(tmp_path):
    pursuits = {'tasks': {'weight': 25, 'evidence': emitting([{'at': NOW.isoformat()}]), 'evidence_time': 'at'}}
    evidence.refresh(pursuits, tmp_path, NOW)

    pursuits['tasks']['evidence'] = failing('asked again')
    later = NOW + timedelta(seconds=evidence.REFRESH_TTL_SECONDS + 1)
    payload = evidence.refresh(pursuits, tmp_path, later)
    assert 'asked again' in evidence.problems(payload)['tasks']


def test_dropping_evidence_from_a_pursuit_drops_its_answer(tmp_path):
    """A weight edit must not leave an old app's verdict alive."""
    pursuits = {'tasks': {'weight': 25, 'evidence': emitting([{'at': NOW.isoformat()}]), 'evidence_time': 'at'}}
    evidence.refresh(pursuits, tmp_path, NOW)

    payload = evidence.refresh({'tasks': {'weight': 25}}, tmp_path, NOW, force=True)
    assert evidence.observed(payload) == {}


def test_a_command_without_a_time_field_is_not_evidence():
    assert evidence.declared({'tasks': {'evidence': 'icb tasks list'}}) == {}
    assert evidence.declared({'tasks': {'evidence_time': 'complete_date'}}) == {}


def test_an_unreadable_cache_costs_a_refresh_and_nothing_else(tmp_path):
    evidence.cache_path(tmp_path).write_text('{ not json')
    assert evidence.load(tmp_path) == {'schema_version': evidence.SCHEMA_VERSION, 'pursuits': {}}


def test_the_cache_round_trips(tmp_path):
    pursuits = {'tasks': {'weight': 25, 'evidence': emitting([{'at': NOW.isoformat()}]), 'evidence_time': 'at'}}
    evidence.refresh(pursuits, tmp_path, NOW)
    written = json.loads(evidence.cache_path(tmp_path).read_text())
    assert written['schema_version'] == evidence.SCHEMA_VERSION
    assert written['pursuits']['tasks']['last']
