"""Tests for doit.happened — the fold over a window, its adapters and its screen.

Backends are never invoked. The completion adapters are fed committed payloads
wrapped in a `sources.Result` carrying the window, and every date is pinned to
`NOW` so a test cannot pass or fail on the day it is run.
"""

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from doit import actor
from doit import evidence
from doit import happened
from doit import journal
from doit import lanes
from doit import sources
from doit import window
from doit.lanes import GridCell
from doit.window import Bounds

FIXTURE_DIR = Path(__file__).resolve().parent / 'fixtures' / 'happened'

# Noon, so a test can move a stamp several hours either way without crossing
# midnight and silently changing which day it lands on.
NOW = dt.datetime(2026, 7, 24, 12, 0, 0).astimezone()
TODAY = NOW.date()
DAY = window.day_of(NOW)
WEEK = Bounds(NOW - dt.timedelta(days=7), DAY.stop)


def fixture(name: str):
    return json.loads((FIXTURE_DIR / f'{name}.json').read_text())


def ok(source: str, payload, bounds: Bounds = DAY):
    return sources.Result(source=source, payload=payload, exit_code=0, bounds=bounds)


def adapter_for(source_id: str):
    return sources.ADAPTERS[source_id].build


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    """Pin the adapters' wall clock. They take no `now`, because `sources.Adapter` is a one-argument callable."""

    class Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is None else NOW.astimezone(tz)

    monkeypatch.setattr(happened, 'dt', SimpleNamespace(**vars(dt) | {'datetime': Frozen}))


# --- reading a record's date ---------------------------------------------------


def test_a_utc_stamp_is_converted_before_its_day_is_read():
    """Nine in the evening on the 24th is already the 25th in UTC, so reading
    the first ten characters would move every evening's work onto tomorrow."""
    evening = dt.datetime(2026, 7, 24, 21, 0, tzinfo=dt.timezone(dt.timedelta(hours=-4)))

    assert happened.local_date('2026-07-25T01:00:00Z', evening) == dt.date(2026, 7, 24)
    assert happened.local_date('2026-07-24T01:00:00Z', evening) == dt.date(2026, 7, 23)


def test_a_day_counts_when_any_of_it_is_inside_the_window():
    """A window opening mid-afternoon still holds a book finished that day, which keeps no hour."""
    afternoon = Bounds(NOW + dt.timedelta(hours=3), NOW + dt.timedelta(days=2))

    assert happened.lands_within('2026-07-24', afternoon, NOW)
    assert not happened.lands_within('2026-07-23', afternoon, NOW)
    assert not happened.lands_within(NOW.isoformat(), afternoon, NOW), 'an instant before the start is outside'


# --- the completion adapters ---------------------------------------------------


def test_project_items_are_listed_by_when_they_were_completed_not_touched():
    """`updated_at` moves on any edit. The third fixture row was completed
    yesterday and edited today, so reading it would list three."""
    [lane] = adapter_for('icb-projects')(ok('icb-projects', fixture('icb-projects-completed')))

    assert [cell.text for cell in lane.grid] == ['the homelab CLI wraps pyinfra', 'digest does every operation in one run']
    assert dt.datetime.fromisoformat(lane.grid[0].done_at) == dt.datetime(2026, 7, 24, 9, 15, tzinfo=dt.UTC)


def test_an_issue_carries_who_closed_it():
    [lane] = adapter_for('icb-issues')(ok('icb-issues', fixture('icb-issues-completed')))

    assert [(cell.text, actor.kind(cell.actor)) for cell in lane.grid] == [
        ('toolchains asks PATH for rustc', actor.BY_HAND),
        ('the log stream has had no client since the old app was removed', actor.SESSION),
    ]


def test_a_wider_window_reaches_further_back_and_says_so_in_its_hint():
    """The hint is the command over the same window, so running it lists what the group read."""
    [lane] = adapter_for('icb-issues')(ok('icb-issues', fixture('icb-issues-completed'), WEEK))

    since, until = (end.isoformat(timespec='seconds') for end in WEEK)

    assert len(lane.grid) == 3
    assert lane.hints == [f'icb issues list --status completed --since {since} --until {until}']


