"""doit — the layer that decides what to attend to and drives it.

Scheduling comes in two halves and doit uses both. :mod:`doit.cadence` is the
deterministic one: a declared interval, a derived due date, an item that is due
or it isn't — what review and labs run on. :mod:`doit.allocate` is what
`doit next` runs on: a running balance against a declared pace, and a weight
that orders what is owed and draws what fills the rest of the screen.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version


def _tool_version() -> str:
    """This build's version, or 'unknown' from a source checkout.

    Read from the installed distribution rather than a constant here, so
    semantic-release owns the one copy in `pyproject.toml` and this cannot drift
    behind it. A checkout that was never installed has no metadata and says so
    rather than inventing a number: release.md is explicit that a version string
    can never be used to tell a release from a dev build.
    """
    try:
        return installed_version('doit')
    except PackageNotFoundError:
        return 'unknown'


__version__ = _tool_version()
