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
from datetime import datetime
from typing import Protocol, final, runtime_checkable

from theurian.domain.context import RequestContext
from theurian.domain.enums import Sensitivity, may_disclose, may_surface
from theurian.domain.errors import DomainError
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

    def at_moment(self, ranked: Sequence[Ranked]) -> tuple[Ranked, ...]:
        """The subset of ``ranked`` -- already :meth:`cleared` -- valid at the
        pinned moment, or ``ranked`` unchanged when nothing is pinned.

        Deliberately a second method rather than a second condition inside
        :meth:`cleared` (#63 phase 2, CRITICAL finding in review round 1 of
        PR #112). ``cleared`` drives the depth-doubling loop in
        :meth:`~theurian.application.retrieval_service.RetrievalService._visible_ranking`:
        a retriever is asked deeper until ``CANDIDATE_DEPTH`` rows survive
        ``cleared`` or it has nothing left, so the number of retriever calls a
        request makes is observable through both count and timing (T-17,
        ``FIRST_PASS_DEPTH``). The caller freely chooses the pinned moment and
        can already read every non-withheld item's own validity window, so
        folding it into ``cleared`` would let a caller spend that knowledge to
        dial the *known* fraction of a page it excludes right up to
        ``CANDIDATE_DEPTH``'s own boundary -- at which point whether one
        further row is also excluded, because it happens to be withheld and
        not because of the pinned moment, is exactly the single-row signal
        ``FIRST_PASS_DEPTH`` exists to require fifty-one of, not one. Applied
        once instead, to the slice ``_visible_ranking``/``_dense`` already
        settled on, after which nothing asks a retriever for more -- so no
        pass count can move with it.
        """
        ...


