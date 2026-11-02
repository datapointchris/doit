"""Tests for doit.pursuits — register validation, state assembly, pins, and what a resolve offers.

The draw's math lives in doit.allocate and is tested there against a seeded
generator. What is tested here is everything layered on top: refusing a register
it cannot trust, deriving the state the draw runs on, pinning by cadence rather
than by chance, and never acting on an item it was not offered.

The commands are tested against the service in `api/tests`. The functions here
are the ones the service calls, handed the journal as it hands them its store.
"""

import datetime as dt
import itertools
import math
import re
from pathlib import Path

import pytest
import typer

from doit import pursuits
from doit import render
from doit import window

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'pursuits' / 'pursuits.yml'
NOW = dt.datetime.fromisoformat('2026-08-04T12:00:00-04:00')


def parsed(text: str, where: object = 'pursuits.yml') -> dict:
    """A register's text, parsed and validated as the service does when it is written."""
    return pursuits.pursuits_from_document(pursuits.parse_register(text, where), where)


def load(path: Path = FIXTURE) -> dict:
    return parsed(path.read_text(), path)


@pytest.fixture
def sandbox() -> list[dict]:
    """The journal a test logs into."""
    return []


def built(records: list[dict] | None = None, register: dict | None = None, now: dt.datetime = NOW, **keywords) -> dict:
    """The state over ``records``, writing first sightings and paces into them as the service writes them into its store."""
    records = [] if records is None else records
    return pursuits.build_state(
        load() if register is None else register, now, list(records), append=records.extend, machine='testbox', **keywords
    )


def tracked() -> dict:
    """`build` declares no pace, beside a paced pursuit the draw can offer."""
    return parsed('pursuits:\n  build:\n    description: Engineering\n  chores:\n    weight: 25\n    cadence: 3d\n')


def drift(
    records: list[dict],
    days: dict | None = None,
    since: str | None = pursuits.DRIFT_SINCE,
    until: str | None = None,
    register: dict | None = None,
) -> dict:
    """Each pursuit's drift row, keyed by name, as the service computes them over ``records`` and the days each app reported."""
    state = built(records, register, observed={}, evidence_days=days or {})
    start, end = window.window(since, until, NOW)
    return {row['pursuit']: row for row in pursuits.drift_rows(state['pursuits'], state, start, end)}


def day(days_ago: int) -> dt.date:
    return (NOW - dt.timedelta(days=days_ago)).date()


def write_register(tmp_path, body: str) -> Path:
    path = tmp_path / 'pursuits.yml'
    path.write_text(body)
    return path


def resolution(name: str, config: dict, document: object) -> dict:
    """What a resolve offers from the document its app answered."""
    return pursuits.choices_from(name, config, document, 'app')


def known_due(state: dict, name: str) -> float:
    """Days until a paced pursuit is due, which a paced pursuit always has."""
    due = pursuits.due_in_days(state, name)
    assert due is not None
    return due


def log_done(records: list[dict], pursuit: str, days_ago: float) -> None:
    records.append({'pursuit': pursuit, 'event': 'done', 'occurred_at': (NOW - dt.timedelta(days=days_ago)).isoformat()})


def test_the_register_reads_each_pursuit_by_name():
    register = load()
    assert register['chores']['cadence'] == '7d'
    assert register['read-library']['weight'] == 30


def test_a_typo_in_the_register_is_refused_as_a_register_error(tmp_path):
    # A typo in a weight file silently misallocates attention for months, so it
    # has to be loud rather than ignored. A slip in the YAML itself is refused the
    # same way, because every screen guarding the register catches only this.
    for text, said in (('pursuits:\n  a:\n    weght: 5\n', 'weght'), ('pursuits: [a\n', 'not valid YAML'), ('- a\n', 'mapping')):
        with pytest.raises(pursuits.RegisterError, match=said):
            load(write_register(tmp_path, text))


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
    documented = {line[4:].split(' ')[0].strip('#').strip() for line in pursuits.TEMPLATE.splitlines() if line.startswith('#   ')}
    unknown = sorted(name for name in documented if name and name.islower() and name.isidentifier() and name not in pursuits.KNOWN_FIELDS)

    assert not unknown, f'named in the template but the loader would refuse it: {", ".join(unknown)}'


def test_a_paced_pursuit_with_no_weight_is_refused(tmp_path):
    path = write_register(tmp_path, 'pursuits:\n  a:\n    description: no weight\n    cadence: 3d\n')
    with pytest.raises(pursuits.RegisterError, match='weight'):
        load(path)


def test_a_boolean_weight_is_refused(tmp_path):
    # bool is an int in Python, so `weight: true` would otherwise pass as 1.
    path = write_register(tmp_path, 'pursuits:\n  a:\n    weight: true\n')
    with pytest.raises(pursuits.RegisterError, match='weight'):
        load(path)


def test_a_nonsense_cadence_is_refused(tmp_path):
    path = write_register(tmp_path, 'pursuits:\n  a:\n    weight: 5\n    cadence: soon\n')
    with pytest.raises(pursuits.RegisterError, match='cadence'):
        load(path)


def test_on_log_without_resolve_is_refused(tmp_path):
    path = write_register(tmp_path, 'pursuits:\n  a:\n    weight: 5\n    cadence: 3d\n    on_log: echo hi\n')
    with pytest.raises(pursuits.RegisterError, match='on_log'):
        load(path)


def test_weekly_minutes_is_read_as_the_pace(tmp_path):
    path = write_register(tmp_path, 'pursuits:\n  a:\n    weight: 5\n    weekly_minutes: 40\n')
    assert load(path)['a']['weekly_minutes'] == 40


@pytest.mark.parametrize('value', ['0', '-5', 'true', '"40"', '12.5'])
def test_weekly_minutes_that_is_not_a_positive_whole_number_is_refused(tmp_path, value):
    path = write_register(tmp_path, f'pursuits:\n  a:\n    weight: 5\n    weekly_minutes: {value}\n')
    with pytest.raises(pursuits.RegisterError, match='weekly_minutes'):
        load(path)


@pytest.mark.parametrize('token', ['1w', '2mo', '3.5d', '1w2d', '0d', '7', 'd'])
def test_a_cadence_that_is_not_whole_days_is_refused(tmp_path, token):
    # `1w` reads naturally and invites `1w2d` and `3.5d`, which nobody checks
    # twice. Days are the one unit every cadence can be written in exactly.
    path = write_register(tmp_path, f'pursuits:\n  a:\n    weight: 5\n    cadence: {token}\n')
    with pytest.raises(pursuits.RegisterError, match='whole number of days'):
        load(path)


def test_a_weight_with_no_pace_is_refused(tmp_path):
    # A forgotten pace leaves exactly this shape. Read as tracked, the pursuit
    # would leave the draw with nobody told.
    path = write_register(tmp_path, 'pursuits:\n  a:\n    weight: 5\n')
    with pytest.raises(pursuits.RegisterError, match='takes no weight'):
        load(path)


def test_a_pursuit_with_no_pace_is_tracked_and_never_drawn(sandbox):
    log_done(sandbox, 'build', 2)

    state = built(sandbox, tracked())

    assert list(state['tracked']) == ['build']
    assert 'build' not in state['active']
    assert 'build' not in state['pool']
    assert 'build' not in pursuits.compute_draw(state, seed=1)['offered']
    assert round(state['days_since']['build']) == 2


def test_a_pursuit_declaring_both_paces_is_refused(tmp_path):
    path = write_register(tmp_path, 'pursuits:\n  a:\n    weight: 5\n    cadence: 7d\n    weekly_minutes: 60\n')
    with pytest.raises(pursuits.RegisterError, match='not both'):
        load(path)


def test_paused_and_expired_pursuits_stay_out_of_the_active_set(sandbox):
    state = built(sandbox)
    assert 'paused-thing' not in state['active']
    assert 'expired-thing' not in state['active']
    assert 'chores' in state['active']


def test_a_paused_pursuit_is_still_listed(sandbox):
    # Out of the draw, not out of the file — the register is the record of intent.
    assert 'paused-thing' in built(sandbox)['pursuits']


def test_the_interval_and_size_are_the_declared_pace(sandbox):
    state = built(sandbox)
    assert state['intervals']['chores'] == 7.0
    assert state['intervals']['study-computer-science'] == 2.0
    assert state['intervals']['read-library'] == 7.0, 'minutes a week are asked for once a week'
    assert state['checkoff_size']['chores'] == 1.0
    assert state['checkoff_size']['read-library'] == 45.0


def test_logging_one_pursuit_moves_no_other_pursuits_numbers(sandbox):
    """The property a declared pace exists for: each goal answers for itself.

    A pace derived from the register as a whole moved every interval whenever
    anything was logged, so a long week of one pursuit made every other one read
    as further behind. Any shared input between two pursuits fails this.
    """
    before = built(sandbox)
    for day in range(10):
        log_done(sandbox, 'study-computer-science', day / 3)
    after = built(sandbox)

    others = [name for name in before['active'] if name != 'study-computer-science']
    assert after['balance']['study-computer-science'] < before['balance']['study-computer-science']
    for name in others:
        assert after['intervals'][name] == before['intervals'][name], name
        assert after['balance'][name] == before['balance'][name], name
        assert after['due'][name] == before['due'][name], name


