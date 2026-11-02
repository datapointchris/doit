"""Every view the service answers, computed from the store and the apps by doit's own functions.

Each method here is the computing half of a CLI command: it reads the documents,
the journal and the apps, calls the function the command calls, and returns the
document the command's `--json` printed. Nothing here re-derives a balance, a
due date or a lane. That is what keeps the service's answer and a desk's answer
from drifting: there is one copy of each rule, and both run it.

Writes go through the same functions. A journal entry is built by
`pursuits.new_record`, and the sightings and paces a build finds are appended
to the store through `build_state`'s `append`, as a desk appended them to its
file.
"""

from __future__ import annotations

import dataclasses as dc
import datetime as dt
import math
from collections.abc import Callable

import click

from doit import dashboard
from doit import evidence
from doit import forecast
from doit import happened
from doit import journal
from doit import lanes
from doit import pursuits
from doit import quota
from doit import review
from doit import sources
from doit import today
from doit import window
from doit.window import Bounds

from . import documents
from .errors import BadRequest
from .errors import Conflict
from .errors import NotFound
from .readers.apps import ReadFailed
from .readers.declared_reads import Readers
from .settings import MACHINE
from .store import Store
from .store import stamp

# How long a draw stands before the next `next` draws again, as on a desk.
DRAW_MINUTES = pursuits.CACHE_MINUTES

# How far ahead a countdown is named above the draw.
CONTEXT_COUNTDOWN_DAYS = 14


def instant(text: str | None, name: str) -> dt.datetime | None:
    """An ISO 8601 instant with its offset, or a refusal naming the field."""
    if text is None:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        raise BadRequest(f'{name}={text!r} is not an ISO 8601 instant') from None
    if parsed.tzinfo is None:
        raise BadRequest(f'{name}={text!r} carries no offset, so it names no instant')
    return parsed.astimezone()


