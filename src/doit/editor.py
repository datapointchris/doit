"""Which editor `doit config show` reports: $VISUAL, then $EDITOR, then vi.

It reports what gotermboard's `EditorArgs` opens for the Go doit's `edit`
verbs, split on whitespace. A blank variable is passed over, because it names
no program to run.
"""

import os

DEFAULT_EDITOR = 'vi'
VARIABLES = ('VISUAL', 'EDITOR')


def editor_variable() -> str | None:
    """The variable that names the editor, or None where the default is opened."""
    return next((variable for variable in VARIABLES if os.environ.get(variable, '').split()), None)


def editor_command() -> str:
    variable = editor_variable()
    return ' '.join(os.environ[variable].split()) if variable else DEFAULT_EDITOR
