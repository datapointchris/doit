"""Tests for doit.today — the due list, the screen, and the day composed from the fold.

The done list is `doit.happened`'s fold over the day, tested there. What is
tested here is what today adds: what is still owed, and how the two lists sit
together.

`build` only ever runs here with every backend replaced. It shells out to every
configured source, and a test that let it would be measuring whichever backends
the machine running the suite happens to have authenticated.
"""

import datetime as dt
import json
from types import SimpleNamespace

import pytest

from doit import allocate
from doit import dashboard
from doit import evidence
from doit import happened
from doit import journal
from doit import lanes
from doit import sources
from doit import today
from doit import window
from doit.lanes import GridCell
from doit.lanes import Lane
from doit.lanes import Row
from doit.lanes import Urgency

# Noon, so a test can move a stamp several hours either way without crossing
# midnight and silently changing which day it lands on.
NOW = dt.datetime(2026, 7, 24, 12, 0, 0).astimezone()
TODAY = NOW.date()
DAY = window.day_of(NOW)


def done(text: str, done_at: str = '') -> GridCell:
    return GridCell(text, True, done_at=done_at)


# --- a day set ---------------------------------------------------------------


def lane_view(name, cells, meta='', hints=()):
    return dashboard.LaneView(name=name, title=name.upper(), meta=meta, grid=list(cells), hints=list(hints))


def habits_view():
    return lane_view(
        'habits',
        [GridCell('Floss', False, '2'), GridCell('Water', True, done_at='2026-07-24T13:00:00Z'), GridCell('Walk', False, '5')],
        hints=['icb habits complete <id>'],
    )


def test_a_habit_left_carries_the_command_that_completes_it():
    """The dashboard prints the bare id beside a hint holding `<id>`. A due row
    has one handle column, so the two arrive joined."""
    [left] = today.set_lanes([habits_view()])

    assert [(row.label, row.handle) for row in left.rows] == [('Floss', 'icb habits complete 2'), ('Walk', 'icb habits complete 5')]


def test_a_grid_is_a_day_set_only_when_it_is_named_as_one():
    """A grid says "the whole set", not "due today". A weekly set emitted as one
    would otherwise land here as a week of work owed this afternoon."""
    assert today.set_lanes([lane_view('chores', [GridCell('Vacuum', False)])]) == []


def test_a_day_set_that_could_not_be_read_is_reported_where_it_is_owed():
    """Found by having a grid, a failed lane has none and would leave the due list without a word."""
    [left] = today.set_lanes([lanes.unavailable('habits', 'HABITS', 'icb: not logged in')])

    assert (left.name, left.available) == ('habits', False)


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


def owed(review_rows=(), lab_rows=()):
    """`maintenance_lanes` over the given statuses, so each test reaches the real table of how an item is described and acted on."""
    kept = happened.Kept(registers={'review': list(review_rows), 'labs': list(lab_rows)})
    return today.maintenance_lanes(kept, DAY, NOW)


def test_a_register_owes_what_today_has_not_cleared():
    """An item on a daily cadence done this morning still reports `overdue: 0`,
    so without subtracting what today cleared it is listed as done and owed at once."""
    rows = [
        {'id': 'brew', 'desc': 'Upgrade brew', 'overdue': 0, 'last': TODAY.isoformat()},
        {'id': 'certs', 'desc': 'Renew certs', 'overdue': 3, 'last': '2026-07-01'},
        {'id': 'fresh', 'overdue': -2, 'last': '2026-07-23'},
        {'id': 'never', 'overdue': None, 'last': None},
    ]

    [review, _] = owed(review_rows=rows)

    assert [row.label for row in review.rows] == ['never', 'certs'], 'never run ranks above any number of days late'
    assert [row.note for row in review.rows] == ['never run', '3d overdue']


def test_a_review_item_is_named_by_what_it_is_for_and_carries_its_own_command():
    [review, _] = owed(review_rows=[{'id': 'certs', 'desc': 'Renew certs', 'command': 'certbot renew', 'overdue': 3, 'last': None}])

    assert review.rows == [Row('certs', 'Renew certs', '3d overdue', Urgency.OVERDUE, 'certbot renew')]


