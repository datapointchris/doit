"""Tests for the source registry, the lane contract and the failure policy around them."""

import datetime as dt
import json

import pytest
import yaml

from doit import dashboard
from doit import lanes
from doit import sources

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


def parsed(body: str) -> sources.Registry:
    return sources.registry_from_document(yaml.safe_load(body) or {})


def test_the_contract_wins_over_a_registered_adapter():
    """An app that starts speaking the contract is picked up the moment it does.

    Its adapter simply stops being reached, with nothing in doit changing.
    """
    sources.register_adapter('never', lambda _: [lanes.Lane(name='from-adapter', title='X')])
    source = sources.Source(id='day', adapter='never')

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
    source = sources.Source(id='trips', adapter='no-such-adapter', lanes=('trips',))

    built = sources.lanes_from(source, sources.Result(source='trips', payload=[], exit_code=0))

    assert [lane.name for lane in built] == ['trips']
    assert built[0].available is False
    assert 'no-such-adapter' in built[0].reason


def test_a_failed_source_that_declared_no_lanes_is_one_unavailable_lane_under_its_id():
    """A source never said which lanes it had, so its id is the only name the dashboard can give the failure."""
    registry = parsed('sources:\n  gh:\n    command: [gh, search, prs]\n')
    result = sources.Result(source='gh', stderr='boom', failure=sources.Failure.FAILED)

    [lane] = dashboard.source_lanes(registry, {'gh': result})

    assert (lane.name, lane.available, lane.reason) == ('gh', False, 'boom')


def test_an_empty_document_configures_nothing():
    """A source that is not configured does not exist, and that is silent."""
    registry = parsed('')

    assert registry.sources == {}
    assert registry.problems == []


def test_a_malformed_entry_is_reported_and_skipped():
    """One bad source must not cost you the dashboard."""
    registry = parsed('sources:\n  good:\n    command: [echo, hi]\n  bad:\n    lanes: [bad]\n')

    assert list(registry.sources) == ['good']
    assert any('bad' in problem for problem in registry.problems)


def test_a_string_command_is_accepted():
    """A one-line command is what someone writes first."""
    registry = parsed('sources:\n  day:\n    command: "day --json"\n')

    assert registry.sources['day'].command == ['day', '--json']


def test_both_blocks_parse_into_the_same_source():
    """An entry means the same thing in either block.

    Which question it answers is the block it sits in and nothing else, so one
    parser and one failure policy serve both.
    """
    registry = parsed('sources:\n  day:\n    command: [day, --json]\ncompletions:\n  done:\n    command: [done, --json]\n')

    assert list(registry.sources) == ['day']
    assert list(registry.completions) == ['done']
    assert registry.completions['done'] == sources.Source(id='done', command=['done', '--json'])


def test_a_command_beside_an_adapter_is_ignored_and_reported():
    """The read belongs to the adapter, so no command in the sources document can drift from it.

    The command is ignored rather than the entry dropped, so the lane keeps
    answering while the line waits to be deleted.
    """
    sources.register_adapter('owned', lambda _: [])
    body = 'sources:\n  quiet:\n    adapter: owned\n  stale:\n    adapter: owned\n    command: [owned, --old-flag]\n'

    registry = parsed(body)

    assert registry.sources['quiet'] == sources.Source(id='quiet', adapter='owned')
    assert registry.sources['stale'] == sources.Source(id='stale', adapter='owned')
    assert len(registry.problems) == 1 and 'stale' in registry.problems[0]


def test_a_file_with_no_completions_block_is_not_a_problem():
    """Every file already on disk predates the block.

    A machine that never adds one gets `doit today` without its count rows
    rather than a warning it cannot act on.
    """
    registry = parsed('sources:\n  life:\n    command: [life, overview, --json]\n')

    assert registry.completions == {}
    assert registry.problems == []


def test_a_malformed_completions_entry_is_reported_and_skipped():
    """The same policy as a malformed source, because it is the same parser."""
    registry = parsed('completions:\n  good:\n    command: [echo, hi]\n  bad:\n    adapter: nope\n')

    assert list(registry.completions) == ['good']
    assert any('bad' in problem for problem in registry.problems)


def test_one_id_in_both_blocks_is_reported_and_the_completion_dropped():
    """A round's results are keyed by id, so two reads under one id collide.

    One result is then read by both blocks and the lane the loser would have
    built disappears with nothing said — a habits lane gone from the screen
    while `doit sources list` prints both entries as if they ran. Dropping the
    completion keeps the dashboard whole, which is the half that has no
    workaround.
    """
    registry = parsed(
        'sources:\n  meso:\n    command: [meso, overview, --json]\n    adapter: meso\n'
        'completions:\n  meso:\n    command: [meso, review, --json]\n    adapter: meso-review\n',
    )

    assert list(registry.sources) == ['meso']
    assert registry.completions == {}
    assert any('meso' in problem for problem in registry.problems)


def test_a_block_that_is_not_a_mapping_names_itself():
    """Two blocks means the message has to say which one was wrong."""
    registry = parsed('completions: [life, tasks]\n')

    assert any('completions' in problem for problem in registry.problems)


def test_source_order_is_the_files_order():
    """Lane order follows sources.yml, which is what makes it yours to change."""
    body = 'sources:\n  b:\n    command: [b]\n  a:\n    command: [a]\n'

    assert list(parsed(body).sources) == ['b', 'a']


def test_a_failing_source_shows_its_first_stderr_line():
    result = sources.Result(
        source='life',
        exit_code=1,
        stderr='error: not logged in — run `life auth login`\nUsage:\n  life overview [flags]',
        failure=sources.Failure.FAILED,
    )

    assert sources.reason('life', result) == 'error: not logged in — run `life auth login`'


def test_each_answer_carries_its_block_and_verdict():
    day = sources.answer('outstanding', sources.Source(id='day', command=['day']), sources.Result(source='day', payload=CONFORMING))
    gone = sources.answer(
        'happened',
        sources.Source(id='gone', command=['gone']),
        sources.Result(source='gone', stderr='gone did not answer', failure=sources.Failure.FAILED),
    )

    assert (day['block'], day['answered'], day['lanes'], day['via']) == ('outstanding', True, 1, 'contract')
    assert (gone['block'], gone['answered'], gone['failure'], gone['reason'], gone['lanes']) == (
        'happened',
        False,
        'failed',
        'gone did not answer',
        0,
    )


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
