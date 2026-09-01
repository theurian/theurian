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

   > **Amended in Milestone 5.** Half of that is on the wire. `Fused.ranks` holds
   > the per-retriever positions and is what `fusedScore` is computed from, but
   > the published `foundBy` is the retriever *names*, sorted — see
   > `schemas/knowledge/retrieval-result.schema.json`, which is the normative
   > shape. So a caller can see *that* both retrievers agreed and not *where*
   > each put the hit. The ADR is corrected rather than the wire: publishing the
   > ranks is a schema change, and the explanation `foundBy` exists to give —
   > why a result is in the list at all — is carried by the names.
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

  > **Amended in Milestone 5. `CANDIDATE_DEPTH` now counts rows the caller may
  > see, not rows the index returned**, and that changes what this sentence
  > means. As written it described a fixed `LIMIT`: fifty rows came back from
  > each retriever and whatever the canonical store later withdrew came out of
  > those fifty. So a document the caller *may* read could be pushed out of the
  > fusion by one it may not — which is not only a recall loss but a channel,
  > because every number computed downstream moved with it (T-17).
  >
  > A retriever is now read through the caller's `Visibility`: `FIRST_PASS_DEPTH`
  > rows, then twice as many, until fifty *visible* rows exist or it returns
  > fewer rows than it was asked for. Fifty visible rows are therefore the same
  > fifty whether or not a withheld document happens to match. The recall
  > consequence stands as written, against a corpus with nothing withheld — it is
  > the *depth* that is now honest about what it counts.
  >
  > The alternative measured against it was an eager exclusion: ask the canonical
  > store up front which revisions are surfaceable and filter them out in SQL.
  > That pays 32 ms on every query, including the ones against an index with
  > nothing stale about it, and the canonical scan inside that figure grows with
  > the corpus; depth doubling pays only when there is something to skip, and in
  > proportion to how much. The breakdown, and the depth-doubling figures beside
  > it, are in T-17 of the [threat model](../security/threat-model.md) and in
  > `RetrievalService._visible_ranking`'s docstring — this is the decision, not
  > the measurement.
  >
  > The dense retriever is the exception and does not double: an exact vector
  > scan scores every embedding whatever it is asked for, so
  > `IndexStore.search_dense` lost its `limit` rather than pretending one bounded
  > the work. It returns its whole ranking, and the cut to fifty happens on the
  > far side of the gate.
- Two candidates ranked `(i, j)` and `(j, i)` by two retrievers score *exactly*
  equal — the sum `1/(k+i) + 1/(k+j)` is symmetric in `i` and `j` — so on such a
  pair the `chunk_id` tie-break decides, not relevance. `chunk_id` is
  `<revision ULID>#<ordinal>`, so ties resolve by revision creation order: a
  determinism device standing in for a relevance judgement the fusion cannot
  make.

  > **Measured in Milestone 5.** Not rare. Over a 30-document corpus and 15
  > queries, fusing the lexical and trigram retrievers: 12 of 135 adjacent
  > top-10 pairs, 9%, were exact ties. An independent measurement on a
  > different corpus put it at 16%. The share is corpus-dependent; the
  > mechanism is not. Recorded in
  > `theurian.domain.ranking.reciprocal_rank_fusion`'s docstring, where it is
  > caused. Breaking such a tie on relevance needs a per-retriever weighting
  > decision this milestone did not take — see Compliance.

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

For the amendment to the depth consequence (fusion runs behind the gate):

- `tests/integration/test_retrieval_service.py::test_the_scores_the_gate_publishes_are_computed_over_the_survivors`
  — the fused scores a caller receives are the ones an index without the withheld
  document would have produced, rather than the ones it produced with it and then
  had corrected.
- `test_the_limit_is_applied_to_results_and_not_to_candidates` — a withheld
  candidate does not consume a result slot.
- `test_a_withheld_row_cannot_choose_which_chunk_of_a_visible_document_is_published`
  — `diversify` is downstream of the gate, so `per_item` caps what is visible
  rather than what was ranked. Scripted ranks, because the channel needs one
  exact arrangement.
- `tests/integration/test_mcp_tools.py::test_a_withheld_document_changes_nothing_a_caller_can_see`
  — the end-to-end statement of the same property: one query, two corpora, every
  published value equal, at `limit` 50 and at the default. Paired with
  `test_the_depth_probe_reaches_the_withheld_document_inside_the_candidate_depth`,
  which asserts the fixture can still violate it.

The security argument these discharge, and the five fields it took three rounds
of review to close, are recorded in T-17 of the
[threat model](../security/threat-model.md) rather than here. What belongs to
this ADR is the ordering: fusion is a function of what the caller may see, and of
nothing else.

