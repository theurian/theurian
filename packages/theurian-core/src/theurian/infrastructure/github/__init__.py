"""GitHub adapter for the ``ReviewProvider`` port.

**Not implemented.** This package holds no adapter and no HTTP client; it is the
place the Milestone 7 ingestion work will land, and ``system.capabilities``
reports ``reviewIngestion: false`` until it does.

The adapter will fetch pull requests, reviews, threads, inline comments,
resolution state, and CI outcomes as structured evidence. It must never
classify, generalise, or call a model -- that separation is what lets raw
ingestion succeed when candidate generation fails (FR-V5).

Repositories must be allowlisted in ``.theurian/config.yaml`` before one is
contacted (SEC-10). No reader of that file exists in ``src/`` today, so the
allowlist is owed with the adapter rather than in force
(`#129 <https://github.com/theurian/theurian/issues/129>`_).
"""
