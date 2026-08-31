# ADR-0023: A trigram index beside the word index, not instead of it

- Status: accepted
- Date: 2026-08-03
- Deciders: Theurian maintainers
- Requirements: FR-R1, FR-R2, ADR-0009, ADR-0021, ADR-0022

## Context

The lexical retriever is SQLite FTS5 with the `unicode61` tokenizer. `unicode61`
splits on whitespace and punctuation and on nothing else, which is correct for
languages that put spaces between words and useless for those that do not.

Measured against this project's own knowledge, a Japanese sentence produces
exactly one token:

```text
input:  署名付きトークンを持つ
tokens: ["署名付きトークンを持つ"]
```

So `トークン` does not match `署名付きトークン`, `ローテーション` does not match a
sentence containing it, and a Japanese-language knowledge base is absent from
lexical search entirely — except on the rare query that happens to equal a whole
heading. Theurian's own knowledge is written in Japanese, so this is not an edge
case; it is the common case for the first real corpus the system was pointed at.

Dense retrieval does not rescue it. The bundled embedder is a hashed character
n-gram vectoriser whose scores do not separate related from unrelated queries
(see the Milestone 5 amendment to [ADR-0021](0021-rank-fusion-over-score-normalisation.md)),
and it is off by default for that reason.

## Decision

**Index the same text twice, with two tokenizers, and fuse the results.**

1. `chunks_trigram` is a second FTS5 table over the same `chunks` content, with
   `tokenize="trigram"`. `chunks_fts` keeps `unicode61 remove_diacritics 2` and
   is unchanged.
2. Both are external-content tables (`content='chunks'`) with their own insert,
   delete, and update triggers, so neither holds a second copy of the text and
   neither can drift from it.
3. They are **two retrievers**, not one index with two strategies. `search_lexical`
   and `search_substring` return independent ranked lists that enter Reciprocal
   Rank Fusion under the names `lexical` and `substring` (ADR-0021). Their BM25
   scores are never compared: two BM25 numbers computed over different token
   spaces, with different average document lengths and different IDFs, are no
   more comparable than a BM25 and a cosine.
