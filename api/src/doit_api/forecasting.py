"""The service's own forecast, taken on an interval inside the process.

A forecast is a reading of the register run forward, kept so a later trend can
grade it against what the journal went on to record. The service takes one
every `interval` from the register as it stands, and stores it as the service's
machine.

The first reading after a start waits out whatever remains of the interval since
the service's newest reading, so a restart does not take one early. A reading
that fails is logged at error and the loop carries on: the next interval is the
retry. A register with no pursuits yet, as on a service not yet imported
into, is nothing to forecast and is logged as skipped.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading

from .computed_views import Views
from .errors import NotFound
from .errors import RequestFailed
from .settings import MACHINE

log = logging.getLogger(__name__)


def first_wait(views: Views, interval: dt.timedelta) -> float:
    """Seconds until the next reading is due, counting from the service's newest one."""
    taken = [reading.generated for reading in views.readings() if reading.machine == MACHINE]
    if not taken:
        return 0.0
    newest = dt.datetime.fromisoformat(max(taken))
    remaining = interval - (views.now() - newest)
    return max(remaining.total_seconds(), 0.0)


class ForecastLoop:
    """A daemon thread taking a reading every `interval` until `stop` is called."""

    def __init__(self, views: Views, interval: dt.timedelta) -> None:
        self.views = views
        self.interval = interval
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self.run, name='forecast', daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()
        self.thread.join(timeout=5)

    def run(self) -> None:
        try:
            wait = first_wait(self.views, self.interval)
        except Exception:
            log.exception('forecast_failed')
            wait = 0.0
        while not self.stopping.wait(wait):
            self.take()
            wait = self.interval.total_seconds()

    def take(self) -> None:
        try:
            reading = self.views.take_forecast()
        except NotFound as error:
            log.info('forecast_skipped', extra={'reason': str(error)})
            return
        except RequestFailed as error:
            log.error('forecast_failed', extra={'error': str(error)})
            return
        except Exception:
            log.exception('forecast_failed')
            return
        log.info('forecast_taken', extra={'generated': reading['generated'], 'pursuits': len(reading['weights'])})
