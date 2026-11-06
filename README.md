# doit

What to do now, and everything that decides it.

Every other personal CLI here stewards records. icb holds tasks, habits, books and projects.
learning holds tracks, meso holds workouts, and nomad holds trips. doit is the one that consumes
them and points somewhere: what is outstanding, what to do next, what is due to revisit and what to
practice.

doit owns no domain record. Which book is next, which habit is due and when a task is late are
answered by the CLI that owns the domain, and doit lays the answer out. A change that has doit
deciding a domain rule belongs in that CLI instead.

doit covers what a person does themselves. Engineering work that agents do belongs in the tracker
those agents read, and no pursuit or daily list carries it.

## Install

```bash
uv tool install --index https://pypi.ichrisbirch.com/simple/ doit   # the newest release
doit --version                                                       # the release that installed
doit update --check                                                  # whether a newer one exists
```

Every release is published to that package index. Name it on the install: without it uv asks PyPI,
where `doit` is a different project. uv records the index beside the tool, which is where
`doit update` reads it back.

`doit update` installs the newest release over the running one. It refuses an install from a
branch, a commit or a local checkout, because none of them carries a release version to compare.
Every other command checks for a newer release at most once a day, and names one it finds in a line
on stderr.

uv fetches the Python doit needs.

## Starting from scratch

