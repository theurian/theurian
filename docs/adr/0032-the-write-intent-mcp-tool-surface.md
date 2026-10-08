# ADR-0032: The write-intent MCP tool surface — two tools onto one `ProposalService`, and `writeTools` flips with the first registration

- Status: proposed
- Date: 2026-09-12
- Deciders: Theurian maintainers
- Requirements: FR-I3, SEC-11, SEC-12, SEC-17, INV-7, INV-8, T-3, T-12
- Situates against [ADR-0013](0013-ai-writes-produce-proposals.md) (the tools
  this ADR opens are the ones it named), [ADR-0026](0026-evidence-plane-not-control-plane.md)
  (capability honesty — a flag may not be flipped ahead of the feature),
  [ADR-0027](0027-accept-validates-before-it-moves.md) and
  [ADR-0028](0028-a-local-proposal-is-a-different-directory.md) (the accept path
  and the local location these tools inherit), [ADR-0031](0031-mcp-input-is-schema-validated-in-middleware.md)
  (SEC-12, this surface's precondition) and
  [ADR-0034](0034-migrate-apply-enforces-the-merge.md) (T-15, the other
  precondition)

**This ADR records a decision and ships no code.** No tool registers, no schema
is written, no flag moves, no test lands; the diff is confined to `docs/`. What
each implementation slice owes is named in *Compliance*.

**Every repository fact below was measured on 2026-09-12 against `be977ea7`**,
which is reachable from `origin/main`. Where a fact is a *population*, the key is
stated beside the number so a reader can attack the key and not only the count.

> **Amended by GHSA-v2qg-23fc-7fqp (2026-10-01).** Implementing decision 1 as
> written gave `knowledge.proposeChange` a way to lower an item's sensitivity,
> the effect decision 3 meant to keep off this surface. It also gave it, and
> `theurian propose`, a way to readmit a retired item, the effect decision 3
> pulled `restoreItem` to keep off it. The fix moves this
> record in the places listed below. The block sits here, above *Context*,
> because those places span *Decision*, *Consequences* and *What this does not
> close*; the paragraphs themselves stand as they were accepted.
>
> **What implementing it revealed.** A content update is not neutral about an
> item's labels. `KnowledgeItem.with_revision` (`domain/knowledge.py`) gives the
> item the status, owner, trust level, sensitivity, namespace and kind of the
> revision an `upsertRevision` lands, so a revision that leaves a label out does
> not leave the item's label alone. The loader reads an absent `sensitivity` as
> `internal` and an absent `trustLevel` as `unverified`, and the drafter wrote an
> absent `namespace` as the item id's own prefix. Under decision 1's rows:
>
> - an update that omitted `sensitivity` turned a `confidential` item
>   `internal`, and the migration a reviewer approved said nothing about it.
>   Over the wire this needs the item in the caller's view, so a serving ceiling
>   raised to `confidential`;
> - an update that named `sensitivity: public` lowered an `internal` item, in
>   the default configuration;
> - `review.generateKnowledgeCandidate` named `internal` on every update;
>   `theurian propose` drafted the same way as `knowledge.proposeChange`; and
>   `theurian propose accept` landed all of it;
> - every revision the drafter wrote said `status: approved`, so a merged
>   update put a `deprecated`, `superseded` or `rejected` item back in the
>   served set, `rejected` included: through `theurian propose` for any retired
>   item, and over the wire for one a `createItem` made and nothing revised,
>   whose first revision is the call the wire lookup's `None` lets through.
>
> What moves:
>
> - **Decision 1, the `namespace?` and `trustLevel?`/`sensitivity?` rows.**
>   "Absent defaults to the item id's own" and "absent means 'not stated'" now
>   hold only for **an item the drafter's lookup does not find** — on the CLI,
>   one no landed migration creates; over the wire, also one the caller may not
>   see (the decision 6 item below) — where there is no label to keep and #249's
>   reasoning stands. For an item it finds, an omitted `sensitivity`,
>   `trustLevel` or `namespace` is the item's current value, and the drafter
>   writes it into the migration, so the reviewer reads the label the item will
>   hold. That is every update, and also the first revision of an item a
>   `createItem` made and no `upsertRevision` has revised: such an item already
>   holds labels, and its first revision would replace them with the loader's
>   defaults exactly as an update would. That first revision still takes no
>   `expectedRevision`, because `_check_expected_revision` keys on the revision
>   and not on the item, so the `expectedRevision?` row stands as written. Of
>   the three labels, only the namespace rewrite was visible in the diff before;
>   the other two changed silently. A label the caller names still wins, with
>   the one exception in the next item.
> - **Decision 3's reason for pulling `changeSensitivity`.** "V1 does not carry
>   it" was true of `knowledge.generateMigrationDraft` and false of the surface:
>   `knowledge.proposeChange` reached the same effect through `upsertRevision`'s
>   `metadata`, the operation decision 3's table classifies only as *Moves
>   content*. The reason stands — a declassification widens who may read an item
>   by an amount the reviewer cannot see — and it now holds on every proposal
>   path. `ProposalService.draft` — which `theurian propose`,
>   `knowledge.proposeChange` and `review.generateKnowledgeCandidate` call;
>   `generateMigrationDraft` drafts through `draft_from_document` and carries
>   neither `changeSensitivity` nor `upsertRevision` — refuses a draft that
>   names a sensitivity below the current one of an item its lookup returns,
>   with a remedy naming the
>   one declassification path: a hand-authored `changeSensitivity` migration
>   under `.theurian/migrations/`. The CLI's lookup returns any item the landed
>   set holds; the MCP tools' returns one the caller may see (the decision 6
>   item below). Over the wire an item outside the caller's view is answered as
>   an id nothing created is, so a lower sensitivity named for it is not refused
>   at draft, and the `accept` floor below is what refuses it.
>   `review.generateKnowledgeCandidate`'s
>   `internal` is the candidate type's default, not a statement, and its request
>   says so (`ProposalRequest.sensitivity_is_default`, which only that path
>   sets): for an item the lookup finds, the drafter drops it, so the candidate
>   inherits the item's sensitivity rather than meeting the refusal — on an
>   update, where the request names none anyway, and on the first revision of a
>   create-only item, where it names `internal`
>   ([ADR-0033](0033-knowledge-candidate-generation.md)'s matching amendment). A
>   sensitivity named through `knowledge.proposeChange` or `theurian propose` is
>   compared. `theurian propose accept` refuses any proposal after whose replay
>   with the landed set an existing item holds a lower sensitivity than the
>   landed set alone gives it, whichever operation moved the label. That covers a
>   proposal drafted before this fix with the key omitted, one whose
>   `sensitivity` was edited by hand, one whose migration sorts after a later
>   reclassification, and one carrying a `changeSensitivity` of its own. The
>   comparison needs the landed set to replay on its own. When it replays only
>   together with the proposal — as when the proposal creates an item that a
>   landed `changeSensitivity` sorting after it reclassifies — `accept` has no
>   labels in place to compare against, so it refuses with that reason and moves
>   nothing. The refusal carries the replay engine's own words, and its remedy
>   is to read what they name in `.theurian/migrations/` and make the set replay
>   on its own — `theurian migrate apply` runs the same replay;
>   `theurian migrate validate` still reports such a set valid, because its
>   verdict does not rest on a replay, and its `permissiveMovesUnavailable` says
>   the set does not replay
>   (`test_permissive_move_report.py::test_validate_on_a_set_that_does_not_replay_stays_valid_and_says_the_report_is_unavailable`)
>   — then accept again. A
>   lowering's remedy names one step per cause: `theurian migrate apply` when the
>   served state lags the landed migrations, which is how a draft made over the
>   wire comes to carry a stale label; otherwise a new draft naming the item's
>   current sensitivity or omitting it; and, to lower the label, the
>   hand-authored `changeSensitivity`. It also says that once that migration
>   lands, the update is drafted again with `theurian propose` rather than this
>   proposal accepted again, because the proposal's migration id predates the
>   migration and replays before it. Review found the lowering remedies without
>   that clause, which the readmission remedy already carried; it is now
>   `item_labels.DRAFT_AGAIN_CLAUSE`, in `ACCEPT_LOWERING_REMEDY` and in
>   `_floor_refusal`'s remedy for a proposal that readmits every item it lowers
>   (`test_floor_refusal_remedy.py::test_every_remedy_that_sends_the_reader_to_a_migration_says_to_draft_again_not_accept_again`).
>   Since the re-check's fix wave below, the clause also says to edit that
>   migration's id into the new draft's `dependsOn`: at first only if that
>   migration declared `dependsOn`, and since the fix wave's id-order finding
>   whatever it declares, because only `dependsOn` places the new draft after it.
> - **Decision 3's reason for pulling `restoreItem`.** "`restoreItem` is
>   pulled, because it readmits from any status" — `rejected` included, with no
>   transition check — and pulling it kept readmission off the surface. That
>   was true of `knowledge.generateMigrationDraft` and false of the surface:
>   `knowledge.proposeChange` reached readmission through `upsertRevision`'s
>   `metadata.status`, which the drafter always wrote as `approved`, for a
>   create-only retired item, and `theurian propose` reached it for any retired
>   item. The reason stands, and it now holds on every proposal path.
>   `ProposalService.draft` refuses to draft for an item its lookup returns
>   retired (`_refuse_a_retired_item`), in one constant message that names no
>   status, with a remedy naming the one readmission path: a hand-authored
>   `restoreItem` migration under `.theurian/migrations/`. Only the CLI's lookup
>   returns a retired item, so the refusal reaches `theurian propose` and
>   `theurian okf import`. An import never names `expectedRevision`, so the
>   cheap revision check refuses a retired item with a revision first; on that
>   refusal path one replay then finds it retired, and the retired refusal is
>   what `draft` raises, as for `theurian propose` with a missing or stale
>   `--expected-revision` (the decision 6 item below). Measured on 2026-10-02 by
>   a spy on `ProposalService.draft` under `okf import`; the import's refusal
>   record names only `ProposalError`, so its output is the same either way. A
>   create-only retired item meets the check without that first refusal. Over
>   the wire the refusal is
>   unreachable by construction: decision 6's lookup answers `None` for a
>   retired item, so the tools answer it as an id nothing created, and `accept`
>   is where a draft for it is refused. `theurian propose accept` refuses any
>   proposal after whose replay with the landed set an item the landed set
>   alone leaves non-surfaceable — `deprecated`, `superseded` or `rejected` — is
>   surfaceable — `approved`, `draft` or `proposed` — whichever operation moved
>   the status. Both sides are judged by
>   `may_surface(status, include_unapproved=True)`, the read gates' own
>   predicate under its widest flag, in
>   `application/item_labels.py :: readmitted_items`, so the floor and the gates
>   cannot disagree about what is served. That covers an update with `approved`
>   drafted before this check, one restated by hand to `draft` or `proposed`, a
>   draft whose migration replays after a landed deprecation that sorts before
>   it, the
>   wire's draft for a create-only retired item, and a proposal carrying a
>   `restoreItem` of its own. Its remedy names the hand-authored `restoreItem`.
>   Moving `draft` or `proposed` to `approved` through a proposal stays allowed,
>   and not as a special case: both are surfaceable on that same predicate, so
>   nothing is readmitted, and the merge of the proposal is the approval.
> - **What `accept` lands, in two halves (H3).** `accept` has no operation write
>   set: it moves whatever operations a proposal's migration carries. Because
>   both floors compare the state a replay leaves rather than the operations a
>   document names, a proposal carrying a `changeSensitivity` that lowers an
>   item, or a `restoreItem` that readmits one, is refused. That is a
>   consequence of comparing post-state, not a widening of what `accept` checks:
>   neither floor reads which operations a proposal carries, so one that moves
>   no gate label input in the permissive direction passes both. Which operations a
>   proposal may carry at all — the write set — stays with #841's A1 slice.
> - ***Consequences*, *Negative*, the third item**, says an agent that reaches
>   for `changeSensitivity` "has no tool to be redirected to". Until this fix
>   the effect had one: the tool `generateMigrationDraft`'s own `upsertRevision`
>   refusal redirects to. It has none now, and the sentence is true as written.
> - ***What this does not close*, item 4**, says widening the surface to
>   `changeSensitivity` needs its own recorded justification. The effect was
>   reachable without that widening and without that justification. From this
>   fix on no write-intent tool lowers a sensitivity, which is the premise the
>   item rests on; the justification is still unwritten.
> - **Decision 6 covers the new refusal, through a renamed lookup.**
>   `CurrentRevisionLookup` is now `CurrentItemLookup`
>   (`application/item_labels.py`): one read answers both the current revision
>   and the labels a draft inherits, so the two cannot come from different
>   states. The name `CurrentRevisionLookup` returns for something narrower: an
>   optional, revision-only lookup that the CLI and the OKF import inject so a
>   missing or stale `expectedRevision` is decided before `CurrentItemLookup`
>   replays the landed set. When that check refuses, `draft` makes one replay,
>   on the refusal path only, to tell a retired item apart: its remedy, "Pass
>   --expected-revision", is a step the retired refusal then blocks, so a retired
>   item gets the retired refusal instead. A draft the check passes makes no
>   replay beyond its own.
>   `test_candidate_default_flag_and_revision_precedence.py::test_an_update_without_an_expected_revision_is_refused_and_replays_only_for_retirement`
>   and `::test_an_update_with_a_stale_expected_revision_is_refused_and_replays_only_for_retirement`
>   assert that `current_item_in` is called once, for the item, on each refusal,
>   and `::test_an_update_with_the_current_expected_revision_reaches_the_replay`
>   once on a draft that passes;
>   `test_okf_import_composition_root.py::test_a_concept_for_an_item_with_a_revision_is_refused_and_replays_only_to_check_retirement`
>   asserts the same single call under `okf import`; and
>   `test_retired_item_remedies.py::test_a_retired_item_drafted_without_expected_revision_gets_the_retired_refusal`
>   asserts exit 2, the retired message, a remedy naming `restoreItem` and not
>   "Pass --expected-revision", and nothing written. `draft` then checks the
>   revision again against `CurrentItemLookup`'s answer, so the revision it
>   accepts and the labels it inherits still come from one read; the MCP root
>   injects none. The MCP
>   closure decision 6's *Landed* entry names as
>   `register._draft_only_proposals.current_revision` is now
>   `register._draft_only_proposals.current_item`, and it answers an item with
>   no revision too, with no revision id, so a create-only item in the caller's
>   view keeps its labels over the wire as well. The lowering refusal runs only
>   for an item that lookup returned, so a withheld id is answered as an absent
>   one is — refused for an `expectedRevision` it names, drafted when it names
>   none — and the refusal's text is a constant that names no sensitivity
>   level. The constant does not make the refusal silent about the level: refused or
>   drafted is one bit, below the item's class or not, about an item in the
>   caller's view — the population this decision's bind deliberately leaves out.
>   For an item with a revision the bit adds nothing, since `knowledge.get`
>   publishes its `sensitivity`. For a create-only item it does: `knowledge.get`
>   answers such an item as not present, in the same text it gives for an id
>   nothing stored, while `knowledge.proposeChange` refuses a lowering for it and
>   drafts the same call for an absent id.
> - **Decision 6's table, its first row.** The refusal quoted there as
>   "`<itemId> does not exist yet, so its first revision cannot replace <rev>`"
>   now reads
>   "`<itemId> has no current revision, so --expected-revision <rev> has nothing to replace.`",
>   with the remedy "Drop --expected-revision to draft its first revision, or
>   correct --item-id." It is one constant for an id nothing created, an id
>   outside the caller's view and a create-only item, so what it publishes is
>   *no current revision*, not non-existence: a create-only item exists, and
>   dropping the flag drafts its first revision. Unlike the lowering refusal
>   above, it does not tell an in-view create-only item from an absent id.
>   `test_create_only_item_labels.py::test_an_mcp_expected_revision_on_an_in_view_create_only_item_matches_an_absent_id`
>   sends one `expectedRevision` for an `internal` create-only item under the
>   default ceiling and for an id nothing created, and asserts that the item's
>   refusal is that text and remedy and that the two whole responses are equal.
>   That the item is in view there is held by
>   `::test_an_mcp_draft_omitting_labels_for_a_create_only_item_drafts_its_create_item_labels`,
>   which drafts on the same fixture and stages its `security` namespace, not
>   the id's `architecture`. For an id outside the caller's view,
>   `::test_the_same_call_on_a_corpus_without_the_item_gets_the_same_bytes` and
>   `::test_a_create_only_item_above_the_ceiling_answers_like_an_id_that_never_existed`
>   under *What holds it* send an `expectedRevision`, assert that the text
>   carries "has no current revision", and compare the raw responses of two
>   corpora.
>
> **The closure.** The read gates take two label inputs from an item: its
> status, through `may_surface`, and its sensitivity, through `may_disclose`.
> Revision presence is read beside them and is not a label input, by design:
> the read paths `CanonicalVisibility._may_surface`, `register.knowledge_get`
> and `mcp/search.py :: _scan`, and the index builder and the OKF export, each
> skip an item whose `current_revision_id` is `None`, inline. Only a first
> revision moves that read, and what it then serves is the content its own
> proposal carries, which the merge of that proposal approves. Two reads look
> like further inputs and are not: `cli/index_commands.py :: _indexable_items`
> repeats the builder's rule inline to count what a build was offered, and of
> an item's labels reads only status and sensitivity; and
> `validity.contains(asOf)`, in `_scan` and in the ranked path's `at_moment`,
> is a filter the caller chooses, which drops rows the two gates admitted and
> never adds one. This advisory is one class, a proposal that moves a label
> input in the permissive direction — status from non-surfaceable to
> surfaceable, or sensitivity to a lower class. Three controls hold it. The
> draft refusals keep such a proposal from being written for an item the
> drafter's lookup returns. The two `accept` floors refuse one against the
> migrations landed when it is accepted, and they close the class by the
> gates' own input set rather than by a list of operations: each compares the
> state a replay leaves for every item the landed set holds, so an operation, a
> document key or an order among the landed migrations that nobody listed meets
> the same comparison. The permissive-move report names what the floors cannot
> see, a withdrawal merged after the accept that the accepted update then
> undoes, and `accept` refuses a proposal that would add a row to that report
> or re-attribute one it already holds (below). Readmission has one path, a
> hand-authored `restoreItem`
> migration under `.theurian/migrations/`, as declassification has one, a
> hand-authored `changeSensitivity`. Trust level, namespace, owner and kind are
> not gate label inputs, so they are outside the class: an `upsertRevision`
> still sets them, neither floor compares them, and they are residuals of this
> fix, recorded here. What holds each floor is below.
> `tests/unit/test_gate_item_inputs.py` holds the premise that the gates read
> only these two label inputs, as far as its body reaches: it asserts that the
> public `may_*` callables `domain/enums.py` defines, a cache-wrapped one
> included, are exactly `may_surface` and `may_disclose`, that the first
> parameter of each is annotated `KnowledgeStatus` and `Sensitivity`
> respectively, and that `lowered_sensitivities` reads `sensitivity` and
> `readmitted_items` reads `status`, and no other `ItemLabels` field, as
> attributes in their own source. It reads names, annotations and attribute
> access, and no gate parameter after the first, so a gate that decides inline,
> outside `domain/enums.py` or under a name not starting `may_`, or one that
> reads an item field through `getattr` or a helper, is outside it — the inline
> revision-presence read above among them, which is why that read is named here
> rather than left to the test. `tests/unit/test_gate_call_sites.py` pins every
> call site of `may_surface` and `may_disclose`.
>
> **What the floors cannot see, and the third control.** The floors compare
> once, at `accept`, and implementing them revealed a case that comparison
> cannot reach. An approved item is updated through
> `theurian propose --expected-revision`, and the update is accepted and
> applied. A hand-authored `deprecateItem`, or a `changeSensitivity` to
> `confidential`, whose id sorts before the update's migration — authored
> first, merged after the accept — then lands. A full replay runs the
> withdrawal first and the accepted upsert's `status: approved` and
> `sensitivity: internal` second, so the withdrawal is nullified and nothing
> refuses the set.
> `test_permissive_move_report.py::test_an_update_accepted_after_a_withdrawal_that_sorts_first_is_reported_undoing_it`
> builds both faces and asserts that `migrate validate` and `migrate apply` exit
> 0 and that the item ends `approved` and `internal`; on 2026-10-02 an
> in-process probe outside the suite found `knowledge.get` serving the updated
> body, for both faces, to a caller under the default ceiling. It is graded
> CRITICAL, on a corrected grading anchor: a merge approves only what its diff
> shows, and a merge that cannot show the conflict is not an approval of the
> outcome. Neither the withdrawal's diff nor the update's shows the other.
>
> **Decided: a report, not a refusal.** Refusing such a set would stop
> histories that already apply, so `migrate validate` and `migrate apply` print
> `permissiveMoves` and neither exit code moves. A replayed `upsertRevision` is
> reported for a field only when both of two clauses hold: (1) the upsert
> itself loosened the field, compared with the state just before it; and (2)
> its migration left the item looser than it found it, comparing the
> migration's end with its start. "Looser" is the floors' own test — for status,
> `may_surface(…, include_unapproved=True)` going from false to true; for
> sensitivity, a lower class by `DISCLOSURE_ORDER` — decided at
> `application/permissive_moves.py :: _loosens`, which
> `test_gate_call_sites.py` registers in `STATUS_GATE_READER_SITES`: it decides
> what the report returns, and the upsert lands whatever it decides.
> **Amended 2026-10-08:** since GHSA-wwq9-p8wq-5m68 `_loosens` also decides
> `accept`'s end-state refusal
> (`application/permissive_moves.py :: loosened_after`), so it decides whether
> a proposal lands, not only what the report returns, and
> `test_gate_call_sites.py` registers it in `STATUS_GATE_WRITER_SITES`. An item
> absent at its migration's start is never compared, and only upserts are
> reported, never the sanctioned `deprecateItem`, `restoreItem` and
> `changeSensitivity`. A row carries `migrationId`, `itemId`, `field`, `before`
> and `after` (the field at that migration's start and end), `undoes` (the
> migration that, before it, last changed whether the item may surface, or its
> class) and `kind` — `undoes` when that change tightened the field, `lowers`
> otherwise, by the definition below. `migrate validate` reports the whole set
> every time, from a throwaway replay used only for the report, and its `valid`
> verdict and exit code do not depend on that replay; when the replay fails,
> `permissiveMoves` is `null` and `permissiveMovesUnavailable` carries the
> engine's own words. `migrate apply` reports what that run applied.
> `propose accept` does not print it, though it reads it to refuse (below),
> and no file under `mcp/` or `schemas/mcp/` contains the key: it is the
> operator's own replay, not a served path.
> [The migration format](../protocol/migrations.md#permissive-moves-are-reported-not-refused)
> documents it, each sentence with the test or measurement that holds it, the
> tests in `tests/integration/test_permissive_move_report.py`,
> `tests/integration/test_accept_refuses_a_reported_upsert.py`,
> `tests/integration/test_accept_never_introduces_a_report_row.py`,
> `tests/unit/test_permissive_move_writer_property.py`,
> `tests/unit/test_floor_refusal_remedy.py` and
> `tests/unit/test_introduced_moves.py`. They collect 45 tests from 33
> functions, 15 from 14, 13 from 13, 52 from 2, 14 from 5 and 13 from 8, by
> `pytest --collect-only -q` on 2026-10-03 after the fix wave's id-order
> finding below, a function being a top-level `def test_`. This sentence said
> 11 from 10, 12 from 12 and 7 from 3 for the second, the third and the fifth
> after the re-check's fix wave, the same day; 9 from 8 and 7
> from 7 for the second and the third after the second widening, the same day;
> 4 from 4 and 11 from 7 for the third and the sixth after the first widening,
> on 2026-10-02; 41 from 32 and 6 for the first two before the granularity fix
> below, and 28 for the first before those, each of which no longer matched;
> the first widening added the third and the sixth file.
> **Amended 2026-10-06:** a sanctioned write is now reported, as
> `kind: "reorders"` (GHSA-wwq9-p8wq-5m68). The sentence above kept the
> sanctioned operations out as reviewed intent, which holds only in id order:
> a migration declaring `dependsOn` replays after every one declaring none, so
> a smaller-id lowering or `restoreItem` undid a larger id's raise or
> deprecation with no row. The rule for a `reorders` row is stated under that
> name in [the migration format](../protocol/migrations.md#permissive-moves-are-reported-not-refused).
> The report judges each migration against the attribution there; `accept`'s
> end-state refusal judges the proposal's own field where the replay ends. By
> attribution `accept` would pass a larger-id landed loosening, which takes the
> attribution; by end state the report would name every lowering that follows
> a raise in id order, which the sentence above leaves unreported.
> Decided 2026-10-06: `migrate validate` and `migrate apply` report a
> `reorders` row and refuse nothing, because the report never moves an exit
> code (*Decided: a report, not a refusal*, above) and a refusal would stop
> histories that already apply; an ordering fix is to follow in its own ADR.
> **Amended 2026-10-08:** no such ADR exists;
> [#897](https://github.com/theurian/theurian/issues/897) records the option
> and its costs, and the GHSA-wwq9-p8wq-5m68 amendment before *Context* why it
> was not taken.
>
> **`kind` is decided by the change's effect, not its operation.** The report
> first decided it by operation type: a `deprecateItem` or `changeSensitivity`
> was a withdrawal and every upsert was not. Review found that this misfiled
> two races. An accepted update that undid an in-place `upsertRevision`
> withdrawal — the current revision re-declared `rejected`, `superseded` or
> `deprecated`, or at a higher class, the shape ADR-0024 decision 5 writes —
> read `lowers`, which hid the reviewed withdrawal the report exists to
> surface. And a lowering after a `changeSensitivity` that declassified the
> item read `undoes`, naming a tightening that never happened. A change is now
> a withdrawal when it tightened the field — status from a value
> `may_surface(…, include_unapproved=True)` admits to one it does not,
> sensitivity raised by `DISCLOSURE_ORDER` — which is `_loosens` read the other
> way round. A write that does not change the field does not replace the
> recorded one; what counts as a change is the next paragraph. A `createItem`
> is never a withdrawal, and neither is any write in the migration that
> created the item: that is the item's first labelling, at the granularity at
> which the report never compares a new item. Held by
> `test_permissive_move_report.py::test_an_in_place_withdrawal_by_upsert_is_what_an_accepted_update_is_reported_undoing`
> (an accepted update, then an in-place upsert sorting before it that states
> `rejected`, `superseded` or `deprecated`, or `confidential` over `internal`;
> each row `kind: undoes`, naming the in-place migration),
> `::test_a_declassification_is_not_a_withdrawal_so_a_further_lowering_is_reported_as_lowers`
> (`confidential`, a `changeSensitivity` to `internal`, then an upsert to
> `public`: `kind: lowers`),
> `::test_a_restated_status_does_not_replace_the_withdrawal_that_set_it` (a
> deprecation, an upsert restating `deprecated`, then one stating `approved`:
> the row names the deprecation's migration, `kind: undoes`),
> `::test_a_create_item_is_never_a_withdrawal_so_a_later_lowering_is_reported_as_lowers`
> (a `createItem` stating `confidential`, then an upsert to `internal`:
> `kind: lowers`), and
> `::test_a_hand_authored_upsert_lowering_an_earlier_upserts_sensitivity_is_reported_as_lowers`,
> whose item is created and stated `confidential` by an upsert in one
> migration and later lowered: `kind: lowers`.
>
> **The effect is judged at the floors' granularity, not the value's.** The
> previous closure measured effect at value granularity, which is the wrong
> instrument for status. It decided "unchanged" by the label's value and
> "withdrawal" by the floors' predicate. A status write between two retired
> values — `deprecated`, `superseded`, `rejected` — changes the value and not
> the predicate, so it was recorded as the field's writer, not as a
> withdrawal, and displaced the real withdrawal before it: the accepted update
> that then readmitted the item read `kind: lowers`. Review met four faces: a
> `deprecateItem` then an in-place upsert to `rejected`; a `deprecateItem` then
> one to `superseded`; an in-place `rejected` then a `deprecateItem`; and one
> migration that deprecates and then supersedes. The third read `undoes` under
> the operation-typed rule, naming the `deprecateItem`, so the effect rule
> regressed it; the other three read `lowers` under both. Measured on
> 2026-10-02 by feeding the four write sequences to `LabelWriters` as it stood
> at each rule. A write now replaces the field's recorded writer only when it
> changes the field's predicate value — for status, whether
> `may_surface(…, include_unapproved=True)` admits the item; for sensitivity,
> its class — and it is a withdrawal only when that change tightened the
> field. Sensitivity never showed the defect: `DISCLOSURE_ORDER` is a total
> order, so every change of a sensitivity value is a change of class, and value
> and predicate granularity coincide there. That is stated here rather than
> left assumed, and the second property test below holds it. Held by
> `test_permissive_move_report.py::test_a_retired_to_retired_write_does_not_displace_the_withdrawal_an_update_is_reported_undoing`
> (an accepted update, then each face sorting before it; each row names the
> first write's migration, `kind: undoes`, from `migrate validate` and from
> `migrate apply`) and by `test_permissive_move_writer_property.py`, which
> feeds `LabelWriters` a first write and then a second for every ordered pair
> of values. Of the 36 status pairs, the second replaces the first as the
> writer exactly when it changes what `may_surface` answers, and is a
> withdrawal exactly when it tightened it
> (`::test_a_status_write_replaces_the_recorded_writer_only_by_changing_surfaceability`);
> of the 16 sensitivity pairs, exactly when the value changes, and a withdrawal
> exactly when it is raised
> (`::test_a_sensitivity_write_replaces_the_recorded_writer_only_by_changing_the_class`).
> Its first write always leaves a writer recorded, so it does not reach a field
> with none, one an earlier `apply` last set. There the rule does not hold: a
> write that changes the value is recorded even when the predicate does not
> move, as no withdrawal, so a readmission after a write between two retired
> statuses reads `kind: lowers` naming that write. Measured on 2026-10-02 by
> feeding `LabelWriters` the two upserts with no earlier write; no test drives
> it.
>
> **The recorded design limit: one migration is one reviewed diff.** A
> migration that withdraws and re-asserts an item, or lowers and then restores
> it, nets out and is not reported
> (`test_permissive_move_report.py::test_a_migration_that_withdraws_and_reasserts_an_item_in_one_diff_is_not_reported`,
> `::test_an_upsert_lowering_that_a_later_operation_restores_is_not_reported`).
> Its reviewer saw both operations in one diff, which is what the grading
> anchor asks of an approval.
>
> **Rejected predicates, each with the false positive it was measured to
> produce.**
>
> - *Compare each operation with the state just before it.* It reported 26
>   rows on this repository's own corpus, each a new item labelled in the
>   migration that creates it: a `createItem` with the defaulted `internal`,
>   then an upsert stating `public`, which is what
>   `theurian propose --sensitivity public` writes for a new item. A report
>   that fires on the maintainers' own history is noise nobody reads;
>   `::test_a_new_item_labelled_in_the_migration_that_creates_it_reports_no_move`
>   holds that shape at zero.
> - *Compare the upsert's result with its migration's start alone.* It
>   reported a migration doing `restoreItem` and then an upsert, which is the
>   migration `READMIT_REMEDY` leads its reader to
>   (`::test_a_restore_and_content_update_in_one_migration_reports_no_move`),
>   and one doing `changeSensitivity: public` and then an upsert raising the
>   item to `internal`
>   (`::test_an_upsert_raising_the_label_is_not_reported_after_the_migration_made_it_public`).
>   Clause (1) is what removes both.
> - *Keep the start-only compare and record its false row (O1).* Rejected:
>   Theurian's own remedy would then produce a permanent false `undoes` row.
>
> **An accept never introduces or re-attributes a report row.** This was first
> stated, and ruled, for the proposal's own row only, as "nothing accepted is
> reported at accept time"; the two widenings are recorded below. The same
> false row was
> still reachable another way. The floors compare where the landed set ends
> with where it ends with the proposal, so they pass a proposal whose
> migration replays before a landed one that re-sets what its upsert loosened.
> The readmission refusal's remedy produced one: it sent its reader to
> hand-author a `restoreItem` and stopped there, so the next step was to accept
> the same proposal again. Its migration id was minted at the draft and sorts
> before the restore, so its upsert takes the item from `deprecated` to
> `approved` before the restore re-sets it, and both replays end alike;
> accepted, it would be reported `kind: undoes` for as long as the migration
> exists. Ruled in two parts. `accept` now reads the report of the replay with
> the proposal, which the floors already run, and refuses, with exit 1 and
> nothing moved, a proposal whose own migration a row names; it runs after the
> two floors, so their refusals keep their text and order
> (`application/proposal_service.py :: _refuse_a_reported_upsert`). Its error
> names the proposal's migration, the item, the field, both values, and the
> landed migrations that replay after it and write that field of that item,
> read from their operations: `upsertRevision` and `createItem` write both
> labels, `deprecateItem` and `restoreItem` status, and `changeSensitivity`
> sensitivity. It first named every landed migration replaying after the
> proposal's, which sent the reader to review migrations with nothing to do
> with the refusal. Its remedy was then a fresh draft with `theurian propose`,
> said to get a later migration id, and deleting the stale proposal directory.
> Since the re-check's fix wave below it is routed by every kind the proposal
> carries and names `dependsOn: [<every landed migration the error names>]`,
> whatever those migrations declare: a later id is not what places the new
> draft after them. And `ACCEPT_READMISSION_REMEDY` now says that once the
> restore lands, the update is drafted again rather than accepted again, as
> the lowering remedies do (above), with `dependsOn: [<its id>]` edited into
> the new draft since the fix wave. The class is closed in three steps: a
> permissive move is refused at accept, reported after, and an accept never
> introduces or re-attributes a report row.
> `test_accept_refuses_a_reported_upsert.py::test_accepting_a_proposal_that_replays_before_a_landed_restore_is_refused_and_moves_nothing`
> drafts over MCP for a create-only `deprecated` item, has the first accept
> refused, lands a `restoreItem` and asserts that the second accept exits 1,
> with an error naming the proposal's migration id, the item, `status`, both
> values (`deprecated` and `approved`) and the landed restore's migration id,
> and saying it "replays before", a remedy naming `theurian propose` and
> reading "replays after `<restore>`", and nothing moved under the proposals,
> migrations or knowledge directories.
> `::test_no_row_names_the_migration_of_a_proposal_accepted_across_a_landed_restore`
> asserts that after that accept and a `migrate apply`, `migrate validate`
> carries no row for the proposal's migration;
> `::test_a_fresh_draft_after_the_restore_is_accepted_and_reports_nothing`
> followed the remedy as it then read: a fresh `theurian propose` draft,
> declaring no `dependsOn` and asserted to sort after a restore that declares
> none, is accepted and the report is empty. Since the fix wave's id-order
> finding below the remedy names `dependsOn`, and that test holds only a
> restore that declares none and sorts before the fresh draft;
> `::test_the_first_accept_of_a_proposal_for_a_retired_item_prints_the_draft_again_remedy`
> asserts that the first refusal's remedy names `restoreItem` and
> `theurian propose`, which the new refusal's remedy would fail had it run
> first; `::test_the_readmission_remedy_says_to_draft_again_after_the_restore_lands`
> reads the constant;
> `::test_the_refusal_names_only_the_landed_migrations_that_wrote_the_reported_field`
> lands one more migration after the restore, a `createItem` of another item
> or a `changeSensitivity` of the same one, and asserts that the migration ids
> in the error are exactly the proposal's and the restore's;
> `::test_accepting_a_proposal_while_another_migrations_row_exists_leaves_only_that_row`
> holds that a row the report already holds, naming another migration, does
> not refuse — with a race row for one item already in the report, a first
> revision drafted for a second item is accepted with exit 0, and after
> `migrate apply` the report holds that row and no other; and
> `::test_an_accepted_update_leaves_no_report_row_before_any_withdrawal_lands`
> is the control.
>
> *Rejected: re-minting the migration id at `accept`* so that it sorts after
> the landed set. The id is the proposal's provenance
> ([ADR-0013](0013-ai-writes-produce-proposals.md)), recorded in its evidence
> and named by its file, and rewriting it at `accept` is not a patch-shaped
> change.
>
> **Widened after the anchored release-cut pass, by the maintainer's ruling of
> 2026-10-02.** The refusal above keyed on the proposal's own migration, and
> the anchored pass found a proposal that adds a row naming a landed one. A
> `knowledge.generateMigrationDraft` call stages a `deprecateItem` for an
> approved item, minting its migration id; a content update drafted after it
> with `theurian propose` is accepted and applied; then the deprecation is
> accepted. Its migration replays before the update's, whose
> `status: approved` re-sets what it withdrew. That is the set the race above
> leaves, which
> `test_permissive_move_report.py::test_an_update_accepted_after_a_withdrawal_that_sorts_first_is_reported_undoing_it`
> holds ending `approved` with `migrate validate` and `migrate apply` at exit
> 0; its other face, a raise undone by an update restating the lower class, is
> reached the same way by a proposal carrying a `changeSensitivity` raise.
> Neither floor refused such an accept, since the item ends the same with the
> proposal and without it, and the own-row refusal did not, since the row
> names the update: on 2026-10-02 the withdrawal, raise and re-draft tests
> below, run against the source before the widening, each failed on
> `assert code == 1` with exit 0, and the same-item control passed. The
> withdrawal was accepted and undone, and only the report printed after it
> named the landed update as undoing it.
>
> The ruling widened the invariant: an accept never introduces a report row.
> `accept` refuses, with exit 1 and nothing moved, when the report of the
> replay with the proposal holds a row whose `(migrationId, itemId, field)`
> the report of the landed set alone does not
> (`application/permissive_moves.py :: introduced_moves`, called from
> `_refuse_a_reported_upsert`). A row under a held key is the same row,
> whatever `before`, `after` or `kind` the replay with the proposal reads for
> it. **The baseline is the landed-alone report, not an empty one, because a
> row already in the history, naming the same migration in `undoes`, must
> never block an accept.** The `undoes` condition is the second widening's,
> below. That row is not this accept's, and a project holding one, the
> original defect's victims included, must still accept a proposal that leaves
> it as it is. **Amended 2026-10-05:** this holds for the report check's baseline;
> the end-state refusal (`_refuse_a_landed_overwrite`, after this check) can still refuse a proposal that leaves a held row as it is
> (`test_accept_never_introduces_a_report_row.py::test_a_stale_withdrawal_minted_before_a_landed_reapproval_is_refused`).
> With an empty baseline `accept` refuses both controls below:
> measured on 2026-10-02 by running them against the source with the baseline
> replaced by an empty tuple. The baseline is the report of the landed-alone replay both floors
> already compare against, which `_refuse_an_effective_lowering` now returns,
> so the widening adds no replay; and the check stays where the own-row check
> was, after both floors. The proposal's own row keeps the error and remedy
> above. A landed migration's row is refused with "Accepting this proposal
> would make the landed migration `<id>` move `<item>` `<field>` from
> `<before>` to `<after>`, undoing what this proposal sets: this proposal's
> migration replays before it." Its remedy was then "Nothing has moved. Draft
> this change again with `theurian propose` so it gets a later migration id and
> replays after `<id>`. Then delete `<proposal dir>/`."; the re-check's fix
> wave below routes it by every operation kind the proposal carries and names
> `dependsOn: [<id>]` in every route, since no id order is what places the new
> migration after the landed one. Re-minting the id stays rejected, for the
> reason above.
>
> `test_accept_never_introduces_a_report_row.py::test_a_withdrawal_minted_before_a_landed_update_is_refused_and_moves_nothing`
> builds that face on an `approved`, `internal` item and asserts exit 1, an
> error naming the update's migration id, the item and `status` and reading
> "from deprecated to approved", a remedy naming the update's migration id and
> not `theurian propose`, with no "accept" followed by "again" within one
> sentence, nothing moved under the proposals, migrations or knowledge
> directories, and, after `migrate apply`, the item `approved` and an empty
> report.
> `::test_a_sensitivity_raise_minted_before_a_landed_update_is_refused_and_moves_nothing`
> hand-edits a staged `changeOwner` into a `changeSensitivity` to
> `confidential`, lands an update naming `internal`, and asserts the same of
> `sensitivity`, the error reading "from confidential to internal", with the
> item `internal` after `migrate apply`.
> `::test_the_withdrawal_drafted_again_after_the_update_is_accepted_and_takes_effect`
> followed the remedy as it then read: a deprecation drafted with
> `knowledge.generateMigrationDraft` after the update, declaring no
> `dependsOn` and asserted to sort after it, is accepted with exit 0, and after
> `migrate apply` the item is `deprecated` and the report empty. Since the fix
> wave's id-order finding below the remedy names `dependsOn`, and that test
> holds only an update that declares none and sorts before the fresh draft. The
> baseline's two controls:
> `::test_a_proposal_on_the_item_of_an_existing_row_that_leaves_the_row_alone_is_accepted`
> reports a race row for the item, accepts a `changeOwner` of that same item
> with exit 0, and asserts after `migrate apply` the report it had before; and
> `test_accept_refuses_a_reported_upsert.py::test_accepting_a_proposal_while_another_migrations_row_exists_leaves_only_that_row`,
> above, is the other-item case. `tests/unit/test_introduced_moves.py` feeds
> `introduced_moves` held and union rows and asserts: an own row and a landed
> row the held rows lack are introduced; a row under a held key is not,
> identical or with other `before` and `after` or another `kind`; a row
> differing from a held one in migration, item or field is; an
> empty union introduces nothing; and the result keeps the union's order. The own-row
> refusal's text through the widening is
> `test_accept_refuses_a_reported_upsert.py::test_accepting_a_proposal_that_replays_before_a_landed_restore_is_refused_and_moves_nothing`,
> above.
>
> **Widened again on 2026-10-03, as inside the maintainer's earlier ruling.**
> The three-field key let an accept re-attribute a row the landed set already
> reports: the same `(migrationId, itemId, field)`, naming another migration
> as what it undoes. Both faces start from an `approved`, `internal` item, a
> staged proposal P, an update U drafted after P with `theurian propose` and
> accepted, and a migration D that sorts before P and lands after U's accept:
>
> - sensitivity: D raises the item to `confidential`, P to `restricted`, and U
>   names `internal`; U's row, `confidential` to `internal` undoing D, becomes
>   `restricted` to `internal` undoing P, so P's raise is overwritten;
> - status: D deprecates the item, and P is a `restoreItem` then a
>   `deprecateItem`; U's `deprecated` to `approved` row moves from undoing D to
>   undoing P, its `before` and `after` unchanged.
>
> On 2026-10-03, with the three-field key patched back in-process, `accept`
> exited 0 for both, and after `migrate apply` the item was `internal` and
> `approved` respectively and U's row named P. The key is now
> `(migrationId, itemId, field, undoes)`, so both are refused with exit 1,
> nothing moved, and the landed-row error above, with the remedy the
> re-check's fix wave below routes. It takes `undoes`
> and not `before` because `undoes` is predicate-granular (above): it names the
> last write that changed whether the item may surface, or its class. `before`
> is the field's value at the migration's start: the status face leaves it
> unchanged, and a write between two retired statuses moves it without
> changing what either gate answers. The control is a deprecation of an item
> already deprecated: it changes no predicate, so U's row keeps naming D and
> the accept exits 0. **Superseded 2026-10-05:** the end-state refusal now
> refuses the control, exit 1, as the stale-withdrawal test below asserts.
>
> `test_accept_never_introduces_a_report_row.py::test_a_raise_minted_before_an_update_that_already_undoes_a_landed_raise_is_refused`
> asserts that before the accept the report is exactly U's `sensitivity` row,
> `confidential` to `internal`, undoing D; then exit 1, an error naming U's
> migration id, the item and `sensitivity` and reading "from restricted to
> internal", a remedy naming U's migration id and not `theurian propose`, with
> no "accept" followed by "again" within one sentence, nothing moved under the
> proposals, migrations or knowledge directories, and after `migrate apply`
> the report it started with.
> `::test_a_withdrawal_that_restores_first_is_refused_when_it_would_take_over_an_update_row`
> asserts the same of the status face, reading the starting report as U's
> `status` row undoing D and the error for `status` reading "from deprecated
> to approved".
> `::test_a_stale_withdrawal_minted_before_a_landed_reapproval_is_refused`,
> which under its earlier name asserted the control's exit 0, now asserts
> exit 1, nothing moved, a `dependsOn: [<U>]` remedy and, after
> `migrate apply`, the report it started with.
> **Superseded 2026-10-05:** the control's acceptance read P's replay
> position, after D, not the `approved` item P withdrew, which U left `approved`.
> `tests/unit/test_introduced_moves.py::test_a_row_under_a_held_key_whose_undoes_differs_is_introduced`
> holds as introduced a `status` and a `sensitivity` row naming the incoming
> migration where the held row names another, and a row with no writer
> recorded where the held row has one; the first widening's test asserted the
> last was not introduced. Run against the three-field key as above, both face
> tests failed on `assert code == 1` with exit 0, the three unit cases failed,
> and the control passed. The same day, the remedy assertion these tests share
> with the first widening's was tightened from any remedy containing
> `theurian propose` or "again"; the descriptions above were corrected in
> place, and no test reads them against the assertions.
>
> **2026-10-03, the re-check's fix wave.** The adversarial re-check of the two
> widenings found the reported-upsert remedies unfollowable in two ways, each
> graded HIGH, and the landed-row error's direction unpinned, graded MEDIUM:
>
> - (a) The remedy sent every refused proposal to `theurian propose`, which
>   drafts content only. A refused `deprecateItem` could not be drafted again
>   there, and neither tool drafts a `changeSensitivity` or `restoreItem`:
>   `knowledge.generateMigrationDraft` refuses both (decision 3).
> - (b) Both remedies said a fresh draft's later id replays after the landed
>   migration. It does not when that migration declares `dependsOn`: each
>   round of the replay sort takes every migration that is ready, by id
>   (`domain/migration.py :: MigrationSet._topological_order`), so a migration
>   declaring no `dependsOn` replays before one declaring any, whatever the
>   ids. A fresh draft declares none, so it was refused again, and the remedy
>   sent its reader round the same loop. That a later id was enough otherwise
>   was itself false; the id-order finding below records why.
> - (d) The tests read the error's `before` and `after` by membership, so an
>   error stating them swapped would have passed.
>
> (c) Both remedies were then built by
> `application/proposal_service.py :: _redraft_remedy`, routed by the refused
> proposal's operation kinds, the most restrictive first: a `changeSensitivity`
> or `restoreItem` to a migration authored by hand and applied with
> `theurian migrate apply` once a human has reviewed it; another kind
> `knowledge.generateMigrationDraft` carries to that tool; anything else to
> `theurian propose`, which the own row always took; the own row's remedy has
> since named the landed migrations its error names. Routing by the hardest
> kind sent a mixed proposal to a route that drafts only part of it — a
> proposal restoring then deprecating an item was told to author only the
> `restoreItem`, which readmits the item, and an `upsertRevision` with a
> `deprecateItem` was sent to `generateMigrationDraft`, which refuses
> content — so the same day both rows were routed by every kind the proposal
> carries instead: `theurian propose` when every kind is `createItem` or
> `upsertRevision`, `generateMigrationDraft` when every kind is one it drafts,
> and otherwise one migration authored by hand naming all of them.
> `test_redraft_remedy.py::test_every_pair_of_kinds_is_routed_to_a_tool_that_can_draft_all_of_it`
> calls `_redraft_remedy` for every unordered pair of the kinds in
> `_REFUSED_TO_CONTENT_PATH`, `V1_OPERATION_KINDS` and `_REFUSED_TO_CLI`, and
> asserts each of `theurian propose`, `knowledge.generateMigrationDraft` and
> `theurian migrate apply` named exactly on its own route, the last with
> "Author the " and both kinds; through `accept`,
> `test_accept_never_introduces_a_report_row.py::test_a_withdrawal_that_restores_first_is_refused_when_it_would_take_over_an_update_row`
> asserts the restore-first face's remedy naming `restoreItem`,
> `deprecateItem`, "Author the " and `theurian migrate apply`. When a
> migration the new one must replay after declared `dependsOn`, the remedy then
> named those ids as the new one's `dependsOn`: declared by the authored
> migration, in the `generateMigrationDraft` document, or, as
> `theurian propose` has no option for it, edited into the drafted migration
> file; otherwise it did not mention `dependsOn`. Since the id-order finding
> below it names every landed migration the error names, in the same three
> forms, whatever they declare. "Nothing has moved.", the
> step deleting the stale proposal directory, and the absence of
> "accept … again" are unchanged. The
> floor remedies' fresh draft after a hand-authored migration had the same
> loop, so `item_labels.DRAFT_AGAIN_CLAUSE` and `ACCEPT_READMISSION_REMEDY` then
> added: "If that migration declares `dependsOn`, edit `dependsOn: [<its id>]`
> into the new draft's migration file." Since the id-order finding below they
> end "…, and only `dependsOn` places the new draft after it, so edit
> `dependsOn: [<its id>]` into the new draft's migration file."
> `test_accept_never_introduces_a_report_row.py::test_a_withdrawals_remedy_routes_to_the_migration_draft_tool_not_the_content_path`
> refuses a staged `deprecateItem` behind a landed update declaring no
> `dependsOn` and asserts a remedy naming `knowledge.generateMigrationDraft`
> and `dependsOn: [<update>]`, and none of `theurian propose`, `proposeChange`
> or "later migration id"; until the id-order finding it asserted no
> `dependsOn`.
> `::test_a_sensitivity_raises_remedy_routes_to_a_hand_authored_migration`
> asserts, for a staged `changeSensitivity` raise, a remedy holding "author",
> `changeSensitivity` and "migration" in that order within one sentence, and
> naming neither `theurian propose` nor `generateMigrationDraft`.
> `::test_the_remedy_names_dependson_when_the_landed_update_declares_it`, with
> the update declaring `dependsOn`, asserts a remedy naming `dependsOn`
> followed by the update's migration id, and `knowledge.generateMigrationDraft`.
> `::test_a_fresh_withdrawal_without_dependson_is_refused_again_behind_a_dependent_update`
> is the loop: a fresh deprecation whose id sorts after the update's is
> refused with exit 1 and an error naming the update.
> `::test_a_fresh_withdrawal_that_depends_on_the_dependent_update_is_accepted_and_takes_effect`
> follows the remedy: drafted with `dependsOn` naming the update, the
> deprecation is accepted with exit 0, and after `migrate apply` the item is
> `deprecated` and the report empty. For the own row,
> `test_accept_refuses_a_reported_upsert.py::test_the_own_row_remedy_keeps_the_content_path_for_an_upsert_and_never_says_accept_again`
> asserts, behind a landed restore declaring no `dependsOn`, a remedy naming
> `` `theurian propose` `` and `dependsOn: [<restore>]` and not "later
> migration id", with no "accept" followed by "again" within one sentence
> (until the id-order finding it asserted no `dependsOn`), and
> `::test_the_own_row_remedy_names_dependson_when_the_landed_restore_declares_it`,
> behind one declaring it, exit 1, an error naming the restore, and a remedy
> naming `dependsOn` followed by the restore's migration id. No test then read
> the authored route's `dependsOn` clause, the `restoreItem` authored route's
> text, the `theurian propose` route for a landed migration's row, or the floor
> remedies' `dependsOn` clause. Measured on 2026-10-03, before the id-order
> finding, by a scratch run outside the suite, which printed the texts of that
> time: a staged `changeSensitivity` raise behind an update declaring
> `dependsOn` printed "… replays after `<update>`, declaring
> `dependsOn: [<update>]`, and apply it with `theurian migrate apply` once a
> human has reviewed it."; and after a readmission refusal printing the new
> clause, and a `restoreItem` declaring `dependsOn`, a `theurian propose` draft
> without it was refused with the own-row remedy naming
> `dependsOn: [<restore>]`, while one with it edited in was accepted with exit
> 0 and the report was empty after `migrate apply`. A content raise drafted
> with `theurian propose` before a landed update met the revision-conflict
> refusal first, so that run did not reach the `theurian propose` route of a
> landed migration's row. Since the routing by every kind, three of the four
> are read by unit tests that build the remedy directly rather than through
> `accept`:
> `test_redraft_remedy.py::test_dependson_names_every_landed_migration_whether_or_not_it_declares_one`
> asserts each route's own `dependsOn` form, the authored one among them;
> `::test_each_cli_kind_alone_is_authored_and_applied_by_a_human` asserts
> "Author the restoreItem operation as a migration that replays after" the
> landed id; and
> `test_floor_refusal_remedy.py::test_every_remedy_that_says_to_draft_again_says_to_edit_dependson_in`
> asserts the floor remedies' `dependsOn` sentence. No test reaches the
> `theurian propose` route through a landed migration's row. This sentence
> also said, until later on 2026-10-03, that no test reached the own row of a
> proposal carrying more than content;
> `test_accept_refuses_a_reported_upsert.py::test_the_own_row_remedy_authors_every_kind_of_a_proposal_carrying_a_non_content_one`
> does: it appends a `changeOwner` to the staged document of a proposal
> refused for its own row, and asserts exit 1, an error naming the proposal's
> and the restore's migration ids, and a remedy holding "Author the
> changeOwner and createItem and upsertRevision operations as a migration"
> and not `theurian propose`. Measured on 2026-10-03 at the id-order fix by
> wrapping `_redraft_remedy` in-process over the 20 test files, 880 tests,
> that
> `git grep -l -E '"propose",[[:space:]]*"accept"|propose_accept|\.accept\(|"accept",' -- packages/theurian-core/tests`
> lists beside its support module: of 24 calls, the own-row site took the
> `theurian propose` route 11 times and the authored route once, that test's;
> the landed-row site took `generateMigrationDraft` 8 times, the authored route
> 4 times, and `theurian propose` never.
>
> (d) The landed-row tests' shared assertion now requires an error reading
> "from `<before>` to `<after>`" in that order, for the status, sensitivity,
> re-attribution and restore-first faces above, and a remedy that does not
> name `theurian propose`, since every proposal those tests refuse is a
> migration. The descriptions above were corrected in place to the tests'
> bodies, as were the sentences presenting a remedy as current; the remedy
> quoted for the first widening stays as it printed then. The bold sentence on
> the baseline was qualified in place with the `undoes` condition the second
> widening added.
>
> The re-check also met a re-attribution face the second widening did not
> name: a landed migration can become what the update overwrites because of
> the proposal. D deprecates the item and sorts first, P is a staged
> `restoreItem`, and X, a landed `deprecateItem` of the already-deprecated
> item, sorts between P and U. Landed alone, X changes no predicate, so U's
> row undoes D; with P, X withdraws the restored item, and U's row undoes X.
> Measured on 2026-10-03 by a scratch run outside the suite: the landed-alone
> report named D, the report with P copied into the landed set named X, and
> `accept` exited 1 naming U. No test builds it. The docstrings of
> `introduced_moves` and `_refuse_a_reported_upsert` now name it.
>
> **Later on 2026-10-03, the id-order finding.** The re-check of the fix wave
> graded HIGH that the redraft remedies still rested on id order: they said a
> fresh draft "gets a later migration id and replays after `<id>`", and named
> `dependsOn` only when the landed migration declared one. Each round of the
> replay sort takes every migration whose `dependsOn` have replayed, in id
> order, so a landed migration whose id sorts after the drafting clock replays
> after every fresh draft that declares no `dependsOn`, however late it is
> drafted. A hand-chosen id, a staged id edited before `accept` and a
> collaborator's clock running ahead each produce one. Measured on 2026-10-03
> by a scratch run outside the suite: behind a landed `restoreItem` at
> `SORTS_AFTER_A_DRAFT`, an id past the clock, the remedy followed as it then
> read — a fresh `theurian propose` draft, the stale directory deleted — was
> refused three times running, exits `[1, 1, 1]`.
>
> A sharper condition, naming `dependsOn` also when the landed id sorts after
> the clock, was rejected for a rule with none. The fix wave's condition was
> already one enumeration of where id order fails, and the re-check found a
> case outside it; the drafting clock is read after the refusal, by whoever
> drafts and perhaps on another machine, so no condition `accept` evaluates
> covers it. `dependsOn: [X]` places a migration in a round after X's whatever
> the ids. So every route of `_redraft_remedy` now says the new migration
> replays after `<id>` "only through its `dependsOn`" and names
> `dependsOn: [<every landed migration the error names>]` in its own form —
> edited into the drafted file for `theurian propose`, in the document for
> `knowledge.generateMigrationDraft`, declared by the authored migration — and
> no remedy says a later id is enough. The draft-again constants lost their
> condition the same way (above).
> `test_redraft_remedy.py::test_dependson_names_every_landed_migration_whether_or_not_it_declares_one`
> builds the remedy for every kind alone and for the two mixed proposals
> above, behind a root migration, a dependent one and both, and asserts the
> route's `dependsOn` form listing every id passed, no "later migration id"
> and no "accept … again";
> `::test_every_landed_migration_is_listed_in_the_clause_in_the_order_given`
> asserts three ids listed in the order passed, which is not id order.
> Through `accept`, with the landed migration at `SORTS_AFTER_A_DRAFT`,
> `test_accept_refuses_a_reported_upsert.py::test_a_redraft_after_a_restore_whose_id_sorts_after_every_draft_lands_with_dependson`
> (the own row) and
> `test_accept_never_introduces_a_report_row.py::test_a_withdrawal_redrafted_behind_a_future_id_update_lands_with_dependson`
> (a landed row) assert a remedy naming `dependsOn: [<landed id>]`, a fresh
> draft whose id sorts before it, that draft accepted with exit 0 once
> `dependsOn` is in it, and an empty report after `migrate apply`, the item
> `approved` at the fresh revision in the first and `deprecated` in the
> second. The root controls above —
> `::test_a_withdrawals_remedy_routes_to_the_migration_draft_tool_not_the_content_path`
> and
> `test_accept_refuses_a_reported_upsert.py::test_the_own_row_remedy_keeps_the_content_path_for_an_upsert_and_never_says_accept_again`
> — flipped from asserting no `dependsOn` to asserting it.
> `test_floor_refusal_remedy.py::test_every_remedy_that_says_to_draft_again_says_to_edit_dependson_in`
> asserts "edit `dependsOn: [<its id>]` into the new draft's migration file."
> and no "declares" in each of its five remedies, and
> `::test_the_draft_again_constants_carry_the_dependson_clause` the same of
> both constants' endings. The same re-check graded MEDIUM that nothing pinned
> which landed migrations the own-row remedy names:
> `test_accept_refuses_a_reported_upsert.py::test_accepting_a_proposal_that_replays_before_a_landed_restore_is_refused_and_moves_nothing`
> now asserts "replays after `<restore>`", and
> `::test_a_redraft_after_two_dependent_restores_names_both_and_lands`, a
> second restore declaring `dependsOn` on the first, asserts both ids in the
> error, "replays after `<first>, <second>`" and
> `dependsOn: [<first>, <second>]` in the remedy, and, with both edited into a
> fresh draft, exit 0 and an empty report after `migrate apply`. Run on
> 2026-10-03 with the package source and schemas of the tree before the fix
> and these tests as they now are, 57 of the 61 collected cases outside the
> "replays after `<restore>`" test failed; the four that passed are the first
> unit test's authored routes behind a lone migration declaring `dependsOn`,
> which the old remedy already named. All 62 pass against the fix.
>
> **Two premises in the review record measured false, and recorded as such.**
> (a) That `migrate validate` never replays: it used not to, and now it
> replays once, into a throwaway database, for the report only; its verdict
> still does not rest on a replay. (b) That the product has a CI surface running
> `migrate validate`: it has none. No workflow or template ships under
> `packages/theurian-core/src/` or `plugins/` —
> `git ls-files packages/theurian-core/src plugins | grep -i -E 'workflow|template|\.github'`
> printed nothing on 2026-10-02 — so nothing runs it unless a project's own CI
> does. The operator guidance is therefore two lines: run
> `theurian migrate validate` before merging any `changeSensitivity` or
> `deprecateItem`, and read the report `theurian migrate apply` prints after
> it. The race is a residual detected at `migrate validate` and
> `migrate apply`, not prevented. Once `kind` was decided by effect, the first
> line was widened to any migration that withdraws or raises a label, naming
> first the in-place `upsertRevision` that re-declares a revision `rejected`,
> `superseded` or `deprecated`, or at a higher class, because it is how a
> revision is withdrawn in place (ADR-0024 decision 5) and superseding or
> retiring is the step T-15 names for removing a secret; then `deprecateItem`
> and a raising `changeSensitivity`. The two named before were never the only
> withdrawals.
>
> **Known cost.** A history that already holds such an upsert, the original
> defect's victims included, is reported on every `migrate validate` and on
> every `migrate apply` that replays it. The report is true and cannot be
> silenced; a way to acknowledge a row is post-publication work. The replay
> raised `migrate validate` from 7.71 s to 8.39 s at 1,000 single-item
> migrations, median of three runs at load average 5–10, recorded at
> `cli/commands.py :: _permissive_move_fields`. On 2026-10-02 this
> repository's 49 migrations reported no row from `migrate validate` —
> `::test_the_dogfood_corpus_reports_no_permissive_move` re-checks that on
> every run — and `examples/sample-project/` reported none from `validate` or
> `apply`.
>
> **The advisory's item B3**, an audit of lowerings already landed (not
> decision 7's slice B3), is substantially discharged by the report:
> `migrate validate --json` lists every upsert in a history that meets both
> clauses, and filtering its rows by `kind` separates nullified withdrawals
> (`undoes`) from lowerings no withdrawal preceded (`lowers`). That holds
> because `kind` is decided by the effect of the field's last change (above),
> so an in-place withdrawal by `upsertRevision` files as `undoes` and a lowering
> after a declassification as `lowers`; decided by operation type, it filed
> both the other way. And because that effect is judged at the floors'
> granularity, a write between two retired statuses no longer stands in for the
> withdrawal before it, which at value granularity filed the four faces above
> as `lowers`. It does not list
> what the design limit nets out inside one migration, nor a lowering made by
> `changeSensitivity`, the sanctioned path.
>
> **What holds it**, read from each test's body. In
> `tests/integration/test_update_label_inheritance.py` a drafting surface
> updates a project whose one item carries chosen labels, and the test reads the
> staged migration, the item row after `propose accept` and `migrate apply`, or
> both:
> `::test_an_update_omitting_sensitivity_leaves_the_items_sensitivity_unchanged`
> (a `confidential` item over MCP under a raised ceiling and over the CLI, a
> `public` one over MCP under the default ceiling and over the CLI),
> `::test_an_update_omitting_trust_level_and_namespace_leaves_the_items_own`
> (MCP and CLI),
> `::test_a_candidate_update_of_a_confidential_item_leaves_it_confidential`, and
> `::test_a_first_revision_over_mcp_gains_no_label_the_caller_did_not_name` for
> an item id nothing created. The draft-time refusal is
> `::test_an_mcp_update_naming_a_lower_sensitivity_is_refused_without_naming_a_label`
> — four lowerings, each answered with text that names `changeSensitivity` and
> no level word, and nothing written under either proposals directory — with
> `::test_an_mcp_update_naming_an_equal_or_higher_sensitivity_drafts_it` as its
> control, and
> `::test_theurian_propose_naming_a_lower_sensitivity_refuses_and_writes_nothing`
> on the CLI. Decision 6's bind is
> `::test_naming_a_lower_sensitivity_on_a_withheld_item_answers_like_an_absent_id`:
> eight cases — two above the default ceiling; four retired within their
> ceiling, `deprecated` twice, `superseded` and `rejected`; two above a raised
> ceiling — each sent with an `expectedRevision` and compared with a call about
> an id nothing stored, on the normalised response, the SELECT statements read
> through `open_read_connection`, and an unchanged proposals tree. It runs in
> one corpus, the same-corpus form
> decision 6's *Landed* entry describes, with the limit that entry states: it
> cannot see a channel carried by collection-wide state.
> `::test_the_same_call_on_a_corpus_without_the_item_gets_the_same_bytes` is the
> two-corpora form of the same eight cases: each builds one project holding the
> withheld item and one built the same way without it, sends both the same call,
> and compares the raw responses with no normalisation, each project's
> proposals tree unchanged.
>
> `tests/integration/test_create_only_item_labels.py` holds the create-only
> case, on an item whose one migration is a `createItem` naming its
> `sensitivity`, `trustLevel` and `namespace`.
> `::test_a_cli_draft_for_a_create_only_item_drafts_the_labels_its_create_item_holds`
> and
> `::test_an_mcp_draft_omitting_labels_for_a_create_only_item_drafts_its_create_item_labels`
> read the staged labels, and
> `::test_a_create_only_item_keeps_its_labels_after_its_first_revision_lands`
> the item row after `propose accept` and `migrate apply`.
> `::test_a_cli_draft_naming_a_lower_sensitivity_for_a_create_only_item_is_refused`
> and
> `::test_an_mcp_draft_naming_a_lower_sensitivity_for_a_create_only_item_is_refused`
> hold the refusal with nothing written under either proposals directory, the
> MCP one sent with no `expectedRevision` and answered with text that names
> `changeSensitivity` and no level word.
> `::test_a_create_only_item_above_the_ceiling_answers_like_an_id_that_never_existed`
> is the two-corpora comparison for a `confidential` create-only item under the
> default ceiling, raw responses equal and both proposals trees unchanged, and
> `::test_a_first_revision_draft_for_a_withheld_create_only_item_matches_an_absent_id`
> sends the call without `expectedRevision` to both corpora and asserts that
> both draft and neither answer names `changeSensitivity`.
>
> `tests/integration/test_accept_sensitivity_floor.py` holds accept. It refuses
> a staged update with its `sensitivity` line removed, one with it edited to a
> lower value (`confidential` to `internal`, and `restricted` to
> `confidential`), and a draft whose migration sorts after a reclassification,
> each asserting exit 1, a remedy naming `changeSensitivity`, and that nothing
> under the proposals, migrations or knowledge directories moved; its landing
> tests — a kept or raised label, and a draft that sorts before the
> reclassification — are the controls. Its two `changeSensitivity` tests are
> under *Owed* below. Each stages its proposal through `theurian propose`, and
> what carries the refusals over to a proposal drafted elsewhere is that the
> floor compares replayed labels and reads nothing about which surface drafted
> it. A lowering drafted over the wire for a create-only item outside the
> caller's view, a case only the floor refuses, is
> `test_create_only_item_labels.py::test_accept_refuses_the_lowering_an_mcp_draft_for_a_withheld_create_only_item_stages`:
> a `confidential` create-only item under the default ceiling, a
> `knowledge.proposeChange` call with no `expectedRevision` that names `public`
> or omits the sensitivity and is answered without error, a staged label that
> is `public` where the call named it and is not `confidential` in either case,
> and `propose accept` exiting 1 with an error that says "lower the
> sensitivity" and names the item, nothing under the proposals, migrations or
> knowledge directories moved, and the row still `confidential` with no
> revision. `tests/integration/test_accept_floor_refusal_text.py`
> holds what the refusals say.
> `::test_accept_does_not_blame_the_landed_set_for_a_proposal_that_makes_it_replay`
> builds the landed set that replays only with the proposal and asserts the
> refusal says it "does not replay on its own", not "with or without this
> proposal", with nothing moved.
> `::test_accept_of_a_draft_made_against_a_lagging_served_state_points_at_migrate_apply`
> drafts over MCP while a committed reclassification is unapplied, so the draft
> carries the stale `internal`, and asserts that `accept` refuses it with a
> remedy naming `theurian migrate apply`, with nothing moved.
> `::test_the_lowering_remedy_names_theurian_propose_as_the_route_to_draft_again`
> reads the redraft step: a CLI draft naming `internal`, a reclassification to
> `confidential` sorting before it, and a refusal whose error says "lower the
> sensitivity" and whose remedy names `theurian propose`.
> `::test_a_landed_set_that_fails_alone_is_not_sent_to_migrate_validate` reads
> the remedy of the replay refusal: a patched replay fails the landed set alone,
> and the test asserts that the error carries the replay's own words, that the
> remedy names `.theurian/migrations/` and not `migrate validate` and says that
> `theurian migrate apply` runs the same replay, and that nothing moved. The
> draft side's two replay refusals are
> `test_draft_status_refusal.py::test_a_draft_over_a_landed_set_that_does_not_replay_carries_the_engines_words`,
> on a landed set `theurian migrate validate` reports valid with exit 0 and the
> engine refuses as a revision conflict, and
> `::test_a_replay_that_holds_no_such_item_refuses_and_names_migrate_apply_not_validate`,
> whose replay reader is patched to hold nothing; each asserts the remedy does
> not name `migrate validate`, the first that it says `theurian migrate apply`
> runs the same replay, and the second that it names `theurian migrate apply`.
> `::test_migrate_apply_refuses_the_set_that_migrate_validate_passes_in_the_same_words`
> holds that sentence on the draft side, on the first one's landed set:
> `theurian migrate validate` reports it valid with exit 0, the draft is refused
> with the engine's `Revision conflict on <item>: migration expected <revision>`,
> and `theurian migrate apply` exits non-zero carrying the same words.
>
> `tests/integration/test_accept_status_floor.py` holds the status floor at
> `accept`. The first five refusals below go through one helper that asserts
> exit 1, a remedy naming `restoreItem`, and no file moved under the proposals,
> migrations or knowledge directories; the two after them assert a non-zero
> exit.
> `::test_accept_refuses_a_proposal_that_would_readmit_a_retired_item` plants,
> for a `deprecated`, a `superseded` and a `rejected` item, an update drafted in
> a second project that holds the item `approved`, and asserts after
> `migrate apply` that the row keeps its status and its revision.
> `::test_accept_refuses_a_readmission_to_a_surfaceable_status_that_is_not_approved`
> restates that update's status to `draft` and to `proposed`.
> `::test_accept_refuses_the_mcp_draft_for_a_deprecated_create_only_item` runs
> `propose accept` on a `knowledge.proposeChange` draft the tool returned
> without error, and the row is still `deprecated` after `migrate apply`.
> `::test_accept_refuses_a_draft_that_replays_after_a_deprecation` drafts while
> the item is `approved`, then lands a deprecation that sorts before the draft.
> `::test_accept_refuses_a_proposal_whose_own_restore_item_readmits_the_item`
> restates the update to `deprecated` and appends a `restoreItem`.
> `::test_the_refusal_names_the_item_and_both_statuses_and_the_way_to_readmit`
> reads the error and the remedy, and
> `::test_a_proposal_that_lowers_a_sensitivity_and_readmits_an_item_is_refused_for_both`
> refuses both defects in one proposal with exit 1 and nothing moved, and
> asserts that the error names both causes and the item twice and that the
> remedy names `changeSensitivity` before `restoreItem`. The controls land:
> `::test_accept_lands_a_draft_that_replays_before_a_deprecation` (the
> deprecation sorts after the draft, and the row ends `deprecated` at the new
> revision), the carve-out's
> `::test_accept_lands_an_update_of_a_surfaceable_unapproved_item_as_approved`
> (`draft` and `proposed`) and
> `::test_accept_lands_the_first_revision_of_a_create_only_draft_item_as_approved`,
> `::test_accept_lands_a_proposal_that_keeps_a_retired_item_retired`,
> `::test_accept_lands_a_hand_written_proposal_that_deprecates_an_approved_item`
> and `::test_the_donor_proposal_is_acceptable_where_the_item_is_not_retired`.
> `tests/unit/test_readmission_carve_out.py::test_a_rejected_item_is_retired_for_the_floor_while_draft_and_proposed_are_not`
> asserts that `may_surface` withholds `rejected` under
> `include_unapproved=True`, and that `readmitted_items` reports `rejected` to
> `approved` and reports neither `draft` nor `proposed` to `approved`.
>
> `tests/integration/test_draft_status_refusal.py` holds the draft side.
> `::test_propose_refuses_to_draft_for_a_retired_item_and_writes_nothing` runs
> `theurian propose` for a `deprecated`, a `superseded` and a `rejected` item
> with a revision and for a `deprecated` create-only one, and asserts a non-zero
> exit, a remedy naming `restoreItem`, and the proposals tree and `.gitignore`
> unchanged. `::test_the_draft_refusal_is_one_constant_that_names_no_status`
> asserts that the three statuses' answers are equal and that the error names no
> status word.
> `::test_okf_import_refuses_a_concept_whose_item_is_retired_locally_and_writes_nothing`
> imports a concept for a `deprecated` create-only item and asserts it is
> refused with nothing written, while its control
> `::test_okf_import_still_drafts_a_concept_for_a_create_only_draft_item`
> admits the same bundle for the item undeprecated;
> `::test_okf_import_over_a_retired_item_with_a_revision_is_refused_as_retired`
> imports one for a `deprecated` item with a revision and asserts one `draft`
> refusal with nothing written under either proposals directory. The import's
> record names only the exception class, so a spy on `ProposalService.draft`
> reads the refusal raised, and the test asserts the messages are exactly
> `[RETIRED_ITEM_MESSAGE]`: a missing-revision refusal, which the record would
> show as the same `draft` refusal, fails it.
> `::test_propose_still_drafts_for_a_surfaceable_item` and
> `::test_propose_still_drafts_the_first_revision_of_a_create_only_draft_item`
> are the CLI's controls. Decision 6's bind for a retired item is
> `test_update_label_inheritance.py::test_a_draft_for_a_withheld_item_matches_an_absent_id_across_two_corpora`:
> a `deprecated` create-only item, a `confidential` create-only one under the
> default ceiling and a `rejected` item with a revision, each sent once with
> `expectedRevision` and once without to a corpus holding it and to one built
> without it. It compares the two answers, the two staged proposals trees and
> the SELECT statements each call ran through `open_read_connection`, all
> scrubbed of minted ids and instants, and asserts that the call without
> `expectedRevision` drafts. `review.generateKnowledgeCandidate`'s is
> [ADR-0033](0033-knowledge-candidate-generation.md)'s matching amendment.
> `test_write_intent_wire.py::test_a_read_control_operation_is_refused_to_the_cli_over_the_wire`
> holds `knowledge.generateMigrationDraft` refusing a `restoreItem`.
>
> **Owed before the release that ships this fix, and met:** a test of a proposal
> that carries a `changeSensitivity` operation of its own. `accept` compares the
> labels a replay leaves rather than the keys a document wrote, which is why it
> refuses one.
> `test_accept_sensitivity_floor.py::test_accept_refuses_a_proposal_whose_own_change_sensitivity_lowers_the_item`
> appends a `changeSensitivity` to `internal` to a draft whose `upsertRevision`
> keeps `confidential`, and asserts the same refusal, remedy and unmoved
> directories as the refusals above. Its control,
> `::test_accept_lands_a_proposal_whose_own_change_sensitivity_raises_the_item`,
> appends one raising an `internal` item to `confidential` and asserts that
> after `accept` and `migrate apply` the row holds the new revision at
> `confidential`.
>
> **Not moved by this fix:** a migration written directly under
> `.theurian/migrations/` still gets the loader's defaults for an omitted
> label. [The migration format](../protocol/migrations.md#an-upsertrevision-re-labels-the-item)
> says why, and what that means for a hand-written update. When the default
> lowers the item, the permissive-move report names it as `lowers`
> (`::test_an_upsert_omitting_sensitivity_lowers_what_the_create_item_set`).

> **Amended by GHSA-wwq9-p8wq-5m68 (2026-10-08).** The GHSA-v2qg amendment
> above rested on an unstated premise: that the label a later proposal sets is
> the label the replay ends on, unless a migration with a later id changes it.
> On that premise it left the sanctioned operations out of `permissiveMoves`,
> and nothing at `accept` compared the label the proposal set with the one the
> replay ends on.
>
> **What implementing it revealed.** `MigrationSet._topological_order`
> (`domain/migration.py`) replays a migration that declares `dependsOn` after
> every one that declares none, whatever the ids. So a landed
> `changeSensitivity` or `restoreItem` with a smaller id, declaring
> `dependsOn`, undid the raise or deprecation of a proposal accepted after it,
> and `knowledge.get` served what the proposal withheld. The face, measured on
> `core-v0.5.1`, and its grade are T-29 in
> [the threat model](../security/threat-model.md).
>
> **Decided: refuse at `accept` by end state, report the inversion, keep the
> order.** The refusals are T-28's control 5 and its residual 10, and the
> `reorders` row is stated under that name in
> [the migration format](../protocol/migrations.md#permissive-moves-are-reported-not-refused);
> none is restated here. What changed for callers, the honest histories
> `accept` now refuses included, is in the 0.5.2 CHANGELOG's two *Changed*
> entries, both marked BREAKING. `_loosens` now decides whether a proposal
> lands as well, as the dated note in *Decided: a report, not a refusal* above
> records.
>
> **The two checks are not to be unified.** The 2026-10-06 note above records
> what each would miss under the other's rule. The report has to define an
> inversion against an order, so attribution is its definition; `accept` has
> to answer whether the label a reviewer approved is the label served, and
> only the end state answers that. The advisory's face is held by both, so
> `test_replay_order_serves_an_overwritten_label.py::test_an_accepted_label_is_not_overwritten_by_a_migration_that_declares_dependson`,
> which builds it, does not catch a change that removes either one; T-29's
> *What holds it* records, measured on 8d7e2f09, which tests do.
>
> **Considered and not taken: (C), change the order.** A priority Kahn sort,
> taking the smallest-id ready migration one at a time instead of whole
> rounds, replays in id order wherever `dependsOn` allows, and would remove
> the inversion rather than refuse and report it. Its costs:
>
> 1. **Served labels can move on upgrade with no migration changing**, in any
>    history that interleaves `dependsOn` and root migrations on a shared
>    field.
> 2. **`MIGRATION_ENGINE_VERSION` must bump.**
>    [ADR-0007](0007-state-hash-partitioned-databases.md) puts the engine
>    version in the state hash "so that an engine change invalidates cached
>    state instead of silently reinterpreting it".
> 3. **[ADR-0039](0039-closed-set-extension-compatibility.md) decision 6 does
>    not admit it as written.** A bump that changes what an earlier-version
>    document does is permitted "only with a recorded argument that no reader
>    of the canonical state observes the difference", and a new order changes
>    served labels, so that argument cannot be made. What is left is an
>    `apiVersion` bump for documents written under the new order, or an
>    amendment of decision 6.
> 4. **The serve path does not check which build wrote a state database**
>    ([#853](https://github.com/theurian/theurian/issues/853)), so one the old
>    order built is served until `migrate apply` runs.
> 5. **[ADR-0005](0005-yaml-knowledge-migrations.md) decision 3**,
>    "`dependsOn` is topologically sorted", moves with it.
>
> It would also leave two things open: a landed migration with a larger id
> still replays after a fresh draft, because id order is then the contract;
> and the post-accept race, T-29 residual 1, is unchanged. Migrations
> declaring `dependsOn`, counted on main at 8d7e2f09 on 2026-10-08 by
> `grep -l '^dependsOn:'` over each `migrations/` directory: 0 of the dogfood
> corpus's 49; 1 of `examples/sample-project`'s 2, which sorts after its
> dependency and writes a different item; 0 of 38 and 0 of 6 in the two eval
> fixture sets. [#897](https://github.com/theurian/theurian/issues/897) tracks
> (C), gated on #853; adopting it changes what upgraded projects serve, so it
> is the maintainer's call.
>
> **The tightening inversion is the recorded cost of keeping the order.**
> `reorders` is loosening-only, so a `dependsOn` migration that tightens a
> label after a larger-id loosening wins with no row and no refusal. Nothing
> is disclosed; a reviewed declassification or readmission is lost silently.
> T-29 residual 4 records it, measured. It is the clearest argument for (C),
> whose order follows ids in both directions wherever `dependsOn` allows.

## Context

ADR-0013 settled the direction — *AI proposes, Git reviews, humans approve* — and
named three write-intent tools: `knowledge.proposeChange`,
`knowledge.generateMigrationDraft` and `review.generateKnowledgeCandidate`. Three
milestones later the direction holds and none of the tools exists.

What *does* exist is the whole machine behind them. `theurian propose` drafts a
proposal today through `ProposalService.draft(ProposalRequest)`
(`application/proposal_service.py`), and everything ADR-0013 point 2 describes —
the schema-valid migration, the per-revision body path, the evidence file — is
that service's output. `application/proposal_service.py`'s secret-scan docstring
already names the arrival this ADR designs, in its own words: the policy is read
inside the service rather than injected because "Milestone 7's write-intent MCP
tools are a second root arriving. A security control that a caller can omit by
omission is not a control."

So the design question is not *what a write-intent tool does*. It is: **which
tools, taking what over the wire, and at what moment does the server stop saying
it has none.**

**The last question is the one with a measurable cost.** `writeTools: false` is
not a note in a document; it is a per-build assertion published on a security
surface and pinned in several places. The population, with its key:

```console
$ git grep -n "writeTools" be977ea7 | wc -l
      18
$ git grep -l "writeTools" be977ea7 | wc -l
      10
```

**Exclusions, measured rather than asserted** — `git grep -n "writeTools"
be977ea7 -- <path>` for each: `.claude/` drops **0**, `.theurian/` drops **0**,
`docs/work-logs/` drops **0**. The unfiltered key is therefore the same
population as a filtered one at this frame, which is why no pathspec is applied.

The ten files are not all prose. **Five** of them carry the flag in a build
artifact rather than in a sentence, and **two** of those five fail a build when
the flag's *value* moves. The difference matters to slice B4, so it is measured
rather than asserted. Flipping `"writeTools": False` to `True` in
`mcp/tools.py`, in a throwaway clone:

```console
# control
$ python -m pytest .../test_mcp_tools.py .../test_wire_contract.py .../test_schemas.py -q
361 passed, 1 xfailed in 58.28s

# mutant: "writeTools": False -> True
$ python -m pytest .../test_mcp_tools.py .../test_wire_contract.py .../test_schemas.py -q
FAILED .../test_mcp_tools.py::test_capabilities_report_what_is_and_is_not_built
1 failed, 360 passed, 1 xfailed in 58.28s

# control
$ python -m pytest tests/e2e/test_daemon_single_instance.py -q -k capabilities
1 passed, 11 deselected in 3.72s

# mutant
$ python -m pytest tests/e2e/test_daemon_single_instance.py -q -k capabilities
FAILED tests/e2e/test_daemon_single_instance.py::test_capabilities_report_no_write_tools
1 failed, 11 deselected in 2.59s
```

| File | What it holds | Moves on a value flip? |
| :-- | :-- | :-- |
| `packages/theurian-core/src/theurian/mcp/tools.py` | the value itself | — it *is* the value |
| `packages/theurian-core/tests/integration/test_mcp_tools.py` | `assert ...["writeTools"] is False`, plus the pinned capability-**key** set | **Yes**, on the value assertion. The key-set pin does not move |
| `tests/e2e/test_daemon_single_instance.py` | `assert capabilities["capabilities"]["writeTools"] is False` over a real client | **Yes** |
| `packages/theurian-core/tests/integration/test_wire_contract.py` | the conformance negative, `{"writeTools": "false"}` — a **string** where a boolean belongs | **No.** It is a type negative |
| `schemas/mcp/system-capabilities-response.schema.json` | the property and its description | **No.** A schema constrains the type, not the value |

A second, narrower key reaches the capabilities *note*, which does not contain
the flag's name:

```console
$ git grep -n "No write-intent tool exists" be977ea7 | wc -l
       3
```

— one in `mcp/tools.py`'s capability block and two in the test that pins its
wording, including the inversion the assertion message spells out.

**The served corpus is outside both keys, and that is measured rather than
assumed.** `.theurian/` drops 0 for the flag key; the corpus's
`ai-writes-produce-proposals` twins carry ADR-0013's *design* statement about
write-intent tools, which registering one does not falsify. Nothing here creates
a re-seed obligation.

## Decision

### 1. `knowledge.proposeChange` is the content path, and it maps 1:1 onto `ProposalService.draft`

The tool takes a proposed change and returns what was written. It adds no
behaviour of its own: the service mints the identifiers, chooses the paths,
validates the migration against the published schema and refuses an unguarded
update. **A second implementation of any of that is the defect this decision
exists to prevent** — ADR-0027 records what it costs when two procedures
disagree about the same set.

The wire input carries `projectId` (every project-scoped tool requires an
explicit one; the server's own instructions say there is no default project)
plus the fields `ProposalRequest` declares:

| Wire field | Maps to | Note |
| :-- | :-- | :-- |
| `itemId`, `title`, `kind`, `owner`, `author`, `description` | the same-named `ProposalRequest` fields | each refused empty at construction |
| `body` | `ProposalRequest.body` | **inline text** — decision 2 |
| `contentType` | `ProposalRequest.content_type` | a closed enum: `text/markdown`, `application/json`, `application/yaml`, the three `MediaType` constants `cli/propose_commands.py`'s `_CONTENT_TYPES` maps its five accepted suffixes onto |
| `evidence` (`agentId`, `taskId`, `model`, `reasoning`) | `Evidence` | decision 4 |
| `sourceAnchors[]` | **both** `Evidence.anchors` and `ProposalRequest.source_anchors` | **one wire field fills two domain fields**, which is what `propose_commands.py`'s `_request` already does from one `--source-uri`. They stay separate fields in the domain because they have separate readers and separate requirements: the anchors in `evidence.json` are read by the humans reviewing the pull request and by no code path, while `metadata.sourceAnchors` is what `migrate apply` enforces for INV-8. The wire does not reproduce the split, because a caller has one answer to "where did this come from" |
| `labels[]`, `scopePaths[]`, `namespace?` | the same-named fields | `namespace` absent defaults to the item id's own |
| `trustLevel?`, `sensitivity?` | the same-named fields | **absent means "not stated"**, never a stamped default: `ProposalRequest` records that writing `unverified`/`internal` into every draft "would assert a judgement the caller did not make (#249)" |
| `expectedRevision?` | `ProposalRequest.expected_revision` | the optimistic-concurrency gate (ADR-0006): required for an update, refused on a first revision — `_check_expected_revision` refuses both directions with a remedy |
| `local?` | `draft(local=...)` | ADR-0028 routing; it is a parameter of the *act*, not a field of the reviewed text |

The output mirrors the CLI's `_drafted_payload` (`cli/propose_commands.py`):
`proposalId`, `proposalDirectory`, `migrationId`, `migrationFile`,
`revisionId`, `expectedRevision`, `bodyFile`, `evidenceFile`, `contentFile`,
`contentSha256`, `bodyDestination` and the next-steps list. **One payload shape
for two front ends** is the point: a human reading a CLI result and an agent
reading a tool result are looking at the same proposal, and a second shape would
be a second thing to keep true.

**`contentType` is explicit on the wire because the CLI's derivation has no
input here.** `_read_body` reads the media type off the body file's suffix and
refuses a suffix it does not know. There is no file on this path (decision 2),
so the caller states the type instead of a filename implying it.

### 2. The body is inline text, and a file path is refused by construction

`knowledge.proposeChange` takes the body as a string. It does **not** take a
path, a URI, or any other reference the daemon would have to dereference.

This is a security decision and not an ergonomic one. The daemon runs as the
operator, and a tool that accepted a path would be a read primitive over the
caller's filesystem reachable by any MCP client that can talk to it — the
caller's private keys, the caller's `~/.claude.json`, anything the operator can
read — laundered into a proposal directory and, by ADR-0013 point 7, into a
pull request. That exact shape has been reproduced on this project once already:
ADR-0013's Milestone 7 amendment records a class of accept-path defect "one of
which read an out-of-project secret (`~/.claude.json`, `~/.kube/config`) into a
git-tracked file, an exfiltration channel", caught in review and never shipped.

Refusing the shape is cheaper than containing it. A containment check has to be
right about symlinks, case folding, Unicode normalisation and TOCTOU on every
platform; an absent parameter has nothing to be right about.

### 3. `knowledge.generateMigrationDraft` is the operations path, and v1 carries ten of the fourteen operation kinds

`knowledge.proposeChange` covers exactly one shape of change: a body and the
revision that carries it. The other twelve things a migration can say — a
deprecation, an alias, an owner change, a relation, a specification registration
— have no tool at all, and the generator cannot express them: `_migration_document`
emits precisely `createItem` and `upsertRevision`
(`application/proposal_service.py`), two of the fourteen members of
`OperationKind` (`domain/migration.py`, counted from the enum body).

So the second tool takes a **migration document** and lands it as a proposal.
Its v1 operation set is the closed `OperationKind` set **minus `createItem`,
`upsertRevision`, `changeSensitivity` and `restoreItem`** — ten of the
fourteen. The first two are refused with a remedy naming
`knowledge.proposeChange`; the other two are refused for their own reasons,
below.

**The set is chosen on an axis, not on a count, and the axis is *what a wrong
proposal moves*.** Classifying all fourteen:

| Axis | Operations | In v1? |
| :-- | :-- | :-- |
| **Moves content** — a body, a revision pointer, the digest pin | `createItem`, `upsertRevision` | **No.** The content path owns them (below) |
| **Moves an enforced read control** — `may_disclose`, the deployment's sensitivity ceiling (#119, ADR-0025) | `changeSensitivity` | **No.** Pulled; see below |
| **Narrows an enforced surfacing control** — `may_surface`, the status gate, in the withholding direction | `deprecateItem` | **Yes.** It can only take an item *out* of the surfaceable set |
| **Widens that same control** — `may_surface`, in the readmitting direction | `restoreItem` | **No.** Pulled; see below |
| **Moves addressing** — a second key that resolves to an item (T-21) | `addAlias`, `removeAlias` | **Yes**, with the refusal's *timing* named below |
| **Moves governance metadata** — ownership, relations, specification lifecycle, evidence links | `addRelation`, `removeRelation`, `changeOwner`, `registerSpecification`, `supersedeSpecification`, `addEvidence`, `removeEvidence` | **Yes** |

**`changeSensitivity` is pulled because it is the one operation whose reviewer
cannot see the thing it moves.** Sensitivity stopped being a write-time refusal
and became an enforced read control when #119 landed (`may_disclose`,
`domain/enums.py`; ADR-0025) — a declassification proposal therefore widens who
may read an item, and *how much* it widens depends on the deployment's
sensitivity ceiling, which is not in the migration and is deliberately not
published on `system.capabilities` (`mcp/tools.py` records that a flag may be
published there while a ceiling may not). A human reading the pull request sees
`confidential → internal` and cannot see what that admits. That is a decision
that deserves its own recorded justification, and this ADR does not write one;
it takes the operation out of v1 instead, additively recoverable later.

`docs/roadmap.md`'s Phase B risks row is the second half of the reason: "review
text is untrusted content, and turning it into a candidate is precisely the path
by which an injected instruction becomes a knowledge candidate." An injected
instruction that reaches a declassification is the worst member of that family,
and v1 does not carry it.

**`deprecateItem` and `restoreItem` both move the status gate, and only one of
them stays. The distinction is which direction it moves it in.**

`deprecateItem` stays. It sets `DEPRECATED`
(`application/migration_engine.py:485`), a status outside `SURFACEABLE_STATUSES`
under both values of `includeUnapproved` (`domain/enums.py`), so the worst a
wrong proposal does is withhold an item that should have stayed visible — a
reviewer meets that as a missing answer, not as a disclosure. A reviewer reading
the migration also sees the whole of what it does: the statuses are in the
document, the set of surfaceable ones is in the domain, and nothing about the
deployment changes the answer.

**`restoreItem` is pulled, because it readmits from any status, and the wire
shape lets it say nothing about why.** Three measurements, each with the key
that settles it.

**One — there is no transition check.** `restoreItem` sets `APPROVED` from
whatever status the item currently holds:

```python
# application/migration_engine.py:497-498
case RestoreItem():
    self._set_status(writer, project_id, operation.item_id, KnowledgeStatus.APPROVED)
```

`_set_status` (`application/migration_engine.py:676-686`) looks the item up,
raises on an unknown id, and writes `item.with_status(status)`. It takes the
target status as an argument and never reads the current one. The walk that
would find a transition rule elsewhere returns nothing:

```console
$ git grep -c "DEPRECATED" -- packages/theurian-core/src
packages/theurian-core/src/theurian/application/migration_alias_guards.py:2
packages/theurian-core/src/theurian/application/migration_engine.py:1
packages/theurian-core/src/theurian/domain/enums.py:1
```

Four lines in three files, read one by one: the alias guard's own status
projection (`:75`) and its deprecated-is-exempt test (`:137`), the
`deprecateItem` write above (`:485`), and the enum member (`domain/enums.py:29`).
Not one of them asks what a restore is restoring *from*. **The key's limit**: it
finds only the spelling `DEPRECATED`, so a transition table written in some other
vocabulary would be invisible to it — which is why the engine's own
`case RestoreItem()` arm is quoted above rather than inferred from the absence.

**Two — `REJECTED` is inside that reach, and it is the one status no flag
surfaces.** `domain/enums.py:206-219` says so in its own words: `REJECTED` "is
deliberately absent and there is no flag that adds it. A rejected revision is
one the team decided must *not* be followed, and it is also where a secret that
caused the rejection still lives." A `restoreItem` over a rejected item is
therefore the one operation in the set that can republish content the read gate
is built never to serve, and it does it in the widening direction.

**Three — the wire shape is weaker than that of the operation this ADR had
already pulled, so admitting one while pulling the other had the contrast the
wrong way round.** `opRestoreItem`
(`schemas/migrations/migration.schema.json:253-262`) requires exactly `op` and
`itemId`; its `reason` is an **optional** property, so a schema-valid restore can
carry no rationale at all. `opChangeSensitivity` (`:305-318`) requires `op`,
`itemId`, `sensitivity` **and** `reason`, and the schema's own description of
that field states the principle: "Reclassification changes who may read the
content, so the rationale is part of the record." The two operations move who
may read an item by different routes, and the one with the weaker record is the
one an earlier draft of this ADR admitted.

**The readmission path is owed, not closed.** Widening is additive — the third
of the three reasons for the content split, below, says why — and a later slice
that admits `restoreItem` has to bring three things with it:

1. **A transition-aware refusal** — restore admitted only over a `DEPRECATED`
   item. That is already the operation's *documented* meaning:
   `docs/protocol/migrations.md:86` gives `restoreItem` as "Undo a
   deprecation", which the engine does not enforce. That mismatch is
   pre-existing, is not created here, and is recorded on
   [#272](https://github.com/theurian/theurian/issues/272) — the
   status-transition-graph ADR candidate that owns the enforcement — in
   [its 2026-09-12 comment](https://github.com/theurian/theurian/issues/272#issuecomment-5646294018).
2. **A wire-required `reason`**, in the shape `opChangeSensitivity` already
   uses, so the human reading the pull request is told why an item is coming
   back.
3. **A named seat.** Apply-time enforcement of the status transition graph is
   `docs/roadmap.md`'s Phase D item ① and its ADR candidate #1 ("Enforcing the
   status transition graph — define the legal transitions and check them in the
   migration engine"). A tool-side refusal is narrower than that and does not
   substitute for it; whichever lands first, the other is still owed.

**`addAlias` carries the T-21 shape, and what is owed about it is *when* the
refusal arrives, not whether.** An alias key equal to a live item id let a read
gate that resolves the alias evaluate the wrong item's authority — closed in
0.1.0.dev6 by `refuse_alias_item_id_collision`
(`application/migration_alias_guards.py`), a whole-set static guard that runs at
`migrate validate`, at `migrate apply` and inside `MigrationEngine.apply`, and
that `propose accept` also runs as stage 3 of its union rehearsal
(`_refuse_unless_the_union_applies`, ADR-0027 decision 2).

`draft` does not run it: it validates the document against the schema and
nothing else. So an agent's colliding `addAlias` is written, reviewed by a human
and then refused at `accept` — the shape ADR-0013's INV-8 note names, where a
document "is schema-valid and then exits 4" after a person has already spent the
review. Slice B4 owes whether the operations path runs the whole-set guards at
generation as well. It is named here so it is a decision rather than a
discovery — **and it is a disclosure question as well as a cost one**, which an
earlier draft of this ADR got wrong by calling it "a cost question … and not a
safety one".

**The guard's refusal is an existence-and-status oracle, measured in the
refusal's own construction.** `AliasItemCollisionError`
(`domain/errors.py:197-240`) renders both the colliding item id and its status —
the first two of the message's four lines, verbatim:

```python
# domain/errors.py:236-237
f"{migration_id}: addAlias {alias} -> {alias_target} collides with knowledge item "
f"{alias} (status {item_status}). An alias key and an item id must be distinct: a "
```

and `item_status` is computed over the **unfiltered** landed set.
`_alias_item_collisions` (`application/migration_alias_guards.py:124-144`) reads
`_final_item_statuses(migration_set)` — a fold over every operation in
`MigrationSet.ordered(...)` with no status filter and no sensitivity filter — and
yields the status straight into the error. So running the guard at generation
would answer, for an alias key the caller chose, *does an item with this id
exist, and what status is it in* — including `rejected`, the status
`domain/enums.py:206-219` records as reachable through no flag. The guard's own
docstring already names the dangerous case ("a `rejected` item is the dangerous
case"); what is new here is that the refusal **publishes** it.

That does not settle which way slice B4 should go: refusing late is the cost
ADR-0013's INV-8 note prices, and refusing early is the oracle above. What it
settles is that the decision is bound by decision 6's rule and cannot be made on
cost alone.

Three reasons for the content split, in order of weight:

1. **The operations path must not become a second body pipeline.** A
   `upsertRevision` accepted here would need `contentFile`, the digest pin, the
   per-revision body path and the replacement guard — every mechanism
   ADR-0013's amendment and ADR-0027 record as hard-won — reimplemented against
   a hand-authored document. Two pipelines that must agree about the same set is
   the defect shape, not the feature.
2. **The split is legible to a caller.** *Content goes to `proposeChange`;
   everything else except `changeSensitivity` and `restoreItem` goes to
   `generateMigrationDraft`* is a rule an agent can follow without reading this
   ADR.
3. **Widening is additive.** Admitting `upsertRevision`, `changeSensitivity` or
   `restoreItem` later adds a capability; no client breaks. Narrowing later
   would break clients, which is why the v1 set is the conservative one.

**Validation reuses the existing entry point, reached the way the service
already reaches it.** `validate_migration_document`
(`infrastructure/filesystem/migration_loader.py`) takes a parsed mapping rather
than a path, and its docstring already names this use: "a generator can refuse
to write a migration it has just built wrong rather than leaving one on disk for
a reviewer to discover. ADR-0013 point 3 is the reason it belongs at generation."
It also already carries the bounds an untrusted document needs
(`MAX_DOCUMENT_NESTING`, `MAX_DOCUMENT_NODES`, `MAX_DOCUMENT_RENDERED_CHARS`,
recorded there for #291 and #245).

**The route to it is the injected `MigrationDocumentValidator`, never a direct
infrastructure import.** `ProposalService` already takes `validate:
MigrationDocumentValidator` in its constructor, and the constant's own docstring
says why: "Supplied by the composition root, because locating and reading
`schemas/` is an adapter's job (ADR-0003)." The CLI fills it with
`lambda document: validate_migration_document(document, schemas)`
(`cli/propose_commands.py`); the MCP composition root fills the same parameter.
A draft-from-document entry that imported the loader would put an adapter import
in the application layer and give this path a *second* validator the accept-time
rehearsal is not holding — `_refuse_unless_the_union_applies` runs "the same
`MigrationDocumentValidator` `draft` calls" precisely so a proposal this build
generated cannot fail its own acceptance.

**Two guarantees the CLI has are the CLI's, and the wire path does not inherit
them.** Both live in `cli/propose_commands.py`, above the service:

| CLI guarantee | Where it lives | What owes the wire equivalent |
| :-- | :-- | :-- |
| The body is capped at `MAX_SOURCE_FILE_BYTES` (8 MiB) | `_read_body`, applied to the body **file**'s `stat().st_size` — there is no file on this path (decision 2), so nothing applies it | ADR-0031's published input schema (slice B2) carries an explicit `maxLength` on `body`, and slice B4 drives it — in a unit that is still open, since `maxLength` counts code points and this cap is in bytes, so the wire bound must either be the sound over-approximation plus a byte check at landing or an explicitly recorded different unit ([#691](https://github.com/theurian/theurian/issues/691)) |
| `--label authored-in-theurian` beside `--authored-here` is deduplicated | `_merge_labels`, which exists because `revisionMetadata.labels` is `uniqueItems` in the migration schema and a duplicate would fail the generator's own validation | The published input schema sets `uniqueItems` on `labels[]`, so the refusal is a schema refusal with a key path rather than a validation failure over a document the caller cannot see |

Neither is a defect in the CLI; both are the consequence of the service taking
already-read text. What would be a defect is assuming they travel.

**What lands is a proposal directory, through a draft-from-document service
entry owed to slice B4.** No such entry exists today: `ProposalService.draft`
takes a `ProposalRequest` and builds the document itself. Writing that entry —
so that this path lands through the same guards, the same identifier minting and
the same secret-scan-at-accept posture — is implementation work this ADR names
and does not do.

### 4. Evidence requiredness is preserved over the wire, and it is already enforced twice

`agentId`, `taskId`, `model` and `reasoning` are required on every write-intent
call, and the enforcement an MCP tool gets is the enforcement that already
exists rather than a third copy:

- `Evidence.__post_init__` calls `require_evidence` (`domain/proposal.py`),
  which refuses an empty `model` and an empty `reasoning`.
- `AgentId` and `TaskId` (`domain/identifiers.py`) refuse an empty or
  over-length value at construction, so an absent agent identity cannot be
  spelled as `""`.
- `ProposalRequest.__post_init__` calls `require_evidence` **again**, and
  `require_evidence`'s own docstring says why the second call is not redundant:
  it makes "rejected at generation" a property of the generation *path* rather
  than of one constructor, "so that a caller holding an `Evidence` built by any
  other route still cannot package a proposal out of it."

A tool that constructs an `Evidence` therefore inherits ADR-0013 point 5 without
asking for it, which is the property this decision records rather than adds.

**What is enforced is presence, not truth, and the heading should not be read
otherwise.** All four values are caller-asserted strings. The daemon does not
authenticate agents — `tool-context.schema.json` says so in its own words about
the same fields: "Provenance only. Theurian does not authenticate agents; this
labels which run produced a proposal." What the two `require_evidence` calls buy
is that a proposal cannot be packaged with the provenance *missing*; an agent
that lies about its identity is outside this control and outside this ADR.

**`agentId` and `taskId` have two wire addresses, and the precedence is decided
here rather than left to collide.** `tool-context.schema.json` types optional
top-level `agentId` and `taskId` on every project-scoped call, and the `evidence`
object above carries the same two names. For the write-intent tools:

> The `evidence` object is **authoritative**. `agentId` and `taskId` inside it
> are required, and they are what reaches `Evidence`. The top-level
> tool-context fields remain optional ambient provenance and are never read into
> a proposal. When both are present and **disagree**, the call is refused rather
> than resolved by precedence — the two spellings would otherwise record one
> identity in `evidence.json` and a different one in whatever observes the
> request.

The refusal is expressed in the published input schema where the relation is
expressible, and in the handler where it is not: a cross-field equality between
a `$ref`'d context property and a nested one is the kind of constraint a schema
can state only awkwardly, and ADR-0031 decision 6's direction is that the schema
may be tighter on a *value domain* — not that every relation must live in it.
Slice B2 settles which half carries it; slice B4 drives the refusal either way.

**INV-8 stands on the same footing.** `ProposalRequest.__post_init__` refuses a
request with no source anchor and no `authored-in-theurian` label, with the
reason recorded in place: without it, a revision "is schema-valid and then exits
4 with 'has no source anchor'" after a human has already reviewed and merged it.

### 5. `writeTools: true` lands in the same commit that registers the first write-intent tool

Not a slice earlier, and not a slice later.

**The argument is that `writeTools: false` is a claim, not a label.** It is
published on `system.capabilities` — the surface `docs/protocol/mcp-tools.md`
calls "the runtime boundary for clients" — and it is asserted across the ten
files the *Context* key returns. `docs/index.md` states what a reader takes from
it: "No MCP write tool can directly create approved knowledge. Every tool a
client can call today is read-only, and `system.capabilities` reports
`writeTools: false`." Registering a write-intent tool while that value says
`false` ships a false answer to a security question, which this project's own
severity rubric grades as a published claim that misleads a security decision.

**ADR-0026 already settled the direction and this is the mirror case.** That ADR
records the flags as "evidence of *capability honesty*: a flag cannot be flipped
ahead of the feature it advertises", and names `reviewFindings: true` as "the
case in the other direction — a flag that moved *with* the feature it
advertises". This decision is that same pairing for `writeTools`: the flag moves
with the feature, in one commit, in both directions.

**ADR-0030 chose the opposite trade deliberately, and recorded what it cost.**
There, the fetch path shipped in slice 1 while `reviewIngestion` stayed `false`
until slice 3, and the ADR records the bounded residual in its own words: "for
those two slices **the machine-readable security statement reads `false` while a
fetch path ships**, which is a wrong answer to a security question even when no
feature is lost by it." What bounded it there was that no tool was callable, so
a client acting on the `false` lost no capability it could have had. **That
bound does not exist here**, because the thing being shipped *is* the callable
tool — so the residual ADR-0030 could price is one this surface cannot.

`review.generateKnowledgeCandidate` (ADR-0033) then arrives additively at slice
B5 with no flag change, because `writeTools` answers *whether any write-intent
tool exists* and not *how many*.

### 6. A refusal about an item the caller may not see is indistinguishable from a refusal about one that does not exist

This surface's refusals answer questions about items. Some of those items the
caller may not read — a `rejected` item, an item above the deployment's
sensitivity ceiling (#119, ADR-0025) — and a refusal that distinguishes
*withheld* from *absent* is a disclosure channel, which is the family
[ADR-0033](0033-knowledge-candidate-generation.md) decision 5 designs the same
shape for on its sibling surface. **The bind is over the surface, not over one
tool**, because both tools take item ids and both have refusals that are
computed over unfiltered sets — `proposeChange` through `_check_expected_revision`
(below) and `generateMigrationDraft` through the alias guard's
`AliasItemCollisionError` (decision 3, if the guard runs at generation):

> For an item outside the caller's view, **every write-intent tool on this
> surface** refuses **indistinguishably** from an item that does not exist. Such
> a refusal carries **no current-revision id** and **no status**.

The status half is not a generalisation for its own sake: it is the one value
the alias guard's refusal publishes and `_check_expected_revision`'s does not,
and a bind written only against the revision id would have left it out.

**The authority is the existing gate pair, not a second comparison.** "Outside
the caller's view" means what `may_surface` and `may_disclose` (`domain/enums.py`)
say it means, and the caller-scoped lookup this decision owes *consults* them
rather than reimplementing the test. The reason is recorded in
`may_surface`'s own docstring — the index builder used to inline the two
comparisons "which is one copy of a security rule too many" — and it is enforced
by the equality pins named in *Compliance*, which fail on an addition as well as
a removal.

**The mechanism this binds is already in the tree, and it is named rather than
inferred.** `expectedRevision` (decision 1) puts `_check_expected_revision`
(`application/proposal_service.py`) on the wire, and its three refusals publish
more than a rejection:

| Refusal | What it publishes |
| :-- | :-- |
| `<itemId> does not exist yet, so its first revision cannot replace <rev>` | non-existence |
| `<itemId> already exists at revision <rev>; an update must state which revision it replaces` (:960) | existence **and** the current revision id |
| `<itemId> is at revision <rev>, but --expected-revision names <rev>` | existence **and** the current revision id |

The current revision comes from the injected `CurrentRevisionLookup`, and the
CLI fills it with `current_revision_in(migrations, item_id)`
(`cli/propose_commands.py`), which folds every `UpsertRevision` in the
**unfiltered** loaded migration set — no status filter, no sensitivity filter
(`domain/migration.py`). On the CLI that is correct: the caller is the operator,
holding the repository the set was read from. On the wire it is an oracle over
items the read surface withholds.

**The seat of the fix is the injection point, not a string change.**
`CurrentRevisionLookup`'s own docstring already anticipates it — "Milestone 7's
MCP tools supply their own view of the same state" — so the wire path supplies a
lookup scoped to what the caller may see, and the refusal shape follows from the
lookup rather than from remembering to redact a message.

**For an in-view item the revision id stays in the refusal, deliberately.** It
is the whole remedy of an optimistic-concurrency failure (ADR-0006): a caller
told only "that is stale" has to go and find the right value, and the message
that names it is the difference between one call and three. The bind is scoped
to out-of-view items, and that scoping is the decision, not an oversight. The
same reading applies to the alias guard's status: an author refused over an item
they may read needs to know it is `rejected` in order to fix the document.

### 7. Preconditions, in order

1. **ADR-0031's input validation (slice B2)** lands before any write-intent tool
   registers. `docs/roadmap.md`'s Phase B row already binds this — "SEC-12 …
   becomes mandatory the moment a write-intent tool opens" — and the reason is
   the direct one: a write-intent tool is the first surface where a dropped key
   means a proposal a human reviews without a constraint the agent believed it
   had set.
2. **ADR-0034's merge enforcement (slice B3)** lands before any write-intent
   tool registers. The same roadmap row states why: "**T-15's 'nothing enforces
   the merge' residual is a Phase B precondition, not a background fact**:
   opening a protocol-level write path multiplies the callers who can put a file
   in `.theurian/migrations/`, and `migrate apply` does not ask whether it was
   committed."
3. **The tools and the flag (slice B4)**, in the order decision 5 fixes.

### 8. The standing guarantees extend to the new tools rather than being weakened for them

- **Registration goes through `_tool`.** The seam refuses a callable that defers
  its body — a coroutine, an async generator, a generator — at registration
  time, because `_forwarding`'s `except` arm "only sees exceptions raised
  *while it is on the stack*" and the conversion "silently stops applying, and
  only under mcp >= 2.1" (`mcp/tools.py`, `_tool`). A write-intent tool that
  wants to be async is a design change with its own reasoning, not a decorator
  choice.
- **The bytecode walk enumerates them automatically, and that is less than it
  sounds — so the gap is stated and slice B4 owes the control that closes it.**
  `tests/integration/test_mcp_tools.py::test_no_registered_tool_can_reach_a_canonical_write`
  enumerates the **built** server rather than a list, so a new tool joins the
  sweep by existing. What the sweep then asks is narrower than
  "reaches approved state":

  - **Its reach is one level.** `_referenced_names` walks the registered
    callable's own code-object chain — the `_forwarding` wrapper, its
    `__wrapped__`, and nested code in `co_consts`. It does not enter the body of
    anything the tool *calls*, so a write performed inside a collaborator is
    invisible to it.
  - **Its forbidden set is the canonical-store movers.** `WRITE_GATEWAYS`
    (`SqliteWriter`, `write_transaction`) together with the method names that
    exist on `SqliteWriter` and not on `SqliteCanonicalStore` — **17** names,
    computed live by `_mutating_method_names`. `draft`, `accept` and `_commit`
    are in none of them.

  So a tool that closes over a `ProposalService` and calls `accept()` passes the
  sweep GREEN, while `_commit` writes into `.theurian/migrations/` and
  `.theurian/knowledge/` — which is approved state in everything but the merge.
  The sweep is a real control over *canonical* writes and it does not, by
  itself, hold "no tool reaches approved state" over this surface.

  **`docs/security/threat-model.md`'s T-12 control rests on that reading**, in
  its own words: "no MCP tool reaches a write path for approved state — not
  behind a flag, not behind a permission. Write-intent tools emit proposal
  files. A test enumerates every registered tool and asserts none reaches a
  canonical write." The first sentence is stronger than the third, and slice B4
  is where the gap between them has to close rather than widen. Both the control
  and the two owed items below are named in *Compliance*.
- **ADR-0013's owed E2E is discharged at slice B4.** Its *Still owed* section
  names it — "an E2E test asserting approved knowledge is unchanged after a full
  agent session that calls every write-intent tool" — and records that the
  property "still holds vacuously today" because no such tool is registered.
  Slice B4 is where it stops being vacuous.
- **SEC-11 needs no wiring.** The secret-scan policy is read inside
  `ProposalService` rather than injected, and the docstring's stated reason is
  this arrival: an injected policy "is one a composition root can forget to
  wire, and Milestone 7's write-intent MCP tools are a second root arriving."
  So a tool that drafts through the service inherits the accept-time gate.
  **Draft-time advisory scanning is a different control and stays owed** —
  [#330](https://github.com/theurian/theurian/issues/330)'s remaining half,
  inherited by this surface rather than introduced by it.

## Consequences

### Positive

- **"AI proposes" becomes a protocol rather than a CLI.** An agent from any
  vendor reaches the same proposal path Claude Code reaches today, which is
  Phase B's stated goal.
- **One service, two front ends.** Every guarantee `ProposalService` holds —
  fresh identifiers, the digest pin, the unguarded-update refusal, the
  containment on writes — arrives with the tools rather than being rebuilt for
  them. The guarantees that live *above* the service, in
  `cli/propose_commands.py`, do not travel, and decision 3 names both of them
  rather than letting this bullet imply they do.
- **The capability surface stays honest at every commit.** No window exists in
  which the machine-readable answer and the registered tool set disagree. This
  line was scoped to *once the coupling pin in Compliance lands*, because until
  then decision 5 was a rule a reviewer enforces; the pin landed with slice B4 as
  `tests/unit/test_write_tools_flag_claims.py::test_writetools_reads_true_exactly_when_a_write_intent_tool_is_registered`,
  so the scoping is discharged rather than deleted.

### Negative

- **The wire surface is now something that can be got wrong.** Two tools with
  large inputs is a larger contract than none, and ADR-0031's schemas are what
  hold it. The two ADRs are load-bearing for each other.
- **A proposal directory is an agent-writable artifact reachable over the
  network-facing daemon.** The daemon is loopback-bound and authenticated
  (ADR-0002, ADR-0011), and the write is confined to the project's proposal
  directory by tests that diff the whole tree — but the set of principals who
  can create a file in a repository grows by exactly this change, which is the
  fact ADR-0034's precondition exists for.
- **`generateMigrationDraft`'s v1 refusals will be met by callers.** An agent
  that reaches for `upsertRevision` there is told to use the other tool; one
  that reaches for `changeSensitivity` or `restoreItem` is told to use the CLI
  and has no tool to be redirected to. That is a deliberate cost of decision 3
  and it will read as a limitation before it reads as a design.
- **Two of the three tools' owed controls were new work, not inherited.**
  Decision 6's caller-scoped revision lookup and decision 8's draft-only facade
  were properties nothing in the tree held when this ADR was accepted; the sweeps
  that looked like they held them did not. Naming that here was the point of the
  two *Compliance* entries, and pretending otherwise is what the round that found
  this was correcting. **Both were built in slice B4** — `DraftOnlyProposals` and
  the lookup `register` injects — and *Compliance* names what holds each.

### Neutral

- **The proposal format does not change.** `docs/roadmap.md`'s Phase B row
  already says so, and this ADR keeps it: the migration, the body path and the
  evidence file are what `ProposalService` already writes.
- **Nothing about the accept path moves.** `theurian propose accept` stays the
  human's command, and ADR-0027's validate-before-move stays its gate.

## What this does not close

1. **`review.generateKnowledgeCandidate`.** Its own design is
   [ADR-0033](0033-knowledge-candidate-generation.md); it registers at slice B5,
   additively.
2. **The remaining planned `knowledge.*` tools** — `getContext`, `trace`,
   `listChanges`, `checkFreshness`, `submitFeedback` — stay planned. This ADR
   neither builds nor retires them.
3. **The ten operation kinds in `generateMigrationDraft`'s v1 set are admitted,
   not exercised.** Which of them a real agent can usefully author, and what
   remedy text each refusal needs, is slice B4's to find by running it.
4. **Widening `generateMigrationDraft` to the content operations, to
   `changeSensitivity`, or to `restoreItem`.** Additive by construction
   (decision 3), and not designed here. Each of the two non-content ones needs
   its own recorded justification: what a declassification admits depends on a
   deployment ceiling the reviewer of the pull request cannot see, and a
   readmission needs the three things decision 3 names — a transition-aware
   refusal, a wire-required `reason`, and a decision about whether the
   enforcement seat is the tool or the engine (Phase D's ADR candidate #1).
5. **Whether any residual disclosure survives decision 6's bind — answered at
   slice B4, and now a measurement.** The bind names the shape and **two**
   seats — a caller-scoped `CurrentRevisionLookup` for `proposeChange`, and, if
   decision 3's open question is answered *at generation*, whatever scopes the
   alias guard's status for `generateMigrationDraft`. Slice B4 ran the equality
   this owed on both — in the same-corpus shape the decision-6 compliance entry
   records, not the two-corpora one asked for here; that entry states the
   difference and the one channel the same-corpus form cannot catch (one carried
   by collection-wide state): the **value** channel by the disclosure oracle
   (`test_write_intent_disclosure_oracle.py`), the **duration** channel by the
   zero-body-read pin (`test_pre_gate_body_materialization.py`) plus an
   out-of-band timing measurement. On the value channel the refusal a caller
   reads carries no residual, to that entry's recorded reach. On the duration
   channel the body-size term is **closed** by the body-free lookup
   (`get_item_metadata`, T-26 face 4), so a refusal's timing no longer scales
   with a withheld body; the one residual is a content-independent existence term
   of ~9 µs — a withheld item that exists reads a bodyless pointer row where an
   absent id reads nothing — which carries no withheld content, sits about 155×
   below the ~1.40 ms transport floor (TB-1), and is not a gradeable disclosure.
   The equality covered refusals as well as responses, which is why *timing* was
   in scope: the duration channel decision 6's *does not close* row named is the
   ~9 µs existence term this measurement bounds, not the body-size one B4 removed.
6. **Rate, size and concurrency bounds on a write-intent call.** A caller able
   to make the daemon spend work no recorded limit bounds is the T-6 family's;
   [#26](https://github.com/theurian/theurian/issues/26)'s concurrency cap is
   the precedent for how such a bound is recorded.
7. **Draft-time secret scanning.** [#330](https://github.com/theurian/theurian/issues/330)'s
   owed half, inherited here and not discharged here.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **Register the tools behind a configuration gate, dark, and flip `writeTools` in a later slice** | It makes a deliberately build-constant surface deployment-dependent. `system.capabilities` resolves no project and passes no `_resolve` (`mcp/tools.py` records that as the reason a *flag* may be published there while a sensitivity *ceiling* may not), so a config-dependent `writeTools` would be the one value on that block whose meaning varies per installation — and the pinned capability-key and tool-set assertions would each need a second, conditional arm. It also does not avoid decision 5's problem; it relocates it, since the build that can be configured to register a write tool is a build whose `false` is already conditional. |
| **One omnibus `knowledge.write` tool taking an operation discriminator** | Its blast radius is unnameable — "what can this tool do" has no answer shorter than the whole migration schema — and SEC-12's input schema becomes a union over fourteen operation shapes plus a body pipeline, which is the shape ADR-0031 decision 6's agreement check cannot usefully hold. Two tools with two schemas is what makes each one reviewable. |
| **Flip `writeTools: true` as its own later slice, after the tools land** | This is ADR-0030's `reviewIngestion` trade, and it does not transfer. There, no tool was callable during the window, which is what bounded the residual to "a wrong answer that costs no capability". Here the callable tool *is* the thing shipping, so the window would be a registered write path published as absent. |
| **Flip `writeTools: true` a slice early, so the flag never lags** | The mirror defect, and the one ADR-0026 names directly: a flag flipped ahead of its feature is exactly the dishonesty the pinned block exists to catch. A client told a write path exists and handed a `METHOD_NOT_FOUND` has been given a false answer in the other direction. |
| **Accept a body file path, with `security/paths.py` containment applied to it** | Containment is designed to keep *project* reads inside the project root; a caller-supplied path on this surface has no project root to be inside, since the body is by definition not yet in the project. The check that would be needed is "is this path one the caller is entitled to read", which the daemon cannot answer — it runs as the operator and sees the operator's whole filesystem. Decision 2 removes the parameter instead. |
| **Have the tools write proposal files directly, bypassing `ProposalService`** | It would produce a second procedure that must agree with `propose accept` about identifiers, digests and landed sets. ADR-0027 records what the first such disagreement cost, and ADR-0013's Compliance section records three separate accept-path procedures moved off filesystem enumeration onto the loaded `MigrationSet` for the same reason. |

## Compliance

**When this ADR was accepted it shipped no behaviour, so it had no shipped test
to name.** Its enforcement at design time was the measurements it cites; its
enforcement at implementation time was the tests slice B4 owed, and the *Still
owed* list below was written as properties an implementation must pin rather
than as files that existed — the same honest split
[ADR-0030](0030-github-review-ingestion-spawns-gh.md) states for the same
reason.

**Slice B4 has since landed, and the sentence above is corrected in place rather
than left standing:** the behaviour ships, and the section now partitions three
ways — the dated design-time measurements, unchanged; what slice B4 discharged,
each entry naming its test; and what is still owed, each naming its owner. An
entry that landed in a *different* shape from the one this ADR asked for says so
in its own words, because the difference is the part a later reader has to be
able to attack.

Measured when this ADR was accepted, and reproducible from it (2026-09-12,
`be977ea7`). **A dated reading, not a standing one** — slice B4 moved several of
these, and the *Landed* entries below say which rather than editing the readings
here:

- The `writeTools` population is **18 lines across 10 files**, with the key and
  its measured exclusions in *Context*, and the capabilities *note* is a second
  key returning **3** lines. Of the five non-prose files, **2** fail a build when
  the flag's value moves — mutation-measured in *Context*, control green.
- The proposal generator emits exactly **2** of `OperationKind`'s **14**
  members — `createItem` and `upsertRevision`, read off `_migration_document`'s
  returned `operations` list in `application/proposal_service.py`. Decision 3's
  v1 set is **10**: fourteen minus those two, minus `changeSensitivity` and
  minus `restoreItem`.
- **`restoreItem` sets `APPROVED` from any status, and nothing in `src/` checks
  the transition.** `application/migration_engine.py:497-498` passes
  `KnowledgeStatus.APPROVED` to `_set_status` (`:676-686`), which reads the
  target status from its argument and never the current one;
  `git grep -c "DEPRECATED" -- packages/theurian-core/src` answers 4 lines in 3
  files and none of them is a transition rule (the key and its limit are in
  decision 3). `REJECTED` is in that reach, and `domain/enums.py:206-219` records
  that `REJECTED` is reachable through no flag: "A rejected revision is one the
  team decided must *not* be followed, and it is also where a secret that caused
  the rejection still lives."
- **The alias guard's refusal renders an item id and its status, over the
  unfiltered set.** `AliasItemCollisionError.__init__` (`domain/errors.py:236-239`)
  formats `... collides with knowledge item {alias} (status {item_status})`, and
  `item_status` arrives from `_alias_item_collisions`
  (`application/migration_alias_guards.py:124-144`), which reads
  `_final_item_statuses(migration_set)` — a fold over `MigrationSet.ordered(...)`
  with no status filter and no sensitivity filter. That is why decision 3's
  guard-timing question is bound by decision 6.
- **The status gate and the disclosure gate are each pinned by an exact-equality
  set**, `STATUS_GATE_CALL_SITES` at **6** entries and
  `DISCLOSURE_GATE_CALL_SITES` at **5** (`tests/unit/test_gate_call_sites.py`,
  counted with `ast` over the two assignments), and each has a prose count beside
  it in `domain/enums.py` (`:231` "six call sites", `:273` "five call sites")
  that no test derives from the set. Both pairs are movers for decision 6's owed
  lookup, and they are listed in *Still owed* rather than left to be discovered.
- **The two pulled non-content operations carry opposite wire requirements.**
  `opRestoreItem` (`schemas/migrations/migration.schema.json:253-262`) requires
  `["op", "itemId"]` and types `reason` as optional; `opChangeSensitivity`
  (`:305-318`) requires `["op", "itemId", "sensitivity", "reason"]`.
- The bytecode sweep's forbidden set is **17** names — `WRITE_GATEWAYS`
  (`SqliteWriter`, `write_transaction`) plus the **15** methods on `SqliteWriter`
  that are not on `SqliteCanonicalStore`, computed live by
  `_mutating_method_names` (`tests/integration/test_mcp_tools.py`). `draft`,
  `accept` and `_commit` are in none of them, which is what decision 8's second
  bullet records.
- `ProposalRequest` declares **16** fields, and `draft` takes one further
  keyword (`local`); the wire table in decision 1 is derived from that
  declaration rather than from the CLI's option list.
- Evidence requiredness is enforced in **two** places on this path —
  `Evidence.__post_init__` and `ProposalRequest.__post_init__`, both calling
  `require_evidence` — with the reason for the second recorded in
  `require_evidence`'s own docstring.

Landed in Phase B slice B4, with the test that discharges each. Each entry keeps
the property as it was written and states what actually holds it:

- **Both tools register through `_tool`, and the walk covers them.** Landed:
  `tests/integration/test_mcp_tools.py::test_no_registered_tool_can_reach_a_canonical_write`
  and `::test_every_registered_tool_goes_through_the_forwarding_seam` are green
  over the enlarged built server, and
  `tests/unit/test_tool_error_type_contract.py::test_every_tool_is_registered_through_the_one_seam`
  holds that the two new tools go through the one seam rather than around it. The
  *positive control* that matters here is
  `test_mcp_tools.py::test_the_walk_reaches_a_real_tool_body`: the sweep is
  asserted to reach a real body, so it cannot pass over a set it never
  enumerated.
- **No write-intent tool holds an object that can move approved state, and the
  sweep is not what holds it (decision 8).** Two controls landed, because one of
  them reaches one level and the other does not:
  1. **A draft-only facade at the MCP composition root.** `DraftOnlyProposals`
     (`application/draft_only_proposals.py`) captures the two entries as closures
     rather than storing the `ProposalService`, so its reachable surface is
     exactly `{draft, draft_from_document}` — `accept` and `_commit` are not
     reachable as a method and not one attribute hop away through a `_service`
     reference. Landed:
     `test_mcp_tools.py::test_no_write_intent_tool_captures_an_object_that_moves_approved_state`
     walks each registered write-intent tool's closure cells over the **built**
     server; `::test_the_closure_walk_flags_a_tool_that_captures_a_canonical_writer`
     is the sibling control that a captured mover *is* flagged, and the same test
     carries a non-vacuity guard that the walk reaches a real captured object;
     `::test_the_object_a_write_intent_tool_is_handed_is_the_draft_only_facade`
     invokes the per-call factory a tool closes over and asserts what comes back
     is the facade and not a `ProposalService`. At the unit level
     `tests/unit/test_draft_only_proposals.py` holds the facade's own surface,
     including that it keeps no bound method back to the service.
  2. **The forbidden-name set grew to the application-layer movers.**
     `_mutating_method_names`'s result is joined with `APPROVED_STATE_MOVERS`,
     `{accept, _commit}`. Landed with a *driving* test rather than an assertion:
     `::test_a_planted_tool_calling_accept_goes_red_for_the_extended_canonical_write_pin`
     plants a tool whose body calls them and asserts it is RED for the extended
     set and GREEN for the pre-extension one, so the extension has teeth against
     the counterexample the one-level sweep would otherwise miss. **The recorded
     bound stands unchanged**: the walk sees names in the registered callable's
     own code chain and does not enter a collaborator's body, so it catches a
     direct call and nothing deeper. That bound is the reason control 1 exists
     beside it rather than instead of it.
- **`docs/security/threat-model.md`'s T-12 control sentence was rewritten in the
  registration commit.** It read "no MCP tool reaches a write path for approved
  state … A test enumerates every registered tool and asserts none reaches a
  canonical write", where the first clause is stronger than the third. It now
  names the two tools, names the draft-only facade as what holds "no tool reaches
  approved state", and calls the canonical-write sweep "a second, narrower
  control: it reaches one level and so does not, by itself, hold the first
  clause". Whether the rewrite is faithful is a reading and no mechanical check
  reaches it; that it happened in the same commit is what was not optional, and
  it did.
- **`writeTools` and the capability note moved in the registration commit
  (decision 5).** Landed, with no intermediate state: the flag, the note, both
  registrations and every assertion they falsify are one commit. The two
  assertions that move on a value flip moved —
  `tests/integration/test_mcp_tools.py::test_capabilities_report_what_is_and_is_not_built`,
  and the e2e value assertion over a real client, now
  `tests/e2e/test_daemon_single_instance.py::test_capabilities_report_write_tools`
  (it was `::test_capabilities_report_no_write_tools`) — along with the note's own
  assertion, which now demands *"No MCP tool writes approved knowledge"* and
  forbids the opposite, where it used to demand *"No write-intent tool exists"*.
  **Of the two pins this entry expected to move at *registration* time, one did
  and one did not, and the difference is recorded rather than smoothed:** the
  tool-set equality moved and became
  `::test_the_tool_set_is_exactly_the_published_nine` (it was
  `::test_the_tool_set_is_read_only`), an equality over the **nine** registered
  names that still does not notice a flag value at all — slice B5's registration
  of `review.generateKnowledgeCandidate` renamed it once more, to
  `::test_the_tool_set_is_exactly_the_published_ten`, which is the name to look
  it up under; the pinned
  capability-**key** set in `test_mcp_tools.py` did **not** move, and could not
  have — registering a tool adds no capability *key*, and `writeTools` was
  already one of them.

  **The coupling is what got pinned, and it is a new test rather than one of the
  above**: `tests/unit/test_write_tools_flag_claims.py::test_writetools_reads_true_exactly_when_a_write_intent_tool_is_registered`
  reads both facts out of `mcp/tools.py`'s source — the value a contributor
  wrote, and the names registered through the one `_tool` seam — and demands
  `writeTools` read `true` exactly when a write-intent tool is registered, with
  `::test_the_coupling_checker_demands_the_other_state_when_either_side_moves`
  as the bidirectional control. It mirrors
  `tests/unit/test_review_ingestion_flag_claims.py`'s shape, as this entry asked.
  **The one instrument named here that did not land is the wire-contract
  positive**: `test_wire_contract.py`'s `writeTools` case is still the *type*
  negative it was, and the response schema still types the field `boolean` with
  no `const`, so the conformance suite alone stays green on either value. The
  property that item existed for — the flag cannot land half-moved — is held by
  the coupling pin and the e2e value assertion instead; the gap between the
  property and the instrument is recorded in *Still owed* rather than closed by
  calling them the same thing.
- **The prose sites that record the retiring meaning moved.** The registration
  commit moved `README.md`, `docs/index.md`, `docs/protocol/mcp-tools.md` and two
  rows of `docs/roadmap.md` — *An agent cannot change approved knowledge
  directly* in §0, and appendix row 6's registered figure and names;
  the documentation cluster that follows the round moved the rest of the roadmap
  (§0's opening, §1's *Shipped* and *Partial* entries and its SEC-12 row, §2's
  *Distribute what approval recorded*, §3's change ①, and the Phase B
  Architecture, MCP/API, Security, Tests and Exit-criteria rows), the README's
  *Alongside instructions and memory* paragraph, `docs/index.md`'s
  *What is enforced, and what is convention*, and this ADR and
  [ADR-0013](0013-ai-writes-produce-proposals.md). Whether that rewrite is
  faithful is a reading and no mechanical check reaches it, which is said here
  rather than left to be inferred from a test name beside it. Two mechanical
  checks do reach part of it —
  `tests/integration/test_documented_tool_set.py` recomputes the README's and the
  protocol page's tool lists from the built server, and the roadmap appendix
  row 6's registered figure and nine names with them.
- **`generateMigrationDraft` refuses four kinds and admits ten (decision 3).**
  Landed twice, at the service and over the wire.
  `tests/integration/test_generate_migration_draft.py` drives the service entry:
  an admitted kind lands a proposal, a content operation is refused to the
  content path, a read-control operation is refused to the CLI, and a mixed
  document is refused on its *first* unadmitted operation.
  `tests/integration/test_write_intent_wire.py` drives the same through the
  registered tool over the transport, where a wiring defect lives, with the
  refused cases parametrized over `_REFUSED_TO_CONTENT_PATH`/`_REFUSED_TO_CLI` so
  the case count moves with the enum-derived set rather than being stated beside
  it, and `::test_the_wire_refused_set_is_exactly_the_two_pulled_pairs` asserting
  the refused set is a disjoint four against `OperationKind`. The partition
  itself is pinned by
  `tests/unit/test_draft_only_proposals.py::test_the_v1_operation_set_partitions_operation_kind`,
  so a fifteenth kind is admitted or refused deliberately and never by omission,
  and the gate is fail-closed for a kind in neither set. **The redirect wording
  reaches the wire**, which it did not at first: a `ProposalError` crossing the
  `_forwarding` seam loses `exc.remedy` for mcp 2.0.0 parity, so each tool body
  catches its own `ProposalError` and re-raises through `_with_remedy`, and the
  wire tests assert the redirect name is in the **message** rather than that
  `.remedy` is set.
- **Applying any admitted operation leaves every item's `(status, sensitivity)`
  pair unchanged, `deprecateItem` excepted.** Landed:
  `tests/unit/test_admitted_ops_preserve_read_controls.py` applies each admitted
  `OperationKind` against a corpus carrying every precondition an admitted
  operation needs — a relation and an alias to remove, a spec to supersede, an
  evidence anchor to remove — and compares every item's `(status, sensitivity)`
  pair before and after. Only `deprecateItem` may move the status half of its own
  item, to `DEPRECATED`, and must leave its sensitivity and every other item
  untouched. Two controls keep it from degrading:
  `::test_the_admitted_operation_map_covers_the_v1_set` asserts the map's
  coverage against `V1_OPERATION_KINDS`, so a fifteenth admitted kind must be
  given a real operation here, and
  `::test_the_base_corpus_holds_the_read_controls_the_invariant_measures`
  asserts the corpus has something to measure. The landing commit records a
  mutation run behind it — `changeOwner` grown a `sensitivity=` keyword at its
  call site flips the item's class and reddens this test — and that result is
  cited to its commit rather than restated here as a fresh measurement. Before
  this, the property was held by a keyword at a call site and nothing else:
  `application/migration_engine.py:536` spells `_replace_item(item,
  owner=operation.owner)` while `_replace_item` (`:727-737`) accepts
  `{"sensitivity", "owner", "trust_level", "status"}`, so an admitted operation
  that grew a second keyword would move a read control with no test going RED.
  This turns round 3's closure argument into a pinned property rather than a
  declaration.
- **A refusal about an out-of-view item is indistinguishable from one about an
  absent item, on **both** tools (decision 6).** Landed as
  `tests/integration/test_write_intent_disclosure_oracle.py`, and **in a
  different shape from the one asked for here, which is worth stating rather than
  smoothing over.** This entry asked for one battery run against two corpora —
  one that held the withheld items and one that never did. What landed is a
  *same-corpus* equality: one synthetic corpus holding two withheld rows
  (`rejected-store`, withheld by `may_surface`, and `confidential-item`, above
  the default serving ceiling) and one in view, with each answer about an
  out-of-view id compared against the answer about an id nothing ever stored.
  The two forms differ in what they can catch — a two-corpora run would also
  catch a channel carried by collection-wide state, which this one cannot — and
  they agree on the channel decision 6 names, which is the refusal a caller
  reads. The **withheld-reach control** is
  `::test_the_out_of_view_items_really_hold_a_revision_the_lookup_suppresses`:
  all three items are asserted to hold a real current revision, so a `None` from
  the caller-scoped lookup is suppression and not genuine absence. The fixture is
  synthetic, which is the only way to have a withheld row in a corpus whose scope
  excludes them (ADR-0030 decision 6's reasoning, one surface over). **Refusals
  are asserted to carry no status, not only no revision id.** **Timing is
  measured at slice B4 by a separate instrument** — this oracle covers *content*
  and does not measure duration; the duration channel is closed by the body-free
  caller-scoped lookup (`get_item_metadata`, T-26 face 4) and pinned by the
  zero-body-read counter (`test_pre_gate_body_materialization.py`), recorded in
  the decision-6 *Landed* entry below, no longer in *Still owed*.

  **The battery covers `generateMigrationDraft` as well as `proposeChange`**, at
  one admitted operation per item-id-bearing input *position*. Over the ten
  admitted kinds those positions are **six** distinct property names, read off
  the schema rather than listed by hand — every property whose `$ref` resolves to
  `#/$defs/itemId` — and the derivation below is now recomputed in the suite by
  `::test_the_six_positions_are_the_schema_derived_item_id_positions`, so a
  fifteenth operation with a seventh position reddens it rather than silently
  falling outside the battery:

  ```python
  # run from the repository root; prints the table below
  import json, pathlib

  defs = json.loads(pathlib.Path("schemas/migrations/migration.schema.json").read_text())["$defs"]
  pulled = {"operation", "opCreateItem", "opUpsertRevision", "opChangeSensitivity", "opRestoreItem"}
  positions: set[str] = set()
  for name, body in sorted(defs.items()):
      if not name.startswith("op") or name in pulled:
          continue
      ids = sorted(
          p for p, q in body.get("properties", {}).items() if q.get("$ref", "").endswith("itemId")
      )
      positions |= set(ids)
      print(f"{name:26}{ids}")
  print("distinct positions:", len(positions), sorted(positions))
  ```

  ```console
  opAddAlias                ['alias', 'itemId']
  opAddEvidence             ['itemId']
  opAddRelation             ['sourceItemId', 'targetItemId']
  opChangeOwner             ['itemId']
  opDeprecateItem           ['itemId', 'supersededBy']
  opRegisterSpecification   ['itemId', 'specId']
  opRemoveAlias             ['alias']
  opRemoveEvidence          ['itemId']
  opRemoveRelation          ['sourceItemId', 'targetItemId']
  opSupersedeSpecification  ['specId', 'supersededBy']
  distinct positions: 6 ['alias', 'itemId', 'sourceItemId', 'specId', 'supersededBy', 'targetItemId']
  ```

  A battery scoped to `itemId` alone would pass a build that answered through
  `addRelation`'s `targetItemId` or `deprecateItem`'s `supersededBy`, which is
  why the obligation is stated per position and derived from the schema — a
  fifteenth operation with a seventh position joins it by existing.
  **And the refusals are asserted to carry no status**, not only no revision id —
  the alias guard's message is the one that publishes a status today
  (decision 3), so a battery written against the revision id alone would pass a
  build that answered `(status rejected)`.
- **The wire path's `CurrentRevisionLookup` is caller-scoped (decision 6).**
  Landed: `register` builds the lookup at the MCP composition root and it
  consults `may_surface` and `may_disclose`, so an item this caller may not see
  answers `None`.
  `test_write_intent_disclosure_oracle.py::test_proposechange_refuses_an_out_of_view_item_like_an_absent_one`
  drives it, and `::test_proposechange_carries_the_revision_for_an_in_view_item`
  is the control the entry asks for — without it, a lookup returning `None` for
  everything would satisfy the equality while breaking the
  optimistic-concurrency remedy for in-view items. The landing commit records a
  mutation run in both directions — dropping the `may_surface`/`may_disclose`
  gate leaks the out-of-view revision, and returning `None` for everything drops
  the in-view one — cited to that commit rather than restated here.

  **And the lookup is body-free (T-26 face 4).** It reads `get_item_metadata`,
  not the body-joining `get_item`, so a withheld item's refusal materialises no
  body and its *timing* is content-independent — it does not scale with the
  withheld body's size.
  `test_pre_gate_body_materialization.py::test_the_real_propose_change_handler_reads_no_body_before_a_withheld_refusal`
  pins the zero body read on the withheld refusal (RED when the closure's
  `get_item_metadata` reverts to `get_item`), and
  `::test_the_real_propose_change_handler_names_the_current_revision_for_an_in_view_draft`
  pins `include_unapproved=True` in the lookup — the `draft`/`proposed` statuses
  `may_surface` treats differently under the flag — so the in-view concurrency
  remedy the #210 class depends on stays live. The one residual is a
  content-independent existence term of ~9 µs (a withheld item that exists reads a
  bodyless pointer row; an absent id reads nothing), about 155× below the
  ~1.40 ms transport floor (TB-1) and not a gradeable disclosure — the
  measurement open-question 5 owed.

  **This item moved the pinned counts it said it would, in the commit that added
  the call site.** The lookup is one call site on each gate, so
  `STATUS_GATE_CALL_SITES` is **7** and `DISCLOSURE_GATE_CALL_SITES` is **6**,
  both carrying the new `("mcp/tools.py", "register._draft_only_proposals.current_revision")`
  entry, and both prose counts in `domain/enums.py` moved with them — `may_surface`'s
  docstring now reads *seven* and `may_disclose`'s *six*, each with the new site
  enumerated. The disclosure *axis* was not touched, so
  `requirements-analysis.md`'s `enforced-axes` block and `SECURITY.md`'s copy of
  it did not move. **The table below is the reading this ADR was accepted
  against and is kept as that**, not corrected in place: it is what the four
  records held before the lookup landed.

  | Record | What it holds now | Measured |
  | :-- | :-- | :-- |
  | `tests/unit/test_gate_call_sites.py`'s `STATUS_GATE_CALL_SITES` | an **exact-equality** set of `(module, function)` pairs, so it fails on an addition as well as a removal | **6** entries |
  | the same file's `DISCLOSURE_GATE_CALL_SITES` | the same shape for `may_disclose` | **5** entries |
  | `may_surface`'s docstring (`domain/enums.py`) | "it is consulted from six call sites", enumerated in prose | the word **six** |
  | `may_disclose`'s docstring (`domain/enums.py`) | "Consulted from five call sites" | the word **five** |

  Counted with `ast` over the two assignments in `test_gate_call_sites.py`
  (`STATUS_GATE_CALL_SITES 6` / `DISCLOSURE_GATE_CALL_SITES 5`); the two
  docstring words are read at `domain/enums.py:231` and `:273`. Both sets are
  asserted by equality, so neither degrades silently — but neither prose count
  is derived from its set, so those two move by hand or not at all.
  **And if the disclosure axis itself is touched**,
  `docs/architecture/requirements-analysis.md`'s `enforced-axes` block (:103-106,
  "**three** enforced axes — `chunks.project_id`, `chunks.status` and
  `chunks.sensitivity`") and `SECURITY.md`'s copy of it move too; the same test
  file checks both against what `_scope` emits, token set and spelled count.
- **An `agentId`/`taskId` stated twice and disagreeing is refused (decision 4).**
  Landed over the wire, with both controls:
  `test_write_intent_wire.py::test_a_top_level_identity_that_disagrees_with_evidence_is_refused`,
  `::test_a_top_level_identity_that_agrees_is_accepted` and
  `::test_the_top_level_identity_absent_is_accepted` — so the check is not
  satisfied by a build that refuses every call carrying the field. The evidence
  block stays the authoritative one; the top-level pair remains ambient
  provenance no handler reads into a proposal, which is
  [#665](https://github.com/theurian/theurian/issues/665)'s standing residual and
  not something this decision closes.
- **The operations path lands through the same guards as the content path,
  reached through the injected validator.** Landed in part.
  `test_generate_migration_draft.py::test_it_reaches_the_injected_validator`
  spies the injected `MigrationDocumentValidator` and asserts the document it saw
  is the *stamped* one — the minted id, not a caller value — so the generator and
  the accept-time rehearsal validate the same bytes;
  `::test_the_landed_migration_is_schema_valid_and_carries_the_operation`
  re-validates the written file against the published migration schema;
  `::test_the_service_mints_the_migration_id_over_a_caller_supplied_one` holds
  that a caller cannot choose an id that collides with a landed migration; and
  `::test_a_document_the_schema_rejects_is_refused_and_nothing_is_written` is the
  nothing-written arm. **Two halves of this entry did not land** and are in
  *Still owed*: the end-to-end drive through `theurian propose accept`, and a
  test for the structural no-direct-loader-import property.
- **The CLI-only guarantees have wire equivalents (decision 3).** Landed, and the
  two rows landed differently. Duplicate `labels[]` is a *schema* refusal naming
  the `labels` key path over the wire
  (`test_write_intent_wire.py::test_duplicate_labels_are_refused_by_the_schema_over_the_wire`).
  The `body` cap is published as a `maxLength` on the `body` key and pinned to
  `MAX_REQUEST_BODY_BYTES` by `tests/unit/test_input_schema_bounds.py`, but
  `::test_the_body_size_bound_is_a_schema_constraint_on_the_body_key` drives it
  against the **loaded schema** rather than over the wire, because over the wire
  the rendered-character bound and the transport cap are both tighter and fire
  first — so the published `maxLength` is not the refusal a caller meets
  ([#699](https://github.com/theurian/theurian/issues/699)). The unit it counts
  is [#691](https://github.com/theurian/theurian/issues/691)'s. Both are recorded
  in *Still owed* rather than read as discharged.
- **ADR-0013's owed E2E is discharged.** Landed as
  `tests/e2e/test_write_intent_session.py::test_a_session_calling_every_write_intent_tool_leaves_approved_knowledge_unchanged`:
  against a real daemon the session calls each write-intent tool, and approved
  knowledge is asserted unchanged two ways — the read tools report the identical
  status and approved item *through* the daemon, and the canonical store's main
  file and the approved bodies are byte-identical on disk (SQLite's read-time WAL
  and SHM sidecars excluded, since a read creates empty ones). The non-vacuity
  control is that each call is asserted to have landed a distinct proposal. The
  landing commit records a mutation run against an isolated branch build — a
  write-intent tool appending to an approved body reddens the digest assertion —
  cited to that commit rather than restated here.
  **[ADR-0013](0013-ai-writes-produce-proposals.md)'s own *Still owed* entry moved
  to a *Landed in Phase B slice B4* section naming this test**, and the coverage
  residual that section first recorded — a committed argument set, which cannot
  redden when a *newly* registered write-intent tool is missing from it — was
  closed in the same slice: the set is now derived from `tools/list`, keyed on
  decision 4's required `evidence` object, and asserted equal to the arguments the
  session carries. What remains is the bound of that key, recorded in ADR-0013's
  *Still owed* rather than here, since it is that ADR's property.
- **Every wire field carries its published input schema (ADR-0031).** Landed:
  `schemas/mcp/knowledge-propose-change-input.schema.json` and
  `schemas/mcp/knowledge-generate-migration-draft-input.schema.json` ship, and
  both tools join `test_input_validation_wire.py`'s per-tool parametrization, so
  they are covered by ADR-0031's fail-closed rule — a registered tool that
  resolves to no loaded schema is refused at dispatch — rather than by being
  remembered. Owed there rather than here, and named here because this surface is
  the one that makes it mandatory.

Still owed, with the milestone that will satisfy it:

**The first three below are addressed to a slice and to no issue.** Measured
2026-09-15, `gh issue list --state open --search "write-intent OR proposeChange
OR generateMigrationDraft OR facade OR draft-only"` returns nothing that covers
any of them, so this section is their only owner — a reader should treat that as
the gap it is rather than assume a tracker entry exists. The last three each name
their issue. (Open-question 5's timing measurement, formerly a fourth
slice-addressed item here, landed at slice B4 and moved to the decision-6
*Landed* entry above.)

- **Slice B5 or later — a file path is refused by construction, and nothing
  recomputes it (decision 2).** The property holds as shipped: neither published
  input schema declares a path, URI, or reference field, and neither handler
  signature has a path parameter. **What is owed is the check**, and the shape
  matters — a refusal test would pin today's spelling of a rejection, while what
  this needs is a structural test that no write-intent tool's input schema
  declares a path-shaped field. Today the absence is held by review and by
  ADR-0031's `unevaluatedProperties: false` refusing any key the schema does not
  name, which stops a *caller* smuggling one and does nothing about a schema
  someone widens.
- **Slice B5 or later — a value-level wire-contract assertion on `writeTools`.**
  The *Landed* entry above records why: the conformance file's case is a type
  negative and the response schema types the field `boolean` with no `const`, so
  the conformance suite alone is green on either value. The coupling pin and the
  e2e value assertion hold the property; this closes the gap between the property
  and the instrument this ADR named.
- **Slice B5 or later — the two halves of the operations-path entry that did not
  land.** A test that a document drafted through `draft_from_document` produces a
  proposal directory `theurian propose accept` accepts, driven through the
  shipped commands rather than asserted about the service; and a structural test
  that the entry imports no migration-loader symbol directly (ADR-0003). The
  second holds today by reading `application/proposal_service.py`'s imports, and
  by nothing else.
- **[#691](https://github.com/theurian/theurian/issues/691) — the unit the
  published `body` `maxLength` counts.** JSON Schema counts code points; the
  constant it transcribes counts bytes, so the bound admits up to four times the
  bytes it names. Recorded, not closed.
- **[#699](https://github.com/theurian/theurian/issues/699) — that `maxLength` is
  unreachable over the shipped transport.** The rendered-character bound and the
  transport cap are both tighter and fire first, so a caller never meets the
  bound this surface publishes.
- **[#665](https://github.com/theurian/theurian/issues/665) — the top-level
  `snapshotId`, `agentId` and `taskId`.** Admitted by the enforced contract on
  both new tools, as on the other seven, and read by no handler. Decision 4 makes
  the *evidence* pair authoritative and refuses a disagreeing top-level one; it
  does not make the top-level trio read.
