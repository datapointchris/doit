"""doit's command tree.

Every node prints help when given no arguments, and a usage error exits 2 —
both from typer rather than hand-rolled here, which is the whole reason the
tree is built this way. Each subcommand module owns its own `app`; this file
only assembles them.
"""

import sys
from typing import Annotated
from typing import Any

import typer
from pyselfupdate import Config
from pyselfupdate import notify
from pyselfupdate.typercmd import add_update_command
from typer.core import TyperGroup

from doit import __version__
from doit import claude
from doit import content
from doit import dashboard
from doit import find
from doit import forecast
from doit import happened
from doit import labs
from doit import pursuits
from doit import review
from doit import shell
from doit import sources
from doit import today
from doit import tools
from doit import workflows
from doit.render import set_no_input

TAGLINE = 'What to do now, and everything that decides it.'

# A command list answers "what is there" and the reader arrives with "what do I
# do now". Routing lives on this screen and on a hub that owns several children,
# never on a leaf — a tool that explains its workflow at every altitude buries
# the command list it is trying to introduce.
#
# The examples sit in an indented block because markdown joins the lines of a
# paragraph, and each example has to keep a line of its own.
ROOT_HELP = f"""{TAGLINE}

Examples:

    doit dashboard     the spread: every lane, ranked across none of them
    doit next          be handed one thing, ranked by your pursuit weights
    doit today         only what the day can finish, above what it has had
    doit happened      what every record says happened this past week
    doit review due    every review item due; the dashboard shows three
    doit labs due      every Labs exercise due to practice now

**next** and **dashboard** are separate systems. They meet only on the dashboard.
"""


class RootGroup(TyperGroup):
    """The root group, which says where an option a command refused is accepted.

    `--no-input` belongs to the root, so `doit next --no-input` is refused by
    `next`. click's "No such option" alone sends the reader to `--help` to find
    where the option goes, so the refusal names the placement that works.
    """

    def invoke(self, ctx: Any) -> Any:
        try:
            return super().invoke(ctx)
        except Exception as failure:
            name_accepted_placements(failure)
            raise


def accepted_placements(failure: Any) -> list[str]:
    """The refused command line with the option moved onto each group above it that declares it.

    click's `NoSuchOption` is recognized by its `option_name` and `possibilities`
    rather than by class, because typer vendors click under a private module.
    Its `ctx` is the command that refused, and its parents are the groups above.
    """
    option = getattr(failure, 'option_name', None)
    refusing = getattr(failure, 'ctx', None)
    if not option or not hasattr(failure, 'possibilities') or refusing is None:
        return []
    chain: list[Any] = []
    while refusing is not None:
        chain.insert(0, refusing)
        refusing = refusing.parent
    names = [context.info_name or '' for context in chain]
    return [
        ' '.join([*names[: depth + 1], option, *names[depth + 1 :]])
        for depth, context in enumerate(chain[:-1])
        if any(option in getattr(param, 'opts', ()) for param in context.command.params)
    ]


def name_accepted_placements(failure: Any) -> None:
    """Append the placements that work to a refused option's message.

    The near-miss options click would suggest are dropped once one is found,
    because the option the reader typed exists verbatim and only sits in the
    wrong place.
    """
    placements = accepted_placements(failure)
    if placements:
        listed = ' or '.join(f"'{placement}'" for placement in placements)
        failure.message = f'{failure.message}. It goes before the command: {listed}.'
        failure.possibilities = None


app = typer.Typer(name='doit', cls=RootGroup, no_args_is_help=True, help=ROOT_HELP, rich_markup_mode='markdown')


# Notify-only by house rule: one check per 24h, one line to stderr, and
# `doit update` is the only thing that writes anything. The notice and the
# command share this one config so they cannot advertise a release the command
# then refuses.
#
# No credential is configured because pyselfupdate authenticates itself, running
# `gh auth token` when a request is about to be made. `$GITHUB_TOKEN_COMMAND`
# redirects that or empties it, and it is the user's lever rather than ours.
UPDATE_CONFIG = Config(tool='doit', owner='datapointchris')

# The draw and the two things you do to what it just offered sit at the root:
# they act on the answer, while `pursuits` manages the file that produced it.
app.command('next')(pursuits.next_command)
app.command('dashboard')(dashboard.dashboard_command)
app.command('today')(today.today_command)
app.command('happened')(happened.happened_command)
app.command('find')(find.find_command)
app.command('choose')(find.choose_command)
app.command('launch')(find.launch_command)

app.command('show')(find.show_command)
add_update_command(app, UPDATE_CONFIG)

# What the fzf preview pane calls, not what you type.
app.command('__preview', hidden=True)(find.preview_command)

app.add_typer(pursuits.app, name='pursuits')
app.add_typer(forecast.app, name='forecast')
app.add_typer(review.app, name='review')
app.add_typer(labs.app, name='labs')
app.add_typer(workflows.app, name='workflows')
app.add_typer(claude.app, name='claude')
app.add_typer(tools.app, name='tools')
app.add_typer(find.kit_app, name='kit')
app.add_typer(shell.app, name='shell')
app.add_typer(sources.app, name='sources')
app.add_typer(content.app, name='content')


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
    """Root callback, hosting the app-level options.

    The content clone happens here so a new machine needs no setup step anyone
    has to remember. It is one `exists()` once the checkout is there — see
    `doit.content.ensure_cloned` for why a failure only warns.

    Keeping it current is the same argument on every later run, so that is not a
    verb anyone has to remember either — the pull is detached, so a command costs
    the same whether or not one was due. `content` is exempt: its own subcommands
    report on the checkout, and a pull racing them would have `status` describe a
    tree being rewritten underneath it.
    """
    set_no_input(no_input)
    # A help screen reads no cards, so neither the clone nor the pull belongs on
    # one. Typer runs this callback before answering a subcommand's --help, so
    # without the guard `doit anything --help` clones a repo on a new machine
    # and starts a pull on every other.
    if content.worth_a_pull(sys.argv):
        content.ensure_cloned()
        if ctx.invoked_subcommand != 'content':
            content.autosync()

    # `update` runs its own check, so notifying too would ask the releases API
    # twice for one command and let the notice contradict what `--check` just
    # printed. Every other path is gated locally before it reaches the network.
    if ctx.invoked_subcommand != 'update':
        notify(UPDATE_CONFIG)


def main() -> None:
    app()
