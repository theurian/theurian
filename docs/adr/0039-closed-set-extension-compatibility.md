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
| `status` | `$defs/status` | `domain/enums.py` `KnowledgeStatus` | 6 | wire-enumerated |
| `sensitivity` | `$defs/sensitivity` | `domain/enums.py` `Sensitivity` | 4 | wire-enumerated |
| `trustLevel` | `$defs/trustLevel` | `domain/enums.py` `TrustLevel` | 4 | wire-enumerated |
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

The population is keyed on the three classes the additive policy governs —
`KnowledgeKind`, `RelationType` and `OperationKind` — at every place a value
becomes one of them: a construction from a value, a call of the MCP layer's
closed-set parser with one of them, and a CLI option typed by one of them. JSON
Schema validation, which refuses before any of them is built, is listed too.

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
$ git grep -n -E '_closed_value\((KnowledgeKind|RelationType|OperationKind),' -- packages/theurian-core/src
packages/theurian-core/src/theurian/mcp/tools.py:3382:                kind=_closed_value(KnowledgeKind, kind, "kind"),
packages/theurian-core/src/theurian/mcp/tools.py:3542:                    kind=_closed_value(KnowledgeKind, kind, "kind"),
$ git grep -n -E '^[[:space:]]+(KnowledgeKind|RelationType|OperationKind)( \| None)?,' -- packages/theurian-core/src/theurian/cli
packages/theurian-core/src/theurian/cli/propose_commands.py:149:        KnowledgeKind | None, typer.Option("--kind", help="What sort of knowledge (required).")
```

Of the first command's twelve lines, three are class statements and four are the
loader's constructions, which run after schema validation, so no unknown value
reaches them. That leaves five construction sites — `okf_import.py:287`,
`proposal_service.py:3768`, and the store's `:1681`, `:1707` and `:1784` — and
the table accounts for each.

The construction sites of the wire-enumerated and retiring sets are outside this
population: decision 8 sends a change to a wire-enumerated set to its own ADR,
which takes that population on, and the retiring set goes with #841. They are
reachable today. `SqliteCanonicalStore.count_surfaceable_by_status` constructs
`Sensitivity` from a stored row (`infrastructure/sqlite/store.py:845`), and
`knowledge.status` reaches it (`mcp/tools.py:2471`) on the same MCP path that
opens the pointer's database without loading migrations (`mcp/tools.py:1769`;
*The derived store*, below), so the ADR that changes one of those sets inherits
that path for its own decoders.

The entry points an unknown value can reach refuse it as follows:

| Entry point | Where and as what | Names the unknown value? | Established by |
| :-- | :-- | :-- | :-- |
| Migration loader: `load_migrations` → `_load_one` (`infrastructure/filesystem/migration_loader.py:1607-1611`), for every command that loads migrations | JSON Schema validation, `MigrationError`: `<file> is invalid at operations/<N>: does not satisfy 'oneOf' (expected [...]); the value there is {...}`. The location is the operation index and the keyword is the discriminated `oneOf`, which fails whole, so neither the field nor the enum that failed is named | Only inside the echoed operation, which nothing marks as the offending value and which is bounded at `MAX_ECHOED_VALUE` (1,000 characters, `:213`). A legal operation can truncate it away | Measured: a copy of `examples/sample-project` with one value replaced. An unknown `kind` in `createItem` or in `upsertRevision.metadata`, `relationType`, `status`, `sensitivity`, `trustLevel` or `op`, and a `registerSpecification` renamed away, were each refused so, with the value inside the echo; the unmodified copy loads. An `addRelation` carrying a legal 990-character `note` (its `maxLength` is 1,000, and `note` sorts before `relationType` in the echo) and `relationType: traces_to` was refused with `traces_to` nowhere in the 1,315-character message; the same operation with `constrained_by` loads |
| The same loader, `apiVersion` | JSON Schema `const`, `MigrationError`: `is invalid at apiVersion: does not satisfy 'const' (expected 'theurian.dev/v1'); the value there is 'theurian.dev/v2'`, before the compiled comparison at `:1613` is reached. With `theurian.dev/v2` and an unknown `kind` in one document, this is the refusal reported | Yes | Measured, same copy |
| `validate_migration_document` (`:1103`), the validator the proposal service is given | The loader's seam (`_schema_rejection`), `MigrationError`: `invalid migration at operations/<N>: does not satisfy 'oneOf' ...` | As the loader | Measured: an unknown `relationType` and an unknown `op` refused, a valid document accepted |
| Proposal service `draft_from_document` (`application/proposal_service.py`), behind `knowledge.generateMigrationDraft` and OKF import's relations draft | The v1 gate skips an unknown `op` (`OperationKind(raw)` at `:3767-3770` `continue`s) and leaves it to the injected validator: `MigrationError` at draft, with nothing written (the method's `Raises`, `:1002-1004`). At `propose accept`, `_refuse_a_document_the_schema_rejects` wraps the validator's refusal as `ProposalError` (`:1850-1861`) | As the loader | Read from source, not run; the validator is the row above |
| MCP `knowledge.proposeChange` and `review.generateKnowledgeCandidate`, their `kind`, through `_closed_value` (`mcp/tools.py:1251`; called at `:3382` and `:3542`) | Handler, `ToolError`: `` `kind` must be one of: architecture, decision, domain, operations, security, testing, api, incident, convention, rejected-approach, known-exception. `` | No: it names the field and the valid set | Measured: `_closed_value` called with an unknown `kind`; `domain` accepted |
| `theurian propose --kind` (`cli/propose_commands.py:148-150`, typed `KnowledgeKind`) | Option parsing, Typer `BadParameter`: `'requirement' is not one of 'architecture', ..., 'known-exception'.` | Yes, with the valid set | Measured: the option's own type converted the value on the built command; the command was not invoked |
| OKF import, a concept's `type` (`application/okf_import.py:285-289`, `:544-550`) | Per concept, `ImportRefusal(kind="concept", key=<file>, literal="unrecognized type: 'specification'")`; other concepts still import | Yes | Measured: `_map_concept` on one concept file; `type: domain` maps |
| OKF import, a relation entry's `type` (`:586-590`, `_draft_relations` `:829-853`) | Carried verbatim into an `addRelation` in the one relations draft. An unknown type fails that draft's validation, and the whole draft is refused as one `ImportRefusal(kind="relations", key="addRelation")` carrying the validator's message, so every relation in the bundle is dropped, not only the unknown one | As the loader | Read from source, not run |
| Derived store row decoders: `_item_from_row` (`infrastructure/sqlite/store.py:1681`), `_revision_from_row` (`:1707`) and `_relation_from_row` (`:1784`). `knowledge.get`'s first store read is `get_item_metadata` → `_item_from_row` (`mcp/tools.py:2343`), before its `may_surface`/`may_disclose` gate (`:2351-2352`); `list_relations` decodes every edge inside `_read_all` (`store.py:968-973`) before `_relation_is_visible` filters them (`mcp/tools.py:2415`); `_revision_from_row` runs after the gate, through `current_revision` → `get_revision` (`mcp/tools.py:2397`; `store.py:582`, `:538`) | The decoder raises `ValueError` (`'requirement' is not a valid KnowledgeKind`, `'traces_to' is not a valid RelationType`), and every store read runs its decoder inside `_reading()` (`_read_one`, `:418-420`), which re-raises it as `StateDatabaseUnreadableError`: `This project's state database cannot be read (ValueError): it is damaged, or holds a value this build cannot interpret.`, followed by a remedy to delete `.theurian/state/` and run `theurian migrate apply` | No: the message names only the exception type; the value is on `__cause__` | Measured: each decoder called on a mapping inside `_reading()`, as `_read_one` calls it; the valid values decode |

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
reach the row decoders above without meeting the loader's refusal first, and
the store then reports an unreadable database whose remedy, a rebuild through
`theurian migrate apply`, runs into that same loader's refusal. The path is read
from source, not run; what the tool returns then is item 3 of *Compliance*'s
obligations.

