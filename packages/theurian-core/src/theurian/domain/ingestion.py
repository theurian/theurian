"""Ingestion result types.

A parser failure fails **one document**, not the run. A malformed YAML file
among two hundred knowledge documents must not make the other 199 unavailable,
so failures are values carried in a report rather than exceptions that unwind
the whole walk (FR-S1, FR-S4).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from theurian.domain.errors import InvariantViolationError
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.values import ContentHash, MediaType


@dataclass(frozen=True, slots=True)
class ParseWarning:
    """Something the parser noticed but did not fail on.

    A warning is not a lesser error -- it is the mechanism for the cases where
    silently doing nothing would mislead. A ``status: approved`` in front matter
    that Theurian ignores is the motivating example (ADR-0019): the author
    believes something is approved, and only a warning tells them otherwise.
    """

    code: str
    message: str
    #: The document-relative location, when the parser can identify one.
    location: str | None = None

    def __post_init__(self) -> None:
        if not self.code:
            raise InvariantViolationError("ParseWarning.code must not be empty")


@dataclass(frozen=True, slots=True)
class ParseFailure:
    """One document that could not be parsed.

    Carried in the report rather than raised, so one bad file does not take down
    the run.
    """

    path: str
    reason: str
    media_type: str | None = None


@dataclass(frozen=True, slots=True)
class IngestedDocument:
    """One source document, normalized (ADR-0010).

    ``structured`` is what separates normalization from conversion. Discarding
    it would make specification coverage and contradiction detection impossible
    later, and impossible to add back without reprocessing everything.
    """

    path: str
    title: str
    body: str
    content_type: MediaType
    #: Hash of the canonical body -- what the document *says*. For Markdown this
    #: excludes front matter, so adding metadata to a file does not change the
    #: identity of its content (ADR-0019).
    content_hash: ContentHash
    #: Hash of the raw source bytes -- what the file *is*. Change detection uses
    #: this, because it is what the next run can compute without parsing.
    #: Conflating the two makes any file whose body differs from its bytes --
    #: every Markdown file with front matter -- reparse on every run.
    source_hash: ContentHash
    anchors: tuple[SourceAnchor, ...]
    parser_id: str
    structured: dict[str, object] | None = None
    text_projection: str | None = None
    warnings: tuple[ParseWarning, ...] = ()

    def __post_init__(self) -> None:
        if not self.path:
            raise InvariantViolationError("IngestedDocument.path must not be empty")
        expected = ContentHash.of_text(self.body)
        if expected != self.content_hash:
            raise InvariantViolationError(
                f"{self.path}: content hash mismatch -- declared {self.content_hash.short}, "
                f"body hashes to {expected.short}"
            )
        if not self.anchors:
            raise InvariantViolationError(
                f"{self.path}: every ingested document needs a source anchor (FR-S3)"
            )

    @property
    def searchable_text(self) -> str:
        """Body plus projection, which is what lexical search indexes.

        A structured document's body is often thin -- an OpenAPI file's prose is
        a few descriptions -- so searching the body alone would miss most of what
        the document actually says.
        """
        if self.text_projection is None:
            return self.body
        return f"{self.body}\n\n{self.text_projection}" if self.body else self.text_projection


@dataclass(slots=True)
class IngestionReport:
    """The outcome of one ingestion run."""

    documents: list[IngestedDocument] = field(default_factory=list)
    failures: list[ParseFailure] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        """Whether every discovered document parsed.

        Distinct from "the run completed": a run that parses 199 of 200
        documents completes and did not succeed, and the caller needs to be able
        to tell those apart.
        """
        return not self.failures

    @property
    def warnings(self) -> tuple[ParseWarning, ...]:
        return tuple(w for document in self.documents for w in document.warnings)

    @property
    def total_discovered(self) -> int:
        return len(self.documents) + len(self.failures) + len(self.unchanged) + len(self.skipped)
