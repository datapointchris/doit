"""XDG base-directory resolution, defined here once so every part of doit agrees
on where content, config and state live.

Content — the cards and Labs — belongs under the data dir, where ``doit content
sync`` keeps a checkout of the terminal library. Hand-edited files doit only ever
reads (pursuits, the review register, sources) belong under the config dir: they
are personal, and both of doit's repos are public. State doit writes belongs
under the state dir, and anything a recompute can rebuild under the cache dir.
Whether the state dir replicates across machines is arranged by the sync layer,
not here — these functions only ever resolve local paths.

The library is the one path here doit does not own, so it resolves in the three
rungs standards/data.md § "A shared file is named in config; only the tool's own
default is compiled in" prescribes: ``$DOIT_LIBRARY_DIR``, then ``library_dir``
in config.toml, then a default. Each rung answers a different question — the
variable is this shell, the config is this machine, the default is every machine
that says nothing.
"""

import os
import socket
import tomllib
from pathlib import Path
from typing import Any


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


def config_path() -> Path:
    """doit's own config file. Optional — doit runs on defaults alone."""
    return xdg_config_home() / 'doit' / 'config.toml'


def load_config() -> dict[str, Any]:
    """Parse config.toml, answering {} for every way it can fail.

    Optional by design: a machine keeping the library where doit expects it should
    not have to hold a file saying so. Erroring here would break exactly that
    machine, so an unreadable or malformed file falls through to the default.
    """
    try:
        return tomllib.loads(config_path().read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}


def resolve_path(env_var: str, key: str, default: Path, config: dict[str, Any] | None = None) -> Path:
    """Resolve a shared path: $env_var, then the config key, then the default.

    ``or`` rather than a two-argument get, for the reason the XDG resolvers use
    it: a variable exported as empty is what a partially-initialised shell
    produces, and taking that as an answer resolves the path to the root.
    """
    config = load_config() if config is None else config
    value = os.environ.get(env_var) or config.get(key)
    return Path(value).expanduser() if value else default


def setting_source(env_var: str, key: str, config: dict[str, Any] | None = None) -> str:
    """Which layer supplied a setting.

    Travels with the value because the value alone does not explain itself: an
    empty library is usually a config that was never read, not a wrong path.

    Kept in step with resolve_path by hand. A source naming a layer the value did
    not come from is worse than none, because it answers the question the reader
    arrived with.
    """
    config = load_config() if config is None else config
    if os.environ.get(env_var):
        return f'${env_var}'
    if config.get(key):
        return str(config_path())
    return 'default'


def library_dir() -> Path:
    """The terminal-library checkout: the cards, the Labs, everything authored there.

    Named for the library and not for doit, because doit is one reader of it. A
    path under `doit/` would say the collection is doit's, and the collection
    outlives any one tool that parses it.

    Resolved here once rather than at every module that reaches into a
    subdirectory of it: a literal repeated per consumer is one more place a move
    has to be chased, and this path has already moved twice.

    The rungs are what let a second checkout be a pointer rather than a clone.
    The library carried two clones before them — a dev one under `~/tools` and
    the XDG one doit reads — kept in step by two sync mechanisms that never
    compared notes and had no check that they agreed.
    """
    return resolve_path('DOIT_LIBRARY_DIR', 'library_dir', xdg_data_home() / 'terminal-library')


def library_source() -> str:
    """Which layer named the checkout, for the commands that report it."""
    return setting_source('DOIT_LIBRARY_DIR', 'library_dir')


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