Nothing at open checks which engine built the database. `create_database`
writes `schema_metadata.engine_version` (`infrastructure/sqlite/connection.py:1075`),
and the only read of `schema_metadata` at open selects `schema_version` alone
(`:1084`); no statement under `packages/theurian-core/src` selects the
`engine_version` column. So ADR-0007's invalidation holds on the build path, where the
state hash names the database, and not on the serve path, which opens whatever
the pointer names. The serve-path check is #853's.

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

**The key for "enumerates".** A construct closes a value when it names the
values an instance may take: an `enum`, a `const`, or a `oneOf` or `anyOf` whose
every branch is itself one of those. A closing construct enumerates a governed
set when every value it admits other than null is a member of that set. Over
every schema under `schemas/` except the migration schema, which is the read
grammar itself, the program below finds four such constructs and none of
`kind`, `relationType` or the operation set:
`schemas/knowledge/retrieval-result.schema.json`'s `status`, `trustLevel` and
`sensitivity`, and `schemas/config/project-config.schema.json`'s
`retrieval.includeStatuses`, which lists all six `status` members. Two enums
share members with a governed set without being contained in one, so the key
does not count them: `review-generate-knowledge-candidate-input`'s `category`
shares `rejected-approach` and `known-exception` with `KnowledgeKind`
(`domain/enums.py:69-70` against `:133-134`), and `review-findings-response`'s
`reviewer` shares `security` (`domain/enums.py:64` against
`domain/review_finding.py:65`). The key has two holes, and each is classified
by a person when it appears rather than passed silently. A construct carrying a
governed set plus other members is not contained in the set, so the key does
not count it. A `pattern` can close a value too — an alternation of members
would — and the key does not read patterns. Today no `pattern` names a governed
member: the ones that accept one are open identifier grammars (`projectId`,
`itemId`, `schemas/cli/version.schema.json`'s `platform`) that accept it as any
other identifier. Among `const`, `default`, `examples` and `pattern` sites, the
only governed value is `includeStatuses`' `default`, `["approved"]`, and a
`default` closes nothing.

```python
# Run from the repository root with `uv run --frozen python`.
import json
from collections import Counter
from pathlib import Path

from theurian.domain import enums, migration

GOVERNED = (
    enums.KnowledgeKind,
    enums.RelationType,
    migration.OperationKind,
    enums.KnowledgeStatus,
    enums.Sensitivity,
    enums.TrustLevel,
    enums.SpecificationStatus,
)
SETS = {cls.__name__: {member.value for member in cls} for cls in GOVERNED}
READ = Counter()


def construct(node):
    return "enum" if "enum" in node else "const" if "const" in node else "oneOf/anyOf"


def closed(node):
    """The values a closing construct admits, as text, or None if it closes nothing."""
    if isinstance(node.get("enum"), list):
        raw = node["enum"]
    elif "const" in node:
        raw = [node["const"]]
    else:
        branches = node.get("oneOf") or node.get("anyOf")
        if not isinstance(branches, list) or not branches:
            return None
        parts = [closed(branch) if isinstance(branch, dict) else None for branch in branches]
        return None if None in parts else set().union(*parts)
    return {
        value if isinstance(value, str) else json.dumps(value) for value in raw if value is not None
    }


def walk(node, path):
    if isinstance(node, dict):
        values = closed(node)
        if values is not None:
            READ[construct(node)] += 1
            yield path, values
            if construct(node) == "oneOf/anyOf":
                return
        for key, value in node.items():
            yield from walk(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, f"{path}/{index}")


for schema in sorted(Path("schemas").rglob("*.json")):
    if schema.name == "migration.schema.json":
        continue
    for path, values in walk(json.loads(schema.read_text()), "#"):
        inside = [name for name, members in SETS.items() if values and values <= members]
        shared = {
            name: sorted(values & members) for name, members in SETS.items() if values & members
        }
        if inside:
            print("enumerates", inside, schema, path)
        elif shared:
            print("overlaps", shared, schema, path)
print("read", dict(sorted(READ.items())))
```

It prints the following; the last line is the positive control that the walk
reaches every kind of construct the key names.

```text
enumerates ['KnowledgeStatus'] schemas/config/project-config.schema.json #/properties/retrieval/properties/includeStatuses/items
enumerates ['KnowledgeStatus'] schemas/knowledge/retrieval-result.schema.json #/properties/status
enumerates ['TrustLevel'] schemas/knowledge/retrieval-result.schema.json #/properties/trustLevel
enumerates ['Sensitivity'] schemas/knowledge/retrieval-result.schema.json #/properties/sensitivity
overlaps {'KnowledgeKind': ['security']} schemas/mcp/review-findings-response.schema.json #/properties/findings/items/properties/reviewer
overlaps {'KnowledgeKind': ['known-exception', 'rejected-approach']} schemas/mcp/review-generate-knowledge-candidate-input.schema.json #/properties/category
read {'const': 17, 'enum': 20, 'oneOf/anyOf': 2}
```

The MCP input schemas `knowledge-propose-change-input` and
`review-generate-knowledge-candidate-input` type `kind` as a string (and the
first types `trustLevel` and `sensitivity` as string or null), and
`knowledge-generate-migration-draft-input` constrains no `op`. The schemas do
hold an enum on a property named `kind` — `review-search-response`'s
`records.items.kind`, `pull-request`, `review-submission` and `review-thread`,
the kind of a review record and not `KnowledgeKind` — which is the positive
control that the walk reaches such a property.

Values still travel, un-enumerated, in two response fields, neither under a
response schema: `knowledge.get` publishes each visible relation's
`relationType` (`mcp/tools.py:2434`), and `knowledge.generateMigrationDraft`
publishes the drafted document's operation names as `operations`
(`_drafted_migration_payload`, `mcp/tools.py:1365`). A client meets a member it
does not know there as an unrecognised string. The key was
`git grep -n -E '"operations"|"relationType"|\.value for ' -- packages/theurian-core/src/theurian/mcp`,
whose other hits sit on refusal paths, `_closed_value`'s among them, which lists
`kind`'s members in its message (`mcp/tools.py:1263`). No MCP response publishes a
`KnowledgeKind`: under `mcp/`, it appears only as the two `_closed_value`
inputs above, and the one `.kind` published is the review record's
(`mcp/review_search.py:523`).

**`status`, `sensitivity` and `trustLevel` are enumerated on the wire.**
`knowledge-search-response`
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
   loader dispatches on. *Wire-enumerated* — `status`, `sensitivity`,
   `trustLevel` — is published as an `enum` on the `knowledge.search` wire
   (*Context*). Two of them are also *gate-feeding*: `status` feeds
   `may_surface` and `sensitivity` feeds `may_disclose` (`domain/enums.py:222`,
   `:269`). `trustLevel` feeds neither gate; it is a governance label all the
   same, which OKF import never copies from a bundle
   (`application/okf_import.py`'s module docstring, under ADR-0037 decision 1)
   and which import and candidate generation fix at `inferred`
   (`application/okf_import.py:180`, `domain/review.py:335`). *Retiring* —
   `SpecificationStatus` — leaves Core's writers with its entity under #841. The
   version constant, `apiVersion`, is what the other classes move or do not
   move.
2. **Adding a vocabulary member is additive.** No `apiVersion` bump and no
   `protocolVersion` bump; it is a Core MINOR, recorded under the CHANGELOG's
   `Added` naming the first Core version that reads it. An older Core keeps
   refusing a document that names the member — fail-closed, unchanged — because
   its schema does not admit it. A version bump would buy no safety: the older
   Core refuses either way. What it would buy is a better message, and that
   message is owed without one: today's refusal cannot be told from a typo
   (*Context*), and a refusal naming the file, the field path and the value, and
   saying that a newer Core may define it, is the scope of #849, which is due
   before #841 adds its `kind` member. That first member also waits on #853, the
   check at open that keeps an older Core from decoding a newer build's database
   row by row (*Context*, *The derived store*). This takes roadmap §4's
   recommendation for the first two of its three clauses.
3. **Adding an operation keeps ADR-0005's rule: it bumps `apiVersion`.** The
   rule is kept by deference, not re-derived here. An older Core refuses an
   unknown operation at schema validation exactly as it refuses an unknown
   `kind` (*Context*), so decision 2's argument reaches operations too; but
   ADR-0005 states the bump as a standing rule, roadmap Phase C's *Migration*
   row plans on it, and re-deciding a governed rule inside this ADR needs a
   benefit that neither keeping nor dropping it offers (*Alternatives
   considered*). The Core that bumps reads every earlier `apiVersion`
   (decision 5), and every document Core authors declares the **lowest**
   `apiVersion` whose grammar admits the document and under which it carries the
   meaning Core wrote, so a document that uses no new operation and no changed
   meaning stays readable by older Cores. Core authors documents in one place:
   the two lines under `packages/theurian-core/src` that stamp `apiVersion` are
   both in `application/proposal_service.py` (`:3813`, `:4393`), behind
   `theurian propose`, the MCP draft tools and OKF import.
4. **Removing or renaming a member or an operation takes it out of Core's own
   writers only. It never leaves the read grammar of the `apiVersion` that
   admitted it, and the removal by itself bumps neither `apiVersion` nor
   `protocolVersion`.** The reason is in the codebase, not in preference:
   applied migrations are frozen — an edited one is refused by
   `verify_no_applied_migration_changed`, a deleted one by
   `verify_no_applied_migration_removed` — and FR-K4 replays every committed
   document on a fresh clone, where nothing is recorded as applied, so a Core
   cannot tell an old document from a new one and must read both. An
   `apiVersion` bump for the removal would buy nothing: under decision 3's
   lowest-version rule no writer would declare it, and a Core that always
   declared it would lock out older Cores that read the document fine. OKF
   import is one of those writers —
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
   the new spelling plus this decision for the old one. A retiring set's
   read-side parse stays for the same reason: `SpecificationStatus` leaves
   Core's writers, and the loader's parse of a v1 `registerSpecification` keeps
   reading its `specId` and `status` (`infrastructure/filesystem/migration_loader.py:1714`,
   `:1718`; `domain/migration.py:272-287`), by these types or by a re-typed
   parse, which is #841's choice. This decision does not apply to the
   wire-enumerated sets (decision 8).
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
   difference. Otherwise the operation keeps its original effect. A reader of the
   canonical state is any site in `STATUS_GATE_CALL_SITES` or
   `DISCLOSURE_GATE_CALL_SITES`
   (`packages/theurian-core/tests/unit/test_gate_call_sites.py`) — the index
   builder, the withdrawal purge and the OKF export among them — plus
   `knowledge.status`'s counts, and #275's trace once it exists. The recorded
   argument is settled by measurement, not by reasoning: the committed corpus
   replayed under the old engine and the new one, with identical tool
   responses. A change whose effect reaches `status`, `sensitivity` or the
   withdrawal purge does not take this path; it takes decision 8's ADR-first
   route. And the bump invalidates cached state on the build path only: nothing
   at open reads `engine_version` (*Context*, *The derived store*), and the
   serve-path check is #853's. This is where this ADR meets ADR-0038's rejected
   alternative (b), whose objection to an
   engine-version bump was that it "does not tell a reader of the document which
   meaning it carries". That objection is about documents that can still be
   written; for them this decision requires the `apiVersion` bump. It cannot
   apply to a frozen document, which carries the version it was written under
   and can carry no other, and the recorded argument is what establishes that
   there is no second meaning for a reader to be told about. This decision does
   not apply to the wire-enumerated sets (decision 8).
7. **Reordering members is a reviewed diff, not a version event.** Values are
   strings, so a reorder changes no value's meaning and bumps nothing. The order
   is visible — the published schema's `enum` order is what a third party reads,
   and `packages/theurian-core/tests/unit/test_adr_0037_claims.py` holds
   `$defs/relationType` equal to `RelationType` as an ordered list because "a
   reorder is a diff its reviewer should see" — but neither it nor the order a
   refusal lists the valid values in is a contract.
8. **Wire-enumerated sets are outside the additive class.** Decision 2's "no
   `protocolVersion` bump" rests on decision 9's ground, that no schema outside
   the migration format enumerates the set; `retrieval-result.schema.json`
   enumerates all three of these (*Context*), so a change to one can move a
   published `enum`, and `protocolVersion` is a live question rather than a
   formality. Any change to `status`, `sensitivity` or `trustLevel` is therefore
   written as its own ADR, which decides its effect on `apiVersion`, on
   `protocolVersion`, on the Core version and on the CHANGELOG. For the two gate-feeding sets, `status`
   and `sensitivity`, that ADR is also written first, before implementation,
   under roadmap §6 principle 3. `trustLevel` feeds no gate; the governance
   ground in decision 1 is why its ADR still gets the same care. A change to
   `status` also moves `project-config.schema.json`'s `retrieval.includeStatuses`,
   which lists all six members; that is a published configuration schema, not
   the wire, and the same ADR answers for it. The concrete case is roadmap §9
   candidate 2, whose change to `SURFACEABLE_STATUSES` would move the
   three-member `status` enum `retrieval-result.schema.json` publishes.
9. **No change to `kind`, `relationType` or the operation set bumps
   `protocolVersion`**, because no schema outside the migration format
   enumerates any of them under the key *Context* states, which reads `enum`,
   `const`, and a `oneOf`/`anyOf` built from them, and leaves `pattern` as a
   stated hole. A member a client does not know reaches it as
   an unrecognised string, in the two response fields that publish any of the
   three: a `relationType` in `knowledge.get`'s `relations`, and an operation
   name in `knowledge.generateMigrationDraft`'s `operations`. A later change that
   publishes one of them as a wire `enum` re-opens this decision.
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
| **C2** Add an operation (decision 3) | Bumped; Core writes the lowest version whose grammar admits the document and under which it carries the meaning Core wrote | Unchanged (decision 9) | MINOR, pre- and post-1.0: no document an earlier Core read stops loading (decision 5) | `Added`, naming the new `apiVersion` and the first Core that reads it | Refuses a document declaring the new version at the schema `const`; a document using no new operation keeps the earlier version and loads | Reads it under its own version's grammar (decision 5) | `knowledge.generateMigrationDraft` carries the operation only once `V1_OPERATION_KINDS`, or its successor, admits it by a deliberate edit; an older Core's draft path refuses it at validation | Not reached: a bundle carries concepts and relation entries, not operations |
| **C3** Remove a member or an operation (decision 4) | Unchanged; the member stays in the read grammar of every version that admitted it | Unchanged (decision 9) | A break for Core's writers: MINOR pre-1.0, MAJOR post-1.0 | `Changed`, `BREAKING`, naming the writers that stop accepting it and stating that documents naming it still load | Unaffected: it still has the member | Reads it (decision 4); what it then does is decision 6's | Writers refuse it against their explicit write sets; a stored relation of a removed type is still published. The removal updates the examples that advertise a member: `plugins/claude-code/commands/propose.md:51` (`--kind architecture`), and the `kind` descriptions of `knowledge-propose-change-input` ("e.g. architecture, decision, security") and `review-generate-knowledge-candidate-input` ("e.g. convention, architecture, security") | Unaffected. The reverse direction — a newer Core importing a bundle naming it — refuses it at import |
| **C4** Rename a member or an operation (decision 4) | As C1 for a member, as C2 for an operation; the old spelling stays readable | Unchanged (decision 9) | As C3: MINOR pre-1.0, MAJOR post-1.0 | `Added` for the new spelling and `Changed`, `BREAKING`, for the old | As C1 or C2 for the new spelling | Reads the old spelling | As C1 or C2 for the new spelling, as C3 for the old | As C1 for the new spelling |
| **C5** Reorder members (decision 7) | Unchanged | Unchanged | No version event | None required; the diff is reviewed | No effect | No effect | No effect: values are strings, and the order a refusal lists them in is not a contract | No effect |
| **C6** Change what a member or operation means (decision 6) | Bumped for documents under the new meaning, and Core writes such a document at the version that carries that meaning, never at an earlier one whose grammar also admits it (decision 3); earlier versions keep the earlier meaning. Where a Core must change what an earlier document does, `MIGRATION_ENGINE_VERSION` is bumped instead, only with a recorded argument, settled by replay, that no reader observes the difference | Unchanged (decision 9) | MINOR, pre- and post-1.0: no document changes meaning for any reader | `Changed`, naming the `apiVersion`, or the engine version, under which the new meaning applies | Refuses a document declaring the new version | Earlier meaning; under the engine-version path, a different effect no reader of the canonical state observes | No effect by itself | No effect: a bundle carries values, not a migration `apiVersion` |
| **C7** Any of C1–C6 on `status`, `sensitivity` or `trustLevel` (decision 8) | Decided by the change's own ADR | Decided by that ADR: all three are enumerated on the wire | Decided by that ADR | Decided by that ADR | Decided by that ADR; until one exists, an unknown value is refused at schema validation like any other | Decided by that ADR | Decided by that ADR; today `_closed_value` refuses an unknown `trustLevel` or `sensitivity`, and `retrieval-result.schema.json` enumerates all three | Decided by that ADR |
| **C8** `SpecificationStatus`, which leaves with the entity (#841) | Unchanged by its removal (decision 4); `$defs/opRegisterSpecification/properties/status` stays in the v1 read grammar. No member is added: nothing new is built on the entity (ADR-0038 decision 3) | Unchanged: it appears on no wire schema | As C3, in #841's release | As C3, in #841's entry | Unaffected | Reads a v1 `registerSpecification` naming it; what that operation then does is decision 6's, in #841 | Not published | Not reached: the OKF export does not read the `specifications` table (ADR-0038, *Context*) |

## Consequences

### Positive

- **Phase C's new kinds land without breaking a document.** `requirement` and
  `specification` (roadmap §4, item 1) are C1: a Core MINOR, readable by that
  Core and every later one, refused by earlier ones exactly as today.
- **No committed document becomes unreadable under this policy.** Decisions 4
  and 5 together mean FR-K4's replay holds on every Core that ships under it.
- **`apiVersion` moves only for grammar and for meaning.** For meaning it is the
  one signal a reader of the document has (decision 6). For grammar it carries
  nothing an older Core's refusal lacks, and it is kept by deference to
  ADR-0005's rule (decision 3).

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
- **Decision 6's engine-version path rests on a recorded replay, not a standing
  check.** Each use records its own replay of the committed corpus; nothing
  re-runs it afterwards, and nothing at open reads the engine version.
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
| **Bump `protocolVersion` for a vocabulary or grammar change** | No schema outside the migration format closes a value to `kind`, `relationType` or the operation set under *Context*'s key, so no client validating against a published schema validates against their membership (decision 9); `pattern` is the key's stated hole. The wire-enumerated sets are left to their own ADRs (decision 8). |
| **Stop bumping `apiVersion` for operations too** | An older Core refuses an unknown operation at schema validation exactly as it refuses an unknown `kind` (*Context*), so decision 2's argument reaches operations as well. Declined here, not refuted: ADR-0005 states the bump as a standing rule and roadmap Phase C's *Migration* row plans on it, and re-deciding a governed rule inside this ADR needs a benefit that neither keeping nor dropping it offers (decision 3). It stays the named option for a later slice. |

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
   newer Core may define it (decision 2). The value it names stays bounded by
   `MAX_ECHOED_VALUE` and escaped by `repr`, because an author, an agent or an
   imported bundle chose it.
2. **[#841](https://github.com/theurian/theurian/issues/841), the retirement.**
   An explicit write set at each writer decision 4 names; the two
   operations and `SpecificationStatus` kept in the v1 schema and loader; and
   either the recorded argument decision 6 requires, with the
   `MIGRATION_ENGINE_VERSION` bump, or the operations' original effect kept.
3. **[#853](https://github.com/theurian/theurian/issues/853), what an older
   Core does with a state database a newer build wrote.** It is decided per
   database, at open, before any row is decoded. A per-row refusal of any
   wording carries a withheld-versus-absent bit: a withheld row naming a member
   the Core lacks is refused as an unreadable database before the gate runs,
   where an absent id is answered as absent. Its two faces are the item decode
   (`knowledge.get`'s `get_item_metadata`) and the relation decode
   (`list_relations`), both ahead of the gate (*Context*). Its acceptance is a
   two-corpora test: a withheld row carrying an unknown member against an absent
   id, with identical responses. The serve-path engine check decision 6 names
   is part of it.
4. **The slice that first bumps `apiVersion`** — Phase C's edge operation, if
   [#275](https://github.com/theurian/theurian/issues/275)'s representation
   needs one: the multi-version read (decision 5) in the schema, the loader and
   the proposal service's check, and the lowest-version write (decision 3).
5. **The slice that adds the first `relationType` member**: ADR-0037's
   amendment, for the four places it states the count.
6. **Roadmap §9 candidate 2's ADR**: its effect on `protocolVersion`, since the
   `status` enum it would move is published (decision 8).