def test_a_window_holding_more_than_the_backend_returns_is_partial_rather_than_answered_whole():
    """A window holding more completions than learning's limit of 100 would read as answered whole, its oldest missing."""
    notice = '100 newest completed item(s), cut by --limit; older ones exist.'
    cut = sources.Result(source='learning-completed', payload=fixture('learning-completed'), exit_code=0, stderr=notice, bounds=DAY)

    [lane] = adapter_for('learning-completed')(cut)

    assert (lane.available, lane.reason) == (True, notice)
    assert [cell.text for cell in lane.grid] == ['Structured concurrency in practice']
    assert happened.verdict_lines([lane], 80)[1].plain.strip() == 'all 1 answered · learning partial · no actor recorded'


def test_meso_folds_three_kinds_of_record_into_one_group_with_no_hours():
    [lane] = adapter_for('meso-review')(ok('meso-review', fixture('meso-review')))

    assert lane.name == 'training'
    assert sorted(cell.text for cell in lane.grid) == ['Lower A', 'bodyweight', 'steady']
    assert {cell.done_at for cell in lane.grid} == {''}
    assert {cell.done_on for cell in lane.grid} == {TODAY.isoformat()}


@pytest.mark.parametrize(('adapter_id', 'lane_name'), happened.COMPLETION_ADAPTERS)
def test_every_completion_adapter_degrades_rather_than_disappears(adapter_id, lane_name):
    """A group that vanishes reads as "nothing done". Asserted over the table, so a new adapter cannot skip it."""
    failed = sources.Result(source=adapter_id, exit_code=1, stderr='no', failure=sources.Failure.FAILED)

    [lane] = adapter_for(adapter_id)(failed)

    assert (lane.name, lane.available, lane.reason) == (lane_name, False, 'no')


@pytest.mark.parametrize(('adapter_id', 'lane_name'), happened.COMPLETION_ADAPTERS)
def test_an_expired_login_answering_an_error_with_exit_zero_is_unavailable_and_says_why(adapter_id, lane_name):
    [lane] = adapter_for(adapter_id)(ok(adapter_id, {'error': 'auth expired'}))

    assert (lane.name, lane.available, lane.reason) == (lane_name, False, f'{adapter_id} answered with an error: auth expired')


@pytest.mark.parametrize(('adapter_id', 'lane_name'), happened.COMPLETION_ADAPTERS)
def test_a_missing_binary_is_named_by_its_source_id_not_its_lane(adapter_id, lane_name):
    """`reason` looks the argv back up by source id, so a lane name would print `books is not installed` for a missing `icb`."""
    [lane] = adapter_for(adapter_id)(sources.Result(source=adapter_id, failure=sources.Failure.NOT_INSTALLED))

    assert lane.reason == f'{adapter_id} is not installed on this machine'


# --- a done group --------------------------------------------------------------


def done(text: str, done_at: str = '', done_on: str = '') -> GridCell:
    return GridCell(text, True, done_at=done_at, done_on=done_on)


def test_each_day_runs_its_timed_entries_in_order_then_its_untimed_ones():
    cells = [
        done('later day', '2026-07-24T08:00:00+00:00'),
        done('undated'),
        done('day only', done_on='2026-07-23'),
        done('late', '2026-07-23T15:00:00+00:00'),
        done('early', '2026-07-23T09:00:00+00:00'),
    ]

    lane = happened.done_lane('x', 'X', cells, '')

    assert [cell.text for cell in lane.grid] == ['early', 'late', 'day only', 'later day', 'undated']


def test_the_order_is_by_instant_even_where_the_offsets_differ():
    """Sorted as text, `10:00-04:00` lands ahead of `13:00+00:00` although it happened an hour later."""
    cells = [done('second', '2026-07-24T10:00:00-04:00'), done('first', '2026-07-24T13:00:00+00:00')]

    assert [cell.text for cell in happened.done_lane('x', 'X', cells, '').grid] == ['first', 'second']


# --- doit's own records --------------------------------------------------------


def pursuit_state(touched=()):
    """The parts of a `build_state` dict that `moved_within` reads."""
    return {
        'now': NOW,
        'records': [],
        'observed': {},
        'evidence_days': evidence.occurrences({'pursuits': {name: {'dates': [TODAY.isoformat()]} for name in touched}}),
    }


