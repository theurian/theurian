# ADR-0025: Sensitivity is enforced before 0.1.0 stable

- Status: accepted
- Date: 2026-08-20
- Deciders: Theurian maintainers
- Requirements: FR-R1, SEC-13, SEC-14, T-17, T-17a
- Decision recorded in [#119](https://github.com/theurian/theurian/issues/119)
  ([the upgrade](https://github.com/theurian/theurian/issues/119#issuecomment-5350556157),
  [the implementation shape](https://github.com/theurian/theurian/issues/119#issuecomment-5351154317))
  and adopted as Phase 0 of [the roadmap](../roadmap.md)
- Extends [ADR-0024](0024-a-purge-is-a-build.md) decision 5 (the purge trigger set)

## Context

`sensitivity` is published on every retrieval result and **filtered on by no
query**. `SqliteIndexStore._scope` emits two predicates, project and status; its
own docstring names `sensitivity`, `trust_level` and `namespace` as columns "no
query reads". `docs/architecture/requirements-analysis.md` and SECURITY.md both
say the same thing in the present tense: a `restricted` document *is* ingested
and returned, labelled — so the label is a published claim about who may read
something, and nothing acts on it.

By this project's own severity rubric that is the shape of a published claim
that misleads a security decision. It was a recorded deferral, which was
defensible while the only writer was one human at a CLI. Two things changed it:

1. The roadmap's Phase B opens a protocol-level write path, multiplying the
   callers who can *set* a sensitivity that nothing reads.
2. 0.1.0 stable is the first release that promises an upgrade path. Shipping a
   governance label as stable, then later making it a control, changes what
   existing corpora mean without changing their bytes.

The deferral was therefore upgraded to a release gate by a maintainer decision
on 2026-08-20, recorded in the issue and adopted in the roadmap.

## Decision

**Before 0.1.0 stable, `sensitivity` stops being a published label and becomes
an enforced read control.**

The implementation is **at least four parts — three of implementation and one of
proof — and each applies to *both halves of the derived index*, leaf and
forest.** A change that ships fewer than all four, or that ships all four over
the leaf half alone, does not discharge this ADR:

1. **Build-time gating and derivation.** `IndexBuilder._build` gated on status
   only and wrote `sensitivity` into every chunk row as a label; `ForestBuilder.derive`
   (`application/forest_builder.py`) then derives summary nodes over what the
   builder wrote. Both have to participate, because a query-time predicate alone
   leaves withheld-by-sensitivity text in the FTS5 tables — leaf text in
   `chunks_fts`/`chunks_trigram`, and summary text in
   `nodes_fts`/`nodes_trigram`. **Done in #119 phase 3** (2026-08-24): the
   builder now consults `may_disclose` beside `may_surface` and writes no row for
   an item above the ceiling — see *Deliberately left open* below for which of
   the two candidate shapes that settled, and Compliance for the test.
2. **A `changeSensitivity`-triggered purge**, extending
   [ADR-0024](0024-a-purge-is-a-build.md) decision 5's trigger set.
3. **The read-side predicate**, adding the axis to `_scope` *and* to
   `_node_scope` (`index_store.py:1310`) — the forest's own first gate, which
   emits the same two predicates and is where a routed query meets a summary
   node before it descends to any leaf.
4. **The two-corpora equality suite parametrized over the sensitivity axis** —
   an index holding the withheld rows and an index that never held them must
   return the same response to the same query — **across all four BM25 scoring
   surfaces**: `bm25(chunks_fts)` and `bm25(chunks_trigram)`
   (`index_store.py:1060,1142`), and `bm25(nodes_fts)` and `bm25(nodes_trigram)`
   (`index_forest.py:103-104`).

Parts 1 and 2 are not index-side detail. Part 2 is an application-layer change
to the migration engine's withdrawal candidate set. What that purge then *does*
differs by half, and the difference is load-bearing: for the chunk half it
copies the published build and deletes rows from the copy, while for the forest
half `recompute_forest` **re-derives each affected scope's summary trees over
the surviving rows** (ADR-0008 decision 9, `index_purge.py:443`). A sensitivity
trigger inherits both behaviours and has to be correct in both.

Sensitivity is a scope component, so moving it is a `SEC-14` operation: the
published label already follows a reclassification immediately, because
`result_payload` reads the item's current sensitivity rather than the index
row's. That is exactly why the index column is allowed to go stale today, and
exactly why it stops being allowed to once a gate reads it.

### Deliberately left open

This ADR does not settle, and **must be amended when #119's implementation
does**:

- **The entitlement model.** What makes a row withheld-by-sensitivity in a
  single-user loopback daemon? Caller identity does not exist — the daemon
  authenticates a bearer token, not a person. Candidates include a per-call
  scope parameter, per-project configuration, or a declared operator profile.
  Each has a different disclosure surface, and choosing between them is design
  work this ADR is not the place for.
- **Exclusion versus gating.** ~~Whether withheld-by-sensitivity rows are kept
  out of the index entirely, **or indexed and gated at read time behind a
  mechanism that isolates the collection statistics** — a per-entitlement build
  flavor of the kind ADR-0024 already runs for status (`indexes_unapproved`).
  Part 1 requires the builder to participate either way; it does not choose
  which. Part 4 constrains both branches and chooses neither.~~

  **Settled 2026-08-24 by #119 phase 3: exclusion at build time, with one build
  flavor per deployment.** `IndexBuilder._build` consults `may_disclose` against
  the deployment's serving profile beside the status gate it already ran, so an
  above-ceiling item writes no chunk row — and therefore no summary node, since
  the forest is derived over what the build wrote. The two candidates were not
  equally available: gating at read time leaves the withheld text in
  `chunks_fts`, `chunks_trigram`, `nodes_fts` and `nodes_trigram`, whose BM25
  scores are computed against collection statistics over every row the file
  holds, so the visible rows keep being priced against content no query can
  return. That is T-17a's mechanism on this axis, and no read-side predicate
  removes it.

  What exclusion costs, and what pays for it:

  - **A build is now specific to the ceiling it ran under.** The published
    pointer records that flavor as `indexedSensitivities`, beside
    `indexesUnapproved`, and `mcp.search._published_index` stands aside a build
    whose flavor differs from the grant in force —
    `fallbackReason: serving-profile-mismatch`, degrading to the canonical scan,
    which carries the same grant as a SQL predicate. Both directions refuse: a
    wider build prices the visible rows against text this deployment does not
    serve, and a narrower one answers with a silence a caller reads as "this team
    has made no such decision".
  - **`INDEX_SCHEMA_VERSION` goes 5 → 6.** Every version-5 index predates the
    exclusion and may hold above-ceiling text, and no pointer field can establish
    what such a build excluded. The bump makes those files unusable by
    construction (`index-schema-mismatch`, rebuilt by the standing remedy) rather
    than filtered on read.
  - **The read-side predicate is still owed** (part 3). Until it lands, a
    document reclassified upward *after* a build keeps its chunk row and is
    withheld by the canonical re-check alone — which is now the only way an
    above-ceiling row is in a file this deployment reads at all.

Naming these is the point. An ADR that pretended to settle them would be
overturned by the first implementation attempt.

## Consequences

### Positive

- The gap between what a label claims and what the system does closes before the
  first release that promises anything.
- T-17a's closure argument extends from one axis to two by the same mechanism,
  rather than by a second mechanism that would have to be argued separately.
- Phase B opens onto a gate that already exists, rather than racing it.

### Negative

- 0.1.0 stable is gated on design work with a genuinely open question, and the
  entitlement model may prove larger than a release-blocking item should be. If
  it does, the honest response is to reopen this decision, not to ship part 3
  alone and call the ADR discharged.
- Enforcing an axis that was previously inert will withhold rows from callers
  who see them today. That is the intended effect and it is still a behaviour
  change inside a pre-1.0 line.

### Neutral

- No canonical schema change and no new index columns: the columns already
  exist and are already written.
- The published response shape does not change. What changes is which rows reach
  it — and *that* is the part part 4 exists to hold.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **Ship the read-side predicate alone** | Two measured defects. (a) The index still holds the withheld text, so BM25 collection statistics computed over the whole index file continue to price the visible rows — T-17a's mechanism, moved from the status axis to the sensitivity axis; the threat model records T-17a's closure as covering "the status axis only" for exactly this reason. (b) `migration_engine.py` excludes `changeSensitivity` from the withdrawal candidate set the purge reads on the recorded ground that the stale column "is read by no gate before #119". The moment a gate reads it, that exclusion inverts into a defect: a document reclassified `internal → restricted` keeps clearing the gate under its stale label until the next manual `index build`, and there is no canonical re-check for sensitivity of the kind `CanonicalVisibility._may_surface` performs for status. |
| **Keep sensitivity a label until after 1.0** | A published governance label that no query reads is, by this project's own grading, a claim that misleads a security decision. Deferral was defensible while one human at a CLI was the only writer; Phase B multiplies the writers, and a stable release turns the current meaning of every corpus into a compatibility promise. |
| **Remove the label until it can be enforced** | `sensitivity` is already in the published wire contract and carries real information a caller may act on. Removing it is a breaking change to that contract, it deletes information the corpus genuinely holds, and it buys no safety — the underlying content is served either way. It trades a misleading claim for no claim at all, at the cost of a break. |
| **Enforce tenant and ACL group at the same time** | Not rejected, deferred within the same issue: those axes are refused at write time today, so they hold no content and nothing routes on them. Sensitivity is the axis whose values actually vary, which is what makes it the one that misleads. |

## Compliance

**Part 1 is discharged as of #119 phase 3 (2026-08-24). Parts 2, 3 and 4 are
owed.** The owner of all of them is
[#119](https://github.com/theurian/theurian/issues/119), and the milestone is
Phase 0 — before 0.1.0 stable.

- **Part 1 — done.** `IndexBuilder._build` gates on `may_disclose` against the
  deployment's serving profile beside the status gate, so an above-ceiling item
  writes no chunk row and the forest, derived over what the build wrote, gets no
  summary node either.
  `tests/integration/test_forest_builder.py::test_an_above_ceiling_document_reaches_neither_half_of_the_index`
  holds it over both halves and all four text indexes: two builds of one corpus,
  the first under the shipped default to establish that the document *is*
  indexable and that the table under test really indexes a term unique to it,
  the second under a declared `internal` ceiling. Measured RED by reverting the
  gate. The builder's call site is pinned in
  `tests/unit/test_gate_call_sites.py::DISCLOSURE_GATE_CALL_SITES`, so its
  removal fails there as well.

Still owed, with the part of the decision each discharges:

- **Part 2** — a test that a `changeSensitivity` migration publishes a purged
  build in the same `migrate apply`, the way a withdrawal already does. The
  current behaviour is the opposite and is deliberate:
  `_withdrawal_affected_item` (`migration_engine.py:658-669`) excludes the
  operation from the withdrawal candidate set, with the reasoning in its
  docstring; `revisions_to_purge` then reduces that set by build flavor and
  would also need a sensitivity notion of "flavor" to do its half.
- **Part 3** — a test that `_scope` and `_node_scope` refuse a withheld
  sensitivity. Both emit two predicates today, project and status. **The axis
  set itself is already pinned**, which changes what the implementer owes rather
  than adding a gap:
  `tests/unit/test_gate_call_sites.py::test_the_axes_security_md_publishes_are_the_axes_the_scope_filter_emits`
  binds `_scope`'s emitted `chunks.<column>` tokens to the prose in two
  documents, and runs once per document (`SECURITY.md` and
  `requirements-analysis.md`). A behaviour-neutral third predicate added to
  `_scope` turns it RED in both parametrizations — measured. So adding the
  sensitivity axis is a change to the SQL, that test, and both prose surfaces
  **in one commit**, or the suite refuses it.
- **Part 4** — the existing two-corpora equality tests, parametrized over the
  sensitivity axis and covering all four scoring surfaces. The status-axis
  closure is the model to follow and took three tests, not one:
  `tests/integration/test_absence_proof.py` for the leaf surfaces,
  `test_forest_purge_equality.py::test_a_purged_forest_equals_one_that_never_held_the_withdrawn_rows`,
  and
  `test_forest_builder.py::test_a_purged_forest_leaves_no_residue_in_a_node_text_index`
  over both node indexes. Nothing runs any of them for sensitivity. Phase 3
  changed what this owes rather than discharging it: with the build excluding
  above-ceiling rows, the pair `test_absence_proof.py` generates for the ceiling
  shape now holds two indexes that never held the withheld text, and asserts they
  hold *identical* text (`_indexed_text`). The pair still owed is the one whose
  two indexes differ -- build at `internal`, reclassify to `restricted`, serve at
  `internal` -- which is the only arrangement that puts an above-ceiling row in
  front of a ranked query once part 1 has landed.

**All four means all four over both halves.** A change that gates
`chunks_fts`/`chunks_trigram` and leaves `nodes_fts`/`nodes_trigram` open does
not discharge this ADR: the threat model records the two node surfaces as the
same T-17a class by the same FTS5 mechanism, where a withheld node reweights the
`idf` of the visible nodes it is scored against and so moves which node routes
and what score a leaf inherits.

Until all four land, `system.capabilities` must not advertise sensitivity
enforcement in any form, and **SECURITY.md's statement that no retrieval
predicate reads `chunks.sensitivity`, so it is "a published label, not a
control", stays as written** — it is currently true, and this ADR is the record
that it is not meant to stay true. That is the only sentence this ADR protects:
SECURITY.md separately claims that sensitivity is refused at write time, which
is false today (only `tenantId` and `aclGroup` are), and is owed by the
roadmap's appendix item 12.

**When #119's implementation settles the entitlement model or the
exclusion-versus-gating question, this ADR is amended with what was chosen and
why** — the reasoning is the artifact, and an ADR that silently acquires an
answer it once called open is worse than one that never named it.
