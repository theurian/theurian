---
description: Build a fresh Theurian search index from current state.
allowed-tools: Bash(theurian:*)
---

# /theurian:index

Build and publish a fresh derived search index from the current knowledge state.

## What to do

```sh
theurian index status --json
theurian index build --json
```

Report the resulting index build id and how long it took.

## Rules

- This is not incremental. `theurian index build` creates a fresh derived index
  from the current canonical state on every run.
- RAPTOR is opt-in. Add `--raptor` only when the project keeps a summary forest;
  when requested, the build fully re-derives the eligible forest for that build.
- The previously published index keeps serving every query while the build runs.
  Search never goes dark, and a partial build is never searchable.
- `/theurian:reindex` uses this same build step, then adds explicit
  superseded-build cleanup.
