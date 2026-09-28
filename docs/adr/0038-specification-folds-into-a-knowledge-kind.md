# ADR-0038: The Specification entity folds into a knowledge `kind`

- Status: accepted
- Date: 2026-09-29
- Deciders: Theurian maintainers
- Requirements: FR-T1, FR-T2, FR-T3
- **Resolves** [`docs/roadmap.md`](../roadmap.md) §9 ADR candidate 6 — whether
  the `Specification` entity folds into a knowledge `kind`
  ([#277](https://github.com/theurian/theurian/issues/277))
- Situates against [ADR-0005](0005-yaml-knowledge-migrations.md) (the closed
  operation set the retirement changes) and
  [ADR-0010](0010-three-layer-knowledge-model.md) (a structured source is
  preserved, never converted)

**This ADR records a decision and ships no code.** No enum member, operation,
schema or table moves here. It is Phase C slice C1; the closed-enum
extension-compatibility policy
([#274](https://github.com/theurian/theurian/issues/274), roadmap §9 candidate
3) is slice C2, and the traceability representation
([#275](https://github.com/theurian/theurian/issues/275), candidate 4) is C3.

**Every repository fact below was measured on 2026-09-29 against `origin/main`
at `855ebd87`.** Source paths are relative to
`packages/theurian-core/src/theurian/`; paths beginning `packages/`, `schemas/`,
`examples/` or `.theurian/` are from the repository root.

## Context

Two representations of a specification coexist. The uniform model —
`KnowledgeItem`, `KnowledgeRevision`, the closed `KnowledgeKind`, typed
`KnowledgeRelation`s — carries every governed item. Beside it,
`domain/specification.py` declares a `Specification` entity with its own table
(`CREATE TABLE specifications` in `infrastructure/sqlite/schema.py`), its own
identifier (`SpecId`), its own status vocabulary (`SpecificationStatus`) and two
migration operations, `registerSpecification` and `supersedeSpecification`.
Phase C builds traceability edges (FR-T2) and the queries over them (FR-T3) on
one of the two. #277 states why the choice is made now: it is expensive to
reverse once edges point at ids. No edge exists yet: `traceability_edges` is
declared in `schema.py`, and `CanonicalStore.add_traceability_edge` and
`list_traceability_edges` are declared on `domain/ports/canonical_store.py` with
no implementation and no caller.

**The entity is already a sidecar to a knowledge item.**
`application/migration_engine.py`'s `_register_specification` looks up the
operation's `itemId`, refuses when that item has no current revision, and
constructs a `Specification` whose `revision_id` is the item's
`current_revision_id` and whose `title` is the spec id's own string; it passes
no `structured` and no `anchors`. The table's `revision_id` is a foreign key to
`knowledge_revisions(revision_id)`; the table has no `anchors` column and no
`sensitivity` column. `infrastructure/sqlite/store.py`'s
`_specification_from_row` builds a `Specification` without `anchors`, so a
stored specification always reads back with `anchors=()`.

**Its readers are implemented and never called.** `store.py` implements two
readers, `get_specification` and `list_specifications`, and they hold the only
`FROM specifications` SQL in `packages/theurian-core/src`. No call of
`.get_specification(` or `.list_specifications(` exists anywhere in the
repository. The only calls into the store's specification methods are the two
writes in `migration_engine.py`, `register_specification` and
`supersede_specification`. The OKF export (`application/okf_export.py`) walks
knowledge items, revisions and relations and never the `specifications` table,
so today a specification is exportable only as the knowledge item it was
registered against.

**Neither side persists a parsed payload.** `Specification.structured` defaults
to an empty dict, and `_register_specification` never sets it.
`KnowledgeRevision.structured` is declared, stored
(`knowledge_revisions.structured`) and published by `knowledge.get`
(`mcp/tools.py`), but no construction of a `KnowledgeRevision` in
`packages/theurian-core/src` other than the store's row decoder passes
`structured`: the migration engine's does not, and `KnowledgeRevision.create`
has no caller there. The parsers under `infrastructure/filesystem/parsers/` do
produce a parsed form, and `application/ingestion_service.py` carries it into
`IngestedDocument.structured`, whose only reader is the `theurian ingest`
report. What FR-T1 asks for — a specification in its native format without
forcing Markdown — is what the revision body and its preserved `contentType`
already provide (ADR-0010); neither `structured` field contributes to it.

**The roadmap recommends the fold and does not state its constraint.** Roadmap
§4 ("Four additions", item 1) recommends the unified form: spec-as-knowledge in
`kind`, the machine-readable payload in `structured`. §9 candidate 6 only poses
the question. Both halves of that fold move a closed set — a new `kind` member,
and two operations leaving ADR-0005's closed operation set — and the policy for
moving a closed set is candidate 3, #274. The roadmap lists candidates 3 and 6 as owed "before
Phase C" and candidate 4, #275, as Phase C's own.

**Why a decision record precedes Phase C's implementation.** §6 principle 3 ("A
disclosure change is ADR-first") names Phase D's history access as its most
direct case, and the README marks only Phase D ADR-first. Phase C's own
Security row nevertheless opens "A graph response is a new disclosure family",
and this ADR was written first on that row's strength. Whether the roadmap
should label Phase C ADR-first too is a question about the roadmap and is not
settled here.

## Decision

1. **A specification is a knowledge item of `kind: specification`.** Its native
   form is the revision body under its `contentType`; its parsed,
   machine-readable form belongs in `KnowledgeRevision.structured`; its title,
   validity, anchors, lifecycle and supersession are the knowledge model's own
   (table below). `KnowledgeKind` has no `specification` member today, and this
   ADR does not add one — decision 4 says when it lands.
2. **Traceability addresses a specification by its knowledge item id, from its
   first edge.** A `TraceNode` whose `node_type` is
   `TraceNodeType.SPECIFICATION` carries a knowledge item id: the node type
   names the role, and the id space is the knowledge item id space that
   `KNOWLEDGE` and `DECISION` nodes share. How an edge is represented is #275's
   and is not decided here.
3. **Nothing new is built on the entity.** No new code reads or writes the
   `Specification` entity, the `specifications` table, `SpecId`,
   `SpecificationStatus`, `registerSpecification` or `supersedeSpecification`.
   They stay in the tree, unchanged, until the retirement slice removes them.
4. **The retirement is sequenced after #274.** Neither the `kind` member nor the
   removal of the two operations lands before #274's policy is accepted,
   because each changes a closed set that policy governs (*Negative*, first
   item).
5. **The two operations are not retired by reinterpreting them under
   `theurian.dev/v1`.** Reading `registerSpecification` as an `addAlias` from
   the spec id to the item, or `supersedeSpecification` as a `supersedes`
   relation, under the same `apiVersion` is rejected (*Alternatives
   considered*). A translation layer, if the retirement has one, is something
   #274's policy may choose under a new version; v1 does not silently acquire
   it.

### Where each `Specification` field lands

| `Specification` (`domain/specification.py`) | Knowledge-side counterpart |
| :-- | :-- |
| `spec_id` | The knowledge item id. A spec id such as `spec.order-cancellation` may be carried as an alias, since `SpecId` shares `ItemId`'s grammar (*Neutral*): reachability may resolve an alias, authority never does (T-21) |
| `project_id` | `KnowledgeItem.project_id` / `KnowledgeRevision.project_id` |
| `revision_id` | Already a `KnowledgeRevision.revision_id` — the table's foreign key |
| `title` | `KnowledgeRevision.title` |
| `status` (`SpecificationStatus`) | The item's `KnowledgeStatus` — **not a rename**; see *Negative* |
| `content_format` | `KnowledgeRevision.content_type` |
| `source_uri` | A `SourceAnchor` in `KnowledgeRevision.source_anchors` |
| `validity` | `KnowledgeRevision.validity` |
| `structured` | `KnowledgeRevision.structured` — unpopulated on both sides (*Context*) |
| `anchors` | `KnowledgeRevision.source_anchors` |
| the table's `superseded_by` column, set by `supersedeSpecification` | The `supersedes` / `superseded_by` relation, which `deprecateItem`'s `supersededBy` already writes and which `ACYCLIC_RELATIONS` declares acyclic (INV-6). `KnowledgeRelation.must_be_acyclic` has no caller in `packages/theurian-core/src`; apply-time enforcement is Phase C's Schema row |

## Consequences

### Positive

- **Specification endpoints land in the id space the existing gate already
  reads.** `mcp/tools.py`'s `_relation_is_visible` checks both endpoints of a
  relation, each read by the id it literally names through
  `CanonicalReadSession.get_item_exact_metadata`, which does not resolve an
  alias, against `may_surface` and `may_disclose`. Keeping the entity would put
  specification endpoints in a second id space with its own status vocabulary
  and no sensitivity column — a second gate to build. **This decision settles
  the id space, not the gate:** Phase C's Security row requires the per-hop
  gate in T-21's corrected form ("a traversal hop must not resolve an alias
  when deciding authority"), and this ADR neither implements nor weakens it.
  Whatever reads a specification endpoint on a hop owes both of the existing
  gate's properties: it does not resolve an alias (T-21), and it reads the
  endpoint's metadata, never its body — the joined `get_item_exact`
  materialised a withheld endpoint's body before withholding it, so a refusal's
  duration carried the body's size, which is
  [T-26](../security/threat-model.md), closed in 0.2.3 by the metadata form.
  A hop read through `get_item_exact` keeps the first property and drops the
  second.
- **A specification gets the governance every item has.** Status, sensitivity,
  ownership, aliases, relations and supersession are the knowledge model's, so
  none of them is built a second time for specifications.
- **Nothing is lost.** Every `Specification` field has a knowledge-side home
  (table above), and `structured`, the payload's recommended home, is as empty
  on the entity as on the revision.

### Negative

- **Both halves of the fold are protocol changes, and neither can land before
  #274.**
  - *The `kind` member.* `KnowledgeKind.SPECIFICATION` extends the closed
    `kind` set. `schemas/migrations/migration.schema.json` publishes it as
    `$defs/kind`. The MCP input schemas
    `schemas/mcp/knowledge-propose-change-input.schema.json` and
    `schemas/mcp/review-generate-knowledge-candidate-input.schema.json` type
    `kind` as a string whose description names `KnowledgeKind` as the closed
    set their handlers refuse against. A new member changes what a v1
    migration and both of those tools admit.
  - *The retiring operations.* `registerSpecification` and
    `supersedeSpecification` are members of ADR-0005's closed operation set.
    ADR-0005 makes *adding* an operation "a protocol change" requiring an
    `apiVersion` bump and says nothing of removing one — the stronger break,
    since committed documents already name the removed kind. Both are in
    `schemas/migrations/migration.schema.json` (`$defs/opRegisterSpecification`,
    `$defs/opSupersedeSpecification`) and in `application/proposal_service.py`'s
    `V1_OPERATION_KINDS`, the set `knowledge.generateMigrationDraft` admits
    ([ADR-0032](0032-the-write-intent-mcp-tool-surface.md) decision 3), so the
    write-intent path can author both today.
  - *The version check has no window.* The published schema pins `apiVersion`
    with `"const": "theurian.dev/v1"`, and two compiled checks refuse on exact
    equality against `MIGRATION_API_VERSION` (`"theurian.dev/v1"`,
    `domain/migration.py`): `infrastructure/filesystem/migration_loader.py`
    (`if document["apiVersion"] != MIGRATION_API_VERSION:`) and
    `application/proposal_service.py`
    (`if document.get("apiVersion") != MIGRATION_API_VERSION:`). A bump with no
    dual-acceptance window refuses every committed v1 document.
  - *What is committed, measured 2026-09-29 at `855ebd87`.* The dogfood corpus
    holds 49 migrations
    (`git ls-tree -r --name-only HEAD .theurian/migrations/ | grep -c '\.yaml$'`),
    all `apiVersion: theurian.dev/v1`
    (`git grep -h '^apiVersion' -- .theurian/migrations/ | sort | uniq -c`
    prints `49 apiVersion: theurian.dev/v1`), and none names either operation
    (`git grep -c -E 'registerSpecification|supersedeSpecification' -- .theurian/migrations/`
    prints nothing). Across every committed `.yaml` and `.yml` file, the one
    naming either operation is the sample project's
    `examples/sample-project/.theurian/migrations/01K1DEFABC01234567890ABCDE-add-order-cancellation.yaml`
    (`git grep -l -E 'registerSpecification|supersedeSpecification' -- '*.yaml' '*.yml'`).
    Migrations in other repositories cannot be measured from here, so the
    retirement cannot assume that none of them names either operation.
- **`SpecificationStatus` does not survive as a rename.** It holds `draft`,
  `active`, `superseded` and `retired`; `KnowledgeStatus` holds `draft`,
  `proposed`, `approved`, `deprecated`, `superseded` and `rejected`. The obvious
  starting correspondence — `draft`→`draft`, `active`→`approved`,
  `superseded`→`superseded`, `retired`→`deprecated` — is a candidate, not a
  decision: `active` says a specification is in force where `approved` says an
  item passed review, `proposed` and `rejected` have no specification
  counterpart, and whichever mapping is chosen decides what `may_surface` does
  with a specification. The retirement slice owes it.
- **Until the retirement lands, the old path stays open.** A v1 document may
  still carry `registerSpecification`, including one drafted through
  `knowledge.generateMigrationDraft`, and it writes a row that no caller reads.

### Neutral

- **Existing `specifications` rows are not migrated.** The canonical store is
  derived ([ADR-0004](0004-sqlite-is-a-derived-artifact.md);
  `infrastructure/sqlite/schema.py`'s module docstring: every byte is
  reconstructible by replaying the Git-tracked migrations into an empty file),
  so the rows stop being produced when the operations retire. How a committed
  v1 document that names a retired operation is then read is #274's to decide,
  under a version signal.
- **`SpecId` and `ItemId` are separate namespaces by name only.** Both subclass
  `_DottedId` (`domain/identifiers.py`), and the migration schema types `specId`
  and `supersededBy` as `$defs/itemId`. Carrying a spec id as an alias puts it
  where the alias guards (`application/migration_alias_guards.py`) and T-21's
  rule already apply.
- **`SpecificationProvider` is not retired here.** The port
  (`domain/ports/specification_provider.py`) has no implementation — no
  `def discover` exists under `packages/` outside it — and its `discover`
  returns `tuple[Specification, ...]`. Under the fold that return type moves
  with the entity; the port itself is a discovery concern
  ([ADR-0003](0003-ports-and-adapters.md)) and stays.
- **`UNPOPULATED_TABLES` does not move.**
  `packages/theurian-core/tests/integration/test_canonical_store_corruption.py`
  excludes `traceability_edges`, and only that table, from its
  every-table-holds-a-row check. A change to that set belongs to the slice that
  populates the table.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **Keep `Specification` a separate entity and bridge it to knowledge by relations or traceability edges** | It doubles every future surface: a second id space for trace endpoints; a second status vocabulary that `may_surface` does not read; no sensitivity axis, since the table has no `sensitivity` column; a second supersede mechanism beside the `supersedes` relation and INV-6; and a second population for every walker — the index build, `knowledge.get`'s relations, the OKF export — none of which reads the `specifications` table today. It keeps a table that holds nothing the knowledge side lacks (the field table) and whose readers are never called (*Context*). And #277's reason to decide now cuts this way: no edge points at a spec id yet, so this is the cheap moment to keep one from ever doing so. |
| **Retire the two operations now by reinterpreting them under `theurian.dev/v1`** — `registerSpecification` read as `addAlias` from the spec id to the item, `supersedeSpecification` as a `supersedes` relation | Checked against source; it fails three ways. **(a)** The published v1 schema admits both as their own shapes (`$defs/opRegisterSpecification`, `$defs/opSupersedeSpecification`) and the loader parses them into their own operation classes (`RegisterSpecification`, `SupersedeSpecification`), so the reinterpretation changes what v1 means with no version signal. **(b)** Identical v1 documents would yield different canonical states on builds either side of the change. `MIGRATION_ENGINE_VERSION` (`domain/migration.py`) is hashed into the state hash "so an engine change invalidates cached state instead of silently reinterpreting it (ADR-0007)"; it keeps a cache from being trusted across the change, and does not tell a reader of the document which meaning it carries. Saying that a format changed is `apiVersion`'s job. **(c)** The operations do not behave like their proposed readings. The alias collision guard in `application/migration_alias_guards.py` reads `addAlias` and `removeAlias` and never `registerSpecification`, so committed documents could meet a refusal they were validated without; and `supersedeSpecification` is an `UPDATE` of one `specifications` row — nothing when no row matches — where `addRelation` is an `INSERT OR IGNORE` into `knowledge_relations`, so replay and idempotency differ. |
| **Defer this decision until #274 is accepted, and decide both together** | The questions separate, and only one of them blocks Phase C. The id space (decision 2) depends on nothing #274 decides, and the population slice needs it before its first edge; what depends on #274 is how the entity leaves (decisions 4 and 5). Deferring would leave that slice to choose an id space by default. |

## Compliance

**This ADR ships no behaviour.** Its enforcement is a claims pin over the
repository facts it states, plus the obligations below.

Landing with this pull request:

- **`packages/theurian-core/tests/unit/test_adr_0038_claims.py`, the claims
  pin.** It is owed both directions for each fact this record states about the
  codebase: a prose side that reddens when this record drifts back from what it
  says, and a fact side derived from live source that reddens when the codebase
  moves and this record must move with it — a call of a specification reader
  (which is also how a breach of decision 3 shows), a `specification` member in
  `KnowledgeKind`, a third compiled `apiVersion` comparison. Its reach is stated
  in its own module docstring.

Rests on enforcement that already holds:

- **The one-hop visibility gate.** `mcp/tools.py`'s `_relation_is_visible`
  reads each endpoint by its literal id through `get_item_exact_metadata`
  (*Positive*, first item). A specification endpoint under decision 2 is read
  by the same gate.
- **The alias collision guard** (`application/migration_alias_guards.py`, T-21's
  write side) applies to any spec id carried as an alias.

Still owed, with the issue or slice that will satisfy it:

1. **[#274](https://github.com/theurian/theurian/issues/274)'s policy, accepted
   before any code of the fold lands** (decision 4): whether a `kind` member is
   additive, how an operation leaves the closed set, and how a committed v1
   document that names a retired operation is read.
2. **The retirement slice, after #274.** Add `KnowledgeKind.SPECIFICATION` and
   its `$defs/kind` member; remove `registerSpecification` and
   `supersedeSpecification` from `OperationKind`, the migration schema, the
   loader and `V1_OPERATION_KINDS`; remove the entity, the `specifications`
   table (a DDL change, so a `SCHEMA_VERSION` bump), `SpecId`,
   `SpecificationStatus` and their store and port methods;
   decide the `SpecificationStatus` correspondence (*Negative*); move
   `SpecificationProvider.discover`'s return type; amend ADR-0005's operation
   list and ADR-0032 decision 3's v1 set; and move every committed document and
   fixture that names either operation
   (`git grep -l -E 'registerSpecification|supersedeSpecification'`), the
   sample project's migration among them.
3. **[#275](https://github.com/theurian/theurian/issues/275)'s edge
   representation**, including how a `SPECIFICATION`-typed `TraceNode` carries
   its knowledge item id (decision 2) and the per-hop gate in T-21's corrected
   form.
4. **[#834](https://github.com/theurian/theurian/issues/834)'s resolution of
   `KnowledgeRevision.structured`.** Owed under either alternative, since
   neither side persists a parsed payload (*Context*), and due before anything
   reads a specification's parsed form, as
   [`traceability.md`](../architecture/traceability.md)'s `spec.getCoverage`
   would. This ADR does not choose between the closure options #834 records.
