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

import os
import sys
from collections.abc import Callable, Collection, Iterator, Mapping
from pathlib import Path

import pytest
import yaml
from fakes.clock import FrozenClock
from fakes.ids import SeededIdGenerator

from theurian.application.draft_only_proposals import DraftOnlyProposals
from theurian.application.okf_import import (
    KIND_RELATIONS,
    ImportRefusal,
    OkfImportError,
    OkfImportRequest,
    OkfImportService,
)
from theurian.application.project_service import (
    ProjectPathEscapeError,
    ProjectPaths,
    initialize_project,
)
from theurian.application.proposal_service import (
    DraftedMigration,
    DraftedProposal,
    ProposalRequest,
    ProposalService,
)
from theurian.cli.migration_pipeline import rehearse_migration_set
from theurian.domain.identifiers import AgentId, ItemId, MigrationId, ProjectId, RevisionId, TaskId
from theurian.domain.migration import Migration, current_revision_in
from theurian.domain.project import DEFAULT_KNOWLEDGE_DIRECTORY
from theurian.domain.proposal import Evidence
from theurian.domain.values import ContentHash
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


def _seed_existing_item(paths: ProjectPaths, item_id: str) -> None:
    """Land a real migration so `item_id` is already approved canonical state.

    A re-import of the same id then has no `--expected-revision` to offer,
    so `.draft()`'s own `_check_expected_revision` refuses it -- the shape
    HIGH-2's regression test needs, produced without a second fake service.
    """
    body = "Pre-existing body.\n"
    relative_body = f"{item_id}.01K0000000000000000000REV1.md"
    body_file = paths.knowledge / relative_body
    body_file.parent.mkdir(parents=True, exist_ok=True)
    body_file.write_text(body, encoding="utf-8")
    document = {
        "apiVersion": "theurian.dev/v1",
        "id": "01K0000000000000000000EXST",
        "createdAt": "2026-01-01T00:00:00+00:00",
        "author": "seed@example.com",
        "operations": [
            {
                "op": "createItem",
                "itemId": item_id,
                "kind": "architecture",
                "namespace": "",
                "owner": "team",
            },
            {
                "op": "upsertRevision",
                "itemId": item_id,
                "revisionId": "01K0000000000000000000REV1",
                "contentFile": f"../knowledge/{relative_body}",
                "contentSha256": ContentHash.of_text(body).value,
                "metadata": {
                    "title": "Pre-existing",
                    "contentType": "text/markdown",
                    "kind": "architecture",
                    "namespace": "",
                    "status": "approved",
                    "owner": "team",
                    "sourceAnchors": [{"provider": "seed", "sourceUri": "seed:existing"}],
                },
            },
        ],
    }
    migration_file = paths.migrations / "01K0000000000000000000EXST-seed.yaml"
    migration_file.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


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


def test_an_unselected_malformed_concept_is_never_read_and_emits_no_refusal(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """M-a: a concept the `--item` filter excludes costs nothing to reach that
    conclusion -- it is filtered by its path-derived id before anything is
    read, so a malformed concept outside the filter is not even opened.
    """
    bundle = tmp_path / "bundle"
    _write(bundle, "broken.md", "---\ntitle: Missing type and status\n---\n\nbody\n")
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)

    result = _service(paths).import_bundle(_request(bundle, item_filter=frozenset({"vanilla"})))

    assert {p.item_id.value for p in result.concepts_admitted} == {"vanilla"}
    assert not result.refusals, "the excluded, malformed concept must cost nothing at all"


def test_an_item_filter_value_matching_nothing_is_reported(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """M-b: a typo'd or absent `--item` id no longer succeeds silently."""
    bundle = tmp_path / "bundle"
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)

    result = _service(paths).import_bundle(
        _request(bundle, item_filter=frozenset({"vanilla", "does-not-exist"}))
    )

    assert {p.item_id.value for p in result.concepts_admitted} == {"vanilla"}
    [refusal] = result.refusals
    assert refusal.key == "does-not-exist"
    assert refusal.literal == "no concept in this bundle is reachable by this item id"


