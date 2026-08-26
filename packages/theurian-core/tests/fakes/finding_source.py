"""A canned :class:`ReviewFindingSource` (ADR-0003, OSS-15).

Returns a fixed tuple of findings so a consumer -- the write path and the
recurrence query of a later slice -- can be exercised offline without a git
repository. The git adapter's own behaviour (scoping, byte-preservation) is
tested against real repositories; this fake exists for the ports that inject the
source, not to re-test git.
"""

from __future__ import annotations

from typing import final

from theurian.domain.review_finding import ReviewFinding


@final
class FakeReviewFindingSource:
    """Replays a preset finding tuple, in the order it was given."""

    def __init__(self, findings: tuple[ReviewFinding, ...] = ()) -> None:
        self._findings = findings

    def load_findings(self) -> tuple[ReviewFinding, ...]:
        return self._findings
