"""Tests for doit.pursuits — register validation, state assembly, pins, cache, write-through.

The draw's math lives in doit.allocate and is tested there against a seeded
generator. What is tested here is everything layered on top: refusing a register
it cannot trust, deriving the state the draw runs on, pinning by cadence rather
than by chance, and never acting on an item it was not offered.

`load_pursuits` resolves REGISTER at call time rather than binding it as a
parameter default, so an autouse fixture can repoint it — the dotfiles version
froze the path at import and needed env vars set before the module loaded.
"""

import json
import math
import re
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from doit import journal
from doit import pursuits
from doit import render
from doit.cli import app as cli_app

runner = CliRunner()

FIXTURE_DIR = Path(__file__).resolve().parent / 'fixtures' / 'pursuits'
NOW = datetime.fromisoformat('2026-08-04T12:00:00-04:00')


@pytest.fixture(autouse=True)
def register(monkeypatch):
    """The committed fixture register, with no journal or cache behind it."""
    monkeypatch.setattr(pursuits, 'REGISTER', FIXTURE_DIR / 'pursuits.yml')
    monkeypatch.setattr(pursuits, 'JOURNAL_DIR', FIXTURE_DIR / 'does-not-exist-journal')
    monkeypatch.setattr(pursuits, 'CACHE_DIR', FIXTURE_DIR / 'does-not-exist-cache')


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point the journal and cache at a writable temp directory."""
    monkeypatch.setattr(pursuits, 'JOURNAL_DIR', tmp_path / 'state')
    monkeypatch.setattr(pursuits, 'CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setattr(pursuits, 'DRAW_CACHE', tmp_path / 'cache' / 'next-draw.json')
    monkeypatch.setattr(pursuits, 'NAMES_CACHE', tmp_path / 'cache' / 'next-names.txt')
    return tmp_path


def write_register(tmp_path, body: str) -> Path:
    path = tmp_path / 'pursuits.yml'
    path.write_text(body)
    return path


def log_done(directory: Path, pursuit: str, days_ago: float) -> None:
    journal.append(
        journal.journal_path(directory, 'testbox'),
        {'pursuit': pursuit, 'event': 'done', 'occurred_at': (NOW - timedelta(days=days_ago)).isoformat()},
    )


def test_load_pursuits_reads_the_register():
    register = pursuits.load_pursuits()
    assert register['chores']['cadence'] == '1w'
    assert register['read-library']['weight'] == 30


def test_a_missing_register_is_empty_not_an_error(tmp_path):
    assert pursuits.load_pursuits(tmp_path / 'nothing.yml') == {}


def test_an_unknown_field_is_refused(tmp_path):
    # A typo in a weight file silently misallocates attention for months, so it
    # has to be loud rather than ignored.
    path = write_register(tmp_path, 'pursuits:\n  a:\n    weght: 5\n')
    with pytest.raises(pursuits.RegisterError, match='weght'):
        pursuits.load_pursuits(path)


def test_every_field_the_register_accepts_is_named_in_the_template():
    # The template is the whole schema documentation: it is what a fresh install
    # writes and what the file being hand-edited carries at its head. A field the
    # loader accepts and the header never names is a feature nobody can find.
    # Matched as a whole word: `id` is two letters and a substring test would
    # find it inside "consider" and call the field documented.
    undocumented = sorted(field for field in pursuits.KNOWN_FIELDS if not re.search(rf'\b{re.escape(field)}\b', pursuits.TEMPLATE))

    assert not undocumented, f'accepted but never named in the template: {", ".join(undocumented)}'


def test_the_template_names_no_field_the_register_would_refuse():
    # The inverse, and the reason a typo here is worse than a missing line: the
    # header is copied when a pursuit is added, so a name that drifted out of
    # KNOWN_FIELDS would be pasted into a register that then refuses to load.
    documented = {
        word.strip('#').strip() for line in pursuits.TEMPLATE.splitlines() if line.startswith('#   ') for word in [line[4:].split(' ')[0]]
    }
    unknown = sorted(name for name in documented if name and name.islower() and name.isidentifier() and name not in pursuits.KNOWN_FIELDS)

    assert not unknown, f'named in the template but the loader would refuse it: {", ".join(unknown)}'


def test_orphaned_offer_counts_names_a_total_a_retirement_stranded(sandbox, monkeypatch):
    """Retiring a pursuit leaves its offer count behind under the old key, and
    drift iterates the register — so the row never appears there again."""
    counts = sandbox / 'state'
    counts.mkdir(parents=True, exist_ok=True)
    (counts / 'next-offers-testbox.json').write_text(json.dumps({'chores': 4, 'retired-thing': 8}))

    assert pursuits.orphaned_offer_counts(pursuits.load_pursuits()) == ['retired-thing']


def test_a_counter_matching_the_register_is_not_orphaned(sandbox):
    counts = sandbox / 'state'
    counts.mkdir(parents=True, exist_ok=True)
    (counts / 'next-offers-testbox.json').write_text(json.dumps({'chores': 4}))

    assert pursuits.orphaned_offer_counts(pursuits.load_pursuits()) == []


def test_a_missing_weight_is_refused(tmp_path):
    path = write_register(tmp_path, 'pursuits:\n  a:\n    description: no weight\n')
    with pytest.raises(pursuits.RegisterError, match='weight'):
        pursuits.load_pursuits(path)


def test_a_boolean_weight_is_refused(tmp_path):
    # bool is an int in Python, so `weight: true` would otherwise pass as 1.
    path = write_register(tmp_path, 'pursuits:\n  a:\n    weight: true\n')
    with pytest.raises(pursuits.RegisterError, match='weight'):
        pursuits.load_pursuits(path)


def test_a_nonsense_cadence_is_refused(tmp_path):
    path = write_register(tmp_path, 'pursuits:\n  a:\n    weight: 5\n    cadence: soon\n')
    with pytest.raises(pursuits.RegisterError, match='cadence'):
        pursuits.load_pursuits(path)


def test_on_log_without_resolve_is_refused(tmp_path):
    path = write_register(tmp_path, 'pursuits:\n  a:\n    weight: 5\n    on_log: echo hi\n')
    with pytest.raises(pursuits.RegisterError, match='on_log'):
        pursuits.load_pursuits(path)


def test_minutes_is_read_as_an_estimate(tmp_path):
    path = write_register(tmp_path, 'pursuits:\n  a:\n    weight: 5\n    checkoff_minutes: 40\n')
    assert pursuits.load_pursuits(path)['a']['checkoff_minutes'] == 40


@pytest.mark.parametrize('value', ['0', '-5', 'true', '"40"', '12.5'])
def test_a_minutes_that_is_not_a_positive_whole_number_is_refused(tmp_path, value):
    path = write_register(tmp_path, f'pursuits:\n  a:\n    weight: 5\n    checkoff_minutes: {value}\n')
    with pytest.raises(pursuits.RegisterError, match='checkoff_minutes'):
        pursuits.load_pursuits(path)


def test_paused_and_expired_pursuits_stay_out_of_the_active_set(sandbox):
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)
    assert 'paused-thing' not in state['active']
    assert 'expired-thing' not in state['active']
    assert 'chores' in state['active']


def test_a_paused_pursuit_is_still_listed(sandbox):
    # Out of the draw, not out of the file — the register is the record of intent.
    assert 'paused-thing' in pursuits.build_state(pursuits.load_pursuits(), NOW)['pursuits']


def test_an_explicit_cadence_overrides_the_implied_interval(sandbox):
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)
    assert state['intervals']['chores'] == 7.0
    assert state['intervals']['read-library'] != 7.0


def test_shares_come_from_active_weights_only(sandbox):
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)
    assert abs(sum(state['shares'].values()) - 1.0) < 1e-9
    assert 'paused-thing' not in state['shares']


def test_the_rate_falls_back_before_there_is_anything_to_measure(sandbox):
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)
    assert state['measured_rate'] is None
    assert state['logs_per_day'] == pursuits.FALLBACK_LOGS_PER_DAY


def test_the_rate_is_measured_once_the_journal_has_history(sandbox):
    for day in range(6):
        log_done(sandbox / 'state', 'read-library', day / 2)
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)
    assert state['measured_rate'] is not None
    assert state['logs_per_day'] == state['measured_rate']


def test_a_cadence_pursuit_never_done_is_pinned(sandbox):
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)
    assert pursuits.pinned(state) == ['chores']


def test_a_cadence_pursuit_done_inside_its_cadence_is_not_pinned(sandbox):
    log_done(sandbox / 'state', 'chores', 2)
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)
    assert pursuits.pinned(state) == []


def test_a_cadence_pursuit_past_its_cadence_is_pinned_again(sandbox):
    log_done(sandbox / 'state', 'chores', 30)
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)
    assert pursuits.pinned(state) == ['chores']


def test_a_pinned_pursuit_is_not_also_sampled(sandbox):
    # Pinned means guaranteed; drawing it again would waste a slot on it.
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)
    selection = pursuits.compute_draw(state, seed=1)
    assert selection['pinned'] == ['chores']
    assert 'chores' not in selection['drawn']


def test_the_draw_fills_up_to_the_screen_size_across_pins_and_samples(sandbox):
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)
    selection = pursuits.compute_draw(state, seed=1)
    assert len(selection['pinned']) + len(selection['drawn']) <= pursuits.DRAW_SIZE


def test_a_just_logged_pursuit_is_never_the_heaviest_candidate(sandbox):
    """It can still fill a row — the screen is sized for five and a one-row draw
    is a queue. What it cannot do is outrank anything that is actually owed,
    which is what reads as the tool not having noticed the log."""
    log_done(sandbox / 'state', 'read-library', 0.0)
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)

    assert state['effective']['read-library'] == 0.0
    owed = [value for name, value in state['pool'].items() if state['effective'][name] > 0]
    assert owed and state['pool']['read-library'] < min(owed)


def test_a_cached_draw_is_reused_inside_the_window(sandbox):
    pursuits.save_cached_draw({'draw_id': 'abc', 'created_at': NOW.isoformat(), 'pinned': [], 'drawn': ['chores']})
    assert pursuits.load_cached_draw(NOW + timedelta(minutes=5))['draw_id'] == 'abc'


def test_a_cached_draw_expires(sandbox):
    pursuits.save_cached_draw({'draw_id': 'abc', 'created_at': NOW.isoformat(), 'pinned': [], 'drawn': []})
    assert pursuits.load_cached_draw(NOW + timedelta(minutes=pursuits.CACHE_MINUTES + 1)) is None


def stand_a_draw(drawn: list[str], resolved: dict | None = None, logged: list[str] | None = None) -> None:
    """Cache a draw created now, so it is live for the rest of the window."""
    payload = {
        'draw_id': 'abc',
        'created_at': datetime.now().astimezone().isoformat(),
        'pinned': [],
        'drawn': drawn,
        'resolved': resolved or {},
    }
    if logged is not None:
        payload['logged'] = logged
    pursuits.save_cached_draw(payload)


def test_logging_takes_the_pursuit_off_the_standing_draw(sandbox, monkeypatch):
    """The draw outlives the log by up to a quarter of an hour.

    Nothing marked it, so a pursuit done inside that window was offered again on
    the very next run — with its own status column reading `today`, which reads as
    the log having gone nowhere.
    """
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')
    stand_a_draw(['chores', 'read-library'])

    assert pursuits.cmd_log('chores', [], None, None, assume_yes=True, no_write=False) == 0

    cached = pursuits.load_cached_draw(datetime.now().astimezone())
    assert pursuits.without_logged(cached)['drawn'] == ['read-library']


def test_a_logged_pursuit_leaves_the_draw_record_intact(sandbox, monkeypatch):
    """Marked, never cleared, because three things still read the draw it is on.

    `was_offered` and `rank_in_draw` read the drawn list, and the item written
    through to the owning CLI comes from the resolved map rather than a second
    ask. Unlinking the cache the way a skip does would take all three.
    """
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')
    stand_a_draw(['chores', 'read-library'], resolved={'read-library': {'label': 'Dune', 'id': '7'}})

    assert pursuits.cmd_log('chores', [], None, None, assume_yes=True, no_write=False) == 0
    assert pursuits.cmd_log('read-library', [], None, 30, assume_yes=True, no_write=False) == 0

    second = journal.read_all(sandbox / 'state')[1]
    assert second['pursuit'] == 'read-library'
    assert second['draw_id'] == 'abc'
    assert second['was_offered'] is True
    assert second['rank_in_draw'] == 2
    assert second['item']['label'] == 'Dune'


def test_a_fully_logged_draw_is_replaced_rather_than_shown_empty(sandbox, monkeypatch):
    """Everything offered is done, so the standing draw has no answer left to give."""
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')
    monkeypatch.setattr(pursuits, 'todays_context', list)
    stand_a_draw(['chores'], logged=['chores'])

    assert pursuits.cmd_next(False, False, False) == 0

    assert pursuits.load_cached_draw(datetime.now().astimezone())['draw_id'] != 'abc'


def test_a_cached_failure_is_asked_again_without_disturbing_the_draw(sandbox, tmp_path):
    """A backend that recovers must show through the window, and rerolling is not
    the way — it changes the draw, which is what the cache exists to hold still."""
    payload = tmp_path / 'row.json'
    payload.write_text('[{"name": "Trim Dingo Nails"}]')
    selection = {
        'draw_id': 'abc',
        'created_at': NOW.isoformat(),
        'pinned': [],
        'drawn': ['chores'],
        'resolved': {'chores': {'pursuit': 'chores', 'error': 'error: unknown flag: --limit', 'backend': 'icb'}},
    }

    pursuits.retry_failed_resolves(selection, {'chores': {'resolve': f'cat {payload}', 'label': 'name'}})

    assert selection['drawn'] == ['chores']
    assert selection['resolved']['chores']['label'] == 'Trim Dingo Nails'
    assert 'error' not in selection['resolved']['chores']
    assert pursuits.load_cached_draw(NOW)['resolved']['chores']['label'] == 'Trim Dingo Nails'


def test_a_retry_that_finds_nothing_drops_the_stale_error(sandbox, tmp_path):
    # resolve_all omits a pursuit that resolved to nothing, so the failed entry has
    # to go before the merge or the dead message outlives the backend it came from.
    payload = tmp_path / 'empty.json'
    payload.write_text('[]')
    selection = {
        'draw_id': 'abc',
        'created_at': NOW.isoformat(),
        'pinned': [],
        'drawn': ['chores'],
        'resolved': {'chores': {'pursuit': 'chores', 'error': 'exited 1', 'backend': 'icb'}},
    }

    pursuits.retry_failed_resolves(selection, {'chores': {'resolve': f'cat {payload}', 'label': 'name'}})

    assert 'chores' not in selection['resolved']


def test_a_cached_draw_that_resolved_cleanly_is_not_asked_again(sandbox):
    selection = {
        'draw_id': 'abc',
        'created_at': NOW.isoformat(),
        'pinned': [],
        'drawn': ['chores'],
        'resolved': {'chores': {'label': 'Trim Dingo Nails'}},
    }

    pursuits.retry_failed_resolves(selection, {'chores': {'resolve': 'definitely-not-a-real-command', 'label': 'name'}})

    assert selection['resolved']['chores'] == {'label': 'Trim Dingo Nails'}


def test_a_corrupt_cache_is_a_miss_not_a_crash(sandbox):
    pursuits.DRAW_CACHE.parent.mkdir(parents=True, exist_ok=True)
    pursuits.DRAW_CACHE.write_text('{half a file')
    assert pursuits.load_cached_draw(NOW) is None


def test_the_names_cache_is_what_the_shell_completion_reads(sandbox):
    pursuits.write_names_cache(pursuits.load_pursuits())
    lines = pursuits.NAMES_CACHE.read_text().splitlines()
    assert 'chores\tThe maintenance list' in lines
    # Paused pursuits are still completable — you log them by hand all the time.
    assert any(line.startswith('paused-thing\t') for line in lines)


def test_match_pursuit_takes_an_exact_name():
    assert pursuits.match_pursuit('chores', pursuits.load_pursuits()) == 'chores'


def test_match_pursuit_takes_an_unambiguous_prefix():
    assert pursuits.match_pursuit('stu', pursuits.load_pursuits()) == 'study-computer-science'


def test_match_pursuit_refuses_an_ambiguous_prefix():
    # read-library and read-longform both match; guessing either would log the wrong one.
    assert pursuits.match_pursuit('read', pursuits.load_pursuits()) is None


def test_match_pursuit_returns_none_for_a_miss():
    assert pursuits.match_pursuit('nope', pursuits.load_pursuits()) is None


@pytest.mark.parametrize(
    ('token', 'expected'),
    [('90m', timedelta(minutes=90)), ('3h', timedelta(hours=3)), ('2d', timedelta(days=2)), ('1w', timedelta(weeks=1))],
)
def test_parse_ago_units(token, expected):
    assert pursuits.parse_ago(token) == expected


def test_parse_ago_defaults_a_bare_number_to_hours():
    assert pursuits.parse_ago('3') == timedelta(hours=3)


def test_parse_ago_rejects_nonsense():
    assert pursuits.parse_ago('yesterday') is None


def test_dig_follows_a_dotted_path():
    assert pursuits.dig({'a': {'b': [1, 2]}}, 'a.b') == [1, 2]


def test_dig_dead_ends_to_none():
    assert pursuits.dig({'a': {}}, 'a.b.c') is None


def test_dig_indexes_a_list_with_a_numeric_key():
    # Membership arrives as an array, so the name of the work an item belongs to
    # is unreachable without this.
    document = {'projects': [{'name': 'First'}, {'name': 'Second'}]}
    assert pursuits.dig(document, 'projects.0.name') == 'First'
    assert pursuits.dig(document, 'projects.1.name') == 'Second'


@pytest.mark.parametrize('path', ['projects.9.name', 'projects.name'])
def test_dig_dead_ends_rather_than_raising_on_a_bad_list_key(path):
    assert pursuits.dig({'projects': [{'name': 'First'}]}, path) is None


def test_resolve_one_reads_plain_lines_when_no_label_is_named():
    resolved = pursuits.resolve_one('p', {'resolve': 'printf "first line\\nsecond\\n"'})
    assert resolved['label'] == 'first line'


def test_resolve_one_maps_json_fields(tmp_path):
    payload = tmp_path / 'tasks.json'
    payload.write_text(json.dumps([{'name': 'Trim Dingo Nails', 'id': 422}]))
    resolved = pursuits.resolve_one('chores', {'resolve': f'cat {payload}', 'label': 'name', 'id': 'id'})
    assert resolved['label'] == 'Trim Dingo Nails'
    assert resolved['id'] == '422'
    assert resolved['raw']['name'] == 'Trim Dingo Nails'


def test_resolve_one_digs_into_a_nested_list(tmp_path):
    payload = tmp_path / 'overview.json'
    payload.write_text(json.dumps({'in_progress_resources': [{'name': 'Chapter 6'}]}))
    config = {'resolve': f'cat {payload}', 'items': 'in_progress_resources', 'label': 'name'}
    assert pursuits.resolve_one('cs', config)['label'] == 'Chapter 6'


def test_resolve_one_accepts_a_single_object_where_a_list_would_do(tmp_path):
    # icb overview hands back the next project item as an object, having already
    # picked it. The register should not need a wrapper command to unwrap that.
    payload = tmp_path / 'overview.json'
    payload.write_text(json.dumps({'projects': {'items': {'next': {'title': 'Ship the CLI', 'id': 'abc'}}}}))
    config = {'resolve': f'cat {payload}', 'items': 'projects.items.next', 'label': 'title', 'id': 'id'}
    resolved = pursuits.resolve_one('build', config)
    assert resolved['label'] == 'Ship the CLI'
    assert resolved['id'] == 'abc'


def test_resolve_one_carries_where_the_item_lives_and_what_it_is_about(tmp_path):
    payload = tmp_path / 'items.json'
    payload.write_text(
        json.dumps(
            [
                {
                    'title': 'Give cobracmd a usage-error exit code of 2',
                    'repo': 'goselfupdate',
                    'notes': 'Cobra returns flag-parse failures as ordinary errors. Fix belongs in cobracmd.',
                    'projects': [{'name': 'CLI machine contract conformance'}],
                }
            ]
        )
    )
    config = {
        'resolve': f'cat {payload}',
        'label': 'title',
        'context': ['repo', 'projects.0.name'],
        'detail': 'notes',
    }

    resolved = pursuits.resolve_one('build', config)

    assert resolved['context'] == 'goselfupdate · CLI machine contract conformance'
    assert resolved['detail'] == 'Cobra returns flag-parse failures as ordinary errors.', 'the gist, not the whole note'


def test_resolve_one_takes_a_single_context_path_as_well_as_several(tmp_path):
    payload = tmp_path / 'tasks.json'
    payload.write_text(json.dumps([{'name': 'Trim Dingo Nails', 'category': 'Dingo'}]))
    config = {'resolve': f'cat {payload}', 'label': 'name', 'context': 'category'}

    assert pursuits.resolve_one('tasks', config)['context'] == 'Dingo'


def test_resolve_one_skips_a_context_field_the_row_does_not_carry(tmp_path):
    # An errand has no repo. The note reads as the projects alone, never as a
    # leading separator with nothing in front of it.
    payload = tmp_path / 'items.json'
    payload.write_text(json.dumps([{'title': 'Glove 80', 'repo': None, 'projects': [{'name': 'Sell Unused Shite'}]}]))
    config = {'resolve': f'cat {payload}', 'label': 'title', 'context': ['repo', 'projects.0.name']}

    assert pursuits.resolve_one('build', config)['context'] == 'Sell Unused Shite'


def test_resolve_one_fills_the_offered_items_id_into_the_view_command(tmp_path):
    payload = tmp_path / 'items.json'
    payload.write_text(json.dumps([{'title': 'Ship the CLI', 'id': '019fa297-0c01-7737-bb4f-8f05de2fe2cd'}]))
    config = {'resolve': f'cat {payload}', 'label': 'title', 'id': 'id', 'view': 'icb projects items show {id}'}

    resolved = pursuits.resolve_one('build', config)

    assert resolved['view'] == 'icb projects items show 019fa297-0c01-7737-bb4f-8f05de2fe2cd'


def test_a_view_command_wanting_an_id_the_backend_withheld_is_not_printed(tmp_path):
    # A command shown with a hole in it reads as something you could run.
    payload = tmp_path / 'items.json'
    payload.write_text(json.dumps([{'title': 'Ship the CLI'}]))
    config = {'resolve': f'cat {payload}', 'label': 'title', 'view': 'icb projects items show {id}'}

    assert pursuits.resolve_one('build', config)['view'] == ''


def test_a_view_command_needing_no_id_is_printed_as_written():
    assert pursuits.view_command('nomad trips list', None) == 'nomad trips list'


def test_view_without_resolve_is_refused(tmp_path):
    path = write_register(tmp_path, 'pursuits:\n  a:\n    weight: 5\n    view: icb tasks show {id}\n')
    with pytest.raises(pursuits.RegisterError, match='view'):
        pursuits.load_pursuits(path)


def test_context_and_detail_without_a_label_are_refused(tmp_path):
    # Both read fields off a parsed row, and there is no row without `label` —
    # the resolver falls back to reading plain lines.
    path = write_register(tmp_path, 'pursuits:\n  a:\n    weight: 5\n    resolve: echo hi\n    context: repo\n')
    with pytest.raises(pursuits.RegisterError, match='label'):
        pursuits.load_pursuits(path)


def test_resolve_one_reports_a_failing_backend_rather_than_dying():
    # `false` fails with nothing on either stream, so the status has to stand in —
    # a row that renders an empty value looks like a resolver that returned nothing.
    resolved = pursuits.resolve_one('p', {'resolve': 'false'})
    assert resolved['error'] == 'exited 1'
    assert resolved['backend'] == 'false'


def test_resolve_one_reports_a_backend_that_is_not_installed():
    resolved = pursuits.resolve_one('p', {'resolve': 'definitely-not-a-real-command --json'})
    assert resolved['error']
    assert resolved['backend'] == 'definitely-not-a-real-command'


def test_resolve_one_reports_json_that_is_not_json():
    resolved = pursuits.resolve_one('p', {'resolve': 'echo notjson', 'label': 'name'})
    assert 'JSON' in resolved['error']


def test_resolve_one_returns_none_for_an_empty_result(tmp_path):
    payload = tmp_path / 'empty.json'
    payload.write_text('[]')
    assert pursuits.resolve_one('p', {'resolve': f'cat {payload}', 'label': 'name'}) is None


def test_resolve_one_counts_the_rows_that_matched(tmp_path):
    """Three books equally in progress are candidates, not a decision.

    The first row still renders, because a title is better context than none. What
    the count buys is everything downstream knowing the backend did not choose.
    """
    payload = tmp_path / 'books.json'
    payload.write_text(json.dumps([{'title': 'Ego and Archetype'}, {'title': 'Lucid Dreaming'}]))
    resolved = pursuits.resolve_one('read', {'resolve': f'cat {payload}', 'label': 'title'})
    assert resolved['label'] == 'Ego and Archetype'
    assert resolved['candidates'] == 2


def test_resolve_one_counts_after_the_register_narrows_the_rows(tmp_path):
    payload = tmp_path / 'tasks.json'
    payload.write_text(json.dumps([{'name': 'Journal'}, {'name': 'Pumice Stone'}]))
    config = {'resolve': f'cat {payload}', 'label': 'name', 'resolve_where': {'name': 'Journal'}}
    assert pursuits.resolve_one('journal', config)['candidates'] == 1


def test_resolve_all_only_asks_pursuits_that_declare_a_resolver():
    register = {'a': {'resolve': 'echo hello'}, 'b': {'description': 'no resolver'}}
    resolved = pursuits.resolve_all(['a', 'b'], register)
    assert set(resolved) == {'a'}


def test_on_log_substitutes_the_offered_items_id(sandbox, tmp_path):
    marker = tmp_path / 'ran.txt'
    config = {'resolve': 'echo x', 'on_log': f'cp {tmp_path / "seed.txt"} {marker}'}
    (tmp_path / 'seed.txt').write_text('422')
    result = pursuits.run_on_log(config, {'id': '422', 'label': 'Task'}, '', None, assume_yes=True)
    assert result['ran'] is True
    assert marker.read_text() == '422'


def test_on_log_is_not_run_when_there_is_nobody_to_confirm_with(sandbox, tmp_path):
    """The prompt would block on a stdin that never closes, so an unconfirmed
    on_log is skipped rather than asked about. -y is what runs it unattended."""
    marker = tmp_path / 'ran.txt'
    config = {'resolve': 'echo x', 'on_log': f'touch {marker}'}

    assert pursuits.run_on_log(config, {'id': '1', 'label': 'Task'}, '', None, assume_yes=False) is None
    assert not marker.exists()


def test_on_log_is_not_run_under_no_input_even_on_a_terminal(sandbox, tmp_path, monkeypatch):
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    monkeypatch.setattr(render, '_no_input', True)
    marker = tmp_path / 'ran.txt'
    config = {'resolve': 'echo x', 'on_log': f'touch {marker}'}

    assert pursuits.run_on_log(config, {'id': '1', 'label': 'Task'}, '', None, assume_yes=False) is None
    assert not marker.exists()


def test_on_log_is_skipped_when_the_item_has_no_id():
    # Nothing was offered, so there is nothing to complete — better than guessing.
    config = {'resolve': 'echo x', 'on_log': 'icb tasks complete {id}'}
    assert pursuits.run_on_log(config, {'label': 'no id here'}, '', None, assume_yes=True) is None


def test_logging_re_resolves_past_a_cached_failure(sandbox, tmp_path, monkeypatch):
    """A cached failure is truthy but carries no id.

    Left in place it satisfies the guard that would otherwise re-resolve, so the
    write-through to the owning CLI is skipped and nothing on screen says the item
    was never completed.
    """
    marker = tmp_path / 'completed.txt'
    payload = tmp_path / 'row.json'
    payload.write_text('[{"id": 422, "name": "Trim Dingo Nails"}]')
    register = write_register(
        tmp_path,
        'pursuits:\n'
        '  chores:\n'
        '    weight: 25\n'
        f'    resolve: cat {payload}\n'
        '    label: name\n'
        '    id: id\n'
        f'    on_log: sh -c "echo {{id}} > {marker}"\n',
    )
    monkeypatch.setattr(pursuits, 'REGISTER', register)
    pursuits.save_cached_draw(
        {
            'draw_id': 'abc',
            'created_at': datetime.now().astimezone().isoformat(),
            'pinned': [],
            'drawn': ['chores'],
            'resolved': {'chores': {'pursuit': 'chores', 'error': 'exited 1', 'backend': 'icb'}},
        }
    )

    assert pursuits.cmd_log('chores', [], None, None, assume_yes=True, no_write=False) == 0
    assert marker.read_text().strip() == '422'


def test_logging_names_no_item_when_the_backend_matched_several(sandbox, monkeypatch):
    """Logging `read` says an hour of reading happened, never which book.

    Three books are in progress and the note is prose, so nothing here can tell
    which one it was. Naming the first put a book in the journal that the reader
    had not opened.
    """
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')
    offered = {'label': 'Difficult Conversations', 'id': '269', 'candidates': 3}
    stand_a_draw(['read-library'], resolved={'read-library': offered})

    assert pursuits.cmd_log('read-library', ['ego', 'and', 'archetype'], None, 60, assume_yes=True, no_write=False) == 0

    record = journal.read_all(sandbox / 'state')[0]
    assert record['note'] == 'ego and archetype'
    assert record['item'] is None


def test_logging_names_the_item_the_write_through_completed(sandbox, monkeypatch, tmp_path):
    """A completed row is a fact about what was done, however many were offered."""
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')
    marker = tmp_path / 'completed'
    register = write_register(
        tmp_path,
        'pursuits:\n'
        '  chores:\n'
        '    weight: 25\n'
        '    resolve: echo unused-the-draw-already-resolved-it\n'
        f'    on_log: sh -c "echo {{id}} > {marker}"\n',
    )
    monkeypatch.setattr(pursuits, 'REGISTER', register)
    stand_a_draw(['chores'], resolved={'chores': {'label': 'Pumice Stone', 'id': '7', 'candidates': 3}})

    assert pursuits.cmd_log('chores', [], None, None, assume_yes=True, no_write=False) == 0

    assert marker.read_text().strip() == '7'
    assert journal.read_all(sandbox / 'state')[0]['item']['label'] == 'Pumice Stone'


def test_on_log_is_skipped_when_the_pursuit_declares_none():
    assert pursuits.run_on_log({'resolve': 'echo x'}, {'id': '1'}, '', None, assume_yes=True) is None


def test_record_event_writes_the_state_that_produced_it(sandbox, monkeypatch):
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)
    pursuits.record_event('done', 'chores', state, {'note': 'trimmed'})

    records = journal.read_all(sandbox / 'state')
    assert len(records) == 1
    record = records[0]
    assert record['pursuit'] == 'chores'
    assert record['machine'] == 'testbox'
    assert record['note'] == 'trimmed'
    # The weights at the moment of the log, so drift stays honest after a re-weight.
    assert record['state_at_log']['weights']['chores'] == 25
    assert 'probability' in record['state_at_log']


def test_record_event_defaults_occurred_at_to_now_but_accepts_a_past_time(sandbox, monkeypatch):
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)
    earlier = (NOW - timedelta(hours=3)).isoformat()
    pursuits.record_event('done', 'chores', state, {'occurred_at': earlier})

    record = journal.read_all(sandbox / 'state')[0]
    assert record['occurred_at'] == earlier
    assert record['logged_at'] != earlier


def test_term_ended_only_after_the_date():
    assert pursuits.term_ended({'until': NOW.date() - timedelta(days=1)}, NOW.date())
    assert not pursuits.term_ended({'until': NOW.date() + timedelta(days=1)}, NOW.date())
    assert not pursuits.term_ended({}, NOW.date())


def test_the_log_hint_keeps_its_optional_argument(sandbox, monkeypatch, capsys):
    """`[note]` has to survive rich, which reads a bare bracket as a style tag.

    It did not: the hint shipped as markup and rich swallowed the argument, so the
    line told you to run `doit log <pursuit>` and never mentioned the note.
    """
    monkeypatch.setattr(pursuits, 'todays_context', list)

    assert pursuits.cmd_next(False, False, True) == 0

    assert 'doit log <pursuit> [note]' in capsys.readouterr().out


def test_a_drawn_row_says_where_the_item_lives_and_what_it_is_about(monkeypatch, capsys):
    monkeypatch.setenv('COLUMNS', '200')
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)
    resolved = {
        'chores': {
            'label': 'Give cobracmd a usage-error exit code of 2',
            'context': 'goselfupdate · CLI machine contract conformance',
            'detail': 'Cobra returns flag-parse failures as ordinary errors.',
        }
    }

    pursuits.render_row(1, 'chores', state, resolved, False, 6)

    printed = capsys.readouterr().out
    assert 'goselfupdate · CLI machine contract conformance' in printed
    assert 'Cobra returns flag-parse failures as ordinary errors.' in printed


def test_a_drawn_row_prints_the_command_that_opens_the_item(monkeypatch, capsys):
    """The one place a sixty-column UUID invocation fits.

    The dashboard's three-row glance cannot spend the width on it, so the row
    there carries context and the draw carries the command.
    """
    monkeypatch.setenv('COLUMNS', '200')
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)
    view = 'icb projects items show 019fa297-0c01-7737-bb4f-8f05de2fe2cd'
    resolved = {'chores': {'label': 'Give cobracmd a usage-error exit code of 2', 'view': view}}

    pursuits.render_row(1, 'chores', state, resolved, False, 6)

    assert f'↳ {view}' in capsys.readouterr().out


def test_a_row_says_how_many_more_the_backend_matched(monkeypatch, capsys):
    """Without the count the title reads as the backend having chosen.

    Three books are equally in progress, so the first is one of three rather than
    the next one. The count is what stops a scan reading it as a decision.
    """
    monkeypatch.setenv('COLUMNS', '200')
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)
    resolved = {'chores': {'label': 'Difficult Conversations', 'context': 'Douglas Stone', 'candidates': 3}}

    pursuits.render_row(1, 'chores', state, resolved, False, 6)

    assert '+2 more' in capsys.readouterr().out


def test_a_row_with_nothing_extra_to_say_stays_one_line(capsys):
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)

    pursuits.render_row(1, 'chores', state, {'chores': {'label': 'Trim Dingo Nails'}}, False, 6)

    assert len(capsys.readouterr().out.strip().splitlines()) == 1


def test_a_backend_that_failed_gets_no_continuation_line(capsys):
    # The row already says the backend failed; a second line under it would be
    # context for an item that was never resolved.
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)
    resolved = {'chores': {'error': 'exited 1', 'backend': 'icb', 'context': 'stale', 'detail': 'stale'}}

    pursuits.render_row(1, 'chores', state, resolved, False, 6)

    assert len(capsys.readouterr().out.strip().splitlines()) == 1


def test_a_failed_row_carries_what_the_backend_said(monkeypatch, capsys):
    """A stale register entry and a logged-out CLI fail the same way.

    Naming the backend and calling it unavailable reads as an outage, which sends
    you to check a service that is answering fine. The message the backend printed
    is the only part that says which of the two happened.
    """
    monkeypatch.setenv('COLUMNS', '200')
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)
    resolved = {'chores': {'error': 'error: unknown flag: --limit', 'backend': 'icb'}}

    pursuits.render_row(1, 'chores', state, resolved, False, 6)

    assert 'icb: error: unknown flag: --limit' in capsys.readouterr().out


def test_format_elapsed_switches_unit_rather_than_format():
    assert pursuits.format_elapsed(None) == 'never'
    assert pursuits.format_elapsed(0.2) == 'today'
    assert pursuits.format_elapsed(3) == '3d ago'
    assert pursuits.format_elapsed(30) == '4w ago'
    assert pursuits.format_elapsed(200) == '6mo ago'


BALANCE_REGISTER = """
pursuits:
  chore:
    description: Complete chore that is not on the task list
    weight: 25
    cadence: 1d

  read:
    description: The same schedule, measured in minutes rather than occurrences
    weight: 25
    cadence: 1d
    checkoff_minutes: 45
