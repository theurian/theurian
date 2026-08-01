"""Ingestion against a real filesystem (FR-S1 .. FR-S6).

Two properties matter more than the happy path: one bad document must not take
down the run, and an unchanged document must not be reparsed.
"""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

import pytest

from theurian.application.ingestion_service import (
    IngestionRequest,
    IngestionService,
    manifest_from,
)
from theurian.domain.ingestion import IngestionReport
from theurian.domain.ports import SourceParser
from theurian.domain.values import MediaType
from theurian.infrastructure.filesystem.parsers.registry import (
    ParserRegistry,
    detect_media_type,
)

pytestmark = pytest.mark.integration


class _Resolver:
    def __init__(self) -> None:
        self._registry = ParserRegistry()

    def detect(self, path: PurePosixPath, data: bytes) -> MediaType | None:
        return detect_media_type(path, data)

    def for_media_type(self, media_type: MediaType) -> SourceParser | None:
        return self._registry.for_media_type(media_type)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    for relative in ("knowledge/architecture", "knowledge/domain", "specifications", "generated"):
        (root / ".theurian" / relative).mkdir(parents=True)
    return root


def _ingest(root: Path, known: dict[str, str] | None = None) -> IngestionReport:
    return IngestionService(_Resolver()).ingest(
        IngestionRequest(
            project_root=root,
            knowledge_dir=root / ".theurian",
            known_hashes=known or {},
            commit_sha="a" * 40,
            repository="acme/demo",
        )
    )


def _write(root: Path, relative: str, content: str) -> Path:
    path = root / ".theurian" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# -- The happy path --------------------------------------------------------


def test_every_supported_format_is_ingested(project: Path) -> None:
    _write(project, "knowledge/architecture/auth.md", "# Auth\n\nSigned tokens.\n")
    _write(project, "specifications/spec.yaml", "id: spec.x\ntitle: Spec X\n")
    _write(project, "specifications/data.json", '{"title": "Data"}')
    _write(project, "specifications/api.yaml", "openapi: 3.1.0\ninfo:\n  title: API\npaths: {}\n")

    report = _ingest(project)

    assert report.succeeded
    assert {d.parser_id for d in report.documents} == {"markdown", "yaml", "json", "openapi"}


def test_every_document_carries_an_anchor(project: Path) -> None:
    """FR-S3. A commit SHA plus a path pins an immutable object, so the anchor
    still resolves after the file is edited or moved."""
    _write(project, "knowledge/architecture/auth.md", "# Auth\n")

    document = _ingest(project).documents[0]
    anchor = document.anchors[0]

    assert anchor.commit_sha == "a" * 40
    assert anchor.repository == "acme/demo"
    assert anchor.file_path == ".theurian/knowledge/architecture/auth.md"
    assert anchor.is_git_anchored


def test_structured_documents_gain_a_projection(project: Path) -> None:
    _write(project, "specifications/spec.yaml", "outcomes:\n  failure:\n    code: NOPE\n")

    document = _ingest(project).documents[0]

    assert document.text_projection is not None
    assert "outcomes.failure.code: NOPE" in document.text_projection
    assert "NOPE" in document.searchable_text


# -- Failure isolation -----------------------------------------------------


def test_one_bad_document_does_not_stop_the_run(project: Path) -> None:
    """A malformed file among two hundred must not make the other 199
    unavailable."""
    _write(project, "knowledge/architecture/good.md", "# Good\n")
    _write(project, "specifications/broken.yaml", "key: [unclosed\n")
    _write(project, "specifications/fine.yaml", "id: ok\n")

    report = _ingest(project)

    assert len(report.documents) == 2
    assert len(report.failures) == 1
    assert report.failures[0].path.endswith("broken.yaml")
    assert not report.succeeded


def test_completing_and_succeeding_are_different(project: Path) -> None:
    """A run that parses 199 of 200 completes and did not succeed, and a caller
    needs to tell those apart."""
    _write(project, "knowledge/architecture/good.md", "# Good\n")
    _write(project, "specifications/broken.yaml", "key: [unclosed\n")

    report = _ingest(project)

    assert report.total_discovered == 2
    assert not report.succeeded


def test_invalid_utf8_fails_only_its_own_document(project: Path) -> None:
    (project / ".theurian/knowledge/architecture/bad.md").write_bytes(b"\xff\xfe not utf8")
    _write(project, "knowledge/architecture/good.md", "# Good\n")

    report = _ingest(project)

    assert len(report.documents) == 1
    assert len(report.failures) == 1


# -- Incremental ingestion -------------------------------------------------


