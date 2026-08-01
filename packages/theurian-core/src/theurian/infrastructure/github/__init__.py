"""GitHub adapter for the ``ReviewProvider`` port.

Fetches pull requests, reviews, threads, inline comments, resolution state, and
CI outcomes as structured evidence. It never classifies, generalises, or calls a
model -- that separation is what lets raw ingestion succeed when candidate
generation fails (FR-V5).

Repositories must be allowlisted in ``.theurian/config.yaml``; one that is not
listed is never contacted (SEC-10).
"""
