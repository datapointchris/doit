# doit — development context

## What this is

The layer that decides what to attend to and drives it. It stewards no records of its own — the
per-domain CLIs do that — so a change that has `doit` deciding a domain rule belongs in the CLI
that owns the domain, not here.

## What will look like a bug and is not

**The dashboard duplicates due-ness.** It imports nothing from the cadence module and instead
mirrors the `overdue` field the review register already emits. Backends own due-ness and speak
`--json`; that is the contract, and it is what lets the dashboard need no dependencies. Inside one
package it reads as removable duplication. Do not remove it.

**The journal records each pursuit's pace, though the register already declares it.** The
register says only what is asked from now on. Standing counts back to the zero point, so a pace
read off the register alone reprices every past day whenever it is edited, and bills a pause in
full the day it ends. A build writes a `pace` record wherever the register disagrees with the
newest one, and `asked_since` prices each span at the pace recorded over it. Reading the register
in its place would look simpler and be wrong about every day before the last edit.

**`doit today` counts a timed pursuit behind long before the due list does.** Its line counts
every minute the pace asked before today and nothing paid. The due list calls a pursuit owed only
once it is a whole checkoff behind, and a timed pursuit's checkoff is a week. So work can read
hours behind there while `doit next` still says `due in 3d`. Both are right: one says how far
behind, the other when it comes due. Converting a counted checkoff into minutes, or either unit
into points, would let the two be summed. Both conversions were weighed and rejected, because a
checkoff has no length and a point is a quantity nobody declared.

## Content, config, and state are three different things

- **Content** — cards, Labs, and the tool registry (`cards/`, `labs/`, `tools/`). The
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
- **State** — `$XDG_STATE_HOME/doit/`, or `$DOIT_JOURNAL_DIR` where a box reads the share somewhere
  else, resolved once by `paths.journal_dir()`. Per-machine wherever a sync layer would otherwise
  have to merge concurrent writes, which it cannot.

A path `$DOIT_JOURNAL_DIR` or `$DOIT_PURSUITS` names must exist, and doit refuses one that does not,
naming the variable. A missing default is a fresh machine and reads as empty. A missing named path
is a share that has not arrived. Read as empty, it lets a scheduled job run on no data and exit 0,
writing into a directory that never syncs.

## One renderer per collection, and `doit.tools` owns most of them

`doit find` and `doit show` assemble a subject from every collection that has it and render none
of it themselves — each lens hands off to whatever owns it. `doit.tools` is that owner for the
registry and for the shell collections, so its card shapes take plain arguments rather than index
rows: that is what lets `doit show` compose them without `tools` importing `index`, which would
close a cycle.

The registry's own path and loader live in `doit.tools` for the same reason — `index` and `labs`
both read it, and a constant defined per reader is a constant that drifts.

## Sources are configuration, not code

`doit` must not know which apps supply *lanes*. Adding a source that prints the lane document is an
edit to `sources.yml`, never a release. A source with an adapter is already code, and the adapter
carries its command too. The command and the model it returns are one contract, and a copy of the
command in `sources.yml` is one no sweep of the code finds when its tool renames a flag.
A source that is not configured is silent; one configured but missing gets a single line; one that
runs and fails shows its error. A lane is never silently dropped — a dashboard that quietly omits a
lane reads as "nothing outstanding", which is the worst available failure.

## The service runs the root package's view code, never a copy

`api/` is its own project and depends on the root package by path. Each method of
`doit_api.computed_views.Views` calls the function the matching CLI command calls, handing in
what it read from the store where the command read a desk's file. A rule therefore lives in one
module, and the service and a desk cannot disagree about it. A change that has `api/` compute a
balance, a due date or a lane itself is the drift this arrangement exists to prevent: add a seam
to the view module instead, one that takes its data rather than reading it.

Every external effect the service has goes through one wrapper:

- **The store** is `doit_api.store.Store`, the only module that writes SQL.
- **An app** is read through `doit_api.readers.apps.App.get`, GitHub through
  `readers.github.GitHub.call`, ClickHouse through `readers.clickhouse.checks`.
- **A failed read** raises `ReadFailed`, and `readers.declared_reads` logs it at error as
  `source_failed` with the source's name. An alert is filed on that line, so keep its name and its
  `source` field.

Shapes that look wrong and are not:

- **The image's build context is the repository root**, with `api/Dockerfile.dockerignore`
  admitting only the root package and `api/`. A context of `api/` cannot reach the view code.
- **`deploy/` names the image `doit-api` with no tag.** `release/pin-manifests` pins it by digest in
  the copy the release pushes, and `validate.yml` lints what that renders. kube-linter refuses the
  committed base, which is why it reads the rendered copy.
- **A pursuit's `resolve` and `evidence` are a command on a desk and a read in the service**:
  `{app, path, query}`. The register's validator accepts either. The import rewrites one into the
  other, and the reader that runs it says which form it cannot run.
- **`readers/icb.py` composes icb's overview itself.** icb serves one route per section and no
  overview, so the module is a copy of icb's CLI composition: the focus project, the article
  queue, the countdowns, and the rule that a refused credential refuses the whole document. A
  change to that composition in icb is made here too, until icb serves the document.
- **The desk-check lane reads every record the table keeps.** Each machine and check's newest
  outcome stands, with no time window, so a failure stays until a later pass. A window would drop
  a failure from a machine that went quiet before it was fixed.

## Generated config — never hand-edit

`.pre-commit-config.yaml`, `.github/workflows/validate.yml`, `.editorconfig`, `.markdownlint.yaml`
and the `pyproject.toml` keys listed under `[tool.forge] managed` are generated from this repo's
declared toolchain components. Regenerate with `forge repos apply precommit -F doit` and
`forge repos apply ci -F doit` — `forge dies` is the library and carries no `run` verb; a
reconcile verb takes the die name. Anything edited outside a `# > custom:` marker is overwritten
on the next sync.
