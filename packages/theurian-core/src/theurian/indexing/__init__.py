"""Indexing: Canonical Layer to Index Layer.

Chunking, FTS5 population, embedding, RAPTOR construction, and graph edges.

Builds are incremental and become visible only through an atomic swap of
``active_indexes``. The previously published index answers every query
throughout, so search never goes dark and a partial build is never reachable
(NFR-4).

External model calls happen outside write transactions. Holding a transaction
across a summarization call turns a slow model into a stalled daemon (NFR-8).
"""