The pursuits, the registers and every record live in doit's service, described under
[The service](#the-service--one-store-and-every-view-computed-behind-it). A machine logs in to it
once, and every command after that reads and writes there.

```bash
doit auth login https://doit.example.com   # once per machine: names the service and logs you in
doit config show             # what this machine resolved, and where each value came from

doit sources example         # an annotated sources document, printed, writing nothing
doit sources edit            # the sources in $EDITOR, starting from that example where none are written
doit sources list            # every configured source, and whether the service's read of it answers
doit dashboard               # every lane, each ordered by the app that owns it

doit pursuits edit           # the pursuits in $EDITOR, starting from an annotated template where none are written
doit pursuits list           # proves the register parsed: each pursuit, its weight, its goal, when it is due
doit next                    # everything owed, then the rest of the screen drawn by weight
doit pursuits log            # record one; whatever you leave out is asked for

doit review edit             # the review register in $EDITOR, starting from a one-item template
doit review due              # what is due to revisit, most overdue first

eval "$(doit shell init zsh)"         # in .zshrc: a nudge about due items, gated by doit review nudge-every
eval "$(doit shell completion zsh)"   # in .zshrc: completion for the verbs and your pursuit names

doit today                   # every record at once: what the day still owes, and what it has had
doit happened --since 7d     # what every record says happened over the week, and who did it
```

Each `edit` opens the document the service holds, or its template where none is written yet. The
service validates the whole text and stores nothing it refuses. A refusal names the first fault and
reopens the editor on the text as you left it, so one typo never costs the edit. A save that fails
any other way keeps the text in `$XDG_STATE_HOME/doit/<name>-unsaved.yml`, and the next `edit`
starts from it. doit never rewrites a document itself, so your comments and layout survive.

## The grammar

The noun comes first and the verb last: `doit review due`, `doit forecast run`. The draw sits at the
root as `next`, because it is what you type most. Logging and skipping a pursuit are
`doit pursuits log` and `doit pursuits skip`, beside the file that holds the pursuits. The other
single-verb reads sit at the root beside `next`, and `doit --help` lists them.

Every node prints its help when given no arguments, so the tree can be walked one word at a time
without a cryptic error. A usage error exits 2. `--no-input` never prompts, and fails naming the
flag that would have answered.

## Which surface answers which question

| Ask | Surface | Reads |
| --- | --- | --- |
| What should I do now | `next` | the pursuits, the journal, each pursuit's `resolve` and `evidence` reads |
| What is outstanding anywhere | `dashboard` | every `sources:` entry, plus doit's own review register |
| What does today still want, and what has it had | `today` | the same sources, every `completions:` entry but closed issues, the journal, the review completions |
| What happened over a window, and who did it | `happened` | every `completions:` entry, the journal and the evidence cache, the review completions |
| Is each goal getting what it asked for | `pursuits drift` | the journal and how often each pursuit was offered each day |
| Would this register fit in my days | `forecast run`, `forecast trend` | the register and the journal run through the real draw, then each stored forecast against the journal |
| What is due to revisit or practice | `review due` | the review register and its completions |
| Which apps does doit ask | `sources list` | the sources, each read once by the service |
| What is this machine set to, and who set it | `config show` | the config file, the environment and the defaults, asking no service |

The service answers every row, reading what the row names.

`dashboard`, `today` and `next` are three densities over the same records, not three subjects.
`dashboard` ranks across nothing. `next` ranks across everything, because you declared the
ordering. `today` admits only what a day can finish. `next` and `dashboard` meet in one line under
the lanes, naming the pursuits that are behind.

## `next` — the one ranked list, because you declared the ranking

```bash
doit next                                     # everything owed, heaviest first, then the rest drawn by weight
doit next --explain                           # the same draw with every number behind it
doit pursuits log read-library --minutes 40   # record a 40-minute sitting of a timed pursuit
doit pursuits skip chores --for 2w            # out of the draw until it expires, owing nothing meanwhile
doit pursuits resume chores                   # end that skip early
doit pursuits reset chores                    # move the zero point to now, which forgives what it owed
doit pursuits drift                           # what each goal asked for against what got done
doit forecast run                             # the register run forward through the real draw
doit forecast trend                           # what earlier forecasts predicted, against what got logged
```

A pursuit is a strand of life you want to spend attention on. It declares two separate things:
its pace, which is how much of it you want, and its weight, which is how much it matters against
the others. The annotated template `doit pursuits edit` starts from explains every field.

`doit pursuits log` writes through to the app that owns the item. It offers to run the pursuit's
`on_log` command on this machine, such as completing the task it resolved to. `--yes` runs it
without asking, and `--no-write` logs only. The service records the log and what the command did,
and never runs one itself.

### A pursuit declares its goal, and its weight only orders

A pursuit declares at most one pace. `cadence: 3d` asks for one occurrence every three days, in
whole days only, so twice a week is `3d` or `4d`. `weekly_minutes: 240` asks for four hours a week
and makes the pursuit measured in time. `doit pursuits log` asks a timed pursuit how long it took and never
asks a counted one. `weekly_minutes:` is what a week asks for. `doit pursuits log --minutes` is how long one
sitting took. A timed pursuit is paid only by what is logged that way. An app reports which day
something happened and never how long, so the register refuses `evidence` on a pursuit measured in
minutes.

A weight never moves a pace. It orders what is owed, heaviest first, and when less than a screen is
owed it draws the rest. So whether a pursuit is getting enough is answered by its own goal alone,
whatever the rest of the register weighs and however much else got logged that week.

A weight is set by hand, and `reason:` says why it is what it is. `doit pursuits list`, `drift`,
`dormant` and `doit next --explain` print it, and the `--json` of those that have one carries it. Nothing reads
it to adjust anything.

A pursuit declaring no pace is tracked. It asks for nothing, so it is never due, never drawn, and
takes no weight. Its logs and its app's evidence are read all the same, so `doit today` lists it as
done and `doit pursuits drift` counts it. That suits something already done plenty of, whose time
you want to see. `doit pursuits log --minutes` records how long a sitting took when given, and nothing asks
for it. A weight with no pace is refused, because a forgotten pace leaves exactly that shape.

### Standing is one balance, and the screen states it as a date

Standing is one balance in whichever unit applies: what the goal asked for since the pursuit's zero
point, less what was done since. Three chores in one evening count as three. Twenty minutes of
reading pays twenty minutes off a week's goal. Nothing ages out. A month away is owed in full for as
long as it goes unpaid, and a burst stays credited the same way. A reset is the one thing that
forgives a debt.

Each day is asked for at the pace in force that day. doit journals a pursuit's pace whenever the
register says something the journal has not recorded, so editing one never reprices the days before
the edit. A span the register took a pursuit out of asks for nothing: paused, past its `until`, or
weighing nothing. Moving a pursuit between a cadence and `weekly_minutes` starts its balance again,
because a count and a number of minutes do not add.

The zero point is written the first time doit reads a pursuit, one interval back. So a pursuit just
declared opens one checkoff owed, and goes overdue as time passes.

That balance is never the number on screen. Every view states standing as a date: `3d overdue`,
`due today`, `due in 2w`. `doit.allocate` holds the conversion. A skip still running holds the date
until it ends. `doit next --json` carries the raw balance, and `doit pursuits drift` sets what each
goal asked for beside what got done.

`doit pursuits skip` takes a pursuit out of the draw until the skip expires, and it owes nothing meanwhile.
`doit pursuits reset` with no name moves every zero point, after confirming. The journal is
append-only either way. A skip and a reset are markers written into it, never a rewrite of what
happened.

### The screen is one list in one order

Every pursuit owing a whole checkoff is shown, however many there are. When less than a screen is
owed, the rest is drawn by weight from what is current. A register with nothing outstanding says
what there is rather than going blank. A pursuit you are a whole checkoff ahead on stays out of the
draw, unless leaving it out would empty the screen.

The offered list is one list. What is owed comes first, heaviest first and then latest first. What
was drawn follows, soonest due first. That one order is what the screen shows, what the forecast
walks top-down, and what `rank_offered` counts in a journal entry. The draw is cached for fifteen
minutes, so running `next` again while deciding does not reshuffle the list underneath you.

A pursuit whose `resolve` read matches several rows offers the first three, stacked under its
name. One row is a decision the register made. Several are a choice you make, so `doit pursuits log` asks
which one wherever the pursuit's `on_log` would complete it. Enter names none, and so does a log
with nobody there to ask.

`doit next` is one table with a row for every active pursuit. Each row states the weight, the goal,
what was done of what the goal asked since its zero point and over how many days, and when it next
comes due. The offered rows come first and carry what to do. Every other pursuit follows, dimmed,
with its standing and nothing to do. A skipped one says `skipped` in place of a date. A narrow pane
gives up the weight, then the goal, then the tally, and keeps the name and the date.

The forecast runs the real draw against a journal it builds as it goes, over a week, two weeks and a
month. A day's budget is `forecast.budget_minutes` in the register, or `--budget` on the command.
The competition for that budget is the thing worth simulating: whether a register's goals fit in its
days cannot be read off the page.

## `dashboard` — every lane, ranked across none of them

```bash
doit dashboard                 # every lane, three rows deep
doit dashboard --lane tasks    # one lane, ten rows deep
doit dashboard --json          # the lane model, for a script
doit sources list              # which apps it asks, and whether each answers
doit sources contract          # the lane document a source may print, so it needs no adapter
```

Each lane is ordered by the app that owns it, and no lane is comparable to the one beside it. An
ordering over unlike things would be invented rather than meaningful, so the dashboard ranks across
none of them. A lane of rows shows three, and tasks and learning show five. Naming one lane with
`--lane` shows ten. A grid such as the habits is a set only useful whole, so it is never cut.

The sources document declares which sources to ask, so turning one on or off is an edit, never a
release. Each source names an adapter. The service reads the adapter's app over HTTP and the
adapter shapes the answer into lanes. The adapter carries the read as well as the shape, because
the two are one contract with that app. An app with no adapter needs a release before doit can ask
it. A source declared with a `command` instead is shown as failed, saying the service runs no
command.

A source that is not configured is silent. One that is configured and fails shows its error on its
lane. A lane is never silently dropped, because a dashboard that quietly omits one reads as nothing
outstanding. The command exits 0 whatever its lanes say. A script reading `--json` checks each
lane's `status`. `doit sources list` exits 0 too, and names each source whose read failed.

The dashboard computes no schedule. It mirrors the `overdue` each backend emits, because the backend
owns due-ness. The MAINTENANCE lane shows three rows of review and practice items, and there are
usually more. `doit review due` is that lane at full depth.

The dashboard is the glance across tasks, habits, books and learning. `fleet info` is the other
pane: the development brief across repos that a coding session reads.

## `today` — only what a day can finish

```bash
doit today          # what the day still owes, above what it has had
doit today --json   # the two lists side by side, each group in the lane contract's shape
```

An outstanding count can only climb, so a lane of hundreds of unread articles reads exactly like a
lane of two overdue chores. `doit today` admits only what a day can finish.

The top list is what is still owed. Habits and pursuits show up to ten rows and every other group
three, then a row saying how many more and what to type to see them. Anything done today is off it,
however far behind its record still is. So a pursuit logged this morning is listed as done and only
as done. The list underneath names everything the day has had, grouped by where it was recorded and
in the order it happened.

Above both, one line says what today asks to hold every pace. It counts minutes across the timed
pursuits and checkoffs of the counted ones, each in its own unit and never converted into the
other. The next line counts the habits, review and practice as checklists. Under them is what is behind
beyond today, and how much more a day, in each unit, would clear it over each timescale worth
showing.

### Done is the day's share of `happened`

The done list is what `doit happened` reads, over the day, minus closed issues. An issue closed is
engineering work, which the issue tracker reports, and only `doit happened` asks for it. The section
below says where each entry comes from. A group that could not be read is said once, in the due list.

## `happened` — every record over one window

```bash
doit happened                                     # the last 7 days, to now
doit happened --since 2026-10-19 --until 2026-10-25
doit happened --since 12h --json                  # each group in the lane contract's shape
doit happened --until 2026-09-01                  # everything up to September
```

`--since` and `--until` each take a span, a day, or an RFC 3339 instant. `doit happened --help` lists
the forms. `--until` alone runs from the earliest record.

Pursuits come from the journal and the evidence cache together, so one satisfied inside its own CLI
is listed without also being typed. Every journal entry in the window counts, whether or not the
register still names its pursuit. Review and practice items come from their recorded completions.
Everything else is a `completions:` entry in the sources, asked about the same window.

A line above the groups names every record asked and how many answered. A group that answered with
nothing prints nothing, so that line is what says it was asked. A group that could not be read
always prints, with why. A backend that answered and still wrote to stderr, such as a list cut at
its own limit, leaves its group partial: the rows print under what it said. The command exits 1
when no record could be asked, and 0 otherwise.

Each entry shows its day when the window spans several, and the hour its record kept. A record that
keeps only the day lists its entries after that day's timed ones, with the hour blank rather than
shown as midnight.

Where a record says who did a thing, the entry says which of three: `by hand`, `session` for a
Claude session someone drives, or `unattended` for a scheduled run. `doit pursuits log` stamps each journal
entry the same way. An identity in a shape doit does not recognize reads `unknown`, and the line
above the groups counts it. A group whose record keeps no actor has no actor column. `--json`
carries the identity as recorded.

A `completions:` entry takes the same settings a lane source takes, and answers "what happened"
instead of "what is outstanding". Its adapter asks the app for the window itself, so the app
filters its own rows rather than shipping a backlog to be filtered here. Nothing is required. With
the block absent, `doit happened` and `doit today` list doit's own records and say nothing about
the rest.

## `review` — an entry is done when you record it

```bash
doit review due              # what is due to revisit or practice, most overdue first
doit review done <id>        # record it done today
doit review done <id> --on 2026-07-17  # record a day it was done, after the fact
doit review undo <id>        # take back its newest completion
doit review nudge-every 8h   # how often the shell's startup nudge may print
```

The review register is one of the service's documents, edited with `doit review edit`. Each item
declares what to revisit and a cadence such as `2w` or `1mo`. Its last-done date is the newest `doit review done` for it, and
nothing else. Running the command an item names does not mark it done.

A wrong completion reads exactly like the review having happened, so it has to be correctable.
`doit review undo` takes back the newest one, and the entry reads as it did before. The service
keeps the removed row, and `GET /api/v1/history/review_completions?key=<id>` reads it back with the
moment it was taken back as its `valid_to`.

The register's `practice:` block holds what you work through on a schedule, each entry with the same
fields. `doit review done <id>` records one. The startup nudge leaves practice out. `doit today`
counts it as its own checklist, so a deck of never-practiced items cannot bury the maintenance that
is due. howto carries the Labs, and a practice entry's `command` names the howto verb that opens one.

```yaml
practice:
  rg-search-code:
    description: Search code with ripgrep
    cadence: 2w
    command: howto labs show rg-search-code
```

## The service — one store, and every view computed behind it

`api/` is doit as a service: a FastAPI app over one SQLite database. It holds the three
documents a person edits — the pursuits, the review register and the sources — and the journal,
the review completions, the offered counts and the forecast readings. Every pursuit, review,
lane, forecast and source view the CLI prints with `--json` has a route that answers the same
document, computed by the same functions.

The CLI is the service's client. A command that reads asks a route and renders what it answers,
and a command that writes posts what it records, naming the machine and who ran it. A machine
logged in anywhere sees the same draw, the same journal and the same nudge schedule, because there
is one copy of each.

- **History is kept on every edit.** A document is stored as its text, comments included, and each
  entry in it is also a row of its own table. An update keeps the row it replaced, with when it
  held, in the table's `_history` twin: the SQL:2011 system-versioned temporal-table pattern, done
  with triggers. `GET /api/v1/history/pursuits?key=read` is every weight the pursuit ever had.
- **The journal is an event log, and the views are read models of it.** A write appends an entry
  and never edits one. Every view replays the log as the CLI does.
- **Apps are read over HTTP, with the service's own credential.** Each app is asked as a
  client-credentials client holding read scopes only. A source names an adapter, and the adapter
  name selects the HTTP read. A pursuit's `resolve` and `evidence` name an app, a path and a query,
  and the register refuses either written as a command:

  ```yaml
  resolve:
    app: icb
    path: /tasks
    query: {limit: 3}
  ```

  A read that fails becomes the reason on its lane or row, and the service logs it at error as
  `source_failed` with the source's name.
- **A forecast is taken on an interval inside the process**, every `DOIT_API_FORECAST_INTERVAL`.

Every `/api/v1` route answers a person's login, verified as an RFC 9068 access token from the
issuer. `/health`, `/ready` and `/.well-known/oauth-protected-resource` answer anyone.

The image is built from the repository root, because the service runs the root package:

```bash
docker build -f api/Dockerfile -t doit-api .
```

It reads its configuration from the environment and nowhere else:

| Variable | Holds |
| --- | --- |
| `DOIT_API_ISSUER` | The OIDC issuer whose tokens the API accepts. Required. |
| `DOIT_API_PUBLIC_URL` | The URL clients reach the API at. Required. |
| `DOIT_API_DATABASE` | The SQLite file. The image sets it to a path on its data volume. |
| `TZ` | The zone "today" is computed in: the person's own. |
| `DOIT_API_FORECAST_INTERVAL` | How often a forecast is taken, as a whole number and one of `m`, `h` or `d`. Optional. |
| `DOIT_API_DRAW_LIFETIME` | How long a draw keeps answering `doit next`, in the same form. `15m` when unset. |
| `DOIT_API_<APP>_URL`, `DOIT_API_<APP>_CLIENT_SECRET` | Each app the service reads, named as in `Settings.from_environment`. |
| `DOIT_API_GITHUB_CLIENT_ID`, `DOIT_API_GITHUB_PRIVATE_KEY` | The GitHub App the pull request lane reads through. |
| `DOIT_API_CLICKHOUSE_URL`, `_USER`, `_PASSWORD` | The account that reads desk check results. |

An app, GitHub or ClickHouse left unset is not an error at startup. Its lanes say which variable
is missing, so the service runs while a credential is on its way.

### On Kubernetes

Each release pushes `deploy/` to `oci://ghcr.io/datapointchris/doit/manifests:<version>`, with the
image named by digest. It runs the API as a StatefulSet of one behind the Service `doit-api`,
because a SQLite file takes one writer. Its claim `state-doit-api-0` asks the cluster's default
storage class for 1 GiB.

The cluster supplies what the base leaves out:

- A ConfigMap and a Secret, both named `doit-api-settings`, holding the variables above. Each
  credential goes in the Secret.
- A Secret named `doit-api-github-app`, holding the two `DOIT_API_GITHUB_*` variables. It is
  optional. A pod started before it exists reads it once restarted.
- A route to `doit-api` reaching `/.well-known/oauth-protected-resource` as well as `/api/v1`, at
  the address `DOIT_API_PUBLIC_URL` names.
- Each NetworkPolicy the pod needs to reach the issuer and its sources.

The pod runs as uid 10001 under the restricted Pod Security profile, on a read-only root
filesystem.

The release signs the image and the manifests with Sigstore, as this repository's release workflow
on `main`. Check a version before applying it:

```bash
cosign verify \
  --certificate-identity https://github.com/datapointchris/doit/.github/workflows/release.yml@refs/heads/main \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/datapointchris/doit/manifests:<version>
```

## Where it writes

The service holds the documents and every record. A machine keeps these of its own, and the service
needs none of them.

- **Config** is `$XDG_CONFIG_HOME/doit/config.toml`. It names the service, its issuer and this
  machine's client, which `doit auth login` writes. The login's tokens are in the system keychain.
- **State** is `$XDG_STATE_HOME/doit/`. It holds any edit the service never took.
- **Cache** is `$XDG_CACHE_HOME/doit/`. It holds what the shell's completion and startup gate read
  without starting Python or asking the service. Each file is rewritten by the command that fetches
  it.
