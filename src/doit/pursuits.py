"""What to do now, drawn from what you said matters. Invoked as `doit next`.

The one place in doit that holds an opinion, and the opinion is yours. Every other
view is a renderer: `doit dashboard` shows independent lanes and deliberately
refuses to rank across them, because an ordering it invented over unlike things
would be meaningless. This ranks across everything — legitimately, because it is
not inventing the ordering. You declare it, as a weight per pursuit.

A *pursuit* is a named strand of life you want to spend attention on:
study-computer-science at 35, read-library at 30, visit-new-places at 70 for a
year. Weights are relative magnitudes, never normalized — the implied share is
displayed so a number that dominates more than you meant is visible.

A pursuit is measured in **minutes** where it declares a checkoff size and in
**occurrences** where it does not, and that declaration is the only thing
separating the two kinds. Standing is one running balance in whichever unit
applies: what the weight-derived schedule has asked for since the pursuit's zero
point, less what has been done. Positive is owed. Nothing is capped in either
direction, so a burst counts for what it was, a fortnight away is owed in full,
and a balance too large to be true is the register saying its weight is wrong.

Three files, kept apart like the review register:
  - pursuits.yml    declarative config you hand-edit; only ever read here. Under
                    the XDG config dir, never either doit repo — a life's
                    intentions are personal and both repos are public.
  - next-log-*.jsonl  the append-only journal, one file per machine, merged on
                    read. See doit.journal for why per-machine.
  - next-offers-*.json  how many times each pursuit has been offered, so `drift`
                    can tell "never comes up" apart from "comes up and is ignored".

The draw is fresh every run, not a queue — but cached for 15 minutes, because
running it three times while deciding must not reshuffle the list underneath you.
That cache also carries which concrete item each pursuit resolved to, which is what
lets `doit log` write through to the CLI that owns it.

What a pursuit resolves to is never doit's business. It shells out to whoever owns
that domain — `icb books` knows which book, `icb tasks` knows which chore — exactly
as the dashboard delegates its lanes.
"""

import json
import math
import os
import random
import shlex
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.table import Table
from rich.text import Text

from doit import evidence
from doit import journal
from doit.allocate import DEFAULT_CATCHUP_EXPONENT
from doit.allocate import FALLBACK_LOGS_PER_DAY
from doit.allocate import PERIOD_DAYS
from doit.allocate import balance
from doit.allocate import candidates
from doit.allocate import draw
from doit.allocate import effective_weights
from doit.allocate import first_draw_probabilities
from doit.allocate import implied_intervals
from doit.allocate import implied_shares
from doit.allocate import period_amount
from doit.cadence import parse_cadence
from doit.journal import bump_counts
from doit.journal import checkoff_equivalent
from doit.journal import counts_by_machine
from doit.journal import counts_path
from doit.journal import days_since
from doit.journal import journal_path
from doit.journal import latest_occurrence
from doit.journal import load_counts
from doit.journal import new_id
from doit.journal import rate_per_day
from doit.paths import machine_name
from doit.paths import xdg_cache_home
from doit.paths import xdg_config_home
from doit.paths import xdg_state_home
from doit.render import can_prompt
from doit.render import console
from doit.render import error_console
from doit.render import first_sentence
from doit.render import join_context

REGISTER = Path(os.environ.get('DOIT_PURSUITS') or xdg_config_home() / 'doit' / 'pursuits.yml')
JOURNAL_DIR = Path(os.environ.get('DOIT_JOURNAL_DIR') or xdg_state_home() / 'doit')
CACHE_DIR = Path(os.environ.get('DOIT_CACHE_DIR') or xdg_cache_home() / 'doit')
DRAW_CACHE = CACHE_DIR / 'next-draw.json'
NAMES_CACHE = CACHE_DIR / 'next-names.txt'

# Long enough to survive deciding what to do, short enough that coming back after
# a task gives a fresh draw. Re-running inside the window is the common case —
# glance, get interrupted, glance again — and a reshuffle there reads as the tool
# having changed its mind.
CACHE_MINUTES = int(os.environ.get('DOIT_CACHE_MINUTES') or 15)

DRAW_SIZE = 5

# How many names the one-line standing summary spells out before counting the
# rest. It is a glance rather than a report, and a line that wraps is one nobody
# reads to the end.
STANDING_NAMES = 4

# How many weeks of its own schedule a balance may drift before it is called out.
# Two is far enough that a bad fortnight cannot explain it, which leaves the
# stated weight as the thing to argue with.
DEFAULT_WARN_WEEKS = 2.0

# Width of the "weight · standing" column, sized for the widest balance a real
# register produces so the resolved item starts at one column on every row.
STATUS_COLUMN = 18

# Everything a row prints before its resolved title: two spaces of indent, the
# index and its space, then the name column, two spaces, the status column and
# two more. Only the name column varies, so the rest is a constant the
# continuation line adds to it to sit directly under the title.
CONTINUATION_INDENT = 8 + STATUS_COLUMN

# A resolver is one network call to a product CLI. They run concurrently and only
# for what was actually drawn, so this is the whole wait, not a per-pursuit one.
RESOLVE_TIMEOUT_SECONDS = 5.0

# Fields a pursuit may declare. Anything else is a typo, and a typo in a weight
# file is silent damage — it would allocate attention by a number nobody wrote.
KNOWN_FIELDS = {
    'description',
    'weight',
    'cadence',
    'until',
    'paused',
    'catchup_exponent',
    'warn_weeks',
    'resolve',
    'resolve_where',
    'items',
    'label',
    'id',
    'context',
    'detail',
    'view',
    'on_log',
    'evidence',
    'evidence_time',
    'evidence_items',
    'evidence_where',
    'evidence_files',
    'minutes',
}

TEMPLATE = """\
# Weighted pursuits, read by `doit next`. Hand-edit freely — this tool only reads.
#
# A pursuit is a strand of life you want to spend attention on. Weights are
# relative magnitudes, not percentages: 35/30/70 is fine and nothing has to add
# up. `doit pursuits list` shows the share each weight actually implies.
#
#   weight       required; how much attention this deserves relative to the rest
#   description  what it means, shown when there is nothing resolved to show
#   minutes      optional; the size of one checkoff, in minutes. Declaring it is
#                what makes a pursuit measured in time rather than in occurrences
#   cadence      optional hard schedule (2w / 1mo); a checkoff owed pins it above
#                the draw
#   until        optional end date; after it the pursuit pauses and says so
#   paused       optional; keeps it in the file but out of the draw. A pause has
#                no timestamp, so pair unpausing with `doit pursuits reset` — the
#                schedule kept asking while nothing was reading the answer
#   catchup_exponent  optional; how sharply urgency climbs once a checkoff is
#                owed. Superlinear above 1, so the 1.5 default lets something well
#                behind outrun a heavier pursuit that is current
#   warn_weeks   optional; how far this one may drift before it is called out,
#                overriding the register-wide `balance.warn_weeks` below
#   resolve      optional command answering "specifically what?" — see below
#   on_log       optional command run after logging, e.g. completing the task
#
# A cadence replaces the interval the weight implies rather than sitting beside
# it, so declaring one shorter than that interval multiplies how urgent the
# pursuit gets, and one longer divides it. `doit pursuits list` names both.
#
# Standing is one running balance per pursuit: what the schedule has asked for
# since its zero point, less what has been done. Positive is behind and negative
# is ahead, in minutes where `minutes:` is declared and in checkoffs where it is
# not. Nothing is capped, so a burst counts for what it was and partial time
# always rolls over — a 20-minute read against a 45-minute checkoff pays 20
# minutes off the balance. `doit pursuits reset <pursuit>` moves the zero point to
# now, which is what to do after a long pause; with no name it moves every one.
#
# A balance further from current than `warn_weeks` of that pursuit's own schedule
# is reported by `doit next`. It is evidence the stated weight is wrong, since
# nothing else in the model bends to absorb it.
#
# resolve prints either plain lines (first line wins) or JSON. For JSON, name the
# fields to read: `label` for what to show, `id` for what on_log substitutes into,
# and `items` when the list is nested inside the document.
#
# How many rows come back is the register saying whether it means one thing or
# several. One row is a decision, so the draw shows it and `doit log` records it.
# Several are candidates: the first still renders as context, the row says how
# many more there are, and the log names none of them — an hour of reading is not
# a claim about which book. Narrow the command to one where you mean one.
#
#   resolve_where  optional field: value pairs keeping only the rows that are
#                  this pursuit. Narrow the command itself where the backend can;
#                  reach for this where it has no filter for the distinction.
#
# A title alone rarely says enough to pick something, so two more fields put the
# rest of the resolved row on screen. Both take dotted paths, and a number in one
# indexes a list (`projects.0.name`):
#
#   context      where it lives — one path or several, joined
#   detail       the field to take a one-sentence gist from, usually notes
#   view         command that opens the item in full, {id} substituted
#
# Where resolve asks "what should I do?", evidence asks the same app "did I
# already?" — so a pursuit you satisfy through its own CLI stops being offered
# without you also logging it here. The later of the two answers wins, and the
# apps are asked on a timer rather than per command. `doit pursuits evidence`
# asks them now and names any that could not be reached.
#
#   evidence        command printing JSON rows of what got done
#   evidence_time   the timestamp field on each row
#   evidence_items  optional key holding the rows when they are nested
#   evidence_where  optional field: value pairs selecting the rows that count
#   evidence_files  a directory instead of a command, for a practice whose
#                   output is files — the newest one is when it last happened

pursuits:
  chores:
    description: The maintenance list nobody else is going to do
    weight: 25
    cadence: 1w
    resolve: icb tasks list --limit 3 --json
    label: name
    id: id
    context: category
    view: icb tasks show {id}
    on_log: icb tasks complete {id}
    evidence: icb tasks list --status completed --limit 100 --json
    evidence_time: complete_date

  read-library:
    description: Read what is already on the shelf
    weight: 30
    minutes: 30
    resolve: icb books list --progress reading --json
    label: title
    context: author

  study-computer-science:
    description: Work the CS track rather than reading about working it
    weight: 35
    minutes: 45
    resolve: learning overview --json
    items: in_progress_resources
    label: title
    detail: notes

# How far any pursuit may drift from current before `doit next` says so, in weeks
# of that pursuit's own schedule. A single pursuit overrides it with `warn_weeks`.
balance:
  warn_weeks: 2
"""


class RegisterError(Exception):
    """A pursuits file that cannot be trusted to allocate attention correctly."""


