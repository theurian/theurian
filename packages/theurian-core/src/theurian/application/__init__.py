"""Application layer: use cases and orchestration.

Depends on :mod:`theurian.domain` only. Adapters arrive by constructor
injection; nothing here names a concrete implementation (ADR-0003).

Milestone 1 onward: ``SetupService``, ``MigrationService``, ``IndexingService``,
``RetrievalService``, ``ReviewService``, ``TraceabilityService``.

``SetupService`` is shared by ``theurian setup`` and ``/theurian:setup``. There
is exactly one implementation of setup, because two would drift (FR-L1).
"""
