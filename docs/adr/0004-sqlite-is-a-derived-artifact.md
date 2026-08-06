# ADR-0004: SQLite is a derived artifact, never a Git-tracked one

- Status: accepted
- Date: 2026-08-01
- Deciders: Theurian maintainers
- Requirements: FR-K4, NFR-5, OSS-2, §4.3 of the brief

## Context

Theurian's local canonical store is SQLite. The obvious way to share team
knowledge is to commit the database file. That is wrong for reasons that compound:

- A binary blob does not diff, does not merge, and does not code-review. The
  entire value proposition — knowledge that a team reviews and approves — dies at
  the first merge conflict.
- WAL and shm sidecar files make the on-disk state non-deterministic; two
  machines that applied identical changes produce different bytes.
- Every branch switch becomes a binary conflict.
- Embeddings and RAPTOR trees are model-version-dependent. Committing them
  commits a snapshot of a model's behaviour, which then silently disagrees with
  the model the next contributor has.

## Decision

Git holds inputs. SQLite holds a rebuildable projection.

**Git-tracked (the record of truth):**

- knowledge migrations (`.theurian/migrations/*.yaml`)
- approved knowledge bodies (`.theurian/knowledge/**`)
- structured specifications (`.theurian/specifications/**`)
- schemas (`.theurian/schema/**`)
- RAPTOR build configuration, retrieval evaluation data
- model and prompt version metadata
- source references and, when useful, knowledge change proposals

**Never Git-tracked (derived):**

- `*.sqlite`, `*.sqlite-wal`, `*.sqlite-shm`
- FTS indexes, embeddings, RAPTOR trees, retrieval caches
- raw GitHub review caches, normalized temporary documents
- generated Markdown views (`.theurian/generated/**`)
- `.theurian/state/`, `.theurian/cache/`, `.theurian/runtime/`

The controlling requirement: **`theurian migrate apply` against an empty
database must reproduce the complete canonical state from Git-tracked inputs
alone.** Anything that cannot be rebuilt that way is either a bug or belongs in
Git.

A corollary that constrains design elsewhere: `.theurian/generated/` must never
be the only home for something a human needs to keep. If a generated Markdown
review summary is worth preserving, it is promoted into approved knowledge
through a migration — not rescued from a git-ignored directory.

## Consequences

### Positive

- Knowledge changes arrive as reviewable text diffs. Review-to-knowledge
  promotion becomes an ordinary pull request.
- No binary merge conflicts, ever.
- Branch switching is safe: state is partitioned by state hash (ADR-0007).
- Contributors can rebuild from scratch, which makes "reproduce the bug" a real
  instruction.

### Negative

- A fresh clone needs an index build before search works. Mitigated by
  incremental builds, a progress report, and `/theurian:setup` doing the first
  build once.
- Rebuild cost grows with knowledge volume. Mitigated by state-hash-keyed
  database reuse — the rebuild happens once per distinct state, not per checkout.

### Neutral

- Teams that want a shared warm index build it in CI and publish it as a cache
  artifact. That is a derived-artifact distribution problem, not a Git problem.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| Commit the SQLite file | Binary conflicts; non-deterministic bytes; unreviewable. |
| Commit a SQL dump | Textual but still generated; invites hand-editing the projection instead of the input; duplicates the migration log. |
| Git LFS for the database | Solves storage, not merging, not reviewability, not determinism. |
| Commit embeddings only | Model-version-coupled and large; rebuilt cheaply from Git-tracked content. |

## Compliance

- Root `.gitignore` and the `.gitignore` block written by `theurian init` cover
  every derived path.
  `tests/unit/test_project_and_traceability.py::test_gitignore_block_covers_every_derived_location`
  and `tests/e2e/test_migration_workflow.py::test_gitignore_covers_every_derived_path`.

Still owed, with the milestone that will satisfy it:

- **Nothing rebuilds from an empty database and compares the result.** This
  section said "CI job `empty-db-rebuild` applies all migrations to an empty
  database and compares the resulting canonical state against a golden fixture".
  No such job exists — the name appeared in four documents and in no workflow —
  and no test or script does the equivalent. FR-K4 is what this
  ADR's whole argument rests on, so the gap is at the load-bearing point:
  [#64](https://github.com/theurian/theurian/issues/64) (Milestone 6).
- **`theurian doctor` does not ask Git what is tracked.** This section said it
  warns when a derived artifact is tracked. `probe_gitignore` reads
  `.gitignore` and checks for the string `.theurian/state`; a database committed
  before the block was added stays committed and the probe reports
  `SATISFIED`. The check the ADR describes — `git ls-files` over the derived
  paths — is the one worth having, and is filed with #64 because it is the same
  guarantee from the other end.
- **The rebuild-determinism test is over the hash function, not over a
  rebuild.** This section claimed an integration test asserting a second rebuild
  from the same inputs produces an identical state hash.
  `tests/unit/test_state_hash.py::test_hash_is_stable_across_processes` is the
  test that exists: it calls `compute_state_hash` in three separate interpreters
  under differing `PYTHONHASHSEED` values and requires one answer. That holds
  determinism of the *function*. Nothing applies a migration set twice and
  compares what the store ended up holding. Also #64.
