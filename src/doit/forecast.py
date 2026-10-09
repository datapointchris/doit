"""What the register would actually have you do, simulated forward a month at a time.

`doit pursuits drift` answers this backwards — each goal against what you did.
It can only speak about days that already happened, and it needs a season of them
before it says anything. This answers it forwards, from the register as it stands
right now, which is what makes a goal or a weight arguable *before* a month is
spent proving it wrong.

**It runs the real draw, never a model of it.** Every simulated day calls
:func:`doit.pursuits.build_state` and :func:`doit.pursuits.compute_draw` against a
journal the simulation is building as it goes. A reimplementation would be easier
to read and would start disagreeing with the allocator the first time either
moved, at which point the forecast becomes a confident description of a tool that
no longer exists. The injection points on ``build_state`` exist for this.

**The competition for a day is the thing worth simulating, and it is why a
spreadsheet cannot do this.** Every goal is declared on its own, but a day's
budget is shared. What is owed comes first, heaviest first, so a heavy pursuit
falling behind takes the head of the list, and whatever it pushes off the end of
a short day falls behind in turn. Whether a register's goals fit in its budget
cannot be read off the page.

Two inputs it cannot derive and does not pretend to:

**How long a sitting takes.** Measured from ``duration_minutes`` in the journal
once :data:`MEASURED_MINIMUM` logs carry one, and :data:`FALLBACK_MINUTES` until
then. A goal in minutes a week says how much, never how long one sitting is, so
the register has no estimate to offer. Which of the two answered is recorded per
pursuit and printed, because a forecast resting on eight defaults is a different
claim from one resting on eight measurements, and nothing else on screen would
say which you are reading.

**What a day holds.** ``forecast.budget_minutes`` in the register. Discretionary
time the draw is allowed to spend, not the length of a day.

The behavioral model is one rule: walk the offered list from the top, do what
fits in what is left, stop when nothing left fits. What that order is belongs to
`pursuits.offered_order`, which puts what is owed first, heaviest first — so the
budget goes to the heaviest debt, and the tail of the list is what a short day
never reaches.

Durations are point estimates rather than sampled from an invented spread. What
varies across replicates is then which pursuits the draw offers, which is a real
random process, rather than a distribution nobody chose dressed up as a
confidence interval. The order within one offer does not vary, because it is
derived from the balances that day.

Readings live under the state directory for the reason :mod:`doit.digest` gives:
a recompute cannot rebuild one, because it would read a register and a journal
that have since moved. One append-only file per machine, one writer each.
"""

import dataclasses
import datetime as dt
import json
import statistics
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Annotated

import typer
from rich.text import Text

from doit import journal
from doit import pursuits
from doit.allocate import balance
from doit.paths import journal_dir
from doit.paths import machine_name
from doit.render import console
from doit.render import error_console

SCHEMA_VERSION = 1

# Horizons a reading reports. A week is the shortest span over which a weekly
# goal can express itself at all, and a month is long enough for every cadence a
# real register declares to come round more than once.
HORIZONS = (7, 14, 30)

DEFAULT_BUDGET_MINUTES = 120

# Replicates per reading. The draw is sampled without replacement from eight
# candidates, so the mean settles quickly; this is chosen for a stable tenth of an
# occasion rather than for a tight tail.
REPLICATES = 300

# Logs carrying a duration before the journal outranks the register's estimate.
# Below this a single unusual evening moves the median more than the estimate is
# wrong by.
MEASURED_MINIMUM = 3

# What a pursuit declaring nothing is assumed to cost. Deliberately close to the
# median of a register that has been filled in, so an unestimated pursuit is not
# silently free and not silently ruinous.
FALLBACK_MINUTES = 45

# Below this there is no point starting anything, so the evening is over. Real
# registers put nothing under a quarter of an hour.
MINIMUM_SLICE_MINUTES = 10


@dataclass(frozen=True)
class Duration:
    """What one occasion of a pursuit is assumed to cost, and where that came from."""

    minutes: float
    source: str  # 'measured' | 'default'
    samples: int


@dataclass(frozen=True)
class Prediction:
    """One pursuit's share of one horizon, beside what its goal asks over the same span.

    ``asked`` is in the pursuit's own unit — minutes where it declares
    `weekly_minutes`, occurrences where it declares a cadence — so it is read
    against ``minutes`` for one and ``occasions`` for the other.
    """

    occasions: float
    minutes: float
    occasions_low: float
    occasions_high: float
    asked: float
    unit: str  # 'minutes' | 'checkoffs'


