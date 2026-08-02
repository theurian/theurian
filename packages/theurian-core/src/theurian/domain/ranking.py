"""Fusion, diversification, and packing (FR-R2, FR-R4).

Pure functions over ranked lists. Nothing here reads an index, embeds a query, or
opens a database — which is what lets the ranking behaviour be pinned by tests
that would otherwise need a corpus, a model, and a machine to run them on.

This module owns the middle of the pipeline::

    filter → [ lexical ranking ] ┐
                                 ├→ fuse → diversify → pack → results
             [ dense ranking   ] ┘

Filtering happens before ranking and belongs to the store (FR-R1). Provenance and
trust labelling happen after packing and belong to :mod:`theurian.domain.retrieval`
(FR-R5, FR-R6).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
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


class RetrievalMode(StrEnum):
    """Which retrievers contributed to a result set.

    Reported on every response so a caller can tell a hybrid answer from a
    degraded one. A vector index that failed to build should visibly reduce a
    search to lexical, never silently return worse answers that look the same.
    """

    LEXICAL = "lexical"
    DENSE = "dense"
    HYBRID = "hybrid"


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
class Fused:
    """A candidate after fusion, with the evidence for its position."""

    chunk_id: str
    item_id: str
    revision_id: str
    fused_score: float
    #: Which retrievers found it, and at what rank. This is what makes a ranking
    #: explainable rather than merely reproducible.
    ranks: Mapping[str, int] = field(default_factory=dict)

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


@dataclass(frozen=True, slots=True)
class Packed:
    """What fitted in the budget, and what did not."""

    candidates: tuple[Fused, ...]
    used_tokens: int
    #: Candidates dropped for space. Reported rather than discarded, so a caller
    #: can tell "nothing else matched" from "your budget ran out".
    dropped: int = 0


def pack(
    candidates: Sequence[Fused],
    sizes: Mapping[str, int],
    *,
    budget_tokens: int,
) -> Packed:
    """Take candidates in rank order until the budget is spent (FR-R4).

    Strictly in order, never a knapsack fill. Skipping a large high-ranked result
    to fit two small low-ranked ones would quietly reorder relevance to optimise
    a number the caller cannot see.

    Always returns at least one candidate when any exist: a caller whose budget
    is smaller than the single best result is better served by one over-long
    answer they can truncate than by an empty one they cannot act on.
    """
    if budget_tokens < 1:
        msg = f"budget_tokens must be at least 1, got {budget_tokens}"
        raise RankingError(msg)

    kept: list[Fused] = []
    used = 0
    for candidate in candidates:
        # A missing size means the caller could not price this candidate.
        # Charging the whole budget is the conservative reading; treating it as
        # free is how a budget is silently exceeded, and `estimate_tokens`
        # already errs high for the same reason.
        cost = sizes.get(candidate.chunk_id, budget_tokens)
        if kept and used + cost > budget_tokens:
            break
        kept.append(candidate)
        used += cost

    return Packed(candidates=tuple(kept), used_tokens=used, dropped=len(candidates) - len(kept))


def mode_of(rankings: Mapping[str, Sequence[Ranked]]) -> RetrievalMode:
    """Which retrievers actually contributed anything.

    A dense index that is missing or empty degrades a search to lexical. Saying
    so is the difference between a caller trusting a hybrid answer and a caller
    trusting a worse answer that looks identical.
    """
    contributing = {name for name, results in rankings.items() if results}
    if DENSE in contributing and contributing - {DENSE}:
        return RetrievalMode.HYBRID
    if contributing == {DENSE}:
        return RetrievalMode.DENSE
    return RetrievalMode.LEXICAL
