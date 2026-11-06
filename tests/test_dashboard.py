"""Tests for doit.dashboard — lane composition, degradation, and the JSON model.

Backends are never invoked here: lanes are built from committed fixture payloads
at a fixed date, and the subprocess layer is exercised with fabricated commands.
Nothing in this file touches the network.

The module binds no paths at import time — every backend is a subprocess — so
unlike its siblings it needs no fixture repointing at all.
"""

import datetime as dt
import json
from pathlib import Path

import pytest

from doit import dashboard
from doit import happened
from doit import render
from doit import sources
from doit.lanes import GridCell
from doit.lanes import Lane
from doit.lanes import Row
from doit.lanes import Urgency

FIXTURE_DIR = Path(__file__).resolve().parent / 'fixtures' / 'dashboard'

TODAY = dt.date(2026, 7, 24)


def fixture(name: str):
    return json.loads((FIXTURE_DIR / name).read_text())


def ok(name: str, source: str = ''):
    return sources.Result(source=source, payload=fixture(name), exit_code=0)


def all_results() -> dict:
    return {
        'icb': ok('icb-overview.json'),
        'learning': ok('learning-overview.json'),
        'review': ok('review-list.json'),
    }


def lanes_by_name(results: dict) -> dict:
    """Every lane, built at a fixed date.

    The builders are called directly rather than through the adapters, because
    an adapter reads today's date and these fixtures are pinned to one.
    """
    built = [build(results, TODAY) for _, build in dashboard.ICB_LANES]
    built.append(dashboard.build_learning_lane(results, TODAY))
    built.append(dashboard.build_maintenance_lane(results, TODAY))
    return {lane.name: lane for lane in built}


def test_every_lane_builds_from_fixtures():
    lanes = lanes_by_name(all_results())

    covered = {name for name, _ in dashboard.ICB_LANES} | {'learning', *dashboard.LOCAL_LANES}

    assert set(lanes) == covered
    assert all(lane.available for lane in lanes.values())


# The least payload each adapter accepts: a mapping for the two reading an
# overview document, a list for the four reading a listing. Enough to reach the
# branch that builds rows, which is a different naming site from the one a
# failure takes.
ADAPTER_PAYLOADS: dict[str, dict | list] = {
    'icb': {},
    'learning': {},
    'meso': [],
    'meso-feedback': [],
    'nomad': {},
    'prs': [],
    'dotfiles': [],
}


def test_every_shipped_adapter_names_the_lanes_it_declares():
    """What `--lane` offers, against what the adapters actually build.

    Derived rather than listed. A hand-written enum and a hand-written test
    population agree with each other while both omit the same lane, and nothing
    surfaces the omission.

    Both branches of every builder, because a lane is named twice — once where it
    builds rows and once where it reports itself unavailable. One site is reached
    only when the source answers and the other only when it fails, so a walk over
    either alone passes while the two disagree.

    The title is asserted in the same walk. It is the word printed above the rows
    and the name is the word typed at `--lane`, so a reader who copies the header
    has to get a lane back.
    """
    for adapter_id, adapter, declared in dashboard.SHIPPED_ADAPTERS:
        answered = sources.Result(source=adapter_id, payload=ADAPTER_PAYLOADS[adapter_id], exit_code=0)
        broke = sources.Result(source=adapter_id, exit_code=1, stderr='no', failure=sources.Failure.FAILED)

        for result in (answered, broke):
            built = adapter(result)

            assert [lane.name for lane in built] == list(declared), f'{adapter_id} builds {[x.name for x in built]}'
            for lane in built:
                assert lane.title == lane.name.upper(), f'{lane.title} is headed differently from `--lane {lane.name}`'


def test_the_lane_enum_covers_every_adapter_doit_ships():
    """A lane nobody can discover is a lane that does not exist for the reader.

    Containment rather than equality. `doit.happened` registers completion
    adapters into the same global registry, and whether they are present here
    depends on whether anything has imported that module yet — so an equality
    check passes or fails on test ordering rather than on the invariant. What
    `--lane` needs is that every dashboard adapter is reachable, and
    `test_the_two_adapter_tables_do_not_overlap` holds the other half.
    """
    registered = set(sources.ADAPTERS)
    tabled = {adapter_id for adapter_id, *_ in dashboard.SHIPPED_ADAPTERS}

    assert tabled <= registered, 'a tabled adapter that never registered is one `--lane` cannot reach'
    assert set(dashboard.LOCAL_LANES) <= set(dashboard.LANE_NAMES)


def test_the_two_adapter_tables_do_not_overlap():
    """One id cannot answer two questions, because registration is last-wins.

    The dashboard's adapters and the completion adapters share `sources.ADAPTERS`, so a name in
    both would have one silently replace the other at import time — and which
    won would depend on import order.
    """
    dashboard_ids = {adapter_id for adapter_id, *_ in dashboard.SHIPPED_ADAPTERS}
    completion_ids = {adapter_id for adapter_id, _ in happened.COMPLETION_ADAPTERS}

    assert not dashboard_ids & completion_ids, 'an id in both tables is one registration overwriting the other'


def test_tasks_lane_labels_a_pin_or_the_queue_position():
    lane = lanes_by_name(all_results())['tasks']

    assert lane.meta == '12 open'
    assert lane.rows[0].label == 'pin'
    assert lane.rows[0].text == 'Renew passport'
    assert lane.rows[0].note == 'chore'
    assert lane.rows[2].label == '#3', 'a bare numeral in a gutter of words reads as a row number'


