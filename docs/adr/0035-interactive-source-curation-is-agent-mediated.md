# ADR-0035: Interactive curation of sources is agent-mediated and does not relax the approval gate

- Status: proposed
- Date: 2026-09-13
- Deciders: Theurian maintainers
- Requirements: FR-I3, FR-V4, T-3
- Situates against [ADR-0013](0013-ai-writes-produce-proposals.md) (the approval
  gate this ADR preserves — proposal → PR → human review → merge → apply),
  [ADR-0026](0026-evidence-plane-not-control-plane.md) (the daemon does not
  become a control point, so interactivity cannot live in it),
  [ADR-0032](0032-the-write-intent-mcp-tool-surface.md) (the write-intent tool
  surface the dialogue terminates in) and
  [ADR-0033](0033-knowledge-candidate-generation.md) (the caller is the model;
  the provenance model that records how a proposal was distilled), and against
  [`docs/roadmap.md`](../roadmap.md)'s Phase B write path — the phase this
  design waits for — and [#223](https://github.com/theurian/theurian/issues/223)
  (external-source ingestion, a different, trust-model-gated class this ADR does
  not touch)

**This ADR records a design direction and ships no code.** No tool registers, no
flag moves, no command grows an option; the diff is confined to `docs/`. It fixes
a *principle* — agent-mediated, gate-preserved, evidence-captured — and
deliberately does not fix the *mechanism*, which waits for Phase B's write API to
land so the mechanism is designed against a real surface rather than on paper
(*What this does not close*).

## Context

Theurian's value grows only if decisions made across heterogeneous sources —
Jira, Notion, Confluence, Slack, a design thread — actually get recorded as
governed knowledge. Two forces work against that, and both are load-bearing:

1. **Noise.** A team's SaaS tools are a firehose of mutable content, most of
   which is not decision-grade. A raw feed of it is not knowledge; distilling the
   signal from it is the work.
2. **Friction.** The path from *a decision was made* to *it is governed
   knowledge* is, today, hand-authoring a migration document and then
   proposal → PR → review → apply. For a single ADR that is proportionate. For
   the steady stream of small decisions a team actually makes, it is cumbersome
   enough that most of them are never recorded — and a corpus that never fills is
   the reason the product would not spread. **Adoption is the stake here, not
   convenience.**

The tempting fix is to make the capture conversational: an agent reads the
sources, the human confirms, and it is recorded. Left unstated, "confirms" slides
into "approves", and the reviewable diff that [ADR-0013](0013-ai-writes-produce-proposals.md)
makes the whole product rest on is gone — replaced by a fast "yes" in a chat.
This ADR records the direction that removes the friction *without* removing the
review, and it names the invariant precisely so that a later implementation
cannot lose it by accident.

## Decision

### 1. Interactivity is agent-mediated; the daemon does not become interactive

The conversational curation lives in the **calling agent**, not in Theurian. The
agent reads the sources, converses with the human to separate decision-grade
signal from noise, and drafts a proposal. Theurian stays the deterministic
evidence plane [ADR-0026](0026-evidence-plane-not-control-plane.md) makes it:
*it does not orchestrate, does not approve, does not enforce.*

This is not an ergonomic preference. A daemon that ran an interactive curation
loop would be a second control point — the thing ADR-0026 rejects — and a
loopback, single-user daemon (ADR-0002) is the wrong place for a conversational
UI regardless. The agent is already where the conversation is, already holds the
model, and is already the party ADR-0033 decision 1 puts the judgement work on:
*the caller is the model.* Interactive curation is that same division applied one
step earlier — the agent distills, Theurian verifies and packages.

### 2. The dialogue is an on-ramp to the write path, not a new ingestion channel

The curation dialogue has exactly one terminus: a proposal produced through
Phase B's write path — `knowledge.proposeChange`
([ADR-0032](0032-the-write-intent-mcp-tool-surface.md) decision 1), the roadmap's
"AI proposes, over a protocol". **It produces a proposal; it never writes
approved knowledge, and it is not a route into the source layer.**

The distinction matters because the two failure modes are different. A dialogue
that wrote to the source layer directly would make an agent's distillation the
system of record with no review at all, which is what FR-I3 forbids — *route AI
writes to proposal files, never into approved state*. A dialogue that is an
on-ramp to `proposeChange` inherits every guard ADR-0032 and
[ADR-0027](0027-accept-validates-before-it-moves.md) already hold: the
schema-valid migration, the digest-pinned body, the accept-time secret scan, the
containment on writes. It adds no new write primitive; it feeds an existing one.

Ingesting the raw sources *themselves* as a governed source class — snapshotting
noisy SaaS content behind a trust model — is a separate problem, owned by
[#223](https://github.com/theurian/theurian/issues/223) and gated post-1.0. This
ADR is about an agent *reading* those sources and distilling a decision from
them; it is not about Theurian ingesting them.

### 3. The approval gate is preserved — this is the load-bearing invariant

> **The dialogue removes the friction of *authoring*. It does not remove, relax,
> or shortcut *approval*. Approval remains proposal → PR → human review → merge →
> `migrate apply`, exactly as [ADR-0013](0013-ai-writes-produce-proposals.md)
> point 4 states it. No conversational "yes" — however explicit, however
> confident — becomes approved knowledge without passing that gate.**

The friction the dialogue removes is *distillation and drafting*: the labour of
turning a scattered discussion into a well-formed migration document. The friction
it must never remove is *review*: a human reading a reviewable diff and merging a
pull request. These are different costs, and collapsing the second into the first
is the defect this decision exists to prevent.

A conversational approval is the same failure ADR-0013's alternatives table
already rejects, one register over. "Confidence-threshold auto-approval" fails
because model confidence is not correctness; "a signed human token per write"
fails as approval theatre — *mechanically satisfiable without anyone reading
anything*. A fast "yes" in a chat window is that second failure wearing a
friendlier face: it is a human act, but it is not the human act FR-V4 requires,
which is *approval recorded as a migration* against *a reviewable diff*. The
dialogue's output is a proposal a human still has to review and merge; the
conversation is upstream of the gate, never a substitute for it.

### 4. The dialogue's origin is captured as evidence

Because the proposal is produced through the write path, it carries the
provenance [ADR-0033](0033-knowledge-candidate-generation.md) and
[ADR-0032](0032-the-write-intent-mcp-tool-surface.md) decision 4 already require:
`agentId`, `taskId`, `model`, `reasoning`, and the `sourceAnchors` the agent
distilled *from*. That record is what makes an interactively-curated proposal
auditable — a reviewer, and any later reader, can see which agent drove the
dialogue, which model, the reasoning it recorded, and the specific source
material it claims to be summarising.

This is provenance, not authentication. As ADR-0033 and the tool-context schema
both record, Theurian does not authenticate agents; the evidence labels *which
run produced a proposal*. An agent that misattributes itself is outside this
control — and outside this ADR — but a proposal with the provenance *missing* is
already refused at generation (ADR-0013 point 5), and interactive curation
inherits that refusal rather than needing a new one.

### 5. Noise reduction must not become a new trust hole

Interactive curation shifts trust onto the agent's distillation. A human
confirming in a fast dialogue can miss what a careful PR review would catch — the
agent could over-summarise, drop a caveat, or (the case the roadmap names)
forward an instruction injected into the noisy source content as if it were a
decision. That last is [T-3](../security/threat-model.md) reaching the write path,
and the roadmap's Phase B risks row states it directly: *review text is untrusted
content, and turning it into a candidate is precisely the path by which an
injected instruction becomes a knowledge candidate.* The same is true of any
noisy source an agent distills.

So the guard is a requirement on the flow, not an afterthought. Any interactive
curation affordance built under this direction **must**:

- **(a) show its source anchors** — the reviewer sees what the agent distilled
  *from*, not only the polished result;
- **(b) keep the raw source viewable alongside the draft** — so a distortion or
  an injected instruction is catchable against the material, rather than trusted
  because it reads well;
- **(c) leave the final artifact a reviewable pull request** — the proposal is
  reviewed in the tool the team already reviews in, per ADR-0013.

The dialogue reduces the friction of *reaching* review. It must never eliminate
the review surface. The existing mitigations — never-auto-approve (FR-V4), the
SEC-15 safety triple on every served row, secret scanning at accept — remain in
force; what this decision adds is that the raw source stays visible so the human
review is a real one and not a rubber stamp on prose the agent chose.

## Consequences

### Positive

- **The adoption path opens without opening a trust hole.** The friction removed
  is authoring; the review surface is preserved intact. A team can capture the
  small decisions it makes today without hand-writing a migration for each, and
  none of them becomes governed knowledge without a human merging a pull request.
- **The daemon boundary is unchanged.** [ADR-0026](0026-evidence-plane-not-control-plane.md)'s
  test — *does this hold a fact, or perform an action?* — still answers "hold a
  fact" for Theurian. The action (the conversation) is the agent's; Theurian
  verifies and packages, as it already does for the CLI's `propose`.
- **The interactive origin is auditable.** ADR-0033's provenance model records
  how a proposal was distilled, so "an agent and a human curated this from a Slack
  thread" is a fact in the record rather than lost context.

### Negative

- **Trust shifts onto the agent's distillation, and the human review has to
  absorb it.** A fast dialogue makes it easier to approve without reading; the
  guard (decision 5) mitigates this by keeping the raw source viewable, but it
  cannot force a careful review. FR-V4's human is the control, and this ADR does
  not pretend the dialogue checks the distillation's fairness.
- **A new prompt-injection path, and it is the one the roadmap already names.**
  Distilling noisy, mutable SaaS content is precisely where an injected
  instruction can ride into a proposal (T-3). The mitigation is the existing
  triple plus decision 5's raw-source visibility, and the threat model's T-3
  section owes the interactive-curation path as a named addition when the
  mechanism lands — recorded here rather than dressed as already covered.
- **Candidate quality is the agent's and the human's, never Theurian's.** As with
  ADR-0033's candidate generation, Theurian verifies the gate and packages the
  proposal; it does not and cannot check that the agent's summary is a fair
  reading of the sources.

### Neutral

- **Nothing ships.** No flag moves, no schema changes, no command grows an
  option. The proposal format is unchanged, because the dialogue feeds the
  existing write path.
- **The mechanism is deferred by design.** When Phase B's write API has landed,
  the affordance is designed against a real surface — the same discipline
  ADR-0032 and ADR-0033 followed, and the reason each caught a design premise that
  a paper API would have hidden.

## What this does not close

1. **The concrete mechanism.** Whether interactive curation is a
   `theurian propose --interactive` affordance, a packaged agent skill, or purely
   the agent driving Phase B's write tools directly, is deferred until Phase B's
   write API (ADR-0032) has landed. This ADR fixes the principle; the mechanism
   is a later decision that must argue against decisions 1–5, not around them.
2. **Ingesting the raw sources as a governed source class.** Snapshotting noisy
   SaaS content behind a trust model is
   [#223](https://github.com/theurian/theurian/issues/223)'s external-source
   gateway (post-1.0), not this ADR. This ADR is about an agent reading those
   sources, never about Theurian ingesting them.
3. **Whether the agent's distillation is a fair reading of the sources.** That is
   FR-V4's human at the pull request, the same authority ADR-0033 gives the "fair
   reading" question. This ADR does not propose a machine that would replace them.
4. **The T-3 threat-model entry for the interactive-curation path.** Owed when the
   mechanism lands, alongside ADR-0033's owed candidate-path entry, and recorded
   here rather than written now.
5. **Any provenance-schema addition for recording the dialogue itself.** ADR-0033's
   existing `agentId`/`taskId`/`model`/`reasoning` plus source anchors are what
   this direction relies on; whether the interactive origin needs a field beyond
   them is a mechanism-time question, not settled here.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **Make the daemon interactive — a conversational curation service inside Theurian** | It makes Theurian a second control point, which [ADR-0026](0026-evidence-plane-not-control-plane.md) rejects with three grounded reasons; a loopback single-user daemon is the wrong host for a conversational UI (ADR-0002); and it is the entrance to the ALM product this project exists not to be. The agent is already where the conversation is. |
| **Collapse approval into the dialogue — a conversational "yes" becomes approved knowledge** | This is ADR-0013's rejected "signed human token per write" — approval theatre, *mechanically satisfiable without anyone reading anything* — with a friendlier interface. It removes the reviewable diff FR-V4 requires and the review surface the whole product rests on. Decision 3 is the direct refusal of it. |
| **Treat the dialogue as a new ingestion channel writing to the source layer directly** | It bypasses the proposal path FR-I3 mandates and makes an agent's distillation the system of record with no review. Ingesting external sources as a governed class is a distinct, trust-model-gated problem owned by [#223](https://github.com/theurian/theurian/issues/223), not a shortcut to bolt onto curation. |
| **Do nothing — keep hand-authoring every migration** | The friction is real and it is the adoption stake, not a convenience: a costly capture process is why good knowledge never gets recorded, and an empty corpus is why the product would not spread. But the friction to remove is *authoring*, not *approval* — which is the whole point of decisions 2 and 3. |
| **Design the mechanism now — specify `theurian propose --interactive` in this ADR** | Phase B's write API has not landed. ADR-0032 and ADR-0033 each caught a design premise that only measurement against the real surface revealed; designing an interactive affordance against a paper `proposeChange` would repeat exactly the mistake those ADRs avoided. Fix the principle now, design the mechanism against the real API later. |

## Compliance

**This ADR ships no behaviour, so it has no shipped test to name.** Like
[ADR-0026](0026-evidence-plane-not-control-plane.md), part of what it records is
policy held by prose and review, and part rests on enforcement that already
exists for the gate it preserves. This section says which is which.

Rests on enforcement that already holds the approval gate:

- **No MCP tool reaches a canonical write.**
  `tests/integration/test_mcp_tools.py::test_no_registered_tool_can_reach_a_canonical_write`
  walks the bytecode of every registered tool, so an interactive affordance that
  registered a write path would fail it rather than ship. This is the pin
  ADR-0013 and ADR-0026 already name; interactive curation inherits it rather
  than adding one, because its terminus is `knowledge.proposeChange` and not a new
  primitive. Its recorded limit is ADR-0032 decision 8's — the walk reaches one
  level and does not enter a collaborator's body — which is why the draft-only
  facade that closes that gap is slice B4's, not this ADR's.
- **Write intent produces a proposal, never approved state.** FR-I3's routing is
  held by `tests/integration/test_proposal_service.py::test_generation_writes_only_under_the_proposal_directory`
  and its siblings (ADR-0013 Compliance), which diff the whole tree. An
  interactive affordance drafting through `ProposalService` inherits this.

Held by prose and review, not by a test:

- **The daemon does not become interactive**, and **approval is not collapsed
  into the dialogue.** Nothing fails today if a future change tried either — the
  first write-intent tool has not registered. What constrains it is this ADR,
  ADR-0026's evidence-plane boundary, and ADR-0013's approval model, applied at
  review of whatever mechanism Phase B's slice proposes.

Still owed, with the phase that would satisfy it:

- **When an interactive-curation mechanism is designed (a Phase B slice, after
  the write path lands):** a test that the affordance lands **only** a proposal
  directory and reaches no merge or `migrate apply` — the driving property of
  decision 3, which cannot be written before a mechanism exists to drive. Owed
  with it: that the proposal carries the ADR-0033 provenance (decision 4), and
  that the raw source anchors are recorded on it (decision 5a). These are the
  same shape as ADR-0032's and ADR-0033's owed slice tests, and they are named
  here so the mechanism slice cannot land without them.
- **The T-3 threat-model entry for the interactive-curation path**, owed when the
  mechanism lands, alongside ADR-0033's owed candidate-path entry. A prose
  obligation with no test, recorded here rather than dressed as discharged.
