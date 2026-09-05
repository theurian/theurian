# Orchestration checklists

[CLAUDE.md](../../CLAUDE.md) holds the rules that govern how this project is run
with Claude Code: who owns which work, when to call a specialist, what a review
round is for, what "green" means. This file is the act-level form of those
rules — four checklists whose lines are things to *do* at the moment they have
to be done, and three protocols saying where a lesson, a finding and an issue
land.

**These checklists operationalize CLAUDE.md; they never override it.** Where a
line here and a rule there could be read as disagreeing, CLAUDE.md wins and the
line here is the defect to fix. Nothing here grants a permission CLAUDE.md
withholds.

A line earns its place here when a rule was violated in practice *and* the
violation had an act-shaped cause — a command run in the wrong order, a check
nothing prompted, an instrument that could not see. Rules whose violation is a
judgement failure stay in CLAUDE.md, where judgement is discussed.

Every dated claim below names the pull request, commit or document it was
measured against. A line with no anchor is a rule, not a measurement.

This is a project-process document, like the [release procedure](release.md):
it describes how *this repository* is worked on, not how Theurian behaves. It is
excluded from the published documentation site for that reason.

## DISPATCH — before an assignment leaves the orchestrator

- Reproduce the issue first. The **measured file set** partitions the clusters;
  the issue title does not.
- Group by that file set: same set → one worktree, worked serially; disjoint
  sets → parallel worktrees.
- Put any change touching `SCHEMA_VERSION` or the CHANGELOG's `[Unreleased]`
  section on the serial spine, whatever cluster it belongs to.
- Query the project's own failure history before the ACs are written.
  `knowledge.search` for the failure families; `review.findings` for the landed
  `Review-Finding:` trailers, filtered by `family`, `specialist` or `severity`.
  This closes the loop through the product — failures become governed knowledge,
  surfaced at the next Definition of Ready.
  - **`review.findings` takes no path.** Its filters are `projectId`,
    `reviewer`, `severity`, `family`, `specialist`, `commitSha`, `pullRequest`,
    `q` and `limit`, and no field of its response carries a path either. To get
    from a file to its findings, bridge through history yourself:
    `git log --format=%H -- <path>` for the commits that touched it, then one
    `commitSha` query per commit. A path filter on the tool is a product gap for
    a future phase, not something this line can promise.
  - Honest caveat: retrieval quality here is **unmeasured**. There is no
    golden-query baseline until [Phase A](../roadmap.md) ships one, and
    `review.findings` serves trailers from git history — it is not review
    ingestion, which the server still reports as `reviewIngestion: false`. So
    this step *supplements* the checklists; it does not replace them.
- Write the acceptance criteria before dispatch, in EARS: a DON'T as
  *If \<condition>, the \<system> shall NOT \<behaviour>*, a behaviour example
  as Given-When-Then.
- Name each AC's verification explicitly — a named test, a measurement, or a
  search command. An AC with no named check is not Testable and does not
  dispatch.
- Put the authority question in the brief: **what authority makes each factual
  question factual?** Ask it in round one, not at the round where the class
  finally names itself.
- Declare the perspective block per reviewer at design time — standing mandate,
  this-change lenses, claims to attack — and freeze it for the PR's whole review
  lifecycle.
- Restate the applicable fences **in** the brief. A fence read once elsewhere
  measurably does not prevent the act; only a fence in the brief does.
