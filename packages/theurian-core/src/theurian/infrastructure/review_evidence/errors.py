"""What review-evidence reads and writes refuse with.

One type for both halves, because an operator does the same thing about either:
look at the file the message names. The distinction that would justify two types
-- "the provider sent something impossible" against "what is on disk is not what
this build writes" -- is carried by the sentence, not by the class.
"""

from __future__ import annotations

from theurian.domain.errors import TheurianError


class ReviewEvidenceError(TheurianError):
    """An evidence file could not be written, or could not be read back.

    ``remedy`` is set by the raise site rather than looked up, unlike
    :data:`~theurian.domain.review_ingest.REMEDIES`. The population here is not
    closed by a grade -- a corrupt file, an unwritable directory and an id no
    filesystem should carry are three different cures -- so the constructor
    requires one and refuses an empty string, which is the property the lookup
    table buys on the other path.
    """

    def __init__(self, message: str, *, remedy: str) -> None:
        if not remedy.strip():
            raise ValueError(
                "A review-evidence refusal must carry a remedy naming the artefact "
                "and something the reader can run; this one is empty."
            )
        super().__init__(message)
        self.remedy = remedy
