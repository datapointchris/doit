"""Opening a file in the user's editor, for every command that edits one.

Each `edit` verb opens what it owns through `open_in_editor`, so `$EDITOR`
unset or empty lands every command on the same program. An empty value is
treated as unset because there is no program named by it to run.
"""

import os
import subprocess
from pathlib import Path

DEFAULT_EDITOR = 'nvim'


def editor_command() -> str:
    return os.environ.get('EDITOR') or DEFAULT_EDITOR


def open_in_editor(path: Path) -> None:
    subprocess.run([editor_command(), str(path)], check=False)
