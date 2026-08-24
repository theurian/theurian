# RAPTOR forest

Decision record: [ADR-0008](../adr/0008-raptor-forest.md), as amended in
Milestone 6. Where this page and that ADR would both state a mechanism, this one
points at it — a second telling is a second thing to keep true.

**The forest is built, purged, and retrieved through.** `theurian index build
--raptor` derives the three tiers below and writes them into the index — a build
without the flag writes zero node rows — a withdrawal purges them transitively
and re-derives what survives, and retrieval routes a query through summary nodes
to the leaves beneath them. `system.capabilities` reports `"raptor": true`, and a
surfaced leaf carries a `raptorPath`: its forest ancestry, catalog root to leaf.
A summary node is a router and is never itself a result row (ADR-0008 decision
8). This page is present-tense throughout; the work still ahead — incremental
subtree rebuild (deferred, decision 3), an abstractive adapter, and the
prompt-constraint, staleness and groundedness checks that come with one — is
marked *deferred* or *owed* where it appears, not "will".

## What RAPTOR does

RAPTOR builds a tree of recursive summaries over a corpus, so a broad query can
match a high-level summary and retrieval can descend from it to the specifics.
It is aimed at "what is our approach to authentication?" — a question flat chunk
retrieval handles badly, because no single chunk contains the answer. Theurian
answered that question from leaf chunks alone until the forest was retrieved
through: a `--raptor` index now matches a high-level summary and descends from it
to the specifics, reaching sibling leaves a leaf-only search for the same term
misses (`test_a_summary_match_routes_to_sibling_leaves_a_leaf_search_misses`).

## Why a forest and not a tree

Applied naively to a whole organization's knowledge, RAPTOR produces one
enormous tree with two failure modes: one operational, one a security incident.

**Operational.** Every edit invalidates summaries all the way to the root. A
one-line ADR correction triggers a full-tree rebuild.

**Security.** A summary node is *a new document synthesized from its children*.
If a node's children span a `restricted` incident report and a `public` API
guide, the summary text contains restricted facts and inherits whichever ACL the
implementation happened to assign. The leak then lives in generated text with no
anchor to the restricted source, which is what makes it nearly undetectable
afterwards.

That describes the un-partitioned alternative, not Theurian's retrieval. Two
separate things stop it here, and only one of them is partitioning.