> **Corrected in Milestone 5, review round 4.** The first item above claims the
> fused scores a caller receives "are the ones an index without the withheld
> document would have produced". That is true of everything the gate controls and
> false of one thing it does not. FTS5's `bm25` weights each phrase by an `idf`
> computed from `nHit`, the number of rows matching that phrase, and the withheld
> rows are still in the index file being counted — so a withheld document can
> reorder two *visible* rows inside one retriever, and RRF fuses ranks, so the
> reordering reaches `fusedScore`. Measured; recorded as T-17a in the threat
> model, and unfixed until Milestone 6.
>
> The three tests cited still assert what they say they assert and still pass.
> What they hold is that no stage computes a rank from a withheld **row**. They
> do not hold — and cannot, at this layer — that the corpus a visible row is
> scored *against* is free of withheld rows. That is a property of the index
> file, not of the fusion, which is why nothing here turned red.
>
> Accepted for Milestone 5 with the reasoning recorded in T-17a, tracked at HIGH
> against Milestone 6 as
> [#15](https://github.com/theurian/theurian/issues/15). The fix is the
> blue/green build that removes the stale window, not a change to the fusion:
> RRF's contract is "ranks in, ranks out", and it holds. What it cannot do is
> repair a rank that was wrong before it arrived.
>
> **Widened in review round five.** The paragraph above names `idf`/`nHit` as the
> mechanism, which is the channel an attacker can *steer* and not the only one
> that moves an order. `avgdl` — the corpus mean document length — enters BM25's
> length norm `k1 * (1 - b + b * D / avgdl)`, which is a function of each row's
> own `D` and therefore not a common factor across rows. Measured on withheld
> rows sharing no term with the query, with every phrase's `nHit` asserted
> identical in both indexes: 1,218 configurations reorder two visible rows. So
> the ranks arriving at RRF can be wrong even when the withheld documents share
> no vocabulary with the query at all. The acceptance was re-taken on the
> corrected text; T-17a carries the measurements and the terms.
>
> **Resolved in Milestone 6
> ([#15](https://github.com/theurian/theurian/issues/15)).** The residual above
> closes for the status axis: `theurian migrate apply` now publishes a purged
> build the moment a withdrawal lands (ADR-0024 decision 5), so a published index
> no longer holds the withdrawn rows the `bm25` statistics counted, and the ranks
> arriving at RRF are again those an index without the withheld document would have
> produced. The fix is that write-path purge, not a change to the fusion — RRF's
> "ranks in, ranks out" contract held throughout. Two content-independent residuals
> remain: a request in flight at the pointer swap, and a purge that fails (reported
> through the apply's `indexPurge`, not silent). T-17a carries the closure and the
> residuals.
>
> **Amended by GHSA-97q9-xxfg-33r6.** The "purge that fails" residual named above
> is now closed: a purge failure taints the active-index pointer
> (`mark_active_index_purge_failed`) and the serve path
> (`mcp.search._published_index`) refuses to serve the tainted build whole, so it
> no longer feeds stale `bm25` statistics to RRF — nor, for a `--raptor` build, a
> withheld document's text verbatim through a visible sibling's `raptorPath`. The
> in-flight residual is unchanged; T-17a's residual 2 carries the narrower windows
> that remain.

For the amendment to point 5 (dense off by default):

- `tests/integration/test_retrieval_service.py::test_dense_is_off_by_default`
- `test_dense_participates_when_asked_for` — the opt-in path is exercised, which
  is the whole reason the code was kept rather than deleted.
- `tests/unit/test_ranking.py::test_both_retrievers_contributing_is_hybrid` — the
  reported mode is never more generous than what actually ran.

  > **Amended in Milestone 5.** The sibling test previously cited here,
  > `test_no_results_at_all_reports_lexical_rather_than_claiming_hybrid`, no
  > longer exists, and its assertion no longer holds: an empty result set used
  > to report `lexical`, indistinguishable from "the word index answered and
  > found nothing" — exactly the signature a v1 index with no trigram table or
  > an embedder whose vectors do not match the corpus produces. `mode_of` now
  > has a fourth value, `RetrievalMode.NONE`, for "no retriever contributed
  > anything", pinned by
  > `test_no_results_at_all_is_neither_lexical_nor_hybrid`.

Landed in Milestone 5, for the T-17a correction above:

- `tests/integration/test_retrieval_service.py::test_a_withheld_document_can_still_reorder_the_visible_ones`
  — the first condition on the T-17a acceptance that needed a test. It asserts
  that a leak is **present**: two builds of one project, one still holding the
  retired note and one that never did, return the same two visible documents in a
  different order. The existing depth corpora assert the opposite equality and
  pass, because their withheld runbook does not move their crowd far enough to
  reorder it — a test asserting the right thing against a corpus that cannot
  exhibit the defect, which is the shape of the three T-17 rounds that passed
  with a sibling channel open.
- `test_the_bm25_probe_corpus_can_still_flip` — its guard, for the same reason
  `test_the_depth_probe_reaches_the_withheld_document_inside_the_candidate_depth`
  exists. It asserts the fixture's preconditions rather than the outcome, so a
  fixture that quietly stopped being able to flip fails here instead of passing
  the test above for the wrong reason.

Both of those pin the `idf`/`nHit` channel, whose fixture needs the probe term in
visible content. The `avgdl` channel needs a corpus that shares nothing with the
query, so it is a separate pair, landed with the round-5 widening:

- `test_a_withheld_document_sharing_no_vocabulary_still_reorders_the_visible_ones`
  — the withheld document contains neither query term, as a token or as a
  substring, and each phrase's `nHit` is asserted identical in both indexes, so
  `idf` cannot be what moved. The visible order moves anyway.
- `test_removing_the_shared_term_from_the_visible_bodies_stops_this_corpus_flipping`
  — the same demonstration from the other side: take the shared term out of the
  visible bodies and the `idf` channel stops reaching the published order on that
  corpus. Read alone, either one misstates the scope; the pair is what states it.

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
- **A relevance-based tie-break** (Milestone 6). Today, two candidates tied
  exactly under RRF — see the Negative consequence above — resolve by
  `chunk_id`, i.e. by revision creation order, which answers FR-R7's
  reproducibility requirement and nothing about which of the two is the better
  result. The per-term IDF work above is one candidate weighting signal;
  closing this needs a decision about how retriever weight enters the fusion
  at all, which is why it is filed separately rather than assumed to fall out
  of the same fix.