@dataclass(frozen=True)
class Reading:
    """One forecast, beside every input it was taken from.

    The inputs are stored because a prediction is only interpretable against them:
    the same register forecasts differently under one hour a day and under two,
    and a reading that recorded only its output could not later be told apart
    from one taken under a goal nobody holds any more.
    """

    generated: str
    machine: str
    budget_minutes: int
    replicates: int
    weights: dict[str, float] = field(default_factory=dict)
    goals: dict[str, str] = field(default_factory=dict)
    durations: dict[str, dict] = field(default_factory=dict)
    horizons: dict[str, dict[str, dict]] = field(default_factory=dict)
    unspent_minutes_per_day: float = 0.0


def durations(register: dict, records: list[dict]) -> dict[str, Duration]:
    """How long one sitting of each pursuit takes, measured where the journal can say.

    The median rather than the mean, because one evening that ran long is exactly
    the shape of outlier a small journal will hold and exactly the one a mean
    cannot survive.
    """
    logged: dict[str, list[float]] = {}
    for record in records:
        if record.get('event') != journal.Event.DONE:
            continue
        minutes = record.get('duration_minutes')
        if isinstance(minutes, int | float) and not isinstance(minutes, bool) and minutes > 0:
            logged.setdefault(str(record.get('pursuit')), []).append(float(minutes))

    answer: dict[str, Duration] = {}
    for name in register:
        samples = logged.get(name, [])
        if len(samples) >= MEASURED_MINIMUM:
            answer[name] = Duration(statistics.median(samples), 'measured', len(samples))
        else:
            answer[name] = Duration(float(FALLBACK_MINUTES), 'default', len(samples))
    return answer


def spend_a_day(offered: list[str], cost: dict[str, Duration], budget: float) -> list[tuple[str, float]]:
    """Walk the offered list from the top, taking what fits in what is left.

    The whole behavior of a simulated day, and the one place a reader should argue
    with it. What order the list arrives in is the other half and lives in
    `pursuits.offered_order`. Top-down is what makes the head of that list
    expensive: a five-row draw that only ever reaches its third row spends the
    budget on the three things furthest past due.

    Something that does not fit is stepped over rather than ending the day — a
    short row further down is still doable — but nothing is ever done in part.
    """
    spent: list[tuple[str, float]] = []
    left = budget
    for name in offered:
        if left < MINIMUM_SLICE_MINUTES:
            break
        minutes = cost[name].minutes if name in cost else float(FALLBACK_MINUTES)
        if minutes <= left:
            spent.append((name, minutes))
            left -= minutes
    return spent