def test_tasks_lane_labels_a_v2_row_by_position():
    """A v2 payload carries `priority` and no `pinned`; it still renders."""
    payload = fixture('icb-overview.json')
    payload['schema_version'] = 2
    for task in payload['tasks']['items']:
        del task['pinned']
        task['priority'] = task['id']
    results = {'icb': sources.Result(source='icb', payload=payload, exit_code=0)}

    lane = dashboard.build_tasks_lane(results, TODAY)

    assert [row.label for row in lane.rows] == ['#1', '#2', '#3', '#4']


def test_habits_lane_shows_every_habit_as_a_grid():
    """Every current habit is due every day, so the useful view is the whole set
    with the finished ones ticked off — not a ranked excerpt."""
    lane = lanes_by_name(all_results())['habits']

    assert lane.meta == '1 of 4 done today'
    assert lane.rows == [], 'habits are a complete set, not ranked rows'
    assert [cell.text for cell in lane.grid] == ['Stretch', 'Read', 'Journal', 'Exercise']
    assert [cell.done for cell in lane.grid] == [False, False, False, True]


def test_habits_grid_carries_the_id_you_would_type():
    """`icb habits complete` takes an id, so the grid has to show one."""
    lane = lanes_by_name(all_results())['habits']

    outstanding = {cell.text: cell.handle for cell in lane.grid if not cell.done}
    assert outstanding == {'Stretch': '1', 'Journal': '3', 'Read': '2'}
    completed = [cell for cell in lane.grid if cell.done]
    assert completed[0].handle == '', 'a completion identifies the completion, not the habit'


def test_a_finished_habit_carries_when_it_was_done():
    """`doit today` lists what the day had in the order it happened, and the
    completion record is the only thing that knows the hour."""
    lane = lanes_by_name(all_results())['habits']

    assert {cell.text: cell.done_at for cell in lane.grid} == {
        'Stretch': '',
        'Read': '',
        'Journal': '',
        'Exercise': '2026-07-24T12:00:00Z',
    }


def test_habits_grid_keeps_a_completed_habit_in_place():
    """Ordering by habit id, never by completion, so ticking one off never
    shuffles the rest out from under you."""
    before = dashboard.build_habits_lane(all_results(), TODAY)

    payload = fixture('icb-overview.json')
    stretch = payload['habits']['due_today'].pop(0)
    # What icb records is a completion naming the habit, not the habit row itself.
    payload['habits']['completed_today'].append(
        {
            'id': 91,
            'habit_id': stretch['id'],
            'name': stretch['name'],
            'category_id': stretch['category_id'],
            'category': stretch['category'],
            'complete_date': '2026-07-24T13:00:00Z',
        }
    )
    results = {'icb': sources.Result(source='icb', payload=payload, exit_code=0)}
    after = dashboard.build_habits_lane(results, TODAY)

    assert [cell.text for cell in after.grid] == [cell.text for cell in before.grid]
    assert [cell.done for cell in after.grid] == [True, False, False, True]


def test_habits_grid_sends_a_completion_with_no_habit_to_the_end():
    """A completion whose habit was deleted has no id to place it by, and going
    first would push every habit you still owe down a row."""
    payload = fixture('icb-overview.json')
    payload['habits']['completed_today'].append(
        {
            'id': 92,
            'name': 'Retired habit',
            'category_id': 2,
            'category': {'id': 2, 'name': 'Health'},
            'complete_date': '2026-07-24T14:00:00Z',
        }
    )
    results = {'icb': sources.Result(source='icb', payload=payload, exit_code=0)}

    lane = dashboard.build_habits_lane(results, TODAY)

    assert [cell.text for cell in lane.grid][-1] == 'Retired habit'


def test_books_lane_leads_with_one_of_each_kind():
    """With the small row cap, concatenation would hide what is next behind the
    books already in progress."""
    lane = lanes_by_name(all_results())['books']

    assert [row.label for row in lane.rows[:3]] == ['reading', 'next', 'reading']
    assert lane.meta == '2 reading · 40 queued', 'the backlog is context, not rows you are behind on'


def test_books_total_spans_the_whole_pile_not_the_rows_built():
    """The lane's reported size is the pile that exists, not the handful of rows
    round-robin happened to build."""
    lane = lanes_by_name(all_results())['books']

    assert lane.total == 42, '2 in progress + 40 queued'


def test_articles_are_their_own_lane():
    """Books and articles are separate apps; one section spanning both meant
    neither count described the pile above it."""
    lane = lanes_by_name(all_results())['articles']

    assert lane.meta == '60 unread'
    assert lane.rows[0].label == 'reading'
    assert [row.label for row in lane.rows[1:]] == ['next', 'next', 'next']
    assert lane.total == 61, '60 queued plus the one being read'


def articles_lane_with(**fields) -> Lane:
    """The articles lane built from the fixture with its section patched.

    The committed fixture carries neither read field, so patching here is what
    keeps `test_articles_are_their_own_lane` a live guard on the older payload.
    """
    payload = fixture('icb-overview.json')
    payload['articles'].update(fields)
    results = all_results()
    results['icb'] = sources.Result(source='', payload=payload, exit_code=0)
    return lanes_by_name(results)['articles']


def test_reads_inside_the_window_are_reported_as_a_rate():
    """An unread count only climbs, so the lane says whether any of it moves."""
    lane = articles_lane_with(read_last_30_days=3, last_read_at='2026-07-22T00:24:29Z')

    assert lane.meta == '60 unread · 3 read in 30d'


def test_no_reads_in_the_window_makes_the_date_the_thing_worth_saying():
    """A zero is the answer to "am I getting to these", and a bare zero does not
    say how long it has been."""
    lane = articles_lane_with(read_last_30_days=0, last_read_at='2026-04-02T20:36:18Z')

    assert lane.meta == '60 unread · last read 4mo ago'


def test_a_recent_last_read_keeps_the_day_unit():
    lane = articles_lane_with(read_last_30_days=0, last_read_at='2026-07-20T09:00:00Z')

    assert lane.meta == '60 unread · last read 4d ago'


