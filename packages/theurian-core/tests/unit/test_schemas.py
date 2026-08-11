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
import re
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


# -- Published patterns against the domain that produces the values ---------
#
# A published ``pattern`` is ECMA-262, and the clients that read these schemas
# -- ECMA-262 engines, and RE2 wherever a schema is compiled for one -- mean
# *end of input* by ``$``. Python's ``re`` is the outlier: its ``$`` also
# matches immediately before a trailing newline. ``Draft202012Validator``
# compiles ``pattern`` with ``re``, so putting a candidate through it answers a
# question no consumer of these schemas asks, and answers it more loosely than
# the contract does.
#
# That gap is not theoretical, and this file is where it was hiding. ``80f94b6``
# anchored the domain constructors ``\A...\Z`` because ``demo\n`` satisfied a
# ``$``-anchored slug and ``project.list`` published two entries a reader sees as
# one id. This comparison claims *exact* agreement between the published pattern
# and the constructor, and it stayed green straight through that fix -- a
# Python-backed validator agreed with the unfixed domain, and would have gone on
# agreeing with a ``$``-anchored one indefinitely. Nothing in the candidate list
# contained a newline, so the disagreement was never put to it.
#
# Hence: the anchors are rewritten to Python's end-of-input forms before the
# subschema reaches the validator. Everything else in the subschema -- ``type``,
# ``maxLength``, which carries the 200-character bound -- is still evaluated by
# the real validator, and that is the reason for translating the pattern in
# place rather than matching it by hand.
#
# Do not simplify this back to ``Draft202012Validator(subschema)``. It will look
# like a pointless indirection, it will pass every candidate that has no
# whitespace in it, and it reinstates the blind spot in full.


def _end_of_input_anchored(pattern: str) -> str:
    r"""Rewrite an ECMA-262 pattern's ``^``/``$`` as Python's ``\A``/``\Z``.

    A scan rather than :meth:`str.replace`, because both characters are ordinary
    members of a character class and both occur as one here: ``contentType``'s
    published pattern contains ``[a-z0-9!#$&^_.+-]``, which a blind replacement
    turns into a class Python refuses to compile. Escape pairs are copied
    through for the same reason, so a later ``\$`` keeps meaning a literal
    dollar rather than an anchor.

    ``^`` becomes ``\A`` although Python's non-multiline ``^`` already means
    start-of-input -- the pair is what makes the intent legible, and the
    translation is checked in both positions below.
    """
    out: list[str] = []
    in_class = False
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            if index + 1 >= len(pattern):
                raise ValueError(f"pattern ends in a dangling escape: {pattern!r}")
            out.append(pattern[index : index + 2])
            index += 2
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == "^":
            char = r"\A"
        elif char == "$":
            char = r"\Z"
        out.append(char)
        index += 1
    if in_class:
        raise ValueError(f"pattern has an unterminated character class: {pattern!r}")
    return "".join(out)


def _admits(subschema: dict[str, Any], candidate: str) -> bool:
    """Whether the *published* schema admits the candidate.

    Deliberately not ``Draft202012Validator(subschema).is_valid(candidate)``:
    see the note above this function for why that validator is a lax reader of
    an ECMA-262 pattern rather than the contract's own.
    """
    assert "pattern" in subschema, (
        "this comparison is about a published pattern; a subschema without one "
        "would agree with the domain only by accident"
    )
    published = {**subschema, "pattern": _end_of_input_anchored(subschema["pattern"])}
    return Draft202012Validator(published).is_valid(candidate)


def _constructs(build: Callable[[str], object], candidate: str) -> bool:
    from theurian.domain.errors import TheurianError

    try:
        build(candidate)
    except TheurianError:
        return False
    return True


@pytest.mark.parametrize(
    ("published", "python_equivalent"),
    [
        ("^abc$", r"\Aabc\Z"),
        # `contentType`'s own pattern: `$` and `^` inside a class are literals,
        # and this is the case `str.replace` corrupts.
        ("^[a-z0-9!#$&^_.+-]+$", r"\A[a-z0-9!#$&^_.+-]+\Z"),
        # An escaped dollar is a dollar, not an anchor.
        (r"^\$[0-9]+$", r"\A\$[0-9]+\Z"),
        # Anchors are not only at the ends of the string.
        ("^a$|^b$", r"\Aa\Z|\Ab\Z"),
        # A class closed by an escaped bracket stays open until the real one.
        (r"^[a\]$]+$", r"\A[a\]$]+\Z"),
    ],
)
def test_the_anchor_translation_moves_anchors_and_leaves_every_other_character(
    published: str, python_equivalent: str
) -> None:
    """The oracle's own correctness, pinned separately from what it measures.

    Every verdict in the agreement test below is only as good as this rewrite,
    and a rewrite that quietly mangles a character class would make the schema
    look stricter than it is -- a disagreement invented by the test rather than
    found in the contract.
    """
    assert _end_of_input_anchored(published) == python_equivalent


