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
from doit.allocate import DEFAULT_ALPHA
from doit.allocate import FALLBACK_LOGS_PER_DAY
from doit.allocate import banked_days_since
from doit.allocate import banked_position
from doit.allocate import draw
from doit.allocate import effective_weights
from doit.allocate import first_draw_probabilities
from doit.allocate import implied_intervals
from doit.allocate import implied_shares
from doit.cadence import overdue_days
from doit.cadence import parse_cadence
from doit.cadence import status_label
from doit.journal import bump_counts
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

# Width of the "weight · when" column, sized for the longest status cadence.py
# produces ("never done") so the resolved item starts at one column on every row.
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
    'alpha',
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
    'credit',
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
#   cadence      optional hard schedule (2w / 1mo); overdue pins it above the draw
#   credit       optional window (1w); banks each completion forward, so three in
#                one evening pay three intervals rather than one
#   until        optional end date; after it the pursuit pauses and says so
#   paused       optional; keeps it in the file but out of the draw
#   alpha        optional; how sharply urgency climbs once past the interval.
#                Superlinear above 1, so the 1.5 default lets something well
#                overdue outrun a heavier pursuit done recently
#   minutes      optional; how long one of these takes, until enough logs carry
#                --minutes for the journal to answer it. `doit forecast` reads it
#   resolve      optional command answering "specifically what?" — see below
#   on_log       optional command run after logging, e.g. completing the task
#
# A cadence replaces the interval the weight implies rather than sitting beside
# it, so declaring one shorter than that interval multiplies how urgent the
# pursuit gets, and one longer divides it. `doit pursuits list` names both.
#
# credit makes that cadence a rate rather than a rhythm. Each completion advances
# the satisfied-through point one interval from wherever it already stood, so a
# burst counts for what it was. The window caps the position both ways: far
# enough ahead cannot silence a daily prompt for a month, and a fortnight away
# cannot accrue a debt no evening can clear. Only pursuits declaring it reach the
# standing line `doit dashboard` prints, which says how many completions would
# bring them back to current.
#
# resolve prints either plain lines (first line wins) or JSON. For JSON, name the
# fields to read: `label` for what to show, `id` for what on_log substitutes into,
# and `items` when the list is nested inside the document.
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
    evidence: icb tasks list --status completed --limit 1 --json
    evidence_time: complete_date

  read-library:
    description: Read what is already on the shelf
    weight: 30
    resolve: icb books list --progress reading --json
    label: title
    context: author

  study-computer-science:
    description: Work the CS track rather than reading about working it
    weight: 35
    resolve: learning overview --json
    items: in_progress_resources
    label: title
    detail: notes
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
            raise RegisterError(f'{name}: unknown field(s) {", ".join(sorted(unknown))}')
        weight = config.get('weight')
        if not isinstance(weight, int | float) or isinstance(weight, bool) or weight < 0:
            raise RegisterError(f'{name}: weight must be a non-negative number')
        if config.get('cadence') and parse_cadence(config['cadence']) <= 0:
            raise RegisterError(f'{name}: cadence must look like 10d / 2w / 1mo / 1y')
        if config.get('until') and not isinstance(config['until'], date):
            raise RegisterError(f'{name}: until must be a date (YYYY-MM-DD)')
        estimate = config.get('minutes')
        if estimate is not None and (not isinstance(estimate, int) or isinstance(estimate, bool) or estimate <= 0):
            raise RegisterError(f'{name}: minutes must be a positive whole number')
        if config.get('on_log') and not config.get('resolve'):
            raise RegisterError(f'{name}: on_log needs resolve — there is no item to act on without it')
        if (config.get('context') or config.get('detail')) and not config.get('label'):
            raise RegisterError(f'{name}: context and detail read fields off the resolved row, so they need label')
        if config.get('view') and not config.get('resolve'):
            raise RegisterError(f'{name}: view needs resolve — there is no item to look at without it')
    return pursuits


