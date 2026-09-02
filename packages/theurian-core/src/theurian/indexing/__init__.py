"""Indexing: Canonical Layer to Index Layer. **Empty: the code lives elsewhere.**

What exists today is ``application/index_builder.py`` and
``infrastructure/sqlite/index_store.py``: chunking, FTS5 and trigram population,
and embedding. RAPTOR construction and graph edges are Milestone 6.

**NFR-4 is not discharged, and this said it was.** The paragraph here read
"builds are incremental and become visible only through an atomic swap of
``active_indexes``. The previously published index answers every query
throughout, so search never goes dark and a partial build is never reachable
(NFR-4)." Builds are not incremental -- ``test_building_over_an_existing_file_is_refused``
holds that a build is all-or-nothing -- and a search concurrent with a rebuild is
not protected. ADR-0022 records both under Still owed and ADR-0018 records NFR-4
as undischarged; this docstring was the third statement and the only one that
claimed it worked. The blue/green work in Milestone 6 is what makes it true.

**Corrected 2026-09-01 (#140 member 1): that blue/green work landed, so the
sentence above has an answer and this docstring is stale rather than wrong.**
ADR-0024 points 6 and 7 shipped it -- publishing stops reaping, reclaiming
becomes ``theurian index gc``, and a search holds one read connection for the
duration of a request -- and that ADR's Compliance section carries the
reconciliation across every record that states NFR-4, with the pins named:
``tests/integration/test_gc_during_a_search.py``, decision 7's acceptance module,
whose four tests include the connection-per-call counterexample;
``test_publishing_a_build_no_longer_reclaims_the_one_it_replaced``; and
``test_a_read_of_a_missing_index_creates_no_file``. **Builds are still not
incremental**, and the test named above still holds that. What remains owed is a
*test* rather than a mechanism: nothing in the suite issues a query while a build
is running, which ADR-0007's Still-owed bullet records and which no open issue
owns.

External model calls outside write transactions (NFR-8) is a rule with nothing to
apply it to yet: no provider that calls a model has an implementation. It is
recorded here as a constraint on the Milestone 6 summarisation path, not as
something being observed.
"""
