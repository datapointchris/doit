"""Tests for the source registry and the lane contract.

The property that matters most is at the bottom: a source doit has never heard
of contributes a lane with no doit code at all. Everything else is the failure
policy around it.
"""

import json
from datetime import datetime

import pytest

from doit import lanes
from doit import sources

NOW = datetime(2026, 8, 6, 12, 0, 0)

CONFORMING = {
    'schema_version': 1,
    'lanes': [
        {
            'name': 'day',
            'title': 'DAY',
            'meta': 'evening · wind down',
            'rows': [{'label': 'now', 'text': 'Lights to evening scene', 'note': '18:40', 'urgency': 'due'}],
            'total': 2,
            'hints': ['day status'],
        }
    ],
}


def write_sources(tmp_path, body: str):
    path = tmp_path / 'sources.yml'
    path.write_text(body)
    return path


def test_a_source_doit_has_never_heard_of_needs_no_code():
    """The whole interface, in one assertion.

    A tool that prints a lane document appears on the dashboard with no adapter,
    no mapping and no release of doit.
    """
    source = sources.Source(id='day', command=['day', '--json'])
    result = sources.Result(source='day', payload=CONFORMING, exit_code=0)

    built = sources.lanes_from(source, result)

    assert [lane.name for lane in built] == ['day']
    assert built[0].rows[0].text == 'Lights to evening scene'
    assert built[0].rows[0].urgency is lanes.Urgency.DUE
    assert built[0].hints == ['day status']


def test_the_contract_wins_over_a_registered_adapter():
    """An app that starts speaking the contract is picked up the moment it does.

    Its adapter simply stops being reached, with nothing in doit changing.
    """
    sources.register_adapter('never', lambda _: [lanes.Lane(name='from-adapter', title='X')])
    source = sources.Source(id='day', command=['day'], adapter='never')

    built = sources.lanes_from(source, sources.Result(source='day', payload=CONFORMING, exit_code=0))

    assert [lane.name for lane in built] == ['day']


def test_a_source_may_be_restricted_to_some_of_its_lanes():
    source = sources.Source(id='day', command=['day'], lanes=('nothing-matching',))

    assert sources.lanes_from(source, sources.Result(source='day', payload=CONFORMING, exit_code=0)) == []


def test_a_declared_lane_survives_the_call_that_failed():
    """The silent drop, closed for conforming sources.

    A conforming source names its lanes only in its payload, so a failed call
    used to take the lane off the dashboard entirely — reading as "nothing
    outstanding" exactly when a backend is broken. Declaring `lanes` is the only
    statement of what should have been there, so it doubles as the fallback.
    """
    source = sources.Source(id='gh', command=['gh', 'search', 'prs'], lanes=('prs',))
    result = sources.Result(source='gh', exit_code=1, stderr='gh: could not authenticate', failure=sources.Failure.FAILED)

    built = sources.lanes_from(source, result)

    assert [lane.name for lane in built] == ['prs']
    assert built[0].available is False
    assert 'authenticate' in built[0].reason


def test_an_undeclared_lane_still_vanishes_when_its_source_fails():
    """Nothing was claimed, so there is nothing to report missing.

    doit cannot invent a name for a lane a source never said it had.
    """
    source = sources.Source(id='gh', command=['gh', 'search', 'prs'])
    result = sources.Result(source='gh', exit_code=1, stderr='boom', failure=sources.Failure.FAILED)

    assert sources.lanes_from(source, result) == []


def test_a_non_conforming_payload_without_an_adapter_yields_nothing():
    """Silence rather than a guess: doit does not know this app's model."""
    source = sources.Source(id='day', command=['day'])

    assert sources.lanes_from(source, sources.Result(source='day', payload={'stuff': []}, exit_code=0)) == []


