"""Tests for doit.observe — deriving last-done from traces the act left behind.

The failure this module exists to prevent is an item reporting weeks overdue
hours after it ran, because its only record was one you had to type. So the cases
that matter are those where evidence exists somewhere and has to be found, and
those where a near-miss must not be mistaken for evidence.

`isolated_shell_history` (conftest) points HISTORY at a path that does not exist,
so every test here supplies its own history or deliberately has none.
"""

import json
from datetime import date
from datetime import datetime
from datetime import timedelta

import pytest

from doit import observe


def write_history(tmp_path, monkeypatch, entries: list[tuple[date, str]]) -> None:
    """A zsh EXTENDED_HISTORY file recording each command on each date."""
    lines = [f': {int(datetime.combine(when, datetime.min.time()).timestamp())}:0;{command}' for when, command in entries]
    history = tmp_path / 'history'
    history.write_text('\n'.join(lines) + '\n')
    monkeypatch.setattr(observe, 'HISTORY', history)
    observe.history_entries.cache_clear()


def write_dates(tmp_path, name: str, payload: object) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(payload))
    return str(path)


def test_last_run_finds_the_date_a_command_was_last_typed(tmp_path, monkeypatch):
    old, recent = date(2026, 1, 2), date(2026, 3, 4)
    write_history(tmp_path, monkeypatch, [(old, 'brew-maintenance'), (recent, 'brew-maintenance')])

    assert observe.last_run('brew-maintenance').date == recent.isoformat()


def test_last_run_takes_the_newest_timestamp_not_the_last_line(tmp_path, monkeypatch):
    """Several shells append to one file, so its order is write order — only
    approximately chronological. Reading the last match would report the older
    run whenever two sessions interleaved."""
    newest, older = date(2026, 5, 1), date(2026, 2, 1)
    write_history(tmp_path, monkeypatch, [(newest, 'indy index'), (older, 'indy index')])

    assert observe.last_run('indy index').date == newest.isoformat()


def test_last_run_counts_the_command_with_arguments(tmp_path, monkeypatch):
    when = date(2026, 6, 7)
    write_history(tmp_path, monkeypatch, [(when, 'toolbox check --verbose')])

    assert observe.last_run('toolbox check').date == when.isoformat()


def test_last_run_will_not_claim_a_longer_command_that_merely_starts_the_same():
    """The case that would silently mark the wrong item done: two register items
    whose commands share a prefix, so one reports itself done off the other."""
    assert observe.is_invocation_of('claude /audit-repo-docs', 'claude /audit-repo') is False
    assert observe.is_invocation_of('claude /audit-repo', 'claude /audit-repo') is True
    assert observe.is_invocation_of('claude /audit-repo --fix', 'claude /audit-repo') is True


def test_last_run_is_silent_when_the_command_never_ran(tmp_path, monkeypatch):
    write_history(tmp_path, monkeypatch, [(date(2026, 1, 1), 'something else')])

    assert observe.last_run('never-typed').date is None


def test_last_run_without_a_history_file_observes_nothing_and_does_not_complain():
    """A machine with no history yet is not a misconfigured register."""
    assert observe.last_run('anything') == observe.Observation(None, '')


def test_newest_date_in_reads_the_latest_stamp_in_a_foreign_state_file(tmp_path):
    """toolbox's shape: every tool stamped with the day it last surfaced, so the
    newest value is when a reminder last actually landed."""
    path = write_dates(tmp_path, 'reminders.json', {'jira': '2026-08-07', 'eza': '2026-06-19', 'bat': '2026-07-01'})

    assert observe.newest_date_in(path).date == '2026-08-07'


def test_newest_date_in_ignores_values_that_are_not_dates(tmp_path):
    path = write_dates(tmp_path, 'mixed.json', {'good': '2026-08-07', 'version': '3', 'flag': True})

    assert observe.newest_date_in(path).date == '2026-08-07'


def test_newest_date_in_treats_a_missing_file_as_nothing_observed(tmp_path):
    """Absence is the ordinary state of a fresh machine — the owning tool writes
    its state the first time it records anything — so it is not a problem."""
    assert observe.newest_date_in(str(tmp_path / 'not-yet.json')) == observe.Observation(None, '')


