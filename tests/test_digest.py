"""Tests for doit.digest — reading the usage table without sending the history.

What reaches the model is what this module has to get right, and it has two
halves that fail independently. The payload half is about what cannot reach the
prompt: a recorded command line, a field added to `usage.Row` upstream, the date
a row last ran. The containment half is about the command that carries it — the
flags are the entire mechanism bounding what `claude` loads beside the payload,
and nothing about a successful reading reveals that one went missing. So they are
asserted against the argv rather than against the answer.

The rest covers what a caller can tell apart: which way a reading failed, the
reading that was taken but could not be stored, and what a scheduled run hands
its scheduler.
"""

import dataclasses
import datetime as dt
import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from doit import digest
from doit import paths
from doit import usage
from doit import usage_table
from doit.cli import app as cli_app
from doit.index import Entry
from doit.observe import Invocation

TODAY = dt.date(2026, 8, 12)

SECRET = 'AKIAIOSFODNN7EXAMPLE'

FIXTURE_DIR = Path(__file__).resolve().parent / 'fixtures' / 'digest'


def recorded_reply(name: str) -> str:
    """A real `claude -p --output-format json` reply, captured on 2.1.293.

    The init frame is stripped of the machine it ran on. `answered` carries a
    second model in `modelUsage`, the shape that makes the envelope undercount.
    `refused` is an unknown model: exit 1, `is_error`, and the diagnosis in
    `result`.
    """
    return (FIXTURE_DIR / f'{name}.json').read_text(encoding='utf-8')


def answered(text: str) -> digest.Reply:
    return digest.Reply(text=text, session_id='', usage=digest.Usage())


def stand_in_claude(tmp_path, monkeypatch, reply: str, exit_code: int = 0, stderr: str = '') -> Path:
    """A `claude` first on PATH that answers `-p` with a recorded reply.

    Returns the file it writes the result variable to as it saw it, or `unset`,
    so a test can ask whether the session could have written the run's result.
    """
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / 'reply.json').write_text(reply)
    (bin_dir / 'reply.err').write_text(stderr)
    seen = tmp_path / 'claude-saw-result-file'
    binary = bin_dir / 'claude'
    binary.write_text(
        '#!/bin/sh\n'
        'if [ "$1" = --version ]; then echo "2.1.293 (Claude Code)"; exit 0; fi\n'
        'cat > /dev/null\n'
        f'printf %s "${{{digest.RESULT_FILE_ENV}-unset}}" > {shlex.quote(str(seen))}\n'
        f'cat {shlex.quote(str(bin_dir / "reply.err"))} >&2\n'
        f'cat {shlex.quote(str(bin_dir / "reply.json"))}\n'
        f'exit {exit_code}\n'
    )
    binary.chmod(0o755)
    monkeypatch.setenv('PATH', str(bin_dir), prepend=os.pathsep)
    return seen


def ran(command: str, when: str = '2026-08-10', host: str = 'archlinux') -> Invocation:
    return Invocation(when, host, command)


def tool(name: str, invocation: str) -> Entry:
    return Entry(source='tool', name=name, invocation=invocation)


def row(typed: str, count: int = 3, last: str = '2026-08-10') -> usage.Row:
    return usage.Row(typed=typed, sources=('tool',), names=(typed,), count=count, last=last)


def host(name: str, through: str = '2026-08-10', rows: tuple[usage.Row, ...] = (), commands: int = 10) -> usage_table.HostUsage:
    return usage_table.HostUsage(host=name, through=through, commands=commands, rows=rows or (row('fd'), row('rg')))


