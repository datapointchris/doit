"""Tests for doit.review — the register's schema, and the schedule status derived from it.

The commands are tested against the service in `api/tests`. What is tested here
is what both sides call: the parser and the derived status.
"""

import datetime as dt
import re
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from doit import review
from doit.service_commands import review as review_commands

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'review' / 'register.yml'
TODAY = dt.date(2026, 8, 4)


def registered(text: str | None = None) -> dict[str, dict]:
    return review.register_from_document(yaml.safe_load(FIXTURE.read_text() if text is None else text))


def test_the_register_reads_each_item_by_id():
    items = registered()['items']
    assert set(items) == {'never-done', 'overdue-item', 'fresh-item'}
    assert items['overdue-item']['cadence'] == '1w'
    assert items['fresh-item']['show'] == 'echo fresh'


def test_unknown_item_field_is_refused():
    """A misspelled field would otherwise drop what the item declared without a word."""
    with pytest.raises(review.RegisterError, match='cadance'):
        registered('items:\n  typo:\n    cadance: 1w\n')


def test_statuses_never_done_reads_as_most_urgent():
    rows = review.statuses_of(registered(), {}, TODAY)
    assert rows[0]['overdue'] is None
    assert {row['id']: row for row in rows}['never-done']['due'] is True


def test_statuses_orders_most_overdue_first():
    done = {'overdue-item': (TODAY - dt.timedelta(days=30)).isoformat(), 'fresh-item': TODAY.isoformat()}

    rows = review.statuses_of(registered(), done, TODAY)

    assert [row['id'] for row in rows] == ['never-done', 'overdue-item', 'fresh-item']
    assert rows[1]['overdue'] == 23  # 30 days since done, 7-day cadence
    assert rows[2]['overdue'] == -28  # done today, 28-day cadence
    assert rows[1]['due'] is True
    assert rows[2]['due'] is False


def test_the_template_names_every_field_an_item_may_declare():
    """The template is the whole schema documentation — it is what a fresh
    install writes and what the file being hand-edited carries at its head."""
    for field in [*review.ITEM_FIELDS, *review.BLOCKS]:
        assert re.search(rf'\b{field}\b', review.TEMPLATE), f'{field} is read by review and absent from its template'


def test_parse_duration_minutes():
    assert review.parse_duration_minutes('4h') == 240
    assert review.parse_duration_minutes('90m') == 90
    assert review.parse_duration_minutes('4') == 240, 'a bare number means hours'
    assert review.parse_duration_minutes(' 30m ') == 30
    assert review.parse_duration_minutes('soon') is None
    assert review.parse_duration_minutes('') is None


def test_nudge_every_rejects_an_unparsable_duration():
    """A bad value is a usage error, which typer.BadParameter is what earns."""
    result = CliRunner().invoke(review_commands.app, ['nudge-every', 'soon'])

    assert result.exit_code == 2
    assert 'expected a duration' in result.output
