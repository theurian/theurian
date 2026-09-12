# ADR-0034: `migrate apply` enforces the merge

- Status: proposed
- Date: 2026-09-12
- Deciders: Theurian maintainers
- Requirements: SEC-17, FR-I3, INV-7, T-12, T-15
- Situates against [ADR-0013](0013-ai-writes-produce-proposals.md) (point 4 —
  approval *is* the merge, which is what makes this checkable at all),
  [ADR-0027](0027-accept-validates-before-it-moves.md) (the other pre-apply
  gate), [ADR-0032](0032-the-write-intent-mcp-tool-surface.md) (the surface this
  is a precondition for) and
  [#281](https://github.com/theurian/theurian/issues/281) (the
  approval-*provenance* pointer, which is explicitly not this)

**This ADR records a decision and ships no code.** No check runs, no flag
exists, no module is created; the diff is confined to `docs/`. What slice B3
owes is named in *Compliance*.

**Every repository fact below was measured on 2026-09-12 against `be977ea7`**,
which is reachable from `origin/main`.

## Context

ADR-0013 point 4 says approval is "a human reviews the proposal, moves the
migration into `.theurian/migrations/`, and merges the pull request". The
merge is not a ceremony around the approval; in this product it **is** the
approval, which is what makes it a thing code can ask about.

Nothing asks. `docs/security/threat-model.md` records it as T-15's residual, in
its own words:

> **Residual: nothing enforces the merge.** `migrate apply` applies whatever is
> in `.theurian/migrations/`, committed or not — the human's review is a
> workflow convention, not a check the code makes, and the actors table's
> untrusted same-UID process can run it directly.

**The code says the same thing, measured — and the shape is *reads that
describe*, not *no reads at all*.** `cli/commands.py`'s apply path makes
**three** git calls while building the `Project` record it stores:
`repository_url`, `default_branch` and `current_commit`, each a
`subprocess.run` in `cli/context.py`. Every one of them is read-only metadata,
and **none of the three is ever compared to anything.** `last_seen_commit` is
the clearest case — its whole population is a write in `project register`, a
write here, the field on `domain/project.py`, the column in
`infrastructure/sqlite/schema.py`, three lines of the upsert in
`infrastructure/sqlite/store.py` and the read-back that reconstructs the
object:

```console
$ git grep -n "last_seen_commit" be977ea7 -- packages/theurian-core/src | wc -l
       8
```

Eight sites across five files, none of them a comparison. So the gap is not
that `migrate apply` cannot reach git; it reaches it three times and asks it
nothing that decides anything.

**No tracking check exists anywhere in `src/`, and the key is the argument
vectors themselves.** Walking every list or tuple literal in the shipped package
whose first element is the string `"git"` returns **five** vectors in **two**
modules, and the subcommand is what settles it:

| Module | Vector, after `git` |
| :-- | :-- |
| `cli/context.py` | `rev-parse --show-toplevel` |
| `cli/context.py` | `rev-parse HEAD` |
| `cli/context.py` | `symbolic-ref --short HEAD` |
| `cli/context.py` | `remote get-url origin` |
| `infrastructure/git/trailer_source.py` | `-c log.showSignature=false --no-optional-locks --no-replace-objects log -z` |

Not one of them asks what git *tracks* or whether a file matches `HEAD`: there
is no `ls-files`, no `status`, no `diff`, no `cat-file`. **The key's recorded
limit** is the one `tests/unit/test_network_call_sites.py` states about its own
scan — a vector assembled at runtime rather than written as a literal is
invisible to it, and no name-based walk can do better.

**Phase B is what turns this from a background fact into a precondition**, and
`docs/roadmap.md` already says so in the Phase B security row: "**T-15's
'nothing enforces the merge' residual is a Phase B precondition, not a
background fact**: opening a protocol-level write path multiplies the callers
who can put a file in `.theurian/migrations/`, and `migrate apply` does not ask
whether it was committed."