def done_record(pursuit: str, when: dt.datetime, **extra) -> dict:
    return {'event': journal.Event.DONE, 'pursuit': pursuit, 'occurred_at': when.isoformat(), **extra}


def test_a_pursuit_is_done_from_either_record():
    """A pursuit satisfied inside its own CLI appears only in evidence, at the time its app saw it."""
    state = pursuit_state(touched=['build'])
    state['observed'] = {'build': NOW - dt.timedelta(hours=2)}
    state['records'] = [
        done_record('chore', NOW),
        done_record('read', NOW - dt.timedelta(days=3)),
        {'event': journal.Event.SKIP, 'pursuit': 'read', 'occurred_at': NOW.isoformat()},
    ]

    assert [cell.text for cell in happened.pursuits_group(state, DAY).grid] == ['build', 'chore']
    assert [cell.text for cell in happened.pursuits_group(state, WEEK).grid] == ['read', 'build', 'chore'], 'the week reaches the 21st'


def test_evidence_adds_nothing_for_a_day_the_pursuit_was_also_typed():
    state = pursuit_state(touched=['read'])
    state['records'] = [done_record('read', NOW)]

    assert [cell.text for cell in happened.pursuits_group(state, DAY).grid] == ['read']


def test_each_entry_typed_is_its_own_row_with_its_minutes_and_its_actor():
    """A timed pursuit logged without `--minutes` records no duration on purpose, so it gets no number rather than a zero."""
    state = pursuit_state()
    state['records'] = [
        done_record('read', NOW - dt.timedelta(hours=3), duration_minutes=45, actor='ada@laptop'),
        done_record('chore', NOW - dt.timedelta(hours=1)),
    ]

    grid = happened.pursuits_group(state, DAY).grid

    assert [(cell.text, cell.actor) for cell in grid] == [('read · 45 min', 'ada@laptop'), ('chore', '')]


def test_a_pursuit_no_longer_in_the_register_still_counts():
    """The journal is what happened. Pausing a pursuit this afternoon does not undo this morning."""
    state = pursuit_state()
    state['watched'] = {'kept': {}}
    state['records'] = [done_record('retired', NOW), done_record('kept', NOW - dt.timedelta(hours=1))]

    assert [cell.text for cell in happened.pursuits_group(state, DAY).grid] == ['kept', 'retired']


def test_a_register_lists_each_item_whose_latest_date_falls_in_the_window():
    review = next(entry for entry in happened.registers() if entry.name == 'review')
    rows = [
        {'id': 'brew', 'desc': 'Upgrade brew', 'last': TODAY.isoformat()},
        {'id': 'certs', 'last': '2026-07-20'},
        {'id': 'never', 'last': None},
    ]

    assert [cell.text for cell in happened.register_group(review, rows, DAY, NOW).grid] == ['Upgrade brew']
    assert [cell.text for cell in happened.register_group(review, rows, WEEK, NOW).grid] == ['certs', 'Upgrade brew']


# --- the fold ------------------------------------------------------------------


def test_a_source_that_produced_no_group_is_still_counted_among_those_asked():
    """A conforming source declaring no `lanes:` yields nothing when it fails, and the verdict would lose it."""
    quiet = sources.Source(id='quiet', command=['quiet'])
    broken = sources.Source(id='broken', command=['broken'])
    registry = sources.Registry(completions={'quiet': quiet, 'broken': broken})
    results = {
        'quiet': ok('quiet', {'lanes': []}),
        'broken': sources.Result(source='broken', failure=sources.Failure.NOT_INSTALLED),
    }

    groups = happened.fold(registry, results, happened.Kept(), DAY, NOW)

    assert [(lane.name, lane.available) for lane in groups] == [('review', True), ('labs', True), ('quiet', True), ('broken', False)]


def test_a_pursuits_register_that_will_not_load_costs_its_group_and_nothing_else():
    groups = happened.fold(sources.Registry(), {}, happened.Kept(state_error='pursuits.yml: weight must be a number'), DAY, NOW)

    assert [(lane.name, lane.available) for lane in groups] == [('pursuits', False), ('review', True), ('labs', True)]


