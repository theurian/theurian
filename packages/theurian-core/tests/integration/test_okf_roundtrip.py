"""``theurian okf export`` -> ``theurian okf import``, round-tripped for real
(ADR-0037 decisions 2, 6, 7).

Every other OKF integration test drives one side of the pipeline against a
hand-built fixture: `test_okf_export.py`/`test_okf_commands.py` write a bundle
and stop, `test_okf_import.py`/`test_okf_import_path_containment.py` read a
hand-authored bundle and never wrote it. Nothing exercised the two together,
which is exactly the seam H-1 lived in: export writes `theurian_body_file` as
the bare, document-relative sidecar filename (decisions 2 and 7), and import
used to resolve it against the bundle root -- correct for a root-level
concept, wrong for a namespaced one, whose sidecar sits beside its document
one or more directories down. A namespaced non-Markdown concept was refused
and lost on re-import, and no test caught it because none ran both halves.

The matrix is every combination of **where** a concept sits ({root,
namespaced}) and **what its body is** ({markdown, json, yaml, text/plain,
openapi}, plus two alias types below), each built through the real write
path -- `init`, `migrate apply`, `okf export` -- into a real bundle
directory, then read back through `OkfImportService.import_bundle` into a
second, independent project. Markdown never sidecars (it always embeds) and
is here as the control: it admits at both positions before and after H-1's
fix, so a matrix that went green by accident -- everything admitting
regardless of the bug -- is not what this file measures.

Six cells (markdown/json/yaml, each at both positions) are ADMITTED: the
concept drafts, and its body reaches the drafted proposal's body file
byte-for-byte. The rest are REFUSED: `domain/proposal.py::body_extension`
admits exactly `text/markdown`, `application/json` and `application/yaml` --
literal values, not format classes -- which is ADR-0037 decision 7's
recorded boundary (export is total over content types, import narrows back
to what a proposal body can hold, M-1). Before this PR that refusal
happened at `.draft()`, leaking the raw `InvariantViolationError` class
name; it now happens at the reference stage, named. Two of the refused
cells (`application/schema+json`, `text/x-yaml`) are aliases export treats
as the same format as an admitted one (HIGH-1): `sidecar_extension` writes
them as ordinary `.json`/`.yaml` sidecars on export, and they are still
refused on import, because the literal-value boundary does not widen for an
alias -- alias normalization is a deliberate non-goal, filed as its own
issue.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest
from fakes.clock import FrozenClock
from fakes.ids import SeededIdGenerator
from git_harness import commit_migrations
from typer.testing import CliRunner

from theurian.application.draft_only_proposals import DraftOnlyProposals
from theurian.application.okf_import import OkfImportRequest, OkfImportService
from theurian.application.project_service import ProjectPaths, initialize_project
from theurian.application.proposal_service import ProposalService
from theurian.cli.main import app
from theurian.cli.migration_pipeline import rehearse_migration_set
from theurian.domain.identifiers import AgentId, ItemId, MigrationId, ProjectId, RevisionId, TaskId
from theurian.domain.migration import current_revision_in
from theurian.domain.project import DEFAULT_KNOWLEDGE_DIRECTORY
from theurian.domain.proposal import Evidence
from theurian.infrastructure.filesystem.migration_loader import (
    load_migrations,
    validate_migration_document,
)

pytestmark = pytest.mark.integration

runner = CliRunner()
SCHEMAS = Path(__file__).resolve().parents[4] / "schemas"

MIGRATION_ID: Final = "0MG00000000000000000000000"

EVIDENCE: Final = Evidence(
    agent_id=AgentId("claude-code"),
    task_id=TaskId("task-okf-roundtrip"),
    model="claude-opus-5",
    reasoning="Round-tripping an OKF bundle across every position and content type.",
    anchors=(),
)


@dataclass(frozen=True, slots=True)
class Cell:
    """One matrix cell: where a concept sits, what its body is, and the outcome."""

    item_id: str
    revision_id: str
    content_type: str
    body: str
    admitted: bool


#: {root, namespaced} x {markdown, json, yaml, text/plain, openapi}. `backend.*`
#: item ids put the namespaced half one directory below the bundle root
#: (`ItemId.namespace` derives from the dotted prefix, `okf_bundle.py::_stem`'s
#: reason); root ids carry no dot at all, so their concept sits at the bundle
#: root and their sidecar (when they have one) never crossed a directory
#: boundary -- which is why the root half of this matrix never reddened.
CELLS: Final[tuple[Cell, ...]] = (
    Cell(
        "root-markdown",
        "0RMD1000000000000000000000",
        "text/markdown",
        "# Root Markdown\n\nSentinel ROOT-MARKDOWN-BODY-9f21.\n",
        admitted=True,
    ),
    Cell(
        "root-json",
        "0RJS2000000000000000000000",
        "application/json",
        '{"sentinel": "ROOT-JSON-BODY-4c7a"}\n',
        admitted=True,
    ),
    Cell(
        "root-yaml",
        "0RYM3000000000000000000000",
        "application/yaml",
        "sentinel: ROOT-YAML-BODY-1e83\n",
        admitted=True,
    ),
    Cell(
        "root-textplain",
        "0RTX4000000000000000000000",
        "text/plain",
        "Sentinel ROOT-TEXTPLAIN-BODY-6b05.\n",
        admitted=False,
    ),
    Cell(
        "root-openapi",
        "0RA50000000000000000000000",
        "application/vnd.oai.openapi",
        "openapi: 3.0.0\ninfo:\n  title: ROOT-OPENAPI-BODY-2d94\n  version: '1.0'\n",
        admitted=False,
    ),
    Cell(
        "backend.namespaced-markdown",
        "0NMD6000000000000000000000",
        "text/markdown",
        "# Namespaced Markdown\n\nSentinel NS-MARKDOWN-BODY-7a31.\n",
        admitted=True,
    ),
    Cell(
        "backend.namespaced-json",
        "0NJS7000000000000000000000",
        "application/json",
        '{"sentinel": "NS-JSON-BODY-3f68"}\n',
        admitted=True,
    ),
    Cell(
        "backend.namespaced-yaml",
        "0NYM8000000000000000000000",
        "application/yaml",
        "sentinel: NS-YAML-BODY-8c02\n",
        admitted=True,
    ),
    Cell(
        "backend.namespaced-textplain",
        "0NTX9000000000000000000000",
        "text/plain",
        "Sentinel NS-TEXTPLAIN-BODY-5e77.\n",
        admitted=False,
    ),
    Cell(
        "backend.namespaced-openapi",
        "0NA10000000000000000000000",
        "application/vnd.oai.openapi",
        "openapi: 3.0.0\ninfo:\n  title: NS-OPENAPI-BODY-0a49\n  version: '1.0'\n",
        admitted=False,
    ),
    # HIGH-1: an alias content type export treats as the same format
    # (`sidecar_extension` maps any `+json`/`+yaml` suffix or `text/x-yaml` to
    # the json/yaml sidecar extension) but `body_extension`'s literal set does
    # not -- exported as a genuine json/yaml sidecar, still refused on import.
    Cell(
        "root-schema-json",
        "0RSJ1100000000000000000000",
        "application/schema+json",
        '{"sentinel": "ROOT-SCHEMAJSON-BODY-7c31"}\n',
        admitted=False,
    ),
    Cell(
        "backend.namespaced-x-yaml",
        "0NXY1200000000000000000000",
        "text/x-yaml",
        "sentinel: NS-XYAML-BODY-2f84\n",
        admitted=False,
    ),
)

_ADMITTED_IDS: Final = frozenset(cell.item_id for cell in CELLS if cell.admitted)
_REFUSED_CONTENT_TYPES: Final = tuple(cell.content_type for cell in CELLS if not cell.admitted)


def _body_pin(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _operation_block(cell: Cell) -> str:
    namespace, _, leaf = cell.item_id.rpartition(".")
    title = leaf.replace("-", " ").title()
    return f"""  - op: createItem
    itemId: {cell.item_id}
    kind: architecture
    namespace: {namespace or "root"}
    owner: platform-team
  - op: upsertRevision
    itemId: {cell.item_id}
    revisionId: {cell.revision_id}
    contentFile: ../knowledge/{leaf}.src
    contentSha256: {_body_pin(cell.body)}
    metadata:
      title: {title}
      contentType: {cell.content_type}
      kind: architecture
      namespace: {namespace or "root"}
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/{leaf}.src
"""


def _migration_document() -> str:
    header = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
"""
    return header + "".join(_operation_block(cell) for cell in CELLS)