4. Query terms shorter than three characters are dropped from the trigram
   expression rather than sent. FTS5 cannot match them against a trigram index,
   and including one makes the whole expression return nothing.

   > **Amended in Milestone 5.** True of the *expression*, and incomplete about
   > the answer. When every term in a query is short enough to be dropped, the
   > expression this point describes is empty, and `search_substring` no longer
   > returns nothing for it: it falls through to a scoped `LIKE '%...%'` scan
   > over `chunks.text` and `chunks.heading` instead, under the same `_scope`
   > filter (FR-R1, SEC-13) and the same `limit` the lookup would have used.
   >
   > Measured, this is not a nicety: `theurian index build`, the documented
   > operation, used to make search strictly worse than having no index at all
   > for the most common noun length in Japanese. 認証, 決済, 監査, and 契約 —
   > two characters each — returned results before a build and nothing after,
   > with `count: 0, indexed: true` and no fallback reason to explain why. The
   > evidence was never missing; `chunks.text` held it the whole time, reachable
   > by `substring_answer`'s unranked scan and by a plain grep, just not by this
   > retriever. See `index_query.to_trigram_expression` and
   > `SqliteIndexStore._scan_below_the_trigram_floor`, and the amendments to the
   > Negative consequences below for what is still left open.
   >
   > **The scan ranks, and this amendment first said it did not.** It shipped
   > describing the scan as "unranked — every hit keeps score `0.0` — and
   > ordered by `chunk_id`", which described the code as written and was the
   > wrong design. Under a `LIMIT` the ordering key is the *selection* key:
   > `chunk_id` is `<revision ULID>#<ordinal>`, so once more than `limit` chunks
   > matched, the oldest `limit` were the only rows any caller could reach, and
   > revising a document — which mints a newer ULID — sank it further. The sort
   > key is now how many characters of the query a chunk accounts for
   > (occurrences weighted by term length), and `chunk_id` is the tie-break
   > underneath it, which is what keeps the order total and the answer
   > reproducible (FR-R7).
   >
   > **The floor's lower bound is not one character of anything.** A term earns
   > a pass over the corpus at two characters, or at one when that character is
   > a *letter* of a script written without word boundaries: hiragana, katakana,
   > CJK ideographs including extension A and the compatibility block, Hangul
   > syllables, and halfwidth katakana. `鍵` is a noun that no other retriever
   > can answer; `e` is a letter, and the word index already answers it as a
   > word. The table is deliberately **not**
   > `domain.ranking._DENSE_SCRIPT_RANGES`, which answers a different question —
   > how expensive a character is to tokenize — and therefore spans whole blocks
   > including emoji and CJK punctuation. Borrowing it would let `。` and `🎉`
   > each start a scan of the corpus. Letters only, so `ー` and the sound and
   > iteration marks at the end of the kana blocks are refused too.
   >
   > **What the scan costs, and the bound that keeps it affordable.** Counting
   > occurrences is a `replace()` over every matching row per term spent, and
   > matching is a `LIKE` over the same rows, so the price is roughly linear in
   > terms. `index_scan.SCAN_TERMS` caps a query at **eight** terms, which is
   > one number governing both halves: the same slice builds the `WHERE` and the
   > `ORDER BY`. The worst legal query — every term missing except the last,
   > which matches every row — falls from 4.25s at the old unbounded 64 to
   > 1.67s. The cost table and the corpus it was measured on (20,000 chunks of
   > 1,000 CJK characters) live at `index_scan.SCAN_TERMS` and in
   > `index_scan.scan_statement`'s docstring rather than being copied here, so
   > there is one of each to keep true.
   >
   > **Amended again in Milestone 5, because the version of this paragraph that
   > shipped first pointed at the wrong knob.** It said the bound was
   > `RANKING_TERMS`, that it capped *voting* at the four longest terms, and
   > that terms past it "still select — every term is in the `WHERE` — they
   > simply get no vote on the order". Every clause of that is either gone or
   > was mis-stated:
   >
   > - **The constant did not hold the cost it was credited with.** Decomposed,
   >   the ordering was roughly 15% of the query while the `WHERE` — which no
   >   constant touched — was the rest. It bounded the cheap half in front of an
   >   unbounded expensive one. Bounding *terms* instead is what moves the
   >   number: the table at `SCAN_TERMS` runs 0.81s, 1.67s, 3.37s, 4.25s for
   >   four, eight, sixteen and sixty-four.
   > - **"Selects but does not order" is not a milder loss, and it is now
   >   removed by construction rather than mitigated.** Under a `LIMIT` the
   >   ordering key is the selection key, so a term that could not vote put its
   >   rows at score zero, behind every row any voting term touched. Measured on
   >   the shipped code: `認証 決済 監査 契約 暗号` against a chunk carrying
   >   `暗号` thirty times ranked it below chunks carrying `契約` twice, and at
   >   `limit=10` against sixty such chunks it did not come back at all. One
   >   slice for both halves is what removes it — there is no longer a term that
   >   can select without ranking, so this is not a residual to watch.
   > - **The new cost is honest absence, and it is not the selectivity trade the
   >   old text implied.** A query with more than eight short terms searches only
   >   its first eight, and *first* means first **typed**. `_query_terms` sorts
   >   longest first as a selectivity proxy, but every term reaching this branch
   >   is one or two characters, so a stable sort over equal lengths leaves the
   >   typed order untouched and that proxy buys nothing here. Choosing among
   >   `認証 決済 監査 契約` needs corpus statistics this retriever does not
   >   have; the caller's order is used rather than guessed at, and the ranking
   >   model is Milestone 6's (ADR-0021).
   >
   > Eight is a tuning constant, not a proven value. It is held inside a band
   > whose edges are the two measurements that justify it — below five it stops
   > answering an ordinary five-noun Japanese keyword query, and at sixteen the
   > worst case is back over 3s on the way to the 4.25s the bound exists to cut.
   > Seven and nine are deliberately unpinned: a test that fails on `8 → 9`
   > would be a change detector, not a statement about the product.
5. `INDEX_SCHEMA_VERSION` goes 1 → 2. An index built before this change has no
   `chunks_trigram` table.

## Consequences

### Positive

- Japanese, Chinese, and Thai knowledge is reachable by search at all. Verified
  end to end: a real `theurian index build` over a Japanese knowledge item
  answers `トークン` and `ローテーション`, each `foundBy: ["substring"]`, while
  `kubernetes` returns nothing. Measured across all three scripts, the word
  index scores zero hits and the trigram index scores one, with no
  cross-matching between documents.

  Korean is the partial case worth naming: it *does* put spaces between eojeol,
  so `unicode61` already tokenizes it and the word index finds a whole-eojeol
  query. What the trigram index adds there is matching below the eojeol
  boundary, which is a smaller gain than for the scripts above.
- Exact terms keep the index that is good at them. `parses_json` still ranks on
  the term in `chunks_fts` rather than on its overlapping fragments.
- Agreement between the two is meaningful in the way ADR-0021 relies on:
  a chunk that a word match and a substring match both surface is better
  evidence than either alone.
- No new dependency, no dictionary, no native extension. `uv sync && pytest`
  still passes offline on any machine (ADR-0009).

### Negative

