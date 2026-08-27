"""The serve gate checks content identity, not only revision identity (GHSA-3f65).

:meth:`~theurian.application.visibility.CanonicalVisibility._may_surface` clears a
retrieved row only if its build-time served-content hash still equals the hash
canonical holds for that revision's served text -- its title-plus-body -- now. A
companion to :mod:`test_result_gate_session`, which drives the same gate along the
status and revision-id axes; this one isolates the content-identity axis a title
or body drift moves (the CLI end-to-end reproduction of both faces is
``tests/integration/test_same_revision_drift.py``).

The withholding lives inside ``cleared``, beside status and sensitivity, and never
at excerpt time -- so a drifted row is dropped *before* the candidate cut and
cannot occupy a slot the depth loop tallies. That is the SEC-13 displacement
property, and it is asserted here for the content axis exactly as
``test_result_gate_session`` asserts it for the others: a drifted row must not
change which visible rows clear, wherever it is ranked.

Pure: the store is a fake, and no file is opened.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import final

import pytest

from theurian.application.visibility import CanonicalVisibility
from theurian.domain.context import RequestContext
from theurian.domain.enums import KnowledgeKind, KnowledgeStatus, Sensitivity, TrustLevel
from theurian.domain.identifiers import ItemId, ProjectId, RevisionId
from theurian.domain.knowledge import KnowledgeItem, KnowledgeRevision
from theurian.domain.ranking import Ranked
from theurian.domain.values import ContentHash, ValidityPeriod

pytestmark = pytest.mark.unit

PROJECT = ProjectId("demo")
CONTEXT = RequestContext(project_id=PROJECT)
EVERY_SENSITIVITY = frozenset(Sensitivity)
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _ulid(number: int) -> str:
    return f"01K1A{number:021d}"


def _row(number: int) -> Ranked:
    """A retriever row whose build-time content hash is derived from its revision.

    The honest case: the hash the index recorded matches the one canonical still
    holds, because the same revision names one immutable body (INV-1).
    """
    revision = _ulid(number)
    return Ranked(
        chunk_id=f"{revision}#0",
        item_id=f"architecture.a{number:04d}",
        revision_id=revision,
        served_content_sha256=ContentHash.of_text(revision).value,
    )


def _canonical_hash(row: Ranked, *, drifted: bool) -> ContentHash | None:
    """What canonical holds for ``row``'s revision.

    ``drifted`` is the attack's end state: the served text (a title or a body)
    changed under the same revision id, so canonical's served hash no longer equals
    the row's build-time one. ``None`` models a current pointer the gate read could
    not dereference.
    """
    if drifted:
        return ContentHash.of_text(f"drifted:{row.revision_id}")
    return ContentHash(row.served_content_sha256)


@final
class _Session:
    """A canonical read session returning one approved, current item per known row.

    Every axis but content identity is held equal to the row's -- approved status,
    the row's own revision id, a wide-open sensitivity -- so a row that fails to
    clear here fails for the content hash and nothing else. ``drifted`` and
    ``hashless`` name the item ids whose current-revision hash disagrees with the
    row, or is absent; every other known row is honest. ``reads`` counts the
    ``get_item`` calls, so a test can show the gate walked the whole ranking
    rather than stopping at a drifted row.
    """

    def __init__(
        self,
        rows: Sequence[Ranked],
        *,
        drifted: frozenset[str] = frozenset(),
        hashless: frozenset[str] = frozenset(),
    ) -> None:
        self._rows = {row.item_id: row for row in rows}
        self._drifted = drifted
        self._hashless = hashless
        self.reads = 0

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *details: object) -> None:
        return None

    def list_items(self, context: RequestContext) -> tuple[KnowledgeItem, ...]:
        raise NotImplementedError  # pragma: no cover - CanonicalVisibility never lists

    def get_item(
        self,
        context: RequestContext,  # noqa: ARG002 - project-blind fake
        item_id: ItemId,
    ) -> KnowledgeItem | None:
        self.reads += 1
        row = self._rows.get(item_id.value)
        if row is None:
            return None
        content = (
            None
            if item_id.value in self._hashless
            else _canonical_hash(row, drifted=item_id.value in self._drifted)
        )
        return KnowledgeItem(
            item_id=item_id,
            project_id=PROJECT,
            namespace="architecture",
            kind=KnowledgeKind.ARCHITECTURE,
            status=KnowledgeStatus.APPROVED,
            current_revision_id=RevisionId(row.revision_id),
            owner="platform-team",
            trust_level=TrustLevel.REVIEWED,
            sensitivity=Sensitivity.INTERNAL,
            validity=ValidityPeriod(valid_from=NOW),
            current_served_content_sha256=content,
        )

    def get_item_exact(self, context: RequestContext, item_id: ItemId) -> KnowledgeItem | None:
        return self.get_item(context, item_id)

    def get_revision(
        self, context: RequestContext, revision_id: RevisionId
    ) -> KnowledgeRevision | None:
        raise NotImplementedError  # pragma: no cover - the gate never dereferences here


def _gate(session: _Session) -> CanonicalVisibility:
    return CanonicalVisibility(
        session, CONTEXT, include_unapproved=False, visible_sensitivities=EVERY_SENSITIVITY
    )


def test_a_row_whose_content_hash_matches_canonical_surfaces() -> None:
    """AC-2, the honest path. Everything else equal, a matching hash clears.

    The precondition for the drift test below: if an honest row did not surface,
    the withholding one would prove nothing about *why*.
    """
    row = _row(0)
    assert _gate(_Session((row,))).cleared((row,)) == (row,)


def test_a_row_whose_body_drifted_under_the_same_revision_is_withheld() -> None:
    """AC-1, the CRITICAL. The revision id still matches; only the body drifted.

    This is the whole guard in one assertion: the row's build-time content hash
    and canonical's current-revision hash disagree, so the row is withheld even
    though every other axis -- status, revision id, sensitivity -- clears. Neuter
    the content comparison in `_may_surface` and this goes green with the row
    surfaced, which is the disclosure.
    """
    row = _row(0)
    session = _Session((row,), drifted=frozenset({row.item_id}))
    assert _gate(session).cleared((row,)) == ()


def test_a_current_revision_the_gate_cannot_hash_is_withheld() -> None:
    """A pointer whose revision the read could not dereference is unverifiable.

    ``current_served_content_sha256`` is ``None`` -- a dangling current revision, or a
    read that did not join it -- and a check that cannot run is not a check that
    passes. Withheld, the only direction a derived file may fail in (ADR-0004).
    """
    row = _row(0)
    session = _Session((row,), hashless=frozenset({row.item_id}))
    assert _gate(session).cleared((row,)) == ()


#: More visible rows than a candidate cut would keep, so a gate that let a drifted
#: row occupy a slot would be visible as a *missing* visible row, not merely a
#: reordering. Mirrors ``test_result_gate_session``'s ``VISIBLE_HEAD``.
VISIBLE_HEAD = 60

#: The drifted counts the same visible answer is priced at: none, the single-row
#: grain the channel turns on, and a count well past any candidate cut.
DRIFTED_COUNTS = (0, 1, 50)


@pytest.mark.parametrize("drifted", DRIFTED_COUNTS)
@pytest.mark.parametrize("drifted_first", [True, False], ids=["drifted-first", "drifted-last"])
def test_a_drifted_row_does_not_occupy_a_candidate_slot(drifted: int, drifted_first: bool) -> None:
    """AC-3 (HARD): a drifted row is dropped in ``cleared``, so it takes no slot.

    The visible rows that clear must be exactly the honest ones, in order, however
    many drifted rows are interleaved and wherever they sit -- the placement
    invariance ``test_result_gate_session`` requires of the status axis, here for
    content drift. A drifted row that displaced a visible one would change this
    set; one that short-circuited the walk would change the read count. Neither
    moves: ``cleared`` is total, so the canonical read count is the ranking
    length, and every honest row survives.
    """
    visible = tuple(_row(number) for number in range(VISIBLE_HEAD))
    drifted_rows = tuple(_row(VISIBLE_HEAD + number) for number in range(drifted))
    drifted_ids = frozenset(row.item_id for row in drifted_rows)
    ranking = drifted_rows + visible if drifted_first else visible + drifted_rows

    session = _Session(visible + drifted_rows, drifted=drifted_ids)
    cleared = _gate(session).cleared(ranking)

    assert cleared == visible, (
        "every honest row must clear in order and no drifted row may take a slot; "
        "a difference means the content-identity check displaced or reordered a "
        "visible row instead of dropping the drifted one"
    )
    assert session.reads == len(ranking), (
        "the gate must walk the whole ranking -- a read count short of its length "
        "means a drifted row stopped `cleared` early, the short-circuit SEC-13 "
        "keeps out of this method so a withheld row cannot count toward the cut"
    )
