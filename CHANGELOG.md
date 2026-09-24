# Changelog

This repository ships **two independently versioned artifacts**
([ADR-0001](docs/adr/0001-monorepo-with-independent-artifacts.md)). Their
changelogs are the ones you want:

- **Theurian Core** — [`packages/theurian-core/CHANGELOG.md`](packages/theurian-core/CHANGELOG.md)
- **Claude Code plugin** — [`plugins/claude-code/CHANGELOG.md`](plugins/claude-code/CHANGELOG.md)

This file records repository-level events only: governance changes, licence
changes, and milestone completions.

## Repository history

### 2026-09-24 — Phase A complete: the RAPTOR decision closed its exit criteria

The entry below, from earlier the same day, recorded the baseline and said in
terms that it was **not** Phase A's completion, because the row's third exit
clause — "the RAPTOR, CJK and dense decisions each have a measurement behind
them" — was unmet. It is met now, and that entry stays as the honest record of
where the phase stood at that point.

What closed it is a decision rather than more code: **extractive RAPTOR stays
opt-in, and default-on is declined on the measurement.** A second, raptor-ON arm
of the harness runs the same corpus and the same queries with one variable
changed, and the committed baseline's `comparison` block carries the deltas. On
the class RAPTOR was designed for, top rank improves substantially —
broad-architectural Recall@1 `0.111111` → `0.361111`, MRR `0.583333` → `1.0`.
Across all seven classes it costs top rank instead: Recall@1 `-0.147727`, MRR
`-0.097601`, concentrated in `cross-adr` and `rejected-alternative` — over 26
queries, of which 22 carry a `recallAtK` at all, the four `unknown` ones judging
no relevant item. ΔRecall@10 is `0.0` in every class that carries a delta, so
the same judged-relevant items stay within the first ten. The returned content's
composition does move: `evidencePrecision` shifted, and since it is a ratio over
the set of anchors a response returns it is order-invariant, so that movement is
the routing changing which content comes back — ADR-0008 decision 8 doing its
designed work. What decides the question is still the top-rank figures. Moving
in RAPTOR's favour, and weighed rather than omitted: evidence precision rose
(`+0.006173` overall, about +16% relative in one of the two cost classes), and
abstention accuracy and the superseded-knowledge error rate did not move at all.
Turning it on remains one `theurian index build --raptor` away — though that
takes the whole per-class profile, since forest routing is index-wide whenever a
forest exists — which is what makes declining the default a choice about a
default rather than a withdrawal of the capability.

The reasoning, the relative-not-absolute caveat and the re-check command are in
[ADR-0008](docs/adr/0008-raptor-forest.md)'s decision 10 amendment. The other
two clauses were already satisfied by measurements rather than by this decision:
the CJK defects are quantified by the baseline's own two CJK members, and the
dense question rests on the hashed embedder that
[ADR-0009](docs/adr/0009-no-llm-vendor-lock-in.md) already measured as
uninformative — an arm that ran and lost, with only a real-model arm awaiting a
real provider.

Recorded here as a phase completion, the successor to the milestone completions
below. No artifact version moved: nothing in this phase reaches an installer.

### 2026-09-24 — Phase A's retrieval evaluation baseline committed

Retrieval quality has a reproducible measurement for the first time, so a
ranking change is argued with a number instead of an intuition
([ADR-0036](docs/adr/0036-golden-judgements-are-committed-regression-fixtures.md),
[`docs/roadmap.md`](docs/roadmap.md) Phase A). Four parts, **none of them inside
either distributed artifact**:

- The `tools/eval/` harness — a loader that refuses a malformed corpus under
  fourteen named rules (`corpus.py`'s own `CorpusError` tags; `corpus_build.py`
  raises four more at build time, `census-mismatch` among them), a two-plane
  build (`full` and `clean`, both under `index build --include-unapproved`, so
  what a disclosure-equality query measures is the query-time gate rather than a
  build-time exclusion), an in-process Streamable HTTP wire client through the
  SEC-12 middleware seat, and pure metrics.
- The frozen fixture corpus at `tests/fixtures/eval` — 33 items in `full` and 26
  in `clean`, with 26 of its 27 declared golden queries enabled.
- The first committed baseline under `tools/eval/baseline/`.
- An advisory CI comparison (`tools/eval/compare_baseline.py`, run by
  `core.yml`'s `retrieval-baseline` job when retrieval-affecting paths change).

**The harness produces; it does not assert.** No minimum Recall@k, no MRR floor
and no maximum latency appears in the three contract schemas or in the harness,
and the baseline's own README states what that makes the first run: "This run
DEFINES the baseline; it does not assert one." The figures are a dated
measurement — 2026-09-24, at `a58fdcb5` — and whether any of them is acceptable
is a judgement recorded against a later run. Whether the advisory comparison
ever becomes a blocking gate is a decision ADR-0036 records as untaken.

Recorded here rather than in either artifact's changelog because none of it
reaches an installer: `tools/eval/` and the fixture corpus are repository
development tooling, and no wire surface, schema or runtime dependency moved.
**It is not Phase A's completion.** That row's Exit criteria ask for three
things, and this is the first two — a baseline report committed, and CI
reporting against it. The third, that "the RAPTOR, CJK and dense decisions each
have a measurement behind them", is what a baseline makes possible rather than
something it settles: none of those decisions is taken here.

### 2026-08-20 — Forward planning moved from milestones to phases

[`docs/roadmap.md`](docs/roadmap.md) was adopted as the plan of record, and the
README's forward-looking milestone rows were retired in favour of its phases.
The numbering had stopped being trustworthy: ADR-0013 records `theurian propose`
as landing in Milestone 7 while the README listed Milestone 7 as `planned`, and
the definition of that milestone differed between documents. Milestones 0–6 stay
in the README as shipped history.

Recorded here because it changes how work is planned and announced. It is not a
milestone completion, and it is not comparable to the entries below — and the
entries below are not a complete set: milestones 1 through 6 are missing from
this file, an inconsistency the roadmap's own appendix records against Phase 0.

### 2026-08-01 — Milestone 0 complete

Architecture and OSS foundation.

- Requirements, non-functional, security, and OSS requirements catalogued with
  stable identifiers referenced from ADRs and tests.
- Fifteen ADRs, each with rejected alternatives and an enforcement mechanism.
- Domain model with ten enforced invariants; fourteen ports as `Protocol`s.
  *(What Milestone 0 shipped. The set has grown since;
  [ADR-0003](docs/adr/0003-ports-and-adapters.md) point 5's Milestone 7
  amendment names `ALL_PORTS` as the register and carries the current count.)*
- Public JSON Schemas as the Core/plugin contract, co-owned in CODEOWNERS.
- Claude Code plugin skeleton: twelve commands, a bounded `SessionStart` hook,
  and a connection template that setup installs rather than the plugin
  auto-registering.
- Threat model v1: four trust boundaries, sixteen enumerated threats.
- Path-filtered CI: quality, tests, an offline run with the network blocked,
  packaging, CodeQL, dependency review, licence scan, SBOM, secret scan,
  Conventional Commits, DCO, and documentation link checking.
- 275 tests, 82% coverage, strict type checking, all offline.

### 2026-08-01 — Project started

Apache-2.0, DCO, maintainer-led governance.

## Compatibility matrix

| Plugin | Core | Protocol |
| :-- | :-- | :-- |
| 0.1.x | ≥ 0.1.0-dev.0, < 0.2.0 | `theurian/v1` |
