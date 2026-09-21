"""Tests for doit.today — the day scoreboard, its adapters, and the due list.

Backends are never invoked. The completion adapters are fed committed payloads
wrapped in a `sources.Result`, exactly the way `test_dashboard.py` feeds the
lane adapters, and every date is pinned to `NOW` so a test cannot pass or fail
on the day it is run.

`build` itself is not exercised end to end here. It shells out to every
configured source, and a test that did would be measuring whichever backends
the machine running the suite happens to have authenticated. The pieces it
composes are each tested directly instead.
"""

import json
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import pytest

from doit import dashboard
from doit import journal
from doit import lanes
from doit import sources
from doit import today
from doit.lanes import GridCell
from doit.lanes import Lane
from doit.lanes import Row
from doit.lanes import Urgency

FIXTURE_DIR = Path(__file__).resolve().parent / 'fixtures' / 'today'

# Noon, so a test can move a stamp several hours either way without crossing
# midnight and silently changing which day it lands on.
NOW = datetime(2026, 7, 24, 12, 0, 0).astimezone()
TODAY = NOW.date()


def fixture(name: str):
    return json.loads((FIXTURE_DIR / f'{name}.json').read_text())


def ok(source: str, payload):
    return sources.Result(source=source, payload=payload, exit_code=0)


def broke(source: str):
    return sources.Result(source=source, exit_code=1, stderr='no', failure=sources.Failure.FAILED)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    """Pin every wall-clock read inside the adapters.

    They take no `now` argument, because `sources.Adapter` is a one-argument
    callable and widening it for the tests would change the contract the
    dashboard's adapters are also written against.
    """

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is None else NOW.astimezone(tz)

    monkeypatch.setattr(today, 'datetime', Frozen)


# --- reading a backend's date ------------------------------------------------


def test_a_plain_date_and_an_instant_land_on_the_same_day():
    """Books answer with a day and tasks with a moment. Both are one day here."""
    assert today.local_date('2026-07-24', NOW) == TODAY
    assert today.local_date('2026-07-24T17:33:56Z', NOW) == TODAY


def test_an_absent_or_unparsable_date_is_no_day_rather_than_an_error():
    """A backend that omits the field must cost that row, never the lane."""
    assert today.local_date(None, NOW) is None
    assert today.local_date('', NOW) is None
    assert today.local_date('whenever', NOW) is None


def test_rows_are_filtered_to_today_even_when_the_command_already_asked_for_one_day():
    """`{today}` in the argv saves transfer; it is not what makes this right.

    An entry edited to drop the flag would otherwise report a whole backlog as
    this morning's work, and report it silently.
    """
    rows = today.rows_done_today(fixture('icb-books-finished'), 'read_finish_date', NOW)

    assert [row['title'] for row in rows] == ['The Mythical Man-Month']


def test_a_payload_that_is_not_a_list_yields_no_rows():
    assert today.rows_done_today({'books': []}, 'read_finish_date', NOW) == []
    assert today.rows_done_today(None, 'read_finish_date', NOW) == []


# --- the completion adapters -------------------------------------------------


def adapter_for(source_id: str):
    return sources.ADAPTERS[source_id]


def test_project_items_are_counted_by_when_they_were_completed_not_touched():
    """`updated_at` moves on any edit, so it overstates what a day contained.

    The third fixture row was completed yesterday and edited today. A reader
    on `updated_at` counts three; the honest answer is two.
    """
    [lane] = adapter_for('icb-projects')(ok('icb-projects', fixture('icb-projects-completed')))

    assert lane.meta == '2 done'
    assert [cell.text for cell in lane.grid] == [
        'digest does every operation in one run',
        'the homelab CLI wraps pyinfra',
    ]


def test_every_completion_cell_is_marked_done():
    """`sum(cell.done)` has to be the count on every lane in this document.

    The habits lane arrives from the dashboard with both halves in its grid, so
    a completion lane storing finished things as undone cells would make the
    two disagree about what a grid means.
    """
    [lane] = adapter_for('icb-projects')(ok('icb-projects', fixture('icb-projects-completed')))

    assert all(cell.done for cell in lane.grid)
    assert lane.total == sum(1 for cell in lane.grid if cell.done)


