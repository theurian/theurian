"""Fusion, diversification, and packing (FR-R2, FR-R4).

Pure functions over ranked lists. Nothing here reads an index, embeds a query, or
opens a database — which is what lets the ranking behaviour be pinned by tests
that would otherwise need a corpus, a model, and a machine to run them on.

This module owns the middle of the pipeline::

    filter → [ lexical ranking   ] ┐
             [ substring ranking ] ├→ fuse → diversify → results → budget
             [ dense ranking     ] ┘

Filtering happens before ranking and belongs to the store (FR-R1). Provenance and
trust labelling happen after ranking and belong to :mod:`theurian.mcp.results`
(FR-R5, FR-R6).

The budget is last on purpose. It is applied to *results*, after the canonical
store has decided which candidates may be shown at all, because a budget spent
on a candidate that is then withheld is both a wrong number and a signal about
withheld content (:func:`take_within_budget`).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from theurian.domain.errors import TheurianError

#: Reciprocal Rank Fusion's smoothing constant. 60 is the value from Cormack et
#: al. (2009) and the one comparable systems use. A constant rather than a
#: parameter: tuning it per call would make two callers see different orders for
#: the same query against the same index, which FR-R7 rules out.
RRF_K: Final = 60

#: Rough characters per token for scripts that use spaces. See
#: :func:`estimate_tokens`.
CHARS_PER_TOKEN: Final = 4

#: Tokens per character for CJK, kana, and emoji. Measured against `cl100k_base`
#: on Japanese prose: 450 characters tokenized to ~600, so one-token-per-
#: character still *under*-counts by a quarter. 1.5 keeps the estimate on the
#: conservative side this module promises, which four-characters-per-token — the
#: English heuristic — misses by roughly fivefold.
_DENSE_TOKENS_PER_CHAR: Final = 1.5

# Either rate, and the ranges below, also decide which sentences the RAPTOR
# summarizer keeps, and that choice is persisted per node and staled against its
# `SEMANTICS_VERSION` (ADR-0008 decision 5) — so a change here is a bump there.
# `test_extractive_summarizer.py` pins the two rates to put that in front of
# review; the ranges are not pinned, so adding one needs the same judgement
# without a red test to prompt it.

#: Ranges whose characters are counted at the dense rate above.
_DENSE_SCRIPT_RANGES: Final = (
    (0x3000, 0x30FF),  # CJK punctuation, hiragana, katakana
    (0x3400, 0x4DBF),  # CJK extension A
    (0x4E00, 0x9FFF),  # CJK unified ideographs
    (0xAC00, 0xD7AF),  # Hangul syllables
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
    (0xFF00, 0xFFEF),  # Halfwidth and fullwidth forms
    (0x1F300, 0x1FAFF),  # Emoji
)

#: The retriever names this module expects.
#: `mode_of` compares against these, so a caller that invents its own name
#: gets LEXICAL rather than a silently wrong mode.
LEXICAL: Final = "lexical"
#: Trigram substring matching. A second *lexical* strategy, not a semantic one:
#: it exists because `unicode61` cannot segment scripts without word boundaries.
SUBSTRING: Final = "substring"
DENSE: Final = "dense"
#: Forest routing (ADR-0008 decision 8). A leaf reached by matching a summary
#: node's text and descending `node_derivation` to the chunks beneath it, rather
#: than by matching the leaf's own text. Named as its own retriever so a leaf
#: found only through the forest is attributed honestly: hiding it under a leaf
#: retriever's name would be a false published claim about how the hit was found.
SUMMARY: Final = "summary"


class RetrievalMode(StrEnum):
    """Which retrievers contributed to a result set.

    Reported on every response so a caller can tell a hybrid answer from a
    degraded one. A vector index that failed to build should visibly reduce a
    search to lexical, never silently return worse answers that look the same.
    """

    LEXICAL = "lexical"
    DENSE = "dense"
    HYBRID = "hybrid"
    #: No retriever contributed anything.
    #:
    #: Distinct from LEXICAL, which used to cover this case and so reported
    #: "the word index answered" for a search where nothing answered at all.
    #: That is the exact signature of the failures worth catching -- a v1 index
    #: with no trigram table, an embedder whose vectors do not match the corpus,
    #: a query in a script the lexical retriever cannot segment -- and it was
    #: indistinguishable from a healthy search over a corpus that simply had no
    #: match.
    NONE = "none"


class RankingError(TheurianError):
    """A ranking could not be produced. Carries a remedy, never a stack trace."""


@dataclass(frozen=True, slots=True)
class Ranked:
    """One retriever's opinion about one chunk.

    ``score`` is deliberately *not* used for fusion. Lexical BM25 and cosine
    similarity are not comparable quantities, and normalising them onto one scale
    needs assumptions about their distributions that do not survive a change of
    corpus or of embedding model. RRF uses rank alone, which is why it travels.
    """

    chunk_id: str
    item_id: str
    revision_id: str
    #: The retriever's own score, kept for explanation and never for fusion.
    score: float = 0.0


@dataclass(frozen=True, slots=True)
class RetrieverPage:
    """One retriever's answer, and whether it has anything further to give.

    **The second field is the whole point.** The depth-doubling loop in
    :meth:`~theurian.application.retrieval_service.RetrievalService._visible_ranking`
    asks a retriever again, deeper, whenever too few of what came back survived
    the visibility gate, and stops when the retriever is out of rows. Before this
    type existed it had to reconstruct "out of rows" from a row count, and one
    expression read three different ``limit`` semantics off that one number: a
    ceiling in ``search_lexical``, a floor in ``search_substring``, and no
    ``limit`` at all in ``search_dense``. The port's docstrings spent a great deal
    of prose explaining which rule applied where, and prose is not enforcement —
    an adapter that capped its output above ``limit`` without that cap being
    exhaustive satisfied every word of it and cost the caller rows it never
    learned it lost.

    ``exhausted`` may be ``True`` only when the implementation has **verified**
    there is nothing further, not when it merely handed back fewer rows than it
    was asked for. Verifying costs something — the SQLite adapter asks for one row
    past the limit and reports whether it arrived — and that cost is the price of
    the loop no longer guessing.
    """

    rows: tuple[Ranked, ...]
    #: ``True`` when this is the whole of what the retriever has for this query.
    exhausted: bool

    def __post_init__(self) -> None:
        # An empty page that claims more is coming is a non-terminating loop:
        # `_deeper` doubles without a ceiling, and a retriever that keeps
        # answering "nothing yet, ask deeper" is never contradicted. It is also
        # not a state a conforming implementation can reach -- every method on
        # `IndexStore` ranks best-first and counts `limit` from the top, so no
        # rows at one depth means no rows at any greater one. Refused here rather
        # than defended against in the loop, because the loop cannot tell an
        # honest empty page from this one.
        if not self.rows and not self.exhausted:
            raise RankingError(
                "A retriever returned no rows while reporting itself not exhausted. "
                "Ranking best-first means no rows at one depth is no rows at any "
                "greater depth, so this page cannot be honest. Fix the adapter to "
                "report `exhausted=True` when it has nothing to return."
            )


@dataclass(frozen=True, slots=True)
class Fused:
    """A candidate after fusion, with the evidence for its position."""

    chunk_id: str
    item_id: str
    revision_id: str
    fused_score: float
    #: Which retrievers found it, and at what rank. This is what makes a ranking
    #: explainable rather than merely reproducible.
    ranks: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # `frozen=True` freezes the binding, not what it points at. A plain dict
        # here was reachable and mutable through every candidate this module
        # hands out, and `found_by` derived from it goes out on the wire as
        # `foundBy` -- so one caller editing it changes another caller's answer.
        # Copied first, so the dict the constructor was given is not a way in
        # either.
        object.__setattr__(self, "ranks", MappingProxyType(dict(self.ranks)))

    @property
    def found_by(self) -> tuple[str, ...]:
        return tuple(sorted(self.ranks))

    @property
    def agreed(self) -> bool:
        """Whether more than one retriever surfaced it.

        Agreement between a lexical and a dense retriever is the strongest cheap
        signal available: the terms matched *and* the meaning matched.
        """
        return len(self.ranks) > 1


def reciprocal_rank_fusion(
    rankings: Mapping[str, Sequence[Ranked]], *, k: int = RRF_K
) -> tuple[Fused, ...]:
    """Fuse ranked lists by reciprocal rank (FR-R2).

    Each retriever contributes ``1 / (k + rank)`` for every chunk it ranked. A
    chunk found at rank 1 by one retriever and rank 40 by another outranks a
    chunk found at rank 3 by one and nowhere by the other — which is the
    behaviour worth having, because agreement is evidence.

    Known limitation, and the reason the tie-break below is documented twice.
    Two chunks ranked ``(i, j)`` and ``(j, i)`` by two retrievers score
    *exactly* equal — the sum is symmetric — so the tie-break, not the fusion,
    decides which comes first. It breaks on ``chunk_id``, which is
    ``<revision ULID>#<ordinal>``, so those pairs come out in **revision
    creation order**: a determinism device standing in for a relevance
    judgement it cannot make.

    Not rare. Measured over a 30-document corpus and 15 queries with the lexical
    and trigram retrievers: 12 of 135 adjacent top-10 pairs, 9%, were exact
    ties. The share is corpus-dependent — an independent measurement on a
    different corpus put it at 16% — but the mechanism is not.

    Breaking such a tie on relevance needs a per-retriever weighting, which is a
    decision this milestone did not take (M6). Until then the order within a tie
    is reproducible and arbitrary, and saying so is better than letting a caller
    read it as ranking.

    Args:
        rankings: Retriever name to its ordered results, best first.
        k: Smoothing constant. Larger values flatten the advantage of top ranks,
            making agreement matter more relative to position.

    Returns:
        Candidates ordered best first. Ties break on chunk id, so two runs over
        the same data produce the same order (FR-R7).
    """
    if k < 1:
        msg = f"RRF k must be at least 1, got {k}"
        raise RankingError(msg)

    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    identity: dict[str, Ranked] = {}

    for retriever, results in rankings.items():
        for position, result in enumerate(results, start=1):
            scores[result.chunk_id] = scores.get(result.chunk_id, 0.0) + 1.0 / (k + position)
            ranks.setdefault(result.chunk_id, {})[retriever] = position
            identity.setdefault(result.chunk_id, result)

    fused = [
        Fused(
            chunk_id=chunk_id,
            item_id=identity[chunk_id].item_id,
            revision_id=identity[chunk_id].revision_id,
            fused_score=score,
            ranks=dict(sorted(ranks[chunk_id].items())),
        )
        for chunk_id, score in scores.items()
    ]
    # Descending score, then ascending chunk id. The second key is what makes the
    # order total: without it, equal scores come out in dict order, which is
    # insertion order, which depends on which retriever happened to answer first.
    fused.sort(key=lambda candidate: (-candidate.fused_score, candidate.chunk_id))
    return tuple(fused)


def diversify(candidates: Iterable[Fused], *, per_item: int = 2) -> tuple[Fused, ...]:
    """Cap how many chunks any one knowledge item may contribute (FR-R4).

    A long document tends to win every lexical rank simply by containing the
    query terms many times, crowding out a short document that answers the
    question directly. Capping per item costs a little on the rare query that
    genuinely concerns one document, and buys back the ability to see a second
    opinion at all.

    Order within the cap is preserved, so each item keeps its best chunks.
    """
    if per_item < 1:
        msg = f"per_item must be at least 1, got {per_item}"
        raise RankingError(msg)

    seen: dict[str, int] = {}
    kept: list[Fused] = []
    for candidate in candidates:
        count = seen.get(candidate.item_id, 0)
        if count >= per_item:
            continue
        seen[candidate.item_id] = count + 1
        kept.append(candidate)
    return tuple(kept)


def _is_dense_script(character: str) -> bool:
    code = ord(character)
    return any(low <= code <= high for low, high in _DENSE_SCRIPT_RANGES)


def estimate_tokens(text: str) -> int:
    """A conservative token estimate.

    Deliberately an over-estimate. Exceeding a caller's budget silently drops the
    end of their context — often including their own instructions — while
    under-filling it merely costs a little recall.

    Scripts are counted differently because they tokenize differently. A
    character-count heuristic tuned on English under-counts Japanese roughly
    fivefold, so a project whose knowledge is written in Japanese — which this
    one's is — would blow every budget it was given while the estimate insisted
    it had erred high.

    A real tokenizer arrives when one is a dependency worth taking (ADR-0009 —
    no vendor lock-in).
    """
    dense = sum(1 for character in text if _is_dense_script(character))
    sparse = len(text) - dense
    return max(1, math.ceil(dense * _DENSE_TOKENS_PER_CHAR) + -(-sparse // CHARS_PER_TOKEN))


def take_within_budget(costs: Sequence[int], *, budget_tokens: int) -> tuple[int, int]:
    """How many leading items fit a budget, and what they cost (FR-R4).

    Strictly a prefix, never a knapsack fill. Skipping a large high-ranked result
    to fit two small low-ranked ones would quietly reorder relevance to optimise
    a number the caller cannot see.

    Always takes at least one when any exist: a caller whose budget is smaller
    than the single best result is better served by one over-long answer they can
    truncate than by an empty one they cannot act on.

    The only budget rule in the system, and deliberately so. There was a second
    one — a `pack` over chunk sizes, applied to *candidates* before they were
    resolved into results — and having two meant the number reported to the
    caller was measured on something other than what was sent to them: a chunk
    body rather than a result payload, and a candidate set that still held
    entries the canonical store would later withhold. Both defects follow from
    charging in one place and sending from another, so there is now one place.

    Returns:
        ``(count, used_tokens)``.
    """
    if budget_tokens < 1:
        msg = f"budget_tokens must be at least 1, got {budget_tokens}"
        raise RankingError(msg)

    used = 0
    for count, cost in enumerate(costs):
        if count and used + cost > budget_tokens:
            return count, used
        used += cost
    return len(costs), used


def mode_of(contributors: Iterable[str]) -> RetrievalMode:
    """Which retrievers are behind a set of results.

    A dense index that is missing or empty degrades a search to lexical. Saying
    so is the difference between a caller trusting a hybrid answer and a caller
    trusting a worse answer that looks identical.

    Takes the retriever names *carried by the results being returned*, not the
    rankings that produced them, even though the canonical store's gate now runs
    before fusion (:class:`~theurian.application.retrieval_service.Visibility`),
    so a ranking should already hold nothing the caller may not see. Deriving the
    mode from the results themselves rather than trusting an upstream ordering
    to hold is what keeps a caller from learning anything through this field if
    that ordering ever regresses — SEC-13 is not supposed to depend on this
    function remembering where the gate currently lives.
    """
    contributing = set(contributors)
    if not contributing:
        return RetrievalMode.NONE
    if DENSE in contributing and contributing - {DENSE}:
        return RetrievalMode.HYBRID
    if contributing == {DENSE}:
        return RetrievalMode.DENSE
    return RetrievalMode.LEXICAL