def load_pursuits(path: Path | None = None) -> dict:
    """The register, validated. Raises rather than guessing at a malformed entry.

    Every failure here is silent misallocation if it were tolerated: a missing
    weight would default to something nobody chose, a misspelled field would be
    ignored, and either way the tool would confidently offer the wrong things for
    months. A weight file has to be right or refuse to run.

    The default is read at call time rather than bound as a parameter default, so
    a test repointing REGISTER is seen by everything that reads it.
    """
    path = REGISTER if path is None else path
    if not path.exists():
        return {}
    document = yaml.safe_load(path.read_text()) or {}
    pursuits = document.get('pursuits') or {}
    if not isinstance(pursuits, dict):
        raise RegisterError(f'{path}: `pursuits` must be a mapping of name to settings')

    for name, config in pursuits.items():
        if not isinstance(config, dict):
            raise RegisterError(f'{name}: must be a mapping of settings, not {type(config).__name__}')
        unknown = set(config) - KNOWN_FIELDS
        if unknown:
            # The accepted set is named because this is the one error a register
            # written against an older schema hits, and every pursuits command
            # goes dark until the file is edited. A reader who can see
            # `catchup_exponent` in the list can fix `alpha` without leaving the
            # terminal; one told only what is wrong cannot.
            raise RegisterError(f'{name}: unknown field(s) {", ".join(sorted(unknown))}. Accepted: {", ".join(sorted(KNOWN_FIELDS))}')
        weight = config.get('weight')
        if not isinstance(weight, int | float) or isinstance(weight, bool) or weight < 0:
            raise RegisterError(f'{name}: weight must be a non-negative number')
        if config.get('cadence') and parse_cadence(config['cadence']) <= 0:
            raise RegisterError(f'{name}: cadence must look like 10d / 2w / 1mo / 1y')
        if config.get('until') and not isinstance(config['until'], date):
            raise RegisterError(f'{name}: until must be a date (YYYY-MM-DD)')
        size = config.get('minutes')
        if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size <= 0):
            raise RegisterError(f'{name}: minutes must be a positive whole number')
        exponent = config.get('catchup_exponent')
        if exponent is not None and (not isinstance(exponent, int | float) or isinstance(exponent, bool) or exponent <= 0):
            raise RegisterError(f'{name}: catchup_exponent must be a positive number')
        weeks = config.get('warn_weeks')
        if weeks is not None and (not isinstance(weeks, int | float) or isinstance(weeks, bool) or weeks <= 0):
            raise RegisterError(f'{name}: warn_weeks must be a positive number')
        if config.get('on_log') and not config.get('resolve'):
            raise RegisterError(f'{name}: on_log needs resolve — there is no item to act on without it')
        if (config.get('context') or config.get('detail')) and not config.get('label'):
            raise RegisterError(f'{name}: context and detail read fields off the resolved row, so they need label')
        if config.get('view') and not config.get('resolve'):
            raise RegisterError(f'{name}: view needs resolve — there is no item to look at without it')
    return pursuits


def register_block(key: str, known: set[str], path: Path | None) -> dict:
    """One of the register's sibling blocks, validated against the keys it accepts.

    Siblings of `pursuits:` rather than keys on one, because each states a fact
    about the person and not about any single strand. Read from the same file so
    there is one thing to hand-edit and one thing to sync.

    An unknown key is refused here for the reason it is refused on a pursuit: a
    file that turns one typo away silently and refuses another teaches the reader
    it is strict, and they stop proofreading the half that is not. `warn_weekz: 2`
    reverting to a default nobody chose is the same silent misallocation a
    misspelled `weight` would be.
    """
    path = REGISTER if path is None else path
    if not path.exists():
        return {}
    document = yaml.safe_load(path.read_text()) or {}
    block = document.get(key) or {}
    if not isinstance(block, dict):
        raise RegisterError(f'{path}: `{key}` must be a mapping of settings')
    unknown = set(block) - known
    if unknown:
        raise RegisterError(f'{path}: {key} has unknown key(s) {", ".join(sorted(unknown))}. Accepted: {", ".join(sorted(known))}')
    return block


def load_settings(path: Path | None = None) -> dict:
    """The register's `forecast:` block — what a day is assumed to hold."""
    settings = register_block('forecast', {'budget_minutes'}, path)
    budget = settings.get('budget_minutes')
    if budget is not None and (not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0):
        raise RegisterError(f'{REGISTER if path is None else path}: forecast.budget_minutes must be a positive whole number')
    return settings


def load_balance_settings(path: Path | None = None) -> dict:
    """The register's `balance:` block — how far from current is worth reporting."""
    settings = register_block('balance', {'warn_weeks'}, path)
    weeks = settings.get('warn_weeks')
    if weeks is not None and (not isinstance(weeks, int | float) or isinstance(weeks, bool) or weeks <= 0):
        raise RegisterError(f'{REGISTER if path is None else path}: balance.warn_weeks must be a positive number')
    return settings


def term_ended(config: dict, today: date) -> bool:
    """Whether a time-boxed pursuit is past its `until` date."""
    until = config.get('until')
    if not until:
        return False
    return bool(until < today)


def is_active(config: dict, today: date) -> bool:
    """Paused, term-ended, and zero-weight pursuits stay in the file but out of the draw."""
    return not config.get('paused') and not term_ended(config, today) and config.get('weight', 0) > 0


def declared_minutes(register: dict) -> dict[str, float]:
    """Every pursuit's checkoff size, over the whole register rather than the active set.

    A record keeps the size it was logged under whatever happens to the pursuit
    afterwards. Scoping this to the active set instead lets pausing one timed
    pursuit reclassify its entire history as one-checkoff-per-entry, and that
    number is the single divisor every other pursuit's interval is derived from.
    """
    return {name: float(config['minutes']) for name, config in register.items() if config.get('minutes')}


def records_by_pursuit(records: list[dict]) -> dict[str, list[dict]]:
    """The journal indexed by pursuit, so one state build is one pass over it.

    :mod:`doit.forecast` calls :func:`build_state` thousands of times against a
    journal it is growing as it goes, and a scan per pursuit per simulated day is
    what turns a reading into a wait.
    """
    found: dict[str, list[dict]] = {}
    for record in records:
        found.setdefault(str(record.get('pursuit')), []).append(record)
    return found


def zero_point(reset: datetime | None, now: datetime, interval: float, window: float | None) -> datetime:
    """When a pursuit's balance starts counting.

    An explicit ``doit pursuits reset`` sets it. Without one the origin is a
    single interval back, which opens a pursuit exactly one checkoff behind and
    keeps it there until something is logged: the pursuit was declared because it
    is wanted, and doit cannot know when it was added.

    Nothing about a payment may move this. Deriving the origin from the oldest
    thing on record does exactly that, and in the wrong direction — a backdated
    log drags the origin behind itself, so the schedule bills for the whole span
    it opened up and the entry credits one checkoff against it. Recording that
    you did the thing then increases what you owe.

    ``window`` bounds how far back the demand side may run for a pursuit whose
    payments come from a source that only remembers so far. Billing over a span
    the credit side cannot answer for is a debt that grows by construction, on
    the pursuit most reliably done.
    """
    opened = reset if reset is not None else now - timedelta(days=0.0 if math.isinf(interval) else interval)
    if window is None:
        return opened
    return max(opened, now - timedelta(days=window))


def completed_since(records: list[dict], app_days: list[date], now: datetime, origin: datetime, minutes: float | None) -> float:
    """Everything done since ``origin``, in the pursuit's own unit.

    A typed entry contributes the duration it carries where a checkoff size is
    declared, and one whole checkoff where none is — so an hour typed as four
    fragments and an hour typed once pay the same amount off the balance, and no
    fragment can strand.

    An app day the journal already carries is one act reported by both records, so
    it counts once. An app answers in days rather than durations, so a day it
    reports is one checkoff whatever happened inside it. That is also what keeps a
    backend emitting a row per task from outrunning one emitting a row per session.
    """
    size = minutes or 1.0
    total = 0.0
    typed_days = set()
    for record in records:
        if record.get('event') != journal.Event.DONE:
            continue
        when = journal.parse_time(record.get('occurred_at') or record.get('logged_at'))
        if when is None or when < origin:
            continue
        typed_days.add(journal.local_day(record, now))
        total += checkoff_equivalent(record, minutes) * size
    opened = origin.astimezone(now.tzinfo).date()
    for day in app_days:
        if day >= opened and day not in typed_days:
            total += size
    return total


def merged_span_days(spans: list[tuple[datetime, datetime]]) -> float:
    """Total days covered by a set of possibly overlapping spans.

    Renewing a skip before the last one expires is the ordinary case, and adding
    the two lengths would take the same fortnight off the clock twice.
    """
    total = 0.0
    reached: datetime | None = None
    for start, finish in sorted(spans):
        begin = start if reached is None or start > reached else reached
        if finish <= begin:
            continue
        total += (finish - begin).total_seconds() / 86400.0
        reached = finish
    return total


def skipped_days(records: list[dict], now: datetime, origin: datetime) -> float:
    """Days inside the balance window that a skip took out of the schedule.

    A pass is a decision not to do the thing, never a decision to owe it later, so
    the clock stops for as long as the skip runs. A skip carrying no expiry states
    nothing about a span and takes nothing out.
    """
    spans = []
    for record in records:
        if record.get('event') != journal.Event.SKIP:
            continue
        start = journal.parse_time(record.get('occurred_at') or record.get('logged_at'))
        finish = journal.parse_time(record.get('expires_at'))
        if start is None or finish is None:
            continue
        spans.append((max(start, origin), min(finish, now)))
    return merged_span_days(spans)


def skip_expiry(records: list[dict]) -> datetime | None:
    """When the newest skip on record runs out, or None where none does.

    The newest rather than the one reaching furthest, so a later skip can shorten
    an earlier one and `doit pursuits resume` can end one outright. Taking the
    maximum expiry instead makes a mistyped `--for 1y` unreachable by any command
    the tool ships, on an append-only file that is never rewritten.
    """
    newest: datetime | None = None
    expires: datetime | None = None
    for record in records:
        if record.get('event') != journal.Event.SKIP:
            continue
        when = journal.parse_time(record.get('occurred_at') or record.get('logged_at'))
        finish = journal.parse_time(record.get('expires_at'))
        if when is None or finish is None:
            continue
        if newest is None or when >= newest:
            newest, expires = when, finish
    return expires


