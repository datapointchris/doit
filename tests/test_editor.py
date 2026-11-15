"""Tests for doit.editor — the command line every `edit` verb runs."""

from pathlib import Path

import pytest

from doit import editor


@pytest.mark.parametrize(
    ('value', 'argv'),
    [
        ('code -w', ['code', '-w']),
        ("'/opt/My Editor/bin/ed' --wait", ['/opt/My Editor/bin/ed', '--wait']),
        ('   ', [editor.DEFAULT_EDITOR]),
    ],
)
def test_editor_runs_as_a_split_command_line(monkeypatch, value, argv):
    ran = []
    monkeypatch.setenv('EDITOR', value)
    monkeypatch.setattr(editor.subprocess, 'run', lambda command, **_: ran.append(command))
    editor.open_in_editor(Path('/tmp/doc.toml'))
    assert ran == [[*argv, '/tmp/doc.toml']]
