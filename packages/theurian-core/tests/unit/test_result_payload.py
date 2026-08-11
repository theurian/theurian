"""What a result payload reports for sensitivity, and where that value comes from.

**Written RED, ahead of the fix that makes sensitivity item-authoritative.** A
revision is immutable, so its ``metadata.sensitivity`` records the classification
in force when the revision was *written*. A ``changeSensitivity`` migration moves
the classification on the *item* without touching any revision, so a payload that
reads ``revision.metadata.sensitivity`` reports the stale label -- and sensitivity
decides who may read the content (SEC-14). ``result_payload`` already takes
``status`` as an item-authoritative parameter for exactly this reason (a
deprecation moves the item's status, not the revision's); this pins that
``sensitivity`` must travel the same way.

Until the fix lands, ``result_payload`` has no ``sensitivity`` parameter, so the
call below raises ``TypeError`` -- the API these tests describe does not exist
yet, the way ``tests/unit/test_forest_derivation.py`` was written against a
builder that did not exist.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from theurian.domain.enums import KnowledgeKind, KnowledgeStatus, Sensitivity, TrustLevel
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId
from theurian.domain.knowledge import KnowledgeRevision, RevisionMetadata, SourceAnchor
from theurian.domain.values import MARKDOWN, ValidityPeriod
from theurian.mcp.results import result_payload

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def _revision(*, sensitivity: Sensitivity) -> KnowledgeRevision:
    """A revision whose stored metadata records ``sensitivity`` at write time."""
    return KnowledgeRevision.create(
        revision_id=RevisionId("01K1REV00101234567890ABCDE"),
        item_id=ItemId("architecture.auth-policy"),
        project_id=ProjectId("demo"),
        migration_id=MigrationId("01K1MAG00101234567890ABCDE"),
        title="Auth policy",
        body="Every call carries a signed token.",
        content_type=MARKDOWN,
        metadata=RevisionMetadata(
            kind=KnowledgeKind.ARCHITECTURE,
            namespace="backend",
            status=KnowledgeStatus.APPROVED,
            trust_level=TrustLevel.REVIEWED,
            sensitivity=sensitivity,
            owner="platform-team",
        ),
        validity=ValidityPeriod(valid_from=NOW),
        author="engineer@example.com",
        created_at=NOW,
        source_anchors=(SourceAnchor(provider="git", source_uri="git://demo/a.md"),),
    )


def test_the_payload_reports_the_items_sensitivity_not_the_revisions() -> None:
    """After a reclassification, the payload's ``sensitivity`` is the item's
    current value, not the label the revision was written under.

    The revision here records ``internal`` -- the classification when it was
    authored -- while the item has since been reclassified ``restricted``. A
    payload built from ``revision.metadata.sensitivity`` would tell a caller the
    content is more widely readable than the item now says, which is a
    disclosure decision made on stale authority (SEC-14). The two values are
    deliberately different, so a payload echoing the revision fails the equality
    below rather than passing by coincidence.
    """
    revision = _revision(sensitivity=Sensitivity.INTERNAL)
    assert revision.metadata.sensitivity is Sensitivity.INTERNAL, (
        "the fixture must record a revision-time label that differs from the item's"
    )

    payload = result_payload(
        revision,
        status=KnowledgeStatus.APPROVED,
        sensitivity=Sensitivity.RESTRICTED,
        now=NOW,
    )

    assert payload["sensitivity"] == Sensitivity.RESTRICTED.value, (
        "the payload reported the revision's stale sensitivity rather than the item's "
        "current one -- the label decides who may read the content"
    )
