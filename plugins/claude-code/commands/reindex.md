---
description: Rebuild the index from current state, then reclaim the builds it replaces.
allowed-tools: Bash(theurian:*)
---

# /theurian:reindex

Rebuild the search index from the current knowledge state, then reclaim the
builds that rebuild superseded.

The build half is the call `/theurian:index` makes, plus `--raptor` where the
project keeps a summary forest. `theurian index` registers `build`, `gc` and
`status`, and `build` re-derives everything from canonical state on every run,
so there is no separate rebuild path to reach for.
What this command adds is step 3: publishing a build does not delete the one it
replaced, so superseded builds accumulate until something removes them, and
removing them is explicit rather than automatic (ADR-0007, ADR-0024 point 6).

## What to do

1. Ask the user whether this project's index is built with the RAPTOR summary
   forest. You cannot work it out: `theurian index status --json` reports the
   build id, the three state hashes and the schema version, and nothing at all
   about summaries. The answer decides step 2, and **never add `--raptor` on
   your own judgement** — it is opt-in because a forest must not arrive as the
   side effect of something else (ADR-0008 decision 10).

2. Build and publish. Without the forest:

   ```sh
   theurian index build --json
   ```

   With it, and only on their yes:

   ```sh
   theurian index build --raptor --json
   ```

   Report the resulting `indexBuildId`, and `raptor` and `nodes` from the same
   output: `raptor` echoes the flag, and `nodes` is how many summary nodes the
   build wrote. Measured on a 128-chunk corpus, `--raptor` gave `nodes: 5` where
   a plain build of the same state gave `nodes: 0`. There is no duration field —
   if the elapsed time matters, time the call yourself.

3. Show what would be reclaimed, ask, then reclaim:

   ```sh
   theurian index gc --dry-run --json
   ```

   It reports `reclaimed` and `bytesReclaimed` and deletes nothing. Show it to
   the user — it names the files by build id — and ask them to confirm only
   once they have seen it, because those files do not come back. Relay
   `strandedBuilding` if it is non-empty: those are reported and never deleted.

   ```sh
   theurian index gc --json
   ```

   Same output, and this one deletes. Among what it deletes is the build step 2
   superseded, so if step 2 was run without `--raptor` by mistake, this is where
   the forest's only copy goes — rebuild with `--raptor` *before* this command,
   not after. Measured: the superseded `--raptor` build was one of the two files
   reclaimed, leaving the published `nodes: 0` build alone on disk.

## Rules

- A rebuild touches only derived artifacts: chunks, FTS rows, embeddings, and
  RAPTOR nodes. Canonical knowledge is not affected, because it is rebuilt from
  Git-tracked migrations and content, not from the index.
- The previously published index keeps serving every query while the build runs.
  Search never goes dark, and a partial build is never searchable. Step 3 runs
  after the new build is published, so there is no window with no published
  index.
- **`theurian index gc` never reclaims the build the pointer names**, nor a build
  whose id sorts above it (a writer that has finished and not published yet), nor
  anything under a `.building` suffix.
- It refuses the whole run rather than treating every build on disk as
  unreferenced, in two cases: the pointer names a build whose file is missing,
  and the pointer cannot be read at all. Both exit 1 having reclaimed nothing,
  with `--dry-run` too. Relay the remedy it prints instead of retrying — for the
  missing file that is to run `theurian index build` and publish a build that
  exists, *"because reclaiming now would delete every build on disk"*.
- Reach for this when the index is suspected to be inconsistent, after changing
  an embedding or summarization provider, or after a Theurian upgrade that
  changes the index format. The provider case is the one where step 1 matters
  most: a summarization provider only shows up in a build given `--raptor`, so
  rebuilding without it changes nothing about summaries and then step 3 discards
  the forest the old provider had produced.
