"""Who did a thing: the identity a record is stamped with, and which kind of actor it names.

The identity is spelled the way an icb issue's `closed_by` is, so a journal
entry and an issue close read alike and one `kind` sorts both.
"""

import os
import re
import socket

# Set by the scheduler on every job it runs, as `<job>@<machine>`.
SCHEDULED_JOB_ENV = 'ICB_ACTOR'

BY_HAND = 'by hand'
SESSION = 'session'
UNATTENDED = 'unattended'
UNKNOWN = 'unknown'

USER_AT_HOST = re.compile(r'[^/@\s]+@[^/@\s]+|[^/@\s]+')


def identity() -> str:
    """This process's actor: a Claude session, a scheduled job, or the user at this host."""
    job = os.environ.get(SCHEDULED_JOB_ENV, '')
    session = os.environ.get('CLAUDE_CODE_SESSION_ID', '')
    if session:
        if job:
            return f'claude-code-unattended/{job}/{session}'
        if os.environ.get('CLAUDE_CODE_SESSION_ATTENDED') == '0':
            return f'claude-code-unattended/{session}'
        return f'claude-code/{session}'
    if job:
        return f'worker/{job}'
    user = os.environ.get('USER') or 'unknown'
    host = socket.gethostname()
    return f'{user}@{host}' if host else user


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
