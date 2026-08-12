---
description: Configure this machine to run Theurian, and connect Claude Code to it. Run once.
allowed-tools: Bash(theurian:*), Bash(command -v:*), Read
---

# /theurian:setup

Run Theurian's first-time setup. This is the **only** command that registers an
OS service or writes the MCP connection entry. Everything else in this plugin
reads.

It does **not** install Theurian Core. Core is a prerequisite, installed with
`uv tool install 'theurian[daemon]'` or `pipx install 'theurian[daemon]'` —
`theurian setup` runs from that installation, so it cannot be what creates it.
The `[daemon]` extra is the part setup configures. A Core that lacks it has no
`uvicorn`, and setup stops at `core-present` rather than registering a service
that cannot start.

## What to do

1. Confirm Core is installed. If this prints nothing, stop and tell the user to
   run `uv tool install 'theurian[daemon]'` (or `pipx install 'theurian[daemon]'`)
   first. Steps 2 and 5 shell out to this binary and are the only things here
   that do any work; without it they fail with `command not found`.

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

6. Report the result using the report's `steps` array. Every step carries a
   `status` — what the final probe found — and an `outcome`, which is what this
   run did to it: `changed`, `unchanged`, `failed` or `not-attempted`. Show what
   changed and what was already satisfied.

   Do not treat `missing` plus an `action` as the mark of a step setup skips.
   Every step setup performs reports exactly that while there is work to do, so
   an agent relaying all of them would ask the user to go and "Create
   ~/.theurian with mode 0700" themselves. An `action` describes the work; it is
   not always a command anyone can run.

   What needs reporting is every step whose `status` is *still* `missing` or
   `conflicting` once the run has finished — the report lists those in
   `warnings` too. Split them by whether the `action` contains a command:

   - **It names one** — `theurian init`, `theurian project register`. Setup does
     not run these. Quote the command to the user.
   - **It does not** — `daemon-running` ends `missing` with "Start the service
     that was just registered." when nothing answers on the port. That is setup
     describing its own work, which did not finish. Report it as unfinished and
     point at `/theurian:doctor`; there is nothing here for the user to run.

   Skip this step's second and third paragraphs unless `state` is `converged` or
   `degraded`. In `plan-built`, `awaiting-consent`, `halted` and `aborted`
   the verification pass never ran, so `status` is still the plan rather than the
   result.

   When `state` is `halted`, also name the report's `changedPaths`: setup stopped
   without undoing anything, so those files are on disk now — possibly including
   the `auth/mcp-token` credential — for the user to decide whether to keep or
   remove.

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
