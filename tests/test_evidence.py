"""Tests for the evidence channel.

The property that matters is the first one: a pursuit satisfied in its own app
counts as done with nothing typed into doit. Everything else is the failure
policy around it, and the failure policy is the part that decides whether the
draw degrades or lies — a backend that cannot be reached must fall back to the
journal, never silently report "never done".
"""

import json
import os
import shlex
import sys
from datetime import datetime
from datetime import timedelta

from doit import evidence

NOW = datetime(2026, 8, 16, 12, 0, 0).astimezone()


def emitting(document) -> str:
    """A command printing one JSON document, with no shell involved."""
    code = f'import json,sys; sys.stdout.write(json.dumps({document!r}))'
    return f'{shlex.quote(sys.executable)} -c {shlex.quote(code)}'


def failing(message: str = 'not logged in') -> str:
    code = f'import sys; sys.stderr.write({message!r}); sys.exit(1)'
    return f'{shlex.quote(sys.executable)} -c {shlex.quote(code)}'


def test_an_app_that_saw_it_counts_as_done_with_nothing_typed(tmp_path):
    """The whole point, in one assertion.

    Nothing was logged by hand. The app answered, so the pursuit has a last-done.
    """
    yesterday = (NOW - timedelta(days=1)).isoformat()
    pursuits = {
        'tasks': {
            'weight': 25,
            'evidence': emitting([{'complete_date': yesterday}]),
            'evidence_time': 'complete_date',
        }
    }
    payload = evidence.refresh(pursuits, tmp_path, NOW)
    seen = evidence.observed(payload)
    assert 'tasks' in seen
    assert evidence.merged({}, seen)['tasks'] == seen['tasks']


def test_the_later_of_typed_and_observed_wins():
    typed = {'tasks': NOW - timedelta(days=5), 'connect': NOW - timedelta(days=2)}
    seen = {'tasks': NOW - timedelta(days=1)}
    merged = evidence.merged(typed, seen)
    assert merged['tasks'] == NOW - timedelta(days=1), 'the app saw it more recently'
    assert merged['connect'] == NOW - timedelta(days=2), 'no app sees connect, so the log stands'


def test_a_row_filter_picks_one_habit_out_of_the_call(tmp_path):
    """Twenty habits come back from one call and one of them is the pursuit."""
    rows = [
        {'name': 'Read', 'complete_date': (NOW - timedelta(days=1)).isoformat()},
        {'name': 'Yoga', 'complete_date': NOW.isoformat()},
    ]
    pursuits = {
        'read': {
            'weight': 25,
            'evidence': emitting(rows),
            'evidence_time': 'complete_date',
            'evidence_where': {'name': 'Read'},
        }
    }
    seen = evidence.observed(evidence.refresh(pursuits, tmp_path, NOW))
    assert seen['read'] == NOW - timedelta(days=1), 'Yoga today must not satisfy the reading pursuit'


def test_the_most_recent_row_wins_not_the_first():
    rows = [
        {'at': (NOW - timedelta(days=9)).isoformat()},
        {'at': (NOW - timedelta(days=2)).isoformat()},
        {'at': (NOW - timedelta(days=6)).isoformat()},
    ]
    found = evidence.latest_in(rows, {'evidence_time': 'at'}, NOW)
    assert found == NOW - timedelta(days=2)


def test_a_backend_answering_without_an_offset_is_still_comparable():
    """A naive stamp compared against an aware now raises rather than sorting."""
    rows = [{'at': '2026-08-15T09:30:00'}]
    found = evidence.latest_in(rows, {'evidence_time': 'at'}, NOW)
    assert found is not None
    assert (NOW - found).days == 1


def test_nested_rows_and_a_lone_object_both_read():
    nested = {'sessions': [{'at': NOW.isoformat()}]}
    assert evidence.latest_in(nested, {'evidence_time': 'at', 'evidence_items': 'sessions'}, NOW) == NOW
    assert evidence.latest_in({'at': NOW.isoformat()}, {'evidence_time': 'at'}, NOW) == NOW


def test_a_failed_read_keeps_the_previous_answer_and_says_why(tmp_path):
    """A logged-out CLI degrades that pursuit to its journal, not to a wrong zero."""
    yesterday = (NOW - timedelta(days=1)).isoformat()
    working = {'train': {'weight': 25, 'evidence': emitting([{'at': yesterday}]), 'evidence_time': 'at'}}
    evidence.refresh(working, tmp_path, NOW)

    broken = {'train': {'weight': 25, 'evidence': failing(), 'evidence_time': 'at'}}
    payload = evidence.refresh(broken, tmp_path, NOW, force=True)

    assert evidence.observed(payload)['train'] == datetime.fromisoformat(yesterday)
    assert 'not logged in' in evidence.problems(payload)['train']