def test_learning_is_filtered_here_because_it_takes_no_date_flag():
    """One of its three rows is today, one is older, one never completed."""
    [lane] = adapter_for('learning-completed')(ok('learning-completed', fixture('learning-completed')))

    assert lane.meta == '1 done'
    assert [cell.text for cell in lane.grid] == ['Structured concurrency in practice']


def test_meso_folds_three_kinds_of_record_into_one_row():
    """Sessions, measurements and log entries all count as training happening."""
    [lane] = adapter_for('meso-review')(ok('meso-review', fixture('meso-review')))

    assert lane.name == 'training'
    assert lane.meta == '3 done'
    assert sorted(cell.text for cell in lane.grid) == ['Lower A', 'bodyweight', 'steady']


def test_a_row_missing_its_label_renders_as_a_dash_rather_than_an_empty_cell():
    payload = [{'title': '', 'completed_at': NOW.isoformat()}]
    [lane] = adapter_for('icb-projects')(ok('icb-projects', payload))

    assert [cell.text for cell in lane.grid] == ['—']


@pytest.mark.parametrize(('adapter_id', 'lane_name'), today.COMPLETION_ADAPTERS)
def test_every_completion_adapter_degrades_rather_than_disappears(adapter_id, lane_name):
    """A lane that vanishes reads as "nothing done", which is the worst answer.

    Asserted over the table rather than per adapter, so a seventh registered
    without this behavior fails here instead of going unnoticed until the day
    its backend is logged out.
    """
    [lane] = adapter_for(adapter_id)(broke(adapter_id))

    assert lane.name == lane_name
    assert lane.available is False
    assert lane.reason == 'no', "a failed call's own stderr is the most useful thing to print"


@pytest.mark.parametrize(('adapter_id', 'lane_name'), today.COMPLETION_ADAPTERS)
def test_a_missing_binary_is_named_by_its_source_id_not_its_lane(adapter_id, lane_name):
    """`reason` looks the configured argv back up by source id.

    Passing the lane name would lose the command on an exit-2 and name a thing
    that was never a source — `books is not installed` for a missing `icb`.
    """
    absent = sources.Result(source=adapter_id, failure=sources.Failure.NOT_INSTALLED)

    [lane] = adapter_for(adapter_id)(absent)

    assert lane.reason == f'{adapter_id} is not installed on this machine'


@pytest.mark.parametrize(('adapter_id', 'lane_name'), today.COMPLETION_ADAPTERS)
def test_every_completion_adapter_is_registered_under_its_tabled_id(adapter_id, lane_name):
    assert adapter_id in sources.ADAPTERS


# --- the scoreboard rows -----------------------------------------------------


def lane_view(name, cells, meta='', hints=()):
    return dashboard.LaneView(name=name, title=name.upper(), meta=meta, grid=list(cells), hints=list(hints))


def test_a_grid_lane_is_restated_as_a_ratio_so_the_column_scans():
    """A source phrases its own summary, and on the dashboard that is right.

    In a column where every row is a ratio, "1 of 6 done today" beside "0 of
    11" makes the reader parse each row instead of scanning the column.
    """
    built = [lane_view('habits', [GridCell('a', True), GridCell('b', False)], meta='1 of 2 done today')]

    [lane] = today.grid_lanes(built)

    assert lane.meta == '1 of 2'
    assert lane.total == 2


def test_a_lane_with_rows_and_no_grid_is_not_a_day():
    """Books and articles are inventories. Nothing about them completes a day."""
    rows_only = dashboard.LaneView(name='books', title='BOOKS', rows=[Row('reading', 'Dune')])

    assert today.grid_lanes([rows_only]) == []


def test_review_counts_what_is_due_and_what_is_done_without_double_counting():
    """An item on a daily cadence done this morning still reports `overdue: 0`.

    Zero means "wanted today" and it was, so without subtracting the done half
    a register of one item reads `1 of 2`.
    """
    rows = [
        {'id': 'brew', 'overdue': 0, 'last': TODAY.isoformat()},
        {'id': 'certs', 'overdue': 3, 'last': '2026-07-01'},
        {'id': 'fresh', 'overdue': -2, 'last': '2026-07-23'},
        {'id': 'never', 'overdue': None, 'last': None},
    ]

    lane = today.statuses_lane('review', 'REVIEW', rows, TODAY, 'doit review due')

    assert lane.meta == '1 of 3', 'brew is done, certs and never are due, fresh is not yet wanted'
    assert lane.total == 3