def exported(
    directory: Path,
    machine: str = 'archlinux',
    hosts: tuple[usage_table.HostUsage, ...] = (),
    generated: str = '2026-08-12T09:00:00+00:00',
    history: str = 'atuin',
) -> Path:
    """One machine's usage table in ``directory``, holding its own host unless told otherwise."""
    table = usage_table.UsageTable(machine=machine, generated=generated, history=history, hosts=hosts or (host(machine),))
    return usage_table.write(directory, table)


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
        return subprocess.CompletedProcess(command, 0, stdout=recorded_reply('answered'), stderr='')

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

    prompt = digest.build_prompt(rows, TODAY, days=90, history_ends=['2026-08-12'])

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
    prompt = digest.build_prompt([row('fd')], TODAY, days=45, history_ends=['2026-08-12'])

    assert '45 days' in prompt


def test_the_prompt_says_how_far_back_each_history_stops():
    """The rows carry no host, so a row typed only on a stalled desk reads as cold as the stall is long."""
    prompt = digest.build_prompt([row('fd')], TODAY, days=90, history_ends=['2026-07-04', '2026-08-12'])

    assert 'history stops: 0, 39.' in prompt
    assert '2026-07-04' not in prompt


def test_every_tool_is_denied_so_the_session_cannot_open_the_history_file():
    """The payload guarantee is worth nothing if the session can go and read the source."""
    assert {'Bash', 'Read', 'Grep', 'Glob'} <= set(digest.DENIED_TOOLS)


def test_a_missing_claude_is_reported_as_a_failure(monkeypatch, tmp_path):
    """Not a silent skip: a scheduled run that quietly does nothing is undiagnosable."""
    monkeypatch.setattr(digest.shutil, 'which', lambda _: None)
    exported(tmp_path)

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


def frames_with_result_text(text: str) -> str:
    frames = json.loads(recorded_reply('answered'))
    for frame in frames:
        if frame['type'] == 'result':
            frame['result'] = text
    return json.dumps(frames)


@pytest.mark.parametrize('stdout', ['   \n', frames_with_result_text('  \n')], ids=['nothing printed', 'blank result'])
def test_an_empty_answer_is_a_failure_rather_than_an_empty_digest(monkeypatch, stdout):
    monkeypatch.setattr(digest.shutil, 'which', lambda _: '/usr/bin/claude')
    monkeypatch.setattr(
        digest.subprocess,
        'run',
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=stdout, stderr=''),
    )

    with pytest.raises(digest.DigestFailed) as empty:
        digest.ask('anything')

    assert empty.value.reason is digest.Failure.EMPTY


def test_a_reply_with_no_result_frame_is_its_own_failure(monkeypatch):
    """A CLI whose output format moved wants reading, not retrying, so it is not an empty answer."""
    frames = [frame for frame in json.loads(recorded_reply('answered')) if frame['type'] != 'result']
    monkeypatch.setattr(digest.shutil, 'which', lambda _: '/usr/bin/claude')
    monkeypatch.setattr(
        digest.subprocess,
        'run',
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=json.dumps(frames), stderr=''),
    )

    with pytest.raises(digest.DigestFailed) as unread:
        digest.ask('anything')

    assert unread.value.reason is digest.Failure.NO_RESULT


def test_the_result_frame_is_found_wherever_it_sits():
    """Taking the last element works only while nothing is appended after the result."""
    frames = json.loads(recorded_reply('answered'))

    reply = digest.read_reply(json.dumps(frames[::-1]))

    assert reply is not None
    assert reply.text.startswith('You reach for rg and fd')


def test_the_envelope_counts_only_where_no_model_is_broken_out():
    """Without a breakdown the envelope is the whole measurement, and dropping it records the call as free."""
    frame = {
        'type': 'result',
        'result': 'ok',
        'usage': {'input_tokens': 2, 'cache_creation_input_tokens': 11164, 'cache_read_input_tokens': 12407, 'output_tokens': 4527},
    }

    reply = digest.read_reply(json.dumps([frame]))

    assert reply is not None
    assert reply.usage == digest.Usage(input=2, cache_creation=11164, cache_read=12407, output=4527)


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


def test_the_command_asks_for_the_reply_as_frames(invocation):
    """Plain text carries the answer alone, and every run would then fail with no result frame.

    A stand-in replies in frames whatever it is asked, so only the argv shows the flag went missing.
    """
    assert value_after(invocation['command'], '--output-format') == 'json'


