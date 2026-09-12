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

The ten files are not all prose. **Five** of them hold the claim in a form that
fails a build rather than a reading: `schemas/mcp/system-capabilities-response.schema.json`
(the property and its description), `packages/theurian-core/src/theurian/mcp/tools.py`
(the value itself), `packages/theurian-core/tests/integration/test_mcp_tools.py`
(the value assertion and the pinned capability-key set),
`packages/theurian-core/tests/integration/test_wire_contract.py` (the conformance
negative), and `tests/e2e/test_daemon_single_instance.py` (what a real client
sees over the wire). A second, narrower key reaches the capabilities *note*,
which does not contain the flag's name:

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

### 3. `knowledge.generateMigrationDraft` is the operations path, and v1 carries the closed operation set minus the two the content path owns

`knowledge.proposeChange` covers exactly one shape of change: a body and the
revision that carries it. The other thirteen things a migration can say — a
deprecation, an alias, an owner change, a relation, a specification registration
— have no tool at all, and the generator cannot express them: `_migration_document`
emits precisely `createItem` and `upsertRevision`
(`application/proposal_service.py`), two of the fourteen members of
`OperationKind` (`domain/migration.py`, counted from the enum body).

So the second tool takes a **migration document** and lands it as a proposal.
Its v1 operation set is the closed `OperationKind` set **minus `createItem` and
`upsertRevision`**, which are refused with a remedy naming
`knowledge.proposeChange`.

Three reasons, in order of weight:

1. **The operations path must not become a second body pipeline.** A
   `upsertRevision` accepted here would need `contentFile`, the digest pin, the
   per-revision body path and the replacement guard — every mechanism
   ADR-0013's amendment and ADR-0027 record as hard-won — reimplemented against
   a hand-authored document. Two pipelines that must agree about the same set is
   the defect shape, not the feature.
2. **The split is legible to a caller.** *Content goes to `proposeChange`;
   everything else goes to `generateMigrationDraft`* is a rule an agent can
   follow without reading this ADR.
3. **Widening is additive.** Admitting `upsertRevision` later adds a capability;
   no client breaks. Narrowing later would break clients, which is why the v1
   set is the conservative one.

