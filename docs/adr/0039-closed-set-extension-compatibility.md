# ADR-0039: How the migration format's closed sets change

- Status: accepted
- Date: 2026-09-30
- Deciders: Theurian maintainers
- Requirements: FR-K2, FR-K3, FR-K4, FR-K5, FR-K10, CP-6
- **Resolves** [`docs/roadmap.md`](../roadmap.md) §9 ADR candidate 3 — the
  compatibility policy for extending a closed enum
  ([#274](https://github.com/theurian/theurian/issues/274))
- Situates against [ADR-0005](0005-yaml-knowledge-migrations.md) (the closed
  operation set, and its one rule: adding an operation bumps `apiVersion`) and
  [ADR-0038](0038-specification-folds-into-a-knowledge-kind.md) (the fold that
  adds a `kind` member and retires two operations, sequenced after this policy)

**This ADR records a decision and ships no code.** No enum member, operation,
schema or refusal message moves here. It is Phase C slice C2, as ADR-0038 names
it. The retirement it unblocks is
[#841](https://github.com/theurian/theurian/issues/841), and the diagnosable
refusal it calls for is [#849](https://github.com/theurian/theurian/issues/849).

**Every repository fact below was measured on 2026-09-30 against `origin/main`
at `f0e4d754`.** Source paths are relative to
`packages/theurian-core/src/theurian/`; paths beginning `packages/`, `schemas/`,
`plugins/` or `examples/` are from the repository root. A behaviour marked
*measured* was observed by calling the function named, never through the CLI; one
marked *read from source, not run* was not driven at all.

## Context

### The closed sets the v1 format publishes

`git grep -n -E '"(enum|const)":' -- schemas/migrations/migration.schema.json`
prints 21 lines: the `apiVersion` const, six enums, and the fourteen `op` consts
of `$defs/operation.oneOf`'s fourteen branches.

| Set | Published as | Python mirror | Members | Class (decision 1) |
| :-- | :-- | :-- | --: | :-- |
| `kind` | `$defs/kind` | `domain/enums.py` `KnowledgeKind` | 11 | vocabulary |
| `relationType` | `$defs/relationType` | `domain/enums.py` `RelationType` | 14 | vocabulary |
| the operation set | `$defs/operation.oneOf`, one `op` const per branch | `domain/migration.py` `OperationKind` | 14 | grammar |
| `status` | `$defs/status` | `domain/enums.py` `KnowledgeStatus` | 6 | gate-bearing |
| `sensitivity` | `$defs/sensitivity` | `domain/enums.py` `Sensitivity` | 4 | gate-bearing |
| `trustLevel` | `$defs/trustLevel` | `domain/enums.py` `TrustLevel` | 4 | gate-bearing |
| a specification's status | `$defs/opRegisterSpecification/properties/status` | `domain/enums.py` `SpecificationStatus` | 4 | retiring |
| the format version | `properties/apiVersion`, `const` `theurian.dev/v1` | `domain/migration.py` `MIGRATION_API_VERSION` | 1 | the version constant |

`domain/migration.py` also holds `MIGRATION_ENGINE_VERSION = 1`, which is
hashed into the state hash (`domain/state.py:132`) and reported as
`engineVersion` by `theurian project status` (`cli/commands.py:2115`).

Until the amendment this ADR adds to it, ADR-0005 stated one rule about these
sets: adding an operation "is a protocol change and requires a version bump of
`apiVersion`". It said nothing of adding a `kind` or `relationType` member, of
removing, renaming or reordering anything, or of changing what a member means.
The published schema pins
`apiVersion` with a `const`, and both compiled checks compare it for equality
with `MIGRATION_API_VERSION` (ADR-0038, *Negative*), so no Core reads two
versions today.

### What an unknown value meets today

The population is keyed on where a value becomes one of these enums — every
construction from a value, and every call of the MCP layer's closed-set parser —
plus the two mechanisms that refuse before any enum is built by name: JSON
Schema validation, and a CLI option typed by the enum
(`git grep -n -E '^[[:space:]]+(KnowledgeKind|RelationType|OperationKind|KnowledgeStatus|Sensitivity|TrustLevel|SpecificationStatus)( \| None)?,' -- packages/theurian-core/src/theurian/cli`
finds `propose`'s `--kind`, `--trust-level` and `--sensitivity`, at
`cli/propose_commands.py:149`, `:201` and `:209`).

```console
$ git grep -n -E 'KnowledgeKind\(|RelationType\(|OperationKind\(' -- packages/theurian-core/src
packages/theurian-core/src/theurian/application/okf_import.py:287:        return KnowledgeKind(concept.kind)
packages/theurian-core/src/theurian/application/proposal_service.py:3768:            kind = OperationKind(raw)
packages/theurian-core/src/theurian/domain/enums.py:57:class KnowledgeKind(StrEnum):
packages/theurian-core/src/theurian/domain/enums.py:73:class RelationType(StrEnum):
packages/theurian-core/src/theurian/domain/migration.py:51:class OperationKind(StrEnum):
packages/theurian-core/src/theurian/infrastructure/filesystem/migration_loader.py:1670:                kind_=KnowledgeKind(payload["kind"]),
packages/theurian-core/src/theurian/infrastructure/filesystem/migration_loader.py:1690:                relation_type=RelationType(payload["relationType"]),
packages/theurian-core/src/theurian/infrastructure/filesystem/migration_loader.py:1697:                relation_type=RelationType(payload["relationType"]),
packages/theurian-core/src/theurian/infrastructure/filesystem/migration_loader.py:1899:            kind=KnowledgeKind(metadata["kind"]),
packages/theurian-core/src/theurian/infrastructure/sqlite/store.py:1681:        kind=KnowledgeKind(row["kind"]),
packages/theurian-core/src/theurian/infrastructure/sqlite/store.py:1707:            kind=KnowledgeKind(row["kind"]),
packages/theurian-core/src/theurian/infrastructure/sqlite/store.py:1784:        relation_type=RelationType(row["relation_type"]),
$ git grep -n '_closed_value(' -- packages/theurian-core/src
packages/theurian-core/src/theurian/mcp/tools.py:3382:                kind=_closed_value(KnowledgeKind, kind, "kind"),
packages/theurian-core/src/theurian/mcp/tools.py:3395:                    else _closed_value(TrustLevel, trustLevel, "trustLevel")
packages/theurian-core/src/theurian/mcp/tools.py:3400:                    else _closed_value(Sensitivity, sensitivity, "sensitivity")
packages/theurian-core/src/theurian/mcp/tools.py:3542:                    kind=_closed_value(KnowledgeKind, kind, "kind"),
packages/theurian-core/src/theurian/mcp/tools.py:3543:                    category=_closed_value(ReviewCommentCategory, category, "category"),
```

Three of the first twelve lines are class statements. The loader's four
constructions run after schema validation, so no unknown value reaches them. The
entry points an unknown value can reach refuse it as follows:

| Entry point | Where and as what | Names the unknown value? | Established by |
| :-- | :-- | :-- | :-- |
| Migration loader: `load_migrations` → `_load_one` (`infrastructure/filesystem/migration_loader.py:1607-1611`), for every command that loads migrations | JSON Schema validation, `MigrationError`: `<file> is invalid at operations/<N>: does not satisfy 'oneOf' (expected [...]); the value there is {...}`. The location is the operation index and the keyword is the discriminated `oneOf`, which fails whole, so neither the field nor the enum that failed is named | Only inside the echoed operation, which nothing marks as the offending value and which is bounded at `MAX_ECHOED_VALUE` (1,000 characters, `:213`). A legal operation can truncate it away | Measured: a copy of `examples/sample-project` with one value replaced. An unknown `kind` in `createItem` or in `upsertRevision.metadata`, `relationType`, `status`, `sensitivity`, `trustLevel` or `op`, and a `registerSpecification` renamed away, were each refused so, with the value inside the echo; the unmodified copy loads. An `addRelation` carrying a legal 990-character `note` (its `maxLength` is 1,000, and `note` sorts before `relationType` in the echo) and `relationType: traces_to` was refused with `traces_to` nowhere in the 1,315-character message; the same operation with `constrained_by` loads |
| The same loader, `apiVersion` | JSON Schema `const`, `MigrationError`: `is invalid at apiVersion: does not satisfy 'const' (expected 'theurian.dev/v1'); the value there is 'theurian.dev/v2'`, before the compiled comparison at `:1613` is reached. With `theurian.dev/v2` and an unknown `kind` in one document, this is the refusal reported | Yes | Measured, same copy |
| `validate_migration_document` (`:1103`), the validator the proposal service is given | The loader's seam (`_schema_rejection`), `MigrationError`: `invalid migration at operations/<N>: does not satisfy 'oneOf' ...` | As the loader | Measured: an unknown `relationType` and an unknown `op` refused, a valid document accepted |
| Proposal service `draft_from_document` (`application/proposal_service.py`), behind `knowledge.generateMigrationDraft` and OKF import's relations draft | The v1 gate skips an unknown `op` (`OperationKind(raw)` at `:3767-3770` `continue`s) and leaves it to the injected validator: `MigrationError` at draft, with nothing written (the method's `Raises`, `:1002-1004`). At `propose accept`, `_refuse_a_document_the_schema_rejects` wraps the validator's refusal as `ProposalError` (`:1850-1861`) | As the loader | Read from source, not run; the validator is the row above |
| MCP `knowledge.proposeChange` (`kind`, `trustLevel`, `sensitivity`) and `review.generateKnowledgeCandidate` (`kind`), through `_closed_value` (`mcp/tools.py:1251`) | Handler, `ToolError`: `` `kind` must be one of: architecture, decision, domain, operations, security, testing, api, incident, convention, rejected-approach, known-exception. `` | No: it names the field and the valid set | Measured: `_closed_value` called with an unknown `kind`, `trustLevel` and `sensitivity`; `domain` accepted |
| `theurian propose --kind` (`cli/propose_commands.py:148-150`, typed `KnowledgeKind`); `--trust-level` and `--sensitivity` (`:200-214`) are typed the same way | Option parsing, Typer `BadParameter`: `'requirement' is not one of 'architecture', ..., 'known-exception'.` | Yes, with the valid set | Measured for `--kind`: the option's own type converted the value on the built command, and the command was not invoked. The other two read from source, not run |
| OKF import, a concept's `type` (`application/okf_import.py:285-289`, `:544-550`) | Per concept, `ImportRefusal(kind="concept", key=<file>, literal="unrecognized type: 'specification'")`; other concepts still import | Yes | Measured: `_map_concept` on one concept file; `type: domain` maps |
| OKF import, a relation entry's `type` (`:586-590`, `_draft_relations` `:829-853`) | Carried verbatim into an `addRelation` in the one relations draft. An unknown type fails that draft's validation, and the whole draft is refused as one `ImportRefusal(kind="relations", key="addRelation")` carrying the validator's message, so every relation in the bundle is dropped, not only the unknown one | As the loader | Read from source, not run |
| Derived store row decoders: `_item_from_row` (`infrastructure/sqlite/store.py:1681`), `_relation_from_row` (`:1784`) | Bare `ValueError`: `'requirement' is not a valid KnowledgeKind`, `'traces_to' is not a valid RelationType` | Yes | Measured: each decoder called on a mapping; the valid values decode |

### The derived store

The state hash's inputs are the migration ids and checksums, the body checksums,
`SCHEMA_VERSION` and `MIGRATION_ENGINE_VERSION` (`domain/state.py:47-54`); no Core
version and no enum membership. The CLI names a state database from a loaded
migration set (`application/project_service.py:2582`,
`resolve_state_hash(loaded: LoadedMigrations, ...)`), so a CLI command reaches a
store only after every migration has loaded, which an older Core refuses when a
document names a member it lacks. The MCP tools do not load migrations: they
open the database the project's active-state pointer names
(`mcp/tools.py:1769`, `read_active_state`; `:1817`, `state_database_named`). An
older daemon serving a project whose pointer a newer Core wrote can therefore
reach the row decoders above without meeting the loader's refusal first. Read
from source, not run; what the tool returns then is owed (*Compliance*, Still
owed item 3).

### What `theurian compat check` compares (read from source, not run)

`resolve_compatibility(declaration, core_version, core_protocol_version)`
(`domain/compatibility.py:439`) decides one of `compatible`, `core-missing`,
`core-too-old`, `core-too-new` and `protocol-mismatch` (`:395-399`). The
command's options are `--plugin-version`, `--core-minimum`,
`--core-maximum-exclusive`, `--protocol-version` and `--json`
(`cli/main.py:149-166`), and the protocol it compares against is
`CURRENT_PROTOCOL_VERSION = "theurian/v1"` (`domain/compatibility.py:125`). It
reads no project, no migration and no enum.

### What the wire carries

No `kind`, `relationType` or operation set is enumerated in any published wire
schema. `git grep -n -E '"(enum|const)":' -- schemas/mcp schemas/knowledge`
lists every `enum` and `const` those schemas hold, and none is one of the three.
The MCP input schemas `knowledge-propose-change-input` and
`review-generate-knowledge-candidate-input` type `kind` as a string (and the
first types `trustLevel` and `sensitivity` as string or null), and
`knowledge-generate-migration-draft-input` constrains no `op`. The listing does
hold an enum on a property named `kind` — `review-search-response`'s
`records.items.kind`, `pull-request`, `review-submission` and `review-thread`,
the kind of a review record and not `KnowledgeKind` — which is the positive
control that the key reaches such a property.

Values still travel. `knowledge.get` publishes each visible relation's
`relationType` (`mcp/tools.py:2434`) and has no response schema, so a client
meets a member it does not know as an unrecognised string. No MCP response
publishes a `KnowledgeKind`: under `mcp/`, it appears only as the two
`_closed_value` inputs above, and the one `.kind` published is the review
record's (`mcp/review_search.py:523`).

**The gate-bearing sets are enumerated on the wire.** `knowledge-search-response`
types each result by `$ref` to `schemas/knowledge/retrieval-result.schema.json`,
which enumerates `status` as the three surfaceable members (`approved`, `draft`,
`proposed`), `trustLevel` (4) and `sensitivity` (4); `mcp/results.py:95-97`
publishes all three on every result.

`plugins/` uses a member in two places and parses none: three operation names in
the prose of `plugins/claude-code/commands/index.md:47-49`, and
`--kind architecture` in the example invocation at `commands/propose.md:51`. The
key is all 39 `kind`, `relationType` and `op` values, as whole words:

```sh
python3 -c 'import json; d = json.load(open("schemas/migrations/migration.schema.json"))["$defs"]; print("\n".join(d["kind"]["enum"] + d["relationType"]["enum"] + [d[r["$ref"].split("/")[-1]]["properties"]["op"]["const"] for r in d["operation"]["oneOf"]]))' \
  | git grep -n -w -F -f - -- plugins
```

It prints 37 lines, all in Markdown. The 33 besides those four use the word for
something else: `decision`, `operations`, `incident`, `rejects` and
`architecture` as English, `security` in paths and configuration keys
(`security/project_config.py`, `security.secretScan`), `architecture` inside an
example item id, and `api` inside an example URL.

### Applied migrations are frozen, and a fresh clone has applied nothing

ADR-0005 rule 2 and FR-K5: `verify_no_applied_migration_changed`
(`application/migration_engine.py:168`) refuses an applied migration whose
checksum moved, and `verify_no_applied_migration_removed` (`:194`,
[#116](https://github.com/theurian/theurian/issues/116)) one whose file is gone.
Both compare against `migration_history`, a table of the derived store
(`infrastructure/sqlite/schema.py:407`). FR-K4 rebuilds the canonical state from
an empty store, so on a fresh clone nothing is recorded as applied and every
committed document is replayed. A Core cannot tell a document written before a
change from one written after it.

## Decision

1. **The policy governs the eight entries of the table above, each in exactly
   one class**, because what a change puts at risk differs by class.
   *Vocabulary* — `kind`, `relationType` — is a value inside an operation shape
   that already exists. *Grammar* — the operation set — is the set of shapes the
   loader dispatches on. *Gate-bearing* — `status`, `sensitivity`, `trustLevel`
   — is governance: `status` feeds `may_surface` and `sensitivity` feeds
   `may_disclose` (`domain/enums.py:222`, `:269`); `trustLevel` feeds neither
   gate, and is classed with them as the third closed-set governance label: OKF
   import never copies it from a bundle (`application/okf_import.py`'s module
   docstring, under ADR-0037 decision 1), and import and candidate generation
   fix it at `inferred` (`application/okf_import.py:180`,
   `domain/review.py:335`). *Retiring* — `SpecificationStatus` — leaves with its
   entity under #841. The version constant, `apiVersion`, is what the other
   classes move or do not move.
2. **Adding a vocabulary member is additive.** No `apiVersion` bump and no
   `protocolVersion` bump; it is a Core MINOR, recorded under the CHANGELOG's
   `Added` naming the first Core version that reads it. An older Core keeps
   refusing a document that names the member — fail-closed, unchanged — because
   its schema does not admit it. A version bump would buy no safety: the older
   Core refuses either way. What it would buy is a better message, and that
   message is owed without one: today's refusal cannot be told from a typo
   (*Context*), and a refusal naming the file, the field path and the value, and
   saying that a newer Core may define it, is the scope of #849, which is due
   before #841 adds its `kind` member. This takes roadmap §4's recommendation for
   the first two of its three clauses.
3. **Adding an operation keeps ADR-0005's rule: it bumps `apiVersion`.** An
   operation changes the grammar the loader dispatches on, not a value inside a
   shape it already reads. The Core that bumps reads every earlier `apiVersion`
   (decision 5), and every document Core authors declares the **lowest**
   `apiVersion` whose grammar admits it, so a document that uses no new operation
   stays readable by older Cores. Core authors documents in one place: the two
   lines under `packages/theurian-core/src` that stamp `apiVersion` are both in
   `application/proposal_service.py` (`:3813`, `:4393`), behind
   `theurian propose`, the MCP draft tools and OKF import.
4. **Removing or renaming a member or an operation takes it out of Core's own
   writers only. It never leaves the read grammar of the `apiVersion` that
   admitted it, and the removal by itself bumps nothing.** The reason is in the
   codebase, not in preference: applied migrations are frozen — an edited one is
   refused by `verify_no_applied_migration_changed`, a deleted one by
   `verify_no_applied_migration_removed` — and FR-K4 replays every committed
   document on a fresh clone, where nothing is recorded as applied, so a Core
   cannot tell an old document from a new one and must read both. A version bump
   for the removal would buy nothing: under decision 3's lowest-version rule no
   writer would declare it, and a Core that always declared it would lock out
   older Cores that read the document fine. OKF import is one of those writers —
   ADR-0037 decision 6 makes it an on-ramp to the existing write path, not a new
   source class — so after a removal a bundle concept naming the removed member
   is refused at import like any new draft, not admitted on the strength of the
   read grammar. After a removal the Python enum and the published schema, which
   are the read grammar, are wider than what Core writes, so every writer needs
   an explicit write set: the proposal service, whose validator is the published
   schema itself; the MCP `_closed_value` handlers; `propose --kind`; and OKF
   `_resolve_kind`. `V1_OPERATION_KINDS` is already that pattern for the
   operation set — enumerated rather than derived as
   `frozenset(OperationKind) - refused`, which would admit a new kind by omission
   (`application/proposal_service.py:327-351`). A rename is decision 2 or 3 for
   the new spelling plus this decision for the old one.
5. **Read support is permanent.** Every Core reads every `apiVersion` any
   released Core admitted, under that version's meaning.
6. **Changing what an existing member or operation means, under its existing
   spelling, bumps `apiVersion` for documents written under the new meaning;
   documents under the earlier version keep the earlier meaning.** That is
   ADR-0038 decision 5: no silent reinterpretation. A frozen document cannot
   acquire a new version signal. So **if a Core must change what an
   earlier-version document does** — #841's case, where the table the retired
   operations write goes away — the change bumps `MIGRATION_ENGINE_VERSION`,
   whose stated purpose is that "an engine change invalidates cached state
   instead of silently reinterpreting it" (ADR-0007), and it is permitted only
   with a recorded argument that no reader of the canonical state observes the
   difference. Otherwise the operation keeps its original effect. This is where
   this ADR meets ADR-0038's rejected alternative (b), whose objection to an
   engine-version bump was that it "does not tell a reader of the document which
   meaning it carries". That objection is about documents that can still be
   written; for them this decision requires the `apiVersion` bump. It cannot
   apply to a frozen document, which carries the version it was written under
   and can carry no other, and the recorded argument is what establishes that
   there is no second meaning for a reader to be told about.
7. **Reordering members is a reviewed diff, not a version event.** Values are
   strings, so a reorder changes no value's meaning and bumps nothing. The order
   is visible — the published schema's `enum` order is what a third party reads,
   and `packages/theurian-core/tests/unit/test_adr_0037_claims.py` holds
   `$defs/relationType` equal to `RelationType` as an ordered list because "a
   reorder is a diff its reviewer should see" — but neither it nor the order a
   refusal lists the valid values in is a contract.
8. **Gate-bearing sets are outside the additive class.** Any change to `status`,
   `sensitivity` or `trustLevel` is written as an ADR first, under roadmap §6
   principle 3 for the two sets that feed a gate and under decision 1's
   governance ground for `trustLevel`, and that ADR decides its own effect on
   `apiVersion`, on `protocolVersion`, on the Core version and on the CHANGELOG.
   `protocolVersion` is a live question for these sets, not a formality: the
   wire enumerates all three (*Context*). The concrete case is roadmap §9
   candidate 2, whose change to `SURFACEABLE_STATUSES` would move the
   three-member `status` enum `retrieval-result.schema.json` publishes.
9. **No change to `kind`, `relationType` or the operation set bumps
   `protocolVersion`**, because no published schema enumerates any of them
   (*Context*). A member a client does not know reaches it as an unrecognised
   string — a `relationType` in `knowledge.get`'s `relations`, the one place any
   of the three is published. A later change that publishes one of them as a
   wire `enum` re-opens this decision.
10. **`theurian compat check` does not surface an enum mismatch, and roadmap §4's
    third clause — "make `compat check` detect it" — is declined.** `compat
    check` is the plugin-to-Core axis and reads no project (*Context*). An enum
    mismatch is between a project's documents and a Core, and it surfaces
    wherever migrations load — every command that calls `load_migrations`,
    `theurian migrate validate` among them — in the form #849 specifies.

### The matrix

Core versions follow
[`plugin-core-compatibility.md`](../protocol/plugin-core-compatibility.md):
pre-1.0 a MINOR may break, post-1.0 a break is a MAJOR. A change that stops a
Core writer accepting input it accepted is a break for that writer's callers
and is marked `BREAKING` in the CHANGELOG.

| Change | `apiVersion` | `protocolVersion` | Core version | CHANGELOG | An older Core reading a newer document | A newer Core reading an older (frozen) document | An MCP client against a Core | An older Core importing a newer Core's OKF bundle |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| **C1** Add a `kind` or `relationType` member (decision 2) | Unchanged | Unchanged (decision 9) | MINOR, pre- and post-1.0 | `Added`, naming the first Core that reads it | Refuses the whole document at schema validation, today with the undiagnosable message and after #849 with a diagnosable one | Reads it; every earlier member stays | The write tools admit a new `kind` only against a Core that has it; an older Core's `_closed_value` refuses it, listing its own set. A new `relationType` reaches an older client as an unrecognised string in `knowledge.get`'s `relations` | A concept of the new `kind` is refused on its own (`unrecognized type`); a relation of the new type fails the one relations draft, dropping every relation in the bundle |
| **C2** Add an operation (decision 3) | Bumped; Core writes the lowest version whose grammar admits the document | Unchanged (decision 9) | MINOR, pre- and post-1.0: no document an earlier Core read stops loading (decision 5) | `Added`, naming the new `apiVersion` and the first Core that reads it | Refuses a document declaring the new version at the schema `const`; a document using no new operation keeps the earlier version and loads | Reads it under its own version's grammar (decision 5) | `knowledge.generateMigrationDraft` carries the operation only once `V1_OPERATION_KINDS`, or its successor, admits it by a deliberate edit; an older Core's draft path refuses it at validation | Not reached: a bundle carries concepts and relation entries, not operations |
| **C3** Remove a member or an operation (decision 4) | Unchanged; the member stays in the read grammar of every version that admitted it | Unchanged (decision 9) | A break for Core's writers: MINOR pre-1.0, MAJOR post-1.0 | `Changed`, `BREAKING`, naming the writers that stop accepting it and stating that documents naming it still load | Unaffected: it still has the member | Reads it (decision 4); what it then does is decision 6's | Writers refuse it against their explicit write sets; a stored relation of a removed type is still published | Unaffected. The reverse direction — a newer Core importing a bundle naming it — refuses it at import |
| **C4** Rename a member or an operation (decision 4) | As C1 for a member, as C2 for an operation; the old spelling stays readable | Unchanged (decision 9) | As C3: MINOR pre-1.0, MAJOR post-1.0 | `Added` for the new spelling and `Changed`, `BREAKING`, for the old | As C1 or C2 for the new spelling | Reads the old spelling | As C1 or C2 for the new spelling, as C3 for the old | As C1 for the new spelling |
| **C5** Reorder members (decision 7) | Unchanged | Unchanged | No version event | None required; the diff is reviewed | No effect | No effect | No effect: values are strings, and the order a refusal lists them in is not a contract | No effect |
| **C6** Change what a member or operation means (decision 6) | Bumped for documents under the new meaning; earlier versions keep the earlier meaning. Where a Core must change what an earlier document does, `MIGRATION_ENGINE_VERSION` is bumped instead, only with a recorded argument that no reader observes the difference | Unchanged (decision 9) | MINOR, pre- and post-1.0: no document changes meaning for any reader | `Changed`, naming the `apiVersion`, or the engine version, under which the new meaning applies | Refuses a document declaring the new version | Earlier meaning; under the engine-version path, a different effect no reader of the canonical state observes | No effect by itself | No effect: a bundle carries values, not a migration `apiVersion` |
| **C7** Any of C1–C6 on `status`, `sensitivity` or `trustLevel` (decision 8) | Decided by the change's own ADR | Decided by that ADR: all three are enumerated on the wire | Decided by that ADR | Decided by that ADR | Decided by that ADR; until one exists, an unknown value is refused at schema validation like any other | Decided by that ADR | Decided by that ADR; today `_closed_value` refuses an unknown `trustLevel` or `sensitivity`, and `retrieval-result.schema.json` enumerates all three | Decided by that ADR |
| **C8** `SpecificationStatus`, which leaves with the entity (#841) | Unchanged by its removal (decision 4); `$defs/opRegisterSpecification/properties/status` stays in the v1 read grammar. No member is added: nothing new is built on the entity (ADR-0038 decision 3) | Unchanged: it appears on no wire schema | As C3, in #841's release | As C3, in #841's entry | Unaffected | Reads a v1 `registerSpecification` naming it; what that operation then does is decision 6's, in #841 | Not published | Not reached: the OKF export does not read the `specifications` table (ADR-0038, *Context*) |

## Consequences

### Positive

- **Phase C's new kinds land without breaking a document.** `requirement` and
  `specification` (roadmap §4, item 1) are C1: a Core MINOR, readable by that
  Core and every later one, refused by earlier ones exactly as today.
- **No committed document becomes unreadable under this policy.** Decisions 4
  and 5 together mean FR-K4's replay holds on every Core that ships under it.
- **One version signal, spent where it carries information.** `apiVersion` moves
  only for grammar and for meaning, where a reader of the document cannot
  otherwise know what it holds.

### Negative

- **Until #849 is merged, the older Core's refusal is undiagnosable.** Decision 2
  keeps fail-closed, and fail-closed with a message that names the operation
  index, reports `oneOf`, and can lose the value to truncation reads like a typo
  (*Context*). The additive class depends on #849 to be usable, which is why
  #849 is due before the first member.
- **The read grammar only grows.** A removed operation keeps its `$defs` branch
  and its loader path for good, and a hand-written document can still name it:
  the schema cannot tell an old document from a new one, which is decision 4's
  premise. What Core no longer does is write it.
- **Every writer carries a write set the enum no longer states.** After the
  first removal, a writer that validates against the Python enum or the schema
  admits the removed member. Of the writers decision 4 names, only the proposal
  service's operation gate has an explicit set today.
- **Decision 6's engine-version path rests on an argument, not a mechanism.**
  Whether a reader observes a difference is a claim each use must record and
  defend; nothing checks it.
- **ADR-0037 is moved by decision 2, prospectively.** It states `RelationType`'s
  fourteen members in decision 4, the *Consequences*, the alternatives table and
  *Compliance*, and `packages/theurian-core/tests/unit/test_adr_0037_claims.py`
  asserts `len(RelationType) == 14`, so it fails on a fifteenth member. Under
  decision 2 the first `relationType` addition is additive, and the slice that
  makes it owes ADR-0037's amendment. ADR-0037's
  sentences saying candidate 3 is not decided there stay true — they say
  ADR-0037 did not decide it — as does ADR-0027's.
- **Decisions 3 and 5 are not implemented, because no second `apiVersion`
  exists.** The schema's `const` and both equality checks read one version; the
  slice that first bumps it builds the multi-version read and the lowest-version
  write.

### Neutral

- **`schemas/config/project-config.schema.json` carries its own `apiVersion`
  const with the same spelling, `theurian.dev/v1`.** It is a different document
  format; this ADR's `apiVersion` rules govern the migration format only.
- **`MIGRATION_ENGINE_VERSION` keeps its meaning.** It still describes the
  engine, not the format; decision 6 names the one case in which a format
  concern reaches it.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **Bump `apiVersion` for every new vocabulary member** | The older Core refuses the document either way: its schema does not admit the member. The bump would add a version every Core must read for good (decision 5) and buy only a clearer refusal, which #849 provides without one. |
| **A tolerant reader: an older Core admits an unknown member as opaque, or skips the operation** | Identical documents would produce different canonical states on different Cores, which FR-K4's replay and the state hash exist to rule out, and a skipped `addRelation` is a silently missing edge. Fail-closed is kept. |
| **Removal as a version event: drop the member from a new `apiVersion`'s read grammar** | A frozen document cannot move to the new version (FR-K5), and a fresh clone replays it (FR-K4), so every Core must still read the old version and the member with it. Under decision 3 no writer would declare the new version, and one that always did would lock out older Cores that read the document fine (decision 4). |
| **Make `theurian compat check` detect an enum mismatch** (roadmap §4's third clause) | It compares a plugin declaration with the running Core and reads no project. The mismatch is between a project's documents and a Core, and it already surfaces at every migration load; the form it surfaces in is #849's (decision 10). |
| **Bump `protocolVersion` for a vocabulary or grammar change** | No published schema enumerates `kind`, `relationType` or the operation set, so no client validates against their membership (decision 9). The gate-bearing sets, which are enumerated, are left to their own ADRs (decision 8). |

## Compliance

**This ADR ships no behaviour.** Its enforcement is a claims pin over the
repository facts it states, plus the obligations below.

Landing with this pull request:

- **`packages/theurian-core/tests/unit/test_adr_0039_claims.py`, the claims
  pin.** It is owed both directions for each fact this record states about the
  codebase: a prose side that fails when this record drifts from what it says,
  and a fact side derived from live source that fails when the codebase moves
  and this record must move with it — a closed set added to or removed from the
  schema walk, a governed set published as a wire `enum`, a parameter added to
  `resolve_compatibility`, a matrix row gained or lost. Its reach is stated in
  its module docstring.

Rests on enforcement that already holds:

- **Fail-closed at the schema.** The loader refuses an unknown value in every
  set *Context* measured it on, and `validate_migration_document` shares its
  validation seam; decision 2 keeps that.
- **The freeze.** `verify_no_applied_migration_changed` and
  `verify_no_applied_migration_removed` (FR-K5), on which decision 4 rests.
- **The operation write set.** `V1_OPERATION_KINDS` is enumerated, and
  `test_the_v1_operation_set_partitions_operation_kind` fails when an
  `OperationKind` is routed nowhere.
- **The `RelationType` mirror.** `test_adr_0037_claims.py` holds the enum at
  fourteen members and `$defs/relationType` equal to it in order.

Still owed, with the issue or slice that will satisfy it:

1. **[#849](https://github.com/theurian/theurian/issues/849)**, due before
   #841 adds its `kind` member: the loader's and `validate_migration_document`'s
   refusal names the file, the field path and the unknown value, and says a
   newer Core may define it (decision 2).
2. **[#841](https://github.com/theurian/theurian/issues/841), the retirement.**
   An explicit write set at each writer decision 4 names; the two
   operations and `SpecificationStatus` kept in the v1 schema and loader; and
   either the recorded argument decision 6 requires, with the
   `MIGRATION_ENGINE_VERSION` bump, or the operations' original effect kept.
3. **#841, before it adds its `kind` member: what an older Core does with a
   state database a newer Core built.** The MCP tools open the pointer's database
   without loading migrations, and the row decoders raise a bare `ValueError`
   on a member they lack (*Context*); what a tool returns then, and whether
   such a database opens under an older Core at all, was not run.
4. **The slice that first bumps `apiVersion`** — Phase C's edge operation, if
   [#275](https://github.com/theurian/theurian/issues/275)'s representation
   needs one: the multi-version read (decision 5) in the schema, the loader and
   the proposal service's check, and the lowest-version write (decision 3).
5. **The slice that adds the first `relationType` member**: ADR-0037's
   amendment, for the four places it states the count.
6. **Roadmap §9 candidate 2's ADR**: its effect on `protocolVersion`, since the
   `status` enum it would move is published (decision 8).
