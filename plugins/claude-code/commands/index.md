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

**Exit 6 is not a failure.** It means the index *was* published and something in
it looks like a credential (SEC-11). Report the build id exactly as you would on
exit 0, then relay every line of `secretFindings` and the `remedy` beside it, and
say the index is live. Only exit 1 means nothing was published.

## Rules

- This is not incremental. `theurian index build` creates a fresh derived index
  from the current canonical state on every run.
- **`theurian index build` has two non-zero exits and they are opposites.** 1
  means the build failed and nothing was published, so retrieval still uses the
  previous build. 6 means a complete index was published *and* its content
  carries a secret-shaped string: the build succeeded, and `theurian doctor` will
  keep reporting it until a build comes back clean. Never report 6 as a failed
  build, and never re-run the build in response to it — the finding is in the
  content, so it comes back. Getting a landed secret out means rotating the value
  and then removing it from the corpus by the route its channel needs — a new
  `upsertRevision` for a body, a title or a source anchor, `removeRelation` for a
  note on an edge, `deprecateItem` for any of them — which is what the `remedy`
  field says.
- `secretFindings` names the item and the channel the string sits in and quotes
  at most four characters of the match. It is the only account of what was found
  — the record `doctor` reads afterwards carries a count and no items — so relay
  it in full rather than summarising it. Do not echo the credential itself; you
  do not have it.
- A `recordWarning` beside those findings means the index published but the
  verdict could not be written down, so `theurian doctor` will answer
  `unrecorded` rather than repeating the findings. Relay it with them.
- RAPTOR is opt-in. Add `--raptor` only when the project keeps a summary forest;
  when requested, the build fully re-derives the eligible forest for that build.
- The previously published index keeps serving every query while the build runs.
  Search never goes dark, and a partial build is never searchable.
- `/theurian:reindex` uses this same build step, then adds explicit
  superseded-build cleanup.
