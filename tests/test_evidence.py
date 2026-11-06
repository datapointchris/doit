"""Tests for the evidence channel.

The property that matters is the first one: a pursuit satisfied in its own app
counts as done with nothing typed into doit. Everything else is the failure
policy around it, and the failure policy is the part that decides whether the
draw degrades or lies — a backend that cannot be reached must fall back to the
journal, never silently report "never done".
"""

import datetime as dt

from doit import evidence

NOW = dt.datetime(2026, 8, 16, 12, 0, 0).astimezone()


class App:
    """A stand-in for the service's evidence read: answers each pursuit with ``said``, or fails with it when it is text."""

    def __init__(self, said: object) -> None:
        self.said = said

    def read(self, name: str, config: dict, now: dt.datetime) -> tuple[str, dict]:
        if isinstance(self.said, str):
            return name, {'checked_at': now.isoformat(), 'error': self.said}
        return name, evidence.answer_from(self.said, config, now)


def refresh(payload: dict, register: dict, app: App, now: dt.datetime = NOW, *, force: bool = False) -> dict:
    evidence.refreshed(payload, register, now, app.read, force=force)
    return payload


def test_an_app_that_saw_it_counts_as_done_with_nothing_typed():
    """The whole point, in one assertion.

    Nothing was logged by hand. The app answered, so the pursuit has a last-done.
    """
    yesterday = (NOW - dt.timedelta(days=1)).isoformat()
    register = {'tasks': {'weight': 25, 'evidence': 'tasks', 'evidence_time': 'complete_date'}}
    seen = evidence.observed(refresh({}, register, App([{'complete_date': yesterday}])))
    assert 'tasks' in seen
    assert evidence.merged({}, seen)['tasks'] == seen['tasks']


def test_the_later_of_typed_and_observed_wins():
    typed = {'tasks': NOW - dt.timedelta(days=5), 'connect': NOW - dt.timedelta(days=2)}
    seen = {'tasks': NOW - dt.timedelta(days=1)}
    merged = evidence.merged(typed, seen)
    assert merged['tasks'] == NOW - dt.timedelta(days=1), 'the app saw it more recently'
    assert merged['connect'] == NOW - dt.timedelta(days=2), 'no app sees connect, so the log stands'


def test_a_row_filter_picks_one_habit_out_of_the_call_and_bounds_its_days():
    """Twenty habits come back from one call and one of them is the pursuit."""
    rows = [
        {'name': 'Read', 'complete_date': (NOW - dt.timedelta(days=2)).isoformat()},
        {'name': 'Yoga', 'complete_date': NOW.isoformat()},
    ]
    config = {'evidence_time': 'complete_date', 'evidence_where': {'name': 'Read'}}
    answer = evidence.answer_from(rows, config, NOW)
    assert answer['last'] == (NOW - dt.timedelta(days=2)).isoformat(), 'Yoga today must not satisfy the reading pursuit'
    assert answer['dates'] == [(NOW - dt.timedelta(days=2)).date().isoformat()]


def test_the_most_recent_row_wins_not_the_first():
    rows = [
        {'at': (NOW - dt.timedelta(days=9)).isoformat()},
        {'at': (NOW - dt.timedelta(days=2)).isoformat()},
        {'at': (NOW - dt.timedelta(days=6)).isoformat()},
    ]
    assert evidence.answer_from(rows, {'evidence_time': 'at'}, NOW)['last'] == (NOW - dt.timedelta(days=2)).isoformat()


def test_a_backend_answering_without_an_offset_is_still_comparable():
    """A naive stamp compared against an aware now raises rather than sorting."""
    rows = [{'at': '2026-08-15T09:30:00'}]
    last = evidence.answer_from(rows, {'evidence_time': 'at'}, NOW)['last']
    assert (NOW - dt.datetime.fromisoformat(last)).days == 1


def test_nested_rows_and_a_lone_object_both_read():
    nested = {'sessions': [{'at': NOW.isoformat()}]}
    assert evidence.answer_from(nested, {'evidence_time': 'at', 'evidence_items': 'sessions'}, NOW)['last'] == NOW.isoformat()
    assert evidence.answer_from({'at': NOW.isoformat()}, {'evidence_time': 'at'}, NOW)['last'] == NOW.isoformat()


def test_a_failed_read_keeps_the_previous_answer_and_its_days_and_says_why():
    """A logged-out app degrades that pursuit to its journal, not to a wrong zero."""
    rows = [{'at': (NOW - dt.timedelta(days=1)).isoformat()}, {'at': (NOW - dt.timedelta(days=4)).isoformat()}]
    register = {'train': {'weight': 25, 'evidence': 'train', 'evidence_time': 'at'}}
    payload = refresh({}, register, App(rows))

    refresh(payload, register, App('not logged in'), force=True)

    assert evidence.observed(payload)['train'] == NOW - dt.timedelta(days=1)
    assert evidence.occurrences(payload)['train'] == [(NOW - dt.timedelta(days=4)).date(), (NOW - dt.timedelta(days=1)).date()]
    assert 'not logged in' in evidence.problems(payload)['train']


def test_an_answer_is_asked_for_again_only_once_it_ages_out():
    register = {'tasks': {'weight': 25, 'evidence': 'tasks', 'evidence_time': 'at'}}
    payload = refresh({}, register, App([{'at': NOW.isoformat()}]))

    refresh(payload, register, App('should not run'), NOW + dt.timedelta(seconds=60))
    assert evidence.problems(payload) == {}, 'inside the TTL the app is left alone'

    refresh(payload, register, App('asked again'), NOW + dt.timedelta(seconds=evidence.REFRESH_TTL_SECONDS + 1))
    assert 'asked again' in evidence.problems(payload)['tasks']


