# doit — development context

## What this is

The layer that decides what to attend to and drives it. It stewards no records of its own — the
per-domain CLIs do that — so a change that has `doit` deciding a domain rule belongs in the CLI
that owns the domain, not here.

## Two things that will look like bugs and are not

**The dashboard duplicates due-ness.** It imports nothing from the cadence module and instead
mirrors the `overdue` field the backends already emit. Backends own due-ness and speak `--json`;
that is the contract, and it is what lets the dashboard need no dependencies. Inside one package it
reads as removable duplication. Do not remove it.

**Same-day commits reappear in a review.** A cadence register holds a date while commits hold a
timestamp, so resolving the date to the newest preceding commit shows same-day commits again on the
next run. Repeating a few is the correct direction to be wrong in; the alternative drops them unread.

## Content, config, and state are three different things

- **Content** — cards and Labs. Their own repo, cloned into `$XDG_DATA_HOME/doit/`, updated by
  `doit content sync`. Authored often, so it must never need a release of this repo to change.
  That path is resolved, never assumed to be a real directory: a machine that authors cards points
  it at a checkout kept where its git lives, so writing a card and reading it stay one file. Making
  that so is the installing layer's job — doit only ever opens the path it is given.
- **Config** — `pursuits.yml`, the review register, `sources.yml`. `$XDG_CONFIG_HOME/doit/`,
  hand-edited, and only ever *read* here so comments and layout survive. Never in either repo: it is
  personal, and both repos are public.
- **State** — `$XDG_STATE_HOME/doit/`. Per-machine wherever a sync layer would otherwise have to
  merge concurrent writes, which it cannot.

## Sources are configuration, not code

`doit` must not know which apps exist. Adding a source is an edit to `sources.yml`, never a release.
A source that is not configured is silent; one configured but missing gets a single line; one that
runs and fails shows its error. A lane is never silently dropped — a dashboard that quietly omits a
lane reads as "nothing outstanding", which is the worst available failure.

## Generated config — never hand-edit

`.pre-commit-config.yaml`, `.github/workflows/validate.yml`, `.editorconfig`, `.markdownlint.json`
and the `pyproject.toml` keys listed under `[tool.forge] managed` are generated from this repo's
declared toolchain components. Regenerate with
`forge dies run maintenance/sync-{pre-commit,ci}.sh -F doit`. Anything edited outside a
`# > custom:` marker is overwritten on the next sync.
