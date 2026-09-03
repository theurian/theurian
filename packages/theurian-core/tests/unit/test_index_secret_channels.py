"""The build's two non-body scan channels, driven at the arithmetic (SEC-11, #329).

``test_index_secret_scan.py`` runs the real CLI over real corpora and is where
the control's behaviour lives. It cannot reach *these* two claims: both helpers
spend the build's remaining budget, and a corpus that filled the budget from a
single body never asks either of them for more than it has left. Building a
corpus that did would mean twenty-odd credentials spread across anchors and edges
to move one number.

So the helpers are called directly, which is what makes the ceiling the subject
rather than a consequence. ``room`` is the build's remaining budget at the moment
the helper is reached; each must treat it as a total across everything it walks,
never as an allowance per anchor field or per edge. Mutating either subtraction
to ``max_findings=room`` survived the whole suite before these rows existed
(round 2, adversarial).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from theurian.application.index_builder import _anchor_secrets, _relation_secrets
from theurian.domain.context import RequestContext
from theurian.domain.enums import RelationType
from theurian.domain.identifiers import ItemId, ProjectId
from theurian.domain.knowledge import KnowledgeRelation, SourceAnchor

pytestmark = pytest.mark.unit

PROJECT = ProjectId("demo")
CONTEXT = RequestContext(project_id=PROJECT)
ITEM = "architecture.aaa"

#: Six distinct credentials in one string, so one note or one field crowds the
#: budget on its own. ``AKIA`` plus exactly sixteen upper-case characters is the
#: one family that reports each, joined at run time so a repository-wide gitleaks
#: pass over this file finds nothing contiguous.
CROWDED = "\n".join(
    "Retired key " + "AKIA" + f"NOTE{index}".ljust(16, "Z")[:16] + "." for index in range(6)
)
CREDENTIALS_PER_STRING = 6


def _anchor(fields: int) -> SourceAnchor:
    """One anchor carrying a crowded credential run in ``fields`` of its strings.

    The optional fields are filled in the order
    :data:`~theurian.domain.knowledge.AUTHORED_ANCHOR_FIELDS` fixes, so a row
    below can say how many *findings* the anchor holds without depending on which
    field the helper reaches first.
    """
    values = dict.fromkeys(
        ("source_uri", "repository", "file_path", "external_id", "provider"), CROWDED
    )
    chosen = dict(list(values.items())[:fields])
    return SourceAnchor(
        provider=chosen.get("provider", "git"),
        source_uri=chosen.get("source_uri", "git://demo/x.md"),
        repository=chosen.get("repository"),
        file_path=chosen.get("file_path"),
        external_id=chosen.get("external_id"),
    )


class _Relations:
    """The one method :func:`_relation_secrets` reads off its session.

    A stub rather than a real store, because the subject here is the budget
    arithmetic and not the SQL: the integration file holds every claim that
    depends on what ``list_relations`` really returns, the mirroring included.
    """

    def __init__(self, relations: tuple[KnowledgeRelation, ...]) -> None:
        self._relations = relations

    def list_relations(
        self,
        context: RequestContext,  # noqa: ARG002 - the port's signature, unused by the stub
        item_id: ItemId,
    ) -> tuple[KnowledgeRelation, ...]:
        return tuple(
            relation
            for relation in self._relations
            if item_id in (relation.source_item_id, relation.target_item_id)
        )


def _edges(count: int) -> tuple[KnowledgeRelation, ...]:
    return tuple(
        KnowledgeRelation(
            project_id=PROJECT,
            source_item_id=ItemId(ITEM),
            target_item_id=ItemId(f"architecture.far-{index}"),
            relation_type=RelationType.RELATED_TO,
            note=f"edge {index}\n{CROWDED}",
            created_at=datetime(2026, 9, 3, tzinfo=UTC),
        )
        for index in range(count)
    )


@pytest.mark.parametrize("room", [1, 2, 5])
def test_one_anchors_fields_share_the_budget_they_were_handed(room: int) -> None:
    """Five crowded fields on one anchor answer ``room``, not ``room`` each.

    The subtraction inside the loop is the whole of it: without it each field is
    handed the full remaining budget and one anchor can return five times what the
    build had left, breaching the ceiling the report publishes.
    """
    anchors = (_anchor(fields=5),)
    assert len(_anchor_secrets(anchors, at=ITEM, room=100)) == 5 * CREDENTIALS_PER_STRING, (
        "the fixture no longer crowds five fields, so a per-field allowance and a "
        "shared one would answer the same number below"
    )

    findings = _anchor_secrets(anchors, at=ITEM, room=room)

    assert len(findings) == room, (
        f"one anchor returned {len(findings)} findings for a budget of {room}"
    )


@pytest.mark.parametrize("room", [1, 2, 5])
def test_the_budget_is_shared_across_anchors_as_well_as_across_their_fields(room: int) -> None:
    """Three anchors of one crowded field apiece, on the same total.

    The sibling above cannot see a subtraction that resets between anchors; this
    one cannot see one that resets between fields. Both loops carry the budget.
    """
    anchors = (_anchor(fields=1), _anchor(fields=1), _anchor(fields=1))

    findings = _anchor_secrets(anchors, at=ITEM, room=room)

    assert len(findings) == room, (
        f"three anchors returned {len(findings)} findings for a budget of {room}"
    )
    assert all(finding.channel.startswith("sourceAnchors[") for finding in findings), findings


@pytest.mark.parametrize("room", [1, 2, 5])
def test_relation_notes_share_one_budget_across_every_edge(room: int) -> None:
    """Three crowded notes answer ``room``, not ``room`` per edge.

    How many edges an item carries is the corpus's choice, so a per-edge
    allowance is no ceiling at all -- the reasoning ``_findings_in`` records for
    the accept path's channels, arriving here for the build's.
    """
    store = _Relations(_edges(3))
    visible = {ITEM, *(f"architecture.far-{index}" for index in range(3))}
    unbounded = _relation_secrets(
        store,  # type: ignore[arg-type]
        CONTEXT,
        [ITEM],
        visible=visible,
        room=100,
    )
    assert len(unbounded) == 3 * CREDENTIALS_PER_STRING, (
        f"the fixture no longer crowds three notes past every budget below: {len(unbounded)}"
    )

    findings = _relation_secrets(
        store,  # type: ignore[arg-type]
        CONTEXT,
        [ITEM],
        visible=visible,
        room=room,
    )

    assert len(findings) == room, (
        f"three notes returned {len(findings)} findings for a budget of {room}"
    )
    assert all(finding.channel.endswith(".note") for finding in findings), findings
