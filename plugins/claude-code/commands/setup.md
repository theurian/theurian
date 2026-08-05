---
description: Configure this machine to run Theurian, and connect Claude Code to it. Run once.
allowed-tools: Bash(theurian:*), Bash(command:*), Read, Edit
---

# /theurian:setup

Run Theurian's first-time setup. This is the **only** command that registers an
OS service or writes the MCP connection entry. Everything else in this plugin
reads.

It does **not** install Theurian Core. Core is a prerequisite, installed with
`uv tool install theurian` or `pipx install theurian` — `theurian setup` runs
from that installation, so it cannot be what creates it.

## What to do

1. Confirm Core is installed. If this prints nothing, stop and tell the user to
   run `uv tool install theurian` (or `pipx install theurian`) first; every step
   below shells out to this binary and will fail without it.

   ```sh
   command -v theurian
   ```

2. Show the plan before changing anything:

   ```sh
   theurian setup --dry-run --json
   ```

3. Present the plan to the user as a short list of the steps that would run,
   grouped by what they touch (data directory, credentials, OS service, project
   registration, Claude Code MCP configuration). Name every file that would be
   created or modified.

4. If the plan is empty, report that Theurian is already fully configured and
   stop. Do not re-run anything.

5. Otherwise ask the user to confirm, then run:

   ```sh
   theurian setup --json
   ```

6. Report the result using the report's `steps` array. Show what changed and
   what was already satisfied. A step that reports `missing` alongside an
   `action` is one setup does not perform itself — relay the command it names
   rather than implying setup handled it.

7. If the report's `serenaDetected` field is true, tell the user that Theurian
   and Serena are configured to work together, and briefly state the split:
   Theurian answers "what did we decide and why", Serena answers "where is this
   symbol defined".

## Rules

- Never edit a configuration file yourself. `theurian setup` owns every write,
  so that the CLI and this command cannot drift apart (CP-7).
- If a step reports `conflicting`, show the diff from the report and ask the
  user before proceeding. Never overwrite.
- If setup finishes in `degraded` state, that is a success with warnings.
  Report the warnings; do not retry.
- If setup fails, report the failing step and suggest `/theurian:doctor`. Do
  not attempt a repair.
