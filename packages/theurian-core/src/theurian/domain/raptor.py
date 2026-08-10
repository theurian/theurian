"""Tree identity for RAPTOR summary nodes (ADR-0008 decision 1, SEC-14, T-10, R-14).

ADR-0008 decision 1 says a node's tree is determined by the six-component scope
tuple -- ``(project, tenant, sensitivity, acl_group, namespace, status)`` as of
the Milestone 6 amendment -- so a node whose children differ in any component
cannot exist: there is no tree it could belong to. That is a structural
guarantee only if construction itself refuses the mismatch; :class:`SummaryNode`
is where that refusal lives.
"""

from __future__ import annotations

from dataclasses import dataclass

from theurian.domain.errors import DomainError
from theurian.domain.values import Scope


@dataclass(frozen=True, slots=True)
class SummaryNode:
    """A node in a RAPTOR tree, identified by the scope its children share.

    Per ADR-0008 decision 1, a node's tree is the six-component scope tuple. A
    node built from children that disagree on any component -- project, tenant,
    sensitivity, acl_group, namespace, or status -- has no tree to belong to, so
    construction refuses it rather than producing a node an isolation check would
    later have to catch.
    """

    scope: Scope
    children: tuple[Scope, ...]

    def __post_init__(self) -> None:
        if not self.children:
            raise DomainError(
                "SummaryNode must have at least one child -- a node with no "
                "children summarises nothing and has no tree to belong to"
            )
        for child_scope in self.children:
            if child_scope != self.scope:
                raise DomainError(
                    f"SummaryNode child scope {child_scope!r} differs from the "
                    f"node's own scope {self.scope!r} -- a node whose children "
                    "disagree on scope has no tree to belong to (ADR-0008 "
                    "decision 1)"
                )