def build_state(
    pursuits: dict,
    now: datetime,
    records: list[dict] | None = None,
    observed: dict[str, datetime] | None = None,
    balance_settings: dict | None = None,
) -> dict:
    """Everything the draw and every view need: rates, intervals, urgency, weights.

    Assembled in one place and passed around, because the same numbers are what
    gets drawn on, what gets displayed by `--explain`, and what gets recorded into
    the journal as the state at the moment of a log. Recomputing them per view is
    how those three drift apart. The draw pool and every pursuit's warning band
    are here for that reason and not because the draw needs them assembled: a
    renderer that reaches past this to the register reads a file a simulated day
    was never running against.

    ``records``, ``observed`` and ``balance_settings`` default to the journal on
    disk, a live round trip to every backend, and the register's `balance:` block.
    :mod:`doit.forecast` supplies all three instead, which is what lets a
    simulated day run this function rather than a second copy of the model — a
    copy is the only way the forecast could come to disagree with the draw it
    claims to predict. Injecting them is also what keeps a thirty-day simulation
    from making thirty evidence calls and thirty file reads per replicate.
    """
    today = now.date()
    active = {name: config for name, config in pursuits.items() if is_active(config, today)}
    weights = {name: float(config['weight']) for name, config in active.items()}
    # Declaring `minutes:` is the only thing that makes a pursuit measured in
    # time, and the register is what says so. Scoped to the whole file rather
    # than to the active set, because the rate below walks every record in the
    # journal: a paused pursuit's history has to keep the size it was logged
    # under, or pausing one timed pursuit reclassifies its whole past as
    # one-checkoff-per-entry and moves the divisor every other interval reads.
    checkoff_minutes = declared_minutes(pursuits)
    sizes = {name: checkoff_minutes.get(name, 1.0) for name in active}

    if records is None:
        records = journal.read_all(JOURNAL_DIR) if JOURNAL_DIR.exists() else []
    if balance_settings is None:
        balance_settings = load_balance_settings()
    register_warn_weeks = float(balance_settings.get('warn_weeks') or DEFAULT_WARN_WEEKS)
    measured_rate = rate_per_day(records, now, checkoff_minutes)
    logs_per_day = measured_rate if measured_rate is not None else FALLBACK_LOGS_PER_DAY

    shares = implied_shares(weights)
    implied = implied_intervals(weights, logs_per_day)
    intervals = dict(implied)
    # An explicit cadence is a statement about the world, not about relative
    # attention, so it wins over the interval the weight implies. Both are kept
    # because urgency divides by the one that won: declaring a cadence shorter
    # than the implied interval multiplies how urgent the pursuit gets by the
    # ratio between them, and a register cannot be read without seeing that.
    for name, config in active.items():
        if config.get('cadence'):
            intervals[name] = float(parse_cadence(config['cadence']))

    # The apps are asked before the draw is weighed, so a pursuit satisfied in its
    # own CLI stops being offered without anyone retyping it here.
    observations = {} if observed is not None else evidence.refresh(active, CACHE_DIR, now)
    seen = evidence.observed(observations) if observed is None else observed
    seen_days = evidence.occurrences(observations)
    last_done = evidence.merged(latest_occurrence(records, journal.Event.DONE), seen)
    elapsed = days_since(last_done, list(active), now)

    # Both records feed the balance. A journal-only reading made `tasks` overdue
    # on a day eight of them were completed inside `icb`, so a pursuit with a
    # backend has to count what that backend saw as well as what got retyped.
    mine = records_by_pursuit(records)
    reset_at = latest_occurrence(records, journal.Event.RESET)
    origins: dict[str, datetime] = {}
    balances: dict[str, float] = {}
    suppressed: set[str] = set()
    for name, config in active.items():
        own = mine.get(name, [])
        app_days = seen_days.get(name, [])
        # An app remembers a bounded number of days, so a pursuit paid through
        # one is only billable over the span that source can answer for.
        window = float(evidence.OCCURRENCE_WINDOW_DAYS) if evidence.answerable(config) else None
        origin = zero_point(reset_at.get(name), now, intervals[name], window)
        origins[name] = origin
        done = completed_since(own, app_days, now, origin, checkoff_minutes.get(name))
        # The clock stops for the span a skip covers, so passing on something is
        # never a way to owe more of it later.
        span = max((now - origin).total_seconds() / 86400.0 - skipped_days(own, now, origin), 0.0)
        balances[name] = balance(span, intervals[name], sizes[name], done)
        standing = skip_expiry(own)
        if standing is not None and standing > now:
            suppressed.add(name)

    exponents = {name: float(config.get('catchup_exponent', DEFAULT_CATCHUP_EXPONENT)) for name, config in active.items()}
    effective = effective_weights(weights, balances, sizes, exponents, suppressed)
    # Resolved here rather than at the renderer, so `--explain` and every journal
    # entry report the pool the draw actually samples. Deriving the odds from
    # `effective` while sampling something else put five chosen rows on screen at
    # 0.0% each, under a legend calling that number the chance of being drawn.
    ratios = {name: balances[name] / sizes[name] for name in active}
    pool = candidates(effective, weights, ratios, suppressed, DRAW_SIZE)
    # Over every active pursuit rather than over the pool's own members, because
    # a row absent from the pool has a real answer — zero — and every view here
    # walks the active set. Reporting only the members leaves the others unpriced
    # on a table that has a column for it.
    odds = first_draw_probabilities(pool)
    probability = {name: odds.get(name, 0.0) for name in active}
    bands = {name: float(config.get('warn_weeks') or register_warn_weeks) for name, config in active.items()}

    return {
        'now': now,
        'today': today,
        'pursuits': pursuits,
        'active': active,
        'weights': weights,
        'shares': shares,
        'intervals': intervals,
        'implied_intervals': implied,
        'days_since': elapsed,
        'balance': balances,
        'checkoff_size': sizes,
        'checkoff_minutes': checkoff_minutes,
        'origins': origins,
        'suppressed': sorted(suppressed),
        'warn_weeks': bands,
        'effective': effective,
        'pool': pool,
        'probability': probability,
        'logs_per_day': logs_per_day,
        'measured_rate': measured_rate,
        'last_done': {name: when.isoformat() for name, when in last_done.items()},
        'records': records,
        'observed': seen,
        # Folded from the refresh above rather than re-read, so `drift` and the
        # draw can never disagree about which days an app reported.
        'evidence_days': seen_days,
        'evidence_errors': evidence.problems(observations),
    }


def pinned(state: dict) -> list[str]:
    """Pursuits with a hard cadence owing at least one checkoff, furthest behind first.

    Pinned rather than sampled on purpose. A weighted draw makes an overdue thing
    likely, and likely is not good enough for the case this tool exists for — the
    chore that has been at the top of the task list for a year does not need better
    odds, it needs to stop being optional.

    A standing skip reaches a pin, where the sampled half is answered by a zero
    weight. A skip names the span it covers, so honoring it in both halves is
    what makes it a decision rather than a reroll.
    """
    passed = set(state['suppressed'])
    owing = []
    for name, config in state['active'].items():
        if not config.get('cadence') or name in passed:
            continue
        ratio = state['balance'][name] / state['checkoff_size'][name]
        if ratio >= 1.0:
            owing.append((name, ratio))
    owing.sort(key=lambda row: -row[1])
    return [name for name, _ in owing]


def compute_draw(state: dict, seed: int | None = None) -> dict:
    """Draw the pins plus enough sampled pursuits to fill the screen."""
    pins = pinned(state)
    sampled = {name: weight for name, weight in state['pool'].items() if name not in pins}
    rng = random.Random(seed) if seed is not None else random.Random()
    drawn = draw(sampled, max(DRAW_SIZE - len(pins), 0), rng)
    return {
        'draw_id': new_id(state['now']),
        'created_at': state['now'].isoformat(),
        'pinned': pins,
        'drawn': drawn,
    }


def load_cached_draw(now: datetime) -> dict | None:
    """The draw from the last few minutes, or None once it has aged out."""
    if not DRAW_CACHE.exists():
        return None
    try:
        cached = json.loads(DRAW_CACHE.read_text())
    except json.JSONDecodeError:
        return None
    created = journal.parse_time(cached.get('created_at'))
    if created is None or now - created > timedelta(minutes=CACHE_MINUTES):
        return None
    return cached