def test_an_article_list_nobody_has_read_claims_no_rate():
    """Reporting "0 read" over a list never touched adds nothing the backlog
    count did not already carry."""
    lane = articles_lane_with(read_last_30_days=0, last_read_at=None)

    assert lane.meta == '60 unread'


def test_an_unparsable_read_stamp_drops_the_clause_rather_than_saying_never():
    lane = articles_lane_with(read_last_30_days=0, last_read_at='not-a-date')

    assert lane.meta == '60 unread'


def test_article_titles_are_collapsed_onto_one_line():
    """Scraped titles arrive wrapped in the source page's newlines and tabs."""
    lane = lanes_by_name(all_results())['articles']

    assert lane.rows[0].text == 'An Article Being Read'


def test_clean_collapses_any_whitespace():
    assert dashboard.clean('\n\tRAG on structured data\n') == 'RAG on structured data'
    assert dashboard.clean('  spaced   out  ') == 'spaced out'
    assert dashboard.clean(None) == ''


def test_learning_lane_is_the_plan_head_in_its_order():
    """Started resources lead, and what follows fills the head, each noted with its stream."""
    lane = lanes_by_name(all_results())['learning']

    assert lane.meta == '2 started · 3 next'
    assert [(row.label, row.text, row.note) for row in lane.rows] == [
        ('open', 'A Started Book', 'First Track'),
        ('open', 'A Started Article', 'A Section'),
        ('start', 'The Next Resource', 'Second Track'),
        ('start', 'The One After Next', 'First Track'),
        ('start', 'The Fifth Resource', 'A Section'),
    ]
    assert lane.rows[0].handle == 'learning resources show 11'


def test_maintenance_lane_counts_only_what_is_due():
    lane = lanes_by_name(all_results())['maintenance']

    # 3 review items due (never-done, overdue, due-today) and 1 lab; the
    # not-yet-due entries are excluded.
    assert lane.meta == '3 reviews · 1 lab due'


def test_maintenance_lane_says_what_an_entry_is_for():
    """The register slug identifies a row to the tooling; what it is for only
    exists in the description."""
    lane = lanes_by_name(all_results())['maintenance']

    assert 'never-done-item' not in {row.text for row in lane.rows}
    assert 'Has never been marked done' in {row.text for row in lane.rows}


def test_maintenance_lane_ranks_by_how_late_a_row_actually_is():
    """Never-run leads because its lateness is unbounded, then the genuinely
    overdue by how late they are — sorting the note text would rank "16d overdue"
    below "9d overdue"."""
    lane = lanes_by_name(all_results())['maintenance']

    assert lane.rows[0].note == 'never run', 'nothing has ever shown this happening'
    reviews = [row.note for row in lane.rows if row.label == 'review']
    assert reviews == ['never run', '16d overdue', 'due today'], 'ranked within a kind; labs interleave'
    assert lane.rows[0].urgency is dashboard.Urgency.OVERDUE, 'never run is a late state, not a neutral one'
    assert lane.rows[-1].urgency is dashboard.Urgency.DUE, 'due today is the least late state'


def test_every_lane_that_can_name_a_handle_does():
    """A row you can read and not act on sends you hunting for the thing it named.

    Projects was the one exception for as long as an item's only id was a UUID.
    It carries a short number now, so there is no lane left without a handle.
    """
    lanes = lanes_by_name(all_results())

    for name in ('tasks', 'books', 'articles', 'upcoming', 'learning', 'projects'):
        assert all(row.handle for row in lanes[name].rows), f'{name} has a row with nothing to act on'
    # Maintenance is the one lane whose handle is the register's to supply, so a
    # review entry that declares no command has none to show.
    assert all(row.handle for row in lanes['maintenance'].rows if row.label == 'lab')


def test_a_handle_is_the_read_verb_not_a_write():
    lanes = lanes_by_name(all_results())

    assert lanes['tasks'].rows[0].handle.startswith('icb tasks show ')
    assert lanes['books'].rows[0].handle.startswith('icb books show ')
    assert lanes['articles'].rows[0].handle.startswith('icb articles show ')
    assert lanes['learning'].rows[0].handle.startswith('learning resources show ')
    assert lanes['projects'].rows[0].handle.startswith('icb projects items show ')


def test_a_row_whose_backend_gave_no_id_offers_no_handle():
    # A handle that would fail reads as something you can run.
    assert dashboard.view_handle('icb tasks show', {'name': 'no id here'}) == ''
    assert dashboard.view_handle('icb tasks show', {'id': 422}) == 'icb tasks show 422'


def test_maintenance_rows_carry_the_command_that_does_them():
    """A row naming a thing to revisit and withholding the command that revisits
    it is a row you can read and cannot act on."""
    lane = lanes_by_name(all_results())['maintenance']

    handles = {row.text: row.handle for row in lane.rows}
    assert handles['Past its due date'] == 'echo overdue', "the register's own command"


def test_maintenance_shows_both_kinds_when_every_row_is_equally_urgent():
    """A never-run review and a never-run lab rank identically.

    Sorted as one list they tie, and reviews are collected first, so a stable sort
    put every review above every lab — leaving a heading reading "14 reviews · 16
    labs due" above ten reviews and not one lab.
    """
    reviews = [{'id': f'r{n}', 'block': 'items', 'desc': f'Review {n}', 'command': '', 'overdue': None} for n in range(5)]
    labs = [{'id': f'l{n}', 'block': 'practice', 'desc': f'Lab {n}', 'command': '', 'overdue': None} for n in range(5)]
    results = {'review': sources.Result(source='review', payload=reviews + labs, exit_code=0)}

    lane = dashboard.build_maintenance_lane(results, TODAY)

    assert lane.meta == '5 reviews · 5 labs due'
    assert [row.label for row in lane.rows[:4]] == ['review', 'lab', 'review', 'lab']


