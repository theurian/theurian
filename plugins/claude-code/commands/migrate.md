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