def test_a_python_validator_reads_a_published_pattern_more_loosely_than_a_client_does() -> None:
    """The whole reason ``_admits`` does not hand ``pattern`` to the validator.

    The published ``itemId`` pattern is the domain's own, transcribed. Under
    ECMA-262 -- what every client compiles it as -- it refuses a trailing
    newline, exactly as ``ItemId`` does. Under Python's ``re`` it admits one. So
    a Python-backed validator and the domain can be in perfect agreement while
    the *published* contract and the domain are not, which is the state this
    file was in for a milestone.

    If this test is ever deleted, ``_admits`` may be simplified back and nothing
    will object.
    """
    published = _load(RETRIEVAL_RESULT)["properties"]["itemId"]["pattern"]

    assert re.search(published, "architecture.auth-policy\n") is not None
    assert re.search(_end_of_input_anchored(published), "architecture.auth-policy\n") is None


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
    # The case ``80f94b6`` fixed, and the only kind of case that separates a
    # Python-read pattern from a published one. Keep it.
    "architecture.auth-policy\n",
    "",
    "a" * 200,
    "a" * 201,
)

#: No trailing-newline case here, and that is a finding rather than an
#: oversight: ``MediaType`` is still ``$``-anchored, so it and the published
#: pattern genuinely disagree about one. The disagreement is carried by
#: ``test_a_media_type_with_a_trailing_newline_is_refused_like_the_identifiers``
#: as a strict xfail, so it is reported every run instead of sitting here as a
#: row that looks like a pass.
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
    # A ULID that is valid up to the newline. Same case as the item id above.
    "01K1DEFABC1234567890ABCDEF\n",
    "",
)

#: ``namespace``, ``tenantId`` and ``aclGroup`` are the three ``Scope``
#: components that are free-form text (``project_id`` is already a slug and
#: ``sensitivity``/``status`` are enums), so all three share the C0-and-DEL
#: rejection ``_reject_control_characters`` enforces and the published schema
#: was missing until this pass -- a migration whose ``namespace`` carried
#: ``\x1f`` validated and applied, then ``KnowledgeItem.scope`` raised on the
#: very value the schema had just accepted. ``"a\x1fb"`` is not an arbitrary
#: control character: it is the unit separator ``Scope.key`` joins its
#: components with, and the exact pair ``test_scope_isolation.py`` measured
#: colliding before construction refused it. ``"a\nb"`` is the same rejection
#: through a byte a human is more likely to paste by accident.
_NAMESPACE_CANDIDATES = (
    "architecture",
    "architecture.auth-policy",
    "",
    "a" * 200,
    "a" * 201,
    "a\x1fb",
    "a\nb",
)

_TENANT_ID_CANDIDATES = (
    "local",
    "a",
    "",
    "a" * 128,
    "a" * 129,
    "a\x1fb",
    "a\nb",
)

_ACL_GROUP_CANDIDATES = (
    "default",
    "a",
    "",
    "a" * 128,
    "a" * 129,
    "a\x1fb",
    "a\nb",
)

#: Where each field's published pattern lives -- every location it is
#: transcribed to, not just one. The first three are single properties of
#: ``retrieval-result``; the scope components are nested under
#: ``revisionMetadata`` in the migration schema instead. ``namespace`` has a
#: second, independent occurrence: ``opCreateItem`` declares its own
#: ``namespace`` property rather than ``$ref``-ing ``revisionMetadata``, and it
#: reaches the identical domain check -- ``migration_engine._create_item``
#: builds a ``KnowledgeItem`` from it, and ``KnowledgeItem.scope`` constructs
#: a ``Scope`` from that same string -- so a pattern fixed on one and missed on
#: the other is the same published-vs-domain gap this table exists to catch.
#: `grep -n '"namespace"\|"tenantId"\|"aclGroup"'` against the migration
#: schema is the full population: these four property definitions, plus two
#: `required`-array mentions that name a property rather than defining one.
#: `tenantId` and `aclGroup` have no second occurrence -- neither is declared
#: outside `revisionMetadata`.
_PUBLISHED_PATTERN_LOCATIONS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "itemId": ((RETRIEVAL_RESULT, ("properties", "itemId")),),
    "contentType": ((RETRIEVAL_RESULT, ("properties", "contentType")),),
    "revisionId": ((RETRIEVAL_RESULT, ("properties", "revisionId")),),
    "namespace": (
        (
            "migrations/migration.schema.json",
            ("$defs", "revisionMetadata", "properties", "namespace"),
        ),
        (
            "migrations/migration.schema.json",
            ("$defs", "opCreateItem", "properties", "namespace"),
        ),
    ),
    "tenantId": (
        (
            "migrations/migration.schema.json",
            ("$defs", "revisionMetadata", "properties", "tenantId"),
        ),
    ),
    "aclGroup": (
        (
            "migrations/migration.schema.json",
            ("$defs", "revisionMetadata", "properties", "aclGroup"),
        ),
    ),
}


