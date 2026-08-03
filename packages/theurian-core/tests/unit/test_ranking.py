"""Fusion, diversification, and packing (FR-R2, FR-R4).

Pure functions, so these are the cheapest tests in the suite and the ones that
pin the behaviour everything else in retrieval depends on.
"""

from __future__ import annotations

import pytest

from theurian.domain.ranking import (
    DENSE,
    LEXICAL,
    RRF_K,
    Fused,
    Ranked,
    RankingError,
    RetrievalMode,
    diversify,
    estimate_tokens,
    mode_of,
    pack,
    reciprocal_rank_fusion,
)


def _ranked(chunk: str, item: str = "item.a") -> Ranked:
    return Ranked(chunk_id=chunk, item_id=item, revision_id=f"rev-{chunk}")


def _fused(chunk: str, item: str, score: float = 1.0) -> Fused:
    return Fused(chunk_id=chunk, item_id=item, revision_id=f"rev-{chunk}", fused_score=score)


# -- Fusion ----------------------------------------------------------------


def test_agreement_beats_a_single_strong_rank() -> None:
    """The property RRF exists for. A chunk both retrievers found, neither at
    the top, should outrank one that only a single retriever loved."""
    both = _ranked("agreed")
    fused = reciprocal_rank_fusion(
        {
            LEXICAL: [_ranked("lexical-favourite"), both],
            DENSE: [_ranked("dense-favourite"), both],
        }
    )

    assert fused[0].chunk_id == "agreed"
    assert fused[0].agreed


def test_fusion_uses_rank_not_score() -> None:
    """BM25 and cosine similarity are not comparable quantities. A retriever
    reporting enormous scores must not be able to buy the top slot."""
    modest = Ranked(chunk_id="modest", item_id="i", revision_id="r", score=0.01)
    enormous = Ranked(chunk_id="enormous", item_id="i", revision_id="r", score=9999.0)

    fused = reciprocal_rank_fusion({LEXICAL: [modest, enormous]})

    assert fused[0].chunk_id == "modest", "rank 1 wins regardless of score"


def test_a_chunk_found_by_one_retriever_still_ranks() -> None:
    """Dense-only recall is the point of having a dense retriever."""
    fused = reciprocal_rank_fusion({LEXICAL: [], DENSE: [_ranked("only-dense")]})

    assert [c.chunk_id for c in fused] == ["only-dense"]
    assert not fused[0].agreed


def test_the_contribution_of_a_rank_is_the_documented_formula() -> None:
    """Pinning the formula, not just its consequences: a change here silently
    reorders every result in the system."""
    fused = reciprocal_rank_fusion({LEXICAL: [_ranked("first")]})

    assert fused[0].fused_score == pytest.approx(1.0 / (RRF_K + 1))


def test_ties_break_deterministically() -> None:
    """FR-R7. Two runs over the same data must produce the same order, or a
    pinned snapshot does not reproduce anything."""
    rankings = {LEXICAL: [_ranked("b"), _ranked("a")], DENSE: [_ranked("a"), _ranked("b")]}

    first = [c.chunk_id for c in reciprocal_rank_fusion(rankings)]
    second = [c.chunk_id for c in reciprocal_rank_fusion(dict(reversed(list(rankings.items()))))]

    assert first == second
    assert first == ["a", "b"], "equal scores break on chunk id, ascending"


def test_the_ranks_that_produced_a_position_are_reported() -> None:
    """A ranking nobody can explain is a ranking nobody can debug."""
    fused = reciprocal_rank_fusion({LEXICAL: [_ranked("x"), _ranked("y")], DENSE: [_ranked("y")]})
    y = next(c for c in fused if c.chunk_id == "y")

    assert y.ranks == {DENSE: 1, LEXICAL: 2}
    assert y.found_by == (DENSE, LEXICAL)


def test_fusing_nothing_yields_nothing() -> None:
    assert reciprocal_rank_fusion({LEXICAL: [], DENSE: []}) == ()


@pytest.mark.parametrize("k", [0, -1])
def test_a_nonsensical_k_is_refused(k: int) -> None:
    with pytest.raises(RankingError, match="at least 1"):
        reciprocal_rank_fusion({LEXICAL: [_ranked("a")]}, k=k)


# -- Diversification --------------------------------------------------------


def test_one_long_document_cannot_take_every_slot() -> None:
    """A long document wins lexical ranks by repetition alone. Without a cap it
    crowds out the short document that actually answers the question."""
    candidates = [
        _fused("long-1", "item.long", 0.9),
        _fused("long-2", "item.long", 0.8),
        _fused("long-3", "item.long", 0.7),
        _fused("short-1", "item.short", 0.6),
    ]

    kept = diversify(candidates, per_item=2)

    assert [c.chunk_id for c in kept] == ["long-1", "long-2", "short-1"]


def test_diversification_keeps_the_best_chunks_of_each_item() -> None:
    kept = diversify(
        [_fused("a1", "a", 0.9), _fused("a2", "a", 0.5), _fused("a3", "a", 0.1)], per_item=1
    )

    assert [c.chunk_id for c in kept] == ["a1"]


