"""doit's command tree.

Every node prints help when given no arguments and a usage error exits 2, both
from typer. Each subcommand module owns its own `app`, and this file assembles them.
"""

import os
from typing import Annotated

import typer
from pyclilogin import LoginError
from pyclilogin.typercmd import add_auth_commands
from pyselfupdate import Config
from pyselfupdate import notify
from pyselfupdate.typercmd import add_update_command
from typer._click.core import Context
from typer._click.exceptions import NoSuchOption
from typer.core import TyperGroup

from doit import __version__
from doit import config_commands
from doit import service
from doit import shell
from doit.render import error_console
from doit.render import examples
from doit.render import set_no_input
from doit.service_commands import dashboard
from doit.service_commands import forecast
from doit.service_commands import happened
from doit.service_commands import pursuits
from doit.service_commands import review
from doit.service_commands import sources
from doit.service_commands import today

# A command list answers "what is there" and the reader arrives with "what do I
# do now". Routing lives on this screen and on a hub that owns several children,
# never on a leaf — a tool that explains its workflow at every altitude buries
# the command list it is trying to introduce.
ROOT_HELP = """What to attend to now, from your pursuits, your review items and what your apps say is outstanding.

Most commands are a noun, then a verb: `doit pursuits log`. So a list, log, drift loop on your
pursuits changes only the last word. The views you read most are one word: `doit today`.

You never name a file or a machine. doit's service holds your records and computes every view.
This machine's login says where the service is, and `doit auth login <url>` writes it once.

Start with `doit dashboard` to see everything outstanding, or `doit next` to be handed one thing.
The dashboard's last line names any pursuit that is behind, and `doit next` offers it first.

A **pursuit** is something you mean to keep doing, at a pace you declare, with a weight that orders
it against the others. The **draw** is what `doit next` offers from them: everything owed, then the
rest picked by weight. A **lane** is one list on the dashboard: what one app says is outstanding.
A **review item** is something to revisit on a cadence.

Run any partial command with no arguments or `--help` to see what comes next.
"""

ROOT_EXAMPLES = examples(
    ('doit dashboard', 'everything outstanding, every lane at a glance'),
    ('doit next', 'what to do now, drawn from your pursuits'),
    ('doit today', 'what today still owes, above what it has had'),
    ('doit happened --since 7d', 'what your apps and logs recorded this past week'),
    ('doit review due', 'every review item due, where the dashboard shows the first few'),
    ('doit pursuits log read --minutes 40', 'record a 40-minute sitting of a timed pursuit'),
)

WHAT_TO_DO_NOW = 'What to do now'
WHAT_CAME_OF_IT = 'What came of it'
WHAT_YOU_DECLARED = 'What you declared'
THIS_MACHINE = 'This machine'

# Typer lists a group's commands ahead of its sub-apps and draws each panel where
# its first command falls. Left alone, `update` would put This machine above What
# you declared.
ROOT_ORDER = ('next', 'today', 'dashboard', 'happened', 'forecast', 'pursuits', 'review', 'sources', 'auth', 'config', 'shell', 'update')


class RootGroup(TyperGroup):
    """The root group, which lists its commands by panel and says where an option a command refused is accepted.

    `--no-input` belongs to the root, so `doit next --no-input` is refused by
    `next`. click's "No such option" alone sends the reader to `--help` to find
    where the option goes, so the refusal names the placement that works.
    """

    def list_commands(self, ctx: Context) -> list[str]:
        return sorted(self.commands, key=lambda name: ROOT_ORDER.index(name) if name in ROOT_ORDER else len(ROOT_ORDER))

    def invoke(self, ctx: Context) -> object:
        try:
            return super().invoke(ctx)
        except NoSuchOption as refused:
            name_accepted_placements(refused)
            raise


def accepted_placements(refused: NoSuchOption) -> list[str]:
    """The refused command line with the option moved onto each group above it that declares it.

    Its `ctx` is the command that refused, and its parents are the groups above.
    """
    option = refused.option_name
    chain: list[Context] = []
    context = refused.ctx
    while context is not None:
        chain.insert(0, context)
        context = context.parent
    names = [each.info_name or '' for each in chain]
    return [
        ' '.join([*names[: depth + 1], option, *names[depth + 1 :]])
        for depth, each in enumerate(chain[:-1])
        if any(option in param.opts for param in each.command.params)
    ]


def name_accepted_placements(refused: NoSuchOption) -> None:
    """Append the placements that work to a refused option's message.

    The near-miss options click would suggest are dropped once one is found,
    because the option the reader typed exists verbatim and only sits in the
    wrong place.
    """
    placements = accepted_placements(refused)
    if placements:
        listed = ' or '.join(f"'{placement}'" for placement in placements)
        refused.message = f'{refused.message}. It goes before the command: {listed}.'
        refused.possibilities = None


# No typer completion: `doit shell completion` is the one that answers without starting Python.
app = typer.Typer(
    name='doit',
    cls=RootGroup,
    no_args_is_help=True,
    add_completion=False,
    help=ROOT_HELP,
    epilog=ROOT_EXAMPLES,
    rich_markup_mode='markdown',
)


