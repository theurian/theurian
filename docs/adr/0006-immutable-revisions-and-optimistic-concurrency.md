# ADR-0006: Immutable revisions with optimistic concurrency

- Status: accepted
- Date: 2026-08-01
- Deciders: Theurian maintainers
- Requirements: FR-K1, FR-K6, INV-1, INV-2, INV-3

## Context

Knowledge is cited. An agent that says "per ADR-0009 we use hexagonal
architecture" is making a claim about a specific version of a specific document.
If that document can be edited in place, every prior citation silently becomes a
claim about text that no longer exists, and there is no way to tell whether a
past decision was made with the information it appears to have been made with.

Concurrently, several people and agents propose knowledge changes against the
same items, on different branches, at the same time.

## Decision

**A `KnowledgeRevision` is immutable. A `KnowledgeItem` is a mutable pointer to
one.**

1. Writing a revision means inserting a new row. There is no `UPDATE` path for
   revision content, metadata, or hash. `restoreItem` and `deprecateItem` move
   the item pointer and status; they never rewrite history.
2. `KnowledgeItem.current_revision_id` names the revision that is current *now*.
   History remains fully queryable.
3. `content_sha256` is computed over the revision body as stored. It is verified
   on read in `doctor` and on write always (INV-3).
4. `upsertRevision` carries `expectedRevision`:
   - absent → the operation must be creating the item's first revision;
   - present and matching the item's current revision → apply;
   - present and not matching → `RevisionConflictError`, with the expected
     revision, the actual revision, and the divergence point.
5. Conflicts are surfaced, never auto-merged. Knowledge is prose and structured
   decisions; an automatic three-way merge of a design decision produces a
   sentence nobody approved.
6. Correcting a mistake creates a new revision. Erasure is reserved for legal or
   security necessity and is an explicit, audited administrative operation
   outside the normal write path.

## Consequences

### Positive

- A citation to `revisionId` is stable forever. `knowledge.trace` can show what a
  decision actually said at the time it was made.
- Concurrent edits from different branches or agents are detected, not silently
  lost.
- Auditing "who changed this and why" is a query, not an archaeology project.
- Content-addressed revisions make deduplication and cache invalidation exact.

### Negative

- Storage grows monotonically. Acceptable: knowledge bodies are text, and the
  volume is bounded by human authorship rate. A retention policy for
  never-approved draft revisions can be added later without changing this model.
- Callers must handle `RevisionConflictError`. This is the intended cost.

### Neutral

- Immutability is what makes the state hash in ADR-0007 meaningful: a state hash
  over mutable rows would not identify a state.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| Mutable rows with an audit table | The audit log and the data drift; citations still break; two sources of truth for history. |
| Last-write-wins | Silently discards a colleague's approved decision. Unacceptable for a governance tool. |
| Automatic three-way merge | Produces knowledge text no human approved — the exact failure mode Theurian exists to prevent. |
| Locking (pessimistic) | Requires a live coordinator, breaks offline and branch-parallel work. |

## Compliance

- `tests/unit/test_domain_invariants.py` asserts INV-1 through INV-3 and that
  revision objects are frozen at the type level.
- `tests/unit/test_migration_engine.py::test_a_stale_expected_revision_is_a_conflict`
  applies two migrations with the same `expectedRevision` and asserts the second
  raises. `test_omitting_expected_revision_over_an_existing_item_is_a_conflict`
  covers the adjacent case, and
  `tests/integration/test_cli_commands.py::test_a_revision_conflict_is_reported_not_merged`
  carries it to the CLI. This section called the first an integration test; it
  runs against the in-memory fake, so what it holds is the engine's decision, not
  the store's behaviour under a real transaction.
- The `CanonicalStore` port exposes no revision-update method — the restriction is
  in the type signature, not only in the documentation.
- `tests/integration/test_canonical_store.py::test_a_revision_cannot_be_moved_out_from_under_the_item_pointing_at_it`
  — decision 2's pointer is scoped by the schema the way every read of it is
  ([#24](https://github.com/theurian/theurian/issues/24), closed in Milestone 6).
  Until then `knowledge_items.current_revision_id` referenced
  `knowledge_revisions(revision_id)` alone, while the two reads that resolve the
  pointer — `get_revision` and `list_revisions` — filter on `project_id` as well,
  so a revision whose `project_id` moved left the item
  pointing at a row its own project-scoped read could not see — and `PRAGMA
  foreign_key_check` reported the file as satisfied. The key is composite over
  `(project_id, revision_id)` since schema version 3 (ADR-0017), and the test
  holds both arms: the stranding `UPDATE` is refused, and a revision no item
  points at is still movable.

**Where INV-2 is enforced, stated because #24 was exactly the mistake of
assuming.** The composite key scopes the pointer to a *project*, and that is the
whole of what the schema says. That the revision belongs to the same *item* is
enforced above it, in two layers and never in the database: by
`KnowledgeItem.with_revision` in the domain
(`tests/unit/test_domain_invariants.py::test_item_rejects_a_revision_belonging_to_another_item`),
and by `append_revision` and `put_item` in both `MigrationWriter` adapters, so a
write constructing a `KnowledgeItem` directly rather than through that method is
refused by the store rather than trusted
(`tests/integration/test_writer_contract.py`, which binds the pair of adapters to
one behaviour). Recorded here rather than left to read as though the foreign key
covered it: revert those guards and the schema accepts a cross-item pointer
inside one project without complaint.
