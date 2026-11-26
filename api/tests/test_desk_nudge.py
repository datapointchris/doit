"""The desk nudge's answers against the real service, in process."""

import uuid

FIRED_AT = '2026-11-22T14:00:00-05:00'


def answer(api, nudge_id: uuid.UUID, given: str) -> dict:
    sent = api.post(
        '/api/v1/desk-nudge/answers', json={'nudge_id': str(nudge_id), 'answer': given, 'fired_at': FIRED_AT, 'machine': 'desk'}
    )
    assert sent.status_code == 201, sent.text
    return sent.json()


def test_an_answer_moves_the_interval_and_too_soon_then_too_late_cancel(api):
    first, second = uuid.uuid4(), uuid.uuid4()
    assert api.get('/api/v1/desk-nudge').json()['interval_minutes'] == 30

    soon = answer(api, first, 'too_soon')
    resent = answer(api, first, 'too_soon')
    late = answer(api, second, 'too_late')

    assert soon['interval_minutes'] == 33
    assert (soon['added'], resent['added']) == (True, False)
    assert resent['stored'] == soon['stored'], 'a resend answers the row first stored'
    assert resent['interval_minutes'] == 33, 'a resend is stored once'
    assert late['interval_minutes'] == 30
    assert api.get('/api/v1/desk-nudge').json()['answer_counts'] == {'good_timing': 0, 'too_soon': 1, 'too_late': 1, 'bothered': 0}


def test_a_second_timing_answer_replaces_the_first_and_history_keeps_it(api):
    nudge = uuid.uuid4()
    answer(api, nudge, 'too_soon')
    bothered = answer(api, nudge, 'bothered')
    changed = answer(api, nudge, 'too_late')

    assert bothered['replaced'] == []
    assert changed['replaced'] == ['too_soon']
    assert changed['answer_counts'] == {'good_timing': 0, 'too_soon': 0, 'too_late': 1, 'bothered': 1}
    history = api.get('/api/v1/history/desk_nudge_answers', params={'key': str(nudge)}).json()
    assert [row['answer'] for row in history if row['valid_to']] == ['too_soon']