def test_output_that_is_not_json_is_an_error_rather_than_a_crash(tmp_path):
    pursuits = {'pr': {'weight': 25, 'evidence': f'{shlex.quote(sys.executable)} --version', 'evidence_time': 'at'}}
    payload = evidence.refresh(pursuits, tmp_path, NOW, force=True)
    assert 'pr' in evidence.problems(payload)
    assert evidence.observed(payload) == {}


def test_a_fresh_answer_is_not_asked_for_again(tmp_path):
    pursuits = {'tasks': {'weight': 25, 'evidence': emitting([{'at': NOW.isoformat()}]), 'evidence_time': 'at'}}
    evidence.refresh(pursuits, tmp_path, NOW)

    pursuits['tasks']['evidence'] = failing('should not run')
    payload = evidence.refresh(pursuits, tmp_path, NOW + timedelta(seconds=60))
    assert evidence.problems(payload) == {}, 'inside the TTL the app is left alone'


def test_an_aged_out_answer_is_asked_for_again(tmp_path):
    pursuits = {'tasks': {'weight': 25, 'evidence': emitting([{'at': NOW.isoformat()}]), 'evidence_time': 'at'}}
    evidence.refresh(pursuits, tmp_path, NOW)

    pursuits['tasks']['evidence'] = failing('asked again')
    later = NOW + timedelta(seconds=evidence.REFRESH_TTL_SECONDS + 1)
    payload = evidence.refresh(pursuits, tmp_path, later)
    assert 'asked again' in evidence.problems(payload)['tasks']


def test_dropping_evidence_from_a_pursuit_drops_its_answer(tmp_path):
    """A weight edit must not leave an old app's verdict alive."""
    pursuits = {'tasks': {'weight': 25, 'evidence': emitting([{'at': NOW.isoformat()}]), 'evidence_time': 'at'}}
    evidence.refresh(pursuits, tmp_path, NOW)

    payload = evidence.refresh({'tasks': {'weight': 25}}, tmp_path, NOW, force=True)
    assert evidence.observed(payload) == {}


def test_a_command_without_a_time_field_is_not_evidence():
    assert evidence.declared({'tasks': {'evidence': 'icb tasks list'}}) == {}
    assert evidence.declared({'tasks': {'evidence_time': 'complete_date'}}) == {}


def test_an_unreadable_cache_costs_a_refresh_and_nothing_else(tmp_path):
    evidence.cache_path(tmp_path).write_text('{ not json')
    assert evidence.load(tmp_path) == {'schema_version': evidence.SCHEMA_VERSION, 'pursuits': {}}


def test_the_cache_round_trips(tmp_path):
    pursuits = {'tasks': {'weight': 25, 'evidence': emitting([{'at': NOW.isoformat()}]), 'evidence_time': 'at'}}
    evidence.refresh(pursuits, tmp_path, NOW)
    written = json.loads(evidence.cache_path(tmp_path).read_text())
    assert written['schema_version'] == evidence.SCHEMA_VERSION
    assert written['pursuits']['tasks']['last']


def test_every_day_the_app_reported_survives_the_read(tmp_path):
    """The occurrences are what `latest_in` alone throws away."""
    rows = [
        {'at': (NOW - timedelta(hours=1)).isoformat()},
        {'at': (NOW - timedelta(hours=2)).isoformat()},
        {'at': (NOW - timedelta(days=3)).isoformat()},
        {'at': (NOW - timedelta(days=11)).isoformat()},
    ]
    pursuits = {'build': {'weight': 35, 'evidence': emitting(rows), 'evidence_time': 'at'}}
    payload = evidence.refresh(pursuits, tmp_path, NOW)

    assert evidence.occurrences(payload)['build'] == [
        (NOW - timedelta(days=11)).date(),
        (NOW - timedelta(days=3)).date(),
        NOW.date(),
    ], 'twice in one evening is one day, and the days arrive oldest first'
    assert evidence.observed(payload)['build'] == NOW - timedelta(hours=1), 'the latest answer is unchanged'


def test_a_day_outside_the_window_is_not_kept(tmp_path):
    """Bounded, so one pursuit cannot grow a cache for as long as its app has history."""
    inside = NOW - timedelta(days=evidence.OCCURRENCE_WINDOW_DAYS - 1)
    outside = NOW - timedelta(days=evidence.OCCURRENCE_WINDOW_DAYS)
    rows = [{'at': inside.isoformat()}, {'at': outside.isoformat()}]
    pursuits = {'build': {'weight': 35, 'evidence': emitting(rows), 'evidence_time': 'at'}}
    payload = evidence.refresh(pursuits, tmp_path, NOW)

    assert evidence.occurrences(payload)['build'] == [inside.date()]