def test_an_article_row_says_where_it_came_from():
    """Three hundred unread titles say what to read and nothing about where it is."""
    lane = lanes_by_name(all_results())['articles']

    assert lane.rows[0].note == 'example.com'
    assert dashboard.source_host('https://www.postgresqltutorial.com/x') == 'postgresqltutorial.com'
    assert dashboard.source_host('') == '', 'an article saved without a URL simply has no source'


def test_an_event_row_says_where_it_is():
    # The venue is most of what a row about somewhere you have to be is for, and
    # it was dropped entirely.
    lane = lanes_by_name(all_results())['upcoming']

    assert 'A Concert — A Venue' in {row.text for row in lane.rows}


def test_upcoming_lane_uses_one_distance_format_and_names_the_day():
    lane = lanes_by_name(all_results())['upcoming']

    assert lane.rows[0].label == 'in 12d'
    assert lane.rows[0].note == '05 Aug 2026', 'the gutter says how far, the note says which day'
    assert lane.meta == '2 countdowns · 1 event'
    assert lane.total == 3, 'the total spans both countdowns and events'


def test_projects_lane_separates_next_from_blocked():
    lane = lanes_by_name(all_results())['projects']

    assert lane.meta == 'A Project · 5 next · 1 blocked', 'the focus project is named once, ahead of its counts'
    assert [row.label for row in lane.rows] == ['next', 'next', 'blocked']


def test_projects_lane_from_an_overview_with_no_focus_project_keeps_the_bare_counts():
    payload = fixture('icb-overview.json')
    del payload['project_items']['project']
    results = {'icb': sources.Result(source='icb', payload=payload, exit_code=0)}

    lane = dashboard.build_projects_lane(results, TODAY)

    assert lane.meta == '5 next · 1 blocked'
    assert lane.rows[0].note == 'A Project', 'with no focus named, each row names its own projects'


def test_projects_lane_carries_the_item_note_so_a_bare_title_says_something():
    lane = lanes_by_name(all_results())['projects']

    assert lane.rows[0].text == 'An actionable item — what it actually means.', 'the gist, not the whole note'
    assert lane.rows[1].text == 'A second item', 'no note means no dangling separator'


def test_projects_lane_says_where_the_work_lands():
    lane = lanes_by_name(all_results())['projects']

    assert lane.rows[0].note == '', 'an errand in the focus project alone has nothing more to say'
    assert lane.rows[1].note == 'somerepo · Another Project', 'the meta already names the focus project'
    assert lane.rows[2].note == 'somerepo', 'a repo with no membership still says where to go'


def test_projects_lane_rows_carry_a_handle_you_can_type():
    """The one lane that used to have no handle, because an item's only id was a
    UUID sixty columns wide. Items carry a short number now and icb takes it."""
    lane = lanes_by_name(all_results())['projects']

    assert [row.handle for row in lane.rows] == [
        'icb projects items show 101',
        'icb projects items show 102',
        'icb projects items show 103',
    ]


def test_projects_lane_row_without_a_number_has_no_handle():
    """An item created in todoui while the API was unreachable has no number
    until its create is pushed. A handle that would fail is worse than none."""
    payload = fixture('icb-overview.json')
    del payload['project_items']['next'][0]['number']
    results = {'icb': sources.Result(source='icb', payload=payload, exit_code=0)}

    lane = dashboard.build_projects_lane(results, TODAY)

    assert lane.rows[0].handle == ''


def test_projects_lane_survives_an_icb_that_omits_membership():
    """Older `icb` builds have no projects field; that reads as no membership."""
    payload = fixture('icb-overview.json')
    for item in payload['project_items']['next']:
        del item['projects']
    results = {'icb': sources.Result(source='icb', payload=payload, exit_code=0)}

    lane = dashboard.build_projects_lane(results, TODAY)

    assert lane.available is True
    assert lane.rows[0].note == ''


def test_totals_come_from_the_backend_not_the_row_count():
    lane = lanes_by_name(all_results())['tasks']

    assert len(lane.rows) == 4, 'the payload carries fewer rows than the true total'
    assert lane.total == 12, "icb's --limit must not decide what the heading claims"


def test_lane_is_unavailable_when_its_backend_is_missing():
    results = all_results()
    results['icb'] = sources.Result(source='icb', stderr='icb did not answer within 15s', failure=sources.Failure.FAILED)

    lanes = lanes_by_name(results)

    assert lanes['tasks'].available is False
    assert 'did not answer' in lanes['tasks'].reason
    assert lanes['maintenance'].available is True, 'one dead backend must not cost the other lanes'


def test_section_warning_degrades_only_its_own_lane():
    payload = fixture('icb-overview.json')
    payload['warnings'] = [{'section': 'books', 'message': 'books: API request failed (500)'}]
    results = all_results()
    results['icb'] = sources.Result(source='icb', payload=payload, exit_code=0)

    lanes = lanes_by_name(results)

    assert lanes['books'].reason == 'books: API request failed (500)'
    assert lanes['tasks'].reason == ''


def test_newer_schema_version_is_reported_rather_than_half_read():
    payload = fixture('icb-overview.json')
    payload['schema_version'] = dashboard.ICB_SCHEMA_VERSION + 1
    results = {'icb': sources.Result(source='icb', payload=payload, exit_code=0)}

    lane = dashboard.build_tasks_lane(results, TODAY)

    assert lane.available is False
    assert 'newer than this dashboard' in lane.reason