def test_newest_date_in_reports_a_file_it_cannot_parse(tmp_path):
    path = tmp_path / 'broken.json'
    path.write_text('{not json')

    assert observe.newest_date_in(str(path)).date is None
    assert 'not valid JSON' in observe.newest_date_in(str(path)).problem


def test_observed_defaults_to_watching_the_items_own_command(tmp_path, monkeypatch):
    """No `observe:` needed for most items: the command is already the item's own
    statement of what doing it looks like."""
    when = date(2026, 4, 5)
    write_history(tmp_path, monkeypatch, [(when, 'review-diff')])

    assert observe.observed(None, 'review-diff').date == when.isoformat()


def test_observed_false_turns_observation_off(tmp_path, monkeypatch):
    """For an item whose command is not the work — a picker, a browse view."""
    write_history(tmp_path, monkeypatch, [(date(2026, 4, 5), 'doit labs pick')])

    assert observe.observed(False, 'doit labs pick').date is None


def test_observed_takes_an_explicit_observer_over_the_command(tmp_path, monkeypatch):
    write_history(tmp_path, monkeypatch, [(date(2026, 1, 1), 'toolbox remind')])
    path = write_dates(tmp_path, 'reminders.json', {'jira': '2026-08-07'})

    assert observe.observed({'newest-date-in': path}, 'toolbox remind').date == '2026-08-07'


def test_observed_reports_an_observer_it_does_not_recognise():
    """The register is hand-edited, so a typo must degrade one item's date and
    say so — not fail silently as a task apparently never done."""
    result = observe.observed({'psychic': 'x'}, 'cmd')

    assert result.date is None
    assert "unknown observer 'psychic'" in result.problem


def test_observed_reports_a_malformed_spec():
    assert observe.observed({'history': 'a', 'newest-date-in': 'b'}, 'cmd').problem
    assert observe.observed('just a string', 'cmd').problem


@pytest.mark.parametrize(
    ('dates', 'expected'),
    [
        ((None, None), None),
        (('2026-01-01', None), '2026-01-01'),
        ((None, '2026-01-01'), '2026-01-01'),
        (('2026-01-01', '2026-05-05'), '2026-05-05'),
        (('2026-05-05', '2026-01-01'), '2026-05-05'),
    ],
)
def test_newest_never_moves_a_date_backwards(dates, expected):
    """A stamp and an observation are both true reports of the same act, so
    adding an observer can only improve an item's date, never lose one."""
    assert observe.newest(*dates) == expected


def test_history_entries_skips_lines_that_are_not_invocations(tmp_path, monkeypatch):
    """A multi-line command continues on unprefixed lines; a continuation is
    never the start of an invocation, so skipping it is correct."""
    history = tmp_path / 'history'
    history.write_text(': 1786153106:0;toolbox remind \\\n  --brief\nplain junk\n')
    monkeypatch.setattr(observe, 'HISTORY', history)
    observe.history_entries.cache_clear()

    assert [entry.command for entry in observe.history_entries()] == ['toolbox remind \\']


def test_history_survives_undecodable_bytes(tmp_path, monkeypatch):
    """Years of a real history file will contain something that is not UTF-8, and
    losing every observation to it would be silent."""
    history = tmp_path / 'history'
    history.write_bytes(b': 1786153106:0;toolbox remind\n: 1786153107:0;echo \xe9\xff\n')
    monkeypatch.setattr(observe, 'HISTORY', history)
    observe.history_entries.cache_clear()

    assert len(observe.history_entries()) == 2


def test_a_recent_run_clears_an_item_a_stale_stamp_left_overdue(tmp_path, monkeypatch):
    """The whole point, end to end: doing the thing is what marks it done."""
    stamped = (date.today() - timedelta(days=33)).isoformat()
    ran_today = date.today()
    write_history(tmp_path, monkeypatch, [(ran_today, 'toolbox remind')])

    assert observe.newest(stamped, observe.observed(None, 'toolbox remind').date) == ran_today.isoformat()


def stub_history(monkeypatch, entries: list[tuple[str, str, str]]) -> None:
    """Make history_entries return exactly these (date, host, command) rows.

    Feeds the atuin seam rather than replacing `history_entries` itself, which is
    cached — swapping the cached function out leaves conftest with nothing to
    clear on teardown.
    """
    invocations = tuple(observe.Invocation(*entry) for entry in entries)
    monkeypatch.setattr(observe, 'atuin_invocations', lambda: invocations)
    observe.history_entries.cache_clear()


