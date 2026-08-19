"""What this machine declares it installs, asked of dotfiles.

The registry is one flat file for the whole fleet — reference, not declaration —
while everything it gets checked against is already scoped to one box. PATH is.
``index.shell_files`` is, because the symlink layer links only what applies here.
So a row for another machine's tool has nowhere to be filed except rot, and
`bbkt` reads as a broken entry on every desk except the one it was written for.

dotfiles is what closes that. It holds the manifests, it runs on every machine
including the work box, and `machines show` resolves the one this box is without
needing the fleet clone that only some machines have — `$MACHINE` comes from
``~/.env``, which `dotfiles env apply` writes everywhere.

Calling it from code rather than through ``sources.yml`` is the direction the
tiers already sanction: doit is `experimental` and dotfiles is `universal`, so
when the one below moves, this is what changes.

Unknown is a third state and not an empty set. A box without dotfiles, or one
whose manifest cannot be read, must fall back to judging rows by PATH alone —
treating "declares nothing" as the answer would mark every package-installed row
foreign and empty the lane, which is the one failure worse than the noise this
removes.
"""

import json
import os
import subprocess
import time
from functools import cache
from pathlib import Path
from typing import NamedTuple

from doit.paths import xdg_cache_home

# The resolution rather than the raw manifest: `system_packages: workstation`
# has to become the packages it stands for before a name can be looked up in it.
MANIFEST_QUERY = ('dotfiles', 'machines', 'show', '--json')
MANIFEST_TIMEOUT = 10

CACHE_DIR = Path(os.environ.get('DOIT_CACHE_DIR') or xdg_cache_home() / 'doit')
MANIFEST_CACHE = CACHE_DIR / 'machine-manifest.json'

# A day, because the answer changes when a manifest is edited and not when a tool
# is installed — `dotfiles apply` moves what is on PATH, which this never asks
# about. Stale here only misfiles a row that already failed to resolve, so the
# cost of the window is one row in the wrong section of one command. Delete the
# file to force a read.
CACHE_HOURS = float(os.environ.get('DOIT_MANIFEST_CACHE_HOURS') or 24)


class Declaration(NamedTuple):
    """The executables one machine declares, and the manager that installs them.

    `package_manager` is carried beside them because it is declared as a
    coordinate rather than as an item, so a row requiring `brew` finds nothing to
    match against on a Mac without it.
    """

    executables: frozenset[str]
    package_manager: str
    known: bool

    def declares(self, name: str) -> bool:
        return bool(name) and (name in self.executables or name == self.package_manager)


UNKNOWN = Declaration(frozenset(), '', known=False)


@cache
def declaration() -> Declaration:
    """This machine's resolved manifest, read once per process.

    Nothing here is asked unless a row already failed to resolve, so a machine
    whose index is clean never pays for it.
    """
    return resolve_declaration()


def resolve_declaration() -> Declaration:
    """The manifest as a `Declaration`, or `UNKNOWN` if it cannot be had.

    `executable` before `name` because they differ where it matters — the package
    is `neovim` and the command is `nvim`, and it is the command a registry row
    names.
    """
    payload = manifest_payload()
    if payload is None:
        return UNKNOWN
    items = payload.get('items') or []
    names = {name for item in items if (name := item.get('executable') or item.get('name'))}
    coordinates = payload.get('coordinates') or {}
    return Declaration(frozenset(names), coordinates.get('package_manager') or '', known=True)


def manifest_payload() -> dict | None:
    """The resolved manifest, from the cache while it is fresh and dotfiles if not."""
    cached = read_cache()
    if cached is not None:
        return cached
    payload = query_dotfiles()
    if payload is not None:
        write_cache(payload)
    return payload


def read_cache() -> dict | None:
    """The cached manifest, or None when it is missing, stale or unreadable.

    A damaged cache reads as a miss rather than an error. It is derivable from
    one subprocess, so there is nothing here worth failing over.
    """
    try:
        age_hours = (time.time() - MANIFEST_CACHE.stat().st_mtime) / 3600
    except OSError:
        return None
    if age_hours > CACHE_HOURS:
        return None
    try:
        payload = json.loads(MANIFEST_CACHE.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_cache(payload: dict) -> None:
    """Store the manifest, or carry on if the cache cannot be written."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        MANIFEST_CACHE.write_text(json.dumps(payload))
    except OSError:
        pass


def query_dotfiles() -> dict | None:
    """`dotfiles machines show --json`, or None if it cannot be asked.

    Any failure is silence: a machine without dotfiles is a machine this cannot
    speak for, and it is the caller's fallback that decides what to do about it.
    """
    try:
        result = subprocess.run(MANIFEST_QUERY, capture_output=True, text=True, timeout=MANIFEST_TIMEOUT, check=False)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None
