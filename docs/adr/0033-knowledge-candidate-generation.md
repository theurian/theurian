# ADR-0033: `KnowledgeCandidate` generation — the caller is the model, Theurian verifies the gate and lands a proposal

- Status: accepted
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

**This ADR records a decision and ships no behaviour.** No tool registers, no
gate type changes, no candidate is constructed. The non-`docs/` changes it does
carry are named in *Compliance* — a ledger row in `tools/audit/`, and nothing
that runs at runtime. What slice B5 owes is named there too, including the
**pins this change will deliberately move**. [**2026-09-19:** true of this ADR's
own commit, and kept as history. Slice B5 landed the behaviour on
[PR #744](https://github.com/theurian/theurian/pull/744) — the tool registers,
the gate type is widened, and a candidate is constructed in one place — and the
*Compliance* amendments below name the test that discharges each owed item, or
say what is still owed and why.]

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

**That absence was itself pinned, in three places and two registers.**
`tests/unit/test_adr_0030_claims.py::test_nothing_in_the_shipped_package_constructs_a_knowledge_candidate`
walked every module under `src/` for a call — both the bare-name and the
attribute spelling — and asserted the site list was empty, with
`::test_the_construction_scan_sees_both_spellings_of_a_construction` as the
positive control that makes an empty answer mean something. Two documents rested
on it and were named in its failure message: `README.md`'s *AI proposes, humans
approve* row, which quotes the grep, and
`docs/architecture/review-knowledge.md`'s "no code path generates a candidate".
A third record leaned on it from the security side: `docs/security/threat-model.md`
listed *No promotion path out of review evidence* as a control in T-24's holds
table, citing that same test by name.

So the producer this ADR designs is a change that makes four recorded claims
false at once, and the pin exists so that the commit "cannot land without
meeting them" — the test's own words.

Slice B5 moved all four in one commit rather than deleting any of them, so the
grep above now answers one line. The pin is
`::test_the_only_construction_site_of_a_knowledge_candidate_is_the_candidate_generator`,
an equality against one recorded module instead of against the empty set; the
README row and the architecture page both state *the only construction site is
the candidate generator*; and T-24's row is *The promotion path out of review
evidence ends at an unapproved proposal*.

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
2. **Establishes the `PromotionGate`** — five signals recomputed from the stored
   record, the caller's `fixCommit` **verified against the local git
   repository**, and `generalizable` satisfied by the submission (decision 3) —
   then constructs the `KnowledgeCandidate`, which refuses an unmet gate at
   construction (`domain/review.py`, `KnowledgeCandidate.__post_init__`). The
   git read is the one thing here that touches something outside the evidence
   store, and it is local: no network, still.
3. **Routes it through `ProposalService.draft()`** into an ordinary proposal
   directory — ADR-0013 point 2's shape and nothing special — with
   `trustLevel: inferred`, which the candidate type already fixes with
   `init=False`.

**A `KnowledgeCandidate` is not a `ProposalRequest`, and the gap is eight of
sixteen fields.** The candidate carries **15** fields and the request **16**
(`dataclasses.fields` on each: `domain/review.py` and
`application/proposal_service.py:525`), and they do not nest. **Eight of the
request's sixteen have no field on the candidate to read**, and they split two
ways: four whose source this ADR has to **decide** — `owner`, `description`,
`content_type` and `author`, each with its own row below and its own reason —
and four that come **straight off the wire** under ADR-0032 decision 1's
existing rules, needing no decision here: `labels`, `scope_paths`, `namespace`,
`expected_revision`. The other eight do read the candidate — `evidence` only in
part, since `proposal.Evidence` needs four values the candidate does not carry,
and `trust_level` from a field the candidate's declaration fixes at `INFERRED`
with `init=False` rather than from anything a caller says. ADR-0032 decision 1
has its own wire-to-request table for the same reason, and this is the one for
this tool.

| `ProposalRequest` field | Where it comes from |
| :-- | :-- |
| `item_id` | `KnowledgeCandidate.proposed_item_id` |
| `title`, `body`, `kind` | the same-named candidate fields — the caller's generalization (decision 1) |
| `source_anchors` | `KnowledgeCandidate.evidence`, which is `tuple[SourceAnchor, ...]` |
| `evidence` | **built, not copied.** `proposal.Evidence` needs `agent_id`, `task_id`, `model` and `reasoning`, none of which the candidate carries; they come from the tool's own `evidence` input, exactly as ADR-0032's tools take them. Its `anchors` are the candidate's `evidence` again — the one-field-fills-two shape ADR-0032 decision 1 records |
| `owner` | **not on the candidate.** Caller-supplied, like the generalization itself |
| `description` | **not on the candidate.** Caller-supplied; a candidate's `category` and `source_thread_id` are provenance, not a description |
| `content_type` | **not on the candidate.** Fixed to `text/markdown` for this tool — a generalization is prose, and there is no file whose suffix could say otherwise (ADR-0032 decision 2) |
| `author` | **decided here.** The migration schema defines it as "Identity of the human who authored this change. Agent-generated proposals record the agent separately in `evidence.json`". So `author` is the caller-supplied human-attributable identity the wire input requires, **distinct from `evidence.agentId`**, and the two are never filled from each other |
| `trust_level` | **`KnowledgeCandidate.trust_level`, and never the wire.** The field is `field(default=TrustLevel.INFERRED, init=False)` (`domain/review.py:307`), so a candidate cannot be constructed carrying any other value; ADR-0032 decision 1's "absent means not stated" does **not** apply on this path, and the owed test below pins `inferred` on the written migration |
| `sensitivity` | **`KnowledgeCandidate.sensitivity`** (`domain/review.py`), fixed to `Sensitivity.INTERNAL` by the type's default and never set at generation — `CandidateGenerator.generate` constructs the candidate with no `sensitivity` argument — so it is never widened. There is no review-project default this tool reads; the honest form is `trust_level`'s: fixed internal by the type, never widened at generation. Not a wire field of this tool |
| `labels`, `scope_paths`, `namespace`, `expected_revision` | as ADR-0032 decision 1 has them |

`KnowledgeCandidate.generator_model` stays what its type says it is: `str | None`
recording the caller's *declared* model, provenance rather than a control.
Theurian runs no model (decision 1), so there is nothing for it to record on its
own behalf, and the field is filled from the same `evidence.model` the caller
supplies.

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
| gate recomputation from the stored record, five signals (decision 3) | the generalization text — the candidate's `title` and `body` |
| verification of the caller's `fixCommit` against the local repository (decision 3) | the `category` judgement (decision 6) |
| candidate construction, with `trustLevel: inferred` fixed by the type | whether the generalization is a *fair reading* of the thread — FR-V4's human does that |
| the proposal directory, through `ProposalService.draft()` | any summarization, ranking or rewriting of the thread |

The table is the reasoning; the sentence pair below is the quotable form of it,
and the tool's own description and its published input schema's description carry
the same bytes:

> Theurian does not author the generalization. The caller supplies the title,
> the body, the kind and the category; Theurian verifies the promotion gate and
> packages the result.

It is spelled with no dash, no backtick and no quote character, so that the
identical bytes survive Markdown prose, a JSON string and a Python string
literal — a paraphrase on any one surface is three claims a year later, not one.
What a test can hold is that the three do not diverge from each other, and
`tests/integration/test_candidate_name_honesty.py` holds exactly that: one arm
per surface asserting this sentence pair is contained in it, with
`::test_a_different_write_tool_does_not_carry_the_claim` as the control that the
containment means something. Whether the wording is *faithful* is a reading, and
no mechanical check reaches it.

### 3. No gate signal is a caller's assertion, and the seven split three ways by *how* each is established

The rule this decision enforces is **not** "every signal is read out of the
stored record". That statement was in an earlier draft of this ADR and it is
false of one signal, which is enough to have shipped a tool that refuses every
call. The rule is the one underneath it: **no signal is satisfied by the caller
saying so.** There is more than one way to meet that, and the seven signals use
three.

| Signal | How it is established |
| :-- | :-- |
| `pull_request_merged` | **Recomputed** from the stored record |
| `thread_resolved` | **Recomputed** from the stored record |
| `not_dismissed_or_outdated` | **Recomputed** from the stored record |
| `has_evidence` | **Recomputed** from the stored record — but see the note below: over any thread that *loads*, this recompute is structurally always `True` |
| `ci_successful` | **Recomputed**, tri-state — decision 4 |
| `fix_commit_present` | **Supplied and verified**: the caller names a commit; Theurian checks it against the local repository |
| `generalizable` | **Satisfied by the submission itself**: offering a generalization *is* the claim the gate forwards |

The reason no signal is a bare caller assertion is direct: a caller-asserted gate
is a forgeable promotion signal. The gate's stated job, in
`review-knowledge.md`'s own words, is to answer *"should someone look at this?"*
on **observed facts**, "not a model's opinion, so the decision is auditable". A
gate the caller fills is a gate the caller decides, and the tool would be a
proposal generator with a ceremony attached.

**`has_evidence` is a recompute that cannot come out `False`, and saying so here
keeps a later test from being written against it.** `ReviewThread.__post_init__`
(`domain/review.py:170-172`) raises `InvariantViolationError` on an empty
`comments` tuple, and the codec decodes every stored thread *through* that
constructor (`infrastructure/review_evidence/codec.py:347`). So a thread with no
comments cannot be loaded, and a `has_evidence` recomputed from the thread's
comments is `True` over the whole reachable domain. The signal stays in the gate
— it is a real property, held by construction one layer down — but it is a
**vacuous** recompute, and the owed slice-B5 control that "a change to the
*stored record* does change one signal" must therefore pick a different signal
to change. Left unsaid, that control is the one an implementer would reach for
first and the one that cannot fail.

#### `fix_commit_present` is supplied and verified, because the adapter has never produced one

**The measurement that forced this.** `ReviewResolution.fix_commit` is
`str | None` (`domain/review.py`), and the shipped GitHub adapter never assigns
it: `review_provider.py` builds every thread's `ReviewResolution` with `state`
and `resolved_by` and nothing else, and the `REVIEW_THREADS` GraphQL document
(`infrastructure/github/queries.py`) selects no field that could carry a fix
commit — `PullRequestReviewThread` records none, the same shape ADR-0030
decision 5 met for `resolvedAt`. Every ingested thread therefore has
`fix_commit is None`, so a gate that read the signal off the record would
**refuse every ingested thread**, for ever, and the tool's only reachable
behaviour would be a refusal.

**The one route to a truthy signal today is worse than that.** A hand-authored
evidence file reaches `fix_commit` through the codec's `_optional_string`
(`infrastructure/review_evidence/codec.py`), which checks that the value is a
string and nothing else — not a SHA, not a commit that exists. So the earlier
draft's rule inverted its own anti-forgery rationale: the honest ingested record
fails the gate, and a fabricated file passes it.

**The decision.** The caller names a commit SHA on the wire, and **Theurian
verifies it against the local git repository before the gate passes**: the
commit exists, and — where the stored thread carries a `file_path` — that commit
touches that path. The signal is satisfied by the verification, not by the
caller's word. This is the same posture as decision 1: the caller supplies what
only it knows, and Theurian checks what can be checked.

**What the verification establishes is narrower than "this is the thread's fix",
and the gap is measurable.** Passing the check means *a commit exists that
touched the same file*. The choice set that satisfies it is the file's whole
history, which for an ordinary path in this repository is large. Measured at
`be977ea7`, on three paths a review thread could plausibly be anchored to:

```console
$ for p in packages/theurian-core/src/theurian/application/proposal_service.py \
           packages/theurian-core/src/theurian/mcp/tools.py \
           docs/security/threat-model.md; do
    printf '%s\t%s\n' "$(git rev-list --count be977ea7 -- $p)" "$p"
  done
15	packages/theurian-core/src/theurian/application/proposal_service.py
25	packages/theurian-core/src/theurian/mcp/tools.py
71	docs/security/threat-model.md
```

So a caller who wants a truthy `fix_commit_present` and does not have the real
fix commit has between 15 and 71 accepted answers to choose from on these three
paths alone, and the check cannot tell which. What it does close is the whole
class of values that touch *nothing* — a random forty hex digits, a commit from
another repository, the fabricated `"e" * 40` the suite's own fixtures use. That
is the bound, and it is a real one: it is the difference between a signal the
caller writes and a signal the caller has to find something real to satisfy.

**Fix-ness is FR-V4's human, and this ADR does not move it.** Whether the named
commit is *this thread's* fix is a reading of the diff against the conversation,
which is exactly the judgement FR-V4 assigns to the human reviewing the
candidate — the same authority decision 2's table already gives the "fair
reading" question to. The gate's job is to decide whether a thread has earned
that human's attention; it is not to pre-empt the answer.

**The `file_path is None` branch is a decision slice B5 owes, not an
implementation detail.** `ReviewThread.file_path` is `str | None`
(`domain/review.py:162`) and the shipped adapter yields `None` whenever GitHub's
node carries no string `path`:

```text
# infrastructure/github/review_provider.py:665 -- one argument of the
# ReviewThread(...) construction, quoted as it stands
file_path=node.get("path") if isinstance(node.get("path"), str) else None,
```

For such a thread the "touches that path" half has no path to check, and the
verification degenerates to bare existence — which, against a repository whose
whole history is an accepted answer (`git rev-list --count be977ea7` → **285**),
is very nearly no check at all. The two answers are *refuse the call, because
the signal cannot be verified for this thread* and *pass on existence alone,
with the weaker basis recorded in the refusal-free path*. This ADR does not pick
one, and slice B5 records the choice
with its reasoning rather than letting the `None` branch be settled by whichever
line an implementer writes first.

> **Amended in slice B5 (2026-09-18, before the implementation): the branch is
> decided — a thread with no `file_path` refuses.** `fix_commit_present` cannot
> be verified for such a thread in v1, and an unverifiable signal does not pass.
>
> **Rejected: pass on bare existence with the weaker basis recorded.** Existence
> alone is the 285-answer choice set measured above, which is very nearly no
> check — and it would arrive exactly on the thread class where verification is
> weakest. The two mistakes are also not symmetrical: widening refuse into
> accept later is additive, while narrowing accept into refuse is a breaking
> behaviour change, so refusing first is what keeps the later decision free.
>
> **The refusal names the thread property plainly, and is *not* folded into the
> single commit-verification refusal below.** *This thread carries no file
> anchor, so the fix-commit signal cannot be verified* discloses nothing:
> `filePath` is a published key on every `review.search` record
> (`mcp/review_search.py`'s `AUTHOR_CONTROLLED_FIELDS` and `review_record`,
> which emits every key including the `None` ones), so the property is already
> caller-visible. Decision 5's bind governs withheld-versus-absent and
> distinctions about repository contents; this is neither. The distinct message
> is also the honest instruction — *this thread cannot generate a candidate in
> v1* — instead of sending the caller to hunt for a better commit.
>
> **The excluded class, stated rather than left to be discovered:** a thread
> stored with `file_path` `None` cannot generate a candidate in v1. The adapter
> yields `None` for any node whose `path` is not a string
> (`review_provider.py:665`); which GitHub thread kinds produce such a node is
> not measured here, so the class is named by what Theurian stores and not by a
> claim about the provider's schema.
>
> Consulted before recording, this being a judgment and not a Blocking Issue:
> the orchestrator's recommendation, an independent Codex read (concurred, and
> raised the refusal-shape question the paragraph above answers), and the
> `watchdog` agent (concurred; not a Blocking Issue). The module gains its half
> of this record when the verification lands.