def simulate(
    register: dict,
    seed_records: list[dict],
    observed: dict[str, dt.datetime],
    cost: dict[str, Duration],
    start: dt.datetime,
    days: int,
    budget: float,
    replicate: int,
) -> list[tuple[int, str, float]]:
    """One replicate: the real draw, run forward a day at a time against its own journal.

    ``observed`` is frozen at the moment of the forecast. Evidence would otherwise
    have to be invented for a future the backends cannot be asked about, and an
    invented one would decide the answer — a pursuit whose backend keeps reporting
    it as freshly done never comes up at all.
    """
    records = list(seed_records)
    done: list[tuple[int, str, float]] = []
    for day in range(days):
        when = start + dt.timedelta(days=day)
        state = pursuits.build_state(register, when, records=records, observed=observed)
        selection = pursuits.compute_draw(state, seed=replicate * 100_003 + day)
        for name, minutes in spend_a_day(selection['offered'], cost, budget):
            done.append((day, name, minutes))
            records.append(
                {
                    'pursuit': name,
                    'event': journal.Event.DONE,
                    'occurred_at': when.isoformat(),
                    'duration_minutes': minutes,
                }
            )
    return done


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile, which needs no interpolation and no numpy."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def forecast(register: dict, now: dt.datetime, budget: float, replicates: int = REPLICATES) -> Reading:
    """Run every replicate and fold them into one reading."""
    directory = journal_dir()
    records = journal.read_all(directory) if directory.exists() else []
    live = pursuits.build_state(register, now)
    active = live['active']
    cost = durations(active, records)
    horizon = max(HORIZONS)
    units = {name: 'minutes' if name in live['weekly_minutes'] else 'checkoffs' for name in active}

    runs = [simulate(register, records, live['observed'], cost, now, horizon, budget, replicate) for replicate in range(replicates)]

    horizons: dict[str, dict[str, dict]] = {}
    for days in HORIZONS:
        counts: dict[str, list[float]] = {name: [] for name in active}
        minutes: dict[str, list[float]] = {name: [] for name in active}
        for run in runs:
            per_run_count = dict.fromkeys(active, 0.0)
            per_run_minutes = dict.fromkeys(active, 0.0)
            for day, name, spent in run:
                if day < days and name in per_run_count:
                    per_run_count[name] += 1
                    per_run_minutes[name] += spent
            for name in active:
                counts[name].append(per_run_count[name])
                minutes[name].append(per_run_minutes[name])
        horizons[str(days)] = {
            name: dataclasses.asdict(
                Prediction(
                    round(statistics.mean(counts[name]), 2),
                    round(statistics.mean(minutes[name]), 1),
                    percentile(counts[name], 0.1),
                    percentile(counts[name], 0.9),
                    round(balance(days, live['intervals'][name], live['checkoff_size'][name], 0.0), 1),
                    units[name],
                )
            )
            for name in active
        }

    spent_per_day = statistics.mean(sum(minutes for _, _, minutes in run) / horizon for run in runs)
    return Reading(
        generated=now.isoformat(),
        machine=machine_name(),
        budget_minutes=int(budget),
        replicates=replicates,
        weights=dict(live['weights']),
        goals={name: pursuits.goal_text(config) for name, config in active.items()},
        durations={name: dataclasses.asdict(value) for name, value in cost.items()},
        horizons=horizons,
        unspent_minutes_per_day=round(budget - spent_per_day, 1),
    )


def reading_path(directory: Path, machine: str) -> Path:
    """This machine's readings. One writer per file is the whole sync story."""
    return directory / f'forecast-{machine}.jsonl'


def append(path: Path, reading: Reading) -> Reading:
    record = {'schema_version': SCHEMA_VERSION, **dataclasses.asdict(reading)}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record) + '\n')
    return reading


def read_all(directory: Path) -> list[Reading]:
    """Every machine's readings, merged and ordered oldest first.

    A malformed line is skipped rather than fatal, for the reason the journal
    gives: one half-synced line must not make the rest of the record unreadable.
    """
    stored: list[Reading] = []
    if not directory.exists():
        return stored
    for path in sorted(directory.glob('forecast-*.jsonl')):
        for line in path.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            names = {entry.name for entry in dataclasses.fields(Reading)}
            fields = {name: value for name, value in record.items() if name in names}
            if fields.get('generated'):
                stored.append(Reading(**fields))
    return sorted(stored, key=lambda reading: reading.generated)


def select(stored: list[Reading], handle: str = '') -> Reading | None:
    """The reading a handle names, or the newest one when no handle is given."""
    matching = [reading for reading in stored if reading.generated.startswith(handle)] if handle else stored
    return matching[-1] if matching else None


@dataclass(frozen=True)
class Verdict:
    """One pursuit's prediction against what the journal went on to record."""

    pursuit: str
    predicted: float
    actual: float
    readings: int

    @property
    def error(self) -> float:
        return self.actual - self.predicted


def actual_occasions(records: list[dict], start: dt.datetime, days: int) -> dict[str, int]:
    """Done events per pursuit inside a window, counted from the journal."""
    finish = start + dt.timedelta(days=days)
    counted: dict[str, int] = {}
    for record in records:
        if record.get('event') != journal.Event.DONE:
            continue
        when = journal.parse_time(record.get('occurred_at') or record.get('logged_at'))
        if when is None or not (start <= when < finish):
            continue
        counted[str(record.get('pursuit'))] = counted.get(str(record.get('pursuit')), 0) + 1
    return counted


def matured(stored: list[Reading], now: dt.datetime, days: int) -> list[Reading]:
    """Readings old enough that their `days`-horizon prediction can be graded.

    A prediction cannot be wrong yet if the window it named has not closed, so
    grading one early would report every pursuit as under-done and the error would
    be the calendar rather than the model.
    """
    ready = []
    for reading in stored:
        when = journal.parse_time(reading.generated)
        if when is not None and (now - when) >= dt.timedelta(days=days):
            ready.append(reading)
    return ready


