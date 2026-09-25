"""ADR-0037 decision 6's three containment pins, over a real disk.

``test_okf_import.py`` already drives the common escape shape -- a relative
``../`` traversal in ``theurian_body_file`` -- through
``test_a_bad_body_file_reference_is_refused_without_the_resolved_path_or_the_bundle``.
This file adds the shapes that pin does not reach: an *absolute*
``theurian_body_file`` (a distinct branch of ``resolve_within_root``, refused
for being absolute rather than for climbing above the root); a symlink
*inside* the bundle at each of ``okf_import.py``'s two ``read_source_file``
call sites (``test_path_security_call_sites.py`` names them --
``_decode_concept_file`` and ``_resolve_body``); the bundle root itself
reached through a symlinked parent, which must *not* be refused; and a sweep
of the whole output surface for T-25's disclosure -- not one refusal's
``literal`` field, but every byte a run writes to disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes.clock import FrozenClock
from fakes.ids import SeededIdGenerator

from theurian.application.draft_only_proposals import DraftOnlyProposals
from theurian.application.okf_import import OkfImportRequest, OkfImportService, _read_failure_reason
from theurian.application.project_service import ProjectPaths, initialize_project
from theurian.application.proposal_service import ProposalService
from theurian.cli.migration_pipeline import rehearse_migration_set
from theurian.domain.errors import (
    PathDepthExceededError,
    PathEscapeError,
    SymlinkBudgetExceededError,
    UnanchoredLinkTargetError,
    UnreadableLinkError,
)
from theurian.domain.identifiers import AgentId, ItemId, MigrationId, ProjectId, RevisionId, TaskId
from theurian.domain.migration import current_revision_in
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


def _service(paths: ProjectPaths) -> OkfImportService:
    def current_revision(item_id: ItemId) -> RevisionId | None:
        loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)
        return current_revision_in(loaded.migration_set, item_id)

    def landed_migration(migration_id: MigrationId) -> object:
        loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)
        return loaded.migration_set.get(migration_id)

    def landed_migrations() -> object:
        loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)
        return loaded.migration_set

    service = ProposalService(
        paths=paths,
        project_id=ProjectId("demo"),
        clock=FrozenClock(),
        ids=SeededIdGenerator(),
        validate=lambda document: validate_migration_document(document, SCHEMAS),
        current_revision=current_revision,
        landed_migration=landed_migration,  # type: ignore[arg-type]
        landed_migrations=landed_migrations,  # type: ignore[arg-type]
        rehearse=lambda candidate: rehearse_migration_set(candidate, clock=FrozenClock()),
    )
    return OkfImportService(drafts=DraftOnlyProposals(service))


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


def _all_written_bytes(paths: ProjectPaths) -> bytes:
    """Every byte a run wrote under ``.theurian/proposals`` -- the whole disk surface."""
    chunks = [p.read_bytes() for p in paths.proposals.rglob("*") if p.is_file()]
    return b"\n".join(chunks)


# -- The escape pin: shapes beyond the ../ traversal `test_okf_import.py` already drives ----


def test_an_absolute_theurian_body_file_is_refused_and_its_target_content_never_drafts(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """`resolve_within_root` refuses any absolute `relative` outright (decision 6).

    Distinct from a `../` climb: `Path.is_absolute()` fires before any
    resolution or comparison runs, so this is a second branch of the same
    containment function, not a restatement of the traversal case.
    """
    bundle = tmp_path / "bundle"
    outside = tmp_path / "outside-abs.json"
    outside.write_text('{"secret": "absolute-path-sentinel"}', encoding="utf-8")
    concept = f"""---
type: decision
title: Absolute escape
status: stable
theurian_content_type: application/json
theurian_body_file: {outside}
---