def test_every_pursuit_never_done_is_pinned_heaviest_first(sandbox):
    state = built(sandbox)
    assert pursuits.pinned(state) == ['study-computer-science', 'read-library', 'chores', 'read-longform']


def test_a_pursuit_done_inside_its_cadence_is_not_pinned(sandbox):
    log_done(sandbox, 'chores', 2)
    state = built(sandbox)
    assert 'chores' not in pursuits.pinned(state)


def test_a_pursuit_past_its_cadence_is_pinned_again(sandbox):
    log_done(sandbox, 'chores', 30)
    state = built(sandbox)
    assert 'chores' in pursuits.pinned(state)


def test_a_pinned_pursuit_is_not_also_sampled(sandbox):
    # Pinned means guaranteed; drawing it again would waste a slot on it.
    state = built(sandbox)
    selection = pursuits.compute_draw(state, seed=1)
    assert 'chores' in selection['pinned']
    assert selection['offered'].count('chores') == 1


def test_the_draw_fills_up_to_the_screen_size_across_pins_and_samples(sandbox):
    state = built(sandbox)
    selection = pursuits.compute_draw(state, seed=1)
    assert len(selection['offered']) <= pursuits.DRAW_SIZE
    assert set(selection['pinned']) <= set(selection['offered']), 'a pin is offered, and pinned records only how'


def test_a_just_logged_pursuit_never_outranks_anything_owed(sandbox):
    """It can still fill a row — the screen is sized for five and a one-row draw
    is a queue. What it cannot do is outrank anything that is actually owed,
    which is what reads as the tool not having noticed the log."""
    log_done(sandbox, 'read-library', 0.0)
    state = built(sandbox)
    selection = pursuits.compute_draw(state, seed=1)

    assert 'read-library' not in selection['pinned']
    assert selection['offered'][-1] == 'read-library'


def test_the_names_cache_is_what_the_shell_completion_reads():
    pursuits.write_names_cache(load())
    lines = pursuits.NAMES_CACHE.read_text().splitlines()
    assert 'chores\tThe maintenance list' in lines
    # Paused pursuits are still completable — you log them by hand all the time.
    assert any(line.startswith('paused-thing\t') for line in lines)


def test_match_pursuit_takes_an_exact_name():
    assert pursuits.match_pursuit('chores', load()) == 'chores'


def test_match_pursuit_takes_an_unambiguous_prefix():
    assert pursuits.match_pursuit('stu', load()) == 'study-computer-science'


def test_match_pursuit_refuses_an_ambiguous_prefix():
    # read-library and read-longform both match; guessing either would log the wrong one.
    assert pursuits.match_pursuit('read', load()) is None


def test_match_pursuit_returns_none_for_a_miss():
    assert pursuits.match_pursuit('nope', load()) is None


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


def test_a_resolve_maps_json_fields():
    resolved = resolution('chores', {'label': 'name', 'id': 'id'}, [{'name': 'Trim Dingo Nails', 'id': 422}])
    assert resolved['choices'][0]['label'] == 'Trim Dingo Nails'
    assert resolved['choices'][0]['id'] == '422'
    assert resolved['choices'][0]['raw']['name'] == 'Trim Dingo Nails'


def test_a_resolve_digs_into_a_nested_list():
    config = {'items': 'in_progress_resources', 'label': 'name'}
    assert resolution('cs', config, {'in_progress_resources': [{'name': 'Chapter 6'}]})['choices'][0]['label'] == 'Chapter 6'


def test_a_resolve_accepts_a_single_object_where_a_list_would_do():
    # icb overview hands back the next project item as an object, having already
    # picked it. The register should not need a wrapper command to unwrap that.
    document = {'projects': {'items': {'next': {'title': 'Ship the CLI', 'id': 'abc'}}}}
    resolved = resolution('build', {'items': 'projects.items.next', 'label': 'title', 'id': 'id'}, document)
    assert resolved['choices'][0]['label'] == 'Ship the CLI'
    assert resolved['choices'][0]['id'] == 'abc'


def test_a_resolve_carries_where_the_item_lives_and_what_it_is_about():
    document = [
        {
            'title': 'Give cobracmd a usage-error exit code of 2',
            'repo': 'goselfupdate',
            'notes': 'Cobra returns flag-parse failures as ordinary errors. Fix belongs in cobracmd.',
            'projects': [{'name': 'CLI machine contract conformance'}],
        }
    ]
    config = {'label': 'title', 'context': ['repo', 'projects.0.name'], 'detail': 'notes'}

    resolved = resolution('build', config, document)

    assert resolved['choices'][0]['context'] == 'goselfupdate · CLI machine contract conformance'
    assert resolved['choices'][0]['detail'] == 'Cobra returns flag-parse failures as ordinary errors.', 'the gist, not the whole note'


def test_a_resolve_takes_a_single_context_path_as_well_as_several():
    config = {'label': 'name', 'context': 'category'}
    assert resolution('tasks', config, [{'name': 'Trim Dingo Nails', 'category': 'Dingo'}])['choices'][0]['context'] == 'Dingo'


def test_a_resolve_skips_a_context_field_the_row_does_not_carry():
    # An errand has no repo. The note reads as the projects alone, never as a
    # leading separator with nothing in front of it.
    document = [{'title': 'Glove 80', 'repo': None, 'projects': [{'name': 'Sell Unused Shite'}]}]
    config = {'label': 'title', 'context': ['repo', 'projects.0.name']}

    assert resolution('build', config, document)['choices'][0]['context'] == 'Sell Unused Shite'


def test_a_resolve_fills_the_offered_items_id_into_the_view_command():
    document = [{'title': 'Ship the CLI', 'id': '019fa297-0c01-7737-bb4f-8f05de2fe2cd'}]
    config = {'label': 'title', 'id': 'id', 'view': 'icb projects items show {id}'}

    assert resolution('build', config, document)['choices'][0]['view'] == 'icb projects items show 019fa297-0c01-7737-bb4f-8f05de2fe2cd'


def test_a_view_command_wanting_an_id_the_backend_withheld_is_not_printed():
    # A command shown with a hole in it reads as something you could run.
    config = {'label': 'title', 'view': 'icb projects items show {id}'}
    assert resolution('build', config, [{'title': 'Ship the CLI'}])['choices'][0]['view'] == ''


def test_a_view_command_needing_no_id_is_printed_as_written():
    assert pursuits.view_command('nomad trips list', None) == 'nomad trips list'


def test_view_without_resolve_is_refused(tmp_path):
    path = write_register(tmp_path, 'pursuits:\n  a:\n    weight: 5\n    cadence: 3d\n    view: icb tasks show {id}\n')
    with pytest.raises(pursuits.RegisterError, match='view'):
        load(path)


def test_context_and_detail_without_a_label_are_refused(tmp_path):
    # Both read fields off a parsed row, and there is no row without `label` —
    # the resolver falls back to reading plain lines.
    path = write_register(tmp_path, 'pursuits:\n  a:\n    weight: 5\n    cadence: 3d\n    resolve: echo hi\n    context: repo\n')
    with pytest.raises(pursuits.RegisterError, match='label'):
        load(path)


@pytest.mark.parametrize('document', [[], None])
def test_a_resolve_that_matched_nothing_says_so(document):
    assert resolution('p', {'label': 'name'}, document) == {'pursuit': 'p', 'candidates': 0, 'backend': 'app', 'choices': []}


def test_a_resolve_counts_the_rows_that_matched():
    """Three books equally in progress are candidates, not a decision.

    Every one is offered, because showing only the first picks for you. The count
    is what tells everything downstream the backend did not choose.
    """
    resolved = resolution('read', {'label': 'title'}, [{'title': 'Ego and Archetype'}, {'title': 'Lucid Dreaming'}])
    assert [choice['label'] for choice in resolved['choices']] == ['Ego and Archetype', 'Lucid Dreaming']
    assert resolved['candidates'] == 2


def test_a_resolve_offers_a_handful_however_many_matched():
    """Fifty-four open tasks are a list, not a choice. The count still says fifty-four."""
    resolved = resolution('tasks', {'label': 'name'}, [{'name': f'task {number}'} for number in range(54)])

    assert len(resolved['choices']) == pursuits.CANDIDATE_ROWS
    assert resolved['candidates'] == 54


def test_a_resolve_counts_after_the_register_narrows_the_rows():
    config = {'label': 'name', 'resolve_where': {'name': 'Journal'}}
    assert resolution('journal', config, [{'name': 'Journal'}, {'name': 'Pumice Stone'}])['candidates'] == 1


def test_on_log_substitutes_the_offered_items_id(tmp_path):
    marker = tmp_path / 'ran.txt'
    config = {'resolve': 'echo x', 'on_log': f'cp {tmp_path / "seed.txt"} {marker}'}
    (tmp_path / 'seed.txt').write_text('422')
    result = pursuits.run_on_log(config, {'id': '422', 'label': 'Task'}, '', None, assume_yes=True)
    assert result is not None and result['ran'] is True
    assert marker.read_text() == '422'


