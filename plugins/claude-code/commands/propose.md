---
description: Draft a knowledge change as a reviewable proposal. Never writes approved state.
allowed-tools: Bash(theurian:*), Write
---

# /theurian:propose

Turn something worth remembering into a proposal a human can review.

`theurian propose` drafts the proposal; `theurian propose accept <id>` moves it
into place once a human agrees. Neither approves anything — `accept` moves files
and stops short of the judgement, and approval is a human merging the pull
request that carries the proposal
([ADR-0013](../../../docs/adr/0013-ai-writes-produce-proposals.md) point 4).
There is no CLI or MCP surface that stands in for that merge.

Containment here is a documented rule, not a server-side check. `allowed-tools`
grants and removes nothing — [`upgrade.md`](upgrade.md) states those semantics
with the vendor citation — so `Bash(theurian:*)` auto-approves *every* `theurian`
invocation, `theurian migrate apply` (a canonical write) and `theurian propose
accept` (which moves a migration into `.theurian/migrations/`) included. The
`Write` grant is only for the body file you hand to `--body-file`; Core writes
the proposal directory itself, under `.theurian/proposals/`, and nothing else.
What keeps approval with the human is the **"You cannot approve knowledge"** rule
below, not the front-matter
([#209](https://github.com/theurian/theurian/issues/209)).

## What to do

1. Establish what is being proposed and, critically, the **evidence** for it: a
   commit, a review thread, a specification, an incident. A proposal with no
   reasoning is rejected at generation, so if you cannot name what justifies the
   change, write nothing and say what is missing instead.

2. Write the body — the knowledge itself — to a file, in its native format: `.md`,
   `.json`, or `.yaml`. That file is the only thing you write directly; Core reads
   it and copies it into the proposal directory, so you can discard it afterward.

3. Draft the proposal. Every field is an option; nothing prompts:

   ```sh
   theurian propose \
     --item-id architecture.retry-policy \
     --title "Retry policy" \
     --kind architecture \
     --owner platform-team \
     --author platform-team@example.com \
     --description "Adopt exponential backoff for outbound calls." \
     --body-file retry-policy.md \
     --source-uri https://github.com/acme/api/commit/0123456789abcdef \
     --source-commit 0123456789abcdef \
     --agent-id claude-code \
     --task-id <the task this came out of> \
     --model <your model identity> \
     --reasoning "Why the evidence supports the claim." \
     --json
   ```

   It writes `.theurian/proposals/<proposal-id>/` — the migration under the name
   it keeps once accepted, the body under a sub-path mirroring its knowledge
   namespace, and `evidence.json` — and writes nowhere else. Approved knowledge
   is not touched. The `--json` output names every path written, and reports the
   `proposalId` you pass to `accept`, the generated `migrationId` and `revisionId`,
   and the `contentSha256` it pinned. The command reports missing options all at
   once, so a bad invocation costs one turn, not one per flag.

   - **`--author` is the human**, not you: the field names who owns the change
     once it is approved. Your identity is the evidence, via `--agent-id`,
     `--task-id` and `--model`.
   - **Name a source, or declare there is none.** `--source-uri` (with
     `--source-commit` and `--source-path` where they apply) records where the
     knowledge came from. Knowledge that originates in Theurian and has no external
     source takes `--authored-here` instead — a claim to make deliberately, not a
     way around a missing anchor. One or the other is required, because
     `theurian migrate apply` refuses a revision that has neither (INV-8).
   - **Updating an item that already exists?** Pass `--expected-revision <id>`
     with the item's current revision id. Without it, `theurian propose` refuses
     the draft rather than emitting an update that validates and then loses a
     concurrent change at `migrate apply`
     ([#210](https://github.com/theurian/theurian/issues/210)). Pass it only for
     an item that exists: a first revision has nothing to replace, and the command
     refuses it there too.
   - **Do not try to pin the digest or pick ids yourself.** The command computes
     `contentSha256` from the body you passed and mints fresh ULIDs for the
     proposal, the migration and the revision. There is nothing to hand-author,
     and no `migration.schema.json` in the repository to read — it ships inside the
     installed Core, and the generator validates against it before it writes a
     file.

4. Show the user what was written — the `proposalId` and the paths from the
   `--json` output — and one sentence on what the migration would change.

5. Give them the next steps. The judgement is theirs; nothing here has been
   approved, and nothing has moved into `.theurian/migrations/` yet.

   1. Read the proposal.
   2. If they agree, accept it — this is the reviewer's step, so run it only once
      they have said yes, not as a continuation of drafting:

      ```sh
      theurian propose accept <proposal-id> --json
      ```

      This moves the migration into `.theurian/migrations/` under the name it
      already has, and the body to the path its `contentFile` names. It moves
      files and nothing else: it does not validate, does not apply, and does not
      approve. The two moves are asymmetric — the migration may never land on an
      existing name (a collision means that migration is already in place), while
      the body *may* replace what is at its path, since on an update to existing
      knowledge that is the intent. `accept` rejects a proposal directory that is
      or contains a symlink, and confines every write to `.theurian/knowledge/`
      and `.theurian/migrations/`.
   3. Check it:

      ```sh
      theurian migrate validate --json
      ```

      This checks schema conformance, and it is the first point at which anything
      is checked. It reads `.theurian/migrations/` only, so it reports nothing
      while a proposal still sits under `.theurian/proposals/`. It does not prove
      the migration will apply: the invariants `migrate apply` enforces — a
      revision's source anchor, a reused revision id — are checked in step 5, after
      the pull request has already merged
      ([#36](https://github.com/theurian/theurian/issues/36)).
   4. Open a pull request with the accepted migration and body in it. **The merge
      is the approval.** `evidence.json` is read by the reviewers or by nobody;
      `.theurian/proposals/` is not in the `.gitignore` block `theurian init`
      writes, so the proposal directory commits as it stands if it is kept.
   5. After it merges:

      ```sh
      theurian migrate apply --json
      ```

      This is where the invariants land. A migration that fails one exits 4 and
      applies nothing.
   6. Rebuild the index, or the knowledge just approved is not searchable.
      **Ask first whether the project keeps a RAPTOR summary forest**, the way
      `/theurian:reindex` step 1 does: a build writes zero summary nodes unless it
      is given `--raptor`, so on a forest-bearing project a plain build publishes
      `nodes: 0` and the summary retriever goes quiet. Never add `--raptor` unasked
      (ADR-0008 decision 10).

      Without the forest:

      ```sh
      theurian index build --json
      ```

      With it, and only on their yes:

      ```sh
      theurian index build --raptor --json
      ```

      `migrate apply` does not index what it applied.

## Rules

- **You cannot approve knowledge.** No CLI or MCP surface offers an approval call
  — `theurian propose accept` moves files, it does not approve — so the boundary
  is this rule and not a check Core performs. Approved knowledge is what an agent
  will cite tomorrow as a team decision, so a human has to have said yes, and
  saying yes is merging the pull request.
- **Draft; do not commit the change for them.** Your authority is `theurian
  propose` and the body file it reads. `theurian propose accept` puts a migration
  where `migrate apply` will act on it, so run it only when the user has agreed,
  and never run `migrate apply` for them.
- If the user asks you to skip review, explain that approval goes through a pull
  request by design, and offer to help write the proposal well instead.
- Record uncertainty in the proposal rather than resolving it silently.
