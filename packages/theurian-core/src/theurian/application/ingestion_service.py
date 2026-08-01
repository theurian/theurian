"""Source ingestion (FR-S1 .. FR-S6, ADR-0010).

Walks a project's knowledge directory, dispatches each file to a parser, and
normalizes the result into the Canonical Layer.

Two properties shape the design:

**Failure is per document.** A malformed YAML file among two hundred must not
make the other 199 unavailable, so a parse failure becomes a value in the report
rather than an exception that unwinds the walk.

**Unchanged files cost one hash.** Touching a file without changing it must not
trigger a reparse and a reindex, so content hashes from the previous run are
compared before any parsing happens.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from theurian.domain.errors import (
    InputTooLargeError,
    PathEscapeError,
    TheurianError,
)
from theurian.domain.ingestion import (
    IngestedDocument,
    IngestionReport,
    ParseFailure,
    ParseWarning,
)
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.ports import NormalizedDocument, SourceParser
from theurian.domain.values import ContentHash, MediaType
from theurian.normalization.projection import project
from theurian.security.paths import MAX_SOURCE_FILE_BYTES, read_source_file

#: Directories under `.theurian/` that hold ingestible sources. Derived
#: directories are excluded: ingesting `generated/` would feed Theurian's own
#: output back into itself as though it were a source (ADR-0004).
INGESTIBLE_SUBDIRECTORIES: tuple[str, ...] = ("knowledge", "specifications")


class ParserResolver(Protocol):
    """Resolves a document to its media type and parser.

    A Protocol rather than a concrete registry so the application layer never
    imports an adapter (ADR-0003).
    """

    def detect(self, path: PurePosixPath, data: bytes) -> MediaType | None: ...
    def for_media_type(self, media_type: MediaType) -> SourceParser | None: ...


class WarningSource(Protocol):
    """Parsers that surface warnings alongside a normalized document.

    ``NormalizedDocument`` carries no warning field, and widening the
    ``SourceParser`` port for one parser's benefit would push a Markdown concern
    into every future adapter. Parsers that have warnings expose them here
    instead, and the service asks only those that do (ADR-0019).
    """

    def warnings_for(self, text: str) -> tuple[ParseWarning, ...]: ...


@dataclass(frozen=True, slots=True)
class IngestionRequest:
    """One ingestion run's inputs."""

    project_root: Path
    knowledge_dir: Path
    #: Content hashes from the previous run, keyed by project-relative path.
    #: Anything matching is reported unchanged and never reparsed.
    known_hashes: dict[str, str]
    #: Commit the sources were read at, so every anchor pins an immutable
    #: object rather than a path that may since have moved (FR-S3).
    commit_sha: str | None = None
    repository: str | None = None


