# ADR-0022: The retrieval index lives in its own database file

- Status: accepted
- Date: 2026-08-03
- Deciders: Theurian maintainers
- Requirements: FR-R2, ADR-0004, ADR-0017, §11 layer rule 3

## Context

Milestone 5 adds chunks, an FTS5 index, and embeddings. The path of least
resistance is to add those tables to the canonical store: one file, one
connection, one migration.

That conflicts with two decisions already made.

ADR-0017 makes the canonical `SCHEMA_VERSION` an **input to the state hash**, and
the state hash names the database file. So adding an index table to the canonical
schema bumps the version, changes every state hash, and invalidates every
existing canonical database — a rebuild of the layer that is authoritative, in
order to accommodate the layer that is disposable.

§11 layer rule 3 says the Index Layer is disposable by definition and deleting it
must never lose information. Sharing a file with the canonical store makes
"delete the index" indistinguishable from "delete the knowledge".

There is also a lifecycle mismatch. An index is rebuilt for reasons that have
nothing to do with canonical change: re-embedding with a different model, a new
chunking strategy, a corrupted FTS table. Conversely a canonical change does not
always need a full re-index.

## Decision

**A separate SQLite file per index build, named for the build.**

1. Index builds live at `.theurian/state/theurian-index-<indexBuildId>.sqlite`,
   beside — but distinct from — `theurian-state-<stateHash>.sqlite`.
2. The prefix is load-bearing. Both live in one directory, and a glob that could
   not tell them apart would hand a retrieval index to the canonical store. This
   is not hypothetical: the first version of this milestone's own test helper
   globbed `*.sqlite` and did exactly that.
3. `INDEX_SCHEMA_VERSION` is independent of the canonical `SCHEMA_VERSION`. They
   version separately because they are rebuilt separately.
4. Each index records the `state_hash` it was built from, so staleness is
   detectable rather than assumed.
5. `.theurian/state/active-index.json` points at the current build, written
   temp-then-`os.replace` so a reader never observes a half-written pointer (the
   same reasoning as ADR-0007).
