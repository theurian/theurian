# ADR-0005: Knowledge migrations are YAML domain operations, not SQL

- Status: accepted
- Date: 2026-08-01
- Deciders: Theurian maintainers
- Requirements: FR-K2, FR-K3, FR-K4, §4.4 and §15 of the brief

## Context

Knowledge state changes need an auditable, reviewable, replayable log. Schema
migrations traditionally use SQL. Here SQL would be a category error:

- It pins the domain log to one storage engine, contradicting the PostgreSQL and
  document-DB targets in ADR-0003.
- `UPDATE knowledge_items SET status='approved' WHERE ...` is unreviewable as a
  statement about knowledge. A reviewer must reconstruct intent from a mutation.
- SQL cannot express `expectedRevision` optimistic concurrency without hand-written
  guard clauses in every statement.
- A structural schema change (adding a column) and a knowledge change (approving
  an ADR) are different concerns with different review audiences and different
  rollback semantics.

## Decision

Two migration systems, deliberately separate:

| | Schema migration | Knowledge migration |
| :-- | :-- | :-- |
| Format | engine-native (SQL for SQLite) | YAML |
| Owns | table structure of a derived store | canonical knowledge state |
| Lives in | `packages/theurian-core/src/theurian/migrations/` | `.theurian/migrations/` in the user's repo |
| Git-tracked as truth | no — the store is derived | yes |
| Reviewed by | Theurian maintainers | the user's team |

Knowledge migration format:

```yaml
apiVersion: theurian.dev/v1
id: 01K1DEFABC1234567890            # ULID; lexical order == creation order
createdAt: 2026-08-01T00:30:00+09:00
author: user@example.com
dependsOn: [01K1ABCXYZ1234567890]
operations:
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: 01K1DEFREV1234567890
    expectedRevision: 01K1ABCREV1234567890
    contentFile: ../knowledge/architecture/auth-policy.md
    metadata: { ... }
```

Rules the engine enforces:

1. Migration IDs and revision IDs are ULIDs.
2. An applied migration is frozen. Same ID with a different checksum is a
   **fatal** error, never an auto-repair.
3. `dependsOn` is topologically sorted; cycles are rejected before any operation runs.
4. `expectedRevision` provides per-item optimistic concurrency.
5. Re-application is a no-op — idempotence is a property of the engine, not of
   each migration author.
6. `contentFile` is resolved with `realpath` and must remain inside the Project
   root, including through symlinks (SEC-7).
7. Every referenced source file's checksum is verified.
8. Applying all migrations to an empty store reproduces the full canonical state.

The operation set is closed: `createItem`, `upsertRevision`, `deprecateItem`,
`restoreItem`, `addRelation`, `removeRelation`, `addAlias`, `removeAlias`,
`changeSensitivity`, `changeOwner`, `registerSpecification`,
`supersedeSpecification`, `addEvidence`, `removeEvidence`. Adding an operation is
a protocol change and requires a version bump of `apiVersion`.

Body content lives in a separate file referenced by `contentFile`, not inline in
the YAML. Reviewers read a normal Markdown/YAML/JSON diff, and content hashing is
over the file's bytes rather than over a YAML-escaped copy of them.

## Consequences

### Positive

- The log is storage-independent: replaying it into PostgreSQL is an adapter, not
  a rewrite.
- A pull request that approves an ADR reads as an approval, because the operation
  is literally named.
- `expectedRevision` makes conflicting concurrent knowledge edits detectable
  rather than last-write-wins.
- Content diffs stay in the content's native format.

### Negative

- Theurian must implement its own migration engine, including topological sort,
  checksum verification, and idempotence. That is roughly Milestone 1.