def test_a_per_item_cap_below_one_is_refused() -> None:
    """It would return nothing at all, which is never what a caller meant."""
    with pytest.raises(RankingError, match="at least 1"):
        diversify([_fused("a", "a")], per_item=0)


# -- Token budget ------------------------------------------------------------


def test_packing_stops_at_the_budget() -> None:
    candidates = [_fused("a", "i"), _fused("b", "i2"), _fused("c", "i3")]
    sizes = {"a": 40, "b": 40, "c": 40}

    packed = pack(candidates, sizes, budget_tokens=100)

    assert [c.chunk_id for c in packed.candidates] == ["a", "b"]
    assert packed.used_tokens == 80
    assert packed.dropped == 1


def test_packing_never_reorders_to_fill_the_budget() -> None:
    """A knapsack fill would skip a large high-ranked result to fit two small
    low-ranked ones, silently trading relevance for a number nobody sees."""
    candidates = [_fused("big", "i", 0.9), _fused("small-1", "j", 0.5), _fused("small-2", "k", 0.4)]
    sizes = {"big": 90, "small-1": 10, "small-2": 10}

    packed = pack(candidates, sizes, budget_tokens=100)

    assert packed.candidates[0].chunk_id == "big"


def test_a_budget_smaller_than_the_best_result_still_returns_it() -> None:
    """One over-long answer a caller can truncate beats an empty one they
    cannot act on."""
    packed = pack([_fused("huge", "i")], {"huge": 10_000}, budget_tokens=10)

    assert [c.chunk_id for c in packed.candidates] == ["huge"]
    assert packed.dropped == 0


def test_the_number_dropped_for_space_is_reported() -> None:
    """ "Nothing else matched" and "your budget ran out" are different answers
    and lead to different next actions."""
    packed = pack(
        [_fused(str(i), f"item{i}") for i in range(10)],
        {str(i): 50 for i in range(10)},
        budget_tokens=100,
    )

    assert packed.dropped == 8


def test_packing_nothing_is_not_an_error() -> None:
    packed = pack([], {}, budget_tokens=100)

    assert packed.candidates == ()
    assert packed.used_tokens == 0


@pytest.mark.parametrize("budget", [0, -5])
def test_a_nonsensical_budget_is_refused(budget: int) -> None:
    with pytest.raises(RankingError, match="at least 1"):
        pack([_fused("a", "i")], {"a": 1}, budget_tokens=budget)


def test_the_token_estimate_errs_high() -> None:
    """Overshooting a caller's budget truncates their context, often including
    their own instructions. Undershooting only costs recall."""
    assert estimate_tokens("a" * 400) >= 100
    assert estimate_tokens("") == 1, "even an empty chunk costs something to send"


# -- Reported mode -----------------------------------------------------------


def test_both_retrievers_contributing_is_hybrid() -> None:
    assert mode_of({LEXICAL: [_ranked("a")], DENSE: [_ranked("b")]}) is RetrievalMode.HYBRID


def test_an_empty_dense_index_degrades_visibly_to_lexical() -> None:
    """The failure this exists to make visible: a vector index that failed to
    build must not silently return worse answers that look the same."""
    assert mode_of({LEXICAL: [_ranked("a")], DENSE: []}) is RetrievalMode.LEXICAL


def test_dense_only_is_reported_as_dense() -> None:
    assert mode_of({LEXICAL: [], DENSE: [_ranked("a")]}) is RetrievalMode.DENSE


def test_no_results_at_all_reports_lexical_rather_than_claiming_hybrid() -> None:
    """An empty result set must not claim a dense retriever ran."""
    assert mode_of({LEXICAL: [], DENSE: []}) is RetrievalMode.LEXICAL


def test_an_unpriced_candidate_is_charged_the_whole_budget() -> None:
    """A missing size means the caller could not price this candidate.

    Charging the whole budget is the conservative reading; treating it as free
    is how a budget is silently exceeded. No test covered this branch, so
    flipping the default to 0 left the entire suite green.
    """
    candidates = [_fused("a", "i1"), _fused("b", "i2"), _fused("c", "i3")]

    packed = pack(candidates, {"a": 10, "c": 10}, budget_tokens=100)

    # `b` is unpriced, so it is charged 100 on top of `a`'s 10 and does not fit.
    # Priced at 0 instead, all three would come back for 20 tokens.
    assert [c.chunk_id for c in packed.candidates] == ["a"]
    assert packed.used_tokens == 10
    assert packed.dropped == 2, "the unpriced candidate and everything behind it"


def test_an_unpriced_first_candidate_still_comes_back_alone() -> None:
    """`pack` always returns at least one candidate. When that one is unpriced
    it spends the whole budget, which is the conservative reading and must be
    visible in `used_tokens` rather than reported as free."""
    packed = pack([_fused("a", "i1"), _fused("b", "i2")], {"b": 10}, budget_tokens=100)

    assert [c.chunk_id for c in packed.candidates] == ["a"]
    assert packed.used_tokens == 100
    assert packed.dropped == 1
