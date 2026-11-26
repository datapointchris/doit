"""The desk nudge's answers, and the interval they tune.

The service keeps every answer and computes the interval from all of them on each
read, so a changed rule reprices the same answers. The clock that counts toward
the interval is the desk's, and the Go watch keeps it.
"""

from collections import Counter
from collections.abc import Iterable
from enum import StrEnum

STARTING_INTERVAL_MINUTES = 30

# Each too soon multiplies the interval by this, and each too late divides it.
ANSWER_STEP = 1.1


class Answer(StrEnum):
    """What a person says of one nudge. Timing and bother are separate axes, so one nudge can take two.

    `good_timing` and `bothered` move no interval. They are stored with the
    others, for a rule that weighs when to interrupt.
    """

    GOOD_TIMING = 'good_timing'
    TOO_SOON = 'too_soon'
    TOO_LATE = 'too_late'
    BOTHERED = 'bothered'


TIMING_ANSWERS = (Answer.GOOD_TIMING, Answer.TOO_SOON, Answer.TOO_LATE)


def replaced_by(answer: str) -> list[str]:
    """The answers that `answer` replaces on its nudge, because a nudge holds one timing answer. bothered replaces none."""
    return [str(other) for other in TIMING_ANSWERS if other != answer] if answer in TIMING_ANSWERS else []


ANSWER_LABELS = {
    Answer.GOOD_TIMING: 'good timing',
    Answer.TOO_SOON: 'too soon',
    Answer.TOO_LATE: 'too late',
    Answer.BOTHERED: 'bothered me',
}


def answer_counts(answers: Iterable[str]) -> dict[str, int]:
    """How many of each answer were given, every answer named, zero included."""
    counted = Counter(answers)
    return {answer.value: counted[answer] for answer in Answer}


def interval_minutes(answers: Iterable[str]) -> float:
    """`STARTING_INTERVAL_MINUTES`, times `ANSWER_STEP` for each too soon and divided by it for each too late, to the hundredth of a minute.

    It has no floor or ceiling. The order of the answers does not matter, so a too
    soon and a too late cancel.
    """
    counted = Counter(answers)
    return round(STARTING_INTERVAL_MINUTES * ANSWER_STEP ** (counted[Answer.TOO_SOON] - counted[Answer.TOO_LATE]), 2)
