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

**Every repository fact below was measured on 2026-09-20, on the branch of
[PR #776](https://github.com/theurian/theurian/pull/776) — named by its pull
request because no sha on it is reachable from `origin/main`.** A branch sha
stops resolving the moment the branch squashes, so an in-flight measurement is
anchored to the pull request rather than to a commit name; where a particular
branch state matters below, the commit is named by its subject.

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

Empty on the branch of PR #776, at the schema-pin commit
`test(eval): pin the corpus contract schemas and their wire-derived bounds` —
named by its subject rather than by its sha, which that squash will strip. It
was obtained through the strictly broader key
`git grep -n -E "tools/|fixtures/" -- packages/theurian-core/src/`, which
returns **11 lines in 5 files**, every one of them an MCP wire-method name
(`tools/call`, `tools/list`) or a comment naming the `tools/` directory, and
none of them a harness path — so the narrow key above is empty, and the
neighbourhood it sits in is stated rather than left to be trusted. The
ratchet that makes this stay true landed in slice S2 (`9cd9ee34`): a committed
test recomputes the scan against the tree, so decision 2 is an enforced
invariant and not only a dated measurement. *Compliance* states its reach.

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
- **Two planes, declared in the manifest and never in a migration.** The
  manifest names each migration file with a `plane` of `visible` or `withheld`
  (`$defs.migrationEntry` in `tools/eval/schemas/manifest.schema.json`). The
  migration documents stay ordinary ADR-0005 migrations carrying no
  evaluation-specific key of any kind: `schemas/migrations/migration.schema.json`
  is `additionalProperties: false` at its root, so a `plane` written into a
  migration is refused by the published schema. The `full` index is built from
  every migration; the `clean` index is built from the `visible` ones only and
  never held the others.
- **Withheld rows are synthetic only.** No real secret, credential or private
  document goes into a fixture, per the roadmap's Phase A Security row. A
  withheld fixture row is invented text that exists to be *not* returned.

  **The rule is authorial, and nothing at the schema layer enforces it.** The
  three contract schemas constrain shape, not content, and none of them can tell
  an invented token from a real one. Two controls do reach the corpus path, and
  neither was built for it:

  - **The full-history secret scan.** `.gitleaks.toml` extends the default
    ruleset (`useDefault = true`; the file's own reason is that "a secret scan
    with no rules is a green job that has stopped checking"). Its two allowlists
    are each narrowed by `targetRules`, `paths` and an anchored value regex
    combined with `condition = "AND"`, and both `paths` sit under
    `^packages/theurian-core/tests/` — so a corpus living under the
    repository-root `tests/` or `tools/` is inside coverage and allowlisted by
    nothing. **What it does not catch is measured as a count, and only partly
    described**: eight literal forms, four reported and four not, of which that
    file **names exactly one** — `NAME: Final = "<token>"`, unreported "because
    `generic-api-key` looks for its keyword within a few characters of the
    separator and a type annotation pushes it out of range". That one gap shape
    is one a YAML corpus document does not have. The other three unreported
    forms are recorded as a number and never described, so nothing here may
    claim a corpus file avoids them.
  - **Index-time secret scanning**, which the corpus gets for free from being
    built through the real path (SEC-11,
    [#329](https://github.com/theurian/theurian/issues/329)). `IndexRequest`
    requires a `SecretScanPolicy` with **no default** — its own comment records
    why, that "a defaulted security control is one a composition root can select
    by forgetting" — and the scan is `theurian.application.index_secret_scan`
    over `theurian.security.content_secrets.scan_text`. It reports rather than
    refusing, because by then the content is already indexed.

  **Discharged at slice S3** ([PR #778](https://github.com/theurian/theurian/pull/778),
  merged as `d11f3552`), whose body records both, measured rather than assumed,
  and records that they do not reach the same bytes: the full-history scan
  covers all 36 bodies — `gitleaks detect` over the commit range and over
  `tests/fixtures` alone, no leaks found, nothing allowlisted — while the
  index-time scan sees only what a build writes, 24 bodies in `clean` and 26 in
  `full`, never the five withheld bodies no build indexes. Reading the two as
  covering one surface is the error that record exists to prevent.
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
*which artifact answered* and neither of which can be a function of a query.

**The exception is asserted as set equality by a named slice-S2 test, never as a
subset or an exclusion.** The product already records why, for its own pair, and
the reason is not the one about a newly varying field — a subset check reddens on
that too. It is the opposite direction:

> An exact set and not a subset: a subset check also passes a response that has
> stopped publishing `appliedMigrations`, and one whose `stateHash` has gone
> insensitive to canonical state, both of which are contract changes that should
> be decided rather than absorbed.
>
> — [threat model](../security/threat-model.md), on
> `test_a_withheld_item_moves_exactly_the_two_fields_the_status_schema_exempts`

Read against the retrieval pair: a subset check passes a harness that has stopped
publishing `retrieval.indexBuildId` at all, and one whose `retrieval.snapshotId`
has gone insensitive to canonical state — leaving an equality battery that
compares two constants and reports agreement.

**And the comparison quantifies over the whole response rather than over a field
list, because a list is what T-17 defeated.** Its root cause was the gate's
position, not an unenumerated field:

> Each round reasoned about the face in front of it — one *quantity*, to be
> moved to the far side of the canonical gate — while the gate itself stayed
> after the ranking, so the round after it found a sibling.
>
> — [threat model](../security/threat-model.md), T-17

Reasoning face by face is what each round did, and it kept producing siblings
while the gate stayed where it was. The record adds that "The last two are not
numbers at all" — a withheld row consuming one of the fifty candidate slots, and
`diversify` choosing which paragraph of a *visible* document to publish — so a
comparison assembled by listing the numbers someone could think of would have
reached neither.

**Which withheld members that comparison actually tests is not settled here.**
*Amendment 1* below states the derivation rule that answers it, and the three
riders that bound what the battery may be read as covering.

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
- **A judged `itemId` is a domain slug, and a canonically-spelled ULID is
  rejected** — `test_judgements_rejects_a_ulid_shaped_item_id` plants
  `01ARZ3NDEKTSV4RRFFQ69G5FAV` and asserts the document fails validation.
  **The rejection is on case, and the reach is stated rather than rounded up to
  "never a ULID".** The pattern admits `[a-z0-9]` only, so the same ULID
  lowercased is 26 admitted characters — and it must be, because the pattern is
  byte-equal to the wire's own `itemId` (the transcription pin above), which
  admits it too. That is the pin's reach, not a defect in it. Telling a slug
  from a lowercased ULID, if it is ever worth telling, belongs to the S2 loader,
  which can check an id against the built corpus.
- **Closedness**, which is what decision 4 leans on for "no target key can be
  authored into a contract file", is declared by `additionalProperties: false`
  on **ten** objects across the three schemas —
  `git grep -c '"additionalProperties": false' -- tools/` reports 4 in the
  judgements schema, 4 in the manifest and 2 in the queries schema — and it is
  **driven at three of the ten**: `corpusCensus`, by
  `test_manifest_rejects_an_undeclared_property_inside_census_full`;
  `queryEntry`, by
  `test_queries_rejects_an_undeclared_property_on_a_query_entry`; and
  `judgedItem`, by
  `test_judgements_rejects_an_undeclared_property_inside_a_relevant_item`. The
  **seven undriven** are the manifest root, its `census` wrapper and its
  `migrationEntry`; the queries root; and the judgements root, its
  `judgementEntry` and its `evidenceRef`. Three of ten is stated rather than
  rounded, and an earlier draft of this bullet undercounted the remainder as
  four: misreporting what is verified is the defect the roadmap's appendix row
  10 exists to catch, and a sentence written to prevent that reading is the
  worst place to commit it.
- Filename shape, the `kValues` ceiling, an empty `corpora` list, the query
  length bound and duplicate evidence entries each have their own rejection
  test in the same file, and the three `A`-group parametrized cases hold that
  every schema is itself valid JSON Schema.

Landed in Phase A slice S2 (`9cd9ee34`), which also ships the smoke corpus
(`tests/fixtures/eval-smoke/`) the integration pins below build and query. The
reach of each is stated as its body holds it — the fixtures it builds and the
assertions it makes — not as its name or its docstring reads:

- **Decision 2's structural pin** —
  `tests/unit/tools/test_harness_pins.py::test_no_module_under_core_src_references_the_harness_or_its_fixtures`
  recomputes the decision-2 key against the tree. It derives its needle
  population from where a corpus manifest actually sits — an `rglob` for
  `manifest.yaml` under `tests/fixtures/`, so a corpus nested one directory
  deeper is still in scope — plus the harness directory taken from the loader
  module's own `__file__`; expands each root into its repo-relative path, its
  last two segments, and that short form dotted; asserts the population is
  non-empty; and asserts that no line of any `.py` file under
  `packages/theurian-core/src/` contains any of them. A root already two
  segments deep yields two spellings rather than three, which is why the helper
  returns a set.
  **Two positive controls, each against a planted file in `tmp_path` rather
  than against the tree**:
  `test_the_reference_scan_reports_a_planted_full_path_reference` plants a line
  naming a needle with at least two path separators, and
  `test_the_reference_scan_also_reports_a_planted_short_form_reference` plants
  one naming a `fixtures/`-prefixed short form; each asserts the scan returns
  exactly that one file. What the controls hold is that the scan is not blind.
  That the tree itself is clean is the first test's own assertion.
- **Decision 6's set-equality pin, its two reach controls and its companion** —
  `tests/integration/tools/test_harness_pins.py`.
  `test_the_equality_query_differs_from_its_clean_counterpart_only_in_build_identity`
  searches each of the two builds at default flags and asserts that
  `differing_paths` over the whole responses is **equal** to
  `{retrieval.indexBuildId, retrieval.snapshotId}` — equality, not a subset —
  for three parametrized queries: the gate-tested row's own synthetic key, a
  phrase matching one document present identically in both planes, and a query
  written to compete with the draft row for shared vocabulary.
  `test_the_default_flag_gate_hides_the_draft_row_the_include_unapproved_flag_reveals`
  asserts that the first of those returns nothing from `full` at default flags,
  returns the draft item under `includeUnapproved`, and returns nothing from
  `clean` under that flag either — so the equality is earned by the gate rather
  than by the row's absence.
  `test_the_competing_vocabulary_querys_candidacy_is_an_enforced_premise`
  asserts the third reaches at least two visible ids, the same ids in the same
  order from both builds, equal to a pinned set of three, and that the draft row
  comes back under the flag from `full` and never from `clean`.
  The companion,
  `test_both_build_identity_fields_are_constant_and_nonempty_within_one_build`,
  runs three queries against the `full` build alone and asserts the two excepted
  fields are equal across all three and that neither is empty — the harness's
  version of the product's
  `test_the_build_identity_a_search_reports_does_not_vary_with_the_query`.
- **The loader's within-document obligations** — five named refusals in
  `tools/eval/corpus.py`, each driven by a test in
  `tests/unit/tools/test_harness_pins.py` that builds a minimal corpus
  violating exactly one rule and asserts `CorpusError.rule` names it:
  `duplicate-query-id`
  (`test_duplicate_query_id_rule_refuses_two_queries_sharing_an_id`),
  `duplicate-judgement-query-id`
  (`test_duplicate_judgement_query_id_rule_refuses_two_judgements_sharing_a_query_id`),
  `relevant-forbidden-overlap`
  (`test_relevant_forbidden_overlap_rule_refuses_the_same_item_id_in_both_lists`),
  `empty-judgement`
  (`test_empty_judgement_rule_refuses_a_judgement_that_judges_nothing`) and
  `evidence-subsumption`
  (`test_evidence_subsumption_rule_refuses_a_plain_and_narrowed_entry_for_one_source`).
  **The subsumption obligation is held by two mechanisms, and only one of them
  is the loader.** `_check_no_evidence_subsumption` intersects the plain
  `sourceUri` set with the narrowed one, so what that rule and its pin hold is
  the `(u, absent)`-beside-`(u, f)` half — the one that would let a single
  anchor satisfy both entries and score twice, inflating evidence precision.
  The exact-`(sourceUri, filePath)`-pair half is the schema's: `evidence`
  declares `uniqueItems`, and `evidenceRef` is closed at exactly
  `{sourceUri, filePath}`, so two entries sharing a pair are the same object and
  are refused at validation (slice S1, `111ab573`). That closed shape is what
  keeps the pair case inside `uniqueItems`' reach, since `uniqueItems` compares
  whole items and two entries agreeing on the field that matters while differing
  anywhere else would be distinct to it. The S1 test that drives it,
  `test_judgements_rejects_duplicate_evidence_entries`, plants two
  **bare-`sourceUri`** entries; the pair case follows from the closed shape
  rather than from a case any test plants.
- **The determinism pin for decisions 5 and 7** —
  `test_two_consecutive_harness_runs_over_the_smoke_corpus_produce_a_byte_identical_report`,
  in the same file: two runs into two output directories, both asserted to exit
  0, `report.json` asserted byte-identical between them, and `timings.json`
  asserted **not** identical. Decision 7's split is held as a property by that
  second assertion, in the same body rather than by a separate test. The
  docstring carries the scope decision 5 names — one machine, one interpreter,
  one SQLite build, consecutive runs — and nothing here reaches a second machine.

What holds this record against the tree is `tests/unit/tools/test_adr36_ratchet.py`,
landed with this discharge — the file this ADR had never named. Seven pins, three
of them guarding the citations above.
`test_every_test_name_cited_in_the_adrs_s2_compliance_block_collects` parses every
backticked test name out of this block and asserts each appears in `pytest
--collect-only` output over `tests/unit/tools`, `tests/integration/tools` and
`packages/theurian-core/tests`, so a renamed or deleted pin reddens the ADR's own
record rather than rotting in it.
`test_the_five_within_document_rule_names_the_adr_cites_are_live_corpuserror_tags`
reads the five rule tags out of the bullet above and asserts each is still a string
literal some `CorpusError` in `corpus.py` is raised with — containment in that
direction, so a *new* loader rule this ADR does not cite stays green.
`test_the_787_tripwire_published_per_query_metric_key_set_equals_todays_expected_set`
is a tripwire rather than a frozen contract: it reads the key set
`report._query_metrics` can publish from that function's own AST and compares it to
today's snapshot, so the moment #787's channel member lands it goes RED — and that
RED is the signal to move rider 1's "recorded channel" sentence from owed to
implemented.

Measured now, and reproducible from this ADR (2026-09-20, on the branch of
PR #776):

- Decision 2's key returns nothing, with the broader-key derivation recorded
  beside it in the decision.
- Nothing in the repository sets a target value for a retrieval metric, and
  since slice S2 that is held by machine check rather than by a dated reading.
  The three contract schemas declare no threshold property and are closed. In
  the harness,
  `test_no_threshold_floor_or_target_named_identifier_exists_in_the_harness`
  parses every `.py` file directly under `tools/eval/` and collects real
  identifiers from the AST — names, arguments, attributes, function and class
  names, never a string, comment or docstring — then asserts that none contains
  `threshold`, `floor` or `target` in any case. A comment promising no threshold
  therefore cannot satisfy it, which is the point: the harness's own modules
  carry exactly such a statement. **Its reach stops where its key does** — a
  quality gate authored under a `MIN_`/`MAX_` name sits outside it, deliberately,
  because those prefixes are shared with legitimate bounds and the alternative is
  an allowlist a future author can route around.
  `test_a_built_report_carries_no_pass_fail_or_threshold_named_key` covers the
  published half: it calls the real `build_report` over a synthetic corpus
  exercising every publishable branch (a two-corpus equality query and a
  census-tested forbidden item, alongside the base case) and asserts that no
  key, at any nesting depth of the result, matches `pass`, `fail` or
  `threshold`. A threshold neither plainly named nor ever published is what
  neither check can see.

Still owed, with the phase that would satisfy it:

- **Phase A slice S4 — the committed baseline and the advisory CI comparison.**
  The Phase A Exit-criteria row's own words: "A baseline report is committed and
  CI reports regressions against it." Until that lands, decision 4's *advisory*
  describes an intention and not a workflow.

## Amendment 1 — the gate-versus-census derivation rule, and what the equality battery does not test (2026-09-20, Phase A slice S3, PR #778)

> **This is an append-only amendment. The decisions above are unchanged**, and
> nothing in *Compliance* or *Still owed* moves here. What it adds is the rule
> decision 6 left implicit: decision 6 fixes the *form* of the
> disclosure-equality comparison without saying which withheld members that form
> tests, and a battery run against members no build ever indexed reports coverage
> it does not have.
>
> **The wording of the three riders below is the slice-S2 lane's round-one rider
> text**, recorded as a comment on
> [PR #778](https://github.com/theurian/theurian/pull/778) and integrated here
> rather than paraphrased.
>
> **This amendment states rules and measures nothing.** The quantities that
> exercise them live in the corpus manifest's `census` and in the runs recorded
> on [PR #778](https://github.com/theurian/theurian/pull/778) (the corpus) and
> [PR #780](https://github.com/theurian/theurian/pull/780) (the harness). No sha
> on either branch is reachable from `origin/main` as this is written, so both
> are named by pull request.

### The derivation rule

A withheld-plane member is **gate-tested** when its final folded state — the last
`upsertRevision.metadata.status`, overridden by a later `deprecateItem` →
`deprecated`, and the last `upsertRevision.metadata.sensitivity`, overridden by a
later `changeSensitivity` — carries a status the `--include-unapproved` flag
admits beyond the default surface (today `draft` and `proposed`) **and** a
sensitivity within the deployment's serving ceiling. Such a row enters the `full`
index and is withheld by the query-time gate, so the response-equality battery
decision 6 fixes is what tests it.

Every other withheld member is **census-tested**. It is excluded before the index
— `confidential` and `restricted` by the build ceiling
([ADR-0025](0025-sensitivity-is-enforced-before-0-1-0-stable.md)), `superseded`,
`rejected` and `deprecated` by a status no flag re-admits — so what tests it is
the manifest census: `full.items` counting it applied while `full.chunks` counts
it excluded.

**The split is derived from the shipped gates, and is never declared as a
manifest key.** `$defs.migrationEntry` in
`tools/eval/schemas/manifest.schema.json` is closed at `{file, plane}`, so a
coverage label cannot be authored into the manifest without a schema change —
and that is the direction this amendment wants. A declared label is a second
statement of the gates, and it can drift from them silently: a draft approved, a
sensitivity lowered, a status moving into or out of what the flag admits. A
derivation cannot drift, because it is recomputed from those gates on every run.

Both readers implement this one statement rather than each deriving its own, and
building the S3 corpus with the S2 loader is the divergence detector between
them:

- **S3, this pull request.**
  `tests/unit/tools/test_corpus_fixture_consistency.py` folds its two status sets
  out of `may_surface` (at `include_unapproved=` false and true) and its served
  sensitivity set out of `may_disclose` against
  `ServingProfile().visible_sensitivities`, rather than transcribing status
  lists. `test_the_withheld_plane_splits_into_gate_tested_and_census_tested_members`
  asserts, over the corpus as committed, that each class derived from a member's
  own folded state equals its pinned set of item ids and that no withheld member
  falls in neither. Its two twins restate one member's metadata and assert the
  rule fires: a withheld `draft` promoted to `approved`, and a `confidential`
  member lowered to `internal`, which lands in neither class.
- **S2, PR #780.** `tools/eval/corpus.py` classifies each withheld item by the
  same statement, from the same folded state, citing this rule by name. Its
  ceiling side comes from `ServingProfile`; its status side spells the admitted
  pair literally, and the S3 fold above is the half that is recomputed from
  `may_surface`.

### Three honesty riders

**1. The disclosure-equality battery is non-vacuous for the query-time-gate
mechanism only.** Draft and proposed rows enter the index because *both* builds
run `index build --include-unapproved` — one flavour on both sides keeps the
published `retrieval.indexesUnapproved` equal, preserving the
`{retrieval.indexBuildId, retrieval.snapshotId}` exception set — and default-flag
queries make the gate the thing under measurement: one query against an index
holding the withheld documents and an index that never did. Build-time-excluded
mechanisms (sensitivity above the ADR-0025 ceiling) and status-unsurfaceable ones
(superseded, rejected, deprecated) never enter either index, so equality is
trivially satisfied for them and tests nothing.

> **Amended 2026-09-20, after slices S2 (`9cd9ee34`) and S3 (`d11f3552`)
> landed.** The construction above stands: both builds run
> `index build --include-unapproved`, one flavour on both sides is what
> preserves the exception set, and default-flag queries are what make the
> query-time gate the thing under measurement. What the rider got wrong is the
> **scope of the equality claim** — it reads as asserting whole-response
> equality wherever the battery runs. Where the `full` index holds withheld rows
> `clean` never did, equality is not a property the gate can deliver, and the
> reason is already recorded: the gate decides what is returned, while the BM25
> collection statistics are computed over what is *indexed* — "true of
> everything the gate controls and false of what the statistics control"
> ([threat model](../security/threat-model.md), T-17a).
>
> - **Asserted where it is measured and pinned.** On the smoke corpus the
>   whole-response set equality is a committed assertion (*Compliance*, slice
>   S2). On a plain build it holds by construction rather than by the gate: no
>   withheld-plane row reaches either index there, because the loader refuses
>   the one combination that would let one through — approved and within the
>   serving ceiling — under its `withheld-item-disclosable` rule
>   (`test_withheld_item_disclosable_rule_refuses_an_approved_within_ceiling_withheld_item`,
>   whose two accepted-neighbour cases move one clause each and load).
> - **A recorded channel where a withheld row can move a collection
>   statistic.** The S3 corpus is authored with displacement carriers —
>   `q-secret-scan-hardening` is the one its own comment names — so its
>   `--include-unapproved` `full` index holds rows `clean` never did, and the
>   default-flag comparison between the two builds is reported with a count of
>   what differed rather than asserted equal. This is not a second finding: it
>   is T-17a's recorded residual. `index build` filters on `may_surface` and
>   writes no draft, while an `--include-unapproved` index "keeps the drafts and
>   proposals it legitimately holds" ([threat model](../security/threat-model.md),
>   T-17a) — an operator build-time flag, absent from the shipped default.
>
> The reason this stays inside the boundary, in the words
> [#787](https://github.com/theurian/theurian/issues/787)'s annotation work
> carries: *includeUnapproved is a request parameter, not an authorization
> grant; the Core authenticates one principal (#119); the protection is the
> absent authorization boundary, not the `_NO_DRAFTS_INDEXED` gate
> (asymmetric).* Each clause is checkable against the source rather than taken
> on trust: `mcp/search.py`'s own comment calls `includeUnapproved` "a request
> parameter a caller can stop passing", and `_NO_DRAFTS_INDEXED` fires in one
> direction only — drafts requested against an index built without them — so it
> is not what protects the other direction.
>
> **The figure is provisional, and its unit is unsettled.** A reviewer probe
> over the S3 corpus reported that comparison differing at **17 of 26**. No
> record this ADR can cite states which unit that counts: this corpus declares
> 26 enabled queries, while a single response compares many more than 26 paths.
> It is carried here as an unconfirmed observation and may not be quoted as a
> measured property. #787 owns the report-side channel annotation and the joint
> build that will state the figure with its unit; decision 6's *form* is
> unchanged by any of this — what moved is where that form is asserted as a
> property and where it is recorded as a channel.
>
> **Superseded at slice S4a: the figure is measured, the unit is settled, and
> the channel is implemented.** The paragraph above stays as the record of what
> was known on 2026-09-20 and is no longer the live reading. The unit is **per
> query**. On the committed S3 corpus, **18 of 26** enabled queries differ
> beyond the exception set at the harness `limit` of 10, and **21 of 26** at the
> equality limit of 50 — measured at `4ce0868f` with the shipped loader and
> recorded on
> [#787](https://github.com/theurian/theurian/issues/787#issuecomment-5804290254),
> which supersedes the provisional 17 of 26. The differences fall in T-17's own
> families: `fusedScore` most often, then `retrieval.usedTokens` with `count`,
> tail-slot displacement of visible rows, and which paragraph was excerpted.
> **The denominator is a population, not a rate.** `of` counts every equality
> query that ran against both corpora — all 26 of them in this corpus — and that
> includes queries whose responses held no result rows in the run being
> reported: at `4ce0868f`, three of the four abstention-expecting queries
> abstained correctly on the `full` build, the one miss being
> `q-withheld-credential-cache`, which #787 records as benign vocabulary overlap
> by visible rows rather than a leak. Five of the 26 did not differ at the
> equality limit and eight did not at limit 10. **Which queries made up those
> sets is in no record this ADR can cite**, and the per-query population is what
> slice S4b's committed baseline states. Until it does, reading 18 or 21 out of
> 26 as a rate understates the channel — a dated observation about that one run,
> not a property of the corpus.
>
> `report.json` now publishes `equality.channel` — `queriesDiffering` and `of`
> at both limits, beside a `reason` carrying #787's annotation phrasing verbatim
> — and `abstentionCause`, set only where the flag-on probe returns a hit the
> default-flag query did not, so a gate-earned abstention is annotated while an
> absence-earned one stays bare. Both landed in slice S4a
> ([PR #795](https://github.com/theurian/theurian/pull/795)), cited by pull
> request rather than by sha or by commit subject: no sha on that branch is
> reachable from `origin/main`, and a squash-merge replaces the branch with one
> new commit, so a subject citation rots exactly as a sha does — the pin
> commit's subject disappears entirely.
>
> **The flag-on calls are visible in the report, not only in their effect.**
> `report.json` carries a sibling `abstentionProbe` member —
> `{includeUnapproved: true, limits}` — recording that probe calls ran and at
> which limits; and `timings.json`'s per-run rows each carry `includeUnapproved`
> beside `latencyMs`, so a duration measured on a flag-on call over the withheld
> plane is published as one. Named here rather than left for a reader to
> discover: a duration is its own disclosure channel, not a by-product of the
> count.
> `test_the_equality_querys_two_planes_each_carry_their_own_probes_cause_and_timings_row`
> holds three things: each plane's `abstentionCause` derived from its own-limit
> probe; the report's `abstentionProbe` reading
> `{includeUnapproved: true, limits: [10, 50]}`; and, filtered to that query's
> own id, exactly the two flag-on `timings.json` rows `(10, true)` and
> `(50, true)`. **`limits` is a union across every probed query in the corpus,
> never one query's own list** — the smoke corpus's non-equality abstention query
> is probed at the base limit alone, and the member still reads `[10, 50]`
> because the equality query beside it is probed at both. A single query's set is
> derivable from its own entry instead: the base limit always, and the equality
> limit exactly when that entry carries `atEqualityLimit`, since the probe's
> limits and that branch are decided by the same both-corpora predicate.
>
> **What the tripwire caught was `abstentionCause`, not the channel.**
> `EXPECTED_QUERY_METRIC_KEYS` moved in the same commit that added that key,
> which is the owed-to-implemented signal firing as designed. It does not reach
> `equality.channel`: the tripwire scans `_query_metrics`' own AST, while the
> channel summary is assembled one level up in `build_report`, structurally
> outside that scan. `equality.channel` is held by the integration pin named
> below instead.
>
> **The mechanism is pinned; these two figures are not.**
> `test_the_equality_channel_summary_carries_the_787_reason_verbatim_and_the_measured_counts`
> asserts the *smoke* corpus's own summary — `queriesDiffering` 0 of 3 at both
> limits — and pins `reason` by equality against a **literal copy of the phrase
> written into the pin itself**, so a drift in `report._CHANNEL_REASON` diverges
> from that copy and reddens; its own docstring states that those counts are not
> the S3 corpus's. 18 of 26 and 21 of 26 stay a dated measurement anchored to
> `4ce0868f`, and slice S4's committed baseline is what will hold them.
>
> *Corrected in PR #795's trio round, at an adversarial finding:* the sentence
> above previously read that `reason` was pinned "by equality against the
> module's constant, so a paraphrase reddens rather than passing" — enforcement
> that check could not provide. Both sides of the comparison read the same
> constant, so mutating it moved both: the `single-user` paraphrase **survived 78
> of 78**, the measurement recorded in the pin file's own comment. A pin's
> authority has to be independent of the value it checks, which is what the
> literal copy — now at both the unit and the integration site — supplies.
>
> **The aggregate is annotated, not split.** `abstentionAccuracy` still blends
> gate-earned and absence-earned samples, matching the `forbiddenPresentCause`
> convention this mirrors, where an annotated zero still counts toward
> `supersededKnowledgeErrorRate`. #787's design sketch also offered reporting
> the two populations separately, or excluding annotated samples from the mean;
> neither was taken, and `_abstention_cause`'s docstring carries that choice
> where a reader meets the number.

**2. The census is the test for the build-time-excluded and status-unsurfaceable
mechanisms.** No build indexes these members, so no query can test them and a
count is what does — but which count, and what it can catch, differs by side of
the seam. On this branch,
`test_the_manifest_census_agrees_with_the_migrations_it_declares` compares the
declared `items`, `byStatus` and `bySensitivity` against the same figures
replayed from the migration files and stops there: `DERIVABLE_CENSUS_KEYS` omits
`chunks`, and no committed check here measures one. The S3-side guarantee is
therefore the derived split rather than a number —
`test_the_withheld_plane_splits_into_gate_tested_and_census_tested_members`
recomputes each withheld member's class from its folded status and sensitivity
against sets folded out of `may_surface` and `may_disclose`, compares both
classes to their pinned item ids and flags any member in neither, so a member
changing side, or a serving ceiling moving under it, turns that test RED.
Comparing a *measured* `full.chunks` against the manifest's frozen `615` is the
S2 loader's census-mismatch refusal
([PR #780](https://github.com/theurian/theurian/pull/780), in flight as this is
written), and it catches drift only after the freeze: a row leaking past the
build ceiling later moves the count, while one already indexed when the number
was taken is baked into it and no chunk count can report it. Under the
both-sides-`--include-unapproved` flavour that number sharpens: `full.chunks` =
visible + draft/proposed-withheld chunks, and an above-ceiling row still may not
appear in it.

*Corrected in this pull request's round one, at a security-review finding:* the
sentence this replaces said present-tense that such a leak "moves `chunks` and
reddens the census-mismatch refusal", naming an instrument this branch does not
carry and a detection window a frozen number cannot have. The rider's substance
— that the census and not the battery covers these mechanisms — is the
slice-S2 lane's round-one text unchanged.

**3. Any reported metric whose zero is forced by build-time exclusion rather than
by ranking quality carries a cause note beside the number.** The
superseded-knowledge error rate is the canonical case: 0 because superseded rows
are never indexed, not because ranking avoided them. Where the cause cannot be
derived honestly, the number carries no annotation and this ADR states that
limitation. A zero without its cause is a measurement that misleads.

The riders are implemented on both sides of the same seam. S2's `report.json`
carries the equality section's `scope` and, beside a metric whose zero follows
from every forbidden item being census-tested, the cause note — omitted rather
than guessed where the classification leaves the cause undecided (PR #780, whose
loader and report name this rule in their own comments). S3's `manifest.yaml`
`description` names the split and the superseded-knowledge zero, under a citation
of the decision 6 this amendment extends (PR #778).

### What the rule asks of a corpus editor

A withheld row authored to exercise the gate must end `draft` or `proposed` and
within the serving ceiling, or it silently becomes census-tested and the battery
loses a member it is read as covering. An item an **enabled** query's judgement
lists as `relevant` must end `approved`, within the build ceiling, and
visible-plane: the battery queries at default flags, so a draft relevant item
scores zero recall by construction — rider 3's class, a number that measures
the judgement rather than the retriever.

*Corrected in PR #793, at a code-review finding:* the sentence above originally
read "An item a judgement lists as `relevant` must end `approved`:", with no
scope to `enabled`. A **disabled** query's judgement is exempt — S3's own
`q-hist-ttl-evolution` (`class: historical`, `enabled: false`) judges a
superseded item relevant by design, to be validated once its phase enables the
query, not retrievable at today's default flags. The Phase A joint build caught
the unscoped original: it ran the S2 loader over the real S3 corpus and refused
a corpus that was correct under this exemption. `tools/eval/corpus.py`'s
`_check_relevant_items_retrievable` carries that refusal's full narrative and
enforces the split: every judgement's relevant item id must *exist* (a
`relevant-item-unknown` refusal names a typo no migration operation creates),
checked regardless of `enabled`, while *retrievability* — approved,
within-ceiling, visible-plane — is scoped to `enabled: true`.
