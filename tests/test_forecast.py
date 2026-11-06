"""Tests for doit.forecast — durations, the day model, the simulation, and grading.

The draw's own math is tested in test_allocate and test_pursuits. What is tested
here is the layer that runs it forward: which duration wins, how a day is spent,
that a simulated log feeds back into the next day's state, and that a prediction
is only graded once its window has closed.

The simulation deliberately calls the real `build_state`/`compute_draw`, so a test
that a forecast reaches a pinned pursuit is also a test that the two have not come
apart.
"""

import datetime as dt
from pathlib import Path

import pytest

from doit import forecast
from doit import pursuits

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'pursuits' / 'pursuits.yml'
NOW = dt.datetime.fromisoformat('2026-08-04T12:00:00-04:00')


def parsed(text: str) -> dict:
    return pursuits.parse_register(text, 'pursuits.yml')


def registered() -> dict:
    return pursuits.pursuits_from_document(parsed(FIXTURE.read_text()), FIXTURE)


def taken(budget: float, replicates: int) -> forecast.Reading:
    """A forecast of the fixture register on a fresh journal, as the service takes one."""
    register = registered()
    written: list[dict] = []
    live = pursuits.build_state(register, NOW, append=written.extend, machine='testbox')
    return forecast.forecast_from(register, NOW, budget, live, written, [], replicates, machine='testbox')


def done(pursuit: str, minutes: int | None = None, days_ago: float = 0.0) -> dict:
    return {
        'pursuit': pursuit,
        'event': 'done',
        'occurred_at': (NOW - dt.timedelta(days=days_ago)).isoformat(),
        'duration_minutes': minutes,
    }


def reading(generated: dt.datetime, horizons: dict, machine: str = 'testbox') -> forecast.Reading:
    return forecast.Reading(
        generated=generated.isoformat(),
        machine=machine,
        budget_minutes=120,
        replicates=10,
        weights={'chores': 25.0},
        goals={'chores': 'every 7d'},
        durations={'chores': {'minutes': 25.0, 'source': 'default', 'samples': 0}},
        horizons=horizons,
    )


def test_a_weekly_goal_is_never_read_as_the_length_of_a_sitting():
    # Twenty hours a week says how much, not how long one sitting is, so the
    # fallback answers until the journal can.
    register = {'a': {'weight': 5, 'weekly_minutes': 1200}}
    assert forecast.durations(register, [done('a', 90)]).get('a') == forecast.Duration(float(forecast.FALLBACK_MINUTES), 'default', 1)


def test_the_journal_outranks_the_fallback_once_it_has_enough_samples():
    register = {'a': {'weight': 5, 'weekly_minutes': 30}}
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
    register = {'a': {'weight': 5, 'weekly_minutes': 30}}
    records = [done('a', bad), done('a', bad), done('a', bad)]
    assert forecast.durations(register, records)['a'].source == 'default'


def costs(**pairs: float) -> dict[str, forecast.Duration]:
    return {name: forecast.Duration(minutes, 'default', 0) for name, minutes in pairs.items()}


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


def test_the_simulation_feeds_its_own_logs_back_into_the_next_day():
    # The loop that makes this worth simulating: a log pays off that pursuit's
    # balance, which changes what is owed tomorrow and so what heads the list.
    active = pursuits.build_state(registered(), NOW)['active']
    cost = forecast.durations(active, [])
    run = forecast.simulate(registered(), [], {}, cost, NOW, 5, 120, replicate=1)
    assert run
    assert {day for day, _, _ in run} <= set(range(5))
    # Nothing is done twice in one day: the draw samples without replacement.
    for day in range(5):
        names = [name for chosen, name, _ in run if chosen == day]
        assert len(names) == len(set(names))


def test_the_simulation_reaches_the_pursuit_the_real_draw_pins():
    # `chores` has never been done, so the live allocator pins it. It is lighter
    # than the two pursuits ahead of it, so it waits a day for the budget, and a
    # forecast that never offered it would mean the simulation had stopped
    # running the same draw.
    active = pursuits.build_state(registered(), NOW)['active']
    cost = forecast.durations(active, [])
    run = forecast.simulate(registered(), [], {}, cost, NOW, 3, 120, replicate=1)
    assert 'chores' in {name for _, name, _ in run}


def test_a_forecast_predicts_every_active_pursuit_at_every_horizon():
    result = taken(120, replicates=5)
    active = pursuits.build_state(registered(), NOW)['active']
    for days in forecast.HORIZONS:
        assert set(result.horizons[str(days)]) == set(active)
    assert 'paused-thing' not in result.horizons['7']


def test_a_longer_horizon_never_predicts_fewer_occasions():
    result = taken(120, replicates=5)
    for name in result.horizons['7']:
        assert result.horizons['30'][name]['occasions'] >= result.horizons['7'][name]['occasions']


def test_a_bigger_budget_never_predicts_less_work():
    lean = taken(60, replicates=5)
    rich = taken(240, replicates=5)

    def total(result):
        return sum(row['occasions'] for row in result.horizons['30'].values())

    assert total(rich) > total(lean)


def test_a_reading_records_where_each_duration_came_from():
    result = taken(120, replicates=5)
    assert {value['source'] for value in result.durations.values()} <= {'measured', 'default'}


