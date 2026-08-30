"""GitHub adapter for the ``ReviewProvider`` port.

**Not implemented.** This package holds no adapter and no HTTP client; it is the
place the Milestone 7 ingestion work will land, and ``system.capabilities``
reports ``reviewIngestion: false`` until it does.

The adapter will fetch pull requests, reviews, threads, inline comments,
resolution state, and CI outcomes as structured evidence. It must never
classify, generalise, or call a model -- that separation is what lets raw
ingestion succeed when candidate generation fails (FR-V5).

Repositories must be allowlisted in ``.theurian/config.yaml`` before one is
contacted (SEC-10). ``security/project_config.py`` reads that file, but for
``security.secretScan`` alone; no reader of the allowlist key exists in ``src/``
today, so the allowlist is owed with the adapter rather than in force
(`#368 <https://github.com/theurian/theurian/issues/368>`_ carries it, as
`#129 <https://github.com/theurian/theurian/issues/129>`_ closed on this
sentence's wording rather than on the control).
"""