def load_settings(path: Path | None = None) -> dict:
    """The register's `forecast:` block — what a day is assumed to hold.

    A sibling of `pursuits:` rather than a key on one, because a daily budget is a
    fact about the person and not about any single strand. Read from the same file
    so there is one thing to hand-edit and one thing to sync.
    """
    path = REGISTER if path is None else path
    if not path.exists():
        return {}
    document = yaml.safe_load(path.read_text()) or {}
    settings = document.get('forecast') or {}
    if not isinstance(settings, dict):
        raise RegisterError(f'{path}: `forecast` must be a mapping of settings')
    budget = settings.get('budget_minutes')
    if budget is not None and (not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0):
        raise RegisterError(f'{path}: forecast.budget_minutes must be a positive whole number')
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


def credit_ages(records: list[dict], app_days: list[date], pursuit: str, now: datetime) -> list[float]:
    """Every occurrence credit banks: each typed one whole, plus the app days the
    journal does not already carry.

    Typed entries keep their own timestamps rather than collapsing to a date,
    because three chores in one evening is three days of credit and that burst is
    what credit exists to reward. An app day the journal already covers is one act
    reported by both records, so it is dropped rather than banked twice.

    An app answers in days, so a pursuit done five times inside one of them banks
    one. Which is what keeps a backend that emits a row per task from outrunning
    one that emits a row per session at the same interval.
    """
    typed = journal.ages(records, pursuit, 'done', now)
    covered = set(journal.days(records, pursuit, 'done', now))
    today = now.date()
    extra = [float((today - day).days) for day in app_days if day not in covered]
    return sorted(typed + extra)


def build_state(
    pursuits: dict,
    now: datetime,
    records: list[dict] | None = None,
    observed: dict[str, datetime] | None = None,
) -> dict:
    """Everything the draw and every view need: rates, intervals, urgency, weights.

    Assembled in one place and passed around, because the same numbers are what
    gets drawn on, what gets displayed by `--explain`, and what gets recorded into
    the journal as the state at the moment of a log. Recomputing them per view is
    how those three drift apart.

    ``records`` and ``observed`` default to the journal on disk and a live round
    trip to every backend. :mod:`doit.forecast` supplies both instead, which is
    what lets a simulated day run this function rather than a second copy of the
    model — a copy is the only way the forecast could come to disagree with the
    draw it claims to predict. Injecting them is also what keeps a thirty-day
    simulation from making thirty evidence calls per replicate.
    """
    today = now.date()
    active = {name: config for name, config in pursuits.items() if is_active(config, today)}
    weights = {name: float(config['weight']) for name, config in active.items()}

    if records is None:
        records = journal.read_all(JOURNAL_DIR) if JOURNAL_DIR.exists() else []
    measured_rate = rate_per_day(records, now)
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
    last_done = evidence.merged(latest_occurrence(records, 'done'), seen)
    last_skip = latest_occurrence(records, 'skip')
    elapsed = days_since(last_done, list(active), now)
    elapsed_skip = days_since(last_skip, list(active), now)

    # A pursuit declaring credit is stating a rate rather than a rhythm, so doing
    # several at once pays several forward. Only urgency reads this — `days_since`
    # stays the honest time since the last one, because that is what gets shown.
    #
    # Both records feed it, or declaring credit on a pursuit with a backend would
    # be a downgrade: `banked` starts as the app-informed elapsed and the branch
    # below overwrites it, so a journal-only reading made `tasks` overdue on a day
    # eight of them were completed inside `icb`.
    banked = dict(elapsed)
    positions: dict[str, float | None] = {}
    for name, config in active.items():
        if not config.get('credit'):
            continue
        cap = float(parse_cadence(config['credit']))
        occurrences = credit_ages(records, seen_days.get(name, []), name, now)
        if not occurrences:
            continue
        banked[name] = banked_days_since(occurrences, intervals[name], cap)
        positions[name] = banked_position(banked[name], intervals[name])

    alphas = {name: float(config.get('alpha', DEFAULT_ALPHA)) for name, config in active.items()}
    effective = {}
    for name in active:
        one = effective_weights(
            {name: weights[name]},
            {name: intervals[name]},
            {name: banked[name]},
            {name: elapsed_skip[name]},
            alphas[name],
        )
        effective.update(one)

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
        'days_banked': banked,
        'banked_position': positions,
        'days_since_skip': elapsed_skip,
        'effective': effective,
        'probability': first_draw_probabilities(effective),
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
    """Pursuits with a hard cadence that are past due, most overdue first.

    Pinned rather than sampled on purpose. A weighted draw makes an overdue thing
    likely, and likely is not good enough for the case this tool exists for — the
    chore that has been at the top of the task list for a year does not need better
    odds, it needs to stop being optional.
    """
    overdue = []
    for name, config in state['active'].items():
        if not config.get('cadence'):
            continue
        if config.get('credit'):
            # Banking already accounts for the cadence. Asking overdue_days as
            # well would pin a pursuit that has been paid days forward, which is
            # the whole thing credit exists to stop.
            position = state['banked_position'].get(name)
            if position is None or position < 0:
                overdue.append((name, None if position is None else -position))
            continue
        last = state['last_done'].get(name)
        days = overdue_days(last[:10] if last else None, config['cadence'], state['today'])
        if days is None or days >= 0:
            overdue.append((name, days))
    overdue.sort(key=lambda row: (row[1] is not None, -(row[1] or 0)))
    return [name for name, _ in overdue]


