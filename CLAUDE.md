# doit — development context

## What this is

The layer that decides what to attend to and drives it. It stewards no records of its own — the
per-domain CLIs do that — so a change that has `doit` deciding a domain rule belongs in the CLI
that owns the domain, not here.

doit covers what a person does themselves, and that includes the person's own review of the fleet,
so the review register's fleet-maintenance items stay. Engineering work that agents do is tracked by
slate and fleet, and no pursuit or daily list carries it. Chris: "doit is more for personal life
stuff", and of the register's fleet items, "They are personal review things that I personally have
to do, not build tasks that claude is going to do".

howto carries the cards, the Labs and the tool registry, and doit reads nothing from it. A practice
entry's `command` is text in the review document, which doit shows and never runs.

## What will look like a bug and is not

**Keep the dashboard's duplicate of due-ness.** It imports nothing from the cadence module and
mirrors the `overdue` field the review register emits. Backends own due-ness and speak `--json`,
and that contract is what lets the dashboard need no dependencies.

**Keep the journal's record of each pursuit's pace, though the register declares it.** The register
says only what is asked from now on. Standing counts back to the zero point, so a pace read off the
register alone reprices every past day whenever it is edited, and bills a pause in full the day it
ends. A build writes a `pace` record wherever the register disagrees with the newest one, and
`asked_since` prices each span at the pace recorded over it.

**Keep evidence as a list of dates, never a count of rows, and age none of them out.** Row counts
do not compare across apps: a habits backend answers with hundreds and a books backend with one. A
date either carries evidence or does not, whichever app was asked. Standing counts back to a
pursuit's zero point, so a date dropped is a payment the balance no longer sees, and the pursuit
reads as owing it forever.

**Do not reconcile `doit today` and the due list on a timed pursuit.** `doit today` counts every
minute the pace asked before today and nothing paid. The due list calls a pursuit owed only once it
is a whole checkoff behind, and a timed pursuit's checkoff is a week. So work can read hours behind
there while `doit next` still says `due in 3d`. One says how far behind, the other when it comes
due. Converting a counted checkoff into minutes, or either unit into points, would let the two be
summed. Both conversions were weighed and rejected, because a checkoff has no length and a point is
a quantity nobody declared.

**Join the `upcoming` lane and no other.** Two adapters build a lane named `upcoming`, because a
person's dates and planned trips live in two apps and read as one timeline.
`dashboard.upcoming_joined` joins them, and each source fails on its own: it costs its rows and adds
its reason. A merge of every same-named lane was rejected, because it changes what a lane name means
for every other source. Feedback stays the opposite way: one lane per app, never combined, so three
apps draw three lanes.

## The service runs no command, and the CLI starts exactly three processes

- **`$EDITOR`**, for every `edit` verb, in `editor.open_in_editor`.
- **A pursuit's `on_log`**, in `pursuits.run_on_log`, after asking.
- **A review item's `show`**, in `review.run_show`, inside the nudge.

A pursuit's `view` is printed for the reader to run, and nothing starts it. Everything else doit
learns from another program arrives over HTTP through the wrappers below.

## Documents and records are the service's, and a machine keeps only its login and its caches

- **Documents** — the pursuits, the review register and the sources. The service stores each as its
  text, so comments and layout survive, and `service.edit_document` round-trips it through
  `$EDITOR`. Never in the repo: it is personal, and the repo is public.
- **Records** — the journal, review completions, offered counts, forecast readings and the nudge's
  last showing, in the service's store. A command reaches them only through `doit.service`, the
  one module that makes a request.
- **A machine's own** — `$XDG_CONFIG_HOME/doit/config.toml` names the service, its issuer and the
  machine's client. `$XDG_CACHE_HOME/doit/` holds what the shell reads with no Python and no request,
  each file rewritten by the command that fetches it. `doit config show` prints each of these with
  where it came from, and a value doit newly resolves gets a row there.

## Which sources are asked is configuration, and how each is read is code

`doit` must not know which sources a person asks, so turning one on is an edit to the sources
document, never a release. How a source is read is an adapter: an HTTP read in
`readers.declared_reads` and a builder passed to `sources.register_adapter`, joined by the adapter's
name. The read and the model it returns are one contract, and a copy of either in the
sources document is one no sweep of the code finds when the app changes.

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
- **The store keeps an `imports` table nothing writes.** It is the only record of what each desk's
  files added when they moved onto the service.
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
