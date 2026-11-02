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

## Content is the machine's, and documents and records are the service's

- **Content** — cards, Labs, and the tool registry (`cards/`, `labs/`, `tools/`). The
  `terminal-library` repo, cloned into `$XDG_DATA_HOME/terminal-library/`, updated by
  `doit content sync`. Authored often, so it must never need a release of this repo to change.
  Named for the library and not for doit, because doit is one reader of it. `paths.library_dir()`
  is the only place that resolves the root; every module reaches into a subdirectory of that rather
  than rebuilding the path, so the next move is one line.
  That path is resolved, never assumed to be a real directory: a machine that authors cards points
  it at a checkout kept where its git lives, so writing a card and reading it stay one file. Making
  that so is the installing layer's job — doit only ever opens the path it is given.
- **Documents** — the pursuits, the review register and the sources. The service stores each as its
  text, so comments and layout survive, and `service.edit_document` round-trips it through
  `$EDITOR`. Never in either repo: it is personal, and both repos are public.
- **Records** — the journal, review completions, offered counts, forecast readings and the nudge's
  last showing, in the service's store. A command reaches them only through `doit.service`, the
  one module that makes a request.
- **A machine's own** — `$XDG_CONFIG_HOME/doit/config.toml` names the service, its issuer, the
  machine's client and `library_dir`. `$XDG_CACHE_HOME/doit/` holds what the shell reads with no
  Python and no request, each file rewritten by the command that fetches it.

## One renderer per collection, and `doit.tools` owns most of them

`doit find` and `doit show` assemble a subject from every collection that has it and render none
of it themselves — each lens hands off to whatever owns it. `doit.tools` is that owner for the
registry and for the shell collections, so its card shapes take plain arguments rather than index
rows: that is what lets `doit show` compose them without `tools` importing `index`, which would
close a cycle.

The registry's own path and loader live in `doit.tools` for the same reason — `index` and `labs`
both read it, and a constant defined per reader is a constant that drifts.

## Which sources are asked is configuration, and how each is read is code

`doit` must not know which sources a person asks, so turning one on is an edit to the sources
document, never a release. How a source is read is an adapter, and the adapter carries its read
and its command too. The read and the model it returns are one contract, and a copy of either in
the sources document is one no sweep of the code finds when the app changes.

The service runs no command, so a source declared by `command` alone, printing the lane document,
is shown as failed rather than read. An app with no adapter needs a release before doit can ask it.
A source that is not configured is silent, and one that is configured and fails shows its error. A
lane is never silently dropped — a dashboard that quietly omits a lane reads as "nothing
outstanding", which is the worst available failure.

## The service runs the root package's view code, never a copy

`api/` is its own project and depends on the root package by path. Each method of
`doit_api.computed_views.Views` calls the root package's view function, handing in what it read
from the store, and the CLI renders what the route answers. A rule therefore lives in one module,
and nothing computes it twice. A change that has `api/` compute a balance, a due date or a lane
itself is the drift this arrangement exists to prevent: add a seam to the view module instead, one
that takes its data rather than reading it.

So a module the service imports holds rules and renderers, and never a command. The commands that
ask the service live in `doit.service_commands`, one module beside each rule module, so the service
never loads the client that calls it. A new command that asks the service goes there.
`test_the_service_never_loads_the_client_that_calls_it` fails when a rule module imports
`doit.service`.

The CLI and the service are tested together in `api/tests/test_cli_against_service.py`, which
forwards each command's requests into the app in process. A shape changed on one side and not the
other fails there.

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
- **The register's validator accepts a `resolve` or `evidence` written as a command** as well as
  the read `{app, path, query}` the service runs. A desk's register arrives by import in the
  command form, which the import rewrites into the read, and the reader names any it cannot run.
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
