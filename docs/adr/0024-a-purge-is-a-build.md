# ADR-0024: A purge is a build; a published index is never written

- Status: accepted
- Date: 2026-08-08
- Deciders: Theurian maintainers
- Requirements: FR-R2, NFR-4, NFR-7, SEC-13, T-10, T-17a
- Answers the open question in [ADR-0022](0022-index-lives-in-its-own-database.md)
- Discharges the index half of [ADR-0018](0018-single-writer-synchronous-in-m1.md)'s
  "the derived index has no single-writer contract at all"

## Context

[ADR-0022](0022-index-lives-in-its-own-database.md) closes by asking a question
it does not answer:

> So blue/green has to answer a question this ADR has not been asked yet:
> whether a purge produces a new build and swaps the pointer — which makes it an
> ordinary build under points 5 and 6, at the cost of rewriting the whole file to
> remove a few rows — or mutates the published build in place, which is a write
> to the file searches are reading and needs the writer discipline ADR-0018 owes
> for the index.

The question exists because Milestone 6 has to remove withdrawn rows from the
index. [#15](https://github.com/theurian/theurian/issues/15) (T-17a) is the
reason: FTS5's `bm25` weights every visible row against collection statistics
computed over *every* row in the file, so a document retired since the last build
reweights the visible ones. The visibility gate removes rows from the result; it
does not remove them from the statistics the survivors are scored against.
Tombstones do not substitute, because a tombstoned row is still a row those
statistics count.

Two facts already recorded constrain the answer.

- **ADR-0022's own guarantee was withdrawn, not delivered.** Point 6 promised
  that "the previous build is not deleted when a new one is published. A search
  already reading it keeps a consistent view." The Milestone 5 amendment
  withdrew it: `theurian index build` now reaps every build the pointer does not
  name, because `SqliteIndexStore` holds no connection between calls and
  `sqlite3.connect` on a deleted path *creates an empty database there*. NFR-4 —
  "the previously published index answers every query while a new build runs,
  zero read downtime" — is therefore unmet.
- **ADR-0018 has no contract for this writer.** `theurian index build` is
  "serialised by nothing but the fact that a person runs it", and a purge is a
  second writer to a file searches are reading.

The phrase that decides the question is **"at the cost of rewriting the whole
file"**. That cost was assumed and never measured, and it is the only reason the
in-place option looks attractive. So it was measured first.

### What "rewriting the whole file" actually costs

The assumption conflates two different operations. Re-deriving a build reads the
canonical store, chunks every revision, embeds every chunk and writes a new file.
*Copying* a build and deleting rows from the copy re-derives nothing. Both
produce a new file and a pointer swap; only the first is expensive, and the
expensive part of it is the derivation, not the file.

Measured on an Apple M-series laptop, APFS, CPython 3.13.3, SQLite 3.47.1, over a
corpus of mixed English and Japanese documents of ~3,000 characters with
embeddings enabled — median of three runs, eight documents withdrawn:

| Corpus | Index | Re-derive (`index build`) | Purge as a new build | Ratio |
| :-- | --: | --: | --: | --: |
| 400 documents, 1,996 chunks | 12.3 MB | 2,614 ms | 51 ms | 51× |
| 1,600 documents, 7,874 chunks | 48.5 MB | 10,957 ms | 175 ms | 63× |
| 5,000 documents, 24,481 chunks | 150.3 MB | 37,684 ms | 579 ms | 65× |

The ratio is flat across a 12× corpus range because both terms are linear in the
corpus and the constants differ by two orders of magnitude. **Rewriting the whole
file to remove a few rows costs about a sixtieth of "an ordinary build".** The
premise the in-place option rested on is false.

**"A few rows" is load-bearing in that sentence, and the ratio is a property of
it.** Every figure above withdraws eight documents. A purge costs a whole-file
copy plus a delete proportional to the rows removed, while a re-derive is
proportional to the corpus, so the ratio falls as the withdrawn fraction rises.
Measured on 800 documents, a 24.5 MB index, against a 4,735 ms re-derive:

| Withdrawn | Purge | Ratio |
| :-- | --: | --: |
| 8 (1%) | 67 ms | 70.6× |
| 80 (10%) | 152 ms | 31.1× |
| 200 (25%) | 506 ms | 9.3× |
| 400 (50%) | 931 ms | 5.1× |
| 720 (90%) | 1,408 ms | 3.4× |
| 800 (100%) | 1,513 ms | 3.1× |

It never inverts — a purge is cheaper than a re-derive at every fraction,
including withdrawing the entire corpus — so the decision does not turn on this.
What turns on it is the *shape of the argument*: "a sixtieth" describes the case
this design is for, which is a purge triggered per withdrawal (decision 5) and
therefore removing few documents at a time. A caller who retired a quarter of a
knowledge base in one operation would see 9×, not 60×, and would still be right
to purge.

### What an in-place purge costs

The saving an in-place purge buys over a copy is the copy: 175 ms on a 48.5 MB
index, against 24 ms for the `DELETE` alone. What it spends is a torn read.

`SqliteIndexStore` opens and closes a connection per call, and one
`RetrievalService.search` calls it several times. So an in-place purge does not
have to race a *statement* to be observed — it only has to land between two of
one request's connections, which is a window of milliseconds on every search
rather than a narrow one. WAL gives a consistent view of a statement, not of a
request.

Measured deterministically, by firing the purge from a wrapper exactly once
between `search_lexical` and `search_substring`, with the visibility gate
withholding nothing so that only the index decides. 38 of 300 documents
withdrawn, one query:

| | Candidates | From withdrawn documents |
| :-- | --: | --: |
| stale — the index still holds them | 69 | 25 |
| purged — the index no longer holds them | 64 | 0 |
| **torn — the purge landed mid-request** | **81** | **15** |

The torn response equals neither corpus's answer. It is a fusion of a lexical
ranking scored against the pre-purge collection statistics with a substring
ranking scored against the post-purge ones, and nothing in the response says so.
In production the visibility gate still removes the 15 withdrawn rows from what
is *published*; what survives is the scoring, which is exactly the T-17a channel
the purge exists to close, held half-open for the duration of every purge.

### What the purge has to prove, and what it depends on

The property is not "the withdrawn rows are gone from the result" — the gate
already does that, and T-17a is the demonstration that it is not enough. It is:

> An index that held the withdrawn rows and had them purged answers **identically**
> to an index that never held them.

Measured as one query against three corpora, with `stale` as the control so the
comparison can fail. 400 documents, every eighth withdrawn and ten times the
length of the rest — `avgdl` is the channel review round five measured, and a
withheld document of average length moves it least:

| Query | stale (gated) vs fresh | pruned vs fresh |
| :-- | :-- | :-- |
| `retention isolation` | different, **order differs** | identical |
| `authentication token` | different scores | identical |
| `quarantine ledger` | different, **order differs** | identical |
| `connection pool` | different scores | identical |
| `認証トークン` | different scores | identical |
| `接続プール` | different scores | identical |
| `isolation` | different, **order differs** | identical |
| `認証` | same | identical |

"Identical" is the full ranking, chunk ids and BM25 scores to ten decimal places,
untruncated.

**The implementation property this rests on**: FTS5's `'delete'` command, issued
by `chunks_fts_delete` and `chunks_trigram_delete`, removes the row's postings
*and* decrements the averages record — `nRow` and the per-column total sizes —
that `bm25` reads as `N` and `avgdl`. Deleting the `chunks` row is what makes the
statistics equal, not merely what makes the row absent. If a future FTS5 kept the
averages record and only removed postings, every line of that table would still
say "identical" for `nHit` and stop saying it for `avgdl`, so the fixture must
carry a long-document configuration or it stops testing the channel it names.

`認証` is the one row where the control agrees, and that is the expected answer
rather than a weak fixture: two characters fall below the trigram floor and are
answered by the scan, which ranks by occurrences counted inside each row and
reads no collection statistic. #15 says so; this measures it. The purge is not
what fixes that path, because nothing there was broken.

## Decision

**A published index build is immutable. Withdrawal produces a new build and swaps
the pointer, exactly as ADR-0022 points 5 and 6 describe.**

1. **Nothing writes to a file `active-index.json` names.** From the moment the
   pointer names a build, that file is read-only for the rest of its life. This
   is the rule; everything below is how it is kept.

2. **A purge build is derived from the previous build, not from canonical
   state.** Copy the published build to a new `theurian-index-<newId>.sqlite`,
   `DELETE FROM chunks` the withdrawn revisions in the copy, publish the copy.
   The FTS5 delete triggers carry the removal into both the word and the trigram
   index; `embeddings` cascades, because `PRAGMA foreign_keys = ON` is in
   `CONNECTION_PRAGMAS` and is applied to index connections too.

   **The copy inherits the parent's identity, and the purge must overwrite it.**
   `Connection.backup` copies pages, so `index_metadata.index_build_id` in the
   new file still names the build it was copied from, and `built_at` still
   records when *that* was made. Nothing in `src/` reads either back today —
   `mcp/search.py` publishes `indexBuildId` from the pointer — so this is latent
   rather than broken, which is exactly why it is written into the decision: the
   first thing to read it would find a file whose own record of itself disagrees
   with the pointer that names it, and would find it a long way from here.

3. **The copy is `sqlite3.Connection.backup`, not `shutil.copyfile` and not
   `VACUUM INTO`.** Measured on a 49.2 MB index with rowid gaps — the state every
   build after the first purge is in:

   | Mechanism | Includes uncheckpointed WAL content | `chunks` rowids stable | Cost |
   | :-- | :-- | :-- | --: |
   | `shutil.copyfile` | **no — lost 100 committed rows** | yes | 86 ms |
   | `VACUUM INTO` | yes | yes, observed | 157 ms |
   | `Connection.backup` | yes | yes, by construction | 136 ms |

   `copyfile` is disqualified: the `-wal` sidecar is a separate file, so a copy
   taken while a writer holds committed-but-uncheckpointed content is a database
   missing that content, silently.

   **The row above understates it, because the corpus it was measured on already
   had its schema checkpointed.** When the uncheckpointed pages carry the schema
   rather than only rows, the copy has no table at all:

   ```
   committed rows in source: 100      wal present: True (8272 bytes)
   copyfile -> OperationalError: no such table: t
   backup   -> 100
   ```

   `VACUUM INTO` is correct today and rests on something SQLite declines to
   promise — the documentation says VACUUM "may change the ROWIDs of entries in
   any tables that do not have an explicit INTEGER PRIMARY KEY",
   `chunks.chunk_id` is a TEXT primary key, and `chunks_fts` and `chunks_trigram`
   are `content='chunks', content_rowid='rowid'`, so a renumbering would silently
   repoint every posting in both indexes. It was observed stable on 3.47.1,
   including on a gapped table. **A design resting on observed-but-unpromised
   behaviour becomes a silent corruption at the next release.** `backup` copies
   pages, so rowid stability is not a behaviour it could get wrong, and it is the
   faster of the two.

4. **A purge goes through the same single-writer interface as a build, and there
   is exactly one such interface.** This is ADR-0018 point 1 applied to the index
   for the first time. `IndexStore.create` and the purge are both *productions of
   a new build*; publishing is a separate step that takes the index write lock.
   Nothing outside that interface opens an index file for writing.

5. **A purge is triggered by the withdrawal, not by a person remembering.**
   Whatever retires, supersedes or rejects a revision publishes the purged build
   in the same command. The window in which the index holds a withdrawn row is
   then the duration of one command, rather than "until someone runs a rebuild".

   **This is what bounds T-17a, and the swap is not what bounds it.** The swap
   protects the next window; it does nothing about a response already served from
   a build that still held the row. Nothing in this ADR remedies bytes already
   sent, and the only quantity under its control is how long the stale build
   stays published.

6. **Publishing never deletes. Reclaiming becomes explicit `theurian index gc`.**
   Measured against a reader that had already resolved the pointer, with the old
   build reaped the way `_reclaim` reaps it today: **1,889 errors against 163
   successful searches in 1.5 seconds**, each error
   `IndexUnreadableError: ... no such table: chunks_fts`, and a fresh empty
   database left at the reaped path afterwards. `theurian index gc` is already
   named by ADR-0007, ADR-0016 and ADR-0017 and does not exist; this is where it
   lands. Its reap rule is `_reclaim`'s — only builds whose ULID sorts below the
   published one, so two concurrent producers cannot delete each other's work.

7. **A search holds one connection to its build for the whole request.** This is
   what makes NFR-4 true rather than aspirational, and it is the half of point 6
   that retention alone does not buy. Measured: a held connection keeps answering
   after its file is unlinked, in all three configurations — `journal_mode=DELETE`,
   WAL with the sidecars kept, and WAL with all three files unlinked. A *new*
   connection at the same path does not: `is_searchable()` returns `False`,
   the query raises `no such table: chunks_fts`, and a file exists at that path
   again afterwards.

   Index connections are therefore **to be** opened `file:<path>?mode=ro` with
   `uri=True`. Measured: the default `sqlite3.connect` creates a database at a
   missing path; `mode=ro` raises `unable to open database file` and creates
   nothing. That is what turns "the pointer outlived its file" back into the
   fallback ADR-0022 promised, instead of an empty index that reports itself
   healthy.

   **Not yet implemented.** `infrastructure/sqlite/index_store.py:236` is still
   `sqlite3.connect(path)`, and every other decision in this ADR is likewise a
   decision rather than a description — see Compliance, which says so of the
   whole section. Called out here as well because this is the one point stated
   as a property of the code rather than as a rule for it, and a reader who
   stops at the paragraph above would take it for shipped behaviour.

8. **Withdrawal is transitive over derived content.** A node whose text is
   *derived* from a chunk — a RAPTOR summary (ADR-0008) is the case this project
   will have — is not withdrawn by deleting the chunk. A purge can delete a row;
   it cannot delete a sentence out of a summary. So:

   - the index schema records, for every derived node, the chunk ids it was
     derived from;
   - a purge deletes or recomputes every node reachable from a purged chunk
     through that relation, transitively, before it publishes;
   - a derived node whose derivation edges cannot be resolved is deleted, not
     kept. An unresolvable edge is the state a schema migration or a partial
     build leaves, and keeping the node is the failure mode this point exists to
     prevent.

   Recorded now, with `infrastructure/raptor/` an empty package and
   `SummarizationProvider` a port with no adapter, because the alternative is
   designing the purge twice. T-10 makes cross-sensitivity mixing structurally
   impossible at *build* time and says nothing about withdrawal *after* build;
   this point is that gap, closed before the thing that opens it is written.

## Consequences

### Positive

- ADR-0022 points 5 and 6 hold for every writer, not only for builds. "The
  rebuild happens in a file nobody is reading" becomes true of the purge as well.
- **NFR-4 is dischargeable, and points 6 and 7 are not two guards on one window
  — they close different ones, and point 7's exists only because point 6 creates
  it.** Point 6 closes publication: with the old build retained, a search running
  across a publish sees no error at all. Retaining builds is what then makes
  reclaiming necessary, and point 7 is what makes reclaiming safe for a request
  already in flight.

  Measured on a 400-document index, searches during a publish that reaps:

  | Configuration | ok | errors |
  | :-- | --: | --: |
  | neither — a connection per call, no retention (ships today) | 40 | 2,627 |
  | point 7 only — handle scoped to the **request** | 331 | 86,496 |
  | point 7 only — handle scoped to the **process** | 3,420 | 0 |
  | point 6 only — a connection per call, retention | 180 | 0 |
  | both | 1,163 | 0 |

  The first row and point 6's "1,889 errors against 163 successful searches" are
  the same failure counted under different request shapes — one index call per
  iteration there, three per request here — so the absolute numbers differ and
  the ratio does not. Neither is a throughput measurement; what each row asserts
  is whether the error column is zero.

  and the window point 7 is actually for, one request of four index calls with
  the reap landing after the first: **1 of 4 answered** with a connection per
  call, leaving an empty database recreated at the reaped path, against **4 of 4**
  with one held connection and no file recreated.

  **The two "point 7 only" rows are the same design measured two ways, and only
  the first is point 7.** This decision says a search holds one connection *for
  the duration of a request*, so every request beginning after the reap must open
  the file again and every one of them fails — 86,496 times above. A handle
  scoped to the **process** never reopens anything, so no iteration after the
  unlink ever asks the filesystem for the file; it measures "does a descriptor
  survive an unlink", which is true and is not this question. Both rows are kept
  because the second is what a re-measurement naturally produces: this ADR's own
  drafting hit it, read 0 errors, and briefly concluded that point 7 alone closed
  the publish window. It does not.

  The earlier claim here — "retention without a held handle leaves the window
  ADR-0022's amendment measured" — was false in the other direction. That window
  is created by reaping *at publish*, which point 6 abolishes; retention alone
  measures 0 errors.
- T-17a's root fix is a `DELETE` and a swap, not a recomputation of collection
  statistics per request, and not a rebuild. The measured equality is exact.
- ADR-0018's index writer gets an interface rather than a convention, which is
  the property that ADR's closing sentence says cannot be added later.

### Negative

- Old builds accumulate until `theurian index gc` runs. This is the same trade
  ADR-0017 already took for state databases — "disk usage grows until `index gc`
  runs. Deliberately explicit" — and the reason is the same: automatic deletion
  of a file a reader may hold is what point 6 measured.
- **Builds accumulate at one whole file per publish, and that is the real disk
  cost of point 6 — not the +1.5% below.** Measured on 800 documents, a 24.5 MB
  index: ten publishes with nothing reaped leave **ten files and 246.0 MB**,
  of which `gc` reclaims 221.4 MB. The two numbers answer different questions,
  and quoting only the second understates the first by an order of magnitude:
  +1.5% is how much *one* file grows across a chain of purges, while this is how
  many files exist at once. A project that purges on every withdrawal reaches
  this within a working session, so `theurian index gc` is not an occasional
  tidy-up — it is what makes point 6 affordable, and a user who never runs it
  pays a build's worth of disk per withdrawal.
- A purge does not compact. `backup` copies free pages, so a chain of purges
  grows the file slowly: measured at 13.23 MB → 13.43 MB over 20 successive
  single-document purges, +1.5%, and 24.5 MB → 25.0 MB on the corpus above.
  `gc` compacts, and a full `index build` resets it. Recorded rather than
  optimised, because 1.5% over 20 rounds is not a cost worth a second
  mechanism — unlike the accumulation above, which needs one.
- Point 7's guarantee is POSIX. On Windows an unlink of a file with an open
  handle fails, so `gc` must treat a failed unlink as "reclaim it next time"
  rather than as an error. That is a safer failure than the POSIX one and it
  still has to be written, or `gc` raises at a user for a file that is merely
  busy.
- Point 8 costs an index schema change before RAPTOR exists, and therefore an
  `INDEX_SCHEMA_VERSION` bump for a table nothing yet writes to. ADR-0022 point 3
  is exactly why that is affordable: an index schema change costs an index
  rebuild and nothing else.

### Neutral

- A purge and a build are now the same shape — produce a file, publish a pointer
  — and differ only in where the new file's rows come from. Whether they share an
  implementation is a question for the code, not for this ADR.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| Mutate the published build in place, under a lock | Measured: a purge landing between two of one request's connections produces a response equal to neither corpus — 81 candidates against 69 stale and 64 purged, 15 of them scored against statistics the purge had already removed. WAL makes a *statement* consistent, not a request. What it saves is the copy: 175 ms on a 48.5 MB index. |
| Tombstone the withdrawn rows and filter at query time | What leaks is FTS5's collection statistics, and those count rows a tombstone leaves in place. The measured equality holds only where the row is deleted. |
| Purge on read | A write on the retrieval path, to a derived artifact, from a code path that must not take a write lock. It also makes the cost of a search a function of how much was withdrawn, which is the timing channel T-17 records. |
| Re-derive the whole index from canonical state for every purge | 51× to 65× the cost, measured, across a 12× corpus range — and it re-embeds, which against a real embedding provider is a network cost per purge rather than a CPU one. |
| `shutil.copyfile` as the copy primitive | Drops the `-wal` sidecar. Measured: the copy held 1,055 rows while the writer that had committed saw 955 — and where the uncheckpointed pages carry the schema, `no such table`. |
| `VACUUM INTO` as the copy primitive | Correct today and 15% slower, but rests on rowid stability that SQLite documents as *not* guaranteed for tables without an INTEGER PRIMARY KEY, which `chunks` is. Both FTS5 tables are external-content keyed on `chunks.rowid`. Kept as `gc`'s compaction step, where rebuilding the b-trees is the point. |
| Keep reaping eagerly at publish time | 1,889 errors against 163 successful searches in 1.5 seconds, measured against the current `_reclaim`. |
| Recompute collection statistics per request instead of purging | Corrects one channel at the cost of the whole corpus per query, and leaves the withdrawn text in a file that is git-ignored, unsigned and readable (SEC-7). The index holding rows the canonical store has withdrawn is the defect; the statistics are one of its faces. |

## Compliance

**Nothing below is landed. This ADR precedes its implementation deliberately —
the decision is cheaper to get wrong on paper — and the section says so rather
than listing tests that do not exist.** Each item names what must go RED when the
decision is violated.

**Tracked as [#103](https://github.com/theurian/theurian/issues/103), as one
issue rather than eight.** They are not independent debts that could land
separately; they are the acceptance criteria of a single change — the step-3
purge and blue/green publish — and filing them apart would assert that a purge
could ship having satisfied some and not others. The one genuinely separable
item, ADR-0018's single-writer interface for the index, is named as such there.

Owed by the change that implements this ADR:

- **The equality, as one query against two corpora.** An index built from a
  corpus including the withdrawn documents and then purged, and an index built
  from a corpus that never held them, must return byte-identical rankings —
  chunk ids and scores — for both `search_lexical` and `search_substring`. With
  a `stale`-index control in the same test that must be **different**, or the
  assertion is vacuous. The withdrawn documents must be long relative to the
  corpus mean, or the fixture exercises `nHit` and not `avgdl`.
- **A purge does not tear a request.** A purge fired between two retrievers of
  one `RetrievalService.search` must be impossible to construct against the
  shipped composition root, because the file the request holds is not the file
  the purge writes. The test is the wrapper above, asserting that the answer
  equals the pre-purge corpus's exactly.
- **Publishing does not delete.** After a purge, the previous build's file must
  still exist and still be searchable.
- **A search survives `gc`.** A held store must keep answering after its file is
  unlinked, and a new store at that path must report `is_searchable() is False`
  and leave no file behind.
- **`sqlite3.connect` is not reachable for an index path without `mode=ro`.** The
  measured failure is that it creates an empty database; a test that deletes an
  index and asserts the fallback reason is what pins it.
- **Derived nodes.** Once a summary node exists, purging a chunk it was derived
  from must delete or recompute it. Until then, the schema's derivation table and
  the purge's traversal are what carry point 8, and a test that inserts a
  synthetic derived node is what stops the traversal from being written and never
  run.
- **A purged build holds no orphaned row of any kind.** `embeddings` is removed
  by `ON DELETE CASCADE`, which SQLite enforces **per connection**, not per
  database: `PRAGMA foreign_keys` defaults to *off*, and a purge that opens its
  own connection without `CONNECTION_PRAGMAS` deletes the chunk and leaves the
  vector. The failure is silent and one-directional — the dense retriever joins
  `embeddings` to `chunks`, so an orphaned vector returns nothing rather than
  returning a withdrawn row — which is why it needs a test rather than a review:
  count `embeddings` after a purge and assert it fell by the same number as
  `chunks`. The same test covers the FTS5 tables, whose removal rides on triggers
  and needs no pragma.
- **A purged build's `index_metadata` names itself.** Per decision 2: assert
  `index_build_id` in the new file equals the id the pointer publishes, not the
  parent's.

Owed to ADR-0018, and satisfied by point 4 rather than by this ADR's own tests:
its "the derived index has no single-writer contract at all" is discharged for
the index when one interface owns every index write and a test asserts that
surface. Its `CanonicalStore.transaction()` half is not touched here.

NFR-4 is discharged by points 6 and 7 together, and by neither alone. It is not
discharged by this ADR being accepted.
