"""RAPTOR summarization adapters. **The forest is built one layer up.**

What lives here is the ``SummarizationProvider`` side of ADR-0008 decision 7 and
nothing else: ``extractive.py`` implements it deterministically, offline, and by
selecting sentences rather than generating them, so it reads nothing beyond the
``texts`` and ``max_tokens`` of the call in progress (ADR-0008 decision 6's
Milestone 6 amendment).

**The builder is `application/forest_builder.py`, and that is a layering
consequence rather than a filing choice.** `application/index_builder.py` is
where the forest pass has to mount, and
``tests/unit/test_layering.py::test_application_does_not_import_infrastructure``
walks the real import graph -- so a builder in this package could not be called
from the one place that must call it. ADR-0008 decision 7 puts *summarization*
behind a port and ``docs/architecture/raptor.md`` says the hierarchy itself has
none, which leaves the builder as application policy over a port that already
exists.

The design ADR-0008 describes is now code rather than prose. A forest of trees
scoped by ``(project, tenant, sensitivity, acl_group, namespace, status)``,
never one tree: a node whose children differed in any component would have no
tree to belong to, which is what makes cross-sensitivity summary leakage
structurally impossible rather than policy-checked.
``theurian.domain.raptor.SummaryNode`` refuses to be built from children whose
scope disagrees with its own; ``IndexableNode`` refuses one whose declared
children do not stand one per source, which is the half ``SummaryNode`` cannot
see; and the builder derives each declaration from the child it summarises.
``tests/unit/test_scope_isolation.py::test_all_scope_pairs_are_distinguishable``
is exhaustive over the 64 component combinations, and
``tests/integration/test_forest_builder.py`` holds the isolation over a forest a
build actually wrote -- every leaf chunk a node stands on, reached transitively,
agreeing on all six.

**The storage is real too, as of index schema v4** (ADR-0008 decision 5's
Milestone 6 amendment). ``index_schema.py`` declares ``nodes``, carrying decision
5's fourteen provenance columns -- ``tree_id`` and ``summary_prompt_hash`` among
them -- plus ``project_id``, ``sensitivity`` and ``status`` to filter on;
``node_derivation`` for the provenance edges, each naming exactly one of a source
chunk or a source node; and ``nodes_fts``, a separate external-content FTS5 table
so that a summary's text cannot move a leaf chunk's BM25 score. ``index_purge``
walks those edges transitively and ``_verify`` refuses to publish a build holding
an unprovenanced node or an edge whose source is gone -- a traversal that now
meets forests a builder shaped rather than only fixtures written in raw SQL.

**What is still absent, so that nothing here reads as done.** No traversal reads
a node back at query time: ``search_lexical``, ``search_substring`` and
``search_dense`` all name ``chunks``, so a built forest is written, purged and
never retrieved from. Rebuilds are whole rather than incremental -- ``index
build --raptor`` derives every node from every chunk it just wrote -- so the
"incremental, published through an atomic swap" half of ADR-0008 is the swap
only, which the build already had.

Two gaps the tables do not close, and only the second is this package's.
``tenant`` and ``acl_group`` have no column anywhere in the index: ``tree_id``
encodes the whole six-component tuple, so a node's tree is expressible, but no
predicate can filter on those two axes. Sensitivity was deferred with them to
#119 and is no longer: ``nodes.sensitivity`` is a column and ``_node_scope``
filters the node match on it (phase 4). And a node read is enforced *separately*
from a chunk read: ``SqliteIndexStore._scope`` names ``chunks`` in its clauses, so
``_node_scope`` spells the same three predicates again over ``nodes`` -- two
enforcement points that agree rather than one, which is what a per-table clause
list costs. ADR-0008's Compliance section owes exactly that distinction, with the
mutation check that establishes it rather than assuming it.
"""