**The verification's own refusals are inside decision 5's bind.** A check that
answers "that commit does not exist" differently from "that commit does not
touch this thread's file" tells a caller something about the repository's
contents through a review-evidence tool. The two are one refusal, and decision 5
governs it for the same reason it governs the gate-signal refusals: the
distinguishing information is about material the caller has not been granted.

The residual is stated rather than implied: a **hand-authored stored record**
can still fabricate `fix_commit`, which is T-24's accepted residual — a review
evidence directory is source rather than derived state, and this ADR does not
change that. What B5's verification closes is the *wire* path, where the caller
is the untrusted party.

#### `generalizable` is satisfied by the submission

This is the signal that looks like a judgement, and the underivable branch the
earlier draft left open — *derive it from stored structure, or refuse it as
underivable in v1* — is settled here rather than carried, because one arm of it
ships a tool that structurally cannot produce a candidate while
`writeTools: true` advertises it. That is a false capability claim, which is the
defect ADR-0026 exists to prevent.

The resolution is that there is nothing to derive. Decision 1 has the caller
author the generalization; a call that carries a title and a body **is** the
claim that this thread generalizes, and the gate's job is to decide whether the
claim reaches a human, not to adjudicate it. FR-V4 — a human reviews every
candidate — is what grades the claim, and `review-knowledge.md` already prices a
wrong one at "a reviewer's one-line correction".

So `generalizable` is satisfied by a well-formed submission and is **not a wire
field**: there is no boolean the caller sets, which is what keeps it off the
forgeable list. A caller cannot assert it *false* either, which costs nothing —
a caller who does not think a thread generalizes does not call the tool.

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

**Unknown and failed get different refusal text, and the mechanism is named
because `unmet()` cannot produce it.** `unmet()` returns the *names* of the
signals that are falsy (`domain/review.py`), and `None` and `False` are both
falsy — so it reports `"ci_successful"` for either and the two messages would be
identical. The distinction therefore comes from a second read: the refusal
composes its sentence for `ci_successful` by looking at the stored tri-state
value, `None` giving *nobody has told Theurian whether this thread's fix passed*
and `False` giving *this thread's fix did not pass*. Two sentences from one
unmet name.

That is worth its cost because the two are different instructions to the caller:
one says go and get a CI result, the other says this thread is not a candidate.
Flattening them into one boolean at the adapter is what loses that, which is why
the flattening moves out of the adapter and into the gate's own type — and why
the tri-state has to reach the message and not only the predicate.

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
> thread that does not exist — in its text **and in how long it takes**. A
> gate-signal refusal is reachable only for a thread the caller may see.
>
> The same rule governs the **commit-verification** refusals decision 3 adds:
> *that commit does not exist* and *that commit does not touch this thread's
> file* arrive as one refusal, because the difference between them is a fact
> about the repository's contents rather than about the caller's request.

This is the disclosure family Milestone 5 enumerated as *an error that fires for
one input and not another*, and naming it as a family rather than as a case is
what stops the sibling from being met as a surprise. The second paragraph is the
same family met on a different input: the *thread* half distinguishes withheld
from absent, and the *commit* half distinguishes two facts about the repository,
and one rule has to cover both or the refusal path grows a second policy.

