"""XDG base-directory resolution, defined here once so every part of doit agrees
on where state and cache live.

The pursuits, the review register, the sources and the journal are the service's,
so no desk path holds them. Anything a recompute can rebuild belongs under the
cache dir, and an edit the service never took belongs under the state dir.
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


def machine_name() -> str:
    """This machine's identity: the bare lowercased hostname, no platform prefix.

    Recorded on every journal write and offered count. A prefixed form drifted
    across platforms and split one machine into several, so the bare name is
    recorded and any canonicalization happens at read time.
    """
    return canonical_machine(socket.gethostname())


def canonical_machine(name: str) -> str:
    """A hostname as a machine identity: its first label, stripped and lowercased."""
    return name.split('.')[0].strip().lower()