@pytest.mark.parametrize(
    ("field", "candidates"),
    [
        ("itemId", _IDENTIFIER_CANDIDATES),
        ("contentType", _MEDIA_TYPE_CANDIDATES),
        ("revisionId", _ULID_CANDIDATES),
        ("namespace", _NAMESPACE_CANDIDATES),
        ("tenantId", _TENANT_ID_CANDIDATES),
        ("aclGroup", _ACL_GROUP_CANDIDATES),
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
    with an ambiguous character, a namespace carrying the byte ``Scope.key``
    joins components with. Comparing the two decisions is total over the cases
    listed and costs no I/O.

    "Admits" means what a client's regex engine admits, not what a Python one
    does; ``_admits`` and the note above it carry that, and it is the difference
    between this test measuring the contract and measuring a proxy for it.
    """
    from theurian.domain.enums import KnowledgeStatus, Sensitivity
    from theurian.domain.identifiers import ItemId, ProjectId, RevisionId
    from theurian.domain.values import AclGroup, MediaType, Scope, TenantId

    def _namespace_builder(value: str) -> object:
        # ``namespace`` has no dedicated value type -- ``Scope.__post_init__``
        # is where the domain enforces its shape -- so this varies only the
        # field under test, holding every other component at a value that is
        # itself always valid.
        return Scope(
            project_id=ProjectId("backend-service"),
            tenant_id=TenantId(),
            sensitivity=Sensitivity.INTERNAL,
            acl_group=AclGroup(),
            namespace=value,
            status=KnowledgeStatus.APPROVED,
        )

    builders: dict[str, Callable[[str], object]] = {
        "itemId": ItemId,
        "contentType": MediaType,
        "revisionId": RevisionId,
        "namespace": _namespace_builder,
        "tenantId": TenantId,
        "aclGroup": AclGroup,
    }
    for relative, pointer in _PUBLISHED_PATTERN_LOCATIONS[field]:
        subschema = _at(relative, pointer)
        for candidate in candidates:
            assert _admits(subschema, candidate) == _constructs(builders[field], candidate), (
                f"{field} at {relative}#{'/'.join(pointer)}: "
                f"schema and domain disagree about {candidate!r}"
            )


_PROJECT_ID_CANDIDATES = (
    "backend-service",
    "backend",
    "a1",
    "Backend",
    "backend_service",
    "backend service",
    "backend.service",
    "-leading",
    "trailing-",
    # The registry key that reached a real `project.list` array as a second
    # entry a reader could not distinguish from the first.
    "backend-service\n",
    "",
    "a" * 200,
    "a" * 201,
)


#: Every published face of ``ProjectId``. The pattern is hand-copied into each
#: of these files, so the interesting failure is one of them moving alone --
#: a client validating a `project.list` entry against one and a tool argument
#: against another must reach the same verdict about the same id.
_PROJECT_ID_FACES = (
    ("config/project-config.schema.json", ("properties", "projectId")),
    ("mcp/tool-context.schema.json", ("properties", "projectId")),
    ("mcp/knowledge-search-response.schema.json", ("properties", "projectId")),
    (
        "mcp/project-list-response.schema.json",
        ("properties", "projects", "items", "properties", "projectId"),
    ),
)


def _at(relative: str, pointer: tuple[str, ...]) -> dict[str, Any]:
    node: Any = _load(relative)
    for key in pointer:
        node = node[key]
    resolved: dict[str, Any] = node
    return resolved


@pytest.mark.parametrize(("relative", "pointer"), _PROJECT_ID_FACES, ids=str)
def test_every_published_project_id_pattern_admits_exactly_what_projectid_constructs(
    relative: str, pointer: tuple[str, ...]
) -> None:
    r"""The claim ``project-list-response``'s own description says no
    Python-backed test can make.

    ``projectId`` is a *key* of the registry file, so a published pattern is the
    only statement a JavaScript or Go client has about what an id may look like.
    ``project-list-response`` carried a trailing ``\n?`` for a milestone, and
    correctly: while ``ProjectId`` was ``$``-anchored, ``ProjectId("demo\n")``
    constructed and ``project.list`` really did publish it, so a bare slug
    pattern would have rejected Theurian's own output in exactly the ECMA-262 and
    RE2 clients the schema exists for. ``80f94b6`` removed the input, and the
    concession then pointed the other way -- admitting, under those same
    dialects, a value the constructor refuses.

    Neither direction could be defended by a test, because ``jsonschema`` reads
    ``pattern`` with Python's ``re`` and ``demo\n`` satisfies the field under
    both forms. Under the end-of-input oracle it does not, so this goes red if
    the ``\n?`` returns to any of the four, or if the constructor is loosened
    under them.
    """
    from theurian.domain.identifiers import ProjectId

    subschema = _at(relative, pointer)

    for candidate in _PROJECT_ID_CANDIDATES:
        assert _admits(subschema, candidate) == _constructs(ProjectId, candidate), (
            f"{relative}: schema and domain disagree about {candidate!r}"
        )


def test_the_case_the_oracle_exists_for_is_still_in_the_candidate_lists() -> None:
    """The comparison above is total only over the cases listed, and for a
    milestone none of them contained whitespace.

    That is precisely why anchoring the constructors turned nothing red. A
    reader trimming a candidate list has no way to tell that one entry is the
    only reason the agreement is being tested at all; this says so, and fails if
    it goes.
    """
    assert any("\n" in candidate for candidate in _IDENTIFIER_CANDIDATES), (
        "the item-id list lost its trailing-newline case"
    )
    assert any("\n" in candidate for candidate in _ULID_CANDIDATES), (
        "the ULID list lost its trailing-newline case"
    )
    assert any("\n" in candidate for candidate in _PROJECT_ID_CANDIDATES), (
        "the project-id list lost its trailing-newline case"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "MediaType is still `$`-anchored in theurian.domain.values, so it "
        "constructs from a trailing newline that the published pattern refuses. "
        "Open finding, recorded in 80f94b6 and filed for Milestone 6 as #28."
    ),
)
def test_a_media_type_with_a_trailing_newline_is_refused_like_the_identifiers() -> None:
    r"""The third row of the agreement table, kept visible rather than omitted.

    ``itemId`` and ``revisionId`` agree about a trailing newline because
    ``80f94b6`` anchored their constructors ``\A...\Z``. ``MediaType`` was left
    out of that commit and reported instead: ``_MEDIA_TYPE_PATTERN`` still ends
    in ``$``, so ``MediaType('application/json\n')`` constructs -- and then
    compares unequal to every member of ``_STRUCTURED_MEDIA_TYPES``, so a
    structured payload is silently normalised as prose, which is the one thing
    ADR-0010 says must not happen.

    A strict xfail rather than a candidate quietly left out of
    ``_MEDIA_TYPE_CANDIDATES``: this reports as an expected failure every run,
    and turns into a *real* failure the moment the constructor is anchored -- at
    which point the case belongs in the candidate list with the others and this
    test should go.
    """
    from theurian.domain.values import MediaType

    subschema = _load(RETRIEVAL_RESULT)["properties"]["contentType"]

    assert _admits(subschema, "application/json\n") == _constructs(
        MediaType, "application/json\n"
    ), "schema and domain disagree about 'application/json\\n'"


# -- Project config schema --------------------------------------------------


def test_the_raptor_forest_is_declared_off_by_default() -> None:
    """ADR-0008 decision 10, one of the two places it names.

    "A capability whose acceptance tests are owed and whose build cost is
    unmeasured ships opt-in, so that turning it on is somebody's decision and
    not the side effect of an upgrade." The schema declared `default: true`
    from the day the block was written, which was not a decision anyone took --
    nothing in `src/` reads it, or reads `.theurian/config.yaml` at all, so the
    value has never taken effect anywhere and had never been examined either.

    Pinned here rather than left to the loader that will one day read it,
    because a default is a published claim the moment it is in a schema a third
    party validates against. The other place is
    `examples/sample-project/.theurian/config.yaml`, which sets it explicitly
    and is what a reader copies; `tests/unit/test_examples.py` holds that one.
    """
    raptor = _load("config/project-config.schema.json")["properties"]["raptor"]

    assert raptor["properties"]["enabled"]["default"] is False


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