# Notify-only by house rule: one check per 24h, one line to stderr, and
# `doit update` is the only thing that writes anything. The notice and the
# command share this one config so they cannot advertise a release the command
# then refuses.
#
# No credential is configured because pyselfupdate authenticates itself, running
# `gh auth token` when a request is about to be made. `$GITHUB_TOKEN_COMMAND`
# redirects that or empties it, and it is the user's lever rather than ours.
UPDATE_CONFIG = Config(tool='doit', owner='datapointchris')

AUTH_EXAMPLES = {
    'login': examples(
        ('doit auth login https://doit.example.com', 'log a new machine in to that service'),
        ('doit auth login', 'log in again to the service the config file names'),
    ),
    'logout': examples(('doit auth logout', "forget this machine's token")),
    'token': examples(('doit auth token', 'an access token, for calling the service directly')),
    'status': examples(
        ('doit auth status', 'whether this machine is logged in, and to what'),
        ('doit auth status --json', 'the same as data'),
    ),
}

# The draw sits at the root because it is typed most. Logging and skipping live
# under `pursuits`, beside the file they write to.
app.command('next', rich_help_panel=WHAT_TO_DO_NOW, epilog=pursuits.NEXT_EXAMPLES)(pursuits.next_command)
app.command('today', rich_help_panel=WHAT_TO_DO_NOW, epilog=today.TODAY_EXAMPLES)(today.today_command)
app.command('dashboard', rich_help_panel=WHAT_TO_DO_NOW, epilog=dashboard.DASHBOARD_EXAMPLES)(dashboard.dashboard_command)
app.command('happened', rich_help_panel=WHAT_CAME_OF_IT, epilog=happened.HAPPENED_EXAMPLES)(happened.happened_command)
app.add_typer(forecast.app, name='forecast', rich_help_panel=WHAT_CAME_OF_IT)
app.add_typer(pursuits.app, name='pursuits', rich_help_panel=WHAT_YOU_DECLARED)
app.add_typer(review.app, name='review', rich_help_panel=WHAT_YOU_DECLARED)
app.add_typer(sources.app, name='sources', rich_help_panel=WHAT_YOU_DECLARED)
app.add_typer(config_commands.app, name='config', rich_help_panel=THIS_MACHINE)
app.add_typer(shell.app, name='shell', rich_help_panel=THIS_MACHINE)

auth = add_auth_commands(app, service.LOGIN, rich_help_panel=THIS_MACHINE)
auth.info.epilog = examples(
    ('doit auth status', 'whether this machine is logged in, and to what'),
    ('doit auth login https://doit.example.com', 'log a new machine in to that service'),
)
for auth_command in auth.registered_commands:
    auth_command.epilog = AUTH_EXAMPLES[auth_command.name or '']

# pyselfupdate takes no panel or epilog, so its command is given them once registered.
add_update_command(app, UPDATE_CONFIG)
update_command = app.registered_commands[-1]
update_command.rich_help_panel = THIS_MACHINE
update_command.epilog = examples(
    ('doit update --check', 'whether a newer release exists, installing nothing'),
    ('doit update', 'install the newest release over this one'),
)


def version_callback(asked: bool) -> None:
    """`doit --version`, in the one line every CLI here answers it with."""
    if not asked:
        return
    print(f'doit {__version__}')
    raise typer.Exit()


@app.callback()
def root(
    ctx: typer.Context,
    version: Annotated[
        bool | None,
        typer.Option('--version', '-V', callback=version_callback, is_eager=True, help='Show the installed version and exit.'),
    ] = None,
    no_input: Annotated[
        bool,
        typer.Option('--no-input', help='Never prompt; fail naming the flag that would have answered.'),
    ] = False,
) -> None:
    """Root callback, hosting the app-level options."""
    set_no_input(no_input)

    # `update` runs its own check, so notifying too would ask the releases API
    # twice for one command and let the notice contradict what `--check` just
    # printed. Every other path is gated locally before it reaches the network.
    if ctx.invoked_subcommand != 'update':
        notify(UPDATE_CONFIG)


# Each chose the files doit read and wrote. The service holds that data, so one
# still set names a target nothing reads, and is refused rather than ignored.
RETIRED_VARIABLES = ('DOIT_JOURNAL_DIR', 'DOIT_REVIEW_STATE', 'DOIT_PURSUITS', 'DOIT_REGISTER', 'DOIT_SOURCES')

# What a command exits with when no answer came, the one failure the same command run later can outlast.
NO_ANSWER_EXIT = 4


def retired_variable_set() -> str:
    """The refusal for the first retired variable still set, or '' where none is."""
    for variable in RETIRED_VARIABLES:
        if os.environ.get(variable):
            return f'${variable} is set, and doit reads its records from its service. Unset it.'
    return ''


def main() -> None:
    if refusal := retired_variable_set():
        error_console.print(f'doit: {refusal}', soft_wrap=True, markup=False)
        raise SystemExit(2)
    try:
        app()
    except (service.ServiceFailed, LoginError) as failure:
        error_console.print(f'doit: {failure}', soft_wrap=True, markup=False)
        raise SystemExit(NO_ANSWER_EXIT if isinstance(failure, service.NoAnswer) else 1) from None
