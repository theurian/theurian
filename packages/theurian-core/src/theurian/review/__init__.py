"""Review knowledge: ingestion, classification, candidate generation.

**Not implemented.** This package holds no code. The review domain model --
``ReviewThread``, ``PromotionGate`` and ``KnowledgeCandidate`` -- is built and
lives in :mod:`theurian.domain.review`; the three stages named above are owed
with Milestone 7, and ``system.capabilities`` reports ``reviewIngestion: false``
until they land.

**No open issue owns them.** This docstring named
`#129 <https://github.com/theurian/theurian/issues/129>`_ until it closed
``COMPLETED`` on the wording of the documents that describe the absence rather
than on the code, and the nearest open issues do not reach it either:
`#368 <https://github.com/theurian/theurian/issues/368>`_ ingests
``Review-Finding:`` commit trailers and calls itself a git-history source, and
`#429 <https://github.com/theurian/theurian/issues/429>`_ owns only the SEC-10
fetch controls an adapter would need first. Stated rather than repointed,
because naming an issue that does not cover the work is the same defect one
number over.

Review history is evidence; approved knowledge is a generalisation of it. The
step between them is a human judgement, and there is no code path that skips it
(ADR-0013).

The promotion gate decides whether a thread deserves a human's attention, using
seven observed facts. It never decides whether the generalisation is true.
"""
