"""GitHub adapter for the ``ReviewProvider`` port.

**Not implemented.** This package holds no adapter and no HTTP client; it is the
place the Milestone 7 ingestion work will land, and ``system.capabilities``
reports ``reviewIngestion: false`` until it does. ``reviewFindings: true`` is a
different flag and no part of it lives here: that tool reads ``Review-Finding:``
trailers out of local git history (ADR-0029), so it contacts no repository and
needs no allowlist. The first fetch this package performs is what makes SEC-10's
controls load-bearing.

The adapter will fetch pull requests, reviews, threads, inline comments,
resolution state, and CI outcomes as structured evidence. It must never
classify, generalise, or call a model -- that separation is what lets raw
ingestion succeed when candidate generation fails (FR-V5).

Repositories must be allowlisted in ``.theurian/config.yaml`` before one is
contacted (SEC-10). ``security/project_config.py`` reads that file, but for
``security.secretScan`` alone; no reader of the allowlist key exists in ``src/``
today, so the allowlist is owed with the adapter rather than in force
(`#429 <https://github.com/theurian/theurian/issues/429>`_ owns it against the
first external fetch path, as
`#129 <https://github.com/theurian/theurian/issues/129>`_ closed on this
sentence's wording rather than on the control).
"""
