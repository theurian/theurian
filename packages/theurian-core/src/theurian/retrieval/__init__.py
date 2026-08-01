"""Hybrid retrieval.

Pre-filter by project, tenant, ACL, sensitivity, and validity; then FTS5, vector,
and RAPTOR searches; fuse with Reciprocal Rank Fusion; expand parents and
children; rerank, deduplicate, diversify, and pack within a token budget.

Filtering happens *before* ranking. A post-filter returns fewer results than
requested and leaks the existence of hidden content through result-count
differences (FR-R1).

Every result carries provenance and the safety triple. A result with no source
anchor is not returned (FR-R5, SEC-15).
"""