def test_cap_for_gives_tasks_more_room_but_focus_still_wins():
    assert dashboard.cap_for('tasks', dashboard.ROW_CAP) == 5
    assert dashboard.cap_for('books', dashboard.ROW_CAP) == dashboard.ROW_CAP
    assert dashboard.cap_for('tasks', dashboard.FOCUSED_ROW_CAP) == dashboard.FOCUSED_ROW_CAP


def test_round_robin_interleaves_groups():
    rows = dashboard.round_robin(
        [dashboard.Row('a', 'a1'), dashboard.Row('a', 'a2')],
        [dashboard.Row('b', 'b1')],
    )

    assert [row.text for row in rows] == ['a1', 'b1', 'a2']


def test_round_robin_handles_empty_groups():
    assert dashboard.round_robin([], []) == []


def test_relative_day_changes_unit_never_format():
    """Every row in the lane answers the same question, so a far-off date reads
    as a distance too rather than switching to a calendar stamp."""
    assert dashboard.relative_day('2026-07-24', TODAY) == 'today'
    assert dashboard.relative_day('2026-08-05', TODAY) == 'in 12d'
    assert dashboard.relative_day('2026-10-22', TODAY) == 'in 90d'
    assert dashboard.relative_day('2026-10-23', TODAY) == 'in 3mo'
    assert dashboard.relative_day('2028-03-01', TODAY) == 'in 20mo'
    assert dashboard.relative_day('2026-07-20', TODAY) == '4d ago'
    assert dashboard.relative_day('not-a-date', TODAY) == 'not-a-date'


def test_calendar_day():
    assert dashboard.calendar_day('2026-08-05') == '05 Aug 2026'
    assert dashboard.calendar_day('not-a-date') == 'not-a-date'


def test_day_urgency():
    assert dashboard.day_urgency('2026-07-20', TODAY) is dashboard.Urgency.OVERDUE
    assert dashboard.day_urgency('2026-07-24', TODAY) is dashboard.Urgency.DUE
    assert dashboard.day_urgency('2026-08-05', TODAY) is dashboard.Urgency.NONE
    assert dashboard.day_urgency('not-a-date', TODAY) is dashboard.Urgency.NONE


def test_plural():
    assert dashboard.plural(0, 'lab') == '0 labs'
    assert dashboard.plural(1, 'lab') == '1 lab'
    assert dashboard.plural(2, 'lab') == '2 labs'


def test_event_date_falls_back_on_an_unparseable_timestamp():
    assert dashboard.event_date('2026-08-14T16:00:00') == '2026-08-14'
    assert dashboard.event_date('garbage-value') == 'garbage-va'


def test_is_due_row():
    assert dashboard.is_due_row({'overdue': None}) is True
    assert dashboard.is_due_row({'overdue': 0}) is True
    assert dashboard.is_due_row({'overdue': -1}) is False


def test_maintenance_row_pairs_a_note_with_the_rank_it_sorts_on():
    assert dashboard.maintenance_row('review', 'x', {'overdue': None}, '')[0] == (0, 0)
    assert dashboard.maintenance_row('review', 'x', {'overdue': 5}, '')[0] == (1, -5)
    assert dashboard.maintenance_row('review', 'x', {'overdue': 0}, '')[0] == (2, 0)
    assert dashboard.maintenance_row('review', 'x', {'overdue': None}, '')[1].urgency is dashboard.Urgency.OVERDUE


def test_never_run_ranks_the_shortest_cadence_first():
    """A weekly thing never once done has missed far more of its own cycles than
    a six-monthly one, and every never-run row would otherwise tie."""
    weekly = dashboard.maintenance_row('review', 'x', {'overdue': None, 'cadence_days': 7}, '')[0]
    biannual = dashboard.maintenance_row('review', 'x', {'overdue': None, 'cadence_days': 180}, '')[0]

    assert weekly < biannual
    assert weekly < dashboard.maintenance_row('review', 'x', {'overdue': 99}, '')[0], 'never run still leads'


def test_a_row_without_a_cadence_days_still_ranks():
    """A source that predates the field contributes lanes rather than crashing one."""
    assert dashboard.maintenance_row('review', 'x', {'overdue': None}, '')[0] == (0, 0)
    assert dashboard.maintenance_row('review', 'x', {'overdue': 5}, '')[1].note == '5d overdue'
    assert dashboard.maintenance_row('review', 'x', {'overdue': 5}, 'indy index')[1].handle == 'indy index'


def test_describe_joins_a_title_to_the_gist_of_its_note():
    assert dashboard.describe('A Title', 'some detail') == 'A Title — some detail'
    assert dashboard.describe('A Title', '') == 'A Title'
    assert dashboard.describe('A Title', None) == 'A Title'
    assert dashboard.describe('A Title', 'two\nlines') == 'A Title — two lines'
    assert dashboard.describe('A Title', 'The gist. The reasoning behind it.') == 'A Title — The gist.'


def test_a_due_note_renders_rather_than_raising(capsys):
    """rich refuses a style argument when appending a Text.

    Only a due or overdue note carries one, so this raised for exactly the rows
    that matter most — and no lane in the fixtures happened to produce one until
    a conforming source did.
    """
    rows = [dashboard.Row('now', 'Lights to evening scene', '18:40', dashboard.Urgency.OVERDUE)]

    dashboard.render_rows(rows, 80)

    assert '18:40' in capsys.readouterr().out


def test_a_wide_terminal_spends_its_width_on_the_note(monkeypatch, capsys):
    """The note is where a row says which repo and which project it belongs to.

    Capped flat at 22 columns it arrived as "CLI machine contract …" no matter how
    much room was going spare to the right of it.
    """
    monkeypatch.setenv('COLUMNS', '200')
    note = 'goselfupdate · CLI machine contract conformance'
    rows = [dashboard.Row('next', 'Give cobracmd a usage-error exit code of 2', note)]

    dashboard.render_rows(rows, render.MAX_WIDTH)

    assert note in capsys.readouterr().out


