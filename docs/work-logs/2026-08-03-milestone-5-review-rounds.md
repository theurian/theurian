# Milestone 5: what the first five review rounds actually found

Milestone 5 turned `knowledge.search` into hybrid retrieval. **Eight review
rounds ran before the PR — `39ce944`'s message attests the count — and this log
accounts for the first five.** Rounds six to eight are not covered here; their
converted findings are issues
[#17](https://github.com/theurian/theurian/issues/17) to
[#20](https://github.com/theurian/theurian/issues/20), and the arc across all
eight is in the pull request description.

Two rules in [`CLAUDE.md`](../../CLAUDE.md) came out of it — *a finding is closed
by a closure argument, not by a fix*, and *the orchestrator has no reviewer*.
This log holds the account those rules were drawn from, so the file every session
loads can state the rule and point here.

It was written at `79870f9` and committed in `7d3b3f6`, which was sliced from the
same working tree as `07f16ef` — the gate-inside-ranking change this log
describes below as uncommitted. Observations about the state of the tree
therefore name `79870f9` rather than `HEAD`: `HEAD` moves and a commit does not,
so the same command against a later commit gives a different answer without
either answer being wrong.

Round attribution below comes from two sources: the round column in
[T-17](../security/threat-model.md) for the information-disclosure family, and
the fix commits for everything else. The repository attests rounds one through
four directly — the threat model's amendments name round four. Round five's
contents are not recoverable from it, so findings that cannot be mapped to a
numbered round are listed as such rather than guessed at.

## The T-17 family: one defect, five faces

An unprivileged caller queries an index that is older than the knowledge it
serves — the normal gap between `migrate apply` and `theurian index build`.
`results` correctly withholds the matching content, and some other published
value moves anyway, exactly when the query matched text the caller may not read.
The trigram retriever matches any substring of three characters or more, so that
movement is sequential extraction rather than existence detection.

| Face | What was computed before the gate | Found in round |
| :-- | :-- | --: |
| `usedTokens` | the token budget, priced on candidates | 1 |
| `count` | `limit`, truncating candidates | 2 |
| `fusedScore` | the RRF ranks | 3 |
| `CANDIDATE_DEPTH` | the rows *fetched* from each retriever | 3 |
| the excerpt | `diversify` choosing which chunk of a document to publish | 3 |

Each round reasoned about one *quantity* to be moved past the canonical gate
while the gate itself stayed after the ranking, so the next round found a
sibling. What closed the family was not a sixth patch: the gate moved inside the
ranking.
`RetrievalService.search(request, visible)` applies visibility to each
retriever's rows before they are fused, so fusion, `diversify`, `limit` and the
budget all see exactly the rows an index that never held the withheld documents
would have offered.

### The table is a discovery order, not a fix order

This is the more useful shape of the story, and it is not what the table looked
like it was saying. **The family was found one face at a time across rounds one
to three, and closed once, structurally, at the end.** No face has a commit that
closes it on its own:

- At `79870f9`, `application/visibility.py` was untracked and `Visibility`
  appeared zero times in `application/retrieval_service.py`, so the whole
  gate-inside-ranking change was uncommitted. It landed afterwards, as `07f16ef`.
- At `79870f9`,
  `git log -S "usedTokens" 79870f9 -- packages/theurian-core/src/theurian/mcp/`
  returned only `3b7139b`, which introduced the field, and `f30881e`. Round one's
  fix commit `c9a65d3` is large — 13 files, 680 insertions — and its message
  enumerates 2 CRITICAL and 8 HIGH in detail without once mentioning the
  token-budget oracle. `usedTokens` appears twice in that whole diff: as an
  unchanged context line still reading
  `"usedTokens": outcome.used_tokens`, and as one added assertion
  (`assert huge["retrieval"]["usedTokens"] <= 32_000`) which was later judged to
  hold with the clamp deleted. Nothing in the commit moves the budget
  computation to the far side of the gate.

  > **Corrected after this log was first written.** It said `c9a65d3` "touches
  > `mcp/tools.py` alone", which is false: it touches thirteen files. The
  > conclusion is unchanged and now rests on what the diff does rather than on
  > how wide it is. Recorded because the log's own point is that an unverified
  > claim survives by sounding like evidence.
- At `79870f9`, `mcp/search.py` still computed
  `"usedTokens": outcome.used_tokens` over candidates and only afterwards called
  `_resolve_through_canonical`. The face attributed to round one was open
  through every round committed by then.

So the round column answers "which reviewer saw it", and nothing else. The
sentence in [the threat model](../security/threat-model.md) that read *each face
was closed in front of the reviewer who found it* has been corrected to say so;
whether interim per-face patches existed in the working tree between rounds is
not recoverable from the repository.

The lesson is the one the closure-argument rule turns on: three rounds of
per-face work produced no committed fix for the first face, and one structural
change closed all five.

Measured extraction cost, each figure one program run to completion against the
code as it stood:

| Face | Recovered | Calls |
| :-- | :-- | --: |
| `usedTokens` | 20-character credential, superseded path | 257 |
| `usedTokens` | 13-character credential, `deprecateItem` path | 215 |
| `count` | 16-character credential | 203 |
| `CANDIDATE_DEPTH` | 16-character credential, default budget, no parameter set | 442 |

`fusedScore` and the excerpt were measured as movement rather than run to
completion: over 20,000 random rank arrangements, chunk identity moved 9.1% of
the time, visible item order 3.4%, `fusedScore` 3.6%.

Every figure in the two tables above is transcribed from
[the threat model](../security/threat-model.md), where it was recorded as a
measurement taken against the code as it then stood. None of it was re-run for
this log, and the programs that produced it are not in the repository.

The full account, including the timing residual and the accepted T-17a, is in
[the threat model](../security/threat-model.md).

## What a round-one closure argument would and would not have bought

Round four's adversarial reviewer stated the class rather than the instance —
every response field is a function of the pointer, the index metadata, the
canonical state hash, or the caller's parameters — and ended the family in one
round. Asked for in round one, it would have forced the right question earlier.
It would not have prevented all three earlier rounds:

- **`count` and `fusedScore` fall inside it.** Both are published fields
  (`schemas/mcp/knowledge-search-response.schema.json`,
  `schemas/knowledge/retrieval-result.schema.json`), so a field-level argument
  reaches them.
- **`CANDIDATE_DEPTH`'s displacement and the excerpt's identity do not.** Neither
  is a field value. Fifty rows were read from each retriever before anything
  asked who could see them, so a withheld row took one of the fifty and the
  fiftieth visible row fell off the end. And `diversify` chose which chunk of a
  *visible* document to publish out of a ranking that still held withheld rows.
  "Every published field is a function of gate-cleared results" is true of both
  and closes neither — the excerpt did come from a document the caller may read;
  only which paragraph of it moved.

Rounds one through three passed green precisely because nobody had the sharper
abstraction: not "probe query versus control query" but **one query against two
corpora** — an index holding the withheld documents, and an index that never did,
must produce the same response.

## The rest of the milestone, which was not this defect

Rounds one and two carried substantial findings with nothing to do with T-17.
Reading these five rounds as one defect fixed five times is wrong, and the rule
in `CLAUDE.md` is written not to invite that reading.

Round one (`c9a65d3`, 2 CRITICAL and 8 HIGH, the three reviewers' sets barely
overlapping):

- Index-backed search never rechecked live status, so deprecated knowledge was
  returned by default. **CRITICAL**
- Dense search had no similarity floor, so every query returned something: a
  query for `payroll tax withholding` returned an approved authentication
  policy. **CRITICAL**
- FTS5 ANDed every term, so a natural-language query demanded every word in the
  same chunk and lexical search always came back empty.
- Query cost was unbounded: 500 terms over 2,000 chunks took 8.7 s, 2,000 terms
  over 60 s.
- `includeUnapproved` was silently disabled once an index existed; the first fix
  made every status including `rejected` permanently indexed.
- `system.capabilities` still declared the milestone's features unimplemented,
  and a passing test asserted the false value.
- The application layer depended on `sqlite3.Row`, typed as `Any`, which defeated
  both TID251 and strict mypy.
- `estimate_tokens` underestimated CJK by roughly five times, on a project whose
  primary language is Japanese.
- `index_for` interpolated an unvalidated string from `active-index.json` into a
  path (SEC-7).
- Plus: per-revision duplicate results, no lexical tie-break (FR-R7), `pack`
  treating unknown-size chunks as free, unbatched embedding, and no reap of a
  failed build's index file.

Round two (`ceb0496`, `7dc2793`):

- `includeUnapproved=True` skipped the status check itself — the guard was
  wrapped in `not include_unapproved`. **CRITICAL**
- The substring fallback had the same hole, reachable in the default
  configuration. **CRITICAL**
- A stale index returned superseded revisions wearing the *current* revision's
  `approved` label. Reproduced live: a revision with a shared key written inline
  was approved, indexed, replaced by a redacted one, and searched without a
  rebuild; the excerpt containing the secret came back `status: approved`.
- Query cost's real cause was JOIN order, not the term count: the `project_id`
  and `status` predicates made SQLite choose `chunks` as the outer loop and
  re-run the MATCH per row — 64 terms over 20,000 chunks took 235 s, against
  0.25 s with a `CROSS JOIN` putting FTS5 first.
- `_reclaim` deleted index files in use, and `sqlite3.connect` on a deleted path
  *creates* an empty database there, which disabled the "no index file, fall
  back" branch and delivered a raw `no such table` to the agent.

Later, not reliably attributable to a numbered round from the repository:

- `knowledge.get` had no status gate at all (`d4b3fb0`). See the next section.
- Project ids could collide across the registry (`8c3fa0d`).
- Search degraded silently instead of naming why it could not use the index
  (`f30881e`, `c0154a9`).
- `INDEX_SCHEMA_VERSION` was written and never read, so an index built under an
  older schema was used and answered short. Closed by
  `SqliteIndexStore.is_searchable`; see the compliance section of
  [ADR-0022](../adr/0022-index-lives-in-its-own-database.md).
- A query containing a NUL byte or a lone unpaired surrogate reached the agent as
  a driver-level error.

## Two families that were missing from the list

The `CLAUDE.md` family table gained two rows from this milestone that the
original enumeration did not have.

**Another tool reaching the same content.** `knowledge.get` performed no status
check and had no equivalent of `includeUnapproved`. No exploration was required:
`get` on an approved item returns relations, and a `rejects` edge hands over the
rejected item's id directly as `targetItemId`, so two calls reach a body that
`enums.py` declares no flag exists to include — "`REJECTED` is deliberately
absent and there is no flag that adds it". `d4b3fb0` is 44 added and 9 removed
lines in `mcp/tools.py`. The test that pins it states the family directly:

> Closing every path through `search` achieved nothing while `get` had no gate

(`packages/theurian-core/tests/integration/test_mcp_tools.py`, the docstring of
the bypass test.) The same rule had been written three times and left the same
gap three times, which is why `d4b3fb0` consolidated the decision into a single
`_may_surface` in `mcp/tools.py`. It has since moved twice, and now lives in
`theurian.domain.enums.may_surface`.

**State, lifecycle and concurrency artefacts.** Some of this surfaces as error
behaviour, but the family is wider: persistent files, the active pointer, and
races between calls.
[ADR-0022](../adr/0022-index-lives-in-its-own-database.md) had to withdraw its
point 6 — "the previous build is not deleted when a new one is published" —
because `SqliteIndexStore` holds no connection between calls, so "a search
already reading it" described no actual reader, and every gap between those
connections was a window in which the file could vanish anyway. Two concurrent
builds also deleted each other's files. A search racing a rebuild still falls
back to the substring scan; the blue/green work that fixes it is Milestone 6.

## What the round shape cost

| | Source |
| :-- | :-- |
| Round one, all three reviewers at full scope, returned **2 CRITICAL and 8 HIGH** whose sets barely overlapped | `c9a65d3`'s message — repository-verifiable |
| Among them, two extraction oracles, a schema that rejected the product's own output, and three places where deleting the code left the suite green | orchestrator session record; the commit enumerates the findings but not this grouping |
| Round two returned **thirteen LOW**, nearly all deferred | orchestrator session record, not in the repository |
| **Three times** a reviewer's stated mechanism was wrong while the finding was real | orchestrator session record, not in the repository |

The thirteen is the number behind the cap of five LOW per reviewer in
[`CLAUDE.md`](../../CLAUDE.md): thirteen deferred LOWs is a reviewer spending its
budget where the orchestrator will not spend its own. The three wrong mechanisms
are why reproduction is not parallelised with assignment — a brief built on an
unverified mechanism sends the fix at the wrong cause, and the finding survives
it.

## `theurian init` against this checkout, three times

`theurian init` writes `.theurian/` and appends to `.gitignore` in the *current
working directory*. A verification script whose `cd` runs in the wrong order, or
in a subshell that does not inherit it, initialises Theurian into Theurian's own
checkout. It happened three times in this milestone, and each time to an agent
that had already been given the warning — which is why the rule in
[`CLAUDE.md`](../../CLAUDE.md) is now "never depend on `cd` for isolation" rather
than "be careful". Recovery is
`git checkout -- .gitignore && rm -rf .theurian`, and the damage is invisible
unless someone runs `git status --short` afterwards, which is the third rule.

## The orchestrator's own errors

Implementation got three reviewers. The orchestrator's briefs, findings and
decisions got none, and four of them were wrong in ways nothing downstream
caught.

**One was a property, not a behaviour.** The acceptance of the BM25 residual
rested on a premise the orchestrator approved, wrote into the threat model in its
own words, and carried for two rounds before a reviewer measured it and found it
false. No scratch script had tested it and none would have by accident: it was a
claim about a third-party implementation's scoring internals. Reproduction
settles a behaviour; it does not settle a property.

**And the replacement premise was false in the same way.** Round four's
correction left a narrower bound of its own — `avgdl` and `N` are harmless
*because* they are query-independent — which round five measured and broke:
BM25's length norm divides each row's own length by the collection average, so it
is not a common factor across rows and an order does not survive it. 1,218
configurations reorder two *visible* rows with the withheld document sharing no
term with the query. Twice, then, a statistic was cleared by an argument about
what an attacker could **steer**, when what broke was the **equality**, which
does not care whether anyone can steer it. The residual was re-accepted at HIGH
on the corrected text rather than carried on the old; T-17a records both
corrections and the terms of the re-acceptance. The generalisable part is that a
bound stated in the same sentence as its own justification tends to be checked as
one claim, and it is two.

**Three were claims about what this repository contains**, all false for the same
reason — the file that answered them had not been read:

| The brief was going to assert | What the source says |
| :-- | :-- |
| `chunking.MIN_CHARS` is unpinned | `tests/unit/test_chunking.py` documents at length which two tests fail on a raised `MIN_CHARS`, and why it does not import the constant |
| the `IndexStore` port does not state its exhaustion obligation | `domain/ports/index_store.py` states it, including the case where satisfying its letter would still be a defect |
| `search_dense` passes through `_visible_ranking` | it does not; `_dense` calls `visible.cleared(ranked)[:CANDIDATE_DEPTH]`, deliberately, because dense scores the whole corpus whatever depth it is asked for |

Re-reading the file yourself reproduces the error rather than correcting it: the
failure is not knowing where to look. An independent source read is the check —
`codex:codex-rescue` is the one available here.

Cost was not the objection. Two such calls came to roughly 72k tokens against
this milestone's 3.47M of subagent output — a figure from the orchestrator's own
session record, not recoverable from the repository. Latency is the real
constraint: a check before dispatch serialises assignments that otherwise launch
together.

## The round count is a smell, not a target

These five rounds do not mean five defects, and three rounds is not a rule this
milestone supports. What it does support:

- A fourth round finding a *new family* means the closure argument was
  incomplete. Rebuild it rather than fixing what the round found.
- A fifth round finding another instance of a class an earlier round closed means
  that closure was never real — the class had been fixed as instances.

None of round one's ten CRITICAL and HIGH findings was findable by reading, which
is the reason a slow round here is not a failed one.
