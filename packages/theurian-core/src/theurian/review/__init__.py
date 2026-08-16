"""Review knowledge: ingestion, classification, candidate generation.

**Not implemented.** This package holds no code. The review domain model --
``ReviewThread``, ``PromotionGate`` and ``KnowledgeCandidate`` -- is built and
lives in :mod:`theurian.domain.review`; the three stages named above are owed
with Milestone 7, and ``system.capabilities`` reports ``reviewIngestion: false``
until they land
(`#129 <https://github.com/theurian/theurian/issues/129>`_).

Review history is evidence; approved knowledge is a generalisation of it. The
step between them is a human judgement, and there is no code path that skips it
(ADR-0013).

The promotion gate decides whether a thread deserves a human's attention, using
seven observed facts. It never decides whether the generalisation is true.
"""
