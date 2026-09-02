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

> **Amended in Milestone 7, by the write-path design CL
> ([#316](https://github.com/theurian/theurian/issues/316),
> [ADR-0028](0028-a-local-proposal-is-a-different-directory.md)). This ADR's
> two lists are not the same partition as "tracked / ignored", and the managed
> `.gitignore` block stops being a list of derived paths.**
>
> Everything above stays true of *derived* artifacts, and the controlling
> requirement is unchanged. What implementing #265 revealed is that this ADR's
> framing had been read as an equivalence — git-ignored *means* derived —
> because until now every entry in `GITIGNORE_ENTRIES` happened to be one. The
> block `theurian init` writes even says so in its own header comment,
> `# Derived artifacts. Rebuilt from Git-tracked migrations (ADR-0004).`
>
> ADR-0028 adds `.theurian/proposals-local/` to that block. It is **authored
> content that is deliberately not committed**, and nothing rebuilds it. So
> from Milestone 7 there are three categories, not two: Git-tracked inputs,
> derived artifacts, and authored-but-local content. Only the second is
> rebuildable, and only the second is what `Project.is_derived` and
> `DERIVED_SUBDIRECTORIES` may grow — `doctor` uses `is_derived` to tell an
> operator that a tracked path is a rebuildable artifact, and saying that about
> a local proposal would be false in the direction that loses work.
>
> The corollary above applies to the new category with full force and is the
> reason ADR-0028 records `git clean -xdf` as an accepted residual rather than
> a defect: a local proposal must not be the only home for something a human
> needs to keep, and on the dogfood machine it is not — the operator's vault is
> the source and Theurian holds a copy.

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
  every derived path **today**, asserted by
  `tests/unit/test_project_and_traceability.py::test_gitignore_block_covers_every_derived_location`
  and `tests/e2e/test_migration_workflow.py::test_gitignore_covers_every_derived_path`.

  "Every" is the reader's inference, not the test's: it asserts seven string
  literals rather than deriving them from `DERIVED_SUBDIRECTORIES`, which is the
  tuple `is_derived_path` actually branches on. Measured — adding a fifth entry
  to `DERIVED_SUBDIRECTORIES` and not to `GITIGNORE_ENTRIES` leaves both tests
  green, so a future derived directory would be committed with nothing
  complaining. Correct now, unenforced against change; the fix is one line in the
  unit test and is left to whichever milestone adds the fifth directory, because
  writing it now is the kind of change this PR is not making.

- **Derived state is not merely git-ignored; it is refused at serve time unless
  this installation built it** (`0.1.0.dev4`, threat-model T-19). The ignore keeps
  derived state *out* of Git in the ordinary case, but a contributor can
  force-add it past the ignore (`git add -f`) and ship it, and a ZIP or tarball
  carries it with no tracking metadata at all. So the "never Git-tracked"
  corollary is enforced at the point it protects: `theurian migrate apply`,
  `theurian index build` and `theurian findings build` each record what this
  install built in `THEURIAN_DATA_DIR/provenance.json` — out of the repository
  tree — and every MCP read path refuses a `.theurian/state/` artifact that record
  does not name (`BuildProvenance`, `verify_state_provenance`;
  `packages/theurian-core/tests/integration/test_state_provenance.py`).
  Delivery-independent by construction: it does not ask Git what is tracked, so it
  holds for a clone and a repackaged tarball alike.

  **"Does not name" rather than "whose hash is not in it", because the three
  families are keyed two different ways.** The canonical state is recorded under
  its state hash and the retrieval index under its build id — both per-build
  values, so a record vouches for one particular artifact. The review-finding
  store is recorded under the constant `FINDINGS_STORE_ID`, because `findings
  build` rebuilds wholesale under one filename and there is no per-build id to
  record; what the record answers there is "has this installation ever built this
  project's findings store", which is the whole question for an artifact that is
  replaced rather than accumulated. A sentence that said "hash" covered two of the
  three and read as an exemption for the one it did not name.

Still owed, with the milestone that will satisfy it:

- **Nothing rebuilds from an empty database and compares the result.** This
  section said "CI job `empty-db-rebuild` applies all migrations to an empty
  database and compares the resulting canonical state against a golden fixture".
  No such job exists — the name appeared in four documents and in no workflow —
  and no test or script does the equivalent. FR-K4 is what this
  ADR's whole argument rests on, so the gap is at the load-bearing point:
  [#64](https://github.com/theurian/theurian/issues/64) (Milestone 6).
- **`theurian doctor` does not ask Git what is tracked.** This section said it
  warns when a derived artifact is tracked. `probe_gitignore` now judges
  *managed-block identity* — whether the span between Theurian's markers is
  byte-for-byte the block `theurian init` writes ([#87](https://github.com/theurian/theurian/issues/87))
  — which is stricter than the old substring check but still reasons only about
  `.gitignore` text, never about what Git holds: a database committed before the
  block was added, or force-added past the ignore with `git add -f`, stays
  committed and the probe reports `SATISFIED`. Block identity is also blind to
  anything outside the markers — a later `!.theurian/state/` re-inclusion, or a
  nested `.theurian/.gitignore` this step never opens — and those residual faces
  are recorded on #64's body and comment. The check the ADR describes —
  `git ls-files` over the derived paths — is the one worth having, and is filed
  with #64 because it is the same guarantee from the other end.
- **The rebuild-determinism test is over the hash function, not over a
  rebuild.** This section claimed an integration test asserting a second rebuild
  from the same inputs produces an identical state hash.
  `tests/unit/test_state_hash.py::test_hash_is_stable_across_processes` is the
  test that exists: it calls `compute_state_hash` in three separate interpreters
  under differing `PYTHONHASHSEED` values and requires one answer. That holds
  determinism of the *function*. Nothing applies a migration set twice and
  compares what the store ended up holding. Also #64.
