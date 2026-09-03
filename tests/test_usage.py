"""Tests for doit.usage — joining your kit to what shell history says you ran.

The join is the whole of this module; the arithmetic on either side of it is a
count and a max. So the cases below are almost all about the key: a subcommand
row that must not inherit its parent's count, a git alias spelled differently in
the catalog and in history, and a name made entirely of punctuation.
"""

from datetime import date

from doit import usage
from doit.index import Entry
from doit.observe import Invocation


def ran(command: str, when: str = '2026-08-10', host: str = 'archlinux') -> Invocation:
    return Invocation(when, host, command)


def tool(name: str, invocation: str) -> Entry:
    return Entry(source='tool', name=name, invocation=invocation)


def test_a_row_is_measured_by_what_you_type_not_by_its_name():
    """`ripgrep` installs `rg`, so measuring by name reports it as never used."""
    rows = usage.measure([tool('ripgrep', 'rg [pattern] [path]')], (ran('rg needle'), ran('rg other')))

    assert rows[0].typed == 'rg'
    assert rows[0].count == 2


def test_a_subcommand_row_does_not_inherit_its_parents_count():
    """The bug this join exists to fix: 23 registry rows all keyed on `git`.

    `git-forgit-add` invokes `git forgit add`, so keying it on the head word
    hands it every git invocation on the machine and reports it as heavily used.
    """
    entries = [tool('git', 'git [command] [options]'), tool('git-forgit-add', 'git forgit add')]
    history = (ran('git status'), ran('git commit -m x'), ran('git forgit add'))

    by_typed = {row.typed: row for row in usage.measure(entries, history)}

    assert by_typed['git'].count == 3
    assert by_typed['git forgit add'].count == 1


def test_a_git_alias_is_matched_as_git_plus_its_name():
    """The catalog calls it `co`; history records `git co`."""
    entries = [Entry(source='git', name='co', invocation='git co')]

    rows = usage.measure(entries, (ran('git co main'), ran('git status')))

    assert rows[0].typed == 'git co'
    assert rows[0].count == 1


def test_a_prefix_does_not_claim_a_longer_word():
    """`doit review` must not count as a run of a hypothetical `doit rev`.

    Same boundary rule as observe.is_invocation_of, which this delegates to.
    """
    rows = usage.measure([tool('rev', 'rev')], (ran('review something'), ran('rev')))

    assert rows[0].count == 1


def test_a_leading_carrier_credits_the_command_underneath_it():
    """Otherwise `sudo` scores as the most used thing on the machine."""
    rows = usage.measure([tool('pacman', 'pacman [options]')], (ran('sudo pacman -Syu'),))

    assert rows[0].count == 1


def test_an_alias_whose_name_is_dots_survives():
    """`..`, `...` and `....` are real cd-up aliases, not usage-string ellipses."""
    entries = [Entry(source='alias', name='...', invocation='...')]

    rows = usage.measure(entries, (ran('...'),))

    assert rows[0].typed == '...'
    assert rows[0].count == 1


def test_one_thing_catalogd_twice_is_one_row():
    """The registry documents Chris's own shell functions, so several arrive twice.

    Showing `checknode` twice would be reporting the catalog, not the usage.
    """
    entries = [tool('checknode', 'checknode'), Entry(source='func', name='checknode', invocation='checknode')]

    rows = usage.measure(entries, (ran('checknode'),))

    assert len(rows) == 1
    assert rows[0].sources == ('func', 'tool')
    assert rows[0].count == 1


def test_only_collections_shell_history_can_see_are_measured():
    """A tmux keybinding is a keypress; reporting it as unused would be a lie."""
    entries = [Entry(source='tmux', name='prefix + |', invocation='prefix + |'), tool('rg', 'rg')]

    rows = usage.measure(entries, ())

    assert [row.typed for row in rows] == ['rg']


def test_a_row_never_run_is_kept_with_a_zero_count():
    """Never having run it is the answer, so it has to be representable."""
    rows = usage.measure([tool('sd', 'sd [find] [replace]')], (ran('rg needle'),))

    assert rows[0].count == 0
    assert rows[0].last == ''
    assert rows[0].days_since(date(2026, 8, 10)) is None


def test_the_last_date_is_the_newest_not_the_last_row():
    """Several shells and machines feed one store, so its order is write order."""
    history = (ran('fd x', '2026-08-10'), ran('fd y', '2026-04-01'))

    rows = usage.measure([tool('fd', 'fd [pattern]')], history)

    assert rows[0].last == '2026-08-10'
    assert rows[0].days_since(date(2026, 8, 20)) == 10


def test_unused_catches_never_run_and_gone_cold_but_not_the_live_ones():
    rows = [
        usage.Row(typed='live', sources=('tool',), names=('live',), count=5, last='2026-08-01'),
        usage.Row(typed='cold', sources=('tool',), names=('cold',), count=2, last='2026-01-01'),
        usage.Row(typed='never', sources=('tool',), names=('never',), count=0, last=''),
    ]

    got = {row.typed for row in usage.unused(rows, days=90, today=date(2026, 8, 10))}

    assert got == {'cold', 'never'}


def test_minimum_catches_the_barely_used():
    rows = [
        usage.Row(typed='twice', sources=('tool',), names=('twice',), count=2, last='2026-08-10'),
        usage.Row(typed='often', sources=('tool',), names=('often',), count=40, last='2026-08-10'),
    ]

    got = {row.typed for row in usage.unused(rows, minimum=3, today=date(2026, 8, 10))}

    assert got == {'twice'}


def test_staleness_puts_never_run_ahead_of_merely_cold():
    """A cold row's lateness is bounded; a never-run row's is not."""
    rows = [
        usage.Row(typed='cold', sources=('tool',), names=('cold',), count=1, last='2026-01-01'),
        usage.Row(typed='never', sources=('tool',), names=('never',), count=0, last=''),
    ]

    assert [row.typed for row in usage.by_staleness(rows)] == ['never', 'cold']


def test_literal_prefix_stops_at_the_first_placeholder():
    assert usage.literal_prefix('rg [pattern] [path]') == 'rg'
    assert usage.literal_prefix('git forgit add') == 'git forgit add'
    assert usage.literal_prefix('menu labs [list | <id>]') == 'menu labs'
    assert usage.literal_prefix('source aws-profiles') == 'aws-profiles'
    assert usage.literal_prefix('...') == '...'


def test_a_bare_uppercase_placeholder_is_not_taken_as_a_word():
    """Three registry rows spell their arguments in caps rather than brackets.

    Left in the prefix they match no command line that was ever typed, so `bbkt`,
    `dectl` and `jira` report as never used however much they are used.
    """
    assert usage.literal_prefix('bbkt pr VERB [ID] [OPTIONS]') == 'bbkt pr'
    assert usage.literal_prefix('dectl PIPELINE RESOURCE ACTION [ALIAS]') == 'dectl'
    assert usage.literal_prefix('jira issue VERB [KEY]') == 'jira issue'

    rows = usage.measure([tool('jira', 'jira issue VERB [KEY]')], (ran('jira issue list'),))
    assert rows[0].count == 1