def test_a_row_with_a_command_shows_what_to_type(monkeypatch, capsys):
    monkeypatch.setenv('COLUMNS', '200')
    rows = [dashboard.Row('review', 'Re-index indy so search stays current', '25d overdue', handle='indy index')]

    dashboard.render_rows(rows, render.MAX_WIDTH)

    assert f'{dashboard.HANDLE_MARK} indy index' in capsys.readouterr().out


@pytest.mark.parametrize('width', [80, render.MAX_WIDTH])
def test_a_handle_is_printed_whole_at_any_width(monkeypatch, capsys, width):
    monkeypatch.setenv('COLUMNS', str(width))
    rows = [
        dashboard.Row(
            'ask',
            'sessions keep re-reading the help of one tool or guessing its commands',
            'recommends 1. Add a line to the agent instructions',
            dashboard.Urgency.OVERDUE,
            'inbox answer friction/precommit/a-hook-with-a-long-repo-scoped-name <n>',
        ),
        dashboard.Row('next', 'Tests read the workstation', 'a-repo · an initiative with a long name', handle='tracker show 837'),
    ]

    dashboard.render_rows(rows, width)

    printed = capsys.readouterr().out
    for row in rows:
        assert f'{dashboard.HANDLE_MARK} {row.handle}' in printed
    assert all(len(line) <= width for line in printed.splitlines())


def test_a_label_wider_than_the_gutter_leaves_the_handle_on_the_line(monkeypatch, capsys):
    """A PR row's label is its repo, which can be wider than the gutter. The
    lane's gutter widens to it, so the handle stays on the line."""
    monkeypatch.setenv('COLUMNS', '120')
    rows = [
        dashboard.Row('a-long-repo', 'feat(list): add a sectioned shopping list built for the phone, in a form', '0d', handle='prs'),
        dashboard.Row('short', 'feat(cli): point a scan flag given at the root to the check verb', '0d', handle='prs'),
    ]

    dashboard.render_rows(rows, 120)

    lines = capsys.readouterr().out.splitlines()
    assert all(line.endswith(f'{dashboard.HANDLE_MARK} prs') for line in lines)
    assert len({line.index('feat') for line in lines}) == 1, 'the lane shares one gutter'


@pytest.mark.parametrize(('width', 'handle'), [(60, 'tracker labs show a-lab'), (80, 'tracker labs show a-lab-with-a-long-id')])
def test_a_handle_never_takes_a_short_note_from_its_lane(monkeypatch, capsys, width, handle):
    """A handle sizes the column for every row in its lane, so one sitting
    inline at the note's expense would cut the note on all of them. Each handle
    here fits beside the text alone and not beside the text and the note."""
    monkeypatch.setenv('COLUMNS', str(width))
    rows = [
        dashboard.Row('lab', 'Find files fast', 'never run', handle=handle),
        dashboard.Row('lab', 'Search code', 'never run', handle=handle),
    ]

    dashboard.render_rows(rows, width)

    printed = capsys.readouterr().out
    assert printed.count('never run') == 2
    assert printed.count(f'{dashboard.HANDLE_MARK} {handle}') == 2


def test_a_trailer_too_wide_for_the_pane_keeps_each_command_on_one_line(monkeypatch, capsys):
    monkeypatch.setenv('COLUMNS', '30')
    hint = 'tracker items list --status open'

    dashboard.render_trailer(dashboard.LaneView(name='items', title='ITEMS', hints=[hint, hint]))

    assert capsys.readouterr().out.count(f'→ {hint}') == 2


def test_a_narrow_terminal_spends_its_width_on_the_row_itself(monkeypatch, capsys):
    monkeypatch.setenv('COLUMNS', '60')
    rows = [dashboard.Row('next', 'Give cobracmd a usage-error exit code of 2', 'x' * 80)]

    dashboard.render_rows(rows, 60)

    printed = capsys.readouterr().out.rstrip('\n')
    assert 'Give cobracmd' in printed
    assert printed.count('x') <= int(60 * dashboard.NOTE_WIDTH_SHARE), 'the note never crowds out the thing itself'


MESO_CYCLES = [
    {
        'name': 'Lower Body — Return to Running',
        'status': 'active',
        'target_date': '2026-10-09',
        'workouts': [
            {'workout_id': 1, 'workout_name': 'Session A — Loaded Lower', 'phase': 'base', 'frequency': '2×/week'},
            {'workout_id': 2, 'workout_name': 'Session B — CR Mobility', 'phase': 'base', 'frequency': '4×/week'},
        ],
    },
    {
        'name': 'Dance Calf Conditioning',
        'status': 'paused',
        'target_date': None,
        'workouts': [{'workout_id': 9, 'workout_name': 'Calf ladder', 'phase': 'base', 'frequency': 'daily'}],
    },
]


def training_lane(payload, today=TODAY):
    result = sources.Result(source='meso', payload=payload, exit_code=0)
    return dashboard.build_training_lane({'meso': result}, today)


def test_the_training_lane_rows_workouts_not_cycles():
    """A cycle is a plan; a workout is the thing you can go and do."""
    lane = training_lane(MESO_CYCLES)
    assert lane.title == 'TRAINING'
    assert [row.text for row in lane.rows] == ['Session A — Loaded Lower', 'Session B — CR Mobility']
    assert lane.rows[0].handle == 'meso workouts show 1'