**The duration half is in the bind rather than in a residual, and the reason is
that the asymmetry is structural.** Recomputing a gate for a withheld thread
loads the record, reads five signals and verifies a commit against git;
answering for an id that names nothing does none of that. The second is
strictly less work, so a caller timing two calls learns which ids exist — the
*duration* family Milestone 5 met as a surprise on the retrieval side. Binding
it now costs a sentence and an owed measurement; retrofitting it means proving a
timing property about a shipped refusal path. ADR-0030's form — record the reach
and accept it — is the alternative, and it is not taken here because there is
nothing shipped yet to price it against. The serving layer already
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
that enum. `category` is a caller-supplied hint recorded on the candidate, **not
a router**: it chooses neither the knowledge `kind` nor the `namespace` — both are
the caller's own wire fields (decision 1) — and today it is not carried into the
drafted proposal at all, since `ProposalRequest` has no `category` field and
`CandidateGenerator._request` maps none
([#754](https://github.com/theurian/theurian/issues/754) tracks carrying it). The
cost of a wrong choice is bounded, and `docs/architecture/review-knowledge.md`
prices it: a misclassification "costs a reviewer one correction — not a wrong rule
in the knowledge base", because a human reads the proposal before anything becomes
approved knowledge and re-categorising is an edit to a draft.

This is also why classification is not a place a model would earn its keep
inside Theurian: the failure mode is a reviewer's one-line correction, and the
control that makes it survivable is FR-V4, which no classifier changes.

## Consequences

### Positive

- **FR-V3 ships without a model dependency, a new port, or an API key.** The
  core's runtime dependency list is unchanged, and a project with no model
  configuration is not handed a degraded generator — it is handed a tool its own
  agent drives.
- **The gate stays auditable.** Every signal is established by something a
  reader can check afterwards, and decision 3 gives three such sources, not
  two: **a stored record** (the five recomputed signals), **a commit in the
  repository** (`fix_commit_present`), or **the submission the proposal itself
  carries** (`generalizable`). So "why was no candidate generated?" is answered
  with facts rather than with an opinion, which is the property
  `review-knowledge.md` claims for it — with the third source's audit trail
  being the proposal directory itself rather than a check.
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
- **No test in the suite links a `PromotionGate` to an adapter-shaped record,
  and that is why this survived design review.** The suite constructs a
  `PromotionGate` in exactly one place, and it hardcodes every signal to a
  literal `True`:

  ```console
  $ git grep -n "PromotionGate(" be977ea7 -- packages tests tools docs schemas | cut -d: -f2,3
  docs/architecture/review-knowledge.md:171
  packages/theurian-core/tests/unit/test_project_and_traceability.py:569
  ```

  The key takes the sha because this paragraph names the symbol, so an unanchored
  run against `HEAD` returns this ADR's own sentences as well.

  The first is a prose example. The second is `_gate()`
  (`tests/unit/test_project_and_traceability.py:555-569`), whose `base` is
  `dict.fromkeys(("pull_request_merged", …, "has_evidence"), True)` — so
  `fix_commit_present` is satisfied by a **boolean literal**, with no commit
  string anywhere near it, and `_candidate()` builds every candidate in the
  suite on top of it. Nothing anywhere joins the gate to a `ReviewResolution`,
  so no test could have gone red when a design assumed the record supplied a
  `fix_commit`.

  The nine `fix_commit="e" * 40` fixtures are a **separate** population and a
  weaker statement: they are `ReviewResolution` constructions in the ingestion,
  evidence-store, search, wire-contract and landing-gate tests, and none of them
  reaches a gate. `git grep -c` reports them per file, not per line, which is the
  shape of the key:

  ```console
  $ git grep -c 'fix_commit="e" \* 40' -- packages tests
  packages/theurian-core/tests/integration/test_review_search_builder.py:1
  packages/theurian-core/tests/integration/test_review_search_tool.py:1
  packages/theurian-core/tests/integration/test_wire_contract.py:1
  packages/theurian-core/tests/unit/test_review_evidence_store.py:1
  packages/theurian-core/tests/unit/test_review_ingest_service.py:1
  packages/theurian-core/tests/unit/test_review_landing_gate.py:4
  ```

  **6 lines of output summing to 9 matches across 6 files** —
  `git grep -o ... | wc -l` answers **9** directly. An earlier draft of this ADR
  read that as "9 lines across 6 files" *and* attributed the survival of the
  false premise to those nine; both are corrected here, and the correction makes
  the point stronger rather than weaker: the gap is not that the fixtures use a
  fabricated commit, it is that the gate has never been driven from a record at
  all.

  Slice B5 owes at least one gate test driven from a record the **real adapter
  shape** produces — that is, with `fix_commit` absent — so the next design that
  leans on this field meets the truth rather than the fixture.
- **A verification step adds a git read to the candidate path.** `fixCommit`'s
  check reads the local repository, which is work the tool did not previously
  do, and it is inside the two-corpora timing bind (decision 5) rather than
  outside it.

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
2. **Whether a fabricated `fix_commit` in a hand-authored stored record is
   detected.** It is not, and that is T-24's accepted residual: a review evidence
   directory is source rather than derived state, so a clone can deliver records
   this installation never fetched. Decision 3's verification covers the **wire**
   path, where the caller is the untrusted party; it does not vouch for
   `.theurian/review/`, and nothing in this ADR claims it does.
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
7. **Whether the verified `fixCommit` is *this thread's* fix.** Decision 3's
   check establishes that a commit exists and — where a `file_path` is
   stored — that it touched that file; the choice set satisfying that is the
   file's history, measured at 15, 25 and 71 commits on three plausible paths.
   Narrowing it is FR-V4's human, and this ADR does not propose a machine that
   would replace them.
8. **Rate, size and concurrency bounds on the candidate call's git read.** The
   verification spawns git per call on a daemon-reachable path, and no recorded
   limit bounds how often or how expensively a caller may ask for it. That is
   the T-6 family's, not this one's, and it is the sibling of
   [ADR-0032](0032-the-write-intent-mcp-tool-surface.md)'s own item 6;
   [#26](https://github.com/theurian/theurian/issues/26)'s concurrency cap is
   the precedent for how such a bound is recorded.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **Theurian orchestrates a model behind an ADR-0009 port, with an in-tree default** | Every other port's in-tree default is deterministic and grounded — the extractive summarizer "never hallucinates because it never generates", the identity reranker preserves order. There is no deterministic default for *write a rule that generalizes this thread*: a default that produced text would be fabricating the one thing the caller was supposed to supply, and a default that produced nothing would make the whole capability configuration-gated. ADR-0009's Milestone 5 amendment is the precedent for grading an in-tree default honestly rather than calling it usable. |
| **Accept the gate signals as wire inputs, trusting the caller** | A forgeable promotion signal. FR-V4 would still stop auto-approval, so nothing becomes approved knowledge — but the gate's stated purpose is to decide what is *worth a human's attention*, and a caller-filled gate makes that decision the caller's. The result is a proposal generator with a gate-shaped ceremony, which is worse than no gate because it reads as a check. `fixCommit` is not this: a supplied value Theurian *verifies against git* is satisfied by the verification, not by the assertion. |
| **Read `fix_commit_present` off the stored record, as an earlier draft of this ADR said** | Measured false: the adapter never assigns `fix_commit`, so every ingested thread would refuse and the tool's only reachable behaviour would be a refusal. The one route to a truthy signal is a hand-authored evidence file the codec accepts unvalidated — so the rule would admit the fabricated record and refuse the honest one, inverting its own rationale. |
| **Extend ingestion to fetch a fix commit** | GitHub's thread object records none — the same shape ADR-0030 decision 5 met for `resolvedAt`, where "a required field filled by the adapter is a fabricated measurement". There is no field to select; inventing one (the PR's `mergeCommit`, say) would answer a different question, since a merge commit is the pull request's and `fix_commit_present` is the thread's. |
| **Drop `fix_commit_present` from the gate** | It weakens FR-V4's gate by one signal to avoid designing one, and the signal is the one that distinguishes *a conversation was resolved* from *something was changed because of it*. Removing a check because its input is hard to obtain is how a gate becomes a ceremony. |
| **Make `fixCommit` tri-state, with unknown treated as unmet (decision 4's shape)** | It is decision 4's shape applied where decision 4's premise does not hold. `ci_successful` is unknown for *some* threads; `fix_commit` is unknown for **all** of them, so tri-state-unknown-unmet refuses every thread — the measured defect, reached by a different route. |
| **Derive `generalizable` from stored structure, or refuse it as underivable in v1** | The second arm ships a tool that structurally cannot produce a candidate while `system.capabilities` advertises `writeTools: true` — a false capability claim, the defect ADR-0026 exists to prevent. The first arm has nothing to derive from: the record carries a thread, and whether it generalizes is not in it. Decision 3 settles it instead: the submission is the claim, and FR-V4 grades it. |
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

  > **Amended in slice B5 (2026-09-18): the producer landed and the pin moved
  > with it.** The measurement above stands as the 2026-09-12 reading at
  > `be977ea7`; it is no longer the current one. The command answers one line —
  > `application/candidate_generation.py` — and the pin is
  > `::test_the_only_construction_site_of_a_knowledge_candidate_is_the_candidate_generator`,
  > the same scan against one recorded module instead of against the empty set,
  > with the same positive control.
- `ReviewCommentCategory` has exactly **11** members (`domain/enums.py`), and
  `docs/architecture/review-knowledge.md`'s Classification section names the
  same eleven.
- `PromotionGate.ci_successful` is a required `bool` and
  `ReviewEvent.ci_successful` is `bool | None` (`domain/review.py`), which is
  the asymmetry decision 4 closes.

  > **Amended in slice B5 (2026-09-18, the branch commit `ca6246ae` of
  > [PR #744](https://github.com/theurian/theurian/pull/744)): the asymmetry is
  > closed.** The measurement above stands as the 2026-09-12 reading at
  > `be977ea7`; it is no longer the current one.
  > `PromotionGate.ci_successful` is `bool | None`, and `None` does not satisfy
  > the gate. The message half of decision 4 — a `None` refusal that differs
  > from a `False` one — is not in that commit and lands with the tool
  > registration; the *Still owed* item below carries it.
- `KnowledgeCandidate` refuses construction with no evidence, an empty body and
  an unmet gate — each pinned in `tests/unit/test_project_and_traceability.py`
  by `::test_candidate_without_evidence_is_rejected_at_generation`,
  `::test_candidate_with_an_empty_body_is_rejected` and
  `::test_candidate_with_an_unmet_gate_is_rejected`, all three reading
  `KnowledgeCandidate.__post_init__`.
- **`trust_level` is `init=False` on the *field declaration*, not a check.**
  `trust_level: TrustLevel = field(default=TrustLevel.INFERRED, init=False)`
  (`domain/review.py`), and the pin is
  `tests/unit/test_project_and_traceability.py::test_a_candidate_cannot_be_constructed_with_a_trust_level`,
  which expects a `TypeError` naming `trust_level`. Its own docstring records
  why the distinction matters: "`__post_init__` never looks at it, so a
  candidate claiming review-level trust is refused by the *signature* rather
  than by a check", and dropping `init=False` keeps the sibling
  `::test_candidate_is_always_inferred_never_reviewed` green while
  `KnowledgeCandidate(trust_level=TrustLevel.REVIEWED)` starts working — a
  mutation that survived the whole suite (#129).
- `::test_candidate_has_no_self_approval_method` pins something else again —
  that no `approve`/`promote`/`publish` attribute exists and `CandidateStatus`
  has no `AUTO_APPROVED` member. **All five above stay true after this change**
  and are named here so nobody mistakes them for pins slice B5 must move.
- `ReviewResolution.fix_commit` is **never assigned by the shipped adapter**:
  `infrastructure/github/review_provider.py` constructs each thread's
  `ReviewResolution` with `state` and `resolved_by` only, and
  `infrastructure/github/queries.py`'s `REVIEW_THREADS` document selects no
  field that could carry one. The codec reads it back through
  `_optional_string`, which validates that it is a string and nothing more.
- **The suite builds a `PromotionGate` in exactly one place, from literal
  booleans.**
  `git grep -n "PromotionGate(" be977ea7 -- packages tests tools docs schemas`
  answers two lines — anchored, because this ADR now names the symbol itself:
  a prose example in
  `docs/architecture/review-knowledge.md:171`, and
  `tests/unit/test_project_and_traceability.py:569`, the return of `_gate()`
  (`:555-569`), whose `base` is `dict.fromkeys((…seven signal names…), True)`.
  `fix_commit_present` is therefore satisfied by a boolean literal, and **no
  test anywhere joins a gate to a `ReviewResolution`** — which is why a design
  premise about that field could be false and stay green.
- The nine `fix_commit="e" * 40` fixtures are a **different** population:
  `ReviewResolution` constructions, none of them near a gate.
  `git grep -c 'fix_commit="e" \* 40' -- packages tests` reports them **per
  file** — 6 lines of output summing to 9 matches across 6 files; the same key
  under `git grep -o … | wc -l` answers **9**.
- **A `fixCommit` verification bounds the assertion to a commit that touched the
  same file, not to this thread's fix.** `git rev-list --count be977ea7 -- <path>`
  answers **15**, **25** and **71** on three plausible thread paths
  (`application/proposal_service.py`, `mcp/tools.py`,
  `docs/security/threat-model.md`), and **285** for the repository as a whole —
  the last being the choice set on the `file_path is None` branch, where the
  path half of the check has nothing to check.
- `ReviewThread.file_path` is `str | None` (`domain/review.py:162`) and the
  adapter yields `None` for any node whose `path` is not a string
  (`infrastructure/github/review_provider.py:665`).

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

  > **Amended in slice B5 (2026-09-18): discharged, in one commit.** The pin is
  > `::test_the_only_construction_site_of_a_knowledge_candidate_is_the_candidate_generator`,
  > an equality against `application/candidate_generation.py` with
  > `::test_the_construction_scan_sees_both_spellings_of_a_construction` still
  > the positive control. Both documents state *the only construction site is
  > the candidate generator*, held as spelling by
  > `::test_each_document_still_states_the_claim_its_scan_holds`. T-24's row is
  > now *The promotion path out of review evidence ends at an unapproved
  > proposal*, and **no test holds its wording** — the row says so itself rather
  > than leaving a reader to assume the cite it carries covers the sentence
  > around it.
- **Slice B5 — no model is reachable from the candidate path either.** Decision
  1 says Theurian runs no model, and that is a universal whose authority is a
  test that does not exist. Owed: a walk of the built candidate pipeline's
  object graph in the shape of
  `tests/integration/test_review_ingest_is_model_free.py::test_no_callable_in_the_built_pipeline_reaches_a_model`,
  with its planted-model controls — **and with the same recorded bound**, that a
  provider resolved through a factory one level down is invisible to it.

  > **Amended in slice B5 (2026-09-19,
  > [PR #744](https://github.com/theurian/theurian/pull/744)): discharged.**
  > `tests/integration/test_candidate_generation_is_model_free.py` is that walk,
  > eight tests over `tests/model_free_walk.py` — the instrument shared with the
  > ingest walk rather than copied, so an edge kind cannot be dropped from one
  > and not the other. The graph is **captured, not reconstructed**: the
  > candidate pipeline has no factory a test can call, so the fixture drives the
  > real tool over the real transport and keeps the `CandidateGenerator` the
  > composition root wired, with
  > `::test_the_walk_reaches_the_candidate_pipeline_it_claims_to_inspect` as the
  > premise. Two halves —
  > `::test_no_callable_in_the_built_candidate_pipeline_reaches_a_model` over the
  > captured graph's code objects and
  > `::test_no_module_the_candidate_pipeline_is_built_from_names_a_model` over
  > its import closure — with four planted-model controls: on the generator
  > class, in an injected collaborator, behind a bound method, and on the commit
  > verifier decision 3 added.
  >
  > **The one subtraction is measured, not asserted.**
  > `::test_the_excluded_composition_root_is_what_the_exclusion_says_it_is` holds
  > both directions of dropping `theurian.mcp.tools` from the name half's seed:
  > the unpruned closure really does name a model, so the exclusion is not dead
  > weight, and every name it hides arrives through modules composing
  > `knowledge.search` rather than this tool, which is what makes it admissible.
  > If that composition stops naming an embedder the test reddens and the
  > exclusion goes.
  >
  > **The same recorded bound applies, and is not closed.** The walk reads the
  > names a function spells, so a provider reached through `getattr`, a factory
  > looked up in a table, or an import one frame deeper than the walk descends is
  > invisible to it — the ingest walk's bound, carried here rather than argued
  > away.
- **Slice B5 — the five recomputed signals are never read off the request
  (decision 3).** Owed: a test that plants gate-shaped fields in the wire input
  and asserts they change no signal, with the control that a change to the
  *stored record* does change one — without the control, a generator that
  ignored the whole gate would pass. Scoped to the five recomputed signals,
  because `fixCommit` *is* a wire field and `generalizable` is not a field at
  all. **The control must not use `has_evidence`**: decision 3 records that it
  is `True` over every thread that loads, so a record edit cannot move it and
  the control would pass on a generator that read nothing.

  > **Amended in slice B5 (2026-09-19,
  > [PR #744](https://github.com/theurian/theurian/pull/744)): discharged, in
  > three parts.** The planted case is
  > `tests/unit/test_candidate_generation.py::test_gate_shaped_text_in_the_submission_moves_no_signal`,
  > which submits every spelling of an assertion a caller can reach — a `title`,
  > a `body`, a `description` and `labels` all asserting the gate — over a record
  > whose pull request is **not** merged, and asserts the refusal still names
  > `pull_request_merged`. The control is
  > `::test_the_gate_is_recomputed_from_the_stored_record`, which moves one field
  > of the *stored* record — the pull request's `merged` — and requires that same
  > name in the refusal; its docstring records why it is `pull_request_merged`
  > and not `has_evidence`, on this item's own reasoning. The structural half is
  > `::test_the_submission_type_declares_no_gate_signal`, a live intersection of
  > `dataclasses.fields(PromotionGate)` with `dataclasses.fields(CandidateSubmission)`
  > asserted empty, so an eighth signal added to the gate is forbidden on the
  > wire by existing rather than by being listed.
- **Slice B5 — `fixCommit` is verified, not trusted (decision 3).** Owed, in
  order of what each separates: a commit that does not exist in the repository
  refuses; a commit that exists but does not touch the thread's `file_path`
  refuses; a commit that exists and touches it passes. The middle case is the
  one that distinguishes verification from a mere existence check, and without
  it an implementation that only ran `cat-file -e` would pass. Plus the control
  that the check reads the **local** repository and reaches no network — the
  shape `tests/unit/test_network_call_sites.py` already holds for spawn sites,
  since a verification step is a candidate for a sixth one.

  > **Amended in slice B5 (2026-09-19,
  > [PR #744](https://github.com/theurian/theurian/pull/744)): discharged, at
  > both layers.** At the service,
  > `tests/unit/test_candidate_generation.py` drives the three verdicts in the
  > order this item asks for:
  > `::test_a_fix_commit_that_names_no_commit_drafts_nothing` (asserted on the
  > *facade*, so a proposal drafted before the gate refused it is caught, not
  > only the exception),
  > `::test_a_fix_commit_that_touches_no_file_here_is_refused_in_the_same_words`
  > — the middle case, which also holds the two refusals byte-identical over
  > detail *and* remedy — and
  > `::test_a_fix_commit_that_touches_the_threads_file_is_accepted`, the arm that
  > proceeds. Against real git,
  > `tests/integration/test_fix_commit_check_adapter.py` runs the same three over
  > real repositories and adds what the service's fake verdict cannot reach: a
  > root commit verifies, a stored path spelling a pathspec expression verifies
  > nothing, an option-shaped `fixCommit` starts nothing, and a tree id or a blob
  > id names no commit.
  >
  > **The local-only control is the spawn-site equality, and the site is
  > recorded rather than absent.**
  > `tests/unit/test_network_call_sites.py`'s `PROCESS_SPAWN_SITES` carries
  > `("infrastructure/git/fix_commit_check.py", "subprocess")` as a member, so
  > the verification *is* the sixth spawn site this item anticipated and the
  > equality reddens on a seventh as well as on its removal;
  > `test_fix_commit_check_adapter.py::test_the_module_reaches_a_spawn_from_exactly_one_place`
  > holds that the module has one. The adapter's own docstring records that
  > `diff-tree` reads local object storage, names no remote and takes no URL.
- **Slice B5 — the `file_path is None` branch is decided and recorded
  (decision 3).** The adapter yields `None` for any thread GitHub anchors to no
  string `path` (`review_provider.py:665`), and on that branch the check
  degenerates to bare existence over the repository's whole history. Owed: the
  choice — **refuse**, or **pass on existence with the weaker basis recorded** —
  written into the ADR or into the module with its reasoning, plus a driving
  case for the branch either way. An implementation that falls into one arm
  without the decision being made is the defect this item exists to prevent.

  > **Amended in slice B5 (2026-09-18): the choice half is recorded and the
  > rest is still owed.** Decision 3 above now carries it — **refuse**, with the
  > rejected alternative and the reasoning for both. Still owed at slice B5: the
  > driving case for the branch, and the module's half of the record.

  > **Amended in slice B5 (2026-09-19, the tool-registration commit of
  > [PR #744](https://github.com/theurian/theurian/pull/744)): the rest is
  > discharged.** The driving case is
  > `tests/integration/test_candidate_generation_wire.py::test_a_thread_with_no_file_anchor_is_refused_in_its_own_words_over_the_wire`,
  > which calls the tool over the transport for a stored thread whose `file_path`
  > is `None`, passing the **verifying** commit so the refusal cannot be explained
  > by the commit, and asserts three things: the published text differs from the
  > commit-verification refusal, it mentions a file at all, and no proposal was
  > written. The module's half of the record is
  > `application/candidate_generation.py`'s module docstring, which states the
  > refusal, the rejected alternative and why this refusal is not folded into the
  > commit-verification one.
- **Slice B5 — the two commit-verification refusals are indistinguishable
  (decisions 3 and 5).** *That commit does not exist* and *that commit does not
  touch this thread's file* must arrive as one refusal, in text and in duration,
  because the difference between them is a fact about the repository the caller
  was not granted. Owed: the pair driven against the same thread, asserted
  identical, with the control that a **verifying** commit is accepted — without
  it, refusing both the same way is satisfied by a build that refuses
  everything.

  > **Amended in slice B5 (2026-09-19, the tool-registration commit of
  > [PR #744](https://github.com/theurian/theurian/pull/744)): the text half is
  > discharged, the duration half is not.**
  > `tests/integration/test_candidate_generation_wire.py::test_the_two_commit_verification_failures_are_one_refusal_on_the_wire`
  > drives both failures against the same thread — an absent sha, and a commit
  > this repository really holds that touched nothing the thread names — and
  > asserts the two published texts equal, with no proposal written either time.
  > The control this item asks for is
  > `::test_a_thread_meeting_every_signal_lands_a_proposal_over_the_wire`, so
  > "identical" is not satisfied by a build that refuses every call. **Still owed
  > at slice B5:** the duration equality. It is not measured here, and it belongs
  > with the two-corpora battery item below, which is the one that owes an
  > instrument named on both sides.

  > **Amended in slice B5 (2026-09-19, the branch commit `66026d36` of
  > [PR #744](https://github.com/theurian/theurian/pull/744)): the duration half
  > is discharged as a spawn-count property, and it leaves a measured residual
  > that no test bounds.**
  >
  > **The property was false when the battery first measured it, and the battery
  > is what found that.** The verification asked two questions in two git
  > processes — `rev-parse` for *does this name resolve to a commit*, then
  > `diff-tree` for *did it touch this path* — and the first could answer no on
  > its own. So an absent object cost one process and a real commit cost two, and
  > the refusal's wall clock answered *does this object exist here*: **+7.2 ms,
  > P=1.000**, measured end to end at the wire. That is the fact this decision
  > says the pair must not distinguish, arriving through duration while the text
  > was already byte-identical — which is why the bind names both and why the
  > text half alone was not the discharge.
  >
  > **The fix is a collapse, not a delay.** Both questions are now one
  > `diff-tree` invocation and the verdict is read off its exit code and its
  > output: non-zero is `NO_SUCH_COMMIT` (including every fail-closed reading),
  > exit 0 with no output is `TOUCHES_NOTHING_HERE`, exit 0 with output is
  > `VERIFIED`. Every foreclosure survives, and `^{commit}` changed *why* it is
  > load-bearing rather than becoming decorative: under the two-call shape it was
  > what refused a fabricated forty hex digits, and `diff-tree` refuses those on
  > its own — what the suffix holds now is commit-only semantics, since a tree id
  > and a blob id each exit 0 with empty output without it, which this adapter
  > would read as *a commit was found*. The adapter's module docstring carries
  > that re-measurement per token.
  >
  > **What is pinned is the spawn count and the argument vector, not a clock.**
  > `tests/integration/test_fix_commit_check_adapter.py::test_both_failure_verdicts_spawn_one_process_with_the_same_vector_shape`
  > drives the two verdicts against one repository, asserts each spends exactly
  > one process, and asserts the two argv vectors are equal in length and differ
  > at exactly one position — the revision token, the only thing the caller
  > varied. Its sibling
  > `::test_the_same_request_spawns_the_same_vector_whether_the_object_is_here_or_not`
  > is the stronger form: the same sha and the same path against two repositories
  > that differ only in whether they hold the object, one process each and the
  > **whole** argv asserted equal, with the two verdicts asserted different first
  > so a build answering `NO_SUCH_COMMIT` for everything cannot satisfy it. At
  > the caller's own distance,
  > `tests/integration/test_candidate_generation_absence_proof.py::test_each_commit_refusal_spends_exactly_one_git_process`
  > holds the same count through the real store, the real reader, the real git
  > adapter and the real transport, parametrised over both arms. A count is the
  > instrument here on purpose: a committed wall-clock comparison is
  > machine-dependent and becomes a flake on a busy runner, where the count is
  > exact and reproduces everywhere.
  >
  > **The residual, recorded rather than absorbed.** Git still does different
  > internal work for an object it has and one it does not, and the collapse does
  > not touch that. Measured 2026-09-19 at the branch commit `66026d36` of that
  > same pull request — `perf_counter_ns`, arm
  > order rotated, n=300 with 30 discarded, Apple M1 Max, CPython 3.13.3, git
  > 2.47.1 — the absent arm runs **+0.14 ms, P=1.000** at the adapter and
  > **+0.22 ms, P=0.703** at the wire, where ~±0.3 ms of stack noise dominates.
  > Its reach is one existence bit about a forty-hex sha the caller already
  > holds, and the space is not enumerable, so it answers *is this one here* and
  > not *what does this repository contain*.
  >
  > **No test pins that residual's bound, and this sentence is the record rather
  > than a promise.** The pins above hold the spawn count and the argv — the
  > channel that was demonstrated — and they would stay green if git-internal
  > work grew. Closing it means constant-time verification against local object
  > storage, which this ADR does not design; the honest statement is that the
  > demonstrated channel is closed and pinned, and the measured one is bounded,
  > recorded and unpinned.

  > **Amended in slice B5 round 1 (2026-09-19,
  > [PR #744](https://github.com/theurian/theurian/pull/744)): the residual above
  > was one existence bit only after this round's fix, and the round is what found
  > the gap.**
  >
  > **What the amendment above said, and what round 1 revealed.** The block above
  > recorded the residual as "one existence bit about a forty-hex sha the caller
  > already holds" over a space that "is not enumerable", and treated that as the
  > whole reach. It was false at the commit that wrote it. `fixCommit` reached
  > `diff-tree`'s revision argument as a git *revision expression*, not as a bare
  > object name, so a caller could name a commit it did not hold: two reviewers
  > independently recovered a commit's *message* by sending `HEAD^{/<text>}` and a
  > ref-search form (`fix_commit_check.py`'s docstring records the exact
  > alternation the appended `^{commit}` forced), making `fix_commit_present`
  > answer to a description. The residual was a message-recovery channel over the
  > repository's reachable history, not one existence bit — the amendment above
  > priced the *duration* channel it had just closed and was silent about the
  > *expression* channel that stayed open, because a single `diff-tree` call still
  > evaluated whatever revision language it was handed.
  >
  > **Why the residual is now what that amendment claimed.** A grammar funnel
  > refuses any `fixCommit` that is not a full-length lower-case object name — 40
  > or 64 hex digits — with `NO_SUCH_COMMIT` before any process exists
  > (`re.fullmatch` of `[0-9a-f]{40}|[0-9a-f]{64}` at the adapter entry, the same
  > pattern and a `maxLength` in the published input schema; shared corpus
  > `tests/fix_commit_grammar.py`, asked at the wire and here). A revision
  > expression is not full hex, so it is refused pre-spawn and no revision language
  > is ever spent. Only past that funnel is the residual one existence bit about a
  > *valid* object name the caller already holds, over the non-enumerable space the
  > amendment above named. A grammar miss spawns nothing, so a malformed input
  > carries no timing arm, and the git-internal duration residual measured above
  > (+0.14 ms at the adapter, +0.22 ms at the wire) now sits *under* the funnel: it
  > is reachable only for an input that is already a valid full-hex object name,
  > which is the reach that amendment assumed it had.
  >
  > **The class and its universal, so the sibling is not met as a surprise.** The
  > root cause is not `fixCommit`; it is **caller-controlled tokens reaching a
  > subprocess argv**. The universal, stated so it can be refuted by grep: *git —
  > any spawn — receives MCP-caller bytes only through validated funnels.*
  > `tests/unit/test_network_call_sites.py`'s `PROCESS_SPAWN_SITES` pins six spawn
  > sites; exactly one is composed into the daemon/MCP surface a caller's bytes
  > reach — `fix_commit_check.py`, through `mcp/tools.py` (the only importer of it
  > under `mcp/`/`daemon/`) — and its two untrusted tokens are both funnelled: the
  > revision by the full-hex grammar above, the stored `file_path` (T-24,
  > author-controlled) by `--` and `--literal-pathspecs`. The other five spawns
  > take operator, configuration or setup input, none of it wire-reachable. A
  > future tool composing a second spawn module joins this class silently unless
  > the daemon-composed spawn-module set is pinned, which is the ratchet this class
  > still owes (recorded in the round-1 closure argument).
  >
  > **What is pinned, and what is not.** The demonstrated channel — a revision
  > expression reaching git — is closed by the funnel and held at both layers
  > against the shared corpus `tests/fix_commit_grammar.py`: the published schema
  > pattern by `tests/unit/test_candidate_input_schema.py`, and the adapter's entry
  > funnel (a refused member spawns nothing) by
  > `tests/integration/test_fix_commit_check_adapter.py`. T-7's spawn bullet and
  > `PROCESS_SPAWN_SITES`' note are held to the one-`diff-tree` vector by
  > `test_threat_model_t7_claims.py`. The git-internal duration residual is
  > measured, bounded and **unpinned**, as the amendment above records.

  > **Amended in slice B5 round 2
  > ([PR #744](https://github.com/theurian/theurian/pull/744), before the
  > fix-wave-2 commit): the round-1 command did not run on the documented git
  > floor, and the stored-path funnel member the closure argument above relied on
  > was forgeable. Both are replaced; the round-1 block's `diff-tree` and
  > "one-`diff-tree` vector" wording is left intact as the round-1 record, and this
  > block supersedes it in place.**
  >
  > **The command moved to the `log` form (round-2 HIGH-1, code review).** Round 1
  > reached merge commits with a single `diff-tree` call carrying
  > `--diff-merges=first-parent`, which is a git 2.31 feature; the documented floor
  > is git 2.30 (`docs/contributing/development.md`). On a floor install that option
  > errors, the non-zero exit folds to `NO_SUCH_COMMIT`, and **every valid
  > `fixCommit` refuses**. The command is now
  > `git --literal-pathspecs log --no-walk --first-parent -m --name-only --format= --root --end-of-options <sha>^{commit} -- <file_path>`,
  > which reaches the same merge commits on git 2.30 and gives byte-identical
  > verdicts. The finding's pin is class-level rather than a "`--diff-merges` is
  > gone" spot-check:
  > `test_fix_commit_check_adapter.py::test_the_verify_command_uses_only_git_features_at_or_below_the_documented_floor`
  > reads the spawned argv off a captured call and asserts every token is on a floor
  > allowlist introduced at or below the floor, with a planted git-2.31
  > `--diff-merges=first-parent` as the positive control — so a future above-floor
  > token is kept out by construction.
  >
  > **The stored-`file_path` funnel member was forgeable (round-2 HIGH-2,
  > adversarial).** The closure argument above rested on the stored `file_path`
  > being fence-funnelled by `--` and `--literal-pathspecs`. That flag disables
  > `:(…)` *magic* but not a literal **directory**: a stored `file_path` of `.`,
  > `./`, `docs` or `docs/` is a plain path, and a directory pathspec matches every
  > file beneath it — so it **verified a foreign commit** that touched anything
  > under that directory, defeating the check. The verdict is now an
  > **output-membership** check: `VERIFIED` only when the *exact* stored `file_path`
  > is one of the `--name-only` output lines, `TOUCHES_NOTHING_HERE` otherwise. A
  > `--name-only` line is always a single file path — never a directory, never a
  > `:(…)` expression — so membership refuses the whole directory-and-tree-pathspec
  > class, superseding round 1's "`--literal-pathspecs` closes the stored-path
  > class"; the flag stays as defence in depth over the magic half.
  > `test_fix_commit_check_adapter.py::test_a_stored_path_spelling_a_pathspec_expression_verifies_nothing`
  > drives all eight stored-path spellings — the `:(…)` magic forms and the literal
  > directories `.`, `./`, `docs`, `docs/` — against a commit that touched only a
  > foreign file and asserts each answers `TOUCHES_NOTHING_HERE`, with
  > `::test_a_commit_that_touches_the_threads_file_is_verified` and
  > `::test_a_commit_that_touches_another_file_is_not_verified` the positive and
  > negative controls that membership still says yes for the real anchor and refuses
  > a foreign commit for the same reason rather than everything refusing.
  >
  > **The funnel's exact two lengths are pinned across the interior (round-2
  > MEDIUM).** The CRITICAL grammar funnel is unchanged — full-hex, pre-spawn — and
  > its shared corpus `tests/fix_commit_grammar.py` now carries a fifty-two-hex
  > `interior-length` member, strictly between the two object-name widths, driven
  > `REFUSED` at both seams by the corpus-parametrized arms
  > `test_candidate_input_schema.py::test_the_published_schema_refuses_a_fix_commit_that_is_a_revision_expression`
  > (the published `pattern`) and
  > `test_fix_commit_check_adapter.py::test_a_fix_commit_that_is_not_a_full_object_name_is_refused_without_spawning`
  > (the adapter, zero spawns). A widening to a single length bound rather than the
  > alternation of exactly forty and sixty-four reddens both.
  >
  > **What the T-7 records now pin, superseding the sentence above.** The round-1
  > block says T-7's spawn bullet and `PROCESS_SPAWN_SITES`' note are held to the
  > "one-`diff-tree` vector" by `test_threat_model_t7_claims.py`; after this round
  > both are held to the **`log`** argv above, because those arms read the vector
  > off the adapter's own syntax tree every run rather than off a fixed list.
  > `docs/security/threat-model.md`'s T-7 sixth-site paragraph is rewritten to the
  > same `log` command and the same output-membership control, so the two governed
  > records agree.

  > **Amended in slice B5 round 3
  > ([PR #744](https://github.com/theurian/theurian/pull/744), before the
  > fix-wave-3 commit): the round-2 output-membership check compared git's
  > *rendered* lines and falsely refused honest fixes whose filename git does not
  > render verbatim; the command moved to the `-z` byte-membership form. The round-2
  > block is left intact and this block supersedes it in place.**
  >
  > **Line membership refused honest fixes (round-3 HIGH).** Round 2 read
  > `--name-only` output as text — `.splitlines()`, then the stored `file_path`
  > among the lines. git renders paths for humans: under `core.quotePath` it
  > **quotes** a non-ASCII, double-quoted, backslashed or control-character name, so
  > the rendered line never equalled the raw stored path; `.splitlines()` **split** a
  > name carrying an embedded newline into two lines matching neither; and the line
  > filter's `.strip()` **emptied** a file literally named with a single space. Each
  > answered `TOUCHES_NOTHING_HERE` for a commit that genuinely touched the file — a
  > true fix, correctly named, refused.
  >
  > **The closure is byte membership over NUL-delimited entries.** `-z` makes git
  > emit its machine format: each touched path as raw bytes, NUL-delimited, with no
  > quoting and no line structure. `verify` splits git's raw stdout on the NUL byte,
  > drops empty entries only (`!= b""`, never `.strip()` — a `b" "` entry is a real
  > one-space filename), and answers `VERIFIED` iff `file_path.encode("utf-8")` is
  > one of those byte entries. This closes quoting, line-splitting,
  > whitespace-stripping and the round-2 directory-and-pathspec breadth in one move,
  > because none of them survives the machine format. The claim is exactly that —
  > membership of the anchor's UTF-8 bytes in git's raw entry set, **not** a claim
  > about arbitrary path bytes; `--literal-pathspecs` stays as defence in depth over
  > the `:(…)` magic half.
  >
  > **The encoding face is closed on the git side, and is in scope.** With `-z` the
  > entries are raw **bytes** and the anchor is a `str`. git's `-- <file_path>`
  > pathspec is itself byte-based, so a UTF-8-encoded anchor cannot match a non-UTF-8
  > tree path, and the byte comparison would refuse such a path in any case — a
  > non-UTF-8 sequence cannot equal any `str.encode("utf-8")`. A byte-compare and a
  > decode-and-compare therefore agree on every input reachable here, so the byte
  > form narrows nothing; the fail-closed verdict is the recorded property. Pathname
  > **normalization** — a byte-unequal NFC-versus-NFD anchor — is a *separate*
  > equality class, filed [#758](https://github.com/theurian/theurian/issues/758),
  > and is deliberately not part of this closure.
  >
  > **What pins it.**
  > `test_fix_commit_check_adapter.py::test_an_honest_anchor_of_any_path_shape_verifies`
  > drives an ASCII, a CJK, a double-quoted, a backslashed, an embedded-newline and
  > a tab filename, asserting each verifies its own commit and refuses a foreign one;
  > `::test_an_honest_whitespace_only_anchor_verifies` is the single-space face the
  > `.strip()` would drop; `::test_a_non_utf8_disk_path_never_verifies_a_utf8_anchor`
  > pins the fail-closed encoding verdict against a change to the comparison; and the
  > captured-vector arm
  > `::test_the_git_vector_is_one_process_fixed_and_forecloses_an_option_a_path_and_magic`
  > holds the `-z` argv above. `docs/security/threat-model.md`'s T-7 sixth-site
  > paragraph carries the same `-z` command and the same byte-membership closure, so
  > the two governed records agree.

  > **Amended at the 0.4.0 release cut (2026-09-19, re-measured on frozen
  > `6c64a6ba`): the residual's direction is reversed, and it is not a fixed
  > bound — it scales with the present commit's diff. The security consequence is
  > unchanged and no finding reopens. The round-2 block's "+0.14 ms, P=1.000,
  > bounded" figures are left as the superseded record; this block corrects the
  > sign and drops the bound.**
  >
  > **What the blocks above said.** The round-2 amendment measured the
  > git-internal residual as the *absent* arm running **+0.14 ms slower,
  > P=1.000** at the adapter, and called it "bounded, recorded and unpinned" — a
  > single fixed figure the round-1 and round-3 blocks carried forward unchanged.
  >
  > **What re-measurement revealed.** An anchored release-cut pass re-ran the two
  > failure arms at frozen `6c64a6ba` — its `e3`/`e5` scripts, n=1500 per arm,
  > arm order rotated, permutation test, over eight independent runs across six
  > repository shapes at two load levels. The recorded direction does not
  > reproduce: the **absent** arm is **faster** every run, with
  > P(absent > median(present)) in **0.37–0.43**. The mechanism is physical — the
  > absent arm exits 128 without reading a tree, while the present arm walks and
  > diffs one commit's tree — so the +0.14 ms *absent slower* the round-2 block
  > recorded was an artefact of that single run, not the steady state.
  >
  > **It is not a bound; it scales with the present commit's diff.** The residual
  > is a function of how much the verified commit touched, not a fixed ceiling:
  > ~**0.67 ms** against a one-file commit and ~**1.58 ms** against a fifty-file
  > commit, measured by the same pass. The round-2 "bounded" sentence records a
  > fixed limit a larger repository exceeds, so it is superseded by this scaling
  > statement rather than by a new number.
  >
  > **The security consequence is unchanged.** Whichever arm is faster, the
  > residual carries the same one existence bit about a forty-hex sha the caller
  > already holds, over a non-enumerable space — *is this one here*, not *what
  > does this repository contain*. It stays real, measured and **unpinned**: the
  > pins above hold the spawn count and the argv, the demonstrated channel, and
  > would stay green as this residual scales. This corrects a governed timing
  > record; it changes no behaviour and reopens no HIGH. The lesson the pass
  > records for this ADR's own framing: state a timing residual as a scaling
  > relationship measured under load with its instrument named, never as a
  > single-run fixed bound.
- **Slice B5 — at least one gate test is driven from a record the real adapter
  shape produces.** That is, a `ReviewResolution` built the way
  `review_provider.py` builds one, with `fix_commit` **absent**. What lets a
  false premise about this field survive design review is not the nine
  hand-written `fix_commit="e" * 40` fixtures — those are `ReviewResolution`
  constructions that never reach a gate — it is that the suite's **only** gate
  fixture (`tests/unit/test_project_and_traceability.py:555-569`) sets every
  signal from a literal `True`, so no test relates a gate to a record at all. A
  suite that never joins the two cannot catch the next premise either.

  > **Amended in slice B5 (2026-09-19,
  > [PR #744](https://github.com/theurian/theurian/pull/744)): discharged, and
  > not by one test.** `tests/unit/test_candidate_generation.py`'s `_thread`
  > helper builds its `ReviewResolution` the way `review_provider.py` builds one
  > — `state` and `resolved_by`, with `resolved_at` and `fix_commit` left `None`
  > because GitHub's thread object carries neither — and **every** gate test in
  > that module runs on it. So the join this item asks for is the module's
  > default rather than one case in it: the gate is built inside
  > `CandidateGenerator.generate` from a record of the adapter's shape, and a
  > design premise about `fix_commit` being readable off the record now fails at
  > the first test rather than surviving review. The literal-boolean fixture in
  > `test_project_and_traceability.py` is untouched; it exercises the domain
  > type, which is a different question from whether anything joins a gate to a
  > record.
- **Slice B5 — `generalizable` is satisfied by the submission and is not a wire
  field (decision 3).** Owed: the structural property that the published input
  schema declares no `generalizable`, and a driving case that a well-formed
  submission over a thread meeting the other six signals produces a candidate —
  the positive control the earlier draft's underivable branch would have made
  unreachable.

  > **Amended in slice B5 (2026-09-19, the tool-registration commit of
  > [PR #744](https://github.com/theurian/theurian/pull/744)): discharged, and
  > the structural half is wider than this item asked for.**
  > `tests/unit/test_candidate_input_schema.py::test_no_promotion_gate_signal_is_a_field_a_caller_may_set`
  > builds its forbidden set from `dataclasses.fields(PromotionGate)` read live
  > and camel-cased, and asserts it disjoint from every key declared under any
  > `properties` object in the published schema — nested ones included. So
  > `generalizable` is forbidden by being a gate field rather than by being
  > listed, and a signal added to the gate is forbidden by existing. Both sides
  > are asserted non-empty first, so the disjointness cannot hold over nothing.
  > The driving case is
  > `tests/integration/test_candidate_generation_wire.py::test_a_thread_meeting_every_signal_lands_a_proposal_over_the_wire`,
  > which calls the tool over the transport and finds the proposal directory
  > under `.theurian/proposals/`.
- **Slice B5 — unknown CI is unmet and named, and its message differs from
  failed (decision 4).** Owed: a thread whose stored `ci_successful` is `None`
  refuses, and the refusal names `ci_successful` among the unmet signals; a
  sibling asserting a definite `False` refuses too; a definite `True` proceeds —
  three inputs, because two of them would not distinguish "unknown is unmet"
  from "the gate ignores this signal". **A fourth assertion is what separates
  this decision from the rejected adapter-flattening alternative**: the `None`
  and `False` refusals must not be the same string, since `unmet()` returns the
  same name for both. Without it, an implementation that flattened `None` to
  `False` at the adapter — the alternative this ADR rejects — passes all three.

  > **Amended in slice B5 (2026-09-19, the tool-registration commit of
  > [PR #744](https://github.com/theurian/theurian/pull/744)): discharged over
  > the wire.** All four assertions are in
  > `tests/integration/test_candidate_generation_wire.py`, against a corpus
  > carrying one pull request per `ci_successful` state. A stored `None` refuses
  > and the published text contains `ci_successful`
  > (`::test_an_unknown_ci_outcome_is_refused_naming_the_signal_over_the_wire`,
  > which also asserts the cure survived the tool boundary). A stored `False`
  > refuses — the `failed-ci` case of
  > `::test_every_designed_refusal_is_error_classified_and_carries_a_cure`, which
  > asserts `isError` and a `theurian` command in the text, not the signal name. A
  > stored `True` over a thread meeting the other six signals lands a proposal
  > (`::test_a_thread_meeting_every_signal_lands_a_proposal_over_the_wire`). And
  > the `None` and `False` refusal texts are asserted **unequal**
  > (`::test_the_unknown_ci_refusal_is_not_the_words_a_failed_ci_thread_gets`),
  > which is the assertion an adapter-side flattening fails. This is the message
  > half the `ci_successful` measurement above deferred to here; the `bool | None`
  > half landed earlier on the same branch.
- **Slice B5 — the refusal is not a withheld-versus-absent oracle
  (decision 5).** Owed: one battery of requests answered identically over a
  corpus that **held** withheld threads and one that **never did**, at the tool
  layer, covering responses **and refusals**, with the control that the battery
  actually reaches the withheld threads. The fixture is synthetic, and that is
  the only way to have a withheld row in a corpus whose scope excludes them.
  **The battery's equality covers duration as well as text**, because a gate
  recompute plus a commit verification is strictly more work than a miss on an
  id that names nothing. Owed with its instrument named on both sides, so the
  measurement is not a wall-clock number nobody can reproduce.

  > **Amended in slice B5 (2026-09-19,
  > [PR #744](https://github.com/theurian/theurian/pull/744)): discharged.**
  > `tests/integration/test_candidate_generation_absence_proof.py` is that
  > round — 28 arms at the branch commit `66026d36` of that pull request
  > (`uv run --frozen pytest -q --collect-only <file>` → `28 tests collected`).
  > **Three** deployments, not two, and the third is what makes the first two
  > mean anything: `withholding` (the whole corpus built while withholding the
  > keys, their evidence files still on disk, no store row), `never_held` (the
  > corpus minus those records, withholding nothing) and `control` (the whole
  > corpus, withholding nothing). What is compared is the whole JSON-RPC
  > message — `isError`, the structured content and every content block —
  > serialised into one string, so a refusal folds into the same comparison as
  > an answer and error distinguishability is part of the property rather than a
  > separate claim.
  >
  > **The masking is measured, not assumed.** A successful call mints three
  > fresh ULIDs, so two successes cannot be byte-compared for a reason that is
  > not a leak; those three are replaced with fixed tokens and nothing else is,
  > and `::test_a_success_response_varies_only_in_the_three_identifiers_it_mints`
  > holds that two calls to the *same* deployment differ raw and agree once
  > masked — which is what makes the substitution exactly sufficient rather than
  > a mask over a real difference. The reach controls are
  > `::test_the_battery_really_reaches_the_withheld_records`,
  > `::test_the_battery_carries_both_shapes_a_caller_can_receive` and
  > `::test_the_control_generates_a_candidate_from_the_withheld_thread_through_the_same_tool`.
  >
  > **The duration half is a count pinned at zero, and that is deliberate.**
  > `_Spend` tallies the two costs below the resolve — evidence-file reads and
  > git spawns — and
  > `::test_a_key_the_store_does_not_resolve_costs_no_evidence_read_and_no_git_spawn`
  > holds a miss at zero of each whether the key is withheld or was never
  > landed, while `::test_the_same_key_unwithheld_pays_both_the_evidence_read_and_the_git_spawn`
  > is the control that the instrument separates when there is work to separate.
  > A cost never incurred cannot make a refusal's timing carry the record. A
  > committed wall-clock comparison was rejected for T-26's reason: what it
  > asserts is the machine.
  >
  > **The wall clock is an out-of-band corroboration, and both sides are
  > named.** Measured 2026-09-19 against the production code at the
  > branch commit `83245a71` of that same pull request, the one that registered
  > the tool — Apple M1 Max, macOS 26.6.2,
  > CPython 3.13.3, SQLite 3.47.1, `perf_counter_ns` around one `tools/call` on
  > an open session, five resolve-miss arms rotated, n=400 after 40 discarded:
  > the withheld key answered at median 3.8610 ms against `withholding` and
  > 3.8651 ms against `never_held`, **P(withheld > median(never-held)) = 0.477**.
  > The spread across all five miss medians was 0.052 ms, and two plainly absent
  > keys differed by as much as the withheld/never-held pair did, so the residual
  > tracks the key string rather than the corpus. The same key un-withheld is a
  > hit at **+19.0 ms, P = 1.000**.
  >
  > **Why this holds by construction and not only by measurement:**
  > `ReviewSearchBuilder` drops a withheld key before a load exists, so the built
  > store holds no row, no flag and nothing a later read has to remember. There
  > is no withheld-versus-absent branch in the serving path to get wrong.
  > **What this battery does not reach**, stated rather than left to be
  > discovered: generated requests (every example is hand-enumerated;
  > `test_review_search_tool_absence_proof.py` carries the hypothesis arm) and
  > the store's own artifact (`test_review_search_absence_proof.py`'s).
- **Slice B5 — the candidate lands as an ordinary proposal, through the mapping
  decision 1 states.** Owed: a test that the proposal a generated candidate
  produces is one `theurian propose accept` accepts, driven through the shipped
  commands; that its `trustLevel` is `inferred` on the written migration, not
  only on the in-memory object; and that the migration's `author` is the
  caller's human-attributable identity while `evidence.json`'s `agentId` is the
  agent's — the two never filled from each other, which is what the migration
  schema's own `author` description requires.

  > **Amended in slice B5 (2026-09-19,
  > [PR #744](https://github.com/theurian/theurian/pull/744)): discharged, all
  > three parts.** `tests/integration/test_candidate_lands_as_a_proposal.py`
  > drives the proposal a generated candidate produces through the shipped
  > `theurian propose accept`:
  > `::test_the_shipped_propose_accept_accepts_a_generated_candidate`, which is
  > where *an ordinary proposal directory* is cashed rather than claimed — the
  > acceptance runs the secret scan over everything it would land, resolves the
  > `contentFile` through the containment check and replays the whole migration
  > set against a throwaway store before a file moves (ADR-0027), and a proposal
  > ordinary in shape that failed any of those is not one a reviewer can merge.
  > `::test_the_accepted_candidates_trust_level_is_inferred_on_the_migration_and_in_the_state`
  > follows `inferred` past both places it could quietly become `unverified` —
  > the migration in the applied set, and the item `knowledge.get` serves once
  > that set is applied, read back through the tool rather than off the
  > database.
  > `::test_the_accepted_migration_names_the_human_and_the_proposal_named_the_agent`
  > reads `evidence.json` before the acceptance consumes it and the migration
  > after, out of the applied set, and asserts the agent's id appears **nowhere**
  > in the migration — which is what makes the pair mean something rather than
  > two values that happen to differ.
  >
  > The file-level half landed one commit earlier and is separate:
  > `tests/integration/test_candidate_generation_on_disk.py::test_the_written_migration_records_the_candidates_inferred_trust_level`
  > reads `trustLevel` off the `upsertRevision` operation of the drafted
  > migration, where ADR-0032 decision 1's *absent means not stated* would leave
  > the key out and let the loader apply `unverified` — a quieter defect than a
  > wrong value, since nothing refuses and the knowledge claims less trust than
  > the candidate that produced it. Its sibling
  > `::test_the_migration_names_the_human_and_the_evidence_names_the_agent`
  > holds the same `author`/`agentId` split on the drafted files.
- **Slice B5 — the name-honesty split is stated on every surface that
  describes the tool (decision 2).** Owed: the tool description, the published
  input schema's description and this ADR's table agreeing that Theurian does
  not author the generalization. Whether the wording is *faithful* is a reading
  and no mechanical check reaches it; what a test can hold is that the surfaces
  do not diverge from each other.

  > **Amended in slice B5 (2026-09-19, the tool-registration commit of
  > [PR #744](https://github.com/theurian/theurian/pull/744)): discharged.**
  > Decision 2 above now carries the sentence pair verbatim, and
  > `tests/integration/test_candidate_name_honesty.py` holds one containment arm
  > per surface: the description the built server publishes for the tool, the
  > top-level `description` of
  > `schemas/mcp/review-generate-knowledge-candidate-input.schema.json`, and this
  > document's prose with block quoting stripped and soft wraps flattened.
  > `::test_a_different_write_tool_does_not_carry_the_claim` is the control that
  > `knowledge.proposeChange` does *not* carry the sentence, without which three
  > `in` checks over long strings would discriminate nothing, and
  > `::test_the_claim_survives_all_three_of_the_formats_it_is_written_in` holds
  > that the sentence contains no character Markdown, JSON or Python transforms.
  > The limit this item states is unchanged: the module holds divergence and
  > nothing more, so a build whose three surfaces all carried a wrong shared
  > sentence passes it.
- **Slice B5 — the T-3 threat-model entry gains the candidate path.**
  `docs/roadmap.md`'s Phase B risks row names it as owed; it is a prose
  obligation with no test, recorded here rather than dressed as discharged.

  > **Amended in slice B5 (2026-09-19,
  > [PR #744](https://github.com/theurian/theurian/pull/744)): written.** T-3
  > now names the candidate path as its second injection route and states where
  > each existing control stands on it: the proposal's `title`, `body`, `kind`,
  > `category` and anchors come from the submission, so no comment body reaches
  > the candidate; thread text reaches an agent through `review.search` under
  > the safety triple; a candidate is a draft proposal FR-V4's human merges or
  > does not. One narrower path is named rather than denied — a gate refusal
  > quotes the stored `filePath` through `bounded_quote` — and the residual is
  > this entry's own one actor later: an agent that writes a planted instruction
  > into its own submission produces a candidate Theurian cannot distinguish
  > from a fair generalization. **The grade does not move**, and the reason is
  > stated there rather than assumed. **Still a prose obligation with no test**,
  > which this amendment does not change.
- **Slice B5 — [#479](https://github.com/theurian/theurian/issues/479) is
  rescoped to Phase B and owned by this slice.** It is the design-first step
  ADR-0030 was recorded under, and the candidate half is the part of it that
  outlived Milestone 8.
