# Revision-id reuse discloses a withheld item's body (GHSA-7997-g35f-q59h)

This log records the private security fix landed on
`security/revision-id-item-scope` for GHSA-7997-g35f-q59h, and one amendment to a
directive about release timing so the earlier wording is not later cited as
violated.

## The vulnerability and its class

A migration that reused an existing `revisionId` under a second `itemId` — the
shape a copy-pasted `upsertRevision` operation block produces — pointed the second
(approved) item's `current_revision_id` at the first item's revision row. When the
first item was withheld (for example `status: rejected`), its full body — title,
source anchors, and any secret that caused the rejection — reached `knowledge.get`
and `knowledge.search` for a caller who requested the *approved* item's id.
Requesting the withheld item directly was still correctly refused; the reuse
bypassed that gate, and `theurian migrate validate` / `migrate apply` reported
nothing.

Root cause: `SqliteWriter.append_revision` resolved FR-K8 idempotency by
`revision_id` alone — a content-hash match returned a no-op without checking that
the stored row's `item_id` matched the incoming operation. A revision id is
globally unique and names one item for the life of a project, so the reuse wrote a
pointer naming another item's revision, and the read paths that dereference the
pointer served its body.

**Class: identity resolved by `revision_id` where `item_id` is authoritative.**
The class is named by its root cause, not by the shape of the disclosure. It is
closed by invariant restoration rather than face by face:

- The revision-row INSERT is the single write chokepoint; `append_revision`
  refuses to no-op across a different `item_id` (`InvariantViolationError`, before
  the content comparison).
- `put_item` carries a symmetric store-level guard refusing a
  `current_revision_id` that names another item's revision, so the write no longer
  rests on the migration engine's in-memory INV-2. Both existence lookups are
  project-scoped, closing a latent cross-project `item_id` disclosure.
- `SCHEMA_VERSION` is bumped 1→2 (an input to the derived-state hash), so every
  database a fixed build reads has been regenerated through the guarded chokepoint.
  `revision_id → item` uniqueness therefore holds over all accepted data, and the
  four read-side faces (`knowledge.get`, the substring fallback, the ranked path,
  the index build — all through `get_revision`) are closed transitively, including
  faces added later.

Two residual members are closed and verified by running: an affected-version state
database (refused on open by the schema bump — pinned, so reverting the constant
goes RED) and a published index built from a poisoned store (closed transitively,
because no search path serves an index passage without first opening the
now-refused state database). Recorded in the threat model as **T-18 (Critical,
closed in 0.1.0.dev3)**.

Affected: `theurian` 0.1.0.dev0, 0.1.0.dev1, 0.1.0.dev2. Fixed in 0.1.0.dev3. The
CHANGELOG `### Security` entry and the advisory state the same affected range and
fixed version.

## Review round outcome

One three-reviewer round (code / security / adversarial) run before the fix
branch was prepared for release:

- **CRITICAL: 0.** The disclosure itself was reproduced, fixed, and confirmed
  closed by re-running the reproduction against the fixed build — apply refuses
  the reuse at exit 4, a pre-fix poison is refused at store open, and the withheld
  body reaches no tool.
- **HIGH: 0.**
- **MEDIUM: 2, both fixed.** Confirmed by orchestrator-run mutation over the
  suite: the `SCHEMA_VERSION` 2→1 mutation is now killed where it previously
  survived, and the item-scope guard's mutation is killed by the cross-adapter
  contract test.
- **LOW: 3**, recorded in the PR description; none forced another cycle.

The class was closed by a stated closure argument (invariant restoration over the
single write chokepoint plus the schema gate), not face by face — see T-18's
Controls paragraph in the threat model.

## Amendment: what "the CHANGELOG lands with the advisory" meant

The reference material carried a directive — "the CHANGELOG entry lands together
with the advisory." Read literally, that conflicts with landing the release
material on the fix branch now, before the coordinated public merge, which is what
`release.md` §"Fixing a vulnerability privately" step 2 requires.

The directive is amended to its intended meaning: **no public artifact appears
before the coordinated public moment, which is the advisory merge.** The CHANGELOG
entry, the version bump, and this log land on the private fork branch now; none of
them is public until the branch is merged and the advisory is published, and the
advisory publishes once 0.1.0.dev3 is fetchable. Landing the entry on the private
branch ahead of that moment does not violate the directive — the two records
(CHANGELOG and advisory) still become public together at the merge. This is
recorded so the earlier wording is not later cited as a process violation.