def test_on_log_is_not_run_when_there_is_nobody_to_confirm_with(tmp_path):
    """The prompt would block on a stdin that never closes, so an unconfirmed
    on_log is skipped rather than asked about. -y is what runs it unattended."""
    marker = tmp_path / 'ran.txt'
    config = {'resolve': 'echo x', 'on_log': f'touch {marker}'}

    assert pursuits.run_on_log(config, {'id': '1', 'label': 'Task'}, '', None, assume_yes=False) is None
    assert not marker.exists()


def test_on_log_is_not_run_under_no_input_even_on_a_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    monkeypatch.setattr(render, 'no_input', True)
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


def test_a_record_carries_the_state_that_produced_it_and_when_it_happened():
    state = built()
    earlier = (NOW - dt.timedelta(hours=3)).isoformat()

    record = pursuits.new_record('done', 'chores', state, {'note': 'trimmed'}, machine='testbox', identity='someone')
    backdated = pursuits.new_record('done', 'chores', state, {'occurred_at': earlier}, machine='testbox', identity='someone')

    assert (record['pursuit'], record['machine'], record['actor'], record['note']) == ('chores', 'testbox', 'someone', 'trimmed')
    assert record['occurred_at'] == record['logged_at'] == NOW.isoformat()
    assert (backdated['occurred_at'], backdated['logged_at']) == (earlier, NOW.isoformat())
    # The weights at the moment of the log, so drift stays honest after a re-weight.
    assert record['state_at_log']['weights']['chores'] == 25
    assert 'probability' in record['state_at_log']


def test_term_ended_only_after_the_date():
    assert pursuits.term_ended({'until': NOW.date() - dt.timedelta(days=1)}, NOW.date())
    assert not pursuits.term_ended({'until': NOW.date() + dt.timedelta(days=1)}, NOW.date())
    assert not pursuits.term_ended({}, NOW.date())


def test_a_drawn_choice_says_where_the_item_lives_beside_its_title():
    """On the same line, not in a column. A column is sized by the longest title
    on screen, which put a short title's place most of a pane away from it."""
    state = built()
    resolved = {'chores': {'choices': [{'label': 'Face Mud', 'context': 'Home'}]}}

    assert pursuits.offer('chores', state, resolved).choices == ['Face Mud · Home']


def test_a_pursuit_offers_every_choice_its_backend_returned():
    """Three books equally in progress have no next one. Showing only the first
    picks for you; showing all three is what makes it a choice."""
    state = built()
    books = [{'label': 'Difficult Conversations', 'context': 'Douglas Stone'}, {'label': 'Ego and Archetype'}]

    row = pursuits.offer('chores', state, {'chores': {'candidates': 2, 'choices': books}})

    assert row.choices == ['Difficult Conversations · Douglas Stone', 'Ego and Archetype']


def test_a_pursuit_with_no_resolve_offers_its_description():
    state = built()

    assert pursuits.offer('chores', state, {}).choices == [state['pursuits']['chores']['description']]


def test_a_resolve_that_matched_nothing_is_flagged_beside_the_description():
    """The description alone reads as the item, so a pursuit pointed at a project
    kind that no longer held anything looked like a real thing to go and do."""
    state = built()
    resolved = {'chores': pursuits.nothing_matched('chores', 'tracker')}

    row = pursuits.offer('chores', state, resolved)

    assert row.failed
    assert row.choices == [f'tracker: matched nothing · {state["pursuits"]["chores"]["description"]}']


def a_row(name: str, due: str, *choices: str) -> pursuits.Offer:
    return pursuits.Offer(name, due, '', list(choices), False)


@pytest.fixture
def unclipped(monkeypatch):
    """A console too wide to clip anything, so a row that was assembled too wide
    is visible rather than cut to the terminal and indistinguishable from one
    that fits."""
    monkeypatch.setattr(render.console, '_width', 10_000)


@pytest.mark.parametrize('width', [60, 80, 140])
def test_a_row_never_assembles_wider_than_the_line_it_was_given(width, unclipped, capsys):
    """A choice takes what the standing columns leave, never more."""
    long = 'Difficult Conversations · Douglas Stone' * 3
    row = pursuits.Offer('read-library', '3w overdue', '', [long], False, weight='45', goal='45m a week', tally='1h 30m of 3h')
    pursuits.render_offers([row], [], width)

    assert max(len(line) for line in capsys.readouterr().out.splitlines()) <= width


def test_a_narrow_pane_gives_up_the_weight_before_the_date(unclipped, capsys):
    """The name and how late it is are what the screen is read for, so the
    columns explaining the date go first."""
    row = pursuits.Offer(
        'read-library', '3w overdue', '', ['Difficult Conversations'], False, weight='45', goal='45m a week', tally='1h 30m of 3h'
    )
    pursuits.render_offers([row], [], 60)

    heading = capsys.readouterr().out.splitlines()[0].split()
    assert 'pursuit' in heading and 'due' in heading
    assert 'wt' not in heading


def test_a_wide_pane_heads_every_column(unclipped, capsys):
    row = pursuits.Offer(
        'read-library', '3w overdue', '', ['Difficult Conversations'], False, weight='45', goal='45m a week', tally='1h 30m of 3h'
    )
    pursuits.render_offers([row], [], 140)

    assert capsys.readouterr().out.splitlines()[0].split() == [title for title, _ in pursuits.OFFER_COLUMNS] + ['item']


def test_the_name_and_date_are_said_once_per_pursuit(unclipped, capsys):
    """Repeating them down a block reads as three pursuits rather than one with
    three things you could do."""
    pursuits.render_offers([a_row('tasks', 'due in 2d', 'Face Mud · Home', 'Return hoodies · Purchase')], [], 140)

    _, first, second = capsys.readouterr().out.splitlines()
    assert 'tasks' in first and 'due in 2d' in first
    assert 'tasks' not in second and 'due in 2d' not in second
    assert first.index('Face Mud') == second.index('Return hoodies'), 'every choice starts in one column'


def test_blocks_are_separated_once_any_pursuit_offers_several(unclipped, capsys):
    """Flush blocks run the last choice of one into the name of the next."""
    pursuits.render_offers([a_row('study', 'due in 1d', 'A', 'B'), a_row('chore', 'due in 2d', 'C')], [], 140)

    assert capsys.readouterr().out.splitlines()[3] == ''


def test_single_choice_pursuits_stay_one_line_apiece(unclipped, capsys):
    """Spacing a screen of one-liners doubles its height for no grouping to show."""
    pursuits.render_offers([a_row('socialize', '3d overdue', 'See or call someone'), a_row('chore', 'due in 2d', 'C')], [], 140)

    assert '' not in capsys.readouterr().out.splitlines()


def test_what_the_draw_left_out_follows_what_it_offered(unclipped, capsys):
    """Below a gap, so the rows with something to do stay one block."""
    pursuits.render_offers([a_row('socialize', '3d overdue', 'See or call someone')], [a_row('paint', 'due in 5d')], 140)

    _, offered, gap, rest = capsys.readouterr().out.splitlines()
    assert offered.split()[0] == 'socialize'
    assert gap == ''
    assert rest.split() == ['paint', 'due', 'in', '5d']


def test_a_pursuit_the_draw_left_out_has_nothing_to_do():
    state = built()

    row = pursuits.unoffered('chores', state)

    assert row.choices == []
    assert row.goal == 'every 7d'


def test_a_skipped_pursuit_says_so_in_place_of_a_date():
    """Its clock is stopped, so any date would be one the skip is holding."""
    state = balance_state([zeroed('chore', 10.0), skipped('chore', 0.0, 14.0)])

    assert pursuits.unoffered('chore', state).due == 'skipped'


def test_the_tally_sets_what_was_done_beside_what_was_asked():
    records = [zeroed('chore', 40.0), zeroed('read', 40.0), *(done('chore', 1.0) for _ in range(3)), done('read', 1.0, minutes=90)]
    state = balance_state(records)

    assert pursuits.tally_text(state, 'chore') == '3 of 40 in 40d'
    assert pursuits.tally_text(state, 'read') == '1h 30m of 30h in 40d'


@pytest.mark.parametrize('source', ['evidence_files: ~/notes/journal', 'evidence: echo []\n    evidence_time: completed_at'])
def test_a_pursuit_measured_in_minutes_refuses_an_app_source(source):
    """An app says which day, never how long. Crediting a day some guessed
    number of minutes paid a week's journal from one file saved twice."""
    body = f'pursuits:\n  journal:\n    description: x\n    weight: 10\n    weekly_minutes: 60\n    {source}\n'

    with pytest.raises(pursuits.RegisterError, match='journal: weekly_minutes is paid by `doit pursuits log --minutes` only'):
        parsed(body)


def test_a_resume_ends_the_skip_it_cuts_short():
    """A mistyped year-long skip, resumed a day later, takes one day off the
    clock rather than the whole year."""
    records = [zeroed('chore', 30.0), skipped('chore', 20.0, 365.0), skipped('chore', 19.0, 0.0)]

    state = balance_state(records)

    assert state['balance']['chore'] == pytest.approx(29.0)
    assert 'chore' not in state['suppressed']


