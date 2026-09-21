"""Tests for doit.today — the done list, the due list, and the adapters behind them.

Backends are never invoked. The completion adapters are fed committed payloads
wrapped in a `sources.Result`, exactly the way `test_dashboard.py` feeds the
lane adapters, and every date is pinned to `NOW` so a test cannot pass or fail
on the day it is run.

`build` only ever runs here with every backend replaced. It shells out to every
configured source, and a test that let it would be measuring whichever backends
the machine running the suite happens to have authenticated.
"""

import json
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

import pytest

from doit import dashboard
from doit import evidence
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


def test_a_utc_stamp_is_converted_before_its_day_is_read():
    """Every app here stamps in UTC, and the reader sits west of it.

    Nine in the evening on the 20th is already the 21st in UTC, so reading the
    first ten characters would move every evening's work onto tomorrow. The
    same truncation pulls yesterday evening's onto today.
    """
    evening = datetime(2026, 7, 24, 21, 0, tzinfo=timezone(timedelta(hours=-4)))

    assert today.local_date('2026-07-25T01:00:00Z', evening) == date(2026, 7, 24)
    assert today.local_date('2026-07-24T01:00:00Z', evening) == date(2026, 7, 23)


def test_a_plain_day_has_no_offset_to_apply_and_keeps_itself():
    """`read_finish_date`, `performed_on` and `measured_on` carry no time.

    Converting a day parsed as naive midnight would move it, so the branch that
    converts is the one that found a timezone.
    """
    far_east = datetime(2026, 7, 24, 9, 0, tzinfo=timezone(timedelta(hours=13)))

    assert today.local_date('2026-07-24', far_east) == date(2026, 7, 24)


def test_an_absent_or_unparsable_date_is_no_day_rather_than_an_error():
    """A backend that omits the field must cost that row, never the lane."""
    assert today.local_date(None, NOW) is None
    assert today.local_date('', NOW) is None
    assert today.local_date('whenever', NOW) is None


def test_a_plain_day_has_no_hour_rather_than_midnight():
    """Printed, a day parsed as naive midnight is `00:00`, an hour nobody did
    anything at, and it sorts ahead of the whole morning."""
    assert today.local_instant('2026-07-24', NOW) is None
    assert today.instant_text('2026-07-24', NOW) == ''


def test_an_instant_is_carried_in_local_time_with_its_offset():
    """The screen prints the hour it reads, so the stamp arrives already local."""
    evening = datetime(2026, 7, 24, 21, 0, tzinfo=timezone(timedelta(hours=-4)))

    assert today.instant_text('2026-07-25T01:00:00Z', evening) == '2026-07-24T21:00:00-04:00'


def test_an_app_observation_is_already_a_datetime_and_is_read_as_one():
    """`build_state` hands evidence over parsed, not as the text it came from."""
    seen = datetime(2026, 7, 24, 13, 0, tzinfo=UTC)

    assert today.local_instant(seen, NOW) == seen


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


def test_project_items_are_listed_by_when_they_were_completed_not_touched():
    """`updated_at` moves on any edit, so it overstates what a day contained.

    The third fixture row was completed yesterday and edited today. A reader
    on `updated_at` lists three; the honest answer is two. The two arrive
    newest first and are listed in the order they happened.
    """
    [lane] = adapter_for('icb-projects')(ok('icb-projects', fixture('icb-projects-completed')))

    assert [cell.text for cell in lane.grid] == [
        'the homelab CLI wraps pyinfra',
        'digest does every operation in one run',
    ]


def test_a_completion_carries_the_hour_it_happened_in_local_time():
    [lane] = adapter_for('icb-projects')(ok('icb-projects', fixture('icb-projects-completed')))

    first = datetime.fromisoformat(lane.grid[0].done_at)

    assert first == datetime(2026, 7, 24, 9, 15, tzinfo=UTC)
    assert first.utcoffset() == NOW.utcoffset()


