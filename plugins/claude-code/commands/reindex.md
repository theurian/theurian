---
description: Rebuild the index from current state, then reclaim the builds it replaces.
allowed-tools: Bash(theurian:*)
---

# /theurian:reindex

Rebuild the search index from the current knowledge state, then reclaim the
builds that rebuild superseded.

The build half is the same call `/theurian:index` makes. `theurian index`
registers `build`, `gc` and `status`, and `build` re-derives everything from
canonical state on every run, so there is no separate rebuild path to reach for.
What this command adds is step 3: publishing a build does not delete the one it
replaced, so superseded builds accumulate until something removes them, and
removing them is explicit rather than automatic (ADR-0007, ADR-0024 point 6).

## What to do

1. Tell the user what step 3 does — it deletes index builds the published
   pointer no longer names, and those files do not come back. Ask them to
   confirm before you run anything.

2. Build and publish:

   ```sh
   theurian index build --json
   ```

   Report the resulting `indexBuildId` and how long it took.

3. Show what would be reclaimed, then reclaim it:

   ```sh
   theurian index gc --dry-run --json
   theurian index gc --json
   ```

   Both report `reclaimed` and `bytesReclaimed`; only the second deletes
   anything. Show the dry run to the user first — it names the files by build id
   — and relay `strandedBuilding` if it is non-empty, because those are reported
   and never deleted.

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
  anything under a `.building` suffix. It refuses to run at all if the published
  build's file is missing, rather than treating every build on disk as
  unreferenced.
- Reach for this when the index is suspected to be inconsistent, after changing
  an embedding or summarization provider, or after a Theurian upgrade that
  changes the index format.
