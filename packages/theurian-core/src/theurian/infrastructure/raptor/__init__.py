"""RAPTOR forest construction. **Empty: nothing here is built (Milestone 6).**

The design, which is ADR-0008's and is not yet code: a forest of trees scoped by
``(project, tenant, sensitivity, acl_group, namespace, status)``, never one
tree. A node whose children differed in any component would have no tree to
belong to, which is what would make cross-sensitivity summary leakage
structurally impossible rather than policy-checked. Rebuilds would be
incremental and publish through an atomic swap, so that a partial build is
never searchable.

Every sentence above was in the present tense until this pass, in a package with
no builder, no traversal, and -- at the time -- no ``SummarizationProvider``
implementation to summarise with either. "Structurally impossible" is the kind
of claim that is read once and relied on afterwards, so it says "would" here
until something enforces it; ADR-0008's Compliance section carried the same
reading and still lists four tests as owed, the first of them half-discharged.

**The third absence is no longer current.** ``extractive.py`` in this package
now implements ``SummarizationProvider``: deterministic, extractive, and reads
nothing beyond the ``texts`` and ``max_tokens`` a given call passes it
(ADR-0008 decision 6's Milestone 6 amendment). What it does not do is anything
with a node -- there is still no builder to call it and no traversal to place
its output, so the absences that keep this package's claims conditional are
those two, not three.

The scope tuple itself is real and tested, and so is tree identity: ``Scope``,
``TenantId``, ``AclGroup`` and ``Sensitivity`` are domain values,
``tests/unit/test_scope_isolation.py::test_all_scope_pairs_are_distinguishable``
is exhaustive over the 64 component combinations, and the domain value type
``theurian.domain.raptor.SummaryNode`` refuses to be built from children whose
scope disagrees with its own, exposing a ``tree_id`` derived from
``Scope.digest``.

**The storage is real too, as of index schema v4** — this paragraph said the
opposite until that landed (ADR-0008 decision 5's Milestone 6 amendment).
``index_schema.py`` declares ``nodes``, carrying decision 5's fourteen
provenance columns — ``tree_id`` and ``summary_prompt_hash`` among them — plus
``project_id``, ``sensitivity`` and ``status`` to filter on; ``node_derivation``
for the provenance edges, each naming exactly one of a source chunk or a source
node; and ``nodes_fts``, a separate external-content FTS5 table so that a
summary's text cannot move a leaf chunk's BM25 score. ``index_purge`` already
walks those edges transitively and ``_verify`` refuses to publish a build
holding an unprovenanced node or an edge whose source is gone, so the day a
summary node exists it inherits a purge that already carries it.

What is missing is everything between the node type and those tables. No builder
constructs a ``SummaryNode``, nothing maps one onto a ``nodes`` row in either
direction, no row is ever written, and no traversal reads one back at query
time — every test over these tables inserts its fixture with raw SQL. That is
this package's work, and it is the next change.

Two gaps the tables do not close, and only the second is this package's.
``tenant`` and ``acl_group`` have no column anywhere in the index: ``tree_id``
encodes the whole six-component tuple, so a node's tree is expressible, but no
predicate can filter on those two axes, which are deferred along with
sensitivity to #119. And nothing enforces project or status for node reads —
``SqliteIndexStore._scope`` is the single point of enforcement for chunk reads
and names ``chunks`` in its clauses, so a node traversal is a second enforcement
point unless it is built through the same one. ADR-0008's Compliance section
owes exactly that, with the mutation check that distinguishes one enforcement
point from two that happen to agree.
"""
