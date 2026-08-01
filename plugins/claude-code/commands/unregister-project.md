---
description: Remove a project registration. Keeps all Git-tracked knowledge.
allowed-tools: Bash(theurian:*)
---

# /theurian:unregister-project

Stop serving a project from the daemon.

## What to do

1. List projects so the user picks the right one:

   ```sh
   theurian project list --json
   ```

2. Confirm the target with the user, then:

   ```sh
   theurian project unregister <project-id> --json
   ```

3. Report what was removed and, explicitly, what was kept.

## Rules

- Unregistering removes the daemon's registration and derived local state. It
  **never** deletes `.theurian/migrations/`, `.theurian/knowledge/`, or
  `.theurian/specifications/` — those are Git-tracked team knowledge.
- Always state that plainly in the result, so nobody has to wonder whether they
  just lost their team's decisions.
- If the user actually wants to delete knowledge, that is a Git operation they
  perform themselves, with review.
