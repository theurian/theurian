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
Phase C is to build traceability edges (FR-T2) and the queries over them (FR-T3)
on one of the two. #277 states why the choice is made now: it is expensive to
reverse once edges point at ids. No edge exists yet: `traceability_edges` is
declared in `schema.py`, and `CanonicalStore.add_traceability_edge` and
`list_traceability_edges` are declared on `domain/ports/canonical_store.py` with
no implementation and no caller.

**The entity is a reference from a knowledge item to a separate document,
pinned at one revision of that item.**
`application/migration_engine.py`'s `_register_specification` looks up the
operation's `itemId`, refuses when that item has no current revision, and
constructs a `Specification` whose `revision_id` is the item's
`current_revision_id` and whose `title` is the spec id's own string; it passes
no `structured` and no `anchors`. The entity has no item field, so that
revision is its only link to the item. The document is elsewhere: the one
committed `registerSpecification`, the sample project's (*Negative*), registers
`spec.order-cancellation` against `domain.order-cancellation`, a `kind: domain`
item with a `text/markdown` revision, while its `sourceUri` and
`format: application/yaml` name a different tracked file,
`examples/sample-project/.theurian/specifications/order-cancellation.yaml`.
Only a registration writes `revision_id` (an upsert, so re-registering the spec
id re-pins it); a later revision of the item leaves it, since the only other
statement that updates the table, `supersede_specification`, sets `status` and
`superseded_by`. The table's `revision_id` is a foreign key to
`knowledge_revisions(revision_id)`; the table has no `anchors` column and no
`sensitivity` column.
`infrastructure/sqlite/store.py`'s `_specification_from_row` builds a
`Specification` without `anchors`, so a stored specification always reads back
with `anchors=()`.

**Its readers are implemented and never called.** `store.py` implements two
readers, `get_specification` and `list_specifications`, and they hold the only
`FROM specifications` SQL in `packages/theurian-core/src`. No call of
`.get_specification(` or `.list_specifications(` exists anywhere in the
repository. The only calls into the store's specification methods are the two
writes in `migration_engine.py`, `register_specification` and
`supersede_specification`. The OKF export (`application/okf_export.py`) walks
knowledge items, revisions and relations and never the `specifications` table,
so today no export carries a specification's row or the document its
`source_uri` names; the item it was registered against is exported as any item
is.

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
report. What FR-T1 asks for — a specification registered in its native format
without forcing Markdown — is met today only by the row's pointer: `source_uri`
and `content_format` name the native document and its media type, and no stored
content holds that document; neither `structured` field contributes to it.

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