6. The previous build is not deleted when a new one is published. A search
   already reading it keeps a consistent view.

   > **Amended in Milestone 5. This point is withdrawn, not delivered.**
   > `theurian index build` publishes the new pointer and then reaps every build
   > the pointer does not name, so the previous file is gone by the time the
   > command returns. Verified by running two builds in a row against a real
   > project: the first build's file is absent afterwards.
   >
   > The reason is that the guarantee this point promised was never real.
   > `SqliteIndexStore` holds no connection between calls — it opens and closes
   > per query, and one search opens several — so "a search already reading it"
   > describes no actual reader, and every gap between those connections is a
   > window in which the file can vanish anyway. Worse, `sqlite3.connect` on a
   > deleted path *creates an empty database there*, which defeats the "no index
   > file, fall back to substring scan" branch and surfaces a raw `no such
   > table` to the agent. Keeping the old file made that window larger, not
   > smaller.
   >
   > Reaping eagerly also fixes a second problem: two concurrent builds would
   > each have deleted the other's file. Reaping reads the pointer and keeps
   > what it names, rather than what the running process happens to have built.
   >
   > A search that genuinely survives a rebuild needs a reader that holds its
   > file open for the duration, which is the blue/green index work in Milestone
   > 6. Until then, a search racing a rebuild falls back to the substring scan
   > rather than answering from a half-visible index.

## Consequences

### Positive

- Deleting the index is a cache miss, never data loss.
- An index schema change costs an index rebuild and nothing else.
- Publishing a build is a pointer swap, which is what blue/green index builds in
  Milestone 6 need — the rebuild happens in a file nobody is reading.
- Retrieval and canonical writes do not contend on one file's write lock.

### Negative

- Two files to keep in step, and a way for them to disagree. Mitigated by
  recording the state hash in the index and reporting three hashes from
  `theurian index status` — what the knowledge is, what the database holds, and
  what the index was built from.
- Old builds accumulate until replaced. Reaping them belongs with the blue/green
  work in Milestone 6.

  > **Amended in Milestone 5.** Reaping landed here instead, for the reason in
  > the amendment to point 6 above: keeping old builds turned out to be a
  > hazard rather than a courtesy. Each build deletes every index file the
  > published pointer does not name, so a project holds exactly one.

### Neutral

- A cross-project search (FR-R8) will need to open several index files. That is
  a fan-out either way, since projects have separate canonical stores already.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| Index tables in the canonical database | Bumps a `SCHEMA_VERSION` that feeds the state hash, so every index change invalidates every canonical state (ADR-0017). |
| `ATTACH` the index to the canonical connection | Couples their lifetimes and write locks again, and gains nothing over opening the file directly. |
| An external index (Tantivy, Lucene, a vector database) | A runtime dependency and a second storage engine for a local single-user tool; also the lock-in ADR-0009 rejects. |
| One index file per project *and* per state hash | Makes re-embedding with a new model impossible without a canonical change, which is exactly the lifecycle mismatch this ADR exists to fix. |

## Compliance

Landed in Milestone 5:

- `tests/integration/test_index_store.py::test_the_index_lives_apart_from_the_canonical_store`
- `tests/integration/test_retrieval_service.py::test_the_index_is_a_separate_file_from_the_canonical_store`
  — asserts the canonical database is untouched by an index build.
- `test_the_index_and_the_canonical_store_are_distinguishable_by_name` — the
  mistake this milestone's own helper made first.
- `test_building_over_an_existing_file_is_refused` — an index build is
  all-or-nothing; appending to a half-built one produces a file that looks
  complete and is not.
- `test_changing_knowledge_makes_the_index_stale` and
  `test_all_three_hashes_are_reported` — staleness is detected rather than
  assumed.
- `tests/integration/test_mcp_tools.py::test_a_pointer_to_a_missing_index_falls_back_instead_of_failing`
  — a pointer that outlived its file is a missing optimisation, not a refusal to
  answer.

Point 3 was exercised within the same milestone that made it. Adding the trigram
table ([ADR-0023](0023-trigram-index-beside-the-word-index.md)) took
`INDEX_SCHEMA_VERSION` from 1 to 2 and cost an index rebuild and nothing else:
no canonical `SCHEMA_VERSION` bump, no state hash change, no canonical database
invalidated. That is the whole argument of this ADR, run once for real.

`INDEX_SCHEMA_VERSION` is read on open, not only written. This section used to
say the version was "written and never read", which was true when this ADR
was first drafted. It stopped being true later in the same milestone, when
`SqliteIndexStore.is_searchable` was added to compare the stored version
against `INDEX_SCHEMA_VERSION` before any query runs — an index built under an
older schema is now reported and falls back
(`fallbackReason: index-schema-mismatch`) rather than being used and silently
answering short — and this section was not updated to match until now.
`tests/integration/test_index_fallback.py::test_a_fallback_names_the_reason_it_could_not_use_the_index[written-by-another-schema]`,
`test_a_broken_index_is_never_reported_as_a_healthy_one[written-by-another-schema]`,
`test_index_status_reports_the_schema_it_found_and_the_one_it_wants`, and
`test_a_schema_mismatch_is_stale_even_when_the_state_hash_matches`. The
parameter id names the scenario; `index-schema-mismatch` is the reason code it
asserts.

Still owed, with the milestone that will satisfy it:

- **A search concurrent with a rebuild is not protected** (Milestone 6). See the
  amendment to point 6: the guarantee that ADR gave has not been replaced, only
  withdrawn. It belongs with the blue/green work. This is also NFR-4, which
  ADR-0018's Neutral consequence cited as satisfied by WAL — it is not, because
  WAL spans one database file and this rebuild replaces one.
- **Something other than a build will write to an index** (answered below; the
  writer discipline owed,
  [#439](https://github.com/theurian/theurian/issues/439)). This ADR's model is
  that publishing is a pointer swap and "the rebuild happens in a file nobody is
  reading" — which assumes the only writer is a build, producing a fresh file.
  T-17a's root fix breaks that assumption: withdrawn rows have to leave the
  index, the shape chosen is a **single-writer incremental purge rather than a
  purge on read**, and tombstones do not substitute for it, because what leaks
  is FTS5's collection statistics and those count rows a tombstone leaves in
  place.

  So blue/green has to answer a question this ADR has not been asked yet:
  whether a purge produces a new build and swaps the pointer — which makes it an
  ordinary build under points 5 and 6, at the cost of rewriting the whole file
  to remove a few rows — or mutates the published build in place, which is a
  write to the file searches are reading and needs the writer discipline
  ADR-0018 owes for the index. Recorded here rather than in #15 alone, because
  it is a constraint on the blue/green design and not a detail of the purge.

  > **Answered by [ADR-0024](0024-a-purge-is-a-build.md): a new build and a
  > pointer swap.** The phrase "at the cost of rewriting the whole file" is the
  > part that was wrong, and it is the only reason the in-place option looked
  > attractive. It conflates *re-deriving* a build — read the canonical store,
  > chunk, embed, write — with *copying* one and deleting rows from the copy,
  > which re-derives nothing. Measured: 51 ms against 2,614 ms on a 12.3 MB
  > index, 579 ms against 37,684 ms on a 150.3 MB one — about a sixtieth of "an
  > ordinary build", flat across a 12× corpus range.
  >
  > ADR-0024 also replaces what the Milestone 5 amendment to point 6 withdrew:
  > publishing stops reaping, reclaiming becomes `theurian index gc`, and a
  > search holds one connection to its build for the duration of a request. The
  > two together are what NFR-4 needs, and neither alone is enough — measured at
  > 1,889 errors against 163 successful searches when the old build is reaped
  > under a reader.

  > **Repointed on 2026-09-01
  > ([#464](https://github.com/theurian/theurian/issues/464)): this bullet's
  > heading named "(Milestone 6,
  > [#15](https://github.com/theurian/theurian/issues/15))", and neither can
  > carry it.** Milestone 6 has passed. #15 closed on 2026-08-10 (`66a43ae`) by
  > wiring ADR-0024 decision 5 — the withdrawal→purge trigger — which is the
  > *answer* recorded above and not the writer discipline the paragraph before it
  > asks for. The prediction resolved in the narrower form ADR-0024 chose:
  > nothing writes to a published index, and what changed is that `index build`
  > is no longer the only thing that *produces* one — `migrate apply` publishes a
  > purged build through `application/withdrawal_purge.py`, so "the rebuild
  > happens in a file nobody is reading" still holds. What does not exist is the
  > interface that second producer writes through. Measured at `ec0dbcd`:
  > `git grep -nE "flock|lockf|LOCK_EX|write_lock" -- packages/theurian-core/src`
  > returns ten lines, every one of them the canonical `ProjectPaths.write_lock`
  > or the daemon's single-instance lock and none in an index write path, and the
  > purge says so in its own source ("No new index-write lock is taken"). Owed
  > and unscheduled, tracked in
  > [#439](https://github.com/theurian/theurian/issues/439) — where ADR-0018's
  > matching bullet was repointed on 2026-08-31
  > ([#436](https://github.com/theurian/theurian/issues/436)).
