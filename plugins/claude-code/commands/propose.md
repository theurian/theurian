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

2. Write the proposal directory yourself, giving the migration file the name it
   will keep once it is accepted:

   ```text
   .theurian/proposals/<proposal-id>/
   ├── <migration-id>-<kebab-slug>.yaml
   ├── content.md      (or .yaml / .json, matching the knowledge format)
   └── evidence.json
   ```

   `<proposal-id>` is a ULID, and `<migration-id>` is the migration's own `id`.
   `.theurian/migrations/` names its files `<ulid>-<kebab-slug>.yaml`
   ([migrations.md](../../../docs/protocol/migrations.md#naming-and-layout)), so
   writing that name here makes step 4.2 a move that renames nothing. Do not
   call it `migration.yaml`: every proposal would produce that same name, and
   accepting a second one overwrites the first with nothing reported. Measured
   on two proposals accepted in turn — after the second move
   `theurian migrate validate --json` reported `valid: true` and
   `migrationCount: 1`, naming only the second migration, and `migrate apply`
   exited 0 having applied only it. The first change was gone from the set, and
   its body file stayed in `.theurian/knowledge/` with nothing pointing at it.

   The migration has to be schema-valid and directly applicable: the gap
   between a proposal and approved knowledge is human review, not format
   conversion. `migration.schema.json` ships inside the installed Core rather
   than in the repository you are working in, so there is no file here to read
   it from. A change adding one knowledge item looks like this:

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
   - **`metadata.sourceAnchors` is optional to the schema and required by
     `theurian migrate apply`.** Do not drop it from the shape above to save
     space: a revision without one validates and then fails to apply (measured:
     `valid: true`, then apply exit 4, *"has no source anchor"*). The one
     alternative Core accepts is `labels: [authored-in-theurian]` on the
     revision, which declares the knowledge originates in Theurian and has no
     external source — a claim to make deliberately, not a way around a missing
     anchor.
   - **`evidence.json`** records where this came from: your agent and task
     identity, the model, the source anchors, and the reasoning that joins them
     to the claim (ADR-0013 point 5). It is read by the people reviewing the
     pull request; Core never reads it, so the anchors that are actually
     enforced are the ones in `metadata.sourceAnchors`.

3. Show the user what was written — the tree above with the real ids — and one
   sentence on what the migration would change.

4. Give them the next steps. The judgement and the file moves are theirs; the
   only one of these you may run for them is the check in 4.3, which changes
   nothing.

   1. Read the proposal.
   2. If they agree, move the migration file into `.theurian/migrations/` under
      the name it already has, and the body file to the path its `contentFile`
      names. If `.theurian/migrations/` already holds a file of that name, stop
      and say so rather than overwriting it: the name carries the migration's
      id, so a collision means that migration is already in place.
   3. Check it:

      ```sh
      theurian migrate validate --json
      ```

      This is the first point at which anything is checked, and what it checks
      is schema conformance. The validator reads `.theurian/migrations/` only,
      so while a proposal sits under `.theurian/proposals/` it reports zero
      migrations and says nothing about it. It does not tell you the migration
      will apply: the invariants `migrate apply` enforces are checked in step
      4.5, after the pull request has already merged
      ([#36](https://github.com/theurian/theurian/issues/36)).
   4. Open a pull request, and put the proposal directory in it. The merge is
      the approval, and `evidence.json` is read by the reviewers or by nobody —
      `.theurian/proposals/` is not in the `.gitignore` block `theurian init`
      writes (measured: `git check-ignore` exits 1 for a path under it), so the
      directory commits as it stands.
   5. After it merges:

      ```sh
      theurian migrate apply --json
      ```

      This is where the invariants land. A migration that failed one exits 4
      and applies nothing — measured, the run that exited 4 left no state
      database behind, and the corrected run reported `databaseCreated: true`.
   6. Rebuild the index, or the knowledge just approved is not searchable:

      ```sh
      theurian index build --json
      ```

      `migrate apply` does not index what it applied. Measured immediately
      after a successful apply: `theurian index status --json` reports
      `stale: true` with the remedy ``Run `theurian index build`.``
      `/theurian:index` runs the same command.

## Rules

- **You cannot approve knowledge.** There is no code path from this command to
  approved state, and that is the point: approved knowledge is what an agent
  will cite tomorrow as a team decision, so a human has to have said yes.
- Do not write into `.theurian/migrations/` or `.theurian/knowledge/` directly.
  Writing under `.theurian/proposals/` is the whole of your authority here — step
  4.2 is the human's act of approval, and performing it for them makes it yours.
- **Generate fresh ULIDs** for the proposal directory, for the migration's `id`,
  and for every `revisionId`. The ids in the shape above are illustration. A
  `revisionId` that has been applied already is rejected: a revision id names one
  item for the life of the project, and reusing one gets `valid: true` from
  `migrate validate` and then exit 4 from `migrate apply` (measured), after the
  pull request has merged.
- If the user asks you to skip review, explain that approval goes through a pull
  request by design, and offer to help write the proposal well instead.
- Record uncertainty in the proposal rather than resolving it silently.
