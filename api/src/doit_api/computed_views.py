"""Every view the service answers, computed from the store and the apps by doit's own functions.

Each method here is the computing half of a CLI command: it reads the documents,
the journal and the apps, calls the function the command calls, and returns the
document the command's `--json` printed. Nothing here re-derives a balance, a
due date or a lane, so there is one copy of each rule.

Writes go through the same functions. A journal entry is built by
`pursuits.done_record` and its siblings, and the sightings and paces a build
finds are appended to the store through `build_state`'s `append`.
"""

from __future__ import annotations

import dataclasses as dc
import datetime as dt
import logging
from collections.abc import Callable

import click

from doit import dashboard
from doit import evidence
from doit import forecast
from doit import happened
from doit import journal
from doit import lanes
from doit import paths
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
from .readers.declared_reads import source_failed
from .settings import MACHINE
from .store import Store
from .store import stamp

log = logging.getLogger(__name__)

# How far past the service's clock a client's instant may run before it reads as the future.
FUTURE_SKEW = dt.timedelta(minutes=1)

# The zone furthest ahead of UTC. A client's day reads as the future only past its
# date there, because a desk in any zone may already be on it.
FURTHEST_AHEAD_ZONE = dt.timezone(dt.timedelta(hours=14))


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


def completion_day(text: str) -> dt.date:
    """A completion's day, or a refusal naming the field."""
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise BadRequest(f'day={text!r} is not a date (YYYY-MM-DD)') from None


def registered_entry(config: documents.Config, entry: str) -> bool:
    return any(entry in entries for entries in config.review.values())


