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

## `doit` is the Go binary, and runs every command line

`cli/cmd/doit` builds `doit`, which runs every command line itself, bare `doit`, `doit --help` and
`doit tui` among them. `doit tui` draws the boards in `cli/internal/tui`, one per tab, in process.
doit-python's tree holds no verb, and nothing runs it.

- **The cobra tree is the one list of verbs.**
  `TestEveryCommandTheRepoPrintsIsInTheCommandTree` in `cli/cmd/doit` walks each `doit` command
  that the README and the Python source print through the cobra tree.
- **A Tab runs `doit __complete`, and starts no other program and asks no service.** The zsh script
  is cobra's, printed by `doit shell completion zsh`. A pursuit's name after `pursuits log` or `skip`
  is read from `next-names.txt` by `completePursuitNames` in `cli/cmd/doit/pursuits.go`. goclikit
  starts no self-update check on a Tab. `shell` carries goclikit's `StartsNoUpdateCheck` mark,
  because a shell's startup runs it.

- **`cli/internal/login` holds pyclilogin's identifiers for the product `doit`**: the config file,
  the keychain service `doit-cli`, and `$XDG_STATE_HOME/doit` for the fallback token file and the
  refresh lock. Changing any one logs every desk out.
- **`cli/internal/deskfiles` writes two files in `$XDG_CACHE_HOME/doit/` that a Tab or a new shell
  reads without asking the service.**
  - `next-names.txt` holds one `name<TAB>description` line per pursuit, sorted by name. Each command
    that fetches the pursuits rewrites it. `doit __complete` reads it on each Tab after
    `pursuits log` or `pursuits skip`.
  - `nudge-interval-minutes` holds the shell nudge's interval in whole minutes. Each command that
    reads the interval rewrites it, through `keepNudgeInterval`. The gate that
    `doit shell init zsh` prints reads it with no process started.

  The contract test fails where either reader looks at another path.
- **`cli/internal/service` is the Go client of the service.** A route's call is a method that names
  the path and the Go type of its answer. Each field that type declares without `omitempty` is one
  the verb needs, and an answer lacking it is refused with the service's version, never drawn empty.
- **The `cli/` release stream** builds `doit_<version>_<os>_<arch>.tar.gz` for each platform in
  `cli-build`'s matrix, with `checksums.txt`. `doit update` installs from it, and so does a
  machine's own package manager.

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

**Run every desk nudge pick inside the watch.** The panel's view lives in this desk's cache, a pick
takes the desk nudge's state lock, and a pick runs `life habits complete` and a pursuit's `on_log`,
none of which the service runs. `doit desk-nudge panel pick` hands the key to the watch over its
socket rather than running the pick itself. A second runner of picks would be a second owner of the
state file and of every completion rule.

**Never run a started tick again after the watch restarts.** `life habits tick` carries no key, so
a second run records a second set. A tick that the watch stopped inside is recorded uncertain, and
the person ticks again where it did not land. A started habit, log or review does run again,
because life's re-read, the journal entry's id and the review's day each stop a second record.

