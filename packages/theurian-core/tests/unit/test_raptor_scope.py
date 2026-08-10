"""Tree identity for RAPTOR summary nodes (ADR-0008, SEC-14, T-10, R-14).

This is the item ADR-0008's Compliance section names as owed: "constructing a
node from children with differing scope tuples must raise, and the tree-id
function must be total over the tuple." The scope-match refusal and the
tree-id function are discharged here -- not the whole item.
``SummaryNode.__post_init__`` refuses construction -- raising
``InvariantViolationError``, the house type for construction invariants and a
``DomainError`` subclass, so ``pytest.raises(DomainError, ...)`` below catches
it without naming the concrete type -- the moment a child's six-component
``Scope`` disagrees with the node's own. ``SummaryNode.tree_id`` returns
``scope.digest``, total over the tuple because ``Scope.digest`` is. The
Compliance entry also carries the claim that no summary node's *text* spans
two sensitivities; ``SummaryNode`` holds scopes and no text, so that claim is
not discharged here, and the item stays open until decision 5's provenanced
node lands (Milestone 6).

Six components, not five: the Milestone 6 amendment to ADR-0008 decision 1 adds
``status`` to tree identity, because an ``index build --include-unapproved``
run can otherwise mix a ``draft`` and an ``approved`` child into one summary
node with no tree boundary to stop it. The exhaustive 64-combination proof
that the tuple discriminates lives in ``test_scope_isolation.py``, not here;
this file is the boundary where that guarantee is *enforced* at node
construction, restated at the node rather than at the bare tuple.
"""

from __future__ import annotations

import dataclasses

import pytest

from theurian.domain.enums import KnowledgeStatus, Sensitivity
from theurian.domain.errors import DomainError
from theurian.domain.identifiers import ProjectId
from theurian.domain.raptor import SummaryNode
from theurian.domain.values import AclGroup, Scope, TenantId


def _scope(**overrides: object) -> Scope:
    base: dict[str, object] = {
        "project_id": ProjectId("backend-service"),
        "tenant_id": TenantId("local"),
        "sensitivity": Sensitivity.INTERNAL,
        "acl_group": AclGroup("default"),
        "namespace": "architecture",
        "status": KnowledgeStatus.APPROVED,
    }
    base.update(overrides)
    return Scope(**base)  # type: ignore[arg-type]


#: One differing value per component. Mirrors test_scope_isolation.py's
#: VARIATIONS deliberately -- the two files exercise the same six axes, one at
#: the tuple, one at the node that is supposed to enforce it.
_VARIATIONS: dict[str, object] = {
    "project_id": ProjectId("other-service"),
    "tenant_id": TenantId("acme"),
    "sensitivity": Sensitivity.RESTRICTED,
    "acl_group": AclGroup("security-team"),
    "namespace": "operations",
    "status": KnowledgeStatus.DRAFT,
}


def test_a_node_accepts_children_that_all_share_its_scope() -> None:
    """The positive case a structural guarantee must not also refuse.

    A constructor that raised unconditionally would pass every mismatch test
    below for the wrong reason. This is what rules that out: uniform scope
    must construct. The node's own scope and its children's scope are built as
    two INDEPENDENT ``Scope`` objects, equal by value but not the same object,
    deliberately -- a same-object fixture would also let a guard written as
    ``child_scope is not self.scope`` (identity, not equality) pass this test
    while refusing every scope a real caller ever builds, since two calls to
    ``scope_for`` never hand back the same object twice.
    """
    node_scope = _scope()
    child_scope = _scope()
    assert node_scope is not child_scope

    node = SummaryNode(scope=node_scope, children=(child_scope, child_scope))

    assert node.scope == node_scope
    assert node.children == (child_scope, child_scope)


def test_a_node_with_no_children_is_refused() -> None:
    """A summary node summarises its children; with none, there is nothing to
    summarise and no tree for the node to belong to.

    Distinct from the scope-mismatch refusal below: an empty child tuple has no
    scope to compare, so it needs its own check and its own test. The match
    text is the fuller phrase, not just "child": the mismatch error below also
    contains the word "child" (in "child scope"), so a looser match here would
    stay green even if this test's fixture accidentally triggered that error
    instead of the empty-children one.
    """
    scope = _scope()

    with pytest.raises(DomainError, match="at least one child"):
        SummaryNode(scope=scope, children=())


