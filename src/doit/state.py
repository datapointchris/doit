"""Last-done state: a ``{item_id: last_done_iso}`` JSON map, written atomically.

The register/deck config is read-only to the tools; only this state file is ever
written. It lives under the XDG state dir (resolved by the caller); replication
across machines, if any, is arranged by the sync layer, not by this module.
"""

import json
import os
from pathlib import Path


def load_state(path: Path) -> dict:
    """The ``{item_id: last_done_iso}`` map, or an empty dict if none exists yet."""
    if not path.exists():
        return {}
    return json.loads(path.read_text() or '{}')


def save_state(path: Path, state: dict) -> None:
    """Write state as pretty JSON, atomically (temp file + rename).

    The atomic replace means a crash mid-write cannot truncate or corrupt the
    existing state file — either the old or the new content survives, never a
    partial one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(state, indent=2) + '\n'
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(data)
    os.replace(tmp, path)