def test_a_pursuit_counts_as_touched_from_either_record():
    """The journal answers what got typed and evidence answers what apps saw.

    A pursuit satisfied inside its own CLI appears only in the second, so the
    union is the answer rather than either one.
    """
    state = {
        'now': NOW,
        'active': {'build': {}, 'chore': {}, 'read': {}},
        'evidence_days': {'build': [TODAY.isoformat()], 'read': ['2026-07-01']},
        'records': [
            {'event': journal.Event.DONE, 'pursuit': 'chore', 'occurred_at': NOW.isoformat()},
            {'event': journal.Event.DONE, 'pursuit': 'read', 'occurred_at': (NOW - timedelta(days=3)).isoformat()},
            {'event': journal.Event.SKIP, 'pursuit': 'build', 'occurred_at': NOW.isoformat()},
        ],
    }

    assert today.touched_today(state, TODAY) == ['build', 'chore']


def test_a_pursuit_no_longer_in_the_register_is_not_counted_as_touched():
    """A journal is history and the register is now. Commenting one out must
    not keep it scoring against today."""
    state = {
        'now': NOW,
        'active': {'build': {}},
        'evidence_days': {},
        'records': [{'event': journal.Event.DONE, 'pursuit': 'retired', 'occurred_at': NOW.isoformat()}],
    }

    assert today.touched_today(state, TODAY) == []


def test_the_pursuits_row_states_two_counts_and_never_a_ratio():
    """Nine active pursuits is not a daily target — the weights imply intervals
    of days and weeks, so `3 of 9` would put a goal on screen nobody set."""
    state = pursuit_state(balances={'build': 2.0, 'chore': 0.0}, touched=['chore'])

    lane = today.pursuits_lane(state, TODAY)

    assert lane.meta == '1 touched · 1 behind'
    assert lane.total == 1, 'a total above the grid would render this as a ratio and tick boxes nobody asked for'


def pursuit_state(balances, touched=()):
    """A minimal `build_state` shaped dict, for the readers that walk one."""
    return {
        'now': NOW,
        'today': TODAY,
        'active': {name: {'description': f'do {name}'} for name in balances},
        'balance': dict(balances),
        'checkoff_size': dict.fromkeys(balances, 1.0),
        'intervals': dict.fromkeys(balances, 3.0),
        'evidence_days': {name: [TODAY.isoformat()] for name in touched},
        'records': [],
    }


def test_logged_sums_only_the_entries_that_carry_a_duration():
    """A timed pursuit logged without `--minutes` records no duration on
    purpose, so counting it as zero would be as wrong as counting a checkoff."""
    state = pursuit_state(balances={'read': 0.0})
    state['records'] = [
        {'event': journal.Event.DONE, 'pursuit': 'read', 'occurred_at': NOW.isoformat(), 'duration_minutes': 45},
        {'event': journal.Event.DONE, 'pursuit': 'chore', 'occurred_at': NOW.isoformat()},
        {'event': journal.Event.DONE, 'pursuit': 'read', 'occurred_at': (NOW - timedelta(days=1)).isoformat(), 'duration_minutes': 90},
    ]

    lane = today.logged_lane(state, TODAY)

    assert lane.meta == '2 entries · 45 min'


def test_one_entry_is_singular():
    state = pursuit_state(balances={'read': 0.0})
    state['records'] = [{'event': journal.Event.DONE, 'pursuit': 'read', 'occurred_at': NOW.isoformat()}]

    assert today.logged_lane(state, TODAY).meta == '1 entry'


# --- the due list ------------------------------------------------------------


def test_the_due_list_orders_by_how_far_past_due_and_carries_a_handle():
    state = pursuit_state(balances={'chore': 1.0, 'socialize': 4.0})

    lane = today.due_lane(state, [], TODAY)

    assert [row.label for row in lane.rows] == ['socialize', 'chore'], 'furthest behind first'
    assert all(row.handle for row in lane.rows), 'a row you can read but not act on is half a row'
    assert lane.rows[0].handle == 'doit log socialize'


def test_a_pursuit_that_is_current_stays_off_the_due_list():
    """A whole checkoff rather than any positive balance: a balance climbs
    continuously from zero, so everything passes through the fraction above
    it after every checkoff."""
    state = pursuit_state(balances={'chore': 0.4})

    assert today.due_lane(state, [], TODAY).rows == []


