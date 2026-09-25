"""The gated OKF import, end to end onto a real disk (ADR-0037 decisions 5, 6).

Like ``test_candidate_generation_on_disk.py``, the subject is what actually
lands: ``trustLevel: inferred`` on :class:`ImportedConcept` is a type
declaration, and on the *written migration* it is a mapping that could have
dropped it. The whole stack is real -- a real ``.theurian/`` project tree and
``ProposalService`` writing proposal directories -- and every bundle is a real
directory under ``tmp_path``.

Scoped to the pipeline's own acceptance criteria: containment refuses a
reference without aborting the bundle, INV-8's synthesized anchor, the trust
ceiling, relation aggregation into a second proposal, and the operation cap.
The adversarial pins over the containment machinery itself (a symlinked
bundle parent, a symlink escape reached through several hops) are a later
assignment's, per ``security/paths.py``'s own battery.
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

import pytest
import yaml
from fakes.clock import FrozenClock
from fakes.ids import SeededIdGenerator

from theurian.application.draft_only_proposals import DraftOnlyProposals
from theurian.application.okf_import import (
    ImportRefusal,
    OkfImportError,
    OkfImportRequest,
    OkfImportService,
)
from theurian.application.project_service import ProjectPaths, initialize_project
from theurian.application.proposal_service import ProposalService
from theurian.cli.migration_pipeline import rehearse_migration_set
from theurian.domain.identifiers import AgentId, ItemId, MigrationId, ProjectId, RevisionId, TaskId
from theurian.domain.migration import Migration, current_revision_in
from theurian.domain.project import DEFAULT_KNOWLEDGE_DIRECTORY
from theurian.domain.proposal import Evidence
from theurian.infrastructure.filesystem.migration_loader import (
    load_migrations,
    validate_migration_document,
)

pytestmark = pytest.mark.integration

SCHEMAS = Path(__file__).resolve().parents[4] / "schemas"

EVIDENCE = Evidence(
    agent_id=AgentId("claude-code"),
    task_id=TaskId("task-okf"),
    model="claude-opus-5",
    reasoning="Importing an OKF bundle a teammate shared.",
    anchors=(),
)

_EXPORTED_CONCEPT = """---
type: architecture
title: Authentication and authorization policy
status: stable
generated:
  by: theurian/0.4.0
  at: "2026-09-24T12:00:00+00:00"
theurian_export_version: 1
theurian_item_id: architecture.auth-policy
theurian_revision_id: 01K1REV00101234567890ABCDE
theurian_namespace: backend
theurian_owner: platform-team
theurian_trust_level: reviewed
theurian_sensitivity: internal
theurian_content_type: text/markdown
---

# Authentication and authorization policy

Body prose.
"""

_VANILLA_CONCEPT = """---
type: decision
title: A vanilla concept
status: stable
---

