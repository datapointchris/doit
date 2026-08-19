# doit

What to do now, and everything that decides it.

Every other CLI on this machine stewards records — tasks, books, learning tracks, workouts, trips.
`doit` is the only one that consumes them and points somewhere: what is outstanding, what to do
next, what is due to revisit, what to practice, and the reference for actually doing it.

## What it does

```bash
doit next             # what to do now, drawn from weights you declared
doit dashboard        # every lane, unranked — what is outstanding across everything
doit review due       # what is due to revisit, on a cadence
doit labs due         # hands-on practice that is due
doit workflows list   # the reference cards
doit claude skills list # the Claude skills you own, and when each fires
doit tools show <n>   # what one tool is, and what to type
doit kit unused       # what you own, can run, and never reach for
doit kit remind       # resurface one of them, a lens at a time
doit log              # record what actually happened
```

Bare `doit` prints help, and so does every namespace under it — you can walk down one token at a
time and never hit a cryptic error.

### Which of the three to type

`doit next` and `doit dashboard` are separate systems, and they meet only on the dashboard.

- **`doit next`** ranks across everything, because you declared the ordering as a weight per
  pursuit. It hands you five things and expects one back through `doit log`.
- **`doit dashboard`** ranks across nothing. Each lane is ordered by whichever app owns it, three
  rows deep, and no lane is comparable to the one beside it.
- **`doit review due`** is one of those lanes at full depth. Reach for it when MAINTENANCE is the
  line that caught your eye — the dashboard shows three of its rows and there are usually more.

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