def test_machine_scope_ignores_a_run_from_another_box(monkeypatch):
    """Per-machine work — brew, a PATH-vs-disk check — is not done for everyone
    just because one machine did it."""
    monkeypatch.setattr(observe, 'machine_name', lambda: 'macmini')
    stub_history(monkeypatch, [('2026-08-07', 'archlinux', 'brew-maintenance')])

    assert observe.last_run('brew-maintenance', observe.FLEET).date == '2026-08-07'
    assert observe.last_run('brew-maintenance', observe.MACHINE).date is None


def test_machine_scope_counts_a_run_on_this_box(monkeypatch):
    monkeypatch.setattr(observe, 'machine_name', lambda: 'macmini')
    stub_history(monkeypatch, [('2026-08-07', 'macmini', 'brew-maintenance')])

    assert observe.last_run('brew-maintenance', observe.MACHINE).date == '2026-08-07'


def test_fleet_scope_is_the_default(monkeypatch):
    """Most maintenance is done once for everyone, so the narrower reading has to
    be asked for."""
    monkeypatch.setattr(observe, 'machine_name', lambda: 'macmini')
    stub_history(monkeypatch, [('2026-08-07', 'archlinux', 'indy index')])

    assert observe.observed(None, 'indy index').date == '2026-08-07'
    assert observe.observed({'scope': 'machine'}, 'indy index').date is None


def test_scope_narrows_the_default_observer_without_naming_it(monkeypatch):
    monkeypatch.setattr(observe, 'machine_name', lambda: 'macmini')
    stub_history(monkeypatch, [('2026-08-07', 'macmini', 'toolbox check')])

    assert observe.observed({'scope': 'machine'}, 'toolbox check').date == '2026-08-07'


def test_an_unknown_scope_is_reported_not_silently_widened():
    result = observe.observed({'scope': 'galaxy'}, 'cmd')

    assert result.date is None
    assert "unknown scope 'galaxy'" in result.problem


def test_a_state_file_cannot_be_scoped_to_a_machine(tmp_path):
    """It records that something happened, never where — narrowing it would
    answer a different question while looking like it answered this one."""
    path = write_dates(tmp_path, 'reminders.json', {'jira': '2026-08-07'})

    result = observe.observed({'newest-date-in': path, 'scope': 'machine'}, '')

    assert result.date is None
    assert 'records no machine' in result.problem


def test_atuin_rows_are_parsed_with_host_and_date():
    """The command may contain the separator, so it takes the remainder."""
    stdout = '2026-08-07 21:38:53|macmini.trusted|toolbox remind\n2026-07-01 09:00:00|archlinux|rg "a|b" .\nrubbish\n'

    assert observe.parse_atuin_rows(stdout) == (
        observe.Invocation('2026-08-07', 'macmini', 'toolbox remind'),
        observe.Invocation('2026-07-01', 'archlinux', 'rg "a|b" .'),
    )


def test_atuin_host_is_canonicalised_to_the_recorded_form():
    """atuin reports the fully qualified name; a bare comparison would match no
    host at all and report that nothing ever ran anywhere."""
    stdout = '2026-08-07 21:38:53|macmini.trusted|toolbox remind\n'

    assert observe.parse_atuin_rows(stdout)[0].host == 'macmini'


def test_history_falls_back_to_zsh_when_atuin_cannot_be_asked(tmp_path, monkeypatch):
    """A box where atuin is missing, not yet syncing, or broken still observes."""
    when = date(2026, 5, 5)
    write_history(tmp_path, monkeypatch, [(when, 'brew-maintenance')])

    assert observe.last_run('brew-maintenance').date == when.isoformat()


def test_zsh_fallback_attributes_every_row_to_this_machine(tmp_path, monkeypatch):
    """It is this machine's file, so a machine-scoped question is answered right
    and a fleet-scoped one narrows to this box rather than going blank."""
    monkeypatch.setattr(observe, 'machine_name', lambda: 'macmini')
    write_history(tmp_path, monkeypatch, [(date(2026, 5, 5), 'brew-maintenance')])

    assert {entry.host for entry in observe.zsh_invocations()} == {'macmini'}