"""


def write_records(directory, records: list[dict]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({'id': str(index), **record}) for index, record in enumerate(records)]
    (directory / 'next-log-test.jsonl').write_text('\n'.join(lines) + '\n' if lines else '')


def done(name: str, days_ago: float, minutes: int | None = None) -> dict:
    record: dict = {'pursuit': name, 'event': 'done', 'occurred_at': (NOW - timedelta(days=days_ago)).isoformat()}
    if minutes is not None:
        record['duration_minutes'] = minutes
    return record


def zeroed(name: str, days_ago: float) -> dict:
    return {'pursuit': name, 'event': 'reset', 'occurred_at': (NOW - timedelta(days=days_ago)).isoformat()}


def skipped(name: str, days_ago: float, span_days: float) -> dict:
    start = NOW - timedelta(days=days_ago)
    return {
        'pursuit': name,
        'event': 'skip',
        'occurred_at': start.isoformat(),
        'expires_at': (start + timedelta(days=span_days)).isoformat(),
    }


def balance_state(tmp_path, monkeypatch, records: list[dict], register: str = BALANCE_REGISTER) -> dict:
    register_path = tmp_path / 'pursuits.yml'
    register_path.write_text(register)
    monkeypatch.setattr(pursuits, 'REGISTER', register_path)
    monkeypatch.setattr(pursuits, 'JOURNAL_DIR', tmp_path / 'state')
    monkeypatch.setattr(pursuits, 'CACHE_DIR', tmp_path / 'cache')
    write_records(tmp_path / 'state', records)
    return pursuits.build_state(pursuits.load_pursuits(), NOW)


def test_a_counted_pursuit_owes_one_checkoff_per_interval(tmp_path, monkeypatch):
    state = balance_state(tmp_path, monkeypatch, [zeroed('chore', 4.0)])
    assert state['balance']['chore'] == 4.0, 'a daily chore, four days on, with nothing done'
    assert 'chore' in pursuits.pinned(state)


def test_a_timed_pursuit_owes_a_checkoffs_worth_of_minutes_per_interval(tmp_path, monkeypatch):
    state = balance_state(tmp_path, monkeypatch, [zeroed('read', 2.0)])
    assert state['balance']['read'] == 90.0, 'two days at a 45-minute checkoff a day'


def test_a_burst_pays_several_intervals_forward(tmp_path, monkeypatch):
    """The whole point: three chores in one evening is three days of cover, and
    nothing caps how far forward that reaches."""
    state = balance_state(tmp_path, monkeypatch, [zeroed('chore', 0.0)] + [done('chore', 0.0) for _ in range(3)])

    assert state['balance']['chore'] == -3.0
    assert 'chore' not in pursuits.pinned(state)
    assert state['effective']['chore'] == 0.0


def test_partial_minutes_roll_over_rather_than_stranding(tmp_path, monkeypatch):
    """A 20-minute read pays 20 minutes off a 45-minute checkoff. No remainder is
    held anywhere, so the next 25 minutes finish it whenever they happen."""
    part = balance_state(tmp_path, monkeypatch, [zeroed('read', 1.0), done('read', 0.5, minutes=20)])
    rest = balance_state(tmp_path, monkeypatch, [zeroed('read', 1.0), done('read', 0.5, minutes=20), done('read', 0.1, minutes=25)])

    assert part['balance']['read'] == 25.0
    assert rest['balance']['read'] == 0.0


def test_a_long_sitting_counts_for_every_minute_of_it(tmp_path, monkeypatch):
    """The failure the unit change exists to end: a 15-minute read and a 3-hour
    read used to satisfy the pursuit identically."""
    brief = balance_state(tmp_path, monkeypatch, [zeroed('read', 1.0), done('read', 0.5, minutes=15)])
    long = balance_state(tmp_path, monkeypatch, [zeroed('read', 1.0), done('read', 0.5, minutes=180)])

    assert brief['balance']['read'] == 30.0
    assert long['balance']['read'] == -135.0


def test_days_since_stays_the_honest_elapsed_time(tmp_path, monkeypatch):
    """The balance changes what gets weighed, never what gets shown as last-done."""
    state = balance_state(tmp_path, monkeypatch, [zeroed('chore', 0.0)] + [done('chore', 0.0) for _ in range(3)])

    assert state['days_since']['chore'] == 0.0, 'three chores today were all done today'
    assert state['balance']['chore'] == -3.0, 'and the draw sees three days of cover'


def test_the_zero_point_is_the_reset_and_a_payment_cannot_move_it(tmp_path, monkeypatch):
    """Shipping writes one for every pursuit, so history accrued against targets
    that have since moved cannot open a pursuit at a debt nobody agreed to."""
    after = balance_state(tmp_path, monkeypatch, [done('chore', 100.0), zeroed('chore', 2.0)])

    assert after['balance']['chore'] == 2.0, 'billed from the reset, and the 100-day-old entry pays nothing'


def test_recording_something_never_increases_what_is_owed(tmp_path, monkeypatch):
    """Deriving the origin from the oldest record let a backdated log drag it
    behind itself: the schedule billed for the span the entry opened up and the
    entry paid one checkoff against it, so logging doubled the debt."""
    before = balance_state(tmp_path, monkeypatch, [done('chore', 200.0)])
    after = balance_state(tmp_path, monkeypatch, [done('chore', 200.0), done('chore', 400.0)])

    assert after['balance']['chore'] <= before['balance']['chore']


def test_a_pursuit_with_no_reset_is_billed_for_one_interval(tmp_path, monkeypatch):
    """The origin cannot come from the records, so it is a single interval back —
    a pursuit doit has never been told the start of owes exactly one checkoff."""
    fresh = balance_state(tmp_path, monkeypatch, [])
    old = balance_state(tmp_path, monkeypatch, [done('chore', 100.0)])

    assert fresh['balance']['chore'] == 1.0
    assert old['balance']['chore'] == 1.0, 'a century of history does not open a century of debt'


def test_an_evidence_backed_pursuit_is_billed_only_over_what_its_app_remembers(tmp_path, sandbox, monkeypatch):
    """The credit side is a 90-day cache. Billing over a longer span accrues a
    debt by construction, on the pursuit most reliably done."""
    monkeypatch.setattr(pursuits, 'REGISTER', write_register(tmp_path, BACKED_REGISTER))
    window = pursuits.evidence.OCCURRENCE_WINDOW_DAYS
    now = datetime.now().astimezone()
    write_records(sandbox / 'state', [{'pursuit': 'backed', 'event': 'reset', 'occurred_at': (now - timedelta(days=400)).isoformat()}])
    stub_evidence_days(monkeypatch, {'backed': [days_ago_iso(n) for n in range(window)]})

    state = pursuits.build_state(pursuits.load_pursuits(), now)

    assert (now - state['origins']['backed']).days == window
    assert state['balance']['backed'] < 0, 'done every day it can be asked about is ahead, not 300 checkoffs behind'


def test_a_pursuit_with_no_record_anywhere_opens_one_checkoff_behind(tmp_path, monkeypatch):
    """Declared because it is wanted. Reading a fresh entry as current would keep
    it out of the draw until someone zeroed it by hand."""
    state = balance_state(tmp_path, monkeypatch, [])

    assert state['balance'] == {'chore': 1.0, 'read': 45.0}
    assert sorted(pursuits.pinned(state)) == ['chore', 'read']


def test_a_skip_stops_the_clock_rather_than_deferring_the_debt(tmp_path, monkeypatch):
    running = balance_state(tmp_path, monkeypatch, [zeroed('chore', 10.0)])
    passed = balance_state(tmp_path, monkeypatch, [zeroed('chore', 10.0), skipped('chore', 8.0, 4.0)])

    assert running['balance']['chore'] == 10.0
    assert passed['balance']['chore'] == 6.0, 'the four skipped days were never asked for'


def test_overlapping_skips_take_their_span_out_once(tmp_path, monkeypatch):
    """Renewing a skip before the last expires is the ordinary case, and adding
    the two lengths would take the same days off the clock twice."""
    once = balance_state(tmp_path, monkeypatch, [zeroed('chore', 10.0), skipped('chore', 8.0, 4.0)])
    twice = balance_state(tmp_path, monkeypatch, [zeroed('chore', 10.0), skipped('chore', 8.0, 4.0), skipped('chore', 7.0, 3.0)])

    assert once['balance']['chore'] == twice['balance']['chore'] == 6.0


def test_a_standing_skip_reaches_the_pins_as_well_as_the_draw(tmp_path, monkeypatch):
    """A pass now names the span it covers, so honoring it everywhere is what
    makes it a decision rather than a reroll."""
    state = balance_state(tmp_path, monkeypatch, [zeroed('chore', 10.0), skipped('chore', 0.0, 14.0)])

    assert state['suppressed'] == ['chore']
    assert state['effective']['chore'] == 0.0
    assert 'chore' not in pursuits.pinned(state)


def test_a_skip_that_has_run_out_suppresses_nothing(tmp_path, monkeypatch):
    state = balance_state(tmp_path, monkeypatch, [zeroed('chore', 10.0), skipped('chore', 8.0, 4.0)])

    assert state['suppressed'] == []
    assert state['effective']['chore'] > 0


def test_the_standing_line_names_each_pursuit_in_its_own_unit(tmp_path, monkeypatch):
    state = balance_state(tmp_path, monkeypatch, [zeroed('chore', 3.0), zeroed('read', 2.0)])

    assert pursuits.standing_line(state) == 'behind · chore +3.0, read +90m'


def test_the_standing_line_is_silent_when_nothing_is_owed(tmp_path, monkeypatch):
    ahead = [zeroed('chore', 0.0), done('chore', 0.0), zeroed('read', 0.0), done('read', 0.0, minutes=60)]
    assert pursuits.standing_line(balance_state(tmp_path, monkeypatch, ahead)) == ''


def test_a_balance_past_its_band_is_reported(tmp_path, monkeypatch):
    """A daily chore asks for seven a week, so two weeks of band is fourteen."""
    state = balance_state(tmp_path, monkeypatch, [zeroed('chore', 30.0), zeroed('read', 0.0)])

    assert [name for name, _, _ in pursuits.out_of_band(state)] == ['chore']


def test_a_surplus_is_reported_as_loudly_as_a_debt(tmp_path, monkeypatch):
    """Both say the weight is wrong, and only one of them ever feels like it."""
    burst = [zeroed('chore', 1.0), zeroed('read', 0.0)] + [done('chore', 0.5) for _ in range(30)]
    state = balance_state(tmp_path, monkeypatch, burst)

    assert state['balance']['chore'] == -29.0
    assert [name for name, _, _ in pursuits.out_of_band(state)] == ['chore']


def test_a_pursuit_declaring_its_own_band_is_judged_by_that_one(tmp_path, monkeypatch):
    wider = BALANCE_REGISTER.replace('    cadence: 1d\n\n  read:', '    cadence: 1d\n    warn_weeks: 10\n\n  read:')
    state = balance_state(tmp_path, monkeypatch, [zeroed('chore', 30.0), zeroed('read', 0.0)], register=wider)

    assert state['balance']['chore'] == 30.0
    assert pursuits.out_of_band(state) == []


def test_the_balance_spans_every_machines_journal(tmp_path, monkeypatch):
    """One file per machine is the whole sync story, so a balance reading only the
    local one reports a laptop's week as the whole week."""
    register_path = tmp_path / 'pursuits.yml'
    register_path.write_text(BALANCE_REGISTER)
    monkeypatch.setattr(pursuits, 'REGISTER', register_path)
    monkeypatch.setattr(pursuits, 'JOURNAL_DIR', tmp_path / 'state')
    monkeypatch.setattr(pursuits, 'CACHE_DIR', tmp_path / 'cache')
    for machine, minutes in (('archlinux', 30), ('macmini', 45), ('mbp', 15)):
        journal.append(journal.journal_path(tmp_path / 'state', machine), zeroed('read', 1.0))
        journal.append(journal.journal_path(tmp_path / 'state', machine), done('read', 0.5, minutes=minutes))

    state = pursuits.build_state(pursuits.load_pursuits(), NOW)

    assert state['balance']['read'] == 45.0 - 90.0, 'one day asked for, ninety minutes typed across three boxes'