class Views:
    """The store and the apps, and every view computed from them."""

    def __init__(self, store: Store, readers: Readers, clock: Callable[[], dt.datetime] | None = None) -> None:
        self.store = store
        self.readers = readers
        self.clock = clock or (lambda: dt.datetime.now().astimezone())

    def now(self) -> dt.datetime:
        return self.clock()

    def config(self) -> documents.Config:
        return documents.config(self.store)

    # --- pursuits -------------------------------------------------------------

    def observations(self, register: dict, now: dt.datetime, *, force: bool = False) -> dict:
        """Each watched pursuit's answer from its app, asked again only where the kept one aged out."""
        watching = pursuits.watched(register, now.date())
        payload = self.store.evidence_cache()
        if evidence.refreshed(payload, register, now, self.readers.evidence, force=force, ask=watching):
            self.store.save_evidence_cache(payload, now)
        return evidence.answered(payload, watching)

    def state(self, config: documents.Config, now: dt.datetime, *, force: bool = False) -> dict:
        """The pursuit state, with any sighting or pace it finds appended to the journal."""

        def append(found: list[dict]) -> None:
            self.store.append(found, MACHINE, now)

        return pursuits.build_state(
            config.pursuits,
            now,
            self.store.journal(),
            cycles=config.cycles,
            append=append,
            machine=MACHINE,
            observations=self.observations(config.pursuits, now, force=force),
        )

    def registered(self) -> documents.Config:
        found = self.config()
        if not found.pursuits:
            raise NotFound('no pursuits yet; write the pursuits document first')
        return found

    def pursuit_list(self) -> dict:
        """`doit pursuits list --json`: every pursuit and the state behind it."""
        config, now = self.registered(), self.now()
        state = self.state(config, now)
        return {'pursuits': config.pursuits, 'state': pursuits.explain_payload(state)}

    def matched(self, needle: str, register: dict) -> str:
        found = pursuits.match_pursuit(needle, register)
        if not found:
            raise NotFound(f'no pursuit named {needle}')
        return found

    def standing_draw(self, now: dt.datetime) -> dict | None:
        """The draw from the last few minutes, or None once it has aged out or nothing is left of it."""
        selection = self.store.newest_draw()
        if selection is None:
            return None
        created = journal.parse_time(selection.get('created_at'))
        if created is None or now - created > dt.timedelta(minutes=DRAW_MINUTES) or selection.get('retired'):
            return None
        return selection

    def retire_draw(self, now: dt.datetime) -> None:
        standing = self.standing_draw(now)
        if standing is not None:
            self.store.save_draw({**standing, 'retired': True})

    def next(self, machine: str, reroll: bool) -> dict:
        """`doit next --json`: what to do now, the state behind the draw, and today's events above it."""
        config, now = self.registered(), self.now()
        state = self.state(config, now)
        if not state['active']:
            raise Conflict('nothing to draw: every pursuit is paused, past its term, or tracked')
        standing = None if reroll else self.standing_draw(now)
        if standing is not None:
            resolved = standing.get('resolved') or {}
            failed = [name for name, entry in resolved.items() if isinstance(entry, dict) and entry.get('error')]
            if failed:
                resolved = {name: entry for name, entry in resolved.items() if name not in failed}
                resolved.update(self.readers.resolve_all(failed, config.pursuits))
                standing['resolved'] = resolved
                self.store.save_draw(standing)
            shown = pursuits.without_logged(standing)
            standing = shown if shown['offered'] else None
        if standing is None:
            selection = pursuits.compute_draw(state)
            selection['resolved'] = self.readers.resolve_all(selection['offered'], config.pursuits)
            self.store.save_draw(selection)
            self.store.bump_offers(machine, selection['offered'])
        else:
            selection = standing
        return {**selection, 'state': pursuits.explain_payload(state), 'context': self.context(now)}

    def context(self, now: dt.datetime) -> list[str]:
        """Today's events and the countdowns due within two weeks, which sit above the draw and take no part in it."""
        try:
            overview = self.readers.adapter_reads['icb'](window.day_of(now), now)[0]
        except ReadFailed:
            return []
        lines = []
        today_date = now.date()
        for event in (overview.get('events') or {}).get('items') or [] if isinstance(overview, dict) else []:
            when = journal.parse_time(event.get('date'))
            if when and when.astimezone().date() == today_date:
                lines.append(f'{event.get("name", "")} · {event.get("venue") or "today"}')
        for countdown in (overview.get('countdowns') or {}).get('items') or [] if isinstance(overview, dict) else []:
            due = countdown.get('due_date')
            if due:
                days = (dt.date.fromisoformat(str(due)[:10]) - today_date).days
                if 0 <= days <= CONTEXT_COUNTDOWN_DAYS:
                    lines.append(f'{countdown.get("name", "")} in {days}d')
        return lines

    def item(self, needle: str) -> dict:
        """What a pursuit means right now: the standing draw's entry for it, or a fresh resolve."""
        config, now = self.registered(), self.now()
        name = self.matched(needle, config.pursuits)
        standing = self.standing_draw(now) or {}
        entry = (standing.get('resolved') or {}).get(name) or {}
        if entry.get('error') or entry.get('candidates') == 0:
            entry = {}
        if not entry:
            entry = self.readers.resolve(name, config.pursuits[name]) or {}
        return {'pursuit': name, 'resolved': entry, 'draw_id': standing.get('draw_id')}

    def log(self, body: dict, recorded_by: str) -> dict:
        """Record having done a pursuit, as `doit pursuits log` records it, and say when it next comes due."""
        config, now = self.registered(), self.now()
        name = self.matched(str(body['pursuit']), config.pursuits)
        settings = config.pursuits[name]
        minutes = body.get('duration_minutes')
        timed = bool(settings.get('weekly_minutes'))
        if minutes is not None and not timed and pursuits.paced(settings):
            raise BadRequest(f'{name} is counted in completions, so duration_minutes has nothing to measure')
        if timed and minutes is None:
            raise BadRequest(f'{name} is measured in minutes: send duration_minutes')
        occurred = instant(body.get('occurred_at'), 'occurred_at') or now
        state = self.state(config, now)
        standing = self.standing_draw(now)
        offered = (standing or {}).get('offered', [])
        entry = pursuits.new_record(
            journal.Event.DONE,
            name,
            state,
            {
                'occurred_at': occurred.isoformat(),
                'note': body.get('note') or None,
                'duration_minutes': minutes,
                'item': body.get('item') or None,
                'downstream': body.get('downstream'),
                'draw_id': (standing or {}).get('draw_id'),
                'was_offered': name in offered,
                'rank_offered': offered.index(name) + 1 if name in offered else None,
            },
            now,
            machine=str(body.get('machine') or MACHINE),
            identity=str(body.get('actor') or recorded_by),
        )
        self.store.append([entry], recorded_by, now)
        if standing is not None:
            standing['logged'] = sorted({*standing.get('logged', []), name})
            self.store.save_draw(standing)
        return {'entry': entry, 'due_in_days': self.due_after(state, name, entry)}

    @staticmethod
    def due_after(state: dict, name: str, entry: dict) -> float | None:
        """When the pursuit next comes due with this entry counted, from the model rather than by subtraction."""
        if name not in state['balance']:
            return None
        own = [*pursuits.records_by_pursuit(state['records']).get(name, []), entry]
        due = pursuits.standing(
            own,
            state['evidence_days'].get(name, []),
            state['now'],
            state['origins'][name],
            (state['intervals'][name], state['checkoff_size'][name]),
            state['weekly_minutes'].get(name),
        ).due
        return None if due is None else round(due, 2)

    def skip(self, body: dict, recorded_by: str) -> dict:
        """Pass on a pursuit for a span, as `doit pursuits skip` does."""
        config, now = self.registered(), self.now()
        name = self.matched(str(body['pursuit']), config.pursuits)
        state = self.state(config, now)
        if name not in state['active']:
            raise Conflict(f'{name} is not in the draw ({pursuits.why_unpriced(state, name)}), so there is nothing to pass')
        try:
            days = pursuits.skip_span(body.get('for'), state['intervals'][name])
        except click.BadParameter as error:
            raise BadRequest(str(error.message)) from error
        expires = now + dt.timedelta(days=days)
        standing = self.standing_draw(now) or {}
        entry = self.written(
            journal.Event.SKIP, name, state, {'expires_at': expires.isoformat(), 'draw_id': standing.get('draw_id')}, body, recorded_by, now
        )
        self.store.append([entry], recorded_by, now)
        self.retire_draw(now)
        return {'entry': entry, 'days': days, 'until': expires.isoformat()}

    def resume(self, body: dict, recorded_by: str) -> dict:
        """End a standing skip now, for one pursuit or every skipped one."""
        config, now = self.registered(), self.now()
        state = self.state(config, now)
        if body.get('pursuit'):
            name = self.matched(str(body['pursuit']), config.pursuits)
            if name not in state['active']:
                raise Conflict(f'{name} is not in the draw ({pursuits.why_unpriced(state, name)}), so there is nothing to return it to')
            chosen = [name] if name in state['suppressed'] else []
        else:
            chosen = list(state['suppressed'])
        entries = [
            self.written(journal.Event.SKIP, name, state, {'expires_at': now.isoformat()}, body, recorded_by, now) for name in chosen
        ]
        self.store.append(entries, recorded_by, now)
        if entries:
            self.retire_draw(now)
        return {'resumed': chosen, 'entries': entries}

    def reset(self, body: dict, recorded_by: str) -> dict:
        """Start balances again from now: the pursuits named, or every one when none is."""
        config, now = self.registered(), self.now()
        named = body.get('pursuits')
        chosen = sorted(config.pursuits) if not named else [self.matched(str(name), config.pursuits) for name in named]
        state = self.state(config, now)
        entries = [
            self.written(journal.Event.RESET, name, state if index == 0 else None, {}, body, recorded_by, now)
            for index, name in enumerate(chosen)
        ]
        self.store.append(entries, recorded_by, now)
        self.retire_draw(now)
        return {'reset': chosen, 'entries': entries}

    @staticmethod
    def written(event: str, name: str, state: dict | None, extra: dict, body: dict, recorded_by: str, now: dt.datetime) -> dict:
        return pursuits.new_record(
            event, name, state, extra, now, machine=str(body.get('machine') or MACHINE), identity=str(body.get('actor') or recorded_by)
        )

    def drift(self, since: str | None, until: str | None) -> dict:
        """`doit pursuits drift --json`: what each goal asked over a window, and what got done."""
        config, now = self.registered(), self.now()
        try:
            start, end = window.window(since or (None if until else pursuits.DRIFT_SINCE), until, now)
        except ValueError as error:
            raise BadRequest(str(error)) from error
        state = self.state(config, now)
        rows = pursuits.drift_rows(config.pursuits, state, start, end)
        return {'since': start and start.isoformat(), 'until': (end or now).isoformat(), 'rows': rows}

    def dormant(self) -> dict:
        """Pursuits gone quiet for more than three of their own intervals."""
        config, now = self.registered(), self.now()
        state = self.state(config, now)
        rows = []
        for name in state['active']:
            elapsed, interval = state['days_since'][name], state['intervals'][name]
            if not math.isinf(interval) and (elapsed is None or elapsed > interval * 3):
                rows.append({'pursuit': name, 'days_since': elapsed, 'interval_days': round(interval, 2)})
        return {'dormant': sorted(rows, key=lambda row: -(row['days_since'] or 1e9))}

    def evidence(self) -> dict:
        """`doit pursuits evidence --json`: what each app says was last done, asking every one now."""
        config, now = self.registered(), self.now()
        watching = pursuits.watched(config.pursuits, now.date())
        observations = self.observations(config.pursuits, now, force=True)
        return {
            'observed': {name: when.isoformat() for name, when in evidence.observed(observations).items()},
            'errors': evidence.problems(observations),
            'undeclared': sorted(set(watching) - set(evidence.declared(watching))),
        }

    def offer_counts(self) -> dict:
        """How often each pursuit was offered, by the machine that drew it, and the counts no pursuit owns."""
        config = self.config()
        counts = self.store.offer_counts()
        stranded = sorted({name for by_machine in counts.values() for name in by_machine if name not in config.pursuits})
        return {'counts': counts, 'stranded': stranded}

    # --- the review register --------------------------------------------------

    def review(self, block: str | None) -> dict:
        """`doit review list --json`: every entry with its derived overdue days, and completions no entry owns."""
        if block is not None and block not in review.BLOCKS:
            raise BadRequest(f'no block named {block}; the blocks are {", ".join(review.BLOCKS)}')
        config, now = self.config(), self.now()
        stamps = self.store.review_stamps()
        owned = {entry for entries in config.register.values() for entry in entries}
        return {
            'entries': review.statuses_of(config.register, stamps, now.date(), block),
            'stranded': sorted(entry for entry in stamps if entry not in owned),
        }

    def complete_review(self, entry: str, day: str | None, recorded_by: str) -> dict:
        config, now = self.config(), self.now()
        if not any(entry in entries for entries in config.register.values()):
            raise NotFound(f'no review entry named {entry}')
        done_on = day or now.date().isoformat()
        try:
            dt.date.fromisoformat(done_on)
        except ValueError:
            raise BadRequest(f'day={done_on!r} is not a date (YYYY-MM-DD)') from None
        with self.store.write() as connection:
            added = self.store.add_completion(connection, entry, done_on, recorded_by, stamp(now))
        return {'entry': entry, 'done_on': done_on, 'added': bool(added)}

    def nudge(self) -> dict:
        """The interval the shell nudge waits between showings, when it last showed, and the entries it would show now."""
        held = self.store.nudge()
        due = [row for row in self.review('items')['entries'] if review.is_due(row['overdue'])]
        return {**held, 'due': due[: review.NUDGE_MAX_ITEMS], 'due_total': len(due)}

    def set_nudge(self, interval_minutes: int | None, nudged_at: str | None) -> dict:
        if interval_minutes is not None and interval_minutes <= 0:
            raise BadRequest('interval_minutes must be a positive whole number')
        stamped = instant(nudged_at, 'last_nudged_at')
        with self.store.write() as connection:
            self.store.set_nudge(connection, interval_minutes=interval_minutes, last_nudged_at=stamp(stamped) if stamped else None)
        return self.store.nudge()

    # --- the lanes ------------------------------------------------------------

    def maintenance_lane(self, config: documents.Config, now: dt.datetime) -> lanes.Lane:
        rows = review.statuses_of(config.register, self.store.review_stamps(), now.date())
        return dashboard.build_maintenance_lane({'review': sources.Result(source='local', payload=rows, exit_code=0)}, now.date())

    def kept(self, config: documents.Config, now: dt.datetime) -> happened.Kept:
        """What doit keeps itself, as `happened.read_kept` reads it from a desk's files."""
        found = happened.Kept()
        if config.pursuits:
            found.state = self.state(config, now)
        stamps = self.store.review_stamps()
        for entry in happened.registers():
            found.registers[entry.name] = review.statuses_of(config.register, stamps, now.date(), entry.block)
        return found

    def dashboard(self, wanted: list[str] | None) -> dict:
        """`doit dashboard --json`: every lane, from one round of every configured source."""
        config, now = self.config(), self.now()
        results = self.readers.results(list(config.registry.sources.values()), window.day_of(now), now)
        collected = dashboard.arranged([*dashboard.source_lanes(config.registry, results), self.maintenance_lane(config, now)], wanted)
        return {**lanes.to_document(collected, now), 'problems': config.registry.problems}

    def today(self) -> dict:
        """`doit today --json`: what today still wants, and what it has had."""
        config, now = self.config(), self.now()
        bounds = window.day_of(now)
        everything = [*config.registry.sources.values(), *config.registry.completions.values()]
        results = self.readers.results(everything, bounds, now)
        built = dashboard.arranged([*dashboard.source_lanes(config.registry, results), self.maintenance_lane(config, now)], None)
        day = today.assemble(config.registry, results, built, self.kept(config, now), bounds, now)
        return {
            'schema_version': lanes.SCHEMA_VERSION,
            'generated_at': lanes.timestamp(now),
            'done': [lanes.lane_to_dict(lane) for lane in day.done],
            'due': [lanes.lane_to_dict(lane) for lane in day.due],
            'quota': None if day.figures is None else quota.to_dict(day.figures),
            'problems': config.registry.problems,
        }

    def happened(self, since: str | None, until: str | None) -> dict:
        """`doit happened --json`: what every record says happened over a window."""
        config, now = self.config(), self.now()
        if since is None and until is None:
            since = happened.DEFAULT_SINCE
        try:
            start, stop = window.window(since, until, now)
        except ValueError as error:
            raise BadRequest(str(error)) from error
        bounds = Bounds(start or happened.EARLIEST, stop or now)
        if bounds.start >= bounds.stop:
            raise BadRequest(f'since={since} is not before {until or "now"}')
        results = self.readers.results(list(config.registry.completions.values()), bounds, now)
        groups = happened.fold(config.registry, results, self.kept(config, now), bounds, now)
        return {
            'schema_version': lanes.SCHEMA_VERSION,
            'generated_at': lanes.timestamp(now),
            'since': bounds.start.isoformat(timespec='seconds'),
            'until': bounds.stop.isoformat(timespec='seconds'),
            'asked': [lane.name for lane in groups],
            'unavailable': [lane.name for lane in groups if not lane.available],
            'partial': [lane.name for lane in groups if lane.available and lane.reason],
            'groups': [lanes.lane_to_dict(lane) for lane in groups],
            'problems': config.registry.problems,
        }

    def sources(self) -> dict:
        """`doit sources list --json`: every configured source, and whether it answers."""
        config, now = self.config(), self.now()
        bounds = window.day_of(now)
        everything = [*config.registry.sources.values(), *config.registry.completions.values()]
        results = self.readers.results(everything, bounds, now)
        return {
            'sources': [sources.answer('sources', source, results[source.id]) for source in config.registry.sources.values()],
            'completions': [sources.answer('completions', source, results[source.id]) for source in config.registry.completions.values()],
            'problems': config.registry.problems,
        }

    # --- forecasts ------------------------------------------------------------

    def take_forecast(self, budget: int | None = None, replicates: int = forecast.REPLICATES) -> dict:
        """Take a forecast from the register as it stands, and store it."""
        config, now = self.registered(), self.now()
        minutes = budget or config.forecast.get('budget_minutes') or forecast.DEFAULT_BUDGET_MINUTES
        live = self.state(config, now)
        reading = forecast.forecast_from(
            config.pursuits, now, float(minutes), live, self.store.journal(), config.cycles, replicates, machine=MACHINE
        )
        stored = dc.asdict(reading)
        with self.store.write() as connection:
            self.store.add_reading(connection, stored, stamp(now))
        return stored

    def readings(self) -> list[forecast.Reading]:
        names = {entry.name for entry in dc.fields(forecast.Reading)}
        return [forecast.Reading(**{key: value for key, value in held.items() if key in names}) for held in self.store.readings()]

    def forecast_list(self) -> list[dict]:
        return [dc.asdict(reading) for reading in self.readings()]

    def forecast_show(self, handle: str) -> dict:
        reading = forecast.select(self.readings(), handle)
        if reading is None:
            raise NotFound('no forecast stored yet' if not handle else f'no forecast taken at {handle}')
        return dc.asdict(reading)

    def forecast_trend(self, days: int) -> dict:
        if days not in forecast.HORIZONS:
            raise BadRequest(f'days must be one of {", ".join(str(horizon) for horizon in forecast.HORIZONS)}')
        stored = self.readings()
        verdicts = forecast.grade(stored, self.store.journal(), self.now(), days)
        return {
            'days': days,
            'readings': len(forecast.matured(stored, self.now(), days)),
            'verdicts': [dc.asdict(verdict) for verdict in verdicts],
        }
