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
describe*, not *no reads at all*.** An apply reaches git **four** times, and the
four split three to one:

| # | Call | Helper | What it decides |
| :-- | :-- | :-- | :-- |
| 1 | `rev-parse --show-toplevel` | `find_git_root` (`cli/context.py`) | whether the command runs at all |
| 2 | `remote get-url origin` | `repository_url` | nothing — stored as `Project.repository_url` |
| 3 | `symbolic-ref --short HEAD` | `default_branch` | nothing — stored as `Project.default_branch` |
| 4 | `rev-parse HEAD` | `current_commit` | nothing — stored as `Project.last_seen_commit` |

**The count and the order are a trace of the shipped CLI, not a reading of the
source.** A `git` shim first on `PATH` recorded every invocation of a real
`theurian migrate apply`, run in a scratch repository under a redirected `HOME`
and `THEURIAN_DATA_DIR` (2026-09-12):

```console
$ cat -n "$GIT_TRACE_LOG"
     1	rev-parse --show-toplevel
     2	remote get-url origin
     3	symbolic-ref --short HEAD
     4	rev-parse HEAD
```

Calls 2 to 4 are read-only metadata built into the `Project` record in
`cli/commands.py`'s `migrate_apply`, and **none of the three is ever compared to
anything.** `last_seen_commit` is the clearest case — its whole population is a
write in `project register`, a write here, the field on `domain/project.py`, the
column in `infrastructure/sqlite/schema.py`, three lines of the upsert in
`infrastructure/sqlite/store.py` and the read-back that reconstructs the
object:

```console
$ git grep -n "last_seen_commit" be977ea7 -- packages/theurian-core/src | wc -l
       8
$ git grep -l "last_seen_commit" be977ea7 -- packages/theurian-core/src | wc -l
       4
```

Eight sites across **four** files, none of them a comparison — `cli/commands.py`
(two writes), `domain/project.py` (the field), `infrastructure/sqlite/schema.py`
(the column) and `infrastructure/sqlite/store.py` (three upsert lines and the
read-back).

**Call 1 is the one that decides something, and the earlier draft of this
document did not count it at all.** `find_git_root` is the read `resolve_context`
uses to answer *is there a working tree here*, and the command refuses when the
answer is no (decision 3). It is a real gate, and it is not this ADR's gate: it
asks whether git is present, never what git *tracks*. So the gap is not that
`migrate apply` cannot reach git. It reaches it four times, asks once whether a
repository exists, and asks nothing at all about the file it is about to apply.

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

**The predicate is evaluated against the bytes the engine applies, not against a
second read of the file.** The loader already digests each migration file's raw
bytes — `Migration.checksum` is `ContentHash.of_bytes(raw)` over exactly what it
read, and `Migration.source_path` is that file's project-relative path
(`infrastructure/filesystem/migration_loader.py`). The check is therefore a
comparison of two digests: `migration.checksum` against `ContentHash.of_bytes`
of what `git cat-file blob HEAD:<source_path>` hands back. It is not a `stat`,
and it is not a re-read.

That is what closes the check-to-load race **by construction rather than by
timing**. The shape that loses the race is *check the file on disk, then let the
engine load it*: between the two reads the actors table's untrusted same-UID
process replaces the file, and the control certifies bytes that never apply.
There is no window here, because there is no second read of the working tree.

**One query answers both halves of the predicate**, which is why it is one
query rather than a `ls-files` followed by a `diff`. `git cat-file blob
HEAD:<path>` fails when the path is not in `HEAD` — which *is* the tracked
half, since a staged-but-never-committed file is not — and hands back the
approved bytes when it is. Measured in a scratch repository (2026-09-12):