- When the assignment runs the CLI, restate the dev-machine fences by name:
  `--dry-run` only for `theurian setup` and `theurian uninstall`, absolute
  binary path with the working directory set in the same command, `HOME` /
  `THEURIAN_DATA_DIR` / `UV_TOOL_DIR` / `UV_CACHE_DIR` redirected in that same
  command, `--port 7420` for dev daemon runs. Detail and the port table:
  [development.md](development.md#running-the-daemon-on-a-development-machine).
- When the assignment runs the full suite or a mutation batch, tell it to check
  for another lane's run first — reviewer briefs included, not only implementer
  briefs.
- Give every committing agent its own worktree. Never two committing agents on
  one checkout.
- Give a mutating agent a plain `git clone` of the branch at a non-dot path, not
  a `git worktree add`. `tools/mutate.py` refuses `--with-git` from a linked
  worktree — "a linked worktree or a bare checkout does not carry the index this
  lends" — and dot paths trip the census filter. Met on
  [#557](https://github.com/theurian/theurian/pull/557) round two.
- Tell the assignment to commit at each green and push at the first one. Long
  assignments die in silent phases; an uncommitted phase is lost work.
- State every population as a search command with its output pasted, and name
  the key beside the count. A reader given the key can attack the key.
- Re-read the source for any claim copied from a handoff note, a queue list, or
  an earlier report. Copying it into a brief makes it a claim you are now making.
- List what is known-unfinished, so the reviewer spends its time elsewhere.
- Name the non-goals. An adjacent finding is box-split, never folded in.

## REVIEW — running a round

- Weigh the round by blast radius before dispatching it: disclosure, governed
  state, security claims and the wire contract take the full sync round;
  behaviour with no disclosure surface takes code review sync; prose, process
  guidance and CI plumbing take one light pass.
- Route by what would settle the claim, not by file type. When the two tables
  disagree, the claims table wins: a prose file asserting a measured property is
  a disclosure-class claim in a light-class file.
- Give round one all three reviewers at full scope — for a full-round change;
  the blast-radius table above says which. Narrow later rounds to what the fixes
  newly claim, attacked through the same frozen perspective block.
- Sweep every fix diff for **new universal prose** before dispatching a
  re-review. Each fix-authored *covers / closes / cannot / all / never* sentence
  becomes a derivation, an enumeration with an escape table, or is deleted.
- Re-verify a prose correction at code-review weight unless the sentence is
  wheel-shipped or a security claim.
- Run the reviewers' own methods before dispatching round two: mutation over the
  suite, timing across the paths the change touches. A finding you surface costs
  no round.
- Reproduce every finding yourself before assigning its fix, and grade the
  **mechanism** separately from the finding — a real finding can carry a false
  mechanism, and a brief built on it sends the fix at the wrong cause.
- Hold a fix batch until every reviewer's measurements are done. Landing fixes
  mid-round moves the diff boundary under a reviewer still measuring the same
  worktree, and a finding measured across that boundary cannot be told from a
  phantom afterwards. On
  [#569](https://github.com/theurian/theurian/pull/569) round one the first
  batch went out while a reviewer was still measuring; the measurements happened
  to predate the edits, so nothing was contaminated, and the rule was adopted
  for round two.
- Apply the severity table yourself, never the reviewer who wrote the finding,
  and name every downgrade and its reason in the PR description.
- Drop a reported test gap to MEDIUM only on a recorded check that the behaviour
  underneath is correct. Say what was checked.
- Grade an **unmerged design document's** side matter — counts, anchors, table
  contents, scope claims — at a MEDIUM ceiling: fix-in-pass or file, never
  round-generating. The severity table's "a published claim is false is HIGH"
  was written for shipped surfaces; applied to an unmerged draft it makes every
  side-matter drift gate-blocking and manufactures rounds. Standing order of
  2026-09-05, with its measured ground and its scope recorded in
  [the ruling on this file's introducing pull request](https://github.com/theurian/theurian/pull/580#issuecomment-5553419251).
  **Scope it narrowly:** unmerged design side matter only, never a shipped
  surface — and where this ceiling and CLAUDE.md's severity table conflict, the
  severity table governs.
- Pre-commit the ending of any arc approaching round four — what merges, what
  files — before the round runs. Per-round concurrences that are each locally
  sound do not substitute for the campaign brake.
- Close a finding by verifying it the way it was found. If a reviewer
  reproduced it with a script, run that script.
- Close a class with a closure argument written by someone other than whoever
  wrote the fix.
- Record a round-one finding on a family the brief enumerated as an
  implementation-stage failure, in the PR's round comment. The same specialist
  caught on the same family twice writes that family into the **implementing**
  specialist's agent definition — precedent
  [#554](https://github.com/theurian/theurian/pull/554) and
  [#578](https://github.com/theurian/theurian/pull/578).
- Post the round record as a PR comment **before** any fix dispatch cites it.
  "Recorded" means a URL exists.
- Tag a finding outside the frozen perspective block **out-of-perspective**, file
  it with a disposition, and do not let it hold the flip. A reproducible CRITICAL
  reports immediately regardless.
- Ship at round three: everything that is not a reproducible CRITICAL or HIGH is
  filed as an issue, or recorded and closed, per the filing filter's
  dispositions — and the PR flips. Reaching a third round is itself the finding.

## MERGE — landing a branch

- Run `gh pr list --state open --draft=false` at every transition — closing an
  assignment, opening the next — and merge anything green before dispatching new
  work.
- Read the settled checks output, **then** merge as a separate action. A poll
  loop exits on no-pending, not on no-fail; a chained merge ran over a failing
  non-required check on
  [#470](https://github.com/theurian/theurian/pull/470), fixed forward five
  minutes later in [#475](https://github.com/theurian/theurian/pull/475).
- Confirm `git rev-parse origin/<branch>` equals local HEAD before any flip or
  squash. The squash reads origin; a docs stage committed and not pushed does not
  land.
- Pass an explicit `--body` to `gh pr merge --squash`, carrying that PR's
  `Review-Finding:` trailers and the DCO `Signed-off-by`. Without it the squash
  message is the PR title and description only, and the trailers are dropped:
  [#399](https://github.com/theurian/theurian/pull/399)'s squash `9d17a41`
  carries 0 trailers, and the recovery
  [#402](https://github.com/theurian/theurian/pull/402) (`0bd5857`) carries the
  6 that were lost.
- Count landed trailers with
  `git log -1 --format='%B' <sha> | grep -c '^Review-Finding:'`. There are two
  ways to get a wrong number here and both of them read as success:
  - **Omit the `-1` and you have counted the whole reachable history.** The same
    key over `75fe9b4f` gives 27 with `-1` and 869 without; over `9d17a41`, the
    squash that dropped its trailers, it gives 0 with `-1` and 92 without. The
    check meant to catch a squash drop stops detecting it at all.
  - **Omit `--format='%B'` and `git log`'s indented body defeats the `^`
    anchor**, returning 0 — and a 0 == 0 comparison false-passes.
- Byte-compare commit messages after any rebase. `cleanup=strip` removes
  column-0 `#` lines — an issue reference in a body vanishes silently.
- Grep your own commit body for `^Review-Finding:` before committing. Prose that
  wraps the token to column 0 is indistinguishable from a trailer to every
  collector, so it inflates the branch count, breaks the branch-versus-merge
  comparison above, and enters the served findings corpus as a finding whose
  text is half a sentence. This file's own introducing branch produced one and
  had to rewrite the commit to remove it. Keep the token off column 0 anywhere
  it is not a real trailer.
- After any history rewrite, re-check **both** trailers on every commit — the
  signature and the `Signed-off-by`. `git commit --amend -F <file>` replaces the
  whole message, so a sign-off that `-s` had added is gone unless `-s` is passed
  again; this file's own branch lost one exactly that way, and CI caught it
  because the local check had verified subjects only. An unsigned rewrite blocks
  the merge with no failing check to point at; a missing sign-off fails the DCO
  half loudly. They are two steps of one job — run both, not the one that broke
  last time.
- Check **where** each CHANGELOG entry landed after any rebase or merge across a
  release cut, not that the operation succeeded. Git context-matches the hunk into
  the just-released section with no conflict at all
  ([#513](https://github.com/theurian/theurian/pull/513), 2026-09-03), which would
  document an unshipped fix as shipped.
- Tag the **cut commit**, not HEAD, when anything merged during an open release
  window. A HEAD tag fails the empty-`[Unreleased]` guard
  ([#411](https://github.com/theurian/theurian/pull/411), 0.1.0.dev14). Verify
  with `git rev-parse <tag>^{commit}`.
- Keep the commit scope to `[a-z-]+`. Pipe
  `git rev-list origin/main..HEAD --no-merges` subjects through that pattern
  before pushing; an issue or ADR number goes in the subject text or the body.
- Re-read CodeQL alert numbers from the merge ref immediately before acting on
  them. Alert identity is location-keyed, so moving code can retire one number
  and raise another for the same rule in the same file: across
  [#569](https://github.com/theurian/theurian/pull/569) the
  `py/overly-permissive-file` finding on `no_follow.py` is alert 18 as of
  2026-09-06, and 17 is gone from the list. A number carried over from an
  earlier report may now address a different instance, or nothing at all.
- Retarget a stack's dependents before merging its base. `--delete-branch` on the
  base closes the dependent PR.
- Do not edit `Closes #N` out of a body expecting it to unlink; the squash still
  auto-closes.

## INSTRUMENT — measurement discipline, and the instrument ledger

This section is both a checklist and the ledger where tooling and instrument
quirks are recorded (see [The filing filter](#the-filing-filter)).

- Show the key hitting a **planted or known-present positive** before citing any
  zero, empty diff, or "no members found" as evidence. An instrument's silence
  means nothing until the instrument has been heard to speak.
- State the key, the instrument and the scope beside every count, and keep any
  content filter out of the population key — a filter is not a population.
- Diff the classified set against the population **by script** before posting a
  triage or classification table. A total carrying a ✓ that no script produced
  is asserted, not computed: the 2026-09-06 tracker sweep's own arithmetic was
  wrong twice — one band short by two rows, and six open issues missing from the
  table altogether — and a scripted diff found both
  ([correction](https://github.com/theurian/theurian/issues/551#issuecomment-5553088853)).
- Use `git grep` for repository populations. `rg` honours `.git/info/exclude`
  even for tracked files, and skips dot directories without `--hidden`.
- Do not write `\b` in `git grep -E`. POSIX ERE has no word boundary and the
  pattern fails silently.
- `rg -r` is `--replace`, not recursive. `rg -rln <pattern>` consumes `ln` as the
  replacement text and prints rewritten lines at exit 0 — never a file list.
- Read `wc -l` before any `head`, and use a wrap-aware or multiline pass for a
  phrase key. A line-wrapped phrase escapes a single-line grep.
- Read what `main` contains with `git show origin/main:<path>`, never the
  checkout's file, unless `git rev-parse HEAD` equals `origin/main` and the tree
  is clean.
- Check a cited SHA with `git merge-base --is-ancestor <sha> origin/main`.
  Reachability from `HEAD` approves the defect it is meant to catch: a squash
  replaces the branch, so every SHA `HEAD` vouches for today resolves on no ref
  once the PR lands.
- Keep both sides of a comparison on the same instrument. A `tracemalloc` figure
  against a clean wall-clock figure is a fabricated ratio, and naming the
  instrument on one side only is the same defect.
- Name the scope a measurement excludes and check it bounds what the claim
  bounds — the parse, the served corpus, a function nothing calls.
- Re-run a mutation `KILLED` verdict single-worker when its only failure is a
  concurrency test; a multi-worker batch false-kills.
- Clear `__pycache__` before believing a `SURVIVED` verdict. A stale `.pyc`
  reports the unmutated behaviour.
- Pass `--with-git` to the mutation driver, quote the scope argument, and guard
  against "no tests ran" — pytest's exit 4 otherwise reads as a `KILLED`.
- Clean up a mutation run by the PIDs and paths recorded at spawn. A
  `pkill -f` pattern reaches other lanes.
- Treat a red test as proof a failure exists, not as its location. Get the
  mechanism before grading.
- Verify what the reader ends up with, not that the command resolves. A
  redirect that resolves can still deliver the wrong page.
- Never wait on a pattern your own wait loop matches; the loop matches itself and
  never exits.
- Confirm a daemon is gone by the port being free, not by a `kill` returning 0.
- Run bulk tracker mutations from a **background, resumable, idempotent**
  script. At roughly 4 s an API call a foreground pass hits a ten-minute ceiling
  after about two dozen items: the 2026-09-06 sweep landed 23 closes that way
  and the remaining 115 from a regenerated background script, script-diffed
  against the live open set before it resumed — zero failures, zero
  double-closes
  ([execution record](https://github.com/theurian/theurian/issues/551#issuecomment-5553412781)).
- Run the whole suite from a **non-dot checkout**. `controls_discharge` drops
  every path with a dot component, so a checkout under `.claude/worktrees/`
  gives it an empty test population and two census audits fail on the walker
  rather than on the tree
  ([#558](https://github.com/theurian/theurian/issues/558)). Both pass from a
  plain clone of the same tree at `386aba76` — a branch commit of pull request
  #580, not reachable from `main` and never going to be, since the squash
  replaces it. Measured 2026-09-06.
- Expect `test_bare_install`'s `daemon status` case to fail on a machine running
  a resident daemon: it asserts `listening is False`, and a daemon answering the
  default port makes it true. `lsof -nP -iTCP:7419 -sTCP:LISTEN` says whether
  the failure is the machine or the code. Measured 2026-09-06: it fails the same
  way at `75fe9b4f` with nothing applied.

## The learning loop

An incident is not recorded by being described. It is recorded when it has a
landing place, and the same day it happened.

### Four fields, every time

| Field | What it holds |
| :-- | :-- |
| **Shape** | The incident in one line — what was believed, what was true |
| **Check** | The check that would have caught it, stated as an act |
| **Lives at** | Where that check now lives: a brief template, a line in this file, an agent definition, a test, a CI gate |
| **Knowledge item** | For an incident whose *why* has durable reuse value: a governed item, written through `theurian propose` and landed with `theurian propose accept` |

An incident recorded without a **Lives at** is not recorded; it is narrated.

**The two layers do different jobs, and both are needed.**

| Layer | Direction | When it reaches the reader |
| :-- | :-- | :-- |
| The checklist line | **Push** — binding | At action time. A fence not in the brief does not prevent the act |
| The knowledge item | **Pull** — provenance and answer | On query. Nobody searches mid-mistake, but everybody searches afterwards |

A checklist line is short because it has to be read while acting; it therefore
cannot carry the reasoning. The knowledge item carries the reasoning, the
measurement and the provenance, and is where "why is this line here?" is
answered. Where a line has a knowledge item, the line cites it.

### Recurrence escalates one layer

A lesson that recurs after being recorded has been recorded at too weak a layer.
Move it up exactly one rung — the recurrence itself is the evidence, so no
further argument is required:

1. **Prose note** — a paragraph in a document, a PR comment
2. **Checklist line here** — an act, at the moment it is performed
3. **Agent definition or brief template** — every future instance is born
   knowing it
4. **Executable check** — a test, a lint rule, a CI gate

CLAUDE.md's burn-in rule is rung 3's instance: a specialist caught on the same
enumerated family twice has that family written into its own definition, because
recurrence is a defect of the definition rather than of the instance
([#554](https://github.com/theurian/theurian/pull/554),
[#578](https://github.com/theurian/theurian/pull/578)).

A ledger entry that recurs means the check must move into a brief or a
checklist. Re-ledgering it is the null action.

### Campaign close runs a recurrence audit

At the close of a campaign, milestone or arc, sweep the incident ledger for
shapes that recurred **after** being recorded. Each recurrence is an escalation
owed — a rung to climb, with an owner and a landing place — not a note to write
again. A close that reports only what was delivered has skipped the audit.

## The filing filter

The tracker exists to schedule work. A ticket nobody will schedule is inventory,
not knowledge. Before any issue is opened, three questions:

1. **Does it GATE anything** — a release, a milestone, an open PR's class
   closure?
2. **Can a USER or trier hit it** in shipped or about-to-ship behaviour?
3. **Will anyone SCHEDULE it** within a named horizon — a phase, a cluster, a
   release?

**Three NOs means do not file.** Record it where it lives instead:

- the PR's round record — where LOWs already belong under the severity table;
- a code comment or docstring at the site;
- this file's instrument ledger, for a tooling or instrument quirk;
- a governed knowledge item, per the learning loop above.

"Recorded" and "filed" are different acts. The box-split rule exists to protect
a slice's scope, not to manufacture tickets: a finding can be box-split into a
paragraph of a round record as safely as into a number.

Three refinements:

- **A sibling face of a known class** goes as a ledger comment on the **owning**
  issue, never as a new number. A new number splits the class's history.
- **Security findings keep their full recording discipline.** The filter changes
  *where* a no-action item is recorded, never *whether*. A HIGH converted to a
  design decision still gets its recorded reasoning; a CRITICAL is not
  filterable.
- **Tooling and instrument quirks default to the instrument ledger above**, not
  to tickets — they are read while measuring, and a ticket is not.

## The tracker

The filing filter decides *whether* an issue is opened. This section decides what
an open issue must carry.

Priority and the two dates are org-level issue fields, set through
`PATCH /repos/theurian/theurian/issues/{n}` with an `issue_field_values` entry
whose `value` is the option **name** as a string — not the numeric option id.
Type is GitHub's native issue type. The scheme's first application across the
whole tracker, with the field ids and the positive control each instrument was
set under, is the
[2026-09-06 sweep record](https://github.com/theurian/theurian/issues/551#issuecomment-5553070490)
— read it with its
[correction](https://github.com/theurian/theurian/issues/551#issuecomment-5553088853),
which recomputed the record's own band totals by script and supersedes them.

**The `issue_field_values` PATCH replaces the issue's entire field-value set.**
A later call carrying only the two date fields silently wiped a Priority an
earlier call had set — caught by this file's own light pass and restored in one
combined call
([execution record](https://github.com/theurian/theurian/issues/551#issuecomment-5553412781)).
Write all of an issue's fields in every PATCH, not just the ones you are
changing.

### Priority

| Value | Means | Consequence |
| :-- | :-- | :-- |
| **Urgent** | Blocks a release or a gate, or is a reproducible CRITICAL or data-loss defect in shipped behaviour | Preempts a lane |
| **High** | Current campaign scope | The campaign pulls its next cluster from here |
| **Medium** | Committed to a named horizon, carried as a phase label | Pulled when that phase opens |
| **Low** | Deliberately kept with no horizon — contributor-friendly, or an opportunistic batch | Used sparingly |

An item that is neither Low-worth-keeping nor Medium-schedulable **closes with
its record**, per the filing filter. What is actually queued is Urgent plus
High; counting the rest as backlog overstates it, because everything else is
either horizoned or deliberately parked.

**Reading that set back is not a search-bar qualifier.** Issue search does not
support org-field qualifiers: `gh issue list --search 'priority:High'` returns 0
while the same command without the qualifier returns the full open list
(measured 2026-09-06). Two routes do work:

- **The issues UI's Priority filter and group-by panel**, which renders from the
  org field. This is the one that answers "what is queued" at a glance.
- **`GET /repos/{owner}/{repo}/issues/{n}/issue-field-values`**, per issue. Read
  the name off `single_select_option.name` in the same row: `value` carries the
  numeric option id, and projecting only `value` is what makes the name look
  absent — no second call is needed. An issue with the field unset simply omits
  it from the response.

### Type

Set on every open issue.

| Type | Means |
| :-- | :-- |
| **Bug** | Unexpected behaviour |
| **Feature** | A new capability |
| **Task** | Process, documentation or tooling work |

### Dates

`Start date` and `Target date` are ISO strings.

- **Urgent and High carry real plan dates.** Start is when a lane actually pulls
  the item; target is realistic completion at the measured cadence, not an
  aspiration.
- **Medium is dated only where the horizon has a plausible start**, and then
  coarsely. A phase the roadmap commits no dates to is **never** dated. An
  undated Medium is a legitimate state: it appears in backlog views and not on
  the timeline, which is the truth about it.
- **Low is never dated.**
- **A passed target date is a triage event.** Re-date it with a stated reason, or
  re-triage the item — never leave it stale. Date rot is one of the shapes the
  learning loop's recurrence audit sweeps for.
