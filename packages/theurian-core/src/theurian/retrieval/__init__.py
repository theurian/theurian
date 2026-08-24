"""Hybrid retrieval. **Empty: the code lives elsewhere.**

What runs today is ``application/retrieval_service.py``, ``domain/ranking.py``
and ``infrastructure/sqlite/index_store.py``: FTS5 word and trigram lookups, an
exact dense scan, RRF fusion, rerank, deduplicate, diversify, and packing within
a token budget. RAPTOR search and parent/child expansion are Milestone 6.

**The pre-filter is project, status and sensitivity, not five axes.** This said
"pre-filter by project, tenant, ACL, sensitivity, and validity", which is FR-R1's
text rather than a description of ``SqliteIndexStore._scope``. Tenant and ACL
group are real domain values that default to the single-tenant case
(``tests/unit/test_scope_isolation.py``) and have no column in the index.
``sensitivity`` was in that sentence as a column no query read, and is now
filtered on against the deployment's grant -- by the build, which writes no row
above it, and by ``_scope``/``_node_scope``, which emit an ``IN`` predicate over
it with the match (#119 phases 3-4). See #63, #119, and the schema comment beside
the columns in ``index_schema.py``.

The validity-window axis now has a caller: ``knowledge.search``'s optional
``asOf`` parameter (#63 phase 2), applied through ``ValidityPeriod.contains``
in Python on both answer paths rather than through a SQL filter on
``SqliteCanonicalStore.list_items`` -- that parameter existed once and is
deleted, because it compared a stored ``validFrom``/``validTo`` against
``asOf`` as SQLite TEXT and so silently disagreed with the Python comparison
whenever the two were authored in different UTC offsets (found in review
round 1 of PR #112). It is a refinement a caller opts into, not a default:
omitting ``asOf`` filters on nothing more than before, because a default would
make ``isWithinValidity`` constant-``true`` on a fresh index and give the
ranked path a permanent stale-index statistics residual. On the ranked path
it is applied once, on the far side of the depth-doubling loop that decides
how many times a retriever is asked for more -- never inside the check that
loop's own exit condition watches, because a caller-chosen moment folded into
that check would let the loop's retriever-call count move with ``asOf``,
reviving the single-withheld-row timing oracle the loop's depth margin exists
to prevent (a second CRITICAL finding in the same review round; see
``theurian.application.visibility.Visibility.at_moment``).

**FR-R1's remaining three axes are disposed of, and not all in the same way.**
This paragraph used to say that tenant, ACL and sensitivity "remain unenforced as
controls", with enforcement tracked by #119. #119 has since closed, and the two
halves of it settled differently
(`ADR-0025 <https://github.com/theurian/theurian/blob/main/docs/adr/0025-sensitivity-is-enforced-before-0-1-0-stable.md>`_):

- **Sensitivity is enforced, at read time, in three places that back each other
  up.** A build writes no chunk row for an item above the ceiling this deployment
  declares, so the withheld text is not in the FTS5 tables to price the visible
  rows against; ``_scope`` and ``_node_scope`` emit ``sensitivity IN (…)`` over
  that grant in the same statement as the match, on both halves of the derived
  index; and a ``changeSensitivity`` moving an item past that ceiling purges its
  rows out of the published build in the same ``migrate apply``. The canonical
  re-check on the item's *current* class stands behind all three.
- **Tenant and ACL group are discharged degenerately, which is a different claim
  and a weaker one.** They are refused at write time, so no stored row carries
  anything but the single-tenant default and there is nothing along either axis
  to withhold. No predicate filters on them because none can be exercised. A
  deployment that ever stores a second tenant needs a real control, not this
  argument.

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