def test_the_command_replaces_the_output_style_for_the_call(invocation):
    """What gets stored is what gets read back months later, so its shape is the contract."""
    assert value_after(invocation['command'], '--system-prompt') == digest.SYSTEM_PROMPT


def test_the_call_runs_outside_the_directory_it_was_invoked_from(invocation):
    """`doit` runs from wherever you are standing, and the reading must not depend on that."""
    assert invocation['cwd'] not in (None, str(Path.cwd()))


def test_a_run_stores_what_it_read_and_show_reads_it_back(monkeypatch, tmp_path, capsys):
    exported(tmp_path)
    monkeypatch.setattr(digest, 'ask', lambda prompt: answered('You reach for fd constantly.'))

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
    exported(locked)
    locked.chmod(0o555)
    monkeypatch.setattr(digest, 'ask', lambda prompt: answered('A reading that cost a request.'))

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


@pytest.fixture
def scheduled(monkeypatch, tmp_path) -> Path:
    """A run as fleet's scheduler starts one: the result path set, and nothing there yet."""
    result_file = tmp_path / 'run' / 'result.json'
    result_file.parent.mkdir()
    monkeypatch.setenv(digest.RESULT_FILE_ENV, str(result_file))
    exported(tmp_path / 'state')
    monkeypatch.setattr(digest, 'machine_name', lambda: 'archlinux')
    return result_file


def test_a_scheduled_run_hands_the_scheduler_its_session_and_tokens(monkeypatch, tmp_path, scheduled):
    """The run record is the only place the call's cost lands, and the ledger reads it there.

    The recorded reply reached two models, so the tokens are both models' sum.
    The frame's own `usage` holds the main model alone, 2 uncached input of 439.
    """
    stand_in_claude(tmp_path, monkeypatch, recorded_reply('answered'))

    assert digest.cmd_run(days=90, directory=tmp_path / 'state') == 0

    result = json.loads(scheduled.read_text())
    assert digest.read_all(tmp_path / 'state')[0].generated in result['summary']
    assert result['session_id'] == '2f02c17c-327c-400b-97c9-294b4304aaa3'
    assert result['usage'] == {'input': 439, 'cache_creation': 10317, 'cache_read': 401, 'output': 625}


def test_a_refused_call_still_hands_over_its_session_and_its_reason(monkeypatch, tmp_path, scheduled):
    """A failed run is the one a reader most needs explained, and the call still opened a session.

    The reason is the frame's sentence rather than stderr's error code, because
    that is where a refusal writes the part a person can act on.
    """
    stand_in_claude(
        tmp_path,
        monkeypatch,
        recorded_reply('refused'),
        exit_code=1,
        stderr='[claude-code:unrecognized_model] {"model":"claude-nonexistent-9","query_source":"sdk"}\n',
    )

    assert digest.cmd_run(days=90, directory=tmp_path / 'state') == 1

    result = json.loads(scheduled.read_text())
    assert 'issue with the selected model' in result['summary']
    assert 'unrecognized_model' not in result['summary']
    assert result['session_id'] == '4578dc55-7785-44ae-bf9e-1fa24c5cc38e'
    assert result['usage'] == {'input': 0, 'cache_creation': 0, 'cache_read': 0, 'output': 0}
    assert digest.read_all(tmp_path / 'state') == []


def test_the_session_never_sees_where_the_result_goes(monkeypatch, tmp_path, scheduled):
    """Only the run owns its result. A session holding the path could write there first."""
    seen = stand_in_claude(tmp_path, monkeypatch, recorded_reply('answered'))

    digest.cmd_run(days=90, directory=tmp_path / 'state')

    assert seen.read_text() == 'unset'


def test_a_result_already_written_by_something_else_fails_the_run(monkeypatch, tmp_path, scheduled):
    """Replacing it would hide whatever wrote it, and appending leaves two documents."""
    stand_in_claude(tmp_path, monkeypatch, recorded_reply('answered'))
    scheduled.write_text('{"summary": "written by something else"}')

    assert digest.cmd_run(days=90, directory=tmp_path / 'state') == 1
    assert json.loads(scheduled.read_text()) == {'summary': 'written by something else'}