- Trigram matching is substring matching, so it has no word boundaries:
  a query for `cat` matches `concatenate`. This is precisely why the word index
  is kept — the noise lands in one retriever's ranking rather than in the fused
  result's top positions, because the word index does not corroborate it.

  > **Amended in Milestone 5, with a consequence this ADR did not anticipate:
  > substring matching turns any query-dependent number in the response into an
  > extraction primitive, not merely a detector.** A word index answers "does
  > this document contain this term"; a trigram index answers "does it contain
  > this three-character sequence", which is a guess that can be extended one
  > character at a time. So a published value that moves when a query matches
  > content the caller may not read does not merely reveal that something is
  > there — it spells it out. Measured: 203 ordinary `knowledge.search` calls
  > recovered a sixteen-character credential through `count` alone. Five fields
  > carried it, found over three rounds of review, and the whole account is T-17
  > of the [threat model](../security/threat-model.md).
  >
  > Two things follow for this ADR. **On a corpus written without word spacing
  > the attack needs no setup at all**: `unicode61` contributes almost nothing,
  > so this retriever's fifty candidate slots *are* the candidate list, and the
  > crowd an attacker would otherwise have to construct is the corpus. That is a
  > measurement now, not an argument — against the same 56-document crowd, the
  > word index offers **1** row and this retriever offers a full **50** on
  > Japanese, where English gets fifty from each (see Compliance). And any
  > future retriever with substring reach inherits the same requirement — its
  > rows must be read through the caller's visibility rather than filtered
  > afterwards, which is now where `search_substring` is read from
  > (`RetrievalService._visible_ranking`).
- Disk. The trigram index is the larger of the two, on a file that is derived
  and disposable (ADR-0022), which is the cheapest place to spend it.
- A query term of one or two characters reaches the word index only. For a
  script with no word boundaries that is a short query with no lexical evidence
  either way; it is recorded rather than solved.

  > **Amended in Milestone 5. False on both halves.** A short term no longer
  > reaches the word index only — see the amendment to Decision item 4 above —
  > and the premise that it "carries no lexical evidence either way" was simply
  > wrong: the evidence was in `chunks.text` all along, unreachable only by this
  > one path.
  >
  > What remains, and is left open deliberately, is narrower: a short term
  > *mixed with* a term of three characters or more is still dropped from the
  > trigram expression, because the expression is then non-empty and the floor
  > never fires — `認証 トークン` searches only for `トークン` on this retriever.
  > That is a recall loss, not the blackout the all-short case was, since the
  > long term still answers. Closing it means `LIKE` predicates in the same
  > statement as a `MATCH`, where `bm25` is undefined for the rows only `LIKE`
  > matched, so the retriever would have to return an order it cannot compute —
  > a ranking-model decision, deferred to the per-term IDF work in Milestone 6
  > (ADR-0021). Documented where it is caused:
  > `index_query.to_trigram_expression`'s docstring, not this file.
- **The scan's order is a proxy, and a chunk that saturates one term can
  outrank a chunk that covers two.** Occurrences weighted by term length is not
  IDF: forty repetitions of `認証` beat one `認証` and one `決済`, which BM25
  gets right and this does not. It is the same missing per-term IDF as the
  mixed-length residual above, so one ranking-model decision closes both
  (Milestone 6, ADR-0021).
- **Under a genuine tie the scan is exactly as arbitrary as the path it stands
  in for.** Where no chunk is more about the query than any other, `chunk_id`
  decides which survive the `LIMIT`, so the newest is still unreachable at a
  small one. That is narrower than the defect the ordering closed, where a chunk
  *was* more about the term and could not be reached at any `limit`. Measured
  against the retriever this branch replaces rather than asserted: over 60
  near-identical documents, the trigram lookup ordered by `bm25` selects the
  same first ten in the same order. Recorded as a stated residual and not a
  defect — a tie-break is what decides a tie, and this branch may not select
  differently from the lookup when neither has a relevance signal to go on. It
  is ADR-0021's fusion tie-break, one layer down.
- **"Building an index never answers less than having none" has exactly one
  exception, and it is deliberate: a query that is a single punctuation
  character.** `。` ends every Japanese sentence and `#` opens every Markdown
  heading, so the canonical fallback — which takes the whole query as one
  literal substring — answers both, and the ranked path declines them. Verified:
  against a row holding `署名を持つ。`, `to_scan_terms("。")` yields no terms and
  the word index's `"。"` expression matches nothing, while the fallback's
  needle matches. Answering them would mean reading every row in the index to
  return "the fifty the sort favoured" with a fused score attached, which is a
  ranked answer to a question nobody asked.
- **Case folding is asymmetric across the floor.** SQLite's `LIKE` folds ASCII
  only; the trigram tokenizer folds the whole of Unicode. Measured on SQLite
  itself: against a row holding `ΑΒΓ`, `LIKE '%αβγ%'` matches nothing while
  `trigram MATCH 'αβγ'` matches — so a two-letter Greek query is case-sensitive
  and the same word with one letter more is not. `lower()` is ASCII-only in
  SQLite too, so the obvious remedy buys nothing. Japanese and Chinese are
  caseless, so the scripts this branch exists for are unaffected; this becomes
  real the day a Greek or Cyrillic corpus turns up, and then it is the `icu`
  tokenizer or nothing. Recorded rather than closed, in
  `index_query.to_scan_terms` as well as here.
