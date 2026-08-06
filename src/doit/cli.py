import sys

from pytermstyle import help_end
from pytermstyle import help_header
from pytermstyle import help_row
from pytermstyle import help_section
from pytermstyle import help_usage

from doit import __version__

TAGLINE = 'What to do now, and everything that decides it.'


def usage() -> None:
    help_header('doit', TAGLINE)
    help_usage('doit', 'doit <command> [OPTIONS]')

    help_section('Commands')
    help_row('doit', '', 'What to do now')

    help_end()


def main() -> int:
    args = sys.argv[1:]

    if args and args[0] in ('-V', '--version'):
        print(f'doit {__version__}')
        return 0

    usage()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
