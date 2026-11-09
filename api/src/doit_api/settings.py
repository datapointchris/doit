"""What the service is configured with, read from the environment and nowhere else.

The same image runs under compose or as a Kubernetes Deployment, so every value
arrives as a variable. The issuer, the public URL and the database are required:
a refusal at startup names the variable nothing set, where a compiled-in default
would answer with an outage that reads like someone else's.

Each app the service reads is optional. One left unset is a lane that says which
variable is missing, so the service still starts while a credential is on its way,
and the dashboard says what it cannot read rather than going dark.

The service computes in the process's local zone, which the deployment sets with
`TZ`. A desk computes "today" in its own zone, and the service answers for the
same person, so it runs in theirs.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

# The per-product halves of the client naming: `doit-cli-<machine>` for a
# person's device login and `doit-svc-<service>` for a service's
# client-credentials login. They are this API's identity, not deployment values.
CLIENT_ID_PREFIX = 'doit-cli-'
SERVICE_CLIENT_ID_PREFIX = 'doit-svc-'

# Sent on every request the service makes: discovery and keys at the identity
# provider, each app it reads, GitHub and ClickHouse.
USER_AGENT = 'doit-api (+https://github.com/datapointchris/doit)'

# What a journal entry or a forecast reading the service writes on its own names
# as its machine. The desks' records keep theirs.
MACHINE = 'doit-service'

# How often the service takes a forecast unless told otherwise.
DEFAULT_FORECAST_INTERVAL = '4h'

# How long a draw keeps answering `next`. Long enough to survive deciding what to
# do, short enough that coming back after a task gives a fresh draw. Re-running
# inside it is the common case, and a reshuffle there reads as the tool having
# changed its mind.
DEFAULT_DRAW_LIFETIME = '15m'

INTERVAL = re.compile(r'(\d+)([mhd])')
INTERVAL_UNITS = {'m': 'minutes', 'h': 'hours', 'd': 'days'}


class Unconfigured(RuntimeError):
    """A variable the service cannot start without is unset or unreadable."""


def required_env(name: str) -> str:
    value = os.environ.get(name, '').strip()
    if not value:
        raise Unconfigured(f'{name} is unset; the deployment renders it into the environment the service reads')
    return value


def optional_env(name: str, default: str = '') -> str:
    return os.environ.get(name, '').strip() or default


def parse_interval(name: str, text: str) -> dt.timedelta:
    match = INTERVAL.fullmatch(text.strip())
    if not match or int(match.group(1)) == 0:
        raise Unconfigured(f'{name}={text!r} is not an interval; write a whole number and one of m, h or d, like 4h')
    return dt.timedelta(**{INTERVAL_UNITS[match.group(2)]: int(match.group(1))})


@dataclass(frozen=True)
class AppSettings:
    """One app read with a client-credentials token of the service's own.

    `client_id` is the identity provider's client the app admits, compiled in
    because the app's scope table names it. `secret` is empty where the
    deployment has not supplied one, and `missing` then names the variable.
    """

    name: str
    url: str
    client_id: str
    secret: str
    scopes: tuple[str, ...]
    url_variable: str
    secret_variable: str

    @property
    def missing(self) -> str:
        unset = [name for name, value in ((self.url_variable, self.url), (self.secret_variable, self.secret)) if not value]
        return ', '.join(unset)


@dataclass(frozen=True)
class GitHubSettings:
    """doit's own GitHub App, which reads pull requests and repository metadata on every installation."""

    client_id: str = ''
    private_key: str = ''

    @property
    def missing(self) -> str:
        unset = [
            name
            for name, value in (('DOIT_API_GITHUB_CLIENT_ID', self.client_id), ('DOIT_API_GITHUB_PRIVATE_KEY', self.private_key))
            if not value
        ]
        return ', '.join(unset)


@dataclass(frozen=True)
class ClickHouseSettings:
    """A ClickHouse account granted `SELECT` on `otel.desk_reports` and nothing else."""

    url: str = ''
    user: str = ''
    password: str = ''

    @property
    def missing(self) -> str:
        variables = (
            ('DOIT_API_CLICKHOUSE_URL', self.url),
            ('DOIT_API_CLICKHOUSE_USER', self.user),
            ('DOIT_API_CLICKHOUSE_PASSWORD', self.password),
        )
        return ', '.join(name for name, value in variables if not value)


