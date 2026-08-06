# CHANGELOG


## v0.1.0 (2026-08-06)

### Chores

- Make task lint green without silencing a real finding
  ([`853a892`](https://github.com/datapointchris/doit/commit/853a89218fee8f133199dc910069433b5bd27d20))

bandit failed on 35 findings, one of which was worth answering. B602 on the review nudge is
  suppressed at its line: a register entry is hand-authored config and may hold a pipeline, so
  reaching a shell is the feature.

The rest fired on what doit is — importing subprocess, calling other CLIs by name, drawing at random
  — so they are skipped in config, the same set safekeep and syncer already carry. The lone assert
  bandit flagged is gone instead of skipped: is_lane_document returns a TypeGuard, so mypy narrows
  the payload without a runtime check that vanishes under -O.

- Trim .gitignore to the generated baseline
  ([`3d69294`](https://github.com/datapointchris/doit/commit/3d69294154ecf03ae8343e34428d74aaafde9553))

.venv, __pycache__ and the tool caches were hand-added and are dead weight: uv, pytest, ruff and
  mypy each write a self-ignoring .gitignore into their own cache dir, and uv installs the package
  into .venv so no __pycache__ appears in src at all.

### Documentation

- Correct the command surface to bare-shows-help
  ([`0e19858`](https://github.com/datapointchris/doit/commit/0e19858ea72f50d497b0632bdf9ee55b6a550982))

The draw takes --explain and --reroll, which is cli-design.md's named disqualifier for a bare
  default — flags on the root callback mean the default is a command wearing the root's clothes. It
  is doit next, and every namespace under it prints help bare too.

- Drop citations a reader of a public repo cannot open
  ([`0b6b957`](https://github.com/datapointchris/doit/commit/0b6b957bf2bc9e69356a44a7f625c6f61cbd08b9))

Four comments pointed at ~/dev/standards and ~/dev/vision.md, which resolve only on my own machines.
  The reasoning each one carried is worth keeping, so it stays inline; only the unopenable link
  goes.

### Features

- Bootstrap doit
  ([`a7d5657`](https://github.com/datapointchris/doit/commit/a7d565772df37d2747b9a58677826f7dcbdbd79f))

The attention layer extracted from dotfiles: what is outstanding, what to do now, what is due to
  revisit, what to practice, and the reference for doing it.

Registry entry first, then the forge dies for planning, gitignore, pre-commit and CI. Carries a
  --version entry point and its tests so the install path works end to end before any port lands.

- Clone the content repo on first use
  ([`7f287be`](https://github.com/datapointchris/doit/commit/7f287be94c775ba4264fed70aef23c329ef78b83))

doit-content now exists, private, holding the 58 cards and 16 Labs. doit clones it into
  $XDG_DATA_HOME/doit on first run, so a new machine needs no setup step anyone has to remember —
  including the work box, which is outside the Syncthing fleet and was the reason a repo won over a
  synced folder.

The remote is named here after all, reversing what this module said yesterday. Cloning on first use
  requires knowing where from; `sync` afterwards still asks git, so a fork or a moved remote keeps
  working without this constant being right forever.

Every later invocation costs one exists(). A failed clone warns and carries on — the draw, the
  dashboard and the register need no cards, and refusing to run because a clone failed would be the
  tail wagging the dog. A directory holding files git did not put there is left alone rather than
  adopted.

- Fold workflows in, collapsing two card browsers into one
  ([`7f0b449`](https://github.com/datapointchris/doit/commit/7f0b449621c5a9f44b74d0e9bc0282fb60c0bbc4))

apps/common/workflows was 260 lines of bash doing what doit.labs already did in Python: strip YAML
  frontmatter, take the title from the first H1, render the body through bat. doit.cards is now that
  shape, and both collections read it — Labs loses its private copies.

`new` no longer walks a symlink backwards. The bash version had no idea where its own content lived,
  so it listed the installed directory for any symlink, resolved it to the dotfiles source, wrote
  there, and linked forward again. The cards directory is a git checkout, so it writes the file the
  reader reads. Same deletion the Labs port made, same reason.

`search` and `learn` do not come across. Searching cards is `doit find --source workflow` — a card
  is one row in the federated index beside tools, skills, aliases and tmux keys, and scoping that is
  a filter rather than a second search. `learn` was `random` with paging off and has no live caller:
  the only references are a registry example and a MOTD block that already lives in git lost-found.

Still open on this item: the federated search itself, and `doit launch`.

- Own the shell couplings and the content checkout
  ([`a5d2def`](https://github.com/datapointchris/doit/commit/a5d2def54824b7643d85c115aa7a8426a13af430))

The doit half of dissolving the dotfiles couplings. Both blocks that lived in dotfiles referred to
  doit's internals — an eleven-line startup gate testing `-x ~/.local/bin/menu-review`, and a
  completion function reading a cache path by hand. Both broke the moment the binaries collapsed
  into one, which is the tell that a config repo was carrying implementation details because there
  was nowhere else to put them. dotfiles now needs one eval line per block.

`zsh -n` is in the tests. A generated script that does not parse breaks at shell startup on every
  machine at once, a long way from where it was written.

Completion stays a generated script rather than typer's --install-completion, which spawns the
  program on every TAB. It reads the flat names cache the draw already leaves behind, so a TAB never
  waits on a Python start.

CONTENT IS `doit content`, NOT `doit update`. cli-design.md reserves `update` for a tool updating
  itself, one spelling everywhere — and the content and the binary are two different things to bring
  up to date, so one word for both would make "did you update?" ambiguous. The spec said `doit
  update`; the standard wins.

Nothing here knows the remote. The checkout does, so `content sync` is a fast-forward and a missing
  checkout prints the clone command with the path filled in. A URL written into this file is a URL
  that goes stale in a repo nobody thinks to grep.

That clone line is soft-wrapped: rich had been breaking the path mid-way, and a command you cannot
  copy is not a command.

- Port labs, collapsing the source and installed decks
  ([`86a147a`](https://github.com/datapointchris/doit/commit/86a147a03079c6cdc748dec4e65ce292be98c4b9))

menu-labs becomes doit.labs. Two things go away because the extraction removed the reason for them,
  not because they were tidied.

The Labs directory is one directory now. LABS_SRC, _authoring_target, and the symlink cmd_new
  dropped into the installed dir all existed because Labs lived in dotfiles and were symlinked into
  place. The deck is a git checkout that `doit update` pulls, so authoring writes the file the
  reader reads, and cmd_edit no longer needs a two-path fallback.

`menu labs <arg>` meant "a Lab id, or failing that a tool name, in which case federate to menu's
  lens composer". That overload is what the settled surface removes: a Lab is `doit labs show <id>`,
  a tool subject belongs to `doit find`, and drilling its examples is already `doit labs flash
  <tool>`. cmd_federate and cmd_subject are dropped rather than moved — the capability survives
  across those two commands, the one composite command does not.

- Port review as the first real subcommand
  ([`4482256`](https://github.com/datapointchris/doit/commit/448225605c0535db6a7cb759687b1b6f44865e05))

menu-review becomes doit.review, dispatched from cli.py through a command table. The
  importlib-by-path harness its tests needed goes with it — a real module is a real import, so the
  fixture register is monkeypatched rather than set as an env var before the module loads.

Surface follows the settled one: bare `doit` and bare `doit review` print help, and what bare `menu
  review` used to do is now `doit review due`. An unknown command exits 2 rather than 0, which is
  the change with teeth — 0 told a caller the command had succeeded.

The register reads from the config dir now, not the data dir. Nothing is removed from dotfiles:
  menu-review still runs there against the old paths until the couplings are dissolved.

- Port the dashboard, completing the subcommand move
  ([`7bb24fa`](https://github.com/datapointchris/doit/commit/7bb24faf72cfff0a4b4270fe8032db01abeeb239))

menu-dashboard becomes doit.dashboard, the last of the four.

It keeps talking to `review` and `labs` over `--json` as subprocesses rather than calling them
  in-process now that they are siblings. The uniformity is the contract the source registry is built
  on: the moment doit's own lanes get a shortcut, a source stops being a source. The module
  docstring says so, next to the existing note about is_due_row mirroring doit.cadence rather than
  importing it.

The rendering port was verified by diff, not by reading: old and new output at COLUMNS=100 are
  byte-identical across the projects, books and upcoming lanes, including every clip and column
  boundary. The only change is the header, now a rich rule like every other view.

`clip(text, used)` became a `fitted()` helper over Text.truncate. The original passed a column width
  where clip expects the width consumed by everything else, which is arithmetic I could not derive
  from the code — the diff is what establishes the port is faithful.

Two mypy errors dotfiles never saw: isinstance() on `review.payload if review else None` does not
  narrow `review.payload`, so both branches now bind the value they tested.

The learning lane still filters status ids. The section_focus that replaces that shipped in learning
  but is not in the installed build, and the lane becomes config in the source registry anyway — so
  it moves as it is rather than growing code that item deletes.

- Port the federated index, fixing the rows that named nothing
  ([`b84166e`](https://github.com/datapointchris/doit/commit/b84166e5375e52036d562434bae24501c8c1eccd))

doit.index builds all eight collections — tools, cards, skills, shell functions, aliases, git
  aliases, forgit, tmux keys — at exact parity with the bash version: 382 rows, same per-lens
  counts.

THE FIX: every row now carries its invocation. The bash index used the registry key, so `ripgrep`,
  `git-delta` and twenty `git-forgit-*` entries rendered as rows naming things absent from PATH — 65
  of 130 tool rows. The command was in the registry the whole time, in each entry's `usage`. Reading
  it takes unresolvable rows from 65 to 11, and `ripgrep` now reads `rg [pattern] [path]` rather
  than a dead word.

`doit index unresolved` is the second half of that fix. The remaining 11 are runtime shell
  integrations (`br`, `z`, `nvm`) and genuine rot (`long-gone`-shaped entries), and a list you can
  act on is the difference between an index that decays and one that does not. Rot you rediscover on
  every search is rot nobody ever fixes.

`doit launch` offers your own tools rather than all 130: a launcher answering "what can I run here"
  with every third-party binary is answering a different question.

fzf is shelled out to, not reimplemented. `__show` and `__preview` are hidden because they are what
  a picker calls, not what you type.

- Port the shared scheduling model from dotfiles
  ([`71afd70`](https://github.com/datapointchris/doit/commit/71afd70daa4d7e72a402097c5034f083567ab625))

menucore and apppaths move whole, as doit.{cadence,state,render,journal, allocate} and doit.paths.
  The four subcommands that still import them in dotfiles port next; nothing staying behind does.

This is what the extraction was for. Three sys.path.insert(parents[2]) hacks, a package namespace at
  a config repo's root, a top-level tests/menucore/, and five PEP 723 headers each re-pinning
  pytermstyle all collapse into one package and one lockfile.

The menucore re-export barrel is not ported. It existed so five separate scripts had one import
  surface; inside a single package it is indirection, and every paragraph of its docstring was
  already stated by the module that owns it. What no single module owned — how the deterministic and
  weighted halves relate — is now the package docstring.

apppaths arrived untested. tests/test_paths.py covers the empty-string env case, which reads
  `get(VAR) or default` rather than `get(VAR, default)` on purpose: a variable exported empty is
  what a partially-initialised shell produces, and the two-argument form would resolve every path to
  the filesystem root.

- Port the weighted draw as doit next
  ([`28e13a9`](https://github.com/datapointchris/doit/commit/28e13a9ae6e525b500f7ff34e4d4911ec17ac43c))

menu-next becomes doit.pursuits — named for what it owns rather than for the verb, since a module
  called `next` shadows the builtin at every import site.

The surface splits the way the settled one says: `doit next`, `doit log` and `doit skip` sit at the
  root because they act on the answer the draw just gave, while `doit pursuits
  list|drift|dormant|edit` manages the file that produced it. `next` keeps its flags legitimately
  now that it is a named command rather than a bare default.

--explain and drift become rich Tables. They were hand-aligned f-string columns, which is the part
  of this that rich straightforwardly does better; no test asserted on their layout, so nothing
  constrained the change but taste.

load_pursuits resolves REGISTER at call time instead of binding it as a parameter default. The
  default froze at import, which is why the dotfiles tests had to set env vars before importing the
  module at all.

Two mypy errors that dotfiles never saw, fixed rather than silenced: a None-guard that only narrowed
  at runtime, and an Optional indexed after a `(x or {}).get()` check that does not narrow.

Running it against the real register caught the bug worth having a test for — `[note]` in the log
  hint was written as markup, and rich read the bracket as a style tag and dropped the argument
  silently.

- Publish a lane contract and make sources config
  ([`541923a`](https://github.com/datapointchris/doit/commit/541923a269b0922e6a7b89564a7550c7a9347dee))

The interface is at the top now, and it is symmetric: what `doit dashboard --json` emits is exactly
  what a source may emit. A tool that prints that shape appears on the dashboard with no adapter, no
  mapping and no release of doit. `doit sources contract` prints it.

doit.lanes owns the shape and is the only place that knows what a lane is. doit.sources owns which
  apps are asked — sources.yml, the one place the set of backends is written down — plus the failure
  policy, which stays code because it must not vary by source.

icb and learning become named adapters: ordinary Python that turns an app's own model into lanes
  because that app predates the contract. The contract is tried first even when an adapter is
  registered, so an app that starts emitting lanes is picked up the moment it does and its adapter
  stops being reached with nothing here changing.

REJECTED, and this is the reversal: expressing the mapping in sources.yml as paths and format
  strings. That is a template language rebuilt in YAML, it could not express a label chosen by a
  boolean without growing conditionals, and it puts the shape burden on the consumer forever rather
  than on the producer once. It was designed to satisfy "day integrates with zero code change",
  which was a generated line in a planning doc rather than a requirement.

Verified with a source doit has never heard of: a script printing a lane document renders as a DAY
  lane, and `doit sources list` reports it as "1 lanes via contract" beside "6 lanes via
  adapter:icb".

That source also surfaced a real crash. render_rows passed a style argument while appending a Text,
  which rich refuses — and only a due or overdue note carries a style, so it raised for exactly the
  rows that matter most. Nothing in the fixtures produced one until now.

--lane is no longer validated against a closed set, because a conforming source contributes lanes
  doit cannot know. An unmatched name is reported with what was actually offered.

- Version doit from its release and add self-update
  ([`d51a69b`](https://github.com/datapointchris/doit/commit/d51a69b585594247d0852b04aebe97a259d36262))

doit had no tag, so the dotfiles manifest entry the strip is about to add would have resolved to the
  default branch — the unpinned git install that release.md calls the degraded state, and that left
  syncer eight releases behind while the updater reported it current.

python-semantic-release owns the version in pyproject.toml, and __version__ now reads the installed
  distribution rather than a second constant that can only drift behind it. A checkout that was
  never installed reports 'unknown' instead of inventing a number.

pyselfupdate's [typer] extra supplies `doit update` ready-made; safekeep declines it only because it
  is argparse. The root callback notifies on every other path, which keeps one config behind both
  the notice and the command so they cannot disagree about which release exists.

### Performance Improvements

- Read doit's own lanes in-process instead of shelling out to itself
  ([`13b1fc1`](https://github.com/datapointchris/doit/commit/13b1fc11118454a962c2ce3c8e6c97103323be0d))

The dashboard called `doit review list --json` and `doit labs list --json` as subprocesses,
  justified by "uniformity over --json is the contract the source registry is built on". Measured:
  the maintenance lane cost 0.98s CPU / 0.70s wall, of which two full interpreter starts booting
  typer and rich were spent reading one local YAML file and a directory of markdown already
  reachable from the calling process. It is now 0.37s CPU / 0.39s wall — doit's own startup and
  nothing else.

The contract that actually matters is untouched. review.statuses() and labs.statuses() return the
  exact rows their --json prints, so a backend still owns due-ness and the dashboard still never
  re-derives a cadence. The source registry exists so doit need not know which *other* apps are
  installed; it does know its own modules.

`__show` becomes `doit show`. It is the composite that replaced `menu labs <tool>`, and knowing the
  name is the common case — hiding it as a picker callback was the spec talking, not the ergonomics.

### Refactoring

- Build the command tree on typer
  ([`4eabb5a`](https://github.com/datapointchris/doit/commit/4eabb5a3a72185757a69b935ca6693302d524678))

doit was the only Python CLI on the fleet not on typer. syncer, indy, relate, ypl and dectl all are;
  doit inherited pytermstyle's help_* helpers from safekeep, its sibling extraction, and hand-rolled
  the rest.

Hand-rolling was already visible as a smell: a needs_argument() helper, `return 2` in eight places,
  and bare-shows-help written out at three levels. Those are no_args_is_help=True,
  typer.BadParameter, and Click's own missing-argument handling. All three are now deleted rather
  than maintained, and `--install-completion` arrives free, which the settled surface needed anyway.

The cmd_* functions are untouched and still return int; the typer commands are thin wrappers over
  them, so the logic stays testable without a runner and the port stays a port.

pytermstyle stays for the palette in command output, shared with the bash and Go CLIs through its
  counterparts. Its help_* helpers are dropped despite rendering the full typeable invocation that
  typer's help omits: fixing that locally would make doit a sixth variant, and the defect is already
  filed against all five typer CLIs needing one shared implementation. doit now fails that rule with
  them rather than alone.

Bare `doit` exits 2, matching indy, relate and syncer.

- Render everything through rich, dropping pytermstyle
  ([`34334f7`](https://github.com/datapointchris/doit/commit/34334f76397bf9458a744ae4edf05f76198fcd02))

Adopting typer means adopting rich's display semantics whole, so keeping pytermstyle for output left
  doit half-converted: a typer help screen in one style above command output in another.

What rich replaces is arithmetic. The nudge clipped by measuring uncoloured text against
  shutil.get_terminal_size and slicing; that is now no_wrap plus overflow='ellipsis'. Column widths
  were computed on uncoloured names so escapes could not shove later columns right; rich measures
  printable width itself.

Content from a register, Lab or command is built as rich Text rather than markup strings.
  Text.append does not parse [...], so a bracket in a description cannot be eaten as a style tag —
  which the flashcard counter would have hit immediately, since it prints a tool name inside square
  brackets.

--json stays on plain print, commented at both call sites: a Console soft-wraps at terminal width,
  which would put newlines inside JSON strings and hand a consumer a parse error instead of data.
  The bat fallback stays plain for the same reason.

Colour is rich's concern now, so the tests that asserted on escapes are gone rather than kept
  passing.
