"""Tests for doit.skills — reading the library, grouping it, and describing one.

The fixture library is deliberately four awkward cases rather than four tidy
ones: a description carrying `e.g.`, a description whose unquoted `: ` makes the
frontmatter invalid YAML, a manual-only skill, and a name that does not parse as
`<verb>-<target>`. Each is something the real library actually contains.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from doit import skills
from doit.cli import app as cli_app

FIXTURE_DIR = Path(__file__).resolve().parent / 'fixtures' / 'skills'


@pytest.fixture(autouse=True)
def library(monkeypatch):
    monkeypatch.setattr(skills, 'SKILLS_DIR', FIXTURE_DIR)


def by_name() -> dict[str, skills.Skill]:
    return {skill.name: skill for skill in skills.load_skills()}


def test_load_reads_the_frontmatter_a_listing_needs():
    skill = by_name()['capture-note']

    assert skill.description.startswith('Capture a durable technical note.')
    assert skill.argument_hint == '<what to capture>'
    assert skill.auto is True
    assert skill.strict is True


def test_disable_model_invocation_is_read_as_manual_only():
    """The flag also drops the description from context, so it is worth showing."""
    assert by_name()['audit-repo'].auto is False


def test_an_invalid_yaml_description_still_loads():
    """Claude Code tolerates an unquoted `: `; dropping the skill would hide it."""
    skill = by_name()['learn-explain']

    assert skill.strict is False
    assert skill.description.startswith('Explain content Chris hands over.')
    assert skill.argument_hint == '[content]'


def test_summary_does_not_end_at_an_abbreviation():
    """`e.g.` is why this cannot reuse render.first_sentence."""
    assert by_name()['capture-note'].summary == 'Capture a durable technical note.'


def test_the_group_comes_from_the_name_not_a_hardcoded_vocabulary():
    """The closed verb set lives in review-fleet; a copy here would drift."""
    names = by_name()

    assert names['capture-note'].group == 'capture'
    assert names['audit-repo'].group == 'audit'
    assert names['orphan'].group == ''


def test_sections_are_the_body_headings():
    assert by_name()['capture-note'].sections == ('Step 1 — Identify the tidbit', 'Step 2 — Write it')


def test_list_groups_by_verb_and_puts_an_unparseable_name_on_its_own(capsys):
    assert skills.cmd_list(None) == 0

    out = capsys.readouterr().out
    assert 'capture' in out
    assert 'ungrouped' in out
    assert 'orphan' in out


def test_list_reports_frontmatter_that_is_not_valid_yaml(capsys):
    assert skills.cmd_list(None) == 0

    err = capsys.readouterr().err
    assert 'learn-explain' in err
    assert 'not valid YAML' in err


# There is deliberately no test that the human list clips to the summary and
# `--full` does not. Both assertions were on rendered console output, so both
# were really about the terminal width: at 80 columns the clipped row hid the
# phrase the summary test asserted absent, and the test passed with the sentence
# splitter disabled entirely. Pinning a width would only have chosen which
# consumer's terminal the suite pretends to be. `summary` is tested directly
# above and carried in the JSON below; which of the two a row prints is a
# rendering detail, and a test asserting a detail stays the same is one that
# fails when the layout improves.


def test_list_json_carries_what_a_caller_would_filter_on(capsys):
    assert skills.cmd_list(None, as_json=True) == 0

    rows = {row['name']: row for row in json.loads(capsys.readouterr().out)}
    assert rows['audit-repo']['auto_invocable'] is False
    assert rows['learn-explain']['valid_yaml'] is False
    assert rows['capture-note']['group'] == 'capture'


def test_list_can_be_scoped_to_one_group(capsys):
    assert skills.cmd_list('capture') == 0

    out = capsys.readouterr().out
    assert 'capture-note' in out
    assert 'audit-repo' not in out


def test_an_unknown_group_is_a_usage_error_naming_the_ones_that_exist():
    """Exit 2, like `find --source`. Exit 1 cannot be told from a run that failed."""
    result = CliRunner().invoke(cli_app, ['claude', 'skills', 'list', '--group', 'nope'])

    assert result.exit_code == 2
    assert 'ungrouped' in result.output


def test_an_unknown_group_is_caught_before_json_is_emitted():
    """`--group nope --json` printed `[]` and exited 0 — the group read as empty."""
    result = CliRunner().invoke(cli_app, ['claude', 'skills', 'list', '--group', 'nope', '--json'])

    assert result.exit_code == 2
    assert '[]' not in result.output


def test_an_empty_library_does_not_turn_a_group_into_a_usage_error(tmp_path, monkeypatch, capsys):
    """On the work box every group is unknown; the explanation is the useful answer."""
    monkeypatch.setattr(skills, 'SKILLS_DIR', tmp_path / 'absent')

    assert skills.cmd_list('capture') == 0
    assert 'work box' in capsys.readouterr().out


def test_show_accepts_the_slash_form_a_user_would_type(capsys):
    """`/capture-note` is how the skill is invoked, so it is what gets pasted."""
    assert skills.cmd_show('/capture-note') == 0

    assert 'Capture a durable technical note' in capsys.readouterr().out


def test_show_names_the_steps_so_the_body_need_not_be_opened(capsys):
    assert skills.cmd_show('capture-note') == 0

    assert 'Step 2 — Write it' in capsys.readouterr().out


def test_an_unknown_skill_points_at_the_federated_search(capsys):
    assert skills.cmd_show('nope') == 1

    err = capsys.readouterr().err
    assert 'doit claude skills list' in err
    assert 'doit find nope' in err


def test_groups_counts_each_verb(capsys):
    assert skills.cmd_groups(as_json=True) == 0

    counts = {row['group']: row['skills'] for row in json.loads(capsys.readouterr().out)}
    assert counts == {'audit': 1, 'capture': 1, 'learn': 1, 'ungrouped': 1}


def test_a_machine_without_the_library_is_not_an_error(tmp_path, monkeypatch, capsys):
    """~/.claude is Syncthing-synced, so the work box has no skills at all."""
    monkeypatch.setattr(skills, 'SKILLS_DIR', tmp_path / 'absent')

    assert skills.cmd_list(None) == 0
    assert skills.load_skills() == []
    assert 'work box' in capsys.readouterr().out


def test_the_collection_lives_under_the_claude_namespace():
    """`doit skills` would collide with the skills `learning` stewards."""
    runner = CliRunner()

    assert runner.invoke(cli_app, ['claude', 'skills', 'list']).exit_code == 0
    assert runner.invoke(cli_app, ['skills', 'list']).exit_code == 2


def test_groups_is_a_namespace_not_a_bare_noun_that_lists():
    """The call `doit tools categories` already made — every node prints help bare."""
    runner = CliRunner()

    assert runner.invoke(cli_app, ['claude', 'skills', 'groups', 'list']).exit_code == 0
    assert 'list' in runner.invoke(cli_app, ['claude', 'skills', 'groups']).output


def test_search_is_not_a_verb_here():
    """Scoping the federated index is a filter: `doit find --source skill`."""
    result = CliRunner().invoke(cli_app, ['claude', 'skills', 'search', 'note'])

    assert result.exit_code == 2