def test_a_run_by_hand_adds_only_its_reading(monkeypatch, tmp_path):
    """With `$FLEET_RESULT_FILE` unset, no result file lands in the working directory or beside the reading."""
    monkeypatch.delenv(digest.RESULT_FILE_ENV, raising=False)
    state = tmp_path / 'state'
    exported(state)
    monkeypatch.setattr(digest, 'machine_name', lambda: 'archlinux')
    stand_in_claude(tmp_path, monkeypatch, recorded_reply('answered'))
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)
    before = set(state.iterdir())

    assert digest.cmd_run(days=90, directory=state) == 0

    assert list(work.iterdir()) == []
    assert set(state.iterdir()) - before == {digest.digest_path(state, 'archlinux')}


def test_a_run_names_each_host_and_how_far_its_history_reaches(monkeypatch, tmp_path, scheduled):
    """Labeled with the table's own date instead, a host whose sync stalled would read as current."""
    state = tmp_path / 'state'
    exported(state, 'archlinux', (host('archlinux'), host('mbp', through='2026-09-01')), generated='2026-10-08T09:00:00+00:00')
    exported(state, 'scheduler-lxc', (host('scheduler-lxc', through='2026-10-07'),), generated='2026-10-07T09:00:00+00:00', history='zsh')
    monkeypatch.setattr(digest, 'ask', lambda prompt: answered('A reading.'))

    assert digest.cmd_run(days=90, directory=state) == 0

    summary = json.loads(scheduled.read_text())['summary']
    assert "archlinux through 2026-08-10, mbp through 2026-09-01 from archlinux's atuin export" in summary
    assert "scheduler-lxc through 2026-10-07 from scheduler-lxc's zsh export" in summary
    reading = digest.read_all(state)[0]
    assert reading.exports['archlinux'].hosts == {'archlinux': '2026-08-10', 'mbp': '2026-09-01'}
    assert reading.histories() == {'archlinux': '2026-08-10', 'mbp': '2026-09-01', 'scheduler-lxc': '2026-10-07'}


def test_list_prints_how_far_each_hosts_history_reaches(tmp_path, capsys):
    reading = digest.Digest(
        generated='2026-10-08T09:00:00+00:00',
        machine='archlinux',
        rows=2,
        days=90,
        text='a reading',
        exports={'archlinux': digest.ExportUsed('2026-10-08T09:00:00+00:00', 'atuin', {'archlinux': '2026-10-08', 'mbp': '2026-09-01'})},
    )
    digest.append(digest.digest_path(tmp_path, 'archlinux'), reading)

    assert digest.cmd_list(as_json=False, directory=tmp_path) == 0

    assert 'over archlinux through 2026-10-08, mbp through 2026-09-01' in ' '.join(capsys.readouterr().out.split())


def prompt_table(prompt: str) -> dict[str, dict]:
    """The table a prompt carries, keyed by what is typed."""
    line = prompt.split('TABLE\n', 1)[1].split('\n', 1)[0]
    return {entry['typed']: entry for entry in json.loads(line)}


def test_every_hosts_counts_reach_the_prompt_summed(monkeypatch, tmp_path):
    """One row per thing typed, whichever hosts typed it, and a row one host holds keeps that host's count."""
    seen: dict = {}
    on_archlinux = (
        usage.Row(typed='rg', sources=('tool',), names=('ripgrep',), count=5, last='2026-08-01'),
        usage.Row(typed='pacman', sources=('tool',), names=('pacman',), count=2, last='2026-07-01'),
    )
    on_mbp = (usage.Row(typed='rg', sources=('alias',), names=('rg',), count=3, last='2026-08-09'),)
    exported(tmp_path, 'archlinux', (host('archlinux', rows=on_archlinux), host('mbp', rows=on_mbp)))

    def capture(prompt):
        seen['prompt'] = prompt
        return answered('A reading.')

    monkeypatch.setattr(digest, 'ask', capture)

    assert digest.cmd_run(days=90, directory=tmp_path) == 0

    table = prompt_table(seen['prompt'])
    newer_run = (dt.date.today() - dt.date(2026, 8, 9)).days
    assert table['rg'] == {'typed': 'rg', 'sources': ['alias', 'tool'], 'count': 8, 'days_since': newer_run}
    assert table['pacman']['count'] == 2