@final
class CanonicalVisibility:
    """The canonical store's answer, for one request.

    The index is never authoritative (ADR-0004). Its ``status`` and
    ``sensitivity`` columns are build-time snapshots and its ``revision_id`` is
    whichever revision was current when it was written, so the three checks below
    are the difference between a stale index returning *fewer* results and it
    returning wrong ones.

    Memoised by item for the life of one request. The retrievers overlap, one
    document contributes several chunks, and re-reading cannot change the answer
    inside a single session — so this costs one ``get_item`` per distinct
    document per request however deep the retrievers are asked to go. Measured at
    1.4 ms per hundred items, against 3.3 ms per hundred for the revision reads
    that used to happen on this path for every candidate.

    That is the *canonical read* count, and :meth:`cleared` separates it from the
    number of times this class is asked — which is one per ranked row, and larger
    by the chunks-per-document factor. Naming them with one word is how they came
    to be reported as one number.
    """

    def __init__(
        self,
        store: CanonicalReadSession,
        context: RequestContext,
        *,
        include_unapproved: bool,
        visible_sensitivities: frozenset[Sensitivity],
        moment: datetime | None = None,
    ) -> None:
        """``moment`` is the caller's ``asOf``, or ``None`` for no pin (#63).

        ``visible_sensitivities`` is what this deployment serves (#119): the set
        the operator's declared ceiling expanded to, resolved once at startup and
        threaded down. Required, like ``include_unapproved`` and for the same
        reason -- "everything is visible" is the bug, and a default parameter is
        how it comes back. Nothing in the request reaches it: it is a property of
        the deployment, which is what makes it safe to apply inside
        :meth:`cleared` where a caller-chosen filter must not go (see below).

        Defaulted, unlike ``include_unapproved``, because ``None`` here means
        "apply no *additional* temporal restriction" rather than "everything is
        visible" -- status and current-revision identity are still checked
        unconditionally by :meth:`cleared`, whatever ``moment`` is. It is the
        FR-R1 axis `knowledge.search`'s optional ``asOf`` parameter exists to
        fill, and it stays a refinement rather than a default filter for the
        reason recorded on that parameter: a permanent filter would make
        `isWithinValidity` constant-``true`` on a fresh index and inherit a
        stale-index residual shaped like T-17a's, for a different cause, with
        no way to turn it off.

        Deliberately never read by :meth:`cleared`/:meth:`_may_surface` --
        only by :meth:`at_moment`, which every caller applies *after*
        ``cleared`` has already run. See :meth:`at_moment` for why that split
        exists.
        """
        self._store = store
        self._context = context
        self._include_unapproved = include_unapproved
        self._visible_sensitivities = visible_sensitivities
        self._moment = moment
        self._items: dict[str, KnowledgeItem | None] = {}

    def cleared(self, ranked: Sequence[Ranked]) -> tuple[Ranked, ...]:
        """Every row of ``ranked`` is asked about, including once fifty have passed.

        Not short-circuited at :data:`~theurian.application.retrieval_service.CANDIDATE_DEPTH`,
        although the caller truncates there and the rest of the work is thrown
        away. Stopping early makes the number of ``get_item`` calls — and so the
        time the call takes — a function of how many rows were withheld above the
        fiftieth visible one, one row at a time, which is the same quantity every
        field in the response has been arranged not to state (SEC-13, T-17).

        **Two counts move here and they are not the same number — this docstring
        stated one of them under the other's name, and it is the second quantity
        mix-up on this path.** Written in words that cannot be confused:

        - **``Visibility.item`` calls** are ``len(ranked)``, one per ranked row.
          All but the first per document are a ``dict`` lookup and reach no store;
        - **``CanonicalReadSession.get_item`` calls** are the *distinct item
          count* of ``ranked``, because :meth:`item` memoises on ``self._items``
          for the life of the request. This is the number a canonical store can
          observe, so this is the number T-17 is about.

        The two differ by chunking rather than marginally:
        :data:`~theurian.domain.chunking.TARGET_CHARS` is 1,000, so one document
        is several rows. Measured on 400 documents of Japanese prose at 8,410
        characters each, nine chunks apiece: 3,600 ``Visibility.item`` calls
        against **400** ``get_item`` calls. The old claim named ``len(ranked)``
        for both, which overstates the leak by the chunks-per-document factor —
        a safe direction, and still wrong in a number that feeds the issue #15
        decision.

        **Walking the whole ranking does not make the canonical read count
        independent of the withheld count, and this docstring used to say it
        did.** It is the distinct item count of ``ranked``: on the branches whose
        retriever truncates and whose match set fills the ask, ``ranked`` holds
        ``depth`` rows whatever was withheld — the shape the claim was read on,
        and there it holds — and elsewhere it holds the visible documents plus the
        withheld ones. On the scan below the trigram floor, whose statement
        carries no ``LIMIT``, ``ranked`` is the entire match set and the claim is
        inverted: 3,000 visible rows with 1,000 withheld below the fiftieth reach
        this method 4,000 times against the 50 a short-circuit would cost, in one
        pass either way, and charge the store one read per distinct document on
        either side of that. What totality buys where it does buy something is a
        *coarser* observable — a fifty-row staircase rather than a one-row count —
        and not less work; on the branch that never truncates it buys neither.
        T-17 carries the argument and the measurements.

        Measured at 15 us per distinct document, so walking a whole 6,000-row
        ranking costs 0.09 s against the 0.5 s scan that produced it, and 400
        documents retired after the build cost 6.047 ms against 0.163 ms with
        none — linear in the withheld *document* count, with no threshold in it,
        **on a published build that still holds the withdrawn rows.** That scope
        is the whole claim rather than a caveat on it: the line exists because
        the withdrawn rows are in the file the retriever ranked, and the
        withdrawal→purge trigger removes *this* term rather than reducing it.

        **On a purged build there is no line left to be linear.** Re-measured
        2026-09-01 against a real index and its purged twin (`ec0dbcd`;
        ``docs/work-logs/2026-09-01-472-purged-build-re-measurement.md``,
        F2/F1'): the stale build reproduces the shape — 10 reads at nothing
        withheld rising to 6,000 at 5,990 withheld, 0.2349 ms to 157.7126 ms,
        and 24.3 us per withheld row over the record's own 0 → 400 sweep —
        while the purged build reads **10 at every withheld count from 0 to
        5,990**, spanning 0.2339 ms to 0.2433 ms across that entire sweep. The
        24.3 us is a different machine 27 days after the 15 us above, so read
        the pair for shape and not for magnitude. What the purged column shows
        is not a shallower slope but the absence of the term.

        **Why the term is absent is branch-dependent**, and stating it as one
        mechanism was this docstring's own error, caught in review. On the scan
        below the trigram floor, which carries no ``LIMIT``, ``ranked`` holds the
        visible documents alone. On the branches that truncate, ``ranked`` holds
        ``depth`` rows whatever was withheld — before and after the purge alike —
        so there is nothing for a per-withheld-row rate to multiply either way,
        by a different mechanism. Pinned over withheld counts 0, 50 and 200 by
        ``test_a_purged_build_reads_canonical_once_per_visible_row_however_many_were_withheld``
        and over 49-52 by
        ``test_a_purged_build_stays_at_one_retriever_pass_across_the_first_pass_depth_edge``,
        both in ``tests/integration/test_purged_build_quantities.py``; the first's
        stale control asserts ``visible + withheld`` first — so a fixture that
        never held the withdrawn rows fails there before it reaches the claim.

        **What the purge does not remove is the segment residue, and the scope
        of "removes the term" is this method's own quantity.** FTS5's ``'delete'``
        tombstones the postings and nothing in the shipped purge merges them, so
        query duration on the trigram path stays monotone in the withdrawn count
        — 5.67x end to end at 5,950 withdrawn, measured in PR #498's round-one
        review and owned by
        `#499 <https://github.com/theurian/theurian/issues/499>`_ as a face of
        T-17a. The canonical-read count this docstring is about is genuinely
        term-free after a purge; the clock is not.

        Zero rows is the case worth naming: a query that matched nothing asks
        this store nothing, which is why
        :meth:`~theurian.domain.ports.canonical_store.CanonicalReadSession.__enter__`
        is required to have opened the session already.
        """
        return tuple(row for row in ranked if self._may_surface(row))

    def at_moment(self, ranked: Sequence[Ranked]) -> tuple[Ranked, ...]:
        """FR-R1's validity-window axis, applied once, after :meth:`cleared`
        has already run (#63 phase 2; see the Protocol docstring for why it is
        not folded into :meth:`cleared` itself).

        Reads ``item()``'s memo rather than the store again: every row here
        already passed ``cleared``, so its item has already been paid for and
        this adds no canonical read of its own -- only the ``ValidityPeriod.
        contains`` comparison, in Python, against a timezone-aware ``datetime``
        on both sides. That is the whole fix for the review-round-1 offset
        defect too: there is no second implementation of this comparison
        anywhere left to disagree with this one -- `mcp.search._scan` calls
        this same method's twin logic (`item.validity.contains`) directly, not
        a SQL clause that compared timestamps as text.
        """
        moment = self._moment
        if moment is None:
            return tuple(ranked)
        surfaced: list[Ranked] = []
        for row in ranked:
            item = self.item(row.item_id)
            if item is not None and item.validity.contains(moment):
                surfaced.append(row)
        return tuple(surfaced)

    def item(self, item_id: str) -> KnowledgeItem | None:
        """The item behind a chunk, or ``None`` if there is nothing to ask about.

        Public because the caller that shapes a result needs the item's *current*
        status for the payload, and it has already been paid for here. Reading it
        again from the store would be a second read that could, across a session
        boundary, disagree with the one that admitted the row.

        ``None`` covers two cases the caller treats identically, because they are
        identical to it: the store no longer holds the item, and the row does not
        name one an ``ItemId`` can be built out of.
        """
        if item_id not in self._items:
            self._items[item_id] = self._lookup(item_id)
        return self._items[item_id]

    def _lookup(self, item_id: str) -> KnowledgeItem | None:
        """One canonical read, or ``None`` if the id cannot survive validation.

        **``item_id`` is index data, and the argument for treating it as such was
        already written below — for the *other* id on the same row.**
        :meth:`_may_surface` compares ``revision_id`` as a string precisely
        because "an id that failed validation would raise here rather than simply
        fail to match", and this method then built an ``ItemId`` out of the field
        two lines above it. Measured through the real ``knowledge.search`` against
        an index with 1 to 40 random bytes corrupted past the first page:
        ``ItemId must be lowercase dot-separated kebab-case segments, got
        'architecture.auth-poli\\x06y'`` reached the agent as a bare tool failure
        in 3 of 400 fixtures, naming no remedy — an ``InvalidIdentifierError`` is
        a ``DomainError``, so ``hybrid_answer``'s ``IndexBuildError`` handler does
        not see it. ``UPDATE chunks SET item_id = ''`` and an id past 200
        characters reach it too; ``revision_id = 'nope'`` answers ``count: 0``
        and raises nothing, which is what this side now does as well.

        Validation is kept rather than dropped, and its refusal is spent the way
        the comparison below spends a mismatch: a row naming an id the domain
        will not accept names no item, so it is withheld. Failing towards *fewer*
        results is the only direction available to a derived, unsigned file
        (ADR-0004, SEC-7) — the alternative was to hand an unvalidated string to
        the canonical store.

        ``DomainError`` rather than ``InvalidIdentifierError``, because what
        matters is that the domain refused index data, not which rule it refused
        it under. An enumeration one class narrower than the truth is what this
        finding is a member of.
        """
        try:
            validated = ItemId(item_id)
        except DomainError:
            return None
        return self._store.get_item(self._context, validated)

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
        # The deployment's sensitivity ceiling, checked here beside status and for
        # the same reason (#119): the *item* carries the level, so the index's
        # build-time copy of it is a snapshot, and an item reclassified upward
        # after the build would otherwise be served on the strength of what it used
        # to be. This is the gate that closes that window before the index-side
        # exclusion exists to make it unnecessary.
        #
        # Inside `cleared`, never in `at_moment`, and the two placements are not
        # interchangeable. `cleared` is what the depth loop's exit condition counts
        # (`len(cleared) >= CANDIDATE_DEPTH` in `RetrievalService._visible_ranking`);
        # `at_moment` runs only after that loop has stopped asking retrievers, over
        # the whole cleared set and *before* the `[:CANDIDATE_DEPTH]` cut, not after
        # it (`retrieval_service.py`, reordered by the HIGH in review round 2 of PR
        # #112). So a withholding folded into `at_moment` would let a withheld row
        # count toward `CANDIDATE_DEPTH` and occupy a candidate slot the loop's exit
        # condition tallied -- displacing a visible row the loop then never digs
        # deeper to reach -- the displacement defect SEC-13 was reopened by twice,
        # where `count`, `usedTokens` and every rank move with a document the caller
        # may not read. What makes `cleared` the *safe*
        # place for this one, when `self._moment` is deliberately excluded from
        # it, is that no request parameter reaches this set: `moment` is chosen
        # freely by the caller, so folding it in here would hand them a dial with
        # which to tune the excluded fraction up to the depth loop's boundary and
        # read off a single withheld row (CRITICAL, review round 1 of PR #112).
        # A deployment ceiling is fixed for the process and offers no such dial.
        if not may_disclose(item.sensitivity, visible=self._visible_sensitivities):
            return False
        # Deliberately no validity-window check here. `self._moment` (#63
        # phase 2) is applied by `at_moment`, once, after the depth loop in
        # `RetrievalService._visible_ranking` has already stopped asking
        # retrievers for more -- never here, where it would join the count
        # that loop's exit condition watches. See `at_moment` and the CRITICAL
        # finding recorded in review round 1 of PR #112: a caller freely
        # chooses `self._moment` and can already read every non-withheld
        # item's own validity window, so checking it here would let that
        # caller spend that knowledge to dial the *known* fraction of a page
        # this method excludes right up to the depth loop's own boundary,
        # reviving the single-withheld-row timing oracle `FIRST_PASS_DEPTH`
        # exists to require fifty-one rows of, not one.
        #
        # Likewise for *which revision* is current. Replacing a revision is how a
        # secret gets removed from approved knowledge, so serving the pinned one
        # would keep answering with the very text the team just retracted, under
        # the new revision's `approved` label.
        #
        # Compared as strings rather than by building a `RevisionId` out of index
        # data: this runs once per ranked row, and an id that failed validation
        # would raise here rather than simply fail to match.
        if item.current_revision_id.value != row.revision_id:
            return False
        # Content identity, not only revision identity (GHSA-3f65). The revision
        # check above trusts that a `revision_id` names one immutable body (INV-1),
        # and on the write path it does. But the state database is a derived,
        # unsigned, git-ignored file (ADR-0004, SEC-7): the *served* content can be
        # made to drift under an *unchanged* revision id. The title face is the
        # cheapest: a title is migration metadata that no `contentSha256` pins, so
        # editing it (and removing `.theurian/state/active.json` so `_verify_history`
        # early-returns, then re-applying) leaves the revision id and the body hash
        # both unchanged while canonical now holds a different title -- and the
        # index prepends the title to the body, so its excerpt still carries the old
        # one. A body edit that re-pins `contentSha256` is the other face. Either
        # way the revision check passes on both sides, so without this one the gate
        # cleared the stale bytes and `chunk_texts` excerpted them (a new face of
        # the derived-state-trust class, GHSA-266v). Both fields are the
        # `served_content_hash` of title-plus-body -- the exact text an excerpt is
        # cut from: `row.served_content_sha256` is what the index recorded at build
        # time, `item.current_served_content_sha256` is what canonical holds for
        # that revision now. Equal in the honest case by INV-1; unequal means the
        # served text drifted, so the row is withheld.
        #
        # `None` -- a current revision whose row the gate read could not
        # dereference -- is withheld too: a check that cannot be performed is not a
        # check that passes, and failing towards fewer results is the only
        # direction a derived file may fail in.
        #
        # In `cleared`, beside status and sensitivity, and never deferred to excerpt
        # or passage time -- for the reason spelled out above them: a row dropped
        # after the depth loop has counted it toward `CANDIDATE_DEPTH` displaces a
        # visible row the loop then never digs deeper to reach, and `count`,
        # `usedTokens` and every rank move with a document the caller may not read
        # (SEC-13, the displacement defect PR #112 reopened twice). No request
        # parameter reaches this comparison, so it is safe to apply where `moment`
        # must not.
        return (
            item.current_served_content_sha256 is not None
            and item.current_served_content_sha256.value == row.served_content_sha256
        )


__all__ = ["CanonicalVisibility", "Visibility"]