```console
$ printf 'approved\n' > sub/m.yaml && git add sub/m.yaml && git commit -qm add
$ printf 'tampered\n' > sub/m.yaml
$ git cat-file blob HEAD:sub/m.yaml | shasum -a 256 | cut -d' ' -f1
7f8518f7db5e9a55049f49c4ea6d6e8f509695231e60cbd607bcb36c88a75a14
$ shasum -a 256 < sub/m.yaml | cut -d' ' -f1
92e78d0b032962f47792a9fa95fd981ef63e1e3ef074d536d6304c75eddbe29f
$ printf 'x\n' > sub/staged.yaml && git add sub/staged.yaml
$ git cat-file blob HEAD:sub/staged.yaml; echo $?
fatal: path 'sub/staged.yaml' exists on disk, but not in 'HEAD'
128
```

The `<rev>:<path>` spelling also costs less to make safe than a separate path
argument would: the whole argument begins with `HEAD:`, so a filename shaped
like an option is read as a path. Driven with a committed file literally named
`--force.yaml`, `git cat-file blob 'HEAD:sub/--force.yaml'` returned its
contents and exited 0. That closes the option half of the untrusted-filename
question and not the rest of it, which stays owed in *Compliance*.

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

**Whose reach it narrows, actor by actor** — because "closes the gap" is not one
statement, it is four, one per actor in `docs/security/threat-model.md`'s own
table who can author a migration, and they are not worth the same:

| Actor (threat model's own table) | What the floor costs them | Worth |
| :-- | :-- | :-- |
| An untrusted same-UID process | `git add && git commit` — two commands it can already run, since it has the user's account | **A speed bump.** It converts a silent apply into one that leaves a commit in the repository, and nothing more. Said plainly here rather than implied away by the ADR's title |
| An MCP client, through the write-intent tools (ADR-0032) | Nothing today; **everything once slice B4's draft-only facade lands** — at which point it cannot reach `.theurian/migrations/` at all, because the tools write under `.theurian/proposals/` and the distance to the applied directory is a human's `propose accept` plus a merge | **Real, and it is the reason this is a Phase B precondition** — *conditional on that facade*. See the note below the table; this is the actor whose population Phase B multiplies |
| A repository contributor | Nothing. A migration that reached the working tree through a merged pull request is tracked and byte-identical to `HEAD` **by construction**, so it passes the check by definition | **Zero, and that is the row that shows what the floor is.** Decision 1's predicate asks *committed*, and a contributor's migration is committed; the control that stands between this actor and approved knowledge is the human reviewing the pull request, which is ADR-0013 point 4 and not this ADR |
| A human operator mid-development | One flag on the command (decision 2) | **The intended user of the escape hatch**, not a defeat of the control |

**The MCP-client row states a property that does not hold yet, and the
conditioning is the point.** `.theurian/migrations/` is unreachable to that
caller only when nothing it can call can write there.
[ADR-0032](0032-the-write-intent-mcp-tool-surface.md) decision 8 records that
this is **not** today's state and names the two controls slice B4 owes for it: a
draft-only facade at the MCP composition root, and the forbidden-name set grown
to the application-layer movers. Its measurement is that
`test_no_registered_tool_can_reach_a_canonical_write`'s forbidden set is 17
names and contains neither `draft`, `accept` nor `_commit`, so a tool holding a
`ProposalService` and calling `accept()` passes the sweep green while `_commit`
writes into `.theurian/migrations/`. Round 2 of this pull request's review
records that gap as "driven with a positive control (accept/`_commit` pass
green; `append_revision` caught)". Until B4's facade lands, this row's worth is
the plan's, not the tree's.

The Positive section below is scoped to that row for that reason — and is scoped
the same conditional way: the last unenforced link gets a check against the
actor Phase B is about to add **once that actor is contained**, and a speed bump
against the actor who was already inside the boundary.

### 2. There is one escape hatch, it is a flag, and using it is visible

An explicit flag — `--allow-uncommitted` is the working name; the exact spelling
is slice B3's — restores today's behaviour for the two cases that need it:
development, where a migration is written and applied before it is committed,
and recovery, where the repository is present but its object store cannot answer
for a file and the knowledge still has to be rebuilt.

