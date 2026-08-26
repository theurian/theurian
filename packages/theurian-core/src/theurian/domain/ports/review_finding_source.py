"""ReviewFindingSource port: git-history trailers to canonical findings (ADR-0029).

The FR-S1 Git-commit-metadata arm, distinct from the FR-V GitHub-API arm. It is a
local read of ``git log`` that works on any clone with no network and no token,
and it is the seam the write path (a later slice) and the recurrence query
(ADR-0029 decision 5) inject rather than naming a concrete git adapter.

The ``SourceParser`` port does *not* fit this source, which is why this port
exists: ``SourceParser.parse`` maps one file's ``bytes`` -- claimed by media type
-- to one ``NormalizedDocument``, whereas a git-history read is claimed by no file
media type, runs ``git`` itself, and yields *many* findings across many commits.
Forcing the trailer read through the media-type registry would be the bad fit
ADR-0003's port set exists to avoid.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from theurian.domain.review_finding import FindingLoad


@runtime_checkable
class ReviewFindingSource(Protocol):
    """Reads ``Review-Finding:`` trailers from a source into canonical records.

    A source ingests only what its embargo boundary allows (ADR-0029 decision 6).
    The public git-history adapter reads only the public default branch, so it
    structurally holds no embargoed trailer; a future non-public source owes the
    serving-layer refusal instead. That scoping is the adapter's contract to keep,
    not this port's to express -- the port returns the findings the source
    resolved, in a deterministic, total order (decision 1, AC-6).
    """

    def load_findings(self) -> FindingLoad:
        """Every keyed line the source resolves, accepted or rejected (D3).

        The result is the pair ``(accepted findings, rejected lines)``: every
        column-0 keyed line is accounted in exactly one tuple, never silently
        dropped and never a fatal abort, so the mapping stays loss-free (AC-1). A
        malformed line is captured as a :class:`~theurian.domain.review_finding.RejectedTrailer`
        rather than raised, both tuples in a stable total order (AC-6).

        Raises:
            An adapter that reads external state may raise its own error when that
            state is unreachable (the git adapter raises
            ``GitHistoryUnavailableError`` / ``GitOutputFramingError``); a malformed
            trailer is *not* one of those -- it is a rejected record, not an error.
        """
        ...
