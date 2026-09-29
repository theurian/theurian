"""ADR-0038's repository facts, held against the tree they were measured on.

ADR-0038 decides that the ``Specification`` entity folds into a knowledge
``kind``; the branch where the entity stays separate is the rejected one. The
ADR ships no code, so every sentence it states about the codebase was measured
once, at ``855ebd87``, and the slices written against it -- #274's policy,
#275's edge representation, the retirement (#841) -- read those sentences rather
than the tree.

Each claim is held from both sides: a fact half recomputed from live source,
RED when the tree moves, and a prose half (:func:`test_the_adr_still_states`,
here), RED when the record drifts. A new reference to a specification reader --
the shape a breach of decision 3 takes -- reddens
``test_adr_0038_populations.py``'s
:func:`test_the_store_specification_methods_are_reached_only_by_the_engines_two_writes`.

What is held here: the decision itself, the Compliance section's naming of this
module, and the prose half. The fact halves are in three siblings:

- ``test_adr_0038_populations.py`` -- who references, reads, writes and defines
  what: the entity's store methods and the ``specifications`` SQL, the
  ``structured`` readers and whole-object reads, the compiled ``apiVersion``
  checks, ``must_be_acyclic``'s callers, the provider's implementations and the
  alias guard's operations.
- ``test_adr_0038_model.py`` -- the entity, its table and its registration; the
  field table and where each field lands on the specification item; relations,
  trace types and ``with_revision``; the relation gate the id space lands in;
  and the two status vocabularies.
- ``test_adr_0038_records.py`` -- what the records the ADR cites say: the sample
  project's registration, the roadmap and README, the published schemas,
  ADR-0005, the dogfood corpus, and the source docstring, comment and test
  constant the ADR cites.

Every module of the set reads the tree and the documents through
``adr_0038_support``, whose docstring states once what the scans reach and what
they do not parse.

Pure: syntax trees, in-memory domain objects, JSON schemas, YAML, Markdown, and
read-only ``git ls-files`` and ``git grep``. No database, socket or temporary
directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest
from adr_0038_support import REPO_ROOT, _adr, _collapsed, _section, _table

pytestmark = pytest.mark.unit


THIS_MODULE: Final = Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()


# -- The decision (AC-1) ------------------------------------------------------


def test_the_adr_records_the_fold_as_accepted_and_rejects_keeping_the_entity_separate() -> None:
    lines = _adr().read_text(encoding="utf-8").splitlines()
    alternatives = _table(_section(_adr(), "## Alternatives considered"))

    assert lines[0] == "# ADR-0038: The Specification entity folds into a knowledge `kind`"
    assert "- Status: accepted" in lines
    assert alternatives[0] == ("Alternative", "Why rejected")
    assert (
        "Keep Specification a separate entity and bridge it to knowledge by relations or "
        "traceability edges"
    ) in {_collapsed(row[0]) for row in alternatives[1:]}


def test_the_compliance_section_names_this_module() -> None:
    assert THIS_MODULE == "packages/theurian-core/tests/unit/test_adr_0038_claims.py"


# -- The prose half -----------------------------------------------------------


#: What ADR-0038 still has to say, in its own markup and wrapped at will: the
#: comparison collapses whitespace on both sides. Each entry's fact half is the
#: test, here or in a sibling module, that carries the same claim.
ADR_STATES: Final[dict[str, str]] = {
    "measured-at": """**Every repository fact below was measured on 2026-09-29 against
        `origin/main` at `855ebd87`.**""",
    "decision-1": "1. **A specification is a knowledge item of `kind: specification`.**",
    "decision-3": "3. **Nothing new is built on the entity.**",
    "entity": """`domain/specification.py` declares a `Specification` entity with its own
        table (`CREATE TABLE specifications` in `infrastructure/sqlite/schema.py`), its own
        identifier (`SpecId`), its own status vocabulary (`SpecificationStatus`) and two
        migration operations, `registerSpecification` and `supersedeSpecification`.""",
    "edges-unbuilt": """`traceability_edges` is declared in `schema.py`, and
        `CanonicalStore.add_traceability_edge` and `list_traceability_edges` are declared on
        `domain/ports/canonical_store.py` with no implementation and no caller.""",
    "reference": """**The entity is a reference from a knowledge item to a separate document,
        pinned at one revision of that item.**""",
    "registration": """`application/migration_engine.py`'s `_register_specification` looks up
        the operation's `itemId`, refuses when that item has no current revision, and constructs
        a `Specification` whose `revision_id` is the item's `current_revision_id` and whose
        `title` is the spec id's own string; it passes no `structured` and no `anchors`.""",
    "only-link": "The entity has no item field, so that revision is its only link to the item.",
    "sample-document": """the one committed `registerSpecification`, the sample project's
        (*Negative*), registers `spec.order-cancellation` against `domain.order-cancellation`, a
        `kind: domain` item with a `text/markdown` revision, while its `sourceUri` and
        `format: application/yaml` name a different tracked file,
        `examples/sample-project/.theurian/specifications/order-cancellation.yaml`.""",
    "re-pin": """Only a registration writes `revision_id` (an upsert, so re-registering the spec
        id re-pins it); a later revision of the item leaves it,""",
    "supersede-sets": "`supersede_specification`, sets `status` and `superseded_by`.",
    "table-shape": """The table's `revision_id` is a foreign key to
        `knowledge_revisions(revision_id)`; the table has no `anchors` column and no
        `sensitivity` column.""",
    "decoder": """`_specification_from_row` builds a `Specification` without `anchors`, so a
        stored specification always reads back with `anchors=()`.""",
    "readers": """`store.py` implements two readers, `get_specification` and
        `list_specifications`, and they hold the only `FROM specifications` SQL in
        `packages/theurian-core/src`.""",
    "readers-uncalled": """No call of `.get_specification(` or `.list_specifications(` exists
        anywhere in the repository.""",
    "writes-only": """The only calls into the store's specification methods are the two writes
        in `migration_engine.py`, `register_specification` and `supersede_specification`.""",
    "okf-export": """The OKF export (`application/okf_export.py`) walks knowledge items,
        revisions and relations and never the `specifications` table""",
    "structured-spec": """`Specification.structured` defaults to an empty dict, and
        `_register_specification` never sets it.""",
    "structured-revision": """`KnowledgeRevision.structured` is declared, stored
        (`knowledge_revisions.structured`) and published by `knowledge.get` (`mcp/tools.py`),
        but no construction of a `KnowledgeRevision` in `packages/theurian-core/src` other than
        the store's row decoder passes `structured`: the migration engine's does not, and
        `KnowledgeRevision.create` has no caller there.""",
    "ingest-reader": """`application/ingestion_service.py` carries it into
        `IngestedDocument.structured`, whose only reader is the `theurian ingest` report.""",
    "fr-t1": "neither `structured` field contributes to it.",
    "roadmap-recommends": """the unified form: spec-as-knowledge in `kind`, the machine-readable
        payload in `structured`.""",
    "candidates-owed": """The roadmap lists candidates 3 and 6 as owed "before Phase C" and
        candidate 4, #275, as Phase C's own.""",
    "principle-3": """§6 principle 3 ("A disclosure change is ADR-first") names Phase D's
        history access as its most direct case, and the README marks only Phase D ADR-first.""",
    "security-row": """Phase C's own Security row nevertheless opens "A graph response is a new
        disclosure family\"""",
    "kind-absent": """`KnowledgeKind` has no `specification` member today, and this ADR does not
        add one""",
    "parsers-produce": """which the structured and OpenAPI parsers under
        `infrastructure/filesystem/parsers/` already produce at ingestion""",
    "trace-node": """A `TraceNode` whose `node_type` is `TraceNodeType.SPECIFICATION` is to carry
        a knowledge item id: the node type names the role, and the id space is the knowledge
        item id space.""",
    "refs-trailer": """[`traceability.md`](../architecture/traceability.md) draws a commit
        trailer, `Refs: spec.order-cancellation`""",
    "kind-rides": """`kind` rides on the revision: `KnowledgeItem.with_revision` adopts
        `kind=revision.metadata.kind` (`domain/knowledge.py`), so an item entered before
        `kind: specification` exists can be retyped by a later revision without changing the id
        its edges address.""",
    "same-grammar": "since `SpecId` shares `ItemId`'s grammar",
    "relation-no-revision": "a `KnowledgeRelation` names two items and no revision.",
    "pin-unread": "Nothing reads the pin today (*Context*).",
    "one-field-lacking": """It keeps a table whose one field the knowledge side lacks is the
        revision pin (the field table)""",
    "source-commit": "(roadmap §4 item 2's `source_commit` pinning)",
    "structured-column": """The payload's home moves from a column that exists,
        `specifications.structured TEXT NOT NULL DEFAULT '{}'`, to a field whose writer is
        owed""",
    "self-relation": """two specifications registered against one item would be a
        self-relation, which `KnowledgeRelation` refuses""",
    "supersedes-row": """which `deprecateItem`'s `supersededBy` already writes and which
        `ACYCLIC_RELATIONS` declares acyclic (INV-6). `KnowledgeRelation.must_be_acyclic` has
        no caller in `packages/theurian-core/src`""",
    "gate": """`mcp/tools.py`'s `_relation_is_visible` checks both endpoints of a relation, each
        read by the id it literally names through
        `CanonicalReadSession.get_item_exact_metadata`, which does not resolve an alias, against
        `may_surface` and `may_disclose`.""",
    "t21-corrected-form": '("a traversal hop must not resolve an alias when deciding authority")',
    "two-properties": """it does not resolve an alias (T-21), and it reads the endpoint's
        metadata, never its body""",
    "t26": "[T-26](../security/threat-model.md), closed in 0.2.3 by the metadata form.",
    "get-item-exact": """A hop read through `get_item_exact` keeps the first property and drops
        the second.""",
    "gate-others": """both endpoints judged, with no direction inference; a missing endpoint
        withheld; and the read scoped to the project, as `_ITEM_METADATA_SQL`'s `project_id`
        scopes it and a `TraceNode`, which carries no project, cannot.""",
    "no-trace-path": """No traceability read path exists for it to gate — `knowledge.trace` is
        not registered, `system.capabilities` publishes `traceability: false`, and
        `TraceNode.node_id` is a free string""",
    # The refusal itself is held by tests/integration/test_alias_item_id_collision.py.
    "alias-guard-keys": """`refuse_alias_item_id_collision` refuses an `addAlias` key equal to
        the id of a live, non-deprecated item. It takes alias keys from `addAlias` and
        `removeAlias` alone, so a spec id reaches it only as an `addAlias`""",
    "trace-types-stay": """but not `TraceNode`, `TraceabilityEdge`, `TraceabilityRule` or
        `TraceabilityPolicy`, which stay in `domain/specification.py`""",
    "get-coverage": "[`traceability.md`](../architecture/traceability.md)'s `spec.getCoverage`",
    "kind-published": "`schemas/migrations/migration.schema.json` publishes it as `$defs/kind`.",
    "mcp-kind": """type `kind` as a string whose description names `KnowledgeKind` as the closed
        set their handlers refuse against.""",
    "closed-set": """`registerSpecification` and `supersedeSpecification` are members of
        ADR-0005's closed operation set.""",
    "adr-0005": """ADR-0005 makes *adding* an operation "a protocol change" requiring an
        `apiVersion` bump and says nothing of removing one""",
    "schema-and-v1": """Both are in `schemas/migrations/migration.schema.json`
        (`$defs/opRegisterSpecification`, `$defs/opSupersedeSpecification`) and in
        `application/proposal_service.py`'s `V1_OPERATION_KINDS`""",
    "api-version": """The published schema pins `apiVersion` with `"const": "theurian.dev/v1"`,
        and two compiled checks refuse on exact equality against `MIGRATION_API_VERSION`
        (`"theurian.dev/v1"`, `domain/migration.py`):
        `infrastructure/filesystem/migration_loader.py`
        (`if document["apiVersion"] != MIGRATION_API_VERSION:`) and
        `application/proposal_service.py`
        (`if document.get("apiVersion") != MIGRATION_API_VERSION:`).""",
    "measured-corpus": "*What is committed, measured 2026-09-29 at `855ebd87`.*",
    "corpus-command": r"""(`git ls-tree -r --name-only HEAD .theurian/migrations/ |
        grep -c '\.yaml$'`)""",
    "version-command": """(`git grep -h '^apiVersion' -- .theurian/migrations/ | sort |
        uniq -c`""",
    "none-names-command": """none names either operation
        (`git grep -c -E 'registerSpecification|supersedeSpecification' --
        .theurian/migrations/` prints nothing).""",
    "yaml-population": """Across every committed `.yaml` and `.yml` file, the one naming either
        operation is the sample project's
        `examples/sample-project/.theurian/migrations/01K1DEFABC01234567890ABCDE-add-order-cancellation.yaml`
        (`git grep -l -E 'registerSpecification|supersedeSpecification' -- '*.yaml'
        '*.yml'`).""",
    "status-vocabularies": """It holds `draft`, `active`, `superseded` and `retired`;
        `KnowledgeStatus` holds `draft`, `proposed`, `approved`, `deprecated`, `superseded` and
        `rejected`.""",
    "no-counterpart": "`proposed` and `rejected` have no specification counterpart",
    "old-path": """A v1 document may still carry `registerSpecification`, including one drafted
        through `knowledge.generateMigrationDraft`, and it writes a row that no caller reads.""",
    "schema-docstring": """`infrastructure/sqlite/schema.py`'s module docstring: every byte is
        reconstructible by replaying the Git-tracked migrations into an empty file""",
    "dotted-id": """Both subclass `_DottedId` (`domain/identifiers.py`), and the migration schema
        types `specId` and `supersededBy` as `$defs/itemId`.""",
    "provider": """The port (`domain/ports/specification_provider.py`) has no implementation —
        no `def discover` exists under `packages/` outside it — and its `discover` returns
        `tuple[Specification, ...]`.""",
    "unpopulated": """excludes `traceability_edges`, and only that table, from its
        every-table-holds-a-row check.""",
    "walkers": """the index build, `knowledge.get`'s relations, the OKF export — none of which
        reads the `specifications` table today.""",
    "loader-classes": """the loader parses them into their own operation classes
        (`RegisterSpecification`, `SupersedeSpecification`)""",
    "engine-version": """`MIGRATION_ENGINE_VERSION` (`domain/migration.py`) is hashed into the
        state hash "so an engine change invalidates cached state instead of silently
        reinterpreting it (ADR-0007)\"""",
    "alias-guard": """The alias collision guard in `application/migration_alias_guards.py` reads
        `addAlias` and `removeAlias` and never `registerSpecification`""",
    "sql-semantics": """`supersedeSpecification` is an `UPDATE` of one `specifications` row —
        nothing when no row matches — where `addRelation` is an `INSERT OR IGNORE` into
        `knowledge_relations`""",
    "compliance": "`packages/theurian-core/tests/unit/test_adr_0038_claims.py`, the claims pin.",
}


@pytest.mark.parametrize("fragment", ADR_STATES.values(), ids=list(ADR_STATES))
def test_the_adr_still_states(fragment: str) -> None:
    assert _collapsed(fragment) in _collapsed(_adr().read_text(encoding="utf-8")), (
        f"ADR-0038 no longer states:\n\n  {_collapsed(fragment)}\n\nIf the fact half is GREEN, "
        f"the tree did not move and the record is what gets restored; if it is RED, the "
        f"sentence moves with the tree."
    )
