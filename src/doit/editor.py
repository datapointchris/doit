"""Opening a file in the user's editor, for every command that edits one.

Each `edit` verb opens what it owns through `open_in_editor`, so `$EDITOR`
unset or empty lands every command on the same program. An empty or blank
value is treated as unset because there is no program named by it to run.

`$EDITOR` is a command line rather than a program name, as `git` and most
Unix tools read it, so `code -w` or `emacsclient -t` is split with shell-word
rules into the program and its arguments before the file is appended.
"""

import os
import shlex
import subprocess
from pathlib import Path

DEFAULT_EDITOR = 'nvim'


def editor_command() -> str:
    return os.environ.get('EDITOR', '').strip() or DEFAULT_EDITOR


def open_in_editor(path: Path) -> None:
    subprocess.run([*shlex.split(editor_command()), str(path)], check=False)
