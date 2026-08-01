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
- An integration test applies two migrations with the same `expectedRevision` and
  asserts the second raises `RevisionConflictError`.
- The `CanonicalStore` port exposes no revision-update method — the restriction is
  in the type signature, not only in the documentation.