def test_a_run_with_no_export_spends_no_request(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(digest, 'ask', lambda prompt: pytest.fail('a run with nothing to read asked the model'))

    assert digest.cmd_run(days=90, directory=tmp_path) == 1
    assert 'doit kit digest export' in capsys.readouterr().err


def test_an_unreadable_export_fails_the_run_before_any_request(monkeypatch, tmp_path, scheduled):
    """Reading the rest would report every host the bad file held as idle."""
    state = tmp_path / 'state'
    usage_table.table_path(state, 'mbp').write_text('{"half": ')
    monkeypatch.setattr(digest, 'ask', lambda prompt: pytest.fail('a run over an unreadable export asked the model'))

    assert digest.cmd_run(days=90, directory=state) == 1

    assert 'usage-table-mbp.json is not JSON' in json.loads(scheduled.read_text())['summary']
    assert digest.read_all(state) == []


def test_a_share_that_has_not_arrived_reaches_the_scheduler_as_the_reason(monkeypatch, tmp_path, scheduled):
    """Created on demand, the directory would collect readings that never leave the box."""
    missing = tmp_path / 'share' / 'doit-state'
    monkeypatch.setenv(paths.JOURNAL_DIR_ENV, str(missing))
    monkeypatch.setattr(paths, 'JOURNAL_DIR', missing)

    assert digest.cmd_run(days=90) == 1

    assert '$DOIT_JOURNAL_DIR' in json.loads(scheduled.read_text())['summary']
    assert not missing.exists()


def test_list_refuses_a_named_journal_directory_that_is_missing(monkeypatch, tmp_path):
    """Read as empty, it would print `No reading stored yet.` where the share has not arrived."""
    missing = tmp_path / 'share' / 'doit-state'
    monkeypatch.setenv(paths.JOURNAL_DIR_ENV, str(missing))
    monkeypatch.setattr(paths, 'JOURNAL_DIR', missing)

    ran = CliRunner().invoke(cli_app, ['kit', 'digest', 'list'])

    assert ran.exit_code == 1
    assert '$DOIT_JOURNAL_DIR' in ran.output
    assert 'pursuits.yml' not in ran.output


@pytest.fixture
def kit(monkeypatch):
    """Two cataloged tools, standing in for this machine's index."""
    monkeypatch.setattr(digest, 'build_index', lambda: [tool('fd', 'fd [pattern]'), tool('rg', 'rg [pattern]')])
    monkeypatch.setattr(digest, 'machine_name', lambda: 'archlinux')


def test_an_export_holds_every_host_atuin_holds(monkeypatch, tmp_path, kit):
    """atuin syncs, so this history holds the other desks' commands too."""
    history = (ran('rg a', '2026-10-08', 'archlinux'), ran('rg b', '2026-09-01', 'mbp'), ran('fd c', '2026-10-07', 'macmini'))
    monkeypatch.setattr(digest.observe, 'atuin_invocations', lambda: history)

    assert digest.cmd_export(tmp_path) == 0

    (written,) = usage_table.read_all(tmp_path).tables
    assert (written.machine, written.history) == ('archlinux', 'atuin')
    assert {entry.host: entry.through for entry in written.hosts} == {
        'archlinux': '2026-10-08',
        'macmini': '2026-10-07',
        'mbp': '2026-09-01',
    }


def test_a_zsh_export_holds_this_machine_alone(monkeypatch, tmp_path, kit):
    """zsh records no host, so every row is this box's and is tagged as such."""
    history_file = tmp_path / 'history'
    history_file.write_text(': 1786153106:0;rg needle\n')
    monkeypatch.setattr(digest.observe, 'HISTORY', history_file)
    monkeypatch.setattr(digest.observe, 'machine_name', lambda: 'archlinux')

    assert digest.cmd_export(tmp_path / 'state') == 0

    (written,) = usage_table.read_all(tmp_path / 'state').tables
    assert written.history == 'zsh'
    assert [entry.host for entry in written.hosts] == ['archlinux']


def test_an_empty_history_writes_no_table(tmp_path, kit, capsys):
    """A table of zeros would read as every row gone unused."""
    assert digest.cmd_export(tmp_path) == 1

    assert usage_table.read_all(tmp_path) == usage_table.Found([], [])
    assert 'No shell history to export' in capsys.readouterr().err


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
            'model': '',
            'claude_version': '',
            'exports': {},
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
        # Written before the attribution fields existed, and still readable.
        'model': '',
        'claude_version': '',
        'exports': {},
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


def test_a_kit_with_nothing_measurable_fails_rather_than_reading_an_empty_table(monkeypatch, tmp_path, capsys):
    exported(tmp_path, hosts=(usage_table.HostUsage(host='archlinux', through='2026-08-10', commands=4, rows=()),))
    monkeypatch.setattr(digest, 'ask', lambda prompt: pytest.fail('asked the model about an empty table'))

    assert digest.cmd_run(days=90, directory=tmp_path) == 1
    assert 'Nothing measurable' in capsys.readouterr().err


def fake_claude(tmp_path, monkeypatch, version_line: str, exit_code: int = 0) -> None:
    """A `claude` on PATH answering --version from the given line."""
    binary = tmp_path / 'bin' / 'claude'
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text(f'#!/bin/sh\nprintf "%s\\n" {version_line!r}\nexit {exit_code}\n')
    binary.chmod(0o755)
    monkeypatch.setenv('PATH', str(binary.parent), prepend=False)


def test_claude_version_keeps_the_number_and_drops_the_product_name(tmp_path, monkeypatch):
    """`claude --version` says "2.1.238 (Claude Code)" and the rest of the line
    is the product, which every record would then carry."""
    fake_claude(tmp_path, monkeypatch, '2.1.238 (Claude Code)')

    assert digest.claude_version() == '2.1.238'


def test_claude_version_is_empty_when_the_cli_will_not_answer(tmp_path, monkeypatch):
    """A reading is worth more than its attribution, so this never raises."""
    fake_claude(tmp_path, monkeypatch, 'broken', exit_code=1)

    assert digest.claude_version() == ''


def test_claude_version_is_empty_with_no_claude_on_path(tmp_path, monkeypatch):
    monkeypatch.setenv('PATH', str(tmp_path / 'empty'))

    assert digest.claude_version() == ''


def test_a_record_written_before_the_fields_still_loads(tmp_path):
    """Every digest already stored carries neither, and losing them all to a
    schema addition would destroy the readings the field exists to compare."""
    stored(tmp_path, '2026-08-12T09:00:00+00:00', text='a reading')

    loaded = digest.read_all(tmp_path)

    assert len(loaded) == 1
    assert loaded[0].model == ''
    assert loaded[0].claude_version == ''


def test_a_stored_reading_records_the_cli_that_took_it(tmp_path, monkeypatch):
    """A reading that moved because the model changed is otherwise
    indistinguishable from one that moved because the kit did."""
    fake_claude(tmp_path, monkeypatch, '2.1.238 (Claude Code)')
    monkeypatch.setattr(digest, 'ask', lambda _: answered('a reading'))
    exported(tmp_path)
    monkeypatch.setattr(digest, 'machine_name', lambda: 'archlinux')

    assert digest.cmd_run(days=90, directory=tmp_path) == 0

    assert digest.read_all(tmp_path)[0].claude_version == '2.1.238'
