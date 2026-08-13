"""Tests for doit.digest — reading the usage table without sending the history.

The payload is the whole of what this module has to get right, so most of what
is below is about what cannot reach the prompt: a recorded command line, a field
added to `usage.Row` upstream, the date a row last ran. The rest covers the two
things a caller can tell apart — a reading that was stored, and one that could
not be taken because `claude` is absent.
"""

import dataclasses
import datetime as dt
import json
import subprocess

import pytest

from doit import digest
from doit import usage
from doit.index import Entry
from doit.observe import Invocation

TODAY = dt.date(2026, 8, 12)

SECRET = 'AKIAIOSFODNN7EXAMPLE'


def ran(command: str, when: str = '2026-08-10', host: str = 'archlinux') -> Invocation:
    return Invocation(when, host, command)


def tool(name: str, invocation: str) -> Entry:
    return Entry(source='tool', name=name, invocation=invocation)


def row(typed: str, count: int = 3, last: str = '2026-08-10') -> usage.Row:
    return usage.Row(typed=typed, sources=('tool',), names=(typed,), count=count, last=last)


def stored(directory, generated: str, text: str = 'a reading', machine: str = 'archlinux') -> digest.Digest:
    entry = digest.Digest(generated=generated, machine=machine, rows=2, days=90, text=text)
    return digest.append(digest.digest_path(directory, machine), entry)


def test_a_recorded_command_line_cannot_reach_the_prompt():
    """The join is what makes the payload safe: history contributes a count, never a line.

    A secret typed at a prompt is in the history this table is measured against.
    It must not be in the prompt, and it is not, because no `Row` field is built
    from `Invocation.command`.
    """
    history = (ran(f'aws configure set aws_secret_access_key {SECRET}'), ran('aws s3 ls'))
    rows = usage.measure([tool('aws', 'aws [command]')], history)

    prompt = digest.build_prompt(rows, TODAY, days=90)

    assert SECRET not in prompt
    assert 'configure' not in prompt
    assert '"typed":"aws"' in prompt
    assert '"count":2' in prompt


def test_a_field_added_to_a_row_upstream_does_not_join_the_payload():
    """The allowlist filters a serialized row, so a new field is dropped rather than sent."""

    @dataclasses.dataclass(frozen=True)
    class RowWithNewField(usage.Row):
        leaked: str = SECRET

    assembled = digest.row_payload(RowWithNewField(typed='rg', sources=('tool',), names=('rg',), count=9, last='2026-08-10'), TODAY)

    assert 'leaked' not in assembled
    assert SECRET not in json.dumps(assembled)


def test_the_payload_carries_the_allowlisted_fields_and_nothing_else():
    assembled = digest.row_payload(row('fd'), TODAY)

    assert tuple(assembled) == digest.PAYLOAD_FIELDS


def test_the_date_a_row_last_ran_stays_out_of_the_payload():
    """`days_since` answers the same question without saying which day you were at a keyboard."""
    assembled = digest.row_payload(row('fd', last='2026-07-13'), TODAY)

    assert 'last' not in assembled
    assert assembled['days_since'] == 30


def test_a_row_that_never_ran_is_representable_in_the_payload():
    """Never having run it is the answer the digest exists to surface."""
    assembled = digest.row_payload(row('sd', count=0, last=''), TODAY)

    assert assembled['count'] == 0
    assert assembled['days_since'] is None


def test_the_prompt_orders_the_table_by_frequency():
    """Most-reached-for first, so the tail the reading is about sinks to the bottom."""
    table = digest.payload([row('rare', count=1), row('common', count=40)], TODAY)

    assert [entry['typed'] for entry in table] == ['common', 'rare']


def test_the_prompt_names_the_threshold_it_was_built_with():
    """A digest naming cold rows is uninterpretable without the number that made them cold."""
    prompt = digest.build_prompt([row('fd')], TODAY, days=45)

    assert '45 days' in prompt


def test_every_tool_is_denied_so_the_session_cannot_open_the_history_file():
    """The payload guarantee is worth nothing if the session can go and read the source."""
    assert {'Bash', 'Read', 'Grep', 'Glob'} <= set(digest.DENIED_TOOLS)


def test_a_missing_claude_is_reported_as_a_failure(monkeypatch, tmp_path):
    """Not a silent skip: a scheduled run that quietly does nothing is undiagnosable."""
    monkeypatch.setattr(digest.shutil, 'which', lambda _: None)
    monkeypatch.setattr(digest.usage, 'measure', lambda: [row('fd')])

    code = digest.cmd_run(days=90, directory=tmp_path)

    assert code == 1
    assert digest.read_all(tmp_path) == []


def test_a_claude_that_fails_is_reported_with_its_own_error(monkeypatch):
    monkeypatch.setattr(digest.shutil, 'which', lambda _: '/usr/bin/claude')
    monkeypatch.setattr(
        digest.subprocess,
        'run',
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, stdout='', stderr='not logged in'),
    )

    with pytest.raises(digest.DigestFailed, match='not logged in'):
        digest.ask('anything')


