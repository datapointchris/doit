import sys

from pytermstyle import help_end
from pytermstyle import help_header
from pytermstyle import help_row
from pytermstyle import help_section
from pytermstyle import help_usage

from doit import __version__
from doit import labs
from doit import review

TAGLINE = 'What to do now, and everything that decides it.'

# Every command's entry point takes its remaining argv and returns an exit code.
# Bare `doit` and every bare namespace under it print help instead of acting —
# see cli-design.md, "No args shows help. Always."
COMMANDS = {
    'review': review.main,
    'labs': labs.main,
}


def usage() -> None:
    help_header('doit', TAGLINE)
    help_usage('doit', 'doit <command> [OPTIONS]')

    help_section('Commands')
    help_row('doit review', '', "What's due to revisit, on a cadence")
    help_row('doit labs', '', 'Hands-on practice that is due')

    help_end()


def main() -> int:
    args = sys.argv[1:]

    if args and args[0] in ('-V', '--version'):
        print(f'doit {__version__}')
        return 0
    if not args or args[0] in ('help', '-h', '--help'):
        usage()
        return 0

    command, rest = args[0], args[1:]
    if command in COMMANDS:
        return COMMANDS[command](rest)

    print(f'Unknown command: {command}', file=sys.stderr)
    usage()
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
