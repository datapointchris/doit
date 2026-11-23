"""The desk nudge's rules: the answers that a person gives a nudge, and the interval that they tune.

The service keeps every answer and computes the interval from all of them on each
read, so a changed rule reprices the same answers. Nothing here asks the service.
"""

from collections import Counter
from collections.abc import Iterable
from enum import StrEnum

STARTING_INTERVAL_MINUTES = 60

# Each too soon multiplies the interval by this, and each too late divides it.
ANSWER_STEP = 1.1


class Answer(StrEnum):
    """What a person says of one nudge. Timing and bother are separate axes, so one nudge can take two.

    `good_timing` and `bothered` move nothing yet. They are kept so that a later
    rule can weigh when to interrupt.
    """

    GOOD_TIMING = 'good_timing'
    TOO_SOON = 'too_soon'
    TOO_LATE = 'too_late'
    BOTHERED = 'bothered'


def answer_counts(answers: Iterable[str]) -> dict[str, int]:
    """How many of each answer were given, every answer named, zero included."""
    counted = Counter(answers)
    return {answer.value: counted[answer] for answer in Answer}


def interval_minutes(answers: Iterable[str]) -> float:
    """60 minutes, times 1.1 for each too soon and divided by 1.1 for each too late, to the hundredth of a minute.

    It has no floor or ceiling. The order of the answers does not matter, so a too
    soon and a too late cancel.
    """
    counted = Counter(answers)
    return round(STARTING_INTERVAL_MINUTES * ANSWER_STEP ** (counted[Answer.TOO_SOON] - counted[Answer.TOO_LATE]), 2)
