"""`doit config show`: every value this machine resolved, and where each came from."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from pyclilogin import Deployment
from pyclilogin import NotConfigured
from pyclilogin import Setting
from rich.text import Text

from doit import actor
from doit import editor
from doit import paths
from doit import pursuits
from doit import service
from doit.render import console
from doit.render import examples

SCHEMA_VERSION = 1

app = typer.Typer(
    name='config',
    no_args_is_help=True,
    help='Where this machine points doit, and the files it keeps.',
    epilog=examples(
        ('doit config show', 'every setting this machine resolved, and where each came from'),
        ('doit config show --json', 'the same settings as data'),
    ),
)


@dataclass(frozen=True)
class Resolved:
    """One setting: its value, the kind of place that set it, and which one. An empty value means nothing set it."""

    value: str = ''
    source: str = ''
    origin: str = ''

    def reads(self) -> str:
        """Where it came from, as the screen says it."""
        if not self.value:
            return 'not set'
        return {'file': 'config file', 'environment': self.origin}.get(self.source, self.source)


def from_login(setting: Setting) -> Resolved:
    return Resolved(setting.value, str(setting.source or ''), setting.origin)


def from_variable(value: Path | str, variable: str) -> Resolved:
    """A value `variable` sets when it is set and non-empty, and that takes its default otherwise."""
    if os.environ.get(variable):
        return Resolved(str(value), 'environment', f'${variable}')
    return Resolved(str(value), 'default')


def loaded_login() -> Deployment:
    """What the config file says, including where it says too little for a command to run."""
    try:
        return service.LOGIN.load()
    except NotConfigured as unconfigured:
        return unconfigured.deployment


def resolved_settings(login: Deployment) -> dict[str, Resolved]:
    """Every value doit resolves on this machine."""
    secret_variable = service.LOGIN.client_secret_variable
    return {
        'api': from_login(login.api),
        'issuer': from_login(login.issuer),
        'client_id': from_login(login.client_id),
        'token_command': Resolved(' '.join(login.token_command), 'file', str(login.path)) if login.hands_tokens() else Resolved(),
        # Whether it is set and never what it is.
        'client_secret': Resolved('set', 'environment', f'${secret_variable}') if service.LOGIN.is_service() else Resolved(),
        'config_file': from_variable(login.path, 'XDG_CONFIG_HOME'),
        'cache_dir': from_variable(pursuits.CACHE_DIR, 'XDG_CACHE_HOME'),
        'state_dir': from_variable(paths.xdg_state_home() / 'doit', 'XDG_STATE_HOME'),
        'editor': from_variable(editor.editor_command(), 'EDITOR'),
        'machine': Resolved(paths.machine_name(), 'hostname'),
        'actor': Resolved(actor.identity(), 'environment', actor.identity_source()),
    }


def to_document(settings: dict[str, Resolved]) -> dict[str, object]:
    """The `--json` answer, which `schemas/config-show.schema.json` describes."""
    return {
        'schema_version': SCHEMA_VERSION,
        'settings': {name: {'value': found.value, 'source': found.source, 'from': found.origin} for name, found in settings.items()},
    }


def render(settings: dict[str, Resolved], login: Deployment) -> None:
    names = max(len(name) for name in settings)
    values = max(len(found.value or '-') for found in settings.values())
    for name, found in settings.items():
        line = Text('  ')
        line.append(f'{name:<{names}}', style='white')
        line.append(f'  {found.value or "-":<{values}}  ')
        line.append(found.reads(), style='dim')
        console.print(line, soft_wrap=True)
    if login.missing():
        console.print('\n  [cyan]doit auth login <url>[/] fills in what the config file lacks.', soft_wrap=True)


@app.command(
    'show',
    epilog=examples(
        ('doit config show', 'what doit will use on this machine'),
        ('doit config show --json', 'the same as data'),
    ),
)
def show_command(
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
) -> None:
    """Every setting this machine resolved, and where each came from.

    The values doit acts on, not a copy of the config file. `doit auth status` says
    whether the login behind them is still good.
    """
    login = loaded_login()
    settings = resolved_settings(login)
    if as_json:
        print(json.dumps(to_document(settings), indent=2))
    else:
        render(settings, login)
    raise typer.Exit(0)
