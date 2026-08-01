"""The published JSON Schemas are the contract between Core and every client.

A malformed schema fails open: every document validates, and the contract stops
meaning anything. These tests check that the schemas themselves are valid, and
that the constraints the design depends on are actually present.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
SCHEMAS = REPO_ROOT / "schemas"

ALL_SCHEMA_PATHS = sorted(SCHEMAS.rglob("*.schema.json"))


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
        "81K1DEFABC1234567890ABCDEF",
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
    schema = _load("knowledge/retrieval-result.schema.json")
    required = set(schema["required"])
    assert {
        "sourceAnchors",
        "snapshotId",
        "indexBuildId",
        "contentClassification",
        "mayContainInstructions",
        "executable",
    } <= required


def test_retrieval_results_require_at_least_one_source_anchor() -> None:
    """A result with no route back to its origin is an unverifiable assertion."""
    schema = _load("knowledge/retrieval-result.schema.json")
    assert schema["properties"]["sourceAnchors"]["minItems"] == 1


# -- CLI contract ----------------------------------------------------------


def test_version_output_matches_its_published_schema() -> None:
    """The first contract the plugin depends on."""
    from theurian.cli.main import _version_payload

    Draft202012Validator(_load("cli/version.schema.json")).validate(_version_payload())