**Keep `doit tui`'s now tab reading the desk nudge panel with the code of `panel show` and `panel
pick`.** `tui.DeskPanel` in `cli/internal/tui/now_tab.go` calls `Desk.Show` and `Paths.AskPick`,
as those two commands do. The tab draws the `rows` that the panel document carries, the rows that
`panel show` prints, and the watch writes them, so no reader holds the rule that lists them. The
board parses no key and lists no kind. Where it needs another fact about a row, the row carries it,
as `group`, `minutes`, `suggested` and `untick_key` do. Only the panel has this reason, so a tab
over the service's data reads the service's API.

**Let the panel page draw the panel document's `view`, not its `rows`.** The page places each part
of the view on a card of its own, the suggestion, the habits, the pursuits and each lane, which a
flat list of rows cannot carry. It still parses no key: every key it posts is one the view names,
and the watch runs every pick. How a pick ended is the watch's to say, so the page takes it from the
answer to its `POST /api/picks` and from the panel's `picks`, and never infers it from a row's
absence. The Log button's chips are the page's own control, not the terminal's minutes step.

**Keep the placeholder at `cli/internal/panelpage/dist/index.html`.** `go:embed` needs a file there,
so a checkout with no page build still compiles. `npm run build` in `cli/web` writes over it, and git
then shows it modified, so `git restore -- cli/internal/panelpage/dist/index.html` before a commit.
The release builds the page and fails where the build wrote no script under `dist/assets`.

## The service runs no command, and the CLI's own code starts the processes below

A pursuit's `view` is printed for the reader to run, and nothing starts it. Everything else doit
learns from another program arrives over HTTP through the wrappers below.

The Go binary starts these:

- **The editor**, by `openInEditor` in `cli/cmd/doit/document_edit.go`, for every `edit` verb, with
  no bound, because it waits as long as the person edits. gotermboard's `EditorCommand` names it:
  `$VISUAL`, then `$EDITOR`, then `vi`, split on whitespace.
- **A pursuit's `on_log`**, by `pursuits.Log` in `cli/internal/pursuits/log.go`, at
  `doit pursuits log`, stopped after 30 seconds by `OnLogBound`. It runs after the log's check has
  named the item and before the log is posted, so the journal records how it ended. It asks first
  unless `--yes` is passed, and runs nothing where nobody can answer.
- **A review item's `show`**, as `sh -c <show>`, by `ranShow` in `cli/cmd/doit/review.go`, inside
  `doit review nudge`, stopped after 10 seconds by `showBound`.
- **The browser**, through goclilogin's `OpenBrowser`, which runs pkg/browser's `xdg-open` or `open`,
  at `doit auth login`.
- **`webviewrs -n doit-panel -t "doit panel" --dark --close-on-blur <url>`**, by `panelWindowArgv` in
  `cli/cmd/doit/desk_nudge.go`, at `doit desk-nudge panel open`, which replaces the process with it.
- **The desk nudge's programs**, each an argv built by a function in
  `cli/internal/desknudge/programs.go`:
  - `claude-sessions --json`, by `claudeSessionsProgram`, at each firing, answer and pick.
  - `hyprctl instances -j`, then `hyprctl -i <instance> activewindow -j`, at the same moments. A
    unit started at login never has `HYPRLAND_INSTANCE_SIGNATURE`, so the instance is always named.
  - `hyprctl -i <instance> monitors -j`, as a nudge turns ready.
  - `canberra-gtk-play --id <chimeSound>`, by `chimeProgram`, once as a nudge turns ready,
    unless every display is off. A firing opens nothing else.
  - `notify-send -a doit`, by `notifyProgram`, for a passing notice after an answer or a pick,
    and through `Notify` where `panel open` finds no watch to answer it.
  - `loginctl show-session auto -p IdleHint --value`, by `idleHintProgram`, before each timed read
    of the desk panel view. It never moves the clock.
  - `life habits complete <id> --date <day>`, by `habitProgram`, at a pick on a habit, on the desk's
    day when it was picked. The argv is built from the habit's id in code, never read from the
    panel's cache.
  - `life habits completed --since <day> --until <day> --json`, by `habitsCompletedProgram`, as a
    pick on a habit commits, because life accepts a second completion of a habit already done.
  - `life habits tick <item> --date <day>` and `untick`, by `tickProgram` and `untickProgram`, at a
    pick on a checklist item.
  - A pursuit's `on_log`, by `pursuits.OnLogProgram`, at a pick on that pursuit, unasked, because
    the pick is the asking. The template is split into words before the item's fields fill it, so
    no field can add an argument.

  Each program's bound is declared beside its argv, in `programs.go` or, for `on_log`, in
  `cli/internal/pursuits/on_log.go`. Each runs outside the state file's lock. `TestEachProgramTheWatchStartsHasItsArgvAndBound` is the table of every argv
  and bound.

The libraries start processes of their own. pyclilogin and goclilogin each run a login's configured
`token_command`. goselfupdate runs `$GITHUB_TOKEN_COMMAND`, which defaults to `gh auth token`, for
`doit update` and its daily notice where neither `$GITHUB_TOKEN` nor `$GH_TOKEN` is set.

## Documents and records are the service's, and a machine keeps its login, its caches and its desk nudge

- **Documents** — the pursuits, the review register and the sources. The service stores each as its
  text, so comments and layout survive, and `editDocument` in `cli/cmd/doit/document_edit.go`
  round-trips it through the editor. Never in the repo: it is personal, and the repo is public.
- **Records** — the journal, review completions, offered counts, forecast readings and the nudge's
  last showing, in the service's store. A Go verb reaches them only through `cli/internal/service`.
- **A machine's own** — `$XDG_CONFIG_HOME/doit/config.toml` names the service, its issuer and the
  machine's client. `$XDG_CACHE_HOME/doit/` holds what the shell reads with no Python and no request,
  each file rewritten by the command that fetches it, and the desk nudge panel's rows, which the
  watch rewrites and `panel show` reads. `$XDG_STATE_HOME/doit/tui-display.json` keeps the tab
  and display that `doit tui` was left on. `doit config show` prints each of these with
  where it came from, and a value doit newly resolves gets a row there.
- **Held logs** — `$XDG_STATE_HOME/doit/held-logs/` keeps each pursuit log from just before its
  post until the service answers it, one file per entry, named by its id. Where the answer is lost,
  the next `doit pursuits log` or pursuit pick sends it again under its id. The service then stores
  it once, and on_log never runs twice. `HeldLogs` in `cli/internal/pursuits/held.go` is the store.
- **The desk nudge's clock** — the time a desk counts is a fact of that desk, so `$XDG_STATE_HOME/doit/`
  holds the nudge's state file and its record file, which a log shipper reads. Only its answers
  go to the service, so the interval follows the login to any desk. The watch serves its picks on
  `doit/watch.sock` in the runtime directory, in a 0700 directory. `runtimeDir` in
  `cli/internal/desknudge/files.go` says which directory that is where `$XDG_RUNTIME_DIR` is unset.
  The watch also serves the panel page on `127.0.0.1`, at a port it picks at each start. The page's
  cookie is bought with a one-use code that only the socket's `POST /page-codes` mints, so a process
  that cannot open the socket reaches no route.
- **The desk nudge's schemas** — `schemas/desk-nudge-status.schema.json`,
  `desk-nudge-record.schema.json` and `desk-nudge-panel.schema.json` are the source of each shape,
  edited by hand. `TestEachDocumentTheWatchWritesMatchesItsSchemaFile` validates what the Go watch
  writes against them.

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

So a module the service imports holds rules and renderers, and never a command. Every verb that
asks the service is a Go command, and Python holds no client of the service.

`cli/internal/contract` runs this commit's Go doit against this commit's service, started from
`api/tests/serve_on_a_temporary_store.py` on a temporary store. A shape changed on one side and not
the other fails there. `api/tests/test_cli_against_service.py` pins what the service answers on its
own.

Every external effect the service has goes through one wrapper:

- **The store** is `doit_api.store.Store`, the only module that writes SQL.
- **An app** is read through `doit_api.readers.apps.App.get`, GitHub through
  `readers.github.GitHub.call`, ClickHouse through `readers.clickhouse.checks`.
- **An app's write** goes through `App.post`. The service names the person's login in its body, so
  the app records the decision as theirs and keeps the record.
- **A failed read** raises `ReadFailed`, and `readers.declared_reads` logs it at error as
  `source_failed` with the source's name. An alert is filed on that line, so keep its name and its
  `source` field. **A failed write** raises `WriteFailed`, and `computed_views.refusal_of` maps it
  to the route's status.

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
