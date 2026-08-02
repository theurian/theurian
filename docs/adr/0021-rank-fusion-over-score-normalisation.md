# ADR-0021: Fuse retrievers by rank, not by normalised score

- Status: accepted
- Date: 2026-08-03
- Deciders: Theurian maintainers
- Requirements: FR-R2, FR-R4, FR-R7

## Context

Hybrid retrieval runs two retrievers over the same corpus and has to produce one
ordered list. The two produce numbers that look comparable and are not:

- SQLite FTS5 returns BM25, an unbounded negative score whose scale depends on
  corpus statistics — average document length, term frequency across the
  collection. The same document scores differently as the corpus grows.
- The dense retriever returns cosine similarity, bounded to [-1, 1], whose
  distribution depends on the embedding model. Some models produce similarities
  clustered tightly near 0.8; others spread across the range.

The obvious approach is to normalise both onto [0, 1] and take a weighted sum.
It requires knowing each retriever's score distribution, and that distribution is
a property of *this corpus with this model* — so any constants chosen would be
tuned against whatever knowledge base happened to be at hand and would quietly
mis-rank on the next one.

There is a second constraint. FR-R7 requires a pinned snapshot to reproduce its
results, which means the order has to be **total**: no two candidates may tie in
a way that leaves their relative position to chance.

## Decision

**Fuse by reciprocal rank. Never compare scores across retrievers.**

1. Each retriever contributes `1 / (k + rank)` for every chunk it ranked, with
   `k = 60` (Cormack et al., 2009). Only positions are used; the retrievers'
   own scores are carried for explanation and never enter the arithmetic.
2. `k` is a constant, not a parameter. Tuning it per call would make two callers
   see different orders for the same query against the same index.
3. Ties break on chunk id, ascending. Without a second key, equal scores come out
   in dictionary order — which is insertion order, which depends on which
   retriever happened to answer first.
4. Every fused candidate reports `foundBy`: which retrievers surfaced it and at
   what rank.
5. A retriever that returns nothing is not an error. The reported retrieval mode
   degrades from `hybrid` to `lexical`, visibly.

## Consequences

### Positive

- Agreement between retrievers becomes the dominant signal, which is the right
  one: the terms matched *and* the meaning matched.
- Nothing needs tuning per corpus, so behaviour does not silently change when a
  knowledge base grows or an embedding model is swapped.
- The order is total and reproducible, which is what FR-R7 actually requires.
- A ranking can be explained — `foundBy` says why a result is where it is.

### Negative

- A retriever that is *very* confident cannot express that. A document at rank 1
  by an overwhelming margin contributes exactly what any rank 1 contributes.
  This is the cost of not trusting scores, and it is the intended trade.
- Recall depends on how deep each retriever is asked to go. A document neither
  retriever ranks within `CANDIDATE_DEPTH` cannot be fused at all.

### Neutral

- Adding a third retriever (RAPTOR summary nodes in Milestone 6, a reranker
  later) is a new key in the rankings map and no change to the fusion.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| Weighted sum of normalised scores | Requires per-corpus, per-model distribution assumptions that do not survive either changing. |
| Score normalisation by min-max over the result set | Makes a document's score depend on which other documents happened to be retrieved, so adding one irrelevant result reorders the rest. |
| Lexical only | Loses morphological and typographic tolerance entirely; `rotating` would not retrieve `rotation`. |
| Dense only | Loses exact-term precision, which is what engineering knowledge is mostly queried by — identifiers, error codes, config keys. |
| Learning to rank | Needs labelled relevance judgements this project has no way to collect, and would make ranking un-reproducible across installs. |

## Compliance

- `tests/unit/test_ranking.py::test_agreement_beats_a_single_strong_rank` — the
  property the whole ADR exists for.
- `test_fusion_uses_rank_not_score` — a retriever reporting enormous scores
  cannot buy the top slot.
- `test_ties_break_deterministically` — asserted by fusing the same rankings with
  the retrievers in both orders and requiring the same result (FR-R7).
- `test_the_contribution_of_a_rank_is_the_documented_formula` — pins the formula
  itself, because a change to it silently reorders every result in the system.
- `tests/integration/test_retrieval_service.py::test_two_identical_searches_return_the_same_order`
  — the same property end to end, over a real index.
- `test_an_index_without_embeddings_degrades_visibly_to_lexical` — a search that
  lost its dense half does not look identical to a healthy one.
