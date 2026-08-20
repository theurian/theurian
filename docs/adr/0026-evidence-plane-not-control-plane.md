# ADR-0026: Theurian is an evidence plane, not a control plane

- Status: accepted
- Date: 2026-08-20
- Deciders: Theurian maintainers
- Requirements: SEC-15, T-3
- Decision recorded in [the roadmap](../roadmap.md) §11, adopted 2026-08-20
- Rests on [ADR-0013](0013-ai-writes-produce-proposals.md) (approval is a Git
  merge) and [ADR-0009](0009-no-llm-vendor-lock-in.md) (local-first, offline):
  both are inputs to the reasoning below rather than decisions this one changes

## Context

As the traceability and drift work in the roadmap's later phases came into
focus, a natural-sounding framing appeared with it: that Theurian should become
the **control plane** for AI-assisted engineering — the place where policy is
declared, where changes are approved, and where non-compliant work is blocked.

The framing is attractive because Theurian already holds the material a control
plane would need: statuses, relations, validity windows, and soon a graph. It
would also be a mistake, and the reasons are in the codebase rather than in
taste. This ADR records the rejection so that a future proposal has to argue
against something rather than rediscover it.

The positioning was settled by a maintainer decision on 2026-08-20. The
normative sentence below now appears verbatim in the README, the published
documentation front page, and the roadmap.

## Decision

**Theurian is the evidence plane — the system of record that agents, humans and
CI all *consult*.** It is not a control point.

> **Theurian does not orchestrate, does not approve, does not enforce.**

Concretely, and in the same order:

- **It does not orchestrate.** Workflow sequencing, task assignment and progress
  state do not live here. Starting an implementation agent once a specification
  is approved is the caller's job — a human, an agent runtime, or CI. No
  workflow-specific schema is added, because the existing substrate already
  expresses the states such a workflow needs: "awaiting approval" is an item at
  `status=proposed`, "this implements that" is an `implements` relation, "we
  rejected this" is an approved item of `kind=rejected-approach`.
- **It does not approve.** Approval is the act of merging a pull request. There
  is no approval command and no approver field. That the merge *happened* is a
  workflow convention rather than a check the code makes — T-15's recorded
  residual, which stays recorded in the threat model and is not restated here.
- **It does not enforce.** Enforcement belongs to Git branch protection and to
  CI. "Theurian labels; it does not enforce. Acting on the label is the calling
  agent's responsibility" (T-3).

**CI may consult Theurian** — a future drift command is the intended shape — and
may block a pull request on what it finds. That is a welcome arrangement, and the
thing that blocked is CI.

## Consequences

### Positive

- T-3's grading survives. The threat model grades an agent acting on injected
  content as High rather than Critical *because* Theurian labels rather than
  enforces. A control plane would make "the agent did not comply" a Theurian
  vulnerability, and would re-grade a family of threats by changing a sentence.
- The product boundary gains a test that is cheap to apply to a feature request:
  does this hold a fact, or does it perform an action? Only the first is in
  scope.
- It stays composable. An organisation can put Theurian behind whatever
  orchestrator and whatever CI it already runs, and replace either without
  replacing Theurian.

### Negative

- Theurian cannot promise that anything it records is acted upon. A team can run
  it and continue to ignore every decision in it, and no report will say so.
  That is the honest cost of the boundary, not an oversight to fix later.
- Some genuinely useful features are ruled out — an approval UI, a policy engine,
  a blocking gate — and users will ask for them. The answer is the alternatives
  table below.

### Neutral

- Nothing in the current implementation changes. This ADR records a boundary the
  code already sits inside; it constrains what may be added.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **Become the control plane** — declare policy, approve changes, block non-compliant work | Three reasons, each with a source. (1) It contradicts the security stance the threat model's grading rests on: "Theurian labels; it does not enforce" is why T-3 is High and not Critical, so crossing that line re-grades threats rather than adding a feature. (2) It competes with control points that already exist and are better at the job — Git branch protection for approval, CI for enforcement — and being a third one is the entrance to the sprawling ALM product this project exists not to be. (3) A local-first, loopback, single-user daemon cannot honour a control plane's availability and integrity obligations. Meeting them makes hosting inevitable, which contradicts ADR-0009 and the permanent commitments in GOVERNANCE.md. |
| **Orchestrate the specification → implement → review workflow** | Workflow state belongs to the agents and CI that perform the work, and Theurian's existing statuses, kinds and relations are sufficient substrate for anything a caller needs to read. Adding a workflow schema would create a second place where progress is recorded, and the two would disagree the first time a run failed halfway. |
| **Enforce only "soft" policy — warn, never block** | A warning nothing acts on is a label, which is what already ships. A warning something acts on is enforcement with the responsibility hidden. There is no stable middle position, and naming it "soft" moves the boundary without admitting it. What is rejected here is a *judgment on the user's changes*. A report about Theurian's own state is not policy and is not affected: `theurian doctor`, `retrieval.stale` and `indexPurge.failed` all describe the system to its operator, which is what an evidence plane is for. |
| **Keep the positioning informal rather than writing it down** | The framing had already begun to appear in planning discussion. An unwritten boundary is re-litigated by whoever arrives next, and the cost of rediscovering these three reasons is higher than the cost of this file. |

## Compliance

**Part of this decision is enforced by tests. Most of it is policy, and this
section says which is which** — an ADR that implied the whole boundary was
machine-checked would itself be the kind of false claim it exists to prevent.

Held by tests today — **one clause of the three**:

- **"Does not approve", on the MCP surface.** `system.capabilities` reports
  `writeTools: false`, and
  `tests/integration/test_mcp_tools.py::test_no_registered_tool_can_reach_a_canonical_write`
  walks the bytecode of every registered tool rather than trusting the tool list,
  so a write path added later fails the suite instead of shipping. This holds the
  MCP half only; that a human *merged* the proposal is T-15's recorded residual
  and is held by nothing.

> The other capability flags — `traceability: false`, `reviewIngestion: false` —
> are asserted with their reasoning in `test_mcp_tools.py`, and it is worth
> saying what that does and does not do for this ADR. It is evidence of
> *capability honesty*: a flag cannot be flipped ahead of the feature it
> advertises. It is not evidence of the boundary. A build could report every flag
> truthfully and still orchestrate.

Held by prose and review, not by a test:

- **"Does not orchestrate" and "does not enforce."** Nothing fails if a future
  change adds a workflow state machine or a blocking policy engine. What exists
  is this ADR, the roadmap's *Not recommended* table — kept deliberately as the
  record of what was not built — and the product-boundary test in the roadmap's
  §2: does the change hold a fact, or perform an action?
- **The normative sentence's placement.** It appears verbatim in **four files** —
  `README.md`, `docs/index.md`, `docs/roadmap.md`, and this ADR, which became the
  fourth copy the moment it was written — and nothing checks that the four agree
  or that they still contain it.

Still owed, with the phase that would satisfy it:

- **A test pinning the boundary sentence across all four locations**, so that
  editing one and not the others goes red. This is the same shape as the existing
  call-site and config-key pins. Filed as
  [#283](https://github.com/theurian/theurian/issues/283).
- **A recorded decision on where a drift command's blocking behaviour lives**
  (Phase E). This ADR says CI blocks and Theurian reports. The first
  implementation of `drift` is where that stops being a sentence and becomes an
  exit code, and it should cite this ADR when it does.
