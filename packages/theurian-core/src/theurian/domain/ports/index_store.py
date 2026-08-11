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

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from theurian.domain.chunking import ChunkScope, IndexableChunk
from theurian.domain.ranking import RetrieverPage
from theurian.domain.raptor import IndexableNode
from theurian.domain.retrieval import RaptorPathSegment

#: Re-derive the summary forest of each affected scope over a purged build's
#: surviving rows, in place, before the build is verified and published.
#:
#: Called by :func:`~theurian.infrastructure.sqlite.index_purge.purge_into` with
#: the building file's path and the scopes whose rows the purge removed. It is a
#: `Callable` and not a method on this port because it closes over the
#: application-layer forest builder and the embedder, which the infrastructure
#: purge may not name (ADR-0003); the composition root builds it (see
#: :func:`~theurian.application.withdrawal_purge.make_forest_recompute`) and it
#: rides through :meth:`IndexStore.derive_purged` as data.
ForestRecompute = Callable[[Path, Sequence[ChunkScope]], None]


@runtime_checkable
class IndexStore(Protocol):
    """Writes and reads one index build.

    **Every search method states its own exhaustion.** Each returns a
    :class:`~theurian.domain.ranking.RetrieverPage`, whose ``exhausted`` field
    means *there is nothing further for this query* — and may be ``True`` only
    when the implementation has verified it, never when it merely returned fewer
    rows than it was asked for.

    That field replaces a paragraph of prose. The depth-doubling loop that reads
    these methods —
    :meth:`~theurian.application.retrieval_service.RetrievalService._visible_ranking`
    — used to reconstruct exhaustion from a row count, and ``limit`` does not
    mean the same thing to all three: a ceiling in :meth:`search_lexical`, a
    floor in :meth:`search_substring`, absent in :meth:`search_dense`. One
    expression read three rules off one number, and no rule was enforceable,
    because a count is what an adapter produces rather than what it promises.
    ``limit`` still differs per method and each says how below; what no longer
    differs is how the caller learns there is nothing further.
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

    def add_nodes(
        self,
        nodes: Sequence[IndexableNode],
        *,
        embedding_model: str,
        embedding_model_revision: str,
        embedding_dimension: int,
    ) -> int:
        """Insert a derived RAPTOR forest and its provenance. Returns nodes written.

        **The nodes and their derivation edges are one write.** A node without
        its edges cannot say what it holds, which is the exact shape
        :data:`~theurian.infrastructure.sqlite.index_purge._UNANCHORED_NODES`
        deletes and :func:`~theurian.infrastructure.sqlite.index_purge._verify`
        refuses to publish. Two calls would make that state reachable between
        them for any implementation that commits eagerly.

        ``nodes`` must be ordered so that a node's sources are written no later
        than the node itself is *readable*; an implementation that writes every
        node before any edge satisfies that for free, which is what the SQLite
        adapter does.

        The embedder is named per call rather than per node because it is a fact
        about the build, not about the summary: the same forest derived with no
        embedding provider configured is the same forest, and the columns then
        record that no vector was produced. Passing an empty ``embedding_model``
        with a non-empty ``add_node_embeddings`` is a row that lies about its own
        vector; the caller holds that pairing.
        """
        ...

    def add_node_embeddings(self, vectors: Sequence[tuple[str, Sequence[float]]]) -> int:
        """Store one vector per summary node. Returns how many were written.

        Separate from :meth:`add_embeddings` because ``embeddings`` is keyed on
        ``chunk_id REFERENCES chunks`` and a node id is not a chunk id -- the
        reason index schema v4 added a second table rather than widening the
        first.

        Partial is worse than absent, exactly as it is for chunks: dense
        retrieval would rank the embedded summaries and silently never surface
        the rest, which reads as a relevance problem rather than a build one.
        """
        ...

    def derive_purged(
        self,
        target: Path,
        *,
        revision_ids: Sequence[str],
        index_build_id: str,
        state_hash: str,
        recompute_forest: ForestRecompute | None = None,
    ) -> int:
        """Write this build minus `revision_ids` to `target`. Returns rows removed.

        **A purge is a build** (ADR-0024). It produces a new file and a pointer
        swap, exactly as :meth:`create` does, and never writes to the file this
        store already names — which is what lets a search keep reading the
        published build while a purge runs.

        The count returned may exceed the chunks of `revision_ids` alone, and
        that is the contract rather than a surprise: withdrawal is transitive.
        A row derived from a withdrawn chunk holds its content — a summary is not
        withdrawn by deleting the passage it summarises — so everything reachable
        from a withdrawn chunk goes with it, and so does any derived row whose
        provenance cannot be resolved.

        `recompute_forest`, when given, re-derives each affected scope's summary
        trees over the surviving rows so the purged forest equals one built from a
        corpus that never held the withdrawn rows (ADR-0008 decision 9). Absent
        it, or over a build with no forest, the purge is delete-only. The count is
        unchanged either way: it is the withdrawn rows removed, not the survivors
        a re-derivation deletes and re-writes identically.

        All-or-nothing. An implementation that cannot produce a build fit to
        publish must leave no file behind, because what publishes a build is a
        pointer swap and no later stage inspects it.
        """
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
    ) -> RetrieverPage:
        """Rank by term match, best first.

        Filtering happens with the match, before ranking (FR-R1). A malformed
        query returns nothing rather than raising: a search box that punishes
        punctuation is a broken search box.

        **``limit`` is a true ceiling**: never more rows than ``limit``, and it
        must be at least 1. Zero or negative is refused rather than interpreted
        -- there is no sensible reading of "the best -2 rows", and an
        implementation that slices with it produces a page that is neither
        bounded by ``limit`` nor honest about ``exhausted``.

        Returning fewer than ``limit`` no longer implies exhaustion and must not
        be read as implying it — ``exhausted`` says so, and an implementation
        that returns exactly ``limit`` rows has to establish for itself whether a
        further row exists. Asking the storage engine for ``limit + 1`` and
        reporting whether the extra row arrived is what the SQLite adapter does;
        anything that answers the same question will do.
        """
        ...

    def search_substring(
        self,
        query: str,
        *,
        project_id: str,
        limit: int,
        include_unapproved: bool,
    ) -> RetrieverPage:
        """Rank by substring match, best first.

        The retriever that makes scripts without word boundaries searchable.

        **``limit`` is a floor, not a ceiling**, and that asymmetry with
        :meth:`search_lexical` is deliberate. It must still be at least 1, for
        the reason given there. An adapter must return at least the best
        ``limit`` rows it has — but it may return more, and should whenever
        bounding the answer would not bound the work. A short CJK query falls
        below the trigram floor and is answered by a scan that must score every
        matching row before it can name the best of them; truncating that to
        ``limit`` would mean saying ``exhausted=False`` about a ranking that is
        already complete, and the caller's response to that is another full scan
        for no new rows.

        So the branch that cannot bound its work returns everything it found and
        reports ``exhausted=True`` on its first and only call, whatever the
        corpus and whatever the canonical store has since withdrawn. That is a
        structural property now rather than a memoised one: there is no second
        call left to make cheap.
        """
        ...

    def search_summaries(
        self,
        query: str,
        *,
        project_id: str,
        limit: int,
        include_unapproved: bool,
    ) -> RetrieverPage:
        """Rank *leaves* by matching the RAPTOR summary above them (ADR-0008 dec. 8).

        The forest retriever, and the reason a query about "backend architecture"
        reaches a document that never contains that phrase: it matches the summary
        node's text -- the summariser's paraphrase of its children -- and descends
        ``node_derivation`` to the leaf chunks beneath the matched node, returning
        *those leaves*, ranked best-first by the score of the summary that reached
        them. A summary node is a routing device; it is never itself a result row,
        because it has no ``(item, current revision)`` pair the canonical gate
        could clear.

        **The double gate is the disclosure spine (SEC-13, T-15).** The *node*
        match is scoped exactly as the leaf retrievers are -- Project, and status
        unless the caller asked for drafts -- so a draft-scope summary is not even
        traversed on a default query; and the *descended leaves* are scoped again,
        so a leaf whose build-time status is withheld never leaves this method.
        The caller then re-clears every leaf through the canonical store in
        :meth:`~theurian.application.retrieval_service.RetrievalService._visible_ranking`,
        as it does every retriever's rows. Routing changes which leaves are
        candidates; it never changes whether a gated row may surface.

        **``limit`` is a true ceiling**, like :meth:`search_lexical`'s and for the
        same reason: leaves are ranked best-first and counted from the top, so
        fetching one past the ceiling is how the caller learns whether more
        remain. Returns nothing over a build with no forest -- a chunk-only index
        holds no summary nodes to match.
        """
        ...

    def raptor_path(self, revision_id: str, *, project_id: str) -> tuple[RaptorPathSegment, ...]:
        """A surfaced leaf's forest ancestry, catalog root to leaf (FR-R5, dec. 8).

        Walks ``node_derivation`` *upward* from the revision's chunks to their
        Document node, then to its Domain parent, then to the Catalog -- returning
        one segment per ancestor, root first, each ``{node_id, level, title}``
        with ``title`` the node text bounded by
        :func:`~theurian.domain.retrieval.excerpt`. Empty over a build with no
        forest, or for a revision no summary was derived from, so a caller can
        tell "no forest here" from "here is the path".

        **Only ever called for a leaf that already cleared the gate**
        (:meth:`~theurian.application.retrieval_service.ResultGate._surfaced`). A
        node's children share its six-component scope by construction (ADR-0008
        decision 1), so every ancestor of a cleared leaf is in that leaf's own
        scope, and a title carries no content from a scope the leaf is not in. A
        withheld leaf surfaces nothing and so has no path; its ancestors' titles
        never reach the wire. The walk genuinely filters on the leaf's own
        ``project_id`` and ``status`` too (SEC-13) -- not only the parameter this
        signature takes, but a predicate the ancestor-node reads themselves apply
        (:func:`~theurian.infrastructure.sqlite.index_forest.walk_raptor_path`),
        so a scope-disagreeing ancestor is dropped even were the construction-time
        invariant above ever violated, a defense in depth this docstring
        previously did not claim.
        """
        ...

    def search_dense(
        self,
        query_vector: Sequence[float],
        *,
        project_id: str,
        include_unapproved: bool,
    ) -> RetrieverPage:
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

        ``exhausted`` is therefore always ``True`` here, and it is a fact rather
        than a formality: this method returns the whole ranking, so there is
        never anything further. The caller clears and cuts it in a single pass
        instead of going through the depth loop at all
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