def test_learning_is_filtered_here_because_it_takes_no_date_flag():
    """One of its three rows is today, one is older, one never completed."""
    [lane] = adapter_for('learning-completed')(ok('learning-completed', fixture('learning-completed')))

    assert [cell.text for cell in lane.grid] == ['Structured concurrency in practice']


def test_meso_folds_three_kinds_of_record_into_one_group_with_no_hours():
    """Sessions, measurements and log entries all count as training happening.

    meso records the day of each and never the hour, so none of them is timed.
    """
    [lane] = adapter_for('meso-review')(ok('meso-review', fixture('meso-review')))

    assert lane.name == 'training'
    assert sorted(cell.text for cell in lane.grid) == ['Lower A', 'bodyweight', 'steady']
    assert {cell.done_at for cell in lane.grid} == {''}


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


# --- a done group ------------------------------------------------------------


def done(text: str, done_at: str = '') -> GridCell:
    return GridCell(text, True, done_at=done_at)


def test_a_done_group_runs_in_the_order_it_happened_and_the_untimed_follow():
    """An entry whose record kept only the day has no place in the order, so it
    follows the timed ones rather than being guessed into it."""
    cells = [done('undated'), done('late', '2026-07-24T15:00:00+00:00'), done('early', '2026-07-24T09:00:00+00:00')]

    lane = today.done_lane('x', 'X', cells, '')

    assert [cell.text for cell in lane.grid] == ['early', 'late', 'undated']


def test_the_order_is_by_instant_even_where_the_offsets_differ():
    """A day crossing a clock change carries two offsets. Sorted as text,
    `10:00-04:00` lands ahead of `13:00+00:00` although it happened an hour
    later."""
    cells = [done('second', '2026-07-24T10:00:00-04:00'), done('first', '2026-07-24T13:00:00+00:00')]

    lane = today.done_lane('x', 'X', cells, '')

    assert [cell.text for cell in lane.grid] == ['first', 'second']


# --- a day set ---------------------------------------------------------------


def lane_view(name, cells, meta='', hints=()):
    return dashboard.LaneView(name=name, title=name.upper(), meta=meta, grid=list(cells), hints=list(hints))


def habits_view():
    return lane_view(
        'habits',
        [GridCell('Floss', False, '2'), GridCell('Water', True, done_at='2026-07-24T13:00:00Z'), GridCell('Walk', False, '5')],
        hints=['icb habits complete <id>'],
    )


def test_a_day_set_splits_into_what_is_done_and_what_is_left():
    [finished], [left] = today.set_lanes([habits_view()], NOW)

    assert [cell.text for cell in finished.grid] == ['Water']
    assert datetime.fromisoformat(finished.grid[0].done_at) == datetime(2026, 7, 24, 13, 0, tzinfo=UTC)
    assert [row.label for row in left.rows] == ['Floss', 'Walk']


def test_a_habit_left_carries_the_command_that_completes_it():
    """The dashboard prints the bare id beside a hint holding `<id>`. A due row
    has one handle column, so the two arrive joined."""
    _, [left] = today.set_lanes([habits_view()], NOW)

    assert [row.handle for row in left.rows] == ['icb habits complete 2', 'icb habits complete 5']


def test_a_grid_is_a_day_set_only_when_it_is_named_as_one():
    """A grid says "the whole set", not "due today". A weekly set emitted as one
    would otherwise land here as a week of work owed this afternoon."""
    weekly = lane_view('chores', [GridCell('Vacuum', False)])

    assert today.set_lanes([weekly], NOW) == ([], [])


def test_a_day_set_that_could_not_be_read_is_reported_where_it_is_owed():
    """Found by having a grid, a failed lane has none and would leave both lists
    without a word."""
    failed = lanes.unavailable('habits', 'HABITS', 'icb: not logged in')

    finished, left = today.set_lanes([failed], NOW)

    assert finished == []
    assert [(lane.name, lane.available) for lane in left] == [('habits', False)]


# --- appointments ------------------------------------------------------------


def upcoming_view(*rows):
    return dashboard.LaneView(name='upcoming', title='UPCOMING', rows=list(rows), hints=['icb countdowns list', 'icb events list'])


