"""Tests for doit.digest — reading the usage table without sending the history.

What reaches the model is what this module has to get right, and it has two
halves that fail independently. The payload half is about what cannot reach the
prompt: a recorded command line, a field added to `usage.Row` upstream, the date
a row last ran. The containment half is about the command that carries it — the
flags are the entire mechanism bounding what `claude` loads beside the payload,
and nothing about a successful reading reveals that one went missing. So they are
asserted against the argv rather than against the answer.

The rest covers what a caller can tell apart: which of the four ways a reading
fails, and the reading that was taken but could not be stored.
"""

import dataclasses
import datetime as dt
import json
import subprocess
from pathlib import Path

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


def value_after(command: list[str], flag: str) -> str:
    """What `command` passes for `flag`, or '' when the flag is not there at all."""
    return command[command.index(flag) + 1] if flag in command else ''


@pytest.fixture
def invocation(monkeypatch) -> dict:
    """The call `ask` builds, captured instead of run.

    The subject is the command, not the answer: `claude` returns the same reading
    whether or not it was told to load nothing and open nothing, so a test that
    reads the result cannot see a dropped flag.
    """
    seen: dict = {}

    def spy(command, **kwargs):
        seen['command'] = command
        seen.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout='a reading', stderr='')

    monkeypatch.setattr(digest.shutil, 'which', lambda _: '/usr/bin/claude')
    monkeypatch.setattr(digest.subprocess, 'run', spy)
    digest.ask('the whole table')
    return seen


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


def test_an_absent_binary_and_a_refusal_are_different_failures(monkeypatch):
    """A machine missing claude wants installing; a session that ran and lost wants retrying."""
    monkeypatch.setattr(digest.shutil, 'which', lambda _: None)

    with pytest.raises(digest.DigestFailed) as absent:
        digest.ask('anything')

    assert absent.value.reason is digest.Failure.NOT_INSTALLED


def test_a_claude_that_fails_is_reported_with_its_own_error(monkeypatch):
    """The key carries the condition; the substring proves claude's own stderr reached the reader."""
    monkeypatch.setattr(digest.shutil, 'which', lambda _: '/usr/bin/claude')
    monkeypatch.setattr(
        digest.subprocess,
        'run',
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, stdout='', stderr='not logged in'),
    )

    with pytest.raises(digest.DigestFailed) as refused:
        digest.ask('anything')

    assert refused.value.reason is digest.Failure.FAILED
    assert 'not logged in' in str(refused.value)


def test_an_empty_answer_is_a_failure_rather_than_an_empty_digest(monkeypatch):
    monkeypatch.setattr(digest.shutil, 'which', lambda _: '/usr/bin/claude')
    monkeypatch.setattr(
        digest.subprocess,
        'run',
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout='   \n', stderr=''),
    )

    with pytest.raises(digest.DigestFailed) as empty:
        digest.ask('anything')

    assert empty.value.reason is digest.Failure.EMPTY


def test_a_call_that_never_answers_is_distinguishable_from_one_that_refused(monkeypatch):
    """A timeout is worth retrying and a refusal is not, so a caller has to tell them apart."""

    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd='claude', timeout=kwargs['timeout'])

    monkeypatch.setattr(digest.shutil, 'which', lambda _: '/usr/bin/claude')
    monkeypatch.setattr(digest.subprocess, 'run', hang)

    with pytest.raises(digest.DigestFailed) as expired:
        digest.ask('anything', timeout=12)

    assert expired.value.reason is digest.Failure.TIMED_OUT
    assert '12' in str(expired.value)


def test_every_failure_mode_has_wording_of_its_own():
    """A key with no entry raises rather than printing a blank line."""
    assert set(digest.FAILURE_TEXT) == set(digest.Failure)


def test_the_prompt_travels_on_stdin_never_in_argv(invocation):
    """Two hundred rows is an argument list a shell refuses, and argv is world-readable."""
    assert invocation['input'] == 'the whole table'
    assert 'the whole table' not in invocation['command']


def test_the_command_denies_every_tool_the_deny_list_names(invocation):
    """The constant is only a guarantee if it reaches the command that runs.

    Asserting the joined value rather than the flag alone: a deny list that
    arrives half-empty restricts nothing and looks identical from the outside.
    """
    assert value_after(invocation['command'], '--disallowed-tools') == ','.join(digest.DENIED_TOOLS)


def test_the_command_loads_none_of_this_machines_configuration(invocation):
    """Without this flag the session's own memory rides along beside the payload.

    A `claude -p` run injects the user-level `CLAUDE.md` from the home directory,
    which is personal, unbounded, and nothing to do with a table of command
    counts. Dropping the flag changes nothing a reading looks like.
    """
    assert '--safe-mode' in invocation['command']


def test_the_command_replaces_the_output_style_for_the_call(invocation):
    """What gets stored is what gets read back months later, so its shape is the contract."""
    assert value_after(invocation['command'], '--system-prompt') == digest.SYSTEM_PROMPT


def test_the_call_runs_outside_the_directory_it_was_invoked_from(invocation):
    """`doit` runs from wherever you are standing, and the reading must not depend on that."""
    assert invocation['cwd'] not in (None, str(Path.cwd()))


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


def test_a_reading_that_cannot_be_stored_still_reaches_stdout(monkeypatch, tmp_path, capsys):
    """The request is spent either way, and only one of the two losses is recoverable.

    An unwritable state directory must not take the answer down with the file:
    the reading is on screen, so a failed write costs a record that can be
    re-taken and nothing that cannot.
    """
    locked = tmp_path / 'locked'
    locked.mkdir()
    locked.chmod(0o555)
    monkeypatch.setattr(digest.usage, 'measure', lambda: [row('fd')])
    monkeypatch.setattr(digest, 'ask', lambda prompt: 'A reading that cost a request.')

    try:
        code = digest.cmd_run(days=90, directory=locked)
    finally:
        locked.chmod(0o755)

    captured = capsys.readouterr()
    assert 'A reading that cost a request.' in captured.out
    assert code == 1
    # The console folds a long path at whatever width the consumer has, so the
    # path is compared with the whitespace taken back out of it.
    assert str(digest.digest_path(locked, digest.machine_name())).replace(' ', '') in ''.join(captured.err.split())


