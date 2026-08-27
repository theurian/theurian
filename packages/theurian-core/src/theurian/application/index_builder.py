"""Turning a canonical state into an index build (FR-R2, FR-R3, ADR-0022).

Split out of :mod:`theurian.application.retrieval_service` for the reason
:mod:`theurian.application.visibility` was split out of it before: that file had
grown past the size at which it can be read in one sitting, and this is a seam
rather than a cut. It described itself as "three use cases over one index file",
and this is the one that *writes*. Everything left there reads.

The seam is real rather than arithmetic. Nothing here consults a
:class:`~theurian.application.visibility.Visibility`, because at build time there
is no caller to be visible *to*: the filters that apply are
:func:`~theurian.domain.enums.may_surface` against the operator's
``include_unapproved`` and :func:`~theurian.domain.enums.may_disclose` against
the deployment's serving profile, and they decide what is written rather than
what is shown. The equality property the query side exists to hold has no
counterpart here.

**Two build flavors, for two reasons that are not the same.** ``include_unapproved``
exists so an operator can guarantee a draft is not in the file at all; the
sensitivity filter exists because a row that *is* in the file is priced into the
FTS5 collection statistics -- ``N``, ``avgdl`` and the per-term document
frequencies -- that score every visible row against it, whatever a later query
predicate does (T-17a, ADR-0025 part 1). Excluding at build time is the choice
ADR-0025 left open and #119 settled: a read-side predicate alone leaves the
withheld text in the index, and the statistics it moves are observable without
ever returning it.

**The RAPTOR forest is derived here too, over the chunks that survived that
filter** (ADR-0008). It has to be: a summary of a withheld revision holds the
withheld revision's content, so the forest must stand on what was written rather
than on what the canonical store holds. Deriving it from ``indexable`` rather
than reading the rows back is what keeps the derivation a pure function of this
build's own output, which ADR-0008 decision 9's two-corpus equality rests on.

Takes its collaborators by injection, so a build is testable without a database,
without an embedding provider, and without a summariser.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, final

from theurian.application.forest_builder import ForestBuilder
from theurian.domain.chunking import IndexableChunk, chunk_document
from theurian.domain.context import RequestContext
from theurian.domain.enums import Sensitivity, may_disclose, may_surface
from theurian.domain.errors import InvariantViolationError
from theurian.domain.identifiers import ProjectId
from theurian.domain.knowledge import served_content_hash, served_content_text
from theurian.domain.ports.canonical_store import CanonicalReadSession
from theurian.domain.ports.embedding import EmbeddingProvider
from theurian.domain.ports.index_store import IndexStore
from theurian.domain.raptor import IndexableNode

#: Chunks per embedding request. An API-backed provider caps request size, and a
#: local one gains nothing from an unbounded batch -- while an unbounded batch
#: holds the whole corpus and all its vectors in memory at once.
EMBED_BATCH: Final = 128


@dataclass(frozen=True, slots=True)
class IndexRequest:
    """What to index, and where to put it."""

    database: Path
    index_path: Path
    project_id: str
    state_hash: str
    index_build_id: str
    #: The disclosure classes this deployment serves, already expanded from the
    #: operator's declared ceiling (#119, ADR-0025). An item outside it writes no
    #: chunk row, and therefore no summary node either -- the forest is derived
    #: over what this build wrote.
    #:
    #: **No default, deliberately**, for the reason
    #: :func:`~theurian.domain.enums.may_disclose` has none: "everything is
    #: visible" is the state this filter exists to stop being implicit, and a
    #: default parameter is how it would come back. Every caller states it, which
    #: is four lines in the composition roots and a keyword in each test.
    visible_sensitivities: frozenset[Sensitivity]
    #: Whether unapproved revisions are written at all. Off by default, so an
    #: operator who never opts in has a hard guarantee that no draft is in the
    #: file — not merely that a query filter is expected to hold.
    include_unapproved: bool = False
    #: Whether to derive a RAPTOR forest over what was indexed. Off by default,
    #: and for the same shape of reason: ADR-0008 decision 10 ships the forest
    #: opt-in, so that turning on a capability whose acceptance tests are owed
    #: and whose build cost is unmeasured is somebody's decision rather than the
    #: side effect of an upgrade. A build without it writes zero node rows.
    raptor: bool = False


@final
class IndexBuilder:
    """Turns a canonical state into an index build."""

    def __init__(
        self,
        *,
        store_factory: Callable[[Path], CanonicalReadSession],
        index_factory: Callable[[Path], IndexStore],
        embedder: EmbeddingProvider | None = None,
        forest_builder: ForestBuilder | None = None,
    ) -> None:
        self._store_factory = store_factory
        self._index_factory = index_factory
        self._embedder = embedder
        self._forest_builder = forest_builder

    def build(self, request: IndexRequest) -> dict[str, object]:
        """Write a new index file from a canonical state.

        Unapproved revisions are written only when asked for, `rejected` never
        is, an item above this deployment's disclosure ceiling never is, and a
        summary forest is derived only when asked for.

        The obvious simplification — index everything, filter at query time —
        was tried and reverted. It makes `includeUnapproved=True` a single
        boolean that reaches content the team decided must not be followed, and
        it removes the operator's ability to guarantee that a draft is not in
        the file at all. The cost is that `includeUnapproved=True` cannot return
        rows that were never written, which is reported rather than hidden:
        `indexesUnapproved` says whether this build can answer such a query.

        Either a whole index file or none: a build that fails part-way deletes
        what it wrote. That guarantee used to live only in the CLI while
        :meth:`_embed`'s docstring asserted it here, so any other caller of
        `build` — a daemon, a test, a future scheduled rebuild — got a
        half-written file and the promise that it could not happen.

        **What it wrote, and nothing else.** `IndexStore.create` refuses to
        overwrite an existing file and raises; the cleanup then unlinked the very
        file it had just been refused permission to touch — which is the file
        `active-index.json` names, so a build against an already-taken path
        deleted the published index and left the pointer aimed at nothing. Not
        reachable from `theurian index build`, which mints a fresh ULID per
        build, but `build` is a public application-layer API and `create`'s
        contract says an existing file is left alone.
        """
        # Sampled before `create` rather than inferred from the exception: only a
        # path this call brought into existence may be removed by it.
        preexisting = request.index_path.exists()
        try:
            return self._build(request)
        except Exception:
            # A partial index is worse than none. It looks complete, ranks the
            # fraction it holds, and never surfaces the rest -- which reads as a
            # relevance problem rather than a build failure, and so does not get
            # investigated. Nothing this build wrote is published until it
            # returns, so deleting here loses no index a search could have used.
            if not preexisting:
                request.index_path.unlink(missing_ok=True)
            raise

    def _build(self, request: IndexRequest) -> dict[str, object]:
        index = self._index_factory(request.index_path)
        index.create(index_build_id=request.index_build_id, state_hash=request.state_hash)

        context = RequestContext(project_id=ProjectId(request.project_id))
        indexable: list[IndexableChunk] = []

        with self._store_factory(request.database) as store:
            for item in store.list_items(context):
                # The same authority the search paths consult. Inlined here as
                # two comparisons until `may_surface` moved to the domain, which
                # is one copy of a security rule too many.
                if not may_surface(item.status, include_unapproved=request.include_unapproved):
                    continue
                # The second axis, and the one a query-time predicate cannot
                # fully cover. `_scope` does emit one for it now (#119 phase 4),
                # and it is defence in depth rather than the control: an FTS5
                # external-content table scores what it returns against
                # collection statistics computed over every row it holds, so a
                # row filtered out on read goes on pricing the rows that are
                # returned. Excluded here, an above-ceiling document contributes
                # nothing to `N`, to `avgdl` or to any term's document frequency,
                # so T-17a is held by construction on this axis rather than by a
                # filter (ADR-0025 part 1).
                #
                # The item's current level, matching the `sensitivity=` written
                # below and for the same reason: `changeSensitivity` moves the
                # classification on the item without writing a new revision
                # (ADR-0005), so reading the revision's would admit a document by
                # the label it was authored under.
                if not may_disclose(item.sensitivity, visible=request.visible_sensitivities):
                    continue
                if item.current_revision_id is None:
                    continue
                revision = store.get_revision(context, item.current_revision_id)
                if revision is None:  # pragma: no cover - a composite foreign key holds this (#24)
                    continue
                if not item.owns(revision):
                    # A `current_revision_id` naming a sibling item's revision is
                    # type-valid, satisfies the composite foreign key, and moves
                    # neither `#30` integrity count, so nothing upstream of this
                    # loop refuses it. Read through here it is the one face of
                    # that damage the query side cannot then catch:
                    # `CanonicalVisibility._may_surface` clears a ranked row by
                    # comparing the indexed `revision_id` against the live
                    # pointer, and a build that followed the corrupt pointer
                    # writes chunks the pointer *agrees* with -- so the withheld
                    # revision's body would rank and be excerpted under this
                    # item's approved status.
                    #
                    # Refused rather than skipped, and refused for the whole
                    # build: skipping would drop an approved document out of the
                    # index with nothing said, which reads as a relevance problem
                    # rather than as damage. `IndexBuilder.build` unlinks what
                    # this run wrote and `_run_build` converts this into
                    # `{error, remedy}` at exit 1, so nothing is published and the
                    # previous index still answers.
                    #
                    # Names no id and no cell: the message reaches the operator's
                    # terminal through `theurian index build`, and the id it would
                    # carry is the withheld item's.
                    msg = (
                        "This project's canonical state disagrees with its own records: an "
                        "item points at a revision that belongs to a different item, so this "
                        "build would index that revision's content under the wrong item."
                    )
                    raise InvariantViolationError(msg)

                # The title is prepended to the body before splitting so that a
                # query matching only the title still finds the document. A
                # separately indexed title field would need its own retriever and
                # its own fusion weight for the same effect. `served_content_text`
                # is the single definition of this format, shared with the serve
                # gate so the text the index chunks and the text the gate re-hashes
                # cannot drift apart (GHSA-3f65).
                served = served_content_text(revision.title, revision.body)
                for chunk in chunk_document(revision.revision_id.value, served):
                    indexable.append(
                        IndexableChunk(
                            chunk=chunk,
                            project_id=request.project_id,
                            item_id=item.item_id.value,
                            revision_id=revision.revision_id.value,
                            # The hash of exactly the text served above, so the
                            # gate can catch a title or body that later drifts
                            # under this same revision id (GHSA-3f65). Not the
                            # revision's body-only `content_sha256`: the served
                            # text prepends the title, and a title can drift while
                            # the body hash holds. `served_content_hash` re-runs
                            # `served_content_text` internally, so this is provably
                            # the hash of the `served` string the chunker just
                            # received -- one format definition, both uses.
                            served_content_sha256=served_content_hash(
                                revision.title, revision.body
                            ).value,
                            status=item.status.value,
                            # The item's, not the revision's, and for the same
                            # reason `status` is: a `changeSensitivity` moves the
                            # classification on the item without writing a new
                            # revision (ADR-0005), so the immutable revision keeps
                            # the label it was authored under. Sensitivity is a
                            # component of the scope tuple a RAPTOR tree *is*
                            # (ADR-0008 decision 1) and decides who may read the
                            # content (SEC-14), so a build reading the revision's
                            # would stamp every chunk and node with a stale label.
                            # `namespace` and `kind` below need no such treatment:
                            # no operation moves them after creation, so the item
                            # always carries its current revision's values for them.
                            sensitivity=item.sensitivity.value,
                            trust_level=revision.metadata.trust_level.value,
                            # The scope tuple's namespace, and the kind that
                            # selects a Domain tree inside it (ADR-0008
                            # decisions 1 and 2). Both come off the revision's
                            # metadata rather than the item's registration:
                            # metadata is what `RevisionMetadata.scope_for`
                            # reads, so a forest partitioned any other way would
                            # disagree with the domain's own answer.
                            namespace=revision.metadata.namespace,
                            kind=revision.metadata.kind.value,
                        )
                    )

        index.add_chunks(indexable)
        # After the chunks and before either embedding pass: the forest stands
        # on `chunks` rows through `node_derivation`'s foreign key, and both
        # embedding passes want the rows they vectorise already written.
        nodes = self._derive_forest(request, index, indexable)
        embedded = self._embed(index, indexable)
        self._embed_nodes(index, nodes)

        return {
            "indexBuildId": request.index_build_id,
            "stateHash": request.state_hash,
            "indexPath": str(request.index_path),
            "chunks": len(indexable),
            "embeddings": embedded,
            "embeddingModel": self._embedder.model_id if self._embedder else "",
            "indexesUnapproved": request.include_unapproved,
            # Both, because the count alone cannot tell a forest-free build apart
            # from one whose corpus fell below every threshold -- the same
            # confusion `indexesUnapproved` exists to prevent for drafts.
            "raptor": request.raptor,
            "nodes": len(nodes),
        }

    def _derive_forest(
        self, request: IndexRequest, index: IndexStore, indexable: Sequence[IndexableChunk]
    ) -> tuple[IndexableNode, ...]:
        """Derive and store the RAPTOR forest, or nothing when it was not asked for.

        Derived from ``indexable`` rather than read back out of the index,
        which keeps the derivation a pure function of what this build wrote
        (ADR-0008 decision 9) and costs no second pass over the corpus.

        A ``raptor`` request with no builder wired is refused rather than
        answered with an empty forest: the report would say ``raptor: true,
        nodes: 0``, which is what a corpus below every threshold says, and the
        difference is a composition error nobody would look for. Unreachable
        from `theurian index build`, which always composes one, so this refuses
        a caller bug -- `ValueError` for the reason `index_store` gives at
        `_require_a_positive_limit`: no `theurian` command produces it, so there
        is no remedy to carry.
        """
        if not request.raptor:
            return ()
        if self._forest_builder is None:
            msg = (
                "IndexRequest.raptor is set and no ForestBuilder was injected, so no "
                "forest can be derived. Construct IndexBuilder with "
                "forest_builder=ForestBuilder(summarizer=...)."
            )
            raise ValueError(msg)

        nodes = self._forest_builder.derive(indexable)
        index.add_nodes(
            nodes,
            # The embedder's identity, not the vectors: it is known before a
            # single one is computed, so the row records it once instead of
            # being rewritten after the fact. Empty when no provider is
            # configured, which is the truthful record of a node with no vector.
            embedding_model=self._embedder.model_id if self._embedder else "",
            embedding_model_revision=self._embedder.model_revision if self._embedder else "",
            embedding_dimension=self._embedder.dimension if self._embedder else 0,
        )
        return nodes

    def _embed(self, index: IndexStore, indexable: Sequence[IndexableChunk]) -> int:
        """Embed every chunk, or none.

        Batched, because a real provider caps request size and a local one gains
        nothing from an unbounded batch.

        A partial embedding is worse than none: the dense retriever would rank
        the embedded half and silently never surface the rest, which looks like
        a relevance problem rather than a build problem. :meth:`build` discards
        the whole index file if any batch raises, so a partial one never exists
        to be published.
        """
        if self._embedder is None or not indexable:
            return 0

        embedded = 0
        for start in range(0, len(indexable), EMBED_BATCH):
            batch = indexable[start : start + EMBED_BATCH]
            vectors = asyncio.run(self._embedder.embed(tuple(c.chunk.text for c in batch)))
            index.add_embeddings(
                [(c.chunk.chunk_id, v) for c, v in zip(batch, vectors, strict=True)]
            )
            embedded += len(vectors)

        index.record_embedding_model(
            model_id=self._embedder.model_id, dimension=self._embedder.dimension
        )
        return embedded

    def _embed_nodes(self, index: IndexStore, nodes: Sequence[IndexableNode]) -> None:
        """Embed every summary node, or none.

        :meth:`_embed` over the node tables, and it is not optional the moment a
        forest exists: `search_dense` ranks what has a vector, so a forest
        without one is a forest dense retrieval can never reach -- the capability
        would exist, be reported, and answer nothing.

        `--no-embeddings` reaches this the same way it reaches the chunks, by
        there being no embedder at all. A flag that skipped only half would mean
        half of what it says.

        No :meth:`IndexStore.record_embedding_model` call: `index_metadata` names
        the model that embedded this build and :meth:`_embed` has already
        recorded it for the same provider, while which model vectorised a *node*
        is on the node's own row (ADR-0008 decision 5), written by
        :meth:`IndexStore.add_nodes`.

        Returns nothing, deliberately. `embeddings` in the report counts chunk
        vectors and sits beside `chunks`; folding node vectors into it would make
        a build of 6 chunks report 10 embeddings. A separate count would say only
        what `nodes` and `embeddingModel` already do -- every node or none -- and
        a second count is a second thing that has to stay true.
        """
        if self._embedder is None or not nodes:
            return

        for start in range(0, len(nodes), EMBED_BATCH):
            batch = nodes[start : start + EMBED_BATCH]
            vectors = asyncio.run(self._embedder.embed(tuple(node.text for node in batch)))
            index.add_node_embeddings(
                [(node.node_id, v) for node, v in zip(batch, vectors, strict=True)]
            )


__all__ = ["EMBED_BATCH", "IndexBuilder", "IndexRequest"]
