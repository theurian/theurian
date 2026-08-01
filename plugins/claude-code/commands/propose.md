---
description: Draft a knowledge change as a reviewable proposal. Never writes approved state.
allowed-tools: Bash(theurian:*), Read, Write
---

# /theurian:propose

Turn something worth remembering into a proposal a human can review.

## What to do

1. Establish what is being proposed and, critically, the **evidence** for it: a
   commit, a review thread, a specification, an incident. A proposal without
   evidence is rejected at generation, and rightly so.

2. Generate the proposal:

   ```sh
   theurian propose --json
   ```

3. Show the user what was written:

   ```text
   .theurian/proposals/<proposal-id>/
   ├── migration.yaml
   ├── content.md      (or .yaml / .json, matching the knowledge format)
   └── evidence.json
   ```

4. Tell them the next step: review the proposal, and if they agree, run
   `theurian propose accept <proposal-id>` to move the migration into
   `.theurian/migrations/`, then open a pull request.

## Rules

- **You cannot approve knowledge.** There is no code path from this command to
  approved state, and that is the point: approved knowledge is what an agent
  will cite tomorrow as a team decision, so a human has to have said yes.
- Do not write into `.theurian/migrations/` or `.theurian/knowledge/` directly.
- If the user asks you to skip review, explain that approval goes through a pull
  request by design, and offer to help write the proposal well instead.
- Record uncertainty in the proposal rather than resolving it silently.