@pytest.mark.parametrize("field", sorted(_VARIATIONS))
def test_a_node_refuses_a_child_that_differs_in_one_component(field: str) -> None:
    """The ADR's structural guarantee, one component at a time.

    A node whose children disagree with its own scope tuple in even one of the
    six components has no tree to belong to. For `sensitivity` or `acl_group`
    this is the cross-sensitivity disclosure ADR-0008 exists to prevent; for
    the Milestone 6 addition, `status`, this is a `draft` and an `approved`
    child landing in the same summary node. The mismatched child sits in the
    MIDDLE of three children, a matching one on each side, so this catches a
    constructor that only checks the first or the last child rather than all
    of them -- either shortcut sees only a matching scope at the position it
    happens to look at and lets the mismatch through.
    """
    node_scope = _scope()
    mismatched_child = _scope(**{field: _VARIATIONS[field]})

    with pytest.raises(DomainError, match="scope"):
        SummaryNode(
            scope=node_scope,
            children=(node_scope, mismatched_child, node_scope),
        )


def test_tree_identity_is_total_over_the_full_scope_tuple() -> None:
    """The security argument ADR-0008 is accepted on, restated at the node
    boundary rather than at the bare tuple.

    `test_scope_isolation.py::test_all_scope_pairs_are_distinguishable` is the
    exhaustive 64-combination proof that no two distinct six-component scopes
    share a digest; that test is not repeated here. What this test pins is
    narrower and specific to this file: a `SummaryNode` must expose its own
    `tree_id`, and that `tree_id` must equal the node's `scope.digest` for
    every one of the six varied scopes -- not merely reflect back whatever
    `Scope.digest` the test itself passed in. Reading `node.scope.digest`
    instead of `node.tree_id` would pass even if the node computed tree
    membership from a private encoding of the tuple that silently drifted
    from the digest (e.g. dropping a component), because `node.scope` is
    simply the object the test constructed; only `node.tree_id` exercises the
    node's own tree-identity mechanism.
    """
    for field, value in _VARIATIONS.items():
        varied_scope = _scope(**{field: value})
        varied_node = SummaryNode(scope=varied_scope, children=(varied_scope,))

        assert varied_node.tree_id == varied_scope.digest, (
            f"the node's tree_id for a scope varied in {field} did not match "
            "that scope's own digest -- see test_scope_isolation.py for the "
            "exhaustive proof this restates"
        )


def test_children_survive_the_caller_mutating_the_list_afterward() -> None:
    """``SummaryNode`` is frozen, but a list handed to its constructor is not
    automatically the dataclass's own storage.

    Unless the ``children`` field converts what it is given, a caller that
    passes a list and keeps a reference to it can mutate a node it was told is
    immutable -- silently, and without going through any of the guards this
    file tests. Constructing with a list and mutating that same list
    afterwards is the fixture that would catch it; a fresh tuple built inside
    ``__post_init__`` is unaffected by anything the caller does to its list
    afterwards.
    """
    scope = _scope()
    other_scope = _scope(**{"namespace": _VARIATIONS["namespace"]})
    mutable_children: list[Scope] = [scope, scope]

    node = SummaryNode(scope=scope, children=mutable_children)  # type: ignore[arg-type]
    mutable_children.append(other_scope)

    assert isinstance(node.children, tuple)
    assert node.children == (scope, scope)


def test_variations_cover_every_field_scope_declares() -> None:
    """A defaulted seventh field added to ``Scope`` must not silently escape
    the parametrisation above.

    Every mismatch and totality test in this file walks ``_VARIATIONS``, not
    ``dataclasses.fields(Scope)`` directly; if a new field carried a default,
    every ``Scope(...)`` call here would keep constructing without it and the
    new field would never be varied, so a mismatch on it alone would never be
    tested.
    """
    assert set(_VARIATIONS) == {field.name for field in dataclasses.fields(Scope)}


def test_a_scope_digest_is_pinned_to_its_exact_component_order_and_encoding() -> None:
    """No other test in this suite pins the join order or the hex encoding --
    they only check that components discriminate from each other, not that the
    six-component tuple is joined in this exact order.

    This hex digest was computed once, by running this exact ``Scope`` through
    ``hashlib.sha256`` of its UTF-8 ``key`` and pasted here as a literal. A
    change that swaps the order of two components (e.g. `namespace` and
    `status`) would leave every isolation test in `test_scope_isolation.py`
    green -- discriminability does not depend on order -- while producing a
    digest a RAPTOR store built before the swap can no longer look up.
    """
    scope = Scope(
        project_id=ProjectId("backend-service"),
        tenant_id=TenantId("local"),
        sensitivity=Sensitivity.INTERNAL,
        acl_group=AclGroup("default"),
        namespace="architecture",
        status=KnowledgeStatus.APPROVED,
    )

    assert scope.digest.value == (
        "ba11c1ad6c4db1fd166a46e98dfc5455511ae1130efb0b86c5ba51a6c2270a6d"
    )
