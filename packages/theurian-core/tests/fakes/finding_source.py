"""A canned :class:`ReviewFindingSource` (ADR-0003, OSS-15).

Returns a fixed tuple of findings so a consumer -- the write path and the
recurrence query of a later slice -- can be exercised offline without a git
repository. The git adapter's own behaviour (scoping, byte-preservation) is
tested against real repositories; this fake exists for the ports that inject the
source, not to re-test git.
"""

from __future__ import annotations

from typing import final

from theurian.domain.review_finding import FindingLoad, RejectedTrailer, ReviewFinding


@final
class FakeReviewFindingSource:
    """Replays preset accepted findings and rejected lines (D3), in the given order."""

    def __init__(
        self,
        findings: tuple[ReviewFinding, ...] = (),
        rejected: tuple[RejectedTrailer, ...] = (),
    ) -> None:
        self._findings = findings
        self._rejected = rejected

    def load_findings(self) -> FindingLoad:
        return FindingLoad(accepted=self._findings, rejected=self._rejected)
