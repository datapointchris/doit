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
        'kit': ok('kit-unresolved.json'),
    }


def lanes_by_name(results: dict) -> dict:
    """Every lane, built at a fixed date.

    The builders are called directly rather than through the adapters, because
    an adapter reads today's date and these fixtures are pinned to one.
    """
    built = [build(results, TODAY) for _, build in dashboard.ICB_LANES]
    built.append(dashboard.build_learning_lane(results, TODAY))
    built.append(dashboard.build_maintenance_lane(results, TODAY))
    built.append(dashboard.build_kit_lane(results))
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
    assert handles['An Overdue Lab'] == 'doit labs show overdue-lab', 'a Lab is a document doit opens'


def test_maintenance_shows_both_kinds_when_every_row_is_equally_urgent():
    """A never-run review and a never-run lab rank identically.

    Sorted as one list they tie, and reviews are collected first, so a stable sort
    put every review above every lab — leaving a heading reading "14 reviews · 16
    labs due" above ten reviews and not one lab.
    """
    reviews = [{'id': f'r{n}', 'desc': f'Review {n}', 'command': '', 'overdue': None} for n in range(5)]
    labs = [{'id': f'l{n}', 'title': f'Lab {n}', 'scheduled': True, 'overdue': None} for n in range(5)]
    results = {
        'review': sources.Result(source='review', payload=reviews, exit_code=0),
        'labs': sources.Result(source='labs', payload=labs, exit_code=0),
    }

    lane = dashboard.build_maintenance_lane(results, TODAY)

    assert lane.meta == '5 reviews · 5 labs due'
    assert [row.label for row in lane.rows[:4]] == ['review', 'lab', 'review', 'lab']


def test_kit_lane_names_the_entry_and_what_it_claims_you_can_type():
    """The gap between the two is the whole finding: the row is keyed `broot`
    and the thing that fails is `br`."""
    lane = lanes_by_name(all_results())['kit']

    assert lane.meta == '3 rows naming nothing runnable'
    assert lane.rows[0].label == 'tool'
    assert lane.rows[0].text == 'broot'
    assert lane.rows[0].note == 'br [path]'


def test_kit_rows_offer_the_read_rather_than_the_command_that_fails():
    lane = lanes_by_name(all_results())['kit']

    assert lane.rows[0].handle == 'doit show broot'
    assert all(row.urgency is dashboard.Urgency.NONE for row in lane.rows), 'rot has no deadline to be late against'


def test_kit_lane_drops_an_invocation_that_only_repeats_the_name():
    lane = lanes_by_name(all_results())['kit']

    assert {row.text: row.note for row in lane.rows}['long-gone'] == ''


def test_a_clean_index_says_so_rather_than_leaving_the_lane_blank():
    """A lane that vanishes when it has nothing to report reads as never having run."""
    results = {'kit': sources.Result(source='kit', payload=[], exit_code=0)}

    lane = dashboard.build_kit_lane(results)

    assert lane.available is True
    assert lane.meta == 'every row resolves'
    assert lane.rows == []


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

    assert lane.meta == '5 next · 1 blocked'
    assert [row.label for row in lane.rows] == ['next', 'next', 'blocked']


def test_projects_lane_carries_the_item_note_so_a_bare_title_says_something():
    lane = lanes_by_name(all_results())['projects']

    assert lane.rows[0].text == 'An actionable item — what it actually means.', 'the gist, not the whole note'
    assert lane.rows[1].text == 'A second item', 'no note means no dangling separator'


def test_projects_lane_says_where_the_work_lands():
    lane = lanes_by_name(all_results())['projects']

    assert lane.rows[0].note == 'A Project', 'an errand carries no repo'
    assert lane.rows[1].note == 'somerepo · A Project · Another Project', 'picking one project would be arbitrary'
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


def test_a_wide_terminal_spends_its_width_on_the_note(monkeypatch, capsys):
    """The note is where a row says which repo and which project it belongs to.

    Capped flat at 22 columns it arrived as "CLI machine contract …" no matter how
    much room was going spare to the right of it.
    """
    monkeypatch.setenv('COLUMNS', '200')
    note = 'goselfupdate · CLI machine contract conformance'
    rows = [dashboard.Row('next', 'Give cobracmd a usage-error exit code of 2', note)]

    dashboard.render_rows(rows, dashboard.MAX_WIDTH)

    assert note in capsys.readouterr().out


def test_a_row_with_a_command_shows_what_to_type(monkeypatch, capsys):
    monkeypatch.setenv('COLUMNS', '200')
    rows = [dashboard.Row('review', 'Re-index indy so search stays current', '25d overdue', handle='indy index')]

    dashboard.render_rows(rows, dashboard.MAX_WIDTH)

    assert f'{dashboard.HANDLE_MARK} indy index' in capsys.readouterr().out


def test_a_trailing_column_takes_what_it_needs_bounded_by_its_share():
    assert dashboard.column_width(140, ['', ''], 0.3, 20) == 0, 'a lane whose rows carry no command reserves nothing'
    assert dashboard.column_width(140, ['↳ indy index'], 0.3, 20) == 12, 'what it needs, when that is under the share'
    assert dashboard.column_width(140, ['x' * 90], 0.3, 20) == 42, 'the share, when the content runs longer'
    assert dashboard.column_width(40, ['x' * 90], 0.3, 20) == 20, 'never below the minimum, however narrow the line'


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


def meso_lane(payload, today=TODAY):
    result = sources.Result(source='meso', payload=payload, exit_code=0)
    return dashboard.build_meso_lane({'meso': result}, today)


def test_the_training_lane_rows_workouts_not_cycles():
    """A cycle is a plan; a workout is the thing you can go and do."""
    lane = meso_lane(MESO_CYCLES)
    assert lane.title == 'TRAINING'
    assert [row.text for row in lane.rows] == ['Session A — Loaded Lower', 'Session B — CR Mobility']
    assert lane.rows[0].handle == 'meso workouts show 1'


def test_a_paused_cycle_contributes_nothing():
    lane = meso_lane(MESO_CYCLES)
    assert lane.meta == '1 cycle active · 2 prescribed'
    assert all('Calf' not in row.text for row in lane.rows)


def test_a_cycle_past_its_target_pushes_harder_than_one_with_months_left():
    relaxed = meso_lane(MESO_CYCLES, date(2026, 1, 1))
    assert relaxed.rows[0].urgency == dashboard.Urgency.NONE

    late = meso_lane(MESO_CYCLES, date(2026, 11, 1))
    assert late.rows[0].urgency == dashboard.Urgency.OVERDUE


def test_a_meso_that_cannot_answer_says_so_rather_than_vanishing():
    """A dropped lane reads as "nothing outstanding", which is the worst thing
    this dashboard could say wrongly."""
    failed = sources.Result(source='meso', exit_code=1, stderr='not logged in', failure=sources.Failure.FAILED)
    lane = dashboard.build_meso_lane({'meso': failed}, TODAY)
    assert lane.available is False
    assert lane.title == 'TRAINING'
    assert 'not logged in' in lane.reason