def test_the_draw_still_offers_something_when_nothing_is_owed(tmp_path, monkeypatch):
    """Seeing what else is on offer while nothing is urgent is the point of the
    fallback. An empty screen reads as the tool having broken, not as current."""
    current = [zeroed('chore', 0.0), done('chore', 0.0), zeroed('read', 0.0), done('read', 0.0, minutes=60)]
    state = balance_state(tmp_path, monkeypatch, current)

    assert set(state['effective'].values()) == {0.0}
    assert pursuits.pinned(state) == []
    assert sorted(pursuits.compute_draw(state, seed=1)['drawn']) == ['chore', 'read']


def test_the_standing_line_counts_the_names_it_does_not_spell_out(tmp_path, monkeypatch):
    wide = 'pursuits:\n' + ''.join(f'  p{index}:\n    weight: 10\n    cadence: 1d\n' for index in range(6))
    state = balance_state(tmp_path, monkeypatch, [zeroed(f'p{index}', 3.0) for index in range(6)], register=wide)

    line = pursuits.standing_line(state)

    assert line.count(',') == pursuits.STANDING_NAMES - 1
    assert line.endswith('doit pursuits list'), 'a typeable command, never a remainder count'


def test_format_balance_carries_the_unit_and_always_the_sign(tmp_path, monkeypatch):
    assert pursuits.format_balance(25.0, 45.0) == '+25m'
    assert pursuits.format_balance(-90.0, 45.0) == '-90m'
    assert pursuits.format_balance(3.0, None) == '+3.0'
    assert pursuits.format_balance(-1.5, None) == '-1.5'
    assert pursuits.format_balance(0.0, None) == '+0.0'