class Views:
    """The store and the apps, and every view computed from them."""

    def __init__(
        self,
        store: Store,
        readers: Readers,
        clock: Callable[[], dt.datetime] | None = None,
        draw_lifetime: dt.timedelta = dt.timedelta(minutes=15),
    ) -> None:
        self.store = store
        self.readers = readers
        self.clock = clock or (lambda: dt.datetime.now().astimezone())
        self.draw_lifetime = draw_lifetime

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
            raise NotFound('no pursuits yet: `doit pursuits edit` writes them')
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
        return selection if pursuits.draw_stands(selection, now, self.draw_lifetime) else None

    def retire_draw(self, now: dt.datetime) -> None:
        standing = self.standing_draw(now)
        if standing is not None:
            self.store.save_draw({**standing, 'retired': True})

    def next(self, machine: str | None, reroll: bool) -> dict:
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
            self.store.bump_offers(paths.canonical_machine(machine or MACHINE), selection['offered'], now.date())
        else:
            selection = standing
        context, context_error = self.context(now)
        return {
            **selection,
            'state': pursuits.explain_payload(state),
            'context': context,
            'context_error': context_error,
            'pursuits': config.pursuits,
        }

    def register(self) -> dict:
        """The pursuits as declared, which of them are in the draw, and what the standing draw offers, with no app asked."""
        config, now = self.registered(), self.now()
        standing = self.standing_draw(now) or {}
        return {
            'pursuits': config.pursuits,
            'active': sorted(name for name, declared in config.pursuits.items() if pursuits.is_active(declared, now.date())),
            'offered': standing.get('offered', []),
            'draw_id': standing.get('draw_id'),
        }

    def context(self, now: dt.datetime) -> tuple[list[str], str | None]:
        """The lines above the draw, and why they could not be read where they could not, so a failed read never passes for a quiet day."""
        try:
            overview = self.readers.adapter_reads['life'](window.day_of(now), now)[0]
        except ReadFailed as error:
            source_failed('next-context', 'life', error.reason)
            return [], error.reason
        return pursuits.context_lines(overview, now.date()), None

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
        minutes = body.get('duration_minutes')
        if refusal := pursuits.minutes_refusal(name, config.pursuits[name], minutes):
            raise BadRequest(refusal)
        occurred = instant(body.get('occurred_at'), 'occurred_at') or now
        if occurred > now + FUTURE_SKEW:
            raise BadRequest(f'occurred_at={body["occurred_at"]!r} is later than now, and a log records what has happened')
        state = self.state(config, now)
        standing = self.standing_draw(now)
        entry = pursuits.done_record(
            name,
            state,
            standing or {},
            occurred=occurred,
            note=body.get('note'),
            minutes=minutes,
            item=body.get('item'),
            downstream=body.get('downstream'),
            now=now,
            record_id=self.keyed(body),
            **self.author(body, recorded_by),
        )
        self.store.append([entry], recorded_by, now)
        if standing is not None:
            standing['logged'] = sorted({*standing.get('logged', []), name})
            self.store.save_draw(standing)
        return {'entry': entry, 'due_in_days': pursuits.due_with(state, name, entry)}

    def skip(self, body: dict, recorded_by: str) -> dict:
        """Pass on a pursuit for a span, as `doit pursuits skip` does."""
        config, now = self.registered(), self.now()
        name = self.matched(str(body['pursuit']), config.pursuits)
        state = self.state(config, now)
        if refusal := pursuits.outside_draw(state, name):
            raise Conflict(f'{refusal}, so there is nothing to pass')
        try:
            days = pursuits.skip_span(body.get('for'), state['intervals'][name])
        except click.BadParameter as error:
            raise BadRequest(str(error.message)) from error
        expires = now + dt.timedelta(days=days)
        standing = self.standing_draw(now) or {}
        extra = {'expires_at': expires.isoformat(), 'draw_id': standing.get('draw_id')}
        entry = pursuits.new_record(
            journal.Event.SKIP, name, state, extra, now, record_id=self.keyed(body), **self.author(body, recorded_by)
        )
        self.store.append([entry], recorded_by, now)
        self.retire_draw(now)
        return {'entry': entry, 'days': days, 'until': expires.isoformat()}

    def resume(self, body: dict, recorded_by: str) -> dict:
        """End a standing skip now, for one pursuit or every skipped one."""
        config, now = self.registered(), self.now()
        state = self.state(config, now)
        name = self.matched(str(body['pursuit']), config.pursuits) if body.get('pursuit') else None
        if name and (refusal := pursuits.outside_draw(state, name)):
            raise Conflict(f'{refusal}, so there is nothing to return it to')
        chosen = pursuits.resumed(state, name)
        who = self.author(body, recorded_by)
        entries = [
            pursuits.new_record(journal.Event.SKIP, pursuit, state, {'expires_at': now.isoformat()}, now, **who) for pursuit in chosen
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
        entries = pursuits.reset_records(chosen, self.state(config, now), now, **self.author(body, recorded_by))
        self.store.append(entries, recorded_by, now)
        self.retire_draw(now)
        return {'reset': chosen, 'entries': entries}

    @staticmethod
    def author(body: dict, recorded_by: str) -> dict:
        """The machine and actor a write is recorded as: the client's, its machine spelled as a desk spells its own."""
        return {'machine': paths.canonical_machine(str(body.get('machine') or MACHINE)), 'identity': str(body.get('actor') or recorded_by)}

    @staticmethod
    def keyed(body: dict) -> str:
        """The id the client made for this write, so a write sent again is stored once."""
        return str(body['id']) if body.get('id') else ''

    def drift(self, since: str | None, until: str | None) -> dict:
        """`doit pursuits drift --json`: what each goal asked over a window, and what got done."""
        config, now = self.registered(), self.now()
        try:
            start, end = window.window(since or (None if until else pursuits.DRIFT_SINCE), until, now)
        except ValueError as error:
            raise BadRequest(str(error)) from error
        state = self.state(config, now)
        rows = pursuits.drift_rows(config.pursuits, state, start, end, self.store.offers_by_day())
        return {'since': start and start.isoformat(), 'until': (end or now).isoformat(), 'rows': rows}

    def dormant(self) -> dict:
        """`doit pursuits dormant --json`, which `schemas/pursuits-dormant.schema.json` describes."""
        config, now = self.registered(), self.now()
        return {'schema_version': 1, 'dormant': pursuits.dormant_rows(self.state(config, now))}

    def evidence(self) -> dict:
        """`doit pursuits evidence --json`: what each app says was last done, asking every one now."""
        config, now = self.registered(), self.now()
        watching = pursuits.watched(config.pursuits, now.date())
        observations = self.observations(config.pursuits, now, force=True)
        return {
            'watched': sorted(watching, key=lambda name: -watching[name].get('weight', 0)),
            'observed': {name: when.isoformat() for name, when in evidence.observed(observations).items()},
            'errors': evidence.problems(observations),
            'undeclared': sorted(set(watching) - set(evidence.declared(watching))),
        }

    def offer_counts(self) -> dict:
        """Offers by the machine that drew them: ever, over drift's default window, and that window's counts no pursuit owns."""
        config, now = self.config(), self.now()
        recent = self.store.offers_since(window.bound(pursuits.DRIFT_SINCE, now, end=False).date())
        stranded: set[str] = set()
        for by_machine in recent.values():
            stranded |= {name for name in by_machine if name not in config.pursuits}
        return {'counts': self.store.offer_counts(), 'recent': recent, 'stranded': sorted(stranded)}

    # --- the review register --------------------------------------------------

    def review(self, block: str | None) -> dict:
        """`doit review list --json`: every entry with its derived overdue days, and completions no entry owns."""
        if block is not None and block not in review.BLOCKS:
            raise BadRequest(f'no block named {block}; the blocks are {", ".join(review.BLOCKS)}')
        config, now = self.config(), self.now()
        stamps = self.store.review_stamps()
        owned: set[str] = set()
        for entries in config.review.values():
            owned |= set(entries)
        return {
            'entries': review.statuses_of(config.review, stamps, now.date(), block),
            'stranded': sorted(entry for entry in stamps if entry not in owned),
        }

    def complete_review(self, entry: str, day: str | None, recorded_by: str) -> dict:
        config, now = self.config(), self.now()
        if not registered_entry(config, entry):
            raise NotFound(f'no review entry named {entry}')
        done_on = day or now.date().isoformat()
        if completion_day(done_on) > now.astimezone(FURTHEST_AHEAD_ZONE).date():
            raise BadRequest(f'day={done_on!r} is later than today anywhere, and a completion records what has happened')
        with self.store.write() as connection:
            added = self.store.add_completion(connection, entry, done_on, recorded_by, stamp(now))
        return {'entry': entry, 'done_on': done_on, 'added': bool(added), 'status': self.review_status(config, entry, now)}

    def review_completions(self, entry: str) -> list[dict]:
        """Every completion an entry holds, oldest day first. An entry the register no longer names keeps its own."""
        held = self.store.review_completions(entry)
        if not held and not registered_entry(self.config(), entry):
            raise NotFound(f'no review entry named {entry}')
        return held

    def take_back_completion(self, entry: str, day: str, removed_by: str) -> dict:
        """Remove one day's completion, and say how the entry reads without it."""
        config, now = self.config(), self.now()
        completion_day(day)
        with self.store.write() as connection:
            removed = self.store.remove_completion(connection, entry, day)
        if removed:
            log.info('review_completion_taken_back', extra={'entry': entry, 'done_on': day, 'removed_by': removed_by})
        return {'entry': entry, 'done_on': day, 'removed': bool(removed), 'status': self.review_status(config, entry, now)}

    def review_status(self, config: documents.Config, entry: str, now: dt.datetime) -> dict | None:
        rows = review.statuses_of(config.review, self.store.review_stamps(), now.date(), None)
        return next((row for row in rows if row['id'] == entry), None)

    def nudge(self) -> dict:
        """Whether the shell nudge shows now, the interval it waits between showings, and the entries it would show."""
        held = self.store.nudge()
        due = [row for row in self.review('items')['entries'] if review.is_due(row['overdue'])]
        return {
            'interval_minutes': held.get('interval_minutes') or review.DEFAULT_NUDGE_MINUTES,
            'last_nudged_at': held.get('last_nudged_at'),
            'show': review.nudge_due(held, self.now()),
            'due': due[: review.NUDGE_MAX_ITEMS],
            'due_total': len(due),
        }

    def set_nudge(self, interval_minutes: int | None, nudged_at: str | None) -> dict:
        if interval_minutes is not None and interval_minutes <= 0:
            raise BadRequest('interval_minutes must be a positive whole number')
        stamped = instant(nudged_at, 'last_nudged_at')
        with self.store.write() as connection:
            self.store.set_nudge(connection, interval_minutes=interval_minutes, last_nudged_at=stamp(stamped) if stamped else None)
        return self.store.nudge()

    # --- the lanes ------------------------------------------------------------

    def maintenance_lane(self, config: documents.Config, now: dt.datetime) -> lanes.Lane:
        rows = review.statuses_of(config.review, self.store.review_stamps(), now.date())
        return dashboard.build_maintenance_lane({'review': sources.Result(source='local', payload=rows, exit_code=0)}, now.date())

    def kept(self, config: documents.Config, now: dt.datetime) -> happened.Kept:
        """What doit keeps itself: the pursuit state and both review registers."""
        found = happened.Kept()
        if config.pursuits:
            found.state = self.state(config, now)
        stamps = self.store.review_stamps()
        for entry in happened.registers():
            found.registers[entry.name] = review.statuses_of(config.review, stamps, now.date(), entry.block)
        return found

    def dashboard(self, wanted: list[str] | None) -> dict:
        """`doit dashboard --json`: every lane, from one round of every configured source."""
        config, now = self.config(), self.now()
        results = self.readers.results(list(config.registry.sources.values()), window.day_of(now), now)
        collected = dashboard.arranged([*dashboard.source_lanes(config.registry, results), self.maintenance_lane(config, now)], wanted)
        return {
            **lanes.to_document(collected, now),
            'problems': config.registry.problems,
            'configured': len(config.registry.sources),
            'pursuit_standing': self.pursuit_standing(config, now),
        }

    def pursuit_standing(self, config: documents.Config, now: dt.datetime) -> dict | None:
        """The dashboard's footer: which pursuits are behind, or why that could not be read. None when none owes anything."""
        if not config.pursuits:
            return None
        try:
            line = pursuits.standing_line(self.state(config, now))
        except (OSError, ValueError) as error:
            return {'text': f'pursuits unavailable — {error}', 'unavailable': True}
        return {'text': line, 'unavailable': False} if line else None

    def today(self) -> dict:
        """`doit today --json`: what today still wants, and what it has had."""
        config, now = self.config(), self.now()
        bounds = window.day_of(now)
        registry = today.day_registry(config.registry)
        everything = [*registry.sources.values(), *registry.completions.values()]
        results = self.readers.results(everything, bounds, now)
        built = dashboard.arranged([*dashboard.source_lanes(registry, results), self.maintenance_lane(config, now)], None)
        day = today.assemble(registry, results, built, self.kept(config, now), bounds, now)
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
            'answers': [
                *(sources.answer('outstanding', source, results[source.id]) for source in config.registry.sources.values()),
                *(sources.answer('happened', source, results[source.id]) for source in config.registry.completions.values()),
            ],
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
        return [forecast.reading_from(held) for held in self.store.readings()]

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
        verdicts = forecast.grade(stored, self.store.journal(), self.now(), days, self.config().pursuits)
        return {
            'days': days,
            'readings': len(forecast.matured(stored, self.now(), days)),
            'verdicts': [dc.asdict(verdict) for verdict in verdicts],
        }
