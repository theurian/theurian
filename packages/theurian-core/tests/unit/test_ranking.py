"""Fusion, diversification, and budgeting (FR-R2, FR-R4).

Pure functions, so these are the cheapest tests in the suite and the ones that
pin the behaviour everything else in retrieval depends on.
"""

from __future__ import annotations

import pytest

from theurian.domain.ranking import (
    CHARS_PER_TOKEN,
    DENSE,
    LEXICAL,
    RRF_K,
    Fused,
    Ranked,
    RankingError,
    RetrievalMode,
    RetrieverPage,
    diversify,
    estimate_tokens,
    mode_of,
    reciprocal_rank_fusion,
    take_within_budget,
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


#: How deep a document may sit in both rankings and still win on agreement.
#:
#: **The number the product prose commits to, not one this file chose.**
#: :data:`~theurian.application.retrieval_service.CANDIDATE_DEPTH` is fifty
#: rather than ten because "a document the dense retriever ranked 30th cannot
#: demonstrate agreement if only 10 were asked for" — which presupposes that a
#: document ranked thirtieth *can* demonstrate it. That presupposition is a
#: claim about :data:`RRF_K`, and it is the claim below.
AGREED_AT = 30


def _filler(retriever: str, count: int) -> list[Ranked]:
    """Rows that pad a ranking without agreeing with the other one.

    Named per retriever on purpose: a filler appearing in both lists would be an
    agreement of its own and would decide the order instead of the thing under
    test.
    """
    return [_ranked(f"{retriever}-{position:02d}") for position in range(count)]


def test_agreement_thirty_deep_still_beats_the_strongest_single_hit() -> None:
    """FR-R2, and the reason ``CANDIDATE_DEPTH`` is fifty rather than ten.

    ``RRF_K`` decides how much a second opinion is worth against a strong first
    one, and the whole retrieval pipeline is built on one answer to that: fifty
    candidates are fetched from every retriever, at the cost of a deeper read,
    *so that* a document neither retriever put near the top can still win by
    being found twice. Below k = 28 that stops being true — the agreed document
    at rank thirty falls behind a rank-one solo hit — and fetching fifty
    candidates becomes work done for a result that can no longer happen. (At
    exactly 28 the two scores are equal to the last bit, and the tie-break
    :func:`reciprocal_rank_fusion` documents decides it; the bound this asserts
    is therefore k >= 28, verified by mutation at 27, 10 and 1.)

    Measured on the shipped code with ``RRF_K = 1``: the winner changes from the
    agreed document to a solo one, and the agreed document drops from first to
    last. The whole suite passed.

    **A lower bound, deliberately.** ``60`` is a citation — Cormack et al.
    (2009) — not a promise this product makes, and no requirement here says how
    *much* agreement should outweigh position. So this pins the direction the
    design depends on and leaves the rest of the band alone, as ``SCAN_TERMS``
    is left.
    """
    agreed = _ranked("agreed")
    solo = _ranked("solo")

    fused = reciprocal_rank_fusion(
        {
            LEXICAL: [solo, *_filler("lex", AGREED_AT - 2), agreed],
            DENSE: [*_filler("den", AGREED_AT - 1), agreed],
        }
    )

    assert fused[0].chunk_id == "agreed", "found twice at rank thirty must beat found once at one"
    assert fused[0].agreed, "and it must win because both retrievers found it"


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


def test_taking_stops_at_the_budget() -> None:
    assert take_within_budget([40, 40, 40], budget_tokens=100) == (2, 80)


def test_taking_never_reorders_to_fill_the_budget() -> None:
    """A knapsack fill would skip a large high-ranked result to fit three small
    low-ranked ones, silently trading relevance for a number nobody sees.

    The costs make the two strategies disagree: a prefix takes 2 and spends
    100, a fill by count takes the last 3 and spends 30.
    """
    assert take_within_budget([90, 10, 10, 10], budget_tokens=100) == (2, 100)


def test_a_budget_smaller_than_the_best_result_still_returns_it() -> None:
    """One over-long answer a caller can truncate beats an empty one they
    cannot act on."""
    assert take_within_budget([10_000], budget_tokens=10) == (1, 10_000)


def test_what_did_not_fit_is_derivable_by_the_caller() -> None:
    """ "Nothing else matched" and "your budget ran out" are different answers
    and lead to different next actions, so the count has to be recoverable."""
    costs = [50] * 10

    kept, _ = take_within_budget(costs, budget_tokens=100)

    assert len(costs) - kept == 8


def test_taking_nothing_is_not_an_error() -> None:
    assert take_within_budget([], budget_tokens=100) == (0, 0)


@pytest.mark.parametrize("budget", [0, -5])
def test_a_nonsensical_budget_is_refused(budget: int) -> None:
    with pytest.raises(RankingError, match="at least 1"):
        take_within_budget([1], budget_tokens=budget)


def test_the_token_estimate_errs_high() -> None:
    """Overshooting a caller's budget truncates their context, often including
    their own instructions. Undershooting only costs recall."""
    assert estimate_tokens("a" * 400) >= 100
    assert estimate_tokens("") == 1, "even an empty chunk costs something to send"


def test_cjk_text_is_not_priced_at_the_english_rate() -> None:
    """This project's own knowledge is written in Japanese, and the docstring
    says the English, space-delimited heuristic under-counts it roughly
    fivefold. Every prior check here used Latin text, so a mutant that dropped
    the dense-script multiplier to the *English* rate -- pricing Japanese
    exactly as cheaply as `CHARS_PER_TOKEN` prices English -- survived the
    whole suite.

    The bound below is independent of the dense multiplier's current value: it
    is derived from `CHARS_PER_TOKEN`, an unrelated constant, so it cannot
    become tautological if the dense rate is later retuned, and it still fails
    against the mutant this test exists to kill.
    """
    text = "あ" * 400  # entirely dense-script, so nothing here is priced sparsely

    # What the English heuristic alone would charge for this many characters.
    english_rate_estimate = -(-len(text) // CHARS_PER_TOKEN)

    # A conservative estimate for Japanese must clear a floor well above what
    # the English rate would give the same text -- "roughly fivefold" leaves
    # comfortable room on both sides of a factor of 2.
    assert estimate_tokens(text) >= english_rate_estimate * 2


# -- Reported mode -----------------------------------------------------------
#
# `mode_of` takes the retriever names carried by the *results being returned*,
# never the rankings that produced them: a ranking still holds candidates the
# canonical store withheld, and a mode derived from those is a field an attacker
# can watch move (SEC-13).


def test_both_retrievers_contributing_is_hybrid() -> None:
    assert mode_of([LEXICAL, DENSE]) is RetrievalMode.HYBRID


def test_an_empty_dense_index_degrades_visibly_to_lexical() -> None:
    """The failure this exists to make visible: a vector index that failed to
    build must not silently return worse answers that look the same."""
    assert mode_of([LEXICAL]) is RetrievalMode.LEXICAL


def test_dense_only_is_reported_as_dense() -> None:
    assert mode_of([DENSE]) is RetrievalMode.DENSE


def test_no_results_at_all_is_neither_lexical_nor_hybrid() -> None:
    """`LEXICAL` used to cover this, so "the word index answered and found
    nothing" and "no retriever answered at all" were the same word -- and the
    second is what a missing trigram table or a mismatched embedding model
    produces."""
    assert mode_of([]) is RetrievalMode.NONE


# -- A retriever's page ------------------------------------------------------
#
# `exhausted` is a claim about the retriever, not about the rows, so the type
# can check exactly one thing: the combination that no honest implementation can
# produce and that the depth loop cannot survive.


def test_a_page_may_be_empty_when_the_retriever_is_finished() -> None:
    """The ordinary "nothing matched" answer, which must stay constructible."""
    assert RetrieverPage(rows=(), exhausted=True).rows == ()


def test_an_empty_page_that_claims_more_is_coming_is_refused() -> None:
    """`_deeper` doubles without a ceiling, so this page is a hang.

    It is also unreachable for a conforming adapter: every `IndexStore` method
    ranks best-first and counts `limit` from the top, so no rows at one depth is
    no rows at any greater depth. Refused at construction because the loop cannot
    tell this page from an honest empty one, and because a hang reported from
    inside a retrieval loop names the wrong thing.
    """
    with pytest.raises(RankingError, match="reporting itself not exhausted"):
        RetrieverPage(rows=(), exhausted=False)


def test_a_non_empty_page_may_say_either_thing() -> None:
    """The type constrains one combination and no others.

    A page with rows that is not exhausted is the normal mid-loop answer, and a
    page with rows that is exhausted is the normal final one. Neither is
    something a value object can second-guess: whether more exists is a fact
    about the index, which only the adapter holds.
    """
    rows = (Ranked(chunk_id="c#0", item_id="i", revision_id="r"),)

    assert RetrieverPage(rows=rows, exhausted=False).exhausted is False
    assert RetrieverPage(rows=rows, exhausted=True).exhausted is True