def test_a_running_skip_holds_the_due_date_until_it_ends():
    """Projected as if it ended now, a skip still running read as due in days
    when nothing would be asked for until it was over."""
    records = [zeroed(name, 1.5) for name in HISTORY_NAMES] + [skipped(name, 1.0, 11.0) for name in HISTORY_NAMES]
    today = history_state(records, 0.0)

    for name in HISTORY_NAMES:
        due = known_due(today, name)
        assert due > 10.0, name
        assert not pursuits.owes(history_state(records, due - 0.01), name), name
        assert pursuits.owes(history_state(records, due + 0.01), name), name


def test_handed_in_evidence_days_pay_as_live_ones_do():
    """The forecast hands its days in, and dropping them read every pursuit paid
    through its app as owing on the first simulated day."""
    days = [day(ago) for ago in range(1, 4)]

    state = pursuits.build_state(parsed(BALANCE_REGISTER), NOW, records=[zeroed('chore', 3.0)], observed={}, evidence_days={'chore': days})

    assert state['done']['chore'] == 3.0


def test_a_skipped_pursuit_is_not_called_behind():
    state = balance_state([zeroed('chore', 3.0), zeroed('read', 0.0), skipped('chore', 0.0, 14.0)])

    assert pursuits.standing_line(state) == ''


def test_a_reset_someone_ran_outranks_any_first_sighting():
    ran = zeroed('chore', 20.0)
    sightings = [{**zeroed('chore', days), 'note': pursuits.FIRST_SEEN} for days in (10.0, 5.0)]

    assert pursuits.zero_resets([ran, *sightings])['chore'] == NOW - dt.timedelta(days=20.0)


def test_the_earliest_first_sighting_stands_when_machines_both_write_one():
    """A machine behind on sync writes its own on first read, later than the one
    already merged. Taking the newer drops every payment between them."""
    sightings = [{**zeroed('chore', days), 'note': pursuits.FIRST_SEEN} for days in (10.0, 5.0)]

    assert pursuits.zero_resets(sightings)['chore'] == NOW - dt.timedelta(days=10.0)


def test_a_fractional_weight_renders_as_written():
    state = balance_state([zeroed('chore', 3.0)], register=BALANCE_REGISTER.replace('weight: 25', 'weight: 2.5', 1))

    assert pursuits.offer('chore', state, {}).weight == '2.5'


def test_a_counted_tally_asks_only_for_checkoffs_that_came_due():
    """Seventeen days of a weekly pursuit is two checkoffs due, the first ten
    days ago, which is the date the row states beside it."""
    weekly = BALANCE_REGISTER.replace('cadence: 1d', 'cadence: 7d')
    state = balance_state([zeroed('chore', 17.0), zeroed('read', 40.0)], register=weekly)

    assert pursuits.tally_text(state, 'chore') == '0 of 2 in 17d'
    assert pursuits.format_due(pursuits.due_in_days(state, 'chore')) == '10d overdue'


def test_a_failed_row_carries_what_the_backend_said_and_nothing_else():
    """A stale register entry and a logged-out CLI fail the same way.

    Naming the backend and calling it unavailable reads as an outage, which sends
    you to check a service that is answering fine. The message the backend printed
    is the only part that says which of the two happened.
    """
    state = built()
    resolved = {'chores': {'error': 'error: unknown flag: --limit', 'backend': 'icb', 'choices': [{'label': 'stale'}]}}

    row = pursuits.offer('chores', state, resolved)

    assert row.failed
    assert row.choices == ['icb: error: unknown flag: --limit']


def test_format_elapsed_switches_unit_rather_than_format():
    assert pursuits.format_elapsed(None) == 'never'
    assert pursuits.format_elapsed(0.2) == 'today'
    assert pursuits.format_elapsed(3) == '3d ago'
    assert pursuits.format_elapsed(30) == '4w ago'
    assert pursuits.format_elapsed(200) == '7mo ago'


BALANCE_REGISTER = """
pursuits:
  chore:
    description: Complete chore that is not on the task list
    weight: 25
    cadence: 1d

  read:
    description: Forty-five minutes a day, measured in minutes rather than occurrences
    weight: 25
    weekly_minutes: 315
"""


def done(name: str, days_ago: float, minutes: int | None = None) -> dict:
    record: dict = {'pursuit': name, 'event': 'done', 'occurred_at': (NOW - dt.timedelta(days=days_ago)).isoformat()}
    if minutes is not None:
        record['duration_minutes'] = minutes
    return record


def zeroed(name: str, days_ago: float) -> dict:
    return {'pursuit': name, 'event': 'reset', 'occurred_at': (NOW - dt.timedelta(days=days_ago)).isoformat()}


def skipped(name: str, days_ago: float, span_days: float) -> dict:
    start = NOW - dt.timedelta(days=days_ago)
    return {
        'pursuit': name,
        'event': 'skip',
        'occurred_at': start.isoformat(),
        'expires_at': (start + dt.timedelta(days=span_days)).isoformat(),
    }


def paced(name: str, days_ago: float, interval: float | None) -> dict:
    """A counted pace taking effect ``days_ago``, or one asking nothing where ``interval`` is None."""
    record: dict = {'pursuit': name, 'event': 'pace', 'occurred_at': (NOW - dt.timedelta(days=days_ago)).isoformat()}
    record.update(
        {'interval_days': interval, 'checkoff_size': None if interval is None else 1.0, 'timed': None if interval is None else False}
    )
    return record


def balance_state(records: list[dict], register: str = BALANCE_REGISTER, now: dt.datetime = NOW) -> dict:
    """The state over ``records`` under ``register``, its cycles included, writing what a build writes into ``records``."""
    document = pursuits.parse_register(register, 'pursuits.yml')
    declared = pursuits.pursuits_from_document(document, 'pursuits.yml')
    return built(records, declared, now, observed={}, cycles=pursuits.cycles_from_document(document, declared, 'pursuits.yml'))


def test_a_counted_pursuit_owes_one_checkoff_per_interval():
    state = balance_state([zeroed('chore', 4.0)])
    assert state['balance']['chore'] == 4.0, 'a daily chore, four days on, with nothing done'
    assert 'chore' in pursuits.pinned(state)


def test_a_timed_pursuit_owes_its_weekly_minutes_spread_across_the_week():
    state = balance_state([zeroed('read', 2.0)])
    assert state['balance']['read'] == pytest.approx(90.0), 'two days of 315 minutes a week'


def test_a_burst_pays_several_intervals_forward():
    """The whole point: three chores in one evening is three days of cover, and
    nothing caps how far forward that reaches."""
    state = balance_state([zeroed('chore', 0.0)] + [done('chore', 0.0) for _ in range(3)])

    assert state['balance']['chore'] == -3.0
    assert 'chore' not in pursuits.pinned(state)


def test_partial_minutes_roll_over_rather_than_stranding():
    """A 20-minute read pays 20 minutes off a 45-minute checkoff. No remainder is
    held anywhere, so the next 25 minutes finish it whenever they happen."""
    part = balance_state([zeroed('read', 1.0), done('read', 0.5, minutes=20)])
    rest = balance_state([zeroed('read', 1.0), done('read', 0.5, minutes=20), done('read', 0.1, minutes=25)])

    assert part['balance']['read'] == 25.0
    assert rest['balance']['read'] == 0.0


def test_a_long_sitting_counts_for_every_minute_of_it():
    """The failure the unit change exists to end: a 15-minute read and a 3-hour
    read used to satisfy the pursuit identically."""
    brief = balance_state([zeroed('read', 1.0), done('read', 0.5, minutes=15)])
    long = balance_state([zeroed('read', 1.0), done('read', 0.5, minutes=180)])

    assert brief['balance']['read'] == 30.0
    assert long['balance']['read'] == -135.0


def test_days_since_stays_the_honest_elapsed_time():
    """The balance changes what gets weighed, never what gets shown as last-done."""
    state = balance_state([zeroed('chore', 0.0)] + [done('chore', 0.0) for _ in range(3)])

    assert state['days_since']['chore'] == 0.0, 'three chores today were all done today'
    assert state['balance']['chore'] == -3.0, 'and the draw sees three days of cover'


def test_the_zero_point_is_the_reset_and_a_payment_cannot_move_it():
    """Shipping writes one for every pursuit, so history accrued against targets
    that have since moved cannot open a pursuit at a debt nobody agreed to."""
    after = balance_state([done('chore', 100.0), zeroed('chore', 2.0)])

    assert after['balance']['chore'] == 2.0, 'billed from the reset, and the 100-day-old entry pays nothing'


def test_recording_something_never_increases_what_is_owed():
    """Deriving the origin from the oldest record let a backdated log drag it
    behind itself: the schedule billed for the span the entry opened up and the
    entry paid one checkoff against it, so logging doubled the debt."""
    before = balance_state([done('chore', 200.0)])
    after = balance_state([done('chore', 200.0), done('chore', 400.0)])

    assert after['balance']['chore'] <= before['balance']['chore']