- **An index built at schema version 1 is not detected.** `search_substring`
  catches `sqlite3.OperationalError` and returns nothing, so a version-1 index
  silently answers without the trigram retriever, and a Japanese knowledge base
  goes back to being invisible with nothing in the response saying so. Confirmed
  by dropping `chunks_trigram` from a real index and querying it. The remedy is
  `theurian index build`; making the mismatch *visible* is owed — see Compliance.

  > **Amended in Milestone 5.** No longer true. `search_substring` (and
  > `search_lexical` and `search_dense`) now distinguish a schema-caused
  > `sqlite3.OperationalError` from a query-caused one and raise
  > `IndexUnreadableError` for the former, which `hybrid_answer` catches and
  > reports as `fallbackReason: index-schema-mismatch` — visible, and
  > `indexed: false`, rather than a silent, falsely healthy answer. See
  > Compliance.

### Neutral

- This is the third retriever, arriving a milestone earlier than ADR-0021
  expected. It cost exactly what that ADR predicted: one more key in the
  rankings map and no change to the fusion.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| Replace `unicode61` with `trigram` | Trades one broken search for another. Trigram matching has no word boundaries, so `cat` retrieves `concatenate` — measured — and the identifiers and error codes that engineering knowledge is mostly queried by would rank on fragments instead of on the term. |
| Reclassify CJK characters with `unicode61`'s `separators` / `tokenchars` | Measured: it does not work. Those options reclassify individual characters as boundaries or as token content; they cannot insert a boundary *between* two characters that are both token content. `トークン` still did not match. |
| One FTS5 table with a tokenizer per column | FTS5 rejects it: `multiple tokenize=... directives`. The tokenizer is a table option, so two tokenizers means two tables regardless. |
| A morphological segmenter (MeCab, Sudachi, ICU) | A native dependency plus a per-language dictionary, in a project that promises `git clone && uv sync && pytest` offline on any machine (ADR-0009). It also fixes only the languages it has dictionaries for, while trigram fixes the class. |
| `LIKE '%...%'` over `chunks` | A full table scan per query, and no ranking — a retriever must return an *order* for RRF to have anything to fuse. |
| Leave it, and rely on the dense retriever | The bundled embedder does not separate related from unrelated queries and is off by default (ADR-0021 as amended). Relying on it would mean Japanese search worked only for someone who has configured a real model. |