def multiplier_state(declared: float | None, implied: float) -> dict:
    return {'intervals': {'a': declared}, 'implied_intervals': {'a': implied}}


def test_a_cadence_shorter_than_the_implied_interval_reports_the_multiple():
    # The number that was invisible: `cadence: 1d` against an implied 3.7d is
    # what took a third of every draw on a weight claiming a ninth of it.
    assert pursuits.urgency_multiplier(multiplier_state(1.0, 3.7), 'a') == ' ×3.7'


def test_a_cadence_longer_than_the_implied_interval_reports_the_division():
    assert pursuits.urgency_multiplier(multiplier_state(7.0, 3.7), 'a') == ' ÷1.9'


@pytest.mark.parametrize('declared', [3.4, 3.7, 4.0])
def test_a_cadence_within_a_tenth_of_the_implied_interval_says_nothing(declared):
    # That close is the measured logging rate wobbling, not a decision.
    assert pursuits.urgency_multiplier(multiplier_state(declared, 3.7), 'a') == ''


def test_a_pursuit_with_no_cadence_has_no_multiplier():
    assert pursuits.urgency_multiplier(multiplier_state(None, 3.7), 'a') == ''


def test_an_infinite_implied_interval_has_no_multiplier():
    # A zero-weight pursuit implies an infinite interval; dividing by it is not a
    # ratio anyone can read.
    assert pursuits.urgency_multiplier(multiplier_state(3.0, math.inf), 'a') == ''


