# Working on Theurian with Claude Code

`.claude/agents/` defines what each specialist does once it is called. This file
defines the part that cannot live there: **when to call them, and how to decide
what to do with what comes back.**

The distinction matters because the expensive mistakes are orchestration
mistakes. A reviewer that finds a CRITICAL is doing its job; an orchestrator that
receives one and quietly downgrades it is not.

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
not effort but **independence**: work that is written and then reviewed inside a
single context gets graded by the mind that produced it, and that mind has
already decided the change is correct. Handing implementation to one agent and
judgement to another is what makes the review round mean anything.

What the orchestrator keeps for itself:

- deciding **what** to assign, and to whom, and in what order;
- writing each brief — scope, requirement IDs, ADRs, known-unfinished;
- **verifying** the returned work by running it, never by reading it;
- weighing findings against the severity rules below, and deciding what is
  blocking;
- relaying results to the user.

Two narrow exceptions, both of which must be stated in the response when used:

1. **Reproduction and verification scratch scripts.** Confirming a defect exists,
   or that a returned fix actually closes it, is the orchestrator's own job — it
   is the check on the specialists, so it cannot be delegated to them.
2. **A trivial mechanical follow-up inside work already delivered and
   reviewed** — a rename, a moved import, a typo. If it needs a decision, it is
   not mechanical; assign it.

When several assignments are independent, launch them **in one message** so they
run concurrently. Sequence them only where one genuinely consumes another's
output.

## Milestone definition of done

A milestone is not done when the code works. It is done when all of this has
happened, in order:

1. **Implement — by assignment, never by the orchestrator** (see above), with the
   quality gate green after every logical commit:
   `uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pytest -q`
2. **Run it for real.** A scratch script or a real CLI invocation against a
   temporary `HOME` and `THEURIAN_DATA_DIR`. Every milestone so far has found a
   defect at this step that no test caught. Reading is not verification.
3. **Three reviews in parallel** — see below. Loop until green.
4. **Documentation follows** — CHANGELOG (breaking changes named as such), README
   status and roadmap, new ADRs, and every ADR compliance section the milestone
   discharged or newly owes.
5. **PR** with a description that states what was found and fixed, not only what
   was built.
6. **CI green**, then squash-merge.

## The review round

Before opening any PR, launch all three **in a single message** so they run
concurrently:

| Agent | Answers |
| :-- | :-- |
| `theurian-code-review` | Is it correct and maintainable? Does it hold its own stated invariants? |
| `theurian-security-review` | Does it satisfy the SEC-* requirements and the threat model? |
| `theurian-adversarial-review` | Can I break it, and can its tests actually fail? |

Each brief must carry, explicitly:

- the diff scope (`git diff main...HEAD`) and the files that matter
- the requirement IDs and ADRs the change touches
- what is *known* to be unfinished, so the reviewer spends its time elsewhere

Do not write these briefs from memory each time — the agent definitions hold the
standing context; the brief adds only what is specific to this change.

### What "green" means

| Severity | Rule |
| :-- | :-- |
| CRITICAL | Must be zero. No exceptions, no deferral. |
| HIGH | Must be zero, or converted to a stated CRITICAL-free design decision with the reasoning recorded in the code or an ADR. |
| MEDIUM | Each one gets an explicit, recorded decision: fix now, or file an issue naming the milestone that will. Never silently carried. |
| LOW | Recorded in the PR description. May be deferred without an issue. |

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

## Relaying subagent output

**A subagent's report is never shown to the user.** Whatever is not relayed did
not happen, from their side.

After a review round, always report: how many findings at each severity, what the
serious ones actually were, what was fixed, and what was consciously deferred and
why. A summary that says "reviews passed" after a round that found a CRITICAL is
a false report, even if the CRITICAL was fixed.

## Standing conventions

- Documentation and commit messages: **English**. Conversation with the user:
  **Japanese**, cat-speech per the global personality rule.
- Review output and anything that may reach a teammate: **professional Japanese,
  no cat-speech.**
- Commits: Conventional Commits, signed, with a DCO `Signed-off-by` trailer
  (`git commit -s`). One topic per PR.
- Never run a real `theurian setup`, `daemon start` (detached), or anything that
  writes to `~/.claude.json` or `~/Library/LaunchAgents` on the user's machine.
  Redirect `HOME`. This has gone wrong once already.
