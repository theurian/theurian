"""Deriving a RAPTOR forest from leaf chunks (ADR-0008 decisions 1, 2, 6, 9).

**A pure function of (surviving rows, scope, configuration).** ADR-0008
decision 9's two-corpus equality -- a purged build's forest must equal one built
from a corpus that never held the withdrawn rows -- is reachable only if nothing
else reaches the output. So this module reads no clock, no database and no
configuration file, hashes nothing that varies per process, and orders every
collection it iterates by a total key. What it is handed is what decides what it
returns.

**Application layer, not infrastructure, and that is a layering fact rather
than a preference.** `application/index_builder.py` is where the forest pass has
to mount, and ``tests/unit/test_layering.py::test_application_does_not_import_
infrastructure`` walks the real import graph -- so a builder under
`infrastructure/` could not be called from the one place that must call it.
ADR-0008 decision 7 puts *summarization* behind a port, and
`docs/architecture/raptor.md` says the hierarchy itself has none, which leaves
the builder as application policy over a port that already exists.

**The three tiers, and what partitions each.** Within one scope the namespace is
already fixed, so ADR-0008 decision 2's "one namespace or kind" reduces to
``kind`` at the Domain tier -- which is why :class:`IndexableChunk` carries it.

| Level | Tier     | One node per            | Built from            |
| :---- | :------- | :---------------------- | :-------------------- |
| 1     | Document | item revision, in scope | that revision's chunks|
| 2     | Domain   | kind, in scope          | its Document nodes    |
| 3     | Catalog  | scope                   | its Domain nodes      |

**What this costs, stated because nothing else bounds it.** One
``summarize`` call per node, and the extractive default refuses more than
:data:`~theurian.infrastructure.raptor.extractive.MAX_TOTAL_INPUT_CHARS`
characters in one call. A Document node is charged its item's whole body, so a
single document past a million characters -- a thousand chunks at the chunker's
target -- fails the build rather than producing a summary nobody could read.
Every tier above is charged its children's *summaries*, each bounded by
:attr:`ForestOptions.summary_max_tokens`. A Domain node's input grows with the
number of *documents of its kind*, which is linear in the corpus, so it is the
tier a growing corpus overruns first and the one that gets an explicit per-node
bound. :data:`MAX_CHILDREN_PER_DOMAIN` is that bound: a kind past it splits into
deterministic batches of at most that many documents, each its own Domain node,
rather than one whose input would cross the character limit above -- except a
trailing batch too small to clear the Domain tier's own floor
(:attr:`ForestOptions.min_children_per_summary`), which merges into the batch
before it instead of minting a node with too few children to summarise, or being
silently dropped (see :func:`_domain_batches`). Fanning out moves the growth up a
tier -- a Catalog node is charged one
summary per Domain node, so its input grows with the number of kinds until a kind
fans out and then with the corpus too, at 1/500 the rate. The Catalog is not
itself fanned out, so a single scope holding one kind at hundreds of thousands of
documents (a thousand Domain batches) is the corpus that would finally cross the
same limit at the Catalog node -- a ceiling this fan-out raises far above the
Domain tier's rather than removing (#144).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final, final

from theurian.domain.chunking import TARGET_CHARS, IndexableChunk
from theurian.domain.enums import KnowledgeStatus, Sensitivity
from theurian.domain.errors import InvariantViolationError
from theurian.domain.identifiers import ProjectId
from theurian.domain.ports.summarization import SummarizationProvider
from theurian.domain.ranking import CHARS_PER_TOKEN
from theurian.domain.raptor import (
    MAX_LEVEL,
    TIERS,
    IndexableNode,
    NodeType,
    SummaryNode,
    node_identity,
    tree_identity,
)
from theurian.domain.values import AclGroup, ContentHash, Scope, TenantId

#: What one summary may cost, in tokens. **A constant, and ADR-0008 decision 6's
#: Milestone 6 amendment is why**: "``max_tokens`` must never be a
#: corpus-derived quantity ... a builder that divided a shared budget by the
#: number of documents would change a visible node's text when a withheld
#: document was added or removed, while the summariser itself read nothing it
#: should not." A summariser cannot hold that property -- it is handed the
#: result and never the recipe -- so it is held here, at the only place that can
#: hold it.
#:
#: One chunk's worth: the chunker's target passage priced at the estimator's
#: characters-per-token. A node that cost more than the passage it stands beside
#: would spend a caller's budget to say less.
#:
#: It has no config surface. `raptor.maxLevels` and `raptor.minChildrenPerSummary` are in
#: `schemas/config/project-config.schema.json`, and no key in the `raptor` block has a reader
#: in `src/`: `.theurian/config.yaml`'s one reader takes `security.secretScan` (ADR-0027).
#: A third key would be a published default with no reader and no measurement behind it.
SUMMARY_MAX_TOKENS: Final = TARGET_CHARS // CHARS_PER_TOKEN

#: The smallest threshold that still means something, and the config schema's
#: own ``minimum``. At one child a "summary" is a paraphrase of a single node.
MIN_CHILDREN_FLOOR: Final = 2

#: The most Document nodes one Domain node may summarise before the tier fans out
#: into several. **A safety margin under the summariser's input limit, not a
#: tuning knob.** A Domain node is charged its children's *summaries*, each at
#: most :data:`SUMMARY_MAX_TOKENS` tokens and so at most that many times
#: :data:`~theurian.domain.ranking.CHARS_PER_TOKEN` characters, so a full batch's
#: input is ``MAX_CHILDREN_PER_DOMAIN * SUMMARY_MAX_TOKENS * CHARS_PER_TOKEN`` --
#: 500 x 250 x 4 = 500k against the extractive default's
#: :data:`~theurian.infrastructure.raptor.extractive.MAX_TOTAL_INPUT_CHARS` of
#: 1M, half of it, so even a full batch never drives the refusal the fan-out
#: exists to remove. Above this many documents of one kind in one scope the tier
#: splits into deterministic batches (:func:`_domain_batches`) rather than
#: minting a single Domain node whose input grows without bound with the corpus.
MAX_CHILDREN_PER_DOMAIN: Final = 500

#: The level each tier carries, derived from :data:`~theurian.domain.raptor.TIERS`
#: so the mapping is declared once. Named so that a comparison below reads as a
#: tier rather than as an integer somebody has to decode.
_DOCUMENT_LEVEL: Final = TIERS.index(NodeType.DOCUMENT) + 1
_DOMAIN_LEVEL: Final = TIERS.index(NodeType.DOMAIN) + 1
_CATALOG_LEVEL: Final = TIERS.index(NodeType.CATALOG) + 1


@final
@dataclass(frozen=True, slots=True)
class ForestOptions:
    """What a derivation is configured with.

    The defaults are `schemas/config/project-config.schema.json`'s own, and
    ``tests/unit/test_forest_derivation.py`` pins the two against it. Two
    independently written defaults that happen to agree today is the shape that
    drifts the moment one is tuned, and the day a config loader lands the drift
    changes behaviour for everyone who set neither.
    """

    #: A ceiling on tiers, where level 1 *is* a tier: ``1`` means Document nodes
    #: and nothing above, which is not the same as no forest at all
    #: (``raptor.enabled: false`` is that). The schema admits up to 8 while
    #: ADR-0008 decision 2 names three, so a larger value builds three -- capped
    #: rather than refused, because a valid config must stay buildable.
    max_levels: int = 3
    #: Below this, a level is skipped. The threshold exists because a summary of
    #: one or two children is a paraphrase: it costs tokens and adds nothing,
    #: which is ADR-0008's own Negative consequence.
    #:
    #: Bounded above by :data:`MAX_CHILDREN_PER_DOMAIN` too, and that bound is
    #: enforced in :meth:`__post_init__` rather than published as this field's
    #: own :attr:`summary_max_tokens`-style constant: :func:`_domain_batches`'s
    #: "only the last cut can be short" holds only while a full batch
    #: (``MAX_CHILDREN_PER_DOMAIN`` documents) cannot itself fall short of this
    #: floor. A caller raising this floor past the cap would put every
    #: non-final batch below it, and the merge that saves the *tail* batch does
    #: not run on the ones before it -- the orphaning :func:`_domain_batches`
    #: exists to close, reopened from the body instead of the tail. Not mirrored
    #: as a schema `maximum` on `raptor.minChildrenPerSummary`
    #: (`project-config.schema.json`): that key bounds every tier, while
    #: `MAX_CHILDREN_PER_DOMAIN` is a Domain-tier-only fan-out constant nothing
    #: in `src/` reads the schema against yet (see :data:`SUMMARY_MAX_TOKENS`),
    #: so a `maximum` there would publish a coupling the schema does not
    #: otherwise express.
    min_children_per_summary: int = 3
    #: What one summary may cost. A constant on purpose and never a share of
    #: anything the corpus decides -- see :data:`SUMMARY_MAX_TOKENS` for the
    #: disclosure channel that closes. It is the one option here with no key in
    #: the config schema.
    summary_max_tokens: int = SUMMARY_MAX_TOKENS

    def __post_init__(self) -> None:
        if self.max_levels < 1:
            raise InvariantViolationError(
                f"max_levels must be at least 1, got {self.max_levels} -- a forest of "
                f"zero tiers is `raptor.enabled: false`, which is a different setting"
            )
        if self.min_children_per_summary < MIN_CHILDREN_FLOOR:
            raise InvariantViolationError(
                f"min_children_per_summary must be at least {MIN_CHILDREN_FLOOR}, got "
                f"{self.min_children_per_summary} -- a summary of one child is a "
                f"paraphrase of it (ADR-0008)"
            )
        if self.min_children_per_summary > MAX_CHILDREN_PER_DOMAIN:
            raise InvariantViolationError(
                f"min_children_per_summary must be at most {MAX_CHILDREN_PER_DOMAIN}, got "
                f"{self.min_children_per_summary} -- _domain_batches's tail-merge proof "
                f"('only the last cut can be short') holds only below this cap; above it, "
                f"every non-final batch is exactly {MAX_CHILDREN_PER_DOMAIN} documents, "
                f"short of the floor, and the merge that saves the tail does not reach them "
                f"-- orphaning documents the way _domain_batches exists to prevent"
            )
        if self.summary_max_tokens < 1:
            raise InvariantViolationError(
                f"summary_max_tokens must be at least 1, got {self.summary_max_tokens} -- "
                f"`estimate_tokens` prices even the empty string at one token"
            )


@final
class ForestBuilder:
    """Derives the summary nodes of one build. Writes nothing.

    Takes the summariser by injection, so a derivation is testable without an
    adapter and so an abstractive provider is a wiring change (ADR-0003,
    ADR-0009).
    """

    def __init__(
        self,
        *,
        summarizer: SummarizationProvider,
        options: ForestOptions | None = None,
    ) -> None:
        self._summarizer = summarizer
        self._options = options if options is not None else ForestOptions()

    def derive(self, chunks: Sequence[IndexableChunk]) -> tuple[IndexableNode, ...]:
        """Every summary node the given chunks earn, deepest tier last.

        Synchronous over an async port. The summariser is awaited once per node
        through :func:`asyncio.run`, the way
        :meth:`~theurian.application.index_builder.IndexBuilder._embed` already
        awaits the embedding provider: a build is a batch job with no other
        concurrency to overlap with, and making this coroutine would push the
        choice onto every caller of `index build` for no benefit an offline
        extractive default can show.

        Returns:
            Nodes ordered by ``(level, node_id)`` -- total, and low tiers first,
            so a caller inserting them in order never names a node it has not
            yet written.
        """
        nodes: list[IndexableNode] = []
        for scope, scoped in _by_scope(chunks):
            nodes.extend(self._forest_for_scope(scope, scoped))
        return tuple(sorted(nodes, key=lambda node: (node.level, node.node_id)))

    def _forest_for_scope(
        self, scope: Scope, chunks: Sequence[IndexableChunk]
    ) -> list[IndexableNode]:
        """One scope's trees. A scope is the isolation boundary, so this is the
        only function that ever sees more than one tree's children at once --
        and it sees them already partitioned.
        """
        tiers = min(self._options.max_levels, MAX_LEVEL)
        built: list[IndexableNode] = []

        by_kind: dict[str, list[IndexableNode]] = {}
        for kind, item_id, item_chunks in _by_item(chunks):
            node = self._document_node(scope, item_id=item_id, chunks=item_chunks)
            if node is not None:
                built.append(node)
                by_kind.setdefault(kind, []).append(node)
        if tiers < _DOMAIN_LEVEL:
            return built

        domains: list[IndexableNode] = []
        for kind in sorted(by_kind):
            batches = _domain_batches(
                kind, by_kind[kind], min_children=self._options.min_children_per_summary
            )
            for discriminator, batch in batches:
                node = self._node_over_nodes(
                    scope, level=_DOMAIN_LEVEL, discriminator=discriminator, children=batch
                )
                if node is not None:
                    built.append(node)
                    domains.append(node)
        if tiers < _CATALOG_LEVEL:
            return built

        # The Catalog is the scope itself, so it has no discriminator: one
        # scope, one catalog tree.
        catalog = self._node_over_nodes(
            scope, level=_CATALOG_LEVEL, discriminator="", children=domains
        )
        if catalog is not None:
            built.append(catalog)
        return built

    def _document_node(
        self, scope: Scope, *, item_id: str, chunks: Sequence[IndexableChunk]
    ) -> IndexableNode | None:
        """One item revision's chunks, summarised. ``None`` below the threshold.

        Children are ordered by ``(ordinal, chunk_id)`` before anything is
        hashed or summarised, so the order the caller happened to hand them over
        in cannot reach the id or the text (ADR-0008 decision 9).
        """
        ordered = sorted(chunks, key=lambda chunk: (chunk.chunk.ordinal, chunk.chunk.chunk_id))
        if len(ordered) < self._options.min_children_per_summary:
            return None

        texts = tuple(chunk.chunk.text for chunk in ordered)
        tree_id = tree_identity(
            scope=scope, node_type=NodeType.DOCUMENT, discriminator=item_id
        ).value
        return IndexableNode(
            node=SummaryNode(scope=scope, children=tuple(_scope_of(chunk) for chunk in ordered)),
            node_id=self._identity(tree_id, level=_DOCUMENT_LEVEL, texts=texts),
            tree_id=tree_id,
            level=_DOCUMENT_LEVEL,
            text=self._summarize(texts, scope),
            summary_model=self._summarizer.model_id,
            summary_model_revision=self._summarizer.model_revision,
            summary_prompt_hash=self._summarizer.prompt_hash,
            # Exact by construction: `_by_item` groups on the revision as well
            # as the item, so this names the revision every one of these chunks
            # came from. `index_purge._DOOMED` retires a node by this stamp
            # whatever its edges still point at, so a node standing on two
            # revisions could name only one of them and survive the other's
            # withdrawal holding its content.
            source_revision_id=ordered[0].revision_id,
            source_chunk_ids=tuple(chunk.chunk.chunk_id for chunk in ordered),
        )

    def _node_over_nodes(
        self, scope: Scope, *, level: int, discriminator: str, children: Sequence[IndexableNode]
    ) -> IndexableNode | None:
        """A tier built from the tier below it. ``None`` below the threshold.

        Children are ordered by their content-addressed ids, which is the
        canonical order a rebuild reproduces -- unlike the order they happen to
        have been derived in, which follows this module's own iteration.
        """
        ordered = sorted(children, key=lambda node: node.node_id)
        if len(ordered) < self._options.min_children_per_summary:
            return None

        texts = tuple(node.text for node in ordered)
        tree_id = tree_identity(
            scope=scope, node_type=TIERS[level - 1], discriminator=discriminator
        ).value
        return IndexableNode(
            node=SummaryNode(scope=scope, children=tuple(node.scope for node in ordered)),
            node_id=self._identity(tree_id, level=level, texts=texts),
            tree_id=tree_id,
            level=level,
            text=self._summarize(texts, scope),
            summary_model=self._summarizer.model_id,
            summary_model_revision=self._summarizer.model_revision,
            summary_prompt_hash=self._summarizer.prompt_hash,
            # Empty above the Document tier: a node built from other nodes has
            # no single revision its text was written against, and withdrawal
            # reaches it through its edges instead (`index_purge._DOOMED`'s
            # closure arm). Safe against the stamp arm because a revision id is
            # a ULID, so no withdrawal set can contain the empty string.
            source_revision_id="",
            source_node_ids=tuple(node.node_id for node in ordered),
        )

    def _identity(self, tree_id: str, *, level: int, texts: Sequence[str]) -> str:
        return node_identity(
            tree_id=ContentHash(tree_id),
            level=level,
            child_hashes=[ContentHash.of_text(text) for text in texts],
        ).value

    def _summarize(self, texts: tuple[str, ...], scope: Scope) -> str:
        """One node's text, charged the configured budget and nothing else.

        ``max_tokens`` is :attr:`ForestOptions.summary_max_tokens` verbatim --
        never divided by a cluster size, a document count, or anything else the
        corpus decides. See that field for the disclosure channel this closes.
        """
        return asyncio.run(
            self._summarizer.summarize(
                texts, scope=scope, max_tokens=self._options.summary_max_tokens
            )
        )


def _scope_of(chunk: IndexableChunk) -> Scope:
    """The six-component scope a chunk belongs to (ADR-0008 decision 1).

    ``tenant_id`` and ``acl_group`` come from the defaults rather than from the
    chunk, because they are enforced at the write path: `migrate validate` and
    `migrate apply` refuse an ``upsertRevision`` naming any other value
    (`migration_engine._scope_violations`) until #119 lands an
    ``AuthorizationProvider``. So no chunk can carry another, and reading them
    off a column the chunk does not have would be inventing a fact.

    Everything else is the chunk's own, not the build's: a build indexes several
    items and they disagree on namespace, sensitivity and status, which is
    exactly what the partition is for.
    """
    return Scope(
        project_id=ProjectId(chunk.project_id),
        tenant_id=TenantId(),
        sensitivity=Sensitivity(chunk.sensitivity),
        acl_group=AclGroup(),
        namespace=chunk.namespace,
        status=KnowledgeStatus(chunk.status),
    )


def _domain_batches(
    kind: str, documents: Sequence[IndexableNode], *, min_children: int
) -> list[tuple[str, list[IndexableNode]]]:
    """One Domain node's children per batch, split when a kind exceeds the cap.

    Below :data:`MAX_CHILDREN_PER_DOMAIN` the whole kind is one batch keyed on
    the bare ``kind``, so a corpus small enough to need no fan-out mints exactly
    the tree id it did before this existed. Above it, the documents -- sorted by
    ``node_id`` so the slice does not depend on the order they were derived in
    (ADR-0008 decision 9) -- are cut into contiguous batches of at most
    ``MAX_CHILDREN_PER_DOMAIN``, each keyed on ``kind`` joined with its partition
    index so the batches mint distinct tree ids rather than colliding on the
    kind. A :class:`~theurian.domain.enums.KnowledgeKind` value cannot contain
    ``#``, so a partitioned discriminator can never equal a bare kind, and the
    partition of a corpus is a function of its content alone -- deterministic
    across rebuilds.

    **The tail batch is merged rather than left to fall below the next tier's
    own floor.** A fixed-size cut leaves a final batch of ``len(documents) %
    MAX_CHILDREN_PER_DOMAIN`` documents, and nothing about that remainder is
    bounded below: at 501, 502 and 1001 documents of one kind the naive cut's
    last batch would hold 1, 2 and 1 document respectively -- short of
    ``min_children`` (``ForestOptions.min_children_per_summary``, floor 3 by
    default) -- and :meth:`ForestBuilder._node_over_nodes` refuses to build a
    node under that floor, so those documents would be present in the Document
    tier and unreachable from every Domain node: orphaned rather than summarised.
    A tail short of the floor is folded into the batch before it instead, which
    is always exactly ``MAX_CHILDREN_PER_DOMAIN`` (only the last cut can be
    short), so a merge can push a batch up to at most
    ``MAX_CHILDREN_PER_DOMAIN + min_children - 1`` documents -- 502 at the
    defaults, still far under the character budget
    :data:`MAX_CHILDREN_PER_DOMAIN`'s own margin is sized against. The merge
    only ever touches the last batch boundary; every earlier batch, and the
    ``node_id`` order the whole partition is sliced from, is unchanged.
    """
    ordered = sorted(documents, key=lambda node: node.node_id)
    if len(ordered) <= MAX_CHILDREN_PER_DOMAIN:
        return [(kind, ordered)]

    starts = list(range(0, len(ordered), MAX_CHILDREN_PER_DOMAIN))
    if len(ordered) - starts[-1] < min_children:
        starts.pop()
    ends = [*starts[1:], len(ordered)]
    return [
        (f"{kind}#{index}", ordered[start:end])
        for index, (start, end) in enumerate(zip(starts, ends, strict=True))
    ]


def _by_scope(chunks: Iterable[IndexableChunk]) -> list[tuple[Scope, list[IndexableChunk]]]:
    """Chunks partitioned by scope, in a total order over the scope key.

    Sorted rather than left in insertion order: a dict preserves the order the
    corpus arrived in, and that order is exactly what ADR-0008 decision 9
    forbids from reaching the output.
    """
    grouped: dict[Scope, list[IndexableChunk]] = {}
    for chunk in chunks:
        grouped.setdefault(_scope_of(chunk), []).append(chunk)
    return sorted(grouped.items(), key=lambda entry: entry[0].key)


def _by_item(chunks: Iterable[IndexableChunk]) -> list[tuple[str, str, list[IndexableChunk]]]:
    """``(kind, item_id, chunks)`` per item revision, ordered by item then revision.

    Grouped on the revision as well as the item so that a Document node's
    ``source_revision_id`` names exactly one revision -- see
    :meth:`ForestBuilder._document_node`. A build indexes one revision per item,
    so this is one group per item there; it is a property of the grouping rather
    than of the caller.

    ``kind`` is read off the group's first chunk because every chunk of one
    revision carries that revision's kind. It travels beside the group instead
    of inside the key so that an item is never split by it.
    """
    grouped: dict[tuple[str, str], list[IndexableChunk]] = {}
    for chunk in chunks:
        grouped.setdefault((chunk.item_id, chunk.revision_id), []).append(chunk)
    return [
        (group[0].kind, item_id, group)
        for (item_id, _revision_id), group in sorted(grouped.items(), key=lambda entry: entry[0])
    ]


__all__ = ["MAX_CHILDREN_PER_DOMAIN", "SUMMARY_MAX_TOKENS", "ForestBuilder", "ForestOptions"]