def test_an_item_due_today_reads_as_due_rather_than_late():
    [review, _] = owed(review_rows=[{'id': 'brew', 'overdue': 0, 'last': '2026-07-17'}])

    assert review.rows[0].urgency == Urgency.DUE


def test_an_on_demand_lab_is_never_owed():
    """A Lab is due only when it is scheduled, which review items always are.

    A rule written here in place of `labs.is_due_row` would read an on-demand
    Lab's absent `overdue` as never done, and owe it every day forever.
    """
    rows = [
        {'id': 'tmux', 'title': 'Panes and sessions', 'overdue': None, 'last': None, 'scheduled': True},
        {'id': 'jq', 'overdue': None, 'last': None, 'scheduled': False},
    ]

    [_, labs] = owed(lab_rows=rows)

    assert [(row.label, row.text, row.handle) for row in labs.rows] == [('tmux', 'Panes and sessions', 'doit labs show tmux')]


def test_a_register_that_cannot_be_read_is_reported_where_it_is_owed():
    kept = happened.Kept(registers={'labs': []}, register_errors={'review': 'state.json: permission denied'})

    assert [(lane.name, lane.available) for lane in today.maintenance_lanes(kept, DAY, NOW)] == [('review', False), ('labs', True)]


# --- the pursuits ------------------------------------------------------------


def pursuit_state(balances, touched=()):
    """A minimal `build_state` shaped dict, for the readers that walk one.

    The evidence days come from `evidence.occurrences`, the function
    `build_state` folds them with, so they are the type the real state carries.
    """
    active = {name: {'description': f'do {name}'} for name in balances}
    return {
        'now': NOW,
        'today': TODAY,
        'active': active,
        'tracked': {},
        'watched': active,
        'balance': dict(balances),
        'checkoff_size': dict.fromkeys(balances, 1.0),
        'weekly_minutes': {},
        'intervals': dict.fromkeys(balances, 3.0),
        'due': {name: allocate.days_until_due(owed, 3.0, 1.0) for name, owed in balances.items()},
        'origins': dict.fromkeys(balances, NOW - dt.timedelta(days=30)),
        'done': dict.fromkeys(balances, 0.0),
        'suppressed': [],
        'evidence_days': evidence.occurrences({'pursuits': {name: {'dates': [TODAY.isoformat()]} for name in touched}}),
        'observed': {},
        'records': [],
    }


def done_record(pursuit: str, when: dt.datetime, **extra) -> dict:
    return {'event': journal.Event.DONE, 'pursuit': pursuit, 'occurred_at': when.isoformat(), **extra}


def test_the_pursuits_owed_run_furthest_behind_first_and_carry_a_handle():
    lane = today.pursuits_due(pursuit_state(balances={'chore': 1.0, 'socialize': 4.0}), DAY)

    assert [row.label for row in lane.rows] == ['socialize', 'chore']
    assert lane.rows[0].handle == 'doit log socialize'


def test_a_pursuit_that_is_current_is_not_owed():
    """`owing` carries why the threshold is a whole checkoff."""
    assert today.pursuits_due(pursuit_state(balances={'chore': 0.4}), DAY).rows == []


def test_a_pursuit_done_today_is_off_the_due_list_while_it_still_owes():
    """Logging it checks it off for the day, from either record. Yesterday's
    log checks off nothing today."""
    state = pursuit_state(balances={'chore': 4.0, 'read': 4.0, 'build': 4.0, 'study': 4.0}, touched=['read'])
    state['records'] = [done_record('chore', NOW), done_record('study', NOW - dt.timedelta(days=1))]

    assert [name for name, _ in today.owing(state)] == ['chore', 'read', 'build', 'study']
    assert [row.label for row in today.pursuits_due(state, DAY).rows] == ['build', 'study']


def test_a_skipped_pursuit_is_not_listed_as_owed():
    """`doit next` says skipped and does not pin it, so this list agrees."""
    state = pursuit_state(balances={'chore': 2.0, 'read': 2.0})
    state['suppressed'] = ['chore']

    assert [name for name, _ in today.owing(state)] == ['read']