def answers(monkeypatch, *replies: str) -> list[str]:
    """Feed the prompts a script, recording what each one asked."""
    asked: list[str] = []
    queued = list(replies)

    def fake_input(prompt: str = '') -> str:
        asked.append(prompt)
        return queued.pop(0)

    monkeypatch.setattr(pursuits.console, 'input', fake_input)
    monkeypatch.setattr(render, '_no_input', False)
    monkeypatch.setattr(pursuits, 'can_prompt', lambda: True)
    return asked


def test_log_without_a_pursuit_asks_which_one(sandbox, monkeypatch):
    asked = answers(monkeypatch, 'chores', '', '')
    assert pursuits.cmd_log(None, [], None, None, assume_yes=True, no_write=True) == 0
    assert journal.read_all(sandbox / 'state')[0]['pursuit'] == 'chores'
    assert any('pursuit' in prompt for prompt in asked)


def test_a_timed_pursuit_is_asked_how_long_it_took(sandbox, monkeypatch):
    # A checkoff there is a number of minutes, so an entry without one records
    # that something happened and not how much of it.
    answers(monkeypatch, '', '45')
    assert pursuits.cmd_log('read-library', [], None, None, assume_yes=True, no_write=True) == 0
    assert journal.read_all(sandbox / 'state')[0]['duration_minutes'] == 45