def grade(stored: list[Reading], records: list[dict], now: dt.datetime, days: int) -> list[Verdict]:
    """Every matured reading's prediction at one horizon, against the journal.

    Averaged across readings rather than reported per reading: several forecasts
    taken hours apart are near-identical predictions of overlapping windows, so
    listing them separately would present one measurement as many.
    """
    ready = matured(stored, now, days)
    if not ready:
        return []
    predicted: dict[str, list[float]] = {}
    happened: dict[str, list[float]] = {}
    for reading in ready:
        window = journal.parse_time(reading.generated)
        if window is None:
            continue
        counts = actual_occasions(records, window, days)
        for name, prediction in reading.horizons.get(str(days), {}).items():
            predicted.setdefault(name, []).append(float(prediction['occasions']))
            happened.setdefault(name, []).append(float(counts.get(name, 0)))
    return sorted(
        (Verdict(name, statistics.mean(values), statistics.mean(happened[name]), len(values)) for name, values in predicted.items()),
        key=lambda verdict: -abs(verdict.error),
    )


def source_summary(reading: Reading) -> str:
    """How much of the forecast rests on measurement rather than on an estimate."""
    tally: dict[str, int] = {}
    for value in reading.durations.values():
        tally[value['source']] = tally.get(value['source'], 0) + 1
    parts = [f'{count} {source}' for source, count in sorted(tally.items(), key=lambda row: -row[1])]
    return ' · '.join(parts)


def goal_met(prediction: dict) -> float | None:
    """How much of its goal a pursuit's horizon reaches, as a percentage.

    Read in the goal's own unit: minutes for a pursuit declaring weekly minutes,
    occasions for one declaring a cadence. A reading taken before goals were
    recorded has nothing to compare against and answers None.
    """
    asked = prediction.get('asked')
    if not asked:
        return None
    did = prediction['minutes'] if prediction.get('unit') == 'minutes' else prediction['occasions']
    return did / asked * 100


def emit(reading: Reading) -> None:
    """One reading, as occasions and hours against what each goal asks for in a month."""
    month = reading.horizons.get('30', {})
    week = reading.horizons.get('7', {})
    goals = max((len(text) for text in reading.goals.values()), default=4)

    console.rule(f'[cyan]Forecast · {reading.budget_minutes} min a day', align='left')
    header = Text('  ')
    header.append(f'{"pursuit":<9} {"/wk":>5} {"/mo":>6} {"h/mo":>6}  {"goal":<{goals}} {"of goal":>8}  {"est":<9}', style='dim')
    console.print(header)
    for name in sorted(month, key=lambda row: -month[row]['occasions']):
        met = goal_met(month[name])
        estimate = reading.durations.get(name, {})
        row = Text('  ')
        row.append(f'{name:<9} ', style='white')
        row.append(f'{week.get(name, {}).get("occasions", 0):>5.1f} {month[name]["occasions"]:>6.1f} ')
        row.append(f'{month[name]["minutes"] / 60:>6.1f}  ')
        row.append(f'{reading.goals.get(name, "—"):<{goals}} ')
        row.append(f'{"—" if met is None else f"{met:.0f}%":>8}  ', style='yellow' if met is not None and met < 90 else '')
        row.append(f'{int(estimate.get("minutes", 0))}m {estimate.get("source", "")}', style='dim')
        console.print(row)

    console.print(f'\n  {reading.unspent_minutes_per_day:.0f} min/day left unspent · {reading.replicates} runs')
    console.print(f'  estimates: {source_summary(reading)} · [cyan]doit log --minutes[/] measures them')


def emit_trend(verdicts: list[Verdict], days: int, readings: int) -> None:
    console.rule(f'[cyan]Trend · {days}-day predictions against the journal', align='left')
    if not verdicts:
        console.print(f'  No reading is {days} days old yet, so nothing can be graded.')
        console.print('  [cyan]doit forecast run[/] takes one; the schedule takes them unattended.')
        return
    header = Text('  ')
    header.append(f'{"pursuit":<9} {"said":>7} {"did":>7} {"error":>8}', style='dim')
    console.print(header)
    for verdict in verdicts:
        row = Text('  ')
        row.append(f'{verdict.pursuit:<9} ', style='white')
        row.append(f'{verdict.predicted:>7.1f} {verdict.actual:>7.1f} ')
        row.append(f'{verdict.error:>+8.1f}', style='yellow' if abs(verdict.error) >= 1 else 'dim')
        console.print(row)
    console.print(f'\n  averaged over {readings} readings whose {days}-day window has closed')
    console.print('  error is what the journal recorded minus what the forecast predicted')