def test_a_training_row_leads_with_when_the_workout_was_last_done():
    """The day goes first so a narrow pane clips the frequency instead. A payload
    without the field keeps the frequency alone rather than reading `never`."""
    cycle = {
        'status': 'active',
        'workouts': [
            {'workout_id': 1, 'workout_name': 'Lower', 'frequency': '2×/week', 'last_performed_on': '2026-07-21'},
            {'workout_id': 2, 'workout_name': 'Mobility', 'frequency': '4×/week', 'last_performed_on': None},
            {'workout_id': 3, 'workout_name': 'Run', 'frequency': '3×/week'},
        ],
    }
    lane = training_lane([cycle])
    assert [row.note for row in lane.rows] == ['3d ago · 2×/week', 'never · 4×/week', '3×/week']


def test_a_paused_cycle_contributes_nothing():
    lane = training_lane(MESO_CYCLES)
    assert lane.meta == '1 cycle active · 2 prescribed'
    assert all('Calf' not in row.text for row in lane.rows)


def test_a_cycle_past_its_target_says_so_once_and_leaves_its_workouts_alone():
    """Lateness belongs to the cycle. Stamped on each workout, it would call one
    done yesterday as overdue as one never started."""
    cycle = {
        'name': 'Lower Body',
        'status': 'active',
        'target_date': '2026-07-14',
        'workouts': [
            {'workout_id': 1, 'workout_name': 'Loaded Lower', 'frequency': '2×/week', 'last_performed_on': '2026-07-23'},
            {'workout_id': 2, 'workout_name': 'Mobility', 'frequency': '4×/week', 'last_performed_on': None},
        ],
    }
    lane = training_lane([cycle])
    assert [(row.text, row.urgency) for row in lane.rows] == [
        ('Lower Body', dashboard.Urgency.OVERDUE),
        ('Loaded Lower', dashboard.Urgency.NONE),
        ('Mobility', dashboard.Urgency.NONE),
    ]
    assert lane.meta == '1 cycle active · 2 prescribed'


def test_a_training_lane_that_cannot_answer_says_so_rather_than_vanishing():
    """A dropped lane reads as "nothing outstanding", which is the worst thing
    this dashboard could say wrongly."""
    failed = sources.Result(source='meso', exit_code=1, stderr='not logged in', failure=sources.Failure.FAILED)
    lane = dashboard.build_training_lane({'meso': failed}, TODAY)
    assert lane.available is False
    assert lane.title == 'TRAINING'
    assert 'not logged in' in lane.reason


def test_a_source_that_did_not_answer_says_so_rather_than_vanishing() -> None:
    lane = dashboard.dotfiles_adapter(sources.Result(source='dotfiles', payload=None, exit_code=1))[0]

    assert lane.name == 'dotfiles'
    assert lane.rows == []


def test_an_ordinary_lane_is_never_quiet_even_when_empty() -> None:
    """Only an alert lane disappears. A work lane with nothing in it still says
    so, which is what `—` is for."""
    lane = dashboard.LaneView(name='tasks', title='TASKS')

    assert dashboard.quiet_lane(lane) is False


def alert_lane(name: str = 'problems', rows: int = 1) -> dashboard.LaneView:
    made = [Row('5d', f'problem {n}', 'archlinux', Urgency.OVERDUE, '') for n in range(rows)]
    return dashboard.LaneView(name=name, title=name.upper(), rows=made, total=rows, alert=True)


def test_an_alert_lane_draws_before_every_work_lane() -> None:
    """A problem you scroll to is one you read after deciding what to do."""
    work = [dashboard.LaneView(name=n, title=n.upper()) for n in ('tasks', 'habits')]

    ordered = dashboard.arranged([*work, alert_lane()], None)

    assert [lane.name for lane in ordered] == ['problems', 'tasks', 'habits']


def test_the_terminal_drops_an_alert_lane_with_nothing_open(capsys) -> None:
    """Seeing it every day while it is empty is what teaches you to stop reading
    the one place that says something is wrong."""
    quiet = alert_lane(rows=0)

    dashboard.render_lanes([quiet, dashboard.LaneView(name='tasks', title='TASKS')], dt.date.today(), 5)

    printed = capsys.readouterr().out
    assert 'PROBLEMS' not in printed
    assert 'TASKS' in printed


def test_the_alert_flag_survives_a_round_trip_through_the_contract() -> None:
    """A source emitting doit's own lane document can declare it without an adapter."""
    document = json.loads(dashboard.lanemodel.dumps([alert_lane(), dashboard.LaneView(name='t', title='T')], dt.datetime.now()))

    read_back = dashboard.lanemodel.from_document(document)

    assert [lane.alert for lane in read_back] == [True, False]


def test_work_in_flight_survives_a_round_trip_through_the_contract() -> None:
    """A source with nothing to list but work under way stays drawn without an adapter."""
    held = dashboard.LaneView(name='problems', title='PROBLEMS', in_flight=2, alert=True)
    document = json.loads(dashboard.lanemodel.dumps([held], dt.datetime.now()))

    read_back = dashboard.lanemodel.from_document(document)[0]

    assert read_back.in_flight == 2
    assert dashboard.quiet_lane(read_back) is False


NOMAD_OVERVIEW = {
    'schema_version': dashboard.NOMAD_SCHEMA_VERSION,
    'as_of_month': '2026-07',
    'scheduled': {
        'items': [
            {'id': 'trip-past', 'name': 'Portland', 'notes': 'Scout it. Book later.', 'planned_date': '2026-04', 'past_its_month': True},
            {'id': 'trip-now', 'name': 'Denver', 'notes': '', 'planned_date': '2026-07', 'past_its_month': False},
            {'id': 'trip-later', 'name': 'Lisbon', 'notes': '', 'planned_date': '2027-03', 'past_its_month': False},
        ],
        'total': 3,
        'past_its_month_total': 1,
    },
    'queued': {'items': [{'id': 'trip-queued', 'name': 'Someday', 'planned_date': None, 'past_its_month': False}], 'total': 1},
    'completed': {'total': 2},
    'warnings': [],
}

