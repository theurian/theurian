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

The implementation is **at least four parts**. A change that ships fewer than
all four does not discharge this ADR:

1. **Build-time gating and derivation.** `IndexBuilder._build` gates on status
   only and writes `sensitivity` into every chunk row
   (`index_builder.py:146,209`). The builder has to participate, because a
   query-time predicate alone leaves withheld-by-sensitivity chunk text in the
   FTS5 tables.
2. **A `changeSensitivity`-triggered purge**, extending
   [ADR-0024](0024-a-purge-is-a-build.md) decision 5's trigger set.
3. **The read-side predicate**, adding the axis to `_scope`.
4. **The two-corpora equality suite parametrized over the sensitivity axis** —
   an index holding the withheld rows and an index that never held them must
   return the same response to the same query.

Parts 1 and 2 are not index-side detail. Part 2 is an application-layer change
to the migration engine's withdrawal set, and under ADR-0024 the purge it
triggers copies the published build and deletes rows from the copy rather than
re-deriving.

### Deliberately left open

This ADR does not settle, and **must be amended when #119's implementation
does**:

- **The entitlement model.** What makes a row withheld-by-sensitivity in a
  single-user loopback daemon? Caller identity does not exist — the daemon
  authenticates a bearer token, not a person. Candidates include a per-call
  scope parameter, per-project configuration, or a declared operator profile.
  Each has a different disclosure surface, and choosing between them is design
  work this ADR is not the place for.
- **Exclusion versus gating.** Whether withheld-by-sensitivity rows are kept out
  of the index entirely or indexed and gated at read time. Part 4 constrains
  both, but does not choose between them.

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
| **Ship the read-side predicate alone** | Two measured defects. (a) The index still holds the withheld text, so BM25 collection statistics computed over the whole index file continue to price the visible rows — T-17a's mechanism, moved from the status axis to the sensitivity axis; the threat model records T-17a's closure as covering "the status axis only" for exactly this reason. (b) `migration_engine.py` excludes `changeSensitivity` from the purge set on the recorded ground that the stale column "is read by no gate before #119". The moment a gate reads it, that exclusion inverts into a defect: a document reclassified `internal → restricted` keeps clearing the gate under its stale label until the next manual `index build`, and there is no canonical re-check for sensitivity of the kind `CanonicalVisibility._may_surface` performs for status. |
| **Keep sensitivity a label until after 1.0** | A published governance label that no query reads is, by this project's own grading, a claim that misleads a security decision. Deferral was defensible while one human at a CLI was the only writer; Phase B multiplies the writers, and a stable release turns the current meaning of every corpus into a compatibility promise. |
| **Remove the label until it can be enforced** | `sensitivity` is already in the published wire contract and carries real information a caller may act on. Removing it is a breaking change to that contract, it deletes information the corpus genuinely holds, and it buys no safety — the underlying content is served either way. It trades a misleading claim for no claim at all, at the cost of a break. |
| **Enforce tenant and ACL group at the same time** | Not rejected, deferred within the same issue: those axes are refused at write time today, so they hold no content and nothing routes on them. Sensitivity is the axis whose values actually vary, which is what makes it the one that misleads. |

## Compliance

**Nothing enforces this ADR today. Every item below is owed.** The owner of all
of them is [#119](https://github.com/theurian/theurian/issues/119), and the
milestone is Phase 0 — before 0.1.0 stable.

Still owed, with the part of the decision each discharges:

- **Part 1** — a test that a build over a corpus containing a withheld-by-
  sensitivity document writes no chunk row a gated query could reach. No such
  test exists; `IndexBuilder._build`'s only scope gate is status.
- **Part 2** — a test that a `changeSensitivity` migration publishes a purged
  build in the same `migrate apply`, the way a withdrawal already does. The
  current behaviour is the opposite and is deliberate:
  `revisions_to_purge` excludes the operation, with the reasoning in its
  docstring.
- **Part 3** — a test that `_scope` refuses a withheld sensitivity. Today
  `_scope` emits two predicates and a test pins the gate's *call sites*
  (`tests/unit/test_gate_call_sites.py`), not its axes.
- **Part 4** — the existing two-corpora equality tests, parametrized over the
  sensitivity axis. They exist for status and are the model to follow; nothing
  runs them for sensitivity.

Until all four land, `system.capabilities` must not advertise sensitivity
enforcement in any form, and SECURITY.md's statement that sensitivity is "a
published label, not a control" stays as written — it is currently true, and
this ADR is the record that it is not meant to stay true.

**When #119's implementation settles the entitlement model or the
exclusion-versus-gating question, this ADR is amended with what was chosen and
why** — the reasoning is the artifact, and an ADR that silently acquires an
answer it once called open is worse than one that never named it.