def cmd_run(as_json: bool, budget: int | None, directory: Path) -> int:
    """Take a forecast and store it."""
    register = pursuits.load_pursuits()
    if not register:
        error_console.print('No pursuits yet:  [cyan]doit pursuits edit[/]')
        return 1
    settings = pursuits.load_settings()
    minutes = budget or settings.get('budget_minutes') or DEFAULT_BUDGET_MINUTES
    reading = forecast(register, dt.datetime.now().astimezone(), float(minutes))
    stored = True
    try:
        append(reading_path(directory, machine_name()), reading)
    except OSError as failure:
        error_console.print(f'Forecast taken but not stored at {directory} — {failure}')
        stored = False
    if as_json:
        print(json.dumps(dataclasses.asdict(reading), indent=2))
    else:
        emit(reading)
    return 0 if stored else 1


def cmd_show(handle: str, as_json: bool, directory: Path) -> int:
    """The newest stored forecast, or the one a timestamp prefix names."""
    stored = read_all(directory)
    reading = select(stored, handle)
    if reading is None:
        error_console.print('No forecast stored yet:  [cyan]doit forecast run[/]')
        return 1
    if as_json:
        print(json.dumps(dataclasses.asdict(reading), indent=2))
    else:
        emit(reading)
    return 0


def cmd_list(as_json: bool, directory: Path) -> int:
    """Every stored forecast, oldest first."""
    stored = read_all(directory)
    if not stored:
        error_console.print('No forecast stored yet:  [cyan]doit forecast run[/]')
        return 1
    if as_json:
        print(json.dumps([dataclasses.asdict(reading) for reading in stored], indent=2))
        return 0
    console.rule('[cyan]Forecasts', align='left')
    for reading in stored:
        row = Text('  ')
        row.append(f'{reading.generated[:16].replace("T", " ")}  ', style='white')
        row.append(f'{reading.machine:<10} {reading.budget_minutes:>4}m/day  ', style='dim')
        row.append(source_summary(reading), style='dim')
        console.print(row)
    plural = '' if len(stored) == 1 else 's'
    console.print(f'\n  {len(stored)} reading{plural} · [cyan]doit forecast trend[/] grades the matured ones')
    return 0


def cmd_trend(days: int, as_json: bool, directory: Path) -> int:
    """What the forecasts predicted against what the journal went on to record."""
    stored = read_all(directory)
    if not stored:
        error_console.print('No forecast stored yet:  [cyan]doit forecast run[/]')
        return 1
    journal_directory = journal_dir()
    records = journal.read_all(journal_directory) if journal_directory.exists() else []
    now = dt.datetime.now().astimezone()
    verdicts = grade(stored, records, now, days)
    if as_json:
        print(json.dumps({'days': days, 'verdicts': [dataclasses.asdict(v) for v in verdicts]}, indent=2))
        return 0
    emit_trend(verdicts, days, len(matured(stored, now, days)))
    return 0


app = typer.Typer(name='forecast', no_args_is_help=True, help='What the register would have you do, run forward.')

JsonOption = Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')]


@app.command('run')
def run_command(
    budget: Annotated[int | None, typer.Option('--budget', help='Minutes a day to spend, overriding the register.')] = None,
    as_json: JsonOption = False,
) -> None:
    """Take a forecast from the register as it stands, and store it."""
    pursuits.run(lambda: cmd_run(as_json, budget, journal_dir()))


@app.command('show')
def show_command(
    handle: Annotated[str, typer.Argument(help='Timestamp prefix; the newest when omitted.')] = '',
    as_json: JsonOption = False,
) -> None:
    """Read a stored forecast back."""
    pursuits.run(lambda: cmd_show(handle, as_json, journal_dir()))


@app.command('list')
def list_command(as_json: JsonOption = False) -> None:
    """Every forecast taken, oldest first."""
    pursuits.run(lambda: cmd_list(as_json, journal_dir()))


@app.command('trend')
def trend_command(
    days: Annotated[int, typer.Option('--days', help='Which horizon to grade: 7, 14 or 30.')] = 7,
    as_json: JsonOption = False,
) -> None:
    """Grade the forecasts whose window has closed against the journal."""
    pursuits.run(lambda: cmd_trend(days, as_json, journal_dir()))
