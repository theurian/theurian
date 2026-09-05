# Working on Theurian with Claude Code

`.claude/agents/` defines what each specialist does once it is called. This file
defines the part that cannot live there: **when to call them, and how to decide
what to do with what comes back.**

The distinction matters because the expensive mistakes are orchestration
mistakes. A reviewer that finds a CRITICAL is doing its job; an orchestrator that
receives one and quietly downgrades it is not.

**This file grows net-zero: added lines are paid for by compressing
elsewhere** — its weight falls on every session and specialist on every load.

## The orchestrator does not implement

**Claude Code in the main session orchestrates. It does not write the change
itself.** Every unit of work — production code, tests, documentation, build and
CI configuration — is assigned to the specialist that owns it:

| Work | Owner |
| :-- | :-- |
| Production Python | `theurian-python` |
| Tests, fixtures, mutation checks | `theurian-tests` |
| README, CHANGELOG, ADRs, docstring-level prose | `theurian-docs` |
| MCP surface: tools, schemas, wire contract | `theurian-mcp` |
| Workflow, packaging, quality gate | `theurian-ci` |
| Review | the three reviewers below |

This holds even when the orchestrator can obviously see the fix. The reason is
not effort but **independence**: work written and reviewed inside one context is
graded by the mind that produced it, and that mind has already decided the change
is correct. Splitting implementation from judgement is what makes a round mean
anything.

What the orchestrator keeps: deciding what to assign, to whom and in what order;
writing each brief — assignment (*The assignment brief*) and review (*The review
round*); **verifying returned work by running it, never by reading it**; weighing
findings against the severity rules below; relaying results to the user.

Two narrow exceptions, both stated in the response when used:

1. **Reproduction and verification scratch scripts** — the check *on* the
   specialists, so it cannot be delegated to them.
2. **A trivial mechanical follow-up inside delivered, reviewed work** — a rename,
   a moved import, a typo. If it needs a decision, it is not mechanical; assign
   it.

Launch independent assignments **in one message**. Sequence only where one
genuinely consumes another's output.

### Independence is a measured file set, not an issue title

Parallel fan-out is load-balanced by **which files each fix actually touches**,
and that key comes from reproduction, not from reading the issue. An issue lies
about its own scope: a title says `propose accept`, and the fix lands in a
helper (`_migration_document`) shared with the create path — two "independent"
assignments now collide in one file. Reproduce first, name the file set, then
partition:

1. **Same file set → same cluster → one worktree, worked serially.** Fanning a
   cluster across worktrees does not remove the conflict; it defers it to a
   merge that someone must untangle by hand.
2. **Disjoint clusters → parallel worktrees.** This is what worktree isolation
   is for; the shared checkout is what the serialize-writers rule protects.
3. **Shared logical resources escape worktrees.** `SCHEMA_VERSION` and the
   CHANGELOG's `[Unreleased]` section collide across worktrees, not just
   within one. Any change touching them joins a serial spine, whatever its
   cluster.
4. **Verification stays serial per item, always.** Parallelism is for
   producing fixes, never for checking them — and the ceiling on useful
   fan-out is the orchestrator's own verification rate, not the worktree
   count. Fixes delivered faster than they can be run and reviewed are
   inventory, not progress.

### The assignment brief

**Definition of Ready:** no assignment dispatches until reproduce → measured file
set → acceptance criteria → fences are written — the first two are *Independence
is a measured file set, not an issue title*, the last two are here. **INVEST** is
the admission test: dispatch a unit only when it is Independent (its own measured
file set), Small (within the cluster cap), and Testable (every AC names a check):

1. **Scope and acceptance criteria.** Scope names the requirement IDs
   (FR-*/SEC-*) and ADRs the change touches; AC are written before dispatch in
   EARS constrained syntax: a DON'T is the Unwanted-Behaviour pattern — *If
   \<condition>, the \<system> shall NOT \<behaviour>* — and a behaviour example
   is Given-When-Then, each GWT roughly one pytest. Every AC line names its
   verification: a named test, a measurement, or a search command.
2. **Do** — the change itself.
3. **Non-goals** — the scope DON'T. An adjacent finding is box-split into its own
   issue, never folded in (*A class stops expanding on a budget, not on running
   dry*).
4. **Fences** — the operational DON'T. Restate the applicable global fences *in*
   the brief; reading them once elsewhere measurably does not prevent the act
   (*Running the CLI on a development machine*).
5. **Observable families** — the applicable ones enumerated in *The review
   round*; they bind the implementer.
6. **Known-unfinished** — so the reviewer spends its time elsewhere.

Draft → Ready then gates on the AC met *and* the round green (*Early push and
Draft PRs*), not either alone.

## Milestone definition of done

A milestone is not done when the code works. It is done when all of this has
happened, in order:

