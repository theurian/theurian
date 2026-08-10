"""Scope isolation (ADR-0008, SEC-14, T-10, R-14).

The highest-severity failure this system can have is a RAPTOR summary built from
a `restricted` incident report and a `public` API guide: the restricted facts end
up inside generated text that carries the wrong label, with no anchor back to the
restricted source. These tests establish the foundation that would make that
impossible by construction rather than by remembering to check -- they prove
every distinct scope renders to a distinct key, so no RAPTOR forest could place
two different scopes in one tree. The forest that would rely on it is Milestone 6
(`infrastructure/raptor/` is not built yet, #115); the scope-key distinguishability
it needs is what exists and is asserted here.
"""

from __future__ import annotations

import itertools

import pytest

from theurian.domain.enums import Sensitivity
from theurian.domain.errors import DomainError
from theurian.domain.identifiers import ProjectId
from theurian.domain.values import AclGroup, Scope, TenantId


def _scope(**overrides: object) -> Scope:
    base: dict[str, object] = {
        "project_id": ProjectId("backend-service"),
        "tenant_id": TenantId("local"),
        "sensitivity": Sensitivity.INTERNAL,
        "acl_group": AclGroup("default"),
        "namespace": "architecture",
    }
    base.update(overrides)
    return Scope(**base)  # type: ignore[arg-type]


VARIATIONS: dict[str, object] = {
    "project_id": ProjectId("other-service"),
    "tenant_id": TenantId("acme"),
    "sensitivity": Sensitivity.RESTRICTED,
    "acl_group": AclGroup("security-team"),
    "namespace": "operations",
}


@pytest.mark.parametrize("field", sorted(VARIATIONS))
def test_every_component_changes_the_tree_identity(field: str) -> None:
    """All five components discriminate.

    If any one did not, content that differs only in that component could share
    a summary node -- which for `sensitivity` or `acl_group` is a disclosure.
    """
    base = _scope()
    changed = _scope(**{field: VARIATIONS[field]})

    assert base.key != changed.key
    assert base.digest != changed.digest
    assert base != changed


def test_identical_scopes_produce_identical_digests() -> None:
    assert _scope().digest == _scope().digest


def test_all_scope_pairs_are_distinguishable() -> None:
    """Exhaustive over the component combinations, not just one-at-a-time."""
    scopes = [
        Scope(
            project_id=project,
            tenant_id=tenant,
            sensitivity=sensitivity,
            acl_group=acl,
            namespace=namespace,
        )
        for project, tenant, sensitivity, acl, namespace in itertools.product(
            (ProjectId("a"), ProjectId("b")),
            (TenantId("t1"), TenantId("t2")),
            (Sensitivity.PUBLIC, Sensitivity.RESTRICTED),
            (AclGroup("g1"), AclGroup("g2")),
            ("ns1", "ns2"),
        )
    ]
    digests = {scope.digest.value for scope in scopes}
    assert len(digests) == len(scopes), "two distinct scopes collided onto one tree"


def test_scope_key_components_cannot_collide_by_concatenation() -> None:
    """A naive separator would let two different scopes render identically.

    With ``|`` as a separator, namespace ``a|b`` and acl group ``a`` + namespace
    ``b`` produce the same string. The unit separator cannot appear in any
    component, so the encoding is unambiguous.
    """
    left = _scope(acl_group=AclGroup("a"), namespace="b|c")
    right = _scope(acl_group=AclGroup("a|b"), namespace="c")
    assert left.key != right.key
    assert left.digest != right.digest


def test_unit_separator_is_used_as_the_delimiter() -> None:
    assert "\x1f" in _scope().key


@pytest.mark.parametrize("component", ["tenant_id", "acl_group"])
def test_oversized_scope_components_are_rejected(component: str) -> None:
    factory = {"tenant_id": TenantId, "acl_group": AclGroup}[component]
    with pytest.raises(DomainError, match="characters"):
        factory("x" * 200)


def test_oversized_namespace_is_rejected() -> None:
    with pytest.raises(DomainError, match="namespace"):
        _scope(namespace="x" * 500)


def test_local_defaults_are_the_single_tenant_case() -> None:
    """The OSS deployment is one tenant and one ACL group; the components exist
    now because retrofitting a scope component means rebuilding every tree."""
    assert TenantId().value == "local"
    assert AclGroup().value == "default"
