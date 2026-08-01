---
description: Build or update the Theurian search index incrementally.
allowed-tools: Bash(theurian:*)
---

# /theurian:index

Bring the search index up to date with the current knowledge state.

## What to do

```sh
theurian index status --json
theurian index build --json
```

Report the resulting index build id and how long it took.

## Rules

- This is incremental. Only changed knowledge items and the affected parts of
  the RAPTOR forest are rebuilt.
- The previously published index keeps serving every query while the build runs.
  Search never goes dark, and a partial build is never searchable.
- For a full rebuild, use `/theurian:reindex` — and be explicit with the user
  that it is the slower path.
