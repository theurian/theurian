---
description: Upgrade Theurian Core, checking plugin compatibility first.
allowed-tools: Bash(theurian:*)
---

# /theurian:upgrade

Upgrade Theurian Core to a version this plugin supports.

## What to do

1. Show the current state:

   ```sh
   theurian version --json
   theurian upgrade --check --json
   ```

2. Report the current version, the available version, and whether the available
   version falls inside this plugin's `coreCompatibility` range.

3. If it does, ask for confirmation, then:

   ```sh
   theurian upgrade --json
   ```

4. Re-run the compatibility check afterwards and report the result.

## Rules

- Never upgrade automatically. Show the plan and ask.
- If the available Core version is outside this plugin's supported range, say so
  and stop. Upgrading Core past the range would break the plugin; the correct
  order is to update the plugin first.
- If an upgrade changes the index format, mention that `/theurian:reindex` will
  be needed and roughly what that costs.
- Never downgrade to resolve a mismatch.