DENTIST = Row('today', 'Dentist', '24 Jul 2026', Urgency.DUE, 'icb events show 8')


def test_only_todays_rows_come_from_a_row_based_lane():
    """`urgency != NONE` means "wants attention", not "is today".

    Learning marks DUE a fortnight out and PRs at three days, so a generic
    filter would put next week on a screen about this afternoon.
    """
    upcoming = upcoming_view(DENTIST, Row('in 9d', 'Passport expires', '02 Aug 2026', Urgency.NONE, 'icb countdowns show 2'))
    learning = dashboard.LaneView(
        name='learning', title='LEARNING', rows=[Row('unit', 'Channels', 'in 11d', Urgency.DUE, 'learning show 4')]
    )

    [lane] = today.appointment_lanes([upcoming, learning])

    assert lane.name == 'upcoming'
    assert [row.text for row in lane.rows] == ['Dentist']


def test_an_appointment_says_how_late_in_its_note():
    """On the dashboard the gutter answers "how far away". Here every row is
    today or past, so that word is the note."""
    [lane] = today.appointment_lanes(
        [upcoming_view(Row('3d ago', 'Renew permit', '21 Jul 2026', Urgency.OVERDUE, 'icb countdowns show 4'))]
    )

    assert lane.rows == [Row('', 'Renew permit', '3d ago', Urgency.OVERDUE, 'icb countdowns show 4')]


# --- the registers -----------------------------------------------------------


@pytest.fixture
def registers(monkeypatch):
    """`maintenance_lanes` over the given statuses, so each test reaches the
    real table of how an item is described and acted on."""

    def read(review_rows=(), lab_rows=()):
        monkeypatch.setattr(today.review, 'statuses', lambda: list(review_rows))
        monkeypatch.setattr(today.labs, 'statuses', lambda: list(lab_rows))
        return today.maintenance_lanes(TODAY)

    return read


def test_a_register_lists_what_today_cleared_and_owes_the_rest(registers):
    """An item on a daily cadence done this morning still reports `overdue: 0`,
    so without subtracting the done half it is cleared and owed at once."""
    rows = [
        {'id': 'brew', 'desc': 'Upgrade brew', 'overdue': 0, 'last': TODAY.isoformat()},
        {'id': 'certs', 'desc': 'Renew certs', 'overdue': 3, 'last': '2026-07-01'},
        {'id': 'fresh', 'overdue': -2, 'last': '2026-07-23'},
        {'id': 'never', 'overdue': None, 'last': None},
    ]

    [cleared, _], [owed, _] = registers(review_rows=rows)

    assert [cell.text for cell in cleared.grid] == ['Upgrade brew']
    assert [row.label for row in owed.rows] == ['never', 'certs'], 'never run ranks above any number of days late'
    assert [row.note for row in owed.rows] == ['never run', '3d overdue']


def test_a_review_item_is_named_by_what_it_is_for_and_carries_its_own_command(registers):
    rows = [{'id': 'certs', 'desc': 'Renew certs', 'command': 'certbot renew', 'overdue': 3, 'last': None}]

    _, [owed, _] = registers(review_rows=rows)

    assert owed.rows == [Row('certs', 'Renew certs', '3d overdue', Urgency.OVERDUE, 'certbot renew')]


def test_an_item_due_today_reads_as_due_rather_than_late(registers):
    _, [owed, _] = registers(review_rows=[{'id': 'brew', 'overdue': 0, 'last': '2026-07-17'}])

    assert owed.rows[0].urgency == Urgency.DUE


def test_an_on_demand_lab_is_never_owed(registers):
    """A Lab is due only when it is scheduled, which review items always are.

    A rule written here in place of `labs.is_due_row` would read an on-demand
    Lab's absent `overdue` as never done, and owe it every day forever.
    """
    rows = [
        {'id': 'tmux', 'title': 'Panes and sessions', 'overdue': None, 'last': None, 'scheduled': True},
        {'id': 'jq', 'overdue': None, 'last': None, 'scheduled': False},
    ]

    _, [_, owed] = registers(lab_rows=rows)

    assert [(row.label, row.text, row.handle) for row in owed.rows] == [('tmux', 'Panes and sessions', 'doit labs show tmux')]