def compute_draw(state: dict, seed: int | None = None) -> dict:
    """Draw the pins plus enough sampled pursuits to fill the screen."""
    pins = pinned(state)
    candidates = {name: weight for name, weight in state['effective'].items() if name not in pins}
    rng = random.Random(seed) if seed is not None else random.Random()
    drawn = draw(candidates, max(DRAW_SIZE - len(pins), 0), rng)
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
        return {'pursuit': name, 'label': str(row)}
    identifier = row.get(config['id']) if config.get('id') else None
    return {
        'pursuit': name,
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
    that ratio raised to alpha, and a longer one divides it. The stated weight is
    then not what the pursuit gets, and nothing else on the row says so — which
    is how a `1d` beside `weight: 25` came to take a third of every draw.

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


def behind_summary(state: dict) -> str:
    """How many completions would bring every banking pursuit back to current.

    A count rather than a duration, because it names the action: "3 away" is
    three chores, and that is a thing you can decide to do tonight. Only pursuits
    declaring credit appear — for the rest, being late is a scalar with no
    countable units behind it.
    """
    owed = {}
    for name, position in state.get('banked_position', {}).items():
        interval = state['intervals'].get(name, 0.0)
        if position is None or interval <= 0 or position > -interval:
            continue
        owed[name] = round(-position / interval)
    if not owed:
        return ''
    total = sum(owed.values())
    detail = ', '.join(f'{name} {count}' for name, count in sorted(owed.items(), key=lambda row: -row[1]))
    return f'{total} away from current · {detail}'


def format_banked(position: float | None, interval: float) -> str | None:
    """How far ahead or behind a banking pursuit stands, in whole units of its cadence.

    None inside a single interval, so the ordinary case falls through to the plain
    elapsed time — "1 ahead" and "today" say the same thing, and only one of them
    needs a new vocabulary to read.
    """
    if position is None or interval <= 0:
        return None
    # Rounded, not truncated. A completion logged seconds ago leaves an age of
    # about 1e-5 days, so three of them bank 2.9999 and truncation reports two.
    if position >= interval:
        return f'banked {round(position)}d'
    if position <= -interval:
        return f'behind {round(-position)}d'
    return None


def render_row(index: int, name: str, state: dict, resolved: dict, pin: bool, width: int) -> None:
    config = state['active'].get(name, {})
    weight = int(state['weights'].get(name, 0))
    banked = format_banked(state['banked_position'].get(name), state['intervals'].get(name, 0.0))
    if banked:
        when = banked
    elif pin:
        last = state['last_done'].get(name)
        days = overdue_days(last[:10] if last else None, config.get('cadence', '0d'), state['today'])
        when = status_label(days)
    else:
        when = format_elapsed(state['days_since'].get(name))

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
    if context or about:
        line = Text(indent)
        if context:
            line.append(context, style='cyan')
        if context and about:
            line.append(' — ')
        line.append(about)
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
    standing = behind_summary(state)
    if standing:
        console.print(Text(f'  {standing}\n', style='yellow'))
    # `\[note]` is escaped: rich reads a bare bracket as a style tag, which
    # silently dropped the optional argument from this hint.
    console.print(r'Log:  [cyan]doit log <pursuit> \[note][/]   Pass:  [cyan]doit skip <pursuit>[/]')
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
        'effective': {name: round(value, 3) for name, value in state['effective'].items()},
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
    for heading in ('wt', 'share', 'every', 'last', 'urgency', 'pick'):
        table.add_column(heading, justify='right')

    for name in sorted(state['active'], key=lambda key: -state['effective'][key]):
        interval = state['intervals'][name]
        every = '—' if math.isinf(interval) else f'{interval:.1f}d'
        urgency_value = state['effective'][name] / state['weights'][name] if state['weights'][name] else 0
        chosen = name in selection['pinned'] or name in selection['drawn']
        table.add_row(
            '[green]●[/]' if chosen else '',
            name,
            f'{int(state["weights"][name])}',
            f'{state["shares"][name] * 100:.1f}%',
            every,
            format_elapsed(state['days_since'][name]),
            f'{urgency_value:.2f}',
            f'{state["probability"][name] * 100:.1f}%',
        )
    console.print(table)
    console.print(f'\n  pick is the chance of being drawn [bold]first[/]; the draw takes {DRAW_SIZE} without replacement.')
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


def prompt_for_minutes() -> int | None:
    """Ask how long it took, accepting nothing as an answer.

    Deliberately not defaulted to the register's `minutes:` estimate. Enter would
    then write that estimate into the journal as a measurement, and the forecast
    reading it back would be quoting its own assumption. An unrecorded duration
    is a gap the forecast can say it has; a fabricated one is a gap it cannot see.
    """
    while True:
        answer = ask('  minutes, or Enter to skip: ')
        if not answer:
            return None
        if answer.isdigit() and int(answer) > 0:
            return int(answer)
        error_console.print('  A whole number of minutes, or Enter.')


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


def record_event(event: str, name: str, state: dict, extra: dict) -> dict:
    """Append one journal entry carrying the full state that produced it.

    The state vector is written into every record on purpose. Weights change, and
    once they do there is no way to reconstruct what the numbers were when a thing
    was logged — which is exactly what `drift` needs to be honest about a past
    week. Cheap to write now, impossible to backfill later.
    """
    now = state['now']
    record = {
        'id': new_id(now),
        'pursuit': name,
        'event': event,
        'logged_at': now.isoformat(),
        'occurred_at': extra.pop('occurred_at', now.isoformat()),
        'tz': str(now.tzinfo),
        'machine': machine_name(),
        **extra,
        'state_at_log': explain_payload(state),
    }
    return journal.append(journal_path(JOURNAL_DIR, machine_name()), record)


def cmd_log(name: str | None, words: list[str], ago: str | None, minutes: int | None, assume_yes: bool, no_write: bool) -> int:
    """Record having done one, asking for whatever was not passed as a flag.

    Anything given on the command line is never asked about, so the fast path
    stays one line. What is asked for is the pursuit, the note and the duration —
    the duration because `--minutes` is the input `drift` and `doit forecast`
    both need and the one nobody discovers from a help screen they never open.
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
        if not words and can_prompt():
            answer = ask('  note, or Enter to skip: ')
            words = answer.split() if answer else []
        if minutes is None and can_prompt():
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

    record_event(
        'done',
        matched,
        state,
        {
            'occurred_at': occurred.isoformat(),
            'note': ' '.join(words) or None,
            'duration_minutes': minutes,
            'item': item or None,
            'downstream': downstream,
            'draw_id': cached.get('draw_id'),
            'was_offered': matched in (cached.get('pinned', []) + cached.get('drawn', [])),
            'rank_in_draw': (cached.get('drawn', []).index(matched) + 1) if matched in cached.get('drawn', []) else None,
        },
    )
    # The draw outlives the log by up to a quarter of an hour, and re-offering
    # something just done reads as the log having gone nowhere.
    mark_logged(matched, now)

    interval = state['intervals'].get(matched)
    when = '' if interval is None or math.isinf(interval) else f' · due again in ~{interval:.0f}d'
    label = f' — {item["label"]}' if item.get('label') else ''
    logged = Text.from_markup('[green]Logged[/] ')
    logged.append(f'{matched}{label}{when}')
    console.print(logged)
    if downstream and downstream.get('ran'):
        ran = Text.from_markup('[green]Ran[/] ')
        ran.append(downstream['command'])
        console.print(ran)
    return 0


def cmd_skip(name: str | None) -> int:
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
    record_event('skip', matched, state, {'draw_id': cached.get('draw_id')})
    # The pass only means something against a new draw, and the suppression it
    # applies is already in the journal, so the stale cache goes.
    DRAW_CACHE.unlink(missing_ok=True)
    passed = Text.from_markup('[yellow]Passed[/] ')
    # A pin is decided by cadence alone and never consults the effective weight, so
    # suppression cannot reach one. Saying "suppressed" there would be a promise
    # the next draw breaks immediately, and the pursuit reappearing at the top
    # would read as the skip having been dropped.
    if matched in pinned(state):
        passed.append(f'{matched} — still pinned by its {pursuits[matched]["cadence"]} cadence, so it will be offered again.')
    else:
        passed.append(f'{matched} — suppressed for about one interval, weight untouched.')
    console.print(passed)
    return 0


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
        if config.get('paused'):
            line.append('  paused', style='yellow')
        elif term_ended(config, state['today']):
            line.append(f'  term ended {config["until"]}', style='yellow')
        console.print(line)
    console.print('\n  share is what each weight implies · ×n is a cadence outrunning it\n  [cyan]doit pursuits edit[/]')
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


def cmd_drift(days: int, as_json: bool) -> int:
    """Stated weight against what actually happened.

    The report the whole thing is for. It never adjusts a weight — revealed and
    stated preference are different signals and blending them would destroy the
    only honest comparison available. Offered counts sit next to realized share
    because they separate the two failures that look identical from the outside: a
    pursuit that never comes up, and one that comes up and gets ignored.

    ``did`` is a share of active days, not of journal entries. A day is the only
    unit both records can express, so it is what makes a pursuit with an app
    behind it comparable to one whose journal is its whole history. It cannot
    tell one commit from eight hours, which is what ``time`` and ``logs`` are
    beside it — both typed, and both blank for a pursuit done inside its app.
    """
    pursuits = load_pursuits()
    if not pursuits:
        return 1
    now = datetime.now().astimezone()
    state = build_state(pursuits, now)
    cutoff = now - timedelta(days=days)

    done = [
        record
        for record in state['records']
        if record.get('event') == 'done' and (journal.parse_time(record.get('occurred_at')) or now) >= cutoff
    ]
    skipped = [
        record
        for record in state['records']
        if record.get('event') == 'skip' and (journal.parse_time(record.get('occurred_at')) or now) >= cutoff
    ]
    counts = load_counts(JOURNAL_DIR)

    total_logs = len(done)
    total_minutes = sum(record.get('duration_minutes') or 0 for record in done)
    by_count: dict[str, int] = {}
    by_minutes: dict[str, int] = {}
    skips: dict[str, int] = {}
    for record in done:
        name = record.get('pursuit')
        by_count[name] = by_count.get(name, 0) + 1
        by_minutes[name] = by_minutes.get(name, 0) + (record.get('duration_minutes') or 0)
    for record in skipped:
        name = record.get('pursuit')
        skips[name] = skips.get(name, 0) + 1

    # A day is the unit both records can express. A pursuit with an app behind it
    # is done inside that app and never reaches the journal, so counting entries
    # measures what got retyped and reports the busiest strand as the idle one.
    typed_days = {name: set(journal.days(done, name, 'done', now)) for name in pursuits}
    app_days = {name: {day for day in seen if day >= cutoff.date()} for name, seen in state['evidence_days'].items()}
    # Over the register rather than over every record, so the shares sum to what
    # the table shows: a retired pursuit still holding journal days would
    # otherwise take a slice of the denominator and never appear in a row.
    active_days = {name: typed_days[name] | app_days.get(name, set()) for name in pursuits}
    # A paused or expired pursuit stated nothing for this window, so it keeps a row
    # only while it still has days in one — and reports no stated share rather than
    # 0%, which reads as a claim it never made. Pausing also drops its evidence
    # entry, so an untouched one would sit here as a hollow row forever.
    shown = [name for name in pursuits if name in state['active'] or active_days[name]]
    total_days = sum(len(active_days[name]) for name in shown)

    rows = []
    for name in sorted(shown, key=lambda key: -pursuits[key].get('weight', 0)):
        stated = state['shares'].get(name, 0.0) * 100 if name in state['active'] else None
        realized = (len(active_days[name]) / total_days * 100) if total_days else 0.0
        time_share = (by_minutes.get(name, 0) / total_minutes * 100) if total_minutes else None
        rows.append(
            {
                'pursuit': name,
                'stated_share': None if stated is None else round(stated, 1),
                'realized_share': round(realized, 1),
                'days': len(active_days[name]),
                'app_days': len(app_days.get(name, set())),
                'typed_days': len(typed_days[name]),
                'time_share': None if time_share is None else round(time_share, 1),
                'logs': by_count.get(name, 0),
                'minutes': by_minutes.get(name, 0),
                'offered': counts.get(name, 0),
                'skips': skips.get(name, 0),
            }
        )

    if as_json:
        # See cmd_next: a Console would soft-wrap this into invalid JSON.
        print(json.dumps({'window_days': days, 'total_logs': total_logs, 'total_days': total_days, 'rows': rows}, indent=2))
        return 0

    console.rule(f'[cyan]Drift · last {days} days', align='left')
    if not total_days:
        console.print('Nothing recorded in the window yet — drift needs history before it can say anything.\n')
        return 0

    table = Table(box=None, pad_edge=False)
    table.add_column('pursuit')
    for heading in ('said', 'did', 'days', 'logs', 'time', 'offered', 'passed'):
        table.add_column(heading, justify='right')
    for row in rows:
        stated = row['stated_share']
        did = f'{row["realized_share"]:.0f}%'
        # A paused pursuit has no claim to have missed, so its days are reported
        # without a verdict rather than colored against a share it never stated.
        if stated is not None:
            gap = row['realized_share'] - stated
            did = f'[green]{did}[/]' if abs(gap) < 10 else f'[yellow]{did}[/]'
        table.add_row(
            row['pursuit'],
            '—' if stated is None else f'{stated:.0f}%',
            did,
            str(row['days']),
            str(row['logs']),
            '—' if row['time_share'] is None else f'{row["time_share"]:.0f}%',
            str(row['offered']),
            str(row['skips']),
        )
    console.print(table)
    plural = '' if total_days == 1 else 's'
    console.print(f'\n  {total_days} active day{plural} across the register · weights are never auto-adjusted')
    console.print(f'  [dim]did = share of those days · logs and minutes are typed only ({total_logs} logs, {total_minutes} min)[/]')
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
    console.print('\n  A pursuit this far past its own interval is a weight that is not true.\n')
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
        typer.Option('--minutes', help='How long it took, so drift and forecast weigh time (asked for when omitted).'),
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
) -> None:
    """Pass on one — suppressed for about an interval, weight untouched."""
    run(lambda: cmd_skip(pursuit))


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
