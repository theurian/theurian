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
- Disk. The trigram index is the larger of the two, on a file that is derived
  and disposable (ADR-0022), which is the cheapest place to spend it.
- A query term of one or two characters reaches the word index only. For a
  script with no word boundaries that is a short query with no lexical evidence
  either way; it is recorded rather than solved.
- **An index built at schema version 1 is not detected.** `search_substring`
  catches `sqlite3.OperationalError` and returns nothing, so a version-1 index
  silently answers without the trigram retriever, and a Japanese knowledge base
  goes back to being invisible with nothing in the response saying so. Confirmed
  by dropping `chunks_trigram` from a real index and querying it. The remedy is
  `theurian index build`; making the mismatch *visible* is owed — see Compliance.

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

Still owed, with the milestone that will satisfy it:

- **A schema version check on open** (Milestone 6). `INDEX_SCHEMA_VERSION` is
  written into `index_metadata` and never read back, so point 5 above is
  recorded and not enforced: a version-1 index degrades silently instead of
  reporting that it predates the trigram table. The blue/green index work in
  Milestone 6 owns index lifecycle and is where this belongs.