def test_a_pursuit_with_no_reset_is_billed_for_one_interval():
    """The origin cannot come from the records, so it is a single interval back —
    a pursuit doit has never been told the start of owes exactly one checkoff."""
    fresh = balance_state([])
    old = balance_state([done('chore', 100.0)])

    assert fresh['balance']['chore'] == 1.0
    assert old['balance']['chore'] == 1.0, 'a century of history does not open a century of debt'


def test_a_pursuit_the_journal_never_zeroed_goes_overdue_as_time_passes():
    """A start derived from now moves with the clock, so the pursuit owed exactly
    one checkoff on every run and read `due today` forever."""
    records = balance_state([])['records']

    later = pursuits.build_state(parsed(BALANCE_REGISTER), NOW + dt.timedelta(days=3), records=records, observed={})

    assert later['balance']['chore'] == pytest.approx(4.0), 'one owed when first seen, and a day more for each of three'
    assert pursuits.due_in_days(later, 'chore') == pytest.approx(-3.0)


def test_a_first_sighting_is_written_once_and_never_over_a_reset():
    records = [zeroed('chore', 2.0)]
    balance_state(records)
    balance_state(records, now=NOW + dt.timedelta(days=1))

    sightings = [record['pursuit'] for record in records if record.get('note') == 'first seen']
    assert sightings == ['read']


def test_a_debt_stands_however_long_ago_the_pursuit_was_zeroed():
    """Nothing ages out, so two hundred days of a daily chore left undone is two
    hundred owed, and forty done a month ago still pay forty of them."""
    state = balance_state([zeroed('chore', 200.0)] + [done('chore', 30.0) for _ in range(40)])

    assert state['origins']['chore'] == NOW - dt.timedelta(days=200.0)
    assert state['balance']['chore'] == pytest.approx(160.0)


def test_an_edited_or_paused_pace_prices_only_the_days_it_was_in_force():
    """Ten days at 3d, ten paused, then ten daily owe 10/3 + 10. Read off today's
    register alone, all thirty would be billed daily."""
    records = [zeroed('chore', 30.0), paced('chore', 30.0, 3.0), paced('chore', 20.0, None), paced('chore', 10.0, 1.0)]

    state = balance_state(records)

    assert state['balance']['chore'] == pytest.approx(10.0 / 3.0 + 10.0)


def test_a_build_journals_a_pace_when_the_register_changes_and_only_then():
    """An edit, a pause and a move from occurrences to minutes each write once,
    and the move also starts the balance again, since no unit carries across."""
    records = [zeroed('chore', 5.0), zeroed('read', 5.0)]
    balance_state(records)
    balance_state(records)
    edited = BALANCE_REGISTER.replace('cadence: 1d', 'cadence: 2d').replace(
        'weight: 25\n    weekly_minutes', 'weight: 25\n    paused: true\n    weekly_minutes'
    )
    balance_state(records, edited)
    balance_state(records, BALANCE_REGISTER.replace('cadence: 1d', 'weekly_minutes: 60'))

    written = [
        (record['pursuit'], record['event'], record.get('interval_days'), record.get('checkoff_size'))
        for record in records
        if record['event'] == 'pace' or record.get('note') == pursuits.UNIT_CHANGED
    ]
    assert written == [
        ('chore', 'pace', 1.0, 1.0),
        ('read', 'pace', 7.0, 315.0),
        ('chore', 'pace', 2.0, 1.0),
        ('read', 'pace', None, None),
        ('chore', 'reset', None, None),
        ('chore', 'pace', 7.0, 60.0),
        ('read', 'pace', 7.0, 315.0),
    ]


CYCLE_REGISTER = """
pursuits:
  chore:
    weight: 25
    cadence: 4d
  read:
    weight: 25
    weekly_minutes: 315
cycles:
  - name: hold
    until: 2026-08-07
    multiply: {read: 2}
    cadence: {chore: 2d}
"""


def test_a_cycle_steers_weights_and_pace_through_its_until_date_and_then_lapses():
    """Four days at the held 2d and four at the declared 4d owe 2 + 1, so the
    days a cycle ran are priced at its cadence and none after it."""
    records = [zeroed('chore', 0.0)]
    running = balance_state(records, CYCLE_REGISTER)
    lapsed = balance_state(records, CYCLE_REGISTER, NOW + dt.timedelta(days=4))
    later = balance_state(records, CYCLE_REGISTER, NOW + dt.timedelta(days=8))

    assert running['weights'] == {'chore': 25.0, 'read': 50.0}
    assert running['intervals']['chore'] == 2.0
    assert pursuits.explain_payload(running)['cycles'] == ['hold']
    assert lapsed['weights'] == {'chore': 25.0, 'read': 25.0}
    assert lapsed['intervals']['chore'] == 4.0
    assert pursuits.explain_payload(lapsed)['cycles'] == []
    assert later['balance']['chore'] == pytest.approx(3.0)


@pytest.mark.parametrize(
    'cycle',
    [
        '{name: c, until: 2026-08-07, multiply: {missing: 2}}',
        '{name: c, until: 2026-08-07, multiply: {read: 0}}',
        '{name: c, until: 2026-08-07, cadence: {read: 2d}}',
        '{name: c, until: 2026-08-07, cadence: {chore: 1w}}',
        '{name: c, multiply: {read: 2}}',
        '{name: c, until: 2026-08-07, multiplier: {read: 2}}',
    ],
)
def test_a_cycle_the_draw_cannot_trust_is_refused(cycle):
    """A cycle naming no pursuit, a weight it would zero, a timed pursuit moved
    onto a cadence, a pace that is not whole days, a cycle with no end, and a
    key the loader does not read would each lean the draw by something nobody wrote."""
    document = pursuits.parse_register(CYCLE_REGISTER.split('cycles:')[0] + f'cycles:\n  - {cycle}\n', 'pursuits.yml')
    register = pursuits.pursuits_from_document(document, 'pursuits.yml')

    with pytest.raises(pursuits.RegisterError):
        pursuits.cycles_from_document(document, register, 'pursuits.yml')


HISTORY_REGISTER = """
pursuits:
  daily:
    description: Done most days
    weight: 25
    cadence: 1d
  weekly:
    description: Done about once a week
    weight: 25
    cadence: 7d
  timed:
    description: Five hours a week
    weight: 25
    weekly_minutes: 300
"""

HISTORY_NAMES = ('daily', 'weekly', 'timed')

# Each history carries a reset for every pursuit, as the journal does once
# `record_first_sightings` has run.
HISTORIES = {
    'ahead': [zeroed(name, 40.0) for name in HISTORY_NAMES]
    + [done('daily', 1.0) for _ in range(30)]
    + [done('weekly', 2.0) for _ in range(6)]
    + [done('timed', day, 500) for day in (1.0, 2.0, 3.0)],
    'behind': [zeroed(name, 40.0) for name in HISTORY_NAMES],
    # A fortnight's skip that has ended, whose days are never asked for.
    'skipped': [zeroed(name, 40.0) for name in HISTORY_NAMES]
    + [skipped(name, 17.0, 14.0) for name in HISTORY_NAMES]
    + [done(name, day) for name, day in itertools.product(('daily', 'weekly'), (2.0, 1.0))]
    + [done('timed', 1.0, 200)],
}


def history_state(records: list[dict], days_on: float, register: str = HISTORY_REGISTER) -> dict:
    """State ``days_on`` after NOW with nothing further logged, writing nothing into ``records``."""
    return pursuits.build_state(parsed(register), NOW + dt.timedelta(days=days_on), records=records, observed={})


@pytest.mark.parametrize('history', HISTORIES)
def test_a_due_date_only_comes_closer_while_nothing_is_logged(history):
    """Time passing can only bring a pursuit due. A due date that recedes on a
    quiet day is a schedule forgiving what nobody did."""
    dues = [history_state(HISTORIES[history], day) for day in range(40)]

    for name in HISTORY_NAMES:
        series = [known_due(state, name) for state in dues]
        assert all(later <= earlier + 1e-9 for earlier, later in itertools.pairwise(series)), name


@pytest.mark.parametrize('history', HISTORIES)
def test_a_projected_due_date_is_the_moment_the_pursuit_comes_to_owe(history):
    """The date on screen is a promise about a later run: nothing owed before it,
    a whole checkoff owed just after it."""
    today = history_state(HISTORIES[history], 0.0)

    for name in HISTORY_NAMES:
        due = pursuits.due_in_days(today, name)
        if due is None or due <= 0.01:
            continue
        assert not pursuits.owes(history_state(HISTORIES[history], due - 0.01), name), name
        assert pursuits.owes(history_state(HISTORIES[history], due + 0.01), name), name


@pytest.mark.parametrize('name', HISTORY_NAMES)
@pytest.mark.parametrize('weight', [1, 500])
def test_no_weight_moves_any_pursuits_standing(name, weight):
    """A weight orders what is owed and nothing else. Whether a pursuit is
    getting enough has to read the same whatever any pursuit weighs."""
    reweighed = re.sub(rf'(  {name}:\n    description: [^\n]*\n    weight: )25', rf'\g<1>{weight}', HISTORY_REGISTER)
    standing = ('intervals', 'checkoff_size', 'asked', 'done', 'balance', 'due')

    before = history_state(HISTORIES['ahead'], 0.0)
    after = history_state(HISTORIES['ahead'], 0.0, register=reweighed)

    assert after['weights'][name] == weight
    assert {key: after[key] for key in standing} == {key: before[key] for key in standing}