def test_an_edited_declaration_is_asked_again_inside_the_ttl():
    """Narrowing a pursuit's evidence to one kind of closer kept the unfiltered
    answer, `last today`, until the cache aged out half an hour later."""
    register = {'build': {'evidence': 'closed', 'evidence_time': 'closed_ts'}}
    payload = refresh({}, register, App([{'closed_ts': NOW.isoformat()}]))

    register['build']['evidence'] = 'closed by me'
    refresh(payload, register, App([]), NOW + dt.timedelta(seconds=60))

    assert evidence.observed(payload) == {}


def test_a_failed_read_keeps_no_answer_to_an_edited_declaration():
    """The cached answer was to a different question, so failing to ask the new one leaves none."""
    register = {'build': {'evidence': 'closed', 'evidence_time': 'closed_ts'}}
    payload = refresh({}, register, App([{'closed_ts': NOW.isoformat()}]))

    register['build']['evidence'] = 'closed by me'
    refresh(payload, register, App('not logged in'), NOW + dt.timedelta(seconds=60))

    assert evidence.observed(payload) == {}
    assert 'not logged in' in evidence.problems(payload)['build']


def test_an_answer_cached_with_no_declaration_survives_a_failed_read():
    """A cache written before answers carried their declaration degrades rather than empties."""
    yesterday = (NOW - dt.timedelta(days=1)).isoformat()
    payload = {'pursuits': {'train': {'checked_at': NOW.isoformat(), 'last': yesterday, 'dates': [yesterday[:10]]}}}

    refresh(payload, {'train': {'evidence': 'train', 'evidence_time': 'at'}}, App('not logged in'), NOW + dt.timedelta(seconds=60))

    assert evidence.observed(payload)['train'] == dt.datetime.fromisoformat(yesterday)


def test_dropping_evidence_from_a_pursuit_drops_its_answer():
    """A weight edit must not leave an old app's verdict alive."""
    payload = refresh({}, {'tasks': {'weight': 25, 'evidence': 'tasks', 'evidence_time': 'at'}}, App([{'at': NOW.isoformat()}]))

    refresh(payload, {'tasks': {'weight': 25}}, App('never asked'), force=True)
    assert evidence.observed(payload) == {}


def test_a_declaration_without_a_time_field_is_not_evidence():
    assert evidence.declared({'tasks': {'evidence': 'icb tasks list'}}) == {}
    assert evidence.declared({'tasks': {'evidence_time': 'complete_date'}}) == {}


def test_every_day_the_app_reported_survives_the_read():
    rows = [
        {'at': (NOW - dt.timedelta(hours=1)).isoformat()},
        {'at': (NOW - dt.timedelta(hours=2)).isoformat()},
        {'at': (NOW - dt.timedelta(days=3)).isoformat()},
        {'at': (NOW - dt.timedelta(days=11)).isoformat()},
    ]
    payload = refresh({}, {'build': {'weight': 35, 'evidence': 'build', 'evidence_time': 'at'}}, App(rows))

    assert evidence.occurrences(payload)['build'] == [
        (NOW - dt.timedelta(days=11)).date(),
        (NOW - dt.timedelta(days=3)).date(),
        NOW.date(),
    ], 'twice in one evening is one day, and the days arrive oldest first'
    assert evidence.observed(payload)['build'] == NOW - dt.timedelta(hours=1), 'the latest answer is unchanged'


def test_a_day_the_backend_dated_ahead_of_now_is_dropped():
    """Nothing has happened tomorrow yet, so a row stamped then pays nothing."""
    assert evidence.dates_of([NOW + dt.timedelta(days=1)], NOW) == []
    assert evidence.dates_of([NOW], NOW) == [NOW.date().isoformat()]


def test_a_cache_from_either_side_of_this_still_reads():
    """A cache is written by whichever doit ran last, and both must read it.

    An older one wrote no days, and reads as no occurrences rather than as damage.
    A newer one writes a field the older one never asks for, and answering `last`
    must not depend on knowing every key in the entry.
    """
    older = {'pursuits': {'tasks': {'checked_at': NOW.isoformat(), 'last': (NOW - dt.timedelta(days=2)).isoformat()}}}
    assert evidence.observed(older)['tasks'] == NOW - dt.timedelta(days=2)
    assert evidence.occurrences(older) == {}

    newer = {'pursuits': {'tasks': {**older['pursuits']['tasks'], 'dates': [NOW.date().isoformat()], 'something_later': 'unknown'}}}
    assert evidence.observed(newer)['tasks'] == NOW - dt.timedelta(days=2)
    assert evidence.occurrences(newer)['tasks'] == [NOW.date()]
    assert evidence.problems(newer) == {}


def test_a_day_that_is_not_a_date_is_dropped_rather_than_fatal():
    payload = {
        'pursuits': {
            'tasks': {'checked_at': NOW.isoformat(), 'dates': ['not a date', NOW.date().isoformat()]},
            'read': {'checked_at': NOW.isoformat(), 'dates': None},
            'train': {'checked_at': NOW.isoformat(), 'dates': 'yesterday'},
        },
    }
    assert evidence.occurrences(payload) == {'tasks': [NOW.date()]}


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