ICB_DATES = {
    'schema_version': dashboard.ICB_SCHEMA_VERSION,
    'countdowns': {
        'items': [{'id': 7, 'name': 'Dentist', 'due_date': TODAY.isoformat()}, {'id': 8, 'name': 'Passport', 'due_date': '2026-08-02'}],
        'total': 2,
    },
    'events': {'items': [], 'total': 0},
}


def answered(source: str, payload: object) -> sources.Result:
    return sources.Result(source=source, payload=payload, exit_code=0)


def failed(source: str, said: str) -> sources.Result:
    return sources.Result(source=source, stderr=said, failure=sources.Failure.FAILED)


def test_planned_trips_sit_among_the_dates_at_month_grain_and_one_past_its_month_is_owed() -> None:
    results = {'icb': answered('icb', ICB_DATES), 'nomad': answered('nomad', NOMAD_OVERVIEW)}

    lane = dashboard.build_upcoming_lane(results, TODAY)

    assert [(row.label, row.text, row.note, row.urgency) for row in lane.rows] == [
        ('today', 'Dentist', '24 Jul 2026', Urgency.DUE),
        ('3mo owed', 'Portland — Scout it.', 'Apr 2026', Urgency.OVERDUE),
        ('this month', 'Denver', 'Jul 2026', Urgency.NONE),
        ('in 9d', 'Passport', '02 Aug 2026', Urgency.NONE),
        ('in 8mo', 'Lisbon', 'Mar 2027', Urgency.NONE),
    ]
    assert lane.rows[1].handle == 'nomad trips show trip-past'
    assert lane.meta == '2 countdowns · 0 events · 3 trips · 1 owed'
    assert lane.total == 5


def test_a_source_that_cannot_answer_costs_only_its_own_rows_and_says_why() -> None:
    icb, nomad = answered('icb', ICB_DATES), answered('nomad', NOMAD_OVERVIEW)
    icb_down, nomad_down = failed('icb', 'icb did not answer'), failed('nomad', 'nomad answered 503 to GET /overview')

    without_trips = dashboard.build_upcoming_lane({'icb': icb, 'nomad': nomad_down}, TODAY)
    without_dates = dashboard.build_upcoming_lane({'icb': icb_down, 'nomad': nomad}, TODAY)
    without_either = dashboard.build_upcoming_lane({'icb': icb_down, 'nomad': nomad_down}, TODAY)

    assert [row.text for row in without_trips.rows] == ['Dentist', 'Passport']
    assert without_trips.reason == 'nomad answered 503 to GET /overview'
    assert [row.note for row in without_dates.rows] == ['Apr 2026', 'Jul 2026', 'Mar 2027']
    assert without_dates.reason == 'icb did not answer'
    assert without_either.available is False
    assert without_either.reason == 'icb did not answer; nomad answered 503 to GET /overview'


def test_a_newer_nomad_overview_is_reported_rather_than_half_read() -> None:
    newer = {**NOMAD_OVERVIEW, 'schema_version': dashboard.NOMAD_SCHEMA_VERSION + 1}

    lane = dashboard.build_upcoming_lane({'icb': answered('icb', ICB_DATES), 'nomad': answered('nomad', newer)}, TODAY)

    assert [row.text for row in lane.rows] == ['Dentist', 'Passport']
    assert 'newer than this dashboard' in lane.reason


def test_two_sources_of_upcoming_are_one_lane_where_the_first_stood() -> None:
    results = {
        'nomad': answered('nomad', NOMAD_OVERVIEW),
        'learning': answered('learning', {'plan_head': []}),
        'icb': answered('icb', ICB_DATES),
    }
    document = {'sources': {name: {'adapter': name} for name in results}}

    built = dashboard.source_lanes(sources.registry_from_document(document), results)

    assert [lane.name for lane in built] == ['upcoming', 'learning', 'tasks', 'habits', 'books', 'articles', 'projects']
    assert {row.handle.split()[0] for row in built[0].rows} == {'icb', 'nomad'}


def test_open_feedback_lists_each_body_and_says_where_it_was_raised() -> None:
    item = {
        'id': 'abc',
        'body': 'The rest timer\nis too small. It hides.',
        'context_path': '/sessions/4',
        'created_at': '2026-07-20T12:00:00Z',
    }

    [lane] = dashboard.meso_feedback_adapter(answered('meso-feedback', [item]))

    assert (lane.name, lane.meta) == ('meso - feedback', '1 open')
    assert [(row.text, row.note, row.handle) for row in lane.rows] == [
        ('The rest timer is too small.', '/sessions/4', 'meso admin feedback show abc')
    ]


def test_feedback_with_none_open_prints_nothing_and_a_failed_read_says_why(capsys) -> None:
    [nothing_open] = dashboard.meso_feedback_adapter(answered('meso-feedback', []))
    document = json.loads(dashboard.lanemodel.dumps([nothing_open], dt.datetime.now()))

    dashboard.render_lanes(dashboard.lanemodel.from_document(document), dt.date.today(), 5)
    assert 'FEEDBACK' not in capsys.readouterr().out

    broke = dashboard.meso_feedback_adapter(failed('meso-feedback', 'meso answered 403 to GET /feedback'))
    dashboard.render_lanes(broke, dt.date.today(), 5)
    assert 'meso answered 403 to GET /feedback' in capsys.readouterr().out


def test_when_a_cell_was_done_survives_a_round_trip_through_the_contract() -> None:
    lane = dashboard.LaneView(name='t', title='T', grid=[GridCell('a', True, done_at='2026-07-24T12:00:00Z'), GridCell('b', False, '4')])
    document = json.loads(dashboard.lanemodel.dumps([lane], dt.datetime.now()))

    [read_back] = dashboard.lanemodel.from_document(document)

    assert read_back.grid == lane.grid
