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
    nodes -- each reachable from exactly one, none orphaned below the next
    threshold. Determinism is checked by deriving the same corpus twice and
    demanding identical node ids and texts, because a partition that depended on
    iteration order would break ADR-0008 decision 9's two-corpus equality.

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