**Both cases are inside a git working tree**, which is the whole reach of the
flag. A directory with no repository at all never gets this far (decision 3), so
the flag is not a way back into one.

**It is a flag and not a configuration key, and that is the decision.** A
config default is invisible at the moment of use: a project that set it once
applies uncommitted migrations for ever, and nobody reviewing an incident sees
it in the command that ran. A flag is in the command line, in the shell history,
in the CI log and in whatever recorded the invocation. The control this ADR adds
is weak enough — decision 1 says how weak — that making it trivially and
silently disablable would leave nothing.

### 3. A tree with no git already refuses, this ADR does not change it, and the flag does not reach it

`migrate apply` in a directory that is not a git repository refuses **today**,
unconditionally. The refusal is `resolve_context`'s, not this control's:
`find_git_root` returns `None` and the command raises before a project exists
(`cli/context.py`). Driven in the same sandbox as the trace above, against a
directory with no `.git` (2026-09-12):

```console
$ theurian migrate apply
error: <dir> is not inside a Git repository. Theurian scopes a project to a
Git working tree, so that branches and worktrees stay isolated.
Run this inside a Git repository.
$ echo $?
1
$ cat -n "$GIT_TRACE_LOG"
     1	rev-parse --show-toplevel
```

One git call, no project, exit 1, and nothing written.

**So decision 2's flag governs exactly one case: a git tree whose migration file
is not committed.** It does not restore applying where there is no repository,
and at decision 4's seat it could not: the check runs *after the project
resolves*, and in a non-git tree the project never resolves, so the band the
check sits in is never entered.

**An earlier draft of this ADR said the opposite, and the correction is recorded
rather than swept.** It designed a "refuses without the flag" path into a
refusal that already ships, and it owed a driving test for it. That test cannot
go RED — the behaviour it would assert is the behaviour on `main` — and its
stated control, *the same tree applies under the flag*, cannot be constructed at
all without replacing project resolution, which is a change this ADR neither
prices nor proposes. The owed item is deleted rather than re-milestoned: an
obligation whose control cannot be built is a sentence, not an obligation.

What survives of the original reasoning is a **non-goal**, and it is kept as
one. A tree with no git has no approval record for this check to read, so
raising the non-git case to *apply anyway* would be the fail-open shape
ADR-0031 decision 5 refuses. Nothing here proposes to, and ADR-0013 point 4 —
**this project's approval model *is* the merge** — is why the existing refusal is
the right answer rather than an inconvenience to be flagged away.

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

**One record and two pins move with the set, and the coupling is measured rather
than assumed.** The record is `docs/security/threat-model.md`'s T-7 spawn
bullet, which spells the number word **four** and names each of the four module
paths; the pins are `PROCESS_SPAWN_SITES`'s own equality and the test that holds
the bullet against it. Counting the two tests as one record is what an earlier
draft did, and it makes the owed work read as smaller than it is — a prose edit
and two test edits, not one of each.
`tests/unit/test_threat_model_t7_claims.py::test_the_t7_spawn_bullet_names_every_pinned_spawn_site_and_spells_how_many`
derives both sides independently — the fact side from `PROCESS_SPAWN_SITES`, the
prose side from the entry — so the bullet reddens the moment the set grows.
Planting a fifth entry in a throwaway checkout takes both pins RED together,
against a green control on the same two files:

```console
$ python -m pytest .../test_threat_model_t7_claims.py .../test_network_call_sites.py -q
62 passed in 0.81s

# a fifth ("infrastructure/git/merge_state.py", "subprocess") planted
$ python -m pytest .../test_threat_model_t7_claims.py .../test_network_call_sites.py -q
FAILED test_threat_model_t7_claims.py::test_the_t7_spawn_bullet_names_every_pinned_spawn_site_and_spells_how_many
FAILED test_network_call_sites.py::test_no_module_outside_the_recorded_spawn_sites_can_start_another_program
2 failed, 60 passed in 0.72s
```

