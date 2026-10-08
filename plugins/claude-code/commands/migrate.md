---
description: Validate and apply Theurian knowledge migrations.
allowed-tools: Bash(theurian:*), Read
---

# /theurian:migrate

Apply pending knowledge migrations to the canonical store.

## What to do

1. Always validate first:

   ```sh
   theurian migrate status --json
   theurian migrate validate --json
   ```

2. If validation is clean and migrations are pending, summarise what they will
   change — item ids, operations, and status transitions — and ask the user to
   confirm.

3. Then:

   ```sh
   theurian migrate apply --json
   ```

## Rules

- Never apply without validating first.
- If validation reports a **checksum mismatch**, stop. It means a migration that
  was already applied has since been edited on disk, so the recorded history and
  the file now make different claims. Do not apply, do not "fix" the checksum,
  and do not delete state. Explain the situation and ask the user to restore the
  original file or write a new migration.
- If validation reports a **revision conflict**, show the expected revision, the
  actual revision, and the item. Two people changed the same knowledge item;
  a human has to decide which is right. Never auto-merge.
- If validation reports a **dependency cycle**, list the cycle.
- If `permissiveMoves` is not empty in the output of `migrate validate` or
  `migrate apply`, show the user every row. A row is one `status` or
  `sensitivity` that `migrationId` loosened on `itemId`, from `before` to
  `after`. For `kind: undoes` and `kind: lowers`, a new revision in
  `migrationId` loosened it, and `undoes` is the migration that, before
  `migrationId`, last changed whether the item may be served, or its
  sensitivity class; a
  move between two retired statuses (deprecated, superseded, rejected) is not
  a change. `kind: undoes` means that change withdrew the field: it retired
  the item or raised its sensitivity class, whatever write made it, a revision
  re-declared in place included, and the new revision undid it — as when
  `undoes` was merged after the update was accepted, with an id that sorts
  before the update's.
  `kind: lowers` means that change withdrew nothing. `kind: reorders` means
  `migrationId`, by any change, replayed through its `dependsOn` after
  `undoes`, the largest id to have written the field before it and a larger
  one than its own, and took it from at or above that id's level to below. Say that nothing was
  refused: the report changes no exit code. If `migrate validate` prints
  `permissiveMovesUnavailable` instead, show it: the set did not replay, so
  there is no report, and `migrate apply` runs the same replay. The report is
  described in
  [migrations.md](../../../docs/protocol/migrations.md#permissive-moves-are-reported-not-refused).