def test_a_pursuit_with_no_interval_is_owed_rather_than_dropped():
    """`priced` returns None for a name the schedule does not reach, and a
    pursuit owing a checkoff is owed whether or not a date can be put on it.

    It sorts last among the owed, because a row with no date cannot be compared
    against one that has a date and guessing an order would be inventing one.
    """
    state = pursuit_state(balances={'chore': 1.0, 'undated': 1.0})
    del state['intervals']['undated']

    lane = today.due_lane(state, [], TODAY)

    assert [row.label for row in lane.rows] == ['chore', 'undated']
    assert lane.rows[1].note == 'due today'


def test_an_unfinished_grid_becomes_one_row_naming_what_is_left():
    built = [
        lane_view(
            'habits', [GridCell('Floss', False), GridCell('Walk', False), GridCell('Water', True)], hints=['icb habits complete <id>']
        )
    ]
    state = pursuit_state(balances={})

    lane = today.due_lane(state, built, TODAY)

    [row] = lane.rows
    assert row.label == 'habits'
    assert row.text == 'Floss · Walk'
    assert row.note == '2 left'
    assert row.handle == 'icb habits complete <id>'


def test_a_finished_grid_contributes_no_due_row():
    built = [lane_view('habits', [GridCell('Water', True)])]

    assert today.due_lane(pursuit_state(balances={}), built, TODAY).rows == []


def test_only_todays_rows_come_from_a_row_based_lane():
    """`urgency != NONE` means "wants attention", not "is today".

    Learning marks DUE a fortnight out and PRs at three days, so a generic
    filter would put next week on a screen about this afternoon.
    """
    upcoming = dashboard.LaneView(
        name='upcoming',
        title='UPCOMING',
        rows=[
            Row('today', 'Dentist', '24 Jul 2026', Urgency.DUE, 'icb events show 8'),
            Row('in 9d', 'Passport expires', '02 Aug 2026', Urgency.NONE, 'icb countdowns show 2'),
        ],
    )
    learning = dashboard.LaneView(
        name='learning',
        title='LEARNING',
        rows=[Row('unit', 'Channels', 'in 11d', Urgency.DUE, 'learning show 4')],
    )

    lane = today.due_lane(pursuit_state(balances={}), [upcoming, learning], TODAY)

    assert [row.text for row in lane.rows] == ['Dentist']


def test_an_event_today_sorts_above_everything_owed():
    """An appointment has a clock on it and a chore does not."""
    upcoming = dashboard.LaneView(
        name='upcoming',
        title='UPCOMING',
        rows=[Row('today', 'Dentist', '24 Jul 2026', Urgency.DUE, 'icb events show 8')],
    )
    state = pursuit_state(balances={'socialize': 9.0})

    lane = today.due_lane(state, [upcoming], TODAY)

    assert [row.text for row in lane.rows] == ['Dentist', 'do socialize']


# --- the document ------------------------------------------------------------


def test_the_document_is_the_lane_contract_so_no_consumer_learns_a_second_schema():
    day = today.Day(
        [Lane(name='habits', title='HABITS', meta='1 of 2', grid=[GridCell('a', True), GridCell('b', False)], total=2)],
        frozenset(),
    )

    parsed = json.loads(lanes.dumps(day.lanes, NOW))

    assert parsed['schema_version'] == lanes.SCHEMA_VERSION
    assert parsed['lanes'][0]['grid'] == [
        {'text': 'a', 'done': True, 'handle': ''},
        {'text': 'b', 'done': False, 'handle': ''},
    ]


def test_a_register_that_will_not_load_costs_two_rows_rather_than_the_screen(monkeypatch):
    """The lanes either side of it have nothing to do with the weights."""

    def refuses():
        raise ValueError('pursuits.yml: weight must be a number')

    monkeypatch.setattr(today.pursuits, 'load_pursuits', refuses)

    built, state = today.pursuit_lanes(NOW, TODAY)

    assert state is None
    assert [lane.name for lane in built] == ['pursuits']
    assert built[0].available is False


def test_an_empty_register_contributes_nothing_and_is_not_an_error(monkeypatch):
    monkeypatch.setattr(today.pursuits, 'load_pursuits', dict)

    built, state = today.pursuit_lanes(NOW, TODAY)

    assert (built, state) == ([], None)
