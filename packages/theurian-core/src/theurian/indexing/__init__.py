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

External model calls outside write transactions (NFR-8) is a rule with nothing to
apply it to yet: no provider that calls a model has an implementation. It is
recorded here as a constraint on the Milestone 6 summarisation path, not as
something being observed.
"""
