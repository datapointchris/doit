# doit

What to do now, and everything that decides it.

Every other CLI on this machine stewards records — tasks, books, learning tracks, workouts, trips.
`doit` is the only one that consumes them and points somewhere: what is outstanding, what to do
next, what is due to revisit, what to practice, and the reference for actually doing it.

## What it does

```bash
doit next             # what to do now, from the goals and weights you declared
doit dashboard        # every lane, unranked — what is outstanding across everything
doit today            # what today has had, and what it still wants
doit review due       # what is due to revisit, on a cadence
doit labs due         # hands-on practice that is due
doit workflows list   # the reference cards
doit claude skills list # the Claude skills you own, and when each fires
doit tools show <n>   # what one tool is, and what to type
doit kit unused       # what you own, can run, and never reach for
doit kit remind       # resurface one of them, a lens at a time
doit log              # record what actually happened
doit forecast run     # what the register would have you do over the next month
doit forecast trend   # what earlier forecasts predicted, against what got logged
```

Bare `doit` prints help, and so does every namespace under it — you can walk down one token at a
time and never hit a cryptic error.

### Which one to type

`doit next` and `doit dashboard` are separate systems, and they meet only on the dashboard.

- **`doit next`** ranks across everything, because you declared the ordering as a weight per
  pursuit. It shows everything behind its goal and fills the rest of five rows by weight, and
  expects one back through `doit log`.
- **`doit dashboard`** ranks across nothing. Each lane is ordered by whichever app owns it, three
  rows deep, and no lane is comparable to the one beside it.
- **`doit today`** admits only what a day can finish. An outstanding count can only climb, so a
  lane of hundreds of unread articles reads exactly like a lane of two overdue chores. Here the top
  list names everything the day has had, grouped by where it was recorded and in the order it
  happened. The list underneath is what is still owed, three to a group, then a row saying how many
  more and what to type to see them.
- **`doit review due`** is one of those lanes at full depth. Reach for it when MAINTENANCE is the
  line that caught your eye — the dashboard shows three of its rows and there are usually more.

### Done is whatever record saw it

Habits come from `icb overview`, which already reports what was ticked. Pursuits come from the
journal and the evidence cache together, so one satisfied inside its own CLI is listed without also
being typed. Review items and Labs come from their own state files. Those are the records doit
either owns or already reads.

Each entry shows the hour its record kept. Review, Labs and meso record only the day, so their
entries follow the timed ones with the hour left blank rather than shown as midnight.

Everything else is a `completions:` entry in `sources.yml` — the same settings a lane source takes,
answering "what happened today" instead of "what is outstanding". An argv part written as `{today}`
becomes the local ISO date, so a backend filters its own rows rather than shipping a backlog to be
filtered here. Nothing is required. With the block absent, `doit today` prints the records above
and says nothing about the rest.

## A pursuit declares its goal, and its weight only orders

Every pursuit declares one pace. `cadence: 3d` asks for one occurrence every three days, in whole
days only, so twice a week is `3d` or `4d`. `weekly_minutes: 240` asks for four hours a week and
makes the pursuit measured in time: `doit log` asks a timed pursuit how long it took and never asks
a counted one. `weekly_minutes:` is what a week asks for; `doit log --minutes` is how long one
sitting took.

A weight never moves a pace. It orders what is owed, heaviest first, and when less than a screen is
owed it draws the rest. So whether a pursuit is getting enough is answered by its own goal alone,
whatever the rest of the register weighs or however much else got logged that week.

Standing is one balance in whichever unit applies: what the goal asked for over the last four
weeks, less what was done in them. The four weeks slide. Standing is a reading of how the last
month went, not an account kept to the day. Three chores in one evening count as three, and twenty
minutes of reading pays twenty minutes off a week's goal. A burst carries a pursuit until it is four
weeks old, and four weeks away is owed in full. A cadence longer than a fortnight looks back two of
its own intervals instead. A pursuit paid through an app looks back no further than the app
remembers.

The window opens no earlier than the pursuit's zero point. That is written the first time doit
reads a pursuit, one interval back, so a new pursuit opens one checkoff owed and goes overdue as
time passes.

That balance is never the number on screen. Every view states standing as a date — `3d overdue`,
`due today`, `due in 2w` — and `doit.allocate` holds the conversion. The date is projected: a
pursuit paid ahead comes due the day enough of that payment leaves the window. `doit next --json`
carries the raw balance, and `doit pursuits drift` sets what each goal asked for beside what got
done.

A pursuit whose resolver matches several rows offers the first three, stacked under its name. One
choice is a decision the register made; several are a choice you make, so `doit log` asks which one
wherever the pursuit's `on_log` would complete it, and names none on Enter or when nobody is there
to ask.

`doit skip <pursuit> --for 2w` takes one out of the draw until it expires, and it owes nothing
meanwhile; `doit pursuits resume <pursuit>` ends the skip early. `doit pursuits reset <pursuit>`
moves the zero point to now, which is what to reach for after a long pause or a change of target;
with no name it moves every one, after confirming. The journal is append-only either way — both are
markers written into it, never a rewrite of what happened.

Every pursuit owing a whole checkoff is shown, however many there are. When fewer than five are
owed, the rest of the screen is drawn by weight from what is current, so a register with nothing
outstanding still says what there is rather than going blank — and a pursuit you are a whole
checkoff ahead on stays out of it.

The offered list is one list: what is owed first, heaviest first and then latest first, then what
was drawn, soonest due first. That one order is what the screen shows, what the forecast walks
top-down, and what `rank_offered` counts in a journal entry.

## Sources are configuration

`doit` knows nothing about which apps exist. `~/.config/doit/sources.yml` declares each source's id,
the command to run, and how its output maps to a lane; every backend already speaks `--json`. Adding
a source is an edit, never a release.

A source that is not configured is silent. One that is configured but missing gets a single line.
One that runs and fails shows its error. A lane is never silently dropped.

## Due is observed, not declared

A cadence item is done when something shows it was done — the command it names appearing in shell
history, or another tool's state file recording the work. `doit review done <id>` still works and
still counts, but nothing depends on your remembering to type it, because an item you did and never
reported reads exactly like one you never did.

History comes from atuin, which records the machine each command ran on and syncs between them, so
an item done at one desk counts at the other. Work that is genuinely per-machine says
`scope: machine` and is only answered by runs on that box.

## Content

Cards, Labs and the tool registry are content, not code — they live in
[terminal-library](https://github.com/datapointchris/terminal-library), cloned into
`$XDG_DATA_HOME/terminal-library/` and updated by `doit content sync`. It stays a git checkout at
the installed path, so writing a card works on any machine and no release stands between writing one
and having it. A machine that authors cards can point that path at a checkout of its own; doit
resolves it either way and never needs to know which it got. The library is named for itself rather
than for doit because doit is one reader of it.

Personal registers (`pursuits.yml`, the review register) stay in `$XDG_CONFIG_HOME/doit/` and are
never in this repo.