def test_a_pursuit_with_no_record_anywhere_opens_one_checkoff_behind():
    """Declared because it is wanted. Reading a fresh entry as current would keep
    it out of the draw until someone zeroed it by hand."""
    state = balance_state([])

    assert state['balance'] == {'chore': 1.0, 'read': 315.0}, 'one occurrence, and one week of minutes'
    assert sorted(pursuits.pinned(state)) == ['chore', 'read']


def test_a_skip_stops_the_clock_rather_than_deferring_the_debt():
    running = balance_state([zeroed('chore', 10.0)])
    passed = balance_state([zeroed('chore', 10.0), skipped('chore', 8.0, 4.0)])

    assert running['balance']['chore'] == 10.0
    assert passed['balance']['chore'] == 6.0, 'the four skipped days were never asked for'


def test_overlapping_skips_take_their_span_out_once():
    """Renewing a skip before the last expires is the ordinary case, and adding
    the two lengths would take the same days off the clock twice."""
    once = balance_state([zeroed('chore', 10.0), skipped('chore', 8.0, 4.0)])
    twice = balance_state([zeroed('chore', 10.0), skipped('chore', 8.0, 4.0), skipped('chore', 7.0, 3.0)])

    assert once['balance']['chore'] == twice['balance']['chore'] == 6.0


def test_a_standing_skip_reaches_the_pins_as_well_as_the_draw():
    """A pass now names the span it covers, so honoring it everywhere is what
    makes it a decision rather than a reroll."""
    state = balance_state([zeroed('chore', 10.0), skipped('chore', 0.0, 14.0)])

    assert state['suppressed'] == ['chore']
    assert 'chore' not in state['pool']
    assert 'chore' not in pursuits.pinned(state)


def test_a_skip_that_has_run_out_suppresses_nothing():
    state = balance_state([zeroed('chore', 10.0), skipped('chore', 8.0, 4.0)])

    assert state['suppressed'] == []
    assert 'chore' in pursuits.pinned(state)


def test_the_standing_line_names_what_is_behind_and_no_more():
    """Minutes and checkoffs do not add, so a list of amounts has no total behind it.

    The question the line answers is which strands are slipping, and the names
    alone answer it. How far behind each one is has a column on the row.
    """
    state = balance_state([zeroed('chore', 3.0), zeroed('read', 8.0)])

    assert pursuits.standing_line(state) == 'behind · chore · read'


def test_the_standing_line_names_the_longest_overdue_first():
    """By days late, the date every other screen states. Three checkoffs of a
    daily chore is two days late, and under three of a weekly one is nearly two
    weeks late."""
    register = BALANCE_REGISTER + '\n  slow:\n    description: Once a week\n    weight: 10\n    cadence: 7d\n'
    state = balance_state([zeroed('chore', 3.0), zeroed('read', 0.0), zeroed('slow', 20.0)], register=register)

    assert pursuits.standing_line(state) == 'behind · slow · chore'


def test_the_standing_line_is_silent_when_nothing_is_owed():
    ahead = [zeroed('chore', 0.0), done('chore', 0.0), zeroed('read', 0.0), done('read', 0.0, minutes=60)]
    assert pursuits.standing_line(balance_state(ahead)) == ''


def test_the_draw_still_offers_something_when_nothing_is_owed():
    """Seeing what else is on offer while nothing is urgent is the point of the
    fallback. An empty screen reads as the tool having broken, not as current."""
    ahead = [zeroed('chore', 0.0), done('chore', 0.0), zeroed('read', 0.0), done('read', 0.0, minutes=400)]
    state = balance_state(ahead)

    assert pursuits.pinned(state) == []
    assert sorted(pursuits.compute_draw(state, seed=1)['offered']) == ['chore', 'read']


def test_the_standing_line_points_at_the_rest_rather_than_counting_it():
    wide = 'pursuits:\n' + ''.join(f'  p{index}:\n    weight: 10\n    cadence: 1d\n' for index in range(6))
    state = balance_state([zeroed(f'p{index}', 3.0) for index in range(6)], register=wide)

    line = pursuits.standing_line(state)

    assert len([part for part in line.split('·') if part.strip().startswith('p')]) == pursuits.STANDING_NAMES
    assert line.endswith('doit pursuits list'), 'a typeable command, never a remainder count'


def test_the_offered_list_leads_with_whatever_is_furthest_past_due():
    """The screen's headline behavior, and nothing pinned it: replacing the sort
    with `pass` and negating the sort key both left the suite green."""
    state = balance_state([zeroed('chore', 2.0), zeroed('read', 9.0)])

    assert pursuits.offered_order(state, ['chore', 'read']) == ['read', 'chore']
    assert pursuits.offered_order(state, ['read', 'chore']) == ['read', 'chore'], 'the input order is not the answer'


def test_an_owed_row_is_ordered_by_weight_before_lateness():
    """Ordering what is owed is the one thing a weight says: which of two debts
    you would rather clear first. Lateness only breaks a tie between equals."""
    register = BALANCE_REGISTER + '\n  light:\n    weight: 5\n    cadence: 1d\n'
    state = balance_state([zeroed('chore', 1.2), zeroed('light', 40.0)], register=register)

    assert known_due(state, 'light') < known_due(state, 'chore')
    assert pursuits.offered_order(state, ['light', 'chore']) == ['chore', 'light']
    assert pursuits.pinned(state)[-1] == 'light', 'the furthest behind, and the lightest of everything owed'


def test_a_weight_never_moves_a_row_that_is_not_owed_above_one_that_is():
    register = BALANCE_REGISTER + '\n  heavy:\n    weight: 90\n    cadence: 1d\n'
    records = [zeroed('chore', 1.2), zeroed('heavy', 0.0), done('heavy', 0.0)]
    state = balance_state(records, register=register)

    assert pursuits.offered_order(state, ['heavy', 'chore']) == ['chore', 'heavy']


def test_an_unpriced_row_sorts_last_rather_than_first():
    """A pursuit the register dropped has no due date, and a missing number must
    not read as zero days and take the top of the screen."""
    state = balance_state([zeroed('chore', 4.0)])

    assert pursuits.offered_order(state, ['edited-away', 'chore']) == ['chore', 'edited-away']


def test_the_due_column_is_colored_only_once_something_is_late():
    assert pursuits.due_style(-2.0) == 'yellow'
    assert pursuits.due_style(-0.5) == '', 'due today is not late'
    assert pursuits.due_style(6.0) == ''
    assert pursuits.due_style(None) == ''


def test_the_due_text_says_which_side_of_the_schedule_it_is_on():
    """The sign carried the whole reading and is the first character a reader skips."""
    assert pursuits.format_due(None) == '—'
    assert pursuits.format_due(-21.0) == '3w overdue'
    assert pursuits.format_due(-2.0) == '2d overdue'
    assert pursuits.format_due(-0.4) == 'due today'
    assert pursuits.format_due(0.6) == 'due today'
    assert pursuits.format_due(2.4) == 'due in 2d'
    assert pursuits.format_due(120.0) == 'due in 4mo'


@pytest.mark.parametrize(
    ('config', 'expected'),
    [
        ({'cadence': '9d'}, 'every 9d'),
        ({'cadence': '14d'}, 'every 14d'),
        ({'weekly_minutes': 45}, '45m a week'),
        ({'weekly_minutes': 90}, '1h 30m a week'),
        ({'weekly_minutes': 1200}, '20h a week'),
        ({}, 'tracked'),
    ],
)
def test_the_goal_is_stated_as_the_register_writes_it(config, expected):
    # Days stay days. `14d` printed as `2w` is a rounding of what was written,
    # and the list is the one screen whose job is to show the register.
    assert pursuits.goal_text(config) == expected


def test_a_paused_pursuit_still_reports_the_goal_it_declares():
    """`build_state` fills `intervals` from the active set, so a paused pursuit is
    absent from it — and the register still declares its pace."""
    assert pursuits.goal_text({'cadence': '9d', 'paused': True}) == 'every 9d'


def answers(monkeypatch, *replies: str) -> list[str]:
    """Feed the prompts a script, recording what each one asked."""
    asked: list[str] = []
    queued = list(replies)

    def fake_input(prompt: str = '') -> str:
        asked.append(prompt)
        return queued.pop(0)

    monkeypatch.setattr(pursuits.console, 'input', fake_input)
    monkeypatch.setattr(render, 'no_input', False)
    monkeypatch.setattr(pursuits, 'can_prompt', lambda: True)
    return asked


@pytest.mark.parametrize(
    ('config', 'minutes', 'refused'),
    [
        ({'weight': 5, 'cadence': '3d'}, 20, 'counted in completions'),
        ({'weight': 5, 'weekly_minutes': 60}, None, 'measured in minutes'),
        ({'weight': 5, 'weekly_minutes': 60}, 20, None),
        ({'weight': 5, 'cadence': '3d'}, None, None),
        ({'description': 'tracked'}, 90, None),
        ({'description': 'tracked'}, None, None),
    ],
)
def test_minutes_are_required_of_a_timed_log_refused_on_a_counted_one_and_optional_on_a_tracked_one(config, minutes, refused):
    """The register's declaration is what makes a pursuit measured in time. A
    tracked pursuit is measured against nothing, so it keeps what it is given."""
    refusal = pursuits.minutes_refusal('p', config, minutes)

    assert refusal is None if refused is None else refused in (refusal or '')


