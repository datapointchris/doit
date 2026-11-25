"""The service on a temporary store, for the Go doit's contract test in cli/internal/contract.

It serves every route as the deployed service does, on the real clock, with
`conftest.TOKEN` the one token it accepts. life answers `/tasks` with TASKS, so a
pursuit that resolves there offers choices, and every other app is unconfigured.
It prints the port it listens on, on 127.0.0.1, then serves until it is stopped.

    uv run --project api python api/tests/serve_on_a_temporary_store.py <store file>
"""

import socket
import sys
from pathlib import Path

import uvicorn
from conftest import Verifier
from conftest import stand_in
from conftest import unconfigured_apps

from doit_api.app import create_app
from doit_api.computed_views import Views
from doit_api.readers.declared_reads import Readers
from doit_api.readers.github import GitHub
from doit_api.settings import ClickHouseSettings
from doit_api.settings import GitHubSettings
from doit_api.settings import Settings
from doit_api.store import Store

# Invented tasks. The contract test logs one by its id and reads its label back.
TASKS = [{'name': 'Water the plants', 'id': 501}, {'name': 'Return the parcel', 'id': 512}, {'name': 'Sort the mail', 'id': 474}]


def main(database: Path) -> None:
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    store = Store(database)
    apps = unconfigured_apps() | {'life': stand_in('life', {'/tasks': lambda request: TASKS})}
    readers = Readers(apps, GitHub(GitHubSettings()), ClickHouseSettings())
    settings = Settings(issuer='https://id.test', public_url=f'http://127.0.0.1:{port}', database_path=database)
    app = create_app(settings=settings, store=store, views=Views(store, readers), verifier=Verifier())
    print(port, flush=True)
    uvicorn.Server(uvicorn.Config(app, log_level='warning')).run(sockets=[listener])


if __name__ == '__main__':
    main(Path(sys.argv[1]))
