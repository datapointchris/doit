"""Which editor `doit config show` reports, from `$EDITOR` or the default.

The Go doit opens it for each `edit` verb. An empty or blank `$EDITOR` is
treated as unset, because there is no program named by it to run.
"""

import os

DEFAULT_EDITOR = 'nvim'


def editor_command() -> str:
    return os.environ.get('EDITOR', '').strip() or DEFAULT_EDITOR