def test_unchanged_documents_are_not_reparsed(project: Path) -> None:
    _write(project, "knowledge/architecture/auth.md", "# Auth\n")
    first = _ingest(project)
    manifest = manifest_from(first, {})

    second = _ingest(project, manifest)

    assert second.documents == []
    assert len(second.unchanged) == 1


def test_a_document_with_front_matter_stays_unchanged(project: Path) -> None:
    """The regression that made every Markdown file with front matter reparse
    forever: the manifest stored the body hash while the early exit compared the
    source hash, and for such a file those differ.
    """
    _write(
        project,
        "knowledge/architecture/auth.md",
        "---\nreviewers: [a]\n---\n\n# Auth\n\nBody.\n",
    )

    first = _ingest(project)
    manifest = manifest_from(first, {})
    second = _ingest(project, manifest)
    third = _ingest(project, manifest_from(second, manifest))

    assert len(second.unchanged) == 1, "front matter must not defeat the early exit"
    assert len(third.unchanged) == 1, "and must stay stable across further runs"


def test_the_manifest_carries_unchanged_entries_forward(project: Path) -> None:
    """Unchanged files are never reparsed, so they are absent from `documents`.
    Dropping them from the manifest would make every second run a full reparse.
    """
    _write(project, "knowledge/architecture/a.md", "# A\n")
    _write(project, "knowledge/architecture/b.md", "# B\n")

    first = manifest_from(_ingest(project), {})
    second_report = _ingest(project, first)
    second = manifest_from(second_report, first)

    assert len(second) == 2
    assert second == first


def test_an_edited_document_is_reparsed(project: Path) -> None:
    path = _write(project, "knowledge/architecture/auth.md", "# Auth\n")
    manifest = manifest_from(_ingest(project), {})

    path.write_text("# Auth\n\nNow with more content.\n", encoding="utf-8")
    report = _ingest(project, manifest)

    assert len(report.documents) == 1
    assert report.unchanged == []


# -- What is and is not ingested -------------------------------------------


def test_derived_directories_are_not_ingested(project: Path) -> None:
    """Ingesting `generated/` would feed Theurian's own output back into itself
    as though it were a source (ADR-0004)."""
    _write(project, "generated/reviews/pr-431.md", "# Generated\n")
    _write(project, "knowledge/architecture/real.md", "# Real\n")

    report = _ingest(project)

    assert [d.path for d in report.documents] == [".theurian/knowledge/architecture/real.md"]


def test_unknown_formats_are_skipped_not_failed(project: Path) -> None:
    """A `.txt` note is not a parse failure; nothing claims it."""
    _write(project, "knowledge/architecture/notes.txt", "plain text")

    report = _ingest(project)

    assert report.skipped == [".theurian/knowledge/architecture/notes.txt"]
    assert report.succeeded


def test_hidden_files_are_ignored(project: Path) -> None:
    _write(project, "knowledge/architecture/.gitkeep", "")
    assert _ingest(project).total_discovered == 0


def test_an_empty_project_ingests_nothing(project: Path) -> None:
    report = _ingest(project)
    assert report.succeeded
    assert report.total_discovered == 0


def test_discovery_order_is_stable(project: Path) -> None:
    """A failure must report the same first offender on every run rather than
    whichever the filesystem happened to yield first."""
    for name in ("c", "a", "b"):
        _write(project, f"knowledge/domain/{name}.md", f"# {name}\n")

    first = [d.path for d in _ingest(project).documents]
    second = [d.path for d in _ingest(project).documents]

    assert first == second == sorted(first)


# -- Security --------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_symlink_escaping_the_project_fails_only_itself(project: Path) -> None:
    """SEC-7, T-5, through the ingestion walk."""
    _write(project, "knowledge/architecture/good.md", "# Good\n")
    (project / ".theurian/knowledge/architecture/leak.md").symlink_to("/etc/passwd")

    report = _ingest(project)

    assert len(report.documents) == 1
    assert any("escapes the permitted root" in f.reason for f in report.failures)


def test_warnings_reach_the_report(project: Path) -> None:
    """ADR-0019: a governed key in front matter must be visible to the user."""
    _write(
        project,
        "knowledge/architecture/auth.md",
        "---\nstatus: approved\n---\n\n# Auth\n",
    )

    report = _ingest(project)

    assert {w.code for w in report.warnings} == {"front-matter-governed-field"}


def test_front_matter_status_never_reaches_the_document(project: Path) -> None:
    _write(
        project,
        "knowledge/architecture/auth.md",
        "---\nstatus: approved\n---\n\n# Auth\n\nBody.\n",
    )

    document = _ingest(project).documents[0]

    assert "approved" not in document.body
    assert document.structured is not None
    assert document.structured["frontMatter"]["status"] == "approved"  # type: ignore[index]