def test_a_register_that_cannot_be_read_is_reported_where_it_is_owed(monkeypatch):
    def refuses():
        raise OSError('state.json: permission denied')

    monkeypatch.setattr(today.review, 'statuses', refuses)
    monkeypatch.setattr(today.labs, 'statuses', list)

    cleared, owed = today.maintenance_lanes(TODAY)

    assert [lane.name for lane in cleared] == ['labs']
    assert [(lane.name, lane.available) for lane in owed] == [('review', False), ('labs', True)]


# --- the pursuits ------------------------------------------------------------


def pursuit_state(balances, touched=()):
    """A minimal `build_state` shaped dict, for the readers that walk one.

    The evidence days come from `evidence.occurrences`, the function
    `build_state` folds them with, so they are the type the real state carries.
    """
    return {
        'now': NOW,
        'today': TODAY,
        'active': {name: {'description': f'do {name}'} for name in balances},
        'balance': dict(balances),
        'checkoff_size': dict.fromkeys(balances, 1.0),
        'intervals': dict.fromkeys(balances, 3.0),
        'evidence_days': evidence.occurrences({'pursuits': {name: {'dates': [TODAY.isoformat()]} for name in touched}}),
        'observed': {},
        'records': [],
    }


def done_record(pursuit: str, when: datetime, **extra) -> dict:
    return {'event': journal.Event.DONE, 'pursuit': pursuit, 'occurred_at': when.isoformat(), **extra}


def test_a_pursuit_is_done_from_either_record():
    """The journal answers what got typed and evidence answers what apps saw.

    A pursuit satisfied inside its own CLI appears only in the second, and it
    takes its place in the order at the time its app saw it.
    """
    state = pursuit_state(balances={'build': 0.0, 'chore': 0.0, 'read': 0.0}, touched=['build'])
    state['observed'] = {'build': NOW - timedelta(hours=2)}
    state['records'] = [
        done_record('chore', NOW),
        done_record('read', NOW - timedelta(days=3)),
        {'event': journal.Event.SKIP, 'pursuit': 'read', 'occurred_at': NOW.isoformat()},
    ]

    lane = today.pursuits_done(state, TODAY)

    assert [cell.text for cell in lane.grid] == ['build', 'chore']


def test_evidence_adds_nothing_for_a_pursuit_also_typed_today():
    state = pursuit_state(balances={'read': 0.0}, touched=['read'])
    state['records'] = [done_record('read', NOW)]

    assert [cell.text for cell in today.pursuits_done(state, TODAY).grid] == ['read']


def test_each_entry_typed_today_is_its_own_row_with_its_minutes():
    """A timed pursuit logged without `--minutes` records no duration on
    purpose, so it gets no number rather than a zero."""
    state = pursuit_state(balances={'read': 0.0, 'chore': 0.0})
    state['records'] = [
        done_record('read', NOW - timedelta(hours=3), duration_minutes=45),
        done_record('chore', NOW - timedelta(hours=1)),
        done_record('read', NOW, duration_minutes=30),
        done_record('read', NOW - timedelta(days=1), duration_minutes=90),
    ]

    assert [cell.text for cell in today.pursuits_done(state, TODAY).grid] == ['read · 45 min', 'chore', 'read · 30 min']


def test_a_pursuit_no_longer_in_the_register_is_not_listed():
    """A journal is history and the register is now. Commenting one out takes
    it off today."""
    state = pursuit_state(balances={'build': 0.0})
    state['records'] = [done_record('retired', NOW)]

    assert today.pursuits_done(state, TODAY).grid == []


def test_the_pursuits_owed_run_furthest_behind_first_and_carry_a_handle():
    lane = today.pursuits_due(pursuit_state(balances={'chore': 1.0, 'socialize': 4.0}))

    assert [row.label for row in lane.rows] == ['socialize', 'chore']
    assert lane.rows[0].handle == 'doit log socialize'


