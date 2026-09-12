# ADR-0033: `KnowledgeCandidate` generation — the caller is the model, Theurian verifies the gate and lands a proposal

- Status: proposed
- Date: 2026-09-12
- Deciders: Theurian maintainers
- Requirements: FR-V2, FR-V3, FR-V4, FR-V5, INV-7, INV-8, SEC-12, SEC-13,
  SEC-15, T-3
- Discharges [ADR-0030](0030-github-review-ingestion-spawns-gh.md)'s *What this
  does not close* item 2 — FR-V2 classification and FR-V3 candidate generation,
  "and with it **how `PromotionGate` should treat an unknown CI outcome**"
- Situates against [ADR-0009](0009-no-llm-vendor-lock-in.md) (the model question
  ADR-0030 left open), [ADR-0013](0013-ai-writes-produce-proposals.md) (point 6,
  never auto-approved), [ADR-0029](0029-review-findings-are-governed-knowledge.md)
  (decision 6's uniform-refusal shape) and
  [ADR-0032](0032-the-write-intent-mcp-tool-surface.md) (the surface this tool
  joins additively)

**This ADR records a decision and ships no code.** No tool registers, no gate
type changes, no candidate is constructed; the diff is confined to `docs/`. What
slice B5 owes is named in *Compliance*, including the **pins this change will
deliberately move**.

**Every repository fact below was measured on 2026-09-12 against `be977ea7`**,
which is reachable from `origin/main`.

## Context

The domain model has been built since Milestone 1 and nothing fills it.
`PromotionGate` and `KnowledgeCandidate` live in `domain/review.py`; the
candidate refuses construction with no evidence, with an empty body, and with an
unmet gate; `trust_level` is `init=False` and fixed to `INFERRED`;
`CandidateStatus` has no auto-approved member. What is missing is a producer:

```console
$ git grep -n "KnowledgeCandidate(" -- packages/theurian-core/src
$
```

**That absence is itself pinned, in three places and two registers.**
`tests/unit/test_adr_0030_claims.py::test_nothing_in_the_shipped_package_constructs_a_knowledge_candidate`
walks every module under `src/` for a call — both the bare-name and the
attribute spelling — and asserts the site list is empty, with
`::test_the_construction_scan_sees_both_spellings_of_a_construction` as the
positive control that makes the empty answer mean something. Two documents rest
on it and are named in the failure message: `README.md`'s *AI proposes, humans
approve* row, which quotes the grep, and
`docs/architecture/review-knowledge.md`'s "no code path generates a candidate".
A third record leans on it from the security side: `docs/security/threat-model.md`
lists *No promotion path out of review evidence* as a control in T-24's holds
table, citing that same test by name.

So the producer this ADR designs is a change that makes four recorded claims
false at once, and the pin exists so that the commit "cannot land without
meeting them" — the test's own words.

**The model question is the reason this was sequenced last.** ADR-0030 recorded
it as open rather than answering it: FR-V2 and FR-V3 "raise the model question
([ADR-0009](0009-no-llm-vendor-lock-in.md)) that ingestion does not". This ADR
answers it.

## Decision

### 1. Theurian runs no model; the calling agent authors the generalization

The tool's caller supplies the generalization — the title, the body, the
`kind` and the `category`. Theurian does three things, all of them
verification and packaging:

1. **Loads the ingested thread** from the review evidence store that
   `theurian review build` projected out of `.theurian/review/`. No network, no
   `gh` spawn, no fetch — the same read-only posture `review.search` already
   has.
2. **Recomputes the `PromotionGate`** from the stored signals (decision 3) and
   constructs the `KnowledgeCandidate`, which refuses an unmet gate at
   construction (`domain/review.py`, `KnowledgeCandidate.__post_init__`).
3. **Routes it through `ProposalService.draft()`** into an ordinary proposal
   directory — ADR-0013 point 2's shape and nothing special — with
   `trustLevel: inferred`, which the candidate type already fixes with
   `init=False`.

**This resolves ADR-0009's question in the direction the product already
leans.** Every model-dependent capability sits behind a port with an in-tree
deterministic default, and the honest reading of ADR-0009's Milestone 5
amendment is that the in-tree defaults are stand-ins that keep a code path
exercised rather than capabilities. A generalization is not a retrieval step
with a weak fallback; it is a judgement, and there is no deterministic default
for a judgement. The two available answers were *Theurian orchestrates a
model* — a new port, a new configuration surface, a new dependency for anyone
who wants it to work, and a fabricated generalization for anyone who does
not — or *the caller, which is already a model, does the part only a model can
do*. The second is chosen.

**FR-V5 stays structural rather than becoming a fallback path.** ADR-0030's
decision 5 says "FR-V5 is satisfied structurally rather than by a fallback path:
no model exists anywhere in the ingest path", and slice 2 made that measurable:
`tests/integration/test_review_ingest_is_model_free.py::test_no_callable_in_the_built_pipeline_reaches_a_model`
walks the built ingest pipeline's object graph, with three planted-model cases
proving it can go RED. **This decision does not touch the ingest path at all**,
so that property is preserved by construction rather than re-argued: candidate
generation is a separate call, made by a separate caller, after ingestion has
already landed its files.

### 2. Name honesty: the tool keeps its published name, and this ADR states exactly what "generate" covers

The wire name is `review.generateKnowledgeCandidate`, which
`docs/protocol/mcp-tools.md` has published in its planned-tools table as
*"Planned write-intent | Emit a proposal; no approved-state write"*. It is not
renamed: ADR-0030 rejected inventing a new name for a published one on the
ground that "a tool name is a wire contract", and adding a second name "would
leave a published one orphaned and make clients choose between them".

**But "generate" must not be readable as "Theurian summarized."** The split is
therefore written here rather than left to a reader's inference:

| Theurian computes | Theurian does not compute |
| :-- | :-- |
| gate recomputation from the stored record (decision 3) | the generalization text — the candidate's `title` and `body` |
| candidate construction, with `trustLevel: inferred` fixed by the type | the `category` judgement (decision 6) |
| the proposal directory, through `ProposalService.draft()` | any summarization, ranking or rewriting of the thread |

The tool's own description, its input schema description and this table say the
same thing, and slice B5 owes the property that they agree.

### 3. Gate signals are recomputed from the ingested record, never supplied by the caller

The seven `PromotionGate` signals — `pull_request_merged`, `thread_resolved`,
`fix_commit_present`, `not_dismissed_or_outdated`, `ci_successful`,
`generalizable`, `has_evidence` — are read out of the stored evidence record.
**None of them is a wire input.**

The reason is direct: a caller-asserted gate is a forgeable promotion signal.
The gate's stated job, in `review-knowledge.md`'s own words, is to answer
*"should someone look at this?"* on **observed facts**, "not a model's opinion,
so the decision is auditable". A gate the caller fills is a gate the caller
decides, and the tool would then be a proposal generator with a ceremony
attached.

`generalizable` is the one that looks like a judgement and is not treated as a
caller input either. Whether it is derived from stored structure or is refused
as underivable in v1 is slice B5's to settle against the record's actual
fields — what this ADR fixes is that **the caller does not assert it**.

### 4. `ci_successful` becomes tri-state, and unknown is treated as UNMET **and named**

`PromotionGate.ci_successful` is a required `bool` today (`domain/review.py`).
ADR-0030 decision 5 left it untouched deliberately and recorded why: "`None` is
unrepresentable there today, so *how the gate should treat unknown* is a real
open question — assigned to the candidate-generation design, not answered here."
Meanwhile `ReviewEvent.ci_successful` is already `bool | None`, and the adapter
maps "anything else — pending, expected, absent, or a value this version of the
adapter does not recognise" to `None`.

**The decision: the gate field becomes `bool | None`, and `None` does not
satisfy the gate.** The refusal names `ci_successful` among the unmet signals,
which `PromotionGate.unmet()` already does for a `False` — so the caller is told
*which* signal to obtain rather than being told the thread is unsuitable.

The asymmetry is the point. Unknown and failed are both unmet, and they are not
the same message: a failed CI run says *this thread's fix did not pass*, and an
unknown one says *nobody has told Theurian whether it did*. Flattening them into
one boolean at the adapter is what loses that, which is why the flattening moves
out of the adapter and into the gate's own type.

### 5. The refusal must not become a withheld-versus-absent oracle, and the shape is designed now

A refusal that names an unmet gate signal is a refusal that says something about
a thread. Today that discloses nothing: every ingested thread was fetched from a
public allowlisted repository (ADR-0030 decision 2) and served, so "this thread
has no fix commit" is a fact about material the caller could read anyway.

**That changes with [#575](https://github.com/theurian/theurian/issues/575).**
Private-repository ingestion is where a withheld class first exists — private
repositories, the `securityRelated` marking, and the uniform serve refusal
ADR-0029 decision 6 specifies, whose stated requirement is that "the refusal
must not distinguish 'an embargoed finding exists and is withheld' from 'no such
finding exists', or the refusal is itself a disclosure channel."

So the refusal shape is fixed **now**, while it costs nothing, rather than
retrofitted onto a shipped surface later:

> A thread outside the caller's view refuses **indistinguishably** from a
> thread that does not exist. A gate-signal refusal is reachable only for a
> thread the caller may see.

This is the disclosure family Milestone 5 enumerated as *an error that fires for
one input and not another*, and naming it as a family rather than as a case is
what stops the sibling from being met as a surprise. The serving layer already
holds the analogous property for `review.search`, which is the shape to follow:
`tests/integration/test_review_search_tool.py::test_a_bad_filter_is_refused_the_same_way_whether_or_not_the_project_resolves`
and
`::test_a_store_this_installation_did_not_build_is_refused_in_the_words_a_missing_one_gets`.

**The two-corpora obligation extends to this tool's responses.** ADR-0029's
closure rule is that a new surface owes its own round, and ADR-0030 slice 3 built
the instrument for `review.search`: one query battery against an index that
**held** withheld rows and an index that **never did**, asserted identical at
both the store and the tool layer, with controls proving the battery reaches the
withheld records. `review.generateKnowledgeCandidate` is a new surface and
inherits nothing; it owes its own two-corpora equality at slice B5, over its
responses **and its refusals**.

### 6. `category` is the eleven-member FR-V2 enum, caller-supplied and schema-validated

`ReviewCommentCategory` (`domain/enums.py`) has exactly eleven members —
`specification-gap`, `architecture-rule`, `security-rule`, `performance-rule`,
`reliability-rule`, `coding-convention`, `testing-rule`, `domain-rule`,
`rejected-approach`, `known-exception`, `incident-prevention` — and
`docs/architecture/review-knowledge.md` names the same eleven.

The caller chooses one, and ADR-0031's published input schema constrains it to
that enum. The cost of a wrong choice is bounded and the document already prices
it: "Classification is a hint that routes a candidate to the right knowledge kind
and namespace. It is not a truth claim, and a misclassification costs a reviewer
one correction — not a wrong rule in the knowledge base." A human reads the
proposal before anything becomes approved knowledge, and re-categorising is an
edit to a draft.

This is also why classification is not a place a model would earn its keep
inside Theurian: the failure mode is a reviewer's one-line correction, and the
control that makes it survivable is FR-V4, which no classifier changes.

## Consequences

### Positive

- **FR-V3 ships without a model dependency, a new port, or an API key.** The
  core's runtime dependency list is unchanged, and a project with no model
  configuration is not handed a degraded generator — it is handed a tool its own
  agent drives.
- **The gate stays auditable.** Every signal is read from the record, so "why
  was no candidate generated?" is answered with facts rather than with an
  opinion, which is the property `review-knowledge.md` claims for it.
- **Unknown CI stops being unrepresentable.** ADR-0030's open question closes in
  the direction that neither fabricates a measurement nor promotes unverified
  work.
- **The disclosure shape is decided before there is anything to disclose**,
  which is the only time it is cheap.

### Negative

- **Candidate quality is the caller's.** Theurian verifies the gate and refuses
  a thread that has not earned a human's attention; it does not and cannot check
  whether the generalization the caller wrote is a fair reading of the thread.
  FR-V4 is what absorbs that — a human reviews every candidate — and this ADR
  does not pretend the gate covers it.
- **A new prompt-injection path opens, and it is the one the roadmap names.**
  `docs/roadmap.md`'s Phase B risks row states it: "review text is untrusted
  content, and turning it into a candidate is precisely the path by which an
  injected instruction becomes a knowledge candidate." The mitigations are the
  existing ones — the SEC-15 triple on every served row, and never-auto-approve
  — and the threat model's T-3 section owes the candidate path as a named
  addition at slice B5.
- **Four recorded claims become false in one commit.** The
  `KnowledgeCandidate`-is-never-constructed pin, its two named documents and the
  T-24 control row all move together, and the pin exists precisely to make that
  simultaneous. A commit that moves the code and leaves any one of them is the
  defect the pin catches.
- **The tri-state widens a domain type.** `PromotionGate.ci_successful` moving
  from `bool` to `bool | None` is a breaking change to the domain model, and
  `is_satisfied` / `unmet()` read it. The migration cost is measurable at
  implementation time and is not assumed away here.

### Neutral

- **The ingest path is untouched**, so FR-V5's measured property and the
  model-free walk that holds it are unaffected.
- **`review.search` is untouched.** This tool reads the same store and adds no
  new read surface over it.
- **No flag moves.** `writeTools` is already `true` by slice B4 (ADR-0032
  decision 5), and the flag answers whether any write-intent tool exists, not
  how many.

## What this does not close

1. **Private-repository ingestion and the `securityRelated` marking.** Owned by
   [#575](https://github.com/theurian/theurian/issues/575). Decision 5 designs
   the refusal *shape* for the day that class exists; it does not build the
   class.
2. **How `generalizable` is derived.** Slice B5 settles it against the record's
   fields, within the constraint that the caller does not assert it.
3. **The other four planned `review.*` tools** — `getThread`, `findSimilar`,
   `getDecisions`, `listUnresolved`. Unchanged by this ADR.
4. **Automatic candidate quality scoring.** `docs/roadmap.md`'s Phase B
   benchmark row records it as a non-goal: "Candidate quality is judged by the
   human reviewing."
5. **A Theurian-side generalization model.** Not forbidden for ever — it would
   be a decision with its own reasoning and its own ADR, and decision 1 is what
   it would have to argue against.
6. **The T-3 threat-model entry for the candidate path.** Owed at slice B5,
   named on the roadmap's own Phase B risks row, and not written here.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **Theurian orchestrates a model behind an ADR-0009 port, with an in-tree default** | Every other port's in-tree default is deterministic and grounded — the extractive summarizer "never hallucinates because it never generates", the identity reranker preserves order. There is no deterministic default for *write a rule that generalizes this thread*: a default that produced text would be fabricating the one thing the caller was supposed to supply, and a default that produced nothing would make the whole capability configuration-gated. ADR-0009's Milestone 5 amendment is the precedent for grading an in-tree default honestly rather than calling it usable. |
| **Accept the gate signals as wire inputs, trusting the caller** | A forgeable promotion signal. FR-V4 would still stop auto-approval, so nothing becomes approved knowledge — but the gate's stated purpose is to decide what is *worth a human's attention*, and a caller-filled gate makes that decision the caller's. The result is a proposal generator with a gate-shaped ceremony, which is worse than no gate because it reads as a check. |
| **Keep `ci_successful` a required `bool` and have the adapter fill it** | This is exactly the shape ADR-0030 rejected for `resolved_at`: "A required field filled by the adapter is a fabricated measurement that every downstream consumer reads as real." Filling unknown with `True` promotes unverified work; filling it with `False` reports a failure that did not happen. The rejection is quoted rather than re-derived because it is the same defect one field over. |
| **Treat unknown CI as satisfying the gate** | The gate would promote a thread whose fix nobody has seen pass, and it would do so silently — the caller could not tell the difference between a green run and no run at all. The gate exists to decide whether a thread has earned a human's attention; "we do not know" has not earned it. |
| **Invent a new tool name now that the semantics are settled** | `review.generateKnowledgeCandidate` is published as planned in `docs/protocol/mcp-tools.md`. ADR-0030 rejected the same move for `review.search` on the ground that a tool name is a wire contract; decision 2 carries the name honesty in prose instead, which is where a semantics clarification belongs. |
| **Let the refusal say plainly that a thread is withheld** | It would be a clearer message and a disclosure channel. Today it discloses nothing because no withheld class exists; the moment #575 creates one, a refusal that distinguishes *withheld* from *absent* answers a question the caller was refused. Designing the indistinguishable shape while the corpus has no withheld rows costs one sentence; retrofitting it onto a shipped refusal costs a disclosure round. |
| **Defer the whole disclosure question to #575, since nothing is withheld yet** | This is the deferral ADR-0029's closure rule warns about — a new surface owes its own round, and a round deferred to the change that creates the withheld class is a round run under time pressure on a surface already shipped. The two-corpora fixture is synthetic either way (ADR-0030 decision 6 records why that is the only way to have a withheld row in a corpus whose scope excludes them), so nothing is gained by waiting. |

## Compliance

**This ADR ships no behaviour, so it has no shipped test to name.** Its
enforcement at design time is the measurements it cites; its enforcement at
implementation time is the tests slice B5 owes. The names below are the
properties an implementation must pin, not files that exist today — the same
honest split [ADR-0030](0030-github-review-ingestion-spawns-gh.md) states for
the same reason.

Measured now, and reproducible from this ADR (2026-09-12, `be977ea7`):

- `KnowledgeCandidate` is constructed **nowhere** in the shipped package:
  `git grep -n "KnowledgeCandidate(" -- packages/theurian-core/src` answers
  nothing, and
  `tests/unit/test_adr_0030_claims.py::test_nothing_in_the_shipped_package_constructs_a_knowledge_candidate`
  holds it with a positive control.
- `ReviewCommentCategory` has exactly **11** members (`domain/enums.py`), and
  `docs/architecture/review-knowledge.md`'s Classification section names the
  same eleven.
- `PromotionGate.ci_successful` is a required `bool` and
  `ReviewEvent.ci_successful` is `bool | None` (`domain/review.py`), which is
  the asymmetry decision 4 closes.
- `KnowledgeCandidate` refuses construction with no evidence, an empty body and
  an unmet gate, and `trust_level` is `init=False`
  (`domain/review.py`, `__post_init__`), each already pinned in
  `tests/unit/test_project_and_traceability.py` —
  `::test_candidate_without_evidence_is_rejected_at_generation`,
  `::test_candidate_with_an_empty_body_is_rejected`,
  `::test_candidate_with_an_unmet_gate_is_rejected` and
  `::test_candidate_has_no_self_approval_method`. **These four stay true after
  this change** and are named here so nobody mistakes them for pins slice B5
  must move.

**One non-`docs/` file moved with this ADR, and it is named rather than
counted.** *What this does not close* item 1 names the live owner of the
private-repository arm, which is the form
`tools/audit/owner_position_cites.py` exists to require — and that issue
postdates the audit's tracker snapshot, so its offline run reads a live owner as
no owner. Three ledger rows already record exactly that reading for the
identical cite in ADR-0029 and ADR-0030; this branch adds the fourth, carrying
its own tracker measurement dated 2026-09-12. The alternative was to reword item
1 out of owner position, which is the dodge that audit is built to catch.

**This paragraph deliberately carries no issue link**, and the reason is the
audit's own: a sentence that *describes* an ownership cite is indistinguishable
to the key from one that *makes* one, so repeating the number here would have
produced a second unrecorded suspect about the audit rather than about the
design. The ownership claim lives in item 1, once, where the ledger row points.

Still owed, with the milestone that will satisfy it:

- **Slice B5 — the absence pins move deliberately, and all of them in one
  commit.** `tests/unit/test_adr_0030_claims.py::test_nothing_in_the_shipped_package_constructs_a_knowledge_candidate`
  and its two named documents (`README.md`'s *AI proposes, humans approve* row
  and `docs/architecture/review-knowledge.md`'s "no code path generates a
  candidate"), plus `docs/security/threat-model.md`'s T-24 holds-table row
  citing that test by name. **What replaces the pin is owed with it**: the
  claim that becomes true is *the only construction site is the candidate
  generator*, which is a pin of the same shape with a non-empty expected set,
  not a deletion.
- **Slice B5 — no model is reachable from the candidate path either.** Decision
  1 says Theurian runs no model, and that is a universal whose authority is a
  test that does not exist. Owed: a walk of the built candidate pipeline's
  object graph in the shape of
  `tests/integration/test_review_ingest_is_model_free.py::test_no_callable_in_the_built_pipeline_reaches_a_model`,
  with its planted-model controls — **and with the same recorded bound**, that a
  provider resolved through a factory one level down is invisible to it.
- **Slice B5 — the gate is recomputed and never read off the request
  (decision 3).** Owed: a test that plants gate-shaped fields in the wire input
  and asserts they change no signal, with the control that a change to the
  *stored record* does change one — without the control, a generator that
  ignored the whole gate would pass.
- **Slice B5 — unknown CI is unmet and named (decision 4).** Owed: a thread
  whose stored `ci_successful` is `None` refuses, and the refusal names
  `ci_successful` among the unmet signals; with a sibling asserting a definite
  `False` refuses too, and a definite `True` proceeds — three inputs, because
  two of them would not distinguish "unknown is unmet" from "the gate ignores
  this signal".
- **Slice B5 — the refusal is not a withheld-versus-absent oracle
  (decision 5).** Owed: one battery of requests answered identically over a
  corpus that **held** withheld threads and one that **never did**, at the tool
  layer, covering responses **and refusals**, with the control that the battery
  actually reaches the withheld threads. The fixture is synthetic, and that is
  the only way to have a withheld row in a corpus whose scope excludes them.
- **Slice B5 — the candidate lands as an ordinary proposal (decision 1).**
  Owed: a test that the proposal a generated candidate produces is one
  `theurian propose accept` accepts, driven through the shipped commands; and
  that its `trustLevel` is `inferred` on the written migration, not only on the
  in-memory object.
- **Slice B5 — the name-honesty split is stated on every surface that
  describes the tool (decision 2).** Owed: the tool description, the published
  input schema's description and this ADR's table agreeing that Theurian does
  not author the generalization. Whether the wording is *faithful* is a reading
  and no mechanical check reaches it; what a test can hold is that the surfaces
  do not diverge from each other.
- **Slice B5 — the T-3 threat-model entry gains the candidate path.**
  `docs/roadmap.md`'s Phase B risks row names it as owed; it is a prose
  obligation with no test, recorded here rather than dressed as discharged.
- **Slice B5 — [#479](https://github.com/theurian/theurian/issues/479) is
  rescoped to Phase B and owned by this slice.** It is the design-first step
  ADR-0030 was recorded under, and the candidate half is the part of it that
  outlived Milestone 8.