def app_settings(name: str, client_id: str, scopes: tuple[str, ...]) -> AppSettings:
    prefix = f'DOIT_API_{name.upper()}'
    return AppSettings(
        name=name,
        url=optional_env(f'{prefix}_URL').rstrip('/'),
        client_id=client_id,
        secret=optional_env(f'{prefix}_CLIENT_SECRET'),
        scopes=scopes,
        url_variable=f'{prefix}_URL',
        secret_variable=f'{prefix}_CLIENT_SECRET',
    )


# Each app's client and the scopes it is asked for: every read the service
# makes of that app, and nothing it writes.
LIFE_SCOPES = (
    'life.overview.read',
    'life.habits.read',
    'life.tasks.read',
    'life.articles.read',
    'life.books.read',
)
LEARNING_SCOPES = ('learning.overview.read', 'learning.completed.read')
MESO_SCOPES = ('meso.cycles.read', 'meso.review.read', 'meso.feedback.read')
NOMAD_SCOPES = ('nomad.overview.read',)
PROJECTS_SCOPES = ('projects.items.read',)
SLATE_SCOPES = ('slate.records.read',)


@dataclass(frozen=True)
class Settings:
    """Everything the service reads, resolved once at startup.

    `public_url` is the address clients reach this API at, with no trailing
    slash. Its RFC 9728 metadata names it as `resource`, and a CLI refuses the
    document unless that equals the URL it fetched it for.
    """

    issuer: str
    public_url: str
    database_path: Path
    forecast_interval: dt.timedelta = dt.timedelta(hours=4)
    draw_lifetime: dt.timedelta = dt.timedelta(minutes=15)
    apps: dict[str, AppSettings] = field(default_factory=dict)
    github: GitHubSettings = GitHubSettings()
    clickhouse: ClickHouseSettings = ClickHouseSettings()
    client_id_prefix: str = CLIENT_ID_PREFIX
    service_client_id_prefix: str = SERVICE_CLIENT_ID_PREFIX

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            issuer=required_env('DOIT_API_ISSUER'),
            public_url=required_env('DOIT_API_PUBLIC_URL').rstrip('/'),
            database_path=Path(required_env('DOIT_API_DATABASE')),
            forecast_interval=parse_interval(
                'DOIT_API_FORECAST_INTERVAL', optional_env('DOIT_API_FORECAST_INTERVAL', DEFAULT_FORECAST_INTERVAL)
            ),
            draw_lifetime=parse_interval('DOIT_API_DRAW_LIFETIME', optional_env('DOIT_API_DRAW_LIFETIME', DEFAULT_DRAW_LIFETIME)),
            apps={
                'life': app_settings('life', 'life-svc-doit', LIFE_SCOPES),
                'learning': app_settings('learning', 'learning-svc-doit', LEARNING_SCOPES),
                'meso': app_settings('meso', 'meso-svc-doit', MESO_SCOPES),
                'nomad': app_settings('nomad', 'nomad-svc-doit', NOMAD_SCOPES),
                'projects': app_settings('projects', 'projects-svc-doit', PROJECTS_SCOPES),
                'slate': app_settings('slate', 'slate-svc-doit', SLATE_SCOPES),
            },
            github=GitHubSettings(
                client_id=optional_env('DOIT_API_GITHUB_CLIENT_ID'),
                private_key=optional_env('DOIT_API_GITHUB_PRIVATE_KEY'),
            ),
            clickhouse=ClickHouseSettings(
                url=optional_env('DOIT_API_CLICKHOUSE_URL').rstrip('/'),
                user=optional_env('DOIT_API_CLICKHOUSE_USER'),
                password=optional_env('DOIT_API_CLICKHOUSE_PASSWORD'),
            ),
        )


# Fixed rather than configurable, because the image's EXPOSE and HEALTHCHECK name it too.
# The deployment chooses the published port.
LISTEN_PORT = 8080
