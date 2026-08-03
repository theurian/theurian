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

Still owed, with the milestone that will satisfy it:

- **`INDEX_SCHEMA_VERSION` is written and never read** (Milestone 6). Point 3
  gives the index its own version so it can be rebuilt on its own schedule, but
  nothing compares the stored value against the running one, so an index built
  under an older schema is used rather than reported. Measured for the 1 → 2
  case: the missing table surfaces as `sqlite3.OperationalError`, which
  `search_substring` catches and answers with no results. Belongs with the
  blue/green index work that already owns index lifecycle.
- **A search concurrent with a rebuild is not protected** (Milestone 6). See the
  amendment to point 6: the guarantee that ADR gave has not been replaced, only
  withdrawn. It belongs with the blue/green work.
