---
description: Remove Theurian selectively. Approved knowledge is never deleted without confirmation.
allowed-tools: Bash(theurian:*)
---

# /theurian:uninstall

Remove some or all of Theurian. Every scope is a separate, explicit choice.

## What to do

1. Show exactly what would be removed, before removing anything:

   ```sh
   theurian uninstall --dry-run --json
   ```

2. Ask the user which scopes to remove. Present them individually — never as a
   single "remove everything" option:

   | Scope | Effect |
   | :-- | :-- |
   | Claude Code plugin configuration | removes the MCP connection entry |
   | Daemon service | stops and deregisters the OS service |
   | Theurian binary | removes the installed CLI and daemon |
   | Cache and derived indexes | removes rebuildable data only |
   | User settings | removes `~/.theurian` configuration |
   | Repository `.theurian/` | **removes Git-tracked team knowledge** |

3. Run the uninstall with only the confirmed scopes as explicit flags.

4. Report every path removed and every path kept.

## Rules

- **Never** remove `.theurian/migrations/`, `.theurian/knowledge/`, or
  `.theurian/specifications/` without a separate, explicit confirmation that
  names what is being deleted. That directory is the team's approved knowledge,
  it is Git-tracked, and it is not Theurian's to discard.
- Default every destructive scope to *off*.
- Removing the plugin alone never touches knowledge. State that plainly.
- If the user is unsure, recommend removing the plugin configuration only. It is
  fully reversible with `/theurian:setup`.