def test_a_reading_carries_what_each_goal_asks_over_each_horizon():
    # Stored beside the prediction, because the register can move after the
    # reading is taken and the comparison has to be against the goal it ran on.
    result = taken(120, replicates=3)

    week = result.horizons['7']
    assert (week['chores']['asked'], week['chores']['unit']) == (1.0, 'checkoffs')
    assert (week['study-computer-science']['asked'], week['study-computer-science']['unit']) == (3.5, 'checkoffs')
    assert (week['read-library']['asked'], week['read-library']['unit']) == (45.0, 'minutes')
    assert result.goals['read-library'] == '45m a week'


def test_a_goal_is_met_in_its_own_unit():
    assert forecast.goal_met({'asked': 4.0, 'occasions': 3.0, 'minutes': 999.0, 'unit': 'checkoffs'}) == 75.0
    assert forecast.goal_met({'asked': 200.0, 'occasions': 9.0, 'minutes': 100.0, 'unit': 'minutes'}) == 50.0


def test_a_reading_taken_before_goals_were_recorded_still_renders(capsys):
    old = reading(NOW, {'30': {'chores': {'occasions': 2.0, 'minutes': 50.0}}, '7': {}})
    old = forecast.Reading(**{**old.__dict__, 'goals': {}})

    assert forecast.goal_met(old.horizons['30']['chores']) is None
    forecast.emit(old)

    row = [line for line in capsys.readouterr().out.splitlines() if 'chores' in line]
    assert row and '—' in row[0]


def test_an_estimate_names_its_source_in_full(capsys):
    """Cut to four letters, `default` printed as `defa`."""
    taken = reading(NOW, {'30': {'chores': {'occasions': 2.0, 'minutes': 50.0}}, '7': {}})
    taken = forecast.Reading(**{**taken.__dict__, 'durations': {'chores': {'minutes': 45.0, 'source': 'default', 'samples': 0}}})

    forecast.emit(taken)

    row = next(line for line in capsys.readouterr().out.splitlines() if 'chores' in line)
    assert row.split()[-2:] == ['45m', 'default']


def test_select_takes_the_newest_when_no_handle_is_given():
    selected = forecast.select([reading(NOW - dt.timedelta(days=2), {}), reading(NOW, {})])
    assert selected is not None and selected.generated == NOW.isoformat()


def test_a_handle_selects_by_timestamp_prefix():
    selected = forecast.select([reading(NOW - dt.timedelta(days=2), {}), reading(NOW, {})], '2026-08-02')
    assert selected is not None and selected.generated.startswith('2026-08-02')


def test_occasions_are_counted_inside_the_window_only():
    records = [done('a', days_ago=1), done('a', days_ago=9), done('b', days_ago=2)]
    counted = forecast.actual_occasions(records, NOW - dt.timedelta(days=7), 7)
    assert counted == {'a': 1, 'b': 1}


def test_a_reading_whose_window_has_not_closed_is_not_graded():
    # Grading early reports every pursuit as under-done, and the error would be
    # the calendar rather than the model.
    fresh = [reading(NOW - dt.timedelta(days=3), {'7': {'chores': {'occasions': 2.0}}})]
    assert forecast.matured(fresh, NOW, 7) == []
    assert forecast.grade(fresh, [], NOW, 7, {'chores'}) == []


def test_a_matured_reading_is_graded_against_what_the_journal_recorded():
    stored = [reading(NOW - dt.timedelta(days=8), {'7': {'chores': {'occasions': 2.0}}})]
    records = [done('chores', days_ago=7), done('chores', days_ago=6), done('chores', days_ago=5)]
    verdict = forecast.grade(stored, records, NOW, 7, {'chores'})[0]
    assert verdict.pursuit == 'chores'
    assert verdict.predicted == 2.0
    assert verdict.actual == 3.0
    assert verdict.error == 1.0


def test_grading_averages_across_readings_rather_than_listing_them():
    # Several forecasts taken hours apart predict overlapping windows, so listing
    # them separately would present one measurement as many.
    stored = [
        reading(NOW - dt.timedelta(days=8), {'7': {'chores': {'occasions': 2.0}}}),
        reading(NOW - dt.timedelta(days=9), {'7': {'chores': {'occasions': 4.0}}}),
    ]
    verdicts = forecast.grade(stored, [], NOW, 7, {'chores'})
    assert len(verdicts) == 1
    assert verdicts[0].predicted == 3.0
    assert verdicts[0].readings == 2


def test_the_worst_prediction_is_reported_first():
    stored = [reading(NOW - dt.timedelta(days=8), {'7': {'a': {'occasions': 1.0}, 'b': {'occasions': 9.0}}})]
    assert [v.pursuit for v in forecast.grade(stored, [], NOW, 7, {'a', 'b'})] == ['b', 'a']


def test_a_retired_pursuit_is_not_graded():
    stored = [reading(NOW - dt.timedelta(days=8), {'7': {'chores': {'occasions': 2.0}, 'retired': {'occasions': 9.0}}})]
    assert [v.pursuit for v in forecast.grade(stored, [], NOW, 7, {'chores'})] == ['chores']


def test_the_forecast_reads_the_budget_from_the_register():
    document = parsed('forecast:\n  budget_minutes: 90\npursuits:\n  a:\n    weight: 5\n')
    assert pursuits.settings_from_document(document, 'pursuits.yml') == {'budget_minutes': 90}


@pytest.mark.parametrize('bad', ['0', '-1', 'true', 'lots'])
def test_a_budget_that_is_not_a_positive_whole_number_is_refused(bad):
    document = parsed(f'forecast:\n  budget_minutes: {bad}\npursuits:\n  a:\n    weight: 5\n')
    with pytest.raises(pursuits.RegisterError, match='budget_minutes'):
        pursuits.settings_from_document(document, 'pursuits.yml')