def test_an_absent_file_configures_nothing():
    """A source that is not configured does not exist, and that is silent."""
    registry = sources.load(sources.SOURCES.parent / 'definitely-not-here.yml')

    assert registry.sources == {}
    assert registry.problems == []


def test_a_malformed_entry_is_reported_and_skipped(tmp_path):
    """One bad source must not cost you the dashboard."""
    path = write_sources(tmp_path, 'sources:\n  good:\n    command: [echo, hi]\n  bad:\n    timeout: 3\n')

    registry = sources.load(path)

    assert list(registry.sources) == ['good']
    assert any('bad' in problem for problem in registry.problems)


def test_a_string_command_is_accepted(tmp_path):
    """A one-line command is what someone writes first."""
    registry = sources.load(write_sources(tmp_path, 'sources:\n  day:\n    command: "day --json"\n'))

    assert registry.sources['day'].command == ['day', '--json']


def test_source_order_is_the_files_order(tmp_path):
    """Lane order follows sources.yml, which is what makes it yours to change."""
    body = 'sources:\n  b:\n    command: [b]\n  a:\n    command: [a]\n'

    assert list(sources.load(write_sources(tmp_path, body)).sources) == ['b', 'a']


def test_a_missing_binary_names_itself():
    result = sources.run(sources.Source(id='nope', command=['definitely-not-a-real-command']))

    assert result.failure is sources.Failure.NOT_INSTALLED
    assert sources.reason('nope', result) == 'nope is not installed on this machine'


def test_output_that_is_not_json_is_reported_as_such():
    result = sources.run(sources.Source(id='noise', command=['echo', 'not json']))

    assert result.failure is sources.Failure.UNREADABLE
    assert sources.reason('noise', result) == 'noise did not return JSON'


def test_a_timeout_reports_the_bound_it_actually_used():
    result = sources.run(sources.Source(id='slow', command=['sleep', '5'], timeout=0.25))

    assert result.failure is sources.Failure.TIMED_OUT
    assert sources.reason('slow', result) == 'slow timed out after 0.25s'


def test_a_failing_source_shows_its_first_stderr_line():
    result = sources.Result(
        source='icb',
        exit_code=1,
        stderr='error: not logged in — run `icb auth login`\nUsage:\n  icb overview [flags]',
        failure=sources.Failure.FAILED,
    )

    assert sources.reason('icb', result) == 'error: not logged in — run `icb auth login`'


@pytest.mark.parametrize('bad', [None, {'lanes': 'nope'}, [], 'text'])
def test_only_a_lane_document_is_read_as_one(bad):
    assert lanes.from_document(bad) == []


def test_a_malformed_lane_is_skipped_not_fatal():
    """A source mid-upgrade must not cost you the lanes either side of it."""
    payload = {'lanes': [{'name': 'good', 'rows': []}, {'no': 'name'}, 'not even a mapping']}

    assert [lane.name for lane in lanes.from_document(payload)] == ['good']


def test_a_lane_without_a_title_takes_its_name():
    assert lanes.from_document({'lanes': [{'name': 'day'}]})[0].title == 'DAY'


def test_an_unknown_urgency_falls_back_rather_than_raising():
    payload = {'lanes': [{'name': 'day', 'rows': [{'text': 'x', 'urgency': 'apocalyptic'}]}]}

    assert lanes.from_document(payload)[0].rows[0].urgency is lanes.Urgency.NONE


def test_what_doit_emits_is_what_doit_reads():
    """The contract is symmetric, which is why a source can copy doit's output."""
    original = [
        lanes.Lane(
            name='day',
            title='DAY',
            meta='2 planned',
            rows=[lanes.Row('now', 'Lights', '18:40', lanes.Urgency.OVERDUE, 'day set lights evening')],
            grid=[lanes.GridCell('Exercise', True, '')],
            total=2,
            hints=['day status'],
        )
    ]

    round_tripped = lanes.from_document(json.loads(lanes.dumps(original, NOW)))

    assert round_tripped == original
