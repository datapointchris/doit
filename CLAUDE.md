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

**The review lane shells out to atuin on every render.** The dashboard deliberately stopped
subprocessing its own lanes on cost grounds, so a new one reads as a regression. It is not the same
call: those two spawned a Python interpreter each to re-read a local file this process could already
open, whereas atuin is the only source that knows which machine ran a command, which is what makes a
`scope: machine` item answerable at all. It is one query per process, shared by every item, and it
falls back to this machine's zsh history whenever atuin cannot be asked.

## Content, config, and state are three different things

- **Content** — cards, Labs, and the tool registry (`workflows/`, `labs/`, `tools/`). The
  `terminal-library` repo, cloned into `$XDG_DATA_HOME/terminal-library/`, updated by
  `doit content sync`. Authored often, so it must never need a release of this repo to change.
  Named for the library and not for doit, because doit is one reader of it. `paths.library_dir()`
  is the only place that resolves the root; every module reaches into a subdirectory of that rather
  than rebuilding the path, so the next move is one line.
  That path is resolved, never assumed to be a real directory: a machine that authors cards points
  it at a checkout kept where its git lives, so writing a card and reading it stay one file. Making
  that so is the installing layer's job — doit only ever opens the path it is given.
- **Config** — `pursuits.yml`, the review register, `sources.yml`. `$XDG_CONFIG_HOME/doit/`,
  hand-edited, and only ever *read* here so comments and layout survive. Never in either repo: it is
  personal, and both repos are public.
- **State** — `$XDG_STATE_HOME/doit/`. Per-machine wherever a sync layer would otherwise have to
  merge concurrent writes, which it cannot.

## One renderer per collection, and `doit.tools` owns three of them

`doit find` and `doit show` assemble a subject from every collection that has it and render none
of it themselves — each lens hands off to whatever owns it. `doit.tools` is that owner for the
registry and for the shell collections, so its card shapes take plain arguments rather than index
rows: that is what lets `doit show` compose them and the reminder rotation reuse them without
`tools` importing `index`, which would close a cycle.

The registry's own path and loader live in `doit.tools` for the same reason — `index` and `labs`
both read it, and a constant defined per reader is a constant that drifts.

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