# ---------------------------------------------------------------------------
# Review-Finding: code-review HIGH -- a divergent theurian_item_id was
# unreachable by either --item spelling, and the refusal literal falsely
# claimed no concept in the bundle carried the requested id.
# ---------------------------------------------------------------------------

_DIVERGENT_ID_CONCEPT = """---
type: decision
title: Divergent id
status: stable
theurian_item_id: chosen.one
---

body
"""


def test_the_path_derived_spelling_admits_a_concept_whose_id_diverges(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """`--item zz-file` (the path `zz-file.md` derives) used to decode the
    concept and then exclude it anyway, because only the *decoded* id
    (`chosen.one`) was checked against the filter at that point.
    """
    bundle = tmp_path / "bundle"
    _write(bundle, "zz-file.md", _DIVERGENT_ID_CONCEPT)

    result = _service(paths).import_bundle(_request(bundle, item_filter=frozenset({"zz-file"})))

    assert {p.item_id.value for p in result.concepts_admitted} == {"chosen.one"}
    assert not result.refusals


def test_the_decoded_spelling_alone_still_cannot_reach_a_divergent_concept(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """`--item chosen.one` never reads `zz-file.md` at all -- its path-derived
    id does not spell the filter, so the early gate skips it before decoding
    could reveal the override. Still reported, but honestly: not that no
    concept in the bundle carries this id, only that this spelling never
    reached one.
    """
    bundle = tmp_path / "bundle"
    _write(bundle, "zz-file.md", _DIVERGENT_ID_CONCEPT)

    result = _service(paths).import_bundle(_request(bundle, item_filter=frozenset({"chosen.one"})))

    assert not result.concepts_admitted
    [refusal] = result.refusals
    assert refusal.key == "chosen.one"
    assert refusal.literal == "no concept in this bundle is reachable by this item id"


def test_both_spellings_together_admit_the_concept_and_neither_reports_absent(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """The path spelling triggers the read; the decoded spelling is recorded
    as matched too, so supplying both leaves neither as a false "unmatched"
    refusal sitting next to a successful admission.
    """
    bundle = tmp_path / "bundle"
    _write(bundle, "zz-file.md", _DIVERGENT_ID_CONCEPT)

    result = _service(paths).import_bundle(
        _request(bundle, item_filter=frozenset({"zz-file", "chosen.one"}))
    )

    assert {p.item_id.value for p in result.concepts_admitted} == {"chosen.one"}
    assert not result.refusals


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


class _EscapingDrafts:
    """A `DraftSurface` whose calls fail the way a `.theurian/proposals` symlink
    escape does (#237-class), to prove that failure propagates rather than
    being recorded as a refusal of the concept or relation being drafted.
    """

    def draft(
        self,
        request: ProposalRequest,  # noqa: ARG002 - port shape; unused is exactly the point
        *,
        local: bool = False,  # noqa: ARG002 - ditto
    ) -> DraftedProposal:
        raise ProjectPathEscapeError("`.theurian/proposals` escapes the working tree")

    def draft_from_document(
        self,
        document: Mapping[str, object],  # noqa: ARG002 - port shape; unused is exactly the point
        *,
        evidence: Evidence,  # noqa: ARG002 - ditto
        local: bool = False,  # noqa: ARG002 - ditto
    ) -> DraftedMigration:
        raise ProjectPathEscapeError("`.theurian/proposals` escapes the working tree")


def test_a_project_level_path_escape_propagates_rather_than_becoming_a_refusal(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)
    service = OkfImportService(drafts=DraftOnlyProposals(_EscapingDrafts()))

    with pytest.raises(ProjectPathEscapeError):
        service.import_bundle(_request(bundle))


# ---------------------------------------------------------------------------
# Review-Finding: security HIGH -- the refusal boundary enumerated the
# callee's docstring, not its exception surface; seven shapes abort the
# bundle and leak resolved paths.
# ---------------------------------------------------------------------------


def test_a_directory_named_dot_md_refuses_that_entry_and_the_rest_still_drafts(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """A directory matching the `*.md` glob is a real shape `unbounded_shape`
    admits (it names only FIFOs, sockets and devices, never a directory), so
    `read_bytes()` on it raises `IsADirectoryError` -- previously uncaught.
    """
    bundle = tmp_path / "bundle"
    (bundle / "a-directory.md").mkdir(parents=True)
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)

    result = _service(paths).import_bundle(_request(bundle))

    assert {p.item_id.value for p in result.concepts_admitted} == {"vanilla"}
    [refusal] = result.refusals
    assert refusal.key == "a-directory.md"
    assert refusal.literal == "a directory, not a file"


def test_a_body_file_naming_a_directory_refuses_that_reference_only(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "sidecar-dir").mkdir(parents=True)
    _write(
        bundle,
        "auth-policy.md",
        _EXPORTED_CONCEPT.replace(
            "theurian_content_type: text/markdown",
            "theurian_content_type: application/json\ntheurian_body_file: sidecar-dir",
        ),
    )
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)

    result = _service(paths).import_bundle(_request(bundle))

    assert {p.item_id.value for p in result.concepts_admitted} == {"vanilla"}
    [refusal] = result.refusals
    assert refusal.key == "theurian_body_file"
    assert refusal.literal == "sidecar-dir"


def test_front_matter_past_the_yaml_cap_refuses_the_concept_rather_than_raising(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """`load_yaml_mapping`'s 4 MiB cap is smaller than `read_source_file`'s
    8 MiB file cap: a file comfortably under the file cap can still overrun
    the YAML one, and `decode_concept_document` must not let that escape.
    """
    bundle = tmp_path / "bundle"
    oversized_label = "x" * (5 * 1024 * 1024)
    _write(
        bundle,
        "oversized.md",
        f"---\ntype: decision\ntitle: T\nstatus: stable\ntags: ['{oversized_label}']\n---\nbody\n",
    )
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)

    result = _service(paths).import_bundle(_request(bundle))

    assert {p.item_id.value for p in result.concepts_admitted} == {"vanilla"}
    [refusal] = result.refusals
    assert refusal.key == "oversized.md"
    assert "exceeded" in refusal.literal


# ---------------------------------------------------------------------------
# Review-Finding: adversarial HIGH -- a draft-refused concept still
# contributes its addRelation edges and cap charge.
# ---------------------------------------------------------------------------


def test_a_relation_from_a_concept_that_refused_at_draft_never_lands(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """The reproduced shape: `architecture.auth-policy` already exists, so its
    own re-import refuses at `.draft()` -- and its `theurian_relations` entry
    must not become a dangling `addRelation` with no accompanying proposal.
    """
    _seed_existing_item(paths, "architecture.auth-policy")
    bundle = tmp_path / "bundle"
    concept = _EXPORTED_CONCEPT.replace(
        "theurian_content_type: text/markdown",
        "theurian_content_type: text/markdown\n"
        "theurian_relations:\n"
        "  - type: related_to\n"
        "    target: architecture.session-store\n",
    )
    _write(bundle, "auth-policy.md", concept)
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)

    result = _service(paths).import_bundle(_request(bundle))

    assert {p.item_id.value for p in result.concepts_admitted} == {"vanilla"}
    assert result.relations_proposal is None
    kinds_and_keys = {(r.kind, r.key) for r in result.refusals}
    assert ("draft", "architecture.auth-policy") in kinds_and_keys
    assert (KIND_RELATIONS, "architecture.auth-policy") in kinds_and_keys
    # No `.theurian/proposals/` directory carries the orphaned edge anywhere.
    for migration_file in paths.proposals.glob("*/*.yaml"):
        assert "session-store" not in migration_file.read_text(encoding="utf-8")


def test_a_relation_beside_one_that_refuses_still_lands_for_the_drafted_concept(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """Two concepts, each naming a relation; only one drafts. The cap charge
    and the relations document both reflect the one that actually did.
    """
    _seed_existing_item(paths, "architecture.auth-policy")
    bundle = tmp_path / "bundle"
    refusing = _EXPORTED_CONCEPT.replace(
        "theurian_content_type: text/markdown",
        "theurian_content_type: text/markdown\n"
        "theurian_relations:\n"
        "  - type: related_to\n"
        "    target: architecture.session-store\n",
    )
    drafting = _VANILLA_CONCEPT.replace(
        "status: stable\n---",
        "status: stable\n"
        "theurian_relations:\n"
        "  - type: related_to\n"
        "    target: architecture.other\n"
        "---",
    )
    _write(bundle, "auth-policy.md", refusing)
    _write(bundle, "vanilla.md", drafting)

    result = _service(paths).import_bundle(_request(bundle))

    assert {p.item_id.value for p in result.concepts_admitted} == {"vanilla"}
    assert result.relations_proposal is not None
    document = yaml.safe_load(result.relations_proposal.migration_file.read_text(encoding="utf-8"))
    assert document["operations"] == [
        {
            "op": "addRelation",
            "sourceItemId": "vanilla",
            "relationType": "related_to",
            "targetItemId": "architecture.other",
        }
    ]


# ---------------------------------------------------------------------------
# M13: `_DRAFT_REFUSAL` names three exception types; the existing battery
# only reaches two of them (ProposalError, MigrationError).
# ---------------------------------------------------------------------------


def test_a_content_type_with_no_body_extension_refuses_at_draft_not_the_whole_import(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """`domain/proposal.py::body_extension` raises `InvariantViolationError`
    for a content type outside Markdown/JSON/YAML -- reachable here because
    `MediaType` accepts any `type/subtype` string while `_EXTENSIONS` maps
    only three of them. Dropping this member from `_DRAFT_REFUSAL` would let
    it escape `.draft()` uncaught, aborting the whole import rather than
    refusing this one concept -- HIGH-1's crash shape, from the draft side
    the read-failure battery cannot reach.
    """
    bundle = tmp_path / "bundle"
    _write(bundle, "sidecar.txt", "plain text body")
    _write(
        bundle,
        "plain-body.md",
        "---\ntype: decision\ntitle: Plain text body\nstatus: stable\n"
        "theurian_content_type: text/plain\ntheurian_body_file: sidecar.txt\n---\n\nbody\n",
    )
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)

    result = _service(paths).import_bundle(_request(bundle))

    assert {p.item_id.value for p in result.concepts_admitted} == {"vanilla"}
    [refusal] = result.refusals
    assert refusal.kind == "draft"
    assert refusal.key == "plain-body"
    assert refusal.literal == "the proposal service refused it: InvariantViolationError"


# ---------------------------------------------------------------------------
# M-e: a concept inside an unreadable directory must not vanish.
# ---------------------------------------------------------------------------

_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_a_concept_inside_an_unreadable_directory_is_refused_not_dropped(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """`Path.rglob`'s own directory scan drops a `PermissionError` silently,
    so a concept inside a mode-000 directory vanished with no refusal at all.
    """
    bundle = tmp_path / "bundle"
    locked = bundle / "locked"
    _write(bundle, "locked/hidden.md", _VANILLA_CONCEPT)
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)
    locked.chmod(0o000)
    try:
        result = _service(paths).import_bundle(_request(bundle))
    finally:
        locked.chmod(0o755)

    assert {p.item_id.value for p in result.concepts_admitted} == {"vanilla"}
    [refusal] = result.refusals
    assert refusal.key == "locked"
    assert refusal.literal == "not readable"


# ---------------------------------------------------------------------------
# Round-1 closure pin (HIGH-1): every demonstrated crash face from
# security HIGH -- "the refusal boundary enumerated the callee's docstring,
# not its exception surface" -- planted together. Only three of the seven
# shapes 28f107ac's commit message names got their own individual test.
# ---------------------------------------------------------------------------


def _yaml_concept(
    *, title: str, status: str = "stable", kind: str = "decision", **extra: object
) -> str:
    mapping: dict[str, object] = {"type": kind, "title": title, "status": status, **extra}
    return f"---\n{yaml.safe_dump(mapping, sort_keys=False, allow_unicode=True)}---\n\nbody\n"


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_every_demonstrated_crash_face_refuses_rather_than_raising_and_leaks_no_path(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """`import_bundle` must return a result, never raise, for every one of
    these -- and neither a refusal nor the untouched sibling's own drafted
    migration may carry the bundle's or the project's absolute path (T-25).
    """
    bundle = tmp_path / "bundle"
    name_max = os.pathconf(str(tmp_path), "PC_NAME_MAX") if hasattr(os, "pathconf") else 255
    long_name = "x" * (name_max + 245) + ".txt"  # comfortably past NAME_MAX on any filesystem

    (bundle / "a-directory.md").mkdir(parents=True)
    (bundle / "sidecar-dir").mkdir(parents=True)
    _write(bundle, "plainfile.txt", "not a directory")
    locked_sidecar = bundle / "locked-sidecar.bin"
    locked_sidecar.write_text("secret")
    locked_sidecar.chmod(0o000)
    locked_concept = bundle / "mode000-concept.md"
    locked_concept.write_text(_yaml_concept(title="Locked concept"), encoding="utf-8")
    locked_concept.chmod(0o000)

    _write(
        bundle,
        "body-directory.md",
        _yaml_concept(title="Body is a directory", theurian_body_file="sidecar-dir"),
    )
    _write(bundle, "body-empty.md", _yaml_concept(title="Body is empty", theurian_body_file=""))
    _write(bundle, "body-dot.md", _yaml_concept(title="Body is dot", theurian_body_file="."))
    _write(
        bundle,
        "body-nul.md",
        _yaml_concept(title="Body has a NUL", theurian_body_file="abc\x00def"),
    )
    _write(
        bundle,
        "body-locked.md",
        _yaml_concept(title="Body is unreadable", theurian_body_file="locked-sidecar.bin"),
    )
    _write(
        bundle,
        "body-through-file.md",
        _yaml_concept(
            title="Body reached through a file", theurian_body_file="plainfile.txt/sub.txt"
        ),
    )
    _write(
        bundle,
        "body-too-long.md",
        _yaml_concept(title="Body name too long", theurian_body_file=long_name),
    )
    oversized_label = "x" * (5 * 1024 * 1024)
    _write(
        bundle,
        "oversized.md",
        f"---\ntype: decision\ntitle: T\nstatus: stable\ntags: ['{oversized_label}']\n---\nbody\n",
    )
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)

    try:
        result = _service(paths).import_bundle(_request(bundle))
    finally:
        locked_sidecar.chmod(0o644)
        locked_concept.chmod(0o644)

    assert {p.item_id.value for p in result.concepts_admitted} == {"vanilla"}
    assert len(result.refusals) == 10, result.refusals
    assert all(refusal.kind for refusal in result.refusals), "every refusal names its kind"

    by_literal = {(refusal.kind, refusal.literal) for refusal in result.refusals}
    assert ("concept", "a directory, not a file") in by_literal
    assert ("concept", "not readable") in by_literal
    assert any(kind == "concept" and "exceeded" in literal for kind, literal in by_literal)
    for reference in (
        "sidecar-dir",
        "",
        ".",
        "abc\x00def",
        "locked-sidecar.bin",
        "plainfile.txt/sub.txt",
        long_name,
    ):
        assert ("reference", reference) in by_literal

    haystacks = [
        *(
            field
            for refusal in result.refusals
            for field in (refusal.kind, refusal.key, refusal.literal)
        ),
        _migration_text(result.concepts_admitted[0].proposal.directory),
    ]
    for prefix in (str(tmp_path), str(bundle.resolve())):
        for haystack in haystacks:
            assert prefix not in haystack, f"{prefix!r} leaked into {haystack!r}"


# ---------------------------------------------------------------------------
# M-c: two concepts collapsing on one item id must not both draft.
# ---------------------------------------------------------------------------


def test_two_concepts_colliding_on_one_item_id_admit_only_the_first_in_walk_order(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """`.draft()`'s own guard cannot see this: neither proposal is yet a
    landed migration, so `_check_expected_revision` refuses neither. The
    import service is the only place both concepts are visible at once.
    """
    bundle = tmp_path / "bundle"
    collision = (
        "---\ntype: decision\ntitle: {title}\nstatus: stable\n"
        "theurian_item_id: architecture.duplicate\n---\n\nbody\n"
    )
    _write(bundle, "concept-a.md", collision.format(title="First"))
    _write(bundle, "concept-b.md", collision.format(title="Second"))

    result = _service(paths).import_bundle(_request(bundle))

    assert {p.item_id.value for p in result.concepts_admitted} == {"architecture.duplicate"}
    assert len(result.concepts_admitted) == 1
    [refusal] = result.refusals
    assert refusal.key == "concept-b.md", "the second sighting in bytewise walk order refuses"
    assert "architecture.duplicate" in refusal.literal


def test_the_walk_order_never_decides_which_duplicate_wins(
    tmp_path: Path, paths: ProjectPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_walk_concept_paths` sorts every candidate bytewise before
    `_admit_concepts` ever sees it, so the test above's outcome cannot be
    coincidence with a real directory's own listing order: this forces
    `os.walk`'s own traversal backwards, through its own seam, and the
    bytewise-first path must still be the one that wins.
    """
    bundle = tmp_path / "bundle"
    collision = (
        "---\ntype: decision\ntitle: {title}\nstatus: stable\n"
        "theurian_item_id: architecture.duplicate\n---\n\nbody\n"
    )
    _write(bundle, "concept-a.md", collision.format(title="First"))
    _write(bundle, "concept-b.md", collision.format(title="Second"))

    real_walk = os.walk

    def reversed_walk(
        top: str | os.PathLike[str], onerror: Callable[[OSError], object] | None = None
    ) -> Iterator[tuple[str, list[str], list[str]]]:
        if Path(top).resolve() != bundle.resolve():
            yield from real_walk(top, onerror=onerror)
            return
        for dirpath, dirnames, filenames in real_walk(top, onerror=onerror):
            yield dirpath, sorted(dirnames, reverse=True), sorted(filenames, reverse=True)

    monkeypatch.setattr(os, "walk", reversed_walk)

    result = _service(paths).import_bundle(_request(bundle))

    assert {p.item_id.value for p in result.concepts_admitted} == {"architecture.duplicate"}
    [refusal] = result.refusals
    assert refusal.key == "concept-b.md", (
        "the bytewise-first path must win even when the walk visits it last"
    )


# ---------------------------------------------------------------------------
# M10: the manifest reservation is positional (ADR-0037 decision 2), not
# name-wide -- unlike index.md/log.md, reserved at the bundle root only.
# ---------------------------------------------------------------------------


def test_the_manifest_reservation_is_positional_not_name_wide(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    bundle = tmp_path / "bundle"
    _write(bundle, "theurian-bundle.md", "---\ntype: Theurian Bundle\n---\n")
    _write(
        bundle,
        "architecture/theurian-bundle.md",
        "---\ntype: decision\ntitle: Nested manifest-named concept\nstatus: stable\n---\n\nbody\n",
    )

    result = _service(paths).import_bundle(_request(bundle))

    assert not result.refusals
    assert {p.item_id.value for p in result.concepts_admitted} == {"architecture.theurian-bundle"}