- YAML has sharp edges (the Norway problem, anchors, billion-laughs). Mitigated by
  strict JSON Schema validation, `yaml.safe_load`, and parser input limits (SEC-8).

  > **Amended in Milestone 7, by the migrate-validate error-translation CL
  > ([#291](https://github.com/theurian/theurian/issues/291)).** For the
  > billion-laughs / alias-DAG case this was backwards: "strict JSON Schema
  > validation" was the amplifier, not the mitigation. `yaml.safe_load`
  > collapses a YAML anchor referenced through a doubling alias chain to shared
  > object identity, so the *parsed* structure stays small — but `jsonschema`
  > interpolates the failing instance with `{instance!r}`, and that repr
  > re-expands every shared reference, building a 46 MB rejection message from a
  > ~500-byte file at alias level 22 (measured 2026-08-21, `jsonschema` 4.26.0)
  > before any refusal string exists. None of the three accepted controls caught
  > it: JSON Schema validation *is* where the blow-up happens, and neither
  > `safe_load` nor the parser byte cap (a ~500-byte file passes both) ever sees
  > the expansion.
  >
  > The actual mitigation is the migration loader's own un-memoised node walk,
  > `migration_loader.py`'s `MAX_DOCUMENT_NODES` (100,000), which counts the
  > *expanded* node count ahead of `validate` and refuses the bomb before
  > `jsonschema` reaches it — deliberately not collapsing shared references,
  > because a collapsed count would wave the ~24-distinct-node file through. Its
  > sibling `MAX_DOCUMENT_NESTING` (64) bounds depth for the same reason: past
  > the interpreter's C recursion budget `jsonschema` cannot build its own
  > refusal message, and the `RecursionError` that follows is indistinguishable
  > from a corrupt schema. This is the same un-memoised-walk shape as
  > [#245](https://github.com/theurian/theurian/issues/245)'s OpenAPI `$ref`
  > walk, in another seam, and it is recorded under T-6 in the threat model.
  >
  > The migration path's validate-time controls are **four**, not one, and they
  > split by *where* they act. Three are refused ahead of `validate` in the same
  > walk — nesting (`MAX_DOCUMENT_NESTING`, 64), the node count above
  > (`MAX_DOCUMENT_NODES`, 100,000), and total *rendered magnitude*
  > (`MAX_DOCUMENT_RENDERED_CHARS`, 1,000,000). The third closes a class the node
  > count cannot see — one large scalar aliased into many slots is only a handful
  > of nodes but re-expands under `{instance!r}` to N times its rendered width —
  > by charging every leaf's O(1) width (`_rendered_width`) per un-memoised
  > reference, whatever the leaf's type. It catches both faces of that class: a
  > large *string* aliased into many slots (a hundreds-of-gigabytes transient
  > that raises `MemoryError`, which is not a `ValueError` and would escape the
  > scalar catch below as a raw traceback), and — added by the round-two review,
  > which the round-one budget missed because it charged only `str`/`bytes` — a
  > medium *integer* of a few thousand digits aliased into many slots, which
  > reprs without raising and is O(N) nodes, so it defeated the node count and
  > the scalar catch alike until the budget began charging integers too. The
  > fourth control is for the *single* giant integer the pre-walk cannot refuse:
  > one node whose lone width passes the aggregate budget, it reaches `validate`
  > and is *translated by type* — `jsonschema` renders it past CPython's
  > `int`→`str` limit and raises `ValueError` (a float `multipleOf` would raise
  > `OverflowError`), and `_validate_document` catches the whole
  > `(ValueError, ArithmeticError)` class as a `MigrationError` rather than a raw
  > `--json` traceback. The budget and the catch are complementary, not
  > redundant.
  >
  > The `safe_load` and parser-limit half of the sentence still holds for the
  > Norway problem and for load-time anchor handling; what it did not cover is
  > validate-time re-expansion.

### Neutral

- The separation means two `migrate` commands with distinct semantics. Named
  distinctly in the CLI to prevent confusion.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| SQL migrations | Storage-coupled; unreviewable as knowledge statements; no clean optimistic concurrency. |
| Front matter in Markdown as the sole state | Cannot express relations, dependencies, or ordering; conflates content with state transitions (see ADR-0010). |
| Event-sourced JSON Lines | Similar semantics, far worse review ergonomics in a pull request. |
| An imperative Python migration script | Turing-complete migrations are not analyzable, not portable, and not safely replayable. |

## Compliance

- `schemas/migrations/migration.schema.json` is the normative format; the engine
  validates every file against it before parsing.
- Unit tests cover ULID validation, checksum mismatch, topological sort, cycle
  detection, `expectedRevision` conflict, idempotent re-application, and path
  escape via both `..` and symlink.

Still owed, with the milestone that will satisfy it:

- **Nothing enforces FR-K4.** This section said "the `empty-db-rebuild` CI job
  enforces FR-K4". That job does not exist, and no test rebuilds the canonical
  state from an empty database.
  [#64](https://github.com/theurian/theurian/issues/64) (Milestone 6). The unit
  tests above cover the engine's behaviour on each operation; what is missing is
  the whole-set property FR-K4 states — that the Git-tracked migrations are
  sufficient to reconstruct everything.
