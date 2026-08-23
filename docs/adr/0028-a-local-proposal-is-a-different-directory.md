# ADR-0028: Proposals stay committable; a local proposal is a different directory

- Status: accepted
- Date: 2026-08-23
- Deciders: Theurian maintainers
- Requirements: FR-K9, SEC-7, T-5, T-15
- Decision recorded in [#265](https://github.com/theurian/theurian/issues/265),
  taken as part of [#316](https://github.com/theurian/theurian/issues/316)'s
  write-path design CL
- **Confirms** [ADR-0013](0013-ai-writes-produce-proposals.md) point 7
  (proposal directories may be committed) rather than reversing it
- **Amends** [ADR-0004](0004-sqlite-is-a-derived-artifact.md)'s decision
  framing: the managed ignore block stops being a list of *derived* paths; see
  the cross-reference amendment there

**Every measurement in this ADR was taken on 2026-08-23 against `main` @
`68e8a0b`.**

## Context

ADR-0013 point 7 says proposal directories may be committed: "They are review
input, and they are the one thing under `.theurian/` that is written by an agent
and read by a person." `ProjectPaths.proposals`' docstring repeats it — "Not
derived, and so not git-ignored."

That is a claim about proposals in general. It says nothing about a proposal
whose *content* must not leave the machine, and the dogfooding corpus is exactly
that case. The live dogfood project holds 82 knowledge items; **56 of them must
never be committed**. What keeps them out today is a glob fence in
`.git/info/exclude`:

```text
# >>> local-knowledge (private-by-default; NEVER commit) >>>
.theurian/knowledge/**
.theurian/migrations/*.yaml
.theurian/proposals/**
# <<< local-knowledge <<<
```

The shape is right — private-by-default, so an untracked addition is invisible
to `git add -A` and publishing takes a deliberate `git add -f`, and a fence that
listed the private notes instead would have to be edited every time one arrived.
What is wrong is where it lives. **`.git/info/exclude` is machine-local.** It
reaches this machine's linked worktrees, because they share the common Git
directory, and it reaches nothing past that: a clone gets no fence at all, and
the next contributor who runs a private draft on a clone has nothing standing
between it and `git add -A`.

The repository is not silent about this — the M7 dogfooding work log records the
fence and its three residuals, and `tests/command_population.py` says a fresh
clone has no `.git/info/exclude` to fence its corpus with. What the repository
does not do is *enforce* any of it. The only repository-side assertion is
`tests/unit/test_dogfood_corpus_governance.py`, whose paths come from the Git
index, so it grades what has already been committed. It cannot object to a
private proposal, because a private proposal that has not been committed is not
in the index, and one that has been committed is already the failure.

So the question this ADR answers: **is a draft proposal a committable review
artifact or a machine-local staging file?**

## Decision

### 1. ADR-0013 point 7 stands. A proposal is committable, PR-deliverable review input.

This is not a default that survived for lack of a reason to change it. The
accept path's hardening was designed *on* this premise and would be orphaned
without it: `accept` rejects a proposal directory that is or contains a symlink
anywhere in its read chain, reads through the same size-capped,
containment-checked path `migrate apply` uses (SEC-7, T-5), and writes every
file with `O_NOFOLLOW` and an explicit `0644` mode — because a committed
proposal directory arrives through a pull request and is therefore untrusted
input. Reversing point 7 would make all of that guard against a threat the
product no longer has, and the guarantee it upholds — "prompt injection can, at
worst, create a file a human will read" — is stated in ADR-0013's own
Consequences.

### 2. The boundary is a location the author chooses at draft time.

`theurian propose --local` writes the proposal under
**`.theurian/proposals-local/<proposal-id>/`** instead of
`.theurian/proposals/<proposal-id>/`. The layout inside the directory is
identical; only the parent differs.

`theurian init` adds `.theurian/proposals-local/` to the managed `.gitignore`
block it writes between its markers (`GITIGNORE_ENTRIES` in
`domain/project.py`, today `.theurian/state/`, `.theurian/cache/`,
`.theurian/runtime/`, `.theurian/generated/`, and the three `*.sqlite*` globs).
**A committed ignore rule is inherited by every clone**, which is the single
property `.git/info/exclude` does not have and the whole reason for the change.

`theurian propose accept` accepts from either location, and a proposal id
present in *both* is **refused**, not resolved by precedence. A silent pick
between two directories that could hold different bytes is the ambiguity class
this project has already paid for elsewhere; the refusal names both paths and
tells the author to remove one.

The reads from the new location go through the *same* code as the reads from
`.theurian/proposals/` — the same symlink refusal, the same containment check,
the same size cap. A second location must not become a second reader; SEC-7 is
held by one implementation or by none.

### 3. `.theurian/proposals-local/` is git-ignored and is *not* derived.

These are two different properties and the codebase keeps them in two different
places. `GITIGNORE_ENTRIES` says what `init` writes into `.gitignore`;
`DERIVED_SUBDIRECTORIES` and `Project.is_derived` say what is rebuildable, and
`doctor` uses the latter to warn when Git is tracking something it should not.
**`.theurian/proposals-local/` joins the first and must not join the second.** A
local proposal is authored content; nothing rebuilds it, and classifying it as
derived would make `doctor` tell an operator that a force-added local proposal
is a rebuildable artifact they can safely delete.

Two consequences of that split have to be handled in the implementation
commit, because both are places where the repository currently states the two
sets are the same:

- **The managed block's own header comment.** `init` writes
  `# Derived artifacts. Rebuilt from Git-tracked migrations (ADR-0004).` above
  the entries. That comment becomes false for one of them. The block needs a
  second labelled line for the non-derived entry, or a comment that no longer
  claims the whole list is derived.
- **`test_dogfood_corpus_governance.py::test_the_managed_gitignore_block_lists_exactly_the_derived_patterns`
  compares this repository's own committed `.gitignore` against
  `GITIGNORE_ENTRIES`, in order, exactly.** It goes RED unless the repository's
  `.gitignore` is edited in the same commit — which is the test working. Its
  name and docstring say "derived patterns", and both stop being accurate at the
  same moment.

`init` creates the directory alongside the others in `INITIAL_DIRECTORIES`, and
it does **not** get a `.gitkeep`. The existing code only marks
`migrations/`, `specifications/` and `proposals/`, with the reason written
beside it: "Derived directories are git-ignored, so marking them would commit a
path that is supposed to be absent from the repository." The same argument
applies to an ignored directory that is not derived.

### 4. Out of scope, with the residues named

Three things this decision deliberately does not do. Each gets an issue at
implementation; none is quietly dropped.

- **An overlay for locally-*accepted* knowledge.** `--local` covers a proposal,
  which is the pre-approval artifact. The dogfood machine's 56 private notes are
  *accepted*: their bodies are under `.theurian/knowledge/` and their migrations
  under `.theurian/migrations/`, both of which are tracked locations that
  ADR-0004 lists as the record of truth. A local-accept overlay is a larger
  design — it interacts with sensitivity enforcement
  ([#119](https://github.com/theurian/theurian/issues/119),
  [ADR-0025](0025-sensitivity-is-enforced-before-0-1-0-stable.md)), because
  "which knowledge is local" and "which knowledge is `confidential`" are two
  answers that must not disagree — and it is its own decision. **Until it
  exists, the machine-local `.git/info/exclude` fence remains the only thing
  keeping the 56 accepted notes out of Git**, and this ADR does not change
  that.
- **A repository-side guard against committing a non-`public` proposal.**
  `test_dogfood_corpus_governance.py` grades tracked migrations after
  acceptance; nothing grades a *proposal* a contributor commits from a public
  repository. `--local` makes the right thing easy; it does not make the wrong
  thing fail.
- **`git clean -xdf` deletes the local overlay.** Precisely because the content
  is invisible to Git, the standard clean removes it, and this is the same
  availability residual the current fence has. It is accepted: the operator's
  vault is the source and Theurian holds a copy, not the original, so recovery
  is manual but possible. ADR-0004's corollary — "`.theurian/generated/` must
  never be the only home for something a human needs to keep" — is the rule
  that makes this an accepted residual rather than a defect, and it applies to
  `.theurian/proposals-local/` with the same force.

## Consequences

### Positive

- The boundary becomes a property of the repository instead of a property of
  one developer's machine. Every clone inherits it, which is what #265 asked
  for.
- Choosing it is an explicit act at draft time — a flag the author types —
  rather than a rule someone has to remember to have installed. The failure mode
  changes from "silent leak" to "the author forgot the flag and sees the
  proposal in `git status`", which is visible.
- ADR-0013 point 7 and the accept path's symlink, containment and `O_NOFOLLOW`
  hardening keep their premise. Nothing built on "a proposal arrives through a
  pull request" is orphaned.
- The committable path stays clean: `git status` after an ordinary
  `theurian propose` still shows the proposal, so a review PR is never
  accidentally empty.

### Negative

- **The privacy of a local proposal depends on the author having passed
  `--local`.** A private draft written without the flag lands in a tracked
  location, and nothing detects that its content should not be there — the
  second out-of-scope item above. This decision reduces the leak surface; it
  does not close it.
- **`git clean -xdf` deletes local proposals**, and the recovery is manual.
- **`doctor` will not warn about a force-added local proposal**, because
  `is_derived` deliberately does not gain the path. The alternative — calling it
  derived — would be a worse lie.
- Two directories mean an ambiguity case that did not exist before. It is
  refused rather than resolved, which is a new failure mode a caller can hit.
- The managed `.gitignore` block stops being homogeneous. Its comment, its test
  name, and the ADR-0004 framing behind all three now need a distinction they
  did not need before.

### Neutral

- `ProjectPaths.proposals`' docstring — "Not derived, and so not git-ignored" —
  stays true of `proposals`. The new sibling property needs its own docstring
  saying the opposite half: not derived, and git-ignored anyway, for a reason
  that is not ADR-0004's.
- The touch set for the implementation commit: `GITIGNORE_ENTRIES` and
  `INITIAL_DIRECTORIES` in `domain/project.py`, the block comment in
  `application/project_service.py`, a `proposals_local` property on
  `ProjectPaths` and on `Project`, the `--local` flag and the proposal-lookup
  path in `cli/propose_commands.py` and `application/proposal_service.py`, this
  repository's own `.gitignore`, and the two tests named in decision 3.
  Re-enumerate with
  `grep -rn 'GITIGNORE_ENTRIES\|INITIAL_DIRECTORIES' packages/ tools/` rather
  than trusting this list.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **Git-ignore `.theurian/proposals/` wholesale — every proposal is machine-local until acceptance** | It contradicts ADR-0013 point 7 and orphans the accept path's untrusted-input hardening, which exists because a proposal arrives through a pull request. It also reproduces the failure it is meant to fix, with the sign flipped: the next legitimately committable proposal becomes invisible to `git status`, and the author opens a pull request with nothing in it. A rule that hides the artifact under review is not a privacy control; it is a different leak of the same kind — information that should have travelled and did not. |
| **Sensitivity-keyed filtering at commit time — a `pre-commit` hook, or `.gitattributes` magic keyed on the proposal's declared sensitivity** | Git's ignore model is keyed on paths, not on content: there is no rule that says "ignore files whose YAML declares `sensitivity: confidential`". What is left is a hook, and a hook is machine-local state installed per clone — which is exactly the `.git/info/exclude` failure #265 measured, wearing a different name. It would also make the boundary depend on a field inside a file the author controls, so a mis-declared sensitivity becomes a silent publication. |
| **Status quo: keep the `.git/info/exclude` fence** | Rejected by measurement, not by preference. `.git/info/exclude` is machine-local; a clone inherits nothing, and it reaches this machine's linked worktrees only because they share the common Git directory. The repository does record the fence — in the M7 dogfooding work log and in `tests/command_population.py` — but recording is not inheriting, and both of those are prose about a file that does not travel. |
| **Nest the overlay as `.theurian/proposals/local/` instead of a sibling** | Git supports an ignore rule inside a tracked tree, so this would work mechanically. It reads badly and it costs the clean statement: "everything under `.theurian/proposals/` is committable review input" would acquire an exception that a reader has to know about, and a `git add .theurian/proposals/` becomes a command whose effect depends on a rule several files away. A sibling directory makes the boundary the thing it is — a location — and makes it visible in a directory listing. |
| **A `local: true` flag inside the proposal, rather than a separate directory** | Git ignores paths. A flag inside a file that Git is already tracking changes nothing about whether it is tracked, so the flag would need a hook to be enforced — the rejected alternative above. It also splits the answer: the file system would say "committable" and the file's contents would say "not", and the two would be consulted by different readers. |
| **Reverse ADR-0013 point 7: proposals are never committable, and review happens elsewhere** | The review venue is the pull request, and it is the whole shape of ADR-0013's "AI proposes, Git reviews, humans approve." Moving proposal review out of Git means inventing a second review surface for the one artifact this product exists to get reviewed, and it discards the accept path's hardening in the process. |
| **Do nothing until the local-accept overlay is designed** | The accepted-knowledge overlay is genuinely larger and genuinely entangled with sensitivity enforcement (#119), which is why it is out of scope here. But the proposal half is separable, it is the half a new contributor hits first, and leaving it means every clone continues to ship with no boundary at all while the larger design is worked out. |

## Compliance

**Nothing in this section has landed.** This ADR is written at the design stage
of #316's CL; the tests below are what the implementation owes.

Owed with the implementation:

- A test that `theurian propose --local` writes only under
  `.theurian/proposals-local/<id>/`, in the shape
  `test_proposal_service.py::test_generation_writes_only_under_the_proposal_directory`
  already uses for the tracked location — a whole-tree diff plus a content
  snapshot of every file outside it, so an overwrite that adds no new path is
  caught too.
- A test that the path is ignored by the `.gitignore` `init` writes, driven
  through Git rather than through string matching: initialise a scratch
  repository, write a local proposal, and assert `git status --porcelain`
  reports nothing. A test that only checks membership in `GITIGNORE_ENTRIES`
  passes against a pattern Git does not actually apply.
- The update to
  `test_dogfood_corpus_governance.py::test_the_managed_gitignore_block_lists_exactly_the_derived_patterns`,
  together with this repository's own `.gitignore` — the test compares the two
  in order and exactly, so it goes RED until both move. Its name and docstring
  are corrected in the same edit, because "derived patterns" stops describing
  the list.
- A test that `Project.is_derived` returns `False` for a path under
  `.theurian/proposals-local/`. This is the one that goes RED if a later change
  "tidies up" by folding the new entry into `DERIVED_SUBDIRECTORIES`, which
  would make `doctor` describe authored content as rebuildable.
- A test that `accept` refuses a proposal id present in both locations, naming
  both paths — not one that asserts a precedence.
- A test that a symlinked local proposal directory is refused exactly as a
  symlinked tracked one is (SEC-7, T-5). If the two locations ever disagree
  here, the containment guarantee has two implementations and one of them is
  wrong.
- A test that `init` creates `.theurian/proposals-local/` **without** a
  `.gitkeep`, so the directory a clone must not commit is not committed by the
  command that creates it.

Still owed, with the issue that will satisfy it — each filed at implementation:

- **An overlay for locally-accepted knowledge.** Until it exists, the 56
  accepted private notes on the dogfood machine are held out of Git by the
  machine-local `.git/info/exclude` fence alone, and a clone inherits none of
  it. This is the largest remaining piece of #265 and it is not closed here.
- **A repository-side guard against committing a non-`public` proposal** from a
  public repository. The corpus governance test grades tracked migrations after
  acceptance; nothing grades proposals.
- **Recovery from `git clean -xdf`.** Accepted as an availability residual with
  a manual recovery, recorded rather than fixed.
