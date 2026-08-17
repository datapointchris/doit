# CHANGELOG


## v2.9.1 (2026-08-17)

### Bug Fixes

- **update**: Hand the release lookup a gh credential
  ([`29d8d35`](https://github.com/datapointchris/doit/commit/29d8d3580d022c543c0730f63b38fe3f7f090268))

An unauthenticated caller gets 60 GitHub API requests an hour per IP, shared with every other
  anonymous tool on the machine. Running out surfaces as a failed update rather than as a quota, and
  this repo being public means nothing about the limit.

token_func rather than token: the config is built at import and the notify gate resolves it on every
  invocation to decline most of them, so an eager call would put a gh spawn in front of every doit
  command.


## v2.9.0 (2026-08-17)

### Bug Fixes

- **kit**: Resolve names against the whole deployed shell tree
  ([`1db9942`](https://github.com/datapointchris/doit/commit/1db99428b7174b9ee803b44c93d3ee2be7f56d7b))

Resolution read two files plus a flat <platform>.sh. The shell tree is nested — pkg/<name>/,
  os/<name>/ — and no flat platform file is deployed, so a package manager's shortcuts were
  invisible: five functions and seven aliases missing from the index here, and two library functions
  reported as rot they are not.

Resolvability now matches a definition rather than the #@ annotation. The annotation is the index's
  opt-in and says a function is worth offering; whether the shell defines the name is a different
  question an unannotated helper answers yes to.

unresolved() builds only the four lenses it checks. Reading sixty markdown cards and asking zsh for
  its keymap was most of what it cost.

### Features

- **dashboard**: Put unresolved index rows on their own lane
  ([`623b56d`](https://github.com/datapointchris/doit/commit/623b56d99099b3b44626918245e39ade52d2674c))

Rot in the kit was answerable only by remembering to run `doit kit unresolved`, and the register can
  only say to go and look monthly. The lane says what is wrong the moment it is wrong, and says
  every row resolves when there is nothing to report.

The handle is `doit show`, not the invocation — the row is on the list because running it fails, so
  the act is judging the entry.

One producer feeds both readers. A second shaping would answer a subtly different question within a
  month, and then the lane and the command would disagree about whether the index is clean.


## v2.8.0 (2026-08-17)

### Features

- **dashboard**: Add the meso training lane and a standing line
  ([`f197d74`](https://github.com/datapointchris/doit/commit/f197d742b9bd2969d918c8d9ffc3852185b11f2c))

meso was the one installed CLI exposing a model nothing read. It has no overview verb, so the lane
  is built from cycles: rows are workouts rather than cycles, because a cycle is a plan and a
  workout is the thing you can go and do. What was already performed stays out of it and feeds the
  train pursuit's evidence instead — a session is the opposite of outstanding.

next and dashboard now close with how many completions would bring the banking pursuits back to
  current. A count rather than a duration, because it names the action: four away is four chores,
  and that is something you can decide to do tonight. Pursuits without credit are excluded, where
  being late is a scalar with no countable units behind it.

The dashboard guards its read of the register. A register that refuses to load is a deliberate
  failure mode and must cost one line rather than the whole glance.


## v2.7.0 (2026-08-17)

### Features

- **pursuits**: Bank credit and carry debt on a rate cadence
  ([`f036509`](https://github.com/datapointchris/doit/commit/f03650989a806a4a95018fb6458f023da1744c45))

A cadence stated one rhythm: done yesterday or not. Doing three chores in an evening counted as one,
  and missing five days counted the same as missing one, because urgency reads only the newest entry
  and saturates.

A pursuit may now declare credit, which turns its cadence into a rate. Each completion advances the
  satisfied-through point by one interval from wherever it already stood, so a burst pays days
  forward and a missed stretch is owed rather than waved through. One completion clears one.

Bounded in both directions by the declared window. Ahead, so a spring clean cannot silence a daily
  prompt for a month; behind, because thirty owed chores is a number nobody acts on and a backlog
  only directs attention while it is payable.

days_since stays the honest elapsed time and only urgency reads the banked value, so what gets
  displayed as last-done is unchanged. Pinning reads the banked position too — reading the cadence
  alone pinned a pursuit that had already been paid forward.


## v2.6.0 (2026-08-17)

### Chores

- **pyproject**: Raise assertion verbosity instead of test verbosity
  ([`3bbade3`](https://github.com/datapointchris/doit/commit/3bbade3911129cc9d28da6c67ee8a3f6abdda02d))

A failing assertion truncated its diff and printed "use -vv to show", so the reader re-ran the whole
  suite to see it. addopts = "-vv" answered that by raising test-list verbosity as well, which is a
  different question: a green run printed a line per test and said nothing. verbosity_assertions
  raises only the half that was wanted.

Written by the forge pyproject die.

### Features

- **pursuits**: Derive last-done from the apps that own the act
  ([`2a7d15b`](https://github.com/datapointchris/doit/commit/2a7d15bad56bf83ecbb6e0934346a14cbb9e0c8c))

The journal only ever knew what was retyped into it, so a pursuit satisfied constantly through its
  own CLI read as never done and kept being offered. The ceiling on what the draw could know was
  whatever got logged twice.

A pursuit now declares evidence beside resolve: where resolve asks what to do, evidence asks the
  same backend whether it already happened. The later of the journal and the app wins, so
  hand-logging still carries the pursuits no app can see.

Reads are cached on a half-hour TTL and gathered concurrently, and a backend that cannot be reached
  keeps its previous answer rather than dropping to never — the two are indistinguishable in the
  draw, so 'pursuits evidence' names which is which.


## v2.5.1 (2026-08-15)

### Bug Fixes

- Name the icb tasks verb that exists
  ([`4373053`](https://github.com/datapointchris/doit/commit/43730534351da873e258f01fadb7da09a8cab6e2))

The shipped register template and the dashboard's tasks hint both named 'icb tasks todo'. The verb
  is 'list', so a fresh 'doit pursuits edit' wrote a register whose chores row could never resolve,
  and the dashboard pointed at a command that exits 2.

- **next**: Report what a failed resolve said
  ([`e58f454`](https://github.com/datapointchris/doit/commit/e58f454e7c5798076ef77c81b3768ae15be32393))

A row whose backend failed printed '(icb unavailable)' regardless of what the backend actually said.
  A register naming a verb the CLI dropped is indistinguishable from a service that is down, so the
  row sent you to check a tool that was answering fine.

The draw cache kept the failure for its whole window, so correcting the register left the dead
  message on screen and only a reroll cleared it — which changes the draw the cache exists to hold
  still. Failed rows are now asked again on a cache hit and the repaired result written back.

A cached failure is truthy and carries no id, so 'doit log' treated it as a resolved item, skipped
  the write-through to the owning CLI, and still reported the pursuit logged.


## v2.5.0 (2026-08-14)

### Features

- **dashboard**: Adapt pull-requests rather than take its lane document
  ([`49ccef2`](https://github.com/datapointchris/doit/commit/49ccef2c5bb3aa9707dd93900fb4331aa36d8acb))

pull-requests prints open PRs as JSON and nothing else now. Shaping that into a lane is doit's
  question, so doit answers it.

The old arrangement had a --lane flag over there emitting this file's row shape. Its other consumer,
  the prs picker, renders the same data completely differently — icons, colours, an action menu — so
  the shape was never shared, only located in the wrong place.

Also drops the claim that every adapter is a migration that has not happened. It was a line someone
  wrote confidently, not a measurement, and it had adapters listed as debt when they are where lane
  shape belongs. The conforming path stays available and is now described as what it is: the right
  choice only for a tool whose whole reason to exist is feeding this dashboard.


## v2.4.1 (2026-08-13)

### Bug Fixes

- **index**: Name zsh keys the way a keyboard does
  ([`ad61ee8`](https://github.com/datapointchris/doit/commit/ad61ee8c1ea3af10e69bb326212543da07574c35))

A row displaying ^H could not be found by typing ^H. In fzf's extended syntax a leading caret
  anchors the query to the start of the line, so every control binding matched nothing and every one
  of them was unsearchable by its own key.

Keys now read Ctrl-X Ctrl-R, Alt-c, Tab — the notation the tmux card already uses. The raw sequence
  moves to the card's arrow as a whole bindkey line, which is both off the display line and the
  thing you need to rebind the key.

Found by running the index against generated queries: the 'search by its own name' family missed on
  5.9% of rows, all of them zsh.


## v2.4.0 (2026-08-13)

### Features

- **index**: Add a zsh keybinding lens
  ([`abb63b6`](https://github.com/datapointchris/doit/commit/abb63b6b35a45d234a571107ed0287c2322eb647))

The keys most worth finding are bound by plugins at load time — fzf's ^T and ^[c, atuin's ^R — so
  they appear in no config file any lens could parse. The lens asks zsh itself, for the reason the
  git lens asks git.

A raw keymap is mostly vi motions, which discover nothing, so the live keymap is diffed against a
  stock 'zsh -f'. What survives is exactly what this machine's config added, and the filter needs no
  denylist to maintain.

Narrowed again to the keymaps main actually points at: a plugin binds into both editing modes and
  only one is live, so the dormant copy would offer keys that do nothing when pressed.


## v2.3.0 (2026-08-13)

### Features

- **paths**: Resolve the library in three rungs
  ([`14577ee`](https://github.com/datapointchris/doit/commit/14577ee04b63708bb1c42d37dcb38f6b7c95732e))

The library is the one path here doit does not own, and it had one rung: a variable added so a
  second checkout could be a pointer instead of a clone. standards/data.md § "A shared file is named
  in config; only the tool's own default is compiled in" asks for three, and the middle one is the
  layer that reaches a process sourcing no profile — the rung a variable alone cannot be.

config.toml is new here and optional. A machine keeping the library where doit expects it should not
  have to hold a file saying so, and erroring on a malformed one would break exactly that machine,
  so both fall through.

setting_source travels with the value, per the same standard. An empty library is usually a config
  that was never read rather than a wrong path, and the value alone cannot tell those apart.
  `content status` prints it; `content path` stays one bare line, because a script reads that one.

The rungs are asserted one at a time, each winning over every rung below it. Every rung yields a
  path, so a reordering is invisible to a test that only checks one came back.


## v2.2.0 (2026-08-13)

### Features

- **paths**: Let $DOIT_LIBRARY_DIR name the checkout
  ([`1641e72`](https://github.com/datapointchris/doit/commit/1641e723240bd4564da2a238e085db6163900408))

library_dir compiled the path in and consulted nothing, so the only way to read a second copy of the
  library was to clone one. The library has carried two clones for exactly that reason — a dev one
  under ~/tools and the XDG one doit reads — kept in step by two sync mechanisms that never compared
  notes and had no check that they agreed.

A variable is the rung this wants and a config key is not. The path is the same on every machine,
  unlike the repo registry, whose value differs per machine and so earns one. Every rung has to
  answer a question some machine actually asks.

The default stays outside doit's own directory. A path under doit/ would say the collection is
  doit's, and it outlives any one tool that parses it.


## v2.1.1 (2026-08-13)

### Bug Fixes

- **kit**: Stop the digest sending private context
  ([`2c0f7ed`](https://github.com/datapointchris/doit/commit/2c0f7ed0198d73c8c27a26e42c4c3a1bce207f39))

`claude -p` injects the machine's own configuration ahead of the prompt, so every reading sent the
  whole user-level CLAUDE.md and the account email beside a table this module filters field by
  field. That memory loads from the home directory whatever the working directory is, so running in
  a scratch directory bounded project memory and nothing else, and the claim that the payload was
  the whole of what the session saw did not hold.

`--safe-mode` is the flag that bounds it. Measured against a loopback endpoint on Claude Code
  2.1.229, one call made twice differing only in that flag: a first user message of 64,551
  characters without it and 373 with, an identical tool array, and OAuth authenticating unchanged.
  `--bare` reads as the stronger form of the same flag and breaks that OAuth session, so it is not
  the answer.

Nothing pinned any of it. The argv spy asserted only that the prompt was absent from the command, so
  deleting a containment flag left the suite green. Each flag is now asserted positively, the deny
  list by its joined value.

A reading also reaches stdout before it is stored. The append ran first and only DigestFailed was
  caught, so an unwritable state directory raised through an answer that had already cost a request;
  the file is replaceable and the answer is not. A failed write now names the path and shows in the
  exit code.

DigestFailed carries a Failure key rather than a sentence, which tells the four modes apart and
  keeps a test off the wording. A handle matching nothing lists the handles that do, instead of
  pointing at the verb that spends a request to recover from a typo, and `digest list` is where
  those handles come from.


## v2.1.0 (2026-08-13)

### Features

- **kit**: Read the usage table back as prose
  ([`1cbd2c6`](https://github.com/datapointchris/doit/commit/1cbd2c67e38b3519443fef8eeb7e8c5c956aa73e))


## v2.0.0 (2026-08-12)

### Bug Fixes

- **find**: Report a missing picker as a failure
  ([`dc59c62`](https://github.com/datapointchris/doit/commit/dc59c62cf00505b04204b2cb87e2ec63cb169fd1))

`run_fzf` returned '' both when the pick was cancelled and when fzf was not installed, and every
  caller mapped '' to 0. `choose` is read back through `$(...)`, where exit status is the caller's
  only signal, so a machine without fzf reported "it ran and you chose nothing" — an empty command
  line with no explanation. It returns None for a missing binary and '' for a cancelled pick, and
  `find`, `choose` and `launch` each fail on None. Fixed in `run_fzf` rather than at one call site,
  because all three shared the conflation.

The headers are constants now. Two commands share one picker and the header is the only thing on
  screen naming which one you are in, so the test compares against the constant instead of fishing a
  phrase out of an English sentence — rewording a header was a test failure.

`find` said "Search across every collection you own" beside a `choose` that said what it prints, so
  the adjacent pair gave a reader nothing to choose between. `find` now carries the other half: it
  opens everything known about the one you pick.

Dropped the test asserting the printed invocation was not the row name. Its only assertion was
  satisfied by a crash, and the test above it already asserts the exact output; its reasoning moved
  into that docstring, where the registry key and the command still differ.

### Features

- **find**: Put a picked command on the prompt
  ([`76a5ab2`](https://github.com/datapointchris/doit/commit/76a5ab2b7cb594136db92ac301cb4a80744dccba))

`doit find` ended at a rendered card, so the one thing you would do next with a row — run it — meant
  retyping what was on screen. `doit choose` is the same picker ending at stdout: it prints the
  row's invocation and nothing else, so `$(doit choose)` composes in any shell.

Landing it on the prompt is the shell's half, because the line editor belongs to the parent process
  a subprocess cannot reach into. `doit shell-widgets zsh` emits that half — a ZLE widget replacing
  the current line, and a `dochoose` function loading the next prompt. It is its own block rather
  than part of the startup nudge, which is gated on wanting reminders; a keybinding that vanishes
  with an unrelated toggle is one nobody trusts.

The invocation rides through fzf as a fourth tab field rather than being resolved from the name a
  second time. `--with-nth=1` already hides it, and a second index pass is a second answer that can
  disagree with the row the human pointed at.

The completion offered `index`, a namespace that became `kit`. Fixed, and a test now joins that
  hand-written list to the command tree.

- **shell**: Namespace init, widgets, completion
  ([`1d51aa8`](https://github.com/datapointchris/doit/commit/1d51aa85c6a4c05f7c493e4068baed9dbb8f3d19))

`doit shell init|widgets|completion`, replacing the flat `shell-init`, `shell-widgets` and
  `completion`. Three commands out of one module, all taking the same shell argument and all
  emitting a block to eval — the resource is the shell integration, and `doit shell --help` is now
  the one screen that says what a shell loads from doit. The threshold was already passed by
  `shell-init` and `completion` alone; `shell-widgets` only made it visible.

The widget help claimed a keybinding the block never emitted. It emits none, and now says so: doit
  cannot see what a keymap already holds, so a chord chosen here is one taken from whatever had it.
  The block defines and names the widget, the help shows the bindkey to write, and the rc that knows
  the keymap binds it.

Rejected: keeping the flat spelling with a justification in CLAUDE.md, since a fourth block is
  foreseeable and there is no reason to write. Rejected: splitting the difference by leaving `doit
  completion` at the root to match the sibling `<tool> completion zsh` lines in the zshrc — that
  leaves `doit shell --help` unable to name the third block, which is the discovery surface the
  namespace exists to create, and doit's completion is a cached rc line nobody finds by typing.
  Rejected: hidden root aliases, the permanent cost the standard names, bought to soften a window
  that closes as soon as dotfiles carries the new spelling.

BREAKING CHANGE: `doit shell-init` and `doit completion` are now `doit shell init` and `doit shell
  completion`. dotfiles holds the rc lines that cache both, and its matching change is required.

### Breaking Changes

- **shell**: `doit shell-init` and `doit completion` are now `doit shell init` and `doit shell
  completion`. dotfiles holds the rc lines that cache both, and its matching change is required.


## v1.0.1 (2026-08-12)

### Bug Fixes

- **tools**: Unknown --category is a usage error
  ([`b415b87`](https://github.com/datapointchris/doit/commit/b415b87ded04c1e9c72620574ec38ab736cb3d29))

Exit 1 could not be told from a command that ran and failed, and the --json branch returned ahead of
  the check, so a machine caller naming a category that does not exist was handed [] and exit 0 —
  the category read as empty rather than wrong.

check_category raises typer.BadParameter ahead of both renderings, naming the categories that exist
  and pointing at doit tools categories list. It stays silent on an empty registry, where the doit
  content sync explanation is the useful answer instead.

The category fallback was spelled out at three call sites, which is how the two paths came to
  disagree: the JSON filter compared the raw key while the listing bucketed absent ones under
  uncategorised, so the one name the listing printed was the one name JSON could not select.

### Documentation

- Cite the standards without a machine path
  ([`b40ee85`](https://github.com/datapointchris/doit/commit/b40ee858df251bad437bd4b360bb77b9c3148791))

The citation carried an absolute path from one machine's layout. What a reader needs is the file and
  the section, and those do not move.


## v1.0.0 (2026-08-10)

### Documentation

- **kit**: Record the single history read and its two folds
  ([`803a9d4`](https://github.com/datapointchris/doit/commit/803a9d4123a81b8f7f8accfb5fd401697c8ef9c6))

CLAUDE.md's atuin note explained why the review lane shells out; it now also says that read has a
  second consumer and must keep having one parser, since a second would drift and leave `review` and
  `kit` disagreeing about whether something had been used.

README gains the `doit kit unused` line — it is a headline capability, not a verb someone would find
  by walking the tree.

### Features

- **labs**: Pick becomes choose, the one word for a picker
  ([`3262479`](https://github.com/datapointchris/doit/commit/326247998095b4e0269db194aaea89ee2783e858))

The fleet spelled one act four ways — `theme change`, `font change`, `doit labs pick`, `doit launch`
  — which is cli-design.md's own tell for real drift: one tool spelling it differently from every
  other, for the same job on the same kind of object.

`choose` won on the precedent it sits beside, tmux's choose-tree / choose-client / choose-buffer
  family and gum's `choose`. The reasoning and the rejected alternatives are cli-design.md § "The
  interactive picker is `choose`" and architecture/verb-axes.md.

`doit launch` is deliberately not renamed. It answers "what can I even run here" rather than
  returning a peer of a known kind — after a choose you have a thing, after a launch you have gone
  somewhere.

The review register calls this verb; its entry was updated to match.


## v0.13.0 (2026-08-10)

### Build System

- **precommit**: Resync to forge toolchain 14
  ([`db36f74`](https://github.com/datapointchris/doit/commit/db36f742ccc77e3c959c3aae6a128ea07c48fae0))

### Features

- **kit**: Report what you own and never reach for
  ([`7f2fcc9`](https://github.com/datapointchris/doit/commit/7f2fcc9f44d15d49f3b683f823d355a10ac08d99))

Adds `doit kit usage` and `doit kit unused` — the second of two folds over the federated index.
  `unresolved` joins it against PATH and the shell files and answers "you cannot run this"; these
  join it against shell history and answer "you can run it and never do". Read together they
  separate a stale registry entry from a genuinely forgotten one.

Renames the `index` namespace to `kit`. `index` named the data structure rather than what it holds,
  and held two of the six operations over the union while the everyday four sit at the root, so it
  read as a leftovers drawer. `find`, `show` and `launch` stay where they are.

Counting folds observe.history_entries, the cached atuin-with-zsh-fallback read the review register
  already makes. A second history parser would answer a subtly different question within a month,
  and then two commands would disagree about whether rg had been used.

The join key is the whole of the difficulty and three spellings get it wrong. Measuring by name
  reports ripgrep as never used, because you type rg. Measuring by the head word gives all 23
  git-forgit-* rows git's own count, since their invocation is `git forgit add`. And three rows
  spell arguments in bare caps — bbkt pr VERB, dectl PIPELINE RESOURCE ACTION, jira issue VERB —
  which match no command line ever typed. Matching is therefore is_invocation_of against the literal
  invocation prefix, the same word boundary that stops `claude /audit-repo` claiming an
  /audit-repo-docs run.

Rows sharing a typed form merge, because the unit of the answer is a thing you can type: the
  registry documents several of Chris's own shell functions, so 235 catalogue rows are 204 things.
  tmux, workflow and skill are excluded rather than reported unused — a keybinding never reaches a
  shell history, so a permanent zero there would be a lie rather than a finding.


## v0.12.0 (2026-08-10)

### Bug Fixes

- **skills**: Apply the standards pass findings
  ([`d8b1ef4`](https://github.com/datapointchris/doit/commit/d8b1ef4dc337c0a3d9a58a61ee44776f1e4388c8))

Four rule breaks from the review on #2, and the two judgements it raised that no standard reaches.

Unknown --group is a usage error at exit 2, like find's --source, and the check runs before the
  --json branch. `--group nope --json` was printing `[]` and exiting 0, which tells a machine caller
  the group is empty rather than absent. Silent on an empty library so the work box, which carries
  no ~/.claude at all, still gets the explanation.

`skills groups` becomes `skills groups list`. tools.py made the opposite call three commits ago and
  recorded why beside it: a set being read-only "does not buy it an exemption — predictability is
  the point".

The skill lens in find hands off to doit.skills rather than shelling bat at the raw file. That was
  correct while nothing owned skills; this branch creates the owner and gives it render_skill, so
  `doit show` now renders the card like the workflow lens beside it, in-process rather than spawning
  an interpreter to re-read a file this one can open.

read_frontmatter takes its split from cards.split_document and keeps only the policy. Restating the
  split is what let the copies diverge on guarding a scalar frontmatter; the guard is now in cards
  too, where a `---\nfoo\n---` block was handed back for a caller to .get() on.

cmd_list and cmd_groups take their booleans keyword-only. The call sites read `cmd_list(None, False,
  True)` and `cmd_list(None, True, False)` six lines apart, meaning opposite things.

Both tests asserting on rendered rows are deleted rather than pinned to a width. They were really
  about the terminal: at 80 columns the clipped row hid the phrase the summary test asserted absent,
  so it passed with the sentence splitter disabled entirely. A width would only choose which
  consumer's terminal the suite pretends to be, and which field a row prints is a rendering detail.
  `summary` is tested directly and carried in the JSON.

- **skills**: Report unterminated frontmatter as rot
  ([`27ebd35`](https://github.com/datapointchris/doit/commit/27ebd357dc85eb5d62f8aa6f6b8fa074a2b7dfb1))

`split_document` returns nothing both for a file with no frontmatter and for one that opens `---`
  and never closes it, so read_frontmatter was reporting the second as valid YAML with an empty
  description. That is the shape the strict flag most needs to catch: every field a listing shows
  comes back empty, and the one mechanism built to say why stays silent.

No file in the library has it today, which is why this is a line rather than an item — a skill
  authored with a typo is exactly what the flag is for.

### Features

- **skills**: List and describe the Claude skill library
  ([`980c4d4`](https://github.com/datapointchris/doit/commit/980c4d48b67c79ec4e347e182309a9fe76575782))

`doit skills list|show|groups` — the browse view for ~/.claude/skills, which neither existing path
  could give. `doit find --source skill` keeps only the first sentence of a description, because the
  trigger prose and output rules that follow inflate every fuzzy match; Claude Code's own listing
  shows what the model sees rather than what you would choose from. Here the whole description is
  the point, and --full prints it.

Grouping splits the name on its first hyphen rather than matching a verb list, so the closed
  vocabulary stays in review-fleet § "Skill health" and a new verb needs no release here. A name
  that does not parse lands in "ungrouped", which is the audit finding surfaced for free.

SKILLS_DIR and the loader move from index to the new module, following the rule the registry already
  follows in doit.tools — a path defined per reader is a path that drifts. index and find both read
  through it.

Frontmatter is parsed as YAML with a line-read fallback: four skills in the live library carry an
  unquoted `: ` in their description, which Claude Code tolerates and yaml.safe_load rejects.
  Dropping them would hide the most-used skills, so the listing reports them by name instead —
  unresolved()'s rule, that rot is reported rather than rendered around.

- **skills**: List and describe the Claude skill library
  ([#2](https://github.com/datapointchris/doit/pull/2),
  [`9e8df68`](https://github.com/datapointchris/doit/commit/9e8df68f016ccb0785f32b076ff3d01ce36f39b0))

## What this is for

A browse view of `~/.claude/skills` — every skill, grouped, with the whole description rather than a
  truncated one.

Neither existing path answers it. `doit find --source skill` deliberately keeps only the first
  sentence, because a description carries trigger phrases and output rules that inflate every fuzzy
  match. Claude Code's own listing shows what the *model* sees when deciding whether a skill
  applies, which is not the same question as "what do I own and which one do I want".

``` doit claude skills list [--group G] [--full] [--json] doit claude skills show <name> doit claude
  skills groups [--json] ```

**Why a `claude` namespace and not `doit skills`.** `skills` is the one collection name in doit that
  collides with a concept another CLI owns: `learning` stewards the topics and tracks Chris is
  developing, `doit labs` the practice for them, so a bare `doit skills` reads at least as naturally
  as *his* skills. Workflow cards, Labs and the tool registry have no such collision and stay at one
  word. It is also where a sibling goes — `~/.claude` holds agents, commands, hooks and output
  styles, and deciding now keeps filling one a mount here rather than another product's vocabulary
  arriving loose at doit's root.

## Decisions

**Grouping splits the name, it does not match a verb list.** The closed vocabulary lives in
  `review-fleet` § "Skill health"; a copy here would be a second copy that drifts, and adding a verb
  would need a release. A name that does not parse as `<verb>-<target>` lands in `ungrouped`, so the
  `audit-skills` Step 1 finding shows up for free rather than needing a check that has to be kept
  current.

**`SKILLS_DIR` and the loader moved out of `index` into the new module.** This is the rule the tool
  registry already follows in `doit.tools` — "a constant defined per reader is a constant that
  drifts". `index` and `find` both read through `skills.load_skills()` now. Rejected leaving the
  path in `index`: `skills` would have had to import `index` to reach it, and `index` already
  imports the collections.

**Frontmatter is parsed as YAML, with a line-read fallback.** Four skills in the live library
  (`audit-standards`, `capture-item`, `capture-workflow`, `learn-explain`) carry an unquoted `: `
  inside their description, which Claude Code's loader tolerates and `yaml.safe_load` rejects.
  Parsing strictly would silently drop four of the most-used skills from the listing. The fallback
  keeps them listed and the listing **names them**, following `unresolved()`'s rule that rot is
  reported rather than rendered around.

## What to look at

- `src/doit/skills.py` — `read_frontmatter` is the only interesting part. The fallback is reached
  only after YAML has already refused, so it is not a second parser competing with the first. - The
  `SUMMARY_END` regex deliberately does not reuse `render.first_sentence`. That one stops at any
  punctuation-then-space, which is right for a stored note and wrong for a description dense with
  `e.g.`, `~/notes/` and `/slash-commands`. - `tests/fixtures/skills/` is four awkward cases rather
  than four tidy ones — `e.g.` in a description, invalid YAML, a manual-only skill, and a name that
  does not parse. Each is something the real library contains. - `find.py` had two more
  `index.SKILLS_DIR` references that no test covers; mypy caught them, not the suite.

## Not in this PR

The four invalid-YAML descriptions are reported, not fixed. Every escape-free quoting form is a
  block scalar, and whether Claude Code's own loader reads one is unverified — getting it wrong
  silently empties a skill's trigger prose, which is how a skill stops firing without anything
  saying so.

### Refactoring

- **skills**: Move the listing under a claude namespace
  ([`6d34fe9`](https://github.com/datapointchris/doit/commit/6d34fe953fb064a5b50a58f9036640b6363f908d))

`doit claude skills` rather than `doit skills`. `skills` is the one collection name in doit that
  collides with a concept another CLI owns — `learning` stewards the topics and tracks Chris is
  developing and `doit labs` the practice for them, so a bare `doit skills` reads at least as
  naturally as his skills as it does Claude Code's skill files. Workflow cards, Labs and the tool
  registry are unambiguous at one word and stay there.

It is also where a sibling goes. ~/.claude holds agents, commands, hooks and output styles beside
  the skills; those directories are mostly empty today, and deciding now is what keeps filling one a
  mount rather than another product's vocabulary arriving loose at doit's root — the retrofit
  cli-design.md § "A resource that could ever grow a second command is a namespace today" exists to
  prevent. Nothing was merged or typed by anyone yet, which is the window where this costs one
  commit.

`doit find --source skill` is unchanged: a --source value is a lens filter alongside tool, func and
  tmux, not a command path.


## v0.11.1 (2026-08-09)

### Bug Fixes

- **sources**: Keep a declared lane visible when its source fails
  ([`c4777c7`](https://github.com/datapointchris/doit/commit/c4777c704a7c448a5213f3245bd42030607ef893))

A conforming source names its lanes only in its payload, so a call that failed produced no payload
  and `lanes_from` returned nothing — the lane left the dashboard entirely. That is the silent drop
  the module exists to prevent, and it was invisible exactly when a backend was broken.

`lanes` was already config, used only to filter. It is also the only statement of what should have
  been there, so it now doubles as the fallback: a failed call with a declared lane emits an
  unavailable lane carrying `reason()`, the same line an adapter-backed source would show.

Undeclared lanes still vanish. doit cannot invent a name for a lane a source never said it had.

Found wiring up a `prs` lane over `gh search prs`: logging gh out took the lane off the dashboard
  rather than reporting the auth failure.

- **sources**: Keep a declared lane visible when its source fails
  ([#1](https://github.com/datapointchris/doit/pull/1),
  [`13d5ab4`](https://github.com/datapointchris/doit/commit/13d5ab4502b810696c840adb02732b6eec9c05f1))

## What this is for

Wiring a `prs` lane over `gh search prs` surfaced a hole in the source contract: with `gh` logged
  out, the lane did not report the auth failure — it disappeared from the dashboard entirely.
  `sources.py` opens by saying a lane is never silently dropped, "because a dashboard that quietly
  omits a lane reads as *nothing outstanding*, which is the worst available failure". For conforming
  sources that was not actually true.

## Why it happened

Adapter-backed sources declare their lane names in code, so `dashboard.py` can emit
  `unavailable(name, title, reason)` when the call fails. A conforming source names its lanes **only
  in its payload** — and a failed call has no payload. `lanes_from` returned `[]` and the lane
  ceased to exist.

## The decision

`lanes:` in `sources.yml` was already there, used only to filter a source down to some of its lanes.
  It is also the only place a source states what it *should* have produced, so it now doubles as the
  failure declaration. No new config key, no adapter, no schema change.

Undeclared lanes still vanish on failure, deliberately — doit cannot invent a name for a lane a
  source never claimed. That is the second test.

## What to look at

- `src/doit/sources.py` — `lanes_from`, the `if not built` branch. The ordering matters: the filter
  still runs on success, so the existing restrict-to-some-lanes behaviour is untouched (its test
  still passes unmodified). - Whether `name.upper()` is the right title fallback here. It matches
  `lane_from`'s own default, but an unavailable lane has no payload to take a nicer title from.

393 tests pass, 2 new.


## v0.11.0 (2026-08-08)

### Features

- Never prompt a caller that cannot answer
  ([`306d529`](https://github.com/datapointchris/doit/commit/306d52960d2c77a5d94ddd9427ee01f201175e9d))

The flashcard drill read a keystroke per card with no gate at all, so off a terminal it waited on a
  stdin that never closes — no output, no exit code. It now refuses up front; there is no flag that
  answers a recall drill for you.

run_on_log already skipped its confirmation without a terminal. Both now ask render.can_prompt(), so
  --no-input takes the same branch from a terminal and how a run behaves unattended can be rehearsed
  without faking a pipe.

The gate lives beside the consoles in render.py because it answers the same question they do — who,
  if anyone, is on the other end.


## v0.10.3 (2026-08-08)

### Bug Fixes

- **tools**: Categories is a namespace, not a bare noun that reads
  ([`ea55c26`](https://github.com/datapointchris/doit/commit/ea55c26f7d53b77b5eac7d91388a2cf20f474f59))

`doit tools categories` listed when invoked bare. cli-design.md's 'No args shows help. Always' binds
  at every level of the tree, so a node that reads bare is one you cannot walk down without running
  something you did not ask for — and a noun in the verb slot does not say which of list, show or
  create it meant.

Now `doit tools categories list`, with --json alongside it. That the set is derived and will never
  grow `create` buys no exemption: rustup target list and helm repo list are the shape, and
  predictability is the point.


## v0.10.2 (2026-08-08)

### Bug Fixes

- **dashboard**: Build show handles, not view
  ([`92350ad`](https://github.com/datapointchris/doit/commit/92350ad83a908502ac600bf8f143a60ba6d6b457))

icb and learning renamed the verb that displays one instance from `view` to `show`, so every
  dashboard row handle and the example pursuits register pointed at a command that no longer exists.

The `view:` key in a pursuit is unchanged — it names the field, not the verb. Verb standardization
  rationale is in ~/dev/standards/cli-design.md.


## v0.10.1 (2026-08-08)

### Bug Fixes

- **tests**: Stub rg on PATH so the card verdict is not the machine's
  ([`aaa2332`](https://github.com/datapointchris/doit/commit/aaa23327885920e67da229d2425cdadb100f0d37))

The PATH check resolves through shutil.which, which reads the real PATH, so the tool card's verdict
  depended on what the machine running the tests had installed. CI has neither rg nor most of the
  registry and called the row dead.

Prepended rather than replacing PATH, so long-gone still resolves to nothing and the miss case stays
  covered — the same fixture shape test_index.py uses.


## v0.10.0 (2026-08-08)

### Documentation

- **comments**: State what the code is, not what it replaced
  ([`c527a04`](https://github.com/datapointchris/doit/commit/c527a04a0fd5205e570a1f20d2d69413fb2bb514))

Both docstrings narrated a predecessor implementation — what the bash version did and why it could
  not do this. The next reader does not have that diff, and the narration decays while the code
  moves on.

The durable fact underneath survives the rewrite in each case: cards exist because Labs and workflow
  cards are one file shape, and the checkout is one resolved path rather than a symlink followed
  backwards.

### Features

- **tools**: Render the registry natively as doit tools
  ([`19629b7`](https://github.com/datapointchris/doit/commit/19629b73b244e0028cdd00cac924f997c4a0864a))

`doit tools show/list/categories` replaces `toolbox show`, completing the set beside `doit workflows
  show` and `doit labs show` — three collections in the terminal library, three namespaces rendering
  them alike. find and the fzf preview call it in process instead of spawning toolbox.

doit.tools owns the registry path and loader, which index and labs now import: a constant defined
  per reader is a constant that drifts. Its card shapes take plain arguments, not index rows, so
  composing them does not require importing index and closing a cycle.

Two things the ported card does differently. The PATH check reads the usage string rather than the
  registry key, so `ripgrep` resolves through `rg`. And a miss is stated without a verdict:
  functions and aliases are never on PATH and outnumber genuine rot two to one here, so `doit index
  unresolved` — which decides it against the shell files — stays the one report that says which.

The index gained two fields the cards need and nothing carried: a function's body, which is the
  refresher a description cannot be, and what an alias expands to.


## v0.9.0 (2026-08-08)

### Features

- **tools**: Read the registry from terminal-library
  ([`1256a9a`](https://github.com/datapointchris/doit/commit/1256a9ab3def281babcbd2428dd556f868e01de9))

The registry is a third kind of card beside workflows/ and labs/, so it is read from the library
  rather than from a path named after toolbox. dotfiles still owns the copy toolbox reads; this only
  repoints doit.

$TOOLBOX_REGISTRY becomes $DOIT_TOOLS_REGISTRY. The old name pointed at a tool being archived and
  was neither the reader nor the owner; nothing in the fleet ever set it, and toolbox and dotfiles'
  tool-usage keep their own resolution untouched.

library_dir() absorbed the last two xdg_data_home() callers, so both imports went with them — the
  tools/ subdirectory cost an argument, not a fifth copy of the root.


## v0.8.0 (2026-08-08)

### Documentation

- **review**: A show: command must be a read
  ([`b6680b1`](https://github.com/datapointchris/doit/commit/b6680b117fbaef766fbc00411af79edd44cabe09))

The nudge runs `show:` on a cadence, so a command with side effects changes things nothing asked
  for. Worse, when what it changes is what the item observes, the item marks itself done every time
  the nudge fires — which is what `toolbox remind` did before it grew --peek.

### Features

- **content**: Follow the terminal-library rename
  ([`1f317fc`](https://github.com/datapointchris/doit/commit/1f317fcacf894042caf43a3d8a88cd2d764e18e2))

doit-content became terminal-library. The old name claimed doit owned the collection, and doit is
  one reader of it — the tool registry moving in next makes that plainly wrong. The checkout moves
  with the name, from $XDG_DATA_HOME/doit/ to $XDG_DATA_HOME/terminal-library/.

content, labs, workflows and index each rebuilt that root from a literal, so it is resolved once in
  paths.library_dir() and they reach into a subdirectory of it. Every DOIT_* override still wins
  where it did before; only the defaults move. The next consumer is an import, not a fifth copy.

An existing install re-clones at the new path on first run. Anything uncommitted in the old
  directory stays there — it is not read again and not deleted, so check it before removing it by
  hand.


## v0.7.0 (2026-08-08)

### Chores

- **lint**: Ignore the generated CHANGELOG.md
  ([`d7d3452`](https://github.com/datapointchris/doit/commit/d7d3452b1e67835ba12a76b5cd99e1ddae87a7cc))

semantic-release rewrites CHANGELOG.md on every release, so markdownlint --fix normalizing it is
  undone on the next one and resurfaces as a rebase conflict when a local commit lands on top of the
  release commit.

### Features

- **review**: Observe last-done instead of asking for it
  ([`9918baa`](https://github.com/datapointchris/doit/commit/9918baa55a74cc473fd0d5ddeee52afb586b5c1d))

A cadence item's date had one origin: typing `doit review done <id>`. A declared date has nothing
  underneath it to re-check, so an item done and never reported reads exactly like one never done —
  which is the state the register exists to notice.

An observer supplies evidence instead. By default an item is done when the command it already names
  last ran, so most items need no configuration. `{newest-date-in: <path>}` reads another tool's
  state file for work that leaves no prompt behind: the nudge runs `toolbox remind --brief` as a
  subprocess, so the reminder that fires automatically is the one shell history cannot see.

History comes from atuin, the only source recording which machine ran a command, falling back to
  this machine's zsh history when atuin cannot be asked. That makes `scope: machine` answerable, so
  per-box work is not cleared by the other desk doing it.

`observe: false` covers a command that opens work rather than being it — a review window, a `claude
  /...` session — where observing would count an abandoned session as done.

Never-run now ranks above overdue in the maintenance lane, matching `cadence.is_due` and `doit
  review due`, and ties break on the shorter cadence. `doit review list` names stranded done-dates,
  since renaming an item silently orphans its history under the old key.


## v0.6.0 (2026-08-07)

### Documentation

- Name whose problem the missing projects handle is
  ([`d642565`](https://github.com/datapointchris/doit/commit/d64256542752aa68bb23ba328dbf31fab4408b8b))

The comment said `doit next` prints the item's view command in full. It did for about an hour. A
  project item's id is a UUID, which is unusable as something a person retypes, so the register line
  that produced it is gone and the draw is as bare as the lane.

That is icb's gap rather than this renderer's, and it is filed: project items and projects are the
  only icb resources without the short integer every other one carries. The handle comes back here
  when the number exists.

### Features

- **dashboard**: Give project item rows a handle
  ([`7c891c7`](https://github.com/datapointchris/doit/commit/7c891c7b95742507baa88fd177436907a3ecc81b))

Projects was the only lane whose rows carried no command, because an item's only id was a UUID and
  the invocation ran to sixty columns of hex nobody could retype. Items carry a short number now and
  icb takes it, so the row names the thing it shows and the verb comes out of the hint.

view_handle takes the field to read, because project items are the one resource whose handle is not
  its id. An item created in todoui while the API was unreachable has no number until its create is
  pushed, and falls back to no handle at all — the existing rule that a handle which would fail is
  worse than none.


## v0.5.0 (2026-08-07)

### Features

- Give every row the command that opens the thing it names
  ([`fcba2d3`](https://github.com/datapointchris/doit/commit/fcba2d38bf420abffb2f3dcd21be1521062e4b80))

The maintenance lane was not the only one you could read and not act on. A task showed a priority,
  which is not its id. An article showed a title and nothing else — no source, no id, 309 of them. A
  book, a countdown, a learning resource: all named, none reachable. Every one of those backends has
  a `view` verb, so the handle is uniform across the lanes, it is the read rather than a write, and
  it is built in the icb adapter where doit's knowledge of icb already lives and where it disappears
  when icb speaks the lane contract itself.

Three lanes were also dropping fields they had been handed. An article's host is the one thing
  separating a vendor blog post from a paper without opening either. An event's venue is most of
  what a row about somewhere you have to be is for. A task's notes are why "audi pcv valve" is on
  the list at all.

Projects stays without a handle, deliberately: its ids are UUIDs, so the invocation runs to sixty
  columns and would ellipsise into something that looks copyable and is not, taking the title's
  width with it. The verb goes in the lane hint, and `doit next` prints it in full via a new
  register `view:` field — a drawn row has a line to spend on one and a three-row glance does not.

Columns reorder to text, note, handle: what it is, how urgent it is, what to type. Between the two,
  the command split every title from its own qualifier.


## v0.4.0 (2026-08-07)

### Features

- **dashboard**: Give a row the command that does it
  ([`a77df7a`](https://github.com/datapointchris/doit/commit/a77df7a699154f7116b9d8c5edb8c28b9e06c3f2))

Every maintenance row named an intent and withheld the act. "Re-index indy so semantic search stays
  current" does not tell you to run `indy index`, and the register has carried that command all
  along — `doit review due` and the startup nudge both print it. The lane dropped it in favour of
  the description alone, so the one view meant to be glanced at was the one you could not act from.

A Row gains `handle`, the field a GridCell has had since the beginning and for the same reason: what
  you would type to act on this. Labs carry no command of their own, being documents rather than
  jobs, so the handle is the verb that opens one.

The lane also promised sixteen labs and showed none. A never-run review and a never-run lab rank
  identically, reviews were collected first, and a stable sort over the merged list put all fourteen
  reviews above all sixteen labs — the row cap then cut before a lab could ever appear. Each kind
  now ranks within itself and the two interleave, which is what round_robin already exists in this
  file to do.

Column widths collapse into one rule: what the content needs, bounded by a share of the line,
  nothing when a lane has no commands at all.


## v0.3.0 (2026-08-07)

### Features

- **dashboard**: Say where a project item lands, and spend the width on it
  ([`9a601ed`](https://github.com/datapointchris/doit/commit/9a601ed4621f30cafa9e51e0fa6a5ecafca6516f))

Three faults compounded into a lane that named things without placing them. The note column was
  capped flat at 22 columns, so "CLI machine contract conformance" arrived as "CLI machine contract
  …" with room going spare to the right of it. The whole layout was capped at 100 regardless of the
  terminal. And the row never read `repo` at all — the one word that says where you would go to do
  the thing.

The note now carries repo then projects, deduped so a repo whose project shares its name is placed
  once rather than as "syncer · syncer". It takes a share of the line instead of a fixed count,
  which only binds when the notes are genuinely long, and the overall cap moves to 140.

An item's notes hold the reasoning, the rejected alternatives and the pre-flight checks. Flattening
  all of that into a row that then gets clipped ended the line mid-word in the second paragraph, so
  the row takes the first sentence: it stops where the writer stopped.

- **next**: Show where an offered item lives and what it is
  ([`cc325d1`](https://github.com/datapointchris/doit/commit/cc325d1807f36465382e6440ffc460e066389097))

A drawn row printed the resolved title and dropped the rest of the row the backend had already
  returned. "Give cobracmd a usage-error exit code of 2" arrived with the repo it lands in, the
  project it serves and five paragraphs of reasoning attached, and none of it reached the screen —
  so deciding whether to pick it meant going back and asking the same backend a second time.

The register names the fields, the way it already names label and id:

context where it lives — one dotted path or several, joined detail the field to take a one-sentence
  gist from, usually notes

Declared rather than inferred, because hardcoding icb's field names would put a backend's model back
  inside doit. `dig` gained list indices so a path can reach `projects.0.name`, which is where
  membership actually lives.

Rendered as one continuation line under the title, not two: the draw is five entries you scan, and a
  paragraph under each turns it into a document.


## v0.2.1 (2026-08-07)

### Bug Fixes

- **test**: Stop the rot report depending on the host's PATH
  ([`624ad5d`](https://github.com/datapointchris/doit/commit/624ad5d10953a05e63b4d1c321e4559ad030c7e4))

The fixture pointed every lens at a fixture so no test read the real machine, but `unresolved`
  resolves through `shutil.which`, which reads the real PATH. A machine without ripgrep installed
  called a fixture row dead — which is every CI runner, and why CI has been red since the last two
  pushes.

Stub `rg` and `forge` onto a prepended PATH. Prepended rather than replacing, so `long-gone` still
  resolves to nothing and git stays runnable.


## v0.2.0 (2026-08-07)

### Chores

- **lint**: Disable SC1091/SC1090 from the forge toolchain
  ([`953973f`](https://github.com/datapointchris/doit/commit/953973f06c995dc54e82fde090faf086c83d5acb))

### Documentation

- Name the verb that syncs content, not the one that updates doit
  ([`bbc2666`](https://github.com/datapointchris/doit/commit/bbc266604b6fcf8d591c88437e4168da1040754f))

`doit update` updates the binary; the content checkout is `doit content sync`. Five places said the
  first while meaning the second, which content.py's own docstring had already called out as the
  ambiguity to avoid.

Also say that the content path is resolved rather than assumed to be a real directory, so a machine
  that authors cards can point it at a checkout kept where its git lives. Pointing it there is the
  installing layer's job — doit only ever opens the path it is given.

### Features

- Pull the cards in the background on every run
  ([`2d75ce7`](https://github.com/datapointchris/doit/commit/2d75ce7fe7ea9c32fd745088f161818a8cffb609))

`content sync` was a verb you had to remember, so cards went stale between the times anyone thought
  of it. The pull now runs on every invocation, detached and never waited on, so a command costs the
  same whether or not there was anything to fetch.

Not on a timer: nothing waits on it, so there is nothing to ration.

Three things make that safe. `--ff-only` refuses rather than merging and git declines to overwrite a
  modified file, so an unfinished card is never the price of a sync. An atomic mkdir lock keeps two
  commands started together from colliding on git's index.lock, whose error reads like an
  unreachable remote. And the commands fzf and completion drive are exempt, because a preview pane
  redraws on every keystroke.

A detached pull has nowhere to print, so it leaves git's stderr in a log the next command reports —
  cards that quietly stopped updating read as cards that stopped changing.


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
