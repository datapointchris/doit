"""XDG base-directory resolution, defined here once so every part of doit agrees
on where content, config and state live.

Content — the cards and Labs — belongs under the data dir, where ``doit content
sync`` keeps a checkout of the terminal library. Hand-edited files doit only ever
reads (pursuits, the review register, sources) belong under the config dir: they
are personal, and both of doit's repos are public. State doit writes belongs
under the state dir, and anything a recompute can rebuild under the cache dir.
Whether the state dir replicates across machines is arranged by the sync layer,
not here — these functions only ever resolve local paths.
"""

import os
import socket
from pathlib import Path


def xdg_config_home() -> Path:
    """`$XDG_CONFIG_HOME`, or its spec default when unset or empty."""
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')


def xdg_data_home() -> Path:
    """`$XDG_DATA_HOME`, or its spec default when unset or empty."""
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')


def xdg_state_home() -> Path:
    """`$XDG_STATE_HOME`, or its spec default when unset or empty."""
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state')


def xdg_cache_home() -> Path:
    """`$XDG_CACHE_HOME`, or its spec default when unset or empty."""
    return Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache')


def library_dir() -> Path:
    """The terminal-library checkout: the cards, the Labs, everything authored there.

    Named for the library and not for doit, because doit is one reader of it. A
    path under `doit/` would say the collection is doit's, and the collection
    outlives any one tool that parses it.

    Resolved here once rather than at every module that reaches into a
    subdirectory of it: a literal repeated per consumer is one more place a move
    has to be chased, and this path has already moved twice.

    `$DOIT_LIBRARY_DIR` names a different checkout for one shell. Without it the
    only way to read a second copy was to clone one, and a clone drifts: the
    library carried a dev clone under `~/tools` and an XDG one for exactly this
    reason, kept in step by two sync mechanisms that never compared notes.

    No config rung. Every rung has to answer a question some machine actually
    asks, and this path is the same on all of them — unlike the repo registry,
    whose value differs per machine and so earns one.
    """
    return Path(os.environ.get('DOIT_LIBRARY_DIR') or xdg_data_home() / 'terminal-library')


def machine_name() -> str:
    """This machine's identity: the bare lowercased hostname, no platform prefix.

    Used to name per-machine files in a synced directory. A prefixed form drifted
    (arch/archlinux, Macmini/macmini) and split one machine into several, so the
    bare name is recorded and any canonicalization happens at read time.
    """
    return canonical_host(socket.gethostname())


def canonical_host(name: str) -> str:
    """A hostname from anywhere, reduced to the form :func:`machine_name` records.

    Other tools report the fully qualified name — atuin stores `macmini.trusted`
    — so a comparison against a bare recorded name fails on every host and
    quietly reports that nothing ever ran here.
    """
    return name.split('.')[0].strip().lower()