def test_the_minutes_prompt_refuses_what_is_not_a_positive_whole_number(monkeypatch):
    answers(monkeypatch, 'ages', '-3', '0', '', '30')
    assert pursuits.prompt_for_minutes() == 30


def test_the_pursuit_prompt_marks_what_the_draw_offered(monkeypatch, capsys):
    answers(monkeypatch, 'chores')
    assert pursuits.prompt_for_pursuit(load(), ['chores']) == 'chores'
    printed = capsys.readouterr().out
    assert '› chores' in printed


def test_a_bare_skip_covers_one_interval_and_a_day_where_there_is_none():
    """A bare skip still means "not this time" rather than committing to a length
    nobody chose, so it takes the length the pursuit's own schedule states."""
    assert pursuits.skip_span(None, 7.0) == 7
    assert pursuits.skip_span(None, math.inf) == 1
    assert pursuits.skip_span(None, None) == 1
    assert pursuits.skip_span('2w', 3.0) == 14


@pytest.mark.parametrize('duration', ['1mo', '1y', '3', '12h', '0d'])
def test_skip_for_takes_whole_days_or_weeks_only(duration):
    with pytest.raises(typer.BadParameter):
        pursuits.skip_span(duration, 3.0)


def test_did_counts_what_an_app_saw_and_the_journal_never_did():
    """The inversion this column exists to end: a pursuit with a backend is done
    inside that backend, so counting journal entries reported the busiest strand
    as the idle one."""
    row = drift([zeroed('study-computer-science', 90.0)], {'study-computer-science': [day(n) for n in range(1, 9)]})[
        'study-computer-science'
    ]

    assert row['done'] == 8.0
    assert row['logs'] == 0, 'nothing was ever typed for it'
    assert row['asked'] == 45.0, 'ninety days at one every two days'


def test_a_day_carried_by_both_records_counts_once():
    """Union, not sum. Logging what an app already reported would double it."""
    row = drift([done('chores', 1.0)], {'chores': [day(1)]})['chores']

    assert (row['done'], row['logs']) == (1.0, 1)


def test_drift_asks_nothing_from_before_a_reset():
    """A pursuit begun inside the window would otherwise read as having missed
    the weeks before it existed."""
    row = drift([zeroed('chores', 14.0), done('chores', 30.0)])['chores']

    assert row['since'][:10] == day(14).isoformat()
    assert row['asked'] == pytest.approx(2.0, abs=0.2), 'two weeks at one a week'
    assert row['done'] == 0.0, 'the entry before the reset is history, not progress'


def test_a_retired_pursuit_gets_no_row():
    """drift iterates the register, so a stranded name never gets a row."""
    assert 'gone-from-the-register' not in drift([done('chores', 1.0), done('gone-from-the-register', 2.0)])


def test_each_pursuit_is_reported_in_its_own_unit():
    """Minutes and completions do not add, so each row is read against its own
    goal and never against another row."""
    rows = drift([done('chores', 1.0), done('read-library', 1.0, minutes=90)])

    assert (rows['chores']['unit'], rows['chores']['done']) == ('checkoffs', 1.0)
    assert (rows['read-library']['unit'], rows['read-library']['done']) == ('minutes', 90.0)


def test_an_app_date_outside_the_window_is_not_counted():
    days = {'chores': [day(3), day(40)]}

    assert drift([zeroed('chores', 90.0)], days, since='7d')['chores']['done'] == 1.0
    assert drift([zeroed('chores', 90.0)], days, since='60d', until='10d')['chores']['done'] == 1.0


def test_a_resume_is_not_counted_as_a_second_pass():
    """`resume` writes a skip that expires as it starts, and one skip and one resume read as `passed 2`."""
    assert drift([skipped('chores', 2.0, 14.0), skipped('chores', 1.0, 0.0)])['chores']['skips'] == 1


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
    now = dt.datetime.now().astimezone()
    records = [{'pursuit': 'chores', 'event': 'done', 'occurred_at': (now - dt.timedelta(hours=h)).isoformat()} for h in (1, 3, 5)]

    assert pursuits.completed_since(records, [], now, now - dt.timedelta(days=1), None) == 3.0


def test_completed_since_reads_a_timed_entry_as_the_minutes_it_carries():
    now = dt.datetime.now().astimezone()
    records = [{'pursuit': 'read', 'event': 'done', 'occurred_at': now.isoformat(), 'duration_minutes': 20}]

    assert pursuits.completed_since(records, [], now, now - dt.timedelta(days=1), 45.0) == 20.0


def test_completed_since_drops_an_app_day_the_journal_already_carries():
    """One act reported by both records counts once, or logging what the app
    already saw would pay it off twice."""
    now = dt.datetime.now().astimezone()
    records = [{'pursuit': 'chores', 'event': 'done', 'occurred_at': now.isoformat()}]

    assert pursuits.completed_since(records, [now.date()], now, now - dt.timedelta(days=1), None) == 1.0


def test_completed_since_adds_an_app_day_the_journal_never_saw():
    now = dt.datetime.now().astimezone()
    seen = [now.date() - dt.timedelta(days=n) for n in (1, 2)]

    assert pursuits.completed_since([], seen, now, now - dt.timedelta(days=5), None) == 2.0


def test_completed_since_ignores_everything_before_the_zero_point():
    now = dt.datetime.now().astimezone()
    records = [{'pursuit': 'chores', 'event': 'done', 'occurred_at': (now - dt.timedelta(days=d)).isoformat()} for d in (1, 9)]

    assert pursuits.completed_since(records, [], now, now - dt.timedelta(days=5), None) == 1.0


def test_a_backed_pursuit_reads_the_days_its_app_reported():
    """Reading the journal alone made a pursuit finished inside its own app four
    days running come out overdue."""
    state = built(register=parsed(BACKED_REGISTER), observed={}, evidence_days={'backed': [day(n) for n in (0, 1, 2, 3)]})

    assert state['balance']['backed'] < 0, 'four days running against a 3-day cadence is ahead, not overdue'
    assert 'backed' not in pursuits.pinned(state)


def test_a_backed_pursuit_with_no_record_anywhere_opens_one_checkoff_behind():
    """Nothing on either record is the state a fresh pursuit is in, and it is due
    once rather than infinitely urgent."""
    state = built(register=parsed(BACKED_REGISTER), observed={}, evidence_days={})

    assert state['balance']['backed'] == 1.0
    assert pursuits.pinned(state) == ['backed']


def test_a_paused_pursuit_with_no_days_gets_no_row():
    """Pausing drops its evidence entry too, so an untouched one would sit here
    as a hollow row forever — said 0%, did 0%, no days."""
    assert 'paused-thing' not in drift([], {'chores': [day(1)]})


def test_a_paused_pursuit_with_days_keeps_them_and_asks_nothing_and_renders_a_dash(capsys):
    """Paused mid-window, its history is still history. It asked for nothing
    while paused though, and zero would read as a goal it was held to."""
    row = drift([done('paused-thing', 1.0)])['paused-thing']
    pursuits.render_drift([row])

    assert row['done'] == 1.0
    assert row['asked'] is None
    printed = [line for line in capsys.readouterr().out.splitlines() if 'paused-thing' in line]
    assert printed and '—' in printed[0], printed


def test_the_stranded_count_warning_names_the_box_holding_each_count(capsys):
    """Saying whose a stranded total is turns a nag into an instruction, and a count whose pursuit is held says nothing."""
    counts = {'testbox': {'retired-here': 8, 'chores': 4}, 'otherbox': {'retired-there': 7}}

    pursuits.render_stranded_counts({'counts': counts, 'stranded': []})
    assert capsys.readouterr().out == ''

    pursuits.render_stranded_counts({'counts': counts, 'stranded': ['retired-here', 'retired-there']})
    printed = capsys.readouterr().out
    assert 'retired-here  8  testbox' in printed
    assert 'retired-there  7  otherbox' in printed
    assert 'chores' not in printed


def resolving(rows: list[dict], **config) -> dict:
    return resolution('journal', {'label': 'name', 'id': 'id', **config}, rows)


def test_resolve_where_keeps_only_the_rows_that_are_this_pursuit():
    """A backend with no filter for the distinction returns everything and only
    some of it is the pursuit. `icb tasks` carries no tag, so what marks a prompt
    is its name."""
    rows = [
        {'name': 'Self Authoring', 'id': 344},
        {'name': 'Journal', 'id': 474},
    ]

    resolved = resolving(rows, resolve_where={'name': 'Journal'})

    assert resolved['choices'][0]['label'] == 'Journal'
    assert resolved['choices'][0]['id'] == '474'