def test_a_day_the_backend_dated_ahead_of_now_is_outside_the_window():
    """The window ends today, so a row stamped tomorrow is not in it."""
    assert evidence.dates_of([NOW + timedelta(days=1)], NOW) == []
    assert evidence.dates_of([NOW], NOW) == [NOW.date().isoformat()]


def test_the_row_filter_bounds_the_days_as_well_as_the_last_one(tmp_path):
    """Yoga today must not put a day into the reading pursuit."""
    rows = [
        {'name': 'Read', 'complete_date': (NOW - timedelta(days=2)).isoformat()},
        {'name': 'Yoga', 'complete_date': NOW.isoformat()},
    ]
    pursuits = {
        'read': {
            'weight': 25,
            'evidence': emitting(rows),
            'evidence_time': 'complete_date',
            'evidence_where': {'name': 'Read'},
        }
    }
    payload = evidence.refresh(pursuits, tmp_path, NOW)
    assert evidence.occurrences(payload)['read'] == [(NOW - timedelta(days=2)).date()]


def test_a_failed_read_keeps_the_previous_days(tmp_path):
    """The same policy as `last`: an unreachable backend degrades, it does not empty."""
    rows = [{'at': (NOW - timedelta(days=1)).isoformat()}, {'at': (NOW - timedelta(days=4)).isoformat()}]
    working = {'train': {'weight': 25, 'evidence': emitting(rows), 'evidence_time': 'at'}}
    evidence.refresh(working, tmp_path, NOW)

    broken = {'train': {'weight': 25, 'evidence': failing(), 'evidence_time': 'at'}}
    payload = evidence.refresh(broken, tmp_path, NOW, force=True)

    assert evidence.occurrences(payload)['train'] == [
        (NOW - timedelta(days=4)).date(),
        (NOW - timedelta(days=1)).date(),
    ]
    assert 'not logged in' in evidence.problems(payload)['train']


def test_the_days_are_written_as_plain_dates(tmp_path):
    pursuits = {'tasks': {'weight': 25, 'evidence': emitting([{'at': NOW.isoformat()}]), 'evidence_time': 'at'}}
    evidence.refresh(pursuits, tmp_path, NOW)
    written = json.loads(evidence.cache_path(tmp_path).read_text())
    assert written['pursuits']['tasks']['dates'] == [NOW.date().isoformat()]


def test_a_cache_from_either_side_of_this_still_reads(tmp_path):
    """A cache is written by whichever doit ran last, and both must read it.

    An older one wrote no days, and reads as no occurrences rather than as damage.
    A newer one writes a field the older one never asks for, and answering `last`
    must not depend on knowing every key in the entry.
    """
    older = {
        'schema_version': evidence.SCHEMA_VERSION,
        'pursuits': {'tasks': {'checked_at': NOW.isoformat(), 'last': (NOW - timedelta(days=2)).isoformat()}},
    }
    evidence.save(tmp_path, older)
    loaded = evidence.load(tmp_path)
    assert loaded == older, 'a missing field is absence, not a malformed document'
    assert evidence.observed(loaded)['tasks'] == NOW - timedelta(days=2)
    assert evidence.occurrences(loaded) == {}

    newer = evidence.load(tmp_path)
    newer['pursuits']['tasks']['dates'] = [NOW.date().isoformat()]
    newer['pursuits']['tasks']['something_later'] = 'a field this doit does not know'
    evidence.save(tmp_path, newer)
    reloaded = evidence.load(tmp_path)
    assert evidence.observed(reloaded)['tasks'] == NOW - timedelta(days=2)
    assert evidence.occurrences(reloaded)['tasks'] == [NOW.date()]
    assert evidence.problems(reloaded) == {}


def test_a_day_that_is_not_a_date_is_dropped_rather_than_fatal(tmp_path):
    payload = {
        'schema_version': evidence.SCHEMA_VERSION,
        'pursuits': {
            'tasks': {'checked_at': NOW.isoformat(), 'dates': ['not a date', NOW.date().isoformat()]},
            'read': {'checked_at': NOW.isoformat(), 'dates': None},
            'train': {'checked_at': NOW.isoformat(), 'dates': 'yesterday'},
        },
    }
    assert evidence.occurrences(payload) == {'tasks': [NOW.date()]}


