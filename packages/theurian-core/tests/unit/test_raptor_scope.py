"""Tree identity for RAPTOR summary nodes (ADR-0008, SEC-14, T-10, R-14).

This is the item ADR-0008's Compliance section names as owed: "constructing a
node from children with differing scope tuples must raise, and the tree-id
function must be total over the tuple." A structural guarantee with no test is
a policy check with no policy -- this file is what turns "would have no tree
to belong to" from an argument in the ADR's prose into something a broken
build fails on.

RED as of Milestone 6 CL2: ``theurian.domain.raptor`` does not exist yet
(``infrastructure/raptor/`` has a module docstring and no code, and the domain
layer has no node type at all). Every test below fails at collection with
``ModuleNotFoundError`` until the module lands. The surface asserted against --
a frozen ``SummaryNode`` taking a ``scope`` and a non-empty tuple of children's
``scope``s, refusing construction with a plain ``DomainError`` when a child's
scope disagrees with the node's own -- is proposed, not accepted. An
implementation CL may rename ``SummaryNode`` or introduce a dedicated error
type, but only together with these tests and a note explaining the rename.

Six components, not five: the Milestone 6 amendment to ADR-0008 decision 1 adds
``status`` to tree identity, because an ``index build --include-unapproved``
run can otherwise mix a ``draft`` and an ``approved`` child into one summary
node with no tree boundary to stop it. The exhaustive 64-combination proof
that the tuple discriminates lives in ``test_scope_isolation.py``; this file is
the boundary where that guarantee is supposed to be *enforced* at node
construction, not merely provable of the tuple in isolation.
"""

from __future__ import annotations

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
    below for the wrong reason. This is what rules that out: uniform scope,
    including the node's own scope repeated as a child, must construct.
    """
    scope = _scope()

    node = SummaryNode(scope=scope, children=(scope, scope))

    assert node.scope == scope
    assert node.children == (scope, scope)


def test_a_node_with_no_children_is_refused() -> None:
    """A summary node summarises its children; with none, there is nothing to
    summarise and no tree for the node to belong to.

    Distinct from the scope-mismatch refusal below: an empty child tuple has no
    scope to compare, so it needs its own check and its own test.
    """
    scope = _scope()

    with pytest.raises(DomainError, match="child"):
        SummaryNode(scope=scope, children=())


@pytest.mark.parametrize("field", sorted(_VARIATIONS))
def test_a_node_refuses_a_child_that_differs_in_one_component(field: str) -> None:
    """The ADR's structural guarantee, one component at a time.

    A node whose children disagree with its own scope tuple in even one of the
    six components has no tree to belong to. For `sensitivity` or `acl_group`
    this is the cross-sensitivity disclosure ADR-0008 exists to prevent; for
    the Milestone 6 addition, `status`, this is a `draft` and an `approved`
    child landing in the same summary node. One matching child is included
    alongside the mismatched one so this catches a constructor that only
    checks the first or the last child rather than all of them.
    """
    node_scope = _scope()
    mismatched_child = _scope(**{field: _VARIATIONS[field]})

    with pytest.raises(DomainError, match="scope"):
        SummaryNode(scope=node_scope, children=(node_scope, mismatched_child))


def test_tree_identity_is_total_over_the_full_scope_tuple() -> None:
    """The security argument ADR-0008 is accepted on, restated at the node
    boundary rather than at the bare tuple.

    `test_scope_isolation.py::test_all_scope_pairs_are_distinguishable` is the
    exhaustive 64-combination proof that no two distinct six-component scopes
    share a digest; that test is not repeated here. What this test pins is
    narrower and specific to this file: a `SummaryNode`'s tree comes from that
    same `Scope.digest` (one component changed alone changes it), so tree
    membership cannot silently drift onto a private encoding of the tuple that
    the exhaustive test never exercises.
    """
    base_scope = _scope()
    base_node = SummaryNode(scope=base_scope, children=(base_scope,))

    for field, value in _VARIATIONS.items():
        varied_scope = _scope(**{field: value})
        varied_node = SummaryNode(scope=varied_scope, children=(varied_scope,))

        assert varied_node.scope.digest != base_node.scope.digest, (
            f"{field} did not change the node's tree digest -- see "
            "test_scope_isolation.py for the exhaustive proof this restates"
        )