That is the whole argument for doing it now rather than later. Today the actors
who can write that directory are the operator and whatever runs as them. After
ADR-0032, any MCP client the daemon serves produces migration documents on
disk — under `.theurian/proposals/`, not `.theurian/migrations/`, and the
distance between those two directories is currently one `mv`.

## Decision

### 1. `migrate apply` refuses, by default, a migration file that is not committed

The predicate is: **the file is tracked by git, and its working-tree bytes are
identical to the bytes at `HEAD`.**

Both halves are load-bearing, and the weaker candidates each fail against a real
sequence:

| Candidate predicate | What passes it that should not |
| :-- | :-- |
| **tracked** | `git add` with no commit. A file staged and never committed is tracked, and nothing has been reviewed or merged. |
| **committed anywhere in history** | a file whose content was committed once and edited since. The approved bytes are in history; the bytes that would apply are not. |
| **tracked and byte-identical to `HEAD`** | — this is the one taken |

**What it does and does not prove, said plainly.** It proves the bytes that are
about to apply are the bytes at the current commit. It does **not** prove that
commit reached `main` through a reviewed pull request — a local commit on a
local branch satisfies it. That is a deliberate floor rather than an oversight:
the properties that would distinguish *merged into the default branch* from
*committed* are branch-protection facts held by a forge, not by the working
tree, and a check that asked the forge would be a network call inside
`migrate apply`. The floor still closes the gap the residual names, which is a
file that was **never committed at all** — the gap an agent, a script or a
mistaken `mv` reaches.

### 2. There is one escape hatch, it is a flag, and using it is visible

An explicit flag — `--allow-uncommitted` is the working name; the exact spelling
is slice B3's — restores today's behaviour for the two cases that need it:
development, where a migration is written and applied before it is committed,
and recovery, where a repository's git state is broken and the knowledge still
has to be rebuilt.

**It is a flag and not a configuration key, and that is the decision.** A
config default is invisible at the moment of use: a project that set it once
applies uncommitted migrations for ever, and nobody reviewing an incident sees
it in the command that ran. A flag is in the command line, in the shell history,
in the CI log and in whatever recorded the invocation. The control this ADR adds
is weak enough — decision 1 says how weak — that making it trivially and
silently disablable would leave nothing.

### 3. A tree with no git refuses without the flag

If `.theurian/` sits in a directory that is not a git repository, `migrate
apply` refuses unless the flag is passed.

This follows from ADR-0013 point 4 rather than from caution: **this project's
approval model *is* the merge**. A tree with no git has no approval record for
the check to read, so the honest answer is to say so and name the flag, not to
apply silently as if the check had passed. A check that quietly degrades to
"allow" wherever its input is missing is the fail-open shape ADR-0031 decision 5
refuses for the same reason.

The refusal names the condition and the flag, so the operator who genuinely has
no repository is one flag away rather than one bug report away.

### 4. The check's seat: the CLI's pre-apply refusals, with the git query in `infrastructure/git/`