Body prose with no theurian_* keys at all.
"""


@pytest.fixture
def paths(tmp_path: Path) -> ProjectPaths:
    root = tmp_path / "demo"
    root.mkdir()
    project = ProjectPaths(root=root, knowledge_dir=root / DEFAULT_KNOWLEDGE_DIRECTORY)
    initialize_project(project)
    return project


def _proposal_service(paths: ProjectPaths) -> ProposalService:
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
        validate=lambda document: validate_migration_document(document, SCHEMAS),
        current_revision=current_revision,
        landed_migration=landed_migration,
        landed_migrations=landed_migrations,
        rehearse=lambda candidate: rehearse_migration_set(candidate, clock=FrozenClock()),
    )


def _service(paths: ProjectPaths) -> OkfImportService:
    return OkfImportService(drafts=DraftOnlyProposals(_proposal_service(paths)))


def _write(bundle: Path, relative: str, text: str) -> Path:
    target = bundle / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def _request(bundle: Path, **overrides: object) -> OkfImportRequest:
    fields: dict[str, object] = {
        "root": bundle,
        "owner": "platform-team",
        "author": "dana@example.com",
        "evidence": EVIDENCE,
    }
    fields.update(overrides)
    return OkfImportRequest(**fields)  # type: ignore[arg-type]


def _migration_text(directory: Path) -> str:
    migrations = list(directory.glob("*.yaml"))
    assert len(migrations) == 1, migrations
    return migrations[0].read_text(encoding="utf-8")


def test_a_theurian_exported_and_a_vanilla_concept_both_draft_at_inferred_trust(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    bundle = tmp_path / "bundle"
    _write(bundle, "auth-policy.md", _EXPORTED_CONCEPT)
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)

    result = _service(paths).import_bundle(_request(bundle))

    assert not result.refusals
    assert {p.item_id.value for p in result.concepts_admitted} == {
        "architecture.auth-policy",
        "vanilla",
    }
    for proposal in result.concepts_admitted:
        document = yaml.safe_load(_migration_text(proposal.proposal.directory))
        metadata = document["operations"][1]["metadata"]
        assert metadata["trustLevel"] == "inferred"


def test_a_bad_body_file_reference_is_refused_without_the_resolved_path_or_the_bundle(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    bundle = tmp_path / "bundle"
    concept = _EXPORTED_CONCEPT.replace(
        "theurian_content_type: text/markdown",
        "theurian_content_type: application/json\ntheurian_body_file: ../../../../etc/passwd",
    )
    _write(bundle, "auth-policy.md", concept)
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)

    result = _service(paths).import_bundle(_request(bundle))

    assert len(result.refusals) == 1
    refusal = result.refusals[0]
    assert refusal.key == "theurian_body_file"
    assert refusal.literal == "../../../../etc/passwd"
    assert str(tmp_path) not in refusal.literal
    # The unrelated concept still drafts -- one bad reference refuses only itself.
    assert {p.item_id.value for p in result.concepts_admitted} == {"vanilla"}


def test_theurian_relations_land_as_one_additional_proposal(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    bundle = tmp_path / "bundle"
    concept = _EXPORTED_CONCEPT.replace(
        "theurian_content_type: text/markdown",
        "theurian_content_type: text/markdown\n"
        "theurian_relations:\n"
        "  - type: related_to\n"
        "    target: architecture.session-policy\n"
        "    note: shares a session store\n",
    )
    _write(bundle, "auth-policy.md", concept)

    result = _service(paths).import_bundle(_request(bundle))

    assert not result.refusals
    assert len(result.concepts_admitted) == 1
    assert result.relations_proposal is not None
    document = yaml.safe_load(result.relations_proposal.migration_file.read_text(encoding="utf-8"))
    assert document["operations"] == [
        {
            "op": "addRelation",
            "sourceItemId": "architecture.auth-policy",
            "relationType": "related_to",
            "targetItemId": "architecture.session-policy",
            "note": "shares a session store",
        }
    ]


def test_a_bundle_with_no_relations_drafts_no_relations_proposal(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    bundle = tmp_path / "bundle"
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)

    result = _service(paths).import_bundle(_request(bundle))

    assert result.relations_proposal is None


def test_a_bare_markdown_link_never_synthesizes_a_relation(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """ADR-0037 decision 5: only a `theurian_relations` key ever becomes `addRelation`."""
    bundle = tmp_path / "bundle"
    concept = _VANILLA_CONCEPT + "\nSee also [another concept](/architecture/other.md).\n"
    _write(bundle, "vanilla.md", concept)

    result = _service(paths).import_bundle(_request(bundle))

    assert result.relations_proposal is None


def test_the_item_filter_admits_only_the_named_concept(tmp_path: Path, paths: ProjectPaths) -> None:
    bundle = tmp_path / "bundle"
    _write(bundle, "auth-policy.md", _EXPORTED_CONCEPT)
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)

    result = _service(paths).import_bundle(_request(bundle, item_filter=frozenset({"vanilla"})))

    assert {p.item_id.value for p in result.concepts_admitted} == {"vanilla"}


def test_the_reserved_names_are_skipped_rather_than_treated_as_concepts(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    bundle = tmp_path / "bundle"
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)
    _write(bundle, "index.md", "# Index\n")
    _write(bundle, "log.md", "# Log\n")
    _write(bundle, "theurian-bundle.md", "---\ntype: Theurian Bundle\n---\n")

    result = _service(paths).import_bundle(_request(bundle))

    assert {p.item_id.value for p in result.concepts_admitted} == {"vanilla"}
    assert not result.refusals


def test_too_many_admitted_operations_refuses_the_whole_import_before_drafting(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    bundle = tmp_path / "bundle"
    for index in range(126):
        _write(
            bundle,
            f"concept-{index}.md",
            f"---\ntype: decision\ntitle: Concept {index}\nstatus: stable\n---\n\nbody\n",
        )

    with pytest.raises(OkfImportError) as excinfo:
        _service(paths).import_bundle(_request(bundle))

    assert "--item" in excinfo.value.remedy
    assert not [p for p in paths.proposals.glob("*") if p.is_dir()]


def test_a_malformed_concept_refuses_that_file_and_the_rest_still_drafts(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    bundle = tmp_path / "bundle"
    _write(bundle, "broken.md", "---\ntitle: Missing type and status\n---\nbody\n")
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)

    result = _service(paths).import_bundle(_request(bundle))

    assert {p.item_id.value for p in result.concepts_admitted} == {"vanilla"}
    assert any(isinstance(refusal, ImportRefusal) for refusal in result.refusals)
    assert result.refusals[0].key == "broken.md"
