# ADR-0032: The write-intent MCP tool surface — two tools onto one `ProposalService`, and `writeTools` flips with the first registration

- Status: proposed
- Date: 2026-09-12
- Deciders: Theurian maintainers
- Requirements: FR-I3, SEC-11, SEC-12, SEC-17, INV-7, INV-8, T-3, T-12
- Situates against [ADR-0013](0013-ai-writes-produce-proposals.md) (the tools
  this ADR opens are the ones it named), [ADR-0026](0026-evidence-plane-not-control-plane.md)
  (capability honesty — a flag may not be flipped ahead of the feature),
  [ADR-0027](0027-accept-validates-before-it-moves.md) and
  [ADR-0028](0028-a-local-proposal-is-a-different-directory.md) (the accept path
  and the local location these tools inherit), [ADR-0031](0031-mcp-input-is-schema-validated-in-middleware.md)
  (SEC-12, this surface's precondition) and
  [ADR-0034](0034-migrate-apply-enforces-the-merge.md) (T-15, the other
  precondition)

**This ADR records a decision and ships no code.** No tool registers, no schema
is written, no flag moves, no test lands; the diff is confined to `docs/`. What
each implementation slice owes is named in *Compliance*.

**Every repository fact below was measured on 2026-09-12 against `be977ea7`**,
which is reachable from `origin/main`. Where a fact is a *population*, the key is
stated beside the number so a reader can attack the key and not only the count.

## Context

ADR-0013 settled the direction — *AI proposes, Git reviews, humans approve* — and
named three write-intent tools: `knowledge.proposeChange`,
`knowledge.generateMigrationDraft` and `review.generateKnowledgeCandidate`. Three
milestones later the direction holds and none of the tools exists.

What *does* exist is the whole machine behind them. `theurian propose` drafts a
proposal today through `ProposalService.draft(ProposalRequest)`
(`application/proposal_service.py`), and everything ADR-0013 point 2 describes —
the schema-valid migration, the per-revision body path, the evidence file — is
that service's output. `application/proposal_service.py`'s secret-scan docstring
already names the arrival this ADR designs, in its own words: the policy is read
inside the service rather than injected because "Milestone 7's write-intent MCP
tools are a second root arriving. A security control that a caller can omit by
omission is not a control."

So the design question is not *what a write-intent tool does*. It is: **which
tools, taking what over the wire, and at what moment does the server stop saying
it has none.**

**The last question is the one with a measurable cost.** `writeTools: false` is
not a note in a document; it is a per-build assertion published on a security
surface and pinned in several places. The population, with its key:

```console
$ git grep -n "writeTools" be977ea7 | wc -l
      18
$ git grep -l "writeTools" be977ea7 | wc -l
      10
```

**Exclusions, measured rather than asserted** — `git grep -n "writeTools"
be977ea7 -- <path>` for each: `.claude/` drops **0**, `.theurian/` drops **0**,
`docs/work-logs/` drops **0**. The unfiltered key is therefore the same
population as a filtered one at this frame, which is why no pathspec is applied.

The ten files are not all prose. **Five** of them carry the flag in a build
artifact rather than in a sentence, and **two** of those five fail a build when
the flag's *value* moves. The difference matters to slice B4, so it is measured
rather than asserted. Flipping `"writeTools": False` to `True` in
`mcp/tools.py`, in a throwaway clone:

```console
# control
$ python -m pytest .../test_mcp_tools.py .../test_wire_contract.py .../test_schemas.py -q
361 passed, 1 xfailed in 58.28s

# mutant: "writeTools": False -> True
$ python -m pytest .../test_mcp_tools.py .../test_wire_contract.py .../test_schemas.py -q
FAILED .../test_mcp_tools.py::test_capabilities_report_what_is_and_is_not_built
1 failed, 360 passed, 1 xfailed in 58.28s

# control
$ python -m pytest tests/e2e/test_daemon_single_instance.py -q -k capabilities
1 passed, 11 deselected in 3.72s

# mutant
$ python -m pytest tests/e2e/test_daemon_single_instance.py -q -k capabilities
FAILED tests/e2e/test_daemon_single_instance.py::test_capabilities_report_no_write_tools
1 failed, 11 deselected in 2.59s
```

| File | What it holds | Moves on a value flip? |
| :-- | :-- | :-- |
| `packages/theurian-core/src/theurian/mcp/tools.py` | the value itself | — it *is* the value |
| `packages/theurian-core/tests/integration/test_mcp_tools.py` | `assert ...["writeTools"] is False`, plus the pinned capability-**key** set | **Yes**, on the value assertion. The key-set pin does not move |
| `tests/e2e/test_daemon_single_instance.py` | `assert capabilities["capabilities"]["writeTools"] is False` over a real client | **Yes** |
| `packages/theurian-core/tests/integration/test_wire_contract.py` | the conformance negative, `{"writeTools": "false"}` — a **string** where a boolean belongs | **No.** It is a type negative |
| `schemas/mcp/system-capabilities-response.schema.json` | the property and its description | **No.** A schema constrains the type, not the value |

A second, narrower key reaches the capabilities *note*, which does not contain
the flag's name:

```console
$ git grep -n "No write-intent tool exists" be977ea7 | wc -l
       3
```

— one in `mcp/tools.py`'s capability block and two in the test that pins its
wording, including the inversion the assertion message spells out.

**The served corpus is outside both keys, and that is measured rather than
assumed.** `.theurian/` drops 0 for the flag key; the corpus's
`ai-writes-produce-proposals` twins carry ADR-0013's *design* statement about
write-intent tools, which registering one does not falsify. Nothing here creates
a re-seed obligation.

## Decision

### 1. `knowledge.proposeChange` is the content path, and it maps 1:1 onto `ProposalService.draft`

The tool takes a proposed change and returns what was written. It adds no
behaviour of its own: the service mints the identifiers, chooses the paths,
validates the migration against the published schema and refuses an unguarded
update. **A second implementation of any of that is the defect this decision
exists to prevent** — ADR-0027 records what it costs when two procedures
disagree about the same set.

The wire input carries `projectId` (every project-scoped tool requires an
explicit one; the server's own instructions say there is no default project)
plus the fields `ProposalRequest` declares:

| Wire field | Maps to | Note |
| :-- | :-- | :-- |
| `itemId`, `title`, `kind`, `owner`, `author`, `description` | the same-named `ProposalRequest` fields | each refused empty at construction |
| `body` | `ProposalRequest.body` | **inline text** — decision 2 |
| `contentType` | `ProposalRequest.content_type` | a closed enum: `text/markdown`, `application/json`, `application/yaml`, the three `MediaType` constants `cli/propose_commands.py`'s `_CONTENT_TYPES` maps its five accepted suffixes onto |
| `evidence` (`agentId`, `taskId`, `model`, `reasoning`) | `Evidence` | decision 4 |
| `sourceAnchors[]` | **both** `Evidence.anchors` and `ProposalRequest.source_anchors` | **one wire field fills two domain fields**, which is what `propose_commands.py`'s `_request` already does from one `--source-uri`. They stay separate fields in the domain because they have separate readers and separate requirements: the anchors in `evidence.json` are read by the humans reviewing the pull request and by no code path, while `metadata.sourceAnchors` is what `migrate apply` enforces for INV-8. The wire does not reproduce the split, because a caller has one answer to "where did this come from" |
| `labels[]`, `scopePaths[]`, `namespace?` | the same-named fields | `namespace` absent defaults to the item id's own |
| `trustLevel?`, `sensitivity?` | the same-named fields | **absent means "not stated"**, never a stamped default: `ProposalRequest` records that writing `unverified`/`internal` into every draft "would assert a judgement the caller did not make (#249)" |
| `expectedRevision?` | `ProposalRequest.expected_revision` | the optimistic-concurrency gate (ADR-0006): required for an update, refused on a first revision — `_check_expected_revision` refuses both directions with a remedy |
| `local?` | `draft(local=...)` | ADR-0028 routing; it is a parameter of the *act*, not a field of the reviewed text |

The output mirrors the CLI's `_drafted_payload` (`cli/propose_commands.py`):
`proposalId`, `proposalDirectory`, `migrationId`, `migrationFile`,
`revisionId`, `expectedRevision`, `bodyFile`, `evidenceFile`, `contentFile`,
`contentSha256`, `bodyDestination` and the next-steps list. **One payload shape
for two front ends** is the point: a human reading a CLI result and an agent
reading a tool result are looking at the same proposal, and a second shape would
be a second thing to keep true.

**`contentType` is explicit on the wire because the CLI's derivation has no
input here.** `_read_body` reads the media type off the body file's suffix and
refuses a suffix it does not know. There is no file on this path (decision 2),
so the caller states the type instead of a filename implying it.

### 2. The body is inline text, and a file path is refused by construction

`knowledge.proposeChange` takes the body as a string. It does **not** take a
path, a URI, or any other reference the daemon would have to dereference.

This is a security decision and not an ergonomic one. The daemon runs as the
operator, and a tool that accepted a path would be a read primitive over the
caller's filesystem reachable by any MCP client that can talk to it — the
caller's private keys, the caller's `~/.claude.json`, anything the operator can
read — laundered into a proposal directory and, by ADR-0013 point 7, into a
pull request. That exact shape has been reproduced on this project once already:
ADR-0013's Milestone 7 amendment records a class of accept-path defect "one of
which read an out-of-project secret (`~/.claude.json`, `~/.kube/config`) into a
git-tracked file, an exfiltration channel", caught in review and never shipped.

Refusing the shape is cheaper than containing it. A containment check has to be
right about symlinks, case folding, Unicode normalisation and TOCTOU on every
platform; an absent parameter has nothing to be right about.

### 3. `knowledge.generateMigrationDraft` is the operations path, and v1 carries ten of the fourteen operation kinds

`knowledge.proposeChange` covers exactly one shape of change: a body and the
revision that carries it. The other twelve things a migration can say — a
deprecation, an alias, an owner change, a relation, a specification registration
— have no tool at all, and the generator cannot express them: `_migration_document`
emits precisely `createItem` and `upsertRevision`
(`application/proposal_service.py`), two of the fourteen members of
`OperationKind` (`domain/migration.py`, counted from the enum body).

So the second tool takes a **migration document** and lands it as a proposal.
Its v1 operation set is the closed `OperationKind` set **minus `createItem`,
`upsertRevision`, `changeSensitivity` and `restoreItem`** — ten of the
fourteen. The first two are refused with a remedy naming
`knowledge.proposeChange`; the other two are refused for their own reasons,
below.

**The set is chosen on an axis, not on a count, and the axis is *what a wrong
proposal moves*.** Classifying all fourteen:

| Axis | Operations | In v1? |
| :-- | :-- | :-- |
| **Moves content** — a body, a revision pointer, the digest pin | `createItem`, `upsertRevision` | **No.** The content path owns them (below) |
| **Moves an enforced read control** — `may_disclose`, the deployment's sensitivity ceiling (#119, ADR-0025) | `changeSensitivity` | **No.** Pulled; see below |
| **Narrows an enforced surfacing control** — `may_surface`, the status gate, in the withholding direction | `deprecateItem` | **Yes.** It can only take an item *out* of the surfaceable set |
| **Widens that same control** — `may_surface`, in the readmitting direction | `restoreItem` | **No.** Pulled; see below |
| **Moves addressing** — a second key that resolves to an item (T-21) | `addAlias`, `removeAlias` | **Yes**, with the refusal's *timing* named below |
| **Moves governance metadata** — ownership, relations, specification lifecycle, evidence links | `addRelation`, `removeRelation`, `changeOwner`, `registerSpecification`, `supersedeSpecification`, `addEvidence`, `removeEvidence` | **Yes** |

**`changeSensitivity` is pulled because it is the one operation whose reviewer
cannot see the thing it moves.** Sensitivity stopped being a write-time refusal
and became an enforced read control when #119 landed (`may_disclose`,
`domain/enums.py`; ADR-0025) — a declassification proposal therefore widens who
may read an item, and *how much* it widens depends on the deployment's
sensitivity ceiling, which is not in the migration and is deliberately not
published on `system.capabilities` (`mcp/tools.py` records that a flag may be
published there while a ceiling may not). A human reading the pull request sees
`confidential → internal` and cannot see what that admits. That is a decision
that deserves its own recorded justification, and this ADR does not write one;
it takes the operation out of v1 instead, additively recoverable later.

`docs/roadmap.md`'s Phase B risks row is the second half of the reason: "review
text is untrusted content, and turning it into a candidate is precisely the path
by which an injected instruction becomes a knowledge candidate." An injected
instruction that reaches a declassification is the worst member of that family,
and v1 does not carry it.

**`deprecateItem` and `restoreItem` both move the status gate, and only one of
them stays. The distinction is which direction it moves it in.**

`deprecateItem` stays. It sets `DEPRECATED`
(`application/migration_engine.py:485`), a status outside `SURFACEABLE_STATUSES`
under both values of `includeUnapproved` (`domain/enums.py`), so the worst a
wrong proposal does is withhold an item that should have stayed visible — a
reviewer meets that as a missing answer, not as a disclosure. A reviewer reading
the migration also sees the whole of what it does: the statuses are in the
document, the set of surfaceable ones is in the domain, and nothing about the
deployment changes the answer.

**`restoreItem` is pulled, because it readmits from any status, and the wire
shape lets it say nothing about why.** Three measurements, each with the key
that settles it.

**One — there is no transition check.** `restoreItem` sets `APPROVED` from
whatever status the item currently holds:

```python
# application/migration_engine.py:497-498
case RestoreItem():
    self._set_status(writer, project_id, operation.item_id, KnowledgeStatus.APPROVED)
```

`_set_status` (`application/migration_engine.py:676-686`) looks the item up,
raises on an unknown id, and writes `item.with_status(status)`. It takes the
target status as an argument and never reads the current one. The walk that
would find a transition rule elsewhere returns nothing:

```console
$ git grep -c "DEPRECATED" -- packages/theurian-core/src
packages/theurian-core/src/theurian/application/migration_alias_guards.py:2
packages/theurian-core/src/theurian/application/migration_engine.py:1
packages/theurian-core/src/theurian/domain/enums.py:1
```

Four lines in three files, read one by one: the alias guard's own status
projection (`:75`) and its deprecated-is-exempt test (`:137`), the
`deprecateItem` write above (`:485`), and the enum member (`domain/enums.py:29`).
Not one of them asks what a restore is restoring *from*. **The key's limit**: it
finds only the spelling `DEPRECATED`, so a transition table written in some other
vocabulary would be invisible to it — which is why the engine's own
`case RestoreItem()` arm is quoted above rather than inferred from the absence.

**Two — `REJECTED` is inside that reach, and it is the one status no flag
surfaces.** `domain/enums.py:206-219` says so in its own words: `REJECTED` "is
deliberately absent and there is no flag that adds it. A rejected revision is
one the team decided must *not* be followed, and it is also where a secret that
caused the rejection still lives." A `restoreItem` over a rejected item is
therefore the one operation in the set that can republish content the read gate
is built never to serve, and it does it in the widening direction.

**Three — the wire shape is weaker than that of the operation this ADR had
already pulled, so admitting one while pulling the other had the contrast the
wrong way round.** `opRestoreItem`
(`schemas/migrations/migration.schema.json:253-262`) requires exactly `op` and
`itemId`; its `reason` is an **optional** property, so a schema-valid restore can
carry no rationale at all. `opChangeSensitivity` (`:305-318`) requires `op`,
`itemId`, `sensitivity` **and** `reason`, and the schema's own description of
that field states the principle: "Reclassification changes who may read the
content, so the rationale is part of the record." The two operations move who
may read an item by different routes, and the one with the weaker record is the
one an earlier draft of this ADR admitted.

**The readmission path is owed, not closed.** Widening is additive — the third
of the three reasons for the content split, below, says why — and a later slice
that admits `restoreItem` has to bring three things with it:

1. **A transition-aware refusal** — restore admitted only over a `DEPRECATED`
   item. That is already the operation's *documented* meaning:
   `docs/protocol/migrations.md:86` gives `restoreItem` as "Undo a
   deprecation", which the engine does not enforce. That mismatch is
   pre-existing, is not created here, and is recorded on
   [#272](https://github.com/theurian/theurian/issues/272) — the
   status-transition-graph ADR candidate that owns the enforcement — in
   [its 2026-09-12 comment](https://github.com/theurian/theurian/issues/272#issuecomment-5646294018).
2. **A wire-required `reason`**, in the shape `opChangeSensitivity` already
   uses, so the human reading the pull request is told why an item is coming
   back.
3. **A named seat.** Apply-time enforcement of the status transition graph is
   `docs/roadmap.md`'s Phase D item ① and its ADR candidate #1 ("Enforcing the
   status transition graph — define the legal transitions and check them in the
   migration engine"). A tool-side refusal is narrower than that and does not
   substitute for it; whichever lands first, the other is still owed.

**`addAlias` carries the T-21 shape, and what is owed about it is *when* the
refusal arrives, not whether.** An alias key equal to a live item id let a read
gate that resolves the alias evaluate the wrong item's authority — closed in
0.1.0.dev6 by `refuse_alias_item_id_collision`
(`application/migration_alias_guards.py`), a whole-set static guard that runs at
`migrate validate`, at `migrate apply` and inside `MigrationEngine.apply`, and
that `propose accept` also runs as stage 3 of its union rehearsal
(`_refuse_unless_the_union_applies`, ADR-0027 decision 2).

`draft` does not run it: it validates the document against the schema and
nothing else. So an agent's colliding `addAlias` is written, reviewed by a human
and then refused at `accept` — the shape ADR-0013's INV-8 note names, where a
document "is schema-valid and then exits 4" after a person has already spent the
review. Slice B4 owes whether the operations path runs the whole-set guards at
generation as well. It is named here so it is a decision rather than a
discovery — **and it is a disclosure question as well as a cost one**, which an
earlier draft of this ADR got wrong by calling it "a cost question … and not a
safety one".

**The guard's refusal is an existence-and-status oracle, measured in the
refusal's own construction.** `AliasItemCollisionError`
(`domain/errors.py:197-240`) renders both the colliding item id and its status —
the first two of the message's four lines, verbatim:

```python
# domain/errors.py:236-237
f"{migration_id}: addAlias {alias} -> {alias_target} collides with knowledge item "
f"{alias} (status {item_status}). An alias key and an item id must be distinct: a "
```

and `item_status` is computed over the **unfiltered** landed set.
`_alias_item_collisions` (`application/migration_alias_guards.py:124-144`) reads
`_final_item_statuses(migration_set)` — a fold over every operation in
`MigrationSet.ordered(...)` with no status filter and no sensitivity filter — and
yields the status straight into the error. So running the guard at generation
would answer, for an alias key the caller chose, *does an item with this id
exist, and what status is it in* — including `rejected`, the status
`domain/enums.py:206-219` records as reachable through no flag. The guard's own
docstring already names the dangerous case ("a `rejected` item is the dangerous
case"); what is new here is that the refusal **publishes** it.

That does not settle which way slice B4 should go: refusing late is the cost
ADR-0013's INV-8 note prices, and refusing early is the oracle above. What it
settles is that the decision is bound by decision 6's rule and cannot be made on
cost alone.

Three reasons for the content split, in order of weight:

1. **The operations path must not become a second body pipeline.** A
   `upsertRevision` accepted here would need `contentFile`, the digest pin, the
   per-revision body path and the replacement guard — every mechanism
   ADR-0013's amendment and ADR-0027 record as hard-won — reimplemented against
   a hand-authored document. Two pipelines that must agree about the same set is
   the defect shape, not the feature.
2. **The split is legible to a caller.** *Content goes to `proposeChange`;
   everything else except `changeSensitivity` and `restoreItem` goes to
   `generateMigrationDraft`* is a rule an agent can follow without reading this
   ADR.
3. **Widening is additive.** Admitting `upsertRevision`, `changeSensitivity` or
   `restoreItem` later adds a capability; no client breaks. Narrowing later
   would break clients, which is why the v1 set is the conservative one.

**Validation reuses the existing entry point, reached the way the service
already reaches it.** `validate_migration_document`
(`infrastructure/filesystem/migration_loader.py`) takes a parsed mapping rather
than a path, and its docstring already names this use: "a generator can refuse
to write a migration it has just built wrong rather than leaving one on disk for
a reviewer to discover. ADR-0013 point 3 is the reason it belongs at generation."
It also already carries the bounds an untrusted document needs
(`MAX_DOCUMENT_NESTING`, `MAX_DOCUMENT_NODES`, `MAX_DOCUMENT_RENDERED_CHARS`,
recorded there for #291 and #245).

**The route to it is the injected `MigrationDocumentValidator`, never a direct
infrastructure import.** `ProposalService` already takes `validate:
MigrationDocumentValidator` in its constructor, and the constant's own docstring
says why: "Supplied by the composition root, because locating and reading
`schemas/` is an adapter's job (ADR-0003)." The CLI fills it with
`lambda document: validate_migration_document(document, schemas)`
(`cli/propose_commands.py`); the MCP composition root fills the same parameter.
A draft-from-document entry that imported the loader would put an adapter import
in the application layer and give this path a *second* validator the accept-time
rehearsal is not holding — `_refuse_unless_the_union_applies` runs "the same
`MigrationDocumentValidator` `draft` calls" precisely so a proposal this build
generated cannot fail its own acceptance.

**Two guarantees the CLI has are the CLI's, and the wire path does not inherit
them.** Both live in `cli/propose_commands.py`, above the service:

| CLI guarantee | Where it lives | What owes the wire equivalent |
| :-- | :-- | :-- |
| The body is capped at `MAX_SOURCE_FILE_BYTES` (8 MiB) | `_read_body`, applied to the body **file**'s `stat().st_size` — there is no file on this path (decision 2), so nothing applies it | ADR-0031's published input schema (slice B2) carries an explicit `maxLength` on `body`, and slice B4 drives it — in a unit that is still open, since `maxLength` counts code points and this cap is in bytes, so the wire bound must either be the sound over-approximation plus a byte check at landing or an explicitly recorded different unit ([#691](https://github.com/theurian/theurian/issues/691)) |
| `--label authored-in-theurian` beside `--authored-here` is deduplicated | `_merge_labels`, which exists because `revisionMetadata.labels` is `uniqueItems` in the migration schema and a duplicate would fail the generator's own validation | The published input schema sets `uniqueItems` on `labels[]`, so the refusal is a schema refusal with a key path rather than a validation failure over a document the caller cannot see |

Neither is a defect in the CLI; both are the consequence of the service taking
already-read text. What would be a defect is assuming they travel.

**What lands is a proposal directory, through a draft-from-document service
entry owed to slice B4.** No such entry exists today: `ProposalService.draft`
takes a `ProposalRequest` and builds the document itself. Writing that entry —
so that this path lands through the same guards, the same identifier minting and
the same secret-scan-at-accept posture — is implementation work this ADR names
and does not do.

### 4. Evidence requiredness is preserved over the wire, and it is already enforced twice

`agentId`, `taskId`, `model` and `reasoning` are required on every write-intent
call, and the enforcement an MCP tool gets is the enforcement that already
exists rather than a third copy:

- `Evidence.__post_init__` calls `require_evidence` (`domain/proposal.py`),
  which refuses an empty `model` and an empty `reasoning`.
- `AgentId` and `TaskId` (`domain/identifiers.py`) refuse an empty or
  over-length value at construction, so an absent agent identity cannot be
  spelled as `""`.
- `ProposalRequest.__post_init__` calls `require_evidence` **again**, and
  `require_evidence`'s own docstring says why the second call is not redundant:
  it makes "rejected at generation" a property of the generation *path* rather
  than of one constructor, "so that a caller holding an `Evidence` built by any
  other route still cannot package a proposal out of it."

A tool that constructs an `Evidence` therefore inherits ADR-0013 point 5 without
asking for it, which is the property this decision records rather than adds.

**What is enforced is presence, not truth, and the heading should not be read
otherwise.** All four values are caller-asserted strings. The daemon does not
authenticate agents — `tool-context.schema.json` says so in its own words about
the same fields: "Provenance only. Theurian does not authenticate agents; this
labels which run produced a proposal." What the two `require_evidence` calls buy
is that a proposal cannot be packaged with the provenance *missing*; an agent
that lies about its identity is outside this control and outside this ADR.

**`agentId` and `taskId` have two wire addresses, and the precedence is decided
here rather than left to collide.** `tool-context.schema.json` types optional
top-level `agentId` and `taskId` on every project-scoped call, and the `evidence`
object above carries the same two names. For the write-intent tools:

> The `evidence` object is **authoritative**. `agentId` and `taskId` inside it
> are required, and they are what reaches `Evidence`. The top-level
> tool-context fields remain optional ambient provenance and are never read into
> a proposal. When both are present and **disagree**, the call is refused rather
> than resolved by precedence — the two spellings would otherwise record one
> identity in `evidence.json` and a different one in whatever observes the
> request.

The refusal is expressed in the published input schema where the relation is
expressible, and in the handler where it is not: a cross-field equality between
a `$ref`'d context property and a nested one is the kind of constraint a schema
can state only awkwardly, and ADR-0031 decision 6's direction is that the schema
may be tighter on a *value domain* — not that every relation must live in it.
Slice B2 settles which half carries it; slice B4 drives the refusal either way.

**INV-8 stands on the same footing.** `ProposalRequest.__post_init__` refuses a
request with no source anchor and no `authored-in-theurian` label, with the
reason recorded in place: without it, a revision "is schema-valid and then exits
4 with 'has no source anchor'" after a human has already reviewed and merged it.

### 5. `writeTools: true` lands in the same commit that registers the first write-intent tool

Not a slice earlier, and not a slice later.

**The argument is that `writeTools: false` is a claim, not a label.** It is
published on `system.capabilities` — the surface `docs/protocol/mcp-tools.md`
calls "the runtime boundary for clients" — and it is asserted across the ten
files the *Context* key returns. `docs/index.md` states what a reader takes from
it: "No MCP write tool can directly create approved knowledge. Every tool a
client can call today is read-only, and `system.capabilities` reports
`writeTools: false`." Registering a write-intent tool while that value says
`false` ships a false answer to a security question, which this project's own
severity rubric grades as a published claim that misleads a security decision.

**ADR-0026 already settled the direction and this is the mirror case.** That ADR
records the flags as "evidence of *capability honesty*: a flag cannot be flipped
ahead of the feature it advertises", and names `reviewFindings: true` as "the
case in the other direction — a flag that moved *with* the feature it
advertises". This decision is that same pairing for `writeTools`: the flag moves
with the feature, in one commit, in both directions.

**ADR-0030 chose the opposite trade deliberately, and recorded what it cost.**
There, the fetch path shipped in slice 1 while `reviewIngestion` stayed `false`
until slice 3, and the ADR records the bounded residual in its own words: "for
those two slices **the machine-readable security statement reads `false` while a
fetch path ships**, which is a wrong answer to a security question even when no
feature is lost by it." What bounded it there was that no tool was callable, so
a client acting on the `false` lost no capability it could have had. **That
bound does not exist here**, because the thing being shipped *is* the callable
tool — so the residual ADR-0030 could price is one this surface cannot.

`review.generateKnowledgeCandidate` (ADR-0033) then arrives additively at slice
B5 with no flag change, because `writeTools` answers *whether any write-intent
tool exists* and not *how many*.

### 6. A refusal about an item the caller may not see is indistinguishable from a refusal about one that does not exist

This surface's refusals answer questions about items. Some of those items the
caller may not read — a `rejected` item, an item above the deployment's
sensitivity ceiling (#119, ADR-0025) — and a refusal that distinguishes
*withheld* from *absent* is a disclosure channel, which is the family
[ADR-0033](0033-knowledge-candidate-generation.md) decision 5 designs the same
shape for on its sibling surface. **The bind is over the surface, not over one
tool**, because both tools take item ids and both have refusals that are
computed over unfiltered sets — `proposeChange` through `_check_expected_revision`
(below) and `generateMigrationDraft` through the alias guard's
`AliasItemCollisionError` (decision 3, if the guard runs at generation):

> For an item outside the caller's view, **every write-intent tool on this
> surface** refuses **indistinguishably** from an item that does not exist. Such
> a refusal carries **no current-revision id** and **no status**.

The status half is not a generalisation for its own sake: it is the one value
the alias guard's refusal publishes and `_check_expected_revision`'s does not,
and a bind written only against the revision id would have left it out.

**The authority is the existing gate pair, not a second comparison.** "Outside
the caller's view" means what `may_surface` and `may_disclose` (`domain/enums.py`)
say it means, and the caller-scoped lookup this decision owes *consults* them
rather than reimplementing the test. The reason is recorded in
`may_surface`'s own docstring — the index builder used to inline the two
comparisons "which is one copy of a security rule too many" — and it is enforced
by the equality pins named in *Compliance*, which fail on an addition as well as
a removal.

**The mechanism this binds is already in the tree, and it is named rather than
inferred.** `expectedRevision` (decision 1) puts `_check_expected_revision`
(`application/proposal_service.py`) on the wire, and its three refusals publish
more than a rejection:

| Refusal | What it publishes |
| :-- | :-- |
| `<itemId> does not exist yet, so its first revision cannot replace <rev>` | non-existence |
| `<itemId> already exists at revision <rev>; an update must state which revision it replaces` (:960) | existence **and** the current revision id |
| `<itemId> is at revision <rev>, but --expected-revision names <rev>` | existence **and** the current revision id |

The current revision comes from the injected `CurrentRevisionLookup`, and the
CLI fills it with `current_revision_in(migrations, item_id)`
(`cli/propose_commands.py`), which folds every `UpsertRevision` in the
**unfiltered** loaded migration set — no status filter, no sensitivity filter
(`domain/migration.py`). On the CLI that is correct: the caller is the operator,
holding the repository the set was read from. On the wire it is an oracle over
items the read surface withholds.

**The seat of the fix is the injection point, not a string change.**
`CurrentRevisionLookup`'s own docstring already anticipates it — "Milestone 7's
MCP tools supply their own view of the same state" — so the wire path supplies a
lookup scoped to what the caller may see, and the refusal shape follows from the
lookup rather than from remembering to redact a message.

**For an in-view item the revision id stays in the refusal, deliberately.** It
is the whole remedy of an optimistic-concurrency failure (ADR-0006): a caller
told only "that is stale" has to go and find the right value, and the message
that names it is the difference between one call and three. The bind is scoped
to out-of-view items, and that scoping is the decision, not an oversight. The
same reading applies to the alias guard's status: an author refused over an item
they may read needs to know it is `rejected` in order to fix the document.

### 7. Preconditions, in order

1. **ADR-0031's input validation (slice B2)** lands before any write-intent tool
   registers. `docs/roadmap.md`'s Phase B row already binds this — "SEC-12 …
   becomes mandatory the moment a write-intent tool opens" — and the reason is
   the direct one: a write-intent tool is the first surface where a dropped key
   means a proposal a human reviews without a constraint the agent believed it
   had set.
2. **ADR-0034's merge enforcement (slice B3)** lands before any write-intent
   tool registers. The same roadmap row states why: "**T-15's 'nothing enforces
   the merge' residual is a Phase B precondition, not a background fact**:
   opening a protocol-level write path multiplies the callers who can put a file
   in `.theurian/migrations/`, and `migrate apply` does not ask whether it was
   committed."
3. **The tools and the flag (slice B4)**, in the order decision 5 fixes.

### 8. The standing guarantees extend to the new tools rather than being weakened for them

- **Registration goes through `_tool`.** The seam refuses a callable that defers
  its body — a coroutine, an async generator, a generator — at registration
  time, because `_forwarding`'s `except` arm "only sees exceptions raised
  *while it is on the stack*" and the conversion "silently stops applying, and
  only under mcp >= 2.1" (`mcp/tools.py`, `_tool`). A write-intent tool that
  wants to be async is a design change with its own reasoning, not a decorator
  choice.
- **The bytecode walk enumerates them automatically, and that is less than it
  sounds — so the gap is stated and slice B4 owes the control that closes it.**
  `tests/integration/test_mcp_tools.py::test_no_registered_tool_can_reach_a_canonical_write`
  enumerates the **built** server rather than a list, so a new tool joins the
  sweep by existing. What the sweep then asks is narrower than
  "reaches approved state":

  - **Its reach is one level.** `_referenced_names` walks the registered
    callable's own code-object chain — the `_forwarding` wrapper, its
    `__wrapped__`, and nested code in `co_consts`. It does not enter the body of
    anything the tool *calls*, so a write performed inside a collaborator is
    invisible to it.
  - **Its forbidden set is the canonical-store movers.** `WRITE_GATEWAYS`
    (`SqliteWriter`, `write_transaction`) together with the method names that
    exist on `SqliteWriter` and not on `SqliteCanonicalStore` — **17** names,
    computed live by `_mutating_method_names`. `draft`, `accept` and `_commit`
    are in none of them.

  So a tool that closes over a `ProposalService` and calls `accept()` passes the
  sweep GREEN, while `_commit` writes into `.theurian/migrations/` and
  `.theurian/knowledge/` — which is approved state in everything but the merge.
  The sweep is a real control over *canonical* writes and it does not, by
  itself, hold "no tool reaches approved state" over this surface.

  **`docs/security/threat-model.md`'s T-12 control rests on that reading**, in
  its own words: "no MCP tool reaches a write path for approved state — not
  behind a flag, not behind a permission. Write-intent tools emit proposal
  files. A test enumerates every registered tool and asserts none reaches a
  canonical write." The first sentence is stronger than the third, and slice B4
  is where the gap between them has to close rather than widen. Both the control
  and the two owed items below are named in *Compliance*.
- **ADR-0013's owed E2E is discharged at slice B4.** Its *Still owed* section
  names it — "an E2E test asserting approved knowledge is unchanged after a full
  agent session that calls every write-intent tool" — and records that the
  property "still holds vacuously today" because no such tool is registered.
  Slice B4 is where it stops being vacuous.
- **SEC-11 needs no wiring.** The secret-scan policy is read inside
  `ProposalService` rather than injected, and the docstring's stated reason is
  this arrival: an injected policy "is one a composition root can forget to
  wire, and Milestone 7's write-intent MCP tools are a second root arriving."
  So a tool that drafts through the service inherits the accept-time gate.
  **Draft-time advisory scanning is a different control and stays owed** —
  [#330](https://github.com/theurian/theurian/issues/330)'s remaining half,
  inherited by this surface rather than introduced by it.

## Consequences

### Positive

- **"AI proposes" becomes a protocol rather than a CLI.** An agent from any
  vendor reaches the same proposal path Claude Code reaches today, which is
  Phase B's stated goal.
- **One service, two front ends.** Every guarantee `ProposalService` holds —
  fresh identifiers, the digest pin, the unguarded-update refusal, the
  containment on writes — arrives with the tools rather than being rebuilt for
  them. The guarantees that live *above* the service, in
  `cli/propose_commands.py`, do not travel, and decision 3 names both of them
  rather than letting this bullet imply they do.
- **The capability surface stays honest at every commit.** No window exists in
  which the machine-readable answer and the registered tool set disagree. This
  line was scoped to *once the coupling pin in Compliance lands*, because until
  then decision 5 was a rule a reviewer enforces; the pin landed with slice B4 as
  `tests/unit/test_write_tools_flag_claims.py::test_writetools_reads_true_exactly_when_a_write_intent_tool_is_registered`,
  so the scoping is discharged rather than deleted.

### Negative

- **The wire surface is now something that can be got wrong.** Two tools with
  large inputs is a larger contract than none, and ADR-0031's schemas are what
  hold it. The two ADRs are load-bearing for each other.
- **A proposal directory is an agent-writable artifact reachable over the
  network-facing daemon.** The daemon is loopback-bound and authenticated
  (ADR-0002, ADR-0011), and the write is confined to the project's proposal
  directory by tests that diff the whole tree — but the set of principals who
  can create a file in a repository grows by exactly this change, which is the
  fact ADR-0034's precondition exists for.
- **`generateMigrationDraft`'s v1 refusals will be met by callers.** An agent
  that reaches for `upsertRevision` there is told to use the other tool; one
  that reaches for `changeSensitivity` or `restoreItem` is told to use the CLI
  and has no tool to be redirected to. That is a deliberate cost of decision 3
  and it will read as a limitation before it reads as a design.
- **Two of the three tools' owed controls were new work, not inherited.**
  Decision 6's caller-scoped revision lookup and decision 8's draft-only facade
  were properties nothing in the tree held when this ADR was accepted; the sweeps
  that looked like they held them did not. Naming that here was the point of the
  two *Compliance* entries, and pretending otherwise is what the round that found
  this was correcting. **Both were built in slice B4** — `DraftOnlyProposals` and
  the lookup `register` injects — and *Compliance* names what holds each.

### Neutral

- **The proposal format does not change.** `docs/roadmap.md`'s Phase B row
  already says so, and this ADR keeps it: the migration, the body path and the
  evidence file are what `ProposalService` already writes.
- **Nothing about the accept path moves.** `theurian propose accept` stays the
  human's command, and ADR-0027's validate-before-move stays its gate.

## What this does not close

1. **`review.generateKnowledgeCandidate`.** Its own design is
   [ADR-0033](0033-knowledge-candidate-generation.md); it registers at slice B5,
   additively.
2. **The remaining planned `knowledge.*` tools** — `getContext`, `trace`,
   `listChanges`, `checkFreshness`, `submitFeedback` — stay planned. This ADR
   neither builds nor retires them.
3. **The ten operation kinds in `generateMigrationDraft`'s v1 set are admitted,
   not exercised.** Which of them a real agent can usefully author, and what
   remedy text each refusal needs, is slice B4's to find by running it.
4. **Widening `generateMigrationDraft` to the content operations, to
   `changeSensitivity`, or to `restoreItem`.** Additive by construction
   (decision 3), and not designed here. Each of the two non-content ones needs
   its own recorded justification: what a declassification admits depends on a
   deployment ceiling the reviewer of the pull request cannot see, and a
   readmission needs the three things decision 3 names — a transition-aware
   refusal, a wire-required `reason`, and a decision about whether the
   enforcement seat is the tool or the engine (Phase D's ADR candidate #1).
5. **Whether any residual disclosure survives decision 6's bind — answered at
   slice B4, and now a measurement.** The bind names the shape and **two**
   seats — a caller-scoped `CurrentRevisionLookup` for `proposeChange`, and, if
   decision 3's open question is answered *at generation*, whatever scopes the
   alias guard's status for `generateMigrationDraft`. Slice B4 ran the equality
   this owed on both — in the same-corpus shape the decision-6 compliance entry
   records, not the two-corpora one asked for here; that entry states the
   difference and the one channel the same-corpus form cannot catch (one carried
   by collection-wide state): the **value** channel by the disclosure oracle
   (`test_write_intent_disclosure_oracle.py`), the **duration** channel by the
   zero-body-read pin (`test_pre_gate_body_materialization.py`) plus an
   out-of-band timing measurement. On the value channel the refusal a caller
   reads carries no residual, to that entry's recorded reach. On the duration
   channel the body-size term is **closed** by the body-free lookup
   (`get_item_metadata`, T-26 face 4), so a refusal's timing no longer scales
   with a withheld body; the one residual is a content-independent existence term
   of ~9 µs — a withheld item that exists reads a bodyless pointer row where an
   absent id reads nothing — which carries no withheld content, sits about 155×
   below the ~1.40 ms transport floor (TB-1), and is not a gradeable disclosure.
   The equality covered refusals as well as responses, which is why *timing* was
   in scope: the duration channel decision 6's *does not close* row named is the
   ~9 µs existence term this measurement bounds, not the body-size one B4 removed.
6. **Rate, size and concurrency bounds on a write-intent call.** A caller able
   to make the daemon spend work no recorded limit bounds is the T-6 family's;
   [#26](https://github.com/theurian/theurian/issues/26)'s concurrency cap is
   the precedent for how such a bound is recorded.
7. **Draft-time secret scanning.** [#330](https://github.com/theurian/theurian/issues/330)'s
   owed half, inherited here and not discharged here.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **Register the tools behind a configuration gate, dark, and flip `writeTools` in a later slice** | It makes a deliberately build-constant surface deployment-dependent. `system.capabilities` resolves no project and passes no `_resolve` (`mcp/tools.py` records that as the reason a *flag* may be published there while a sensitivity *ceiling* may not), so a config-dependent `writeTools` would be the one value on that block whose meaning varies per installation — and the pinned capability-key and tool-set assertions would each need a second, conditional arm. It also does not avoid decision 5's problem; it relocates it, since the build that can be configured to register a write tool is a build whose `false` is already conditional. |
| **One omnibus `knowledge.write` tool taking an operation discriminator** | Its blast radius is unnameable — "what can this tool do" has no answer shorter than the whole migration schema — and SEC-12's input schema becomes a union over fourteen operation shapes plus a body pipeline, which is the shape ADR-0031 decision 6's agreement check cannot usefully hold. Two tools with two schemas is what makes each one reviewable. |
| **Flip `writeTools: true` as its own later slice, after the tools land** | This is ADR-0030's `reviewIngestion` trade, and it does not transfer. There, no tool was callable during the window, which is what bounded the residual to "a wrong answer that costs no capability". Here the callable tool *is* the thing shipping, so the window would be a registered write path published as absent. |
| **Flip `writeTools: true` a slice early, so the flag never lags** | The mirror defect, and the one ADR-0026 names directly: a flag flipped ahead of its feature is exactly the dishonesty the pinned block exists to catch. A client told a write path exists and handed a `METHOD_NOT_FOUND` has been given a false answer in the other direction. |
| **Accept a body file path, with `security/paths.py` containment applied to it** | Containment is designed to keep *project* reads inside the project root; a caller-supplied path on this surface has no project root to be inside, since the body is by definition not yet in the project. The check that would be needed is "is this path one the caller is entitled to read", which the daemon cannot answer — it runs as the operator and sees the operator's whole filesystem. Decision 2 removes the parameter instead. |
| **Have the tools write proposal files directly, bypassing `ProposalService`** | It would produce a second procedure that must agree with `propose accept` about identifiers, digests and landed sets. ADR-0027 records what the first such disagreement cost, and ADR-0013's Compliance section records three separate accept-path procedures moved off filesystem enumeration onto the loaded `MigrationSet` for the same reason. |

## Compliance

**When this ADR was accepted it shipped no behaviour, so it had no shipped test
to name.** Its enforcement at design time was the measurements it cites; its
enforcement at implementation time was the tests slice B4 owed, and the *Still
owed* list below was written as properties an implementation must pin rather
than as files that existed — the same honest split
[ADR-0030](0030-github-review-ingestion-spawns-gh.md) states for the same
reason.

**Slice B4 has since landed, and the sentence above is corrected in place rather
than left standing:** the behaviour ships, and the section now partitions three
ways — the dated design-time measurements, unchanged; what slice B4 discharged,
each entry naming its test; and what is still owed, each naming its owner. An
entry that landed in a *different* shape from the one this ADR asked for says so
in its own words, because the difference is the part a later reader has to be
able to attack.

Measured when this ADR was accepted, and reproducible from it (2026-09-12,
`be977ea7`). **A dated reading, not a standing one** — slice B4 moved several of
these, and the *Landed* entries below say which rather than editing the readings
here:

- The `writeTools` population is **18 lines across 10 files**, with the key and
  its measured exclusions in *Context*, and the capabilities *note* is a second
  key returning **3** lines. Of the five non-prose files, **2** fail a build when
  the flag's value moves — mutation-measured in *Context*, control green.
- The proposal generator emits exactly **2** of `OperationKind`'s **14**
  members — `createItem` and `upsertRevision`, read off `_migration_document`'s
  returned `operations` list in `application/proposal_service.py`. Decision 3's
  v1 set is **10**: fourteen minus those two, minus `changeSensitivity` and
  minus `restoreItem`.
- **`restoreItem` sets `APPROVED` from any status, and nothing in `src/` checks
  the transition.** `application/migration_engine.py:497-498` passes
  `KnowledgeStatus.APPROVED` to `_set_status` (`:676-686`), which reads the
  target status from its argument and never the current one;
  `git grep -c "DEPRECATED" -- packages/theurian-core/src` answers 4 lines in 3
  files and none of them is a transition rule (the key and its limit are in
  decision 3). `REJECTED` is in that reach, and `domain/enums.py:206-219` records
  that `REJECTED` is reachable through no flag: "A rejected revision is one the
  team decided must *not* be followed, and it is also where a secret that caused
  the rejection still lives."
- **The alias guard's refusal renders an item id and its status, over the
  unfiltered set.** `AliasItemCollisionError.__init__` (`domain/errors.py:236-239`)
  formats `... collides with knowledge item {alias} (status {item_status})`, and
  `item_status` arrives from `_alias_item_collisions`
  (`application/migration_alias_guards.py:124-144`), which reads
  `_final_item_statuses(migration_set)` — a fold over `MigrationSet.ordered(...)`
  with no status filter and no sensitivity filter. That is why decision 3's
  guard-timing question is bound by decision 6.
- **The status gate and the disclosure gate are each pinned by an exact-equality
  set**, `STATUS_GATE_CALL_SITES` at **6** entries and
  `DISCLOSURE_GATE_CALL_SITES` at **5** (`tests/unit/test_gate_call_sites.py`,
  counted with `ast` over the two assignments), and each has a prose count beside
  it in `domain/enums.py` (`:231` "six call sites", `:273` "five call sites")
  that no test derives from the set. Both pairs are movers for decision 6's owed
  lookup, and they are listed in *Still owed* rather than left to be discovered.
- **The two pulled non-content operations carry opposite wire requirements.**
  `opRestoreItem` (`schemas/migrations/migration.schema.json:253-262`) requires
  `["op", "itemId"]` and types `reason` as optional; `opChangeSensitivity`
  (`:305-318`) requires `["op", "itemId", "sensitivity", "reason"]`.
- The bytecode sweep's forbidden set is **17** names — `WRITE_GATEWAYS`
  (`SqliteWriter`, `write_transaction`) plus the **15** methods on `SqliteWriter`
  that are not on `SqliteCanonicalStore`, computed live by
  `_mutating_method_names` (`tests/integration/test_mcp_tools.py`). `draft`,
  `accept` and `_commit` are in none of them, which is what decision 8's second
  bullet records.
- `ProposalRequest` declares **16** fields, and `draft` takes one further
  keyword (`local`); the wire table in decision 1 is derived from that
  declaration rather than from the CLI's option list.
- Evidence requiredness is enforced in **two** places on this path —
  `Evidence.__post_init__` and `ProposalRequest.__post_init__`, both calling
  `require_evidence` — with the reason for the second recorded in
  `require_evidence`'s own docstring.

Landed in Phase B slice B4, with the test that discharges each. Each entry keeps
the property as it was written and states what actually holds it:

- **Both tools register through `_tool`, and the walk covers them.** Landed:
  `tests/integration/test_mcp_tools.py::test_no_registered_tool_can_reach_a_canonical_write`
  and `::test_every_registered_tool_goes_through_the_forwarding_seam` are green
  over the enlarged built server, and
  `tests/unit/test_tool_error_type_contract.py::test_every_tool_is_registered_through_the_one_seam`
  holds that the two new tools go through the one seam rather than around it. The
  *positive control* that matters here is
  `test_mcp_tools.py::test_the_walk_reaches_a_real_tool_body`: the sweep is
  asserted to reach a real body, so it cannot pass over a set it never
  enumerated.
- **No write-intent tool holds an object that can move approved state, and the
  sweep is not what holds it (decision 8).** Two controls landed, because one of
  them reaches one level and the other does not:
  1. **A draft-only facade at the MCP composition root.** `DraftOnlyProposals`
     (`application/draft_only_proposals.py`) captures the two entries as closures
     rather than storing the `ProposalService`, so its reachable surface is
     exactly `{draft, draft_from_document}` — `accept` and `_commit` are not
     reachable as a method and not one attribute hop away through a `_service`
     reference. Landed:
     `test_mcp_tools.py::test_no_write_intent_tool_captures_an_object_that_moves_approved_state`
     walks each registered write-intent tool's closure cells over the **built**
     server; `::test_the_closure_walk_flags_a_tool_that_captures_a_canonical_writer`
     is the sibling control that a captured mover *is* flagged, and the same test
     carries a non-vacuity guard that the walk reaches a real captured object;
     `::test_the_object_a_write_intent_tool_is_handed_is_the_draft_only_facade`
     invokes the per-call factory a tool closes over and asserts what comes back
     is the facade and not a `ProposalService`. At the unit level
     `tests/unit/test_draft_only_proposals.py` holds the facade's own surface,
     including that it keeps no bound method back to the service.
  2. **The forbidden-name set grew to the application-layer movers.**
     `_mutating_method_names`'s result is joined with `APPROVED_STATE_MOVERS`,
     `{accept, _commit}`. Landed with a *driving* test rather than an assertion:
     `::test_a_planted_tool_calling_accept_goes_red_for_the_extended_canonical_write_pin`
     plants a tool whose body calls them and asserts it is RED for the extended
     set and GREEN for the pre-extension one, so the extension has teeth against
     the counterexample the one-level sweep would otherwise miss. **The recorded
     bound stands unchanged**: the walk sees names in the registered callable's
     own code chain and does not enter a collaborator's body, so it catches a
     direct call and nothing deeper. That bound is the reason control 1 exists
     beside it rather than instead of it.
- **`docs/security/threat-model.md`'s T-12 control sentence was rewritten in the
  registration commit.** It read "no MCP tool reaches a write path for approved
  state … A test enumerates every registered tool and asserts none reaches a
  canonical write", where the first clause is stronger than the third. It now
  names the two tools, names the draft-only facade as what holds "no tool reaches
  approved state", and calls the canonical-write sweep "a second, narrower
  control: it reaches one level and so does not, by itself, hold the first
  clause". Whether the rewrite is faithful is a reading and no mechanical check
  reaches it; that it happened in the same commit is what was not optional, and
  it did.
- **`writeTools` and the capability note moved in the registration commit
  (decision 5).** Landed, with no intermediate state: the flag, the note, both
  registrations and every assertion they falsify are one commit. The two
  assertions that move on a value flip moved —
  `tests/integration/test_mcp_tools.py::test_capabilities_report_what_is_and_is_not_built`,
  and the e2e value assertion over a real client, now
  `tests/e2e/test_daemon_single_instance.py::test_capabilities_report_write_tools`
  (it was `::test_capabilities_report_no_write_tools`) — along with the note's own
  assertion, which now demands *"No MCP tool writes approved knowledge"* and
  forbids the opposite, where it used to demand *"No write-intent tool exists"*.
  **Of the two pins this entry expected to move at *registration* time, one did
  and one did not, and the difference is recorded rather than smoothed:** the
  tool-set equality moved and is now
  `::test_the_tool_set_is_exactly_the_published_nine` (it was
  `::test_the_tool_set_is_read_only`), an equality over the **nine** registered
  names that still does not notice a flag value at all; the pinned
  capability-**key** set in `test_mcp_tools.py` did **not** move, and could not
  have — registering a tool adds no capability *key*, and `writeTools` was
  already one of them.

  **The coupling is what got pinned, and it is a new test rather than one of the
  above**: `tests/unit/test_write_tools_flag_claims.py::test_writetools_reads_true_exactly_when_a_write_intent_tool_is_registered`
  reads both facts out of `mcp/tools.py`'s source — the value a contributor
  wrote, and the names registered through the one `_tool` seam — and demands
  `writeTools` read `true` exactly when a write-intent tool is registered, with
  `::test_the_coupling_checker_demands_the_other_state_when_either_side_moves`
  as the bidirectional control. It mirrors
  `tests/unit/test_review_ingestion_flag_claims.py`'s shape, as this entry asked.
  **The one instrument named here that did not land is the wire-contract
  positive**: `test_wire_contract.py`'s `writeTools` case is still the *type*
  negative it was, and the response schema still types the field `boolean` with
  no `const`, so the conformance suite alone stays green on either value. The
  property that item existed for — the flag cannot land half-moved — is held by
  the coupling pin and the e2e value assertion instead; the gap between the
  property and the instrument is recorded in *Still owed* rather than closed by
  calling them the same thing.
- **The prose sites that record the retiring meaning moved.** The registration
  commit moved `README.md`, `docs/index.md`, `docs/protocol/mcp-tools.md` and two
  rows of `docs/roadmap.md` — *An agent cannot change approved knowledge
  directly* in §0, and appendix row 6's registered figure and names;
  the documentation cluster that follows the round moved the rest of the roadmap
  (§0's opening, §1's *Shipped* and *Partial* entries and its SEC-12 row, §2's
  *Distribute what approval recorded*, §3's change ①, and the Phase B
  Architecture, MCP/API, Security, Tests and Exit-criteria rows), the README's
  *Alongside instructions and memory* paragraph, `docs/index.md`'s
  *What is enforced, and what is convention*, and this ADR and
  [ADR-0013](0013-ai-writes-produce-proposals.md). Whether that rewrite is
  faithful is a reading and no mechanical check reaches it, which is said here
  rather than left to be inferred from a test name beside it. Two mechanical
  checks do reach part of it —
  `tests/integration/test_documented_tool_set.py` recomputes the README's and the
  protocol page's tool lists from the built server, and the roadmap appendix
  row 6's registered figure and nine names with them.
- **`generateMigrationDraft` refuses four kinds and admits ten (decision 3).**
  Landed twice, at the service and over the wire.
  `tests/integration/test_generate_migration_draft.py` drives the service entry:
  an admitted kind lands a proposal, a content operation is refused to the
  content path, a read-control operation is refused to the CLI, and a mixed
  document is refused on its *first* unadmitted operation.
  `tests/integration/test_write_intent_wire.py` drives the same through the
  registered tool over the transport, where a wiring defect lives, with the
  refused cases parametrized over `_REFUSED_TO_CONTENT_PATH`/`_REFUSED_TO_CLI` so
  the case count moves with the enum-derived set rather than being stated beside
  it, and `::test_the_wire_refused_set_is_exactly_the_two_pulled_pairs` asserting
  the refused set is a disjoint four against `OperationKind`. The partition
  itself is pinned by
  `tests/unit/test_draft_only_proposals.py::test_the_v1_operation_set_partitions_operation_kind`,
  so a fifteenth kind is admitted or refused deliberately and never by omission,
  and the gate is fail-closed for a kind in neither set. **The redirect wording
  reaches the wire**, which it did not at first: a `ProposalError` crossing the
  `_forwarding` seam loses `exc.remedy` for mcp 2.0.0 parity, so each tool body
  catches its own `ProposalError` and re-raises through `_with_remedy`, and the
  wire tests assert the redirect name is in the **message** rather than that
  `.remedy` is set.
- **Applying any admitted operation leaves every item's `(status, sensitivity)`
  pair unchanged, `deprecateItem` excepted.** Landed:
  `tests/unit/test_admitted_ops_preserve_read_controls.py` applies each admitted
  `OperationKind` against a corpus carrying every precondition an admitted
  operation needs — a relation and an alias to remove, a spec to supersede, an
  evidence anchor to remove — and compares every item's `(status, sensitivity)`
  pair before and after. Only `deprecateItem` may move the status half of its own
  item, to `DEPRECATED`, and must leave its sensitivity and every other item
  untouched. Two controls keep it from degrading:
  `::test_the_admitted_operation_map_covers_the_v1_set` asserts the map's
  coverage against `V1_OPERATION_KINDS`, so a fifteenth admitted kind must be
  given a real operation here, and
  `::test_the_base_corpus_holds_the_read_controls_the_invariant_measures`
  asserts the corpus has something to measure. The landing commit records a
  mutation run behind it — `changeOwner` grown a `sensitivity=` keyword at its
  call site flips the item's class and reddens this test — and that result is
  cited to its commit rather than restated here as a fresh measurement. Before
  this, the property was held by a keyword at a call site and nothing else:
  `application/migration_engine.py:536` spells `_replace_item(item,
  owner=operation.owner)` while `_replace_item` (`:727-737`) accepts
  `{"sensitivity", "owner", "trust_level", "status"}`, so an admitted operation
  that grew a second keyword would move a read control with no test going RED.
  This turns round 3's closure argument into a pinned property rather than a
  declaration.
- **A refusal about an out-of-view item is indistinguishable from one about an
  absent item, on **both** tools (decision 6).** Landed as
  `tests/integration/test_write_intent_disclosure_oracle.py`, and **in a
  different shape from the one asked for here, which is worth stating rather than
  smoothing over.** This entry asked for one battery run against two corpora —
  one that held the withheld items and one that never did. What landed is a
  *same-corpus* equality: one synthetic corpus holding two withheld rows
  (`rejected-store`, withheld by `may_surface`, and `confidential-item`, above
  the default serving ceiling) and one in view, with each answer about an
  out-of-view id compared against the answer about an id nothing ever stored.
  The two forms differ in what they can catch — a two-corpora run would also
  catch a channel carried by collection-wide state, which this one cannot — and
  they agree on the channel decision 6 names, which is the refusal a caller
  reads. The **withheld-reach control** is
  `::test_the_out_of_view_items_really_hold_a_revision_the_lookup_suppresses`:
  all three items are asserted to hold a real current revision, so a `None` from
  the caller-scoped lookup is suppression and not genuine absence. The fixture is
  synthetic, which is the only way to have a withheld row in a corpus whose scope
  excludes them (ADR-0030 decision 6's reasoning, one surface over). **Refusals
  are asserted to carry no status, not only no revision id.** **Timing is
  measured at slice B4 by a separate instrument** — this oracle covers *content*
  and does not measure duration; the duration channel is closed by the body-free
  caller-scoped lookup (`get_item_metadata`, T-26 face 4) and pinned by the
  zero-body-read counter (`test_pre_gate_body_materialization.py`), recorded in
  the decision-6 *Landed* entry below, no longer in *Still owed*.

  **The battery covers `generateMigrationDraft` as well as `proposeChange`**, at
  one admitted operation per item-id-bearing input *position*. Over the ten
  admitted kinds those positions are **six** distinct property names, read off
  the schema rather than listed by hand — every property whose `$ref` resolves to
  `#/$defs/itemId` — and the derivation below is now recomputed in the suite by
  `::test_the_six_positions_are_the_schema_derived_item_id_positions`, so a
  fifteenth operation with a seventh position reddens it rather than silently
  falling outside the battery:

  ```python
  # run from the repository root; prints the table below
  import json, pathlib

  defs = json.loads(pathlib.Path("schemas/migrations/migration.schema.json").read_text())["$defs"]
  pulled = {"operation", "opCreateItem", "opUpsertRevision", "opChangeSensitivity", "opRestoreItem"}
  positions: set[str] = set()
  for name, body in sorted(defs.items()):
      if not name.startswith("op") or name in pulled:
          continue
      ids = sorted(
          p for p, q in body.get("properties", {}).items() if q.get("$ref", "").endswith("itemId")
      )
      positions |= set(ids)
      print(f"{name:26}{ids}")
  print("distinct positions:", len(positions), sorted(positions))
  ```

  ```console
  opAddAlias                ['alias', 'itemId']
  opAddEvidence             ['itemId']
  opAddRelation             ['sourceItemId', 'targetItemId']
  opChangeOwner             ['itemId']
  opDeprecateItem           ['itemId', 'supersededBy']
  opRegisterSpecification   ['itemId', 'specId']
  opRemoveAlias             ['alias']
  opRemoveEvidence          ['itemId']
  opRemoveRelation          ['sourceItemId', 'targetItemId']
  opSupersedeSpecification  ['specId', 'supersededBy']
  distinct positions: 6 ['alias', 'itemId', 'sourceItemId', 'specId', 'supersededBy', 'targetItemId']
  ```

  A battery scoped to `itemId` alone would pass a build that answered through
  `addRelation`'s `targetItemId` or `deprecateItem`'s `supersededBy`, which is
  why the obligation is stated per position and derived from the schema — a
  fifteenth operation with a seventh position joins it by existing.
  **And the refusals are asserted to carry no status**, not only no revision id —
  the alias guard's message is the one that publishes a status today
  (decision 3), so a battery written against the revision id alone would pass a
  build that answered `(status rejected)`.
- **The wire path's `CurrentRevisionLookup` is caller-scoped (decision 6).**
  Landed: `register` builds the lookup at the MCP composition root and it
  consults `may_surface` and `may_disclose`, so an item this caller may not see
  answers `None`.
  `test_write_intent_disclosure_oracle.py::test_proposechange_refuses_an_out_of_view_item_like_an_absent_one`
  drives it, and `::test_proposechange_carries_the_revision_for_an_in_view_item`
  is the control the entry asks for — without it, a lookup returning `None` for
  everything would satisfy the equality while breaking the
  optimistic-concurrency remedy for in-view items. The landing commit records a
  mutation run in both directions — dropping the `may_surface`/`may_disclose`
  gate leaks the out-of-view revision, and returning `None` for everything drops
  the in-view one — cited to that commit rather than restated here.

  **And the lookup is body-free (T-26 face 4).** It reads `get_item_metadata`,
  not the body-joining `get_item`, so a withheld item's refusal materialises no
  body and its *timing* is content-independent — it does not scale with the
  withheld body's size.
  `test_pre_gate_body_materialization.py::test_the_real_propose_change_handler_reads_no_body_before_a_withheld_refusal`
  pins the zero body read on the withheld refusal (RED when the closure's
  `get_item_metadata` reverts to `get_item`), and
  `::test_the_real_propose_change_handler_names_the_current_revision_for_an_in_view_draft`
  pins `include_unapproved=True` in the lookup — the `draft`/`proposed` statuses
  `may_surface` treats differently under the flag — so the in-view concurrency
  remedy the #210 class depends on stays live. The one residual is a
  content-independent existence term of ~9 µs (a withheld item that exists reads a
  bodyless pointer row; an absent id reads nothing), about 155× below the
  ~1.40 ms transport floor (TB-1) and not a gradeable disclosure — the
  measurement open-question 5 owed.

  **This item moved the pinned counts it said it would, in the commit that added
  the call site.** The lookup is one call site on each gate, so
  `STATUS_GATE_CALL_SITES` is **7** and `DISCLOSURE_GATE_CALL_SITES` is **6**,
  both carrying the new `("mcp/tools.py", "register._draft_only_proposals.current_revision")`
  entry, and both prose counts in `domain/enums.py` moved with them — `may_surface`'s
  docstring now reads *seven* and `may_disclose`'s *six*, each with the new site
  enumerated. The disclosure *axis* was not touched, so
  `requirements-analysis.md`'s `enforced-axes` block and `SECURITY.md`'s copy of
  it did not move. **The table below is the reading this ADR was accepted
  against and is kept as that**, not corrected in place: it is what the four
  records held before the lookup landed.

  | Record | What it holds now | Measured |
  | :-- | :-- | :-- |
  | `tests/unit/test_gate_call_sites.py`'s `STATUS_GATE_CALL_SITES` | an **exact-equality** set of `(module, function)` pairs, so it fails on an addition as well as a removal | **6** entries |
  | the same file's `DISCLOSURE_GATE_CALL_SITES` | the same shape for `may_disclose` | **5** entries |
  | `may_surface`'s docstring (`domain/enums.py`) | "it is consulted from six call sites", enumerated in prose | the word **six** |
  | `may_disclose`'s docstring (`domain/enums.py`) | "Consulted from five call sites" | the word **five** |

  Counted with `ast` over the two assignments in `test_gate_call_sites.py`
  (`STATUS_GATE_CALL_SITES 6` / `DISCLOSURE_GATE_CALL_SITES 5`); the two
  docstring words are read at `domain/enums.py:231` and `:273`. Both sets are
  asserted by equality, so neither degrades silently — but neither prose count
  is derived from its set, so those two move by hand or not at all.
  **And if the disclosure axis itself is touched**,
  `docs/architecture/requirements-analysis.md`'s `enforced-axes` block (:103-106,
  "**three** enforced axes — `chunks.project_id`, `chunks.status` and
  `chunks.sensitivity`") and `SECURITY.md`'s copy of it move too; the same test
  file checks both against what `_scope` emits, token set and spelled count.
- **An `agentId`/`taskId` stated twice and disagreeing is refused (decision 4).**
  Landed over the wire, with both controls:
  `test_write_intent_wire.py::test_a_top_level_identity_that_disagrees_with_evidence_is_refused`,
  `::test_a_top_level_identity_that_agrees_is_accepted` and
  `::test_the_top_level_identity_absent_is_accepted` — so the check is not
  satisfied by a build that refuses every call carrying the field. The evidence
  block stays the authoritative one; the top-level pair remains ambient
  provenance no handler reads into a proposal, which is
  [#665](https://github.com/theurian/theurian/issues/665)'s standing residual and
  not something this decision closes.
- **The operations path lands through the same guards as the content path,
  reached through the injected validator.** Landed in part.
  `test_generate_migration_draft.py::test_it_reaches_the_injected_validator`
  spies the injected `MigrationDocumentValidator` and asserts the document it saw
  is the *stamped* one — the minted id, not a caller value — so the generator and
  the accept-time rehearsal validate the same bytes;
  `::test_the_landed_migration_is_schema_valid_and_carries_the_operation`
  re-validates the written file against the published migration schema;
  `::test_the_service_mints_the_migration_id_over_a_caller_supplied_one` holds
  that a caller cannot choose an id that collides with a landed migration; and
  `::test_a_document_the_schema_rejects_is_refused_and_nothing_is_written` is the
  nothing-written arm. **Two halves of this entry did not land** and are in
  *Still owed*: the end-to-end drive through `theurian propose accept`, and a
  test for the structural no-direct-loader-import property.
- **The CLI-only guarantees have wire equivalents (decision 3).** Landed, and the
  two rows landed differently. Duplicate `labels[]` is a *schema* refusal naming
  the `labels` key path over the wire
  (`test_write_intent_wire.py::test_duplicate_labels_are_refused_by_the_schema_over_the_wire`).
  The `body` cap is published as a `maxLength` on the `body` key and pinned to
  `MAX_REQUEST_BODY_BYTES` by `tests/unit/test_input_schema_bounds.py`, but
  `::test_the_body_size_bound_is_a_schema_constraint_on_the_body_key` drives it
  against the **loaded schema** rather than over the wire, because over the wire
  the rendered-character bound and the transport cap are both tighter and fire
  first — so the published `maxLength` is not the refusal a caller meets
  ([#699](https://github.com/theurian/theurian/issues/699)). The unit it counts
  is [#691](https://github.com/theurian/theurian/issues/691)'s. Both are recorded
  in *Still owed* rather than read as discharged.
- **ADR-0013's owed E2E is discharged.** Landed as
  `tests/e2e/test_write_intent_session.py::test_a_session_calling_every_write_intent_tool_leaves_approved_knowledge_unchanged`:
  against a real daemon the session calls each write-intent tool, and approved
  knowledge is asserted unchanged two ways — the read tools report the identical
  status and approved item *through* the daemon, and the canonical store's main
  file and the approved bodies are byte-identical on disk (SQLite's read-time WAL
  and SHM sidecars excluded, since a read creates empty ones). The non-vacuity
  control is that each call is asserted to have landed a distinct proposal. The
  landing commit records a mutation run against an isolated branch build — a
  write-intent tool appending to an approved body reddens the digest assertion —
  cited to that commit rather than restated here.
  **[ADR-0013](0013-ai-writes-produce-proposals.md)'s own *Still owed* entry moved
  to a *Landed in Phase B slice B4* section naming this test**, and the coverage
  residual that section first recorded — a committed argument set, which cannot
  redden when a *newly* registered write-intent tool is missing from it — was
  closed in the same slice: the set is now derived from `tools/list`, keyed on
  decision 4's required `evidence` object, and asserted equal to the arguments the
  session carries. What remains is the bound of that key, recorded in ADR-0013's
  *Still owed* rather than here, since it is that ADR's property.
- **Every wire field carries its published input schema (ADR-0031).** Landed:
  `schemas/mcp/knowledge-propose-change-input.schema.json` and
  `schemas/mcp/knowledge-generate-migration-draft-input.schema.json` ship, and
  both tools join `test_input_validation_wire.py`'s per-tool parametrization, so
  they are covered by ADR-0031's fail-closed rule — a registered tool that
  resolves to no loaded schema is refused at dispatch — rather than by being
  remembered. Owed there rather than here, and named here because this surface is
  the one that makes it mandatory.

Still owed, with the milestone that will satisfy it:

**The first three below are addressed to a slice and to no issue.** Measured
2026-09-15, `gh issue list --state open --search "write-intent OR proposeChange
OR generateMigrationDraft OR facade OR draft-only"` returns nothing that covers
any of them, so this section is their only owner — a reader should treat that as
the gap it is rather than assume a tracker entry exists. The last three each name
their issue. (Open-question 5's timing measurement, formerly a fourth
slice-addressed item here, landed at slice B4 and moved to the decision-6
*Landed* entry above.)

- **Slice B5 or later — a file path is refused by construction, and nothing
  recomputes it (decision 2).** The property holds as shipped: neither published
  input schema declares a path, URI, or reference field, and neither handler
  signature has a path parameter. **What is owed is the check**, and the shape
  matters — a refusal test would pin today's spelling of a rejection, while what
  this needs is a structural test that no write-intent tool's input schema
  declares a path-shaped field. Today the absence is held by review and by
  ADR-0031's `unevaluatedProperties: false` refusing any key the schema does not
  name, which stops a *caller* smuggling one and does nothing about a schema
  someone widens.
- **Slice B5 or later — a value-level wire-contract assertion on `writeTools`.**
  The *Landed* entry above records why: the conformance file's case is a type
  negative and the response schema types the field `boolean` with no `const`, so
  the conformance suite alone is green on either value. The coupling pin and the
  e2e value assertion hold the property; this closes the gap between the property
  and the instrument this ADR named.
- **Slice B5 or later — the two halves of the operations-path entry that did not
  land.** A test that a document drafted through `draft_from_document` produces a
  proposal directory `theurian propose accept` accepts, driven through the
  shipped commands rather than asserted about the service; and a structural test
  that the entry imports no migration-loader symbol directly (ADR-0003). The
  second holds today by reading `application/proposal_service.py`'s imports, and
  by nothing else.
- **[#691](https://github.com/theurian/theurian/issues/691) — the unit the
  published `body` `maxLength` counts.** JSON Schema counts code points; the
  constant it transcribes counts bytes, so the bound admits up to four times the
  bytes it names. Recorded, not closed.
- **[#699](https://github.com/theurian/theurian/issues/699) — that `maxLength` is
  unreachable over the shipped transport.** The rendered-character bound and the
  transport cap are both tighter and fire first, so a caller never meets the
  bound this surface publishes.
- **[#665](https://github.com/theurian/theurian/issues/665) — the top-level
  `snapshotId`, `agentId` and `taskId`.** Admitted by the enforced contract on
  both new tools, as on the other seven, and read by no handler. Decision 4 makes
  the *evidence* pair authoritative and refuses a disagreeing top-level one; it
  does not make the top-level trio read.
