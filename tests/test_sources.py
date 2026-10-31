"""Tests for the source registry and the lane contract.

The property that matters most is at the bottom: a source doit has never heard
of contributes a lane with no doit code at all. Everything else is the failure
policy around it.
"""

import datetime as dt
import json

import pytest
from typer.testing import CliRunner

from doit import lanes
from doit import sources
from doit.window import Bounds

NOW = dt.datetime(2026, 8, 6, 12, 0, 0)

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
    sources.register_adapter('never', ('day',), lambda _: [lanes.Lane(name='from-adapter', title='X')])
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


def test_a_declared_lane_whose_adapter_this_doit_lacks_names_the_adapter():
    """The call succeeded, so the reason is the only thing saying what is wrong.

    sources.yml and the installed doit disagree about an adapter's name across
    any rename, on every machine, until both have moved.
    """
    source = sources.Source(id='problems', command=['fleet'], adapter='no-such-adapter', lanes=('problems',))

    built = sources.lanes_from(source, sources.Result(source='problems', payload=[], exit_code=0))

    assert [lane.name for lane in built] == ['problems']
    assert built[0].available is False
    assert 'no-such-adapter' in built[0].reason


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


def test_both_blocks_parse_into_the_same_source(tmp_path):
    """An entry means the same thing in either block.

    Which question it answers is the block it sits in and nothing else, so one
    parser, one runner and one failure policy serve both.
    """
    path = write_sources(
        tmp_path,
        'sources:\n  day:\n    command: [day, --json]\ncompletions:\n  done:\n    command: [done, --json]\n    timeout: 9\n',
    )

    registry = sources.load(path)

    assert list(registry.sources) == ['day']
    assert list(registry.completions) == ['done']
    assert registry.completions['done'] == sources.Source(id='done', command=['done', '--json'], timeout=9.0)


def test_an_adapted_entry_runs_its_adapters_command_and_a_restated_one_is_reported(tmp_path):
    """The command belongs to the adapter, so no copy in sources.yml can drift from it.

    A restated command is ignored rather than the entry dropped, so the lane
    keeps answering while the line waits to be deleted.
    """
    sources.register_adapter('owned', ('owned', '--json'), lambda _: [])
    body = 'sources:\n  quiet:\n    adapter: owned\n  stale:\n    adapter: owned\n    command: [owned, --old-flag]\n'

    registry = sources.load(write_sources(tmp_path, body))

    assert registry.sources['quiet'].command == ['owned', '--json']
    assert registry.sources['stale'].command == ['owned', '--json']
    assert registry.problems == ['stale: the owned adapter supplies its own command — delete `command`']


def test_a_file_with_no_completions_block_is_not_a_problem(tmp_path):
    """Every file already on disk predates the block.

    A machine that never adds one gets `doit today` without its count rows
    rather than a warning it cannot act on.
    """
    registry = sources.load(write_sources(tmp_path, 'sources:\n  icb:\n    command: [icb, overview, --json]\n'))

    assert registry.completions == {}
    assert registry.problems == []


def test_a_malformed_completions_entry_is_reported_and_skipped(tmp_path):
    """The same policy as a malformed source, because it is the same parser."""
    path = write_sources(
        tmp_path,
        'completions:\n  good:\n    command: [echo, hi]\n  bad:\n    adapter: nope\n',
    )

    registry = sources.load(path)

    assert list(registry.completions) == ['good']
    assert any('bad' in problem for problem in registry.problems)


def test_one_id_in_both_blocks_is_reported_and_the_completion_dropped(tmp_path):
    """`fetch` keys its results by id, so two commands under one id collide.

    One result is then read by both blocks and the lane the loser would have
    built disappears with nothing said — a habits lane gone from the screen
    while `doit sources list` prints both entries as if they ran. Dropping the
    completion keeps the dashboard whole, which is the half that has no
    workaround.
    """
    path = write_sources(
        tmp_path,
        'sources:\n  meso:\n    command: [meso, overview, --json]\n    adapter: meso\n'
        'completions:\n  meso:\n    command: [meso, review, --json]\n    adapter: meso-review\n',
    )

    registry = sources.load(path)

    assert list(registry.sources) == ['meso']
    assert registry.completions == {}
    assert any('both' in problem and 'meso' in problem for problem in registry.problems)


def test_a_block_that_is_not_a_mapping_names_itself(tmp_path):
    """Two blocks means the message has to say which one was wrong."""
    registry = sources.load(write_sources(tmp_path, 'completions: [icb, tasks]\n'))

    assert any('completions' in problem for problem in registry.problems)