**Validation reuses the existing entry point.** `validate_migration_document`
(`infrastructure/filesystem/migration_loader.py`) takes a parsed mapping rather
than a path, and its docstring already names this use: "a generator can refuse
to write a migration it has just built wrong rather than leaving one on disk for
a reviewer to discover. ADR-0013 point 3 is the reason it belongs at generation."
It also already carries the bounds an untrusted document needs
(`MAX_DOCUMENT_NESTING`, `MAX_DOCUMENT_NODES`, `MAX_DOCUMENT_RENDERED_CHARS`,
recorded there for #291 and #245).

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

### 6. Preconditions, in order

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

### 7. The standing guarantees extend to the new tools rather than being weakened for them

- **Registration goes through `_tool`.** The seam refuses a callable that defers
  its body — a coroutine, an async generator, a generator — at registration
  time, because `_forwarding`'s `except` arm "only sees exceptions raised
  *while it is on the stack*" and the conversion "silently stops applying, and
  only under mcp >= 2.1" (`mcp/tools.py`, `_tool`). A write-intent tool that
  wants to be async is a design change with its own reasoning, not a decorator
  choice.
- **The bytecode walk covers them automatically.**
  `tests/integration/test_mcp_tools.py::test_no_registered_tool_can_reach_a_canonical_write`
  enumerates the **built** server rather than a list, so a new tool joins the
  sweep by existing. That is what makes ADR-0013's structural claim survive this
  change: the tools write proposal files, and the same test that holds
  "no registered tool reaches a canonical write" now holds it over a surface
  that writes.
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
- **One service, two front ends.** Every guarantee `theurian propose` has —
  fresh identifiers, the digest pin, the unguarded-update refusal, the
  containment on writes — arrives with the tools rather than being rebuilt for
  them.
- **The capability surface stays honest at every commit.** *Once the coupling
  pin in Compliance lands*, no window exists in which the machine-readable
  answer and the registered tool set disagree — until then decision 5 is a rule
  a reviewer enforces, and this line is scoped to say so rather than claiming a
  property nothing holds.

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
- **`generateMigrationDraft`'s v1 refusal will be met by callers.** An agent
  that reaches for `upsertRevision` there is told to use the other tool. That is
  a deliberate cost of decision 3 and it will read as a limitation before it
  reads as a design.

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
3. **The other twelve operation kinds in `generateMigrationDraft`'s v1 set are
   admitted, not exercised.** Which of them a real agent can usefully author,
   and what remedy text each refusal needs, is slice B4's to find by running it.
4. **Widening `generateMigrationDraft` to the content operations.** Additive by
   construction (decision 3), and not designed here.
5. **Rate, size and concurrency bounds on a write-intent call.** A caller able
   to make the daemon spend work no recorded limit bounds is the T-6 family's;
   [#26](https://github.com/theurian/theurian/issues/26)'s concurrency cap is
   the precedent for how such a bound is recorded.
6. **Draft-time secret scanning.** [#330](https://github.com/theurian/theurian/issues/330)'s
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

**This ADR ships no behaviour, so it has no shipped test to name.** Its
enforcement at design time is the measurements it cites; its enforcement at
implementation time is the tests slice B4 owes. The names below are the
properties an implementation must pin, not files that exist today — the same
honest split [ADR-0030](0030-github-review-ingestion-spawns-gh.md) states for
the same reason.

Measured now, and reproducible from this ADR (2026-09-12, `be977ea7`):

- The `writeTools` population is **18 lines across 10 files**, with the key and
  its measured exclusions in *Context*, and the capabilities *note* is a second
  key returning **3** lines.
- The proposal generator emits exactly **2** of `OperationKind`'s **14**
  members — `createItem` and `upsertRevision`, read off `_migration_document`'s
  returned `operations` list in `application/proposal_service.py`.
- `ProposalRequest` declares **16** fields, and `draft` takes one further
  keyword (`local`); the wire table in decision 1 is derived from that
  declaration rather than from the CLI's option list.
- Evidence requiredness is enforced in **two** places on this path —
  `Evidence.__post_init__` and `ProposalRequest.__post_init__`, both calling
  `require_evidence` — with the reason for the second recorded in
  `require_evidence`'s own docstring.

Still owed, with the milestone that will satisfy it:

- **Slice B4 — both tools register through `_tool`, and the walk covers them.**
  Owed: the existing
  `tests/integration/test_mcp_tools.py::test_no_registered_tool_can_reach_a_canonical_write`
  and `::test_every_registered_tool_goes_through_the_forwarding_seam` green over
  the enlarged built server — with the *positive control* that matters here,
  that the sweep actually reaches the new tools rather than passing over a set
  it never enumerated.
- **Slice B4 — `writeTools` and the capability note move in the registration
  commit (decision 5).** Owed: the value assertion, the pinned capability-key
  set, the wire-contract conformance negative and the e2e tool-set pin all move
  together, plus the note's own assertion. The property to pin is the
  *coupling*, not the new value: a test that goes RED when a write-intent tool
  is registered while the flag still reads `false`, in the shape
  `tests/unit/test_review_ingestion_flag_claims.py::test_each_record_narrates_the_flag_the_capability_dict_publishes`
  already uses for `reviewIngestion` — including its control, that the checker
  demands the other era when the flag moves.
- **Slice B4 — the prose sites that record the retiring meaning move in the same
  commit.** The key is in *Context*; the movers are the sentences stating that
  *no write-intent tool exists*, as distinct from the sentences stating the
  flag's *value*. Whether that rewrite is faithful is a reading and no
  mechanical check reaches it, which is said here rather than left to be
  inferred from a test name beside it.
- **Slice B4 — a file path is refused by construction (decision 2).** The owed
  property is structural, not a refusal test: the published input schema names
  no path-shaped field and the handler signature has no path parameter, so the
  check is that no write-intent tool's input schema declares one. A refusal test
  would pin today's spelling of a rejection; this pins the absence of the
  parameter.
- **Slice B4 — `generateMigrationDraft` refuses `createItem` and
  `upsertRevision` with a remedy naming the other tool**, and admits the other
  twelve. Owed: one driving case per refused kind, plus at least one admitted
  kind landing a proposal, so the refusal is not satisfied by a tool that
  refuses everything.
- **Slice B4 — the operations path lands through the same guards as the content
  path.** Owed: a test that a document drafted through the new service entry
  produces a proposal directory `theurian propose accept` accepts, driven
  through the shipped commands rather than asserted about the service.
- **Slice B4 — ADR-0013's owed E2E is discharged.** "Approved knowledge is
  unchanged after a full agent session that calls every write-intent tool",
  against a real daemon, with the session actually calling each registered
  write-intent tool — a session that called none would pass it vacuously, which
  is the state ADR-0013 records today. **ADR-0013's own *Still owed* entry moves
  from owed to landed in the same commit, naming the test.**
- **Slice B4 — every wire field carries its published input schema
  (ADR-0031).** Owed there rather than here, and named here because this surface
  is the one that makes it mandatory.