**The check runs at the CLI layer**, beside the refusals already there.
`cli/commands.py`'s apply path calls `_refuse_a_set_a_static_guard_rejects`
before `create_database`, with the reason recorded in place: the refusal is
checked "before `create_database` below, so a refused apply leaves no database
file behind (issue #63, #210, T-21)". A merge check belongs in that same
band — after the project resolves, before anything is created — and for the same
reason.

**The git query lives in a new module under
`packages/theurian-core/src/theurian/infrastructure/git/`**, beside
`trailer_source.py`, which is that package's only current member. Two reasons:

1. **It is an adapter, and ADR-0003 puts adapters there.** Asking git what it
   tracks is infrastructure, and `infrastructure/git/` already exists precisely
   as the place that reads git.
2. **`cli/context.py`'s existing git helpers are the wrong home.**
   `current_commit`, `repository_url` and `default_branch` collect *descriptive
   metadata* for a `Project` record. A predicate that decides whether an apply
   proceeds is a different kind of thing, and putting it beside them would blur
   a security check into a metadata read.

**This adds a spawn site, and the pinned set is an equality.**
`tests/unit/test_network_call_sites.py`'s `PROCESS_SPAWN_SITES` holds four
entries today — `cli/context.py`, `infrastructure/git/trailer_source.py`,
`infrastructure/github/gh_cli.py`, `infrastructure/services/runner.py` — and it
"fails when a site is added *and* when one is removed". Slice B3's commit is
therefore the commit that grows that set by exactly one entry, and the file's
own admission checklist applies: the argument vector is fixed by the adapter
rather than taken from a document or a configuration file, the command cannot be
handed a URL or a remote, there is a timeout, and a test goes red when any of
those stops holding. **The new site reaches no network**, which is the same
answer `trailer_source.py` gives.

**One inconsistency is named rather than inherited silently.** Both existing git
sites spawn the bare name `git` and let the child's `PATH` resolve it
(`cli/context.py`'s `subprocess.run(["git", ...])` carries a
`noqa: S607 - resolved via PATH`; `trailer_source.py` does the same), while
[ADR-0030](0030-github-review-ingestion-spawns-gh.md) clause 5 requires the `gh`
binary to be resolved to an **absolute path** so that `PATH` does not choose the
executable. Whether this check follows the git precedent or the `gh` one is
slice B3's, and it is a decision rather than a default: this site's answer
decides whether a security check's executable is chosen by the environment. It
is recorded here so the choice is made deliberately instead of by copying the
file next door.

### 5. The documents that record the residual move in the same commit as the check

The population, with its key:

```console
$ git grep -n -i -E "nothing enforces the merge|does not verify that a migration was merged|committed or not" be977ea7 -- . ':!docs/work-logs' | wc -l
      11
$ git grep -l -i -E "nothing enforces the merge|does not verify that a migration was merged|committed or not" be977ea7 -- . ':!docs/work-logs' | wc -l
       7
```

**The exclusion is measured**: `docs/work-logs/` drops **0** at this frame, and
it stays excluded on the standing ground that a work log is a dated record of
what was believed then.

**One of the seven files is a dated release record and does not move.**
`packages/theurian-core/CHANGELOG.md`'s hit sits under `## [0.1.0.dev5] -
2026-08-19`, and correcting a released section would falsify it. The rule is
`owner_position_cites`'s: a dated entry describes a shipped tree, while
`[Unreleased]` describes the tree a reader has checked out.

**The key's recorded limit.** It matches one physical line, so a statement of
the residual split across a wrap is invisible to it, and it misses the places
that spell the residual differently. The number is a **dispatch input**,
re-measured when slice B3 is briefed, not a claim about how many sentences are
wrong. The T-15 entry itself is the one that must be rewritten *per control*
rather than repointed: the residual narrows, it does not vanish, because
decision 1's floor does not prove a merge into a protected branch.

## Consequences

### Positive

- **The last unenforced link in ADR-0013's chain gets a check.** Proposal → PR →
  human merge → `migrate apply` is enforced structurally at the MCP end already;
  this closes the end that was pure convention.
- **Phase B's stated precondition is satisfied by a change rather than by a
  plan.** The threat model's own lesson applies here — "an owner has to be the
  change that would implement the control, and an epic in the right milestone is
  not automatically that."
- **The failure mode is a refusal with a remedy, not a silent apply.** An agent
  or a script that moved a file into `.theurian/migrations/` gets told what is
  missing.

### Negative

- **The floor is lower than the sentence "enforces the merge" suggests, and this
  ADR's own title is the thing to watch.** A local commit on a local branch
  passes. What is enforced is *committed*, not *reviewed*, and decision 1 states
  that limit rather than leaving the title to imply otherwise. The T-15 rewrite
  owed at slice B3 must narrow the residual, not delete it.
- **A new spawn site.** Every spawn site is a hole no name-based scan can fully
  watch — `test_network_call_sites.py` records that about itself — and this adds
  the fifth.
- **Development friction, and an escape hatch that will be reached for.** The
  flag exists because the friction is real; a flag that everyone types every
  time is a control that has become a habit, which is a thing to watch for
  rather than a thing this ADR can prevent.
- **Repositories with unusual git layouts will meet decision 3.** A worktree, a
  submodule, a `GIT_DIR` pointed elsewhere: each is a case the check has to
  answer correctly or it refuses an honest project. That is implementation work
  slice B3 owes, and it is a real cost rather than an edge note.

### Neutral

- **`last_seen_commit` is unaffected.** It stays descriptive, and this check does
  not read it: a stored value from an earlier run is not evidence about the file
  in front of the current one.
- **`theurian propose accept` is unchanged.** ADR-0027's validate-before-move
  gate runs where it ran; this is a second, later gate answering a different
  question.
- **Nothing about *who* approved is recorded.** That stays Git and PR metadata by
  design.

## What this does not close

1. **Whether the commit reached the default branch through a reviewed pull
   request.** Decision 1 names this as the deliberate floor. Raising it needs a
   forge, and a network call inside `migrate apply` is a different decision with
   different costs.
2. **Who merged.** Approval identity stays Git/PR metadata outside Theurian's
   model — [#281](https://github.com/theurian/theurian/issues/281) verified zero
   hits for an approver field across `src/` and `schemas/`, and records that
   this stays.
3. **The approval-provenance pointer.** [#281](https://github.com/theurian/theurian/issues/281)
   proposes recording the delivering merge commit in `migration_history` — "a
   **pointer to the approval event, not an approver field** and not an
   enforcement mechanism", and its own text says it explicitly does not close
   T-15's residual. It is adjacent work with its own issue and is not folded in
   here.
4. **The exact flag spelling and the exact refusal wording.** Slice B3's.
5. **Non-git version control.** A project under something other than git has no
   check here and takes the flag; designing a second backend is not this ADR's.
6. **`theurian ingest` and the index build.** Neither applies a migration, so
   neither is in this control's population.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **Require only that the file is tracked** | `git add` with nothing committed passes it, and that is not an approval by any reading. The staged-not-committed state is exactly what a script that wanted to look compliant would produce. |
| **Require only that the content appears somewhere in history** | A file committed once and edited afterwards passes, and the bytes that apply are not the bytes anyone reviewed. This is the same class as ADR-0027's digest pin, one layer out: what matters is that *these* bytes were approved, not that some ancestor of them was. |
| **Ask the forge whether the commit is on a merged pull request** | It would raise the floor to something worth the title, and it costs a network call and a credential inside `migrate apply` — a command that works offline today, on a machine that may have no `gh` login. ADR-0030 took a deliberate, heavily-argued route to make one command reach GitHub; making the *apply* path reach it is a much larger decision than T-15's residual justifies. |
| **A configuration key instead of a flag** | Invisible at the moment of use. A project that set it once stops enforcing anything, and nobody reading the failed command sees why. Decision 2's whole content is that the disable is in the command line. |
| **Apply silently when the tree has no git** | Fail-open on a missing input, which is the shape ADR-0031 decision 5 refuses for the same reason — a control that applies wherever its input happens to be present is a control a caller removes by removing the input. |
| **Enforce it in the migration engine rather than at the CLI** | The engine applies a `MigrationSet` that the loader has already read off disk; by then the file is bytes in memory and the filesystem question has been answered somewhere else. The CLI band is where the other pre-apply refusals sit, and where a refusal costs nothing because `create_database` has not run. |
| **Record the delivering merge commit instead of refusing (#281)** | Provenance is not enforcement, and #281 says so itself: "the pointer records provenance, it does not gate." The two are complementary, and doing the recording instead of the check would answer a different question than the one T-15 asks. |

## Compliance

**This ADR ships no behaviour, so it has no shipped test to name.** Its
enforcement at design time is the measurements it cites; its enforcement at
implementation time is the tests slice B3 owes. The names below are the
properties an implementation must pin, not files that exist today — the same
honest split [ADR-0030](0030-github-review-ingestion-spawns-gh.md) states for
the same reason.

Measured now, and reproducible from this ADR (2026-09-12, `be977ea7`):

- `migrate apply` reads git **three** times — `repository_url`,
  `default_branch`, `current_commit`, all in the `Project` construction in
  `cli/commands.py` — and compares none of the results.
  `git grep -n "last_seen_commit" be977ea7 -- packages/theurian-core/src`
  returns **8** lines across five files (a write in `project register`, the
  write here, the domain field, the schema column, three upsert lines and the
  read-back), and none is a comparison.
- The shipped package builds **5** `git` argument vectors in **2** modules, and
  none is a tracking question — the table and its recorded limit are in
  *Context*.
- `PROCESS_SPAWN_SITES` holds **4** entries and is asserted by equality against
  the whole set (`tests/unit/test_network_call_sites.py`).
- `infrastructure/git/` holds exactly one module besides its `__init__.py`:
  `trailer_source.py`.
- The merge-unenforced prose population is **11 lines across 7 files**, key and
  measured exclusion above, of which **1 file** is a dated release section that
  does not move.

Still owed, with the milestone that will satisfy it:

- **Slice B3 — the predicate is tracked *and* byte-identical to `HEAD`
  (decision 1).** Owed: three driving cases, because two would not separate the
  predicates — a committed-and-unmodified migration applies; a tracked,
  staged-but-never-committed one refuses; a committed-then-edited one refuses.
  The third is the one that distinguishes this predicate from
  *committed-anywhere-in-history* and is the test that goes RED if the
  implementation drifts to the weaker check.
- **Slice B3 — the escape hatch restores the old behaviour and is a flag
  (decision 2).** Owed: each refusing case above applies under the flag, plus
  the property that no configuration key selects it — a test that reads the
  config schema and asserts no key does, in the shape
  `tests/unit/test_config_key_call_sites.py` already uses for config-key
  claims.
- **Slice B3 — a tree with no git refuses, and the refusal names the flag
  (decision 3).** Owed: a driving case over a directory that is not a
  repository, asserting the refusal and that its remedy names the flag; with the
  control that the same tree applies under the flag.
- **Slice B3 — the refusal leaves no database behind (decision 4's seat).**
  Owed: the tree is diffed after a refused apply, in the shape
  `tests/integration/test_proposal_service.py::test_generation_writes_only_under_the_proposal_directory`
  uses — the property #63 and #210 established for the refusals already in that
  band, extended to this one rather than assumed to carry.
- **Slice B3 — the new spawn site joins `PROCESS_SPAWN_SITES`, and its argument
  vector is fixed.** Owed: the equality pin grown by exactly one entry, plus the
  checklist that file states — a test that the vector is fixed by the adapter,
  that it cannot be handed a URL or a remote, and that it carries a timeout.
- **Slice B3 — the git query is bounded and does not trust its input.** A
  migration filename reaches the adapter, and a filename is a path. Owed: the
  vector uses `--` separation so a filename cannot be read as a revision or an
  option, driven by a filename shaped like each.
- **Slice B3 — the residual population is rewritten *per control*, in the same
  commit as the check.** The key and its measured exclusion are in decision 5.
  The T-15 entry narrows rather than closing: what becomes enforced is
  *committed*, and what stays owed is *merged into a reviewed branch*. Whether
  that rewrite is faithful is a reading and no mechanical check reaches it,
  which is said here rather than left to be inferred from a test name beside it.
  The dated CHANGELOG section is **not** a mover.
