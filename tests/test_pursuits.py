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
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import pytest

from doit import journal
from doit import pursuits
from doit import render

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


def test_a_just_logged_pursuit_is_not_drawn_again(sandbox):
    log_done(sandbox / 'state', 'read-library', 0.0)
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)
    assert state['effective']['read-library'] == 0.0
    assert 'read-library' not in pursuits.compute_draw(state, seed=1)['drawn']


def test_a_cached_draw_is_reused_inside_the_window(sandbox):
    pursuits.save_cached_draw({'draw_id': 'abc', 'created_at': NOW.isoformat(), 'pinned': [], 'drawn': ['chores']})
    assert pursuits.load_cached_draw(NOW + timedelta(minutes=5))['draw_id'] == 'abc'


def test_a_cached_draw_expires(sandbox):
    pursuits.save_cached_draw({'draw_id': 'abc', 'created_at': NOW.isoformat(), 'pinned': [], 'drawn': []})
    assert pursuits.load_cached_draw(NOW + timedelta(minutes=pursuits.CACHE_MINUTES + 1)) is None


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


def test_a_row_with_nothing_extra_to_say_stays_one_line(capsys):
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)

    pursuits.render_row(1, 'chores', state, {'chores': {'label': 'Trim Dingo Nails'}}, False, 6)

    assert len(capsys.readouterr().out.strip().splitlines()) == 1


def test_a_backend_that_failed_gets_no_continuation_line(capsys):
    # The row already says the backend is unavailable; a second line under it
    # would be context for an item that was never resolved.
    state = pursuits.build_state(pursuits.load_pursuits(), NOW)
    resolved = {'chores': {'error': 'exited 1', 'backend': 'icb', 'context': 'stale', 'detail': 'stale'}}

    pursuits.render_row(1, 'chores', state, resolved, False, 6)

    assert len(capsys.readouterr().out.strip().splitlines()) == 1


def test_format_elapsed_switches_unit_rather_than_format():
    assert pursuits.format_elapsed(None) == 'never'
    assert pursuits.format_elapsed(0.2) == 'today'
    assert pursuits.format_elapsed(3) == '3d ago'
    assert pursuits.format_elapsed(30) == '4w ago'
    assert pursuits.format_elapsed(200) == '6mo ago'
