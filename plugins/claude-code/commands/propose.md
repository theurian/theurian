---
description: Draft a knowledge change as a reviewable proposal. Never writes approved state.
allowed-tools: Bash(theurian:*), Read, Write
---

# /theurian:propose

Turn something worth remembering into a proposal a human can review.

**The `propose` subcommand ADR-0013 describes is not registered in the CLI yet**
([#89](https://github.com/theurian/theurian/issues/89); Milestone 7 builds it).
Until it lands, this command runs ADR-0013 §4's flow by hand: you write the
proposal directory with `Write`, and every approval step is performed by a person
using commands that do exist.

## What to do

1. Establish what is being proposed and, critically, the **evidence** for it: a
   commit, a review thread, a specification, an incident. A proposal with no
   evidence is rejected, so if you cannot name a source, write nothing and say
   what is missing instead.

2. Write the proposal directory yourself:

   ```text
   .theurian/proposals/<proposal-id>/
   ├── migration.yaml
   ├── content.md      (or .yaml / .json, matching the knowledge format)
   └── evidence.json
   ```

   `<proposal-id>` is a ULID, as `migration.yaml`'s own `id` is.

   `migration.yaml` has to be schema-valid and directly applicable: the gap
   between a proposal and approved knowledge is human review, not format
   conversion. `migration.schema.json` ships inside the installed Core rather
   than in the repository you are working in, so there is no file here to read
   it from — `theurian migrate validate` in step 4.3 is what enforces it. A
   change adding one knowledge item looks like this:

   ```yaml
   apiVersion: theurian.dev/v1
   id: 01K9AB2CD3EF4GH5JK6MN7PQ8R           # ULID, uppercase Crockford base32
   createdAt: 2026-08-17T09:00:00+09:00     # RFC 3339, explicit offset
   author: platform-team@example.com        # the human who will own this
   description: Why the change is being made. Reviewers read this before the diff.
   operations:
     - op: createItem
       itemId: architecture.retry-policy
       kind: architecture
       namespace: architecture
       owner: platform-team
     - op: upsertRevision
       itemId: architecture.retry-policy
       revisionId: 01K9AB2CD3EF4GH5JK6MN7PQ8S
       contentFile: ../knowledge/architecture/retry-policy.md
       metadata:
         title: Retry policy
         contentType: text/markdown
         kind: architecture
         namespace: architecture
         status: approved
         owner: platform-team
         sourceAnchors:
           - provider: git
             sourceUri: https://github.com/acme/api/commit/0123456789abcdef
             commitSha: 0123456789abcdef
   ```

   - **`author` is the human**, not you. Your identity goes in `evidence.json`;
     the field the migration carries is who owns the change once it is approved.
   - **`status: approved`** is right even though nobody has approved it yet: the
     file applies only after a human has merged it, and `draft` or `proposed`
     would land knowledge that `theurian index build` leaves out unless it is
     given `--include-unapproved`.
   - **Leave `trustLevel` and `sensitivity` out.** Both are optional, and
     `trustLevel: reviewed` on something you drafted claims a review that has
     not happened. A reviewer can add them.
   - **`contentFile` is resolved from `.theurian/migrations/`**, not from the
     directory holding the migration file that names it. Write the path the body
     will have *after* step 4.2 moves it; a path relative to the proposal
     directory parses and then fails to resolve once the migration is in place.
   - **`evidence.json`** records where this came from: your agent and task
     identity, the model, the source anchors, and the reasoning that joins them
     to the claim (ADR-0013 point 5).

3. Show the user what was written — the tree above with the real id — and one
   sentence on what the migration would change.

4. Give them the next steps. The judgement and the file moves are theirs; the
   only one of these you may run for them is the check in 4.3, which changes
   nothing.

   1. Read the proposal.
   2. If they agree, move `migration.yaml` into `.theurian/migrations/` and the
      body file to the path its `contentFile` names.
   3. Check it:

      ```sh
      theurian migrate validate --json
      ```

      This is the first point at which anything is checked. The validator reads
      `.theurian/migrations/` only, so while a proposal sits under
      `.theurian/proposals/` it reports zero migrations and says nothing about
      it.
   4. Open a pull request. The merge is the approval.
   5. After it merges:

      ```sh
      theurian migrate apply --json
      ```

## Rules

- **You cannot approve knowledge.** There is no code path from this command to
  approved state, and that is the point: approved knowledge is what an agent
  will cite tomorrow as a team decision, so a human has to have said yes.
- Do not write into `.theurian/migrations/` or `.theurian/knowledge/` directly.
  Writing under `.theurian/proposals/` is the whole of your authority here — step
  4.2 is the human's act of approval, and performing it for them makes it yours.
- If the user asks you to skip review, explain that approval goes through a pull
  request by design, and offer to help write the proposal well instead.
- Record uncertainty in the proposal rather than resolving it silently.