def test_a_pursuit_with_no_interval_is_owed_rather_than_dropped():
    """`priced` returns None for a name the schedule does not reach, and a
    pursuit owing a checkoff is owed whether or not a date can be put on it.

    It sorts last among the owed, because a row with no date cannot be compared
    against one that has a date and guessing an order would be inventing one.
    """
    state = pursuit_state(balances={'chore': 1.0, 'undated': 1.0})
    del state['intervals']['undated']
    state['due']['undated'] = None

    lane = today.pursuits_due(state, DAY)

    assert [row.label for row in lane.rows] == ['chore', 'undated']
    assert lane.rows[1].note == 'due today'


def test_a_register_that_will_not_load_is_reported_where_it_is_owed():
    """The failure goes to the due list, where a missing group reads as nothing owed."""
    [lane] = today.pursuit_lanes(happened.Kept(state_error='pursuits.yml: weight must be a number'), DAY)

    assert (lane.name, lane.available) == ('pursuits', False)


def test_an_empty_register_contributes_nothing_and_is_not_an_error():
    assert today.pursuit_lanes(happened.Kept(), DAY) == []


# --- the screen --------------------------------------------------------------


def test_a_group_past_three_shows_three_then_how_many_more_and_where():
    lane = Lane(name='labs', title='LABS', rows=[Row(str(n), '') for n in range(5)], total=5, hints=['doit labs due'])

    rows = today.visible_rows(lane)

    assert [row.label for row in rows] == ['0', '1', '2', '2 more']
    assert rows[-1].handle == 'doit labs due'


@pytest.mark.parametrize('name', today.ROUTINE_GROUPS)
def test_a_routine_group_shows_ten_before_its_more_row(name):
    lane = Lane(name=name, title=name.upper(), rows=[Row(str(n), '') for n in range(12)], total=12, hints=['h'])

    assert [row.label for row in today.visible_rows(lane)] == [*map(str, range(10)), '2 more']


def test_still_due_prints_above_done(capsys):
    day = today.Day(
        done=[happened.done_lane('tasks', 'TASKS', [done('Pumice Stone')], '')],
        due=[Lane(name='labs', title='LABS', rows=[Row('a-lab', 'Work through it')], total=1)],
    )

    today.render(day, TODAY)
    lines = capsys.readouterr().out.splitlines()

    assert lines.index('STILL DUE') < lines.index('DONE')


def test_a_handle_too_wide_for_its_row_is_printed_whole_beneath_it(monkeypatch, capsys):
    monkeypatch.setenv('COLUMNS', '80')
    wide = 'python ~/.config/hooks/a-report-with-a-long-name --since 7d'
    lane = Lane(
        name='review',
        title='REVIEW',
        rows=[
            Row('a-review', 'Read the report', 'never run', Urgency.OVERDUE, wide),
            Row('labs', 'Work through a Lab', 'never run', handle='doit labs choose'),
        ],
        total=2,
    )

    today.render_due([lane], 80)

    lines = capsys.readouterr().out.splitlines()
    assert lines[1].rstrip().endswith('never run'), 'the row keeps its own line'
    assert lines[2].strip() == f'↳ {wide}'
    assert lines[3].endswith('↳ doit labs choose'), 'a handle that fits stays inline'


def test_a_long_label_leaves_the_text_its_floor_beside_an_inline_handle(monkeypatch, capsys):
    monkeypatch.setenv('COLUMNS', '80')
    text = 'Work through a Lab you are due on'
    lane = Lane(
        name='labs',
        title='LABS',
        rows=[Row('x' * today.DUE_LABEL_MAX, text, 'never run', Urgency.OVERDUE, 'doit labs show a-lab')],
        total=1,
    )

    today.render_due([lane], 80)

    assert text[: today.DUE_TEXT_MIN - 1] in capsys.readouterr().out


def test_a_group_that_answered_with_nothing_prints_nothing_while_one_that_failed_does(capsys):
    day = today.Day(
        done=[Lane(name='articles', title='ARTICLES'), happened.done_lane('tasks', 'TASKS', [done('Pumice Stone')], '')],
        due=[Lane(name='labs', title='LABS'), lanes.unavailable('habits', 'HABITS', 'icb: not logged in')],
    )

    today.render(day, TODAY)
    out = capsys.readouterr().out

    assert 'articles' not in out
    assert 'labs' not in out
    assert 'Pumice Stone' in out
    assert 'unavailable — icb: not logged in' in out


