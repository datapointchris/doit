"""Tests for doit.dashboard — lane composition, degradation, and the JSON model.

Backends are never invoked here: lanes are built from committed fixture payloads
at a fixed date, and the subprocess layer is exercised with fabricated commands.
Nothing in this file touches the network.

The module binds no paths at import time — every backend is a subprocess — so
unlike its siblings it needs no fixture repointing at all.
"""

import json
from datetime import date
from pathlib import Path

from typer.testing import CliRunner

from doit import dashboard
from doit import sources
from doit.cli import app as cli_app

FIXTURE_DIR = Path(__file__).resolve().parent / 'fixtures' / 'dashboard'

TODAY = date(2026, 7, 24)


def fixture(name: str):
    return json.loads((FIXTURE_DIR / name).read_text())


def ok(name: str, source: str = ''):
    return sources.Result(source=source, payload=fixture(name), exit_code=0)


def all_results() -> dict:
    return {
        'icb': ok('icb-overview.json'),
        'learning': ok('learning-overview.json'),
        'review': ok('review-list.json'),
        'labs': ok('labs-list.json'),
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

    assert set(lanes) == set(dashboard.LANE_NAMES)
    assert all(lane.available for lane in lanes.values())


def test_tasks_lane_reads_priority_name_and_category():
    lane = lanes_by_name(all_results())['tasks']

    assert lane.meta == '12 open'
    assert lane.rows[0].label == 'p1', 'a bare numeral in a gutter of words reads as a row number'
    assert lane.rows[0].text == 'Renew passport'
    assert lane.rows[0].note == 'chore'


def test_habits_lane_shows_every_habit_as_a_grid():
    """Every current habit is due every day, so the useful view is the whole set
    with the finished ones ticked off — not a ranked excerpt."""
    lane = lanes_by_name(all_results())['habits']

    assert lane.meta == '1 of 4 done today'
    assert lane.rows == [], 'habits are a complete set, not ranked rows'
    assert [cell.text for cell in lane.grid] == ['Exercise', 'Stretch', 'Journal', 'Read']
    assert [cell.done for cell in lane.grid] == [True, False, False, False]


def test_habits_grid_carries_the_id_you_would_type():
    """`icb habits complete` takes an id, so the grid has to show one."""
    lane = lanes_by_name(all_results())['habits']

    outstanding = {cell.text: cell.handle for cell in lane.grid if not cell.done}
    assert outstanding == {'Stretch': '1', 'Journal': '3', 'Read': '2'}
    completed = [cell for cell in lane.grid if cell.done]
    assert completed[0].handle == '', 'a completion identifies the completion, not the habit'


def test_habits_grid_keeps_a_completed_habit_in_place():
    """Ordering by category and name, never by completion, so ticking one off
    never shuffles the rest out from under you."""
    before = dashboard.build_habits_lane(all_results(), TODAY)

    payload = fixture('icb-overview.json')
    stretch = payload['habits']['due_today'].pop(0)
    payload['habits']['completed_today'].append(stretch)
    results = {'icb': sources.Result(source='icb', payload=payload, exit_code=0)}
    after = dashboard.build_habits_lane(results, TODAY)

    assert [cell.text for cell in after.grid] == [cell.text for cell in before.grid]
    assert [cell.done for cell in after.grid] == [True, True, False, False]


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


def test_article_titles_are_collapsed_onto_one_line():
    """Scraped titles arrive wrapped in the source page's newlines and tabs."""
    lane = lanes_by_name(all_results())['articles']

    assert lane.rows[0].text == 'An Article Being Read'


def test_clean_collapses_any_whitespace():
    assert dashboard.clean('\n\tRAG on structured data\n') == 'RAG on structured data'
    assert dashboard.clean('  spaced   out  ') == 'spaced out'
    assert dashboard.clean(None) == ''


def test_learning_lane_names_resources_rather_than_positions():
    """A track's current unit is a position, not a thing you can open."""
    lane = lanes_by_name(all_results())['learning']

    assert lane.meta == 'A Current Section · 6 of 42 done'
    assert [(row.label, row.text) for row in lane.rows] == [
        ('start', 'What To Open On First Track'),
        ('open', 'A Resource In Progress'),
        ('open', 'Already Open On Third Track'),
        ('open', 'A Started Resource'),
        ('start', 'The Next Resource'),
        ('start', 'The One After Next'),
    ], 'tracks interleave with the section so neither stream crowds the other out'


def test_learning_lane_does_not_repeat_a_resource_listed_twice():
    lane = lanes_by_name(all_results())['learning']

    titles = [row.text for row in lane.rows]
    assert titles.count('A Resource In Progress') == 1, 'the section lists it too'


def test_learning_track_rows_say_which_stream_they_advance():
    lane = lanes_by_name(all_results())['learning']

    notes = {row.text: row.note for row in lane.rows}
    assert notes['What To Open On First Track'] == 'First Track 2/61'
    assert notes['Already Open On Third Track'] == 'Third Track 9/30'
    assert notes['A Resource In Progress'] == '', 'section rows are already named by the heading'


def test_learning_track_with_nothing_left_contributes_no_row():
    lane = lanes_by_name(all_results())['learning']

    assert 'Fourth Track' not in ' '.join(row.note for row in lane.rows)


def test_learning_track_focus_is_not_repeated_from_the_section():
    """A track whose current unit is the section would otherwise list the same
    resource twice."""
    payload = fixture('learning-overview.json')
    payload['track_focus'][0]['resource'] = {'id': 901, 'title': 'The Next Resource', 'status_id': 1}
    results = all_results()
    results['learning'] = sources.Result(source='icb', payload=payload, exit_code=0)

    lane = dashboard.build_learning_lane(results, TODAY)

    assert [row.text for row in lane.rows].count('The Next Resource') == 1


def test_maintenance_lane_counts_only_what_is_due():
    lane = lanes_by_name(all_results())['maintenance']

    # 3 review items due (never-done, overdue, due-today) and 1 lab; the
    # not-yet-due and on-demand entries are excluded.
    assert lane.meta == '3 reviews · 1 lab due'


def test_maintenance_lane_says_what_an_entry_is_for():
    """The register slug identifies a row to the tooling; what it is for only
    exists in the description."""
    lane = lanes_by_name(all_results())['maintenance']

    assert 'never-done-item' not in {row.text for row in lane.rows}
    assert 'Has never been marked done' in {row.text for row in lane.rows}
    assert 'An Overdue Lab' in {row.text for row in lane.rows}, 'labs carry a title instead'


def test_maintenance_lane_ranks_by_how_late_a_row_actually_is():
    """Sorting the note text would rank "16d overdue" below "9d overdue" and put
    never-run entries above everything genuinely due."""
    lane = lanes_by_name(all_results())['maintenance']

    assert [row.urgency for row in lane.rows] == [
        dashboard.Urgency.OVERDUE,
        dashboard.Urgency.OVERDUE,
        dashboard.Urgency.DUE,
        dashboard.Urgency.NONE,
    ]
    overdue = [row.note for row in lane.rows if row.urgency is dashboard.Urgency.OVERDUE]
    assert overdue == ['16d overdue', '9d overdue'], 'most overdue first, numerically'
    assert lane.rows[-1].note == 'never run', 'never run is the least urgent state, not the most'


def test_upcoming_lane_uses_one_distance_format_and_names_the_day():
    lane = lanes_by_name(all_results())['upcoming']

    assert lane.rows[0].label == 'in 12d'
    assert lane.rows[0].note == '05 Aug 2026', 'the gutter says how far, the note says which day'
    assert lane.meta == '2 countdowns · 1 event'
    assert lane.total == 3, 'the total spans both countdowns and events'


def test_projects_lane_separates_next_from_blocked():
    lane = lanes_by_name(all_results())['projects']

    assert lane.meta == '5 next · 1 blocked'
    assert [row.label for row in lane.rows] == ['next', 'next', 'blocked']


def test_projects_lane_carries_the_item_note_so_a_bare_title_says_something():
    lane = lanes_by_name(all_results())['projects']

    assert lane.rows[0].text == 'An actionable item — what it actually means'
    assert lane.rows[1].text == 'A second item', 'no note means no dangling separator'


def test_projects_lane_names_the_work_an_item_belongs_to():
    lane = lanes_by_name(all_results())['projects']

    assert lane.rows[0].note == 'A Project'
    assert lane.rows[1].note == 'A Project · Another Project', 'picking a first would be arbitrary'


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
    results['icb'] = sources.Result(source='icb', failure=sources.Failure.NOT_INSTALLED)

    lanes = lanes_by_name(results)

    assert lanes['tasks'].available is False
    assert 'not installed' in lanes['tasks'].reason
    assert lanes['maintenance'].available is True, 'one dead backend must not cost the other lanes'


def test_maintenance_renders_what_it_has_when_one_backend_is_missing():
    results = all_results()
    results['labs'] = sources.Result(source='icb', failure=sources.Failure.NOT_INSTALLED)

    lane = dashboard.build_maintenance_lane(results, TODAY)

    assert lane.available is True
    assert [row.label for row in lane.rows] == ['review', 'review', 'review']
    assert 'doit labs is not installed' in lane.reason


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
    assert dashboard.cap_for('upcoming', dashboard.ROW_CAP) == dashboard.ROW_CAP
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
    assert dashboard.maintenance_row('review', 'x', {'overdue': None})[0] == (2, 0)
    assert dashboard.maintenance_row('review', 'x', {'overdue': 0})[0] == (1, 0)
    assert dashboard.maintenance_row('review', 'x', {'overdue': 5})[0] == (0, -5)
    assert dashboard.maintenance_row('review', 'x', {'overdue': 5})[1].note == '5d overdue'


def test_describe_joins_a_title_to_its_note():
    assert dashboard.describe('A Title', 'some detail') == 'A Title — some detail'
    assert dashboard.describe('A Title', '') == 'A Title'
    assert dashboard.describe('A Title', None) == 'A Title'
    assert dashboard.describe('A Title', 'two\nlines') == 'A Title — two lines'


def test_clip():
    assert dashboard.clip('short', 10) == 'short'
    assert dashboard.clip('a much longer string', 10) == 'a much lo…'


def test_an_unmatched_lane_is_reported(monkeypatch, tmp_path):
    """`--lane` is not validated against a closed set.

    A conforming source contributes lanes doit has never heard of, so rejecting
    an unknown name would reject the whole point. An unmatched name is reported
    instead.
    """
    monkeypatch.setattr(sources, 'SOURCES', tmp_path / 'none.yml')
    result = CliRunner().invoke(cli_app, ['dashboard', '--lane', 'nonsense'])

    assert result.exit_code == 0
    assert 'nonsense' in result.output


def test_a_due_note_renders_rather_than_raising(capsys):
    """rich refuses a style argument when appending a Text.

    Only a due or overdue note carries one, so this raised for exactly the rows
    that matter most — and no lane in the fixtures happened to produce one until
    a conforming source did.
    """
    rows = [dashboard.Row('now', 'Lights to evening scene', '18:40', dashboard.Urgency.OVERDUE)]

    dashboard.render_rows(rows, 80)

    assert '18:40' in capsys.readouterr().out
