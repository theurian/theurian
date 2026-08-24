# ADR-0016: The state hash covers the working tree, not just committed migrations

- Status: accepted
- Date: 2026-08-02
- Deciders: Theurian maintainers
- Requirements: NFR-5, NFR-6, FR-R7, ADR-0007
- Refines: [ADR-0007](0007-state-hash-partitioned-databases.md)

## Context

ADR-0007 defines the state hash over "the migration set reachable from the
current Git HEAD". Implementing it literally means running `git ls-tree HEAD`
and hashing only committed migration files.

That is wrong for the workflow Theurian is actually used in. An author writes a
migration, runs `theurian migrate apply`, and searches to check the result —
all before committing. Under a HEAD-only rule:

- the new migration is invisible to the state hash, so the state it produced is
  attributed to the *previous* hash;
- two developers with different uncommitted work share a hash and therefore a
  database, and silently corrupt each other's state;
- `theurian migrate apply` on uncommitted work either refuses to run or writes
  into a database keyed by a hash that does not describe its contents.

The last one is the serious failure: a database whose key does not describe its
contents defeats the entire purpose of content-addressing.

## Decision

**The state hash is computed over the migration files present in the working
tree, not over `HEAD`.**

```text
state_hash = SHA256(
    for each migration, ordered by ULID (byte-wise):
        migration_id || migration_file_checksum || referenced_content_checksums
  + schema_version
  + migration_engine_version
)
```

1. Inputs are the files under `<project>/.theurian/migrations/` as they exist on
   disk, plus the content files those migrations reference.
2. Ordering is byte-wise over the ULID text form, never locale collation.
3. No absolute path, mtime, inode, hostname, or environment value enters the
   hash. Two machines with identical file contents produce an identical hash.
4. Git is still what *distributes* the migration set, and a Git worktree is
   still what defines a Project boundary (FR-P5). Git is simply not consulted
   when computing the hash.
5. `theurian migrate status` reports whether the working tree is clean with
   respect to Git, so a user can tell a shared state from a local one.

The consequence worth stating plainly: **an uncommitted migration produces a
state hash nobody else has.** That is correct. It is local work, and it should
occupy a local database until it is committed and shared.

## Interaction with ADR-0005: detecting an edited migration

This decision quietly weakens a guarantee from
[ADR-0005](0005-yaml-knowledge-migrations.md), and the weakening was found by
running the CLI rather than by reading the design.

ADR-0005 requires that editing an already-applied migration be a **fatal**
error. The check compares a migration's file checksum against the checksum
recorded in `migration_history`.

Under this ADR, editing a migration changes the state hash, which routes the
next command to a **different, empty database** — one where nothing has been
applied and nothing looks wrong. The evidence of the edit lives only in the
*previously active* database, which nothing was consulting. The result was a
loud guarantee that had silently become unenforceable exactly when it mattered.

**The check therefore runs against the previously active state as well as the
current one.** Before any command acts, if an active state exists whose hash
differs from the current one, its migration history is read and every recorded
checksum compared. A mismatch is fatal, with exit code 4.

Two properties fall out, and both are the intended behaviour:

- Editing a **migration** is fatal, because it rewrites history that was
  already applied.
- Editing a **content file** forks a new state and is not an error, because a
  new state is exactly what a changed body should produce. The old state remains
  intact and reachable.

## Consequences

### Positive

- The database key always describes the database contents. This is the property
  the whole scheme depends on, and a HEAD-only rule would break it.
- Authoring and applying a migration before committing works normally.
- Two worktrees with different uncommitted work cannot collide.
- No Git invocation on a hot path; hashing is pure filesystem I/O.
- Testable without a Git repository at all, which keeps the migration engine's
  tests fast and independent of Git's behaviour.

### Negative

- Editing a migration file without applying it changes the hash, so the next
  command sees an unbuilt state and builds one. Acceptable: building a state is
  incremental, and the alternative is serving results from a database that no
  longer matches its inputs.
- More distinct state databases accumulate during active authoring. Mitigated by
  explicit `theurian index gc` and by the fact that these are text-derived.
- A user might expect "state hash" to mean "commit". Addressed by reporting the
  Git status alongside the hash rather than by changing the definition.

### Neutral

- Nothing here prevents a future `--from-head` flag for CI, which genuinely
  wants the committed set. The default is what matters.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| Hash the set reachable from `HEAD` | An applied uncommitted migration lands in a database keyed by a hash that does not describe it. The failure this ADR exists to prevent. |
| Hash `HEAD`, refuse to apply uncommitted migrations | Forces a commit before the author can see whether the migration is right. Inverts authoring and review. |
| Include the Git commit SHA in the hash | Overly sensitive: a code-only commit touching no knowledge would invalidate a valid state (already rejected in ADR-0007). |
| Hash `HEAD` plus a dirty-tree marker | Two developers with *different* uncommitted work would share a hash. Worse than either pure option. |

## Compliance

- `tests/unit/test_state_hash.py::test_golden_vector_is_stable` and
  `test_hash_is_stable_across_processes` — a fixture set hashes to a committed
  constant, across runs and `PYTHONHASHSEED` values.
- `tests/e2e/test_migration_workflow.py::test_the_state_hash_does_not_depend_on_the_project_path`
  — the hash is unchanged when the project moves to a different absolute path.
- `tests/unit/test_state_hash.py::test_a_changed_migration_changes_the_hash` and
  `test_a_changed_body_changes_the_hash` — editing either input changes the hash.
- `tests/e2e/test_migration_workflow.py::test_editing_an_applied_migration_is_fatal`
  — applies a migration, edits it, and asserts exit 4 with a checksum-mismatch
  error: the regression that motivated the section above.
- `tests/e2e/test_migration_workflow.py::test_a_new_revision_forks_a_new_state`
  — moving a body forward produces a new state hash with no error, so the two
  cases stay distinguishable. Written as a second revision rather than an edit
  of the landed body since [ADR-0027](0027-accept-validates-before-it-moves.md)
  made `contentSha256` required: an out-of-band rewrite of a pinned body is now
  refused at load, so it is no longer the "no error" half of the pair.

Five bullets, seven tests. Three of the seven are e2e, and no CI job runs the
e2e suite ([#65](https://github.com/theurian/theurian/issues/65)) — including
the one that pins this ADR's own regression,
`test_editing_an_applied_migration_is_fatal`.

Still owed, with the milestone that will satisfy it:

- **Nothing asserts that reverting an edit restores the hash.** This section
  claimed the test above covered both directions. It covers one:
  `test_a_changed_migration_changes_the_hash` compares an edited input against
  the original and requires them to differ. Round-tripping is the property
  branch switching depends on — check out the old commit and you must land back
  on the state you already built — and it is untested. Milestone 6, with
  ADR-0007's O(1) reuse item, which is the same property from the other end.
- **Nothing computes a state hash with no Git repository present.** This section
  claimed a test. ADR-0016's decision is that the hash covers the working tree
  rather than `HEAD`, so working without Git at all is a supported case and this
  is the test that would show it. Milestone 6.