def test_one_group_follows_another_with_no_blank_line_between(capsys):
    day = today.Day(
        done=[
            happened.done_lane('habits', 'HABITS', [done('Floss', NOW.isoformat())], ''),
            happened.done_lane('tasks', 'TASKS', [done('Pumice Stone', NOW.isoformat())], ''),
        ],
        due=[],
    )

    today.render(day, TODAY)

    assert '' not in capsys.readouterr().out.splitlines()


# --- the whole document, composed ---------------------------------------------

HABITS_SOURCE = sources.Source(id='icb-habits', command=['icb'], adapter='icb-habits')


@pytest.fixture
def no_backends(monkeypatch):
    """Everything `build` reads off this machine, answered with nothing but one habit done.

    `build` is asserted here and not only its parts. A unit test can satisfy
    the sentence its own name makes while the composition above it breaks that
    same sentence.
    """
    monkeypatch.setattr(today.sources, 'load', lambda: sources.Registry(completions={'icb-habits': HABITS_SOURCE}))
    monkeypatch.setattr(
        today.sources,
        'fetch',
        lambda asked, bounds: {
            'icb-habits': sources.Result('icb-habits', [{'name': 'Water', 'complete_date': TODAY.isoformat()}], 0, bounds=bounds)
        },
    )
    monkeypatch.setattr(today.dashboard, 'lanes_of', lambda registry, results, wanted: [habits_view(), upcoming_view(DENTIST)])
    frozen = type('Frozen', (dt.datetime,), {'now': classmethod(lambda cls, tz=None: NOW)})
    for module in (today, happened):
        monkeypatch.setattr(module, 'dt', SimpleNamespace(**vars(dt) | {'datetime': frozen}))


def kept_with(state=None, **registers):
    return lambda now: happened.Kept(state=state, registers={'review': [], 'labs': [], **registers})


def test_each_list_keeps_its_groups_in_one_order(no_backends, monkeypatch):
    """`build` carries why the due list runs appointments, pursuits, registers,
    then sets. The done list leads with the day sets, so finishing something
    never shuffles the rest."""
    state = pursuit_state(balances={'chore': 1.0})
    state['records'] = [done_record('chore', NOW)]
    monkeypatch.setattr(happened, 'read_kept', kept_with(state))

    day = today.build(today.sources.load(), NOW)

    assert [lane.name for lane in day.done] == ['habits', 'pursuits', 'review', 'labs']
    assert [lane.name for lane in day.due] == ['upcoming', 'pursuits', 'review', 'labs', 'habits']
    assert [cell.text for cell in day.done[0].grid] == ['Water'], 'the done habits are the fold, not the dashboard grid'


def test_a_group_that_could_not_be_read_is_said_once_in_the_due_list(no_backends, monkeypatch):
    """The fold lists it as unavailable too, and the screen would print the same failure in both lists."""
    monkeypatch.setattr(happened, 'read_kept', lambda now: happened.Kept(state_error='pursuits.yml: weight must be a number'))

    day = today.build(today.sources.load(), NOW)

    assert 'pursuits' not in [lane.name for lane in day.done]
    assert [(lane.name, lane.available) for lane in day.due][:2] == [('upcoming', True), ('pursuits', False)]


def test_the_json_carries_both_lists_with_each_lane_in_the_contract_shape(no_backends, monkeypatch, capsys):
    """Two lists, because a record appears in both under one name. Each lane
    reads back through the contract's own parser, so no consumer learns a second
    lane shape."""
    state = pursuit_state(balances={'chore': 1.0})
    state['records'] = [done_record('chore', NOW, actor='ada@laptop')]
    monkeypatch.setattr(happened, 'read_kept', kept_with(state))

    assert today.cmd_today(as_json=True) == 0
    document = json.loads(capsys.readouterr().out)

    assert document['schema_version'] == lanes.SCHEMA_VERSION
    [_, moved, *_] = lanes.from_document({'lanes': document['done']})
    assert [(cell.text, cell.actor, cell.done_on) for cell in moved.grid] == [('chore', 'ada@laptop', TODAY.isoformat())]
    assert dt.datetime.fromisoformat(moved.grid[0].done_at) == NOW
    assert [lane['name'] for lane in document['due']] == ['upcoming', 'pursuits', 'review', 'labs', 'habits']
