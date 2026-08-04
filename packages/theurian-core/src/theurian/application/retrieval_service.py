"""Answering a query against one index build (FR-R1..R7).

Two use cases, both reads. Writing an index lives in
:mod:`theurian.application.index_builder`, which was split out when this file
reached the size at which it stops being readable in one sitting — and the cut
is the one this docstring already named, between the use case that *writes* an
index and the two that read one.

- :meth:`ResultGate.admit` opens one canonical read session, asks
  :class:`RetrievalService` for candidates *through* it, and bounds what comes
  back by the caller's ``limit`` and token budget (FR-R4);
- :meth:`RetrievalService.search` runs the retrievers and fuses them, dropping
  every row that session withholds **before** it ranks anything (FR-R1, FR-R5).

A candidate is a chunk of an index. A *result* is a chunk the canonical store has
confirmed is still current and still approved.

**The property this module exists to hold (SEC-13, T-15): for every
``limit <= MAX_RESULTS``, every published value equals what the same query would
return if the withheld documents had never been indexed at all.** Not the last
result of fifty; not a bounded residual. Equality — for ``count``,
``usedTokens``, ``droppedForBudget``, ``fusedScore``, ``foundBy``, and the
identity and order of every hit. Held by
``test_a_withheld_document_changes_nothing_a_caller_can_see``
(``tests/integration/test_mcp_tools.py``) — one query, two corpora, the whole
response compared — which goes red in twelve of its twenty parametrisations when
:meth:`RetrievalService._visible_ranking`'s loop is replaced by a single
:data:`CANDIDATE_DEPTH` fetch. It rests on one assumption this module does not
own, and that assumption is measurably false in the two retrievers that score
with ``bm25``: see *Where the equality is conditional* at the end of this
docstring.

It has to be equality because the trigram retriever matches any three-character
substring, so any quantity that moves when withheld content matches does not
merely detect: it extracts, one character per call. That was got wrong in five
different fields at once. The table records where each was *found*, over three
review rounds; it is not a record of where any of them was closed, because none
of them was closed on its own:

======================= ================================================ =====
Field                   What was computed before the gate                Found
======================= ================================================ =====
``usedTokens``          the token budget, priced on candidates               1
``count``               ``limit``, truncating candidates                     2
``fusedScore``          the RRF ranks                                        3
:data:`CANDIDATE_DEPTH` the rows *fetched* from each retriever               3
``excerpt``             ``diversify`` choosing which chunk to publish        3
======================= ================================================ =====

Three of them are numbers, which makes "move that number past the gate" look like
a fix for each in turn. It is not one: the stage computing them still ran over
withheld rows, so closing one number leaves the next. The other two are not
numbers, and that route was never open for them at all. Fifty rows were read from
each retriever before anything asked who may see them, so a withheld row took one
of the fifty, the fiftieth visible row fell off the end and every number
downstream moved with it — 442 ordinary ``knowledge.search`` calls recovered a
sixteen-character credential, at the default token budget, with no parameter
tuning. And ``diversify`` picked one chunk per document out of a ranking that
still held withheld rows, so *which paragraph* of a visible document was
published moved too; re-fusing afterwards cannot undo that, because the chunk it
discarded is gone. Measured over 20,000 random rank arrangements: chunk identity
moved 9.1% of the time, visible item order 3.4%, ``fusedScore`` 3.6%.

So the gate no longer sits after the ranking; it sits inside it, and that one
structural change closes all five faces together rather than one per round. The
retrievers are read through a :class:`Visibility`, deeper if what came back was
withheld, until fifty *visible* rows exist or the retriever is exhausted. Every
stage downstream — fusion, diversification, ``limit``, the budget — then sees
exactly the rows it would have seen had the withheld documents never been
written, so their equality is structural rather than argued field by field — and
what says so is the test named above, not this paragraph. There is nothing left
to move to the far side of the gate, because nothing crosses it.

**Where the equality is conditional (T-17a, issue #15).** The claim above is a
composition of two things, and only the first belongs to this module: that no
withheld row reaches a slot, a rank or a published number — which holds
structurally, by the paragraph above — *and* that a retriever's ranking of the
rows the caller may see does not itself depend on what else the index holds.

FTS5 falsifies the second, through two channels that differ in what an attacker
can do with them. Both are measured now; the second was recorded here as
harmless, under two successive justifications, before anyone measured it.

**The content channel — ``idf``, via ``nHit``.** ``bm25()`` derives each phrase's
``idf`` from ``nHit``, the number of rows in the index matching that phrase, so a
withheld document containing a query term changes the score of every visible row
carrying it. Because ``idf`` is per phrase, a multi-term query changes them by
*different* amounts, and visible rows reorder against each other. This channel is
query-dependent, so a probe can steer it — and it is bounded by ``tf``: a probe
term absent from visible content leaves every visible row with ``tf = 0`` for
that phrase, so nothing comes back. It confirms whether a withheld document
contains a term the caller can already read elsewhere; it cannot spell out one
they cannot.

**The order channel — ``avgdl``, and more weakly ``N``.** These are
query-independent, so no probe can make them answer a question and this channel
extracts nothing. It is also unconditional: it needs no shared vocabulary
whatever. BM25's length norm is ``k1 * (1 - b + b * D / avgdl)``, a function of
**each row's own** ``D``, so it is not a common factor across rows and moving
``avgdl`` does not preserve an order. Measured against ``sqlite3`` FTS5 with the
``unicode61`` tokenizer :mod:`theurian.infrastructure.sqlite.index_schema` uses,
on withheld rows sharing no term with the query — every phrase's ``nHit``
asserted identical in both indexes — **1,218 configurations reorder two visible
rows**, the narrowest turning a score gap of ``-0.0295`` into ``+0.0532``. A
control attributes it: at ``N = 22, avgdl = 8.73`` the order holds, at
``N = 26, avgdl = 18.46`` it reverses, and at ``N = 26, avgdl = 8.62`` — the same
withheld rows padded to the corpus mean length — it holds again. ``N`` reweights
as well, since ``idf`` moves each phrase differently when their ``nHit`` differ,
but ``N`` alone did not flip anything in that control and ``avgdl`` alone did.

So the equality is broken more widely than the content channel alone would break
it — *any* withheld content, whatever its vocabulary, can move ``fusedScore``,
the hit order and ``excerpt`` while the index is stale — while what an attacker
can read back out is the content channel and nothing more. Do not compress the
two into "collection statistics": one answers questions and the other only
shuffles, and a reader who is told the harmless-sounding half is the whole thing
concludes that a withheld document sharing no vocabulary is safe. It is not.

**Both channels are pinned by tests that assert the leak is present**, so
whoever closes the stale window meets a red suite rather than a section gone
quietly wrong: ``test_a_withheld_document_can_still_reorder_the_visible_ones``
(content) and
``test_a_withheld_document_sharing_no_vocabulary_still_reorders_the_visible_ones``
(order), both in ``tests/integration/test_retrieval_service.py``. Both go red
with ``bm25()`` replaced by a constant, the only mutation available: the channels
are SQLite's arithmetic, so what a test can hold is that the retrievers ask.

Their reach, measured rather than reasoned about:

- **Two retrievers**, the two that score with ``bm25``: ``search_lexical`` and
  the trigram lookup. Not the scan below the trigram floor, whose
  ``matched_characters`` is computed from the row's own text, and not
  ``search_dense``, whose cosine similarity is a function of one vector pair.
- **No separation is safe.** Flips were observed with the two visible rows both
  one and two chunks apart, and nothing out to forty was immune — so there is no
  "results this far apart cannot swap" qualifier to write here.
- **It reaches ``excerpt``, not only order.** ``theurian.mcp.search`` fixes
  ``per_item=1`` rather than offering it, so a reordering inside one retriever
  decides which chunk of a document is published, on every call.

Left open deliberately, and the acceptance was re-taken in review round five once
the order channel was measured: what changed is the justification and the set of
values that move, not what an attacker can read. The gate closes neither channel.
The numbers that move are computed inside SQLite from rows the query never
returns, so there is nothing for a :class:`Visibility` to intercept. What closes
them is removing the stale window — an index that no longer holds withdrawn
documents skews no statistic — and that means the read path writing to the
derived index, which is the wrong order of work before Milestone 6 settles
blue/green builds. Tracked as issue #15, and as T-17a in the threat model, which
carries the measurements and the terms of the acceptance.

Both take their collaborators by injection. The ranking they depend on lives in
:mod:`theurian.domain.ranking` and never touches a database, so the interesting
behaviour is testable without one.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, final

from theurian.application.visibility import CanonicalVisibility, Visibility
from theurian.domain.context import RequestContext
from theurian.domain.enums import KnowledgeStatus
from theurian.domain.errors import TheurianError
from theurian.domain.identifiers import ProjectId, RevisionId
from theurian.domain.knowledge import KnowledgeRevision
from theurian.domain.ports.canonical_store import CanonicalReadSession
from theurian.domain.ports.embedding import EmbeddingProvider
from theurian.domain.ports.index_store import IndexStore
from theurian.domain.ranking import (
    DENSE,
    LEXICAL,
    SUBSTRING,
    Fused,
    Ranked,
    diversify,
    estimate_tokens,
    reciprocal_rank_fusion,
    take_within_budget,
)

#: How many candidates each retriever contributes to the fusion. Generous: RRF
#: rewards a document both retrievers found, and a document the dense retriever
#: ranked 30th cannot demonstrate agreement if only 10 were asked for.
#:
#: **Counted in rows the caller may see**, which is the whole of the difference
#: between this and the fixed ``LIMIT`` it replaces. A retriever is asked for
#: this many; if the canonical store has withdrawn any of what came back, it is
#: asked again for twice as many, until fifty survive or it has nothing more to
#: give (:meth:`RetrievalService._visible_ranking`). Fifty visible rows are
#: therefore the same fifty whether or not a withheld document happens to match
#: the query.
#:
#: As a raw ``LIMIT`` it was the fourth member of the family in this module's
#: docstring, and the worst of them: it leaked at the default token budget,
#: because ``droppedForBudget`` published the size of the gate-cleared set, and
#: on a Japanese corpus the precondition was automatic — ``unicode61`` cannot
#: segment CJK, so the trigram retriever's fifty slots *are* the candidate list.
#: It also sits exactly on ``MAX_RESULTS``, so the boundary was reachable through
#: the published API without tuning anything.
CANDIDATE_DEPTH: Final = 50

#: How many raw rows the first pass reads, before anything is withheld.
#:
#: **A timing mitigation, not a relevance choice** (T-17). The number of SQL
#: round-trips a search makes is observable, and with a first pass of exactly
#: :data:`CANDIDATE_DEPTH` a *single* withheld row among the fifty forced a
#: second pass — so latency answered the question the response no longer does.
#: Measured on a 61-document Japanese corpus, 400 interleaved calls: a query
#: matching the withheld document ran 2.09 ms slower at the median, +17.8%, and a
#: single call classified correctly 91.6% of the time. That is an extraction
#: oracle of the same order as the one this milestone closed.
#:
#: Doubling the first pass moves the threshold from "one withheld row matched" to
#: "fifty did", which no probe for a single secret can reach: the same
#: measurement falls to +0.35 ms, +3.0%, 63.0%, against 62.1% for the pipeline
#: that had no depth loop at all — the residual T-17 already records, and no
#: more. It is a mitigation, not a proof: an index withholding fifty rows a
#: single query matches still pays for a second pass, and that is left recorded
#: rather than rediscovered.
#:
#: **How large the residual is depends on the corpus, and +0.35 ms is the
#: smallest one measured here.** The step is one extra pass, worth whatever a
#: pass costs: on 6,000 chunks of 1,000 CJK characters, 12.8 ms (+15%) for a
#: plain two-character noun and 14.0 ms (+2%) for the worst legal query. It was
#: +64 ms and +503 ms while the sub-trigram scan sat inside this loop.
#:
#: **Two counts, not one, on that branch.** How many times
#: ``search_substring`` is *called* is still 1 or 2 and still moves with what
#: was withheld: the exit test cannot tell a complete ranking from a truncated
#: one when the whole ranking totals exactly this constant, so 50 withheld rows
#: cost one call and 51 cost two. How many times the corpus is *scanned* in
#: SQLite is 1 whatever is withheld, because
#: :attr:`~theurian.infrastructure.sqlite.index_store.SqliteIndexStore._scan_cache`
#: memoises the answer — a mitigation for exactly that gap, deleted when
#: ``IndexStore`` states its own exhaustion (issue #16). Collapsing the two into
#: "answers in one pass whatever is withheld" is what this comment used to do,
#: and it read as a closed channel rather than a held-shut one.
#: One test per count, because one cannot hold both:
#: ``test_the_second_pass_arrives_at_fifty_withheld_rows_and_not_before``
#: (``tests/unit/test_retrieval_depth.py``) fails at *both* edges when this
#: constant stops being twice :data:`CANDIDATE_DEPTH`;
#: ``test_one_search_scans_the_corpus_once_however_many_rows_were_withheld``
#: (``tests/integration/test_scan_cache.py``) fails when the memo goes.
#:
#: **A `LIMIT` on an FTS5 query bounds the rows returned and not the index
#: walked**, which cuts both ways and is worth stating once rather than as two
#: unrelated facts. It is why doubling the *first* pass is nearly free: depth 50
#: cost 5.98 ms against depth 100 at 6.05 ms, and a lookup matching every one of
#: 6,000 chunks cost 11.78 ms at `LIMIT 100` against 13.95 ms at `LIMIT 800` — a
#: 256-fold limit for 18% more time. It is equally why a *second* pass costs a
#: whole further lookup rather than a fraction of one, which is what makes six
#: passes 43 ms against 6 ms for one: a straight multiple, not a curve.
#:
#: So the lookup's residual is small because one pass is cheap, never because the
#: `LIMIT` bounded anything. It is the same mechanism that made the sub-trigram
#: scan cost 3.06 s and differs only in the constant, and anyone reasoning about
#: whether this loop stays safe on a larger corpus has to reason from the
#: constant rather than from the `LIMIT`.
FIRST_PASS_DEPTH: Final = CANDIDATE_DEPTH * 2

#: Default context allowance when a caller states none. Roughly a page of prose —
#: enough to answer, small enough that a caller who forgot the parameter is not
#: handed their whole window back.
DEFAULT_BUDGET_TOKENS: Final = 2000


def _deeper(depth: int) -> int:
    """How far the next pass reaches, given how far this one did.

    A function rather than a line inside the loop, and its argument list is the
    point: the next depth may depend on the current depth and nothing else.
    ``depth += len(ranked) - len(cleared)`` is a small-looking edit that passes
    the whole suite and takes the *pass count* from logarithmic in what was
    withheld to linear in it. Round-trips are observable as latency, and how many
    rows were withheld is the one quantity this module is arranged not to state
    (SEC-13, T-17). Here the edit cannot be made without changing a signature.
    Doubling rather than a fixed step because a corpus is finite: a fixed step
    bounds nothing. This is the pass count only — the canonical read count inside
    a single pass is ``len(ranked)`` and already carries the withheld count where
    a retriever does not fill the ask; see :meth:`_visible_ranking` and T-17.
    """
    return depth * 2


class RetrievalError(TheurianError):
    """A retrieval request could not be honoured. Carries a remedy."""


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """One query, and which retrievers may answer it.

    Neither a token budget nor a ``limit``. Both were here, and both were spent
    on candidates that never became results: the budget charged for documents
    that were never sent, and ``limit`` let a withheld document consume a result
    slot. Both belong to :class:`ResultRequest` — and a field that cannot be set
    cannot be applied in the wrong order.

    What is left bounds the *answer's shape*, not its size:
    :data:`CANDIDATE_DEPTH` caps how many visible rows each retriever
    contributes, and ``per_item`` caps how many any one document does. Both are
    counted after the canonical store has had its say, so neither can be spent
    on a document the caller may not read.
    """

    query: str
    project_id: str
    include_unapproved: bool = False
    #: Whether the dense retriever participates.
    #:
    #: Off by default, and that is a measured decision rather than caution. The
    #: bundled embedder is a hashed character n-gram vectoriser, and against a
    #: real corpus 91% of *unrelated* natural-language questions clear the
    #: similarity floor while the lowest genuinely related query sits below the
    #: unrelated median. The distributions overlap; no threshold separates them,
    #: because the thing being measured is English surface-form overlap and not
    #: topical relevance.
    #:
    #: Left in and made opt-in rather than deleted: the code path stays
    #: exercised, and it becomes useful the day a real model is configured
    #: through the same port (ADR-0009).
    use_dense: bool = False
    #: Chunks any one item may contribute. Two lets a long document make its
    #: case twice without crowding out every other opinion.
    #:
    #: A caller that presents *one result per document* must pass 1, not collapse
    #: duplicates afterwards. The cap is applied immediately after fusion and so
    #: before `limit` reaches the resolved list: a second chunk of the same
    #: document removed later has already taken a result slot, so collapsing at
    #: the end costs recall.
    per_item: int = 2


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """A fused, diversified candidate list, and the passages behind it.

    Every entry has already cleared the :class:`Visibility` it was ranked
    through, so this is the list the caller would have got from an index that
    never held the withheld documents at all —
    ``test_a_withheld_document_changes_nothing_a_caller_can_see``
    (``tests/integration/test_mcp_tools.py``) is what says so.

    Not truncated to ``limit``, because that bound belongs to
    :class:`ResultRequest` — but nothing observable follows from which end
    truncates, and this docstring used to claim otherwise.
    :meth:`ResultGate._surfaced` cuts to ``limit`` before anything is shaped or
    priced, so the budget is never shown more: measured on twelve matching
    documents at ``maxTokens=400``, ``count + droppedForBudget`` was 3 at
    ``limit=3`` and 12 at ``limit=12``, never the eleven available.

    No ``embedding_model``. It said which model backed the ranking, and it was
    the same value for every query against one index — so it moved to
    :meth:`RetrievalService.embedding_model`, where it is answerable *without* a
    query and therefore cannot be made to vary with one.
    """

    candidates: tuple[Fused, ...]
    #: Chunk id to matched text, read from the index that produced the
    #: candidates. Fetched here rather than by the caller because the caller no
    #: longer knows which round of retrieval was the last one.
    passages: Mapping[str, str] = field(default_factory=dict)


@final
class RetrievalService:
    """Answers a query against one index build."""

    def __init__(self, index: IndexStore, embedder: EmbeddingProvider | None = None) -> None:
        self._index = index
        self._embedder = embedder

    def search(self, request: SearchRequest, visible: Visibility) -> SearchOutcome:
        """Run the retrievers, fuse, and diversify over what may be shown (FR-R2).

        ``visible`` is applied to each retriever's rows *before* they are fused,
        so fusion, diversification and everything after them see exactly the rows
        an index without the withheld documents would have offered. That is what
        makes the equality this module promises structural: there is no stage
        left that could compute a number from a row the caller may not read.

        Stops at candidates. Neither the caller's ``limit`` nor their budget
        (FR-R4) is applied here — both bound the answer, and the answer is what
        :class:`ResultGate` admits.
        """
        # search_lexical is a true ceiling (IndexStore.search_lexical): an
        # implementation must not return more rows than `depth`, so
        # `_visible_ranking`'s "more than asked" branch is dead for it in a
        # conforming adapter. Nothing checks conformance -- the port says so of
        # itself -- and no test holds the shipped adapter to it, so it is
        # measured: 400 matching chunks, limits 1/50/100/399/400/401 returning
        # 1/50/100/399/400/400 rows.
        lexical = self._visible_ranking(
            lambda depth: self._index.search_lexical(
                request.query,
                project_id=request.project_id,
                limit=depth,
                include_unapproved=request.include_unapproved,
            ),
            visible,
        )
        # search_substring is a floor, not a ceiling (IndexStore.search_substring):
        # it may return more than `depth`, and when it does that has to be its
        # entire ranking, not merely more than was asked -- the one promise
        # `_visible_ranking` cannot check and this port states as a requirement.
        # How that answer is *read* is pinned at both call sites by
        # `test_a_retriever_that_ignores_its_limit_is_read_once_however_much_is_withheld`
        # in `tests/unit/test_retrieval_depth.py`.
        substring = self._visible_ranking(
            lambda depth: self._index.search_substring(
                request.query,
                project_id=request.project_id,
                limit=depth,
                include_unapproved=request.include_unapproved,
            ),
            visible,
        )
        rankings: dict[str, Sequence[Ranked]] = {
            LEXICAL: lexical,
            SUBSTRING: substring,
            DENSE: self._dense(request, visible),
        }
        candidates = diversify(reciprocal_rank_fusion(rankings), per_item=request.per_item)

        return SearchOutcome(
            candidates=candidates,
            passages=self._index.chunk_texts(
                [candidate.chunk_id for candidate in candidates], project_id=request.project_id
            ),
        )

    @staticmethod
    def _visible_ranking(
        fetch: Callable[[int], tuple[Ranked, ...]], visible: Visibility
    ) -> tuple[Ranked, ...]:
        """One retriever's best :data:`CANDIDATE_DEPTH` rows this caller may see.

        Asks for :data:`FIRST_PASS_DEPTH`; if too few of what came back survived
        the canonical store for fifty to remain, asks deeper, until fifty do or
        the retriever has nothing more. A short ranking is therefore short because
        the corpus is, never because something the caller may not read got there
        first.

        **The exit test is ``!=`` and not ``<``, because a pass is not always
        cheap.** A retriever gives three answers and only one is ambiguous: fewer
        rows than asked for is a `LIMIT` saying "that is all there is"; *more*
        rows than asked for is a retriever that never truncated, so it has already
        handed over everything and asking again buys another full scan and no new
        rows; exactly ``depth`` might be either, so deepen. Both terminating
        answers are states rather than an attempt count wearing a loop, so the
        bound is ``log2(matches / 50)`` passes on a retriever that truncates and
        **one** on a retriever that does not.

        ``<`` alone was written for the trigram lookup, where six passes cost
        43 ms against 6 ms for one — a whole extra lookup each time, but a cheap
        one, so the multiplier did not show. It also drove
        ``SqliteIndexStore._scan_below_the_trigram_floor``, which every
        two-character CJK query reaches and where the same multiplier is applied
        to half a second (that method prices it): six passes, 3.06 s against
        0.51 s, on 6,000 chunks with a third of the corpus retired since the
        build. That branch now hands back its whole ranking, so ``!=`` exits on
        the first pass for every corpus that does not total exactly ``depth``
        rows — 0.64 s, or 0.65 s with the entire corpus retired, against 3.06 s
        for the six passes it used to cost.

        **That is the corpus read once, not the port called once, and the
        difference is the whole of what is left of T-17 here.** A ranking of
        exactly :data:`FIRST_PASS_DEPTH` rows is indistinguishable from a
        truncated one, so this loop asks again — at 50 withheld rows once, at 51
        twice. What holds that second call to no further pass over the corpus is
        ``SqliteIndexStore._scan_cache``, a memoisation for this one gap, and not
        the exit test. The residual is the *duration* face of T-17a: securing
        :data:`CANDIDATE_DEPTH` visible rows from a retriever that is not
        exhausted needs another fetch, which follows from the definition of this
        loop rather than from a defect in it, and goes away only when the index
        stops holding withdrawn rows (issue #15). T-17 in the threat model
        carries the argument and what would falsify it.

        A healthy index pays 0.07 s for that: the whole ranking crosses into
        Python and
        :meth:`~theurian.application.visibility.CanonicalVisibility.cleared` asks
        about every row of it, at 15 us per distinct document.

        **That read count is ``len(ranked)``, so it carries the withheld count on
        every branch where the retriever does not fill the ask — and this
        docstring used to claim the opposite.** What it said was that walking the
        whole ranking is deliberate, "because stopping at fifty cleared rows
        would make the *canonical* read count move with the withheld count".
        Measured, that is a property of a branch stated as a property of the
        design. Where ``fetch`` truncates and the match set fills the ask,
        ``len(ranked)`` is ``depth`` whatever was withheld, so the read count
        moves only when the pass count does — a fifty-row staircase, against the
        one-row observable a short-circuit would give; that is the lookup and the
        word index, and there the claim holds. On ``search_substring``'s scan
        branch, which carries no ``LIMIT`` at all
        (:func:`~theurian.infrastructure.sqlite.index_scan.scan_statement`), it is
        backwards: ``ranked`` is the entire match set, both arrangements carry the
        withheld count one row at a time, and the whole-ranking walk is never the
        smaller of the two — 3,000 visible rows with 1,000 withheld below the
        fiftieth cost 4,000 canonical reads against 50, in one pass either way.

        Totality is still what ships: two of the three retrievers truncate, the
        short-circuit is strictly the finer observable on those, and on the third
        it is a smaller count of the same one-row observable rather than a
        different one. What it is not is a closure, and the term it leaves is
        bounded by nothing on the scan branch: 3,000 visible rows and 5,999
        withheld stay at one pass while canonical reads go 3,000 to 8,999, about
        +90 ms against the 0.64 s a healthy scan costs. T-17 in the threat model
        carries the argument, the five conditions that would falsify it, and why
        this goes away with the index purge (issue #15) rather than with a change
        here.

        The alternative to the loop entirely, asking the canonical store up front
        which revisions are surfaceable, cost 32 ms on every query including
        healthy ones, 26 ms of it a canonical scan growing with the *corpus*
        rather than with how stale the index is: every project charged for a delta
        most do not have.

        **The "more than asked" branch trusts a promise this function cannot
        see and does not check.**
        :class:`~theurian.domain.ports.index_store.IndexStore` requires
        ``search_lexical`` to never exceed ``depth`` and ``search_substring``
        to answer with its *entire* ranking whenever it does — this exit test
        is what reads those two, different promises as "exhausted". It cannot
        verify either: ``fetch`` is a plain callable, the same shape
        whichever retriever is behind it, so telling "the whole ranking" from
        "a fixed batch larger than what I asked for, with more still unseen"
        would mean tagging each call site with a claim about the retriever it
        wraps, not merely reading what the callable returns.

        That tag was considered here and left out, not overlooked. Adding it
        would turn this loop from retriever-agnostic into retriever-aware — a
        change to what it promises, not a decoration on top of what it
        already does — and
        ``tests/unit/test_retrieval_depth.py``'s
        ``test_a_retriever_that_ignores_its_limit_is_read_once_however_much_is_withheld``
        already exercises *both* call sites in :meth:`search` with a retriever
        that overshoots ``depth``, a shape only ``search_substring``'s
        contract permits in production. Tagging the call sites would make that
        test's lexical half describe a state a conforming adapter cannot
        reach, which is a change for the suite that owns it to make, not one
        to fold in beside a docstring. So a ``search_lexical`` that both
        truncates and overshoots — the future retriever this loop cannot yet
        rule out — still exits here believing it is exhausted, silently, at
        whatever depth it happened to ask; that is the residual this
        paragraph records rather than closes.
        """
        depth = FIRST_PASS_DEPTH
        while True:
            ranked = fetch(depth)
            cleared = visible.cleared(ranked)
            if len(cleared) >= CANDIDATE_DEPTH or len(ranked) != depth:
                return cleared[:CANDIDATE_DEPTH]
            depth = _deeper(depth)

    def embedding_model(self, *, use_dense: bool) -> str:
        """Which model backs a dense ranking here, or ``""`` for none.

        Answerable without running a query, and that is the point of it being a
        method on the service rather than a field of an outcome. A caller can
        tell an n-gram-backed hybrid search from one backed by a real semantic
        model — while the value depends only on the index and on the caller's own
        parameters, never on what a query matched. A field that flipped with the
        corpus would be one more thing to watch move (SEC-13, T-15).

        Empty for every reason the dense retriever might not run: not asked for,
        no embedder configured, or an index embedded by a different model.
        """
        embedder = self._embedder
        if not use_dense or embedder is None:
            return ""
        stored = str(self._index.metadata().get("embedding_model", ""))
        # Comparable arithmetically, meaningless semantically. Refused rather
        # than scored, because the output would be confident and wrong.
        return "" if stored and stored != embedder.model_id else embedder.model_id

    def _dense(self, request: SearchRequest, visible: Visibility) -> tuple[Ranked, ...]:
        """Rank by vector similarity, or return nothing.

        Nothing is a supported answer. A missing embedder, an index built before
        embeddings existed, or a corpus embedded by a different model all reduce
        the search to lexical rather than failing it.

        **Not depth-doubled**, and the difference from the other two retrievers
        is the difference between a ``LIMIT`` that bounds work and one that
        bounds output. This retriever scores every embedding in the index
        whatever it is asked for — measured at 143 ms on 6,000 chunks, flat from
        depth 50 to depth 12,800 — so a second pass would re-score the whole
        corpus to learn nothing. It hands back its entire ranking instead, and
        the cut to fifty happens here, on the far side of ``visible``.

        **So ``visible`` is asked about the whole index here, and that count is
        ``len(ranked)`` exactly as it is in :meth:`_visible_ranking`** — one
        canonical read per distinct item in the dense ranking, withheld ones
        included, bounded by nothing because ``search_dense`` takes no limit at
        all. Measured with a fake index: 100 visible rows cost 100 reads with
        nothing withheld and 6,000 with 5,900 withheld, in one call. The memo in
        :class:`~theurian.application.visibility.CanonicalVisibility` means this
        re-reads only what the other two retrievers did not reach, and this path
        is opt-in — but it is the third member of T-17's class of rankings that
        are walked before they are cut, not an exception to it.
        """
        embedder = self._embedder
        if embedder is None or not self.embedding_model(use_dense=request.use_dense):
            return ()

        vector = asyncio.run(embedder.embed((request.query,)))[0]
        ranked = self._index.search_dense(
            vector,
            project_id=request.project_id,
            include_unapproved=request.include_unapproved,
        )
        return visible.cleared(ranked)[:CANDIDATE_DEPTH]


@dataclass(frozen=True, slots=True)
class Surfaced:
    """One candidate the canonical store has cleared for this caller.

    Handed to the shaper instead of a finished payload, so the decision to
    publish stays here and the shape of a publication stays with the tool that
    publishes it (ADR-0003). The application layer must not know that a result is
    called ``itemId`` on the wire; the wire must not decide who may see one.
    """

    candidate: Fused
    revision: KnowledgeRevision
    #: The item's status **now**, not the one the index recorded at build time.
    status: KnowledgeStatus
    #: The chunk text that matched. Empty when the index no longer holds it, in
    #: which case the shaper falls back to the head of the document.
    passage: str


#: How a cleared candidate becomes the payload the caller receives.
#:
#: Injected rather than imported, because the wire shape belongs to the MCP tool
#: surface. ``Any`` is the value type of a JSON object and stops here.
ResultShaper = Callable[[Surfaced], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class Resolved:
    """Results the caller may see, priced.

    **What this type carries, exactly.** Every entry has been cleared by a
    canonical read session, and ``used_tokens`` and ``dropped`` were computed
    from those entries and from nothing else. That is a narrower claim than the
    one this docstring used to make — it said :func:`within_budget` was one of
    "the two places a gate is applied", and :func:`within_budget` applies the
    *budget*; the gate on the ranked path is :class:`Visibility`, consulted
    inside :meth:`RetrievalService.search`, and on the fallback path it is
    :func:`~theurian.domain.enums.may_surface`, applied inside
    ``theurian.mcp.search._scan``.

    **It is not a capability token, and cannot be made one here.** A token would
    have to be unforgeable — constructible only by code that has done the
    gating — and Python offers no way to say that which a caller cannot simply
    ignore by calling the constructor. Claiming otherwise in a docstring is worse
    than not claiming it, because the next reader trusts the claim instead of the
    two call sites. So the claim is narrowed to what holds, and what the type
    does buy is kept: the three published numbers are read off one object, built
    in one of two named places, and :meth:`empty` covers the third construction
    site that used to be a bare call.
    """

    results: tuple[dict[str, Any], ...]
    #: What ``results`` cost, priced on each entry exactly as it will be sent.
    used_tokens: int
    #: Results the caller was entitled to see that did not fit the budget.
    #: Reported so "nothing else matched" is distinguishable from "your budget ran
    #: out". Safe to report precisely because everything counted here had already
    #: cleared the gate: a withheld candidate is never "dropped for budget".
    #: ``test_nothing_derived_from_the_withheld_document_is_reported``
    #: (``tests/integration/test_mcp_tools.py``) holds it at zero for a query
    #: matching withheld content and nothing else: publishing one more than was
    #: dropped turns it red, at that assertion and no other.
    #:
    #: It was the last of the four channels to close, and the one most likely to
    #: survive a partial fix, because it is derived from *set sizes* rather than
    #: from results: ``min(len(cleared), limit) - kept`` published the size of the
    #: cleared set, a number no caller ever receives, at the default budget.
    dropped: int = 0

    @classmethod
    def empty(cls) -> Resolved:
        """A response with no results, for pricing an envelope before there are any.

        A named constructor rather than ``Resolved(results=(), used_tokens=0)``
        at the call site: an empty result set is trivially gated, and saying so
        once is better than leaving a third construction site that a reader has
        to check for themselves.
        """
        return cls(results=(), used_tokens=0)


#: How :meth:`ResultGate.admit` obtains candidates.
#:
#: A source rather than a finished list, and it is handed the :class:`Visibility`
#: it must rank through. The fixed tuple this replaced was not, and that is what
#: let a withheld row take a candidate slot before the gate could see it.
#:
#: **The claim stops there, and it has twice been written larger.** Both times it
#: said the gate *cannot* be given candidates ranked without a visibility;
#: ``lambda _visible: precomputed`` is exactly that, because a closure may ignore
#: its parameter and Python offers nothing that forbids it — the same limit
#: :class:`Resolved` records about itself. Not reasoned: handed to
#: :meth:`ResultGate.admit` against a real project, a source built that way had
#: its precomputed candidate published. The signature buys that the visibility is
#: available where candidates come into existence, and that discarding it takes
#: writing a function rather than omitting one. That it is *not* discarded is held
#: by the one call site, ``theurian.mcp.search.hybrid_answer``, and its tests.
CandidateSource = Callable[[Visibility], SearchOutcome]


@dataclass(frozen=True, slots=True)
class ResultRequest:
    """Where to resolve candidates, and the two bounds that apply to the answer."""

    database: Path
    project_id: str
    include_unapproved: bool
    #: How many results the caller asked for. Applied to what survives the gate.
    limit: int
    budget_tokens: int
    #: What the response costs before a single result is added to it — the echoed
    #: query, the ids, and the block that says how the answer was produced.
    #:
    #: Subtracted from ``budget_tokens`` rather than added to the total, because
    #: the caller is charged for the whole message. Measured at 138 to 171 tokens
    #: against a fresh index, none of it counted: a caller asking for 2,000 was
    #: sent 2,030 while being told the answer cost 1,860 (FR-R4).
    reserved_tokens: int = 0

    def __post_init__(self) -> None:
        if self.limit < 1:
            # Raised here rather than clamped, because a zero limit reaching this
            # far means a caller computed it, and silently returning one result
            # would hide the arithmetic that produced it.
            msg = f"limit must be at least 1, got {self.limit}. Pass 1 or more."
            raise RetrievalError(msg)


@final
class ResultGate:
    """One canonical read session, and the answer assembled inside it.

    Opens the session, builds the :class:`Visibility` the retrievers must rank
    through, asks the source for candidates, and bounds what comes back by
    ``limit`` and the caller's budget. One session for the whole request, so
    ``snapshotId`` names one state for the whole response rather than one per
    hit — and so the visibility a row was ranked through is the same visibility
    that admitted it.

    Withheld candidates leave no trace: not a count, not a flag, not a rank, and
    not a missing result slot. Nothing here filters, because by the time a
    candidate reaches this class there is nothing left to filter — which is what
    makes that claim checkable instead of hopeful.
    """

    def __init__(
        self,
        *,
        store_factory: Callable[[Path], CanonicalReadSession],
        shape: ResultShaper,
    ) -> None:
        self._store_factory = store_factory
        self._shape = shape

    def admit(self, request: ResultRequest, source: CandidateSource) -> Resolved:
        """Rank through the canonical store, then bound the answer (FR-R4, FR-R5).

        Retrieve-through-the-gate, truncate, shape, charge — and every step after
        the first sees only documents this caller may read, so no number any of
        them produces can move because of a document they may not.
        """
        context = RequestContext(project_id=ProjectId(request.project_id))
        with self._store_factory(request.database) as store:
            visible = CanonicalVisibility(
                store, context, include_unapproved=request.include_unapproved
            )
            outcome = source(visible)
            surfaced = self._surfaced(store, context, visible, outcome, limit=request.limit)

        shaped = [self._shape(one) for one in surfaced]
        return within_budget(
            shaped,
            budget_tokens=request.budget_tokens,
            reserved_tokens=request.reserved_tokens,
        )

    def _surfaced(
        self,
        store: CanonicalReadSession,
        context: RequestContext,
        visible: CanonicalVisibility,
        outcome: SearchOutcome,
        *,
        limit: int,
    ) -> tuple[Surfaced, ...]:
        """The revisions behind the candidates that will actually be published.

        Only the first ``limit`` are read. A revision carries its body and its
        source anchors, which is the expensive read on this port, and a candidate
        ranked below the limit is never sent — the previous version fetched every
        candidate's revision and used a fifth of them.

        Truncating here is safe in a way it was not before: ``outcome.candidates``
        holds nothing withheld, so the ``limit``-th candidate is the ``limit``-th
        result, not the ``limit``-th guess at one.
        """
        surfaced: list[Surfaced] = []
        for candidate in outcome.candidates[:limit]:
            item = visible.item(candidate.item_id)
            revision = store.get_revision(context, RevisionId(candidate.revision_id))
            if item is None or revision is None:  # pragma: no cover - a foreign key holds this
                # `visible` cleared this candidate moments ago, in this session,
                # against an item whose `current_revision_id` names this revision
                # under a foreign key. Reaching here means the state database
                # disagrees with itself, which is not something to answer around:
                # a silently shorter answer would be indistinguishable from "we
                # have no such decision".
                msg = (
                    f"Item {candidate.item_id!r} names revision "
                    f"{candidate.revision_id!r}, which this project's knowledge state "
                    f"does not hold. Run `theurian migrate apply` to rebuild the state "
                    f"database from its Git-tracked migrations."
                )
                raise RetrievalError(msg)

            surfaced.append(
                Surfaced(
                    candidate=candidate,
                    revision=revision,
                    status=item.status,
                    passage=outcome.passages.get(candidate.chunk_id, ""),
                )
            )
        return tuple(surfaced)


def within_budget(
    results: Sequence[dict[str, Any]], *, budget_tokens: int, reserved_tokens: int = 0
) -> Resolved:
    """Apply FR-R4 to results, which is the only place it can honestly apply.

    One function for both answer paths, taking results rather than candidates.
    Charging a candidate is charging for something that may never be sent: the
    canonical store withdraws retired and superseded items *after* ranking, so a
    budget spent before that both misstates the total and, worse, publishes a
    number that moved because of a document the caller may not read.

    Measured before this existed, on ten documents of which three had been
    retired after the index was built: ``maxTokens=120`` returned ``count: 0``
    and ``usedTokens: 108``, the whole budget spent on the two retired documents
    that happened to rank first, while seven approved and current results sat
    behind them unreturned.

    ``reserved_tokens`` is what the response costs empty. At least one token stays
    spendable however large it is, because :func:`take_within_budget` returns at
    least one result when any exist — a caller whose budget cannot hold the
    envelope is still better served by one over-long answer they can truncate.
    """
    kept, used = take_within_budget(
        [_payload_cost(result) for result in results],
        budget_tokens=max(1, budget_tokens - reserved_tokens),
    )
    return Resolved(results=tuple(results[:kept]), used_tokens=used, dropped=len(results) - kept)


def _payload_cost(result: Mapping[str, Any]) -> int:
    """What one result will cost the caller, priced on what is actually sent.

    The whole serialised object, not the excerpt alone: provenance, trust labels,
    and source anchors travel with every hit and are a real share of a small
    budget. `estimate_tokens` errs high on top of that, which is the side to err
    on — exceeding a budget silently truncates the caller's own instructions.

    Both answer paths price with this function. The ranked path used to price a
    *chunk* instead, on the reasoning that a chunk is the unit it retrieves and
    pins, and both were said to over-estimate. Only one did. Measured on a
    ten-document project, the ranked path under-charged by a factor of four at
    every budget — `maxTokens=500` reported 486 and sent 1,953 — because the
    excerpt is capped at 280 characters while provenance, the trust triple,
    `sourceAnchors` and SAFETY are not. The smaller the documents, the larger
    that fixed overhead is as a share of the payload, and none of it was counted.
    """
    return estimate_tokens(json.dumps(result, ensure_ascii=False))