> **Amended in Milestone 5.** The `LIKE` row above is reversed for one case, not
> overturned. `LIKE` stays rejected as the *primary* retriever, for the reason
> given — `search_substring` never uses it while a trigram expression exists —
> but it is adopted for the case that expression can never cover: every term
> too short to form a trigram, where the choice was `LIKE` or nothing.
>
> **The "no ranking" objection had to be answered, and the first version of
> this amendment dismissed it instead.** It argued that FR-R7 needs a *total*
> order rather than a *relevance* order, and that ordering `LIKE`'s matches by
> `chunk_id` supplies exactly that. A total order is necessary and not
> sufficient. Under a `LIMIT` the ordering key is the *selection* key, so
> `chunk_id` order was not merely presenting the answer in an arbitrary
> sequence — it was choosing which rows were in it, and it chose the oldest,
> with every revision sinking a document further. The row is still reversed,
> and now for a reason that survives the `LIMIT`: the scan computes its own
> relevance order and keeps `chunk_id` as the tie-break beneath it (see the
> amendment to Decision item 4).
>
> The cost objection compared `LIKE` only against the trigram lookup it lost
> to; it was never compared against the alternative that actually runs today,
> `substring_answer`, which already performs this same match in Python, over
> whole revision bodies, one query per document, whenever no index exists at
> all. What the scan does cost is now measured rather than argued —
> `index_scan.scan_statement`, and `index_scan.SCAN_TERMS` for the bound that
> keeps it out of SEC-8 territory.
>
> **What that comparison still missed, and what it cost: this branch was read
> behind a loop that doubles its `limit`, and the `limit` bounded none of its
> cost.** T-17's depth doubling asks a retriever for more rows until enough
> visible ones survive. Measured on 6,000 chunks of 1,000 CJK characters, one
> scan cost 72.6 ms at `LIMIT 50` and 72.0 ms at `LIMIT 3,200` for a single CJK
> noun, 517.0 ms and 532.6 ms for the worst legal eight-term query — flat. So
> every doubling was a whole extra scan, and the six-pass worst case was 3.06 s
> where the trigram lookup measured 43 ms.
>
> **Fixed in the same milestone, by removing the `LIMIT` rather than by tuning
> the loop.** `scan_statement` has none: `ORDER BY matched_characters DESC` has
> to score every matching row before it can name the best of them, so the
> parameter bounded the rows returned and not the work — measured at 0.49s /
> 1.30s / 1.69s / 0.19s with `LIMIT 100` and 0.48s / 1.31s / 1.71s / 0.21s
> without. It saved nothing and cost a second full scan whenever the caller had
> to look past withheld rows. The branch hands back its whole ranking, the loop's
> exit test became `!=` so a non-truncating retriever is not asked twice, and it
> is now **one pass whatever was withheld** — verified from 0 to 5,999 withheld
> rows; 3.06 s → 0.64 s.
>
> The point that outlives the figures: `IndexStore.search_substring` took a
> `limit` that meant two different things on its two branches, and a caller
> reasoning about cost from it was right about one of them. It no longer does on
> this branch, which is the only reason the loop above it is now safe here.
>
> **Corrected in Milestone 5, review round 5: "one pass whatever was withheld" is
> true of the corpus and false of the port.** `!=` exits on the first pass for
> every ranking whose size is not exactly `FIRST_PASS_DEPTH`, which is why the
> 0-to-5,999 sweep saw one pass — a 6,000-row ranking never lands on that number.
> A ranking that totals exactly `FIRST_PASS_DEPTH` is indistinguishable from a
> truncated one, so the loop asks again: `search_substring` is called once at 50
> withheld rows and twice at 51. The corpus is still scanned once either way,
> because `SqliteIndexStore._scan_cache` memoises the answer — a mitigation for
> this one gap, removed when `IndexStore` states its own exhaustion
> ([#16](https://github.com/theurian/theurian/issues/16)).
>
> > **Amended in Milestone 6: #16 landed, and the second call is gone rather
> > than made cheap.** This branch reports itself exhausted on its first and
> > only call, so `search_substring` is called once at 51 withheld rows as well
> > as at 50 — measured against a real index at 0, 49, 50, 51 and 99 withheld.
> > `_scan_cache` was deleted in the same change. What the paragraph below says
> > about the *truncating* retrievers is unaffected and still holds.
>
> This does not reopen the `LIMIT` decision above, which stands: dropping it is
> what took the six-pass worst case from 3.06 s to a single scan. What it
> corrects is the claim that the branch was thereby taken out of the loop
> entirely. The remaining call is the *duration* face of T-17a — an extra fetch
> is what securing `CANDIDATE_DEPTH` visible rows from a retriever that is not
> exhausted means — and what removed it is the index purge, not a change here.
> T-17 in the [threat model](../security/threat-model.md) carries the argument
> and the five things that would falsify it.
>
> > **Tensed on 2026-09-01
> > ([#464](https://github.com/theurian/theurian/issues/464)): the purge has
> > shipped.** This sentence said the face "goes away with the index purge in
> > [#15](https://github.com/theurian/theurian/issues/15)", written while that
> > was future work. #15 closed on 2026-08-10 (`66a43ae`): `theurian migrate
> > apply` publishes a purged build the moment a withdrawal lands (ADR-0024
> > decision 5), so the published index holds no withheld row for a truncating
> > retriever to spend a slot on. Nothing is owed here — what survives is a
> > request in flight at the purge's pointer swap, which
> > `application/retrieval_service.py` records at the scan branch's own
> > paragraph, and a purge that failed, which taints the pointer and stands the
> > build aside.
>
> **Corrected again in review round six: having no `LIMIT` bounds the pass count
> and unbounds the work inside a pass.** `_visible_ranking` hands the whole
> ranking to the visibility, which issues one canonical read per distinct item in
> it, so a branch that returns its entire ranking makes that count the entire
> match set — 6,000 reads on a 6,000-row match set, in a single pass, moving one
> read at a time with what was withheld. The decision stands on its numbers, since
> six full scans cost 3.06 s where 6,000 canonical reads cost about 0.09 s, but
> what it bought is a cheaper unit and not a closed channel: T-17's "closed
> outright on this branch" is retracted there. The residual is the same face of
> the same class and ended the same way: the purge
> [#15](https://github.com/theurian/theurian/issues/15) shipped leaves no
> withheld row in the published index for `_visible_ranking` to price a canonical
> read against. This said "still ends with #15" until 2026-09-01, while #15 had
> closed on 2026-08-10.
>
> **Corrected in Milestone 5, review round 8. The cost paragraph above — "it was
> never compared against the alternative that actually runs today,
> `substring_answer`, which already performs this same match in Python, over
> whole revision bodies, one query per document" — is wrong in two ways and stale
> in a third.** They are separated here because they are not the same kind of
> wrong: two clauses were never true, and one was true of the moment it was
> written.
>
> **"Already performs this same match in Python" — never true.**
> `substring_answer` tests the whole query as a single literal substring
> (`mcp/search.py`, `needle=query.strip().lower()`), where this branch is an
> up-to-eight-term OR with a relevance order evaluated over every matching row.
> Different work, not the same work in a different language, and the difference
> shows from outside: handing the fallback the eight-term worst case costs it
> what a query matching nothing costs, because it does not spend terms.
>
> **"One query per document" — never true. It is two.** `_scan` resolves each
> item through `SqliteCanonicalStore.get_revision`, which reads
> `knowledge_revisions` and then `source_anchors`. Counted off a `sqlite3` trace
> callback rather than read off the code: one `list_items` for the project, then
> two statements per document.
>
> **"Was never compared against the alternative that actually runs today" — true
> when written, and answered in review round 8.** The comparison was made, at
> matched corpus sizes on one machine with a minimum of three runs, and it runs
> the *other way* from the direction this paragraph assumed. The fallback costs
> about **half** of this branch's worst legal query at equal row counts, and on
> document-shaped input — the same characters carried as fewer, longer documents
> — about a seventh of it. The figures and the corpus live at
> `index_scan.scan_statement` and under T-6 of the
> [threat model](../security/threat-model.md) rather than being copied here, for
> the reason given in the amendment to Decision item 4: one of each to keep true.
>
> **The decision this paragraph justified is unchanged, and its ground is
> inverted.** This branch is not preferred for being cheaper, because it is not.
> It is preferred because it is the one member of the three expensive retrievers
> that *releases* the GIL: `_scan`'s match is a Python `in` and `_dense_ranking`
> is pure Python, so both hold the interpreter lock for the whole of their work
> while `sqlite3` gives it up around `execute`. Measured under four concurrent
> callers, the fallback moves the p95 delay of the asyncio loop serving `/health`
> clear of its idle value and this branch leaves it there. So the trade is wall
> clock for latency isolation in a daemon shared by every project on the machine,
> and it is a trade rather than a saving.
>
> **Ordering and ratios, never absolutes.** The harness cannot control the
> machine it runs on. Re-run against a busier one for this amendment, it
> reproduced the ordering in every column and none of the magnitudes: the p95
> ratio came out above the band T-6 records and the worst-case ratio well below
> it, because the idle floor it is measured against had itself moved by about
> three times. A figure quoted from any single run of it is not a fact about the
> product.

## Compliance

Landed in Milestone 5:

- `tests/integration/test_index_store.py::test_japanese_is_searchable_by_substring`
  — parametrised over five Japanese queries, and asserts *both* halves: the word
  index cannot find them and the trigram index can. Asserting only the second
  would keep passing if `unicode61` were quietly swapped for `trigram`.
- `test_substring_matching_still_discriminates` — a substring index that matched
  everything would trade one broken search for another.
- `tests/unit/test_hashing_embedding.py::test_japanese_text_produces_useful_grams`
  — the same script problem on the dense side.
- **A schema version check on open.** `INDEX_SCHEMA_VERSION` is compared
  against the running value before any query runs
  (`SqliteIndexStore.is_searchable`), so point 5 is enforced, not merely
  recorded: a version-1 index is reported and falls back
  (`fallbackReason: index-schema-mismatch`) rather than degrading silently.
  This item was filed under "Still owed" for Milestone 6 when this ADR was
  first written, which was accurate at the time — the check did not exist yet.
  It landed later in the same milestone, in the change that gave
  `knowledge.search` its machine-readable `fallbackReason` values
  (`theurian.mcp.search`), and this section was not updated to match until
  now: it was stale before the round of fixes that prompted this pass.
  `tests/integration/test_index_fallback.py::test_a_fallback_names_the_reason_it_could_not_use_the_index[written-by-another-schema]`,
  `test_a_broken_index_is_never_reported_as_a_healthy_one[written-by-another-schema]`,
  and `tests/integration/test_index_store.py::test_a_missing_table_raises_instead_of_answering_nothing`.
  The parameter id names the *scenario* that produces the mismatch; the reason
  code it asserts is `index-schema-mismatch`. This section cited the reason code
  as the id until the scenarios were parametrised separately from the codes.
- **The scan below the floor, and that it is not worse than no index.**
  `tests/integration/test_short_query_retrieval.py::test_building_an_index_never_answers_less_than_having_none`
  is the one that matters: it compares the ranked path against the unranked one
  over the same corpus, which is a property no fixture can fake, rather than
  asserting that a chosen query returns something. Beside it,
  `test_a_query_below_the_trigram_floor_is_answered_from_the_index`,
  `test_a_query_above_the_floor_was_already_answered` as the control, and
  `test_a_short_query_that_is_absent_still_returns_nothing`.

  **"Not worse" is about what comes back, not what it costs, and the two run in
  opposite directions.** Review round 8 measured this path at roughly twice the
  unranked one at equal row counts — see the last amendment under Alternatives
  considered for why it is still the right path. No test holds that cost
  ordering; it is a benchmark, and it is recorded at `index_scan.scan_statement`
  and under T-6 of the [threat model](../security/threat-model.md).
- **That the scan ranks, and selects on relevance under a `LIMIT`.**
  `tests/integration/test_index_store.py::test_the_scan_below_the_floor_selects_by_relevance_not_by_creation_order`
  — the densest chunk is deliberately the newest, so dropping the relevance term
  from the `ORDER BY` fails it rather than passing by luck — and
  `test_the_scan_below_the_floor_breaks_ties_the_way_the_lookup_does`, which
  pins the tie residual above as an *equivalence* with the trigram lookup rather
  than as a promise about recency.
- **The cost bound, held from both edges rather than pinned to a number.** Three
  tests, because a tuning constant needs a floor, a ceiling and a mechanism, and
  no one of them holds the other two:
  `test_the_scan_spends_a_bounded_number_of_terms` asserts
  `SCAN_TERMS < MAX_QUERY_TERMS` first — a bound no query can exceed is not a
  bound — then that the term past the bound is not spent at all;
  `test_a_realistic_keyword_query_is_searched_in_full` holds it **from below**
  with a floor taken from the product rather than from the constant, five spaced
  two-character Japanese nouns, so shrinking `SCAN_TERMS` fails it (an earlier
  version sized its expectation from the constant and survived mutation to
  `8 → 1`); and `test_every_term_the_scan_matches_on_also_ranks` saturates the
  *last* admitted term against noise matching the first, so it fails the moment
  matching and ranking separate again. The band, 5 to 16, is asserted rather
  than described — its edges are the two measurements that justify eight, and
  retuning inside it is meant to cost nothing.

  > **Amended in Milestone 5.** This item previously cited
  > `test_terms_past_the_ranking_bound_still_select_but_do_not_order`, which no
  > longer exists and whose assertion is now the opposite of the invariant: a
  > term past the bound does not select either. The old item also said the test
  > was "sized from the constant, so retuning moves the test rather than
  > breaking it" — which sounds like care and is the property that let the
  > constant be mutated to 1 with the suite still green.
- **That one search costs this branch one pass over the corpus, and only its
  own.** `tests/integration/test_scan_cache.py`, landed in review round five,
  after the correction to the `LIMIT` amendment above. Two tests, failing in
  opposite directions and neither ruling out the other's failure:
  `test_one_search_scans_the_corpus_once_however_many_rows_were_withheld` —
  delete `SqliteIndexStore._scan_cache` and one request costs two scans, which is
  the timing observable T-17's residual is about — and
  `test_one_callers_withheld_rows_never_make_another_callers_search_cheaper` —
  share the cache between stores and two requests cost one scan, which is the
  same observable moved from between two reads to between two callers.

  They count **statements executed by SQLite**, read off a trace callback, rather
  than calls to `search_substring`. That distinction is the whole reason the file
  exists: the port call count is one or two whether the cache is present or
  absent, so a test built on a counting fake passes with the cache deleted and
  guards nothing while looking like a guard.

  Both are to be **deleted with the cache** when
  [#16](https://github.com/theurian/theurian/issues/16) gives `IndexStore` an
  explicit exhaustion signal, not carried forward. The first fails loudly and
  correctly on that day, because its precondition asserts the retriever was read
  twice. The second goes on passing — two requests cost two scans when there is
  no cache at all — so it has to be taken out deliberately rather than waited on
  to fail.

  > **Amended in Milestone 6.** Everything above is the Milestone 5 record and
  > is left standing; none of it describes code that still exists.
  > [#16](https://github.com/theurian/theurian/issues/16) gave `IndexStore` an
  > explicit exhaustion signal, `SqliteIndexStore._scan_cache` was deleted, and
  > `tests/integration/test_scan_cache.py` became
  > `tests/integration/test_scan_exhaustion.py`.
  >
  > The prediction above held on both halves. The first test failed loudly, its
  > precondition asserting a second read that no longer happens; the second,
  > `test_one_callers_withheld_rows_never_make_another_callers_search_cheaper`,
  > did not announce itself and was taken out deliberately.
  >
  > What replaces them, landed in Milestone 6, not this one:
  > `test_one_search_reads_the_scan_once_however_many_rows_were_withheld` holds
  > the port call count *and* the SQLite statement count at one, over four
  > withheld counts straddling the old 50/51 edge — the call count is assertable
  > now because the exhaustion signal fixed it, and the statement count is kept
  > beside it so a memo reintroduced to paper over a regression would not look
  > like a pass. `test_the_store_holds_no_state_between_searches` replaces the
  > deleted second test with a claim read off the object rather than off a
  > stopwatch: `SqliteIndexStore.__init__` assigns one field, so nothing is left
  > for one caller's query to hand the next.

- **The floor's lower bound, both halves.**
  `test_a_single_letter_does_not_earn_a_pass_over_the_corpus` (parametrised over
  `e`, `a b c`, `7`, `#`) and `test_a_single_character_that_is_a_whole_word_still_scans`.
  Asserting only the second is what a floor "tightened just a little" would keep
  passing.
- **The exception for a lone punctuation character, as a decision on record.**
  `tests/integration/test_short_query_retrieval.py::test_a_lone_punctuation_mark_is_declined_rather_than_answered`,
  parametrised over `。` and `#`: the unranked scan answers them, the ranked path
  declines, and the test says so in both directions so nobody restores the
  behaviour by lowering the floor again.
- **The scan's own correctness at the SQL level.**
  `test_a_like_wildcard_typed_by_a_user_is_a_character_not_a_pattern`,
  `test_an_escaped_wildcard_still_finds_a_literal_one`,
  `test_the_scan_orders_case_insensitively_because_it_matches_that_way` — an
  order that contradicted the selection producing it would be worse than no
  order — and `test_the_scan_below_the_floor_reads_every_column_the_trigram_index_does`,
  which is what stops a column added to the index and forgotten in the scan from
  making a two-character query search less of the corpus than a three-character
  one. Scope is covered by `test_the_scan_below_the_floor_keeps_the_project_filter`
  and `test_the_scan_below_the_floor_withholds_drafts_too` (FR-R1, SEC-13).
- **That this retriever's rows are read through the caller's visibility**, which
  is what the amendment to the first Negative consequence above requires.
  `tests/integration/test_mcp_tools.py::test_a_withheld_document_changes_nothing_a_caller_can_see`
  states the property over the whole response, and
  `test_the_depth_probe_reaches_the_withheld_document_inside_the_candidate_depth`
  asserts the fixture can still break it. One layer down,
  `tests/integration/test_retrieval_service.py::test_a_withheld_row_cannot_choose_which_chunk_of_a_visible_document_is_published`
  pins the part no whole-response comparison can reach.
- **And that it holds on the writing system this ADR exists for.** Both tests
  above are parametrised by corpus — `_DepthCorpus`, ids `english` and
  `japanese` — over the same crowd, the same ids, the same query shape and the
  same staleness. The English corpus is unchanged, so the parametrisation added
  a case rather than editing the one that was passing.

  This item was filed under "Still owed" for Milestone 6 in the pass that found
  it, on the grounds that the strongest evidence for the CJK case was a
  reproduction script CI never runs. It landed in Milestone 5 instead, and what
  it produced is better than the gap-filling it was scoped as:

  **It made the "fifty slots are the candidate list" claim a fixture-pinned
  measurement.** The guard test now records *which* retriever fills the depth,
  through `_DepthCorpus.word_index_rows`. Against the same crowd: English 50
  word-index rows and 50 trigram rows, Japanese **1** and 50. The single
  Japanese row is the withheld runbook itself, reached through the ASCII
  credential — `unicode61` treats each note as one token and cannot match
  `ゲートウェイ` inside it.

  **And it caught a failure English could not show.** Measured against a
  mutation that removes the depth loop from the trigram retriever alone: the
  English case notices only at `maxTokens=32_000`, where the whole ranking is
  published and `usedTokens` moves. The Japanese case notices additionally at
  `limit=50` **with the default budget**, through `droppedForBudget` — 43
  against 44 — which is the exact field and the exact budget the shipped
  extraction attack used. On English the word index carries the ranking and
  hides the displacement behind its own fifty rows. That is the difference
  between a test that covers a writing system and a test that covers the
  failure, and it is the argument for parametrising rather than adding a
  Japanese smoke test beside the English one.

  **Neither corpus can be dropped, and they are necessary in opposite
  directions.** Stated because from either one alone the other looks redundant —
  each is a duplicate of a passing case until you know which mutation it is the
  only witness for. `RetrievalService` reads the depth loop twice, once per
  lexical retriever, so there are two mutations here and not one. Applying each
  on its own to a copy of the tree:

  | Depth loop removed from | English | Japanese |
  | :-- | :-- | :-- |
  | the trigram retriever | fails 4 cases, only at `maxTokens=32_000` and under `useDense` | fails 6, including `limit=50` at the default budget |
  | the word index | fails 4 cases | **fails nothing** |

  The counts are cases of
  `test_a_withheld_document_changes_nothing_a_caller_can_see`; both mutations
  also fail one corpus-free case in `tests/unit/test_retrieval_depth.py`, left
  out because it does not discriminate between them.

  The second row is this ADR's own measurement read backwards. `word_index_rows`
  is 1 on Japanese, so that retriever's depth loop has nothing to skip and
  removing it displaces nothing observable — the mutation is invisible on the
  corpus that exists for this ADR. English is the only case holding the word
  index's half of the loop, exactly as Japanese is the only case holding this
  retriever's. Delete either and one loop loses its only end-to-end witness.

  Two caveats, recorded because neither is visible from a passing run:

  - **The chain has a human link.** The guard fixes "this corpus puts exactly
    one withheld row in the top fifty"; `tests/unit/test_retrieval_depth.py`
    fixes "one withheld row costs one retriever pass". Nothing joins them, so
    the conclusion that the shipped mitigation covers this corpus is drawn by a
    reader, not by CI.
  - **`word_index_rows=1` is a property of the fixture's prose, not of
    Japanese.** The notes carry a tenant number surrounded by spaces, so
    `unicode61` does get digit tokens out of them. A query containing a digit
    would match the whole crowd through the word index and the guard would
    quietly start asserting something else.

Still owed, with the milestone that will satisfy it:

- **The mixed-length residual** (Milestone 6, ADR-0021). A short term mixed
  with a term of three characters or more is still dropped from the trigram
  expression and nothing scans for it in its place — see the amendment to the
  Negative consequence above. Closing it needs the per-term IDF work already
  owed to ADR-0021's relevance-floor and tie-break items, because a `LIKE`
  predicate merged into the same statement as a `MATCH` has no `bm25` score to
  rank by.
- **The scan's saturation residual** (Milestone 6, ADR-0021). Occurrences
  weighted by term length lets one heavily repeated term outrank two terms
  covered once each. It is the same missing per-term IDF as the item above and
  is expected to be discharged by the same change; it is listed separately so
  that closing one and not the other cannot be mistaken for closing both.

Recorded as accepted residuals rather than owed work, so that finding one later
is not mistaken for finding a defect: the tie behaviour, the lone-punctuation
exception, and the case-folding asymmetry, all three under Negative
consequences above. Each is measured, each has a stated reason to be preferred
to its remedy, and none is waiting on a milestone.
