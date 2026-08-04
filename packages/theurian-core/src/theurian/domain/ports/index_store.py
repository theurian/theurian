"""IndexStore port: one retrieval index build (FR-R2, ADR-0003, ADR-0022).

**Returns values, never rows.** A ``sqlite3.Row`` crossing into the application
layer would make the ranking pipeline depend on one adapter's cursor semantics
and column names — the exact coupling the ports rule exists to prevent, and one
that ruff's banned-import check cannot catch, because no import is involved.

That is not hypothetical: the first version of this milestone typed its
collaborators as ``Any`` and did precisely that, which also defeated strict
mypy. A port with real types is what makes the rule enforceable rather than
merely stated.

**No size lookup, deliberately.** A ``token_sizes`` method sat beside
:meth:`IndexStore.chunk_texts` until FR-R4's budget moved off the candidates and
onto the payload, where :func:`~theurian.domain.ranking.take_within_budget` now
prices what is actually sent. Pricing a retrieved chunk instead charges for text
the canonical store may still withdraw — it misstates the total *and* makes that
total move with documents the caller may not read. Anyone re-adding a size read
here is re-opening that, so the reason is recorded where the method was.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from theurian.domain.chunking import IndexableChunk
from theurian.domain.ranking import Ranked


@runtime_checkable
class IndexStore(Protocol):
    """Writes and reads one index build.

    Two of the four search methods below are not read directly by a caller.
    They are read through a depth-doubling loop —
    :meth:`~theurian.application.retrieval_service.RetrievalService._visible_ranking`
    — that asks a retriever again, deeper, whenever too few of what came back
    survived the canonical store, until enough visible rows exist or the
    retriever has nothing further to give. This port carries no separate
    signal for "nothing further"; the loop has to read it off what a method
    returns. So each method states, for itself, what a short answer and a long
    one each mean — and they do not all mean the same thing.
    :meth:`search_lexical` never returns more than ``limit``, so more would be
    meaningless; :meth:`search_substring` may return more, and when it does
    that is the whole of what it has, not merely more than was asked; and
    :meth:`search_dense` takes no ``limit`` at all, because bounding its
    output would not bound the scan it already has to run. A docstring that
    stated one rule for all three would be read as enforcement it is not —
    see each method for what actually holds, and for what an implementation
    that got it wrong would break.
    """

    def create(self, *, index_build_id: str, state_hash: str) -> None:
        """Create an empty index. Refuses to reuse an existing file.

        An index build is all-or-nothing: appending to a half-built one produces
        a file that looks complete and silently is not.
        """
        ...

    def add_chunks(self, chunks: Sequence[IndexableChunk]) -> int:
        """Insert chunks. Returns how many were written."""
        ...

    def add_embeddings(self, vectors: Sequence[tuple[str, Sequence[float]]]) -> int:
        """Store one vector per chunk. Returns how many were written."""
        ...

    def record_embedding_model(self, *, model_id: str, dimension: int) -> None:
        """Record which model produced the vectors.

        A query embedded by a different model than the corpus is comparable
        arithmetically and meaningless semantically; storing the model is what
        lets that be refused instead of scored.
        """
        ...

    def metadata(self) -> Mapping[str, object]:
        """What this build was made from and by."""
        ...

    def search_lexical(
        self,
        query: str,
        *,
        project_id: str,
        limit: int,
        include_unapproved: bool,
    ) -> tuple[Ranked, ...]:
        """Rank by term match, best first.

        Filtering happens with the match, before ranking (FR-R1). A malformed
        query returns nothing rather than raising: a search box that punishes
        punctuation is a broken search box.

        **``limit`` is a true ceiling.** An implementation must never return
        more rows than ``limit``, and returning fewer means nothing else in
        this index matches this query at all — not "nothing else in this
        batch", the whole of it. That second half is what
        :meth:`~theurian.application.retrieval_service.RetrievalService._visible_ranking`
        reads as "this retriever is exhausted" and stops asking deeper. An
        implementation that both truncates *and* returns more than ``limit`` —
        a fixed cap that ignores the argument, say — makes that read wrong
        silently: the loop has no way to tell "the whole ranking" from "more
        than I asked for, but still not all of it" except by trusting this
        contract, so a violation here costs the caller rows it never sees and
        never learns it lost. Contrast :meth:`search_substring`, where the
        opposite holds by design.
        """
        ...

    def search_substring(
        self,
        query: str,
        *,
        project_id: str,
        limit: int,
        include_unapproved: bool,
    ) -> tuple[Ranked, ...]:
        """Rank by substring match, best first.

        The retriever that makes scripts without word boundaries searchable.

        **``limit`` is a floor, not a ceiling**, and that asymmetry with
        :meth:`search_lexical` is deliberate. An adapter must return at least the
        best ``limit`` rows it has — but it may return more, and should whenever
        bounding the answer would not bound the work. A short CJK query falls
        below the trigram floor and is answered by a scan that must score every
        matching row before it can name the best of them; truncating that to
        ``limit`` would hide from the caller that the ranking was already
        complete, and the caller's response is to ask again, deeper, for another
        full scan.

        The caller therefore reads the three answers as: fewer rows than asked
        for, or *more* rows than asked for, both mean the retriever has nothing
        further; exactly ``limit`` is the only ambiguous case
        (:meth:`~theurian.application.retrieval_service.RetrievalService._visible_ranking`).

        **That reading is a requirement on every implementation, not a
        description of this one.** "More rows than asked for" has to mean the
        *entire* ranking came back — not merely more than ``limit``, all of it
        — because the loop above has no independent way to tell "the whole
        ranking" from "a fixed batch larger than what I asked for, with more
        still unseen". An implementation that caps its output above ``limit``
        without that cap being exhaustive satisfies this docstring's letter and
        breaks its promise: the loop would read the excess as proof of
        exhaustion at whatever depth it happened to ask, and hand back a
        visible ranking shorter than the one a conforming adapter would have
        given for the same query, with nothing in the response naming what
        happened. Nothing on this port, and nothing in the loop that reads it,
        checks that an implementation keeps this promise — it is a contract,
        not an enforced one.
        """
        ...

    def search_dense(
        self,
        query_vector: Sequence[float],
        *,
        project_id: str,
        include_unapproved: bool,
    ) -> tuple[Ranked, ...]:
        """Rank by vector similarity, best first. The **whole** ranking.

        Returns nothing when there are no embeddings, which degrades the search
        to lexical rather than failing it.

        No ``limit``, unlike the two above, and the asymmetry is the honest
        shape of the thing. Similarity search here is an exact scan: it scores
        every embedding in the index whatever it is asked for — measured at
        143 ms on 6,000 chunks, flat from depth 50 to depth 12,800 — so a
        ``limit`` would truncate the *output* while claiming to bound the work.
        A caller that believed it bounded the work would re-ask at greater depth
        and pay for the whole corpus again, which is exactly what the caller
        that has to fetch past withheld rows would do.

        The one method of the four not read through the depth-doubling loop at
        all: without a ``limit`` there is no "asked for" to compare a count
        against, so the ceiling/floor distinction the other two carry does not
        apply here, and the caller clears and cuts this ranking in a single
        pass instead
        (:meth:`~theurian.application.retrieval_service.RetrievalService._dense`).
        """
        ...

    def chunk_texts(self, chunk_ids: Sequence[str], *, project_id: str) -> Mapping[str, str]:
        """The matched passage per chunk, so a hit can show what matched.

        ``project_id`` is required even though every id reaching here came from
        a search that was already scoped. Chunk ids are ``<revisionId>#<n>`` and
        revision ids are published in every result, so an id is guessable rather
        than opaque -- and this is the one read on this port that turns an id
        back into text. A by-id read that trusts its ids is one refactor away
        from being the first unscoped read in the pipeline (SEC-13).
        """
        ...
