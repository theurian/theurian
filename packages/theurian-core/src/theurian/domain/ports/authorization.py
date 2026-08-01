"""AuthorizationProvider port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from theurian.domain.enums import Sensitivity
from theurian.domain.identifiers import ProjectId
from theurian.domain.values import AclGroup, TenantId


@runtime_checkable
class AuthorizationProvider(Protocol):
    """Decides what a principal may do.

    Locally this is a single user holding a bearer token, and the implementation
    grants every registered project. The port exists now anyway: authorization
    checks retrofitted after the call sites exist are always incomplete, and the
    hosted deployment needs per-project ACLs, tenant boundaries, and scopes.

    Every method returns a decision. None of them raise on denial -- the caller
    decides whether a denial is an error or a filter, and retrieval needs it as a
    filter (FR-R1).
    """

    async def may_access_project(self, principal: str, project_id: ProjectId) -> bool:
        """Whether ``principal`` may read this project at all (SEC-13)."""
        ...

    async def visible_sensitivities(
        self, principal: str, project_id: ProjectId
    ) -> frozenset[Sensitivity]:
        """Sensitivity levels ``principal`` may see.

        Applied as a pre-filter before ranking, never as a post-filter: filtering
        after ranking returns fewer results than requested and leaks the
        existence of hidden content through result-count differences.
        """
        ...

    async def visible_acl_groups(
        self, principal: str, project_id: ProjectId
    ) -> frozenset[AclGroup]: ...

    async def tenant_for(self, principal: str) -> TenantId:
        """The tenant boundary for ``principal``. Always ``local`` in OSS Core."""
        ...
