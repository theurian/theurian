---
description: Rebuild the Theurian index from scratch. Slower than /theurian:index.
allowed-tools: Bash(theurian:*)
---

# /theurian:reindex

Discard derived index data and rebuild it completely.

## What to do

1. Tell the user this is the slow path and that `/theurian:index` handles the
   normal case incrementally. Ask them to confirm.

2. Then:

   ```sh
   theurian index rebuild --json
   ```

## Rules

- A rebuild touches only derived artifacts: chunks, FTS rows, embeddings, and
  RAPTOR nodes. Canonical knowledge is not affected, because it is rebuilt from
  Git-tracked migrations and content, not from the index.
- The old index continues serving until the new one is verified and published.
- Reach for this when the index is suspected to be inconsistent, after changing
  an embedding or summarization provider, or after a Theurian upgrade that
  changes the index format.