def test_a_counted_pursuit_is_never_asked_how_long_it_took(sandbox, monkeypatch):
    asked = answers(monkeypatch, '')
    assert pursuits.cmd_log('chores', [], None, None, assume_yes=True, no_write=True) == 0
    assert not [prompt for prompt in asked if 'minutes' in prompt]
    assert journal.read_all(sandbox / 'state')[0]['duration_minutes'] is None


def test_minutes_on_a_counted_pursuit_is_a_usage_error(sandbox, monkeypatch):
    """The register's declaration is what makes a pursuit measured in time, so a
    duration on one that is not has nothing to be a fraction of. A flag the run
    cannot honor exits 2, the way every other misuse of this verb does."""
    answers(monkeypatch)
    ran = runner.invoke(cli_app, ['log', 'chores', '--minutes', '20', '--yes', '--no-write'])

    assert ran.exit_code == 2
    assert journal.read_all(sandbox / 'state') == []


def test_a_timed_pursuit_with_nobody_to_ask_is_a_usage_error(sandbox, monkeypatch):
    """The duration is what the entry is made of, so an unanswerable prompt is a
    misuse rather than a reason to write half an entry.

    Nothing here reads the message. typer renders a usage error through rich,
    which forces color under GITHUB_ACTIONS and splits an option name into
    escape-separated pieces — a substring naming the flag cannot match on the
    only run that gates a merge.
    """
    monkeypatch.setattr(pursuits, 'can_prompt', lambda: False)
    ran = runner.invoke(cli_app, ['log', 'read-library', '--yes', '--no-write'])

    assert ran.exit_code == 2
    assert journal.read_all(sandbox / 'state') == []


def test_a_field_passed_as_a_flag_is_never_asked_about(sandbox, monkeypatch):
    asked = answers(monkeypatch)
    assert pursuits.cmd_log('read-library', ['trimmed'], None, 20, assume_yes=True, no_write=True) == 0
    assert asked == []
    record = journal.read_all(sandbox / 'state')[0]
    assert record['note'] == 'trimmed'
    assert record['duration_minutes'] == 20


def test_log_without_a_pursuit_and_without_a_terminal_names_the_argument(sandbox, monkeypatch):
    monkeypatch.setattr(pursuits, 'can_prompt', lambda: False)
    assert pursuits.cmd_log(None, [], None, None, assume_yes=True, no_write=True) == 1
    assert journal.read_all(sandbox / 'state') == []


def test_abandoning_the_pursuit_prompt_logs_nothing(sandbox, monkeypatch):
    answers(monkeypatch, '')
    assert pursuits.cmd_log(None, [], None, None, assume_yes=True, no_write=True) == 1
    assert journal.read_all(sandbox / 'state') == []


def test_the_minutes_prompt_refuses_what_is_not_a_positive_whole_number(monkeypatch):
    answers(monkeypatch, 'ages', '-3', '0', '', '30')
    assert pursuits.prompt_for_minutes() == 30


def test_the_pursuit_prompt_marks_what_the_draw_offered(sandbox, monkeypatch, capsys):
    answers(monkeypatch, 'chores')
    assert pursuits.prompt_for_pursuit(pursuits.load_pursuits(), ['chores']) == 'chores'
    printed = capsys.readouterr().out
    assert '› chores' in printed


def test_a_skip_records_the_span_it_covers(sandbox, monkeypatch):
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')
    assert pursuits.cmd_skip('chores', '2w') == 0

    record = journal.read_all(sandbox / 'state')[0]
    started = journal.parse_time(record['occurred_at'])
    assert (journal.parse_time(record['expires_at']) - started).days == 14


def test_a_skip_with_no_duration_covers_one_interval(sandbox, monkeypatch):
    """A bare skip still means "not this time" rather than committing to a length
    nobody chose, so it takes the length the pursuit's own schedule states."""
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')
    assert pursuits.cmd_skip('chores', None) == 0

    record = journal.read_all(sandbox / 'state')[0]
    started = journal.parse_time(record['occurred_at'])
    assert (journal.parse_time(record['expires_at']) - started).days == 7, 'the cadence is 1w'


def test_a_skip_names_when_the_pursuit_returns(sandbox, monkeypatch, capsys):
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')
    assert pursuits.cmd_skip('read-library', '3d') == 0

    # The interpolated values, never the sentence around them. A year printed as
    # `08 Sep` makes `--for 1y` and `--for 1d` read identically, and the verb that
    # retires the mark has to be reachable from the screen that writes it.
    printed = capsys.readouterr().out
    assert '11 Sep 2026' in printed
    assert 'doit pursuits resume read-library' in printed


def test_skip_span_falls_back_to_a_day_where_the_weight_implies_no_interval():
    assert pursuits.skip_span(None, math.inf) == 1
    assert pursuits.skip_span(None, None) == 1
    assert pursuits.skip_span('2w', 3.0) == 14


def test_a_reset_writes_a_zero_point_without_touching_what_happened(sandbox, monkeypatch):
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')
    log_days_ago(sandbox / 'state', 'chores', 3)

    assert pursuits.cmd_reset('chores', assume_yes=False) == 0

    records = journal.read_all(sandbox / 'state')
    assert [record['event'] for record in records] == ['done', 'reset']
    assert records[1]['pursuit'] == 'chores'


def test_a_reset_with_no_name_zeroes_every_pursuit(sandbox, monkeypatch):
    """Shipping runs this once, which is also the migration: every balance starts
    at zero rather than being backfilled from history."""
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')

    assert pursuits.cmd_reset(None, assume_yes=True) == 0

    zeroed = {record['pursuit'] for record in journal.read_all(sandbox / 'state') if record['event'] == 'reset'}
    assert zeroed == set(pursuits.load_pursuits())


def test_resetting_a_pursuit_that_does_not_exist_writes_nothing(sandbox, monkeypatch):
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')
    assert pursuits.cmd_reset('nonesuch', assume_yes=False) == 1
    assert journal.read_all(sandbox / 'state') == []


def log_days_ago(directory: Path, pursuit: str, count: int, minutes: int | None = None) -> None:
    when = datetime.now().astimezone() - timedelta(days=count)
    record: dict = {'pursuit': pursuit, 'event': 'done', 'occurred_at': when.isoformat()}
    if minutes is not None:
        record['duration_minutes'] = minutes
    journal.append(journal.journal_path(directory, 'testbox'), record)


def days_ago_iso(count: int) -> str:
    return (datetime.now().astimezone() - timedelta(days=count)).date().isoformat()


def stub_evidence_days(monkeypatch, dates_by_pursuit: dict[str, list[str]]) -> None:
    """Answer as the cache would, without asking any app.

    `cmd_drift` reads the clock itself, so its window is real time — a fixture
    date would age out of the window and the test would pass until it didn't.
    """
    payload = {'pursuits': {name: {'dates': dates} for name, dates in dates_by_pursuit.items()}}
    monkeypatch.setattr(pursuits.evidence, 'refresh', lambda *args, **kwargs: payload)


def drift_rows(capsys, days: int = 90) -> dict:
    assert pursuits.cmd_drift(days=days, as_json=True) == 0
    return {row['pursuit']: row for row in json.loads(capsys.readouterr().out)['rows']}


def test_did_counts_what_an_app_saw_and_the_journal_never_did(sandbox, monkeypatch, capsys):
    """The inversion this column exists to end: a pursuit with a backend is done
    inside that backend, so counting journal entries reported the busiest strand
    as the idle one."""
    stub_evidence_days(monkeypatch, {'study-computer-science': [days_ago_iso(n) for n in range(1, 9)]})

    row = drift_rows(capsys)['study-computer-science']

    assert row['amount'] == 8.0
    assert row['logs'] == 0, 'nothing was ever typed for it'
    assert row['realized_share'] == 100.0


def test_a_day_carried_by_both_records_counts_once(sandbox, monkeypatch, capsys):
    """Union, not sum. Logging what an app already reported would double it."""
    log_days_ago(sandbox / 'state', 'chores', 1)
    stub_evidence_days(monkeypatch, {'chores': [days_ago_iso(1)]})

    row = drift_rows(capsys)['chores']

    assert (row['amount'], row['logs']) == (1.0, 1)