def written(directory, name: str, days_ago: float) -> None:
    """A file whose mtime is the moment that entry was written."""
    path = directory / name
    path.write_text('an entry')
    when = (NOW - timedelta(days=days_ago)).timestamp()
    os.utime(path, (when, when))


def test_a_directory_of_files_answers_when_the_practice_last_happened(tmp_path):
    """The file is the record already. A second one you have to remember to make
    is the thing that goes stale."""
    written(tmp_path, '2026-07-01-in-love-with-a-boy.md', 3)
    written(tmp_path, '2024-11-05-addictions.md', 40)

    _, entry = evidence.read_files('journal', str(tmp_path), NOW)

    assert entry['last'].startswith((NOW - timedelta(days=3)).date().isoformat())
    assert entry['dates'] == [(NOW - timedelta(days=40)).date().isoformat(), (NOW - timedelta(days=3)).date().isoformat()]


def test_a_subdirectory_beside_the_entries_is_not_an_entry(tmp_path):
    """Non-recursive, so a topics folder living with the journal does not read as
    having journalled."""
    written(tmp_path, 'entry.md', 5)
    nested = tmp_path / 'topics'
    nested.mkdir()
    written(nested, 'something-to-write-about.md', 0)

    _, entry = evidence.read_files('journal', str(tmp_path), NOW)

    assert entry['dates'] == [(NOW - timedelta(days=5)).date().isoformat()]


def test_a_dotfile_is_not_an_entry(tmp_path):
    written(tmp_path, '.stfolder-marker', 0)
    written(tmp_path, 'entry.md', 5)

    _, entry = evidence.read_files('journal', str(tmp_path), NOW)

    assert entry['dates'] == [(NOW - timedelta(days=5)).date().isoformat()]


def test_an_empty_directory_reads_as_never_rather_than_broken(tmp_path):
    """Nothing written yet is a true answer, and an error would keep the previous
    one standing instead."""
    _, entry = evidence.read_files('journal', str(tmp_path), NOW)

    assert entry['last'] is None
    assert entry['dates'] == []
    assert 'error' not in entry


def test_a_directory_that_is_not_there_is_an_error_not_a_crash(tmp_path):
    _, entry = evidence.read_files('journal', str(tmp_path / 'nope'), NOW)

    assert 'error' in entry
    assert 'last' not in entry


def test_the_path_is_read_the_way_it_is_written(tmp_path, monkeypatch):
    """A config file says ~/notes/journal, and nothing expands it on the way in."""
    monkeypatch.setenv('HOME', str(tmp_path))
    written(tmp_path, 'entry.md', 2)

    _, entry = evidence.read_files('journal', '~', NOW)

    assert entry['dates'] == [(NOW - timedelta(days=2)).date().isoformat()]


def test_a_pursuit_naming_files_needs_no_timestamp_field():
    """A file's own timestamp is the field, so pairing it with evidence_time
    would be a second way to say the same thing."""
    register = {
        'journal': {'evidence_files': '~/notes/journal'},
        'chore': {},
        'half': {'evidence': 'icb tasks list --json'},
    }

    assert set(evidence.declared(register)) == {'journal'}


def test_files_evidence_travels_through_the_cache_like_any_other(tmp_path):
    """`refresh` is the only writer, so a form it cannot reach would answer once
    and never be stored."""
    entries = tmp_path / 'journal'
    entries.mkdir()
    written(entries, 'entry.md', 1)

    payload = evidence.refresh({'journal': {'evidence_files': str(entries)}}, tmp_path / 'cache', NOW)

    assert evidence.observed(payload)['journal'].date() == (NOW - timedelta(days=1)).date()
    assert evidence.occurrences(payload)['journal'] == [(NOW - timedelta(days=1)).date()]


def test_a_filter_matches_regardless_of_case():
    """The value is typed into a config file and the row is written by an app, so
    a miss on capitalization reads as a pursuit nobody has done."""
    rows = [{'name': 'journal'}, {'name': 'Self Authoring'}]

    assert evidence.matching(rows, {'name': 'Journal'}) == [{'name': 'journal'}]
    assert evidence.matching(rows, {'name': 'JOURNAL'}) == [{'name': 'journal'}]


def test_a_filter_still_distinguishes_different_words():
    """Folding case must not fold meaning — a near-miss stays a miss."""
    rows = [{'name': 'Journalling'}, {'name': 'Journal'}]

    assert evidence.matching(rows, {'name': 'Journal'}) == [{'name': 'Journal'}]


def test_a_filter_on_a_number_is_unaffected_by_the_fold():
    rows = [{'id': 474}, {'id': 344}]

    assert evidence.matching(rows, {'id': '474'}) == [{'id': 474}]
