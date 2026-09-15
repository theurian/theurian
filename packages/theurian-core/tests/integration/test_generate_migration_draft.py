"""``generateMigrationDraft``'s service entry (ADR-0032 decision 3).

``ProposalService.draft_from_document`` lands a caller-supplied migration document
as a proposal through the same identifier minting, containment and validator the
content path uses. These tests drive the service directly -- the tool that wires
it is cluster 2's -- so a defect is located in the packaging, not in the wire.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping
from pathlib import Path

import pytest
import yaml
from fakes.clock import FrozenClock
from fakes.ids import SeededIdGenerator

from theurian.application.project_service import ProjectPaths, initialize_project
from theurian.application.proposal_service import (
    DraftedMigration,
    MigrationDocumentValidator,
    ProposalError,
    ProposalService,
)
from theurian.cli.migration_pipeline import rehearse_migration_set
from theurian.domain.errors import MigrationError
from theurian.domain.identifiers import AgentId, ItemId, MigrationId, ProjectId, RevisionId, TaskId
from theurian.domain.migration import Migration, OperationKind, current_revision_in
from theurian.domain.project import DEFAULT_KNOWLEDGE_DIRECTORY
from theurian.domain.proposal import Evidence, is_migration_file_name
from theurian.infrastructure.filesystem.migration_loader import (
    load_migrations,
    validate_migration_document,
)

SCHEMAS = Path(__file__).resolve().parents[4] / "schemas"

EVIDENCE = Evidence(
    agent_id=AgentId("claude-code"),
    task_id=TaskId("task-7"),
    model="claude-opus-5",
    reasoning="The alias review settled that the item should be deprecated.",
    anchors=(),
)


@pytest.fixture
def paths(tmp_path: Path) -> Iterator[ProjectPaths]:
    root = tmp_path / "demo"
    root.mkdir()
    project = ProjectPaths(root=root, knowledge_dir=root / DEFAULT_KNOWLEDGE_DIRECTORY)
    initialize_project(project)
    yield project


def _service(
    paths: ProjectPaths, *, validate: MigrationDocumentValidator | None = None
) -> ProposalService:
    def current_revision(item_id: ItemId) -> RevisionId | None:
        loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)
        return current_revision_in(loaded.migration_set, item_id)

    def landed_migration(migration_id: MigrationId) -> Migration | None:
        loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)
        return loaded.migration_set.get(migration_id)

    def landed_migrations() -> Collection[Migration]:
        loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)
        return loaded.migration_set

    return ProposalService(
        paths=paths,
        project_id=ProjectId("demo"),
        clock=FrozenClock(),
        ids=SeededIdGenerator(),
        validate=validate or (lambda document: validate_migration_document(document, SCHEMAS)),
        current_revision=current_revision,
        landed_migration=landed_migration,
        landed_migrations=landed_migrations,
        rehearse=lambda candidate: rehearse_migration_set(candidate, clock=FrozenClock()),
    )


def _document(*operations: Mapping[str, object], **top: object) -> dict[str, object]:
    """A caller migration document, without the identity the service stamps.

    No ``apiVersion``/``id``/``createdAt``: those are ``draft_from_document``'s to
    mint. The caller supplies ``author``, ``operations`` and (optionally)
    ``description``.
    """
    document: dict[str, object] = {
        "author": "platform-team@example.com",
        "description": "Deprecate the retry policy item after review",
        "operations": list(operations),
    }
    document.update(top)
    return document


def _deprecate(item_id: str = "architecture.retry-policy") -> dict[str, object]:
    return {"op": "deprecateItem", "itemId": item_id}


def _parsed(path: Path) -> Mapping[str, object]:
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _tree(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def _no_proposal_written(paths: ProjectPaths) -> bool:
    """True when a refusal wrote no proposal directory under either location.

    ``initialize_project`` seeds ``.theurian/proposals/`` with a ``.gitkeep``, so
    "nothing was written" is "no *directory* was created" -- a proposal is a
    minted-id subdirectory.
    """
    for parent in (paths.proposals, paths.proposals_local):
        if parent.exists() and any(entry.is_dir() for entry in parent.iterdir()):
            return False
    return True


def test_an_admitted_operation_lands_a_proposal(paths: ProjectPaths) -> None:
    drafted = _service(paths).draft_from_document(_document(_deprecate()), evidence=EVIDENCE)

    assert isinstance(drafted, DraftedMigration)
    assert drafted.operations == (OperationKind.DEPRECATE_ITEM,)
    assert drafted.directory == paths.proposals / drafted.proposal_id.value
    assert drafted.migration_file.parent == drafted.directory
    assert is_migration_file_name(drafted.migration_file.name)
    assert drafted.evidence_file == drafted.directory / "evidence.json"

    # Only the migration and the evidence record -- no body: an operations
    # migration names no contentFile (ADR-0032 decision 3).
    assert _tree(drafted.directory) == {
        drafted.migration_file.name,
        "evidence.json",
    }


def test_the_landed_migration_is_schema_valid_and_carries_the_operation(
    paths: ProjectPaths,
) -> None:
    drafted = _service(paths).draft_from_document(_document(_deprecate()), evidence=EVIDENCE)

    document = _parsed(drafted.migration_file)
    # It validates without raising, so a proposal drafted here is one accept's
    # rehearsal can re-validate (ADR-0032 decision 3).
    validate_migration_document(document, SCHEMAS)
    assert document["apiVersion"] == "theurian.dev/v1"
    assert document["id"] == drafted.migration_id.value
    operations = document["operations"]
    assert isinstance(operations, list)
    assert [op["op"] for op in operations] == ["deprecateItem"]


def test_the_service_mints_the_migration_id_over_a_caller_supplied_one(paths: ProjectPaths) -> None:
    # The caller's `id` is not authority: the service stamps its own, so a caller
    # cannot choose an id that collides with a landed migration.
    forged = "0000000000000000000000009"
    drafted = _service(paths).draft_from_document(
        _document(_deprecate(), id=forged), evidence=EVIDENCE
    )

    assert drafted.migration_id.value != forged
    assert _parsed(drafted.migration_file)["id"] == drafted.migration_id.value


def test_the_evidence_record_names_the_minted_migration_and_the_items(paths: ProjectPaths) -> None:
    drafted = _service(paths).draft_from_document(
        _document(_deprecate("architecture.retry-policy"), _deprecate("architecture.auth-policy")),
        evidence=EVIDENCE,
    )

    record = _parsed(drafted.evidence_file)
    assert record["migrationId"] == drafted.migration_id.value
    assert record["itemId"] == ["architecture.auth-policy", "architecture.retry-policy"]
    assert record["agentId"] == "claude-code"
    assert record["reasoning"] == EVIDENCE.reasoning


def test_it_reaches_the_injected_validator(paths: ProjectPaths) -> None:
    # ADR-0003: the entry validates through the injected callable, the same one
    # `draft` calls and the accept-time rehearsal re-runs -- not a direct loader
    # import that would be a second validator.
    seen: list[Mapping[str, object]] = []

    def spy(document: Mapping[str, object]) -> None:
        seen.append(document)
        validate_migration_document(document, SCHEMAS)

    drafted = _service(paths, validate=spy).draft_from_document(
        _document(_deprecate()), evidence=EVIDENCE
    )

    assert len(seen) == 1
    # The document the validator saw is the stamped one -- the minted id, not a
    # caller value -- so the generator and the accept-time rehearsal validate the
    # same bytes.
    assert seen[0]["id"] == drafted.migration_id.value


def test_a_document_the_schema_rejects_is_refused_and_nothing_is_written(
    paths: ProjectPaths,
) -> None:
    bad = _document({"op": "deprecateItem", "itemId": "Not A Valid Item Id"})

    with pytest.raises(MigrationError):
        _service(paths).draft_from_document(bad, evidence=EVIDENCE)

    assert _no_proposal_written(paths)


@pytest.mark.parametrize(
    ("op", "extra"),
    [
        ("createItem", {"kind": "architecture", "namespace": "architecture", "owner": "x"}),
        ("upsertRevision", {}),
    ],
)
def test_a_content_operation_is_refused_to_the_content_path(
    paths: ProjectPaths, op: str, extra: Mapping[str, object]
) -> None:
    document = _document({"op": op, "itemId": "architecture.retry-policy", **extra})

    with pytest.raises(ProposalError) as raised:
        _service(paths).draft_from_document(document, evidence=EVIDENCE)

    assert op in str(raised.value)
    assert "knowledge.proposeChange" in raised.value.remedy
    assert _no_proposal_written(paths), "a refused content operation wrote a proposal"


@pytest.mark.parametrize(
    ("op", "extra"),
    [
        ("changeSensitivity", {"sensitivity": "confidential", "reason": "reclassify"}),
        ("restoreItem", {}),
    ],
)
def test_a_read_control_operation_is_refused_to_the_cli(
    paths: ProjectPaths, op: str, extra: Mapping[str, object]
) -> None:
    document = _document({"op": op, "itemId": "architecture.retry-policy", **extra})

    with pytest.raises(ProposalError) as raised:
        _service(paths).draft_from_document(document, evidence=EVIDENCE)

    assert op in str(raised.value)
    remedy = raised.value.remedy
    assert "theurian migrate" in remedy, f"the remedy does not name the CLI: {remedy}"
    assert op in remedy
    assert _no_proposal_written(paths), "a refused read-control operation wrote a proposal"


def test_a_mixed_document_is_refused_on_its_first_unadmitted_operation(paths: ProjectPaths) -> None:
    # One admitted op does not launder a refused one: the whole document is
    # refused and nothing is written.
    document = _document(
        _deprecate(),
        {"op": "restoreItem", "itemId": "architecture.retry-policy"},
    )

    with pytest.raises(ProposalError) as raised:
        _service(paths).draft_from_document(document, evidence=EVIDENCE)

    assert "restoreItem" in str(raised.value)
    assert _no_proposal_written(paths)


def test_empty_evidence_is_refused_at_generation(paths: ProjectPaths) -> None:
    # ADR-0013 point 5 on this generation path too. `Evidence` cannot be built
    # empty, so the property is driven with an object whose reasoning was blanked
    # after construction -- the "built by another route" case require_evidence's
    # docstring names.
    hollow = object.__new__(Evidence)
    object.__setattr__(hollow, "agent_id", AgentId("claude-code"))
    object.__setattr__(hollow, "task_id", TaskId("task-7"))
    object.__setattr__(hollow, "model", "claude-opus-5")
    object.__setattr__(hollow, "reasoning", "   ")
    object.__setattr__(hollow, "anchors", ())

    from theurian.domain.errors import InvariantViolationError

    with pytest.raises(InvariantViolationError):
        _service(paths).draft_from_document(_document(_deprecate()), evidence=hollow)

    assert _no_proposal_written(paths)
