"""Tree identity for RAPTOR summary nodes (ADR-0008 decision 1, SEC-14, T-10, R-14).

ADR-0008 decision 1 says a node's tree is determined by the six-component scope
tuple -- ``(project, tenant, sensitivity, acl_group, namespace, status)`` as of
the Milestone 6 amendment -- so a node whose children differ in any component
cannot exist: there is no tree it could belong to. That is a structural
guarantee only if construction itself refuses the mismatch; :class:`SummaryNode`
is where that refusal lives.

:attr:`SummaryNode.children` are the DECLARED child scopes, not the children's
own summary nodes -- the invariant below guarantees only that these
declarations agree with the node's own scope. Deriving each declaration from
the scope the actual child was built with is the builder's obligation, not
something this type can check: a builder that passes ``(parent,) * n`` for
every node satisfies this type without ever consulting a real child. The
structural guarantee ADR-0008 decision 1 describes completes only once the
builder CL derives each declared scope from the child it summarises.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import final

from theurian.domain.errors import InvariantViolationError
from theurian.domain.values import ContentHash, Scope


def _differing_components(node_scope: Scope, child_scope: Scope) -> tuple[str, ...]:
    """Names of the ``Scope`` fields where ``node_scope`` and ``child_scope`` disagree.

    Named rather than the child's full repr in the raised message: a component
    name diagnoses a scope mismatch without echoing the tenant, acl_group or
    namespace value into operator-facing output.
    """
    return tuple(
        f.name
        for f in fields(node_scope)
        if getattr(node_scope, f.name) != getattr(child_scope, f.name)
    )


@final
@dataclass(frozen=True, slots=True)
class SummaryNode:
    """A node in a RAPTOR tree, identified by the scope its children share.

    Per ADR-0008 decision 1, a node's tree is the six-component scope tuple. A
    node built from children that disagree on any component -- project, tenant,
    sensitivity, acl_group, namespace, or status -- has no tree to belong to, so
    construction refuses it rather than producing a node an isolation check would
    later have to catch.

    ``@final``: a subclass overriding ``__post_init__`` could mint a node whose
    children were never checked against its scope, which would defeat the
    guarantee above without touching this file.
    """

    scope: Scope
    children: tuple[Scope, ...]

    def __post_init__(self) -> None:
        # Frozen freezes the binding, not what it points at: a list handed to
        # the constructor is not automatically this dataclass's own storage, so
        # a caller mutating that list afterward would mutate a node it was told
        # is immutable (measured). Normalised first, before either check below,
        # so nothing here can observe the caller's original list.
        object.__setattr__(self, "children", tuple(self.children))
        if not self.children:
            raise InvariantViolationError(
                "SummaryNode must have at least one child -- a node with no "
                "children summarises nothing and has no tree to belong to"
            )
        for child_scope in self.children:
            if child_scope != self.scope:
                differing = ", ".join(_differing_components(self.scope, child_scope))
                raise InvariantViolationError(
                    f"SummaryNode child scope differs from the node's own scope "
                    f"in {differing} -- a node whose children disagree on scope "
                    "has no tree to belong to (ADR-0008 decision 1)"
                )

    @property
    def tree_id(self) -> ContentHash:
        """The tree this node belongs to.

        This is the tree-id function ADR-0008's owed ``test_raptor_scope.py``
        names: total over the six-component scope tuple, because ``Scope.key``
        joins all six and component validation keeps the encoding unambiguous
        (``values.py``), so two distinct scopes cannot produce one ``tree_id``.
        """
        return self.scope.digest
