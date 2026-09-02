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
from theurian.mcp.results import SAFETY, result_payload

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


def test_the_trust_triple_cannot_be_reassigned_from_anywhere_in_the_process() -> None:
    """``SAFETY`` is a read-only mapping, and that is a control rather than style.

    Every knowledge result and every served review finding spreads this one
    constant (``**SAFETY``), so the three SEC-15 labels have exactly one home. That
    is what stops a second surface from labelling differently -- and it is also
    what makes a *mutation* of it global: one ``SAFETY["executable"] = True``,
    executed anywhere in the process, unlabels every result this daemon returns,
    from any module, with no test naming the line that did it.

    ``MappingProxyType`` turns that into a ``TypeError`` at the assignment instead
    of a disclosure at the wire. Reverting it to a plain ``dict`` changes no
    published byte and no assertion about a payload's *contents*, which is exactly
    why the property needs its own pin: nothing else in the suite can tell the two
    apart.

    All three keys are asserted too. The proxy is the guard on the container; the
    values are what the guard is protecting, and a triple that drifted would be a
    correctly-immutable wrong answer.
    """
    assert dict(SAFETY) == {
        "contentClassification": "untrusted-knowledge",
        "mayContainInstructions": True,
        "executable": False,
    }

    with pytest.raises(TypeError):
        SAFETY["executable"] = True  # type: ignore[index]
    with pytest.raises(TypeError):
        SAFETY["contentClassification"] = "trusted"  # type: ignore[index]
    with pytest.raises(TypeError):
        del SAFETY["mayContainInstructions"]  # type: ignore[attr-defined]

    assert SAFETY["executable"] is False, "the triple moved despite the refusals above"

    # The spread every call site uses still works, and still produces a plain,
    # writable dict -- the proxy protects the constant, not the payload built from
    # it, which callers legitimately extend.
    spread = {**SAFETY, "itemId": "architecture.auth-policy"}
    spread["executable"] = True
    assert SAFETY["executable"] is False, (
        "writing to a payload built by spreading `SAFETY` reached the constant, so "
        "the proxy is being shared rather than copied"
    )