Since [#119](https://github.com/theurian/theurian/issues/119), sensitivity is an
enforced read control rather than a published label. A build writes no chunk row
above the deployment's declared ceiling and therefore derives no summary node
from one (ADR-0025 part 1); every retriever emits `sensitivity IN (…)` — `_scope`
over `chunks` and `_node_scope` over `nodes` alike (part 3); and a
reclassification purges the affected rows out of the published build in the same
`migrate apply` (part 2). So on a deployment that does not serve `restricted`, a
node's children cannot span a `restricted` incident report and a `public` guide:
the report is not in the file the forest was derived from.

What *partitioning* adds is orthogonal to that ceiling, which is why one does not
make the other redundant. A deployment that serves **both** levels still derives
no summary mixing them into one text, because the tree identity includes
sensitivity. That is a build-time property and makes no serving decision on its
own. The per-axis register in
[requirements-analysis.md](requirements-analysis.md) records where each axis
stands.

## The structure

```mermaid
flowchart TB
    subgraph Scope1["Scope: (project-a, local, internal, default, architecture, approved)"]
        GC1["Global Catalog Tree"]
        GC1 --> DT1["Domain Tree: decisions"]
        GC1 --> DT2["Domain Tree: conventions"]
        DT1 --> DOC1["Document Tree: auth-policy"]
        DT1 --> DOC2["Document Tree: transaction-boundary"]
        DOC1 --> L1["Leaf chunks → revision + line range"]
        DOC2 --> L2["Leaf chunks"]
    end

    subgraph Scope2["Scope: (project-a, local, restricted, security-team, operations, approved)"]
        GC2["Global Catalog Tree"]
        GC2 --> DT3["Domain Tree: incidents"]
        DT3 --> DOC3["Document Tree: incident-2026-03"]
        DOC3 --> L3["Leaf chunks"]
    end

    Scope1 -. "no node spans these" .-x Scope2

    style Scope2 fill:#5a3a7a,color:#fff
```

Tree identity is
`(project, tenant, sensitivity, acl_group, namespace, status)` — `Scope` in
`domain/values.py`. A node whose children differ in any component **has no tree
it could belong to**, which is what makes the isolation structural rather than a
check somebody has to remember to write. That distinction is the whole point: a
forgotten check produces exactly the undetectable leak described above.

`status` is the sixth component, added by the Milestone 6 amendment to ADR-0008
decision 1. The reason is that a build has a
flavor: `theurian index build --include-unapproved` holds drafts and proposals
beside approved revisions, so without `status` in the tuple a node can be
summarized from children that straddle that boundary, and which query flavor
then traverses it is decided by a column the builder happened to fill. The
amendment states what that costs — routing and recall, not disclosure — and why
`validity` is deliberately *not* a seventh component.

What holds the rule, in three places, because no one of them can hold it alone.
`SummaryNode.__post_init__` (`domain/raptor.py`) raises
`InvariantViolationError` when a declared child scope differs from the node's
own. Those children are *declarations*, so a builder passing its own scope n
times would satisfy that type without consulting a single child: `IndexableNode`
closes that half by refusing a node whose declarations do not stand one per
source, and `application/forest_builder.py` supplies each declaration from the
chunk or node it summarizes. `SummaryNode.tree_id` is `Scope.digest`, total over
all six components; what a stored row carries is `tree_identity`, which adds the
tier and the within-scope partition on top of it, or two items with duplicate
content would mint one id for two nodes.

The result is asserted over a real build rather than at the value level alone:
`tests/integration/test_forest_builder.py::test_no_node_stands_on_chunks_that_disagree_on_a_scope_component`
walks `node_derivation` transitively and requires every leaf chunk a node stands
on to agree on all six components, parametrized over the three axes a corpus can
vary — namespace, sensitivity and status. Tenant and ACL group are not among them
and cannot be: the migration engine refuses a revision naming any value but the
default, so no corpus can carry a second one to mix.

**One limit, stated because it decides whether the assertion may ever be
relaxed.** A declaration equal to the parent's scope cannot be told apart from
one copied off the parent — for a correctly clustered node the two are the same
value. What the refusal catches is a declaration standing for no source, which is
the shape a clusterer crossing a scope boundary produces; the grouping itself is
attacked directly by
`tests/unit/test_forest_derivation.py::test_a_node_never_mixes_two_statuses_under_one_namespace_and_kind`.

The scope key joins the six components with a unit separator (`\x1f`), which no
component can carry: `AclGroup`, `TenantId` and `namespace` reject C0 controls
and DEL at construction, `ProjectId` is a kebab-case slug, and `sensitivity` and
`status` are enums. Without that validation, `acl_group="a\x1fb"` +
`namespace="c"` and `acl_group="a"` + `namespace="b\x1fc"` render one key — a
collision reviewers demonstrated here while the docstring already claimed the
property.
`packages/theurian-core/tests/unit/test_scope_isolation.py::test_all_scope_pairs_are_distinguishable`
asserts that all **64** combinations of two values per component produce 64
distinct digests, and pins the product itself (`assert len(scopes) == 64`) so
that a dropped component cannot pass as a smaller exhaustive run. Three tests
beside it reject a separator in each free-form component — `acl_group`,
`tenant_id`, `namespace` — and a fourth pins that neither half of the measured
colliding pair can be constructed at all.

## Three levels

ADR-0008 decision 2, and `application/forest_builder.py` builds exactly these,
deepest tier last.

| Level | Scope | Summarizes | One node per |
| :-- | :-- | :-- | :-- |
| 1 Document Tree | one knowledge item | that item's chunks | item revision, in scope |
| 2 Domain Tree | one kind, inside a scope | its document nodes | kind, in scope |
| 3 Global Catalog Tree | one scope tuple | its domain nodes | scope |

Decision 2 says "one namespace **or** kind" for the Domain tier, and inside a
scope that reduces to `kind`, because the scope has already fixed the namespace.
`IndexableChunk` carries `kind` for that reason. No *retrieval* reads it, but the
withdrawal purge does: it re-derives each affected scope's Domain trees from the
published index's surviving rows, so `kind` is persisted on `chunks.kind` at index
schema v5 rather than only consumed in memory by the build that produced the
chunk. Without the discriminator a scope holds exactly one Domain tree, the
Catalog tier always has a single child, and three levels are structurally
unreachable.

A level is skipped when it has fewer than `minChildrenPerSummary` children:
summarizing one document produces a paraphrase, which costs tokens and adds
nothing. **The threshold is real now and the config file is still unread.**
`ForestOptions` carries `max_levels` and `min_children_per_summary` with the
defaults `schemas/config/project-config.schema.json` declares, and
`tests/unit/test_forest_derivation.py::test_the_option_defaults_are_the_config_schemas_own`
pins the two against that file so they cannot drift before a loader exists.
Nothing in `src/` reads `.theurian/config.yaml`, so **the CLI flag is the switch
and the config key is not** — `raptor.enabled` is declared `false` in the schema
and set `false` in `examples/sample-project/.theurian/config.yaml` (ADR-0008
decision 10's two places, both flipped by the builder change), and `theurian
index build --raptor` is what actually turns a forest on, for one build.

`summary_max_tokens` is the third option and deliberately has no config key: it
is a constant, never a share of anything the corpus decides, because a budget
divided by document count would move a visible node's text when a withheld
document was added or removed. See the sixth summarization constraint below.

## Building and publication

```mermaid
flowchart TD
    A["Knowledge changed"] --> B["Write a new file under a .building name<br/>chunks, then the forest over them with --raptor"]
    B --> F["A build: refuse one that indexed nothing<br/>A purge: six post-conditions"]
    F -->|refused| G["Discard the build.<br/>The published index is untouched."]
    F -->|passes| R["os.replace into the final name"]
    R --> H["Atomically swap<br/>.theurian/state/active-index.json"]

    style H fill:#1f6f4a,color:#fff
    style G fill:#8a2f2f,color:#fff
```

Publication is a pointer *file*, not a table: `.theurian/state/active-index.json`
names the build that answers queries, written temp-then-`os.replace` so a reader
never observes a half-written pointer
([ADR-0022](../adr/0022-index-lives-in-its-own-database.md) point 5,
[ADR-0024](../adr/0024-a-purge-is-a-build.md)). There is no `active_indexes`
table, in this schema or any earlier one.

**A *partial* build is never pointed at, structurally.** Both writers build
under a `.building` name and `os.replace` into the final one, so a file under
that name is complete by construction, and the pointer is written only
afterwards. *Unverified* is the narrower claim, and it belongs to the purge
path: `_verify`'s six post-conditions raise before anything is published. The
ordinary build path has one post-build refusal, not six — `_refuse_if_empty`
discards a build that indexed nothing while the canonical store holds knowledge,
because publishing that puts a correct-looking empty index in place, where every
later search answers `count: 0` with `indexed: true`.

Search does not go dark across a rebuild, but it can lose its *indexed* answer.
Publishing no longer deletes the build it replaces — reclaiming is the explicit
`theurian index gc` (ADR-0024 point 6) — and a request that has acquired its
read session keeps answering from its own build even if `gc` unlinks the file
underneath (point 7). The window is narrow and named: a request reaped between
resolving the pointer and acquiring that connection has no descriptor to
protect, so it answers from the substring-scan fallback instead of the index.

**Incremental subtree rebuild is deferred; the forest is re-derived in full
inside the existing build path.** `IndexBuilder` derives it from the chunks it
has just written, in memory, rather than by reading the index back — which is
what keeps the derivation a pure function of that build's own output — and
nothing is reused across builds. A full re-derivation is one code path whose
output is a function of canonical state alone, while an incremental one is a
second path that has to agree with the first on every input and fails invisibly:
a node left standing that no longer matches its children. The argument, the
deferral, and what the milestone that lifts it owes are in ADR-0008 decision 3's
amendment. What the full path buys is checkable:
`test_rebuilding_the_same_state_produces_a_byte_identical_forest` rebuilds an
unchanged state and requires every node row to match, `index_build_id` excepted.

Withdrawal is the one traversal that *rewrites* node rows — retrieval reads them
— and it is a build rather than an edit: a purge copies the previous build,
deletes from the copy, verifies, and republishes (ADR-0024). Its rule for derived rows is
universal grounding — a node survives only if *every* declared source terminates
at a surviving chunk in finitely many steps, so one good parent and one that
leads nowhere is still removed — and `_verify` refuses to publish a build
holding an unprovenanced node, an edge whose source is gone, a node on a
provenance cycle, or an orphaned node embedding. The predicate is `_DOOMED` in
`infrastructure/sqlite/index_purge.py`, stated there and not restated here. That
traversal now meets forests a builder shaped rather than only fixtures written in
raw SQL:
`tests/integration/test_forest_builder.py::test_withdrawing_an_item_takes_its_document_node_and_the_domain_node_above_it`
withdraws one item of three and requires its Document node and the Domain node
above it to go while the other two survive, and
`test_a_purged_forest_leaves_no_residue_in_a_node_text_index` reads `nodes_fts`
and `nodes_trigram` through `fts5vocab` to check that no term of the withdrawn
document is still indexed.

**Withdrawal re-derives each affected tree, which is what ADR-0008 decision 9
settles for.** After the delete of every ungrounded node, the purge re-derives
each *scope that lost a row* whole — every tree in it, over the surviving chunks
it reads back from the building file. Clearing the way for the fresh trees is
`SqliteIndexStore.delete_nodes_grounded_in_chunks`, seeded on the scope's
surviving chunks and walking `node_derivation` upward: it deletes the scope's
*entire* current node set, not only the trees the fresh derivation happens to
reproduce. That distinction is what a Domain fan-out re-batch needs — collapsing
`kind#0 .. kind#(b-1)` to one fewer batch on a withdrawal leaves a surviving top
batch the fresh set never names, and a delete keyed on the fresh trees alone
misses it, so the purge fails closed rather than publishing. Whole-scope
re-derivation is coarser than decision 9's per-tree ancestor closure and subsumes
it: an affected scope's unaffected trees re-derive byte-for-byte because
derivation is deterministic, and a scope that lost nothing is never read. A
purged build's forest then equals one built from a corpus that never held the
withdrawn rows, at the fan-out boundary included. Delete-only did not: deleting a
node outright breaks that equality in the other direction, because the
never-held corpus would have built a node from the children that survived, and
content-addressing makes that node a different one than the old node minus a
child.
`tests/integration/test_forest_purge_equality.py::test_a_purged_forest_equals_one_that_never_held_the_withdrawn_rows`
holds the equality over the node tables' full contents — node rows, derivation
edges and node vectors — with a stale pre-purge control asserted different, and
`tests/integration/test_forest_purge_recompute.py` holds it at the fan-out
boundary specifically — a re-batching withdrawal at the exact boundary, from the
final batch, as a bulk withdrawal, and across two scopes withdrawn from at once.
It is the two-corpus shape that
`test_index_purge.py::test_a_purged_build_answers_as_if_the_rows_were_never_indexed`
holds for chunks, extended to the derived layer. The re-derivation is application
policy (`ForestBuilder` and the summariser and embedder) injected into the
infrastructure purge as a callback, not imported up into it, so ADR-0003's
layering holds. It is scoped to deterministic pure providers — the extractive
default; a non-deterministic provider's delete-and-mark-stale fallback (decision
9) is recorded in `make_forest_recompute`'s docstring and built by nothing.

## Node provenance

Every node row carries the provenance below — `nodes` in
`infrastructure/sqlite/index_schema.py`, added at index schema v4 (the schema is
v5 today, which adds `chunks.kind` for the purge's re-derivation), written by
`IndexStore.add_nodes` in the same transaction as its derivation edges, because a
node without its edges cannot say what it holds and is a state `_verify` refuses
to publish:

```text
node_id, tree_id, level, node_type, text, content_hash,
summary_model, summary_model_revision, summary_prompt_hash,
embedding_model, embedding_model_revision, embedding_dimension,
source_revision_id, index_build_id
```

plus `project_id`, `sensitivity` and `status` to filter on. Provenance edges
live in `node_derivation`, one row per (node, the chunk or node it was built
from), which is what the purge above walks. `node_id` is content-addressed —
`node_identity(tree_id, level, the children's content hashes sorted)`, pinned
against a literal by
`tests/unit/test_raptor_scope.py::test_a_node_id_is_pinned_to_its_exact_join_order_sort_and_encoding`
— and `source_revision_id` names the one revision a Document node was built from,
empty above that tier, because a node built from other nodes has no single
revision to name and the purge reaches it through its edges instead.

Model and prompt identity **are** persisted: a node carries the `model_id`,
`model_revision` and `prompt_hash` of the provider that summarized it. What is
missing is the comparison — nothing checks a stored `summary_prompt_hash`
against the active configuration, so a node summarized under an older provider is
not detected as stale and not rebuilt. Changing a prompt therefore still leaves
an index silently mixing two generations with nothing reporting it. That item
stays owed in ADR-0008's Compliance section, on the half that was always the
point.

**Their own tables, not `chunks` rows with a `derived` flag.** `chunks_fts` and
`chunks_trigram` are external-content FTS5 tables, and `bm25` scores every row
against collection statistics computed over *every* row in `chunks` — `N`,
`avgdl`, and the per-term document frequencies. A summary systematically repeats
the terms of the children it was built from, so a summary row in `chunks` would
move all three under every ordinary leaf query, and a visible leaf's rank would
become a function of the forest's shape. `nodes` therefore has its own
`nodes_fts`, `nodes_trigram` and `node_embeddings`; v4 also drops
`chunks.derived` and `chunk_derivation`, the v3 column and table added for a
writer that turned out to want different storage (ADR-0008 decision 5's
amendment).

One gap the tables leave open, and one now closed. Tenant, ACL group and
namespace have no node column — `tree_id` encodes the whole six-component tuple,
so a node's tree is expressible, but no predicate can filter on those axes. What
is closed is project, status and sensitivity for node reads:
`SqliteIndexStore._node_scope` is the single enforcement point, the counterpart of
`_scope` for chunk reads, filtering `nodes.project_id`, `nodes.status` and — since
[#119](https://github.com/theurian/theurian/issues/119) phase 4 —
`nodes.sensitivity IN (…)` over the deployment's expanded grant. It is
load-bearing rather than agreeing with the leaf gate by accident:
`tests/integration/test_forest_node_scope.py` neutralises each clause in turn
over a node whose scope disagrees with its one leaf's — so the leaf gate has
nothing to withhold and only `_node_scope` decides — and requires the matching
isolation test to go RED (ADR-0008 decision 5's amendment, discharged). The
upward walk carries its own gate too, on the same three axes: `walk_raptor_path`
filters its `nodes` lookup on the surfaced leaf's own project, status and
disclosure class — each read off the anchoring chunk row and never off the
caller's grant, because this asks whether an ancestor is in *that leaf's* scope
rather than re-asking what the deployment serves — so a scope-disagreeing
ancestor cannot ride out on a `raptorPath` even were the construction-time
invariant ever violated.

## Summarization constraints

Five constraints on the summary itself, to be enforced in the prompt and
validated during evaluation — for a prompted adapter, neither exists yet: the
one adapter that exists (below) is extractive and carries no prompt at all,
and there is no retrieval evaluation harness to validate against.

| Constraint | Why |
| :-- | :-- |
| State no fact absent from the children | A knowledge platform that emits fiction is worse than one that emits nothing |
| Treat imperative source text as **data being described** | A document saying "ignore previous instructions" is a document that says that (SEC-16) |
| Retain child references | Every summary must be traceable to source text |
| Mark uncertainty rather than resolving it | A confident wrong summary is the expensive failure |
| Inherit sensitivity and ACL | Uniform by construction, given the scope rule |

**Two of the five are held by the builder rather than by any prompt, and are
therefore true of the extractive default as well.** Child references: every node
written names its sources in `node_derivation`, one edge per source, and
`IndexableNode` refuses a node whose declared child scopes do not stand one per
source. Sensitivity and ACL: a node's row carries the scope its children share,
because the scope is what decided which tree it belongs to. The sensitivity in
that scope is the item's current classification — the value `index_builder`
stamps on a chunk, carrying the same authority `status` does — captured at build
time, not the immutable revision's authored label. Uniform by construction is a
build-time property and stays one; what changed with
[#119](https://github.com/theurian/theurian/issues/119) is that the axis is now
also a *retrieval* control, so the two hold different halves. A
`changeSensitivity` no longer waits for the next build to take effect on either:
`_node_scope` filters a node on the deployment's grant on every query, and an
item reclassified past the ceiling its build ran under is purged out of the
published build by the same `migrate apply` (ADR-0025 parts 2 and 3). What still
waits for a build is a reclassification *into* the ceiling — a purge deletes and
cannot restore a row the build was never allowed to write.

Implementations must wrap source content in a delimited untrusted region and
never interpolate it into a system-role message. The port docstring states that
requirement; the one adapter that exists sends no prompt anywhere, so the
requirement is unexercised rather than unenforceable — it binds a future
prompted adapter, not this one.

A sixth constraint was added in Milestone 6, and no prompt can carry it because
it constrains the adapter rather than the model: **a summarizer is a pure
function of its children's texts, its scope tuple, and a configuration-derived
`max_tokens`** — no corpus-wide statistic may enter, and `max_tokens` must never
be a corpus-derived quantity. A statistic computed over a corpus is computed
over documents the caller may not read, so a summarizer using one would write a
withheld document's influence into the text of a *visible* node, where no purge
reaches it: deleting a row cannot delete from a sentence the reason that
sentence was chosen. ADR-0008 decision 6's amendment names the three carriers of
that class, and which two this constraint closes;
`SummarizationProvider.summarize` takes `texts`, `scope` and `max_tokens` and is
handed no corpus handle, so the port is shaped for the constraint without
enforcing it.

**The `max_tokens` half is the caller's to hold, and the builder holds it.** A
summarizer is handed the number and never the recipe, so no adapter can tell a
constant budget from a corpus-derived one. `forest_builder.SUMMARY_MAX_TOKENS` is
one chunk's worth — the chunker's target passage priced at the estimator's
characters-per-token — passed verbatim to every call, never divided by a cluster
size or a document count, and it is the one `ForestOptions` field with no config
key, so no configuration can turn it into a corpus-derived quantity either.
`tests/unit/test_forest_derivation.py::test_the_summary_budget_is_a_constant_and_not_a_share_of_the_corpus`
holds it with a recorder that sees what each call was charged. Carrier (b) —
which children cluster together — is closed by the withdrawal purge's tree-level
re-derivation and held by
`tests/integration/test_forest_purge_equality.py::test_a_purged_forest_equals_one_that_never_held_the_withdrawn_rows`,
which varies the child set by removing a withheld document and regrouping the
visible ones, and by
`tests/integration/test_forest_purge_recompute.py`, which varies it at the one
boundary the first does not reach — a withdrawal that re-batches a fanned-out
Domain tier. Re-deriving each affected scope over the survivors is where that
influence is removed, at both boundaries.

## Working with no model configured

The default `SummarizationProvider` is **extractive**:
`infrastructure/raptor/extractive.py`'s `ExtractiveSummarizer` selects
sentences rather than generating them, so it cannot state a fact its children
do not contain. Quality is lower than an abstractive summary; in exchange every
sentence is one the children already hold. It is deterministic and, per
ADR-0008 decision 6's Milestone 6 amendment, pure — a function of only the
`texts`, `scope` and `max_tokens` one call passes it, nothing cached on the
instance and no corpus handle held — pinned by
`tests/unit/test_extractive_summarizer.py`, in-process and across process
boundaries: `test_summarize_is_stable_across_processes` and
`test_a_tied_selection_is_stable_across_processes` run `summarize` in three
fresh interpreters at `PYTHONHASHSEED` 0, 1 and 999 and require one distinct
output. That seed variance is what cannot be tested within a single process,
and the tied fixture is run separately because a corpus with no score ties
cannot see a tie-break that started reading a hash-seed-dependent key.

**It is what a build calls.** `theurian index build --raptor` composes an
`ExtractiveSummarizer` and hands it each node's children — one call per node, and
the summary it returns is what the `nodes` row stores. It is composed whether or
not the flag was passed, because it holds no state, opens nothing and reaches no
network, so "was a summarizer configured" is not a second thing the flag means.

Two things ride on that choice. It is what lets Theurian produce a usable
forest offline with no API key
([ADR-0009](../adr/0009-no-llm-vendor-lock-in.md)), so abstractive
summarization is an upgrade and not a prerequisite. And its determinism is what
makes the purge's equality hold: a purged build's forest equals a never-held one
(`test_forest_purge_equality.py::test_a_purged_forest_equals_one_that_never_held_the_withdrawn_rows`)
only because tree derivation — the clusterer as much as the summarizer — is a
pure function of surviving rows, scope and configuration. A provider without that
property forfeits the equality, and ADR-0008 decision 9 records what a purge does
instead (delete the affected trees' nodes and record the forest stale for them,
which retains no withheld influence and restores no equality) — a fallback
recorded in `make_forest_recompute`'s docstring and built by nothing today.

## Retrieval through the forest

```mermaid
flowchart LR
    Q["Query"] --> S["Pre-filter:<br/>project, status"]
    S --> T["Search catalog nodes"]
    T --> D["Descend to domain nodes"]
    D --> L["Descend to leaves"]
    L --> E["Expand parents for context"]
    E --> R["Leaf results, with raptorPath"]
```

**Every box now runs.** `SqliteIndexStore._scope` builds the pre-filter every
leaf retriever uses, and `SqliteIndexStore._node_scope` builds the same filter
for the summary match. `search_summaries` matches summary nodes in `nodes_fts`
and `nodes_trigram`, descends `node_derivation` to the leaf chunks beneath a
matched node, and hands those leaves to `RetrievalService.search`, fused with the
leaf retrievers by RRF under the ranking name `summary` (`ranking.SUMMARY`);
`foundBy` gains that value. `search_lexical`, `search_substring` and
`search_dense` still each name `chunks` in their SQL — the forest is a fourth
retriever beside them, not a rewrite of them, so a leaf a direct query already
finds keeps its rank and one only a summary match reaches is what the forest adds
(`test_a_summary_match_routes_to_sibling_leaves_a_leaf_search_misses`).

Retrieval performs no authorization-scope determination, which is what the first
box would otherwise be read as doing. Three axes are enforced: project; status,
as a pre-ranking predicate when the caller has not passed `includeUnapproved` and
as `may_surface` at the canonical gate when they have; and, since
[#119](https://github.com/theurian/theurian/issues/119), sensitivity against the
deployment's declared ceiling — kept out of the build entirely (ADR-0025 part 1),
emitted as `sensitivity IN (…)` beside the status predicate (part 3), and
re-checked on the item's *current* classification at the canonical gate, which is
the only thing that can catch a document reclassified after the build. Tenant and
ACL group are still refused at write time rather than filtered
([requirements-analysis.md](requirements-analysis.md)). The summary match is
gated the same way and then again: `_node_scope` filters the node on project,
status and sensitivity, so neither a draft-scope nor an above-ceiling summary is
traversed at all; the descended leaves are re-filtered by `_scope` and re-cleared
by `may_surface` and `may_disclose`, so a withheld leaf reached through the
forest still does not surface
(`test_routing_over_an_unapproved_forest_cannot_resurrect_a_withheld_leaf`).

Filtering happens **before** ranking, and that is worth keeping whatever the
forest does: a post-filter returns fewer results than requested and leaks the
existence of hidden content through result-count differences.

Summary nodes are **routing-only**: traversal reaches them, and only leaf chunks
are published as results — a summary node is never itself a result row (ADR-0008
decision 8). `raptorPath` is what makes a traversal visible to a caller — the
summary context a hit sits in, followed back down to the source text. It is
declared on the domain result type (`domain/retrieval.py`), walked by
`walk_raptor_path` lazily over only the surfaced leaves, emitted by
`mcp/results.py` as one `{nodeId, level, title}` segment per ancestor from the
catalog root down to the leaf, and declared optional in
`schemas/knowledge/retrieval-result.schema.json` (with `foundBy`'s `summary`
value and `additionalProperties: false` kept). Each `title` is node-derived free
text — a summariser's output on the wire — carried only above a gate-cleared leaf
whose ancestors share its six-component scope, so it discloses nothing from a
scope the leaf is not in
(`test_a_surfaced_leaf_carries_its_forest_ancestry_as_raptor_path`,
`test_a_withheld_documents_text_never_enters_a_surfaced_items_raptor_path`).
That guarantee is about scope, not freshness: a `title`, like any `excerpt`, is
index text and can lag the canonical store between builds — the build-time
staleness residual (T-17a, [#130](https://github.com/theurian/theurian/issues/130))
that `retrieval-result.schema.json` and `index_forest.py` already attach to every
title.

## Replaceable

Summarization sits behind a port: `SummarizationProvider`, which is the port
ADR-0008 decision 7 names. The intent is that swapping extractive for
abstractive, or a hosted model for a local one, touches no domain, application,
or retrieval orchestration code. There is a consumer now — `ForestBuilder` takes
the summarizer by injection and `cli/index_commands.py` is the one place that
names a concrete adapter, so an abstractive provider is a wiring change there and
nowhere else. What is still missing is the *swap*: one adapter exists, there is
no second to exchange it for, so the property remains the shape of the contract
rather than something a swap has been run against.

The hierarchy itself has no port, and the builder that implements it —
`application/forest_builder.py` — is application-layer policy for a reason that
is checkable rather than stylistic: `application/index_builder.py` is where the
forest pass has to mount, and
`tests/unit/test_layering.py::test_application_does_not_import_infrastructure`
walks the real import graph, so a builder under `infrastructure/` could not be
called from the one place that must call it. The port set is closed and adding
one takes an ADR ([ADR-0003](../adr/0003-ports-and-adapters.md)), so a team
wanting a different hierarchical strategy would be changing that module rather
than configuring it.

The scope-partitioning rule is optional for neither: any implementation must
honour it, because it is a security boundary rather than a performance choice.