def test_show_never_reaches_the_network(monkeypatch, tmp_path, capsys):
    """A read must work on a machine with no claude at all, which is what proves it is a read."""
    stored(tmp_path, '2026-08-12T09:00:00+00:00', text='a reading')
    monkeypatch.setattr(digest.shutil, 'which', lambda _: None)
    monkeypatch.setattr(digest, 'ask', lambda prompt: pytest.fail('show asked the model'))

    assert digest.cmd_show('', as_json=False, directory=tmp_path) == 0
    assert 'a reading' in capsys.readouterr().out


def test_show_without_a_stored_reading_points_at_the_verb_that_takes_one(tmp_path, capsys):
    """With nothing stored, spending a request is the only move there is."""
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


def test_a_mistyped_handle_names_the_handles_that_do_resolve(tmp_path, capsys):
    """A typo is not worth a request, so the answer is what was already paid for.

    Pointing at `run` here spends an API call to recover from a wrong date, and
    the handles it could have named are the one thing that fixes it.
    """
    stored(tmp_path, '2026-06-01T09:00:00+00:00')
    stored(tmp_path, '2026-08-12T09:00:00+00:00')

    assert digest.cmd_show('2020-01-01', as_json=False, directory=tmp_path) == 1

    reported = ''.join(capsys.readouterr().err.split())
    assert '2026-06-01T09:00:00+00:00' in reported
    assert '2026-08-12T09:00:00+00:00' in reported
    assert 'doitkitdigestrun' not in reported


def test_a_miss_names_the_newest_handles_and_the_verb_holding_the_rest(tmp_path, capsys):
    """An error is read at a glance, so a long record points onward instead of printing itself."""
    for day in range(1, digest.HANDLES_ON_MISS + 2):
        stored(tmp_path, f'2026-06-{day:02d}T09:00:00+00:00')

    digest.cmd_show('2020-01-01', as_json=False, directory=tmp_path)

    reported = ''.join(capsys.readouterr().err.split())
    assert '2026-06-01T09:00:00+00:00' not in reported
    assert f'2026-06-{digest.HANDLES_ON_MISS + 1:02d}T09:00:00+00:00' in reported
    assert 'doitkitdigestlist' in reported


def test_list_names_every_stored_reading_as_a_handle_show_takes(tmp_path, capsys):
    """Stored handles are otherwise unreachable: `show` needs one and nothing prints them."""
    stored(tmp_path, '2026-06-01T09:00:00+00:00', text='the old one')
    stored(tmp_path, '2026-08-12T09:00:00+00:00', text='the new one', machine='laptop')

    assert digest.cmd_list(as_json=False, directory=tmp_path) == 0

    listed = ''.join(capsys.readouterr().out.split())
    assert '2026-06-01T09:00:00+00:00' in listed
    assert '2026-08-12T09:00:00+00:00' in listed
    assert 'laptop' in listed


def test_list_emits_every_record_whole_as_json(tmp_path, capsys):
    stored(tmp_path, '2026-08-12T09:00:00+00:00', text='a reading')

    assert digest.cmd_list(as_json=True, directory=tmp_path) == 0

    assert json.loads(capsys.readouterr().out) == [
        {
            'generated': '2026-08-12T09:00:00+00:00',
            'machine': 'archlinux',
            'rows': 2,
            'days': 90,
            'text': 'a reading',
        }
    ]


def test_list_with_nothing_stored_is_an_empty_array_not_a_hint(tmp_path, capsys):
    """--json is parsed by whatever asked for it, so it is an array in every state."""
    assert digest.cmd_list(as_json=True, directory=tmp_path) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_list_never_reaches_the_network(monkeypatch, tmp_path, capsys):
    """It answers the question `show` needs answered, so it must work where `run` cannot."""
    stored(tmp_path, '2026-08-12T09:00:00+00:00')
    monkeypatch.setattr(digest.shutil, 'which', lambda _: None)
    monkeypatch.setattr(digest, 'ask', lambda prompt: pytest.fail('list asked the model'))

    assert digest.cmd_list(as_json=False, directory=tmp_path) == 0
    assert '2026-08-12T09:00:00+00:00' in ''.join(capsys.readouterr().out.split())


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
    stored(tmp_path, '2026-08-11T09:00:00+00:00', text='from the laptop', machine='laptop')
    stored(tmp_path, '2026-08-12T09:00:00+00:00', text='from the desktop', machine='desktop')

    assert [entry.text for entry in digest.read_all(tmp_path)] == ['from the laptop', 'from the desktop']


def test_a_malformed_line_does_not_make_the_rest_unreadable(tmp_path):
    stored(tmp_path, '2026-08-12T09:00:00+00:00', text='a reading')
    digest.digest_path(tmp_path, 'archlinux').open('a').write('{ half a line\n')

    assert [entry.text for entry in digest.read_all(tmp_path)] == ['a reading']


def test_a_kit_with_nothing_measurable_fails_rather_than_reading_an_empty_table(monkeypatch, tmp_path):
    monkeypatch.setattr(digest.usage, 'measure', lambda: [])
    monkeypatch.setattr(digest, 'ask', lambda prompt: pytest.fail('asked the model about an empty table'))

    assert digest.cmd_run(days=90, directory=tmp_path) == 1
