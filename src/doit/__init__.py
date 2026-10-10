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
    """This build's version, or 'unknown' from a checkout that was never installed.

    Read from the installed distribution, whose build took the version from the
    release tag, so no copy here can drift behind it. An install past the tag
    reports the distance, as in `5.0.0-post.3+<commit>`. That string never tells
    a release from a dev build; `git tag --points-at HEAD` does.
    """
    try:
        return installed_version('doit')
    except PackageNotFoundError:
        return 'unknown'


__version__ = _tool_version()