def test_an_empty_answer_is_a_failure_rather_than_an_empty_digest(monkeypatch):
    monkeypatch.setattr(digest.shutil, 'which', lambda _: '/usr/bin/claude')
    monkeypatch.setattr(
        digest.subprocess,
        'run',
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout='   \n', stderr=''),
    )

    with pytest.raises(digest.DigestFailed, match='nothing'):
        digest.ask('anything')


def test_the_prompt_travels_on_stdin_never_in_argv(monkeypatch):
    """Two hundred rows is an argument list a shell refuses, and argv is world-readable."""
    seen = {}

    def spy(command, **kwargs):
        seen['command'] = command
        seen['input'] = kwargs.get('input')
        return subprocess.CompletedProcess(command, 0, stdout='a reading', stderr='')

    monkeypatch.setattr(digest.shutil, 'which', lambda _: '/usr/bin/claude')
    monkeypatch.setattr(digest.subprocess, 'run', spy)

    digest.ask('the whole table')

    assert seen['input'] == 'the whole table'
    assert 'the whole table' not in seen['command']


def test_a_run_stores_what_it_read_and_show_reads_it_back(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(digest.usage, 'measure', lambda: [row('fd'), row('rg')])
    monkeypatch.setattr(digest, 'ask', lambda prompt: 'You reach for fd constantly.')

    assert digest.cmd_run(days=90, directory=tmp_path) == 0
    capsys.readouterr()

    assert digest.cmd_show('', as_json=False, directory=tmp_path) == 0
    assert 'You reach for fd constantly.' in capsys.readouterr().out

    only = digest.read_all(tmp_path)[0]
    assert only.rows == 2
    assert only.days == 90


def test_show_never_reaches_the_network(monkeypatch, tmp_path, capsys):
    """A read must work on a machine with no claude at all, which is what proves it is a read."""
    stored(tmp_path, '2026-08-12T09:00:00+00:00', text='a reading')
    monkeypatch.setattr(digest.shutil, 'which', lambda _: None)
    monkeypatch.setattr(digest, 'ask', lambda prompt: pytest.fail('show asked the model'))

    assert digest.cmd_show('', as_json=False, directory=tmp_path) == 0
    assert 'a reading' in capsys.readouterr().out


def test_show_without_a_stored_reading_fails_rather_than_printing_nothing(tmp_path, capsys):
    assert digest.cmd_show('', as_json=False, directory=tmp_path) == 1
    assert 'doit kit digest run' in capsys.readouterr().err


def test_show_takes_the_newest_when_no_handle_is_given(tmp_path, capsys):
    stored(tmp_path, '2026-06-01T09:00:00+00:00', text='the old one')
    stored(tmp_path, '2026-08-12T09:00:00+00:00', text='the new one')

    digest.cmd_show('', as_json=False, directory=tmp_path)

    assert 'the new one' in capsys.readouterr().out


def test_a_date_addresses_the_reading_taken_that_day(tmp_path, capsys):
    """The date printed above a digest is the handle that gets it back."""
    stored(tmp_path, '2026-06-01T09:00:00+00:00', text='the old one')
    stored(tmp_path, '2026-08-12T09:00:00+00:00', text='the new one')

    assert digest.cmd_show('2026-06-01', as_json=False, directory=tmp_path) == 0
    assert 'the old one' in capsys.readouterr().out


def test_a_handle_naming_nothing_is_a_failure(tmp_path):
    stored(tmp_path, '2026-08-12T09:00:00+00:00')

    assert digest.cmd_show('2020-01-01', as_json=False, directory=tmp_path) == 1


def test_json_emits_the_stored_record_whole(tmp_path, capsys):
    stored(tmp_path, '2026-08-12T09:00:00+00:00', text='a reading')

    digest.cmd_show('', as_json=True, directory=tmp_path)

    assert json.loads(capsys.readouterr().out) == {
        'generated': '2026-08-12T09:00:00+00:00',
        'machine': 'archlinux',
        'rows': 2,
        'days': 90,
        'text': 'a reading',
    }


def test_every_machines_readings_are_merged_on_read(tmp_path):
    """One writer per file; the union is the record."""
    stored(tmp_path, '2026-08-11T09:00:00+00:00', text='from the mac', machine='macmini')
    stored(tmp_path, '2026-08-12T09:00:00+00:00', text='from arch', machine='archlinux')

    assert [entry.text for entry in digest.read_all(tmp_path)] == ['from the mac', 'from arch']


def test_a_malformed_line_does_not_make_the_rest_unreadable(tmp_path):
    stored(tmp_path, '2026-08-12T09:00:00+00:00', text='a reading')
    digest.digest_path(tmp_path, 'archlinux').open('a').write('{ half a line\n')

    assert [entry.text for entry in digest.read_all(tmp_path)] == ['a reading']


def test_a_kit_with_nothing_measurable_fails_rather_than_reading_an_empty_table(monkeypatch, tmp_path):
    monkeypatch.setattr(digest.usage, 'measure', lambda: [])
    monkeypatch.setattr(digest, 'ask', lambda prompt: pytest.fail('asked the model about an empty table'))

    assert digest.cmd_run(days=90, directory=tmp_path) == 1
