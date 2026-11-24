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

## `doit` is the Go binary, and doit-python runs each verb it has not ported

The CLI is moving to Go one verb at a time, as a strangler fig. `cli/cmd/doit` builds `doit`, and
`owns` in its `root.go` decides which command lines it runs. It execs `doit-python`, the Python
console script, with the same argv for everything else: an unported verb, a flag Go does not parse,
bare `doit` and `doit --help`. A port adds its verb to cobra's tree and deletes the Python command
in the same change, so no verb has two implementations.

- **The cobra tree is the one list of what Go runs.** The exec hands it to `doit-python` in
  `DOIT_GO_COMMANDS`, and `list_go_commands` in `cli.py` adds an entry that runs nothing for each
  command the Python tree lacks, so the help Python prints names every verb. Nothing in Python lists
  a Go verb by hand. `cli/cmd/doit/testdata/tree-handed-to-doit-python.json` is that list as Go
  hands it, which the Go scenario test checks and the Python tests read.
- **A namespace that Go runs whole carries the `portedWhole` annotation.** Its bare and `--help`
  lines stay in Go. A partly ported namespace carries none, so those lines reach Python, whose
  group lists the Go commands beside its own.

- **`cli/internal/login` holds pyclilogin's identifiers for the product `doit`**: the config file,
  the keychain service `doit-cli`, and `$XDG_STATE_HOME/doit` for the fallback token file and the
  refresh lock. Changing any one logs every desk out of one side.
- **`cli/internal/service` is the Go client of the service.** A route's call is a method that names
  the path and the Go type of its answer. Each field that type declares without `omitempty` is one
  the verb needs, and an answer lacking it is refused with the service's version, never drawn empty.
- **The `cli/` release stream** builds `doit_<version>_<os>_<arch>.tar.gz` for linux and darwin on
  amd64 and arm64, with `checksums.txt`. `doit update` installs from it, and so does a machine's
  own package manager. `doit-tui` still installs with `go install` from the same tags.

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

**Keep the desk nudge's clock blind to input.** A short absence comes every few minutes at a desk,
so a clock that pauses or resets on one rarely reaches its interval, and the nudge never fires.
Only a nudge ending restarts it. An idle hint may gate a background read, never the count.

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

**Keep `doit-tui` reading the desk nudge panel through `doit desk-nudge panel`.** The panel's rows
live in this desk's cache, its picks take the desk nudge's state lock, and a pick runs `life habits
complete` and a pursuit's `on_log`, none of which the service runs. A Go copy of any of it would be a
second owner of the state file and of every completion rule. When doit's CLI moves to Go, the tab
moves with it and calls the same code in process. Only the panel has this reason, so a tab over the
service's data reads the service's API. The board parses no key and lists no kind. Where it needs
another fact about a row, the row carries it, as `group`, `minutes` and `suggested` do.

## The service runs no command, and the CLI's own code starts the processes below

- **`$EDITOR`**, for every `edit` verb, in `editor.open_in_editor`, with no timeout.
- **`doit-tui`**, in `tui.tui_command`, which replaces the process with it. `doit-tui` is the Go
  program in `cli/`. It starts `doit desk-nudge panel show --json` on a timer and `panel pick <key>
  --json` for each pick, each in a session of its own, so every rule about a row stays in this
  package.
- **A pursuit's `on_log`**, in `pursuits.run_on_log`, after asking, with no timeout. A desk nudge
  panel pick runs it unasked, because the pick is the asking.
- **A review item's `show`**, in `review.run_show`, inside the nudge, stopped after 10
  seconds.
- **`claude-sessions --json`**, in `service_commands.desk_nudge.claude_sessions`, at each desk
  nudge firing, answer and panel pick.
- **`hyprctl`**, in `service_commands.desk_nudge.hyprctl`: `instances -j`, then `-i <instance>
  activewindow -j` at the same moments, and `-i <instance> monitors -j` as a nudge turns ready. A
  unit started at login never has `HYPRLAND_INSTANCE_SIGNATURE`, so the instance is always named.
- **`canberra-gtk-play --id <CHIME_SOUND>`**, in `service_commands.desk_nudge.chime`, once as a
  nudge turns ready, unless every display is off. A firing opens nothing else.
- **`notify-send`**, in `notify`, for a passing notice after an answer or a panel pick.
- **`loginctl show-session auto -p IdleHint --value`**, in `service_commands.desk_nudge.session_idle`,
  before each timed read of the desk nudge panel's rows. It never moves the clock.
- **`life habits complete <id>`**, in `service_commands.desk_nudge.completed_habit`, at a panel
  pick on a habit, stopped after `HABIT_SECONDS`. Its argv is `dashboard.habit_completion_argv`,
  built from the habit's id in doit's code, never read from the panel's cache.

The desk nudge programs are named in `service_commands.desk_nudge`, and each one but `life habits
complete` is stopped after `PROGRAM_SECONDS`. Every one runs outside
the state file's lock. A pursuit's `view` is printed for the reader to run, and nothing starts it. Everything else doit learns from another program arrives over HTTP through the wrappers
below.

The Go binary starts these:

- **`doit-python`**, in `passthrough.Exec`, which replaces the process with it for every command line
  that `owns` turns down.
- **The browser**, through pkg/browser's `xdg-open` or `open`, at `doit auth login`.

The libraries start processes of their own. pyclilogin and goclilogin each run a login's configured
`token_command`. goselfupdate runs `$GITHUB_TOKEN_COMMAND`, which defaults to `gh auth token`, for
`doit update` and its daily notice where neither `$GITHUB_TOKEN` nor `$GH_TOKEN` is set.

## Documents and records are the service's, and a machine keeps its login, its caches and its desk nudge

- **Documents** — the pursuits, the review register and the sources. The service stores each as its
  text, so comments and layout survive, and `service.edit_document` round-trips it through
  `$EDITOR`. Never in the repo: it is personal, and the repo is public.
- **Records** — the journal, review completions, offered counts, forecast readings and the nudge's
  last showing, in the service's store. A command reaches them only through `doit.service`, the
  one module that makes a request.
- **A machine's own** — `$XDG_CONFIG_HOME/doit/config.toml` names the service, its issuer and the
  machine's client. `$XDG_CACHE_HOME/doit/` holds what the shell reads with no Python and no request,
  each file rewritten by the command that fetches it, and the desk nudge panel's rows, which the
  watch rewrites and `panel show` reads. `doit config show` prints each of these with
  where it came from, and a value doit newly resolves gets a row there.
- **The desk nudge's clock** — the time a desk counts is a fact of that desk, so `$XDG_STATE_HOME/doit/`
  holds the nudge's state file and its record file, which a log shipper reads. Only its answers
  go to the service, so the interval follows the login to any desk.

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

`api/` is a uv workspace member and depends on the root package through the workspace. Each method of
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
- **`readers/life.py` composes life's overview itself.** life serves one route per section and no
  overview, so the module is a copy of life's CLI composition: the article queue, the
  countdowns, and the rule that a refused credential refuses the whole document. A change to
  that composition in life is made here too, until life serves the document.
- **`readers/projects.py` picks the focus project itself.** projects serves its items and which
  are blocked, and its CLI picks the focus in `projects next`. The module copies that rule, so a
  change to it in projects is made here too.
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