def test_a_pursuit_that_is_current_is_not_owed():
    """`owing` carries why the threshold is a whole checkoff."""
    assert today.pursuits_due(pursuit_state(balances={'chore': 0.4})).rows == []


def test_a_pursuit_with_no_interval_is_owed_rather_than_dropped():
    """`priced` returns None for a name the schedule does not reach, and a
    pursuit owing a checkoff is owed whether or not a date can be put on it.

    It sorts last among the owed, because a row with no date cannot be compared
    against one that has a date and guessing an order would be inventing one.
    """
    state = pursuit_state(balances={'chore': 1.0, 'undated': 1.0})
    del state['intervals']['undated']

    lane = today.pursuits_due(state)

    assert [row.label for row in lane.rows] == ['chore', 'undated']
    assert lane.rows[1].note == 'due today'


def test_a_register_that_will_not_load_costs_its_two_groups_rather_than_the_screen(monkeypatch):
    """The failure goes to the due list, where a missing group reads as nothing owed."""

    def refuses():
        raise ValueError('pursuits.yml: weight must be a number')

    monkeypatch.setattr(today.pursuits, 'load_pursuits', refuses)

    moved, owed = today.pursuit_lanes(NOW, TODAY)

    assert moved == []
    assert [(lane.name, lane.available) for lane in owed] == [('pursuits', False)]


def test_an_empty_register_contributes_nothing_and_is_not_an_error(monkeypatch):
    monkeypatch.setattr(today.pursuits, 'load_pursuits', dict)

    assert today.pursuit_lanes(NOW, TODAY) == ([], [])


# --- the screen --------------------------------------------------------------


def test_a_group_past_three_shows_three_then_how_many_more_and_where():
    lane = Lane(name='labs', title='LABS', rows=[Row(str(n), '') for n in range(5)], total=5, hints=['doit labs due'])

    rows = today.visible_rows(lane)

    assert [row.label for row in rows] == ['0', '1', '2', '2 more']
    assert rows[-1].handle == 'doit labs due'


def test_a_group_of_three_needs_no_more_row():
    lane = Lane(name='labs', title='LABS', rows=[Row(str(n), '') for n in range(3)], total=3, hints=['doit labs due'])

    assert len(today.visible_rows(lane)) == 3


def test_a_group_that_answered_with_nothing_prints_nothing_while_one_that_failed_does(capsys):
    day = today.Day(
        done=[Lane(name='articles', title='ARTICLES'), today.done_lane('tasks', 'TASKS', [done('Pumice Stone')], '')],
        due=[Lane(name='labs', title='LABS'), lanes.unavailable('habits', 'HABITS', 'icb: not logged in')],
    )

    today.render(day, TODAY)
    out = capsys.readouterr().out

    assert 'articles' not in out
    assert 'labs' not in out
    assert 'Pumice Stone' in out
    assert 'unavailable — icb: not logged in' in out


def test_an_entry_with_no_hour_leaves_the_column_blank_and_lines_up(capsys):
    lane = today.done_lane('review', 'REVIEW', [done('Renew certs'), done('Upgrade brew', NOW.isoformat())], '')

    today.render(today.Day([lane], []), TODAY)
    lines = capsys.readouterr().out.splitlines()

    assert lines[-2:] == [f'    {NOW:%H:%M}  Upgrade brew', '           Renew certs']


def test_a_group_name_is_blue_and_a_done_hour_green_while_the_text_stays_plain():
    """A style set on a line's first Text is inherited by everything appended
    to it, which is how a whole row ends up one color."""
    heading = today.group_heading(Lane(name='habits', title='HABITS'))
    line = today.done_line(done('Floss', NOW.isoformat()), 40)

    assert str(heading.style) == today.GROUP_STYLE
    assert str(line.style) == ''
    assert [(line.plain[span.start : span.end], str(span.style)) for span in line.spans if str(span.style)] == [
        (f'{NOW:%H:%M}', today.HOUR_STYLE)
    ]