def test_the_window_ends_replace_whole_argv_parts_and_nothing_else():
    """Equality rather than a substring replace, so a path or a title containing a token passes through untouched."""
    zone = dt.timezone(dt.timedelta(hours=-4))
    bounds = Bounds(dt.datetime(2026, 8, 6, 15, 0, tzinfo=zone), dt.datetime(2026, 8, 7, tzinfo=zone))

    resolved = sources.resolved_command(['grep', 'due {since}', '--since', '{since}', '--until', '{until}'], bounds)

    assert resolved == ['grep', 'due {since}', '--since', '2026-08-06T15:00:00-04:00', '--until', '2026-08-07T00:00:00-04:00']


def test_source_order_is_the_files_order(tmp_path):
    """Lane order follows sources.yml, which is what makes it yours to change."""
    body = 'sources:\n  b:\n    command: [b]\n  a:\n    command: [a]\n'

    assert list(sources.load(write_sources(tmp_path, body)).sources) == ['b', 'a']


def test_a_missing_binary_names_itself():
    result = sources.fetch([sources.Source(id='nope', command=['definitely-not-a-real-command'])])['nope']

    assert result.failure is sources.Failure.NOT_INSTALLED
    assert sources.reason('nope', result) == 'nope is not installed on this machine'


def test_output_that_is_not_json_is_reported_as_such():
    result = sources.fetch([sources.Source(id='noise', command=['echo', 'not json'])])['noise']

    assert result.failure is sources.Failure.UNREADABLE
    assert sources.reason('noise', result) == 'noise did not return JSON'


def test_an_adapter_brings_its_own_timeout_and_the_file_may_still_set_one(tmp_path, monkeypatch):
    """A caller's limit below the tool's own budget cuts the tool off while it still counts itself healthy."""
    monkeypatch.setitem(sources.ADAPTERS, 'patient', sources.Adapter(('patient',), lambda result: [], 20.0))
    body = 'sources:\n  own:\n    adapter: patient\n  set:\n    adapter: patient\n    timeout: 3\n'

    registry = sources.load(write_sources(tmp_path, body))

    assert (registry.sources['own'].timeout, registry.sources['set'].timeout) == (20.0, 3.0)


def test_a_timeout_reports_the_bound_it_actually_used():
    result = sources.fetch([sources.Source(id='slow', command=['sleep', '5'], timeout=0.25)])['slow']

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


def test_list_exits_3_and_quotes_the_tool_when_it_rejects_a_command(tmp_path, monkeypatch):
    """A rejected command waits on a person, so a scheduled `list` fails on it.

    The reason is the tool's own error under the program's name rather than the
    source id. Either the binary or the command can be the stale side, and only
    the tool's words say which flag it refused.
    """
    body = 'completions:\n  stale:\n    command: [sh, -c, "echo \'Error: unknown flag: --start\' >&2; exit 2"]\n'
    monkeypatch.setattr(sources, 'SOURCES', write_sources(tmp_path, body))

    result = CliRunner().invoke(sources.app, ['list', '--json'])

    assert result.exit_code == 3
    [row] = json.loads(result.stdout)
    assert row['reason'] == 'sh: unknown flag: --start'


def test_list_json_carries_each_sources_block_and_verdict(tmp_path, monkeypatch):
    body = (
        'sources:\n'
        f'  day:\n    command: [echo, {json.dumps(json.dumps(CONFORMING))}]\n'
        'completions:\n'
        '  gone:\n    command: [definitely-not-a-real-command]\n'
    )
    monkeypatch.setattr(sources, 'SOURCES', write_sources(tmp_path, body))

    result = CliRunner().invoke(sources.app, ['list', '--json'])

    assert result.exit_code == 0
    rows = {row['id']: row for row in json.loads(result.stdout)}
    assert rows['day']['block'] == 'outstanding'
    assert (rows['day']['answered'], rows['day']['lanes'], rows['day']['via']) == (True, 1, 'contract')
    assert rows['gone']['block'] == 'happened'
    assert (rows['gone']['answered'], rows['gone']['failure'], rows['gone']['lanes']) == (False, 'not-installed', 0)


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


@pytest.mark.parametrize('stamp', [NOW, NOW.replace(tzinfo=dt.timezone(dt.timedelta(hours=9)))])
def test_the_document_stamp_always_carries_an_offset(stamp):
    """A time with no offset cannot be compared across two machines.

    Coerced in `to_document` rather than at each caller, because the commands
    emitting this document do not all hold an aware datetime and this file is
    what anyone building a source copies.
    """
    document = json.loads(lanes.dumps([], stamp))

    assert dt.datetime.fromisoformat(document['generated_at']).utcoffset() is not None
