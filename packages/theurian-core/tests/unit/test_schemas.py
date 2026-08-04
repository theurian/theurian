"""The published JSON Schemas are the contract between Core and every client.

A malformed schema fails open: every document validates, and the contract stops
meaning anything. These tests check that the schemas themselves are valid, and
that the constraints the design depends on are actually present.

A schema can also fail *closed*, which is what happened here. For an entire
milestone ``retrieval-result.schema.json`` rejected every real
``knowledge.search`` result: it required four fields nothing emits and declared
none of the two every ranked hit carries. Nothing noticed, because every test in
this file asserted a property *of the schema* and none had ever compared it
against a response. That comparison now lives in
``tests/integration/test_wire_contract.py`` — a real ``knowledge.search``
response, from the real CLI, validated against these schemas — rather than
here, because it drives subprocess, SQLite and the filesystem, and this
directory is I/O-free by convention. Everything below is schema shape only: no
project is built, no tool is called.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Callable
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
SCHEMAS = REPO_ROOT / "schemas"

ALL_SCHEMA_PATHS = sorted(SCHEMAS.rglob("*.schema.json"))

RETRIEVAL_METADATA = "mcp/retrieval-metadata.schema.json"
RETRIEVAL_RESULT = "knowledge/retrieval-result.schema.json"


def _load(relative: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((SCHEMAS / relative).read_text(encoding="utf-8"))
    return loaded


def test_schemas_directory_is_populated() -> None:
    assert ALL_SCHEMA_PATHS, "no schemas found; the shared contract is missing"


@pytest.mark.parametrize("path", ALL_SCHEMA_PATHS, ids=lambda p: p.name)
def test_schema_is_valid_draft_2020_12(path: pathlib.Path) -> None:
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("path", ALL_SCHEMA_PATHS, ids=lambda p: p.name)
def test_schema_declares_an_id_and_title(path: pathlib.Path) -> None:
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert schema.get("$id", "").startswith("https://theurian.dev/schemas/")
    assert schema.get("title")


@pytest.mark.parametrize("path", ALL_SCHEMA_PATHS, ids=lambda p: p.name)
def test_object_schemas_reject_unknown_properties(path: pathlib.Path) -> None:
    """Silently accepting an unknown field turns a typo into a no-op.

    In a migration format that means an operation someone believes they applied
    and did not.
    """
    schema = json.loads(path.read_text(encoding="utf-8"))
    if schema.get("type") == "object" and "patternProperties" not in schema:
        assert schema.get("additionalProperties") is False, (
            f"{path.name}: top-level object schemas must set additionalProperties: false"
        )


# -- Migration schema ------------------------------------------------------


@pytest.fixture
def migration_validator() -> Draft202012Validator:
    return Draft202012Validator(_load("migrations/migration.schema.json"))


def _valid_migration() -> dict[str, Any]:
    return {
        "apiVersion": "theurian.dev/v1",
        "id": "01K1DEFABC1234567890ABCDEF",
        "createdAt": "2026-08-01T00:30:00+09:00",
        "author": "engineer@example.com",
        "operations": [
            {
                "op": "upsertRevision",
                "itemId": "architecture.auth-policy",
                "revisionId": "01K1DEFREV1234567890ABCDEF",
                "contentFile": "../knowledge/architecture/auth-policy.md",
                "metadata": {
                    "title": "Authentication and authorization policy",
                    "contentType": "text/markdown",
                    "kind": "architecture",
                    "namespace": "backend",
                    "status": "approved",
                    "owner": "platform-team",
                },
            }
        ],
    }


def test_a_representative_migration_validates(migration_validator: Draft202012Validator) -> None:
    migration_validator.validate(_valid_migration())


@pytest.mark.parametrize(
    "bad_id",
    [
        "01K1DEFABC1234567890ABCDE",
        "not-a-ulid",
        "81K1DEFABC1234567890ABCDEF",  # invalid-ulid
        "01k1defabc1234567890abcdef",
    ],
)
def test_malformed_migration_ids_are_rejected(
    migration_validator: Draft202012Validator, bad_id: str
) -> None:
    migration = _valid_migration()
    migration["id"] = bad_id
    with pytest.raises(ValidationError):
        migration_validator.validate(migration)


@pytest.mark.parametrize(
    "bad_path",
    ["/etc/passwd", "C:\\Windows\\system.ini", "C:/Windows/system.ini"],
)
def test_absolute_content_paths_are_rejected_by_schema(
    migration_validator: Draft202012Validator, bad_path: str
) -> None:
    """Schema-level defence in depth. The runtime check in
    ``theurian.security.paths`` is the real control, because only it can resolve
    symlinks -- but rejecting the obvious case at parse time is free.
    """
    migration = _valid_migration()
    migration["operations"][0]["contentFile"] = bad_path
    with pytest.raises(ValidationError):
        migration_validator.validate(migration)


def test_unknown_operation_is_rejected(migration_validator: Draft202012Validator) -> None:
    migration = _valid_migration()
    migration["operations"] = [{"op": "deleteEverything", "itemId": "a.b"}]
    with pytest.raises(ValidationError):
        migration_validator.validate(migration)


def test_unknown_field_in_an_operation_is_rejected(
    migration_validator: Draft202012Validator,
) -> None:
    migration = _valid_migration()
    migration["operations"][0]["expectedRevisions"] = []
    with pytest.raises(ValidationError):
        migration_validator.validate(migration)


def test_empty_operations_list_is_rejected(migration_validator: Draft202012Validator) -> None:
    migration = _valid_migration()
    migration["operations"] = []
    with pytest.raises(ValidationError):
        migration_validator.validate(migration)


def test_change_sensitivity_requires_a_reason(
    migration_validator: Draft202012Validator,
) -> None:
    """Reclassification changes who can read the content and forces affected
    RAPTOR trees to rebuild. The rationale is part of the record."""
    migration = _valid_migration()
    migration["operations"] = [
        {"op": "changeSensitivity", "itemId": "architecture.auth-policy", "sensitivity": "public"}
    ]
    with pytest.raises(ValidationError):
        migration_validator.validate(migration)


def test_migration_schema_covers_every_required_operation() -> None:
    """§15 of the brief names fourteen operations. All must be expressible."""
    defs = _load("migrations/migration.schema.json")["$defs"]
    declared = {
        definition["properties"]["op"]["const"]
        for name, definition in defs.items()
        if name.startswith("op") and name != "operation"
    }
    assert declared == {
        "createItem",
        "upsertRevision",
        "deprecateItem",
        "restoreItem",
        "addRelation",
        "removeRelation",
        "addAlias",
        "removeAlias",
        "changeSensitivity",
        "changeOwner",
        "registerSpecification",
        "supersedeSpecification",
        "addEvidence",
        "removeEvidence",
    }


# -- MCP context schema ----------------------------------------------------


def test_project_id_is_required_on_every_tool_call() -> None:
    """No implicit "current project" (ADR-0002).

    With many agents on one daemon, an implicit default resolves one agent's
    query against another agent's project.
    """
    validator = Draft202012Validator(_load("mcp/tool-context.schema.json"))
    validator.validate({"projectId": "backend-service"})
    with pytest.raises(ValidationError):
        validator.validate({"snapshotId": None})


# -- Retrieval result schema -----------------------------------------------


def test_retrieval_results_are_never_executable() -> None:
    """SEC-15: Theurian returns documents, never runnable content."""
    schema = _load("knowledge/retrieval-result.schema.json")
    assert schema["properties"]["executable"] == {
        "const": False,
        "description": "Always false. Theurian returns documents, never runnable content.",
    }


def test_retrieval_results_require_provenance_and_safety_labels() -> None:
    """SEC-15, FR-R5. A result without an anchor is an unverifiable assertion,
    and one without the trust labels invites an agent to read a document as an
    instruction.

    ``snapshotId`` and ``indexBuildId`` were required here for two milestones
    and were emitted by nothing: this assertion held for a schema describing a
    response Theurian could not produce. A required-but-never-sent field is the
    same defect as a sent-but-never-declared one and is harder to notice,
    because nothing rejects it — reading the schema is not checking it. What
    checks it is ``test_wire_contract.py``, which validates a real tool response
    against this file in both directions.
    """
    schema = _load("knowledge/retrieval-result.schema.json")
    required = set(schema["required"])
    assert {
        "sourceAnchors",
        "contentClassification",
        "mayContainInstructions",
        "executable",
    } <= required


def test_the_published_result_cap_is_the_one_the_tool_enforces() -> None:
    """FR-R4. The number a client reads and the number the tool applies.

    ``knowledge-search-response.schema.json`` tells clients ``count`` is at most
    fifty, and ``knowledge.search`` clamps ``limit`` to :data:`MAX_RESULTS`. Two
    statements of one cap, in two files, with nothing between them: a code cap
    raised above the schema's makes every large answer violate the contract
    Theurian publishes, and one lowered below it leaves the schema promising
    reach the product no longer has.

    Written as an equality rather than an inequality on purpose. ``<=`` would
    hold while the published maximum was pure fiction, which is the failure worth
    catching — the integration test that a caller asking for the cap receives it
    (``test_a_caller_who_asks_for_the_published_maximum_receives_it``) reads its
    number from ``MAX_RESULTS``, and this is what stops that number drifting away
    from the published one.
    """
    from theurian.mcp.tools import MAX_RESULTS

    schema = _load("mcp/knowledge-search-response.schema.json")

    assert schema["properties"]["count"]["maximum"] == MAX_RESULTS


def test_the_source_anchor_contract_matches_the_invariant_it_came_from() -> None:
    """INV-8 is a disjunction, and the schema used to publish half of it.

    ``minItems: 1`` said every result carries an anchor. The domain says every
    revision carries an anchor *or* declares that it originates in Theurian --
    and it enforces that at both ends, refusing to construct a revision
    satisfying neither when a migration is applied and again when the row is
    read back. So an empty array on the wire has exactly one meaning, which is
    why dropping the bound loses nothing a client could act on.

    What the schema must still hold: the key is always present, so a client
    reads an array rather than testing for one, and each anchor is still a
    complete route back. ``tests/integration/test_wire_contract.py`` carries the
    other half -- a real anchorless response, which is how this was found.
    """
    anchors = _load(RETRIEVAL_RESULT)["properties"]["sourceAnchors"]

    assert "minItems" not in anchors, (
        "a revision labelled `authored-in-theurian` is a supported state and "
        "reaches the wire with an empty array"
    )
    assert "sourceAnchors" in _load(RETRIEVAL_RESULT)["required"]
    assert anchors["items"]["required"] == ["provider", "sourceUri"]
    assert anchors["items"]["additionalProperties"] is False


#: ``(schema field, domain type, candidate)``. Each candidate is checked for
#: agreement, never for a fixed verdict: the claim is that the published pattern
#: admits exactly what the domain constructs, so a case is interesting whether
#: it is legal or not. A schema looser than the domain invites a client to
#: handle a value Theurian will never send; a tighter one rejects a legitimate
#: result, which is how ``sourceAnchors`` went a milestone describing a response
#: the product does not produce.
_IDENTIFIER_CANDIDATES = (
    "architecture.auth-policy",
    "architecture",
    "a.b.c",
    "auth-policy-v2.detail",
    "Architecture.Auth",
    "architecture..auth",
    "architecture.",
    "architecture auth",
    "architecture_auth",
    "",
    "a" * 200,
    "a" * 201,
)

_MEDIA_TYPE_CANDIDATES = (
    "text/markdown",
    "application/vnd.oai.openapi+json",
    "text/x-yaml",
    "text/markdown; charset=utf-8",
    "Text/Markdown",
    "text",
    "text/",
    "/markdown",
    "",
)

_ULID_CANDIDATES = (
    "01K1DEFABC1234567890ABCDEF",
    "01K1DEFABC1234567890ABCDE",
    "81K1DEFABC1234567890ABCDEF",  # invalid-ulid
    "01k1defabc1234567890abcdef",
    "01K1DEFABC1234567890ABCDEI",  # invalid-ulid
    "",
)


def _admits(subschema: dict[str, Any], candidate: str) -> bool:
    return Draft202012Validator(subschema).is_valid(candidate)


def _constructs(build: Callable[[str], object], candidate: str) -> bool:
    from theurian.domain.errors import TheurianError

    try:
        build(candidate)
    except TheurianError:
        return False
    return True


@pytest.mark.parametrize(
    ("field", "candidates"),
    [
        ("itemId", _IDENTIFIER_CANDIDATES),
        ("contentType", _MEDIA_TYPE_CANDIDATES),
        ("revisionId", _ULID_CANDIDATES),
    ],
)
def test_published_patterns_admit_exactly_what_the_domain_constructs(
    field: str, candidates: tuple[str, ...]
) -> None:
    """The patterns are transcribed by hand into the schema, so nothing but this
    notices when one side moves.

    A response-based check cannot cover this: a fixture reaches the ids it
    happens to contain, and the interesting cases are the ones no corpus has --
    a 201-character item id, a media type carrying a `charset` parameter, a ULID
    with an ambiguous character. Comparing the two decisions is total over the
    cases listed and costs no I/O.
    """
    from theurian.domain.identifiers import ItemId, RevisionId
    from theurian.domain.values import MediaType

    builders: dict[str, Callable[[str], object]] = {
        "itemId": ItemId,
        "contentType": MediaType,
        "revisionId": RevisionId,
    }
    subschema = _load(RETRIEVAL_RESULT)["properties"][field]

    for candidate in candidates:
        assert _admits(subschema, candidate) == _constructs(builders[field], candidate), (
            f"{field}: schema and domain disagree about {candidate!r}"
        )


# -- CLI contract ----------------------------------------------------------


def test_version_output_matches_its_published_schema() -> None:
    """The first contract the plugin depends on."""
    from theurian.cli.main import _version_payload

    Draft202012Validator(_load("cli/version.schema.json")).validate(_version_payload())


# -- Closed vocabularies: the published enums against the code's constants ---
#
# A response-based conformance check can only exercise the values its fixture
# reaches. Covering all seven `fallbackReason` codes that way needs seven
# differently-broken index states, so a code added in `theurian.mcp.search` and
# forgotten here would sail past it while breaking the one client that switches
# on the field. These compare the published vocabulary against the constants the
# code actually emits from, which is total and costs nothing.


def _published_consts(relative: str, field: str) -> set[Any]:
    """The values a ``oneOf``-of-``const`` property is allowed to take.

    Spelled that way rather than as an ``enum`` so each value carries its own
    description: a client reading `index-pointer-invalid` needs to know its
    remedy differs from `no-index`, and a bare enum has nowhere to say so.
    """
    return {case["const"] for case in _load(relative)["properties"][field]["oneOf"]}


def test_every_fallback_reason_the_code_can_emit_is_published() -> None:
    """Seven codes, from eight ``Fallback`` constants: two notes share
    `index-project-mismatch` because a client's next action is the same for
    both, while a person reading the transcript needs to know whether an id
    changed under the index or was never recorded.
    """
    from theurian.mcp import search
    from theurian.mcp.search import Fallback

    emitted = {value.reason for value in vars(search).values() if isinstance(value, Fallback)}

    assert emitted, "no Fallback constants found; this test would pass vacuously"
    assert _published_consts(RETRIEVAL_METADATA, "fallbackReason") == emitted | {None}


def test_every_retrieval_mode_the_code_can_emit_is_published() -> None:
    """``substring`` is not a ``RetrievalMode`` member.

    It is written inline by the unranked answer path, where it names a scan of
    the canonical store rather than a retriever -- the same word that names the
    trigram retriever in a hit's ``foundBy``. Both are on the wire, so the
    schema describes each precisely instead of renaming either.
    """
    from theurian.domain.ranking import RetrievalMode

    assert _published_consts(RETRIEVAL_METADATA, "mode") == {
        mode.value for mode in RetrievalMode
    } | {"substring"}


def test_every_retriever_a_hit_can_name_is_published() -> None:
    from theurian.domain.ranking import DENSE, LEXICAL, SUBSTRING

    published = set(_load(RETRIEVAL_RESULT)["properties"]["foundBy"]["items"]["enum"])

    assert published == {LEXICAL, SUBSTRING, DENSE}


def test_only_surfaceable_statuses_are_published() -> None:
    """Retired states are reachable through no flag, so they cannot appear on a
    result -- and a schema that allowed them would invite a client to write a
    branch for a value it will never receive, or worse, to expect one."""
    from theurian.domain.enums import SURFACEABLE_STATUSES

    published = set(_load(RETRIEVAL_RESULT)["properties"]["status"]["enum"])

    assert published == {status.value for status in SURFACEABLE_STATUSES}
    assert published.isdisjoint({"deprecated", "superseded", "rejected"})


#: Every key of the ``retrieval`` block, frozen deliberately.
#:
#: This round closed a CRITICAL in which retrieval metadata leaked withheld
#: content: `usedTokens` and a since-removed `withheldSuperseded` let a caller
#: reconstruct a retired credential in 257 ordinary search calls. Adding a field
#: here must therefore be a decision somebody takes, not a diff somebody misses,
#: so the set is written out and this test is the place to argue for a change.
#:
#: The bar a new key has to clear: it describes what was *returned*, the index,
#: or the caller's own parameters -- never what a query matched and the caller
#: may not read.
PUBLISHED_RETRIEVAL_KEYS = frozenset(
    {
        "mode",
        "indexed",
        "stale",
        "staleAgainst",
        "indexesUnapproved",
        "indexBuildId",
        "embeddingModel",
        "fallbackReason",
        "snapshotId",
        "usedTokens",
        "droppedForBudget",
        "note",
    }
)


def test_the_retrieval_block_publishes_exactly_the_fields_that_are_emitted() -> None:
    schema = _load(RETRIEVAL_METADATA)

    assert set(schema["properties"]) == PUBLISHED_RETRIEVAL_KEYS
    assert set(schema["required"]) == PUBLISHED_RETRIEVAL_KEYS, (
        "one shape on both answer paths: an inapplicable key carries null rather "
        "than being absent, so a client never branches on key presence"
    )
    assert schema["additionalProperties"] is False, (
        "this is what makes a successor to `withheldSuperseded` a schema "
        "violation rather than an addition someone has to notice in review"
    )
