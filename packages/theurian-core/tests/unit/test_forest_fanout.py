"""The Domain tier at scale: it fans out rather than overflowing (ADR-0008).

**Written RED, ahead of the Domain fan-out fix**, the way
``test_forest_derivation.py`` was written ahead of the builder itself. Nothing
in the shipped builder caps how many document nodes one Domain node stands on:
``min_children_per_summary`` is a *floor*, and the builder's own cost note
bounds only the Catalog tier ("the input to a Catalog node grows with the number
of kinds and not with the corpus"), leaving the Domain tier's growth linear in
the corpus. A same-kind corpus large enough drives one Domain node's input past
``ExtractiveSummarizer.MAX_TOTAL_INPUT_CHARS`` and the build refuses; short of
that it mints a single unbounded Domain node.

The settled fix: a Domain tier with more than ``MAX_CHILDREN_PER_DOMAIN``
children splits into several Domain nodes, each batch a deterministic slice of
the children sorted by ``node_id``, with a Catalog over the batches. This file
imports that constant, which does not exist yet -- so the fan-out test errors
until the fix lands, and the constant is what its post-fix assertions are sized
against.

Unit, not integration: this is a property of ``ForestBuilder.derive`` over a
large in-memory corpus, and driving hundreds of items through the real CLI would
cost minutes for a fact the pure function already decides. See the report note on
this deviation from the brief's file placement.
"""

from __future__ import annotations

from typing import Any

import pytest

from theurian.domain.chunking import Chunk, IndexableChunk
from theurian.domain.raptor import NodeType, tree_identity
from theurian.infrastructure.raptor.extractive import (
    MAX_TOTAL_INPUT_CHARS,
    ExtractiveSummarizer,
)

pytestmark = pytest.mark.unit

PROJECT = "demo"
DOCUMENT_LEVEL = 1
DOMAIN_LEVEL = 2

_SENTENCES = (
    "Every call carries a signed token issued by the gateway service.",
    "Tokens rotate on restart and expire after one hour of idle time.",
    "The quarantine ledger records every revoked token and its reason.",
)


def _one_kind_corpus(items: int, *, kind: str = "architecture") -> list[IndexableChunk]:
    """``items`` items of one kind, three chunks each, in one scope.

    Short chunk text on purpose: the fan-out is a function of the *number* of
    children, not their length, and short summaries keep a corpus of hundreds of
    items well clear of the character limit so the current single-node build does
    not merely refuse.
    """
    corpus: list[IndexableChunk] = []
    for number in range(items):
        item = f"{kind}.item-{number:05d}"
        revision = f"rev-{number:05d}"
        for ordinal in range(3):
            corpus.append(
                IndexableChunk(
                    chunk=Chunk(
                        chunk_id=f"{revision}#{ordinal}",
                        ordinal=ordinal,
                        text=f"{item} section {ordinal}. {_SENTENCES[ordinal % 3]}",
                        heading="",
                    ),
                    project_id=PROJECT,
                    item_id=item,
                    revision_id=revision,
                    status="approved",
                    sensitivity="internal",
                    trust_level="reviewed",
                    namespace="backend",
                    kind=kind,
                )
            )
    return corpus


def _derive(chunks: list[IndexableChunk]) -> tuple[Any, ...]:
    from theurian.application.forest_builder import ForestBuilder, ForestOptions

    return ForestBuilder(summarizer=ExtractiveSummarizer(), options=ForestOptions()).derive(chunks)