def test_a_retired_pursuit_takes_no_slice_of_the_denominator(sandbox, capsys):
    """drift iterates the register, so a stranded name can never get a row — and
    activity counted into a total it never appears in leaves every share short."""
    log_days_ago(sandbox / 'state', 'chores', 1)
    log_days_ago(sandbox / 'state', 'gone-from-the-register', 2)

    rows = drift_rows(capsys)

    assert 'gone-from-the-register' not in rows
    counted = [row for row in rows.values() if row['unit'] == 'checkoffs']
    assert sum(row['realized_share'] for row in counted) == 100.0


def test_the_two_units_are_reported_against_their_own_denominators(sandbox, capsys):
    """Minutes and completions do not add, so a single cross-register share would
    be a number with no denominator behind it."""
    log_days_ago(sandbox / 'state', 'chores', 1)
    log_days_ago(sandbox / 'state', 'read-library', 1, minutes=90)

    rows = drift_rows(capsys)

    assert (rows['chores']['unit'], rows['chores']['amount']) == ('checkoffs', 1.0)
    assert (rows['read-library']['unit'], rows['read-library']['amount']) == ('minutes', 90.0)
    assert rows['chores']['realized_share'] == rows['read-library']['realized_share'] == 100.0


def test_an_app_date_older_than_the_window_is_not_counted(sandbox, monkeypatch, capsys):
    stub_evidence_days(monkeypatch, {'chores': [days_ago_iso(3), days_ago_iso(40)]})

    assert drift_rows(capsys, days=7)['chores']['amount'] == 1.0


def test_the_table_renders_when_only_an_app_recorded_anything(sandbox, monkeypatch, capsys):
    """App days and no typed logs is the ordinary window for a pursuit with a
    backend, and a guard on the log count suppressed the whole report there."""
    stub_evidence_days(monkeypatch, {'chores': [days_ago_iso(1)]})

    assert pursuits.cmd_drift(days=90, as_json=False) == 0

    printed = capsys.readouterr().out
    assert 'chores' in printed
    assert 'Counted in completions' in printed


def test_a_window_with_nothing_in_it_says_so_rather_than_drawing_an_empty_table(sandbox, capsys):
    assert pursuits.cmd_drift(days=90, as_json=False) == 0
    assert 'Nothing recorded in the window yet' in capsys.readouterr().out


BACKED_REGISTER = """\
pursuits:
  backed:
    description: Done inside its own app, never typed here
    weight: 25
    cadence: 3d
    evidence: echo []
    evidence_time: completed_at
"""


def test_completed_since_counts_each_typed_entry_as_its_own_checkoff():
    """Three in one evening is three checkoffs. Collapsing them to a date is the
    burst the balance exists to credit."""
    now = datetime.now().astimezone()
    records = [{'pursuit': 'chores', 'event': 'done', 'occurred_at': (now - timedelta(hours=h)).isoformat()} for h in (1, 3, 5)]

    assert pursuits.completed_since(records, [], now, now - timedelta(days=1), None) == 3.0


def test_completed_since_reads_a_timed_entry_as_the_minutes_it_carries():
    now = datetime.now().astimezone()
    records = [{'pursuit': 'read', 'event': 'done', 'occurred_at': now.isoformat(), 'duration_minutes': 20}]

    assert pursuits.completed_since(records, [], now, now - timedelta(days=1), 45.0) == 20.0


def test_completed_since_drops_an_app_day_the_journal_already_carries():
    """One act reported by both records counts once, or logging what the app
    already saw would pay it off twice."""
    now = datetime.now().astimezone()
    records = [{'pursuit': 'chores', 'event': 'done', 'occurred_at': now.isoformat()}]

    assert pursuits.completed_since(records, [now.date()], now, now - timedelta(days=1), None) == 1.0


def test_completed_since_adds_an_app_day_the_journal_never_saw():
    now = datetime.now().astimezone()
    seen = [now.date() - timedelta(days=n) for n in (1, 2)]

    assert pursuits.completed_since([], seen, now, now - timedelta(days=5), None) == 2.0


def test_completed_since_ignores_everything_before_the_zero_point():
    now = datetime.now().astimezone()
    records = [{'pursuit': 'chores', 'event': 'done', 'occurred_at': (now - timedelta(days=d)).isoformat()} for d in (1, 9)]

    assert pursuits.completed_since(records, [], now, now - timedelta(days=5), None) == 1.0


def test_an_app_day_on_a_timed_pursuit_counts_one_whole_checkoff():
    """An app answers in days rather than durations, so a day it reports is one
    checkoff whatever happened inside it."""
    now = datetime.now().astimezone()

    assert pursuits.completed_since([], [now.date()], now, now - timedelta(days=1), 45.0) == 45.0


def test_a_backed_pursuit_reads_the_days_its_app_reported(tmp_path, sandbox, monkeypatch):
    """Reading the journal alone made a pursuit finished inside its own app four
    days running come out overdue."""
    monkeypatch.setattr(pursuits, 'REGISTER', write_register(tmp_path, BACKED_REGISTER))
    stub_evidence_days(monkeypatch, {'backed': [days_ago_iso(n) for n in (0, 1, 2, 3)]})

    state = pursuits.build_state(pursuits.load_pursuits(), datetime.now().astimezone())

    assert state['balance']['backed'] < 0, 'four days running against a 3-day cadence is ahead, not overdue'
    assert state['effective']['backed'] == 0.0


def test_a_backed_pursuit_with_no_record_anywhere_opens_one_checkoff_behind(tmp_path, sandbox, monkeypatch):
    """Nothing on either record is the state a fresh pursuit is in, and it is due
    once rather than infinitely urgent."""
    monkeypatch.setattr(pursuits, 'REGISTER', write_register(tmp_path, BACKED_REGISTER))
    stub_evidence_days(monkeypatch, {})

    state = pursuits.build_state(pursuits.load_pursuits(), datetime.now().astimezone())

    assert state['balance']['backed'] == 1.0
    assert pursuits.pinned(state) == ['backed']


def test_a_paused_pursuit_with_no_days_gets_no_row(sandbox, monkeypatch, capsys):
    """Pausing drops its evidence entry too, so an untouched one would sit here
    as a hollow row forever — said 0%, did 0%, no days."""
    stub_evidence_days(monkeypatch, {'chores': [days_ago_iso(1)]})

    assert 'paused-thing' not in drift_rows(capsys)


def test_a_paused_pursuit_with_days_keeps_them_and_states_no_share(sandbox, capsys):
    """Paused mid-window, its history is still history. It stated nothing for the
    window though, and 0% would read as a claim it never made."""
    log_days_ago(sandbox / 'state', 'paused-thing', 1)

    row = drift_rows(capsys)['paused-thing']

    assert row['amount'] == 1.0
    assert row['stated_share'] is None


def test_a_paused_pursuit_renders_a_dash_rather_than_a_share(sandbox, capsys):
    log_days_ago(sandbox / 'state', 'paused-thing', 1)

    assert pursuits.cmd_drift(days=90, as_json=False) == 0

    printed = [line for line in capsys.readouterr().out.splitlines() if 'paused-thing' in line]
    assert printed and '—' in printed[0], printed


def test_the_orphan_warning_names_the_box_holding_each_stranded_count(sandbox, monkeypatch, capsys):
    """Every box writes its own counter file, so one this box did not write is not
    this box's to edit — and editing it anyway is what gives a synced file two
    writers. Saying whose it is turns a nag into an instruction."""
    counts = sandbox / 'state'
    counts.mkdir(parents=True, exist_ok=True)
    (counts / 'next-offers-testbox.json').write_text(json.dumps({'retired-here': 8}))
    (counts / 'next-offers-otherbox.json').write_text(json.dumps({'retired-there': 7}))
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')

    pursuits.render_orphaned_counters(pursuits.load_pursuits())

    printed = capsys.readouterr().out
    assert 'retired-here  8  testbox' in printed
    assert 'another box' not in printed.split('retired-here')[1].split('\n')[0]
    assert 'retired-there  7  otherbox (another box — clear it there)' in printed


def test_the_orphan_warning_stays_silent_when_every_count_has_a_pursuit(sandbox, capsys):
    counts = sandbox / 'state'
    counts.mkdir(parents=True, exist_ok=True)
    (counts / 'next-offers-testbox.json').write_text(json.dumps({'chores': 4}))

    pursuits.render_orphaned_counters(pursuits.load_pursuits())

    assert capsys.readouterr().out == ''


def resolving(tmp_path, rows, **config) -> dict | None:
    """resolve_one against a payload on disk, with no subprocess of our own."""
    payload = tmp_path / 'rows.json'
    payload.write_text(json.dumps(rows))
    return pursuits.resolve_one('journal', {'resolve': f'cat {payload}', 'label': 'name', 'id': 'id', **config})


def test_resolve_where_keeps_only_the_rows_that_are_this_pursuit(tmp_path):
    """A backend with no filter for the distinction returns everything and only
    some of it is the pursuit. `icb tasks` carries no tag, so what marks a prompt
    is its name."""
    rows = [
        {'name': 'Self Authoring', 'id': 344},
        {'name': 'Journal', 'id': 474},
    ]

    resolved = resolving(tmp_path, rows, resolve_where={'name': 'Journal'})

    assert resolved['label'] == 'Journal'
    assert resolved['id'] == '474'


def test_resolve_where_matching_nothing_resolves_to_nothing(tmp_path):
    """Not an error: the pursuit is due and there is no prompt written down, which
    is a true answer and the description is what shows instead."""
    assert resolving(tmp_path, [{'name': 'Self Authoring', 'id': 344}], resolve_where={'name': 'Journal'}) is None


def test_resolve_without_a_where_takes_the_first_row_as_before(tmp_path):
    """The generalization has to leave every register without it untouched."""
    rows = [{'name': 'Self Authoring', 'id': 344}, {'name': 'Journal', 'id': 474}]

    assert resolving(tmp_path, rows)['label'] == 'Self Authoring'


