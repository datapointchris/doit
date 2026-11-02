"""One JSON object per log line, on stdout.

A collector reads a line at a time, so a line that is a whole object is a record
it can label without a parser of its own. uvicorn's loggers are routed here too,
so its access log is the same shape as the service's own lines.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

# The attributes every LogRecord carries. Anything else on a record arrived
# through `extra=` and belongs in the object as a field of its own, except
# uvicorn's `color_message`, which repeats the message with terminal escapes.
STANDARD_ATTRIBUTES = frozenset(vars(logging.LogRecord('', 0, '', 0, '', (), None))) | {'message', 'asctime', 'taskName', 'color_message'}


class JsonFormatter(logging.Formatter):
    """A record as one line of JSON, with any `extra=` fields beside the message."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            'time': dt.datetime.fromtimestamp(record.created, dt.UTC).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
            'level': record.levelname.lower(),
            'logger': record.name,
            'message': record.getMessage(),
        }
        entry.update({key: value for key, value in vars(record).items() if key not in STANDARD_ATTRIBUTES})
        if record.exc_info:
            entry['exception'] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def config(level: str = 'INFO') -> dict:
    """A `logging.config.dictConfig` document, for the service and uvicorn alike."""
    handler = {'class': 'logging.StreamHandler', 'formatter': 'json', 'stream': 'ext://sys.stdout'}
    quiet = {'handlers': ['stdout'], 'level': level, 'propagate': False}
    return {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {'json': {'()': JsonFormatter}},
        'handlers': {'stdout': handler},
        'loggers': {
            'uvicorn': quiet,
            'uvicorn.error': quiet,
            'uvicorn.access': quiet,
            'doit_api': quiet,
            # The bearer check, so a missing, rejected or refused bearer is logged.
            'pyapiauth': quiet,
        },
        'root': {'handlers': ['stdout'], 'level': 'WARNING'},
    }
