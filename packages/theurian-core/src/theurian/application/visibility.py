"""Who may be shown a retrieved row, and who decides (FR-R1, FR-R5, SEC-13).

Split out of :mod:`theurian.application.retrieval_service` for the reason
:mod:`theurian.infrastructure.sqlite.index_query` was split out of its store:
that file had grown past the size at which it can be read in one sitting, and
this is a seam rather than a cut. What lives here is one question — *may this
chunk be shown to this caller at all* — asked once, in one place, by everything
that ranks.

The question belongs beside the ranking rather than after it. FR-R1 says filter
before ranking, and the index can only filter on the status it recorded when it
was built; the half of the filter that knows what is approved **now** is this
one. Asking it late is what made a withheld document able to occupy a candidate
slot, and every number computed from those slots — ``count``, ``usedTokens``,
``fusedScore``, ``droppedForBudget`` — move with it.

Nothing here opens a connection or names an adapter. It takes a
:class:`~theurian.domain.ports.canonical_store.CanonicalReadSession` whose
lifetime belongs to the caller, because the whole point of one session per
request is that two rows in one answer cannot be judged against two states.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, final, runtime_checkable

from theurian.domain.context import RequestContext
from theurian.domain.enums import may_surface
from theurian.domain.identifiers import ItemId
from theurian.domain.knowledge import KnowledgeItem
from theurian.domain.ports.canonical_store import CanonicalReadSession
from theurian.domain.ranking import Ranked


@runtime_checkable
class Visibility(Protocol):
    """Which of a retriever's rows this caller may be shown.

    Consulted while the retrievers are being *read*, not after they have been
    fused, which is what keeps a withheld row from occupying a candidate slot,
    shifting a rank, or reaching a number the caller is told.

    A Protocol rather than a concrete collaborator so the ranking stays testable
    without a canonical database — and so a caller of
    :meth:`~theurian.application.retrieval_service.RetrievalService.search` has
    to name a visibility policy rather than inherit one by omission. There is
    deliberately no default: "everything is visible" is precisely the bug that
    was found four times, and a default parameter is how it would come back.
    """

    def cleared(self, ranked: Sequence[Ranked]) -> tuple[Ranked, ...]:
        """The subset of ``ranked``, in the order given, that may be shown."""
        ...


@final
class CanonicalVisibility:
    """The canonical store's answer, for one request.

    The index is never authoritative (ADR-0004). Its ``status`` column is a
    build-time snapshot and its ``revision_id`` is whichever revision was current
    when it was written, so the two checks below are the difference between a
    stale index returning *fewer* results and it returning wrong ones.

    Memoised by item for the life of one request. The retrievers overlap, one
    document contributes several chunks, and re-reading cannot change the answer
    inside a single session — so this costs one ``get_item`` per distinct
    document per request however deep the retrievers are asked to go. Measured at
    1.4 ms per hundred items, against 3.3 ms per hundred for the revision reads
    that used to happen on this path for every candidate.
    """

    def __init__(
        self,
        store: CanonicalReadSession,
        context: RequestContext,
        *,
        include_unapproved: bool,
    ) -> None:
        self._store = store
        self._context = context
        self._include_unapproved = include_unapproved
        self._items: dict[str, KnowledgeItem | None] = {}

    def cleared(self, ranked: Sequence[Ranked]) -> tuple[Ranked, ...]:
        """Every row of ``ranked`` is asked about, including once fifty have passed.

        Not short-circuited at :data:`~theurian.application.retrieval_service.CANDIDATE_DEPTH`,
        although the caller truncates there and the rest of the work is thrown
        away. Stopping early makes the number of ``get_item`` calls — and so the
        time the call takes — a function of how many rows were withheld above the
        fiftieth visible one, one row at a time, which is the same quantity every
        field in the response has been arranged not to state (SEC-13, T-17).

        **Walking the whole ranking does not make that number independent of the
        withheld count, and this docstring used to say it did.** The count is
        ``len(ranked)``: ``depth`` whatever was withheld where the caller's
        retriever truncates and the match set fills the ask — which is the shape
        the claim was read on, and there it holds — and the visible rows plus the
        withheld ones wherever it does not. On the scan below the trigram floor,
        whose statement carries no ``LIMIT``, ``ranked`` is the entire match set
        and the claim is inverted: 3,000 visible rows with 1,000 withheld below
        the fiftieth cost 4,000 calls here against the 50 a short-circuit would
        cost, in one pass either way. What totality buys where it does buy
        something is a *coarser* observable — a fifty-row staircase rather than a
        one-row count — and not less work; on the branch that never truncates it
        buys neither. T-17 carries the argument and the measurements.

        Measured at 15 us per distinct document, so walking a whole 6,000-row
        ranking costs 0.09 s against the 0.5 s scan that produced it, and 400
        documents retired after the build cost 6.047 ms against 0.163 ms with
        none — linear in what was withheld, with no threshold in it.

        Zero rows is the case worth naming: a query that matched nothing asks
        this store nothing, which is why
        :meth:`~theurian.domain.ports.canonical_store.CanonicalReadSession.__enter__`
        is required to have opened the session already.
        """
        return tuple(row for row in ranked if self._may_surface(row))

    def item(self, item_id: str) -> KnowledgeItem | None:
        """The item behind a chunk, or ``None`` if the store no longer has one.

        Public because the caller that shapes a result needs the item's *current*
        status for the payload, and it has already been paid for here. Reading it
        again from the store would be a second read that could, across a session
        boundary, disagree with the one that admitted the row.
        """
        if item_id not in self._items:
            self._items[item_id] = self._store.get_item(self._context, ItemId(item_id))
        return self._items[item_id]

    def _may_surface(self, row: Ranked) -> bool:
        item = self.item(row.item_id)
        if item is None or item.current_revision_id is None:
            return False
        # The canonical store is the authority for what is approved *now*.
        # Checked whatever `include_unapproved` says: guarding this with `not
        # include_unapproved` once let the opt-in path skip status entirely, so
        # an item retired after the build came back labelled `deprecated` — or
        # `rejected`, which is where the secret that caused the rejection lives.
        if not may_surface(item.status, include_unapproved=self._include_unapproved):
            return False
        # Likewise for *which revision* is current. Replacing a revision is how a
        # secret gets removed from approved knowledge, so serving the pinned one
        # would keep answering with the very text the team just retracted, under
        # the new revision's `approved` label.
        #
        # Compared as strings rather than by building a `RevisionId` out of index
        # data: this runs once per ranked row, and an id that failed validation
        # would raise here rather than simply fail to match.
        return item.current_revision_id.value == row.revision_id


__all__ = ["CanonicalVisibility", "Visibility"]