body
"""
    _write(bundle, "abs-escape.md", concept)
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)

    result = _service(paths).import_bundle(_request(bundle))

    assert len(result.refusals) == 1
    assert result.refusals[0].key == "theurian_body_file"
    assert {p.item_id.value for p in result.concepts_admitted} == {"vanilla"}
    assert b"absolute-path-sentinel" not in _all_written_bytes(paths)


# -- The symlink pin: both directions -------------------------------------------------------


def test_a_symlinked_concept_file_inside_the_bundle_is_refused_by_its_own_path(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """A `.md` file `_walk_concept_paths` discovers may itself be a symlink out.

    `_decode_concept_file`'s `read_source_file` call: the discovered relative
    path never carries `../`, so this is the containment case a literal
    traversal string cannot reach -- only a symlinked entry can.
    """
    bundle = tmp_path / "bundle"
    outside_md = tmp_path / "outside-concept.md"
    outside_md.write_text(
        "---\ntype: decision\ntitle: Outside\nstatus: stable\n---\n\nSYMLINK-MD-SENTINEL\n",
        encoding="utf-8",
    )
    bundle.mkdir()
    (bundle / "linked.md").symlink_to(outside_md)
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)

    result = _service(paths).import_bundle(_request(bundle))

    assert len(result.refusals) == 1
    assert result.refusals[0].key == "linked.md"
    assert result.refusals[0].literal == "escapes the bundle root"
    assert {p.item_id.value for p in result.concepts_admitted} == {"vanilla"}
    assert b"SYMLINK-MD-SENTINEL" not in _all_written_bytes(paths)


# -- The reason-map pin: a PathEscapeError subclass is not always an escape -----------------


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (
            PathDepthExceededError("deep.md", "/root", limit=32),
            "nests too deep below the bundle root",
        ),
        (
            SymlinkBudgetExceededError("chain.md", "/root", limit=40),
            "reached through too many symbolic links",
        ),
        (UnreadableLinkError("link.md", "/root"), "a symbolic link on the path could not be read"),
        (
            UnanchoredLinkTargetError("link.md", "/root"),
            "a symbolic link's target could not be anchored inside the root",
        ),
        (PathEscapeError("../etc/passwd", "/root"), "escapes the bundle root"),
    ],
)
def test_each_path_escape_error_subclass_gets_its_own_reason(
    exc: PathEscapeError, reason: str
) -> None:
    """Issue #233's inaccuracy, one layer up: every subclass used to report
    "escapes the bundle root" too, false for the first four -- a link chain
    can cross the depth or hop budget, fail to read, or land on an
    unanchored target without ever resolving outside the root. Driven
    directly against `_read_failure_reason` rather than through a real
    symlink chain: `test_path_security.py` already proves each subclass
    fires for its own real construction, so this pins only the mapping
    `okf_import.py` owns.
    """
    assert _read_failure_reason(exc) == reason


def test_a_theurian_body_file_reached_through_a_symlink_is_refused(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """`_resolve_body`'s `read_source_file` call, escaping through a symlink rather than `../`.

    `assert_no_symlink_escape` is what this drives: the sidecar name a concept
    names is a plain in-bundle string, and only following the link it names
    leaves the root.
    """
    bundle = tmp_path / "bundle"
    outside_sidecar = tmp_path / "outside-sidecar.json"
    outside_sidecar.write_text('{"secret": "sidecar-symlink-sentinel"}', encoding="utf-8")
    bundle.mkdir()
    (bundle / "sidecar-link.json").symlink_to(outside_sidecar)
    concept = """---
type: decision
title: Sidecar symlink
status: stable
theurian_content_type: application/json
theurian_body_file: sidecar-link.json
---

body
"""
    _write(bundle, "sidecar-concept.md", concept)
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)

    result = _service(paths).import_bundle(_request(bundle))

    assert len(result.refusals) == 1
    assert result.refusals[0].key == "theurian_body_file"
    assert result.refusals[0].literal == "sidecar-link.json"
    assert {p.item_id.value for p in result.concepts_admitted} == {"vanilla"}
    assert b"sidecar-symlink-sentinel" not in _all_written_bytes(paths)


def test_a_bundle_root_under_a_symlinked_parent_directory_still_admits_in_bundle_references(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """The macOS `/tmp` shape: the root is resolved before any comparison.

    Measured: `import_bundle`'s own `root.resolve()` and
    `resolve_within_root`/`assert_no_symlink_escape`'s own internal
    `root.resolve()` calls are each independently sufficient here -- removing
    only one leaves the test green, and it takes removing all three at once to
    turn it red, which is when every in-bundle reference here is judged
    against an *unresolved* root its own resolved position is never
    `relative_to`, refusing a bundle that never escaped anything.
    """
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    bundle = linked_parent / "bundle"
    concept = """---
type: decision
title: Sidecar under a symlinked root
status: stable
theurian_content_type: application/json
theurian_body_file: sidecar.json
---

body
"""
    _write(bundle, "with-sidecar.md", concept)
    _write(bundle, "sidecar.json", '{"ok": true}')
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)

    result = _service(paths).import_bundle(_request(bundle))

    assert not result.refusals
    assert {p.item_id.value for p in result.concepts_admitted} == {"vanilla", "with-sidecar"}


# -- The refusal-literal pin (T-25): the whole output surface, not one field ----------------


def test_no_refusal_or_drafted_file_carries_the_operator_filesystem_layout(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """A resolved absolute path is the operator's own machine layout (T-25).

    Sweeps every refusal's `literal` *and* every byte written under
    `.theurian/proposals/` -- not only the refusal that caused it, because the
    claim is about the whole artifact a pull request would carry, not about
    one field a narrower assertion could happen to hit.
    """
    bundle = tmp_path / "bundle"
    concept = """---
type: decision
title: Bad body file
status: stable
theurian_content_type: application/json
theurian_body_file: ../../../../etc/passwd
---

body
"""
    _write(bundle, "bad.md", concept)
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)

    result = _service(paths).import_bundle(_request(bundle))

    assert {p.item_id.value for p in result.concepts_admitted} == {"vanilla"}
    for refusal in result.refusals:
        assert str(tmp_path) not in refusal.literal
    assert str(tmp_path).encode() not in _all_written_bytes(paths)
