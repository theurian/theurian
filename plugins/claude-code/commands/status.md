---
description: Show Theurian daemon, project, index, and MCP connection status.
allowed-tools: Bash(theurian:*), Bash(curl:*)
---

# /theurian:status

Report the current state of Theurian. Read-only.

## What to do

Run these and summarise:

```sh
theurian daemon status --json
theurian project status --json
theurian index status --json
theurian migrate status --json
```

Present a compact summary:

- daemon: running or not, PID, version, endpoint
- project: registered, not registered, or — when `registered` is `null` — that
  the registry could not be read, naming the ids under `unreadable`; plus the
  project id and current state hash
- index: active build, whether it is stale, whether a build is in progress
- migrations: applied count, pending count, any checksum mismatch

## Rules

- Change nothing. This command has no side effects at all.
- If the daemon is not running, say so and point at `/theurian:setup` (if no
  service is registered) or `theurian daemon start` (if one is).
- Report a migration checksum mismatch prominently. It means an applied
  migration file was edited after the fact, which is a correctness problem, not
  a warning to scroll past.
