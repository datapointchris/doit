"""XDG base-directory resolution, defined here once so every part of doit agrees
on where state and cache live.

The service holds the pursuits, the review register, the sources and the journal,
so no path here names them. Anything a recompute can rebuild belongs under the
cache dir. An edit the service never took, and the desk nudge's clock and
records, belong under the state dir.
"""

import os
import socket
from pathlib import Path


def xdg_state_home() -> Path:
    """`$XDG_STATE_HOME`, or its spec default when unset or empty."""
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state')


def xdg_cache_home() -> Path:
    """`$XDG_CACHE_HOME`, or its spec default when unset or empty."""
    return Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache')


def state_dir() -> Path:
    """doit's own directory under the XDG state base."""
    return xdg_state_home() / 'doit'


def cache_dir() -> Path:
    """doit's own directory under the XDG cache base."""
    return xdg_cache_home() / 'doit'


def desk_nudge_state_file() -> Path:
    """The desk nudge's clock, its nudge in hand, its unsaved answers and the pill's fields."""
    return state_dir() / 'desk-nudge.json'


def desk_nudge_state_lock_file() -> Path:
    """Locked around each read, change and write of the state file."""
    return state_dir() / 'desk-nudge.lock'


def desk_nudge_watch_lock_file() -> Path:
    """Held by the running watch for its whole life, so a second one exits."""
    return state_dir() / 'desk-nudge-watch.lock'


def desk_nudge_records_file() -> Path:
    """One `doit.desk_nudge` record per line, appended and never rotated, for a log shipper to read."""
    return state_dir() / 'desk-nudge-events.jsonl'


def machine_name() -> str:
    """This machine's identity: the bare lowercased hostname, no platform prefix.

    Recorded on every journal write and offered count. A platform prefix would
    split one machine into several, so the bare name is what gets recorded.
    """
    return canonical_machine(socket.gethostname())


def canonical_machine(name: str) -> str:
    """A hostname as a machine identity: its first label, stripped and lowercased."""
    return name.split('.')[0].strip().lower()
