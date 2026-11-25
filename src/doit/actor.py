"""Who did a thing and where: which kind of actor a recorded identity names, and how a machine is named.

The Go doit stamps each write with its identity, in the grammar that
`cli/internal/actor` documents. `kind` reads that grammar back.
"""

import re
import socket

BY_HAND = 'by hand'
SESSION = 'session'
UNATTENDED = 'unattended'
UNKNOWN = 'unknown'

USER_AT_HOST = re.compile(r'[^/@\s]+@[^/@\s]+|[^/@\s]+')


def kind(recorded: str) -> str:
    """Which of the three actors a recorded identity names, blank where none was recorded.

    Only a bare `<user>@<host>` reads as by hand. An identity in a shape this
    does not know reads as unknown, so a new kind of runner is never shown as
    the reader's own work.
    """
    if not recorded:
        return ''
    if recorded.startswith(('claude-code-unattended/', 'worker/')):
        return UNATTENDED
    if recorded.startswith('claude-code/'):
        return SESSION
    return BY_HAND if USER_AT_HOST.fullmatch(recorded) else UNKNOWN


def machine_name() -> str:
    """This machine's identity: the bare lowercased hostname, no platform prefix.

    A platform prefix would split one machine into several, so the bare name is
    what gets recorded.
    """
    return canonical_machine(socket.gethostname())


def canonical_machine(name: str) -> str:
    """A hostname as a machine identity: its first label, stripped and lowercased."""
    return name.split('.')[0].strip().lower()