1. **Implement — by assignment, never by the orchestrator** (see above): scoped
   tests green per logical commit, a commit *each time* they go green, the full
   gate once before the Draft PR opens (see *Commits and local safety*):
   `uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pytest -q`
2. **Run it for real.** A scratch script or a real CLI invocation against a
   temporary `HOME` and `THEURIAN_DATA_DIR` — which contains the writes and not
   the service registration, so `setup` runs here only with `--dry-run` (see
   *Running the CLI on a development machine*). Every milestone so far has found
   a defect at this step that no test caught. Reading is not verification.
3. **Three reviews in parallel** for a full round — see below. Loop until green.
4. **Documentation follows** — CHANGELOG (breaking changes named as such), README
   status and roadmap, new ADRs, and every ADR compliance section the milestone
   discharged or newly owes.
5. **Flip the Draft PR to Ready** — it has been open since the first green
   commit (see *Early push and Draft PRs*) — with a description that states what
   was found and fixed, not only what was built.
6. **CI green**, then squash-merge.

## Depth is instructed; stopping is not — so instruct it

Review discipline makes the orchestrator excellent at *depth*: the accelerator.
The brake below cost Milestones 5 and 6 days, one class at a time.

### A class stops expanding on a budget, not on running dry

When an emergent class has produced **three sibling findings, two follow-up PRs,
or eight hours of elapsed orchestrator time** — whichever comes first — stop
expanding it. "The reviews keep finding real bugs" is not a reason to continue:
in a setup/config-shaped class it is *always* true, and waiting for it to run
dry is how a warm-up slice eats four review rounds (SEC-18 #27, #47).

Finish or park the PR in flight by the normal CRITICAL/HIGH gate — merge if
green, otherwise leave it open with the unresolved findings recorded. Then,
before starting another slice in that class:

- **Split the box.** A finding adjacent to the slice's stated scope, arising
  from a different root cause, is a *different class* — file it as its own issue
  (as #128 was split from #47) and close the original slice on its own scope.
  Score the class, not the face, applies to *which* class a PR owns.
- **Say which it is.** If the slice's own core still carries an unresolved
  CRITICAL/HIGH, it is not ballooning — the core is hard, and that is a
  different problem said plainly. Settle which by grep and source, not by
  running one more round.
- **Compare against the plan.** If no planned milestone item has a commit, a PR,
  or an assignment in progress, the next slot starts a planned item — unless the
  class still has a reproducible CRITICAL in shipped default behaviour.

A warm-up slice must end at warm-up weight, review rounds included. If it does
not, it was never a warm-up.

### Round three is a defect in the closure argument, not a step

**A third round on the same PR is the exception, not the cadence, and reaching
it is itself the finding: the round-two closure argument was incomplete.** Two
enforced rules keep the round count down without weakening round one:

- **Pre-empt round two before dispatching it** — run the reviewers' own methods
  first (mutation and timing, below); a finding you surface costs no round.
- **Round three does not block on anything below the gate.** Entering a third
  round, every finding that is not a reproducible CRITICAL or HIGH is *filed as
  an issue and the PR ships* — it does not earn another cycle. Only a CRITICAL or
  HIGH still open holds the PR, and if one is open after round two the core is
  hard and that is stated plainly, not chased through a fourth round.

### Stalls are flow debt too — keep sessions small

A session that dies mid-verification — thinking with no output, no commit landing
— loses every hour since its last green with nothing to show, and the recovery is
the user noticing. Measured on this project, session stalls were a larger
wall-clock sink on a bad night than any single review round. They come from one
session carrying too much: a cluster of parallel fixes plus their verification in
one context. Prevent them structurally: **commit at every green** (the work
survives the death), **keep a cluster small enough to verify in one sitting**, and
**`/new` at each clean transition** rather than letting one session run a whole
milestone. A stall is not bad luck; it is a session that was asked to hold more
than it could.

### Merge-ready work is checked at every transition

A finished, reviewed, CI-green PR left open is flow debt: the milestone is not
done until it is merged (step 6 above), and the next class is started on top of
work that has not landed. When closing an assignment and when opening the next,
run `gh pr list --state open --draft=false` and merge anything green and
mergeable before dispatching new work. A round is recorded as a PR comment at
the flip to Ready, but this workflow never posts a GitHub *review* — so Ready
plus a clean checks list is the merge signal; do not wait for an approval.

## The review round

### Review weight is set by blast radius, and the default is light

The full three-authority round — code, security and adversarial in one message —
is the instrument for changes whose failure discloses withheld content, corrupts
governed state, or falsifies a security claim: the write path, the gates, the
daemon surface, the threat model. It runs **synchronously**, before the flip to
Ready, and there it earns its hour — every one of this project's four CRITICAL
disclosures and its one fabricated "tests GREEN" report was caught in a sync
round. **Everywhere else that hour is overhead** — spending it everywhere tripled
hours-per-unit-of-work while output stayed flat — so outside the top row
**adversarial review runs async, after merge**: a standing red-team sweep over
`main`, nightly single-file mutation runs included, whose findings enter the
filing-time triage like any other. Building that sweep and its ratchet is tracked
in [#378](https://github.com/theurian/theurian/issues/378), and until it runs the
**middle** row is the one left uncovered — the top row is sync either way. So
while #378 is open, a middle-row change whose claim the routing table sends to
the adversarial reviewer gets that review **sync**, or the PR says the claim went
unattacked. Async adversarial on a *disclosure* surface is the one trade that
never pays — it swaps a sync hour for an embargo week, six GHSAs at days each.

So weigh the review before dispatching it, not after:

| Blast radius of a wrong change | Review weight |
| :-- | :-- |
| Disclosure, governed state, security claims, wire contract | Full **sync** round — all three, before the flip |
| Behaviour a trier runs, but no disclosure surface | Code review sync; adversarial async, when the claims table calls it. The async sweep is not built yet ([#378](https://github.com/theurian/theurian/issues/378)) — until it runs, dispatch that adversarial review sync, or record in the PR that the claim went unattacked |
| Prose, process guidance, CI plumbing, mechanical moves — wrong means "misleading, revertible" | One light pass (code review alone), same day, no round |

**Static gates stay sync and strengthen — the ratchet:** every adversarial or
security finding proposes its own automation (a test, a lint rule, or a CI gate)
before it closes, so the synchronous surface shrinks monotonically. Light-class
issues are harvested 5–10 per PR under one light pass — a sweep is one topic, not
a carve-out — merging in ~84 minutes where a full round takes a day.

The routing table below decides who reviews a *claim*; this table decides how
much apparatus a *change* gets. When the two disagree, the claims table wins —
a prose file asserting a measured property is a disclosure-class claim in a
light-class file. The failure mode this section exists to stop is uniform
weight: pushing a wording fix through the same machinery as a gate change
spends the reviewers' credibility on work that cannot pay for it.

Before flipping any PR out of Draft, when the change takes the full round,
launch all three **in a single message** so they run concurrently:

| Agent | Answers |
| :-- | :-- |
| `theurian-code-review` | Is it correct and maintainable? Does it hold its own stated invariants? |
| `theurian-security-review` | Does it satisfy the SEC-* requirements and the threat model? |
| `theurian-adversarial-review` | Can I break it, and can its tests actually fail? |

**A prose-only change still gets the adversarial reviewer when it asserts
something behavioural.** This project's documentation states measured
properties. Six of Milestone 5's prose-only commits corrected a claim that
measurement had falsified: a cost comparison that ran the other way, an ADR
saying two things that were never true, a comment offering a count of nine that
printed thirteen, a docstring claiming a guard its own file does not enforce, a
test file stating three where its own test knew four. None changed behaviour —
the four that touch `.py` files are AST-identical once docstrings are stripped —
and each made a recorded decision wrong.

Route by what would settle the claim, not by file type:

| The change asserts | Reviewed by |
| :-- | :-- |
| something settled by running it — an invariant, a closure or acceptance argument, a measured cost, a count, "cannot / never / independent / identical", a claim that the suite holds something | all three, adversarial included |
| something settled by reading the source — what the codebase contains, what SQLite or FTS5 guarantees | code and security review |
| nothing behavioural — a changelog inventory, a link, an issue reference, wording | code review alone |

**A false closure argument is the case that does not surface later.** It is
consumed as a settled premise rather than re-examined: the BM25 residual's
acceptance was approved, written into the threat model in the orchestrator's own
words, and carried for two rounds before anyone measured it.

Each brief must carry, explicitly:

- the diff scope (`git diff main...HEAD`) and the files that matter
- the requirement IDs and ADRs the change touches
- what is *known* to be unfinished, so the reviewer spends its time elsewhere
- the observable families the implementation brief enumerated — the claims the
  implementation says it already covers, handed over to be attacked
- the **perspective block**, per reviewer: its standing mandate as one of its
  perspectives, the lenses specific to this change, and the claims to attack —
  the families above. Frozen at dispatch, for the PR's whole review lifecycle;
  a later round adds only the fixes, what each now asserts, and what is settled.

**The family list binds the implementer, not the adversarial reviewer.** The
block is not that list alone: the mandate is inside it, so in round one the
mandate lens runs off-list, on the family nobody enumerated — handing the
checklist over as the reviewer's scope would delete that value. From round two
onward the subject narrows: the scope is what the fixes newly claim, attacked
through that same block. A finding outside it is tagged **out-of-perspective**
and filed with a disposition rather than graded into the round, so it does not
hold the flip to Ready; a reproducible CRITICAL reports immediately regardless.

**An enumerated family is the implementer's to hold, and recurrence burns in.**
A round-one finding on a family the brief enumerated is an implementation-stage
failure, not a review success — record it as one in the PR's round comment. When
the same specialist is caught on the same family twice, that family is written
into the **implementing** specialist's agent definition
(`.claude/agents/theurian-{python,tests,docs,mcp,ci}.md` — never a reviewer's),
so every future instance is born knowing it: recurrence is a defect of the
definition, not of the instance. The loop this closes: a reviewer discovers a
new family → the standing table; the brief selects the applicable ones per
change; a repeat against the same specialist → that specialist's own definition.

Do not write these review briefs from memory each time — the agent definitions
hold the standing context; the brief names it and adds what this change needs.

### Round one is full; later rounds are not

**Round one gets all three reviewers at full scope** — for a full-round change;
the blast-radius table above says which. Nothing it found in
Milestone 5 — extraction oracles, a schema that rejected the product's own
output, tests that stayed green with the code deleted — was findable by reading.
A later round re-reading from zero only looks where round one already has.

**Cap LOW at five per reviewer**; the rest goes in the PR description as one
line. Round two produced thirteen and deferred nearly all of them.

**Assign a confirmed finding as soon as its files are free.** Waiting for all
three serialises the round for no benefit when the findings do not share files.
What must *not* be parallelised is verification: **reproduce a finding yourself
before assigning it.** Three times this milestone the reviewer's stated mechanism
was wrong while the finding was real, and a brief built on an unverified
mechanism sends the fix at the wrong cause.

Do not read a slow round as a failed one. Retrieval is a domain where defects are
invisible until something runs, so the time a round spends running things is the
round working. The next section cuts the *number* of rounds, not the wall clock
inside one; nothing in it is a reason to end a round before its checks have run.

### Round two onward: run the reviewers' methods before they do

A round ends when CRITICAL and HIGH are zero, not when findings stop; neither
MEDIUM nor LOW forces another cycle. Aim at the two that do.

Before dispatching any round after the first:

- **Sweep the diff for property claims.** Every added sentence asserting that
  something does not move, cannot be observed, or is independent of what was
  withheld gets one of three: a named test that goes RED when it is false, a
  pasted measurement, or deletion. Milestone 5 shipped three fixes whose effect
  was real and whose stated mechanism was false, and a reviewer found each one.
- **Write the closure argument for every open class, and hand it over.** A
  reviewer given an argument tries to refute it, which terminates. A reviewer
  given a diff tries to discover the class, which does not.
- **Run the reviewers' own methods first.** The adversarial reviewer's is
  mutation over the whole suite — a surviving mutation is a finding you were
  about to receive. The security reviewer's is timing measurement across the
  paths the change touches. And do not widen the surface mid-round: a mechanism
  met late is a new claim, filed out-of-perspective rather than graded.

What still comes back is a family nobody had enumerated, and that is the round
doing its job.

### A finding is closed by a closure argument, not by a fix

**A CRITICAL or HIGH is not closed when its fix lands. It is closed when someone
states the class the finding belongs to and why no other member of that class
exists.** That argument is written by someone other than whoever wrote the fix,
and it is what makes the round green. Milestone 5 spent three rounds on one
information-disclosure defect with five faces, finding them one at a time and
closing none of them, before round four stated the class and ended all five in
one — and those rounds carried plenty of unrelated work besides. The account is
in [the work log](docs/work-logs/2026-08-03-milestone-5-review-rounds.md).

**Deferral is a closure claim too.** A list of items carried to the next
milestone asserts that they are separate items rather than one class carried
several times. Milestone 5's registry carry-over listed two surfaces; a search
found five consumers of `ProjectRegistry.load()`, four of them silent about the
skipped set and two of them printing a remedy the same milestone had just made
impossible. Apply the argument to the list before it is filed, not to the items
after.

**State the argument over the whole response, not field by field.** Two of those
five faces were not field values; what moved was *which rows reached* the fields
— a withheld row taking one of fifty candidate slots, and `diversify` choosing
which paragraph of a *visible* document to excerpt. "Every published field is a
function of gate-cleared results" is true of both and closes neither. The
argument that holds is **one query against two corpora**: an index holding the
withheld documents and an index that never did must return the same response.

**Enumerate the families before round one, not after round four.** An observable
carries a bit through more than its value, and round four met timing and the
BM25 collection statistics as two separate surprises because this list existed
nowhere:

| Family | What it looked like in Milestone 5 |
| :-- | :-- |
| A published field | `usedTokens`, `count`, `fusedScore` priced on candidates |
| Which rows, or which part of a row, reached a field | candidate displacement and excerpt choice, both above |
| A duration | a withheld row forcing a second retriever pass, 91.6% single-call classification before the first pass was doubled |
| A statistic over rows the caller may not see | BM25 collection statistics (T-17a, accepted for M5) |
| An error that fires for one input and not another | a refusal that distinguishes "withheld" from "does not exist" |
| A resource the query consumes | six scan passes at 3.06 s against one at 0.64 s |
| **Another tool reaching the same content** | `knowledge.get` handed out what all three `search` paths withheld |
| **State, lifecycle and concurrency artefacts** | index files, the active pointer, a search racing a rebuild (ADR-0022) |

**Read the round count as a smell, not a target.** A fourth round that finds a
new family means the closure argument was incomplete — rebuild it rather than
fixing what the round found. A fifth that finds another instance of a class an
earlier round closed means that closure was never real.

### The orchestrator has no reviewer

Implementation gets three of them. The orchestrator's own briefs, findings and
decisions get none, and they are not safer for being written by the node that
reads every report. They are less safe: nothing downstream catches them, because
a brief is read as an instruction, not as a claim.

**Send an independent reader to the source before a brief, a carry-over list, a
PR description or a MEDIUM triage asserts what the codebase does or does not
contain.** All four are read as settled fact by whoever comes next.
`codex:codex-rescue` is the one available here. No script discovers that a test
already pins the constant you were about to ask someone to pin; the failure is
not knowing where to look, which is why re-reading it yourself reproduces the
error rather than correcting it. And **reproduction settles a behaviour, not a
property** — what FTS5 scores or what SQLite guarantees is not something a
scratch script tests by accident.

| The brief asserts | Verified before dispatch by |
| :-- | :-- |
| a behaviour — this value moves, this call fails | the orchestrator's own scratch script |
| what the codebase already does or does not contain — nothing enforces this, this is unpinned, no test covers this | an independent source read |
| a property of a third-party implementation — what FTS5 guarantees, what SQLite bounds | an independent source read |
| how many places something appears — every consumer of this, all the call sites, the only surface that reports it | a search command, with its output pasted into the brief |
| a mechanical instruction — a rename, a move, a test for settled behaviour | neither |

**Counting is neither a reading task nor a reasoning task.** On one day of
Milestone 5 four separate agents — the orchestrator, the docs specialist,
`codex:codex-rescue`, and the consulting session — each answered a "how many
places" question by inference, and each was wrong. One `grep` settled it. An
independent reader helps when the question is whether a claim holds; it does not
help when the question is how many, because a second reader reasons too.

State the population as well as the count. A search answers "how many" only once
the key is chosen, and choosing the key is inference — which is where Milestone 5
got it wrong twice, counting symptoms and then counting the wrong method. A
reader given the key can attack the key; a reader given only the list can attack
only the number.

Four of Milestone 5's orchestrator claims were false; the
[work log](docs/work-logs/2026-08-03-milestone-5-review-rounds.md) names all
four. Cost is not the reason to skip the check. Latency is the reason to keep it
narrow: a check before dispatch serialises assignments this file otherwise
launches together, so spend it on the rows above and not on every brief.

**Not over the reviewers' findings.** They already are independent readers who
go to the source. This is for the node they do not cover.

**A reviewer's claim is a claim.** Independence makes a finding worth acting on;
it does not make its stated mechanism true. Before a claim is carried into a
brief, check it against what the repository already records — the threat model,
the ADRs, the docstring of the thing being described. Two independent readers
can contradict each other, and the orchestrator is the only node that sees both.

### What "green" means

| Severity | Earns it |
| :-- | :-- |
| CRITICAL | Withheld or unauthorised content reaches a caller; or the product asserts something false that changes a security decision; or a published value varies with content the caller may not read, and that variation has been *demonstrated* to recover the content itself. **Reproducible now, in the shipped default configuration.** A parameter the documented API accepts is not an exemption; an operator-only configuration change is. |
| HIGH | Shipped behaviour is wrong, or a published claim is false — **without disclosure of content the caller may not read**; or a published value varies with content the caller may not read, where recovery has not been demonstrated and the reach is bounded and recorded; or a caller can make the system spend work not bounded by any recorded limit, through a documented entry point. |
| MEDIUM | The behaviour is correct but **unproven**: a test that cannot fail, a claim with no measurement, a gap a future change turns into a defect; or a cost that is bounded but materially larger than what the code or the docs record. |
| LOW | Clarity, naming, redundancy, a cost that is measured and bounded. |

CRITICAL is anchored to disclosure rather than to correctness in general, which
is the product's own grading and not a preference: in the threat model T-3 — an
agent following instructions injected into indexed content — is High, not
Critical. The harm of an agent acting on wrong knowledge is already graded High
there, so a retriever that returns the wrong content without disclosing anything
the caller may not read is HIGH here too.

Score the class, not the face. When one root cause produces several observables
and recovery has been demonstrated through any one of them, the class is
CRITICAL and its movement-only siblings describe the class's reach and what its
closure must cover — they are not separate findings a grade lower. A channel
with no demonstrated sibling still needs its own extraction program.

Name the class by its root cause, never by the shape of what it emits. A similar
observable arising from a different root cause is a different class, not a
sibling face: T-17's faces were quantities computed before the gate, while
T-17a and the timing residual are one class named *the index still holds the
withdrawn rows*, and that is why they carry their own severity and their own
recorded decision.

| Severity | Rule |
| :-- | :-- |
| CRITICAL | Must be zero. No exceptions, no deferral. |
| HIGH | Must be zero, or converted to a stated CRITICAL-free design decision with the reasoning recorded in the code or an ADR. |
| MEDIUM | Each one gets an explicit, recorded decision: fix now, or file an issue naming the milestone that will. Never silently carried. |
| LOW | Recorded in the PR description. May be deferred without an issue. |

**A finding is triaged the moment it is filed** into one of four dispositions:
*fix in the open PR*, *next cluster*, *backlog with a named milestone*, or
*recorded and closed*. A filing with no disposition is inventory manufactured,
not work discovered — filings outpaced closures 65 to 28 over 2026-08-19..25.

Each reviewer's scale drifts toward its own mandate, so **the orchestrator
applies the first table, never the reviewer who wrote the finding**, and names
each downgrade and its reason in the PR description. **A reported test gap drops
to MEDIUM only on a recorded check that the behaviour underneath is correct** —
correct, say what was checked; defective, that is a new finding at its own
severity. Round seven's HIGH-1 was a reported test gap leaking a `rejected`
item's note and a `draft` source's incoming edge into the default response:
unresolved, not inflated, and the label did not say which.

A threat-model entry and a review finding are scored on different scales. An
entry's severity is the residual after its controls and its *recorded*
deferrals; a finding's severity is what is true in the code now. T-6 stays
Medium as an entry while its query-side members are HIGH as findings, because
the per-query bound is transport-layer work deferred with its reasoning
recorded. Where no such deferral is recorded, the finding keeps its severity
and must be converted explicitly.

A finding is only closed when the fix is **verified the way the finding was
found** — if the reviewer reproduced it with a script, the fix is confirmed by
running that script, not by reading the patch.

Re-run the affected review after fixing. A round that ends without re-review is
not a round.

## When to stop and ask

The standing instruction is to keep moving through milestones. Escalate to the
user only for a **Blocking Issue**, which means all three of:

1. a choice has to be made now,
2. more than one option is technically defensible, **and**
3. the choice changes the product's character — its security posture, its public
   contract, its dependency footprint, or what it promises users.

Examples that qualify: whether to enforce sensitivity-based access control now or
in a later milestone; whether to take a heavyweight dependency to get semantic
embeddings; whether to break a published wire contract.

Examples that do **not** qualify — decide these, and record why: which of two
equivalent implementations to use, whether a MEDIUM is fixed now or filed, how to
name something, whether a comment is accurate.

When escalating, give the options, the trade-off in one line each, and a
recommendation. Do not stop with an open question and no analysis.

### A non-Blocking judgment goes to the reviewer node, not through the user

A judgment that is *not* a Blocking Issue but that the orchestrator cannot settle
from the request, the code, or the recorded rules does **not** wait on the user.
Route it, in one message, to the second reader and act on what comes back:

- **Codex (`codex exec`)** for anything settled by reading code or reasoning
  about a mechanism.
- **The `watchdog` agent** (`Agent` tool, `subagent_type: watchdog`) for process,
  priority, severity-grading, release/disclosure-handling, and closure-
  sufficiency judgment. It measures repo/CI/tracker state itself before it
  answers.

Form your own position first — options, one-line trade-offs, a marked
recommendation — then hand it over; a consult without a position is offloading.
The answer is a claim, not an order: you still apply the severity table and
record the decision, naming who was consulted. Only if the two readers disagree,
or the doubt survives both, does it become a Blocking Issue for the user.

**Why this is a rule and not a preference:** routing every judgment through the
user makes the user the bottleneck — the orchestrator idles waiting on a decision
a second reader could have settled in-session, and the human is consumed as a
router rather than reserved for the decisions only they can make. Measured on
this project, that routing was the largest single sink of the user's own hours.
The user's time is spent on Blocking Issues; everything else is settled between
the orchestrator, Codex, and the `watchdog` agent.

## Relaying subagent output

**A subagent's report is never shown to the user.** Whatever is not relayed did
not happen, from their side.

After a review round, always report: how many findings at each severity, what the
serious ones actually were, what was fixed, and what was consciously deferred and
why. A summary that says "reviews passed" after a round that found a CRITICAL is
a false report, even if the CRITICAL was fixed.

## Standing conventions

### Language

Theurian is open source. Anything that persists in the repository is read during
PR review by people who do not read Japanese, and it stays in the history
permanently.

| Register | Language |
| :-- | :-- |
| Anything durable in the repository: commit messages, documentation, ADRs, code comments, docstrings, PR descriptions, issue text | **English** |
| Briefs and instructions sent to subagents | **English** |
| Review output, and anything else that may be read by a teammate | **the caller's language, professional register, no cat-speech** |
| Conversation with the user | **Japanese**, cat-speech per the global personality rule |

The dividing line is direction of travel: text that lives in the repository or
instructs an agent is English; text delivered to a person as a report follows
the reader's language (professional register for a teammate; Japanese cat-speech
in conversation with this project's maintainer). A brief looks like scratch,
which is exactly why it needs naming — it gets quoted in reports, pasted into
issues, and read by whoever debugs the orchestration later.

**Non-English text as *data* is correct and must not be translated.** The rule
governs prose, not examples. `署名付きトークンを持つ` in ADR-0023, the CHANGELOG
and the test fixtures is the measured input that demonstrates the CJK
tokenization problem; translating it deletes the thing being demonstrated. The
same holds wherever a query or a corpus sample is in a given language *because*
its language is the point.

### Commits and local safety

- Commits: Conventional Commits, signed, with a DCO `Signed-off-by` trailer
  (`git commit -s`). One topic per PR.
- **A commit is triggered by its scoped tests going green, not by the work being
  finished** — at that moment, not at the end of a review round and not batched
  up for the flip to Ready; the full gate runs once before the Draft PR opens,
  and a report names the scope it ran — an unqualified "GREEN" means the full
  gate. Milestone 5 held 16,300 uncommitted lines for 28 hours; slicing them
  afterwards took three attempts, and the one that built
  opens with a 13,434-line commit. Size was not the cause: a port signature
  change spread across layers, and a commit that removes an API without moving
  its consumers does not build — once they have landed separately, no ordering
  works. Committing at the green keeps them together. It also bounds review:
  rounds four to six were handed `git diff main...HEAD`, which showed 7,792
  lines of a larger change — four production modules and three schemas were
  untracked, and so invisible to it. An uncommitted tree has no boundary a
  reviewer can check. The same green pushes to origin and opens a Draft PR — see
  *Early push and Draft PRs*.
- A cluster PR ships at its planned scope: reaching the planned commits or 8
  elapsed hours closes the batch — flip what is green, box-split the rest. A
  5-commit plan that lands 12 has traded closure latency for review surface.
- Never run a real `theurian setup`, `theurian uninstall`, or a detached
  `daemon start` on the user's machine — those are what write `~/.claude.json`
  and register the OS service. `--dry-run` is the form that is safe here, and
  redirecting `HOME` is not what makes it safe: see *Running the CLI on a
  development machine*, which is two rules and not one.

### Early push and Draft PRs

A topic branch is pushed to origin at its **first green commit**, and a **Draft
PR is opened at the same moment** — not when the work is finished. Every later
green commit is pushed when it lands. The Draft PR is the visibility surface —
the state of every lane is on GitHub, not on a local disk — and the crash-safety
net; CI runs on the Draft from the first push, so the required checks exercise
the work while it is still in flight.

**Draft → Ready is the review gate.** The review round, at the weight its blast
radius sets, runs before the flip; documentation follows the round, so the flip
is the last step and not the next one. The PR does not leave Draft until the
round is green — CRITICAL and HIGH at zero, per *What "green" means*.
Flipping to Ready *asserts* that the round is green; flip with a PR comment
recording the round — findings per severity, what was fixed, what was
consciously deferred and why. What this fixes is *when* the round runs, not how
heavy it is. Merge continues to gate on the required checks.

**A commit that fixes a review finding records the finding as history.** The
commit body carries a structured trailer:

```
Review-Finding: <reviewer> <SEVERITY> — <one-line finding>
```

for example `Review-Finding: adversarial HIGH — byte-identical body accepted
under a second item id`. The trailer is deliberately machine-parseable:
`git log --grep 'Review-Finding:'` reconstructs the review history, and it is
the form a future review-ingestion surface consumes as governed knowledge. The
round comment and the `Review-Finding:` trailer are English, like every other
PR-surface text — the reviewer's findings are summarised, not pasted.

**Embargoed disclosure work follows the same pattern on the private fork.**
Nothing — branch, PR, or CHANGELOG hint — touches public origin until the
advisory ships.

### Running the CLI on a development machine

Two different things escape a careless invocation, and only one of them is a
path. **Redirection contains writes. Nothing contains registration with the
login session's service manager.** Every rule below sits on one side of that
line, and a script that gets the first side right still gets the second side
wrong.

#### Writes: redirect them, and never lean on an earlier `cd`

`theurian init` writes `.theurian/` and appends to `.gitignore` **in the
process's working directory**, and it takes no argument that says where —
`init_command` resolves the project from `Path.cwd()`. So a verification script
that gets its `cd` wrong initialises Theurian into Theurian's own checkout.
Three times, each time to someone who had read the warning above — so the rule
is not "be careful":

- **Never depend on an earlier `cd`.** Invoke the CLI by absolute path to the
  virtualenv binary and set the working directory *in the same command*:
  `bash -c 'cd "$DIR" && exec "$BIN" init'`, or `cwd=` on a subprocess call.
  There is no directory argument to pass instead, which is why the earlier `cd`
  keeps looking like the answer.
- **Set `HOME`, `THEURIAN_DATA_DIR`, `UV_TOOL_DIR` and `UV_CACHE_DIR` to
  temporary directories in the same command** that runs the CLI, not in an
  earlier one.
- **Check `git status --short` here afterwards.** The damage is invisible if
  nobody looks. Undo it by deleting the block between `# >>> theurian >>>` and
  `# <<< theurian <<<` in `.gitignore`, then `rm -rf .theurian` — **not** with
  `git checkout -- .gitignore`, which discards every uncommitted change in that
  file. A `git checkout --` did exactly that to an uncommitted fix this
  milestone.
- **Mutation runs get their own tree.** `tools/mutate.py` copies the checkout;
  never clean a mutation up inside the tree you are editing.
- **A daemon is gone when the port is free, not when a `kill` returned.** A
  `kill` handed a subshell's pid returns 0 and leaves the daemon running, and
  the survivor answers the *next* run's health probe — that is how a
  `daemon-running` step read `satisfied` in a measurement that needed `missing`.
  **Dev-time daemon runs take `--port 7420`, and the check is two lines:**

  ```sh
  lsof -nP -iTCP:7420 -sTCP:LISTEN   # the thing under test
  lsof -nP -iTCP:7419 -sTCP:LISTEN   # where a run that forgot --port landed
  ```

  The second line is not optional, and what it means depends on whether a
  resident daemon exists. **While none does, anything answering on 7419 is the
  accident** — the survivor this bullet exists to catch comes from a run that
  omitted `--port`, and such a run lands on 7419, not 7420 — so no output from
  either line means free. **Once a resident dogfood daemon owns 7419**, that
  line stops reading "free" and starts reading "the resident one, not mine", so
  only the first line answers "is the thing under test gone". Either way the six
  commands that take `--port` (`setup`, `doctor`, `uninstall`, `auth rotate`,
  `daemon start`, `daemon status`) each need `--port 7420`; once a resident
  daemon owns 7419, omitting it no longer merely muddies the check — the command
  describes or acts on the resident daemon while reading as if it described the
  thing under test. `uninstall` is the one that bites, since `--dry-run` is
  mandated for it; `daemon stop` takes no `--port` at all, so a dev daemon is
  started with `--foreground` and stopped with Ctrl-C in its terminal. Full
  detail, table and re-count:
  [development.md](docs/contributing/development.md#running-the-daemon-on-a-development-machine).

#### Registration: `--dry-run` is the only form to run here

`launchctl bootstrap` takes a *domain* and a *path*, and only the path comes
from `HOME`. `LaunchAgentManager` writes the plist to
`$HOME/Library/LaunchAgents/dev.theurian.daemon.plist` — redirected — and then
bootstraps it into `gui/<uid>`, where the uid is `os.getuid()` and no
environment variable reaches it. A real `setup` under a redirected `HOME`
therefore registers a service **in the real login session**, pointing at a plist
in a scratch directory. That happened: the scratch tree had no `uvicorn`, so the
service crash-looped in the developer's own launchd until it was booted out by
hand.

`SystemdUserManager` splits the same way for a different reason. The unit goes
to `$HOME/.config/systemd/user/theurian.service`, while `systemctl --user`
addresses the session's own user manager, reached through `XDG_RUNTIME_DIR`
rather than `HOME`. Not measured — there is no Linux machine here — but no
environment variable redirects that manager either.

- **On a development machine, run `theurian setup --dry-run` and nothing else.**
  `SetupService.run` returns `PLAN_BUILT` before it reaches `_apply`, and every
  probe on the way is a read: a `--dry-run` against a fresh sandbox created no
  files at all — not even `THEURIAN_DATA_DIR` — and registered nothing. A real
  `setup` needs a disposable machine or a container.
- **`theurian uninstall` is not the read-only twin.** It applies by default, and
  it deregisters from the real login session for the same reason `setup`
  registers there. Pass `--dry-run`.
- **The check is not a directory listing.** After a real `setup` under a
  redirected `HOME`, `~/Library/LaunchAgents` looks clean, because the plist is
  in the scratch tree. Ask the service manager instead:

  ```sh
  launchctl list | grep theurian                       # macOS: silent is clean
  launchctl bootout gui/$(id -u)/dev.theurian.daemon   # macOS: the cleanup
  systemctl --user is-active theurian.service          # Linux: the same question
  systemctl --user disable --now theurian.service      # Linux: the cleanup
  ```

  Both cleanups are the commands `LaunchAgentManager.uninstall` and
  `SystemdUserManager.uninstall` issue themselves. Type the launchd one whole:
  `launchctl bootout gui/$(id -u)` with no label boots out the login session.

Say in the report that a script ran the real CLI, what it was pointed at, and
whether it registered anything. An unreported near-miss is the one that becomes
a habit.