def test_a_domain_tier_over_many_documents_fans_out_into_bounded_batches() -> None:
    """A kind with more than ``MAX_CHILDREN_PER_DOMAIN`` documents splits into
    several Domain nodes, deterministically, and every document is under exactly
    one of them.

    The shipped builder puts every document of a kind under one Domain node, so
    the ``len(domains) > 1`` assertion is red today (there is exactly one) and
    the constant it is sized against does not exist -- both are the RED this
    file is written to be. The fix must leave four things true, and all four are
    asserted so none can be satisfied by a builder that fans out wrongly: the
    build does not refuse; more than one Domain node carries the kind; no Domain
    node exceeds the cap; and the document nodes partition across the Domain
    nodes, each reachable from exactly one. Determinism is checked by deriving
    the same corpus twice and demanding identical node ids and texts, because a
    partition that depended on iteration order would break ADR-0008 decision 9's
    two-corpus equality.

    **What the partition check here does not reach, stated because it once read
    as if it did.** ``MAX_CHILDREN_PER_DOMAIN + 3`` leaves a last batch of
    exactly 3 documents -- ``minChildrenPerSummary``'s own default -- which
    meets the threshold rather than falling short of it, so this fixture never
    puts a batch *below* the floor and cannot show what happens to one that
    does. ``test_a_tail_batch_below_the_next_threshold_merges_rather_than_
    orphaning`` below is the one sized to land there; this test's job is the
    partition at a remainder that clears the floor, not under it.

    The cap itself must respect the character limit it exists to stay under:
    ``MAX_CHILDREN_PER_DOMAIN`` summaries at ``summary_max_tokens`` each must fit
    ``MAX_TOTAL_INPUT_CHARS`` with margin, or a full batch would drive the very
    refusal the fan-out removes.
    """
    from theurian.application.forest_builder import (
        MAX_CHILDREN_PER_DOMAIN,
        SUMMARY_MAX_TOKENS,
    )
    from theurian.domain.ranking import CHARS_PER_TOKEN

    assert MAX_CHILDREN_PER_DOMAIN * SUMMARY_MAX_TOKENS * CHARS_PER_TOKEN < MAX_TOTAL_INPUT_CHARS, (
        "a full Domain batch's input must stay under the summariser's character limit, "
        "or the cap does not remove the overflow it exists to prevent"
    )

    corpus = _one_kind_corpus(MAX_CHILDREN_PER_DOMAIN + 3)

    nodes = _derive(corpus)
    again = _derive(corpus)

    documents = {node.node_id for node in nodes if node.level == DOCUMENT_LEVEL}
    domains = [node for node in nodes if node.level == DOMAIN_LEVEL]
    assert len(documents) == MAX_CHILDREN_PER_DOMAIN + 3, (
        "every full item must earn a document node"
    )
    assert len(domains) > 1, (
        "one kind's documents all landed under a single Domain node -- the tier did not "
        "fan out above the cap"
    )
    for domain in domains:
        assert len(domain.source_node_ids) <= MAX_CHILDREN_PER_DOMAIN, (
            f"a Domain node stands on {len(domain.source_node_ids)} documents, past the cap"
        )

    covered: list[str] = [node_id for domain in domains for node_id in domain.source_node_ids]
    assert sorted(covered) == sorted(documents), (
        "the Domain batches do not partition the document nodes -- one is orphaned or counted twice"
    )
    assert len(covered) == len(set(covered)), "a document node is under two Domain nodes"

    def _shape(built: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        return sorted((node.node_id, node.level, node.text) for node in built)

    assert _shape(nodes) == _shape(again), "the fan-out is not deterministic across rebuilds"


# -- The tail batch, specifically -------------------------------------------
#
# The fixture above is sized at ``MAX_CHILDREN_PER_DOMAIN + 3``, whose only
# batch above the cap holds exactly ``minChildrenPerSummary`` (3) documents --
# never fewer. `_domain_batches` slices into fixed-size chunks of
# `MAX_CHILDREN_PER_DOMAIN`, and nothing merges a final chunk that lands below
# the next tier's own floor: at 501, 502 and 1001 documents of one kind the
# last chunk is 1, 2 and 1 document respectively, each short of the default
# `minChildrenPerSummary` of 3, so `_node_over_nodes` refuses to build a node
# for it and that batch's documents are silently unreachable from the Domain
# tier -- present in `nodes`, absent from every `source_node_ids`. The fix:
# a final batch that would fall below the threshold is merged into the batch
# before it rather than dropped, which is why every size below pins both the
# exact batch count *and* the exact batch sizes -- "no orphan" alone would
# also be satisfied by a batch that still exists but is simply smaller than
# pinned, or one merged the wrong way.


@pytest.mark.parametrize(
    ("items", "batch_sizes"),
    [
        (500, [500]),  # exactly the cap: one whole, unpartitioned batch
        (501, [501]),  # remainder 1, below the floor of 3: merges into batch 0
        (502, [502]),  # remainder 2, below the floor of 3: merges into batch 0
        (503, [500, 3]),  # remainder 3 meets the floor exactly: stays its own batch
        (1000, [500, 500]),  # two whole batches, no remainder to merge
        (1001, [500, 501]),  # remainder 1 on the second batch: merges into it
    ],
)
def test_a_tail_batch_below_the_next_threshold_merges_rather_than_orphaning(
    items: int, batch_sizes: list[int]
) -> None:
    """A final Domain batch too small to earn its own node is folded into the
    batch before it, so its documents stay reachable rather than vanishing.

    Distinct from the fixture above, which never drives a remainder below
    `minChildrenPerSummary`: this one is chosen exactly to. The pinned sizes
    are asserted order-independently (`sorted`) because a batch's *discriminator*
    -- which of two same-sized batches is `kind#0` versus `kind#1` -- is not
    what this test is about; `test_the_domain_batch_boundary_names_the_bare_
    kind_only_up_to_the_cap` pins that separately. What is pinned here is the
    *shape* of the partition: how many batches, and how large each one is,
    which is exactly what "no orphan" alone cannot distinguish from a batch
    that merged into the wrong neighbour or dropped a document some other way.
    """
    corpus = _one_kind_corpus(items)

    nodes = _derive(corpus)

    documents = {node.node_id for node in nodes if node.level == DOCUMENT_LEVEL}
    domains = [node for node in nodes if node.level == DOMAIN_LEVEL]
    assert len(documents) == items, "every item must earn a document node"
    assert len(domains) == len(batch_sizes), (
        f"N={items}: expected {len(batch_sizes)} Domain batches, got {len(domains)} -- "
        f"the batch count is not ceil-correct once the tail merge is accounted for"
    )
    assert sorted(len(d.source_node_ids) for d in domains) == sorted(batch_sizes), (
        f"N={items}: the Domain batches are not the pinned sizes {batch_sizes}"
    )

    covered = [node_id for domain in domains for node_id in domain.source_node_ids]
    assert sorted(covered) == sorted(documents), (
        f"N={items}: a document node is unreachable from every Domain node -- the tail "
        f"merge dropped it instead of folding it into the previous batch"
    )
    assert len(covered) == len(set(covered)), (
        f"N={items}: a document node is under two Domain nodes"
    )


# -- Batch identity: which document, which discriminator, which tree --------
#
# The assertions above -- batch count, batch size, "every document under
# exactly one batch" -- are all blind to *which* document lands in *which*
# batch, and to whether two batches that happen to be the same size are also
# two distinct trees. Three properties of `_domain_batches` live entirely in
# that blind spot, and each of the three tests below pins exactly one:
# slicing by content-addressed order rather than derivation order, a distinct
# tree id per batch, and which side of the cap keeps the bare `kind`
# discriminator.


def test_the_domain_batches_slice_by_node_id_not_by_derivation_order() -> None:
    """The cap-sized batch holds the *lowest-node_id* documents of the kind,
    not whichever ``MAX_CHILDREN_PER_DOMAIN`` documents the corpus happened to
    derive first.

    `_domain_batches` sorts its documents by ``node_id`` before slicing, so the
    partition is a function of content alone (ADR-0008 decision 9) rather than
    of `_by_item`'s own order -- item id, then revision id -- which is what the
    documents arrive in before this function ever touches them. Dropping that
    sort would still satisfy every assertion in the fixture above: batch
    count, the cap, and partition membership as a *set* all hold regardless of
    which specific document lands in which specific batch, so none of them can
    tell content-addressed order from derivation order. This test can, because
    it names the two orders separately and demands the batch match one and not
    the other.
    """
    from theurian.application.forest_builder import MAX_CHILDREN_PER_DOMAIN

    corpus = _one_kind_corpus(MAX_CHILDREN_PER_DOMAIN + 3)

    nodes = _derive(corpus)

    documents = [node for node in nodes if node.level == DOCUMENT_LEVEL]
    by_derivation_order = sorted(documents, key=lambda node: node.source_chunk_ids[0])
    by_node_id = sorted(documents, key=lambda node: node.node_id)
    lowest_node_id_group = {node.node_id for node in by_node_id[:MAX_CHILDREN_PER_DOMAIN]}
    first_derived_group = {node.node_id for node in by_derivation_order[:MAX_CHILDREN_PER_DOMAIN]}
    assert lowest_node_id_group != first_derived_group, (
        "node_id order coincides with derivation order in this fixture, so slicing by "
        "either would satisfy the assertion below and the sort this test pins is untested"
    )

    domains = [node for node in nodes if node.level == DOMAIN_LEVEL]
    batch = next(node for node in domains if len(node.source_node_ids) == MAX_CHILDREN_PER_DOMAIN)
    assert set(batch.source_node_ids) == lowest_node_id_group, (
        "the cap-sized Domain batch does not hold the lowest-node_id documents -- the "
        "slice followed derivation order rather than content-addressed order"
    )


def test_each_domain_batch_over_one_kind_mints_a_distinct_tree_id() -> None:
    """Two Domain batches over the same kind must not collide on one tree id.

    `_domain_batches` keys a partitioned batch on ``kind`` joined with its
    partition index (``kind#0``, ``kind#1``, ...) rather than on the bare
    ``kind``, precisely so two batches of one kind mint two trees rather than
    one. The existing fan-out fixture already proves the two batches here are
    not duplicates of *content* -- their sizes are 500 and 3 -- but nothing
    before this test compares their `tree_id`s, and a discriminator that
    dropped the partition index would still leave every other assertion in
    this file satisfied: the batch count, the cap, and the partition are all
    about documents, not about the tree each batch's Domain node belongs to.
    """
    from theurian.application.forest_builder import MAX_CHILDREN_PER_DOMAIN

    corpus = _one_kind_corpus(MAX_CHILDREN_PER_DOMAIN + 3)

    nodes = _derive(corpus)

    domains = [node for node in nodes if node.level == DOMAIN_LEVEL]
    assert len(domains) >= 2, (
        "fewer than two Domain batches over one kind -- there is nothing here for "
        "tree_id distinctness to be about"
    )
    tree_ids = [node.tree_id for node in domains]
    assert len(set(tree_ids)) == len(tree_ids), (
        "two Domain batches over the same kind share a tree_id -- the partition index "
        "was dropped from the discriminator and the batches collided on the bare kind"
    )


def test_the_domain_batch_boundary_names_the_bare_kind_only_up_to_the_cap() -> None:
    """At exactly ``MAX_CHILDREN_PER_DOMAIN`` documents the Domain node keeps
    the bare ``kind`` discriminator; past the cap, the discriminator is
    partitioned instead -- pinned by ``tree_id``, because size and membership
    do not move at this boundary the way they do at every other one this file
    checks.

    ``_domain_batches`` decides which branch to take with ``len(ordered) <=
    MAX_CHILDREN_PER_DOMAIN``. A batch of exactly the cap has the same size
    (500) and, since there is nothing above it to short a merge into, the same
    membership whether it took the unpartitioned branch or a single-batch
    partitioned one -- only the discriminator, and so the tree id, tells the
    two apart, which is why every assertion elsewhere in this file is blind to
    which side of that boundary ran.

    One document past the cap does cross into the partitioned branch, but the
    tail merge (the section above) folds that lone remainder back into the
    same batch rather than minting a second Domain node -- so
    ``MAX_CHILDREN_PER_DOMAIN + 1`` is one Domain node, the same as
    ``MAX_CHILDREN_PER_DOMAIN`` itself, and what actually moves at the
    boundary is the discriminator alone, from the bare ``kind`` to
    ``kind#0``. The `<=`/`<` choice in the cap comparison is exactly what
    decides which of the two a document count of precisely the cap gets.
    """
    from theurian.application.forest_builder import MAX_CHILDREN_PER_DOMAIN

    at_cap = _derive(_one_kind_corpus(MAX_CHILDREN_PER_DOMAIN))
    past_cap = _derive(_one_kind_corpus(MAX_CHILDREN_PER_DOMAIN + 1))

    at_cap_domain = next(node for node in at_cap if node.level == DOMAIN_LEVEL)
    past_cap_domain = next(node for node in past_cap if node.level == DOMAIN_LEVEL)
    assert len(at_cap_domain.source_node_ids) == MAX_CHILDREN_PER_DOMAIN
    assert len(past_cap_domain.source_node_ids) == MAX_CHILDREN_PER_DOMAIN + 1

    scope = at_cap_domain.node.scope
    bare_kind_tree = tree_identity(
        scope=scope, node_type=NodeType.DOMAIN, discriminator="architecture"
    ).value
    partitioned_tree = tree_identity(
        scope=scope, node_type=NodeType.DOMAIN, discriminator="architecture#0"
    ).value
    assert at_cap_domain.tree_id == bare_kind_tree, (
        "a Domain batch of exactly MAX_CHILDREN_PER_DOMAIN documents did not keep the "
        "bare kind discriminator -- the cap comparison let a boundary count fall through "
        "to the partitioned branch"
    )
    assert past_cap_domain.tree_id == partitioned_tree, (
        "a Domain batch one document past the cap did not take the partitioned "
        "discriminator -- the boundary did not move where the cap comparison says it does"
    )
