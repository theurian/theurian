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
   [ADR-0024](0024-a-purge-is-a-build.md) decision 5's trigger set. **Done in
   #119 phase 5** (2026-08-24): the operation joins the withdrawal candidate set
   and `revisions_to_purge` gains the build's recorded disclosure flavor as a
   second axis beside `indexesUnapproved`, so an item reclassified past the
   ceiling its build ran under leaves the published index in the same
   `migrate apply` — see Compliance for the test and for the one direction this
   cannot close.
3. **The read-side predicate**, adding the axis to `_scope` *and* to
   `_node_scope` — the forest's own first gate, which emits the same predicates
   over `nodes` and is where a routed query meets a summary node before it
   descends to any leaf. **Done in #119 phase 4** (2026-08-24): both emit
   `sensitivity IN (…)` over the deployment's expanded grant, in the same
   statement as the match, and every retriever on the `IndexStore` port takes
   that grant as a required argument. It is defence in depth over part 1 rather
   than a substitute for it — see Compliance for what that means it can and
   cannot hold.
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

- **The entitlement model.** ~~What makes a row withheld-by-sensitivity in a
  single-user loopback daemon? Caller identity does not exist — the daemon
  authenticates a bearer token, not a person. Candidates include a per-call
  scope parameter, per-project configuration, or a declared operator profile.
  Each has a different disclosure surface, and choosing between them is design
  work this ADR is not the place for.~~

  **Settled 2026-08-23 by a maintainer decision on
  [#119](https://github.com/theurian/theurian/issues/119#issuecomment-5386235623):
  a deployment serving profile.** The operator declares one sensitivity *ceiling*
  for the whole deployment, and `StaticAuthorizationProvider` expands it once into
  the grant every read carries. Three properties decided it, and each rules out
  one of the candidates above:

  - **It lives in the operator-owned data directory, never in the Git-tracked
    `.theurian/config.yaml`** — `<data_dir>/auth/serving-profile`, one word, mode
    0600, beside the token and refused if another local account can reach it.
    Repository contributors are an untrusted actor class in this model, so a
    committed ceiling would make *raising* the ceiling a contributor-authored
    access-control change: reviewable in principle, indistinguishable from an
    ordinary configuration edit in practice. That is what rules out per-project
    configuration as the control.
  - **It is not a per-call parameter.** A ceiling a caller chooses is a ceiling a
    caller can raise, and the caller here is an agent reasoning over untrusted
    input (T-3). Per-call *narrowing* may be added later as a refinement; it
    cannot be the control.
  - **It is a property of the deployment, not of a project.** The port is
    per-`(principal, project_id)` and the OSS core's implementation deliberately
    ignores both, answering identically for every project rather than inventing a
    nominal identity to look per-project. A per-project or per-principal provider
    is a hosted adapter's own implementation of the same Protocol — the seam
    supports it and the core does not pretend to it (recorded on
    [#119](https://github.com/theurian/theurian/issues/119#issuecomment-5388663423)).

  **The shipped default is restrictive: ceiling `internal`.** `confidential` and
  `restricted` are withheld until an operator raises it, and a deployment with no
  profile file gets that default rather than an allow-all. See *Consequences →
  Negative*, where it is recorded as an accepted, intended behaviour change rather
  than a side effect.
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
  - **A reclassification is now an index-side event.** Exclusion makes a build
    specific to a ceiling, so an item that moves past that ceiling afterwards is
    a row the build would not write today — which is what turns
    `changeSensitivity` into a purge trigger (part 2) where under a
    gate-at-read-time design it would have been nothing but a relabelling. The
    trigger is not symmetric, and cannot be: a purge copies a build and deletes
    from the copy, so an item reclassified *into* the ceiling has no row to
    restore and waits for the next `index build`. Recorded under Compliance.

  - **The read-side predicate** (part 3) was owed here and landed in phase 4.
    Before it, a document reclassified upward after a build kept its chunk row
    and was withheld by the canonical re-check alone.

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

  **It is the *default* rather than something an operator opts into, and that is
  the decision rather than a side effect of it.** `DEFAULT_CEILING` is `internal`
  (the one-line constant change is this branch's closing commit, deliberately
  last so that every record above it describes a system that already exists),
  so a deployment that declares no profile serves `public` and `internal` and
  withholds `confidential` and `restricted` — from `knowledge.search`, from
  `knowledge.get`, from `knowledge.status`'s counts, and from the build itself.
  Every existing installation that upgrades loses results it used to get, with no
  configuration change on its part, and the remedy is one word in one file
  (`echo restricted > <data_dir>/auth/serving-profile`).

  **Accepted knowingly by a maintainer decision on 2026-08-23**
  ([#119](https://github.com/theurian/theurian/issues/119#issuecomment-5386235623)),
  against a measurement rather than a principle: on a resident loopback daemon
  serving this repository's own mixed-sensitivity corpus (82 items, 6 of them
  `confidential`), a default-parameter `knowledge.search` returned four
  `confidential` items ranked and excerpted in the top six, and `knowledge.get`
  served a 5,058-character `confidential` body. A permissive default is what made
  that the shipped behaviour, so a restrictive default is the line that closes it —
  and an operator who is surprised by fewer results has been told something true,
  where an operator surprised by a `confidential` excerpt has not.

### Neutral

- No canonical schema change and no new index columns: the columns already
  exist and are already written.
- The published response shape does not change. What changes is which rows reach
  it — and *that* is the part part 4 exists to hold.
- **One accepted timing residual, measured and recorded rather than removed.**
  `idx_items_status` is `(project_id, status)`, so a canonical read that also
  filters on `sensitivity` fetches the above-ceiling rows before dropping them:
  about 0.20 µs per above-ceiling row on `list_items_by_status` and 0.54 µs on
  `count_surfaceable_by_status`, corpus-bounded because neither statement carries
  a `LIMIT`, and in-process figures thousands of rows below TB-1's 1.40 ms
  end-to-end floor. Recovery of content has not been demonstrated and the
  mechanism offers none. Its own threat-model entry is **T-22**, named by root
  cause — *the canonical index does not carry the gate's column* — and the
  flattening is
  [#338](https://github.com/theurian/theurian/issues/338).
- **`knowledge.status` publishes ceiling-narrowed counts; the integrity
  comparison does not.** Recorded 2026-08-24
  ([#119](https://github.com/theurian/theurian/issues/119#issuecomment-5390252745)).
  `itemCount` and `itemsByStatus` are a statistic over rows the caller may not
  see, so they follow the grant — the disclosure-family member reached through a
  tool nobody checking `knowledge.search` would think to call. The `#30` integrity
  mechanism moves the other way and stays on the **ungated** population
  internally: `expected_surfaceable_count` is written ceiling-blind by
  `migrate apply`, so comparing a narrowed live count against it would make a
  healthy restricted deployment report `damageDetected` from its own ceiling —
  measured, which is why the phase that added the predicate deliberately left both
  halves alone rather than narrowing one. The cost is one `COUNT` this tool used
  to get for free.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **Ship the read-side predicate alone** | Two measured defects. (a) The index still holds the withheld text, so BM25 collection statistics computed over the whole index file continue to price the visible rows — T-17a's mechanism, moved from the status axis to the sensitivity axis; the threat model records T-17a's closure as covering "the status axis only" for exactly this reason. (b) `migration_engine.py` *excluded* `changeSensitivity` from the withdrawal candidate set the purge reads, on the recorded ground that the stale column "is read by no gate before #119". The moment a gate read it, that exclusion inverted into a defect: a document reclassified `internal → restricted` kept its row in the published build until the next manual `index build`. Both halves are closed as of phases 3–5; this row records why shipping part 3 on its own would not have been enough. |
| **Keep sensitivity a label until after 1.0** | A published governance label that no query reads is, by this project's own grading, a claim that misleads a security decision. Deferral was defensible while one human at a CLI was the only writer; Phase B multiplies the writers, and a stable release turns the current meaning of every corpus into a compatibility promise. |
| **Remove the label until it can be enforced** | `sensitivity` is already in the published wire contract and carries real information a caller may act on. Removing it is a breaking change to that contract, it deletes information the corpus genuinely holds, and it buys no safety — the underlying content is served either way. It trades a misleading claim for no claim at all, at the cost of a break. |
| **Enforce tenant and ACL group at the same time** | Not rejected, and **not deferred either, as this row used to say**: #119 closed them by degenerate discharge — refused at write time, so they hold no content and nothing routes on them, with a request-boundary refusal and a test binding the grant to the write refusal beside it (see *Two axes this ADR does not enforce* under Compliance). Sensitivity is the axis whose values actually vary, which is what made it the one that misleads and the one that needed a predicate. |

## Compliance

**All four parts are discharged as of #119 phases 3 to 6 (2026-08-24).** Nothing
in this ADR is owed. Its owner was
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

- **Part 2 — done.** `_withdrawal_affected_item` admits `changeSensitivity` to
  the withdrawal candidate set, `WithdrawalCandidate` carries the item's **final**
  disclosure class, and `revisions_to_purge` reduces the set against *two* flavor
  axes read off the published pointer: `indexesUnapproved` as before, and
  `indexedSensitivities` — the expanded set the build was allowed to write, never
  a ceiling word re-expanded at purge time. A revision goes if it fails either.
  So one `migrate apply` publishes a purged build with no `index build` after it,
  the way a `deprecateItem` already does, and the reclassification meets the same
  `recompute_forest` the withdrawal trigger does rather than a second mechanism.

  This reverses the exclusion the previous revision of this document recorded as
  deliberate, and the reasoning is in `_withdrawal_affected_item`'s own docstring
  rather than only here: the ground for excluding it was that the index's stale
  `sensitivity` column "is read by no gate", which phases 3 and 4 made false.

  `tests/integration/test_sensitivity_purge.py` drives it, through the real CLI
  with no `index build` after the apply. The upward case is parametrized over all
  four text indexes and asserts on the *file* — no chunk row, no summary node
  carrying the document's marker, and no `fts5vocab` term that could only have
  come from it — because that is where the T-17a mechanism lives; a sibling
  asserts the response is still `indexed: true` with no `fallbackReason`, so a
  purge that broke the pointer's flavor and pushed the query onto the canonical
  scan cannot pass as a withholding. `test_the_purged_forest_equals_one_built_
  above_the_ceiling` compares the purged forest against one built when the item
  was already `confidential` — nodes, derivation edges and node vectors — which
  is how the trigger inherits ADR-0008 decision 9 rather than re-arguing it.
  Measured RED against the pre-phase-5 engine: six of the file's eight tests
  failed, the four parametrizations on the item's chunk rows still being present.

  **What part 2 does not close, recorded rather than fixed.** A reclassification
  *downward* — `restricted → internal` under an `internal` ceiling — cannot be
  purged *in*. A purge copies the published build and deletes rows from the copy
  (`index_purge.purge_into`), and the build never wrote a row for an item that
  was above its ceiling at build time, so there is nothing to delete and nothing
  to add. The item stays unserved until the next `index build` re-derives from
  canonical state. That fails toward *fewer* results, which is the same direction
  a draft approved after the build already fails in, and it is pinned in both
  halves by `test_a_downward_reclassification_waits_for_the_next_build` so that a
  change closing the window turns a test RED rather than passing unnoticed.

- **Part 3 — done.** `_scope` and `_node_scope` each emit
  `sensitivity IN (…)` over the deployment's expanded grant beside their project
  and status predicates, bound in the same statement as the match, and the four
  `IndexStore` retrievers take that grant as a required keyword with no default —
  the shape `may_disclose` uses, for the reason it uses it.

  **What it is worth is bounded and is stated at the predicate.** Against a build
  made under the grant in force it excludes nothing, because part 1 already kept
  those rows out of the file; what it answers for is a *wider* build reached
  because a pointer was rewritten, a file was copied in, or
  `_published_index`'s equality check was defeated. It cannot take back what such
  a file's FTS5 collection statistics have already priced, which is why part 1 is
  the control and this is the second line.

  Three tests drive it, all at the store layer, because through the whole stack
  the `serving-profile-mismatch` fallback fires first and a guard no input reaches
  survives its own deletion:
  `test_index_store.py::test_a_build_wider_than_the_grant_is_withheld_by_the_clause_alone`
  over all four SQL shapes `_scope` feeds (the word index, the trigram lookup, the
  scan below the trigram floor, and the dense join);
  `test_index_store.py::test_the_clause_changes_nothing_on_a_build_made_under_the_same_grant`,
  which asserts identical rows *and scores* under the grant and under everything,
  so the predicate is measured inert on an honest build; and
  `test_forest_node_scope.py::test_search_summaries_does_not_descend_a_node_above_the_deployments_ceiling`
  for the node half, isolated from the leaf gate the way that file's other two
  tests are. Measured RED by deleting each clause in turn: the leaf pair fails in
  all four parametrizations with the withheld classes in the answer, and the node
  test fails with the leaked leaf in it.

  The axis set is pinned as well as tested:
  `test_gate_call_sites.py::test_the_axes_security_md_publishes_are_the_axes_the_scope_filter_emits`
  binds `_scope`'s emitted `chunks.<column>` tokens to SECURITY.md and the FR-R1
  register, and went RED in both parametrizations the moment the clause landed —
  which is what forced the SQL, the test and both prose surfaces into one commit,
  exactly as this section predicted.

- **Part 4 — done in #119 phase 6** (2026-08-24), by a file of its own:
  `tests/integration/test_sensitivity_absence_proof.py`, **38 tests**. It states
  `test_absence_proof.py`'s property over a different reason for withholding, and
  it is a second implementation rather than an import because the test tree has no
  `__init__.py` and runs under `--import-mode=importlib` — a cost that module's own
  docstring already priced when it argued that a new axis should be a new file.

  **The coverage grid, because "the suite exists" is not the claim.** Three
  withholding mechanisms cross two ways for a pair to differ:

  | | `one-payload-apart` (content reaches a caller) | `present-in-one-only` (a slot, a count, a token total, a BM25 statistic) |
  | :-- | :-- | :-- |
  | `excluded-at-build` (part 1) | covered | covered |
  | `reclassified-not-purged` (part 3, the canonical re-check alone) | covered | **absent by record** |
  | `reclassified-and-purged` (part 2, through the real CLI) | covered | covered |

  `SHAPES` is written as the list of *valid* cells, so the missing one is absent by
  construction rather than filtered away, and
  `test_the_shape_grid_names_the_cell_it_leaves_out` fails if the grid stops saying
  which. Why it is absent: in that state the published build still holds the
  reclassified document's text, so a control that never held it has different FTS5
  collection statistics and the visible rows are scored against a different `avgdl`
  and different document frequencies. The equality *would* fail, honestly — it is
  T-17a on this axis, recorded under part 2 as the one direction the purge cannot
  close and in the threat model's T-17a entry — and the honest response to that
  failure is not to weaken the assertion.

  Of the 38: 24 are the whole-response comparison over two corpus depths × the
  three valid generated cells × four argument sets, 3 compare `knowledge.status`'s
  counts, 3 sweep every string a caller reads for an above-ceiling payload, and the
  rest are the single-property tests plus the hand-written purged pair
  (`test_a_purged_build_answers_as_one_that_was_never_allowed_to_hold_the_row`),
  which runs the real `theurian migrate apply`. **Nothing is masked** on the
  generated pairs; the three values a two-project comparison would otherwise have
  to exclude — `snapshotId`, `indexBuildId` and the registry id — are held equal as
  *inputs* instead.

  **What this file does not reach is stated in it and delivered elsewhere**, which
  is how all four surfaces are covered rather than two: every pair here builds a
  chunk-only index, so the node half comes from
  `test_sensitivity_purge.py::test_the_purged_forest_equals_one_built_above_the_ceiling`
  (nodes, derivation edges and node vectors, purged against
  never-allowed-to-hold) and
  `test_forest_builder.py::test_an_above_ceiling_document_reaches_neither_half_of_the_index`
  (build-side exclusion, parametrised over all four text indexes). Those two plus
  this file are the three-test shape the status-axis closure needed, on this axis.

  Also not reached, and recorded rather than implied: the unranked fallback path
  (pinned separately in `test_mcp_tools.py`, because reaching it from a pair means
  a second variable in a comparison that has one), non-`approved` statuses (mixing
  the axes would let the status gate satisfy an equality the disclosure gate was
  supposed to), scripts without word boundaries, and **durations** — excluded by
  the recorded decision below rather than by measurement.

The status-axis closure this part was modelled on:

- `tests/integration/test_absence_proof.py` for the leaf surfaces,
  `test_forest_purge_equality.py::test_a_purged_forest_equals_one_that_never_held_the_withdrawn_rows`,
  and
  `test_forest_builder.py::test_a_purged_forest_leaves_no_residue_in_a_node_text_index`
  over both node indexes — three tests, not one, which is why this part's own
  discharge above names three files.

**All four parts are discharged as of #119 phase 6 (2026-08-24), and nothing in
this ADR is owed.**

**All four means all four over both halves.** A change that gates
`chunks_fts`/`chunks_trigram` and leaves `nodes_fts`/`nodes_trigram` open does
not discharge this ADR: the threat model records the two node surfaces as the
same T-17a class by the same FTS5 mechanism, where a withheld node reweights the
`idf` of the visible nodes it is scored against and so moves which node routes
and what score a leaf inherits.

~~Until all four land, `system.capabilities` must not advertise sensitivity
enforcement in any form.~~ **Discharged in #119 phase 6: all four have landed, so
the advertisement is now permitted and is made.** `system.capabilities` reports
`sensitivityEnforcement: true`, pinned by
`test_mcp_tools.py::test_capabilities_report_what_is_and_is_not_built` and by the
block's own population test, so it cannot be flipped or dropped unnoticed. It is a
*build* property, deliberately, and reports only that this build enforces the
axis — **not the ceiling this deployment declares**. Every other flag in that
block is a build property too, and publishing the ceiling word would tell a caller
which levels it is not being shown, which is a statement about withheld content
made on a surface no gate protects. An operator who needs to know the ceiling
reads the profile file.

**The sentence this ADR used to protect is gone, and
its going is what part 3 landing means**: SECURITY.md said no retrieval predicate
read `chunks.sensitivity`, so it was "a published label, not a control", and that
was true until phase 4 made it false. What replaced it names the three places the
axis is now enforced — the build, the retrievers' predicate, the canonical
re-check — and named parts 2 and 4 as still owed; phase 5 moved that to part 4
alone and added the purge trigger to the enforcement it lists, which is the shape
this ADR asked for: the prose may say what the code does and no more. SECURITY.md
separately claimed that sensitivity is refused at write time, which is false
(only `tenantId` and `aclGroup` are); that clause was in the sentence phase 4
rewrote and is not restated there.

**When #119's implementation settles the entitlement model or the
exclusion-versus-gating question, this ADR is amended with what was chosen and
why** — the reasoning is the artifact, and an ADR that silently acquires an
answer it once called open is worse than one that never named it. **Both are
settled and both amendments are above**, in *Deliberately left open*, where the
superseded text is struck through rather than deleted.

### Two axes this ADR does not enforce, and the difference between them

The decision above is about `sensitivity` alone. FR-R1 names two more axes, and
they are **discharged degenerately** rather than enforced — a weaker claim, said
plainly here so nothing downstream reads it as the same one (maintainer decision
4, 2026-08-23).

`migration_engine._scope_violations` refuses at write time any revision naming a
tenant other than `local` or an ACL group other than `default` (#110), so no
stored row can carry anything else and there is nothing along either axis to
withhold. Two things stand beside that refusal so the argument is checkable rather
than asserted: `mcp/tools.py`'s `_resolve` refuses a grant naming another tenant
before the registry is read (`_tenant_boundary_refusal` — unreachable through the
shipped composition, and written out anyway so that a hosted provider meets a
message rather than a comment), and
`test_authorization_provider.py::test_tenant_and_acl_group_are_the_values_write_time_already_refuses`
reads `_ENFORCED_TENANT_ID` and `_ENFORCED_ACL_GROUP` out of the engine and the
grant out of the provider rather than restating either as a literal — because the
discharge holds only while the provider grants *exactly* what the writer refuses
to depart from.

**What this is not.** It is not a predicate, and it does not become one by being
recorded here. A deployment that ever stores a second tenant or a second ACL group
needs a real control; those are hosted columns and hosted work. #119 closes on
these two axes on this basis, and the moment the write refusal is relaxed, this
paragraph is a defect rather than a discharge.