1. **A specification is a knowledge item of `kind: specification`.** The item
   is the specification's *document* — the file a registration's `sourceUri`
   names — not the item it is registered against. Its revision body is to be
   that document in its native format under its own `contentType` (ADR-0010);
   its parsed form — which the structured and OpenAPI parsers under
   `infrastructure/filesystem/parsers/` already produce at ingestion, and which
   goes no further today than `IngestedDocument.structured` (*Context*) —
   belongs in that revision's `KnowledgeRevision.structured`, whose persistence
   is owed (*Compliance*, item 4). The link from the governing item
   is to become a typed relation between the two items, its `RelationType` left
   to [#841](https://github.com/theurian/theurian/issues/841) or #275, and
   supersession a `supersedes` relation between specification items; only the
   revision pin will have no item-level home (table below). `KnowledgeKind` has
   no `specification` member today, and this ADR does not add one — decision 4
   says when it lands.
2. **Traceability will address a specification by its knowledge item id, from
   its first edge.** A `TraceNode` whose `node_type` is
   `TraceNodeType.SPECIFICATION` is to carry a knowledge item id: the node type
   names the role, and the id space is the knowledge item id space. A spec id
   an edge source names — [`traceability.md`](../architecture/traceability.md)
   draws a commit trailer, `Refs: spec.order-cancellation` — must be resolved
   to its knowledge item id when the edge is populated (reachability), never
   inside a hop's authority read (T-21). This composes with decision 4 because
   `kind` rides on the revision: `KnowledgeItem.with_revision` adopts
   `kind=revision.metadata.kind` (`domain/knowledge.py`), so an item entered
   before `kind: specification` exists can be retyped by a later revision
   without changing the id its edges address. How an edge is represented is
   #275's and is not decided here.
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

| `Specification` (`domain/specification.py`) | Where it lands |
| :-- | :-- |
| `spec_id` | The specification item's id. A spec id such as `spec.order-cancellation` may serve as that id or be carried as an alias of it, since `SpecId` shares `ItemId`'s grammar (*Neutral*): reachability may resolve an alias, authority must not (T-21) |
| `project_id` | The specification item's `KnowledgeItem.project_id` / `KnowledgeRevision.project_id` |
| `revision_id` | Split in two, since it is the entity's only link to its governing item (*Context*). **The link** is to become a typed relation from the governing item to the specification item (decision 1). **The pin** — which of the governing item's revisions was current at registration — will **not be carried**: a `KnowledgeRelation` names two items and no revision. Whether an edge or relation carries a revision or commit pin is #275's to decide (roadmap §4 item 2's `source_commit` pinning); until then the fold leaves the pin unrepresented |
| `title` | The specification item's `KnowledgeRevision.title`; the entity's is the spec id's own string (*Context*) |
| `status` (`SpecificationStatus`) | The specification item's `KnowledgeStatus` — **not a rename**; see *Negative* |
| `content_format` | The specification item's `KnowledgeRevision.content_type` — the document's media type (`application/yaml` in the sample), not the governing item's (`text/markdown` there) |
| `source_uri` | The specification item's revision body: the document it names is what that revision is to hold (decision 1) |
| `validity` | The specification item's `KnowledgeRevision.validity` |
| `structured` | The specification item's `KnowledgeRevision.structured`. The payload's home moves from a column that exists, `specifications.structured TEXT NOT NULL DEFAULT '{}'`, to a field whose writer is owed (*Compliance*, item 4); neither holds a payload today (*Context*) |
| `anchors` | The specification item's `KnowledgeRevision.source_anchors` |
| the table's `superseded_by` column, set by `supersedeSpecification` | A `supersedes` relation between two specification items, which `deprecateItem`'s `supersededBy` already writes and which `ACYCLIC_RELATIONS` declares acyclic (INV-6). `KnowledgeRelation.must_be_acyclic` has no caller in `packages/theurian-core/src`; apply-time enforcement is Phase C's Schema row. Mapped onto governing items instead, two specifications registered against one item would be a self-relation, which `KnowledgeRelation` refuses |

## Consequences

### Positive

- **Specification endpoints will land in the id space the existing gate
  already reads.** `mcp/tools.py`'s `_relation_is_visible` checks both
  endpoints of a relation, each read by the id it literally names through
  `CanonicalReadSession.get_item_exact_metadata`, which does not resolve an
  alias, against `may_surface` and `may_disclose`. Keeping the entity would put
  specification endpoints in a second id space with its own status vocabulary
  and no sensitivity column — a second gate to build. **This decision settles
  the id space, not the gate:** Phase C's Security row requires the per-hop
  gate in T-21's corrected form ("a traversal hop must not resolve an alias
  when deciding authority"), and this ADR neither implements nor weakens it.
  Whatever reads a specification endpoint on a hop owes at least both of the
  existing gate's read properties: it does not resolve an alias (T-21), and it
  reads the endpoint's metadata, never its body — the joined `get_item_exact`
  materialised a withheld endpoint's body before withholding it, so a refusal's
  duration carried the body's size, which is
  [T-26](../security/threat-model.md), closed in 0.2.3 by the metadata form.
  A hop read through `get_item_exact` keeps the first property and drops the
  second. It also owes the gate's others: both endpoints judged, with no
  direction inference; a missing endpoint withheld; and the read scoped to the
  project, as `_ITEM_METADATA_SQL`'s `project_id` scopes it and a `TraceNode`,
  which carries no project, cannot.
- **A specification item will get the governance every item has.** Status,
  sensitivity, ownership, aliases, relations and supersession are the knowledge
  model's, so none of them needs building a second time for specifications.

### Negative

- **Both halves of the fold are protocol changes, and neither can land before
  #274.**
  - *The `kind` member.* `KnowledgeKind.SPECIFICATION` would extend the closed
    `kind` set. `schemas/migrations/migration.schema.json` publishes it as
    `$defs/kind`. The MCP input schemas
    `schemas/mcp/knowledge-propose-change-input.schema.json` and
    `schemas/mcp/review-generate-knowledge-candidate-input.schema.json` type
    `kind` as a string whose description names `KnowledgeKind` as the closed
    set their handlers refuse against. A new member would change what a v1
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
- **The fold will drop the revision pin until #275 decides whether an edge
  carries one** (the field table): the relation will keep which item a
  specification governs and lose which of that item's revisions was current at
  registration. Nothing reads the pin today (*Context*).
- **Until the retirement lands, the old path stays open.** A v1 document may
  still carry `registerSpecification`, including one drafted through
  `knowledge.generateMigrationDraft`, and it writes a row that no caller reads.

### Neutral

- **Existing `specifications` rows are not migrated.** The canonical store is
  derived ([ADR-0004](0004-sqlite-is-a-derived-artifact.md);
  `infrastructure/sqlite/schema.py`'s module docstring: every byte is
  reconstructible by replaying the Git-tracked migrations into an empty file),
  so the rows will stop being produced when the operations retire. How a
  committed v1 document that names a retired operation is then read is #274's
  to decide, under a version signal.
- **`SpecId` and `ItemId` are separate namespaces by name only.** Both subclass
  `_DottedId` (`domain/identifiers.py`), and the migration schema types `specId`
  and `supersededBy` as `$defs/itemId`. So a spec id can serve as the
  specification item's own id. Carried as an alias instead, it meets the alias
  collision guard only as an `addAlias` (*Compliance*, Still owed item 2).
- **`SpecificationProvider` is not retired here.** The port
  (`domain/ports/specification_provider.py`) has no implementation — no
  `def discover` exists under `packages/` outside it — and its `discover`
  returns `tuple[Specification, ...]`. Under the fold that return type must
  move with the entity; the port itself is a discovery concern
  ([ADR-0003](0003-ports-and-adapters.md)) and stays.
- **`UNPOPULATED_TABLES` does not move.**
  `packages/theurian-core/tests/integration/test_canonical_store_corruption.py`
  excludes `traceability_edges`, and only that table, from its
  every-table-holds-a-row check. A change to that set belongs to the slice that
  populates the table.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **Keep `Specification` a separate entity and bridge it to knowledge by relations or traceability edges** | It doubles every future surface: a second id space for trace endpoints; a second status vocabulary that `may_surface` does not read; no sensitivity axis, since the table has no `sensitivity` column; a second supersede mechanism beside the `supersedes` relation and INV-6; and a second population for every walker — the index build, `knowledge.get`'s relations, the OKF export — none of which reads the `specifications` table today. It keeps a table whose one field the knowledge side lacks is the revision pin (the field table) — a question #275 owns for every edge, not a reason for a second entity — and whose readers are never called (*Context*). And #277's reason to decide now cuts this way: no edge points at a spec id yet, so this is the cheap moment to keep one from ever doing so. |
| **Retire the two operations now by reinterpreting them under `theurian.dev/v1`** — `registerSpecification` read as `addAlias` from the spec id to the item, `supersedeSpecification` as a `supersedes` relation | Checked against source; it fails four ways. **(a)** The published v1 schema admits both as their own shapes (`$defs/opRegisterSpecification`, `$defs/opSupersedeSpecification`) and the loader parses them into their own operation classes (`RegisterSpecification`, `SupersedeSpecification`), so the reinterpretation changes what v1 means with no version signal. **(b)** Identical v1 documents would yield different canonical states on builds either side of the change. `MIGRATION_ENGINE_VERSION` (`domain/migration.py`) is hashed into the state hash "so an engine change invalidates cached state instead of silently reinterpreting it (ADR-0007)"; it keeps a cache from being trusted across the change, and does not tell a reader of the document which meaning it carries. Saying that a format changed is `apiVersion`'s job. **(c)** The operations do not behave like their proposed readings. The alias collision guard in `application/migration_alias_guards.py` reads `addAlias` and `removeAlias` and never `registerSpecification`, so committed documents could meet a refusal they were validated without; and `supersedeSpecification` is an `UPDATE` of one `specifications` row — nothing when no row matches — where `addRelation` is an `INSERT OR IGNORE` into `knowledge_relations`, so replay and idempotency differ. **(d)** An alias from the spec id to the governing item would make the specification that item, which it is not: the document it names is a different file (*Context*). |
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
  reads both endpoints of a `KnowledgeRelation`, each by its literal id through
  `get_item_exact_metadata` (*Positive*, first item). No traceability read path
  exists for it to gate — `knowledge.trace` is not registered,
  `system.capabilities` publishes `traceability: false`, and `TraceNode.node_id`
  is a free string — so a gate over specification endpoints is owed (item 3).
- **The alias collision guard** (`application/migration_alias_guards.py`, T-21's
  write side): `refuse_alias_item_id_collision` refuses an `addAlias` key equal
  to the id of a live, non-deprecated item. It takes alias keys from `addAlias`
  and `removeAlias` alone, so a spec id reaches it only as an `addAlias`
  (item 2).

Still owed, with the issue or slice that will satisfy it:

1. **[#274](https://github.com/theurian/theurian/issues/274)'s policy, accepted
   before any code of the fold lands** (decision 4): whether a `kind` member is
   additive, how an operation leaves the closed set, and how a committed v1
   document that names a retired operation is read.
2. **[#841](https://github.com/theurian/theurian/issues/841), the retirement
   slice, after #274.** Add `KnowledgeKind.SPECIFICATION` and its `$defs/kind`
   member; enter each specification document as its own item and its link as a
   relation (decision 1); remove `registerSpecification` and
   `supersedeSpecification` from `OperationKind`, the migration schema, the
   loader and `V1_OPERATION_KINDS`; remove the entity, the `specifications`
   table (a DDL change, so a `SCHEMA_VERSION` bump), `SpecId`,
   `SpecificationStatus` and their store and port methods — but not
   `TraceNode`, `TraceabilityEdge`, `TraceabilityRule` or `TraceabilityPolicy`,
   which stay in `domain/specification.py`; decide the `SpecificationStatus`
   correspondence (*Negative*); move `SpecificationProvider.discover`'s return
   type; amend ADR-0005's operation list and ADR-0032 decision 3's v1 set;
   author any spec-id alias it produces as an `addAlias`, or pass it through
   `refuse_alias_item_id_collision`; and move every committed document and
   fixture that names either operation
   (`git grep -l -E 'registerSpecification|supersedeSpecification'`), the
   sample project's migration among them.
3. **[#275](https://github.com/theurian/theurian/issues/275)'s edge
   representation**: how a `SPECIFICATION`-typed `TraceNode` carries its
   knowledge item id (decision 2); whether an edge or relation carries the
   revision pin the fold will drop; and the per-hop gate, which does not exist
   yet.
   It owes T-21's corrected form and T-26's body-free read — each hop endpoint
   read through `get_item_exact_metadata` or an equivalent that neither
   resolves an alias nor reads the body, never the joined `get_item_exact` —
   and the further properties *Positive* lists; Phase C's Security row puts the
   two-corpora equality test for trace responses in the same change.
4. **[#834](https://github.com/theurian/theurian/issues/834)'s resolution of
   `KnowledgeRevision.structured`.** Owed under either alternative, since
   neither side persists a parsed payload (*Context*), and due before anything
   reads a specification's parsed form, as
   [`traceability.md`](../architecture/traceability.md)'s `spec.getCoverage`
   would. This ADR does not choose between the closure options #834 records.
   The `structured` half of roadmap §4 item 1's recommendation is owed to #834,
   not shipped.
