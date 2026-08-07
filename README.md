# doit

What to do now, and everything that decides it.

Every other CLI on this machine stewards records — tasks, books, learning tracks, workouts, trips.
`doit` is the only one that consumes them and points somewhere: what is outstanding, what to do
next, what is due to revisit, what to practice, and the reference for actually doing it.

**Status: early.** The command surface below is the target, not what is built.

## What it will do

```bash
doit next             # what to do now, drawn from weights you declared
doit dashboard        # every lane, unranked — what is outstanding across everything
doit review due       # what is due to revisit, on a cadence
doit labs due         # hands-on practice that is due
doit workflows list   # the reference cards
doit log              # record what actually happened
```

Bare `doit` prints help, and so does every namespace under it — you can walk down one token at a
time and never hit a cryptic error.

## Sources are configuration

`doit` knows nothing about which apps exist. `~/.config/doit/sources.yml` declares each source's id,
the command to run, and how its output maps to a lane; every backend already speaks `--json`. Adding
a source is an edit, never a release.

A source that is not configured is silent. One that is configured but missing gets a single line.
One that runs and fails shows its error. A lane is never silently dropped.

## Content

Cards, Labs, and the register are content, not code — they live in their own repo, cloned into
`$XDG_DATA_HOME/doit/` and updated by `doit content sync`. It stays a git checkout at the installed
path, so writing a card works on any machine and no release stands between writing one and having
it. A machine that authors cards can point that path at a checkout of its own; doit resolves it
either way and never needs to know which it got.
Personal registers (`pursuits.yml`, the review register) stay in `$XDG_CONFIG_HOME/doit/` and are
never in this repo.
