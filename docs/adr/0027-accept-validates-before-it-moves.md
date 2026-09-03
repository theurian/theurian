# ADR-0027: Accept validates before it moves, and every revision pins its body

- Status: accepted
- Date: 2026-08-23
- Deciders: Theurian maintainers
- Requirements: FR-K4, FR-K5, INV-1, INV-3, SEC-7, SEC-11, SEC-13, T-5, T-15
- Decision recorded in [#316](https://github.com/theurian/theurian/issues/316),
  adopted 2026-08-22: one pre-1.0 contract break bundling
  [#210](https://github.com/theurian/theurian/issues/210),
  [#307](https://github.com/theurian/theurian/issues/307) and
  [#198](https://github.com/theurian/theurian/issues/198)
- Amends the division of labour recorded in
  [ADR-0013](0013-ai-writes-produce-proposals.md) §4; see the cross-reference
  amendment there
- Rests on [ADR-0005](0005-yaml-knowledge-migrations.md) (rule 2, an applied
  migration is frozen; rule 8, applying all migrations to an empty store
  reproduces the full canonical state; and the scope of the `apiVersion` bump)
  and [ADR-0006](0006-immutable-revisions-and-optimistic-concurrency.md)
  (`expectedRevision` is optimistic concurrency, never a merge). Names
  [ADR-0018](0018-single-writer-synchronous-in-m1.md) for a residue it does not
  close

**Every number in this ADR was measured on 2026-08-23 against `main` @
`68e8a0b`, except where a later dated measurement is named inline.** Where a
measurement came from running the CLI rather than from reading the tree, the
flow that produced it is named beside it. The cost model's large-corpus figures
and the `$TMPDIR` residue below were measured on 2026-08-24 by the round-one
security review, and say so where they appear.

## Context

Three open defects in Theurian's write path share one shape: **a check exists,
and it runs somewhere other than where the decision is made.**

- **#210.** `opUpsertRevision.contentSha256` is schema-optional. A migration
  that declares no pin is loaded with the body's *current* hash adopted as
  though it had been pinned, so an out-of-band edit to that body is invisible
  afterwards. `migrate validate` warns; nothing refuses.
- **#307.** `theurian propose accept` moves a proposal's files into
  `.theurian/migrations/` and `.theurian/knowledge/` and then deletes the
  proposal directory, without validating what it moved. A self-inconsistent
  hand-authored proposal therefore lands, fails `migrate validate`
  project-wide, and is no longer available to re-accept — its sources are
  gone. Three faces were demonstrated on the issue: two operations naming one
  `contentFile`, a self-pin that mismatches its own body, and `contentFile: ""`
  under a schema `minLength` of 1.
- **#198.** SEC-11 — "scan content for secrets before it becomes an approved
  revision; block or warn per policy" — is not implemented. T-15 is graded
  *High — no content scanner ships* precisely because of that absence, and six
  tracked documentation and configuration surfaces say so in the present tense.

Theurian has no external users and `0.1.0.dev9` is the live release, so the
cost of breaking a published contract is an edit to this repository's own files
and nothing else. The maintainer decision on #316 was to spend that window
once, on all three, rather than three times.

## Decision

### 1. `contentSha256` is required on `upsertRevision`, tightened in place

`contentSha256` joins `required` in `schemas/migrations/migration.schema.json`
`$defs.opUpsertRevision`, which becomes
`["op", "itemId", "revisionId", "contentFile", "contentSha256", "metadata"]`.
**`apiVersion` stays `theurian.dev/v1`.**

Three measurements are the grounds, and each is a fact about the tree rather
than a judgement:

| Measured 2026-08-23, `main` @ `68e8a0b` | Result |
| :-- | :-- |
| Does the generator ever emit an unpinned revision? | No. `ProposalService.draft` computes `ContentHash.of_bytes(body_bytes)` for the body it is about to write and passes it into `_migration_document` on every path (`application/proposal_service.py`). There is no branch that omits it. |
| Does the loader verify a declared pin? | Yes, on every load. `_parse_operation` re-reads the resolved body, hashes the bytes it read, and raises `MigrationError` when a declared `contentSha256` disagrees (`infrastructure/filesystem/migration_loader.py`). |
| How many real migration documents would have to be edited? | **Zero.** 28 migration documents are tracked in this repository — 26 in the dogfood corpus under `.theurian/migrations/`, 2 under `examples/sample-project/` — and each carries a `contentSha256` on each of its `upsertRevision` operations. The live dogfood project's working tree holds 82 (the 26 tracked plus 56 machine-local operator notes); all 82 pin. |

The population key for the third row, so it can be attacked rather than
trusted: every tracked `*.yaml`/`*.yml` containing the line `op:
upsertRevision`, compared against its count of `contentSha256` lines
(`git ls-files '*.yaml' '*.yml'`, then `grep -c` for each), plus a filesystem
count of `.theurian/migrations/*.yaml` in the live dogfood project. Both counts
came out equal to the operation count and nothing was unpinned.

**Why the tightening does not bump `apiVersion`.** ADR-0005 scopes the bump
rule narrowly — "Adding an operation is a protocol change and requires a
version bump of `apiVersion`" — and this adds no operation. The loader matches
the version exactly (`document["apiVersion"] != MIGRATION_API_VERSION` raises),
so publishing `theurian.dev/v2` would make every one of the conforming
documents above unreadable by the new build, in exchange for separating a
population of incompatible documents that does not exist. The general question
of how the closed enums and the operation set evolve compatibly is
[#274](https://github.com/theurian/theurian/issues/274)'s, and this decision
does not settle it.

**What the requirement closes.** A hand-authored migration with no pin
currently passes `migrate validate` at exit 0 with a warning, and the loader
adopts whatever bytes the body holds at load time. After the change, the
absence is a schema error at `validate` and therefore in CI, before the
migration is applied and before it is merged. FR-K5's tamper evidence stops
depending on the author having remembered.

**Two things this decision does *not* buy**, stated because both were claimed
during design and neither is true:

- It does not strengthen the accept-path replacement guard. That guard keys on
  the landed body's `(st_dev, st_ino)` rather than on whether a pin was
  declared, so nothing about what it refuses moves with this decision.
  `tests/integration/test_proposal_service.py::test_accept_refuses_a_byte_identical_replacement_of_a_pinned_body`
  and `::test_accept_refuses_a_byte_different_redeclare_of_a_pinned_landed_revision`
  are the tests that pin it. Their unpinned twins are *deleted* rather than
  renamed, and could not have survived: each reached its state by stripping the
  pin from a landed migration, which after this decision no longer loads. The
  tightening removed the input they were written against, not the property they
  asserted — which the surviving siblings hold on the same key.
- It does not change what an absent `expectedRevision` means. **An absent
  `expectedRevision` permits a first revision, or an exact re-run of the same
  revision id**, and not only the first: `MigrationEngine._check_expected_revision`
  raises `RevisionConflictError` when an item already exists *unless* the
  operation's `revisionId` equals the item's current revision, which is the
  idempotent re-run ADR-0005 rule 5 requires. The schema's own description of
  `expectedRevision` says "or be absent when creating the first revision",
  which is the shorter and wrong half of that sentence.

**`expectedRevision` stays optional**, and that is a decision rather than an
omission. The bundle's original text required it too. It was re-scoped to
[#324](https://github.com/theurian/theurian/issues/324) because requiring it
buys no enforcement: the field is `oneOf` a ULID or `null`, and an explicit
`expectedRevision: null` parses identically to an absent key, so
required-and-nullable is satisfied by a document that guards nothing. The
semantics are already enforced where they can be — by the engine at apply, and
by `ProposalService._check_expected_revision`, which refuses to draft an update
to an existing item with no guard. The edit cost would be all 82 applied
migrations, 56 of them outside Git and outside CI.

#### The second break riding this one: `unpinnedRevisions` is removed

`migrate validate --json` publishes `unpinnedRevisions`, a list of warnings
about revisions that declare no pin. Once the pin is required, that list is
empty for every schema-valid input, and a permanently empty published field is
a claim that the condition is still reachable. **The field is removed.** This
is a second, separate break in the same CL — a published-field removal, not a
schema tightening — and it takes its own CHANGELOG entry at implementation.

The domain flag `UpsertRevision.content_pinned` (`domain/migration.py`) goes
with it. Its only purpose is to distinguish "declared a pin" from "the loader
filled `content_sha256` in from the body it read", and after the change the
loader has no second case to fill in. Its `__post_init__` check and the
`unpinned_revisions()` helper in `application/migration_body_guards.py` become
a guard no real data reaches, which on this project is a guard that survives
its own deletion.

Two surfaces assert, correctly today, that the unpinned state exists, and both
are updated in the implementation commit rather than left to rot:

- `docs/protocol/migrations.md`, the section "What an unpinned body does not
  get" and the `unpinnedRevisions` sample output beneath it.
- `tools/corpus_drift.py`, whose docstring says its conditional pinned-body
  read "is still declared here, because a declaration that describes the corpus
  rather than the code stops being true the moment somebody commits an unpinned
  revision." After this change the code, not the corpus, is what makes every
  anchor pinned, and the sentence has to say so.

### 2. `propose accept` validates, then moves

**Before it moves anything, `accept` proves that the union of the landed
migration set and the incoming proposal survives the same pipeline `migrate
apply` runs. If it does not, `accept` refuses and consumes nothing.**

That replaces the stance recorded in `_parse_migration`'s docstring —
"Deliberately not a validation pass. `accept` moves files; whether the
migration is well-formed is `migrate validate`'s question" — which ADR-0013 §4
is the source of. The division was defensible when `accept` was a `mv` with
guard rails. It stopped being defensible once the command also *deleted* its
sources: a check that runs after the input is destroyed cannot be acted on.

The pre-check has four stages, in this order:

1. **Schema and document limits** — `validate_migration_document`
   (`migration_loader.py`), the same entry `draft` already calls, followed by
   the `apiVersion` exact-match check.
2. **Self-consistency of the incoming proposal** — the digest verification the
   loader performs when it re-reads a referenced body, and
   `refuse_duplicate_content_files` (`application/migration_body_guards.py`)
   scoped to the incoming operations together with the landed set.
3. **The whole-set guards `migrate validate` runs** —
   `refuse_unenforceable_scope` (`application/migration_engine.py`),
   `refuse_duplicate_content_files`, and `refuse_alias_item_id_collision`
   (`application/migration_alias_guards.py`). The population key is the guards
   `validate_command` itself calls; re-enumerate it with
   `grep -n 'refuse_' packages/theurian-core/src/theurian/cli/commands.py`
   rather than trusting this list.
4. **A dry replay** of landed ∪ incoming against a throwaway target, which is
   the stage that catches what nothing above can: the invariants the engine
   enforces only while applying.

**Stages 3 and 4 overlap, deliberately, and the stage list should not be read
as a partition.** `MigrationEngine.apply` calls `refuse_unenforceable_scope`,
`refuse_duplicate_content_files` and `refuse_alias_item_id_collision` itself,
after planning and before any write, so the replay re-reaches all three. Stage 3
runs them statically first for message quality — a refusal that names the
offending migration rather than surfacing from inside a replay — and not because
removing it would leak a face past the pre-check. What stage 4 alone covers is
the invariants the engine can only check while it is applying — a revision's
source anchor, a reused revision id, the revision conflict measured below.

**The rehearsal starts from an empty database, and that bounds what it can
cover.** `rehearse_migration_set` (`cli/migration_pipeline.py`) creates a fresh
store and applies the union into it, so `verify_no_applied_migration_changed` —
the applied-checksum invariant (FR-K5, ADR-0005 rule 2: same id, different
checksum is a fatal error, never an auto-repair) — is *structurally* unreachable
inside the replay. That check compares each migration against the checksums a
**previously active** database recorded, and the rehearsal's store has recorded
none: `MigrationEngine.plan` still calls it, but over an empty `recorded` map, so
it is a no-op there by construction rather than by luck. The invariant is held
one layer up, by the composition root — the CLI's own gate runs
`verify_no_applied_migration_changed` against the real active database before it
calls apply (`cli/commands.py`, the `MigrationChecksumMismatchError` path). So
the rehearsal covers the apply-time invariants reachable from an empty store — a
revision's source anchor, a reused revision id, the revision conflict,
unenforceable scope, duplicate content files — while the applied-migration tamper
check is not the shared pipeline's to keep. **A second write root — Milestone 7's
MCP write path — inherits this boundary: wiring the rehearsal buys it every
invariant in that list and *not* the checksum gate, which it must run itself
against the real store.** This does not weaken `accept`'s closure; it makes the
closure honest about where its boundary is.

**The dry replay is not new machinery; it is a property the format already
promises.** ADR-0005 rule 8 — *"Applying all migrations to an empty store
reproduces the full canonical state"* — is what makes replaying the set against
a throwaway target both well-defined and side-effect-free. A replay that
disagreed with the real apply would be a violation of rule 8 before it was a
bug in `accept`.

**Hard condition on the implementation: the replay invokes the same engine path
`migrate apply` invokes, differing only in the write target.** Not a
replay-shaped subset, not a re-implementation that checks the same invariants.
A second implementation makes the closure argument below false on the day it is
written, whether or not the two agree that day.

**The closure argument, which is the point of the design and not a description
of it:** *`accept` refuses any proposal whose acceptance would leave landed ∪
incoming unable to complete the pipeline `migrate apply` runs, and the
pre-check executes that pipeline's own code rather than a second copy of it.*
A divergence between what `accept` answers and what `apply` would answer is
therefore not a bug to be found later; it is unreachable for as long as the
sharing holds. This project has been burned twice by the other shape — two
detectors deriving one fact independently and drifting — so the sharing is the
requirement, not the reuse.

**The apply layer has at least three faces, and the replay closes the ones
nobody has written yet.** The revision conflict below is the measured one. The
other two are named by the product's own output: `_ACCEPT_STEPS` in
`cli/propose_commands.py` tells every caller that "source anchors and
revision-id reuse are checked by `theurian migrate apply`, after the pull
request has merged." A per-face guard strategy would need one new guard for
each apply-time invariant that exists today *and* one for each added later; the
replay covers the set by construction, including invariants that do not exist
when this ADR is written.

**Why stage 4 is not optional**, measured 2026-08-23 on a scratch project
against a development build of `main` @ `68e8a0b`:

- **The honest sequential flow does not cross-record.** Draft A, accept A,
  draft B with `--expected-revision`, accept B, one `migrate apply`: each
  revision's landed body and its `content_sha256` row match that revision's own
  source bytes, and the item pointer ends on B. #210's clause about two
  proposals cross-recording bodies is measured on this flow and does not
  reproduce.
- **The racing flow produces a set that validates and can never be applied.**
  Draft A and draft B *both* before either acceptance — so both claim the
  item's first revision — then accept both. `migrate validate` exits 0 with
  `valid: true`. `migrate apply` then refuses with `RevisionConflictError` at
  exit 4 (*"migration expected `<none>`, store holds `<revA>`"*), atomically:
  zero rows land and no partial state is written. Nothing cross-records, and
  the set is nonetheless validate-green and apply-red permanently, with both
  proposals already consumed.

Stages 1–3 do not catch that. `migrate validate` is schema conformance plus the
statically decidable set guards, by recorded design — its own docstring says it
gives "no guarantee that validate cannot pass a document apply will reject"
([#36](https://github.com/theurian/theurian/issues/36)), and the `propose` CLI
tells the caller the same thing: "Validation is schema conformance and nothing
more." The dry replay is what makes the refusal cover the racing face, and it
is what lets one closure argument cover all four faces instead of three plus a
surprise.

**The racing face is graded HIGH: shipped behaviour is wrong, and nothing is
disclosed.** `accept` exits 0 and yields a set `migrate validate` calls green
and `migrate apply` refuses permanently, with both proposals already consumed.
It is not a false published claim — `_ACCEPT_STEPS` honestly says validate
"does not prove the migration will apply" — and it discloses nothing the caller
may not read, which is what keeps it off CRITICAL. What makes it HIGH rather
than MEDIUM is the recovery: the only way out is deleting a landed migration
from `.theurian/migrations/`, and
[`plugins/claude-code/commands/propose.md`](https://github.com/theurian/theurian/blob/main/plugins/claude-code/commands/propose.md)
forbids exactly that to the documented actor — "Do not write into
`.theurian/migrations/` or `.theurian/knowledge/` directly". The documented
agent can reach the state and cannot leave it.

**The recovery property this buys.** Today a self-inconsistent proposal lands,
breaks the project's validation, and is gone; re-drafting is the only way
forward. After this change the proposal survives its own rejection, because
nothing was consumed. That is the outcome #307 asked for, stated as a property
rather than as a fix.

**Cost, measured rather than estimated — and its shape is O(corpus bytes), not
a constant.** The replay stages landed ∪ incoming into a throwaway tree before it
applies, which means it copies the corpus — every landed migration, every
referenced body, and a freshly built state database — into `$TMPDIR`. The cost is
therefore **dominated by that tree copy, and grows with the corpus's bytes**, not
process startup. On the live dogfood corpus — 82 migrations, 164 operations — a
full `migrate apply` took **0.55 s wall** and `migrate validate`'s load-and-guards
pass took **0.56 s** (measured 2026-08-23 on a development machine, against a
scratch copy). That near-equality is a property of *this* corpus, whose bodies are
small: when the bytes are few the copy is cheap, and the two figures coincide. It
does not generalise. Measured 2026-08-24 by the round-one security review, against
a synthetic 240 MiB corpus: `migrate validate` took **0.53 s**, while `propose
accept` took **3.76 s** on the success path — one rehearsal — and **5.66 s** on a
refusal, because `_landed_set_alone_fails` (`application/proposal_service.py`)
runs the rehearsal a second time to separate the proposal's fault from a
pre-existing one in the landed set. The gap between 0.53 s and those figures is
the tree copy, and it scales with the corpus. **This is bounded work, not
unbounded:** `accept` is a local, human-gated, interactive command, and the
corpus it copies is the operator's own, so no *caller* can make the system spend
work not bounded by the operator's own corpus size. The correction is to the cost
*model* — it is O(corpus bytes), not a constant "process startup" — and not to
the conclusion, which stands.

**Three residues, named rather than closed:**

- **`migrate validate`'s no-replay division stands by recorded contract**
  ([#36](https://github.com/theurian/theurian/issues/36)), and the measured
  consequence is worth stating plainly: **a validate-green, apply-red-forever
  set is reachable for a migration placed into `.theurian/migrations/` by
  hand**, because such a migration never passes through `accept`. Whether
  `validate` should gain a replay stage of its own is out of scope here.
- **No CI job applies the committed corpus**, so every apply-time invariant is
  unverified against the shipped migrations. That is a **different class** from
  this decision — it is about what CI runs, not about where `accept` checks —
  and it is filed as
  [#325](https://github.com/theurian/theurian/issues/325). It is cited here and
  not absorbed. One detail matters for reading it correctly: the corpus guard's
  uniqueness rule,
  `test_dogfood_corpus_governance.py::test_every_committed_revision_id_is_unique_across_the_corpus`,
  keys on **`revisionId`, not `itemId`**, so a racing pair — two distinct
  revision ids for one item, each claiming the item's first revision — passes
  it.
- **Two `accept` invocations racing at the process level stay deferred, and
  this decision widens the window.** ADR-0018 makes single-writer a contract in
  the application layer, enforced in Milestone 1 by an OS advisory file lock on
  a separate lock file, `.theurian/runtime/write.lock`, held for the duration
  of a write transaction and guarding the state databases under
  `.theurian/state/` — and the accept path's file moves are not under that
  lock. So each of two concurrent invocations can pre-check against a landed
  set the other is about to change, and **the replay lengthens the interval
  between examining and moving by the replay's own duration**. Saying so is
  part of the closure argument, not an aside: a reviewer who finds this
  unstated is right to call the closure incomplete.

### 3. SEC-11 ships as a real control on the accept path

An in-house content secret scanner runs over the proposal's body files before
`accept` moves anything. Its policy comes from `security.secretScan` in
`.theurian/config.yaml`: `block` — also the behaviour when the key or the file
is absent — refuses the acceptance with a remedy; `warn` proceeds and reports;
`off` skips the scan.

**The detector is written here, and takes no new dependency.** The approach is
the one this repository already uses against its own plugin tree for SEC-5:
pattern families for known token shapes, plus a Shannon-entropy heuristic over
candidate tokens. That detector lives in
`packages/theurian-core/tests/unit/test_secret_detector.py` and is a test-only
walker, so the shipped control is a new module rather than a move — but the
technique is one this project has already tuned, self-tested, and run against
real content.

The stance on completeness is the one SECURITY.md already publishes: *"Run a
repository secret scanner — Theurian is not one and is not a replacement for
one."* A best-effort in-house detector is consistent with that sentence. Taking
a scanning dependency to raise the detection rate was rejected, because it
enlarges the dependency footprint (ADR-0014 pins every dependency exactly, and
each one is a supply-chain surface) to improve a control the product
deliberately disclaims completeness on.

**The scan is body-scoped, by decision.** `_scan_bodies_for_secrets` reads the
incoming body files and nothing else, so a secret placed in the revision's own
metadata — its `--title`, `--description` or `--label`, or one of its source
anchors — is not scanned. This is a stated boundary rather than an oversight, and
it is a real disclosure channel, because two of those metadata channels are
published verbatim on every `knowledge.search` and `knowledge.get` result
(`mcp/results.py`, verified 2026-08-24 against the round-two security review): the
title, and the source anchors — provider, sourceUri, repository, commitSha,
filePath — set by `theurian propose`'s `--source-provider`, `--source-uri`,
`--source-commit` and `--source-path`, or by hand in the authored migration. A
URL, repository or file path is exactly where a credential in a token-bearing URL
hides, so the anchors are at least as sharp a published channel as the title. The
title, lowercased, also becomes the migration filename's slug, so a slug-surviving
credential such as an AWS access-key id lands in both. The description and labels
land unscanned in the migration metadata too, but they are not in the result
payload — unscanned-but-committed rather than unscanned-and-published. It is
deferred to [#336](https://github.com/theurian/theurian/issues/336) rather than
folded in here for three reasons stated so a reviewer can weigh them: the
metadata is bounded and human-gated the same way the body is — a human reviews
the migration diff, which carries the metadata as plainly as the body — so it is
not an *undisclosed* channel; shipping the body scan first is what #316's window
bought, and widening the scanner is additive rather than another contract break;
and the metadata fields are short, structured inputs where a false positive under
`block` is more disruptive than in a body, so the extension wants its own tuning.
The graded finding is HIGH — shipped behaviour lets a secret reach a published
field the control does not cover — and this paragraph is its recorded, CRITICAL-
free design decision, not a neutral gap note. What grade this leaves T-15 at is
decided in the threat model, not here; it stays High, and the metadata channel is
one of its recorded residuals.

> **Amended in Milestone 7, by the metadata-scan CL
> ([#336](https://github.com/theurian/theurian/issues/336)). The text above is
> the decision as accepted; the boundary it records no longer holds, and neither
> does this decision's opening sentence — *"runs over the proposal's body
> files"*.**
>
> As accepted, this decision scoped the scan to the proposal's body files and
> named the metadata channel a stated boundary, deferred for three reasons: the
> metadata is human-gated the same way the body is, shipping the body scan first
> is what #316's window bought, and short structured fields wanted their own
> false-positive tuning. `theurian propose accept` now scans the migration
> document's author-written strings as well as the bodies, so *"the scan is
> body-scoped, by decision"* is false and the symbol it names,
> `_scan_bodies_for_secrets`, no longer exists — `_scan_for_secrets`,
> `_document_findings` and `_authored_strings` carry the reach and its reasoning.
>
> **What implementing the deferral revealed is that two of its three reasons were
> weaker than they read.** "Human-gated the same way the body is" is true of
> where a value lands and false of how it is reviewed: a body arrives in a pull
> request as a file a reviewer opens, while a title arrives as one line of YAML
> beside a ULID — and the title and the source anchors are published verbatim on
> every `knowledge.search` and `knowledge.get` result, so a credential in one is
> disclosed to an agent that never opens the body. The tuning reason was
> measurable rather than arguable, and the measurement went the other way: over
> the migration corpus this repository tracks — the 26 documents under
> `.theurian/migrations/` and the 2 under `examples/sample-project/` — the scan
> reports nothing: 510 author-written strings, zero findings (measured against
> `67727eb`). The live dogfood machine's fuller corpus of 82 (those 26 plus 56
> machine-local operator notes) scans clean too, but is not reproducible from the
> repository. What those strings needed was the detector's ULID subtraction,
> which was already load-bearing for bodies.
>
> **The new answer is better for a reason the old one could not state: the
> population is bounded.** Each of the schema's fourteen operation branches and
> each leaf object it defines (anchors, metadata) declares
> `additionalProperties: false` — the `$defs/operation` `oneOf` wrapper does not
> itself, but every branch it selects does — so the string fields those objects
> name are exactly what a document `accept` could apply may carry. The scan reads
> that set and subtracts each derived field only where a mechanism already bars a
> *reported* secret: the ULID- and `^[0-9a-f]{64}$`-shaped identifiers
> (`id`, `revisionId`, `expectedRevision`, `dependsOn`,
> `contentSha256`), which the detector's class gate cannot fire on; the fixed
> vocabularies (`op`, `kind`, `status`, `trustLevel`, `sensitivity` and the other
> enums); and `contentFile`, a path whose secret-in-filename face is the
> artifact-level one. The date fields `createdAt`, `validFrom` and `validTo` are
> *not* in that subtraction — they are scanned, because a committed secret in one
> was reproduced verbatim by the rehearsal's date parse and scanning pre-empts it
> with a redacted refusal. `accept` moves two artifacts into the canonical tree
> and only two — the bodies and this document — so between them the gate sees the
> author-written *bytes* the acceptance makes canonical, but not the artifact
> level: a YAML comment, and the migration and body filenames, are unscanned and
> tracked as their own face
> ([#349](https://github.com/theurian/theurian/issues/349)). The filename in
> particular does not follow from the title's scan — the slug is not re-derived
> from the title at accept (`_require_filename_matches_id` checks only the ULID
> prefix, and a hand-authored slug is free-form), so it is #349's face and not
> the title's.
>
> What does not change is the grade or the disclaimer. T-15 stays High: the count
> that decides it is over the three points content enters the canonical store,
> and this widens the one already covered rather than covering a second. The
> detector is still best effort, there is still no per-finding suppression, and a
> proposal's `evidence.json` is still unscanned — `accept` never moves it into
> the canonical tree, so it is tracked with the draft-time advisory
> ([#330](https://github.com/theurian/theurian/issues/330)).

> **Further amended in Milestone 7, by the artifact-scan CL
> ([#349](https://github.com/theurian/theurian/issues/349)). The paragraphs above
> record the metadata amendment's own boundary — "the artifact level: a YAML
> comment, and the migration and body filenames, are unscanned" — and that
> boundary no longer holds.**
>
> The metadata amendment scanned the document's author-written string *values*
> and named what it did not reach: a YAML comment, the migration and body
> filenames, and the parsed `contentFile`. #349 reads all of them. The scan now
> covers everything the acceptance lands, not only what it parses — the migration
> file's raw bytes (so a comment and every field as written), the migration
> filename, each landed body path, and `contentFile`'s parsed value, which moved
> into `_authored_strings` because it is the one channel that catches a credential
> both `..`-collapsed *and* YAML-escaped, where the byte and path channels each
> miss. What implementing it revealed is that a finding's *location* was the last
> place a credential could still be republished: the body-content channel located
> itself by the very landed path that was the secret, walking around the
> four-character redaction bound the detector holds on the match. Every location
> is now a fixed module literal plus an index, so no refusal and no `accept
> --json` result reproduces more than that prefix — which is the better answer
> because the bound now holds on every channel, not just the field walk. Two
> residuals stay open, named as their own faces rather than folded in: the
> general name hygiene of refusal *messages* elsewhere on the accept path, which
> still echo an author's filename, id or `contentFile` verbatim
> ([#360](https://github.com/theurian/theurian/issues/360)), and a proposal's
> `evidence.json`, which `accept` still leaves in the directory it tells the
> author to commit ([#361](https://github.com/theurian/theurian/issues/361)). The
> filename channel is narrow by construction — a lower-case-kebab slug can spell
> only `sk-` and `xox`, two of the detector's eight families — but the
> less-restricted landed-path channel is not so limited: a path component admits
> upper-case letters, digits and `_`, so every family the slug excludes can be
> spelled and caught in one. T-15 stays High: this widens the one gate
> of three that was already covered.

**This is the first code in `src/` that reads `.theurian/config.yaml`.** Nothing
reads it today — `infrastructure/github/__init__.py` mentions the filename in a
docstring and that is the whole of it, which is the state
[#129](https://github.com/theurian/theurian/issues/129) recorded.
`packages/theurian-core/tests/unit/test_config_key_call_sites.py` exists to go
RED on exactly this diff; its module docstring names the intended failure mode
as "a Milestone 7 diff" and lists what must happen in the same change. **A
reviewer seeing that test fail is seeing it work**, and the implementation
commit updates it rather than silencing it.

## Consequences

### Positive

- The write path's checks move to where the decision is made. A pin that is
  required is checked in CI before merge, not warned about after; a proposal
  that cannot become part of a working set is refused before its sources are
  destroyed.
- One closure argument covers #307's three demonstrated faces *and* the racing
  face that measurement turned up, because the argument is about the pipeline
  rather than about the faces.
- `accept` and `migrate apply` cannot disagree about whether a set is usable,
  by construction rather than by two tests that happen to agree today.
- T-15 gains its first automated control at the point SEC-11 names, and six
  documents stop describing an absence.

### Negative

- Three published contracts break at once: a schema field becomes required, a
  published output field disappears, and a command that used to move files
  unconditionally can now refuse. Each is named as breaking in the CHANGELOG
  with its old and new shape. There are no external users to absorb it, which
  is why the window was spent now.
- `accept`'s failure surface gets wider. It now loads and replays the project's
  whole migration set, so a project fault unrelated to the proposal — a corrupt
  landed migration, an unreadable body — can make an acceptance fail. Every
  such fault must reach the caller as a `ProposalError` with a remedy naming
  what to fix, under the `{error, remedy}` contract
  [#227](https://github.com/theurian/theurian/issues/227) established. The
  *time* cost is O(corpus bytes) — small on a small corpus (0.55 s over the
  82-migration dogfood set) but 3.76 s over a synthetic 240 MiB one (measured
  2026-08-24; see decision 2's cost note) — because the rehearsal copies the
  corpus before it replays; the widened examine-to-move window is the part that
  matters, and it is recorded as decision 2's third residue.
- The secret scanner will produce false positives, and `block` is the default.
  A high-entropy string in a legitimate document blocks an acceptance until the
  author sets `warn` or `off`, and there is no per-finding suppression.
- A best-effort detector shipping as "the SEC-11 control" invites the reading
  that content is now screened. The disclaimer has to survive into every
  surface that flips below, or this decision trades an honest absence for a
  dishonest presence.

### Neutral

- **The flip set.** Six tracked prose and configuration surfaces currently
  assert, truthfully, that SEC-11's scanner does not exist, and each becomes
  false the moment the scanner reads the key: `SECURITY.md`,
  `docs/security/threat-model.md` (T-15's entry and the summary table row),
  `docs/architecture/requirements-analysis.md` (the threat table row),
  `docs/roadmap.md`, `schemas/config/project-config.schema.json` (the
  `security.secretScan` description, "Not in force, and reserved"), and
  `examples/sample-project/.theurian/config.yaml` (the comment above
  `secretScan: block`). Four test files pin those claims and go RED with them:
  `test_config_key_call_sites.py`, `test_examples.py`, `test_schemas.py`, and
  `test_dogfood_corpus_governance.py`. The population key is
  `git grep -l -i "no content scanner\|secret scanner\|secretScan\|SEC-11"`
  over tracked `*.md`, `*.json` and `*.yaml`, excluding `docs/work-logs/` and
  the CHANGELOG, both of which are historical record and must not be rewritten.
  Re-run it at implementation rather than trusting this list.
- Two further surfaces flip with the `unpinnedRevisions` removal:
  `docs/protocol/migrations.md` and `tools/corpus_drift.py`, both named in
  decision 1.
- **One user-facing string flips with decision 2**, and it is the one a caller
  reads first. `_DRAFT_STEPS` in `cli/propose_commands.py` tells the author
  that "the invariants `theurian migrate apply` enforces — a revision's source
  anchor, a reused revision id — are checked after the pull request has merged,
  not before it." After this change `accept` checks them before the pull
  request exists, so the sentence becomes false at the moment the pre-check
  ships. `tests/integration/test_propose_cli.py` reads `_DRAFT_STEPS` and pins
  its length and first element, so a step count that changes goes RED; a step
  whose *text* goes stale does not, which is why it is named here.
- **One in-source carrier of the old division flips with decision 2**, and it
  is the docstring that states the old division most precisely.
  `ProposalService._refuse_if_a_replacement_breaks_an_existing_pin`
  (`application/proposal_service.py`) records that a self-contained breakage in
  one proposal — "two operations naming one `contentFile`, a self-inconsistent
  pin, an empty `contentFile`" — "lands here and is caught by `migrate
  validate` in CI, which is the check by design (ADR-0013 §4)". The scope of
  that sentence is correct as a description of what *that method* refuses, and
  misleading the moment `accept` checks those three faces itself. It is
  tightened in the same CL.
- The threat model's own T-15 grade is not settled here. Whether the residual
  falls from High is a threat-model decision made against the shipped detector,
  and it belongs to the CL that ships it. **Taken 2026-08-24, in the threat
  model rather than here: T-15 stays High**, because the shipped control covers
  one of the three points a body can enter and the other two are live and
  unscanned. Re-graded on 2026-09-03 when
  [#329](https://github.com/theurian/theurian/issues/329) shipped the index-time
  control, and it comes back **High**: the build detects a landed secret in
  every text channel of the approved, in-ceiling corpus this deployment serves
  by default, but it runs on the far side of the disclosure boundary —
  `migrate apply`, not `index build`, is where the content becomes readable — so
  the count of *gates* is unmoved. T-15's entry carries the measurement.
- **Every `accept` copies the project's bodies through `$TMPDIR`, and this is an
  accepted residual.** The rehearsal (`cli/migration_pipeline.py`) stages every
  landed migration and every referenced body — including `confidential` and
  `restricted` bodies — into a `tempfile.TemporaryDirectory` (prefix
  `theurian-rehearsal-`) and rebuilds a fresh state database there, so the
  cleartext of governed content transits `$TMPDIR` for the life of the call.
  Measured safe on 2026-08-24 by the round-one security review: the directory is
  created `0o700` and owned by the process, and Python's `TemporaryDirectory`
  context removes it on every exit path — success, refusal, and config-error —
  leaving no `theurian-rehearsal-*` residue behind. It is accepted rather than
  redesigned on those two properties (mode `0700` plus guaranteed cleanup), with
  one environmental caveat recorded rather than fixed: `$TMPDIR` may sit on a
  different volume than the project — an encrypted checkout with a plaintext
  `/tmp` writes those bodies to the plaintext volume for the duration — the same
  shape of accepted environmental residual as [ADR-0028](0028-a-local-proposal-is-a-different-directory.md)'s
  `git clean -xdf` note, stated so an operator on that setup can weigh it.

## What this does not close

Five residues survive this decision, and none is repaired by it. They are
recorded here because a reader who takes decision 1 as "bodies are now tamper-
evident", or decision 2 as "a broken set can no longer happen", would be wrong
in a specific, reachable way each time.

1. **The integrity anchor lives in disposable derived state.**
   `.theurian/state/` is gitignored (ADR-0004), and a rebuild is a sanctioned
   operation the product's own remedies recommend. An operator who edits a
   body, recomputes its pin in the migration, and deletes `.theurian/state/`
   leaves the product with nothing to detect: every check the loader and the
   engine perform is satisfied by the rewritten pair. Only Git records what the
   migration used to say. This is pre-existing and unchanged by this CL —
   required pins raise the floor for *unedited* migrations, and do nothing
   against an edit that touches both halves.
2. **The writer population of `.theurian/migrations/` is closed, and that is
   what makes decision 2 worth having.** Measured 2026-08-23 against `main` @
   `68e8a0b`: `ProposalService` is the only writer — `self._paths.migrations`
   appears in `src/` at four sites in `proposal_service.py` (one destination
   computation, two resolutions, and the single `mkdir` that precedes the
   move), once in `cli/context.py` as a load, and twice in
   `application/setup_steps.py` as an `is_dir` and a `glob`. Population key:
   `grep -rn '\.migrations\b' packages/theurian-core/src/theurian/`. So after
   this change the only writer validates, and a file placed there by hand
   bypasses the pre-check but is caught by the loader on the next load — later
   than `accept` would have caught it, and before anything is applied.
3. **Concurrency between two acceptances is unaddressed, and this decision
   widens the window it opens**, as decision 2's third residue states.
4. **A validate-green, apply-red-forever set stays reachable by hand**, because
   only `accept` gained the replay. Decision 2's first residue.
5. **No CI job applies the committed corpus**
   ([#325](https://github.com/theurian/theurian/issues/325)) — a different
   class, cited and not absorbed.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **Bump `apiVersion` to `theurian.dev/v2` for the required pin** | There is no incompatible document population for a version boundary to separate: all 28 tracked and all 82 live migration documents already pin (measured 2026-08-23). The loader matches the version exactly, so a v2 const invalidates every one of them for nothing. ADR-0005 scopes the bump rule to adding an operation type, and this adds none. The general enum/operation-set compatibility policy is [#274](https://github.com/theurian/theurian/issues/274)'s question, not this ADR's. |
| **Require `expectedRevision` as well, as #316's original text proposed** | Re-scoped to [#324](https://github.com/theurian/theurian/issues/324). The field is `oneOf` a ULID or `null`, and an explicit `null` parses identically to an absent key, so required-and-nullable buys zero enforcement while costing an edit of all 82 applied migrations — 56 of them outside Git and outside CI. The semantics are already enforced by the engine at apply and by `ProposalService._check_expected_revision` at draft. |
| **Repair the unpinned population instead of tightening the schema** | There is nothing to repair. The measurement is the argument: zero unpinned documents exist. |
| **Fix #210 by editing applied migrations to add missing pins** | Editing an applied migration is the fault the product refuses by name: `MigrationChecksumMismatchError`, ADR-0005 rule 2 — "Same ID with a different checksum is a **fatal** error, never an auto-repair." The escape the remedy offers is deleting `.theurian/state/` and rebuilding, which discards FR-K5's tamper evidence for all 82 migrations at once in order to fix a field none of them is missing. |
| **Leave `unpinnedRevisions` in place, permanently empty** | A published field is a claim that its condition is reachable. An always-empty warning list tells a reader that unpinned revisions are a thing that happens and that this project has none right now, which after the tightening is false. Removing it is a break; keeping it is a lie with no expiry. |
| **Have `accept` re-implement the checks it needs, rather than calling the loader's** | This is the two-detector shape that has already cost this project a review round: two pieces of code deriving one fact independently agree until they do not, and the disagreement surfaces as "`accept` said yes and `apply` said no" with nothing to arbitrate. Calling the loader's own entry points makes the agreement structural. |
| **Validate at `accept` but stop short of the dry replay** | Measured: it leaves the racing face open. Two proposals each claiming an item's first revision are individually schema-valid and pass every statically decidable set guard; the set is validate-green and apply-red forever, and both proposals have been consumed. Stopping at stage 3 would close three faces and ship the fourth. |
| **Keep validation entirely in `migrate validate` and make `accept` non-destructive instead** | Leaving the proposal directory behind after a successful acceptance trades one failure for another: the next `accept` of the same proposal has to decide whether it has already landed, which is the best-effort, untrusted-input diagnosis ADR-0013's amendment describes and [#253](https://github.com/theurian/theurian/issues/253) hardened. It also does not stop a broken migration from reaching `.theurian/migrations/`, which is the part CI and every subsequent command then have to cope with. |
| **Take a secret-scanning dependency (`detect-secrets`, `gitleaks` as a library) instead of writing one** | It enlarges the dependency footprint — ADR-0014 pins every dependency exactly, and each is a supply-chain surface — to improve a control SECURITY.md already tells users not to rely on as their only one. The in-house detector's technique is already written, self-tested and tuned in this repository for SEC-5. |
| **Scan at `theurian ingest` or at `draft` instead of at `accept`** | Neither is the point SEC-11 names. `draft` produces a proposal a human will read, so a refusal there is advisory. `ingest` records a manifest of content that is already approved, so a scan there is a different control at a different point — a real one, and out of #316's scope; see the residues below. `accept` is the approval gate T-15 names, and it is the last place a body can be stopped before it becomes canonical. |
| **Publish a default for `security.secretScan` in the schema before the reader exists** | Already rejected once, and pinned: `test_schemas.py` asserts the key publishes no default, because a default states a policy nothing applies. That constraint lifts in the implementation commit and not before. |

## Compliance

**Everything owed at design time has landed in Milestone 7**, in #316's CL. The
list below names the test that discharges each item, so a reviewer can check it
against the suite rather than against this sentence. Every test path is relative
to `packages/theurian-core/`.

Landed in Milestone 7 with decision 1 (`contentSha256` required):

- The schema requirement, both directions:
  `tests/unit/test_schemas.py::test_every_upsert_revision_must_pin_the_body_it_names`
  reads `$defs.opUpsertRevision`'s `required` and goes RED if the entry is
  deleted, and `::test_a_revision_that_declares_no_pin_is_refused_by_the_published_schema`
  drives a document that omits the field. The loader half is
  `tests/unit/test_migration_loader_required_pin.py::test_a_revision_that_declares_no_body_pin_is_refused_at_load`,
  with `::test_the_same_revision_loads_once_it_pins_its_body` as the control
  that the refusal is the missing pin and not the fixture.
- The "zero edit cost" measurement is now a standing check rather than a
  measurement: `tests/unit/test_dogfood_corpus_governance.py::test_every_committed_migration_matches_the_published_migration_schema`
  walks the tracked corpus against the tightened schema, and
  `tests/unit/test_examples.py::test_the_example_loads_through_the_loader_the_product_itself_runs`
  puts the sample project through the loader the product runs rather than
  through a schema check alone.
- `unpinnedRevisions` is gone from `migrate validate`'s output, held by
  `tests/integration/test_cli_commands.py::test_validate_publishes_exactly_the_recorded_key_set`
  — a recorded key set, so a *re-added* field fails too — and
  `::test_the_human_output_carries_no_pin_warning_either` for the second
  channel. The four tests that read the field were deleted with it, and the
  key set is what replaced them: a removal held only by deletions is held by
  nothing.

Landed in Milestone 7 with decision 2 (`accept` validates first). All three of
#307's demonstrated faces are in `tests/integration/test_proposal_service.py`,
and each asserts the refusal **and** that the proposal directory is untouched:

- `::test_two_operations_naming_one_body_are_refused_with_the_proposal_intact`,
  `::test_a_pin_that_does_not_match_its_own_body_is_refused_with_the_proposal_intact`,
  and `::test_an_empty_content_file_is_refused_with_the_proposal_intact`.
- The racing face:
  `tests/integration/test_propose_cli.py::test_a_proposal_racing_another_onto_one_item_is_refused_and_the_rest_applies`
  — two proposals drafted before either acceptance, the second refused, and the
  set that remains applying cleanly. This is the one that goes RED if the dry
  replay is removed while stages 1–3 stay.
- The **hard condition**, pinned structurally rather than behaviourally:
  `tests/integration/test_propose_cli.py::test_the_accept_replay_and_migrate_apply_reach_one_apply_function`
  and `::test_the_accept_pre_check_reaches_the_loaders_own_entry_points_and_every_guard`.
  They walk the call graph in the shape
  `test_mcp_tools.py::test_no_registered_tool_can_reach_a_canonical_write` uses,
  so a re-implementation that agrees with the engine today still fails them.
- A fault in the landed set is not reported as the proposal's:
  `tests/integration/test_propose_cli.py::test_a_fault_in_the_landed_set_is_not_reported_as_this_proposals_fault`.
  **The delivered contract is narrower than the design text asked for, and
  deliberately.** This item was written as "a `ProposalError` with a remedy
  naming the landed file"; what shipped is `ApprovedSetUnusableError` — its own
  subclass, at **exit 4** rather than proposal-at-fault exit 1, because exit 1
  promises that re-drafting is the recovery and here it mints a duplicate for a
  fault the proposal does not have (#89) — carrying a remedy that points at
  `.theurian/migrations/` and tells the reader to correct *what the message
  names* there. Naming the landed file was unimplementable for one of the faces:
  a `RevisionConflictError` names an item and two revision ids, because the
  engine does not know which of the two migrations claiming that item is the
  wrong one. A remedy that is false for a case is worse than one that is
  general, so the remedy is general and the message does the naming.
- The replay writes nothing outside its throwaway target:
  `tests/integration/test_proposal_service.py::test_a_refused_acceptance_modifies_no_file_anywhere_in_the_project`
  is the whole-tree diff and content snapshot pointed at a refused `accept`, and
  `::test_the_replay_removes_the_throwaway_tree_it_staged_the_union_in` holds
  the other half — that the target it does write to does not survive the call.

Landed in Milestone 7 with decision 3 (SEC-11):

- Detector self-tests in `tests/unit/test_content_secrets.py`, including
  `::test_the_detector_can_fail` — a scan returning nothing for a planted secret
  is this class's failure mode — `::test_each_pattern_family_reports_its_own_shape`
  for the positive fixtures, and
  `::test_the_detector_ignores_a_string_a_knowledge_document_really_contains`
  for the prose that must not trip it.
- One test per policy value, at both layers. Reading:
  `tests/unit/test_project_config.py::test_a_project_with_no_config_file_blocks`,
  `::test_a_config_that_states_no_policy_blocks`,
  `::test_each_published_policy_is_read_back`, and
  `::test_a_bare_off_is_refused_with_the_quoting_cure` for the YAML 1.1 spelling
  a reader gets wrong by copying the enum. Behaviour:
  `tests/integration/test_proposal_secret_scan.py::test_a_body_carrying_a_secret_is_refused_by_default`,
  `::test_a_refused_acceptance_consumes_nothing`,
  `::test_warn_lands_the_body_and_reports_what_it_found`, and
  `::test_off_skips_the_scan_and_the_body_lands`.
- The new call site is recorded in
  `tests/unit/test_config_key_call_sites.py`, whose
  `::test_each_secret_scan_prose_surface_states_the_control_and_its_bound` now
  holds the prose flips as a standing check rather than a one-time edit. The
  schema default lifted with it, pinned by
  `tests/unit/test_schemas.py::test_the_secret_scan_policy_publishes_the_default_the_loader_applies`
  — which asserts the published default *equals* what `read_secret_scan_policy`
  applies, so the two cannot drift apart in either direction.

Landed in Milestone 7 with decision 3's amendment above — metadata-field
scanning ([#336](https://github.com/theurian/theurian/issues/336)), which this
ADR owed as a HIGH finding converted to a recorded design decision. All of these
are in `tests/integration/test_proposal_secret_scan.py`:

- The refusal, over every field a `propose`-drafted document carries:
  `::test_a_secret_in_the_migration_document_is_refused_by_default`, parametrized
  over twelve plants — title, description, author, owner, namespace, label,
  scope path, and five source-anchor strings — with
  `::test_every_planted_field_reaches_the_migration_document_and_is_detectable`
  as the guard that each plant actually reaches the document and is one the
  detector reports — without it a green parametrization could be testing nothing.
- The recovery property on the new input:
  `::test_a_refused_metadata_secret_leaves_the_proposal_intact` asserts the
  proposal directory is unchanged *and* that the document did not reach
  `.theurian/migrations/`, which an implementation scanning after the move would
  fail while satisfying the body-side test.
- The other two policies, and the message:
  `::test_warn_lands_the_proposal_and_names_the_metadata_it_found`,
  `::test_off_leaves_the_migration_document_unscanned_too` — the escape hatch has
  to cover the whole control or it is not one — and
  `::test_a_metadata_refusal_does_not_reproduce_the_secret_it_reports`.
  `::test_one_listing_bound_covers_the_body_and_the_metadata_together` holds that
  the `_MAX_NAMES_LISTED` cap applies to the pair rather than once per kind.
- The false positive that would make this the first control switched off:
  `::test_a_title_quoting_a_migration_filename_is_still_accepted_under_block`.
- The fields no `propose`-drafted document can carry, reached by sixteen
  hand-authored fixtures — one per allowlist field, across `deprecateItem`,
  `addRelation`, `addAlias`, `addEvidence`, `registerSpecification`,
  `changeOwner`, `createItem` and `upsertRevision`, since `propose` itself emits
  only the last two of the schema's fourteen operation types:
  `::test_a_secret_in_a_hand_authored_operation_is_refused_and_the_field_is_named`,
  `::test_a_refused_hand_authored_operation_consumes_nothing_and_is_not_quoted_back`,
  with `::test_a_hand_authored_operation_is_schema_valid_with_and_without_its_planted_secret`
  and `::test_a_hand_authored_operation_carries_no_finding_until_its_field_is_planted`
  as the guards that a refusal is the scan's and not the schema's.
- The population pin that stops the class reopening:
  `::test_every_drivable_allowlist_entry_has_a_fixture_that_reaches_it` reads the
  allowlists themselves and demands equality in both directions, so a name added
  to any of them goes RED until something drives it. Four entries are subtracted
  by name with the reason each is unreachable rather than merely untested:
  `metadata.tenantId` and `metadata.aclGroup`, which the engine refuses any value
  but `local` and `default` for ([#63](https://github.com/theurian/theurian/issues/63)),
  and `anchor.commitSha`/`anchor.blobSha`: a schema-valid document holds only
  `^[0-9a-f]{7,64}$` there, and the detector's class gate cannot fire on lower-case
  hex — no credential family it recognises can be spelled in it.

Landed in Milestone 7 with decision 3's artifact-level completion
([#349](https://github.com/theurian/theurian/issues/349)) — the metadata
amendment above owed the artifact level as its own face, and this discharges it.
The scan now reads everything an acceptance lands, not only what it parses, and
no finding location is built from scanned text. All in
`tests/integration/test_proposal_secret_scan.py`:

- **The migration file's raw bytes**, covering a YAML comment and every field as
  written:
  `::test_a_secret_in_a_yaml_comment_of_the_migration_is_refused_under_block` and
  `::test_a_secret_in_a_yaml_comment_is_reported_under_warn`, with
  `::test_a_secret_in_a_yaml_comment_is_invisible_to_the_parsed_field_scan` as the
  guard that the comment is a *new* channel and not already caught by the field
  walk.
- **The migration filename**:
  `::test_a_secret_in_the_migration_s_own_filename_is_refused_under_block`. Only
  two credential families can be spelled in a lower-case-kebab slug (`sk-` and
  `xox`), which is why the fixture is `sk-`-shaped; the less-restricted
  landed-path channel is not so limited, since a path component admits upper-case
  letters, digits and `_` (measured 2026-08-26: seven families fire by name, and
  a `google-api-key` shape is caught as `high-entropy-token`).
- **Each landed body path**, directory components included:
  `::test_a_secret_in_a_landed_body_leaf_is_refused_under_block`,
  `::test_a_secret_in_a_landed_body_s_directory_component_is_refused_under_block`,
  and
  `::test_a_landed_path_secret_the_migration_bytes_do_not_spell_is_still_refused`
  as the guard that the landed-path and byte channels are complementary rather
  than one a subset of the other.
- **The parsed `contentFile` value**, moved into the field walk:
  `::test_a_secret_shaped_content_file_is_refused_by_the_artifact_scan` and
  `::test_an_escaped_traversal_content_file_is_refused_under_block` — the HIGH the
  review of #349 reproduced, a credential both `..`-collapsed and YAML-escaped
  that only the parsed value catches.
- **No finding location reproduces the value it reports**:
  `::test_a_name_channel_refusal_never_reproduces_the_name_it_refuses`,
  `::test_a_name_channel_finding_locates_by_the_channel_rather_than_by_the_name`,
  `::test_a_body_content_finding_does_not_echo_a_credential_shaped_landed_path` and
  `::test_a_body_content_finding_never_spoofs_the_migration_filename_channel` — the
  last two close the review finding that the body-content channel located itself
  by the very landed path that was the credential.
- **One listing budget over every channel**, not one each:
  `::test_the_finding_budget_is_shared_across_channels_not_per_channel`; and the
  escape hatch covers the new channels too —
  `::test_off_touches_no_input_before_the_policy_is_read` and
  `::test_off_leaves_every_landed_artifact_unscanned`.
- **The false positive that would switch the control off**:
  `::test_an_ordinary_proposal_s_own_artifacts_carry_no_secret_shaped_name` — a
  generated proposal's ULID-shaped names carry no reported secret on any channel.

Still owed, with the issue that will satisfy it:

- ~~**Ingest-time and index-time secret scanning**~~
  ([#329](https://github.com/theurian/theurian/issues/329)) — **shipped
  2026-09-03, at the index build.** T-15's control is approval-time, so this was
  a second and distinct control, out of #316's scope. `theurian index build` now
  scans every body it indexes, with the source anchors and relation notes served
  beside it — every text channel of the approved, in-ceiling corpus this
  deployment serves by default, on every rebuild — so it reaches content that
  entered through `theurian ingest` or through a hand-placed migration. It
  reports and never refuses: the build runs after `migrate apply` has already
  made the content readable, so refusing to publish would deny ranking without
  un-disclosing anything. `theurian ingest` still runs no scan of its own, and
  needs none for a reason about **storage**: it stores no content at all — a
  manifest and an in-memory read — so at that point nothing is persisted for a
  scan to have missed. The reason this replaces, "everything its manifest names
  is read again by the build", was false in both directions: a specification and
  an orphaned body file are named by a manifest and never enter the canonical
  store, and a body reaches the store through routes no manifest names. Two
  parts of the store are outside the build's population and are recorded as
  residuals in the threat model and `SECURITY.md`: an unapproved body reachable
  through `includeUnapproved`, unscanned because reading a withheld row into a
  published count is the T-17 shape; and a superseded revision, unscanned while
  it remains in the store, which is why the remedy rotates before it supersedes.
- **Name hygiene in refusal messages**
  ([#360](https://github.com/theurian/theurian/issues/360)). The scan's own
  finding locations never reproduce the value (the `#349` entry above), but
  several refusal messages elsewhere on the accept path — and the `accept --json`
  `bodyFiles` success field — still echo an author's migration filename, id,
  `contentFile` or landed path verbatim. A different root cause from the scan,
  pre-existing, and disclosing no content the caller may not already read.
- **Draft-time scanning as an advisory, and `evidence.json` with it**
  ([#330](https://github.com/theurian/theurian/issues/330);
  [#361](https://github.com/theurian/theurian/issues/361) for the accept-lands-it
  face). Refusing at `draft` would tell an author sooner, but `accept` is the
  gate, so a draft-time scan is a convenience rather than a control.
  `evidence.json` belongs to the same item and not to the one above: `accept`
  moves the migration document and the bodies it names and leaves the rest of the
  proposal directory alone (ADR-0013 point 7), so the evidence file is committed
  with the pull request and never becomes part of an approved revision — a control
  over it is a control over something this gate does not land, which is the face
  #361 tracks specifically.
- **Concurrency between two `accept` invocations** (decision 2's third
  residue), which belongs with the write path's single-writer work
  ([ADR-0018](0018-single-writer-synchronous-in-m1.md)). The accept path's file
  moves are not under the advisory lock ADR-0018 point 2 describes, and this
  decision lengthens the examine-to-move window rather than shortening it.
- **A CI job that applies the committed corpus**, which is what would turn the
  apply-time invariants into something CI checks against the shipped
  migrations. [#325](https://github.com/theurian/theurian/issues/325); a
  different class from this decision, and not closed by it.
- **The integrity residue in decision 1** — a body edit plus a pin recompute
  plus a state wipe is undetectable by the product. Nothing here will close it;
  Git is the record, and saying so is the control.
