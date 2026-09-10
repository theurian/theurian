"""Review evidence on disk: the source, not a cache (ADR-0030 decision 3).

Normalized review records land as structured JSON under ``.theurian/review/``.
They are **canonical** in ADR-0010's sense and they are the fourth category
ADR-0004's Milestone 8 amendment records: a Canonical record whose own source is
not re-readable. Upstream comments are editable and deletable, so a discarded
local copy of a deleted comment is data loss and no refetch recovers it -- which
is why ADR-0030 withdrew "raw GitHub review caches" from ADR-0004's *Never
Git-tracked* list and why nothing here deletes.

Slice 3 builds the SQLite serving store from these files. :class:`ReviewEvidenceStore`
therefore ships both halves now: the writer that lands them, and the reader that
store will consume -- the writer in
:mod:`theurian.infrastructure.review_evidence.store` and the reader in
:mod:`theurian.infrastructure.review_evidence.reader`, which the store's
``read_all`` delegates to.

:class:`EvidenceReader` is exported beside the store for one caller and one
question: slice 3's build asks *which files still exist* just before it
publishes, so a record deleted since its read is not put back. That is a
directory listing rather than a record read, so it does not belong behind
``read_all`` -- and it is not added to the store façade either, which is the
module the 800-line ceiling already split the reader out of.

**The file grain is the thread, the submission and the pull-request event -- one
file each.** Four constraints decide it, and the third is the one that rules out
the coarser shape:

* *A record is the unit the scan gate refuses whole* (decision 4). Under
  ``block`` the flagged record never becomes a file, so the grain is exactly how
  much evidence one flagged comment costs. One file per pull request would
  discard a whole pull request's threads because a single comment carried a
  secret-shaped string.
* *Refusal identity must reach comment level.* It does: a thread's comments are
  inline in its own file, and the gate reports the comment id inside the refused
  thread even though the file it withholds is the thread's.
* *A flagged record in PR X must not prevent PR Y landing.* Every file's path is
  derived from that record's own provider id, so no two records share a write.
* *No silently partial unit.* A thread is the smallest unit the domain can
  express at all -- :class:`~theurian.domain.review.ReviewThread` refuses a
  thread with no comments -- so a per-comment file would have to invent a shape
  the model does not have, and a half-written thread is not a value this codebase
  can construct.

**Paths are built from provider-generated identifiers, never from the configured
``owner/repo``.** The schema pattern for ``providers.review.repositories``
accepts ``../..``, so joining a configured string into a filesystem path escapes
the directory while satisfying the published contract. A repository becomes a
hashed directory name and a record becomes a leaf named after its provider id --
or, when that id is not a name a filesystem should carry, after its hash -- plus
a short case tag on any spelling a case-folding filesystem would otherwise merge
into another id's file. :mod:`theurian.infrastructure.review_evidence.layout` is
where both rules live, and every write and every read resolves through
``security/paths.py``'s containment on top of them.
"""

from __future__ import annotations

from theurian.infrastructure.review_evidence.errors import ReviewEvidenceError
from theurian.infrastructure.review_evidence.layout import (
    EVIDENCE_FORMAT_VERSION,
    EvidenceKind,
    record_leaf,
    record_path,
    repository_directory,
)
from theurian.infrastructure.review_evidence.reader import EvidenceReader
from theurian.infrastructure.review_evidence.records import EvidenceRecord
from theurian.infrastructure.review_evidence.run import IngestionRun, new_ingestion_run
from theurian.infrastructure.review_evidence.store import ReviewEvidenceStore

__all__ = [
    "EVIDENCE_FORMAT_VERSION",
    "EvidenceKind",
    "EvidenceReader",
    "EvidenceRecord",
    "IngestionRun",
    "ReviewEvidenceError",
    "ReviewEvidenceStore",
    "new_ingestion_run",
    "record_leaf",
    "record_path",
    "repository_directory",
]
