# ADR-0024: A purge is a build; a published index is never written

- Status: accepted
- Date: 2026-08-08
- Deciders: Theurian maintainers
- Requirements: FR-R2, NFR-4, NFR-7, SEC-13, T-10, T-17a
- Answers the open question in [ADR-0022](0022-index-lives-in-its-own-database.md)
- **Narrows** the index half of
  [ADR-0018](0018-single-writer-synchronous-in-m1.md)'s "the derived index has no
  single-writer contract at all": a published build is never written, so there is
  no live file for a second writer to reach. **That property is decisions 1 and
  2 plus the naming discipline, not decision 4** — every production writes a new
  file under a fresh ULID and a `.building` suffix and publishes by `os.replace`,
  and three tests refuse the alternative
  (`test_building_over_an_existing_file_is_refused`,
  `test_a_purge_into_an_existing_path_is_refused`,
  `test_a_purge_refuses_to_write_over_another_writers_building_file`), with
  `test_a_purge_leaves_the_published_build_untouched` holding the published build
  byte-for-byte. It does **not discharge** ADR-0018's contract. This line read
  "Discharges the index half of…" until 2026-09-01, and decision 4's correction
  below records the measurement that retired the word. The contract itself is
  owed and unscheduled, tracked in
  [#439](https://github.com/theurian/theurian/issues/439).

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
by `chunks_fts_delete` and `chunks_trigram_delete`, stops the row's postings
being *matched* and decrements the averages record — `nRow` and the per-column
total sizes — that `bm25` reads as `N` and `avgdl`. Deleting the `chunks` row is
what makes the statistics equal, not merely what makes the row absent. If a
future FTS5 kept the averages record and only stopped matching the postings,
every line of that table would still say "identical" for `nHit` and stop saying
it for `avgdl`, so the fixture must carry a long-document configuration or it
stops testing the channel it names.

> **Corrected on 2026-09-02 in PR #498's round-one review. This sentence said
> `'delete'` "removes the row's postings *and* decrements the averages record",
> and only the second half is true.** `'delete'` writes a **tombstone**: the
> postings stay in the segment structure until a merge, and **nothing in the
> shipped purge merges**. The equality this section measures is real, and its
> stated mechanism was half wrong. The averages half is what carries the
> equality — the record *is* decremented, which is why the rankings above are
> byte-identical — and the postings half is not, and has a measured cost of its
> own.
>
> Measured at 5,950 withdrawn rows (visible 50), a purged build against its own
> `optimize`d copy against a never-held build: **1,481 trigram blocks and
> 5,403,892 trigram bytes purged; 11 and 33,439 optimized; 13 and 35,638
> never-held** — 151× the postings for rows the build no longer serves — with
> the substring scan at 16.8 ms, 1.1 ms and 1.2 ms and every response identical.
> End to end the duration is monotone in the withdrawn count: at 5,950
> withdrawn a request costs **+27.4 ms** more than at nothing withdrawn — the
> round-one measurement, +27.36 ms — and **six later re-runs give +27.59…+28.18
> ms**, so the delta is the stable figure across separate runs while the ratio
> moves with its denominator: 5.08–5.67×, median 5.41×. The conclusion moves on
> neither. That crosses the threat model's 1.40 ms end-to-end floor (TB-1)
> between 500 and 1,000 withdrawn rows.
> The `optimize`d comparison above is the source table's; a `VACUUM` applied in
> the reproduction lands at the same 241,664 B.
>
> So this section's *content* conclusion is unchanged and the *implementation
> property* under it is narrowed, above, to what `'delete'` actually guarantees.
> The residue and its closure — merging inside the purge — are owned by
> [#499](https://github.com/theurian/theurian/issues/499), which is a **face of
> T-17a** (*the index still holds the withdrawn rows*, surviving at the FTS5
> segment level) rather than a new class; its recorded closure is the merge or
> an acceptance carrying the measured bound. The threat model's T-17a entry
> carries the full measurement, and these records move again when #499 lands.

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
   records when *that* was made. Neither column is *served* — `mcp/search.py`
   publishes `indexBuildId` from the pointer — so a disagreement here reaches no
   caller. What separates the two columns is whether anything reads them at all:

   - **`index_build_id` is read back.** `SqliteIndexStore.add_nodes` selects it
     out of this file's own `index_metadata` to stamp each summary node with the
     build it belongs to, rather than taking it as an argument that could
     disagree with the file it writes into. The first reader this paragraph
     predicted has arrived, and it arrived *inside the purge*: `purge_into` runs
     the forest recompute before `_restamp`, so the nodes it writes are stamped
     with the parent's id and `_restamp`'s second statement — `UPDATE nodes SET
     index_build_id` — is what repairs them. That statement was added after a
     purge was measured leaving a survivor naming the build it was copied from
     (`test_restamp_updates_survivors_index_build_id_too`), which is this
     paragraph's own prediction landing one level down.
   - **`built_at` is written and never read**, so for that column the original
     reasoning stands: the first thing to read it would find a file whose own
     record of itself disagrees with the pointer that names it, and would find it
     a long way from here. Measured at `6b83be1`, `git grep -nw built_at
     packages/theurian-core/src` returns six lines: the `index_metadata` column
     declaration, the `create` INSERT and the `_restamp` UPDATE, plus three on
     the unrelated `findings_metadata` table — a declaration, a comment and an
     INSERT. No SELECT of it anywhere. `metadata()` does `SELECT *`, so the
     value is fetched, but neither of its two callers
     (`retrieval_service.py`, `withdrawal_purge.py`) reads that key.

   > **Corrected in the #199 unit-A follow-up
   > ([#426](https://github.com/theurian/theurian/issues/426)).** This paragraph
   > said "nothing in `src/` reads either back today — so this is latent rather
   > than broken". That was true of both columns when the decision was written
   > and is now true of `built_at` only. The restamp discipline this decision
   > asked for is what kept the record honest across the change: the reader
   > arrived on the purge path itself, met the stale id exactly as predicted, and
   > was already covered because `_restamp` had been extended to `nodes`. So the
   > conclusion is unaffected; only its premise had to be split per column, and
   > the "latent rather than broken" framing now belongs to `built_at` alone.

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

4. **A purge is produced the way a build is: a new file, written through
   `SqliteIndexStore` and the purge module it delegates to, and published by a
   pointer swap.** `IndexStore.create` and the purge are both *productions of a
   new build*; publishing is a separate step, and it writes a pointer rather than
   an index. **Nothing outside those two modules opens an index file for
   writing** — which is what makes point 1 keepable, because a writer that cannot
   reach a published file cannot collide with a reader of one.

   > **Corrected on 2026-09-01 against measurement
   > ([#445](https://github.com/theurian/theurian/issues/445)); the work log is
   > `docs/work-logs/2026-09-01-472-purged-build-re-measurement.md`.** This point
   > made three claims. **One holds and two were false when they were written**,
   > and the point above is narrowed to the one that holds. What it said is
   > quoted here rather than deleted, because this ADR's header line and its
   > Compliance section both rested on the pair that did not.
   >
   > It read: *"A purge goes through the same single-writer interface as a build,
   > and there is exactly one such interface. This is ADR-0018 point 1 applied to
   > the index for the first time. `IndexStore.create` and the purge are both
   > productions of a new build; publishing is a separate step that takes the
   > index write lock. Nothing outside that interface opens an index file for
   > writing."*
   >
   > **False: "publishing … takes the index write lock". There is no index write
   > lock for it to take.** Publishing is `write_active_index_pointer` in
   > `application/project_service.py` — a write-to-temp plus `os.replace`, and
   > nothing else. Measured by calling the purge's publish half exactly as
   > `application/withdrawal_purge.py` calls it: the lock file at
   > `<root>/.theurian/runtime/write.lock` does not exist before the publish, does
   > not exist after it, and is never held, while the pointer is written. **The
   > source already said so in the files that do the work** —
   > `withdrawal_purge.py` records "No new index-write lock is taken" and
   > `project_service.py` records "The purge holds no index-write lock" — so this
   > point contradicted the code and ADR-0018's own record, which #436 corrected
   > to *owed*. Statically, at `fe2925c`:
   >
   > ```sh
   > git grep -nE "flock|lockf|LOCK_EX|LOCK_SH|LOCK_NB|write_lock|WriteLock" \
   >   -- packages/theurian-core/src   # -> 33 lines over 5 files
   > ```
   >
   > **Not one of the 33 is an index write path.** Thirty-one are the
   > state-database `WriteLock` family — the class, its timeout error and its
   > `flock` calls in `infrastructure/sqlite/connection.py` (10), the
   > `ProjectPaths.write_lock` property in `application/project_service.py` (1),
   > and the lines threading it through `cli/commands.py` (14) and
   > `cli/migration_pipeline.py` (6) to `write_transaction` — and 2 are
   > `daemon/instance.py`'s single-instance lock. The same key restricted to the
   > five index-writing modules (`index_store.py`, `index_purge.py`,
   > `withdrawal_purge.py`, `index_builder.py`, `cli/index_commands.py`) returns
   > **0**, against 33 over the whole tree, which is the control that says the key
   > can match.
   >
   > **The count is a dated measurement and it moves** — it was 19 at `ec0dbcd`
   > and #478's `migrate apply` serialisation took it to 33 without adding an
   > index lock. **The classification is the claim**, and it is held on `main` by
   > `test_every_lock_in_the_package_belongs_to_one_of_the_two_known_families` in
   > `packages/theurian-core/tests/unit/test_adr_0018_claims.py`: every lock line
   > under the package source must fall in the state-database family or the
   > daemon's single-instance lock, and its failure message says that a lock
   > landing outside them means this point has to be **re-decided against it**
   > rather than corrected as pending.
   >
   > **The pin's reach is narrower than "every lock", and it records its own
   > limit** — cite it with that limit or the citation overclaims. Its key sees
   > two `fcntl` calls, three `fcntl` flags and names built on the existing
   > lock's stem, and nothing else; **`threading` primitives, `asyncio`
   > primitives and SQLite's own `BEGIN IMMEDIATE` idiom are outside it**, and
   > three uncovered examples live in this package today with the sweep green
   > over all of them: `threading.Lock()` at `infrastructure/determinism.py:47`,
   > `threading.BoundedSemaphore` at `mcp/tools.py:505`, and `BEGIN IMMEDIATE`
   > at `infrastructure/sqlite/connection.py:318`. So "no index write lock" is
   > held against the `fcntl` family and the existing lock's naming, which is the
   > shape an index write lock would realistically arrive in — not against every
   > conceivable mutual exclusion. Widening the key means classifying those
   > three, which is separate work; `KNOWN_LOCK_FAMILIES` carries the limit.
   >
   > **False: "there is exactly one such interface". There are eleven writable
   > opens of an index file, across two modules, with no common gate.** An AST
   > walk over `index_store.py` and `index_purge.py` for call sites of the two
   > module-private factories that hand out a *writable* connection (`_connect`,
   > `_writing`), plus the one raw `sqlite3.connect(target)` neither factory
   > covers, at `fe2925c`:
   >
   > | Site | Enclosing function | Visibility |
   > | :-- | :-- | :-- |
   > | `index_store.py:545` | `create` | public |
   > | `index_store.py:559` | `add_chunks` | public |
   > | `index_store.py:626` | `add_nodes` | public |
   > | `index_store.py:685` | `add_node_embeddings` | public |
   > | `index_store.py:835` | `delete_nodes_grounded_in_chunks` | public |
   > | `index_store.py:902` | `add_embeddings` | public |
   > | `index_store.py:917` | `record_embedding_model` | public |
   > | `index_purge.py:526` | `_copy` | private |
   > | `index_purge.py:545` | `_delete` | private |
   > | `index_purge.py:663` | `_restamp` | private |
   > | `index_purge.py:690` | `_verify` | private |
   >
   > Seven public methods on `SqliteIndexStore` open an index file for writing,
   > plus `derive_purged`, which delegates to `index_purge.purge_into`. Nothing
   > serialises them against each other, and nothing serialises either module
   > against the other. "Exactly one interface" is true only if *interface* means
   > "the `IndexStore` port plus the purge module it delegates to" — a **layering**
   > statement, not the single-writer contract ADR-0018 point 1 defines.
   >
   > **Holds: "nothing outside that interface opens an index file for writing",
   > and this is the clause the narrowed point keeps.**
   > `git grep -n "sqlite3.connect(" -- packages/theurian-core/src` returns eleven
   > code lines at `fe2925c`, classified by target: three writable index opens
   > (`index_store.py:265`'s `_connect`, `index_purge.py:394`'s `_writing`,
   > `index_purge.py:526`'s copy writer), two `mode=ro` index opens
   > (`index_store.py:303`, `index_purge.py:525`), three state-database opens
   > (`connection.py:216`, `:237`, `:315`), two findings-store opens
   > (`findings_store.py:203`, `:335`), and one `:memory:` (`index_store.py:246`).
   > Every writable index open is inside `index_store.py` or `index_purge.py`.
   > The one call that looks like a counterexample — `recompute_forest` reaching
   > `delete_nodes_grounded_in_chunks` and `add_nodes` from the *application*
   > layer in `withdrawal_purge.py` — writes to the **building** file `purge_into`
   > hands it, not to the published build, and reaches them through
   > `SqliteIndexStore` either way. Confirmed at runtime by
   > `test_a_purge_leaves_the_published_build_untouched`
   > (`tests/integration/test_index_purge.py`), which holds the published build
   > byte-for-byte across a real `derive_purged`.

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

   Index connections are therefore opened `file:<path>?mode=ro` with `uri=True`
   (`_open_read`). Measured: the default `sqlite3.connect` creates a database at
   a missing path; `mode=ro` raises `unable to open database file` and creates
   nothing. That is what turns "the pointer outlived its file" back into the
   fallback ADR-0022 promised, instead of an empty index that reports itself
   healthy. The path is escaped into the URI rather than interpolated, because a
   filename containing `?` would otherwise be read as a query parameter and
   override the mode.

   **Landed.** The whole read surface goes through `_open_read`, and
   `test_a_read_of_a_missing_index_creates_no_file` pins **seven of the eleven**
   public read methods that raise on a missing file. The pragmas it runs rest on
   index files being WAL, which `test_a_built_index_is_always_in_wal_mode` pins.

   > **Coverage corrected on 2026-09-02 in PR #498's round-one review: this said
   > "over every read method", and it is seven of eleven.** The population is a
   > runnable key rather than a description, because the first statement of it
   > described a procedure and the count moved when a second reader chose a
   > different key:
   >
   > ```sh
   > uv run --frozen python -c "
   > import ast,pathlib
   > s=pathlib.Path('packages/theurian-core/src/theurian/infrastructure/sqlite/index_store.py');src=s.read_text();L=src.splitlines()
   > c=next(n for n in ast.parse(src).body if isinstance(n,ast.ClassDef) and n.name=='SqliteIndexStore')
   > r=sorted(n.name for n in c.body if isinstance(n,ast.FunctionDef) and not n.name.startswith('_') and n.name!='session' and 'self._read' in '\n'.join(L[n.lineno-1:n.end_lineno]))
   > print(len(r),r)"
   > ```
   >
   > **12** at `1a37c86`: `chunk_count`, `chunk_texts`, `holds_any_revision`,
   > `metadata`, `raptor_path`, `schema_version`, `search_dense`,
   > `search_lexical`, `search_substring`, `search_summaries`,
   > `surviving_chunks`, `texts`.
   >
   > **The 11 and the 12 are two keys, not a disagreement, and the relation is
   > one line**: 12 is the public methods that reach `self._read`; **11 is those
   > that *raise*, which is 12 minus `schema_version`**, whose contract is to
   > return `0` on an unreadable build. `session` is excluded from both — it is
   > the context manager the others open through, not a read — and
   > `is_searchable` appears in neither, because it reaches `_read` only by
   > calling `schema_version()` and returns `False` rather than raising. Of the
   > 11, **7 are parametrised** — `chunk_count`, `chunk_texts`, `metadata`,
   > `search_dense`, `search_lexical`, `search_substring`, `texts` — and **4 are
   > not**: `holds_any_revision`, `raptor_path`, `search_summaries`,
   > `surviving_chunks`.
   >
   > **The behaviour underneath is correct, and that is why this is a coverage
   > gap and not a defect.** Each of the four unpinned methods was driven against
   > a missing index path (2026-09-02, this branch): all four raise
   > `IndexUnreadableError` and none creates a file. So the decision holds; what
   > is narrower than the sentence is the *pin*, and a read method added later
   > that forgets `_open_read` is caught only if it is one of the seven.

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

   > **Amended in Milestone 6. The rule holds; the storage it was
   > implemented on does not, and "deletes or recomputes" is now decided.**
   > [ADR-0008](0008-raptor-forest.md), amended the same day, puts summary nodes
   > in their own tables at index schema v4 rather than in `chunks` rows with
   > `derived = 1`: summary text repeats its children's terms, so nodes sharing
   > `chunks_fts` would move `N`, `avgdl` and the per-term document frequencies
   > under every ordinary leaf query, and a visible leaf's rank would become a
   > function of the forest's shape. That ADR also decides what "deletes or
   > recomputes" resolves to for a summary node: withdrawal **re-derives each
   > affected tree** from its surviving rows. Deleting the node leaves the purged
   > index missing a node the never-held corpus would have — the same equality
   > this ADR rests on, broken in the other direction — while a recompute
   > confined to the existing node cannot reproduce a threshold or a clustering
   > decision that the corpus itself determines.
   >
   > **The cost model here is untouched, and is narrower than it may now read.**
   > The 51× to 65× copy-not-derive measurement is about the *chunk* index, and
   > it stands: a purge still copies chunks rather than re-deriving them. What
   > ADR-0008 adds is a re-derivation term for the affected trees only, never for
   > the corpus, unmeasured and owed with the CL that closes the purge over
   > nodes. Nothing in this ADR's tables measured a forest, because there was
   > none to measure.
   >
   > A failed purge over nodes lands in the residual that already exists rather
   > than opening a new one: nothing is published, the stale build keeps serving,
   > and `migrate apply` says so — `indexPurge` carries `published: false`,
   > `failed: true` and a `remedy` naming the rebuild (`cli/commands.py`). That
   > is T-17a's residual 2, unchanged in kind by there being nodes in the build.
   >
   > **Amended by GHSA-97q9-xxfg-33r6.** The paragraph above was wrong on both
   > counts. A failed purge over *nodes* did open a new channel: a `--raptor`
   > build's summary node keeps its build-time text, and a *visible* sibling leaf's
   > `raptorPath[].title` then carries a withheld document's content verbatim — a
   > verbatim disclosure, not the statistical residual T-17a's residual 2 then
   > described. And the stale build no longer "keeps serving": a purge failure now
   > taints the active-index pointer (`mark_active_index_purge_failed`) and the
   > serve path (`mcp.search._published_index`) stands the tainted build aside
   > whole, degrading to the unranked canonical scan until a rebuild. The
   > statistical and the verbatim face close together, and T-17a's residual 2 now
   > records the narrower windows that remain (an in-flight request and a double
   > disk fault). Pinned by `test_purge_failed_build_is_not_served.py`.
   >
   > None of this point's three rules changes; the second one's open choice is
   > simply now made. What changes is where their counterpart has to be written.
   > `chunks.derived` and `chunk_derivation` are **dropped at v4**, because a
   > column nothing will ever write serves nothing. Three places name them and
   > move together: `_DOOMED`, `_verify`'s unprovenanced-row post-condition, and
   > `IndexStore.holds_any_revision` — whose unprovenanced clause is an executed
   > SQL predicate, not a comment, and which `application/withdrawal_purge.py`
   > calls as the pre-check on every withdrawing `migrate apply`. Against a v4
   > index that predicate raises `no such table: chunk_derivation`, so this drop
   > reaches the withdrawal path and not only the purge. The traversal tests
   > listed in Compliance below migrate to the node-table counterpart rather than
   > being deleted — what they hold is this rule, and this rule stands. The
   > third bullet — an unresolvable derivation edge means delete, not keep —
   > survives the switch to re-derivation unchanged: a node that cannot say what
   > it was built from cannot be rebuilt from it either. The amendment to
   > ADR-0008 decision 5 names the owed tests.
   >
   > **Landed at index schema v4.** Everything above this note is the plan as it
   > stood when the amendment was written; what follows is what the schema-v4 CL
   > actually did, so that a later reader does not take the plan for the state.
   > `nodes`, `node_derivation`, `nodes_fts`, `nodes_trigram` and
   > `node_embeddings` are in `index_schema.py`; `chunks.derived` and
   > `chunk_derivation` are gone; and all three predicates named above moved
   > together. The failure this amendment predicted for the third of them is
   > reproduced rather than reasoned about: against a v4 index the v3 clause
   > gives `no such table: chunk_derivation`, and it gives it even where the
   > revision clause alone would have answered.
   >
   > **They did not stay three predicates, and that is the correction to this
   > note.** It said `holds_any_revision`'s clause was "now two `SELECT`s joined
   > by `UNION ALL` because the two halves read different tables" — which is a
   > second hand-written predicate, the arrangement v3 had and the one that let
   > the pair disagree. `holds_any_revision` now runs `index_purge.ANY_DOOMED_ROW`,
   > composed from the same withdrawn-chunk and unanchored-node literals `_DOOMED`
   > is built from, so the pre-check is `_DOOMED` minus an upward closure over an
   > empty seed and the two agree by construction rather than by being kept in
   > step by hand. Ten hand-enumerated graph shapes pin the equivalence
   > (`test_index_purge_nodes.py::test_holds_any_revision_agrees_with_whether_a_purge_removes_anything`),
   > each carrying its own chunk corpus so that no case can agree for the wrong
   > reason through the withdrawn-chunk arm. A first draft shared one corpus
   > across all ten, which made that arm answer `True` whatever the node shape;
   > the mutation that drops the node arm survived it, and mutating the test is
   > what found that.
   >
   > **The disagreement was real, and closing it changed an outcome.** A build
   > whose only damage was a pre-existing dangling edge answered "nothing to
   > purge" on the pre-check, so `migrate apply` skipped it as clean without
   > copying the file, while a purge run directly on that same build refused to
   > publish over the one bad row: the pre-check called clean the very build a
   > purge would not accept. Under the well-founded reading that node is exactly
   > as ungrounded as one with no edges at all — it cannot be shown to hold
   > nothing withdrawn — so it is removed and the build publishes.
   > `test_withdrawal_purge.py::test_a_dangling_edge_is_seen_by_the_pre_check_and_purged`
   > pins the pre-check half and
   > `test_index_purge_nodes.py::test_a_dangling_only_build_is_purged_rather_than_refused`
   > the direct-call half.
   >
   > **Those traversal tests are six, not five — the count this amendment took
   > from Compliance below was that section's, and it was already wrong.**
   > Compliance named five shapes while the suite held a sixth of the same
   > family, `test_a_derived_row_that_cannot_say_where_it_came_from_is_deleted`,
   > which it never listed. All six migrated; the corrected list is in Compliance
   > below. The population is `rg "^def test.*deriv"` over
   > `tests/integration/test_index_purge.py` at v3, which returns **seven** — the
   > six traversal tests and `_verify`'s unprovenanced backstop, which migrated
   > too and belongs to the post-condition family rather than this one.
   >
   > **`_verify` goes from three post-conditions to six, and one of the three it
   > gains this point could not have named, because the v3 storage could not
   > express the state it checks for**: a `node_derivation` edge whose source
   > chunk or source node is gone. One table made a dangling edge and an
   > unprovenanced row the same state; two tables make them different ones, so a
   > node can now hold an edge that points at nothing while still having an edge
   > — which the unprovenanced count does not see. The other two are a node
   > standing on a provenance cycle and an orphaned node embedding; the withdrawn-
   > rows count also widens, from chunks by `revision_id` to those plus nodes
   > carrying a withdrawn `source_revision_id` stamp. The cycle count is computed
   > independently rather than by asking `_DOOMED` a second time, for the reason
   > `_verify` exists at all: a post-condition computed by the function it checks
   > cannot catch that function being wrong, and what publishes a build is a
   > pointer swap with no later stage that looks. Nothing writes a node row yet,
   > so all of this is still pinned over rows inserted with raw SQL.
   >
   > **Amended in Milestone 6, by the extractive-provider CL. Both halves of
   > "Recorded now, with `infrastructure/raptor/` an empty package and
   > `SummarizationProvider` a port with no adapter" are false now.** That
   > package holds `extractive.py`, which implements the port. Nothing calls it —
   > no builder maps a `SummaryNode` onto a row — so this point's three rules and
   > the raw-SQL-fixture state they were recorded against are otherwise
   > unaffected, and the reason the purge was designed before the thing that
   > opens it is unchanged.
   >
   > Written first as "this point's opening sentence is the one that changes",
   > which corrected the adapter half and left the empty-package half standing.
   >
   > **Amended in Milestone 6, by the forest-builder CL. "Nothing writes a node
   > row yet, so all of this is still pinned over rows inserted with raw SQL" is
   > false, and so is "closed before the thing that opens it is written".**
   > `theurian index build --raptor` writes them, and this traversal now meets a
   > graph a builder shaped rather than only fixtures the test that purges them
   > wrote.
   > `tests/integration/test_forest_builder.py::test_withdrawing_an_item_takes_its_document_node_and_the_domain_node_above_it`
   > withdraws one item of three: its Document node is ungrounded and dies, and
   > the Domain node standing on that one dies with it by the upward closure,
   > while the two unaffected Document nodes survive — a purge that took the whole
   > forest would satisfy every assertion about the withdrawn item and destroy
   > the property this ADR was accepted on.
   > `test_a_purged_forest_leaves_no_residue_in_a_node_text_index` reads
   > `nodes_fts` and `nodes_trigram` through `fts5vocab`, which is the check a
   > corpus that never held a node row could not make.
   >
   > **The hand-written fixtures in `test_index_purge_nodes.py` are not made
   > redundant by this, and that is a property of the builder rather than of the
   > tests.** A builder-written forest cannot reach three of the five unanchored
   > arms: it writes every node before any edge in one transaction, and each node
   > carries at least one source, so an unprovenanced node and an edge naming an
   > absent node cannot occur; and it builds each tier only from the one below,
   > so a provenance cycle cannot occur either. Those arms stay covered only by
   > raw SQL, deliberately — they describe states a *migration or a partial build*
   > leaves, not states this builder produces.
   > ADR-0008's family-closure note records that under-correction and its cause.
   >
   > **Amended in Milestone 6, by the purge-recompute CL. "Deletes or recomputes"
   > is now *re-derives*, and the two-corpus equality this ADR rests on holds for
   > the derived layer too.** Until this CL a purge over nodes was delete-only:
   > it removed every node the surviving corpus could no longer ground and stopped,
   > which left the purged index missing a node a never-held corpus would have
   > built from the survivors — this ADR's own equality, broken in the other
   > direction. The purge now re-derives each *scope that lost a row* whole — every
   > tree in it, over the surviving rows it reads back from the building file,
   > coarser than decision 9's per-tree ancestor closure and subsuming it since the
   > unaffected trees re-derive byte-for-byte — after the delete and before
   > `_verify` and the swap,
   > so an ungrounded re-derived node is refused by the same post-conditions a bad
   > delete is. A purged forest then equals one built over a corpus that never held
   > the withdrawn rows — node rows, derivation edges and node vectors alike, held
   > by
   > `tests/integration/test_forest_purge_equality.py::test_a_purged_forest_equals_one_that_never_held_the_withdrawn_rows`
   > with a stale control asserted different. This is the derived-layer counterpart
   > of the chunk equality in the Compliance section's first bullet, and it is
   > scoped to deterministic pure providers (the extractive default); a
   > non-deterministic provider's delete-and-mark-stale fallback is recorded in
   > `make_forest_recompute`'s docstring and built by nothing.
   >
   > **The re-derivation is application-layer policy injected into this
   > infrastructure purge, so ADR-0003's layering holds.** `index_purge`
   > (infrastructure) may not name the `ForestBuilder`, summariser and embedder the
   > recompute needs, so `purge_into` takes an optional `recompute_forest` callback
   > and calls it; the composition root builds that callback
   > (`make_forest_recompute` in `application/withdrawal_purge.py`) closing over
   > those collaborators. A passed-down callable, not an import up —
   > `test_layering` still passes.
   >
   > **Cost, still owed.** The re-derivation term this ADR's decision-8 note left
   > unmeasured — the derived layer of the affected scopes, never the corpus — is
   > now code that runs and is still unmeasured; the 51×–65× copy-not-derive figures
   > are the *chunk* index and are untouched. Index schema v5 adds `chunks.kind`,
   > which the re-derivation reads to key a Domain tree; the schema-mismatch rebuild
   > (ADR-0022 point 3) is the whole migration, as at v4.
   >
   > **Amended in Milestone 6, by the fan-out re-batch fix. The scope-clearing
   > delete this note describes was, until this fix, keyed on the fresh trees
   > rather than on the scope, and a re-batched Domain fan-out (ADR-0008 decision
   > 2's amendment) reached that gap.** A withdrawal that collapses a fan-out's
   > batch count leaves a *surviving* top batch none of whose members was
   > withdrawn, so the universal-grounding delete never dooms it, while the fresh
   > derivation mints one fewer batch and never names that batch's `tree_id`.
   > Deleting by the fresh tree ids missed it; the cascade then stripped its edges
   > when the survivors' Document nodes were re-derived, and `_verify` refused the
   > whole purge over the unprovenanced remnant. A legitimate withdrawal thus
   > published no purge at all — not a doubled forest, but the residual this ADR
   > already names above: nothing published, the stale build keeps serving, and
   > `migrate apply` reports the failure. All three reviewers reproduced it.
   >
   > `SqliteIndexStore.delete_nodes_of_trees` is now
   > `delete_nodes_grounded_in_chunks`, seeded on the scope's *surviving chunks*
   > rather than the fresh trees and walking `node_derivation` upward, so it
   > deletes the scope's entire current node set — stale re-batched batches
   > included — rather than only the trees the fresh derivation happens to
   > reproduce. The equality this ADR rests on now holds at the fan-out boundary
   > too, for deterministic pure providers:
   > `tests/integration/test_forest_purge_recompute.py` asserts a re-batching
   > withdrawal, at the exact boundary and as a bulk withdrawal, publishes a
   > forest identical to a never-held build, with the orphaned batch gone.

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

  Measured on a 400-document index, searches during a publish that reaps. The
  procedure columns are part of the result, not bookkeeping — every disagreement
  between two runs of this experiment has come from one of them:

  | Configuration | Handle scope | Calls / request | Loop | ok | errors |
  | :-- | :-- | --: | --: | --: | --: |
  | neither — no retention (ships today) | per call | 3 | 1.5 s | 40 | 2,627 |
  | point 7 only | **request** | 3 | 1.5 s | 331 | 86,496 |
  | point 7 only, independently | **request** | not recorded | not recorded | 244 | 3,400 |
  | point 7 only | **process** | 3 | 1.5 s | 3,420 | 0 |
  | point 6 only | per call | 3 | 1.5 s | 180 | 0 |
  | both | request | 3 | 1.5 s | 1,163 | 0 |

  Rows two and three are the same configuration measured twice by different
  people, and they disagree by more than an order of magnitude in the error
  column. **The sign is what this decision relies on, and it is the same in both;
  the magnitudes are properties of the harnesses.** Both are recorded so that
  neither gets re-derived later and read as a refutation of the other — which is
  the whole hazard, since a reader who reproduces one number and finds the other
  in the history has no way to tell a disagreement from a defect.

  The first row and point 6's "1,889 errors against 163 successful searches" are
  the same failure under the same treatment: one index call per iteration there,
  three per request here. What each row asserts is whether the error column is
  zero, never throughput.

  **The window point 7 is actually for** is a narrower experiment: one request of
  four index calls with the reap landing after the first. **1 of 4 answered**
  with a connection per call, leaving an empty database recreated at the reaped
  path, against **4 of 4** with one held connection and no file recreated.

  **The request-scoped and process-scoped rows are the same design measured two
  ways, and only the request-scoped one is point 7.** This decision says a search holds one connection *for
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

  > **Amended in Milestone 6. It is billed twice, not once.** v3 bought
  > `chunks.derived` and `chunk_derivation` for a writer that will now never
  > exist — [ADR-0008](0008-raptor-forest.md) puts summary nodes in their own
  > tables — and v4 pays a second bump to add those tables and drop these
  > columns. The affordability argument is unchanged and is what makes this
  > recoverable rather than costly; what is worth recording is that "land the
  > schema ahead of the feature" bought a rehearsal of the *rule* and none of
  > the storage, and the rule is the part that survived.

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

**This ADR preceded its implementation, and most of it has now landed.** The
Compliance section was written before any code, deliberately — the decision is
cheaper to get wrong on paper — and it listed what must go RED when each decision
is violated rather than tests that did not exist. Those tests now exist, across
[#113](https://github.com/theurian/theurian/pull/113); each item below names the
one that holds it.

**Decision 5 — the automatic withdrawal→purge trigger — is now wired**
([#15](https://github.com/theurian/theurian/issues/15)). It was unwired when the
mechanism landed under #113: `IndexStore.derive_purged` had no caller outside
tests, so nothing fired a purge when a revision was retired, superseded or
rejected. It now has one. `theurian migrate apply` (`cli/commands.py`
`migrate_apply`) calls `publish_purge_for_withdrawal`
(`application/withdrawal_purge.py`) synchronously after the write transaction
commits and releases the lock, so a retirement, a supersession, a rejection or an
in-place status change publishes a purged build in the same command that applied
it — no separate `index build`. The revisions removed are computed against the
published index's *own* build flavor: `revisions_to_purge` reads
`indexesUnapproved` off the pointer, so a default index purges what `may_surface`
withholds from it (draft, proposed, deprecated, rejected, superseded, and any
non-current revision) while an `--include-unapproved` index keeps the drafts and
proposals it legitimately holds and purges only what is withheld under every flag
plus non-current revisions. The closure is held by
`test_a_withdrawal_purges_the_published_index_without_a_separate_build`
(`tests/integration/test_absence_proof.py`), parametrised over the four faces —
`deprecate`, `supersede`, `reject`, and an in-place `draft` (the flavor face) —
each of which is RED on the pre-trigger wiring. With this, every decision in this
ADR is wired.

**A reclassification triggers no purge, and needs none.** A `changeSensitivity`
moves a scope component (SEC-14, [ADR-0008](0008-raptor-forest.md) decision 1),
but `migration_engine._withdrawal_affected_item` — the set that feeds
`publish_purge_for_withdrawal` — deliberately excludes it. A purge copies the
published build and deletes withheld rows; it deletes rows, it does not rewrite a
scope column, and a pure reclassification withholds nothing (its status and
current revision are unchanged), so the purge would gather the item only to
discard it. Nothing rebuilds for it, and nothing has to: the live response is
already correct, because a result reads the item's current sensitivity
(`mcp/results.py`), and the built index's stale `sensitivity` column is read by no
gate before [#119](https://github.com/theurian/theurian/issues/119) — an unsigned
local index row nothing reads is not a disclosure (SEC-7). That column matches
canonical again on the next `index build`, which re-derives at the item's current
label. `test_a_reclassification_is_not_a_withdrawal` pins that the engine produces
no purge candidate for it, and
`test_a_reclassification_shows_in_the_response_before_any_rebuild` pins the live
response and the harmless index lag end to end.

> **Amended in #119 phase 5 (2026-08-24). The paragraph above is reversed, and
> the ground it stood on is the reason.** "A reclassification triggers no purge,
> and needs none" rested on the clause it states itself: the built index's stale
> `sensitivity` column "is read by no gate before #119". Phase 3 made a build
> write no chunk row above the deployment's declared ceiling and phase 4 made
> every retriever emit `sensitivity IN (…)`, which turned that column into a
> gate's column and inverted the exclusion into a defect — a reclassified row is
> then the *only* above-ceiling row a served build can hold, withheld from results
> by the canonical re-check while its text stays in `chunks_fts`,
> `chunks_trigram`, `nodes_fts` and `nodes_trigram`, whose collection statistics
> price every visible row against it (T-17a on this axis).
>
> So `_withdrawal_affected_item` admits `changeSensitivity` today, extending this
> ADR's decision 5 trigger set, and `revisions_to_purge` reduces the candidate set
> against a *second* flavor axis read off the published pointer —
> `indexedSensitivities` beside `indexesUnapproved`. The reasoning in the
> paragraph above survives the reversal in one narrow form and is worth keeping
> for it: a reclassification that stays **within** the ceiling the published build
> ran under still purges nothing and still copies no file, which is what lets the
> operation join the candidate set unconditionally rather than the engine needing
> to know a ceiling it cannot see.
>
> `test_a_reclassification_is_not_a_withdrawal` is gone with the decision it
> pinned; `test_migration_engine.py::test_a_reclassification_is_a_withdrawal_only_past_the_builds_own_ceiling`
> and its sibling `test_a_reclassification_within_the_ceiling_purges_nothing` are
> what stand there now, with `tests/integration/test_sensitivity_purge.py` driving
> it through the real CLI. The one direction this cannot close — a reclassification
> back *down* into the ceiling, which has no row to restore and waits for the next
> `index build` — is recorded in
> [ADR-0025](0025-sensitivity-is-enforced-before-0-1-0-stable.md)'s compliance
> section rather than here.

Everything below is the mechanism's own acceptance, which #113 discharges;
[#103](https://github.com/theurian/theurian/issues/103) tracked these eight as
one class, and all eight are green.

Landed by the change that implements this ADR:

- **The equality, as one query against two corpora** — landed:
  `test_a_purged_build_answers_as_if_the_rows_were_never_indexed`, three queries
  with a `stale`-index control asserted different in the same test, and the
  withdrawn documents ten times the corpus mean so `avgdl` moves. An index built
  from a corpus including the withdrawn documents and then purged returns
  byte-identical rankings — chunk ids and scores — to one that never held them,
  for both `search_lexical` and `search_substring`. **The derived layer holds the
  same equality as of the purge-recompute CL** (Milestone 6):
  `tests/integration/test_forest_purge_equality.py::test_a_purged_forest_equals_one_that_never_held_the_withdrawn_rows`
  extends it to the forest — node rows, derivation edges and node vectors — for
  deterministic pure providers, per decision 8's amendment above. That amendment's
  fan-out re-batch follow-up closes the one boundary the equality did not reach at
  first: `tests/integration/test_forest_purge_recompute.py` pins the same equality
  where a withdrawal re-batches a fanned-out Domain tier, unqualified for
  deterministic providers.
- **A search does not tear on a `gc` unlink between its reads** — landed:
  `test_a_gc_unlink_between_a_requests_reads_does_not_tear_it` drives the real
  MCP path and forces the unlink between two of the request's index reads;
  `hybrid_answer` holds one connection for the request (`SqliteIndexStore.session`)
  so the held descriptor keeps the build readable. Mutating the session to a
  no-op makes the request fall back.
- **Publishing does not delete** — landed:
  `test_publishing_a_build_no_longer_reclaims_the_one_it_replaced` and the
  end-to-end CLI run; two builds in a row leave two files.
- **A search survives `gc`** — landed:
  `test_a_request_inside_a_session_finishes_against_the_build_it_started_on`
  answers 4 of 4 reads after the unlink against 1 of 4 without, and the reaped
  path stays reaped.
- **No read of an index path uses a bare `sqlite3.connect`** — landed:
  `test_a_read_of_a_missing_index_creates_no_file`, parametrised over the whole
  read surface, asserts each read of a missing file raises rather than creating
  one. A read method added later that forgets `_open_read` is a missing entry in
  that list.
- **Derived nodes** — landed, and at index schema v4 the traversal runs over the
  node tables. **Six tests, not the five this bullet listed until then**: direct
  (`test_a_node_derived_from_a_withdrawn_chunk_goes_with_it`), a Domain node
  built from a Document node
  (`test_withdrawal_is_transitive_through_a_document_and_a_domain_node`), mixed
  provenance
  (`test_a_node_derived_from_both_a_withdrawn_and_a_surviving_chunk_goes`), a
  node that cannot say where it came from
  (`test_a_node_that_cannot_say_where_it_came_from_is_deleted`), the fixed point
  under an unprovenanced node
  (`test_a_node_derived_only_from_an_unprovenanced_node_goes_with_it`), and a
  control that an ordinary chunk is never swept in
  (`test_an_ordinary_chunk_is_never_treated_as_an_unprovenanced_node`). The
  fourth was in the suite from the start and was never listed here, so the count
  is corrected rather than the test added; the amendment to decision 8 records
  the population that settles it at six.

  **The storage under them changed and the rule did not.** They were `chunks`
  rows with `derived = 1`, provenanced by `chunk_derivation`, until the Milestone
  6 amendment to [ADR-0008](0008-raptor-forest.md) decision 5 gave summary nodes
  their own tables; each migrated rather than being deleted, and each names its
  v3 predecessor in its own docstring. Two `_verify` post-condition tests sit
  beside them: `test_verify_refuses_a_build_that_still_holds_an_unprovenanced_node`,
  which migrated with the rest, and
  `test_verify_refuses_a_build_whose_node_derivation_points_at_a_chunk_that_is_gone`,
  which is new at v4 because one table could not express a dangling edge as a
  state distinct from having no edge at all.

  **The rationale this bullet gave — "nothing writes `derived = 1` yet (RAPTOR
  is empty), which is exactly why the traversal is pinned now" — keeps its force
  and loses its subject.** There is no `derived` column to write. What nothing
  writes is a *node row*: `infrastructure/raptor/` is an empty package and
  `SummarizationProvider` a port with no adapter, so every fixture above goes in
  with raw SQL. That is still exactly why the traversal is pinned now, and it
  stays pinned that way until the builder CL (Milestone 6).

  **Amended in Milestone 6, by the extractive-provider CL: both halves of
  "`infrastructure/raptor/` is an empty package and `SummarizationProvider` a
  port with no adapter" are false now.** That package holds `extractive.py`,
  the port's first adapter. Nothing calls it — no builder maps a `SummaryNode`
  onto a row — so "every fixture above goes in with raw SQL" still holds, for
  the reason the paragraph above already gives: nothing writes a node row.

  Written first as "the second half of that sentence is false now too", which
  read "the corrected first half" as this sentence's when it was the preceding
  paragraph's — the subject correction from `derived = 1` to a node row. The
  empty-package half was left standing by that reading; ADR-0008's
  family-closure note records the class it belongs to.

  **Amended in Milestone 6, by the forest-builder CL. "Nothing writes a node row"
  is false and "it stays pinned that way until the builder CL" has arrived.**
  `index build --raptor` writes them. The raw-SQL fixtures above stay, and not
  out of inertia: they reach three unanchored arms a builder-written forest
  cannot produce — an unprovenanced node, an edge naming an absent node, and a
  provenance cycle — because the builder writes every node before any edge in one
  transaction, gives each node at least one source, and builds each tier only
  from the one below. What the builder adds is the arms it *can* reach, over a
  graph it shaped:
  `tests/integration/test_forest_builder.py::test_withdrawing_an_item_takes_its_document_node_and_the_domain_node_above_it`
  and `test_a_purged_forest_leaves_no_residue_in_a_node_text_index`.
- **A purged build holds no orphaned row** — landed:
  `test_a_purged_build_holds_no_embedding_of_a_withdrawn_chunk`, plus a third
  post-condition inside `_verify` that refuses to publish a build with an
  orphaned embedding. `ON DELETE CASCADE` is enforced per *connection* and
  `PRAGMA foreign_keys` defaults off, so a purge opening its own connection
  without `CONNECTION_PRAGMAS` would delete the chunk and keep the vector — a
  silent, one-directional failure a review does not catch.
- **A purged build's `index_metadata` names itself** — landed:
  `test_a_purged_build_names_itself_in_its_own_metadata`, which also asserts the
  source build is not restamped. `Connection.backup` copies pages, so without the
  restamp the copy would carry the parent's id.

Still owed to ADR-0018, and **not** satisfied by point 4: its "the derived index
has no single-writer contract at all" needs one interface owning every index
write, and there are eleven writable opens across two modules with no common
gate. Its `CanonicalStore.transaction()` half is not touched here either.

> **Corrected on 2026-09-01
> ([#445](https://github.com/theurian/theurian/issues/445)).** This paragraph
> said the ADR-0018 debt is *"satisfied by point 4 rather than by this ADR's own
> tests: … discharged for the index when one interface owns every index write and
> a test asserts that surface."* The condition it names is the right one and it
> is **not met** — decision 4's dated correction carries the measurement and the
> eleven-site table. What point 4 does establish is narrower: **nothing outside
> `index_store.py` and `index_purge.py` opens an index file for writing** — a
> layering fact, measured, and the only one of its three clauses that held.
>
> **The property worth keeping is not point 4's, and attributing it there was
> this correction's own error, caught in review.** "A published build is never
> written, so a second writer has no live file to reach" is **decisions 1 and 2
> plus the naming discipline**: decision 1 states the rule, decision 2 makes a
> purge a copy-and-publish rather than an in-place edit, and every production
> writes a new file under a fresh ULID and a `.building` suffix, published by
> `os.replace`. Three tests refuse the alternative —
> `test_building_over_an_existing_file_is_refused`,
> `test_a_purge_into_an_existing_path_is_refused` and
> `test_a_purge_refuses_to_write_over_another_writers_building_file` — and
> `test_a_purge_leaves_the_published_build_untouched` holds the published build
> byte-for-byte across a real `derive_purged`. That is a property of *when*
> writes happen, not of *how many interfaces* perform them, and the single-writer
> contract is owed and unscheduled under
> [#439](https://github.com/theurian/theurian/issues/439).

NFR-4 is discharged by points 6 and 7 together, and by neither alone. It is not
discharged by this ADR being accepted.

> **Reconciled on 2026-09-01 across every record that states it
> ([#140](https://github.com/theurian/theurian/issues/140) member 1). Six records
> state NFR-4's discharge status; the sentence above is the one that stands, and
> the other five disagreed with it and are corrected.** The population is a key,
> not a list — every file carrying the dated `#140 member 1` correction, read
> with blockquote markers stripped and whitespace collapsed: this ADR,
> `docs/adr/0018-single-writer-synchronous-in-m1.md`,
> `docs/adr/0022-index-lives-in-its-own-database.md`,
> `docs/adr/0007-state-hash-partitioned-databases.md`,
> `packages/theurian-core/src/theurian/indexing/__init__.py` and
> `packages/theurian-core/src/theurian/infrastructure/sqlite/store.py`. **Six and
> five is the pair to quote**; earlier drafts of this reconciliation said "three
> other records", "all four records" and "four different ways", none of which
> agreed with each other or with the corrected set. ADR-0018's Compliance
> section, ADR-0022's Still-owed opener and
> `packages/theurian-core/src/theurian/indexing/__init__.py`'s docstring each
> recorded NFR-4 as undischarged and owed to "Milestone 6's blue/green work" —
> which is the work this ADR *is*, and which has landed. Each now carries a dated
> correction pointing here.
>
> **The evidence is the acceptance pins, read rather than re-run.** NFR-4 is "the
> previously published index answers every query while a new build runs, zero
> read downtime", and its two clauses are covered differently:
>
> - **Zero read downtime — discharged, and this is the clause NFR-4 was recorded
>   unmet for.** The failure was reaping at publish: 1,889 errors against 163
>   successful searches in 1.5 seconds, with an empty database left at the reaped
>   path. Point 6 abolishes that window and
>   `test_publishing_a_build_no_longer_reclaims_the_one_it_replaced`
>   (`tests/integration/test_index_gc_cli.py`) holds it through the real CLI —
>   two builds in a row leave two files. Retention makes reclaiming necessary, so
>   point 7 closes the window that creates:
>   `tests/integration/test_gc_during_a_search.py` is decision 7's own acceptance
>   module and its four tests cover the whole shape — a request in a session
>   finishes against the build it started on (4 of 4 reads after a forced
>   unlink), a read of a reaped build never recreates it, a request starting
>   after the reap reads the published build, and the no-session case is pinned
>   as the counterexample at 1 of 4. `test_a_read_of_a_missing_index_creates_no_file`
>   (`tests/integration/test_index_store.py`) holds the `mode=ro` half over every
>   read method, which is what keeps a reaped path from becoming an empty
>   database that reports itself healthy.
> - **"While a new build runs" — true by construction, with every element pinned,
>   and *no test issues a query while a build is running*.** A build writes to
>   `<final>.building`, a name `theurian index gc` does not reap
>   (`test_a_build_is_written_under_a_name_gc_will_not_reclaim`), renames it with
>   `os.replace`, and publishes by an atomic pointer swap
>   (`write_active_index_pointer`: write-to-temp plus `os.replace`); a build over
>   an existing index file is refused outright
>   (`test_building_over_an_existing_file_is_refused`,
>   `tests/integration/test_index_store.py`); and the previously published file is
>   retained. So a query during a build resolves the pointer to a file the build
>   never opens. **That composite is argued from pinned elements, not measured**:
>   there is no concurrency test in the suite that runs a search against a build
>   in progress.
>
> **So the mechanism is discharged and one acceptance test is still owed**, and
> the distinction is the point of this note. ADR-0007's own Still-owed bullet
> states that residue exactly — "Nothing asserts a query during an in-progress
> build sees the previous complete state" — and it remains accurate; what has
> changed underneath it is that the two records it cites as agreeing with it
> (ADR-0018's and ADR-0022's) no longer do. It is annotated there rather than
> restated here. **The owed test is owned by
> [#497](https://github.com/theurian/theurian/issues/497)**, whose definition of
> done requires every record stating this gap to move in the same pull request
> the test lands in, because each becomes false the moment it exists. That
> population is measured rather than listed — a wrap-aware, blockquote-aware
> search of `while a build is|during a build|during an in-progress build`.
> **Scoped to the files this record-update branch touches it is seven**:
> ADR-0007's bullet, ADR-0018's Compliance bullet, ADR-0022's Still-owed opener,
> this note, `indexing/__init__.py`'s docstring,
> `infrastructure/sqlite/store.py`'s module docstring, and the CHANGELOG entry.
> **Run repo-wide the same key returns eight**, and the eighth is
> `.theurian/knowledge/architecture/state-hash-partitioned-databases.01M0D5GWD03YD4TFJV2E0SHAVW.md`
> — ADR-0007's **dogfood-corpus twin**, which is served content rather than a
> record and is re-seeded from its ADR rather than edited in place. It moves when
> the corpus is re-seeded, and that is the M7 dogfooding lane's, not this
> reconciliation's; the drift checker that would catch it is
> [#317](https://github.com/theurian/theurian/issues/317). State the scope with
> the number or the two disagree: seven is the record population, eight is the
> repository.
>
> A line-oriented `git grep` under-counts either figure, because three of the
> seven wrap the phrase across a soft line break and one sits inside a nested
> blockquote — which is why the key above is run over text with the `>` markers
> stripped and the whitespace collapsed.