def test_one_group_follows_another_with_no_blank_line_between(capsys):
    day = today.Day(
        done=[
            today.done_lane('habits', 'HABITS', [done('Floss', NOW.isoformat())], ''),
            today.done_lane('tasks', 'TASKS', [done('Pumice Stone', NOW.isoformat())], ''),
        ],
        due=[],
    )

    today.render(day, TODAY)

    assert '' not in capsys.readouterr().out.splitlines()


# --- the whole document, composed ---------------------------------------------


@pytest.fixture
def no_backends(monkeypatch):
    """Everything `build` reads off this machine, answered with nothing.

    `build` is asserted here and not only its parts. A unit test can satisfy
    the sentence its own name makes while the composition above it breaks that
    same sentence. `pursuit_lanes` promises a failure costs two groups; only
    `build` decides whether the rest of the screen survives.

    An empty registry shells out to no backend, so what is left is doit's own
    state, and each read is replaced rather than pointed at a temporary file.
    """
    monkeypatch.setattr(today.sources, 'load', sources.Registry)
    monkeypatch.setattr(today.dashboard, 'local_lanes', list)
    monkeypatch.setattr(today.review, 'statuses', list)
    monkeypatch.setattr(today.labs, 'statuses', list)
    monkeypatch.setattr(today.pursuits, 'load_pursuits', dict)


def pursuits_answer(state):
    return lambda now, today_: ([today.pursuits_done(state, TODAY)], [today.pursuits_due(state)])


def test_each_list_keeps_its_groups_in_one_order(no_backends, monkeypatch):
    """`build` carries why the due list runs appointments, pursuits, registers,
    then sets. A done group keeps its place all day for the reason a habit does
    on the dashboard: finishing something never shuffles the rest."""
    monkeypatch.setattr(today.dashboard, 'lanes_of', lambda registry, results, wanted: [habits_view(), upcoming_view(DENTIST)])
    monkeypatch.setattr(today, 'pursuit_lanes', pursuits_answer(pursuit_state(balances={'chore': 1.0})))

    day = today.build(sources.Registry(), NOW)

    assert [lane.name for lane in day.done] == ['habits', 'pursuits', 'review', 'labs']
    assert [lane.name for lane in day.due] == ['upcoming', 'pursuits', 'review', 'labs', 'habits']


def test_a_register_that_will_not_load_leaves_the_rest_standing(no_backends, monkeypatch):
    """One subsystem's failure costs its own groups, never the screen."""

    def refuses():
        raise ValueError('pursuits.yml: weight must be a number')

    monkeypatch.setattr(today.pursuits, 'load_pursuits', refuses)
    monkeypatch.setattr(today.review, 'statuses', lambda: [{'id': 'rg', 'overdue': 2, 'last': None}])

    day = today.build(sources.Registry(), NOW)

    assert [(lane.name, lane.available) for lane in day.due] == [('pursuits', False), ('review', True), ('labs', True)]
    assert [row.label for row in day.due[1].rows] == ['rg']


def test_the_json_carries_both_lists_with_each_lane_in_the_contract_shape(no_backends, monkeypatch, capsys):
    """Two lists, because a record appears in both under one name. Each lane
    reads back through the contract's own parser, so no consumer learns a second
    lane shape."""
    state = pursuit_state(balances={'chore': 1.0})
    state['records'] = [done_record('chore', NOW)]
    monkeypatch.setattr(today, 'pursuit_lanes', pursuits_answer(state))

    assert today.cmd_today(as_json=True) == 0
    document = json.loads(capsys.readouterr().out)

    assert document['schema_version'] == lanes.SCHEMA_VERSION
    [moved, *_] = lanes.from_document({'lanes': document['done']})
    assert [cell.text for cell in moved.grid] == ['chore']
    assert datetime.fromisoformat(moved.grid[0].done_at) == NOW
    assert [lane['name'] for lane in document['due']] == ['pursuits', 'review', 'labs']
