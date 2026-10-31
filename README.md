# doit

What to do now, and everything that decides it.

Every other personal CLI here stewards records. icb holds tasks, habits, books and projects.
learning holds tracks, meso holds workouts, and nomad holds trips. doit is the one that consumes
them and points somewhere: what is outstanding, what to do next, what is due to revisit, what to
practice, and how to use the tool once you are there.

doit owns no domain record. Which book is next, which habit is due and when a task is late are
answered by the CLI that owns the domain, and doit lays the answer out. A change that has doit
deciding a domain rule belongs in that CLI instead. The cards, Labs and tool registry doit reads
live in [terminal-library](https://github.com/datapointchris/terminal-library), so writing one
needs no release of this repo.

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

uv fetches the Python doit needs. git clones the cards. fzf is the picker behind `find`, `choose`
and `labs choose`. atuin is optional: without it, shell history comes from this machine's zsh
history and answers only for this box.

## Starting from scratch

doit runs on defaults alone. Every file it reads is optional, and a surface missing its file says so
and names the command that starts one.

```bash
doit content status          # clones terminal-library on first use, then says where and which setting chose it
doit labs due                # proves the cards arrived: every Lab with a cadence, since none is practiced yet

doit sources example         # an annotated sources.yml, printed, writing nothing
doit sources edit            # writes that example where doit reads it, if no file is there, then opens $EDITOR
doit sources list            # every configured source, and whether it answers
doit dashboard               # every lane, each ordered by the app that owns it

doit pursuits edit           # writes an annotated pursuits.yml, if none is there, then opens $EDITOR
doit pursuits list           # proves the register parsed: each pursuit, its weight, its goal, when it is due
doit next                    # everything owed, then the rest of the screen drawn by weight
doit pursuits log            # record one; whatever you leave out is asked for

doit review edit             # writes a one-item review register, if none is there, then opens $EDITOR
doit review due              # what is due to revisit, most overdue first

eval "$(doit shell init zsh)"         # in .zshrc: a nudge about due items, gated by doit review nudge-every
eval "$(doit shell completion zsh)"   # in .zshrc: completion for the verbs and your pursuit names

doit today                   # reads every file above at once: what the day still owes, and what it has had
doit happened --since 7d     # what every record says happened over the week, and who did it
```

The first run clones terminal-library into `$XDG_DATA_HOME/terminal-library/`. A command asking
for `--help` clones nothing. doit refuses to clone into a directory that already holds files,
because deciding whose files win is not its call. A failed clone only warns, because the draw, the
dashboard and the registers need no cards. With no checkout, `doit content sync` prints the
`git clone` to run by hand.

Each `edit` above writes its template only where no file exists, and never rewrites one.
Everywhere else doit only reads its config, so your comments and layout survive. The templates name
real commands as examples, such as `icb overview --json`. A configured source whose command is not
installed costs one line on the dashboard, not the run.

`$DOIT_JOURNAL_DIR` and `$DOIT_PURSUITS` must name a path that exists. doit refuses one that does
not, naming the variable. A missing default is a fresh machine and reads as empty. A missing named
path is a share that has not arrived, and reading it as empty would let a scheduled job run on no
data and exit 0.

## The grammar

The noun comes first and the verb last: `doit review due`, `doit labs done <lab>`,
`doit forecast run`. The draw and the two things done to its answer sit at the root as `next`,
`log` and `skip`. They act on what was just offered, while `pursuits` manages the file that
produced it. The other single-verb reads sit at the root beside them: `dashboard`, `today`, `find`,
`choose`, `show` and `launch`.

Every node prints its help when given no arguments, so the tree can be walked one word at a time
without a cryptic error. A usage error exits 2. `--no-input` never prompts, and fails naming the
flag that would have answered.

`doit update` brings doit itself up to date, and `doit content sync` brings the cards. They are two
words because they are two things, and one word would make "did you update?" ambiguous.

## Which surface answers which question

| Ask | Surface | Reads |
| --- | --- | --- |
| What should I do now | `next` | `pursuits.yml`, the journal, each pursuit's `resolve` and `evidence` commands |
| What is outstanding anywhere | `dashboard` | every `sources:` entry in `sources.yml`, plus doit's own review, Labs and kit rows |
| What does today still want, and what has it had | `today` | the same sources, every `completions:` entry, the journal, the review and Labs state |
| What happened over a window, and who did it | `happened` | every `completions:` entry, the journal and the evidence cache, the review and Labs state |
| Is each goal getting what it asked for | `pursuits drift` | the journal and how often each pursuit was offered |
| Would this register fit in my days | `forecast run`, `forecast trend` | the register and the journal run through the real draw, then each stored forecast against the journal |
| What is due to revisit | `review due` | `register.yml` and shell history |
| What is due to practice | `labs due` | the Labs in terminal-library, and `labs-state.json` |
| How do I use this tool | `show`, `find`, `tools show` | the registry and cards in terminal-library, your shell's functions and aliases |
| What was that command | `choose` | the same index, ending on the invocation |
| What do I own and never use | `kit unused` | the same index, joined to shell history |
| Which apps does doit ask | `sources list` | `sources.yml`, running each command |
| Have I committed the card I wrote | `content status` | the terminal-library checkout |

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
the others. The annotated `pursuits.yml` that `doit pursuits edit` writes explains every field.

`doit pursuits log` writes through to the app that owns the item. It offers to run the pursuit's `on_log`
command, such as completing the task it resolved to. `--yes` runs it without asking, and
`--no-write` logs only.

### A pursuit declares its goal, and its weight only orders

A pursuit declares at most one pace. `cadence: 3d` asks for one occurrence every three days, in
whole days only, so twice a week is `3d` or `4d`. `weekly_minutes: 240` asks for four hours a week
and makes the pursuit measured in time. `doit pursuits log` asks a timed pursuit how long it took and never
asks a counted one. `weekly_minutes:` is what a week asks for. `doit pursuits log --minutes` is how long one
sitting took. A timed pursuit is paid only by what is logged that way. An app reports which day
something happened and never how long, so the register refuses `evidence` or `evidence_files` on a
pursuit measured in minutes.

A weight never moves a pace. It orders what is owed, heaviest first, and when less than a screen is
owed it draws the rest. So whether a pursuit is getting enough is answered by its own goal alone,
whatever the rest of the register weighs and however much else got logged that week.

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

A pursuit whose `resolve` command matches several rows offers the first three, stacked under its
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

doit knows nothing about which apps exist. `$XDG_CONFIG_HOME/doit/sources.yml` declares which
sources to ask and how each one's output becomes lanes. Every backend already speaks `--json`. A
source that prints doit's lane document names its command and needs no adapter, so adding one is an
edit, never a release. A source that prints its own model names an adapter in doit instead. The
adapter carries the command as well as the shape, because the two are one contract with that tool.
A `command` written beside an `adapter` is reported and ignored.

A source that is not configured is silent. One that is configured but missing gets a single line.
One that runs and fails shows its error. A lane is never silently dropped, because a dashboard that
quietly omits one reads as nothing outstanding. The command exits 0 whatever its lanes say. A script
reading `--json` checks each lane's `status`.

`doit sources list` exits 3 when an installed tool rejects a source's command as a usage error, and
names the tool's own complaint. A tool that is absent, logged out or slow exits 0. A scheduled run
of it therefore fails only on a break that needs a person.

The dashboard computes no schedule. It mirrors the `overdue` each backend emits, because the backend
owns due-ness. The MAINTENANCE lane shows three rows of review and Labs, and there are usually more.
`doit review due` and `doit labs due` are those lanes at full depth.

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
other. The next line counts the habits, review and Labs as checklists. Under them is what is behind
beyond today, and how much more a day, in each unit, would clear it over each timescale worth
showing.

### Done is the day's share of `happened`

The done list is what `doit happened` reads, over the day. The section below says where each entry
comes from. A group that could not be read is said once, in the due list.

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
register still names its pursuit. Review items and Labs come from their own state files, which keep
only each item's latest date. Everything else is a `completions:` entry in `sources.yml`, asked
about the same window.

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
instead of "what is outstanding". An argv part written as `{since}` or `{until}` becomes that end of
the window as an RFC 3339 instant, so a backend filters its own rows rather than shipping a backlog
to be filtered here. Nothing is required. With the block absent, `doit happened` and `doit today`
list doit's own records and say nothing about the rest.

## `review` and `labs` — due is observed, not declared

```bash
doit review due              # what is due to revisit, most overdue first
doit review done <id>        # counts too, and the later of it and the observed date wins
doit review nudge-every 8h   # how often the shell's startup nudge may print
doit labs due                # the Labs due to practice
doit labs show <lab>         # open one to read while you work in another pane
doit labs done <lab>         # mark it practiced, advancing its schedule
```

The review register is `$XDG_CONFIG_HOME/doit/register.yml`. Each item declares what to revisit and
a cadence such as `2w` or `1mo`. An item is done when something shows it was done: the command it
names appearing in shell history, or a date in another tool's state file. Nothing depends on
remembering to type `doit review done`, because an item you did and never reported reads exactly
like one you never did.

History comes from atuin, which records the machine each command ran on and syncs between machines.
So an item done at one desk counts at the other. Work that is per-machine declares
`observe: {scope: machine}`, and only runs on that box answer it. A command that opens work rather
than doing it, such as a picker, declares `observe: false`. Otherwise abandoning it halfway counts
as having done it.

A Lab is one markdown file under `labs/` in terminal-library. Practicing one is recorded by
`doit labs done`, never observed. A Lab with no cadence is practice on demand and never comes due.

## `find`, `show`, `choose` — one index over everything you own, rendered by whoever owns it

```bash
doit find rg                 # fuzzy-match a line, press Enter, get how to use it
doit show rg                 # the same page, when you already know the name
doit choose                  # the same picker, printing the invocation instead of opening it
doit launch                  # doit's own areas, then every tool you own
doit tools list              # the registry, grouped by category
doit workflows show <card>   # one reference card
```

The index is one row shape over every collection you own: the tool registry, the workflow cards,
annotated shell functions, aliases, git aliases, forgit's shortcuts, the tmux keybindings, and the
zsh keys your config binds. `--source` narrows a search to one of `tool`, `workflow`, `func`,
`alias`, `git`, `forgit`, `tmux` or `zsh`.

Every row carries its invocation, not only its name. A registry key is a package name as often as a
command: `ripgrep` installs `rg`, and `git-delta` installs `delta`. A row keyed by name alone would
name something you cannot type.

doit renders no subject itself. Each lens hands off to whatever owns it, because a second renderer
is a second thing to keep in step with the first. fzf is the picker, shelled out to rather than
rebuilt.

`$(doit choose)` works in any shell. In zsh, `eval "$(doit shell widgets zsh)"` defines a widget
that lands the pick on the prompt. Bind it to a chord of your choosing, such as
`bindkey '^X^D' doit-choose-widget`, because doit cannot see what your keymap already holds.

## `kit` — what you own, against what you reach for

```bash
doit kit unused                # what you own, can run, and never do
doit kit usage                 # how often you reach for each thing you own
doit kit unresolved            # rows naming something this machine cannot run
doit kit digest export         # write this machine's usage table where digest run reads it
doit kit digest show           # the last reading, without spending another
```

Usage is shell history folded per row of the index: a count and a last-run date. One parser reads
that history for all of doit. `review` asks it when an item was last done, and `kit` folds the same
read into counts. A second parser would answer a subtly different question, and then `review` and
`kit` would disagree about whether you had used `rg`.

`kit unresolved` turns a row that goes nowhere into a list you can fix, rather than something you
rediscover on every search. Shell integrations defined at runtime, such as `z` and `nvm`, appear on
it without being rot.

`doit kit digest run` reads every exported usage table with claude and stores what it says. It is
one model request, and fleet's scheduler runs it. It sends the aggregated table and never a command
line. It reads no shell history of its own, because it may run where no shell history is yours. It
takes each host once, from the table whose history for that host runs latest, and a table that will
not read stops the run before the request.

## `content` — the cards live in terminal-library, never in this repo

```bash
doit content status          # where the checkout is, which setting chose it, and what is uncommitted
doit content sync            # pull now, and clear a failure the background pull reported
doit content edit <id>       # open a card or Lab in $EDITOR, wherever it lives
doit workflows new <name>    # write a card into the checkout and open it
doit labs new <name>         # scaffold a Lab there and open it
```

Cards, Labs and the tool registry are content, not code. They are authored far more often than doit
is released, so they live in terminal-library, checked out at `$XDG_DATA_HOME/terminal-library/`.
Writing a card and having it are the same act on every machine.
The library is named for itself rather than for doit, because doit is one reader of it.

Every command you type, apart from a help screen and `content` itself, pulls the checkout in the
background, detached and never waited on. The pull is `--ff-only`, so it refuses rather than
merging, and git declines to overwrite a modified file. An unfinished card is never the price of a
sync. A pull that fails is reported on the next run, with `doit content sync` to retry.

The path resolves in three rungs: `$DOIT_LIBRARY_DIR`, then `library_dir` in
`$XDG_CONFIG_HOME/doit/config.toml`, then the default. A machine that authors cards points one of
them at a checkout kept where its git lives, so writing a card and reading it stay one file.
`config.toml` is optional, and a machine keeping the library at the default needs no file saying
so.

## Where it writes

Content, config and state are three different things, kept in three places. A cache sits beside
them, holding nothing a recompute cannot rebuild.

- **Content** is the terminal-library checkout. doit writes there when it pulls, and when you author
  a card or a Lab.
- **Config** is `$XDG_CONFIG_HOME/doit/`: `pursuits.yml`, `register.yml`, `sources.yml` and the
  optional `config.toml`. It is hand-edited and only read, apart from the template an `edit` writes
  where no file exists. It is never in either repo, because it is personal and both repos are
  public.
- **State** is `$XDG_STATE_HOME/doit/`, or `$DOIT_JOURNAL_DIR` where a box reads a share somewhere
  else. The journal, the offer counts, the usage tables and the forecast and digest readings are
  one file per machine, named for it, because a sync layer cannot merge two machines writing one
  file.
- **Cache** is `$XDG_CACHE_HOME/doit/`. It holds the cached draw, and the pursuit names completion
  reads without starting Python.
