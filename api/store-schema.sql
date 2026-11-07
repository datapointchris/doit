CREATE TABLE documents (
        name TEXT PRIMARY KEY CHECK (name IN ('pursuits', 'review', 'sources')),
        text TEXT NOT NULL,
        edited_by TEXT NOT NULL,
     valid_from TEXT NOT NULL);
CREATE TABLE documents_history (
        name TEXT NOT NULL CHECK (name IN ('pursuits', 'review', 'sources')),
        text TEXT NOT NULL,
        edited_by TEXT NOT NULL,
     valid_from TEXT NOT NULL, valid_to TEXT NOT NULL, CHECK (valid_from <= valid_to), UNIQUE (name, valid_from, valid_to));
CREATE TABLE draws (
    draw_id TEXT PRIMARY KEY,
    created_utc TEXT NOT NULL,
    selection TEXT NOT NULL CHECK (json_valid(selection))
);
CREATE TABLE evidence_cache (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    payload TEXT NOT NULL CHECK (json_valid(payload)),
    updated_at TEXT NOT NULL
);
CREATE TABLE forecast_readings (
    machine TEXT NOT NULL,
    generated TEXT NOT NULL,
    generated_utc TEXT NOT NULL,
    reading TEXT NOT NULL CHECK (json_valid(reading)),
    stored_at TEXT NOT NULL,
    PRIMARY KEY (machine, generated)
);
CREATE TABLE forecast_settings (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        budget_minutes INTEGER CHECK (budget_minutes IS NULL OR budget_minutes > 0),
     valid_from TEXT NOT NULL);
CREATE TABLE forecast_settings_history (
        singleton INTEGER NOT NULL CHECK (singleton = 1),
        budget_minutes INTEGER CHECK (budget_minutes IS NULL OR budget_minutes > 0),
     valid_from TEXT NOT NULL, valid_to TEXT NOT NULL, CHECK (valid_from <= valid_to), UNIQUE (singleton, valid_from, valid_to));
