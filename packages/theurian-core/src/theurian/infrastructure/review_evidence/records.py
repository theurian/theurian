"""The record types the writer takes and the reader answers with.

Split out of :mod:`theurian.infrastructure.review_evidence.store` when that
module passed this project's 800-line ceiling. This is the half neither seam
owns: :class:`~theurian.infrastructure.review_evidence.store.ReviewEvidenceStore`
derives a path from an :class:`EvidenceRecord`, and
:class:`~theurian.infrastructure.review_evidence.reader.EvidenceReader` answers
with a :class:`StoredRecord`. They live here rather than in either module
because ``store.py`` imports ``reader.py`` for the delegation, so a type defined
in ``store.py`` would need ``reader.py`` to import it back.
"""

from __future__ import annotations

from dataclasses import dataclass

from theurian.domain.errors import InvariantViolationError
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review import ReviewEvent, ReviewSubmission, ReviewThread
from theurian.domain.review_ingest import bounded_quote
from theurian.infrastructure.review_evidence.layout import EvidenceKind, record_path
from theurian.infrastructure.review_evidence.run import IngestionRun

#: The payload types one record may carry. Named once so the three functions that
#: switch on it are visibly ranging over the same population, and so mypy refuses
#: a ``match`` that stops covering it.
EvidencePayload = ReviewEvent | ReviewSubmission | ReviewThread


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """One record on its way to a file, or read back out of one.

    ``kind`` and ``record_key`` are **derived** from the payload rather than
    carried beside it. A stored ``kind`` is a field that can disagree with the
    object it labels -- and the label decides the directory, so a disagreement is
    a record filed where nothing looks for it.

    **This type is built on both sides of the seam, and the field notes below
    name the write side.** An ingestion run constructs one from what the adapter
    answered; :func:`~theurian.infrastructure.review_evidence.reader._stored`
    constructs one from a file on disk, which -- ``.theurian/review/`` being
    source rather than derived state, and not git-ignored -- may have arrived
    with a clone rather than from any run here (threat-model T-24). Read "the
    provider resolved it" and "Theurian writes it" as statements about the run
    that fetched a record, not as guarantees about a record read back.
    """

    #: The provider that answered, ``"github"`` today.
    provider: str
    #: The repository this record names, ``owner/name`` -- as the provider
    #: resolved it, on the run that fetched it. Written inside
    #: the file and hashed into the directory name, **never joined into a path**:
    #: the published allowlist pattern accepts ``../..``, so a joined value leaves
    #: the directory while satisfying the contract (ADR-0030 decision 3).
    repository: str
    #: FR-S3's pointer back to the upstream object. An ingestion run writes it
    #: rather than receiving it, which is why that run's scan does not read it
    #: (decision 3's third *Controlled by* value). Read back, it is whatever the
    #: file carries: the codec takes a non-empty string and nothing checks it
    #: further.
    anchor: SourceAnchor
    payload: EvidencePayload

    def __post_init__(self) -> None:
        if not self.provider:
            raise InvariantViolationError("EvidenceRecord.provider must not be empty")
        if not self.repository:
            raise InvariantViolationError("EvidenceRecord.repository must not be empty")
        if self.anchor.provider != self.provider:
            # Both values come out of a landed file on the read path -- `_stored`
            # builds this object from the document -- so `!r` alone bounded
            # nothing: a 2,000,000-character `provider` produced a
            # 2,000,394-character refusal (measured). The same class round two
            # closed for `formatVersion` and `kind` one function over; this is
            # the member the published-sentence walk did not reach until it was
            # widened to the raise sites `_read_one` republishes.
            raise InvariantViolationError(
                f"EvidenceRecord names provider {bounded_quote(self.provider)} and carries "
                f"an anchor from {bounded_quote(self.anchor.provider)}. The anchor is the "
                "only pointer back to material Theurian cannot re-fetch, so one that names "
                "another provider is a record that cannot be traced."
            )

    @property
    def kind(self) -> EvidenceKind:
        """Which of the three record kinds this is, from the payload's own type."""
        match self.payload:
            case ReviewEvent():
                return EvidenceKind.PULL_REQUEST
            case ReviewSubmission():
                return EvidenceKind.REVIEW_SUBMISSION
            case ReviewThread():
                return EvidenceKind.REVIEW_THREAD

    @property
    def record_key(self) -> str:
        """The identifier this record carries for itself inside its repository.

        The provider's own on a record an ingestion run fetched; on one read back
        off disk, whatever the payload names.

        A pull request is keyed by its **number** rather than by a node id: the
        number is what a reviewer types, it is unique inside the repository the
        directory already names, and it makes the landed tree readable to whoever
        opens the diff. A submission and a thread are keyed by their node id,
        which is the only identifier the provider gives them.
        """
        match self.payload:
            case ReviewEvent():
                return str(self.payload.number)
            case ReviewSubmission() | ReviewThread():
                return self.payload.external_id

    @property
    def relative_path(self) -> str:
        """Where this record lives, relative to ``.theurian/review/``."""
        return record_path(
            provider=self.provider,
            identity=self.repository,
            kind=self.kind,
            provider_id=self.record_key,
        )


@dataclass(frozen=True, slots=True)
class StoredRecord:
    """One record as it sits on disk, with the run that last observed it."""

    record: EvidenceRecord
    last_seen: IngestionRun
    #: Where it was read from, relative to the review directory and POSIX-spelled,
    #: so a report of what a run found reads identically on every platform.
    relative_path: str