class IngestionService:
    """Normalizes a project's sources into canonical documents."""

    def __init__(self, resolver: ParserResolver) -> None:
        self._resolver = resolver

    def ingest(self, request: IngestionRequest) -> IngestionReport:
        """Walk and normalize every ingestible source.

        Never raises for a document-level problem. A caller inspects
        ``report.failures`` and decides; the run itself always completes.
        """
        report = IngestionReport()

        for path in self._discover(request.knowledge_dir):
            relative = path.relative_to(request.project_root).as_posix()
            try:
                self._ingest_one(relative, request, report)
            except TheurianError as exc:
                # Security and limit refusals are per-document too. One hostile
                # file must not prevent the rest of the tree from being read.
                report.failures.append(ParseFailure(path=relative, reason=str(exc)))

        return report

    def _ingest_one(
        self, relative: str, request: IngestionRequest, report: IngestionReport
    ) -> None:
        try:
            data = read_source_file(request.project_root, PurePosixPath(relative))
        except (PathEscapeError, InputTooLargeError) as exc:
            report.failures.append(ParseFailure(path=relative, reason=str(exc)))
            return
        except OSError as exc:
            report.failures.append(ParseFailure(path=relative, reason=f"unreadable: {exc}"))
            return

        content_hash = ContentHash.of_bytes(data)
        if request.known_hashes.get(relative) == content_hash.value:
            # The cheap early exit: touching a file without changing it costs
            # one hash, not a reparse and a reindex.
            report.unchanged.append(relative)
            return

        media_type = self._resolver.detect(PurePosixPath(relative), data)
        if media_type is None:
            report.skipped.append(relative)
            return

        parser = self._resolver.for_media_type(media_type)
        if parser is None:
            report.failures.append(
                ParseFailure(
                    path=relative,
                    reason=f"no parser registered for {media_type}",
                    media_type=media_type.value,
                )
            )
            return

        anchor = _anchor(relative, request)

        try:
            normalized = parser.parse(data, media_type=media_type, anchor=anchor)
        except (ValueError, InputTooLargeError) as exc:
            report.failures.append(
                ParseFailure(path=relative, reason=str(exc), media_type=media_type.value)
            )
            return

        report.documents.append(
            _to_document(
                normalized,
                path=relative,
                source_hash=content_hash,
                parser=parser,
                warnings=_warnings(parser, data),
            )
        )

    def _discover(self, knowledge_dir: Path) -> Iterator[Path]:
        """Yield candidate source files in a stable order.

        Sorted so a failure reports the same first offender on every run rather
        than whichever the filesystem happened to yield first.
        """
        for subdirectory in INGESTIBLE_SUBDIRECTORIES:
            root = knowledge_dir / subdirectory
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if not path.is_file() or path.name.startswith("."):
                    continue
                # A symlink is refused later by read_source_file if it escapes;
                # skipping obviously-oversized files here avoids reading them.
                if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
                    continue
                yield path


def _warnings(parser: SourceParser, data: bytes) -> tuple[ParseWarning, ...]:
    """Collect warnings from parsers that produce them."""
    collector = getattr(parser, "warnings_for", None)
    if collector is None:
        return ()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:  # pragma: no cover - parse would have failed first
        return ()
    result: tuple[ParseWarning, ...] = collector(text)
    return result


def _anchor(relative: str, request: IngestionRequest) -> SourceAnchor:
    """Build the anchor that makes this document traceable (FR-S3).

    A commit SHA plus a path pins an immutable Git object, so the anchor still
    resolves after the file is edited, moved, or deleted.
    """
    return SourceAnchor(
        provider="git" if request.commit_sha else "filesystem",
        source_uri=f"git://{request.repository or 'local'}/{relative}"
        if request.commit_sha
        else f"file://{relative}",
        repository=request.repository,
        commit_sha=request.commit_sha,
        file_path=relative,
    )


def _to_document(
    normalized: NormalizedDocument,
    *,
    path: str,
    source_hash: ContentHash,
    parser: SourceParser,
    warnings: tuple[ParseWarning, ...],
) -> IngestedDocument:
    projection: str | None = None
    if normalized.structured is not None:
        projection = project(normalized.structured)

    return IngestedDocument(
        path=path,
        title=normalized.title,
        body=normalized.body,
        content_type=normalized.content_type,
        content_hash=normalized.content_hash,
        source_hash=source_hash,
        anchors=normalized.anchors,
        parser_id=parser.parser_id,
        structured=normalized.structured,
        text_projection=projection,
        warnings=warnings,
    )


def manifest_from(report: IngestionReport, previous: dict[str, str]) -> dict[str, str]:
    """Build the content-hash manifest for the next run's early exit.

    Carries forward hashes for unchanged files, because those were never
    reparsed and so are absent from ``report.documents``. Dropping them would
    make every second run a full reparse.
    """
    manifest = {path: previous[path] for path in report.unchanged if path in previous}
    for document in report.documents:
        # The *source* hash, matching what the early exit computes. Storing the
        # body hash instead makes every Markdown file with front matter reparse
        # on every run, because its body and its bytes differ.
        manifest[document.path] = document.source_hash.value
    return manifest
