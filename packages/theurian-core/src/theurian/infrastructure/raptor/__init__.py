"""RAPTOR forest construction. **Empty: nothing here is built (Milestone 6).**

The design, which is ADR-0008's and is not yet code: a forest of trees scoped by
``(project, tenant, sensitivity, acl_group, namespace)``, never one tree. A node
whose children differed in any component would have no tree to belong to, which
is what would make cross-sensitivity summary leakage structurally impossible
rather than policy-checked. Rebuilds would be incremental and publish through an
atomic swap, so that a partial build is never searchable.

Every sentence above was in the present tense until this pass, in a package with
no node type, no tree-id function, no ``summary_prompt_hash`` column in
``index_schema.py``, and no ``SummarizationProvider`` implementation to summarise
with. "Structurally impossible" is the kind of claim that is read once and relied
on afterwards, so it says "would" here until something enforces it; ADR-0008's
Compliance section carried the same reading and now lists its four tests as owed.

The scope tuple itself is real and tested: ``Scope``, ``TenantId``, ``AclGroup``
and ``Sensitivity`` are domain values, and
``tests/unit/test_scope_isolation.py::test_all_scope_pairs_are_distinguishable``
is exhaustive over the 32 component combinations. What is missing is downstream:
``tenant`` and ``acl_group`` have no column in the index, and the scope filter
that does exist covers project and status only (#63). A forest scoped by a tuple
the store cannot express is the first thing Milestone 6 has to resolve.
"""