def test_the_actor_identity_written_is_one_its_kind_reads_back(monkeypatch):
    """The journal writes the identity and the screen sorts it, so the two must agree on every form."""
    for name in ('CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_SESSION_ATTENDED', actor.SCHEDULED_JOB_ENV):
        monkeypatch.delenv(name, raising=False)
    assert actor.kind(actor.identity()) == actor.BY_HAND
    monkeypatch.setenv(actor.SCHEDULED_JOB_ENV, 'nightly')
    assert actor.kind(actor.identity()) == actor.UNATTENDED
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'abc')
    assert actor.kind(actor.identity()) == actor.UNATTENDED
    monkeypatch.delenv(actor.SCHEDULED_JOB_ENV)
    assert actor.identity() == 'claude-code/abc'
    assert actor.kind(actor.identity()) == actor.SESSION


def test_a_runner_prefix_doit_has_never_seen_reads_unknown_rather_than_by_hand():
    """A new kind of runner is counted apart, never shown as the reader's own work."""
    lane = happened.done_lane('issues', 'ISSUES', [GridCell('closed it', True, actor='runner/nightly/abc')], '')

    assert actor.kind('runner/nightly/abc') == actor.UNKNOWN
    assert happened.verdict_lines([lane], 80)[1].plain.endswith('· 1 by an actor of unknown kind')


# --- the screen ----------------------------------------------------------------


def test_a_done_hour_is_green_while_the_text_stays_plain():
    """A style set on a line's first Text is inherited by everything appended to it, which is how a whole row ends up one color."""
    line = happened.done_line(done('Floss', NOW.isoformat()), 40)

    assert str(line.style) == ''
    assert [(line.plain[span.start : span.end], str(span.style)) for span in line.spans if str(span.style)] == [
        (f'{NOW:%H:%M}', happened.HOUR_STYLE)
    ]


def test_the_verdict_names_every_record_asked_and_which_failed():
    """In a split pane narrower than the list, every record asked still shows, wrapped rather than cut."""
    groups = [
        happened.done_lane('issues', 'ISSUES', [GridCell('x', True, actor='ada@laptop')], ''),
        lanes.Lane(name='tasks', title='TASKS'),
        lanes.unavailable('learning', 'LEARNING', 'not logged in'),
    ]

    names, answered = happened.verdict_lines(groups, 24)

    assert all(len(line) <= 24 for line in names.plain.splitlines())
    assert ' '.join(names.plain.split()) == 'asked issues, tasks, learning'
    assert answered.plain.strip() == '2 of 3 answered · actor recorded by issues'


def test_a_window_that_will_not_parse_is_refused_before_any_backend_is_asked():
    assert happened.cmd_happened('6mo', None, as_json=False) == 2


@pytest.fixture
def no_sources(monkeypatch):
    monkeypatch.setattr(happened.sources, 'load', sources.Registry)
    monkeypatch.setattr(happened, 'read_kept', lambda now: happened.Kept(registers={'review': [], 'labs': []}))


def test_until_alone_asks_what_happened_up_to_a_past_date_from_the_earliest_record(no_sources, capsys):
    assert happened.cmd_happened(None, '2026-07-20', as_json=True) == 0
    document = json.loads(capsys.readouterr().out)

    assert dt.datetime.fromisoformat(document['since']) == happened.EARLIEST
    assert document['until'][:10] == '2026-07-21'


def test_a_scripted_caller_reads_exit_1_when_no_record_could_be_asked(no_sources, monkeypatch):
    """Exit 0 there could not be told from a window where nothing happened."""
    failing = happened.Kept(state_error='pursuits.yml: unreadable', register_errors={'review': 'unreadable', 'labs': 'unreadable'})
    monkeypatch.setattr(happened, 'read_kept', lambda now: failing)

    assert happened.cmd_happened(None, None, as_json=True) == 1


def test_the_json_names_the_window_the_records_asked_and_each_group_in_the_contract_shape(no_sources, capsys):
    assert happened.cmd_happened('2026-07-20', '2026-07-23', as_json=True) == 0
    document = json.loads(capsys.readouterr().out)

    assert (document['since'][:10], document['until'][:10]) == ('2026-07-20', '2026-07-24')
    assert document['asked'] == ['review', 'labs']
    assert document['unavailable'] == []
    assert [lane.name for lane in lanes.from_document({'lanes': document['groups']})] == ['review', 'labs']
