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