def test_resolve_where_matches_across_types_the_way_evidence_does(tmp_path):
    """A config file has no types, so an id that is 474 in JSON and "474" in YAML
    is the same id — the same rule `evidence_where` applies."""
    rows = [{'name': 'Journal', 'id': 474}]

    assert resolving(tmp_path, rows, resolve_where={'id': '474'})['id'] == '474'


def test_the_odds_reported_are_the_odds_the_draw_ran_on(tmp_path, monkeypatch):
    """Reporting `probability` from the urgency-multiplied weights while sampling
    something else put five chosen rows on screen at 0.0% each, under a legend
    calling that number the chance of being drawn first."""
    current = [zeroed('chore', 0.0), done('chore', 0.0), zeroed('read', 0.0), done('read', 0.0, minutes=60)]
    state = balance_state(tmp_path, monkeypatch, current)

    drawn = pursuits.compute_draw(state, seed=1)['drawn']

    assert drawn, 'the pool is what fills the screen'
    assert all(state['probability'][name] > 0 for name in drawn)
    assert abs(sum(state['probability'].values()) - 1.0) < 1e-9


def test_pausing_a_timed_pursuit_does_not_move_every_other_interval(tmp_path, monkeypatch):
    """The measured rate walks every record in the journal, so the size map it
    reads is register-wide. Scoping it to the active set reclassified a paused
    pursuit's whole history as one-checkoff-per-entry and tripled the divisor
    every other pursuit's interval is derived from."""
    logs = [done('read', days_ago=index / 2, minutes=90) for index in range(20)]
    running = balance_state(tmp_path, monkeypatch, logs)
    paused = balance_state(tmp_path, monkeypatch, logs, register=BALANCE_REGISTER + '    paused: true\n')

    assert 'read' not in paused['active']
    assert paused['logs_per_day'] == running['logs_per_day']


def test_the_register_wide_size_map_covers_a_pursuit_the_active_set_drops():
    register = {'read': {'weight': 1, 'checkoff_minutes': 30}, 'gone': {'weight': 0, 'checkoff_minutes': 45}}
    assert pursuits.declared_minutes(register) == {'read': 30.0, 'gone': 45.0}


def test_a_row_the_register_dropped_renders_rather_than_crashing(sandbox, monkeypatch, capsys):
    """The draw outlives the register by up to a quarter of an hour, so pausing a
    drawn pursuit inside that window left `doit next` dying on a traceback."""
    pursuits.save_cached_draw({'draw_id': 'abc', 'created_at': NOW.isoformat(), 'pinned': [], 'drawn': ['paused-thing']})
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)

    pursuits.render_row(1, 'paused-thing', state, {}, pin=False, width=12)

    assert 'paused-thing' in capsys.readouterr().out


def test_the_warning_band_never_falls_below_one_checkoff(tmp_path, monkeypatch):
    """Weeks and checkoffs are different units, so two weeks of band on a monthly
    cadence is 0.47 of a chore — one chore coming due pinned as overdue and
    reported as a weight that is not true, on the same screen."""
    monthly = BALANCE_REGISTER.replace('cadence: 1d', 'cadence: 1mo')
    state = balance_state(tmp_path, monkeypatch, [zeroed('chore', 30.0), zeroed('read', 0.0)], register=monthly)

    assert state['balance']['chore'] == 1.0
    assert pursuits.warn_threshold(state, 'chore') == 1.0
    assert pursuits.out_of_band(state) == [], 'exactly one checkoff owed is due, not a wrong weight'


def test_the_standing_line_needs_a_whole_checkoff_before_it_says_behind(tmp_path, monkeypatch):
    """A balance climbs continuously from zero, so every pursuit passes through
    the fraction just above it. `behind · read +0m` names nothing anyone can act on."""
    barely = balance_state(tmp_path, monkeypatch, [zeroed('chore', 0.2), zeroed('read', 0.02)])

    assert barely['balance']['chore'] > 0 and barely['balance']['read'] > 0
    assert pursuits.standing_line(barely) == ''


def test_a_backdated_log_reports_the_standing_the_next_command_will(sandbox, monkeypatch, capsys):
    """Subtracting what was logged printed a number the model disagreed with: an
    entry before the zero point pays nothing off and arithmetic cannot see that."""
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')
    assert pursuits.cmd_reset('chores', assume_yes=True) == 0
    capsys.readouterr()

    assert pursuits.cmd_log('chores', [], '3d', None, assume_yes=True, no_write=True) == 0
    printed = capsys.readouterr().out

    state = pursuits.build_state(pursuits.load_pursuits(), datetime.now().astimezone())
    assert pursuits.format_balance(state['balance']['chores'], None) in printed


def test_resume_ends_a_standing_skip(sandbox, monkeypatch):
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')
    assert pursuits.cmd_skip('chores', '1y') == 0
    assert 'chores' in pursuits.build_state(pursuits.load_pursuits(), datetime.now().astimezone())['suppressed']

    assert pursuits.cmd_resume('chores') == 0

    assert pursuits.build_state(pursuits.load_pursuits(), datetime.now().astimezone())['suppressed'] == []


def test_a_later_skip_shortens_an_earlier_one(sandbox, monkeypatch):
    """Taking the furthest-reaching expiry made a mistyped `--for 1y` unreachable
    by any command the tool ships, on a file that is never rewritten."""
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')
    assert pursuits.cmd_skip('chores', '1y') == 0
    assert pursuits.cmd_skip('chores', '1d') == 0

    now = datetime.now().astimezone()
    own = pursuits.records_by_pursuit(journal.read_all(sandbox / 'state'))['chores']
    assert (pursuits.skip_expiry(own) - now).days == 0


def test_resume_with_no_name_brings_back_everything_skipped(sandbox, monkeypatch):
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')
    assert pursuits.cmd_skip('chores', '2w') == 0
    assert pursuits.cmd_skip('read-library', '2w') == 0

    assert pursuits.cmd_resume(None) == 0

    assert pursuits.build_state(pursuits.load_pursuits(), datetime.now().astimezone())['suppressed'] == []


def test_zeroing_the_whole_register_is_refused_where_nobody_can_answer(sandbox, monkeypatch):
    """Every accrued balance goes at once and nothing the tool ships puts one back."""
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')
    monkeypatch.setattr(pursuits, 'can_prompt', lambda: False)

    assert pursuits.cmd_reset(None, assume_yes=False) == 1
    assert journal.read_all(sandbox / 'state') == []


def test_zeroing_one_pursuit_goes_through_unasked(sandbox, monkeypatch):
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')
    monkeypatch.setattr(pursuits, 'can_prompt', lambda: False)

    assert pursuits.cmd_reset('chores', assume_yes=False) == 0
    assert [record['pursuit'] for record in journal.read_all(sandbox / 'state')] == ['chores']


def test_zeroing_the_register_writes_one_state_snapshot_not_one_per_pursuit(sandbox, monkeypatch):
    """The vector describes the register rather than the pursuit being zeroed, so
    a copy per name writes the same payload N times into a synced append-only file."""
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')
    assert pursuits.cmd_reset(None, assume_yes=True) == 0

    records = journal.read_all(sandbox / 'state')
    assert len(records) == len(pursuits.load_pursuits())
    assert sum('state_at_log' in record for record in records) == 1


def test_an_unknown_key_in_a_sibling_block_is_refused(tmp_path):
    """A file that refuses one typo and silently defaults another teaches the
    reader it is strict, and they stop proofreading the half that is not."""
    path = write_register(tmp_path, 'pursuits:\n  a:\n    weight: 1\nbalance:\n  warn_weekz: 2\n')

    with pytest.raises(pursuits.RegisterError, match='warn_weekz'):
        pursuits.load_balance_settings(path)


def test_a_negative_duration_never_reaches_the_journal(sandbox, monkeypatch):
    """`checkoff_equivalent` reads a non-positive duration as one whole checkoff,
    so `--minutes -5` credited 30 minutes and printed `+5m`."""
    ran = runner.invoke(cli_app, ['log', 'read-library', '--minutes', '-5', '--yes', '--no-write'])

    assert ran.exit_code == 2
    assert journal.read_all(sandbox / 'state') == []


def test_the_explain_payload_carries_the_band_each_row_is_judged_against(sandbox):
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)
    payload = pursuits.explain_payload(state)

    assert set(payload['warn_bands']) == set(state['active'])
    assert payload['pool'] and set(payload['pool']) <= set(state['active'])


def test_drift_compares_both_shares_against_the_same_population(sandbox, capsys):
    """Reading `said` off the register-wide weights while `did` runs inside one
    unit compares two denominators, so a register lived exactly to its weights
    was flagged yellow — and a pursuit alone in its unit read 100% whatever it did."""
    # The counted weights are 25 / 5 / 35, so this is the register lived to plan.
    for name, times in (('chores', 5), ('read-longform', 1), ('study-computer-science', 7)):
        for _ in range(times):
            log_days_ago(sandbox / 'state', name, 1)
    log_days_ago(sandbox / 'state', 'read-library', 1, minutes=45)

    rows = drift_rows(capsys)
    counted = [row for row in rows.values() if row['unit'] == 'checkoffs']

    assert round(sum(row['stated_share'] for row in counted)) == 100
    assert all(abs(row['stated_share'] - row['realized_share']) < 1 for row in counted), 'lived to plan is not drift'
    assert rows['read-library']['stated_share'] == 100.0, 'the only timed pursuit is the whole of its unit'


def test_drift_reads_an_unparsable_timestamp_one_way(sandbox, capsys):
    """The amount and the log tally come from two walks over the same records, so
    a record counted by one and skipped by the other reports work with no time in it."""
    write_records(sandbox / 'state', [{'pursuit': 'chores', 'event': 'done', 'occurred_at': 'not a timestamp'}])

    row = drift_rows(capsys).get('chores')

    assert row is None or (row['logs'], row['amount']) == (0, 0.0)


def test_the_register_names_a_checkoff_size_and_the_log_names_a_measurement():
    """Two different quantities, so they do not share a word. `checkoff_minutes:`
    is how much counts as one checkoff; `--minutes` is what a sitting took."""
    assert 'checkoff_minutes' in pursuits.KNOWN_FIELDS
    assert 'minutes' not in pursuits.KNOWN_FIELDS
    assert '--minutes' in pursuits.TEMPLATE, 'the template says which is which'
