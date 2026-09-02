"""Review knowledge: ingestion, classification, candidate generation.

**Not implemented.** This package holds no code. The review domain model --
``ReviewThread``, ``PromotionGate`` and ``KnowledgeCandidate`` -- is built and
lives in :mod:`theurian.domain.review`; the three stages named above are owed
with Milestone 7, and ``system.capabilities`` reports ``reviewIngestion: false``
until they land.

**Owned by `#479 <https://github.com/theurian/theurian/issues/479>`_**, filed
from #428's measurement after four nearer candidates were each read and verified
not to cover the work -- `#429 <https://github.com/theurian/theurian/issues/429>`_
keeps the SEC-10 fetch controls an adapter needs first, and they stay its scope
rather than this one's. This docstring named
`#129 <https://github.com/theurian/theurian/issues/129>`_ until it closed
``COMPLETED`` on the wording of the documents that describe the absence rather
than on the code. Whether #479 is still open is a tracker fact no offline test
can hold, which is why it was read rather than assumed
(``docs/architecture/source-normalization.md`` carries that reasoning in full).

Review history is evidence; approved knowledge is a generalisation of it. The
step between them is a human judgement, and there is no code path that skips it
(ADR-0013).

The promotion gate decides whether a thread deserves a human's attention, using
seven observed facts. It never decides whether the generalisation is true.
"""
