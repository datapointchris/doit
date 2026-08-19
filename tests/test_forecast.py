"""Tests for doit.forecast — durations, the day model, the simulation, and grading.

The draw's own math is tested in test_allocate and test_pursuits. What is tested
here is the layer that runs it forward: which duration wins, how a day is spent,
that a simulated log feeds back into the next day's state, and that a prediction
is only graded once its window has closed.

The simulation deliberately calls the real `build_state`/`compute_draw`, so a test
that a forecast reaches a pinned pursuit is also a test that the two have not come
apart.
"""

import json
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import pytest

from doit import forecast
from doit import journal
from doit import pursuits

FIXTURE_DIR = Path(__file__).resolve().parent / 'fixtures' / 'pursuits'
NOW = datetime.fromisoformat('2026-08-04T12:00:00-04:00')


@pytest.fixture(autouse=True)
def register(monkeypatch, tmp_path):
    monkeypatch.setattr(pursuits, 'REGISTER', FIXTURE_DIR / 'pursuits.yml')
    monkeypatch.setattr(pursuits, 'JOURNAL_DIR', tmp_path / 'state')
    monkeypatch.setattr(pursuits, 'CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setattr(pursuits, 'machine_name', lambda: 'testbox')
    monkeypatch.setattr(forecast, 'machine_name', lambda: 'testbox')
    return tmp_path


def done(pursuit: str, minutes: int | None = None, days_ago: float = 0.0) -> dict:
    return {
        'pursuit': pursuit,
        'event': 'done',
        'occurred_at': (NOW - timedelta(days=days_ago)).isoformat(),
        'duration_minutes': minutes,
    }


def reading(generated: datetime, horizons: dict, machine: str = 'testbox') -> forecast.Reading:
    return forecast.Reading(
        generated=generated.isoformat(),
        machine=machine,
        budget_minutes=120,
        replicates=10,
        logs_per_day=2.0,
        measured_rate=2.0,
        weights={'chores': 25.0},
        durations={'chores': {'minutes': 25.0, 'source': 'declared', 'samples': 0}},
        horizons=horizons,
    )


def test_a_declared_estimate_is_used_until_the_journal_can_answer():
    register = {'a': {'weight': 5, 'minutes': 30}}
    assert forecast.durations(register, [done('a', 90)]).get('a') == forecast.Duration(30.0, 'declared', 1)


def test_the_journal_outranks_the_estimate_once_it_has_enough_samples():
    register = {'a': {'weight': 5, 'minutes': 30}}
    records = [done('a', 60), done('a', 90), done('a', 120)]
    measured = forecast.durations(register, records)['a']
    assert measured.source == 'measured'
    assert measured.minutes == 90.0
    assert measured.samples == 3


def test_the_median_is_taken_rather_than_the_mean():
    # One evening that ran long is the shape of outlier a small journal holds.
    register = {'a': {'weight': 5}}
    records = [done('a', 20), done('a', 25), done('a', 600)]
    assert forecast.durations(register, records)['a'].minutes == 25.0


def test_a_pursuit_declaring_nothing_falls_back_rather_than_costing_nothing():
    assert forecast.durations({'a': {'weight': 5}}, [])['a'] == forecast.Duration(float(forecast.FALLBACK_MINUTES), 'default', 0)


@pytest.mark.parametrize('bad', [0, -10, None, True])
def test_a_duration_that_is_not_a_positive_number_is_not_a_sample(bad):
    register = {'a': {'weight': 5, 'minutes': 30}}
    records = [done('a', bad), done('a', bad), done('a', bad)]
    assert forecast.durations(register, records)['a'].source == 'declared'


def costs(**pairs: float) -> dict[str, forecast.Duration]:
    return {name: forecast.Duration(minutes, 'declared', 0) for name, minutes in pairs.items()}


def test_a_day_is_spent_from_the_top_of_the_offered_list():
    spent = forecast.spend_a_day(['a', 'b', 'c'], costs(a=50, b=50, c=50), 120)
    assert [name for name, _ in spent] == ['a', 'b']


def test_something_that_does_not_fit_is_stepped_over_rather_than_ending_the_day():
    # A short row further down is still doable, so the day is not over.
    spent = forecast.spend_a_day(['big', 'small'], costs(big=200, small=20), 120)
    assert [name for name, _ in spent] == ['small']


def test_nothing_is_ever_done_in_part():
    spent = forecast.spend_a_day(['a'], costs(a=200), 120)
    assert spent == []


def test_a_remainder_too_small_to_start_anything_ends_the_day():
    spent = forecast.spend_a_day(['a', 'b'], costs(a=115, b=1), 120)
    assert [name for name, _ in spent] == ['a']


def test_an_unpriced_pursuit_still_costs_something():
    spent = forecast.spend_a_day(['ghost'], {}, 120)
    assert spent == [('ghost', float(forecast.FALLBACK_MINUTES))]


def test_the_simulation_feeds_its_own_logs_back_into_the_next_day(register):
    # The loop that makes this worth simulating: a log moves the measured rate,
    # the rate moves every implied interval, and the intervals move the draw.
    active = pursuits.build_state(pursuits.load_pursuits(), NOW)['active']
    cost = forecast.durations(active, [])
    run = forecast.simulate(pursuits.load_pursuits(), [], {}, cost, NOW, 5, 120, replicate=1)
    assert run
    assert {day for day, _, _ in run} <= set(range(5))
    # Nothing is done twice in one day: the draw samples without replacement.
    for day in range(5):
        names = [name for chosen, name, _ in run if chosen == day]
        assert len(names) == len(set(names))


def test_the_simulation_reaches_the_pursuit_the_real_draw_pins(register):
    # `chores` is the fixture's cadence pursuit and has never been done, so the
    # live allocator pins it. A forecast that never offered it would mean the
    # simulation had stopped running the same draw.
    active = pursuits.build_state(pursuits.load_pursuits(), NOW)['active']
    cost = forecast.durations(active, [])
    run = forecast.simulate(pursuits.load_pursuits(), [], {}, cost, NOW, 3, 120, replicate=1)
    assert 'chores' in {name for _, name, _ in run}


def test_a_forecast_predicts_every_active_pursuit_at_every_horizon(register):
    result = forecast.forecast(pursuits.load_pursuits(), NOW, 120, replicates=5)
    active = pursuits.build_state(pursuits.load_pursuits(), NOW)['active']
    for days in forecast.HORIZONS:
        assert set(result.horizons[str(days)]) == set(active)
    assert 'paused-thing' not in result.horizons['7']


def test_a_longer_horizon_never_predicts_fewer_occasions(register):
    result = forecast.forecast(pursuits.load_pursuits(), NOW, 120, replicates=5)
    for name in result.horizons['7']:
        assert result.horizons['30'][name]['occasions'] >= result.horizons['7'][name]['occasions']


def test_a_bigger_budget_never_predicts_less_work(register):
    lean = forecast.forecast(pursuits.load_pursuits(), NOW, 60, replicates=5)
    rich = forecast.forecast(pursuits.load_pursuits(), NOW, 240, replicates=5)

    def total(result):
        return sum(row['occasions'] for row in result.horizons['30'].values())

    assert total(rich) > total(lean)


def test_a_reading_records_where_each_duration_came_from(register):
    result = forecast.forecast(pursuits.load_pursuits(), NOW, 120, replicates=5)
    assert {value['source'] for value in result.durations.values()} <= {'measured', 'declared', 'default'}


def test_a_reading_round_trips_through_the_store(tmp_path):
    stored = reading(NOW, {'7': {'chores': {'occasions': 1.0, 'minutes': 25.0}}})
    path = forecast.reading_path(tmp_path, 'testbox')
    forecast.append(path, stored)
    assert forecast.read_all(tmp_path) == [stored]


def test_a_half_synced_line_does_not_make_the_rest_unreadable(tmp_path):
    path = forecast.reading_path(tmp_path, 'testbox')
    forecast.append(path, reading(NOW, {}))
    with path.open('a') as handle:
        handle.write('{"generated": broken\n')
    assert len(forecast.read_all(tmp_path)) == 1


def test_readings_from_every_machine_are_merged_oldest_first(tmp_path):
    forecast.append(forecast.reading_path(tmp_path, 'b'), reading(NOW, {}, machine='b'))
    forecast.append(forecast.reading_path(tmp_path, 'a'), reading(NOW - timedelta(days=1), {}, machine='a'))
    assert [row.machine for row in forecast.read_all(tmp_path)] == ['a', 'b']


def test_select_takes_the_newest_when_no_handle_is_given(tmp_path):
    path = forecast.reading_path(tmp_path, 'testbox')
    forecast.append(path, reading(NOW - timedelta(days=2), {}))
    forecast.append(path, reading(NOW, {}))
    assert forecast.select(forecast.read_all(tmp_path)).generated == NOW.isoformat()


def test_a_handle_selects_by_timestamp_prefix(tmp_path):
    path = forecast.reading_path(tmp_path, 'testbox')
    forecast.append(path, reading(NOW - timedelta(days=2), {}))
    forecast.append(path, reading(NOW, {}))
    assert forecast.select(forecast.read_all(tmp_path), '2026-08-02').generated.startswith('2026-08-02')


def test_occasions_are_counted_inside_the_window_only():
    records = [done('a', days_ago=1), done('a', days_ago=9), done('b', days_ago=2)]
    counted = forecast.actual_occasions(records, NOW - timedelta(days=7), 7)
    assert counted == {'a': 1, 'b': 1}


def test_a_reading_whose_window_has_not_closed_is_not_graded():
    # Grading early reports every pursuit as under-done, and the error would be
    # the calendar rather than the model.
    fresh = [reading(NOW - timedelta(days=3), {'7': {'chores': {'occasions': 2.0}}})]
    assert forecast.matured(fresh, NOW, 7) == []
    assert forecast.grade(fresh, [], NOW, 7) == []


def test_a_matured_reading_is_graded_against_what_the_journal_recorded():
    stored = [reading(NOW - timedelta(days=8), {'7': {'chores': {'occasions': 2.0}}})]
    records = [done('chores', days_ago=7), done('chores', days_ago=6), done('chores', days_ago=5)]
    verdict = forecast.grade(stored, records, NOW, 7)[0]
    assert verdict.pursuit == 'chores'
    assert verdict.predicted == 2.0
    assert verdict.actual == 3.0
    assert verdict.error == 1.0


def test_grading_averages_across_readings_rather_than_listing_them():
    # Several forecasts taken hours apart predict overlapping windows, so listing
    # them separately would present one measurement as many.
    stored = [
        reading(NOW - timedelta(days=8), {'7': {'chores': {'occasions': 2.0}}}),
        reading(NOW - timedelta(days=9), {'7': {'chores': {'occasions': 4.0}}}),
    ]
    verdicts = forecast.grade(stored, [], NOW, 7)
    assert len(verdicts) == 1
    assert verdicts[0].predicted == 3.0
    assert verdicts[0].readings == 2


def test_the_worst_prediction_is_reported_first():
    stored = [reading(NOW - timedelta(days=8), {'7': {'a': {'occasions': 1.0}, 'b': {'occasions': 9.0}}})]
    assert [v.pursuit for v in forecast.grade(stored, [], NOW, 7)] == ['b', 'a']


def test_run_stores_a_reading_and_show_reads_it_back(register, tmp_path, capsys):
    assert forecast.cmd_run(as_json=False, budget=120, directory=tmp_path) == 0
    capsys.readouterr()
    assert forecast.cmd_show('', as_json=True, directory=tmp_path) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['budget_minutes'] == 120
    assert payload['horizons']['30']


def test_the_budget_flag_overrides_the_register(register, tmp_path, capsys):
    assert forecast.cmd_run(as_json=True, budget=45, directory=tmp_path) == 0
    assert json.loads(capsys.readouterr().out)['budget_minutes'] == 45


def test_show_without_a_stored_reading_says_how_to_take_one(tmp_path, capsys):
    assert forecast.cmd_show('', as_json=False, directory=tmp_path) == 1
    assert 'forecast run' in capsys.readouterr().err


def test_trend_before_anything_has_matured_says_so(register, tmp_path, capsys):
    forecast.append(forecast.reading_path(tmp_path, 'testbox'), reading(datetime.now().astimezone(), {}))
    assert forecast.cmd_trend(7, as_json=False, directory=tmp_path) == 0
    assert 'days old yet' in capsys.readouterr().out


def test_the_forecast_reads_the_budget_from_the_register(tmp_path, monkeypatch):
    path = tmp_path / 'pursuits.yml'
    path.write_text('forecast:\n  budget_minutes: 90\npursuits:\n  a:\n    weight: 5\n')
    assert pursuits.load_settings(path) == {'budget_minutes': 90}


@pytest.mark.parametrize('bad', ['0', '-1', 'true', 'lots'])
def test_a_budget_that_is_not_a_positive_whole_number_is_refused(tmp_path, bad):
    path = tmp_path / 'pursuits.yml'
    path.write_text(f'forecast:\n  budget_minutes: {bad}\npursuits:\n  a:\n    weight: 5\n')
    with pytest.raises(pursuits.RegisterError, match='budget_minutes'):
        pursuits.load_settings(path)


def test_a_register_with_no_forecast_block_has_no_settings(tmp_path):
    path = tmp_path / 'pursuits.yml'
    path.write_text('pursuits:\n  a:\n    weight: 5\n')
    assert pursuits.load_settings(path) == {}


def test_a_simulated_journal_never_touches_the_real_one(register, tmp_path):
    forecast.forecast(pursuits.load_pursuits(), NOW, 120, replicates=3)
    assert journal.read_all(tmp_path / 'state') == []