CREATE TABLE imports (
    id INTEGER PRIMARY KEY,
    imported_at TEXT NOT NULL,
    imported_by TEXT NOT NULL,
    summary TEXT NOT NULL CHECK (json_valid(summary))
);
CREATE TABLE journal_events (
    id TEXT PRIMARY KEY,
    pursuit TEXT NOT NULL,
    event TEXT NOT NULL CHECK (event IN ('done', 'skip', 'reset', 'pace')),
    occurred_at TEXT NOT NULL,
    machine TEXT NOT NULL,
    actor TEXT,
    record TEXT NOT NULL CHECK (json_valid(record)),
    recorded_by TEXT NOT NULL,
    stored_at TEXT NOT NULL
);
CREATE TABLE nudge (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    interval_minutes INTEGER CHECK (interval_minutes IS NULL OR interval_minutes > 0),
    last_nudged_at TEXT
);
CREATE TABLE offer_counts (
    machine TEXT NOT NULL,
    pursuit TEXT NOT NULL,
    offered INTEGER NOT NULL CHECK (offered >= 0),
    PRIMARY KEY (machine, pursuit)
);
CREATE TABLE offer_counts_by_day (
    day TEXT NOT NULL CHECK (day GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    machine TEXT NOT NULL,
    pursuit TEXT NOT NULL,
    offered INTEGER NOT NULL CHECK (offered > 0),
    PRIMARY KEY (day, machine, pursuit)
);
CREATE TABLE pursuit_cycles (name TEXT PRIMARY KEY,
    settings TEXT NOT NULL CHECK (json_valid(settings)),
    until TEXT GENERATED ALWAYS AS (json_extract(settings, '$.until')) VIRTUAL,
 valid_from TEXT NOT NULL);
CREATE TABLE pursuit_cycles_history (name TEXT NOT NULL,
    settings TEXT NOT NULL CHECK (json_valid(settings)),
    until TEXT GENERATED ALWAYS AS (json_extract(settings, '$.until')) VIRTUAL,
 valid_from TEXT NOT NULL, valid_to TEXT NOT NULL, CHECK (valid_from <= valid_to), UNIQUE (name, valid_from, valid_to));
CREATE TABLE pursuits (name TEXT PRIMARY KEY,
    settings TEXT NOT NULL CHECK (json_valid(settings)),
    description TEXT GENERATED ALWAYS AS (json_extract(settings, '$.description')) VIRTUAL,
    weight NUMERIC GENERATED ALWAYS AS (json_extract(settings, '$.weight')) VIRTUAL,
    cadence TEXT GENERATED ALWAYS AS (json_extract(settings, '$.cadence')) VIRTUAL,
    weekly_minutes INTEGER GENERATED ALWAYS AS (json_extract(settings, '$.weekly_minutes')) VIRTUAL,
    until TEXT GENERATED ALWAYS AS (json_extract(settings, '$.until')) VIRTUAL,
    paused INTEGER GENERATED ALWAYS AS (json_extract(settings, '$.paused')) VIRTUAL,
 valid_from TEXT NOT NULL);
CREATE TABLE pursuits_history (name TEXT NOT NULL,
    settings TEXT NOT NULL CHECK (json_valid(settings)),
    description TEXT GENERATED ALWAYS AS (json_extract(settings, '$.description')) VIRTUAL,
    weight NUMERIC GENERATED ALWAYS AS (json_extract(settings, '$.weight')) VIRTUAL,
    cadence TEXT GENERATED ALWAYS AS (json_extract(settings, '$.cadence')) VIRTUAL,
    weekly_minutes INTEGER GENERATED ALWAYS AS (json_extract(settings, '$.weekly_minutes')) VIRTUAL,
    until TEXT GENERATED ALWAYS AS (json_extract(settings, '$.until')) VIRTUAL,
    paused INTEGER GENERATED ALWAYS AS (json_extract(settings, '$.paused')) VIRTUAL,
 valid_from TEXT NOT NULL, valid_to TEXT NOT NULL, CHECK (valid_from <= valid_to), UNIQUE (name, valid_from, valid_to));
CREATE TABLE review_completions (
    id INTEGER PRIMARY KEY,
    entry TEXT NOT NULL,
    done_on TEXT NOT NULL CHECK (done_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    recorded_by TEXT NOT NULL,
    stored_at TEXT NOT NULL, valid_from TEXT GENERATED ALWAYS AS (stored_at) VIRTUAL,
    UNIQUE (entry, done_on)
);
CREATE TABLE review_completions_history (
        entry TEXT NOT NULL,
        done_on TEXT NOT NULL CHECK (done_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
        recorded_by TEXT NOT NULL,
        stored_at TEXT NOT NULL,
     valid_from TEXT NOT NULL, valid_to TEXT NOT NULL, CHECK (valid_from <= valid_to), UNIQUE (entry, valid_from, valid_to));
CREATE TABLE review_entries (
        id TEXT PRIMARY KEY,
        block TEXT NOT NULL CHECK (block IN ('items', 'practice')),
        description TEXT,
        cadence TEXT,
        command TEXT,
        show TEXT,
     valid_from TEXT NOT NULL);
CREATE TABLE review_entries_history (
        id TEXT NOT NULL,
        block TEXT NOT NULL CHECK (block IN ('items', 'practice')),
        description TEXT,
        cadence TEXT,
        command TEXT,
        show TEXT,
     valid_from TEXT NOT NULL, valid_to TEXT NOT NULL, CHECK (valid_from <= valid_to), UNIQUE (id, valid_from, valid_to));
CREATE TABLE sources (
        id TEXT PRIMARY KEY,
        block TEXT NOT NULL CHECK (block IN ('sources', 'completions')),
        adapter TEXT,
        command TEXT CHECK (command IS NULL OR json_valid(command)),
        timeout REAL,
        lanes TEXT CHECK (lanes IS NULL OR json_valid(lanes)),
     valid_from TEXT NOT NULL);
CREATE TABLE sources_history (
        id TEXT NOT NULL,
        block TEXT NOT NULL CHECK (block IN ('sources', 'completions')),
        adapter TEXT,
        command TEXT CHECK (command IS NULL OR json_valid(command)),
        timeout REAL,
        lanes TEXT CHECK (lanes IS NULL OR json_valid(lanes)),
     valid_from TEXT NOT NULL, valid_to TEXT NOT NULL, CHECK (valid_from <= valid_to), UNIQUE (id, valid_from, valid_to));
CREATE INDEX documents_history_by_key ON documents_history (name, valid_from);
CREATE TRIGGER documents_keeps_what_a_delete_removed AFTER DELETE ON documents
    BEGIN
        INSERT INTO documents_history (name, text, edited_by, valid_from, valid_to)
        VALUES (OLD.name, OLD.text, OLD.edited_by, OLD.valid_from, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
    END;
CREATE TRIGGER documents_keeps_what_an_update_replaced AFTER UPDATE ON documents
    BEGIN
        INSERT INTO documents_history (name, text, edited_by, valid_from, valid_to) VALUES (OLD.name, OLD.text, OLD.edited_by, OLD.valid_from, NEW.valid_from);
    END;
CREATE INDEX draws_by_time ON draws (created_utc);
CREATE INDEX forecast_readings_by_time ON forecast_readings (generated_utc);
CREATE INDEX forecast_settings_history_by_key ON forecast_settings_history (singleton, valid_from);
CREATE TRIGGER forecast_settings_keeps_what_a_delete_removed AFTER DELETE ON forecast_settings
    BEGIN
        INSERT INTO forecast_settings_history (singleton, budget_minutes, valid_from, valid_to)
        VALUES (OLD.singleton, OLD.budget_minutes, OLD.valid_from, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
    END;
CREATE TRIGGER forecast_settings_keeps_what_an_update_replaced AFTER UPDATE ON forecast_settings
    BEGIN
        INSERT INTO forecast_settings_history (singleton, budget_minutes, valid_from, valid_to) VALUES (OLD.singleton, OLD.budget_minutes, OLD.valid_from, NEW.valid_from);
    END;
CREATE INDEX journal_events_by_pursuit ON journal_events (pursuit, occurred_at);
CREATE INDEX journal_events_by_time ON journal_events (occurred_at);
CREATE INDEX pursuit_cycles_history_by_key ON pursuit_cycles_history (name, valid_from);
CREATE TRIGGER pursuit_cycles_keeps_what_a_delete_removed AFTER DELETE ON pursuit_cycles
    BEGIN
        INSERT INTO pursuit_cycles_history (name, settings, valid_from, valid_to)
        VALUES (OLD.name, OLD.settings, OLD.valid_from, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
    END;
CREATE TRIGGER pursuit_cycles_keeps_what_an_update_replaced AFTER UPDATE ON pursuit_cycles
    BEGIN
        INSERT INTO pursuit_cycles_history (name, settings, valid_from, valid_to) VALUES (OLD.name, OLD.settings, OLD.valid_from, NEW.valid_from);
    END;
CREATE INDEX pursuits_history_by_key ON pursuits_history (name, valid_from);
CREATE TRIGGER pursuits_keeps_what_a_delete_removed AFTER DELETE ON pursuits
    BEGIN
        INSERT INTO pursuits_history (name, settings, valid_from, valid_to)
        VALUES (OLD.name, OLD.settings, OLD.valid_from, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
    END;
CREATE TRIGGER pursuits_keeps_what_an_update_replaced AFTER UPDATE ON pursuits
    BEGIN
        INSERT INTO pursuits_history (name, settings, valid_from, valid_to) VALUES (OLD.name, OLD.settings, OLD.valid_from, NEW.valid_from);
    END;
CREATE INDEX review_completions_history_by_key ON review_completions_history (entry, valid_from);
CREATE TRIGGER review_completions_keeps_what_a_delete_removed AFTER DELETE ON review_completions
    BEGIN
        INSERT INTO review_completions_history (entry, done_on, recorded_by, stored_at, valid_from, valid_to)
        VALUES (OLD.entry, OLD.done_on, OLD.recorded_by, OLD.stored_at, OLD.valid_from, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
    END;
CREATE TRIGGER review_completions_keeps_what_an_update_replaced AFTER UPDATE ON review_completions
    BEGIN
        INSERT INTO review_completions_history (entry, done_on, recorded_by, stored_at, valid_from, valid_to) VALUES (OLD.entry, OLD.done_on, OLD.recorded_by, OLD.stored_at, OLD.valid_from, NEW.valid_from);
    END;
CREATE INDEX review_entries_history_by_key ON review_entries_history (id, valid_from);
CREATE TRIGGER review_entries_keeps_what_a_delete_removed AFTER DELETE ON review_entries
    BEGIN
        INSERT INTO review_entries_history (id, block, description, cadence, command, show, valid_from, valid_to)
        VALUES (OLD.id, OLD.block, OLD.description, OLD.cadence, OLD.command, OLD.show, OLD.valid_from, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
    END;
CREATE TRIGGER review_entries_keeps_what_an_update_replaced AFTER UPDATE ON review_entries
    BEGIN
        INSERT INTO review_entries_history (id, block, description, cadence, command, show, valid_from, valid_to) VALUES (OLD.id, OLD.block, OLD.description, OLD.cadence, OLD.command, OLD.show, OLD.valid_from, NEW.valid_from);
    END;
CREATE INDEX sources_history_by_key ON sources_history (id, valid_from);
CREATE TRIGGER sources_keeps_what_a_delete_removed AFTER DELETE ON sources
    BEGIN
        INSERT INTO sources_history (id, block, adapter, command, timeout, lanes, valid_from, valid_to)
        VALUES (OLD.id, OLD.block, OLD.adapter, OLD.command, OLD.timeout, OLD.lanes, OLD.valid_from, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
    END;
CREATE TRIGGER sources_keeps_what_an_update_replaced AFTER UPDATE ON sources
    BEGIN
        INSERT INTO sources_history (id, block, adapter, command, timeout, lanes, valid_from, valid_to) VALUES (OLD.id, OLD.block, OLD.adapter, OLD.command, OLD.timeout, OLD.lanes, OLD.valid_from, NEW.valid_from);
    END;
