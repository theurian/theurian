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

> **Amended in Milestone 5.** As accepted, this ADR assumed the two retrievers
> would be the lexical one and the dense one, both running by default. Neither
> half of that survived implementation.
>
> The dense retriever is now **off by default** — see the amendment to point 5.
> The second retriever in the default configuration is a trigram substring index
> ([ADR-0023](0023-trigram-index-beside-the-word-index.md)), which returns BM25
> too.
>
> The reason for fusing by rank is unchanged, and it now has a second instance
> rather than only the one this ADR was written about: two BM25 scores computed
> over *different token spaces* — words versus overlapping character triples,
> with different average document lengths and different IDFs — are no more
> comparable to each other than a BM25 is to a cosine. Normalising them onto one
> scale would need exactly the per-corpus assumptions rejected below.

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

   > **Amended in Milestone 5.** The dense retriever no longer runs by default.
   > `SearchRequest.use_dense` and the MCP parameter `useDense` both default to
   > `false`, so `lexical` is what a *healthy* default search reports and
   > `hybrid` is what an opt-in one reports. Verified by running both against a
   > real index.
   >
   > This is a measured decision, not caution. The bundled embedder is a hashed
   > character n-gram vectoriser, and against a real corpus **91% of unrelated
   > natural-language questions cleared the similarity floor**, while the lowest
   > genuinely related query scored below the unrelated median. The
   > distributions overlap, so no threshold separates them — the quantity being
   > measured is English surface-form overlap, not topical relevance. The floor
   > that is in the code (`DENSE_SIMILARITY_FLOOR = 0.25`) was calibrated
   > against *random strings*, where the 99th percentile was 0.187; random
   > strings turned out to be the easy case and the wrong population to
   > calibrate on.
   >
   > The code path is kept and made opt-in rather than deleted, so it stays
   > exercised and becomes useful the day a real model is configured through the
   > same port (ADR-0009). `theurian index build` still writes embeddings unless
   > `--no-embeddings` is passed, so opting in needs no rebuild.
   >
   > **FR-R2 is therefore only partly discharged.** Both retrievers exist and
   > fusion is real, but the dense half is not on by default and will not be
   > until a provider worth defaulting to exists.

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

  > **Confirmed in Milestone 5.** The third retriever arrived early — the
  > trigram substring index of ADR-0023 — and cost exactly this: one more key in
  > the rankings map, no change to `reciprocal_rank_fusion`, and no change to
  > any of its tests.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| Weighted sum of normalised scores | Requires per-corpus, per-model distribution assumptions that do not survive either changing. |
| Score normalisation by min-max over the result set | Makes a document's score depend on which other documents happened to be retrieved, so adding one irrelevant result reorders the rest. |
| Lexical only | Loses morphological and typographic tolerance entirely; `rotating` would not retrieve `rotation`. |
| Dense only | Loses exact-term precision, which is what engineering knowledge is mostly queried by — identifiers, error codes, config keys. |
| Learning to rank | Needs labelled relevance judgements this project has no way to collect, and would make ranking un-reproducible across installs. |

## Compliance

Landed in Milestone 5:

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

For the amendment to point 5 (dense off by default):

- `tests/integration/test_retrieval_service.py::test_dense_is_off_by_default`
- `test_dense_participates_when_asked_for` — the opt-in path is exercised, which
  is the whole reason the code was kept rather than deleted.
- `tests/unit/test_ranking.py::test_both_retrievers_contributing_is_hybrid` and
  `test_no_results_at_all_reports_lexical_rather_than_claiming_hybrid` — the
  reported mode is never more generous than what actually ran.

Still owed, with the milestone that will satisfy it:

- **An embedding provider worth turning on by default** (no milestone yet). The
  amendment above records why the bundled one is not it. Until then, a caller
  who passes `useDense: true` without configuring a real model gets an
  n-gram-backed hybrid search, and `retrieval.embeddingModel` names it as
  `theurian-hashed-char-ngram` so it cannot be mistaken for a semantic one.
- **A relevance floor for the lexical retriever** (no milestone yet). A query
  whose terms all appear in every document still matches. A review proposed
  excluding hits that score "exactly 0.0"; measured, SQLite returns
  `-1.375e-06` for that case, so a score threshold excludes nothing.
  Distinguishing "matched only common words" from "matched weakly" needs a
  per-term IDF test rather than a threshold on the combined score. Pinned by
  `tests/integration/test_index_store.py::test_a_query_of_only_common_words_still_matches_today`,
  which is written to fail the day it is fixed.