def test_resolve_where_matching_nothing_is_reported_as_matching_nothing():
    """The pursuit is due and no prompt is written down. The row shows the
    description beside the flag, because nothing here can tell that apart from a
    filter that drifted from the backend's field names."""
    resolved = resolving([{'name': 'Self Authoring', 'id': 344}], resolve_where={'name': 'Journal'})
    assert resolved['candidates'] == 0
    assert resolved['choices'] == []


def test_resolve_without_a_where_takes_the_first_row_as_before():
    """The generalization has to leave every register without it untouched."""
    rows = [{'name': 'Self Authoring', 'id': 344}, {'name': 'Journal', 'id': 474}]

    assert resolving(rows)['choices'][0]['label'] == 'Self Authoring'


def test_resolve_where_matches_across_types_the_way_evidence_does():
    """A config file has no types, so an id that is 474 in JSON and "474" in YAML
    is the same id — the same rule `evidence_where` applies."""
    assert resolving([{'name': 'Journal', 'id': 474}], resolve_where={'id': '474'})['choices'][0]['id'] == '474'


def test_the_odds_reported_are_the_odds_the_draw_ran_on():
    """Reporting `probability` from the urgency-multiplied weights while sampling
    something else put five chosen rows on screen at 0.0% each, under a legend
    calling that number the chance of being drawn first."""
    current = [zeroed('chore', 0.0), done('chore', 0.0), zeroed('read', 0.0), done('read', 0.0, minutes=60)]
    state = balance_state(current)

    drawn = pursuits.compute_draw(state, seed=1)['offered']

    assert drawn, 'the pool is what fills the screen'
    assert all(state['probability'][name] > 0 for name in drawn)
    assert abs(sum(state['probability'].values()) - 1.0) < 1e-9


def test_pausing_one_pursuit_moves_no_other_pursuits_numbers():
    """A pace read off the register as a whole moved every interval when one
    pursuit left the active set. Each goal answers for itself."""
    logs = [zeroed('chore', 3.0), *[done('read', days_ago=index / 2, minutes=90) for index in range(20)]]
    running = balance_state(logs)
    paused = balance_state(logs, register=BALANCE_REGISTER + '    paused: true\n')

    assert 'read' not in paused['active']
    assert paused['intervals']['chore'] == running['intervals']['chore']
    assert paused['balance']['chore'] == running['balance']['chore']


def test_the_register_wide_minutes_map_covers_a_pursuit_the_active_set_drops():
    register = {'read': {'weight': 1, 'weekly_minutes': 30}, 'gone': {'weight': 0, 'weekly_minutes': 45}}
    assert pursuits.declared_minutes(register) == {'read': 30.0, 'gone': 45.0}


def test_a_row_the_register_dropped_renders_rather_than_crashing():
    """The draw outlives the register by up to a quarter of an hour, so pausing a
    drawn pursuit inside that window left `doit next` dying on a traceback."""
    state = built()

    row = pursuits.offer('paused-thing', state, {})

    assert row.name == 'paused-thing'
    assert row.due == 'paused', 'a row outside the active set cannot be priced and still has to render'


def test_an_unpriced_row_says_which_reason_it_is():
    """One dash for several facts leaves the one worth seeing invisible: a drawn row
    the register no longer holds reads exactly like one that is simply unscheduled.

    A drawn `socialize` set to `weight: 0` read `no schedule` while its register
    still said `cadence: 7d`."""
    state = {
        'today': NOW.date(),
        'pursuits': {
            'stopped': {'paused': True},
            'expired': {'until': NOW.date() - dt.timedelta(days=1)},
            'logged-only': {'description': 'no pace'},
            'weightless': {'weight': 0, 'cadence': '7d'},
        },
    }

    assert pursuits.why_unpriced(state, 'edited-away') == 'not in register'
    assert pursuits.why_unpriced(state, 'stopped') == 'paused'
    assert pursuits.why_unpriced(state, 'expired') == 'term ended'
    assert pursuits.why_unpriced(state, 'logged-only') == 'tracked'
    assert pursuits.why_unpriced(state, 'weightless') == 'weight 0'


def test_the_standing_line_needs_a_whole_checkoff_before_it_says_behind():
    """A balance climbs continuously from zero, so every pursuit passes through
    the fraction just above it. `behind · read +0m` names nothing anyone can act on."""
    barely = balance_state([zeroed('chore', 0.2), zeroed('read', 0.02)])

    assert barely['balance']['chore'] > 0 and barely['balance']['read'] > 0
    assert pursuits.standing_line(barely) == ''


def test_a_backdated_log_reports_the_standing_the_next_command_will(sandbox):
    """Subtracting what was logged printed a number the model disagreed with: an
    entry before the zero point pays nothing off and arithmetic cannot see that.

    The expectation is a literal, not a second call to the code under test.
    Re-deriving it is `f(x) == f(x)` routed through a test runner: replacing
    `format_due`'s body with a constant left this passing.
    """
    sandbox.append(zeroed('chores', 0.0))

    # `chores` is a 7d cadence reset just now, so the entry lands three days
    # before the zero point, pays nothing off, and the first checkoff is still
    # asked for at the end of the week.
    assert pursuits.format_due(pursuits.due_with(built(sandbox), 'chores', done('chores', 3.0))) == 'due in 7d'


def test_a_later_skip_shortens_an_earlier_one():
    """Taking the furthest-reaching expiry made a mistyped `--for 52w` unreachable
    by any command the tool ships, on a journal that is never rewritten."""
    assert pursuits.skip_expiry([skipped('chores', 0.2, 364.0), skipped('chores', 0.1, 1.0)]) == NOW + dt.timedelta(days=0.9)


def test_zeroing_the_register_writes_one_state_snapshot_not_one_per_pursuit():
    """The vector describes the register rather than the pursuit being zeroed, so
    a copy per name writes the same payload N times into an append-only journal."""
    names = sorted(load())
    records = pursuits.reset_records(names, built(), NOW, machine='testbox', identity='someone')

    assert [record['pursuit'] for record in records] == names
    assert {record['event'] for record in records} == {'reset'}
    assert sum('state_at_log' in record for record in records) == 1


def test_an_unknown_key_in_a_sibling_block_is_refused():
    """A file that refuses one typo and silently defaults another teaches the
    reader it is strict, and they stop proofreading the half that is not."""
    document = pursuits.parse_register('pursuits:\n  a:\n    weight: 1\n    cadence: 3d\nforecast:\n  budget_minute: 90\n', 'pursuits.yml')

    with pytest.raises(pursuits.RegisterError, match='budget_minute'):
        pursuits.settings_from_document(document, 'pursuits.yml')


def test_the_explain_payload_carries_the_zero_point_each_balance_counts_from(sandbox):
    state = built(sandbox)
    payload = pursuits.explain_payload(state)

    assert set(payload['zero_points']) == set(state['active'])
    assert set(payload['pool']) <= set(state['active'])


def test_the_explain_payload_carries_the_projected_due_date(sandbox):
    """A projection reads when each payment leaves the window, so the balance
    recorded beside it cannot be turned back into the date on screen."""
    state = built(sandbox)
    payload = pursuits.explain_payload(state)

    assert payload['due'] == {name: round(state['due'][name], 2) for name in state['active']}


def test_drift_reads_an_unparsable_timestamp_one_way():
    """The amount and the log tally come from two walks over the same records, so
    a record counted by one and skipped by the other reports work with no time in it."""
    row = drift([{'pursuit': 'chores', 'event': 'done', 'occurred_at': 'not a timestamp'}]).get('chores')

    assert row is None or (row['logs'], row['done']) == (0, 0.0)


def test_a_register_naming_minutes_is_pointed_at_weekly_minutes(tmp_path):
    """Two different quantities, so they do not share a word. `weekly_minutes:`
    is what a week asks for; `--minutes` is what one sitting took."""
    path = write_register(tmp_path, 'pursuits:\n  read:\n    weight: 5\n    minutes: 120\n')

    with pytest.raises(pursuits.RegisterError, match=r'unknown field\(s\) minutes\b.*weekly_minutes'):
        load(path)


def test_a_tracked_pursuit_has_a_drift_row_against_no_goal_whatever_it_did():
    """Showing where the time went is all a tracked pursuit is for, so it keeps a
    row with nothing done, and its done column carries the minutes its logs gave."""
    row = drift([done('build', 1.0, minutes=90), done('build', 2.0)], register=tracked())['build']

    assert (row['goal'], row['asked'], row['done'], row['minutes'], row['due_days']) == ('tracked', None, 2.0, 90, None)
    assert drift([done('chores', 1.0)], register=tracked())['build']['done'] == 0.0


def test_a_paused_timed_pursuit_keeps_its_unit_in_drift():
    """A record keeps the unit it was logged in. Reading the minutes off the
    active set drops a paused pursuit's, so its row would report 3 completions
    where it did 135 minutes."""
    register = parsed(
        'pursuits:\n  read:\n    weight: 30\n    weekly_minutes: 45\n    paused: true\n  chores:\n    weight: 25\n    cadence: 3d\n'
    )

    row = drift([done('read', 1.0, minutes=45) for _ in range(3)], register=register)['read']

    assert (row['unit'], row['done']) == ('minutes', 135.0)
    assert row['goal'] == '45m a week'
