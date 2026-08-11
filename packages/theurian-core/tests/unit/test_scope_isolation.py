"""Scope isolation (ADR-0008, SEC-14, T-10, R-14).

The highest-severity failure this system can have is a RAPTOR summary built from
a `restricted` incident report and a `public` API guide: the restricted facts end
up inside generated text that carries the wrong label, with no anchor back to the
restricted source. These tests establish the foundation that would make that
impossible by construction rather than by remembering to check -- they prove
every distinct scope renders to a distinct key, so no RAPTOR forest could place
two different scopes in one tree. The forest that would rely on it is Milestone 6
(`infrastructure/raptor/` holds the default summariser and nothing that builds or
traverses a tree, #115); the scope-key distinguishability it needs is what exists
and is asserted here.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

import pytest

from theurian.domain.enums import KnowledgeKind, KnowledgeStatus, Sensitivity, TrustLevel
from theurian.domain.errors import DomainError
from theurian.domain.identifiers import ItemId, ProjectId
from theurian.domain.knowledge import KnowledgeItem, RevisionMetadata
from theurian.domain.values import AclGroup, Scope, TenantId, ValidityPeriod


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


VARIATIONS: dict[str, object] = {
    "project_id": ProjectId("other-service"),
    "tenant_id": TenantId("acme"),
    "sensitivity": Sensitivity.RESTRICTED,
    "acl_group": AclGroup("security-team"),
    "namespace": "operations",
    "status": KnowledgeStatus.DRAFT,
}


@pytest.mark.parametrize("field", sorted(VARIATIONS))
def test_every_component_changes_the_tree_identity(field: str) -> None:
    """All six components discriminate.

    If any one did not, content that differs only in that component could share
    a summary node -- which for `sensitivity` or `acl_group` is a disclosure,
    and for `status` (Milestone 6) is a `draft` and an `approved` child landing
    in the same summary because an `--include-unapproved` build filled the
    column without a tree boundary to stop it.
    """
    base = _scope()
    changed = _scope(**{field: VARIATIONS[field]})

    assert base.key != changed.key
    assert base.digest != changed.digest
    assert base != changed


def test_identical_scopes_produce_identical_digests() -> None:
    assert _scope().digest == _scope().digest


def test_all_scope_pairs_are_distinguishable() -> None:
    """Exhaustive over the component combinations, not just one-at-a-time.

    Two values per component over six components is 64 combinations (Milestone
    6 amendment to ADR-0008 decision 1, adding `status` as the sixth). This is
    the security argument ADR-0008 is accepted on: a RAPTOR forest that reads
    tree identity from `Scope.digest` cannot place two distinguishable scopes
    in one tree, because no two of these 64 collide.
    """
    scopes = [
        Scope(
            project_id=project,
            tenant_id=tenant,
            sensitivity=sensitivity,
            acl_group=acl,
            namespace=namespace,
            status=status,
        )
        for project, tenant, sensitivity, acl, namespace, status in itertools.product(
            (ProjectId("a"), ProjectId("b")),
            (TenantId("t1"), TenantId("t2")),
            (Sensitivity.PUBLIC, Sensitivity.RESTRICTED),
            (AclGroup("g1"), AclGroup("g2")),
            ("ns1", "ns2"),
            (KnowledgeStatus.APPROVED, KnowledgeStatus.DRAFT),
        )
    ]
    assert len(scopes) == 64, "the product itself drifted off six components"

    digests = {scope.digest.value for scope in scopes}
    assert len(digests) == len(scopes), "two distinct scopes collided onto one tree"


def test_scope_key_components_cannot_collide_by_concatenation() -> None:
    """Refusing the separator is what makes this collision impossible, not
    merely untested.

    Before component construction rejected control characters, this exact
    pair rendered identical keys: ``acl_group="a"`` + ``namespace="b\\x1fc"``
    and ``acl_group="a\\x1fb"`` + ``namespace="c"`` both produced
    ``backend-service\\x1flocal\\x1finternal\\x1fa\\x1fb\\x1fc\\x1fapproved``
    (measured -- ``Scope.key``'s docstring claims the separator "cannot occur
    in any component", and this pair, built with the real separator rather
    than a stand-in like ``|``, was the counterexample). Comparing ``.key``
    after construction is not the right shape for this test: construction
    itself now refuses BUILDING either half of this exact pair, which is what
    makes the collision impossible rather than merely untested, so that is
    what this test asserts.
    """
    with pytest.raises(DomainError, match="control"):
        _scope(acl_group=AclGroup("a"), namespace="b\x1fc")

    with pytest.raises(DomainError, match="control"):
        _scope(acl_group=AclGroup("a\x1fb"), namespace="c")


def test_unit_separator_is_used_as_the_delimiter() -> None:
    assert "\x1f" in _scope().key


def test_an_acl_group_containing_the_separator_is_rejected() -> None:
    """``Scope.key`` trusts that no component can carry its own delimiter.

    An ``acl_group`` that can embed ``\\x1f`` defeats that trust directly: it
    can borrow characters from the ``namespace`` slot that follows it in the
    key, producing the exact concatenation collision
    ``test_scope_key_components_cannot_collide_by_concatenation`` demonstrates.
    Rejecting the separator at construction is what makes that collision
    impossible rather than merely untested.
    """
    with pytest.raises(DomainError, match="control"):
        AclGroup("g\x1fns")


def test_a_tenant_id_containing_the_separator_is_rejected() -> None:
    """Same hazard as the acl_group case, one slot to the left in the key."""
    with pytest.raises(DomainError, match="control"):
        TenantId("t\x1fx")


def test_a_namespace_containing_the_separator_is_rejected() -> None:
    """``namespace`` sits directly before ``status`` in the key; left
    unrejected, it could borrow characters from the status slot the same way
    ``acl_group`` can borrow from ``namespace``.
    """
    with pytest.raises(DomainError, match="control"):
        _scope(namespace="a\x1fb")


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


def test_revision_metadata_scope_for_reports_its_own_status() -> None:
    """``RevisionMetadata.scope_for`` must reflect the metadata it was built
    from, not a fixed status.

    A hardcoded ``APPROVED`` would place every revision -- draft or approved --
    in the same summary tree, which is precisely the ``--include-unapproved``
    mixing the Milestone 6 amendment to ADR-0008 decision 1 exists to prevent.
    """
    metadata = RevisionMetadata(
        kind=KnowledgeKind.DECISION,
        namespace="architecture",
        status=KnowledgeStatus.DRAFT,
        trust_level=TrustLevel.REVIEWED,
        sensitivity=Sensitivity.INTERNAL,
        owner="author",
    )

    scope = metadata.scope_for(ProjectId("backend-service"))

    assert scope.status == KnowledgeStatus.DRAFT


def test_knowledge_item_scope_tracks_a_status_change() -> None:
    """``KnowledgeItem.scope`` must move with the item's status, not freeze at
    construction time.

    A hardcoded ``APPROVED`` would pass for a freshly approved item and only
    show itself once the item is moved back to ``draft`` -- exactly the
    construction site this test exercises through ``with_status``.
    """
    item = KnowledgeItem(
        item_id=ItemId("architecture.auth-policy"),
        project_id=ProjectId("backend-service"),
        namespace="architecture",
        kind=KnowledgeKind.DECISION,
        status=KnowledgeStatus.APPROVED,
        current_revision_id=None,
        owner="author",
        trust_level=TrustLevel.REVIEWED,
        sensitivity=Sensitivity.INTERNAL,
        validity=ValidityPeriod(valid_from=datetime(2026, 1, 1, tzinfo=UTC)),
    )
    assert item.scope.status == KnowledgeStatus.APPROVED

    draft_item = item.with_status(KnowledgeStatus.DRAFT)

    assert draft_item.scope.status == KnowledgeStatus.DRAFT
