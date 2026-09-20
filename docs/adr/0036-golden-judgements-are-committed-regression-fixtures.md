# ADR-0036: Golden judgements are committed regression fixtures, never a ranking input

- Status: accepted
- Date: 2026-09-20
- Deciders: Theurian maintainers
- Requirements: FR-R1, FR-R2, FR-R4, FR-R7
- Resolves [`docs/roadmap.md`](../roadmap.md) §9 ADR candidate 5, which its
  Phase A Risks row asks for
  ([#276](https://github.com/theurian/theurian/issues/276))
- **Confirms** [ADR-0021](0021-rank-fusion-over-score-normalisation.md) rather
  than amending it: its rejection of learning to rank is re-taken below on its
  own grounds

**Every repository fact below was measured on 2026-09-20 against `1aa61e8a`,
the branch commit of [PR #776](https://github.com/theurian/theurian/pull/776).**
That commit is not reachable from `origin/main`, so it carries the pull-request
qualifier rather than being cited as a plain anchor — a branch sha stops
resolving the moment the branch squashes.

## Context

Phase A builds the first measuring stick this project has ever had for
retrieval. The roadmap's Architecture row says what the stick is made of: "A
frozen fixture corpus … with committed golden queries and judgements. Fully
offline and deterministic."

The roadmap also names the problem with that, in its own Phase A Risks row:
ADR-0021 rejects learning to rank, and the sentence it rejects it with is

> Needs labelled relevance judgements this project has no way to collect, and
> would make ranking un-reproducible across installs.

Read as *labelled relevance judgements are rejected*, Phase A contradicts an
accepted ADR and a reader has to pick one. That reading is wrong, and the reason
is inside the sentence: both of its clauses are about properties a **collection
mechanism** would have, not about labels as such.

- *no way to collect.* Collecting relevance labels means a per-install feedback
  loop — click-through, dwell, a rating widget — over a corpus that differs per
  install. Theurian is a local-first daemon serving one operator's repository
  offline (ADR-0002, ADR-0009). There is no population to aggregate, and one
  install's labels would say nothing about another's corpus.
- *un-reproducible across installs.* The defect that clause names is that the
  ranking function would become a function of locally collected data. Two
  installs at the same commit would then order the same query differently, and
  FR-R7's pinned snapshot would reproduce nothing.

A judgement file committed to the repository has neither property. Nothing is
collected per install, and the bytes are identical in every checkout of a given
commit. So this is not a compromise struck between two documents: **committing
the judgements is the mechanism that supplies the exact property ADR-0021's
rejection named as absent.** What ADR-0021 rejects — labels reaching the ranking
function — is still rejected here, by decision 3.

## Decision

### 1. The golden judgements are committed, and committed means fixed

The frozen fixture corpus, `queries.yaml` and `judgements.yaml` are Git-tracked
files in this repository. Nothing about them is derived at install time,
discovered at run time, or accumulated from use. Every install at a given commit
holds identical bytes.

That is the whole reconciliation with ADR-0021, and it is worth stating in the
form that makes it checkable rather than as a reassurance: the property ADR-0021
requires of ranking is that two installs at one commit produce one order. A
judgement corpus that is a committed artifact cannot move that order *per
install*, because it does not vary per install — and, by decision 2, it does not
reach the order at all.

### 2. The retrieval path never reads the judgements

The harness is a development tool that calls the product from outside. No module
under `packages/theurian-core/src/` reads, imports, or names the corpus, the
queries or the judgements, and no runtime configuration key points at them.

This is a population claim, so it is recorded as a measurement. The durable key,
which a later reader re-runs:

```console
$ git grep -n -E "tools/eval|fixtures/eval" -- packages/theurian-core/src/
$
```

Empty at `1aa61e8a`. It was obtained through the strictly broader key
`git grep -n -E "tools/|fixtures/" -- packages/theurian-core/src/`, which
returns **11 lines in 5 files**, every one of them an MCP wire-method name
(`tools/call`, `tools/list`) or a comment naming the `tools/` directory, and
none of them a harness path — so the narrow key above is empty, and the
neighbourhood it sits in is stated rather than left to be trusted. The
ratchet that makes this stay true is a structural test owed to slice S2 and
named under *Still owed*; until it lands, this is a dated measurement and not an
enforced invariant.

### 3. Judgements serve regression detection and design decisions, and nothing else

Two uses, both of them reading:

1. **Regression detection.** A retrieval or ranking change is run against the
   committed corpus and the committed judgements, and the resulting numbers are
   compared with the previous ones. That is the "what did this change improve,
   and what did it break?" the roadmap's Phase A User-value row asks for.
2. **Design decisions.** Whether RAPTOR goes default-on, whether the CJK defects
   are worth fixing — the mixed-length one the roadmap names and the unspaced
   query recorded as [#284](https://github.com/theurian/theurian/issues/284) —
   and whether a real embedding adapter is adopted
   ([#280](https://github.com/theurian/theurian/issues/280), roadmap §9
   candidate 9 and Phase F ③) are each decided against the baseline instead of
   against an intuition.

They are **never an input to ranking**: not as training data, not as weights,
not as a tie-break, not as a boost list, and not as a seed for any of those.
Learning to rank remains a permanent non-goal, and ADR-0021 is confirmed rather
than amended — a future proposal to adopt it argues with ADR-0021's Alternatives
table, exactly as it would have before Phase A existed.

### 4. No target value exists anywhere in the contract or the harness

The roadmap's *What the harness will measure* row already states this, and this
ADR makes it a rule rather than an expectation: "These are the quantities the
harness will produce. They are not current properties and no target value is set
here — the first run defines the baseline, and the baseline is recorded as a
dated measurement pinned to a commit SHA."

So:

- No minimum Recall@k, no MRR floor, no maximum latency appears in
  `manifest.yaml`, `queries.yaml`, `judgements.yaml` or the harness. The three
  contract schemas are closed (`additionalProperties: false`), so a threshold
  key cannot be authored into a corpus without a schema change and the review
  that goes with it.
- **The harness produces; it does not assert.** It emits a report. Whether a
  number is acceptable is a judgement someone records, not a constant the tool
  carries.
- Every published figure is a dated measurement pinned to a commit sha, per the
  roadmap's §6 principle 4 — *a claim about a measured property ships with the
  measurement or not at all.*
- The CI comparison against the committed baseline is **advisory**. Whether it
  becomes a blocking gate is a separate decision this ADR does not take; the
  Phase A Exit-criteria row defers it to after
  [#67](https://github.com/theurian/theurian/issues/67), which has since closed,
  so the decision is available to take and simply is not taken here.

A target set before the first measurement is a guess wearing the authority of a
committed constant, and the first person to miss it would tune the corpus rather
than the ranking.

### 5. Determinism is claimed at the scope it is measured, and at no wider scope

`report.json` is byte-identical across consecutive runs over the same corpus and
the same queries — the property the roadmap's Phase A Tests row asks for — **at
the scope the slice-S2 determinism test measures: one machine, one interpreter,
one SQLite build, consecutive runs.**

**Cross-install identity of BM25-derived orderings is unmeasured and is
deliberately not asserted.** FTS5's `bm25` is computed by the SQLite build in
process, and this project has never measured whether two different builds order
a given query identically. Claiming that portability would be the shape of
defect this repository grades HIGH: a published claim with no measurement
behind it.

That limitation does not weaken decision 1, and the reason is worth spelling
out because it is the reconciliation's own premise: **the reconciliation in
decision 1 rests on the judgements being fixed, not on report portability.**
ADR-0021's clause is about a ranking function that varies with locally collected
data; a committed judgement file cannot vary that way whatever a second machine's
SQLite does to the ordering it is compared against. A second environment running
the harness *extends* the measurement — it would tell us something this ADR does
not know — and nothing here predicts what it would find.

### 6. The corpus interface: two planes, native migrations, three normative schemas

- **Native migration documents.** The fixture corpus is authored as ordinary
  YAML knowledge migrations (ADR-0005) and built by applying them, so the
  harness measures the shipped write, projection and index path rather than a
  hand-assembled database that could drift from it.
- **Two planes.** Each migration declares `plane: visible` or `plane: withheld`.
  The `full` index is built from every migration; the `clean` index is built
  from the `visible` ones only and never held the others.
- **Withheld rows are synthetic only.** No real secret, credential or private
  document goes into a fixture, per the roadmap's Phase A Security row. A
  withheld fixture row is invented text that exists to be *not* returned.
- **The three schemas under `tools/eval/schemas/` are the normative contract**
  for `manifest.yaml`, `queries.yaml` and `judgements.yaml`. Each states the
  shape of one file. Cross-file rules — that every `queryId` names a declared
  query, that every judged `itemId` exists in the built corpus, that the
  manifest's census agrees with a real build — belong to the slice-S2 loader,
  which is where a rule spanning two documents can be checked at all.

**The disclosure-equality form is fixed here, not left to the implementer.**
Equality queries run against both builds, and equality quantifies over the
**whole ordered response**: the result list in order, `count`, `usedTokens`,
`droppedForBudget`, each hit's `fusedScore` and `foundBy`, and which excerpt of
a document was chosen. The only fields permitted to differ are exactly

    {retrieval.indexBuildId, retrieval.snapshotId}

— an index build identifier and a canonical state hash, both of which name
*which artifact answered* and neither of which can be a function of a query. The
exception is asserted as **set equality** by a named slice-S2 test, never as a
membership check over a list of allowed names. The difference is the whole
point: a membership check lets a newly identity-varying field join the exception
silently, while a set equality reddens and forces someone to decide. That is
T-17's lesson in evaluation form — the defect there was not a field whose value
was wrong but a quantity nobody had enumerated, and enumerating field by field
is what let it survive three review rounds
([threat model](../security/threat-model.md), T-17).

### 7. The report splits in two: a deterministic pin and a dated annex

| File | Holds | In the determinism pin |
| :-- | :-- | :-- |
| `report.json` | the metrics: Recall@k, MRR, evidence precision, abstention accuracy, superseded-knowledge error rate. Sorted keys, fixed rounding, **no commit sha embedded** | yes |
| `timings.json` | the dated annex: the commit sha, the environment, per-query latency, index build cost | no |

The split exists so that a re-run whose metrics did not move produces an **empty
diff**. A report carrying its own sha and its own wall-clock timings differs on
every run, which makes a review "did anything change?" unanswerable by reading
and therefore unanswered. Cost figures are still recorded — the roadmap counts
latency and index cost among the quantities the harness produces — they are
simply recorded where a diff is expected to be noisy rather than where it is
expected to be empty.

## Consequences

### Positive

- **A ranking change gets a number.** The roadmap's §1 *Absent* list opens with
  "Any evaluation baseline for retrieval quality", and the RAPTOR, CJK and
  embedding decisions have each been waiting on one.
- **The collision is closed by a recorded argument rather than by precedence.**
  A later reader meeting Phase A and ADR-0021 together finds the reasoning here
  instead of re-deriving it, or picking the document they read second.
- **The disclosure-equality regression comes free with the corpus.** The two
  planes make every equality query a standing test of the property four of this
  project's published advisories were about, run on the same corpus that
  measures quality.
- **A decision that is not taken is visible as untaken.** The blocking-gate
  question and the cross-install determinism question are both recorded here as
  open, with what would settle each.

### Negative

- **A committed corpus ages.** Judgements are a human's reading of what a query
  ought to return, and they will drift from what the corpus means as the corpus
  is edited. There is no mechanism that detects a stale judgement; what exists
  is that changing one is a reviewed diff.
- **The fixture is small and synthetic, so the numbers are relative, not
  absolute.** Recall@5 on a frozen ADR-shaped corpus is a regression signal.
  It is not a claim about how Theurian performs on anyone's real knowledge base,
  and no published figure may be read that way.
- **Judgements are one author's opinion.** With one maintainer there is no
  inter-annotator agreement to report, and a systematically wrong judgement
  becomes a target the ranking is tuned toward. The mitigation is only that the
  judgements are in the diff where a reviewer can disagree with them.
- **Two files must move together.** Editing the corpus without editing the
  judgements silently changes what every metric means. The slice-S2 loader's
  cross-file checks are what make that a failure rather than a drift.

### Neutral

- **The harness is a development tool and ships to nobody.** `tools/eval/` is
  outside the distributed package, adds no runtime dependency, and no wire
  surface changes — the roadmap's Phase A Schema and MCP/API rows both read
  "None".
- **Nothing here constrains a future retrieval design**, only how a change to
  one is argued for. A reranker or a real embedding adapter is as buildable
  after this ADR as before; what changes is that its adoption comes with a
  measurement.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| Learning to rank, trained on the committed judgements | ADR-0021's grounds, re-taken rather than re-derived: the ranking function would stop being a total, corpus-independent order and become a function of a fitted model. Nothing about *committing* the labels repairs that — it repairs only the collection half of the objection. RRF's premise is that scores are incomparable and only ranks fuse; a learned scorer contradicts it. |
| Relevance judgements collected per install from usage | The thing ADR-0021 rejects, and it is rejected here again by construction rather than by preference: there is no population to aggregate in a single-operator offline daemon, and a ranking fitted to one install's history would make two installs at one commit disagree — the exact failure FR-R7's pinned snapshot exists to prevent. |
| Set target values now — a Recall@5 floor, an MRR minimum, a latency budget | A target chosen before the first measurement is a guess with a committed constant's authority, and the first run that missed it would be answered by editing the corpus. The roadmap already requires the first run to *define* the baseline; a threshold authored ahead of it inverts that. Thresholds become takeable once there are dated measurements to choose them from, and then they are their own decision. |
| Put the corpus contract schemas under the repository-root `schemas/` | That directory is the **published wire contract** — the shapes `knowledge.search` and its siblings emit, versioned under a compatibility policy, with `protocolVersion` governing changes to them. A fixture-corpus manifest is development-tool contract: it is consumed by `tools/eval/`, never by a client, and it should move when the harness needs it to rather than under a wire-compatibility policy. Keeping it beside the tool that reads it also keeps decision 2's key one directory wide. |
| Publish one report file carrying metrics, sha, environment and timings | Every run then differs, so "did anything change?" stops being answerable from a diff. Decision 7 keeps both and separates them by whether their content is expected to be stable. |

## Compliance

Landed in Phase A slice S1 — `tests/unit/tools/test_eval_contract_schemas.py`,
against the three schemas under `tools/eval/schemas/`. The reach of each is
stated as its body holds it, not as its name suggests:

- **The three transcription pins (group D)** hold decision 6's "the contract
  transcribes the wire rather than approximating it":
  - `test_judged_item_id_pattern_and_max_length_match_the_retrieval_result_schema`
    asserts that the judgements schema's `judgedItem.itemId` `pattern` and
    `maxLength` are equal to the published
    `schemas/knowledge/retrieval-result.schema.json` `itemId`'s — so a judgement
    file cannot validate against an `itemId` shape the wire no longer emits.
  - `test_query_max_length_matches_the_knowledge_search_input_schema` asserts
    that the queries schema's `queryEntry.query.maxLength` equals the published
    `schemas/mcp/knowledge-search-input.schema.json` `query.maxLength`. It
    compares the two **schemas**, not the Python constant behind them.
  - `test_k_values_maximum_matches_max_results` is the one that reads a live
    constant: it asserts the manifest's `kValues.items.maximum` equals
    `theurian.mcp.tools.MAX_RESULTS`, so a `k` beyond what a real response can
    reach cannot be declared.
- **The two planes** are held by
  `test_manifest_rejects_a_migration_plane_outside_the_declared_enum`, which
  sets one migration entry's `plane` to `"hidden"` and asserts the manifest fails
  validation.
- **The deferred query classes** are held by
  `test_queries_gates_a_deferred_class_on_enabled_false`, which asserts that a
  `historical` query validates with `enabled: false`, and fails both with
  `enabled: true` and with `enabled` absent — so a query cannot run before the
  phase that answers it ships.
- **Abstention** is held by
  `test_judgements_gates_abstention_on_an_empty_relevant_list`: with
  `expectAbstention: true`, a non-empty `relevant` list fails, while an empty
  one and an absent one validate.
- **A judged `itemId` is a domain slug, never a ULID** —
  `test_judgements_rejects_a_ulid_shaped_item_id`, which plants a syntactically
  valid ULID and asserts the document fails.
- **Closedness**, which is what decision 4 leans on for "no target key can be
  authored into a contract file", is declared by `additionalProperties: false`
  throughout all three schemas and **driven at three of those sites**:
  `test_manifest_rejects_an_undeclared_property_inside_census_full`,
  `test_queries_rejects_an_undeclared_property_on_a_query_entry` and
  `test_judgements_rejects_an_undeclared_property_inside_a_relevant_item`. The
  remaining closed objects — the manifest root, a migration entry, a judgement
  entry, an evidence reference — are declared closed and not probed; said here
  so the three are not read as covering the whole contract.
- Filename shape, the `kValues` ceiling, an empty `corpora` list, the query
  length bound and duplicate evidence entries each have their own rejection
  test in the same file, and the three `A`-group parametrized cases hold that
  every schema is itself valid JSON Schema.

Measured now, and reproducible from this ADR (2026-09-20, `1aa61e8a` of
PR #776):

- Decision 2's key returns nothing, with the broader-key derivation recorded
  beside it in the decision.
- Nothing in the repository sets a target value for a retrieval metric: the
  three contract schemas declare no threshold property and are closed, and no
  harness exists yet to carry one.

Still owed, with the phase that would satisfy it:

- **Phase A slice S2 — a structural pin for decision 2.** A test that
  recomputes the decision-2 key against the tree and asserts the result is
  empty, with a positive control proving the scan can see a planted reference —
  an empty answer from a scan that cannot find anything states nothing. Until
  it lands, decision 2 is a dated measurement rather than an enforced
  invariant, and a runtime module could grow a harness import without anything
  going RED.
- **Phase A slice S2 — the set-equality pin for decision 6.** A test asserting
  that the set of fields differing between a `full`-corpus response and a
  `clean`-corpus response is **equal** to
  `{retrieval.indexBuildId, retrieval.snapshotId}` — set equality, so a newly
  identity-varying field reddens instead of joining the exception — over the
  whole ordered response, paired with a control proving the battery's queries
  actually reach the withheld plane.
- **Phase A slice S2 — the determinism pin for decisions 5 and 7.** Two
  consecutive runs over one corpus produce a byte-identical `report.json`, with
  the scope the test measures stated in its own docstring; and its sibling, that
  a run whose `timings.json` differs leaves `report.json` unchanged, which is
  what makes decision 7's split a property rather than a filing convention.
- **Phase A slice S4 — the committed baseline and the advisory CI comparison.**
  The Phase A Exit-criteria row's own words: "A baseline report is committed and
  CI reports regressions against it." Until that lands, decision 4's *advisory*
  describes an intention and not a workflow.