def save_cached_draw(payload: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DRAW_CACHE.write_text(json.dumps(payload, indent=2) + '\n')


def mark_logged(name: str, now: datetime) -> None:
    """Note that a pursuit has been done against the standing draw.

    Marked rather than dropped, and the draw kept rather than discarded. A log
    reads the drawn list for `was_offered` and `rank_in_draw`, and the next log in
    the same window takes its item from the resolved map instead of asking the
    backend again — both need the draw to survive being partly done.
    """
    cached = load_cached_draw(now)
    if cached is None:
        return
    cached['logged'] = sorted(set(cached.get('logged') or []) | {name})
    save_cached_draw(cached)


def without_logged(selection: dict) -> dict:
    """The draw as it should be shown: everything already logged taken out.

    A shallow copy sharing the resolved map, so repairing a failed row still
    reaches the rows on screen.
    """
    logged = set(selection.get('logged') or [])
    return {
        **selection,
        'pinned': [name for name in selection.get('pinned', []) if name not in logged],
        'drawn': [name for name in selection.get('drawn', []) if name not in logged],
    }


def write_names_cache(pursuits: dict) -> None:
    """Rewrite the name<TAB>description file the shell completion reads.

    Completion has to be instant and starting this process is not, so every run
    leaves behind a flat file the completion function can read with no subprocess
    at all.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f'{name}\t{config.get("description", "")}' for name, config in sorted(pursuits.items())]
    NAMES_CACHE.write_text('\n'.join(lines) + '\n' if lines else '')


def dig(document, path: str):
    """Follow a dotted path into a JSON document, returning None if it dead-ends.

    A numeric key indexes a list, so `projects.0.name` reaches into the array a
    backend returns for a many-to-many membership. Without it the only reachable
    fields are the row's own scalars, which is exactly the context an item does
    not carry — what it belongs to lives one level down.
    """
    current = document
    for key in path.split('.'):
        if isinstance(current, list):
            if not key.isdigit() or int(key) >= len(current):
                return None
            current = current[int(key)]
            continue
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def resolve_one(name: str, config: dict) -> dict | None:
    """Ask the owning CLI what this pursuit means right now.

    Never `shell=True`: the command comes from a config file, and while that file
    is yours, a tool that hands config text to a shell has a class of bug you
    cannot audit away later.
    """
    command = config.get('resolve')
    if not command:
        return None
    try:
        result = subprocess.run(
            shlex.split(command),
            capture_output=True,
            text=True,
            timeout=RESOLVE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'pursuit': name, 'error': str(error), 'backend': shlex.split(command)[0]}
    if result.returncode != 0:
        # A backend can fail with nothing on either stream (`false`, a bare
        # non-zero exit), so the status is the message of last resort — the row
        # still has to say something rather than render an empty value.
        said = (result.stderr or result.stdout).strip().splitlines()
        return {
            'pursuit': name,
            'error': said[0] if said else f'exited {result.returncode}',
            'backend': shlex.split(command)[0],
        }

    label_field = config.get('label')
    if not label_field:
        first = next((line for line in result.stdout.splitlines() if line.strip()), '')
        return {'pursuit': name, 'label': first.strip()} if first else None

    try:
        document = json.loads(result.stdout or 'null')
    except json.JSONDecodeError:
        return {'pursuit': name, 'error': 'resolve did not return JSON but names a label field'}
    rows = dig(document, config['items']) if config.get('items') else document
    # A backend that already picked the one thing returns an object, not a list of
    # one — `icb overview` does exactly this for the next project item. Treating it
    # as a single row keeps the register from needing a wrapper command.
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or not rows:
        return None
    # The counterpart to `evidence_where`, for the same reason: a backend with no
    # filter for the distinction you are drawing returns everything and only some
    # of it is the pursuit. Narrow the command first where the API can — this is
    # what is left when it cannot.
    rows = evidence.matching(rows, config.get('resolve_where'))
    if not rows:
        return None
    row = rows[0]
    if not isinstance(row, dict):
        return {'pursuit': name, 'candidates': len(rows), 'label': str(row)}
    identifier = row.get(config['id']) if config.get('id') else None
    return {
        'pursuit': name,
        # How many rows the backend matched, not how many are shown. A resolve
        # narrowed to one thing is a decision the register asked for; three books
        # equally in progress are candidates, and the first is not the answer to
        # anything. Everything downstream reads this before claiming a pick.
        'candidates': len(rows),
        'label': str(row.get(label_field, '')).strip(),
        'id': None if identifier is None else str(identifier),
        'context': row_context(row, config.get('context')),
        'detail': first_sentence(str(dig(row, config['detail']) or '')) if config.get('detail') else '',
        'view': view_command(config.get('view'), identifier),
        'backend': shlex.split(config['resolve'])[0],
        'raw': row,
    }


def view_command(template: str | None, identifier) -> str:
    """The command that opens the offered item, with its id filled in.

    Empty when the template wants an id the backend did not give, because a
    command printed with a hole in it reads as something you could run.
    """
    if not template:
        return ''
    if '{id}' in template and identifier is None:
        return ''
    return template.format(id='' if identifier is None else identifier)


def row_context(row: dict, paths) -> str:
    """Where the resolved item lives, from the fields the register names.

    Several paths rather than one: an item is placed by more than one fact, and a
    path that dead-ends drops out rather than contributing an empty segment.
    """
    if isinstance(paths, str):
        paths = [paths]
    return join_context(dig(row, path) for path in paths or [])


def resolve_all(names: list[str], pursuits: dict) -> dict[str, dict]:
    """Resolve only what was drawn, concurrently. The rest is never asked."""
    wanted = [name for name in names if pursuits.get(name, {}).get('resolve')]
    if not wanted:
        return {}
    with ThreadPoolExecutor(max_workers=len(wanted)) as pool:
        results = pool.map(lambda name: resolve_one(name, pursuits[name]), wanted)
    return {name: result for name, result in zip(wanted, results, strict=True) if result}


def retry_failed_resolves(selection: dict, pursuits: dict) -> None:
    """Ask again for the rows whose backend failed, leaving the draw itself alone.

    The draw is cached so that running it three times while deciding does not
    reshuffle it. A failure is not an answer, though, so caching one pins a dead
    message to the row for the rest of the window — a backend that came back, or a
    register entry that was corrected, keeps showing the old error. Rerolling would
    clear it and change the very thing the cache is protecting.
    """
    resolved = selection.get('resolved') or {}
    failed = [name for name, detail in resolved.items() if isinstance(detail, dict) and detail.get('error')]
    if not failed:
        return
    for name in failed:
        resolved.pop(name)
    resolved.update(resolve_all(failed, pursuits))
    selection['resolved'] = resolved
    save_cached_draw(selection)


def todays_context() -> list[str]:
    """Today's events and imminent countdowns — context, never candidates.

    An event is something happening, not something to choose, so it renders as a
    banner and takes no part in the draw.
    """
    try:
        result = subprocess.run(
            ['icb', 'overview', '--json'],
            capture_output=True,
            text=True,
            timeout=RESOLVE_TIMEOUT_SECONDS,
            check=False,
        )
        payload = json.loads(result.stdout or '{}')
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []

    today = date.today()
    lines = []
    for event in (payload.get('events') or {}).get('items') or []:
        when = journal.parse_time(event.get('date'))
        if when and when.date() == today:
            lines.append(f'{event.get("name", "")} · {event.get("venue") or "today"}')
    for countdown in (payload.get('countdowns') or {}).get('items') or []:
        due = countdown.get('due_date')
        if not due:
            continue
        days = (date.fromisoformat(due) - today).days
        if 0 <= days <= 14:
            lines.append(f'{countdown.get("name", "")} in {days}d')
    return lines


def format_elapsed(days: float | None) -> str:
    if days is None:
        return 'never'
    if days < 1:
        return 'today'
    if days < 14:
        return f'{int(days)}d ago'
    if days < 90:
        return f'{int(days / 7)}w ago'
    return f'{int(days / 30)}mo ago'


def urgency_multiplier(state: dict, name: str) -> str:
    """What a declared cadence does to urgency, when it does anything worth saying.

    Urgency divides elapsed time by whichever interval won, so a cadence shorter
    than the one the weight implies multiplies the pursuit's effective weight by
    that ratio raised to the catchup exponent, and a longer one divides it. The
    stated weight is then not what the pursuit gets, and nothing else on the row
    says so — which is how a `1d` beside `weight: 25` came to take a third of
    every draw.

    Silent inside a tenth, because a ratio that close is the measured logging
    rate wobbling rather than a decision anyone made.
    """
    declared = state['intervals'].get(name)
    implied = state['implied_intervals'].get(name)
    if not declared or not implied or math.isinf(implied):
        return ''
    ratio = implied / declared
    if 0.9 <= ratio <= 1.1:
        return ''
    return f' ×{ratio:.1f}' if ratio > 1 else f' ÷{1 / ratio:.1f}'


def format_amount(size: float, minutes: float | None) -> str:
    """An amount in a pursuit's own unit, unsigned. A band, never a standing."""
    return f'{size:.0f}m' if minutes else f'{size:.1f}'


def format_balance(owed: float, minutes: float | None) -> str:
    """How far behind or ahead a pursuit stands, in the unit it is measured in.

    Signed always, because the sign carries the whole reading and a bare `45m`
    says nothing about which side of current it names. Positive is owed.

    Minutes are whole and checkoffs keep a decimal. A checkoff is the coarse unit
    already, so rounding one to a whole number turns two thirds of a chore into
    either nothing or a whole one.
    """
    if minutes:
        return f'{owed:+.0f}m'
    return f'{owed:+.1f}'


def warn_threshold(state: dict, name: str) -> float:
    """How far this pursuit may drift from current before it is worth reporting.

    Weeks of the pursuit's own schedule rather than a flat amount, so a strand
    asking for three hours a week and one asking for a chore a fortnight are held
    to the same standard in units neither of them shares.

    Floored at one checkoff, because weeks and checkoffs are different units and
    the band falls below one whenever the interval is longer than the band is.
    At `cadence: 1mo` and two weeks the arithmetic gives 0.47 of a chore, so a
    single chore coming due would be reported as a weight that is not true — on
    the same screen that pins it as due, with doing it as neither offered remedy.
    """
    size = state['checkoff_size'][name]
    band = state['warn_weeks'][name] * period_amount(state['intervals'][name], size, PERIOD_DAYS)
    return max(band, size)


def out_of_band(state: dict) -> list[tuple[str, float, float]]:
    """Every pursuit further from current than its own threshold, furthest first.

    A balance this large is a claim about the weight rather than about the week.
    The register is asking for an amount that is not being lived, in one direction
    or the other, and editing it is what settles that.
    """
    found = []
    for name in state['active']:
        threshold = warn_threshold(state, name)
        owed = state['balance'][name]
        if threshold > 0 and abs(owed) > threshold:
            found.append((name, owed, abs(owed) / threshold))
    found.sort(key=lambda row: -row[2])
    return found


def render_out_of_band(state: dict) -> None:
    """Name whatever has drifted past its band, the band it passed, and the repair.

    Printed where the draw is, because that is the moment the register is being
    acted on. A pursuit far ahead is reported alongside one far behind: both say
    the weight is wrong, and only one of them ever feels like it.
    """
    drifted = out_of_band(state)
    if not drifted:
        return
    console.print(Text('  Weights the balance disagrees with:', style='yellow'))
    for name, owed, _ in drifted:
        minutes = state['checkoff_minutes'].get(name)
        line = Text('    ')
        line.append(name, style='yellow')
        line.append(f'  {format_balance(owed, minutes)} against a band of {format_amount(warn_threshold(state, name), minutes)}')
        console.print(line)
    console.print('  Edit the weight, or [cyan]doit pursuits reset <pursuit>[/] to start again\n')


def standing_line(state: dict) -> str:
    """What owes at least one checkoff, each in its own unit.

    A whole checkoff rather than any positive balance, because a balance climbs
    continuously from zero and every pursuit passes through the fraction just
    above it after every checkoff. Reporting those says "behind" beside a number
    that renders as zero, and names nothing anyone can act on.

    Minutes and checkoffs do not add, so there is no total to report and one is
    not invented. The names are what the reader acts on anyway.
    """
    owing = [(name, state['balance'][name]) for name in state['active'] if state['balance'][name] >= state['checkoff_size'][name]]
    if not owing:
        return ''
    owing.sort(key=lambda row: -row[1] / state['checkoff_size'][row[0]])
    detail = ', '.join(f'{name} {format_balance(owed, state["checkoff_minutes"].get(name))}' for name, owed in owing[:STANDING_NAMES])
    return f'behind · {detail}' + ('  ·  doit pursuits list' if len(owing) > STANDING_NAMES else '')


def render_row(index: int, name: str, state: dict, resolved: dict, pin: bool, width: int) -> None:
    # Every read of the state is guarded, because the draw outlives the register
    # by up to a quarter of an hour. Pausing a drawn pursuit, or an `until` that
    # passes at midnight, leaves a cached row naming something outside the active
    # set — and a row that cannot be priced still has to render.
    config = state['active'].get(name, {})
    weight = int(state['weights'].get(name, 0))
    owed = state['balance'].get(name)
    when = '—' if owed is None else format_balance(owed, state['checkoff_minutes'].get(name))

    detail = resolved.get(name) or {}
    failure = detail.get('error')
    text = detail.get('label') or config.get('description') or ''
    if failure:
        # What the backend said, never a verdict about the backend. A register
        # naming a verb the CLI dropped fails identically to one that is logged
        # out, and only the message it printed tells the two apart.
        text = f'{detail.get("backend") or "resolve"}: {failure}'

    line = Text('  ')
    line.append('!' if pin else str(index), style='yellow' if pin else 'cyan')
    line.append(' ')
    line.append(name.ljust(width), style='white')
    line.append('  ')
    line.append(f'{weight:>3} · {when}'.ljust(STATUS_COLUMN))
    if text:
        line.append('  ')
        line.append(text, style='red' if failure else 'green')
    console.print(line, no_wrap=True, overflow='ellipsis')
    if not failure:
        render_context(detail, width)


def render_context(detail: dict, width: int) -> None:
    """The lines under the title: where the offered item lives, and how to open it.

    A title names an item and nothing else. Which repo it lands in, which effort
    it serves and why it is worth the next hour are all on the row the backend
    already returned — dropping them means going back and asking a second time to
    find out whether to pick the thing that was just offered.

    Context and gist share one line, aligned under the title: the draw is five
    entries you scan, and a paragraph under each turns it into a document you
    read. The view command earns its own, because a clipped command is not a
    command — this is where a project item's sixty-column UUID invocation fits
    and the dashboard's three-row glance cannot.
    """
    indent = ' ' * (width + CONTINUATION_INDENT)
    context = detail.get('context') or ''
    about = detail.get('detail') or ''
    rest = max(int(detail.get('candidates') or 1) - 1, 0)
    if context or about or rest:
        line = Text(indent)
        if context:
            line.append(context, style='cyan')
        if context and about:
            line.append(' — ')
        line.append(about)
        # The title above is the first of several equally valid rows, and without
        # this the row reads as the backend having chosen. Three books in progress
        # have no next one; showing one of them silently picks for you.
        if rest:
            line.append(f'  +{rest} more', style='yellow')
        console.print(line, no_wrap=True, overflow='ellipsis')
    if detail.get('view'):
        line = Text(indent)
        line.append(f'↳ {detail["view"]}', style='cyan')
        console.print(line, no_wrap=True, overflow='ellipsis')


def cmd_next(explain: bool, as_json: bool, reroll: bool) -> int:
    pursuits = load_pursuits()
    if not pursuits:
        console.print('No pursuits yet. Start one:  [cyan]doit pursuits edit[/]')
        return 1
    write_names_cache(pursuits)

    now = datetime.now().astimezone()
    state = build_state(pursuits, now)
    if not state['active']:
        console.print('Every pursuit is paused or past its term.')
        return 1

    cached = None if reroll else load_cached_draw(now)
    if cached is not None:
        # Repaired against the whole draw, before anything is filtered out of it —
        # this writes the payload back, and a filtered one would erase the record
        # every later log reads its provenance from.
        retry_failed_resolves(cached, pursuits)
        cached = without_logged(cached)
        # Nothing offered is still outstanding, so the standing draw has no answer
        # left to give and the question earns a fresh one.
        if not cached['pinned'] and not cached['drawn']:
            cached = None
    if cached is None:
        selection = compute_draw(state)
        names = selection['pinned'] + selection['drawn']
        selection['resolved'] = resolve_all(names, pursuits)
        save_cached_draw(selection)
        bump_counts(counts_path(JOURNAL_DIR, machine_name()), names)
    else:
        selection = cached
        names = selection['pinned'] + selection['drawn']

    if as_json:
        # Plain print, never the rich console: a Console soft-wraps at terminal
        # width, which would put newlines inside JSON strings.
        print(json.dumps({**selection, 'state': explain_payload(state)}, indent=2, default=str))
        return 0

    if explain:
        return render_explain(state, selection)

    console.rule('[cyan]What now', align='left')
    context = todays_context()
    if context:
        banner = Text()
        banner.append('Today', style='magenta')
        banner.append('  ' + ' · '.join(context))
        console.print(banner)
        console.print()

    resolved = selection.get('resolved') or {}
    width = max((len(name) for name in names), default=10)
    if selection['pinned']:
        console.print('[yellow]Overdue[/]')
        for name in selection['pinned']:
            render_row(0, name, state, resolved, True, width)
        console.print()
    if selection['drawn']:
        console.print('[cyan]Drawn[/]')
        for index, name in enumerate(selection['drawn'], start=1):
            render_row(index, name, state, resolved, False, width)
    console.print()
    standing = standing_line(state)
    if standing:
        console.print(Text(f'  {standing}\n', style='yellow'))
    render_out_of_band(state)
    console.print('  + is owed, − is ahead · m is minutes, a bare number is checkoffs')
    # `\[note]` is escaped: rich reads a bare bracket as a style tag, which
    # silently dropped the optional argument from this hint.
    console.print(r'Log:  [cyan]doit log <pursuit> \[note][/]   Pass:  [cyan]doit skip <pursuit> \[--for 2w][/]')
    return 0


def explain_payload(state: dict) -> dict:
    """The full numeric state, the same shape recorded into every journal entry."""
    return {
        'logs_per_day': round(state['logs_per_day'], 3),
        'measured_rate': None if state['measured_rate'] is None else round(state['measured_rate'], 3),
        'weights': state['weights'],
        'shares': {name: round(value, 4) for name, value in state['shares'].items()},
        'intervals': {name: round(value, 2) for name, value in state['intervals'].items() if not math.isinf(value)},
        'days_since': {name: None if value is None else round(value, 2) for name, value in state['days_since'].items()},
        'balance': {name: round(value, 2) for name, value in state['balance'].items()},
        'checkoff_minutes': state['checkoff_minutes'],
        # Recorded beside the balance because a balance is only interpretable
        # against the moment it started counting from, and a later reset moves
        # that moment with nothing else in the entry saying so.
        'zero_points': {name: when.isoformat() for name, when in state['origins'].items()},
        'warn_bands': {name: round(warn_threshold(state, name), 2) for name in state['active']},
        'suppressed': state['suppressed'],
        'effective': {name: round(value, 3) for name, value in state['effective'].items()},
        'pool': {name: round(value, 3) for name, value in state['pool'].items()},
        'probability': {name: round(value, 4) for name, value in state['probability'].items()},
        'paused': [name for name, config in state['pursuits'].items() if config.get('paused')],
        'term_ended': [name for name, config in state['pursuits'].items() if term_ended(config, state['today'])],
    }


def render_explain(state: dict, selection: dict) -> int:
    rate = 'assumed' if state['measured_rate'] is None else 'measured'
    console.rule('[cyan]Why these', align='left')
    console.print(f'{state["logs_per_day"]:.2f} logs/day ({rate}) · draw {selection["draw_id"][:8]}\n')

    table = Table(box=None, pad_edge=False)
    table.add_column('')
    table.add_column('pursuit')
    for heading in ('wt', 'share', 'every', 'last', 'balance', 'band', 'urgency', 'pick'):
        table.add_column(heading, justify='right')

    passed = set(state['suppressed'])
    for name in sorted(state['active'], key=lambda key: -state['effective'][key]):
        interval = state['intervals'][name]
        every = '—' if math.isinf(interval) else f'{interval:.1f}d'
        urgency_value = state['effective'][name] / state['weights'][name] if state['weights'][name] else 0
        chosen = name in selection['pinned'] or name in selection['drawn']
        table.add_row(
            '[green]●[/]' if chosen else '',
            f'[yellow]{name}[/]' if name in passed else name,
            f'{int(state["weights"][name])}',
            f'{state["shares"][name] * 100:.1f}%',
            every,
            format_elapsed(state['days_since'][name]),
            format_balance(state['balance'][name], state['checkoff_minutes'].get(name)),
            format_amount(warn_threshold(state, name), state['checkoff_minutes'].get(name)),
            f'{urgency_value:.2f}',
            f'{state["probability"][name] * 100:.1f}%',
        )
    console.print(table)
    console.print(f'\n  pick is the chance of being drawn [bold]first[/]; the draw takes {DRAW_SIZE} without replacement.')
    console.print('  balance is asked-for less done, [bold]+[/] owed · band is the drift allowed · [yellow]yellow[/] is skipped')
    return 0


def match_pursuit(needle: str, pursuits: dict) -> str | None:
    """Exact name, else an unambiguous prefix. Typing three letters is the point."""
    if needle in pursuits:
        return needle
    matches = [name for name in pursuits if name.startswith(needle)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        error_console.print(f'Ambiguous: {needle} matches {", ".join(sorted(matches))}')
        return None
    return None


class Abandoned(Exception):
    """The prompt was interrupted, so nothing should be written."""


def ask(prompt: str) -> str:
    """One line of input, treating an interrupt as abandoning rather than crashing.

    Ctrl-C and Ctrl-D at a prompt both mean "forget it", and a traceback there
    would print a stack over a half-answered question.
    """
    try:
        return console.input(prompt).strip()
    except (KeyboardInterrupt, EOFError) as interrupted:
        raise Abandoned from interrupted


def prompt_for_pursuit(pursuits: dict, offered: list[str]) -> str:
    """Ask which pursuit, listing them with what each means.

    The draw that is still on screen is marked, because the overwhelmingly common
    path is reading five rows and then logging one of them — and a name typed from
    memory is where a prefix collides with a pursuit that was never offered.
    """
    width = max(len(name) for name in pursuits)
    console.print()
    for name, config in sorted(pursuits.items(), key=lambda row: -row[1].get('weight', 0)):
        row = Text('  ')
        row.append('›' if name in offered else ' ', style='cyan')
        row.append(f' {name.ljust(width)}  ', style='white' if name in offered else 'dim')
        row.append(first_sentence(config.get('description', '')), style='dim')
        console.print(row)
    while True:
        answer = ask('\n  pursuit: ')
        if not answer:
            raise Abandoned
        matched = match_pursuit(answer, pursuits)
        if matched:
            return matched
        if answer not in pursuits and not [name for name in pursuits if name.startswith(answer)]:
            error_console.print(f'  No pursuit starts with {answer}.')


def prompt_for_minutes() -> int:
    """Ask how long it took, refusing an empty answer.

    Asked only of a pursuit measured in time, where the duration is the thing
    being logged rather than an annotation on it. A checkoff there is a number of
    minutes, so an entry without one says something happened and not how much.

    Deliberately not defaulted to the register's checkoff size. Enter would write
    that size into the journal as a measurement, and every later reading would be
    quoting the register back at itself.
    """
    while True:
        answer = ask('  minutes: ')
        if answer.isdigit() and int(answer) > 0:
            return int(answer)
        error_console.print('  A whole number of minutes.')


def parse_ago(token: str) -> timedelta | None:
    """'3h' → 3 hours, '2d' → 2 days, '90m' → 90 minutes. None if unparsable."""
    units = {'m': 'minutes', 'h': 'hours', 'd': 'days', 'w': 'weeks'}
    number = ''.join(character for character in token if character.isdigit())
    unit = ''.join(character for character in token if character.isalpha()) or 'h'
    if not number or unit not in units:
        return None
    return timedelta(**{units[unit]: int(number)})


def run_on_log(config: dict, item: dict, note: str, minutes: int | None, assume_yes: bool) -> dict | None:
    """Run the pursuit's write-through command against the item that was offered.

    Prompts unless told not to, and never fires unattended: the draw it takes the
    id from can be up to fifteen minutes old, and completing the wrong task is
    expensive to notice. A prompt is one keystroke; a wrong completion is a
    silent lie in another app's data.
    """
    template = config.get('on_log')
    if not template or not item:
        return None
    if '{id}' in template and not item.get('id'):
        return None
    command = template.format(
        id=item.get('id', ''),
        label=item.get('label', ''),
        note=note or '',
        minutes='' if minutes is None else minutes,
        pursuit=item.get('pursuit', ''),
    )
    if not assume_yes:
        if not can_prompt():
            return None
        prompt = Text('  ')
        prompt.append(command, style='cyan')
        prompt.append('  run it? [y/N] ')
        if console.input(prompt).strip().lower() not in ('y', 'yes'):
            return {'command': command, 'ran': False, 'exit_code': None}
    result = subprocess.run(shlex.split(command), capture_output=True, text=True, check=False)
    if result.returncode != 0:
        failure = Text('  ')
        failure.append(f'{command} exited {result.returncode}', style='yellow')
        failure.append(f': {(result.stderr or "").strip()}')
        error_console.print(failure)
    return {'command': command, 'ran': True, 'exit_code': result.returncode}


def record_event(event: str, name: str, state: dict | None, extra: dict, now: datetime | None = None) -> dict:
    """Append one journal entry, carrying the state that produced it where there is one.

    The state vector is written into a record on purpose. Weights change, and
    once they do there is no way to reconstruct what the numbers were when a thing
    was logged — which is exactly what `drift` needs to be honest about a past
    week. Cheap to write now, impossible to backfill later.

    ``state`` is None where a record carries no numbers of its own to explain: the
    vector describes the whole register, so a run writing several records would
    otherwise put the same payload in each of them.
    """
    now = now or (state['now'] if state else datetime.now().astimezone())
    record = {
        'id': new_id(now),
        'pursuit': name,
        'event': event,
        'logged_at': now.isoformat(),
        'occurred_at': extra.pop('occurred_at', now.isoformat()),
        'tz': str(now.tzinfo),
        'machine': machine_name(),
        **extra,
    }
    if state is not None:
        record['state_at_log'] = explain_payload(state)
    return journal.append(journal_path(JOURNAL_DIR, machine_name()), record)


def restated_balance(state: dict, name: str, entry: dict) -> str:
    """The pursuit's standing with one new entry counted, or nothing where it has none.

    The balance is recomputed from the model rather than by subtracting what was
    logged, because the two disagree exactly where it matters. An entry dated
    before the pursuit's zero point pays nothing off, and subtraction has no way
    to know — it prints a number the next command contradicts.
    """
    if name not in state['balance']:
        return ''
    minutes = state['checkoff_minutes'].get(name)
    own = [*records_by_pursuit(state['records']).get(name, []), entry]
    origin = state['origins'][name]
    now = state['now']
    done = completed_since(own, state['evidence_days'].get(name, []), now, origin, minutes)
    span = max((now - origin).total_seconds() / 86400.0 - skipped_days(own, now, origin), 0.0)
    owed = balance(span, state['intervals'][name], state['checkoff_size'][name], done)
    return f' · {format_balance(owed, minutes)}'


def cmd_log(name: str | None, words: list[str], ago: str | None, minutes: int | None, assume_yes: bool, no_write: bool) -> int:
    """Record having done one, asking for whatever was not passed as a flag.

    Anything given on the command line is never asked about, so the fast path
    stays one line. What is asked for is the pursuit, the note and — for a pursuit
    measured in time — the duration, which is what the entry is made of there
    rather than an extra nobody discovers from a help screen they never open.
    """
    pursuits = load_pursuits()
    if not pursuits:
        error_console.print('No pursuits yet:  [cyan]doit pursuits edit[/]')
        return 1

    now = datetime.now().astimezone()
    # Read before the prompt rather than after: this is a file read, where
    # build_state is a round trip to every backend, and the question should
    # appear at typing speed.
    cached = load_cached_draw(now) or {}
    offered = cached.get('pinned', []) + cached.get('drawn', [])

    try:
        if name is None:
            if not can_prompt():
                error_console.print('Which pursuit? Pass it as the argument:  [cyan]doit log <pursuit>[/]')
                return 1
            matched = prompt_for_pursuit(pursuits, offered)
        else:
            matched = match_pursuit(name, pursuits) or ''
            if not matched:
                error_console.print(f'No pursuit named {name}. See:  [cyan]doit pursuits list[/]')
                return 1
        # Declaring a checkoff size is the only thing that makes a pursuit
        # measured in time, so the register decides this and nothing here keeps a
        # list of which kind each one is.
        timed = bool(pursuits[matched].get('minutes'))
        if minutes is not None and not timed:
            raise typer.BadParameter(f'{matched} is counted in completions, so --minutes has nothing to measure')
        if not words and can_prompt():
            answer = ask('  note, or Enter to skip: ')
            words = answer.split() if answer else []
        if timed and minutes is None:
            if not can_prompt():
                raise typer.BadParameter(f'{matched} is measured in minutes: pass --minutes <n>')
            minutes = prompt_for_minutes()
    except Abandoned:
        error_console.print('  Nothing logged.')
        return 1

    state = build_state(pursuits, now)
    occurred = now
    if ago:
        delta = parse_ago(ago)
        if delta is None:
            raise typer.BadParameter('--ago takes 90m / 3h / 2d / 1w')
        occurred = now - delta

    # The cached draw is what was on screen, so it already knows which concrete
    # item this pursuit meant — no second resolve, and no chance of acting on
    # something different from what was offered.
    item = (cached.get('resolved') or {}).get(matched) or {}
    # A cached failure is truthy and carries no id, so it satisfies the guard below
    # and the write-through to the owning CLI is skipped without saying so.
    if item.get('error'):
        item = {}
    if not item and pursuits[matched].get('resolve'):
        item = resolve_one(matched, pursuits[matched]) or {}

    downstream = None
    if not no_write:
        downstream = run_on_log(pursuits[matched], item, ' '.join(words), minutes, assume_yes)

    # Logging a pursuit says the pursuit happened. It does not say which of its
    # candidates did, and the log has no way to find out — the note is prose. So
    # the item is only named where naming it is a fact: the backend matched one
    # row, or the write-through completed the one it offered.
    acted = bool((downstream or {}).get('ran'))
    named = item if item and (int(item.get('candidates') or 1) == 1 or acted) else {}

    entry = record_event(
        journal.Event.DONE,
        matched,
        state,
        {
            'occurred_at': occurred.isoformat(),
            'note': ' '.join(words) or None,
            'duration_minutes': minutes,
            'item': named or None,
            'downstream': downstream,
            'draw_id': cached.get('draw_id'),
            'was_offered': matched in (cached.get('pinned', []) + cached.get('drawn', [])),
            'rank_in_draw': (cached.get('drawn', []).index(matched) + 1) if matched in cached.get('drawn', []) else None,
        },
    )
    # The draw outlives the log by up to a quarter of an hour, and re-offering
    # something just done reads as the log having gone nowhere.
    mark_logged(matched, now)

    # Asked of the model over the records now in hand, never derived by
    # subtracting what was logged. An entry dated before the pursuit's zero point
    # pays nothing off, and arithmetic cannot see that — it would print a
    # standing the very next command contradicts.
    #
    # Only the balance is recomputed, not the whole state: `completed_since` and
    # `balance` are pure and read what is already here, where a second
    # `build_state` would be another round trip to every backend.
    standing = restated_balance(state, matched, entry)
    label = f' — {named["label"]}' if named.get('label') else ''
    logged = Text.from_markup('[green]Logged[/] ')
    logged.append(f'{matched}{label}{standing}')
    console.print(logged)
    if downstream and downstream.get('ran'):
        ran = Text.from_markup('[green]Ran[/] ')
        ran.append(downstream['command'])
        console.print(ran)
    return 0


def cmd_skip(name: str | None, duration: str | None) -> int:
    pursuits = load_pursuits()
    if not pursuits:
        error_console.print('No pursuits yet:  [cyan]doit pursuits edit[/]')
        return 1
    now = datetime.now().astimezone()
    cached = load_cached_draw(now) or {}
    try:
        if name is None:
            if not can_prompt():
                error_console.print('Which pursuit? Pass it as the argument:  [cyan]doit skip <pursuit>[/]')
                return 1
            matched = prompt_for_pursuit(pursuits, cached.get('pinned', []) + cached.get('drawn', []))
        else:
            matched = match_pursuit(name, pursuits) or ''
            if not matched:
                error_console.print(f'No pursuit named {name}.')
                return 1
    except Abandoned:
        error_console.print('  Nothing skipped.')
        return 1

    state = build_state(pursuits, now)
    span = skip_span(duration, state['intervals'].get(matched))
    expires = now + timedelta(days=span)
    record_event(journal.Event.SKIP, matched, state, {'expires_at': expires.isoformat(), 'draw_id': cached.get('draw_id')})
    # The pass only means something against a new draw, and the span it covers is
    # already in the journal, so the stale cache goes.
    DRAW_CACHE.unlink(missing_ok=True)
    passed = Text.from_markup('[yellow]Passed[/] ')
    passed.append(f'{matched} — out of the draw for {span}d, until {expires:%d %b %Y}. Owes nothing meanwhile, weight untouched.')
    console.print(passed)
    console.print(f'  Back sooner:  [cyan]doit pursuits resume {matched}[/]')
    return 0


def cmd_resume(name: str | None) -> int:
    """End a standing skip now, so the pursuit returns to the next draw.

    The verb that retires the mark `skip` writes. Without one a mistyped span is
    unreachable by any command the tool ships, on a file :mod:`doit.journal` calls
    the record that cannot be reconstructed — the only remedy left is hand-editing
    a synced `.jsonl`.

    Written as a skip that has already expired rather than as a deletion, because
    the journal is append-only and a pass that was made is still a fact about the
    week it was made in.
    """
    pursuits = load_pursuits()
    if not pursuits:
        error_console.print('No pursuits yet:  [cyan]doit pursuits edit[/]')
        return 1
    now = datetime.now().astimezone()
    if name is None:
        state = build_state(pursuits, now)
        chosen = list(state['suppressed'])
        if not chosen:
            console.print('Nothing is skipped.')
            return 0
    else:
        matched = match_pursuit(name, pursuits) or ''
        if not matched:
            error_console.print(f'No pursuit named {name}. See:  [cyan]doit pursuits list[/]')
            return 1
        chosen = [matched]
        state = build_state(pursuits, now)

    for pursuit in chosen:
        record_event(journal.Event.SKIP, pursuit, state, {'expires_at': now.isoformat()})
    DRAW_CACHE.unlink(missing_ok=True)
    back = Text.from_markup('[green]Resumed[/] ')
    back.append(f'{", ".join(chosen)} — back in the next draw.')
    console.print(back)
    return 0


def skip_span(duration: str | None, interval: float | None) -> int:
    """How many days a pass covers.

    One interval when nothing is asked for, so a bare skip still means "not this
    time" rather than committing to a length nobody chose. A pursuit whose weight
    implies no interval gets a day, which is the shortest a pass can be and still
    be one.
    """
    if duration:
        span = parse_cadence(duration)
        if span <= 0:
            raise typer.BadParameter('--for takes 3d / 2w / 1mo / 1y')
        return span
    if interval is None or math.isinf(interval):
        return 1
    return max(round(interval), 1)


def cmd_reset(name: str | None, assume_yes: bool) -> int:
    """Start a balance again from now — one pursuit, or every one when none is named.

    The journal stays append-only: a reset is a boundary marker written into it,
    not a rewrite of what happened. Everything before the marker still reads as
    history and stops counting toward what is owed.

    The whole-register form is confirmed, because it discards every accrued
    balance at once and nothing the tool ships puts one back. A single pursuit
    goes through unasked — that is one number, and the register says what it
    should be.
    """
    pursuits = load_pursuits()
    if not pursuits:
        error_console.print('No pursuits yet:  [cyan]doit pursuits edit[/]')
        return 1
    if name is None:
        chosen = sorted(pursuits)
        if not assume_yes and not confirm_reset(chosen):
            error_console.print('  Nothing reset.')
            return 1
    else:
        matched = match_pursuit(name, pursuits) or ''
        if not matched:
            error_console.print(f'No pursuit named {name}. See:  [cyan]doit pursuits list[/]')
            return 1
        chosen = [matched]

    now = datetime.now().astimezone()
    state = build_state(pursuits, now)
    for index, pursuit in enumerate(chosen):
        # The state vector goes on the first record of the run only. It describes
        # the register rather than the pursuit being zeroed, so one copy per name
        # writes the same payload N times into an append-only synced file.
        record_event(journal.Event.RESET, pursuit, state if index == 0 else None, {})
    DRAW_CACHE.unlink(missing_ok=True)
    done = Text.from_markup('[green]Reset[/] ')
    done.append(f'{", ".join(chosen)} — {"each balance" if len(chosen) > 1 else "the balance"} starts again from now.')
    console.print(done)
    return 0


def confirm_reset(chosen: list[str]) -> bool:
    """Ask before discarding every accrued balance, refusing where nobody can answer."""
    if not can_prompt():
        error_console.print(f'Resetting all {len(chosen)} pursuits discards every balance. Pass [cyan]--yes[/] to do it unattended.')
        return False
    prompt = Text('  ')
    prompt.append(f'Reset all {len(chosen)} pursuits', style='yellow')
    prompt.append(' — every accrued balance is discarded. [y/N] ')
    try:
        return console.input(prompt).strip().lower() in ('y', 'yes')
    except (KeyboardInterrupt, EOFError):
        return False


def cmd_list(as_json: bool) -> int:
    pursuits = load_pursuits()
    if not pursuits:
        console.print('No pursuits yet:  [cyan]doit pursuits edit[/]')
        return 1
    write_names_cache(pursuits)
    state = build_state(pursuits, datetime.now().astimezone())

    if as_json:
        # See cmd_next: a Console would soft-wrap this into invalid JSON.
        print(json.dumps({'pursuits': pursuits, 'state': explain_payload(state)}, indent=2, default=str))
        return 0

    console.rule('[cyan]Pursuits', align='left')
    width = max(len(name) for name in pursuits)
    for name, config in sorted(pursuits.items(), key=lambda row: -row[1].get('weight', 0)):
        share = state['shares'].get(name)
        share_text = f'{share * 100:>5.1f}%' if share else '    —'
        cadence = f' · {config["cadence"]}{urgency_multiplier(state, name)}' if config.get('cadence') else ''
        last = format_elapsed(state['days_since'].get(name))
        line = Text('  ')
        line.append(name.ljust(width), style='white')
        line.append(f'  {int(config.get("weight", 0)):>3} {share_text}{cadence}  {last}')
        if name in state['balance']:
            line.append(f'  {format_balance(state["balance"][name], state["checkoff_minutes"].get(name)):>7}')
        if config.get('paused'):
            line.append('  paused', style='yellow')
        elif term_ended(config, state['today']):
            line.append(f'  term ended {config["until"]}', style='yellow')
        elif name in state['suppressed']:
            line.append('  skipped', style='yellow')
        console.print(line)
    console.print('\n  share is what each weight implies · ×n is a cadence outrunning it')
    console.print('  balance is + owed, − ahead · m is minutes, a bare number is checkoffs')
    console.print('  [cyan]doit pursuits edit[/]')
    render_orphaned_counters(pursuits)
    return 0


def orphaned_offer_counts(register: dict) -> list[str]:
    """Offer counts whose pursuit is no longer in the register.

    The counter is keyed by name, so retiring or renaming a pursuit strands its
    total. ``drift`` iterates the register, so the row never appears there again
    and nothing else would ever mention it. The review deck has the same failure
    and already warns about it; this is the other half of the pair.
    """
    return sorted(name for name in load_counts(JOURNAL_DIR) if name not in register)


def render_orphaned_counters(register: dict) -> None:
    """Name any stranded counts, in the one view that is about the register
    itself rather than about what to do next.

    ``next`` and the nudge stay silent, for the reason :func:`review.render_orphans`
    gives: a misfiled record is not a task, and an interrupt that reports
    bookkeeping is the kind you stop reading.
    """
    orphans = orphaned_offer_counts(register)
    if not orphans:
        return
    here = machine_name()
    by_machine = counts_by_machine(JOURNAL_DIR)
    console.print(Text('\n  Offer counts with no pursuit:', style='yellow'))
    for name in orphans:
        for machine, counts in sorted(by_machine.items()):
            if name not in counts:
                continue
            # Every box writes its own counter file, so one this box did not write
            # is not this box's to edit. Without saying so the warning reads as a
            # repair you keep failing to make, and editing it anyway is what gives
            # a synced file two writers.
            whose = '' if machine == here else ' [dim](another box — clear it there)[/]'
            console.print(f'    [yellow]{name}[/]  {counts[name]}  {machine}{whose}')
    console.print('  Renaming a pursuit strands its total — rename the key in the counter to keep it.')
    console.print(f'  [cyan]{JOURNAL_DIR}[/]')


def drift_rows(pursuits: dict, state: dict, days: int) -> list[dict]:
    """Every pursuit worth a row, with what it did over the window in its own unit.

    A pursuit paused or retired mid-window keeps a row while it still has activity
    in one, and reports no stated share rather than 0% — which would read as a
    claim it never made.
    """
    now = state['now']
    cutoff = now - timedelta(days=days)
    sizes = {name: float(config['minutes']) for name, config in pursuits.items() if config.get('minutes')}
    mine = records_by_pursuit(state['records'])
    counts = load_counts(JOURNAL_DIR)

    rows = []
    for name in sorted(pursuits, key=lambda key: -pursuits[key].get('weight', 0)):
        own = mine.get(name, [])
        seen = state['evidence_days'].get(name, [])
        amount = completed_since(own, seen, now, cutoff, sizes.get(name))
        logs = sum(1 for record in own if record.get('event') == journal.Event.DONE and in_window(record, cutoff))
        passes = sum(1 for record in own if record.get('event') == journal.Event.SKIP and in_window(record, cutoff))
        if name not in state['active'] and not amount and not passes:
            continue
        rows.append(
            {
                'pursuit': name,
                'unit': 'minutes' if name in sizes else 'checkoffs',
                'checkoff_minutes': sizes.get(name),
                'weight': state['weights'].get(name),
                'amount': round(amount, 1),
                'logs': logs,
                'balance': None if name not in state['balance'] else round(state['balance'][name], 1),
                'offered': counts.get(name, 0),
                'skips': passes,
            }
        )

    # Both shares are taken over the same population. Reading `said` off the
    # register-wide weights while `did` runs inside one unit compares two
    # denominators: a register lived exactly to plan then flags a pursuit alone
    # in its unit at 100%, because it is the whole of its own half.
    for unit in ('minutes', 'checkoffs'):
        group = [row for row in rows if row['unit'] == unit]
        did_total = sum(row['amount'] for row in group)
        said_total = sum(row['weight'] or 0.0 for row in group)
        for row in group:
            row['realized_share'] = round(row['amount'] / did_total * 100, 1) if did_total else 0.0
            row['stated_share'] = round((row['weight'] or 0.0) / said_total * 100, 1) if said_total and row['weight'] else None
    return rows


def in_window(record: dict, cutoff: datetime) -> bool:
    """Whether a record happened inside the reporting window.

    A record whose timestamp will not parse is outside it, matching
    :func:`completed_since` — one record read two ways inside one report would
    count toward the log tally and not toward the amount.
    """
    when = journal.parse_time(record.get('occurred_at') or record.get('logged_at'))
    return when is not None and when >= cutoff


def render_drift_group(rows: list[dict], unit: str, heading: str, amount_column: str) -> None:
    """One unit's table, with the share each pursuit took of that unit alone."""
    group = [row for row in rows if row['unit'] == unit]
    if not group:
        return
    console.print(f'\n[cyan]{heading}[/]')
    table = Table(box=None, pad_edge=False)
    table.add_column('pursuit')
    for column in ('said', 'did', amount_column, 'balance', 'offered', 'passed'):
        table.add_column(column, justify='right')
    for row in group:
        stated = row['stated_share']
        did = f'{row["realized_share"]:.0f}%'
        # A paused pursuit has no claim to have missed, so its share is reported
        # without a verdict rather than colored against one it never stated.
        if stated is not None:
            did = f'[green]{did}[/]' if abs(row['realized_share'] - stated) < 10 else f'[yellow]{did}[/]'
        table.add_row(
            row['pursuit'],
            '—' if stated is None else f'{stated:.0f}%',
            did,
            f'{row["amount"]:.0f}' if unit == 'minutes' else f'{row["amount"]:.1f}',
            '—' if row['balance'] is None else format_balance(row['balance'], row['checkoff_minutes']),
            str(row['offered']),
            str(row['skips']),
        )
    console.print(table)


def cmd_drift(days: int, as_json: bool) -> int:
    """Stated weight against what actually happened, in each pursuit's own unit.

    The report the whole thing is for. It never adjusts a weight — revealed and
    stated preference are different signals and blending them would destroy the
    only honest comparison available. Offered counts sit next to realized share
    because they separate the two failures that look identical from the outside: a
    pursuit that never comes up, and one that comes up and gets ignored.

    Minutes and completions do not add, so there is no register-wide `did` and
    none is invented. A pursuit measured in time is compared against the other
    timed ones and a counted one against the other counted ones, because that is
    the only denominator either of them has. `said` spans both, since a weight is
    a share of attention rather than of any unit.
    """
    pursuits = load_pursuits()
    if not pursuits:
        return 1
    now = datetime.now().astimezone()
    state = build_state(pursuits, now)
    rows = drift_rows(pursuits, state, days)

    if as_json:
        # See cmd_next: a Console would soft-wrap this into invalid JSON.
        print(json.dumps({'window_days': days, 'rows': rows}, indent=2))
        return 0

    console.rule(f'[cyan]Drift · last {days} days', align='left')
    if not any(row['amount'] for row in rows):
        console.print('Nothing recorded in the window yet — drift needs history before it can say anything.\n')
        return 0

    render_drift_group(rows, 'minutes', 'Measured in time', 'min')
    render_drift_group(rows, 'checkoffs', 'Counted in completions', 'done')
    console.print('\n  said and did are both shares of their own unit · the two units do not add')
    console.print('  balance is where each stands right now · weights are never auto-adjusted')
    if days > evidence.OCCURRENCE_WINDOW_DAYS:
        console.print(f'  [yellow]Apps keep {evidence.OCCURRENCE_WINDOW_DAYS} days of dates, so days before that are typed logs alone.[/]')
    console.print()
    return 0


def cmd_dormant() -> int:
    """Pursuits gone quiet for far longer than their own weight implies."""
    pursuits = load_pursuits()
    state = build_state(pursuits, datetime.now().astimezone())
    stale = []
    for name in state['active']:
        elapsed = state['days_since'][name]
        interval = state['intervals'][name]
        if math.isinf(interval):
            continue
        if elapsed is None or elapsed > interval * 3:
            stale.append((name, elapsed, interval))
    console.rule('[cyan]Dormant', align='left')
    if not stale:
        console.print('Nothing is running cold.\n')
        return 0
    for name, elapsed, interval in sorted(stale, key=lambda row: -(row[1] or 1e9)):
        line = Text('  ')
        line.append(name, style='white')
        line.append(f'  {format_elapsed(elapsed)} · implies every {interval:.0f}d')
        console.print(line)
    console.print('\n  Cold measures the gap since the last one; the balance measures what is owed.\n')
    return 0


def cmd_evidence(as_json: bool = False) -> int:
    """What each app says you last did, and which of them could not be asked.

    Its own command because the draw consumes this silently. A pursuit whose
    backend is logged out falls back to the journal and simply reads as never
    done, which looks identical to genuinely never having done it — the two need
    to be told apart somewhere, and this is where.
    """
    pursuits = load_pursuits()
    if not pursuits:
        return 1
    now = datetime.now().astimezone()
    active = {name: config for name, config in pursuits.items() if is_active(config, now.date())}
    observations = evidence.refresh(active, CACHE_DIR, now, force=True)
    seen = evidence.observed(observations)
    failed = evidence.problems(observations)
    declared = evidence.declared(active)

    if as_json:
        console.print_json(
            data={
                'observed': {name: when.isoformat() for name, when in seen.items()},
                'errors': failed,
                'undeclared': sorted(set(active) - set(declared)),
            }
        )
        return 0

    console.rule('[cyan]Evidence', align='left')
    for name in sorted(active, key=lambda key: -active[key].get('weight', 0)):
        line = Text('  ')
        line.append(f'{name:<9}', style='white')
        if name not in declared:
            line.append('no backend — logged by hand', style='yellow')
        elif name in failed:
            line.append(failed[name], style='red')
        elif name in seen:
            elapsed = (now - seen[name]).total_seconds() / 86400.0
            line.append(f'{format_elapsed(elapsed)} · {seen[name]:%d %b %H:%M}')
        else:
            line.append('asked, nothing recorded yet')
        console.print(line)
    console.print('\n  The apps are asked on a timer; this asks them now.\n')
    return 0


def cmd_edit() -> int:
    if not REGISTER.exists():
        REGISTER.parent.mkdir(parents=True, exist_ok=True)
        REGISTER.write_text(TEMPLATE)
    subprocess.run([os.environ.get('EDITOR', 'vi'), str(REGISTER)], check=False)
    try:
        write_names_cache(load_pursuits())
    except RegisterError as error:
        error_console.print(f'[yellow]Register not loadable:[/] {error}')
        return 1
    return 0


def cmd_names() -> int:
    """Emit name<TAB>description for the shell completion, refreshing its cache."""
    pursuits = load_pursuits()
    write_names_cache(pursuits)
    for name, config in sorted(pursuits.items()):
        print(f'{name}\t{config.get("description", "")}')
    return 0


def run(action: Callable[[], int]) -> None:
    """Run a command body, turning an untrustworthy register into one message.

    Every entry point loads the register, so without this each would need its own
    try/except — and a RegisterError escaping as a traceback would bury the one
    line saying which field is wrong.
    """
    try:
        code = action()
    except RegisterError as error:
        error_console.print(f'[yellow]pursuits.yml:[/] {error}')
        raise typer.Exit(1) from None
    raise typer.Exit(code)


def next_command(
    explain: Annotated[bool, typer.Option('--explain', help='The same draw with every number behind it.')] = False,
    reroll: Annotated[bool, typer.Option('--reroll', help='Force a fresh draw before the cache expires.')] = False,
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
) -> None:
    """What to do now, drawn from what you said matters."""
    run(lambda: cmd_next(explain, as_json, reroll))


def log_command(
    pursuit: Annotated[str | None, typer.Argument(help='The pursuit, by name or unambiguous prefix (asked for when omitted).')] = None,
    note: Annotated[list[str] | None, typer.Argument(help='Free text recorded with the entry (asked for when omitted).')] = None,
    ago: Annotated[str | None, typer.Option('--ago', help='Log something you did earlier: 90m / 3h / 2d / 1w.')] = None,
    minutes: Annotated[
        int | None,
        typer.Option('--minutes', min=1, help='How long it took, in whole minutes (asked for when a pursuit is measured in time).'),
    ] = None,
    assume_yes: Annotated[bool, typer.Option('-y', '--yes', help="Run the pursuit's on_log command without asking.")] = False,
    no_write: Annotated[bool, typer.Option('--no-write', help='Log only; never touch the owning app.')] = False,
) -> None:
    """Record having done one, writing through to the app that owns it.

    Pass every field to log in one line. Leave one out at a terminal and it is
    asked for, with the pursuits listed and the current draw marked. A field
    already passed is never asked about, and --no-input skips every question.
    """
    run(lambda: cmd_log(pursuit, note or [], ago, minutes, assume_yes, no_write))


def skip_command(
    pursuit: Annotated[str | None, typer.Argument(help='The pursuit, by name or unambiguous prefix (asked for when omitted).')] = None,
    duration: Annotated[
        str | None,
        typer.Option('--for', help='How long to pass for: 3d / 2w / 1mo. One interval when omitted.'),
    ] = None,
) -> None:
    """Pass on one — out of the draw until it expires, owing nothing meanwhile."""
    run(lambda: cmd_skip(pursuit, duration))


app = typer.Typer(name='pursuits', no_args_is_help=True, help='The weights the draw runs on.')


@app.command('list')
def list_command(
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
) -> None:
    """Every pursuit, its weight and implied share."""
    run(lambda: cmd_list(as_json))


@app.command('drift')
def drift_command(
    days: Annotated[int, typer.Option('--days', help='How far back to measure.')] = 90,
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
) -> None:
    """Stated weight against what you actually did."""
    run(lambda: cmd_drift(days, as_json))


@app.command('dormant')
def dormant_command() -> None:
    """Pursuits gone colder than their weight implies."""
    run(cmd_dormant)


@app.command('reset')
def reset_command(
    pursuit: Annotated[str | None, typer.Argument(help='The pursuit to zero; every one when omitted.')] = None,
    assume_yes: Annotated[bool, typer.Option('-y', '--yes', help='Zero every pursuit without confirming.')] = False,
) -> None:
    """Start a balance again from now; the journal keeps its history."""
    run(lambda: cmd_reset(pursuit, assume_yes))


@app.command('resume')
def resume_command(
    pursuit: Annotated[str | None, typer.Argument(help='The pursuit to bring back; every skipped one when omitted.')] = None,
) -> None:
    """End a standing skip, so the pursuit returns to the next draw."""
    run(lambda: cmd_resume(pursuit))


@app.command('evidence')
def evidence_command(
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
) -> None:
    """What each app says you last did, asked now rather than on the timer."""
    run(lambda: cmd_evidence(as_json))


@app.command('edit')
def edit_command() -> None:
    """Edit pursuits.yml in $EDITOR."""
    run(cmd_edit)


@app.command('names', hidden=True)
def names_command() -> None:
    """Emit name<TAB>description for the shell completion."""
    run(cmd_names)