Slice B3's commit is therefore the commit that grows the set, rewrites the T-7
bullet's number word and module list, and lands the adapter — one commit,
because two would be a red gate in between.

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

- **The last unenforced link in ADR-0013's chain gets a check against the actor
  Phase B adds.** Proposal → PR → human merge → `migrate apply` closes the end
  that was pure convention. **The MCP end is *not* enforced structurally today**
  and this bullet used to say it was: the containment that keeps an MCP client
  out of `.theurian/migrations/` is ADR-0032 decision 8's draft-only facade,
  which slice B4 owes and nothing in the tree holds — decision 1's actor table
  and its note say so. So the honest form of this bullet is that the two
  changes are worth their price **together**: B4 contains the caller, B3 checks
  the directory, and either one alone leaves a path. Against the untrusted
  same-UID process it is a speed bump either way, and against a repository
  contributor it is nothing at all.
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
- **Repositories with unusual git layouts will meet the predicate.** A worktree,
  a submodule, a `GIT_DIR` pointed elsewhere: each is a case the check has to
  answer correctly or it refuses an honest project. That is implementation work
  slice B3 owes, and it is a real cost rather than an edge note.
- **The migration files are parsed before they are refused, and that residual
  stays.** The check's seat is after `resolve_context`, which has already run
  `load_migrations` over every file in `.theurian/migrations/` — so an
  uncommitted file is read, parsed and schema-validated before this control sees
  it. The work is bounded by `validate_migration_document`'s existing caps
  (`MAX_DOCUMENT_NESTING`, `MAX_DOCUMENT_NODES`, `MAX_DOCUMENT_RENDERED_CHARS`,
  recorded for #291 and #245), so it is a bounded residual rather than an
  unbounded one — but "refused before it is read" is not what this control
  delivers, and the alternatives table's engine-seat row is where that trade was
  made.
- **Every temporary-directory harness that runs the CLI must `git init` first**,
  which is decision 3's pre-existing cost rather than one this ADR adds — and
  the population that pays it is two orders larger than "both suites that drive
  the real CLI", which is what an earlier draft of this bullet said. Measured
  over the test tree with one key per figure:

  ```console
  $ K='\["git", "init"|_git\([^)]*"init"'
  $ git grep -n -E "$K" -- 'packages/theurian-core/tests' 'tests' | wc -l
       103
  $ git grep -l -E "$K" -- 'packages/theurian-core/tests' 'tests' | wc -l
        58
  $ git grep -l -E '"migrate", "apply"' \
      -- $(git grep -l -E "$K" -- 'packages/theurian-core/tests' 'tests') | wc -l
        42
  $ git grep -l 'shutil.which("theurian")' -- 'packages/theurian-core/tests' 'tests' | wc -l
         5
  ```

  **103 `git init` sites in 58 files; 42 of those files drive `migrate apply`**,
  and each such harness needs a commit (or the decision-2 flag) once decision 1
  lands, because `git init` alone leaves the migration untracked. Only **5**
  files resolve and spawn the installed binary at all. **Two keys, two limits.**
  The first counts *invocation sites*, not fixtures — a file with three harnesses
  contributes three; so 103 is an upper bound on the harnesses and 58 a lower
  one. The second is a file-level overlap: a file that git-inits in one fixture
  and drives `migrate apply` from an unrelated one is counted, so 42 is the
  upper bound on the files that actually need the change. The remaining 16 files
  contain no `"migrate"` token at all, checked one by one.
- **One of the two fixtures the earlier draft named does not drive the CLI.**
  `tests/integration/test_mcp_tools.py`'s `registry` fixture (:267-291) does
  `git init -q` and then calls `_run("init")`, and `_run` (:294-296) is
  `runner.invoke(app, [*args, "--json"])` — Typer's in-process `CliRunner`, in
  the test process, spawning nothing. `tests/e2e/test_daemon_single_instance.py`'s
  `running_daemon` (:92-110) is the one that spawns: its `cli` closure runs
  `subprocess.run([THEURIAN, *args], ...)` against `shutil.which("theurian")`.
  The distinction matters to slice B3 because a check implemented in the CLI
  layer is exercised by both, while a check that shelled out would be exercised
  by neither in the in-process harnesses.

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
5. **Non-git version control.** A project under something other than git already
   cannot run `migrate apply` at all (decision 3), and this ADR neither changes
   that nor designs a second backend. The flag does not reach it.
6. **`theurian ingest` and the index build.** Neither applies a migration, so
   neither is in this control's population.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **Require only that the file is tracked** | `git add` with nothing committed passes it, and that is not an approval by any reading. The staged-not-committed state is exactly what a script that wanted to look compliant would produce. |
| **Require only that the content appears somewhere in history** | A file committed once and edited afterwards passes, and the bytes that apply are not the bytes anyone reviewed. This is the same class as ADR-0027's digest pin, one layer out: what matters is that *these* bytes were approved, not that some ancestor of them was. |
| **Ask the forge whether the commit is on a merged pull request** | It would raise the floor to something worth the title, and it costs a network call and a credential inside `migrate apply` — a command that works offline today, on a machine that may have no `gh` login. ADR-0030 took a deliberate, heavily-argued route to make one command reach GitHub; making the *apply* path reach it is a much larger decision than T-15's residual justifies. |
| **A configuration key instead of a flag** | Invisible at the moment of use. A project that set it once stops enforcing anything, and nobody reading the failed command sees why. Decision 2's whole content is that the disable is in the command line. |
| **Raise the non-git case to "apply anyway"** | Fail-open on a missing input, which is the shape ADR-0031 decision 5 refuses for the same reason — a control that applies wherever its input happens to be present is a control a caller removes by removing the input. Kept as a rejected alternative rather than as a decision, because the refusal it would overturn already ships (decision 3). |
| **Check the file on disk, then let the engine load it** | It advertises a guarantee it can lose a race for: between the check's read and the loader's, the untrusted same-UID process replaces the file and the control certifies bytes that never apply. Decision 1 compares digests of the bytes the loader *already* read, so there is no second read and no window. |
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

- `migrate apply` reads git **four** times, traced through a `PATH` shim on the
  shipped CLI: `rev-parse --show-toplevel` (`find_git_root`, the one read that
  decides anything — whether a repository exists at all), then
  `remote get-url origin`, `symbolic-ref --short HEAD` and `rev-parse HEAD`, the
  three descriptive reads the `Project` construction in `cli/commands.py` makes
  and compares to nothing.
  `git grep -n "last_seen_commit" be977ea7 -- packages/theurian-core/src`
  returns **8** lines across **4** files (a write in `project register`, the
  write here, the domain field, the schema column, three upsert lines and the
  read-back — the first two both in `cli/commands.py`), and none is a
  comparison.
- **A directory that is not a git repository already refuses**, from
  `resolve_context` and not from any flag: one `rev-parse --show-toplevel`, exit
  1, no project, nothing written. Driven against the shipped CLI in the same
  sandbox as the trace (decision 3).
- A loaded `Migration` carries the digest of the bytes it was read from
  (`checksum = ContentHash.of_bytes(raw)`) and its project-relative
  `source_path`, so decision 1's predicate needs no second read of the working
  tree (`infrastructure/filesystem/migration_loader.py`).
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
- The test tree holds **103** `git init` invocation sites in **58** files, of
  which **42** also drive `migrate apply`, and **5** files resolve the installed
  binary with `shutil.which("theurian")`. Keys and their two recorded limits are
  in *Consequences → Negative*. `tests/integration/test_mcp_tools.py`'s
  `registry` fixture drives Typer's in-process `CliRunner` (`_run`, :294-296),
  **not** the installed binary; `tests/e2e/test_daemon_single_instance.py`'s
  `running_daemon` (:92-110) is the one that spawns it.
- **An MCP client's containment is ADR-0032 decision 8's owed facade, not a
  property of the tree.** That ADR's measurement is that the canonical-write
  sweep's forbidden set is 17 names containing neither `draft`, `accept` nor
  `_commit`, so decision 1's actor table conditions the MCP-client row on slice
  B4 rather than asserting it.

Still owed, with the milestone that will satisfy it:

- **Slice B3 — the predicate is tracked *and* byte-identical to `HEAD`
  (decision 1).** Owed: three driving cases, because two would not separate the
  predicates — a committed-and-unmodified migration applies; a tracked,
  staged-but-never-committed one refuses; a committed-then-edited one refuses.
  The third is the one that distinguishes this predicate from
  *committed-anywhere-in-history* and is the test that goes RED if the
  implementation drifts to the weaker check.
- **Slice B3 — the escape hatch restores the old behaviour and is a flag
  (decision 2).** Owed: each of the two refusing cases above — staged-never-
  committed, committed-then-edited — applies under the flag, plus the property
  that no configuration key selects it — a test that reads the
  config schema and asserts no key does, in the shape
  `tests/unit/test_config_key_call_sites.py` already uses for config-key
  claims.
- **Slice B3 — the refusal's remedy names the flag, and the predicate reads the
  loader's own bytes (decision 1).** Owed: the refusal's remedy asserted to name
  the flag; and a test that the comparison is against `migration.checksum` — for
  example by driving a file whose working-tree bytes are replaced *after* the
  load, which must still apply, because the bytes the engine holds are the
  approved ones. The inverse (replaced before the load) refuses, which is the
  third driving case above.
  **Deliberately not owed: a driving case for a tree that is not a git
  repository.** That refusal already ships unconditionally (decision 3), so a
  test of it cannot go RED against this change, and its control — *the same tree
  applies under the flag* — cannot be constructed without replacing project
  resolution. The earlier draft of this ADR owed exactly that test; it is deleted
  here with the reason, not carried to another milestone.
- **Slice B3 — the refusal leaves no database behind (decision 4's seat).**
  Owed: the tree is diffed after a refused apply, in the shape
  `tests/integration/test_proposal_service.py::test_generation_writes_only_under_the_proposal_directory`
  uses — the property #63 and #210 established for the refusals already in that
  band, extended to this one rather than assumed to carry.
- **Slice B3 — the new spawn site joins `PROCESS_SPAWN_SITES`, and its argument
  vector is fixed.** Owed: the equality pin grown by exactly one entry, plus the
  checklist that file states — a test that the vector is fixed by the adapter,
  that it cannot be handed a URL or a remote, and that it carries a timeout.
- **Slice B3 — the T-7 spawn bullet moves in the same commit as the set.**
  `docs/security/threat-model.md`'s bullet spells **four** and names each module
  path;
  `tests/unit/test_threat_model_t7_claims.py::test_the_t7_spawn_bullet_names_every_pinned_spawn_site_and_spells_how_many`
  and
  `tests/unit/test_network_call_sites.py::test_no_module_outside_the_recorded_spawn_sites_can_start_another_program`
  both go RED when a fifth entry lands — measured, above. Owed: the bullet's
  number word and module list rewritten in the commit that grows the set, so no
  commit in between is red.
- **Slice B3 — the git query is bounded and does not trust its input.** A
  migration filename reaches the adapter, and a filename is a path. The
  `HEAD:<path>` form already forecloses the option half — measured, decision 1 —
  so what is owed is the rest: a filename carrying a `:`, a leading `../`, or a
  newline, driven through the adapter and asserted to refuse or to resolve to
  the file it names, never to a different revision.
- **Slice B3 — the residual population is rewritten *per control*, in the same
  commit as the check.** The key and its measured exclusion are in decision 5.
  The T-15 entry narrows rather than closing: what becomes enforced is
  *committed*, and what stays owed is *merged into a reviewed branch*. Whether
  that rewrite is faithful is a reading and no mechanical check reaches it,
  which is said here rather than left to be inferred from a test name beside it.
  The dated CHANGELOG section is **not** a mover.
