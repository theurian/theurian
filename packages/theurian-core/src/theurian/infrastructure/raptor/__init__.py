"""RAPTOR forest construction.

A forest of trees scoped by ``(project, tenant, sensitivity, acl_group,
namespace)``, never one tree. A node whose children differ in any component has
no tree to belong to, which makes cross-sensitivity summary leakage structurally
impossible rather than policy-checked (ADR-0008).

Rebuilds are incremental and publish through an atomic swap. A partial build is
never searchable.
"""
