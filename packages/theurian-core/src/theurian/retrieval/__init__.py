"""Hybrid retrieval. **Empty: the code lives elsewhere.**

What runs today is ``application/retrieval_service.py``, ``domain/ranking.py``
and ``infrastructure/sqlite/index_store.py``: FTS5 word and trigram lookups, an
exact dense scan, RRF fusion, rerank, deduplicate, diversify, and packing within
a token budget. RAPTOR search and parent/child expansion are Milestone 6.

**The pre-filter is project and status, not five axes.** This said "pre-filter by
project, tenant, ACL, sensitivity, and validity", which is FR-R1's text rather
than a description of ``SqliteIndexStore._scope``. Tenant and ACL group are real
domain values that default to the single-tenant case
(``tests/unit/test_scope_isolation.py``) and have no column in the index;
``sensitivity`` has a column and no query reads it; the only validity-window
filter, ``list_items(current_at=...)``, has no caller. See #63, and the schema
comment beside the columns in ``index_schema.py``.

This docstring ships inside the wheel, so it is the one a user reads from
``theurian.retrieval.__doc__`` rather than from the repository.

What does hold, and is the part FR-R1 exists for: filtering happens *before*
ranking, in the same statement as the match. A post-filter returns fewer results
than requested and leaks the existence of hidden content through result-count
differences -- and the gate above it is read the same way, so a withheld row
occupies no result slot, rank, or published number (T-17, ADR-0021).

Every result carries provenance and the safety triple; a result with no source
anchor is not returned (FR-R5, SEC-15).
"""