def _write_knowledge_files(root: Path) -> None:
    knowledge = root / ".theurian" / "knowledge"
    knowledge.mkdir(parents=True, exist_ok=True)
    for cell in CELLS:
        leaf = cell.item_id.rpartition(".")[2]
        (knowledge / f"{leaf}.src").write_text(cell.body, encoding="utf-8")


@pytest.fixture
def export_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A git working tree, applied with every :data:`CELLS` row, as the CWD."""
    root = tmp_path / "export-project"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "export-datadir"))
    monkeypatch.chdir(root)
    yield root


def _invoke(*args: str) -> tuple[int, dict[str, Any]]:
    if args[:2] == ("migrate", "apply"):
        commit_migrations()
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    stream = result.stdout if result.exit_code == 0 else (result.stderr or result.stdout)
    return result.exit_code, json.loads(stream) if stream.strip() else {}


def _export_bundle(project: Path, target: Path) -> None:
    assert _invoke("init")[0] == 0
    _write_knowledge_files(project)
    migrations = project / ".theurian" / "migrations"
    migrations.mkdir(parents=True, exist_ok=True)
    (migrations / f"{MIGRATION_ID}-roundtrip.yaml").write_text(
        _migration_document(), encoding="utf-8"
    )
    assert _invoke("migrate", "apply")[0] == 0

    code, payload = _invoke("okf", "export", str(target))
    assert code == 0, payload
    assert payload["concepts"] == len(CELLS)


def _import_service(paths: ProjectPaths) -> OkfImportService:
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
        project_id=ProjectId("import-target"),
        clock=FrozenClock(),
        ids=SeededIdGenerator(),
        validate=lambda document: validate_migration_document(document, SCHEMAS),
        current_revision=current_revision,
        landed_migration=landed_migration,  # type: ignore[arg-type]
        landed_migrations=landed_migrations,  # type: ignore[arg-type]
        rehearse=lambda candidate: rehearse_migration_set(candidate, clock=FrozenClock()),
    )
    return OkfImportService(drafts=DraftOnlyProposals(service))


def test_every_position_and_content_type_round_trips_as_the_matrix_says(
    tmp_path: Path, export_project: Path
) -> None:
    """Export a real corpus through the real write path, import the bundle it
    produced into a second, independent project, and check every cell against
    what :data:`CELLS` says it should do.
    """
    bundle = tmp_path / "bundle"
    _export_bundle(export_project, bundle)

    import_root = tmp_path / "import-project"
    import_root.mkdir()
    import_paths = ProjectPaths(
        root=import_root, knowledge_dir=import_root / DEFAULT_KNOWLEDGE_DIRECTORY
    )
    initialize_project(import_paths)

    request = OkfImportRequest(
        root=bundle, owner="platform-team", author="dana@example.com", evidence=EVIDENCE
    )
    result = _import_service(import_paths).import_bundle(request)

    admitted_ids = {proposal.item_id.value for proposal in result.concepts_admitted}
    assert admitted_ids == _ADMITTED_IDS

    bodies_by_id = {
        proposal.item_id.value: proposal.proposal.body_file.read_text(encoding="utf-8")
        for proposal in result.concepts_admitted
    }
    for cell in CELLS:
        if not cell.admitted:
            continue
        if cell.content_type == "text/markdown":
            # A markdown body embeds in its concept document (decision 7) rather
            # than sidecaring, and the decoder reads everything after the front
            # matter as the body -- the generated `## Relations` heading
            # included (decision 4 emits it even with no relations, and
            # `decode_concept_document` never strips it back off). The round
            # trip is not byte-identity here (ADR-0037, *What this does not
            # close* item 2); containment is the honest check.
            assert cell.body.strip() in bodies_by_id[cell.item_id], cell.item_id
        else:
            # A non-markdown body sidecars, and decision 7's whole claim for it
            # is byte-for-byte preservation -- this is where H-1 actually broke.
            assert bodies_by_id[cell.item_id] == cell.body, cell.item_id

    reference_refusals = [r for r in result.refusals if r.kind == "reference"]
    assert len(reference_refusals) == len(_REFUSED_CONTENT_TYPES)
    literals = [r.literal for r in reference_refusals]
    for content_type in _REFUSED_CONTENT_TYPES:
        expected = (
            f"content type {content_type} has no proposal-body form; the import accepts "
            "text/markdown, application/json or application/yaml exactly -- an alias "
            "such as application/schema+json or text/x-yaml is refused even though its "
            "body is JSON or YAML"
        )
        assert literals.count(expected) == sum(
            1 for c in CELLS if not c.admitted and c.content_type == content_type
        ), content_type
