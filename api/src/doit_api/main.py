"""The process: build the collaborators from the environment and serve.

`factory` is what uvicorn imports. `serve` is the console script the image runs,
so the container's command is one word and the drain settings live in code.

## Shutting down

uvicorn closes the listener on SIGTERM, waits for in-flight requests and exits 0.
`GRACEFUL_SHUTDOWN_SECONDS` bounds the wait, under the thirty an orchestrator
gives before SIGKILL. Each app call carries its own deadline, but the GitHub read
chains one call per installation and page, so a dashboard can outlast the drain.
A request still open when it ends is cut off, and the client sees no answer. The
forecast loop's thread is a daemon and stops with the process.
"""

from __future__ import annotations

import logging.config

import uvicorn
from pyapiauth import LazyVerifier

from . import log_format
from .app import create_app
from .computed_views import Views
from .forecasting import ForecastLoop
from .readers.apps import App
from .readers.declared_reads import Readers
from .readers.github import GitHub
from .settings import LISTEN_PORT
from .settings import USER_AGENT
from .settings import Settings
from .store import Store

GRACEFUL_SHUTDOWN_SECONDS = 25


def factory():
    """The production app, wired from `Settings.from_environment()`."""
    settings = Settings.from_environment()
    store = Store(settings.database_path)
    readers = Readers(
        {name: App(app, settings.issuer) for name, app in settings.apps.items()},
        GitHub(settings.github),
        settings.clickhouse,
    )
    views = Views(store, readers, draw_lifetime=settings.draw_lifetime)
    return create_app(
        settings=settings,
        store=store,
        views=views,
        verifier=LazyVerifier(
            settings.issuer, settings.client_id_prefix, service_prefix=settings.service_client_id_prefix, user_agent=USER_AGENT
        ),
        forecast_loop=ForecastLoop(views, settings.forecast_interval),
    )


def serve():
    config = log_format.config()
    logging.config.dictConfig(config)
    uvicorn.run(
        'doit_api.main:factory',
        factory=True,
        # Every interface inside the container. The deployment decides what
        # the published port is reachable from.
        host='0.0.0.0',
        port=LISTEN_PORT,
        log_config=config,
        proxy_headers=True,
        forwarded_allow_ips='*',
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )
