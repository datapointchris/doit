"""The Claude Code harness — what lives under `~/.claude`.

A namespace rather than a top-level `doit skills`, because `skills` is the one
collection name here that collides with a concept another CLI owns. `learning`
stewards the topics and tracks Chris is developing and `doit labs` the practice
for them, so a bare `doit skills` reads at least as naturally as *his* skills as
it does Claude Code's skill files. Workflow cards, Labs and the tool registry are
unambiguous at one word and stay there; this one is not.

It is also where the second one goes. `~/.claude` holds agents, commands, hooks
and output styles beside the skills. Those directories are mostly empty today,
and the point of deciding now is that filling one becomes a mount here rather
than another product's vocabulary arriving loose at doit's root — the retrofit
`cli-design.md` § "A resource that could ever grow a second command is a
namespace today" exists to prevent.

`doit find --source skill` is deliberately untouched. A `--source` value is a lens
filter alongside `tool`, `func` and `tmux`, not a command path.
"""

import typer

from doit import skills

app = typer.Typer(name='claude', no_args_is_help=True, help='The Claude Code harness under ~/.claude.')

app.add_typer(skills.app, name='skills')
