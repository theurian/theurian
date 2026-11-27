# ADR-0040: The traceability representation, and where Phase C's edges come from

- Status: accepted
- Date: 2026-10-28
- Deciders: Theurian maintainers
- Requirements: FR-T2, FR-T3, INV-6, SEC-13, SEC-15
- **Resolves** [`docs/roadmap.md`](../roadmap.md) §9 ADR candidate 4 — formalising
  the traceability representation
  ([#275](https://github.com/theurian/theurian/issues/275))
- Situates against [ADR-0038](0038-specification-folds-into-a-knowledge-kind.md)
  (the id space a trace endpoint lives in, and the gate properties its *Positive*
  lists), [ADR-0039](0039-closed-set-extension-compatibility.md) (how a closed
  set changes), [ADR-0029](0029-review-findings-are-governed-knowledge.md) (the Git
  trailer reader Core already has), and
  [ADR-0007](0007-state-hash-partitioned-databases.md),
  [ADR-0016](0016-state-hash-covers-the-working-tree.md) and
  [ADR-0017](0017-sqlite-schema-versioning.md) (what the state hash covers, and
  the exact-match schema version)

**This ADR records a decision and ships no code.** No enum member, operation,
schema, table or tool moves here. Its implementation is three issues: edge
collection and INV-6 enforcement
([#1033](https://github.com/theurian/theurian/issues/1033)), the
`knowledge.trace` tool ([#1034](https://github.com/theurian/theurian/issues/1034)),
and Phase C's exit demonstration
([#1035](https://github.com/theurian/theurian/issues/1035)).

**Every repository fact below was measured on 2026-10-28 against `origin/main`
at `5ad30799`.** Source paths are relative to
`packages/theurian-core/src/theurian/`; paths beginning `docs/`, `schemas/` or
`.theurian/` are from the repository root. Each count names its key, run at
`5ad30799`, and is marked *dated* — a measurement #1033–#1035 are expected to
move — or *standing* — an invariant this record rests on, so a change that moves
it must move this record too. Line cites outside *Context* were re-measured in
#1033's schema-v5 pull request ([#1059](https://github.com/theurian/theurian/pull/1059));
*Context*'s are as measured at `5ad30799`. Those outside it were re-measured again on 2026-11-24, against #1033's collection pull request ([#1097](https://github.com/theurian/theurian/pull/1097)).

**Records this decision moves.** Decision 3 narrows ADR-0038 decision 2, which
required a spec id named by an edge source to be resolved to its item id when
the edge is populated. Decision 2 keeps ADR-0016 decision 4's "Git is simply
not consulted when computing the hash" true, and ends what it implied: a state's
contents are no longer a function of its hash alone. Decision 8 adds a ninth set
to the eight ADR-0039 decision 1 governs. Decision 5 settles which relations
INV-6 covers, which two other records state differently (F8). Each of those
records carries an amendment pointing here, in this pull request
(*Compliance*). The roadmap's Phase C *Architecture* row, which planned three
edge sources, is corrected with this record.

## Context

### What exists

- **F1.** *(dated)* **The dogfood corpus has 26 items, 24 of them ADR twins, against
   40 ADR files.** The twins anchor ADR-0001 to ADR-0024; ADR-0025 to ADR-0040
   have no twin.

   ```sh
   # 26 unique item ids created
   git ls-tree -r --name-only 5ad30799 .theurian/migrations/ | grep '\.yaml$' \
     | xargs -I{} git show 5ad30799:{} | grep -A1 -e '- op: createItem' \
     | grep -o 'itemId: .*' | sort -u | wc -l
   # 24 items created by a migration whose revision anchors a docs/adr/ file
   for f in $(git ls-tree -r --name-only 5ad30799 .theurian/migrations/ | grep '\.yaml$'); do
     git show 5ad30799:$f | grep -q 'filePath: docs/adr/' \
       && git show 5ad30799:$f | grep -A1 -e '- op: createItem' | grep -o 'itemId: .*'
   done | sort -u | wc -l
   # the 24 ADR numbers anchored: 0001 to 0024
   git grep -h -o -E 'filePath: docs/adr/[0-9]{4}' 5ad30799 -- .theurian/migrations | sort -u
   # 40 ADR files
   git ls-tree --name-only 5ad30799 docs/adr/ | grep -c -E '/[0-9]{4}-'
   ```

- **F2.** *(dated)* **The corpus holds no relation and no specification.** The op key
   prints two lines, `58 op: createItem` and `58 op: upsertRevision`: 0
   `addRelation`, 0 `deprecateItem` and 0 `registerSpecification`.
   `addRelation` and `deprecateItem` are the only operations that write a
   relation row — the engine's two
   `writer.add_relation(` calls, `application/migration_engine.py:506` for
   `deprecateItem`'s `supersededBy` and `:522` for `addRelation` — so the
   dogfood state holds no relation row.

   ```sh
   git grep -h -o -E 'op: [A-Za-z]+' 5ad30799 -- .theurian/migrations | sort | uniq -c
   git grep -n 'writer.add_relation(' 5ad30799 -- packages/theurian-core/src
   ```

- **F3.** *(dated)* **`scope.paths` does not discriminate between modules.** Of the 58
   revisions (F2), 56 carry `scope.paths`, and their sorted glob sets take
   exactly three values: 48 `packages/theurian-core/**`, 7 `'**'` (quoted so in
   the YAML) and 1 `plugins/**`. None names a module or a file. A module under
   `packages/theurian-core/` matches 55 of the 56 (48 + 7).

   ```sh
   git ls-tree -r --name-only 5ad30799 .theurian/migrations/ | grep '\.yaml$' \
     | xargs -I{} git show 5ad30799:{} \
     | python3 -c 'import re,sys,collections; print(collections.Counter(tuple(sorted(g.strip()[2:] for g in m.split("\n") if g.strip())) for m in re.findall(r"\n\s+scope:\n\s+paths:\n((?:\s+- .*\n)+)", sys.stdin.read())))'
   ```

- **F4.** *(dated)* **No commit on `main` carries a `Refs:` trailer; 380 lines of its
   history mention an ADR number.** The first key prints 0. The second counts
   *lines*, not commits: a commit naming two ADRs on one line counts once, on
   two lines twice.

   ```sh
   git log 5ad30799 --format=%B | grep -c -E '^Refs: '
   git log 5ad30799 --format='%H %B' | grep -c -E 'ADR-[0-9]{4}'
   ```

- **F5.** *(dated)* **No adapter implements the edge port.** `CanonicalStore` declares
   `add_traceability_edge` and `list_traceability_edges`
   (`domain/ports/canonical_store.py:371`, `:373`). The key prints nothing;
   without its `grep -v` it prints those two port lines, which is its positive
   control.

   ```sh
   git grep -n -E 'def (add_traceability_edge|list_traceability_edges)[(]' 5ad30799 \
     -- packages/theurian-core/src | grep -v ports/
   ```

- **F6.** **The table and the version check.** *(dated)* `traceability_edges`
   (`infrastructure/sqlite/schema.py:386-398`) has no `source_commit` column, and
   `SCHEMA_VERSION` is 4 (`schema.py:58`). *(standing)* `is_supported` returns
   `schema_version == SCHEMA_VERSION` (`schema.py:468`) — "deliberately no
   compatibility window" (ADR-0017). `_connect` opens a state database in
   three places (`infrastructure/sqlite/connection.py:1044`, `:1069`, `:1415`):
   `create_database`, which writes the DDL into a new file, and the two openers
   of an existing one, `open_read_connection` and the write transaction's. Both
   run `_prepare` (`:1046`, `:1417`), whose `_assert_schema_version` (`:980`)
   reads `schema_metadata`'s `schema_version` and nothing else before refusing a
   mismatch.

   ```sh
   git grep -n -E '^SCHEMA_VERSION|return schema_version == SCHEMA_VERSION|_assert_schema_version\(|_prepare\(connection|_connect\(database_path' \
     5ad30799 -- packages/theurian-core/src/theurian/infrastructure/sqlite
   ```

- **F7.** *(standing)* **An `addRelation` endpoint is always an item id.**
   `$defs/opAddRelation` requires `sourceItemId` and `targetItemId`, each a
   `$ref` to `#/$defs/itemId`, and sets `additionalProperties: false`, so a
   module cannot be an endpoint of a migration-declared relation.

   ```sh
   git show 5ad30799:schemas/migrations/migration.schema.json | python3 -c 'import json,sys; d=json.load(sys.stdin)["$defs"]["opAddRelation"]; print(d["required"], d["properties"]["sourceItemId"], d["properties"]["targetItemId"], d["additionalProperties"])'
   ```

- **F8.** **INV-6 is declared and not enforced, and three records disagree on its
   set.** *(dated)* `KnowledgeRelation.must_be_acyclic` (`domain/knowledge.py:470`)
   has no caller in `src`: the key prints nothing, and the same key over
   `packages/theurian-core/tests` prints two lines, the call at
   `test_domain_invariants.py:342` and a docstring at
   `test_adr_0038_claims.py:172`, its positive control. The three sets: `docs/architecture/requirements-analysis.md:1027`
   says `supersedes`; `ACYCLIC_RELATIONS` (`domain/enums.py:104`) holds
   `supersedes`, `superseded_by` and `depends_on`; `docs/roadmap.md:604` says
   `supersedes` and `depends_on`.

   ```sh
   git grep -n -E '\.must_be_acyclic([^a-z_]|$)' 5ad30799 -- packages/theurian-core/src
   ```

- **F9.** *(dated)* **`TraceNodeType` has 12 members and no `commit`**
   (`domain/enums.py:160-174`), and ADR-0039 never names it:
   `git show 5ad30799:docs/adr/0039-closed-set-extension-compatibility.md | grep -c TraceNodeType`
   prints 0. `TraceabilityEdge.relation_type` is typed `str`
   (`domain/specification.py:88`).
- **F10.** *(dated)* **`ingest` reads no Git history and writes no state.** Its
    docstring (`cli/commands.py:3246`) names `.theurian/cache/ingestion.json` as
    the only file written. Its Git reads are `current_commit` and
    `repository_url` (`cli/commands.py:3316-3317`), which run `git rev-parse
    HEAD` and `git remote get-url origin` (`cli/context.py:84`, `:115`). The only
    `git log` invocations in `src` are the two adapters under
    `infrastructure/git/`, imported by `cli/findings_commands.py` and
    `mcp/tools.py` and not by `ingest`. The state database, where `traceability_edges` lives,
    is created on the `migrate apply` path (`cli/migration_pipeline.py:211`,
    `:374`; `cli/commands.py:2657`).

    ```sh
    git grep -n -E '"log",' 5ad30799 -- packages/theurian-core/src
    ```

- **F11.** *(dated)* **`system.capabilities` publishes `"traceability": False`**,
    hard-coded at `mcp/tools.py:3325`.
- **F12.** *(dated)* **The relation gate reads each endpoint non-resolving and
    body-free.** `_relation_is_visible` (`mcp/tools.py:1124`) reads both
    endpoints through `get_item_exact_metadata`
    (`infrastructure/sqlite/store.py:516`), whose `_ITEM_METADATA_SQL`
    (`store.py:1657`) selects `knowledge_items` metadata columns
    `WHERE project_id = ? AND item_id = ?` and consults no alias.

### Two further facts the decisions rest on

- *(standing)* **A label names no single item.** A revision's labels are a JSON
  list in `knowledge_revisions.labels` (`schema.py:259`), and the migration
  schema's `$defs/revisionMetadata/properties/labels` is `uniqueItems` within one
  list only, so nothing makes a label unique across items. A label's grammar is a
  string of at most 64 characters, so `adr-0040` is both a valid label and a
  valid item id.
- *(standing)* **The state hash reads no Git.** Its inputs are the migrations,
  their body checksums, `SCHEMA_VERSION` and `MIGRATION_ENGINE_VERSION`
  (`StateInputs`, `domain/state.py:47-54`), and decision 2 keeps it so.
  *(dated)* Every CLI command that resolves a project computes it, in
  `resolve_context` (`cli/context.py:262`). Search compares a
  published index build's recorded hash against the current one and labels the
  answer stale when they differ (`mcp/search.py:655`), and `index status` reports
  `indexStale` on the same comparison (`cli/index_status_report.py:217`).

### What the roadmap planned

Phase C's *Architecture* row planned "Three edge sources to start: ① migration
declarations (`addRelation`, existing), ② commit trailers (`Refs: ADR-0021`)
collected by `ingest`, ③ `scope.paths`". Measured against the corpus, ① can
reach no module (F7), ② has no instance on `main` (F4), its example names an ADR
number rather than an item id, and `ingest` writes no state (F10), and ③ names no
module (F3). The exit criterion — "why is this module shaped this way" traces to
an ADR — needs an edge from a module to an item, and none of the three supplies
one today.

## Decision

### 1. The edge source: a `Refs:` commit trailer naming a literal item id

**From this ADR onward, a commit declares that it relates to a knowledge item
with a `Refs: <item id>` trailer, and that trailer is Phase C's one edge
source.** For each path the commit changed, it yields one edge:

- source `TraceNode(CODE, <the repository-relative path>)`;
- target `TraceNode(KNOWLEDGE, <the item id>)`;
- relation `related_to`, confidence 1.0;
- evidence `SourceAnchor(commit_sha=<the commit>, file_path=<the path>)`, and
  the same commit as the edge's `source_commit` (decision 4).

The commit is evidence of the edge and its measurement point, not a node, so
`TraceNodeType` needs no `commit` member (F9). The source needs **no new
migration operation and no new closed-set member**: `CODE`, `KNOWLEDGE` and
`related_to` all exist. Under roadmap §9 item 3 as ADR-0039 recorded it — "a new
`kind` or `RelationType` member is additive, a new operation bumps `apiVersion`"
— this source moves neither.

**Main has no `Refs:` trailer today (F4), so recorded edges begin at the
convention's first commit.** No commit before it carries anything to collect, and
a trace response claims no coverage (decision 7).

**`related_to`, not `constrained_by`.** The declaration is made once per commit
and projected onto every path the commit changed. `constrained_by` would assert,
of each of those paths, that the item constrains it — of a lockfile or a fixture
the same commit touched, that is an overclaim. `related_to` asserts only what the
evidence holds: a commit that changed this path declared this item. Confidence
1.0 records that the declaration is explicit, a person wrote it, which is what
confidence separates in [`traceability.md`](../architecture/traceability.md)
("Explicit edges get confidence 1.0"); it is not a strength of relation.

**Every path is a `CODE` node.** The collector does not classify paths: calling
one `TEST` by its name would be inference, and an inferred node type on an
explicit edge would mix the two things confidence exists to keep apart.

**The four alternatives, with their keys:** every source the roadmap or
`traceability.md` names that exists here today; review threads and test names
are collected nowhere (*Neutral*).

| | Source | Measured | Verdict |
| :-- | :-- | :-- | :-- |
| (i) | `Refs: <item id>` trailer | 0 lines on `main` (F4) | **Chosen.** Explicit, per commit, reaches a path |
| (ii) | Prose mentions in commit messages, `ADR-NNNN` | 380 lines (F4) | **Rejected.** Roadmap §10 names false traceability the product's largest risk, and a mention is not a declaration: a message saying "unlike ADR-0007" mentions it. A mention names an ADR number, which needs resolving to an item (decision 3), and 16 of 40 ADRs have no twin (F1). If ever added, it is a distinct source, with a published confidence below 1.0 and field names that keep it apart from *recorded* edges |
| (iii) | Hand-authored `addRelation` migrations | 0 in the corpus (F2) | **Rejected.** Item to item only (F7). Reaching a module needs either a new operation — an `apiVersion` bump under ADR-0039 decision 3, and an owner decision — or modules entered as items, which is a new `kind`, additive under ADR-0039 decision 2 but due only after [#849](https://github.com/theurian/theurian/issues/849) and [#853](https://github.com/theurian/theurian/issues/853), and a governed item per module. `addRelation` rows stay item-to-item edges that `knowledge.trace` walks (decision 7) |
| (iv) | `scope.paths` globs | 55 of 56 match any core module (F3) | **Rejected.** An edge from every core module to nearly every item says nothing. It stays a candidate source once the corpus's globs are module-level |

### 2. Every apply collects, over first-parent history at `HEAD`; the commit is recorded, not hashed

**Collection runs inside the state build** — the write transaction `migrate
apply` already runs (`apply_migration_set`, `cli/migration_pipeline.py`) — after
the migration set applies. It resolves `HEAD` once to the walk commit's OID — a
second read of `HEAD` can name another commit — reads from that OID alone each
first-parent commit's `Refs:` trailers (decision 3) and the paths it changed
against its first parent, and writes the edges into `traceability_edges`. *Amended 2026-11-24: the walk runs before the write lock, outside it ("Amended 2026-11-24", after decision 9).*

**Every `migrate apply` collects, including one with nothing pending,** and
replaces the whole edge set, the recorded walk commit and the boundary inside its
write transaction. It is a full replace, not an incremental walk from the stored
commit: after a rebase or a reset, the stored commit is not an ancestor of
`HEAD`. The expected-count record keeps its condition
(`cli/migration_pipeline.py:159`), because edges are not that record.

**The walk commit is recorded, not hashed.** It is §6 principle 2's "commit it
was measured against", recorded in `schema_metadata` (decision 4) and published
by every trace (decision 7). Principle 2 asks a response to state its commit,
not that every commit invalidate the state, and ADR-0016 rejected a commit sha
in the hash: "a code-only commit touching no knowledge would invalidate a valid
state". **A commit that changes no migration and no body a migration references
leaves `state_hash` unchanged.**

**A state's identity is (`state_hash`, walk commit, boundary), and two states
with one identity hold the same edges;** a state whose collection was capped or
bounded never shares an identity with one whose was not. *Amended 2026-11-25: a
tree-bounded state's identity does not name its edge set ("Amended 2026-11-25",
after decision 9).* `state_hash` names the
canonical state (ADR-0016) and, through `MIGRATION_ENGINE_VERSION`, the
collector's rules, so a change to them, the bounds and caps below among them,
bumps it. The boundary is what the walk left uncollected: the shallow digest,
whether the depth bound or the read budget stopped it, and the capped digest.
*Amended 2026-11-25: in a promisor clone the tree bound stops it too, stored as
the depth bound.*
The collector reads Git and nothing else (decision 3(b)), so after an apply the
edges are a function of the objects first-parent-reachable from the walk commit,
the shallow list and those rules alone: no environment variable, no
configuration at any level, and no replace ref, graft or commit-graph reaches
them. *Amended 2026-11-25: a tree-bounded state's edges also depend on which
trees git finds in the clone, and whether a missing one bounds the walk or refuses it
depends on git configuration.* That holds under the prerequisites an
independent source read names, each
held below or recorded outside the claim:

- **Plumbing, over the walk OID.** `git rev-list --first-parent --parents <OID>`
  lists the commits, every line OIDs or the build refuses, and each is one stdin
  record of `git diff-tree --stdin -r --root --no-renames --name-status -z
  --always --ignore-submodules=none`: `C P1`, or `C` for a root or
  shallow-boundary commit, so no merge-mode default applies (at 2.56,
  `-m --first-parent` diffs a merge against every parent). `--name-status` keeps
  a path named like the next commit's OID from reading as its header; `--always`
  gives an empty commit its record. The build refuses unless diff-tree returns
  one record per commit fed, in order — it drops an unknown stdin commit with
  status 0 — and unless #1012's `log` form, under
  `-c i18n.logOutputEncoding=UTF-8`, reads the messages in rev-list's sequence.
  Under `--no-renames` a rename yields both paths.
- **#1012's bound**, its module docstring verbatim: "With each
  spawn's own flags it neutralises every documented git feature that can make a
  deciding read admit what this repository's own objects do not, except two that
  are tracked: an attribute-driven conversion on the committed check (#1015) and,
  below git 2.45, the fetch child's reading of repository config (#1016).
  Features that can only make a read refuse are outside this bound. A direct
  write to the object store, a cache file or a ref can still forge any answer."
  Every spawn runs under its `child_environment` and argv prefix, unchanged —
  which keep tracing out of every captured stream (#1012's `342ea607`) — plus
  `--no-pager` and `-c core.fsmonitor=false`: a configured fsmonitor hook runs during
  diff-tree's index read and moved its output. The cache files are one class,
  kept out of every read by `-c core.commitGraph=false` and
  `-c core.multiPackIndex=false`. `--ignore-submodules=none` closes
  `submodule.<name>.ignore` in configuration and in the worktree, index or `HEAD`
  `.gitmodules`, each of which moved the output without it.
- **Outside the claim:** object-store integrity, alternates included, is
  #1012's direct-write residual; a corrupt index, `core.maxTreeDepth`, malformed
  configuration and `safe.directory` can only make a read refuse; the git
  executable's identity is a process prerequisite; and behaviour below git 2.56
  is established only where the source read cites a version. #1015 does not
  reach a read of names, which converts no content; #1016 does not reach a walk
  that #1012's environment keeps from fetching. Key: independent source read,
  Codex, 2026-10-29, git 2.56.0 sources; re-check `python3 -I probe.py`,
  `b_plumb3.py` and `b_framing.py` (#1036's round record), which #1033's
  determinism tests become.
- A commit message that is not UTF-8 is contained to its own record (#496) and
  reported by its sha only, so 3(a)'s "never echoed" holds; ADR-0029's reader
  keeps up to 120 bytes of it (`trailer_source.py:182`, `:423`). A path git
  emits that is not UTF-8 is not collected.
- An edge's id and `created_at` derive from its commit — the sha, the path and
  the item id; the committer date — never from the engine's clock or an id
  generator. The evidence's required `source_uri` derives from the commit and
  the path, never from `git remote get-url origin`, which differs between
  clones and prints a filesystem path for a local remote — an operator's
  layout, the class GHSA-923w-f36f-jcfq closed in MCP error responses.
- A failure to read `HEAD`, a walk that fails or outlives the 30-second
  subprocess timeout (`infrastructure/git/trailer_source.py:95`), and a stream
  the framing cannot read refuse the build. None of them degrades to "no
  edges", because then one identity would name two edge sets. *Amended
  2026-11-25: in a promisor clone diff-tree's exit 128 at a root tree git
  does not find is the tree bound, not a refusal, and a promisor read that fails
  answers no rather than refusing.*
  `current_commit`
  returns `None` for an unborn `HEAD` and a failure alike (`cli/context.py:96-108`),
  so the collector reads `HEAD` through a call that tells the two apart.

**What git emits to the walk is bounded by recorded constants:** at most 10,000
first-parent commits, the walk commit first, and a read budget of 100,000 path
entries and 64 MiB, counted as each stream arrives, never read whole, capped
commits included. Git is terminated where a commit would cross a bound; it and
every older commit are not collected, and the boundary records which bound
stopped the walk. A commit listing more than 1,000 paths yields no edges and
joins the capped digest, all or none by 3(c)'s rule. **So one apply writes at
most 32 × 100,000 = 3,200,000 edges.** A bound stops every machine at the same
commit; under a time limit the edge set, or whether `migrate apply` succeeds for
any tool, would vary with the machine's load. At `5ad30799` the walk reads 427
commits (`git rev-list --first-parent --count 5ad30799`), 3,998 path entries, at
most 99 in one commit, and 2,061,969 bytes, in 71 to 87 ms here. The budget is
25 and 32 times that read, the depth 23 times this history, the per-commit cap
10 times its largest commit; at its mean of 9.4 entries and 4.8 KB per commit
the depth binds first, near 93,600 entries and 48 MB. At the budget the read
took at most 0.68 s (*Negative*, third item), 44 times inside the 30-second
timeout, which git's own reads can still cross (*Negative*, fourth item).

**It reads `HEAD`, not ADR-0029's `refs/remotes/origin/main`,** which ADR-0029
pins so that no embargoed finding enters a corpus built from it: an edge
describes the branch the state describes (ADR-0016), in its own state database.

**First-parent** is the line the branch itself records. Under this repository's
squash merges it is the whole history (`git rev-list --merges --count 5ad30799`
prints 0). In a merge workflow a side-branch commit's trailer is collected only
when the merge commit's message carries it (*Negative*).

**The collector never discovers a repository.** `apply_migration_set` takes the
root the composition root resolved (`find_git_root`, `cli/context.py:62`,
`:279`) or None. None collects nothing and records the walk commit and the
boundary as null, as an unborn `HEAD` does; a trace then reports a null commit
and zero edges walked. With a root, every read names it (`--git-dir`), or the
collector refuses unless `rev-parse --show-toplevel` equals it. The rehearsal
passes None: with `TMPDIR` inside a clone, its throwaway copy discovered that
clone and read its history (measured), so `cli/migration_pipeline.py:74-80`'s
comment is #1033's to restate.

**On a shallow clone, the walk stops at the shallow boundary.** The shallow
digest is the lowercase hex SHA-256 of the clone's shallow-commit list — the
lines of the file `git rev-parse --git-path shallow` names, sorted byte-wise,
each followed by a newline — and null when no such file exists, which is a full
clone. The build refuses unless every line is exactly one lowercase hex OID of
the repository's hash length. A commit the list names yields no edges: its
parent is absent, so its
`C` record diffs it against the empty tree. In a depth-2 clone at `5ad30799` the
boundary commit `54a9f673` lists 1,159 paths, its whole tree
(`git ls-tree -r --name-only 54a9f673 | wc -l`), against the 2 the full history
lists. A trace publishes the shallow flag, not the digest (decision 7).
The build reads the digest before the walk and again after it and refuses
unless the two are equal, since `rev-list` and the log form each read the list
again; a list rewritten and restored between the two reads passes, by
construction, and is outside the identity claim above.

> **Amended in Phase C, by [#1033](https://github.com/theurian/theurian/issues/1033).**
> The derivation said "the shas in the file", which read as its
> whitespace-separated tokens. Git reads the file one OID per line, so one
> line holding two OIDs and two lines holding one each gave one token-wise
> digest and two different walks (measured on #1036). The digest is over the
> file's lines, and a line that is not exactly one OID refuses the build.

**Not in `ingest` (F10), and not a new command:** either gives the state
database a second writer whose run is optional, so whether a state held any
edges would depend on a command nothing else requires.

### 3. The trailer grammar: a literal item id, untrusted, never resolved

- **Extraction is ADR-0029's rule.** A `Refs:` trailer is a line of the whole
  commit message, subject included, that begins at column 0 with exactly
  `Refs: ` — ADR-0029 Amendment 1's D1, which `keyed_lines`
  (`domain/review_finding.py:293`) implements for `Review-Finding:` — so one placed
  above the `Signed-off-by:` paragraph is read, and an indented or quoted one is
  not. Its value is the rest of that one physical line with surrounding ASCII
  whitespace removed. One item per line; a commit naming two items carries two
  lines, and a repeated id yields one edge per path.
- **The value is untrusted.** A commit message is author-controlled bytes, the
  same standing as indexed content under [T-3](../security/threat-model.md).
- **(a) A value that is not a valid item id is not collected and never
  echoed.** Valid means the whole value is an `ItemId`: `_DottedId`'s
  `_DOTTED_PATTERN`, at most `MAX_IDENTIFIER_LENGTH` (200) characters
  (`domain/identifiers.py:35`, `:44`, `:128-144`). An invalid value yields no
  edge, and no message, log line, report field or error carries it. That
  includes the `InvalidIdentifierError` `_DottedId` raises, whose message
  repeats the value (`got {value!r}`, `identifiers.py:143`), so it is caught and
  its text discarded. The build reports to its operator the commit's sha and how
  many values it refused, never their text. `Refs: ADR-0040` (upper case),
  `Refs: #1033` and `Refs: a.b, c.d` all fail.
- **(b) A valid id that names no item when the state is built is recorded.**
  Three reasons. The collector then reads Git and nothing else, so the stored
  edges are a function of history alone and identical in two corpora that
  differ only by a withheld item; a drop-on-absence rule would store the edge
  where the item exists and is withheld, and not where it is absent, so the
  edge table itself would vary with withheld content. *Amended 2026-11-25: in a
  promisor clone, of history and of which trees git finds in the clone; neither is a
  canonical row, so the reason stands ("Amended 2026-11-25", after decision
  9).* The per-hop gate withholds
  a missing endpoint (decision 6), so a recorded edge to an absent id surfaces
  exactly what one to a withheld item does: nothing. And a dropped declaration
  is an edge the contributor believes exists; a recorded one starts to surface,
  through the gate, at the first build in which its item exists, with no
  rewrite of history.
  **Observable consequence:** no `knowledge.trace` response shows such an edge,
  counts it, or gives it a slot. The build report counts, per commit, recorded
  edges whose item endpoint named no item at that build — a CLI surface read by
  an operator who can read every migration — and that is where a typo becomes
  visible. Decision 9's rows 1, 2 and 5 hold it on the wire. `Refs: adr-0040` is
  this case: a valid `_DottedId` that names a label, not an item, recorded and
  inert.
- **(c) A commit naming more than 32 distinct valid ids yields no edges, and
  the boundary records it.** Its edges are one per id per changed path, both
  sizes its author chose. The count takes every distinct valid id, item or not,
  so the cap reads Git alone ((b)). All or none, since a partial rule needs an
  order among ids. The capped digest is the lowercase hex SHA-256 of the capped
  commits' shas, sorted byte-wise, each followed by a newline, and null when
  there are none; the build reports each sha, never its values, as (a) does.
  It does not refuse, or every capped commit would refuse every later build
  that walks it; the timeout does, for content that drives git's own reads
  past it (*Negative*, fourth item). 32 is over four times the
  most distinct ADR numbers one commit message on `main` names, 7 at
  `f665ecf6`, the nearest measure while `main` has no `Refs:` trailer (F4):
  `git rev-list 5ad30799 | while read c; do git log -1 --format=%B $c | grep -o -E 'ADR-[0-9]{4}' | sort -u | wc -l; done | sort -n | tail -1`.
- **Collection resolves no alias and no label.** A label names no single item
  (*Two further facts*). An alias is an author-chosen key whose target a later
  migration moves, so a resolved edge would point wherever the alias map of the
  build's migrations sent it, not at what the commit's author named; and
  resolution feeding an authority decision is exactly what
  [T-21](../security/threat-model.md) found leaking. Resolution would also read
  canonical rows, which (b)'s first reason keeps out of the collector. **This
  narrows ADR-0038 decision 2**, which required a spec id named by an edge
  source to be resolved to its item id at population: for this source, the
  trailer names the item id itself.

> **Amended 2026-11-24, for #1033's collection as
> [#1097](https://github.com/theurian/theurian/pull/1097) built it.**
>
> - **The strip set** ("surrounding ASCII whitespace") is `string.whitespace`:
>   space, tab, LF, CR, VT and FF. Git's own `isspace` excludes VT and FF.
> - **An undated commit**, whose committer date `datetime` cannot hold, yields
>   no edges, is reported by its sha as `undated`, and does not join the capped
>   digest for being undated: containment, ADR-0029 D3's class. The flags are
>   independent facts, so an undated commit that is also path-capped joins the
>   digest by the path-cap rule.
> - **3(b)'s count** is `absentItems`, per commit: its recorded edges whose item
>   endpoint names no `knowledge_items` row, counted inside the apply's
>   transaction against the state it built. A withheld item has a row, so it is
>   not absent. The report carries counts and flags by sha, never an id, and
>   lists a commit only when one is set. One walk over a corpus withholding an
>   item and over one that never held it gives one edge set and one report but
>   that count, 1 against 2, in
>   `test_a_withheld_item_and_one_never_held_differ_only_in_the_absent_item_count`.

### 4. The schema: `source_commit`, and `SCHEMA_VERSION` 4 → 5

`traceability_edges` gains `source_commit TEXT NOT NULL` — every edge is
measured against a commit, roadmap §10's "mandatory commit anchor". The commit
is also inside `evidence`, but that column is a JSON array SQL cannot index or
compare, and a later source may carry evidence from several commits while
`source_commit` names the one it was measured against. `relation_type` holds a
`RelationType` value, and #1033 narrows `TraceabilityEdge.relation_type` from
`str` (F9) to `RelationType`; roadmap §4 keeps edge types closed.
`schema_metadata` gains decision 2's walk commit and boundary — the shallow
digest, the depth and budget flags and the capped digest — all null with no walk.

These are DDL changes, so `SCHEMA_VERSION` goes from 4 to 5. Under the exact
match (F6), a v4 build refuses a v5 database and a v5 build a v4 one, per
database, at open, in `_assert_schema_version`, before any row other than
`schema_metadata`'s is read. That is the shape
[#853](https://github.com/theurian/theurian/issues/853) requires: decided per
database, never per row. `SCHEMA_VERSION` is also a state-hash input
(`domain/state.py`), so on the build path a v5 Core never selects a v4
database's name. Re-check, after #1033: the key under F6 prints
`SCHEMA_VERSION: Final = 5`, the exact comparison, and the opener's call.

### 5. INV-6: one set, enforced over the graph an apply leaves

**The set is `ACYCLIC_RELATIONS` as declared — `supersedes`, `superseded_by`
and `depends_on` — read as two graphs.** In the supersession graph a stored
`superseded_by` row from A to B is the `supersedes` row from B to A
(`INVERSE_RELATIONS`, `domain/enums.py:95-100`); checked apart, a cycle that
alternates orientation (A `supersedes` B, A `superseded_by` B) would pass. The
dependency graph is `depends_on` alone. A loop mixing the two graphs violates
neither reason `enums.py` records — "a superseded chain that loops has no current
revision, and a dependency loop has no resolution order" — so it is not
refused. The requirements row's `supersedes` alone is the record that is too
narrow, and the roadmap's `supersedes` and `depends_on` names the inverse only
implicitly (F8); both owe the set stated here.

**Enforcement is a whole-set guard in the migration engine,** run inside
`migrate apply`'s write transaction after the set's operations, over every
relation row of the project the apply leaves — whatever operation wrote it,
`addRelation` or `deprecateItem`'s `supersededBy` (F2). A later `removeRelation`
in the same set therefore cures an earlier cycle, and no applied document, which
is frozen, ever needs editing. A refusal rolls the transaction back.
Ids the alias map connects, through any chain, are one node, since `list_relations` resolves an
alias first (`store.py:935-949`), so a rename whose `supersededBy` names its successor is refused.

**The refusal is a new `RelationCycleError`, a `MigrationError`, not
`MigrationCycleError`.** That one means a `dependsOn` cycle among migrations, so
"no application order exists" (`domain/errors.py:116-122`); it carries migration
ids, and the CLI's remedy for it is "Break the dependency cycle shown above, then
retry." (`cli/commands.py:769-770`). A relation cycle's cure is a new migration
with a `removeRelation`, so the new error names the relation type and the
cycle's item ids, and its remedy says that. The guard is in the engine, so the
error reaches every CLI site that replays a set: the first key below prints
six wiring sites, five of them reachable, and each surfaces the error its own
way:

- `migrate apply`: the error and its own remedy, exit 4;
- `migrate validate`: the error text in `permissiveMovesUnavailable`, with
  `valid` still true — ADR-0027 decision 2's recorded design, which
  [#1056](https://github.com/theurian/theurian/issues/1056) re-decides;
- `theurian propose`, for an item the set creates, and `propose accept`: the
  error text inside their own refusal, whose remedy names `migrate apply` when
  the landed set holds the cycle, and the proposal's file when its own
  relations close it;
- `okf import`, for an item the set creates: the T-25 refusal literal, which
  names only the class of the proposal service's refusal, `ProposalError`.

The sixth, `okf_commands.py:382`'s rehearsal, is unreachable: `okf import`
holds its service through `DraftOnlyProposals`, whose `__slots__` are the two
draft entries, and of `ProposalService`'s public methods only `accept` reaches
`self._rehearse`. The MCP composition root wires the rehearsal to
`_accept_is_unreachable` (`mcp/tools.py:1973`), so no MCP response carries it.

```sh
# the six wiring sites; 9 lines without the ':!' exclusion
git grep -n -P '\b(apply_migration_set|rehearse_migration_set|current_item_in|permissive_moves_in)\(' -- packages/theurian-core/src/theurian/cli ':!packages/theurian-core/src/theurian/cli/migration_pipeline.py' | grep -v -E ':[0-9]+:\s*(#|def )'
# what `okf import` holds, then the public methods reaching the rehearsal:
# ['accept'] from _rehearse, and ['accept', 'draft'] from _current_item
grep -n '__slots__' packages/theurian-core/src/theurian/application/draft_only_proposals.py
python3 -I - packages/theurian-core/src/theurian/application/proposal_service.py _rehearse <<'EOF'
import ast, sys
tree = ast.parse(open(sys.argv[1]).read())
cls = next(n for n in tree.body if getattr(n, "name", "") == "ProposalService")
uses = {f.name: {a.attr for a in ast.walk(f) if isinstance(a, ast.Attribute) and getattr(a.value, "id", "") == "self"} for f in cls.body if isinstance(f, ast.FunctionDef)}
reach = {sys.argv[2]}
for _ in uses:
    reach |= {name for name, used in uses.items() if used & reach}
print(sorted(name for name in reach if not name.startswith("_")))
EOF
```

**What an existing cyclic corpus meets.** The dogfood corpus meets nothing: it
holds no relation row (F2). Corpora in other repositories cannot be measured
from here. One with a cycle meets `RelationCycleError` at its first apply under
the enforcing Core, naming the cycle; until a migration removes a relation of
it, that Core publishes no state for the project.

**No `apiVersion`, `protocolVersion` or `MIGRATION_ENGINE_VERSION` bump.**
ADR-0039 decision 6 sends a change that "moves which rows a gate reads", relation
rows among them, to "decision 8's ADR-first route"; this record is that ADR.
Decision 6's engine-version path is for when "a Core must change what an
earlier-version document does" — "#841's case, where the table the retired
operations write goes away". Refusing a set the old engine applied reads as that
case and is not: the guard adds a refusal and writes nothing, so it changes
which sets are accepted, not what an accepted operation does, and an engine bump
— the version "still describes the engine, not the format" (ADR-0039,
*Neutral*) and is hashed into the state hash — would invalidate every state
built from an acyclic set to buy nothing. The one set whose outcome changes was
never valid: INV-6 predates the format — its row arrived in `481bd0f0`, four
commits before the migration schema's first commit, `0f8c387d`
(`git rev-list --count 481bd0f0..0f8c387d` prints 4). No operation's meaning
changes and no wire field moves, so `apiVersion` and `protocolVersion` stay. The
refusal is a break — a Core MINOR before 1.0, a MAJOR after (ADR-0039, *The
matrix*) — named **BREAKING** in the CHANGELOG.

**The guard validates the whole relation set the apply leaves, on every apply,
including one with nothing pending, so a cyclic set built by a non-enforcing
Core is refused at its next apply.** Without the nothing-pending clause, "next
apply" would not reach a project that adds no migration.

**Why the guard ships with schema v5** (read from source, not run). A refused
apply rolls back and publishes nothing, and the serve path opens the pointer's
state without reading `engine_version` (ADR-0039, *Context*) or the relation
set. A cyclic state built at the enforcing Core's own `SCHEMA_VERSION` would
therefore hash identically, be reused, and keep serving after its refused
re-apply: the failure *Compliance*'s sequencing condition (#1033) prevents.
**Under that condition, every state an enforcing Core serves or applies into was
built by an enforcing Core.** A state a non-enforcing Core built is v4: the
enforcing Core refuses it at open (`_assert_schema_version`,
`infrastructure/sqlite/connection.py:1083-1089`) and names another database for
the project, since `SCHEMA_VERSION` is a state-hash input
(`resolve_state_hash(loaded, SCHEMA_VERSION)`, `cli/context.py:295`,
`cli/migration_pipeline.py:273`, `:429`), which only the enforcing apply builds.

### 6. The per-hop visibility gate

The roadmap's Phase C *Security* row states the requirements. Its T-21 and T-26
sentences, verbatim:

> Apply the per-edge visibility gate at every hop — and apply it in T-21's corrected form, because per-edge gating on its own is what leaked: **a traversal hop must not resolve an alias when deciding authority.**
>
> Reachability may resolve; the visibility decision on each hop's endpoint reads the literally-named row's metadata — through `get_item_exact_metadata` or an equivalent that neither resolves an alias (T-21) nor reads the body (T-26), never the joined `get_item_exact`: a gate on that read materialises a withheld endpoint's body before withholding it, so the refusal's duration carries the body's size.
>
> **Write the two-corpora equality test for trace responses in the same change as the implementation** — not afterwards.

Its next sentence, verbatim except that its link to ADR-0038 is rewritten for
this directory:

> An equivalent must also keep the existing gate's other properties, which [ADR-0038](0038-specification-folds-into-a-knowledge-kind.md) *Positive* lists: both endpoints judged on status and sensitivity (`may_surface`, `may_disclose`), with no direction inference; a missing endpoint withheld; and the read scoped to the project, as `_ITEM_METADATA_SQL`'s `project_id` scopes it — a `TraceNode` carries no project, so it cannot scope the read.

**Decided.** Every item endpoint a hop reaches is read by the id it literally
names, through `get_item_exact_metadata` or an equivalent that keeps all of the
above, and judged on `may_surface` and `may_disclose`:

- **Both endpoints, no direction inference.** A `knowledge_relations` row is
  judged on both of its items, as `_relation_is_visible` judges it (F12). A
  traceability edge has one item endpoint under decision 1, and it is judged
  whichever way the hop crossed the edge.
- **A missing endpoint is withheld,** exactly as a withheld one is.
- **The read is scoped by the edge's `project_id`.** Edges are listed by the
  request's project — both edge indexes lead with `project_id`
  (`schema.py:425-426`) — and each endpoint is read in the edge row's
  `project_id`, which that listing makes the request's. A `TraceNode` carries no
  project, so the edge row is the only thing that can scope the read.
  [#845](https://github.com/theurian/theurian/issues/845)'s M-4, the gate's
  project scope, becomes an acceptance criterion of #1034: a real store holding
  two projects, an edge in one naming an item id that exists only in the other,
  and a trace from either that surfaces nothing of the other.
- **An external endpoint surfaces only as the endpoint of an edge whose item
  endpoint passed.** A `CODE` node is never judged on its own; it appears
  because an edge from it reached a visible item, or because the caller named
  it as the start. An edge with no item endpoint therefore surfaces nothing, and
  no source this ADR admits writes one.

### 7. The response: recorded edges, a commit, no coverage claim

**Fields.** Every `knowledge.trace` response carries §6 principle 2's three
statements, under names that say *recorded edges*:

| Field | Carries |
| :-- | :-- |
| `recordedEdgesCommit` | the commit the state's edges were collected at (decision 2); null with no Git history |
| `recordedEdgesWalked` | how many recorded edges the traversal walked — counted after the gate, so only edges whose endpoints passed |
| `recordedEdgesHistoryShallow` | whether collection stopped at a shallow boundary |
| `recordedEdgesDepthBounded` | whether collection stopped at the depth bound (decision 2). *Amended 2026-11-25: or at a promisor clone's tree bound, which `walk_depth_bounded` stores as the depth bound, so this field cannot tell the two apart ("Amended 2026-11-25", after decision 9)* |
| `recordedEdgesBudgetBounded` | whether collection stopped at the read budget (decision 2) |
| `recordedEdgesCapped` | whether collection skipped a commit over a per-commit cap (decisions 2 and 3(c)) |
| `recordedEdgesCoverage` | the constant `"not-claimed"`: the response asserts nothing about edges that were never recorded |

Descriptions say *recorded* edges and never claim completeness (Phase C
*Risks*). Evidence and confidence pass through unchanged from the stored edge. A
`knowledge_relations` hop is published with exactly the fields `knowledge.get`'s
`relations` publishes for that row after the same gate, so `trace` publishes no
field of a relation that `knowledge.get` withholds. No title, body or excerpt of
any item is in a trace response; a caller reads content through
`knowledge.get`. Every response carries SEC-15's triple, `SAFETY`'s
`contentClassification`, `mayContainInstructions` and `executable`
(`mcp/results.py:45-49`): a hop's `note`, a `CODE` id and an evidence path are
author-controlled text (T-3).

**What a hop follows.** A recorded traceability edge, in either direction —
`related_to` is not in `INVERSE_RELATIONS`, so an edge stored from a path to an
item is also walked from the item — or a `knowledge_relations` row.

**Limits, as numbers.** Depth at most 4, default 2 — the chain the roadmap's
*User value* names, code → PR → spec → ADR → requirement, is four hops. At most
100 nodes and 500 edges per response. Every limit counts units that passed the
gate, so a withheld node never takes a slot; reaching one sets `truncated`. Each
endpoint is read at most once per request. The pre-gate rows a request reads are
bounded by 100 times the largest stored fan-out of a node; with each node's
edges listed once, its edge rows are at most twice the 3,200,000 one apply
writes (decision 2), and its relation rows are the corpus's. #1034 records the
measured cost on the dogfood corpus.

**The start node.** An absent or withheld item id gets `knowledge.get`'s
absent-id refusal, byte for byte. A `CODE` start is the caller's own string, not
gated; it reaches only what its edges reach through the gate.

### 8. Governed parameters, and `TraceNodeType` joins ADR-0039's sets

`knowledge.trace`'s edge-type, direction and node-type parameters are typed `str`
in the handler and parsed by `_closed_value` (`mcp/tools.py:1255`), never
annotated with a `Literal` or an enum. An annotation reaches the input schema
the SDK serves for the tool, the form
[#856](https://github.com/theurian/theurian/issues/856) records ADR-0039's
tripwires missing; a `str` whose description names the set keeps a new member
off the wire contract, as ADR-0039 decision 9's ground requires. The response publishes node and relation
types as strings for the same reason.

**`TraceNodeType` joins ADR-0039's governed sets, as a vocabulary set,**
because #1034 makes it wire-visible and #1033 stores it in the derived state —
the two faces ADR-0039 governs for `kind` and `relationType`. Its publication is
the trace wire and the state, not the migration format, so ADR-0039 decision 2's
additive rule, decision 7 on reordering and decision 9's ground apply to it, and
decisions 3 and 5, which are about `apiVersion`, do not: no migration names a
node type. Adding a member is a Core MINOR, and #853's
per-database check is due before the first new member, as it is for `kind` and
`relationType`. ADR-0039 decision 1's "eight entries" becomes nine, in an
amendment there.

### 9. The disclosure families of a graph response

**The equality instrument is one query against two corpora, at every hop:**
corpus A holds a withheld item W, with whatever relations name it; corpus B
never held W; both share one Git history. Every response must be byte-identical
between them.

**The closure argument.** Decision 3(b) makes the `traceability_edges` rows
identical in A and B, since the collector reads Git alone. What differs is W's
`knowledge_items` row and the `knowledge_relations` rows that name it. Every
read of `knowledge_items` on the trace path is the per-hop gate's read
(decision 6), which gives W and an absent id the same outcome. Every
`knowledge_relations` row is gated on both endpoints before it reaches a count,
a slot or a field, and a row naming W fails the gate in A and does not exist in
B. Every limit and count is taken after the gate (decision 7). So every
published value is a function of rows that are identical in A and B. That
argument holds rows 1, 2, 4, 5 and 7. **It does not cover four families, and the
table names what does:** the time and the rows spent reading and dropping (rows
3 and 6), which state a request reads (row 8), and a decode that runs before the
gate decides (row 9).

| # | Family | Its face in a trace response | Held by | #1034's test, by role |
| --: | :-- | :-- | :-- | :-- |
| 1 | A published field | `recordedEdgesWalked`, any edge or node count | Counted after the gate; edges to W or to an absent id count in neither | Two-corpora equality on every field, a start whose only edges reach W |
| 2 | Which rows reached a field | A withheld node taking one of the 100 slots moves where the walk truncates | Limits count gated units only (decision 7) | Two-corpora equality at the truncation point: a start with more than 100 visible neighbours plus edges to W |
| 3 | A duration | One gate read per hop: W found and decoded, an absent id not found | Body-free metadata read, so the cost does not scale with W's body (T-26); one read per endpoint per request. The found-versus-missing difference remains, and this ADR does not claim it is zero | A two-corpora timing measurement across hop counts, reported as a measurement |
| 4 | A statistic over rows the caller may not see | A count over all stored edges or relations of a node | No pre-gate count is published, by `trace` or by `knowledge.status` | A schema walk of the trace response asserting no field is computed before the gate, plus equality |
| 5 | An error that tells withheld from absent | At the start node; at a hop | The start refusal is `knowledge.get`'s absent-id refusal; a hop never errors on an endpoint, it withholds | A start at W against a start at an absent id: identical bytes; a hop to W against a hop to an absent id: identical response |
| 6 | A resource consumed | Pre-gate rows read: relation rows naming W are read and dropped in A and do not exist in B | Bounded by 100 × the largest fan-out (decision 7); the per-row cost is the one `knowledge.get`'s `relations` already pays | A measurement of rows read per request on the dogfood corpus |
| 7 | Another tool reaching the same content | `knowledge.get` and `trace` on one item | `trace` publishes no relation field `knowledge.get` withholds and no item content at all (decision 7) | For every item in a corpus holding withheld rows, `trace`'s relations of it are a subset of `knowledge.get`'s |
| 8 | State, lifecycle and concurrency | A v4 database under a v5 Core; a trace racing a rebuild, or an apply replacing the edges in place (decision 2) | Exact match at open (decision 4); one read session per request, and the active pointer read once, so a trace sees one state whole | A two-way open test; a trace racing either sees the old state or the new one, never both |
| 9 | A closed-set value decoded before the gate (#853's class) | A `TraceNodeType` or `RelationType` member a Core lacks, decoded before the hop's gate decides | `traceability_edges` rows are identical in A and B (3(b)), so their decode cannot differ; an item row or a `knowledge_relations` row naming W is the face #853 records, held by its per-database check, due before the first new member (ADR-0039) | A withheld row carrying an unknown member against an absent id: identical responses, the acceptance ADR-0039 *Compliance* item 3 states |

### Amended 2026-11-24 — decision 2 as #1033's collection built it

> **Amended 2026-11-24, for #1033's collection as
> [#1097](https://github.com/theurian/theurian/pull/1097) built it, on the walk
> [#1092](https://github.com/theurian/theurian/pull/1092) bounded (`70c6cfad`).**
> Each item names the sentence of decision 2 it amends, which stays where it is.
>
> 1. **The walk runs before the write lock, outside it** ("Collection runs
>    inside the state build — the write transaction"). Git subprocesses inside
>    the transaction would break NFR-8, "No external I/O (LLM, network,
>    subprocess) inside a write transaction"
>    ([`requirements-analysis.md`](../architecture/requirements-analysis.md)),
>    which `migrate apply` already keeps for its other git reads
>    (`cli/commands.py:2621-2628`). It walks once (`cli/commands.py:2639-2647`):
>    the walk resolves `HEAD` to the walk commit's OID, and the edges are a pure
>    function of that OID, which is what is recorded, so a `HEAD` that moves
>    during the walk desynchronises nothing. `apply_migration_set` takes that
>    walk, not a root ("The collector never discovers a repository"); the three
>    replays pass `walk=None` and record a null walk and no edges, as the comment
>    decision 2 left to #1033 now says. Inside the one transaction the apply
>    derives the edges, replaces the edge set, writes the walk record and the
>    boundary, and takes 3(b)'s count. A refusing walk exits 4 with its own
>    remedy before the lock is taken, and nothing is written. Measured in #1097:
>    10 git spawns before the lock and none under it. Rejected: amending NFR-8
>    to admit the walk, which would add its duration, up to each of its four
>    spawns' 30-second deadline, to the lock hold. The `watchdog` agent's ruling
>    of 2026-11-20; not a Blocking Issue, since every apply still collects.
> 2. **What the lock still holds is the replace.** Measured through the adapter
>    in #1097 ([its description](https://github.com/theurian/theurian/pull/1097),
>    *Write-lock hold*): 7.4 ms at 5 edges, 1.006 s at 50,000, and 12.99 s for a
>    320,000-edge replace, about 24.6k rows/s; about 130 s at the 3,200,000
>    budget is a linear extrapolation, not a measurement. Only the walk's read,
>    at most 0.68 s at the budget (*Negative*, third item), left the lock:
>    *Negative*'s "near two minutes" was always the replace.
>    `WRITE_LOCK_TIMEOUT_SECONDS` is 30.0 (`infrastructure/sqlite/connection.py:59`),
>    so above about 740k edges a concurrent writer — a findings build, a review
>    ingest, a second apply — fails with `WriteLockTimeoutError`, whose remedy is
>    to wait and retry. NFR-8 is cited, not amended: its subprocess clause is
>    met, and its < 50 ms target is not met at scale, bounded by the edge budget
>    ([#1038](https://github.com/theurian/theurian/issues/1038)). Recorded as a
>    cost by the `watchdog` agent's ruling of 2026-11-24.
> 3. **Edges are derived state** ("The walk commit is recorded, not hashed").
>    The edge set and the walk record are a function of the walk commit, the
>    boundary and the collector's rules, replaced whole by every apply, and no
>    migration writes them; `state_hash` names the canonical state alone.
>    NFR-5, NFR-6 and ADR-0007 carry the same amendment. The `watchdog` agent's
>    ruling of 2026-11-04.
> 4. **One edge per `edge_id`** ("replaces the whole edge set"). An edge id is
>    content-derived and embeds its commit's sha, so, short of a collision in
>    SHA-256's first 128 bits, two edges share one only within one commit, where
>    they are the same edge and are kept once; a doubled path does not abort the
>    apply on the key. The id omits the project id, and a state database can
>    hold more than one project: the state hash excludes the project id and
>    `register_project` upserts by id, so after a checkout's project id changes,
>    its next apply of an unchanged state writes a second `projects` row into
>    that state's database. The replace therefore deletes the whole table, not
>    the applying project's rows, since deleting only those failed the second
>    project's insert on `edge_id`'s primary key (C-H1, HIGH, in
>    [#1097](https://github.com/theurian/theurian/pull/1097)'s round-one code
>    review); the rows it inserts carry the applying project's id. The edge set
>    is the walk of the last apply to commit in this checkout, as the walk
>    record in `schema_metadata` is.
> 5. **The edge id and its evidence** ("An edge's id and `created_at` derive
>    from its commit"). The id is the first 128 bits of
>    SHA-256(sha ‖ 0x00 ‖ path ‖ 0x00 ‖ item id) over UTF-8, Crockford-base32
>    encoded to 26 characters with no bit cleared: 26 characters carry 130 bits,
>    so a 128-bit value leads with 0–7 and is a valid ULID. The separator is
>    unambiguous: git forbids 0x00 in a path, a sha is hex and an item id holds
>    none. `Ulid`'s "lexical order equals creation order"
>    (`domain/identifiers.py:70-71`) does not hold for these content-addressed
>    ids; `ORDER BY edge_id` is a total order and nothing more. The evidence is
>    `provider="git"` and `source_uri="<sha>:<path>"`, git's own `<rev>:<path>`
>    syntax, which reads no remote (`domain/refs_trailer.py`).
> 6. **The diff-tree feed** ("`C P1`, or `C` for a root or shallow-boundary
>    commit"). A commit the shallow list names is not fed to diff-tree: it is
>    left out of the walk (item 7). `C` alone is fed for a root the list does not
>    name.
> 7. **A shallow-listed chain end** ("A commit the list names yields no edges:
>    its parent is absent, so its `C` record diffs it against the empty tree").
>    The outcome holds by another mechanism, and the premise is false for a true
>    root, which git writes into the list when a clone's depth equals the
>    history's length. A chain end the list names is left out of the walk, a
>    grafted boundary or a true root alike, since rev-list prints the two alike:
>    the cost is one under-collected commit, never a false edge, and the shallow
>    digest marks the walk shallow (`_list_commits`,
>    `infrastructure/git/first_parent_walk.py:266-277`). #1092's code review
>    graded the root's loss HIGH, converted to this recorded decision because
>    telling the two apart needs the raw commit object, another spawn. The
>    depth-2 measurement there stays the reason a boundary's own diff is not
>    collected.
> 8. **Lazy fetch** ("#1016 does not reach a walk that #1012's environment
>    keeps from fetching"). That overstates it below git 2.45, where
>    `GIT_NO_LAZY_FETCH` is not read and only `GIT_ALLOW_PROTOCOL=""` holds the
>    line (#1092, *Notes for review*).
> 9. **Each spawn's timeout is one deadline** ("a walk that fails or outlives
>    the 30-second subprocess timeout"). Since `70c6cfad`, `GIT_TIMEOUT_SECONDS`
>    (30.0, `infrastructure/git/trailer_source.py:95`) is one monotonic deadline
>    over the waits on a spawn's output and exit status — a `selectors` wait
>    before each read and a bounded wait for the status, never a wait for pipe
>    EOF; at it the walk kills git's process group, then git, and refuses at
>    once. diff-tree's feed is staged in an unlinked `TemporaryFile`, so no
>    writer thread waits on git. A descendant that calls `setsid` or `setpgid`
>    escapes the group kill and is left running; the walk still refuses at the
>    deadline. Measured on #1092: group and `setsid` probes for all four spawns
>    refused in 1.0–1.2 s against a 1 s bound, and a descendant holding
>    diff-tree's stdin in 0.23 s (0.25 s under dash). The deadline's exactness
>    is unpinned: two mutants that loosen it survive (#1092, *Deferred
>    claims*). The walk's waits outside every deadline, its read of the shallow
>    file among them, are listed on #1092 (*Notes for review*).
> 10. **A loose ref `HEAD` names, holding bytes that are not an OID, reads as
>     unborn** ("reads `HEAD` through a call that tells the two apart").
>     `rev-parse --verify --quiet HEAD` exits 1 with no line for either, so
>     such a repository records a null walk and no edges rather than refusing
>     (`infrastructure/git/first_parent_walk.py:209-211`). It is #1012's
>     direct-write residual, outside the claim as the object store is.
> 11. **No feed, no stdin.** rev-parse, rev-list and log take no feed and get
>     `subprocess.DEVNULL` as stdin, not `migrate apply`'s, which is the
>     caller's (`_stdin`, `infrastructure/git/first_parent_walk.py:503-507`).
>     Nothing pins it: the mutant that drops it survives (#1092's round-two
>     adversarial LOW-2).
> 12. **The ratchet** ("a change to them, the bounds and caps below among them,
>     bumps it"). `test_the_collector_answers_as_it_did_when_the_engine_version_was_last_set`
>     (`packages/theurian-core/tests/unit/test_refs_trailer.py`) compares a
>     SHA-256 over the collector's answers with a recorded value and, when they
>     differ, says to bump `MIGRATION_ENGINE_VERSION`. The answers: `REFS_KEY`;
>     the cap; which of four `Refs` spellings yields an edge; which whitespace
>     characters below U+3001 are stripped from both ends of a value; whether 32
>     and 33 ids are capped; the edge ids for an ASCII and a CJK path; and a
>     capped digest over a list holding a duplicate. It reads none of the walk's
>     bounds or its path cap: `test_the_shipped_bounds_are_adr_0040_decision_2s`
>     pins those four values and names no engine version. #1033 does not bump
>     the version: no released Core carried a collector, so no database a
>     released engine built holds edges under other rules, and the next
>     release's `SCHEMA_VERSION` 5 rebuilds every upgrader's state anyway (the
>     `watchdog` agent's ruling of 2026-11-20). The rule binds every later
>     change to the collector.

### Amended 2026-11-25 — decision 2's tree bound in a promisor clone (#1103)

> **Amended 2026-11-25, for [#1103](https://github.com/theurian/theurian/issues/1103)'s
> fix as [#1105](https://github.com/theurian/theurian/pull/1105) built it, on
> the collection [#1098](https://github.com/theurian/theurian/pull/1098)
> finished (`7b13dca5`).** It fixes a regression against core-v0.5.3 that no
> release carried. On a treeless partial clone (`git clone --filter=tree:0`)
> 0.5.3's `migrate apply` exits 0; at `8bd840cf` it exited 4 at
> `git diff-tree` and named `git fsck` as the remedy, and fsck exits 0 there and
> reports nothing (#1103's reproduction), so nothing cured it. Each item names
> the sentence it amends, which stays where it is with a pointer here.
>
> 1. **A root tree git does not find in a promisor clone bounds the walk**
>    ("None of them degrades to 'no edges'"). Each sentence this item states
>    about the bound is one of two kinds, and a sentence of neither kind does
>    not belong here: (i) a universal statement over the walk's code, or
>    (ii) an example of git's behaviour on a named shape, introduced as an
>    example and measured at 2.56.0, never a universal over damage shapes
>    (#1105's light pass). The walk keeps `GIT_NO_LAZY_FETCH` in its
>    environment, which tells git not to fetch what a partial clone left on
>    its promisor remote. In a promisor clone, a diff-tree exit 128 that its
>    framing accepts is the tree bound exactly when the absence check below
>    completes and answers `<name> missing` for at least one root tree of the
>    commit diff-tree stopped at and, if it has one, of its first parent. The
>    walk then keeps no commit from that one on, and the apply records edges
>    only from the commits it keeps. For example, measured at 2.56.0: a
>    subtree missing mid-commit gave no partial record of that commit
>    (#1105), and a fresh `git clone --filter=tree:0` with a checkout held its
>    walk commit's root tree and not its parent's, and its walk kept 0
>    commits.
>
>    **The absence check.** After that exit 128, and before the write lock, the
>    walk feeds the stopping commit and its first parent to
>    `rev-list --no-walk=unsorted --format=%T --stdin`, which prints each one's
>    root-tree name, and feeds those names to
>    `cat-file --batch-check=%(objectname)`, which answers each with the name
>    alone or with `<name> missing`. In this item git *does not find* a tree
>    when that answer is `missing`, and a tree held only in a pack git cannot
>    open is one it does not find (the second residual below). When git finds
>    both root trees, every exit 128 in a promisor clone refuses, whatever
>    caused it, and a refusal for that exit status names item 4's fetch
>    remedy. For example, each of these refused at 2.56.0: in a `blob:none`
>    clone unpacked loose, the walk commit's parent's root tree overwritten
>    with `garbage`, and a subtree below two held root trees deleted
>    (`test_a_promisor_clone_damaged_under_held_root_trees_refuses_with_the_fetch[corrupt-root-tree]`
>    and `[absent-subtree]`); `diff.renameLimit = 1k0` in a `blob:none`
>    clone's own config
>    (`test_a_promisor_clone_whose_diff_tree_dies_on_its_config_refuses_and_keeps_the_edges`,
>    which also holds the earlier apply's edges and walk record) and in
>    `$HOME/.gitconfig`,
>    measured at `244a0f0b`, a commit on the branch of #1105; and, in a
>    treeless clone holding both root trees, one byte flipped in a packed root
>    tree's entry header or in its body, one offset each,
>    measured at `a48cd047`, a commit on the branch of #1105. A check that
>    exits non-zero, prints an answer not framed as its tree's, ends inside a
>    record or outlives its deadline refuses too, since a failed check that
>    answered "absent" would make any 128 the bound again
>    (`test_an_absence_check_that_fails_refuses_naming_its_spawn`: each spawn
>    exiting 3, unframed and ending inside a record, and `cat-file` outliving a
>    1-second deadline, each refusing with that spawn and reason). The answer
>    is the name alone because the default format also reads each object's
>    type and size, and, measured at 2.56.0, it answered `missing` for some
>    trees git finds and cannot read, so it cannot tell that damage from
>    absence: for example a loose tree with its first byte flipped, a packed
>    tree with the first byte of its entry header flipped, and a deltified
>    packed tree with the byte 2, 4 or 6 bytes into its body flipped. The
>    names come from `rev-list` because, measured at 2.56.0,
>    `rev-parse --verify --quiet HEAD~1^{tree}` printed no name and exited 1
>    in a fresh treeless clone that lacked that tree, and `log` exited 128
>    under `diff.renameLimit = 1k0` as diff-tree did, where `rev-list` exited
>    0.
>
>    **Why that closes the class.** The bound rests on the check's answer, not
>    on why diff-tree exited: at a stopping pair whose root trees git finds, no
>    cause of an exit 128, a configuration value or damage, makes the walk
>    bound.
>    **Residual, recorded:** an absent root tree in the stopping pair bounds
>    the walk whatever else stopped diff-tree there, a configuration value it
>    dies on or damage to the other root tree or below it, since the check
>    reads absence and not cause (#1105's first review round and its light
>    pass). For example, measured at 2.56.0: with `diff.renameLimit = 1k0` in
>    `$HOME/.gitconfig`, a fresh treeless clone walked as under a clean
>    `HOME`, 0 commits and the tree bound, and once `git log` had fetched the
>    top commit's trees the same value refused with the fetch remedy, where a
>    clean `HOME` collected one commit,
>    measured at `244a0f0b`, a commit on the branch of #1105; and a fresh
>    treeless clone, with or without a checkout, whose walk commit's root tree
>    was written loose as `garbage` beside its parent's absent root tree, kept
>    0 commits under the tree bound as the intact clone did, while `git fsck`
>    exited non-zero,
>    measured at `47fc5946`, a commit on the branch of #1105.
>    **Residual, recorded (#1105's second review round):** a root tree held
>    only in a pack git cannot open bounds rather than refuses, since git does
>    not find it. For example, measured at 2.56.0: a fresh treeless clone
>    fetched each root tree into a pack of its own; one byte flipped in the
>    header or the trailer of the pack holding `HEAD~1^{tree}` made git refuse
>    to open that pack and `cat-file` answer `<name> missing`, and the walk,
>    which collects all 3 commits of the intact clone, kept 0 and took the tree
>    bound, so an apply records no edges. There `git fsck` exited non-zero,
>    and item 4's fetch remedy did not cure it:
>    `git log --first-parent --name-status HEAD` was still fetching lazily at
>    60 seconds, and the walk then bounded as before. All measured at
>    `a48cd047`, a commit on the branch of #1105. Inducing it takes a write to
>    the object store, #1012's direct-write residual. Kept as the decision by
>    the `watchdog` agent's ruling of 2026-11-27: git reads its own store the
>    same way, and in both shapes measured fsck reported the damage. Rejected
>    for now: refusing whenever the check's `cat-file` writes to stderr, which
>    needs a stderr sink in the walk's child and refuses a healthy repository
>    whose git prints a warning; it is
>    [#1110](https://github.com/theurian/theurian/issues/1110).
>
>    Every other non-zero exit, and an exit 128 anywhere but a promisor clone,
>    still refuses. Chosen as (b) by the `watchdog` agent's ruling of
>    2026-11-25. Rejected: (a) refusing with a corrected remedy, which still
>    blocks the apply until the user fetches; (c) allowing the lazy fetch,
>    which is network I/O inside the walk and removes the control
>    `GIT_NO_LAZY_FETCH` is; taking any exit 128 as the bound, as this item
>    said until #1105's first review round, under which an undamaged
>    `blob:none` clone, holding every tree, recorded no edges with
>    `treeBounded` true under `diff.renameLimit = 1k0`, and so did a clone
>    with a present but corrupt tree (both measured at 2.56.0); reading the
>    unreadable object's name off diff-tree's stderr, which depends on git's
>    locale and wording; and keeping any 128 as the bound with narrower
>    prose, which publishes `treeBounded` true for a clone that holds every
>    tree.
> 2. **Whether a clone is a promisor is read from git configuration, before the
>    walk** ("no configuration at any level"). After `rev-parse` and before
>    `rev-list`, the walk spawns `git config` once or twice:
>    `config --type=bool -z --get-regexp '^remote\.(.*\.)?promisor$'` over every
>    scope git reads, then, if no value is true,
>    `config --local -z --get extensions.partialclone`. They are two of the
>    keys git's `promisor-remote.c` reads: includes, `config.worktree` and global
>    configuration count for the first, the common directory's config file
>    alone for the second (measured in #1105 against git's own lazy fetch at
>    2.56.0; the tests drive the clone's own `remote.origin.promisor` and the
>    extension alone, not includes or `config.worktree`). They are not all of
>    them: a `remote.<name>.partialclonefilter` set without `promisor` also
>    makes git fetch lazily, and the walk answers no there, so a treeless
>    clone holding that key alone refuses at diff-tree with the fsck remedy,
>    which fails closed (measured at `244a0f0b`, a commit on the branch of
>    #1105: the walk refused, and git's own
>    `git log --first-parent --name-status HEAD` exited 0 and fetched the 3
>    objects missing before it). `git clone --filter` and
>    `git fetch --filter` write `remote.<name>.promisor = true` beside it, and
>    neither writes `extensions.partialClone` (measured at 2.56.0), so only a
>    hand-set or tool-written configuration reaches this.
>    [#1108](https://github.com/theurian/theurian/issues/1108) tracks reading
>    that key. A read that fails,
>    outlives its deadline or ends inside a record answers *not a promisor*, at
>    the cost of at most that one deadline: killed there as item 9 of the
>    amendment above says, it answers rather than refusing at once. Unlike the
>    walk's other spawns, none of its failures refuses the build, and its *no*
>    keeps the refusal a damaged repository needs
>    (`test_a_promisor_read_that_fails_answers_no_so_an_unreadable_tree_refuses`).
>    Both reads run inside the walk, before the write lock, so NFR-8 holds:
>    `test_no_git_process_starts_while_the_write_lock_is_held` now requires a
>    `config` spawn before the lock, on its tree-bound case a `cat-file`
>    spawn too, and none under it. They take no feed
>    and get `subprocess.DEVNULL` as stdin, as item 11 of the amendment above
>    says of three other spawns; item 1's check feeds both of its spawns, so
>    the `rev-list` item 11 names is the listing's. Their output is outside the
>    read budget, as `rev-parse`'s and the check's are. With the two reads and
>    the check's two spawns the walk spawns at most eight times, so that
>    amendment's item 1, "each of its four spawns' 30-second deadline", counts
>    the walk before this fix. `config` and `cat-file` are new subcommands at
>    an existing spawn site, so `PROCESS_SPAWN_SITES` stays seven; T-7 names
>    both.
> 3. **The boundary gains the tree bound, stored as the depth bound** ("whether
>    the depth bound or the read budget stopped it"). `walk_depth_bounded`
>    holds the depth bound or the tree bound. A budget stop and a shallow
>    boundary also leave the stored set short of a root and keep their own
>    fields, `walk_budget_bounded` and `walk_shallow_digest`, so a 0 in it does
>    not say the walk reached a root. `migrate apply --json` reports
>    `traceability.treeBounded` beside `depthBounded` and `budgetBounded`, and
>    its `depthBounded` is true under the tree bound too. Which of the two
>    stopped the walk is not persisted. For a fresh treeless clone and for one
>    whose top one or two commits' trees were fetched,
>    `test_a_treeless_clone_applies_recording_edges_only_from_commits_whose_trees_it_holds`
>    holds the three report flags at true, true and false, and the stored
>    `walk_depth_bounded` and `walk_budget_bounded` at 1 and 0. No DDL change,
>    and `SCHEMA_VERSION` stays 5, by the `watchdog` agent's pre-ruling of
>    2026-11-25: a column of its own changes schema v5's DDL before 0.6.0, and a
>    v5 database already built would pass the exact-match open (F6) without it,
>    so the column would take `SCHEMA_VERSION` 6. Decision 7's
>    `recordedEdgesDepthBounded` reads this column and carries a pointer here.
> 4. **The remedy in a promisor clone** (new: decision 2 names no remedy). A
>    refusal at `rev-list`, `diff-tree`, `cat-file` or `log` that would name
>    `git fsck` names `git log --first-parent --name-status HEAD` instead
>    (`cat-file` read from source: item 1's check tests compare the spawn and
>    the reason, not the remedy). Run by a git
>    allowed to fetch lazily, that command fetches the trees the walk will not:
>    in
>    `test_a_promisor_clone_walks_to_its_first_unreadable_tree_until_the_remedy_fetches_it`
>    a four-commit treeless clone, under each promisor configuration the tests
>    drive, walks no commit before it and all four after it. In
>    `test_a_promisor_clone_s_exit_status_refusal_names_the_fetch_in_place_of_fsck`,
>    a planted `rev-list` or `diff-tree` exit in the clone names it, the same
>    `rev-list` exit in its origin, which promises nothing, names fsck, and a
>    framing refusal keeps its own remedy. `rev-parse`, which runs before the
>    promisor read, keeps fsck's.
> 5. **A tree-bounded state's identity does not name its edge set** ("two
>    states with one identity hold the same edges"; the edges "a function of
>    the objects first-parent-reachable from the walk commit, the shallow list
>    and those rules alone: no environment variable, no configuration at any
>    level"; "then one identity would name two edge sets"; and 3(b)'s "a
>    function of history alone"). The shallow boundary's digest is part of the
>    identity; the tree bound carries no digest and is published as the depth
>    bound. So two tree-bounded states at one walk commit hold different edges
>    when different trees are local. Measured with git 2.56.0 at
>    `e006d9b2`, a commit on the branch of #1105, by the first block below: one treeless
>    clone of a five-commit history, three of its commits carrying a `Refs:`
>    trailer, applied once `git log` had fetched the top commit's trees and
>    again once it had fetched the top two's, printed one `stateHash`, one
>    `walkCommit`, `depthBounded` true, `budgetBounded` false, `treeBounded`
>    true and null digests both times, and 1 edge, then 2. Whether an exit 128
>    bounds or refuses depends on configuration too: on item 2's promisor keys,
>    `remote.<name>.promisor` in any scope. Measured on the same commit: a repository with no remote,
>    its walk commit's parent tree deleted, refused at diff-tree with the fsck
>    remedy, exit 4; with `remote.x.promisor = true` written to
>    `$HOME/.gitconfig`, the same apply exited 0 with 0 edges and `treeBounded`
>    true, so `HOME`, an environment variable, reaches it as well. So do
>    `XDG_CONFIG_HOME` and a file an `include.path` names: re-measured at
>    `244a0f0b`, a commit on the branch of #1105, by calling the walk on the
>    same repository, the key in `$HOME/.gitconfig`, in
>    `$XDG_CONFIG_HOME/git/config` or in a file the repository's own
>    `include.path` names bounded it at 0 commits with the tree bound, and with
>    none of them it refused with the fsck remedy. The rest
>    stands: a capped or bounded state still never shares an identity with one
>    that was not, and the claims hold as written for a state whose stored
>    boundary is not depth-bounded. A depth-bounded record may be tree-bounded
>    and cannot say which. 3(b)'s reason holds too: the tree bound reads the
>    clone's objects and git configuration, never a canonical row, so the edge
>    table still cannot vary with withheld content. The same narrowing reaches
>    item 3 of the amendment above ("a function of the walk commit, the
>    boundary and the collector's rules"), left as written since this block
>    follows it, ADR-0016 Amendment 1's "two states with one identity hold the
>    same edges" and NFR-5's "one identity holds one edge set", which carry
>    pointers here.
> 6. **Nothing consumes identity equality today.** Every apply re-collects the
>    walk and replaces the whole edge set (decision 2), and nothing under `src`
>    reads the walk record back: the walk columns other than `walk_commit` are
>    named there only by the DDL and `record_walk`'s `UPDATE`, and the one
>    `SELECT` from `schema_metadata` reads `schema_version` (the second block
>    below). The first reader will be
>    [#1034](https://github.com/theurian/theurian/issues/1034)'s
>    `knowledge.trace`, which must treat a depth-bounded identity as not naming
>    its edge set, since every tree-bounded state has one. Recorded rather than
>    closed, by the `watchdog` agent's ruling of 2026-11-25. Rejected:
>    persisting a value that tells two tree-bounded states apart, a DDL change
>    before 0.6.0 against item 3's pre-ruling; and refusing a tree-bounded
>    apply, which is #1103 itself.
>
> Item 5's identity measurement, run in an empty scratch directory with `HOME`,
> `THEURIAN_DATA_DIR` and `TMPDIR` inside it and git's author and committer
> set. Its two lines differ only in `edges`:
>
> ```sh
> git init -q -b main src && echo a > src/a && git -C src add a && git -C src commit -qm init
> (cd src && theurian init --json > /dev/null) && git -C src add -A && git -C src commit -qm theurian
> for i in 1 2 3; do mkdir src/d$i && echo $i > src/d$i/f.py && git -C src add -A &&
>   git -C src commit -qm c$i -m "Refs: architecture.x$i"; done
> git -C src config uploadpack.allowFilter true
> git clone -q --filter=tree:0 "file://$PWD/src" tl
> for k in 1 2; do git -C tl log --first-parent --name-status --max-count=$k HEAD > /dev/null
>   (cd tl && theurian migrate apply --json) |
>     python3 -c 'import json, sys; p = json.load(sys.stdin); t = p["traceability"]; t.pop("commits"); print(p["stateHash"][:16], t)'
> done
> ```
>
> Item 6's keys, from the repository root. The first prints `record_walk`'s two
> `UPDATE` lines, the second the one `schema_version` read:
>
> ```sh
> git grep -n -E 'walk_(shallow_digest|depth_bounded|budget_bounded|capped_digest)' -- packages/theurian-core/src ':!*/sqlite/schema.py'
> git grep -n 'FROM schema_metadata' -- packages/theurian-core/src
> ```

## Consequences

### Positive

- **The exit demonstration needs no new operation, no new member and no
  `apiVersion` bump** (decision 1). AC: `git diff origin/main...HEAD -- schemas`
  is empty for this record.
- **The edge table cannot vary with withheld content** (decision 3(b)), so five
  of decision 9's nine families close on one argument over one gate, and the
  other four are named with what holds them.
- **A mistyped or early declaration costs nothing at read time** and starts to
  surface when its item exists.

### Negative

- **Coverage starts at zero.** No commit on `main` carries a trailer (F4), and
  no edge is recorded for anything before the convention's first commit. The 380
  lines that mention an ADR are not collected.
- **ADR-0025 to ADR-0040 have no twin (F1).** A trailer naming one of them names
  nothing until a migration creates its twin, and is recorded and inert until
  then (decision 3(b)). #1033 plans a squash body carrying `Refs:` with
  ADR-0040's item id; that item does not exist at `5ad30799`. #1035's
  demonstration needs a trailer naming one of the 24 twins, or a twin created
  first.
- **Between a commit, or a checkout that leaves the migrations alone, and the
  next apply, the edges describe the walk commit, not `HEAD`** (decision 2).
  `recordedEdgesCommit` says which; nothing raises a staleness alarm. **Every
  apply pays the walk and a full replace, near two minutes at the budget:** a
  read of at most 0.68 s, then 3,200,000 rows into `schema.py`'s DDL verbatim
  plus decision 4's `source_commit`, 62.6 s written fresh and 116.6 s as a
  replace, in a 2.71 GB database (git 2.56.0, SQLite 3.53.1; `python3 -I
  at_budget_read.py`, `python3 -I at_budget_write.py <schema.py> v5`, #1036's
  round record). A commit larger than what remains of the budget stops every
  walk there until 10,000 newer first-parent commits pass it.

  > **Re-measured 2026-11-24 through the adapter, in
  > [#1097](https://github.com/theurian/theurian/pull/1097)** (its description,
  > *Write-lock hold*): 320,000 edges written fresh in 7.17 s (44,624 rows/s)
  > and as a replace in 12.99 s (24,629 rows/s), the absent-item count in
  > 0.21 s, in a 208 MB database. At the 3,200,000 budget the replace is about
  > 130 s by linear extrapolation, against 116.6 s above; the budget itself
  > was not re-run. The walk now runs before the write lock and the replace
  > inside it, so the replace is the lock hold: above about 740k edges a
  > concurrent writer passes `WRITE_LOCK_TIMEOUT_SECONDS`, 30 s (decision 2's
  > amendment, item 2).
- **The budget bounds git's output; git's reads to produce it are bounded only
  by each spawn's 30-second timeout,** which refuses the build and names the
  spawn that timed out, so the operator sees what to act on. Commit content
  reaches that bound two ways. Headers no spawn emits: 100 first-parent
  commits, each with a 330 MiB `gpgsig` header (475 MB on disk), let `rev-list`
  and `diff-tree` exit 0 in 4.25 s and 9.38 s (8,159 and 5,274 bytes out), and
  `log` timed out at 30.01 s, refusing the build. Trees compared on changed
  paths: a flat directory of 100,000 entries costs 9 ms per commit that changes
  it, about 90 s at the depth bound. Measured against the walk form at #1036's
  branch commit `7685dc5d`, git 2.56.0, scripts in #1036's round record. The
  refusal persists while those commits are inside the 10,000-commit window, and
  whether a walk near the timeout succeeds varies with the machine's load.
  Metering git's reads, a commit object-size cap and a tree-size cap on changed
  paths, is filed under #1038; `rev-list`'s own read stays outside any such meter.

  > **Amended 2026-11-24.** Since
  > [#1092](https://github.com/theurian/theurian/pull/1092) (`70c6cfad`) that
  > bound holds against a wrapper git whose descendant keeps git's output
  > open (decision 2's amendment, item 9).
- **A rename breaks the path link.** An edge names the path its commit changed;
  the module at its new path has no edge until a later commit with a trailer
  changes it.
- **In a merge workflow, a side-branch commit's trailer is lost** unless the
  merge commit's message carries it (decision 2). The squash-body rule in
  [`orchestration.md`](../contributing/orchestration.md) is this repository's
  form of that.
- **A commit-level declaration is projected onto every path the commit
  changed.** `related_to` keeps the claim true, and weak.

### Neutral

- **`traceability.md`'s other sources and its nine tools are not built.** Review
  threads, test names and `scope.paths` stay design; `trace` is the one tool.
- **No `commit` node type.** A commit is evidence and `source_commit`, never a
  node (decision 1).
- **`UNPOPULATED_TABLES` moves in #1033.**
  `packages/theurian-core/tests/integration/test_canonical_store_corruption.py`
  excludes `traceability_edges`, and only it, from its every-table-holds-a-row
  check (ADR-0038, *Neutral*); the slice that populates it owns the change.

  > **Moved 2026-11-24, in
  > [#1097](https://github.com/theurian/theurian/pull/1097).** The set is
  > empty: the corruption corpus holds an edge row, and
  > `test_every_table_the_schema_declares_holds_a_row_to_corrupt` finds a
  > row in every declared table.
- **ADR-0038's revision pin stays unrepresented.** An edge does not carry the
  pin the fold drops, which ADR-0038 left to this record: it carries a commit,
  and ADR-0038 records why a commit is not that pin.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **Prose mentions, `addRelation`, `scope.paths`** as the source | Decision 1's table, each with its key |
| **`constrained_by`** as the relation | Projects a commit-level declaration onto every changed path as a per-path constraint (decision 1) |
| **A `commit` `TraceNode`**, with edges path → commit → item | A new `TraceNodeType` member, so #853 first (decision 8); two hops per declaration against the four-hop depth limit; and the commit is what measured the edge, not what the edge is about |
| **Collect in `ingest`**, as the roadmap row planned, **or in a new command** | `ingest` writes no state (F10); either gives the state database an optional second writer (decision 2) |
| **`HEAD` in `state_hash`** | §6 principle 2 asks the response to state its commit, not that every commit invalidate the state, and ADR-0016 rejected a commit sha in the hash for invalidating a valid state on a code-only commit. A staleness alarm after every commit, the stale index label (*Two further facts*) among its faces, is §10's trust failure by another route: a signal that is always on is read as noise. Read from source, not run (decision 2) |
| **Hash a digest of the collected edges** | Every command that resolves a project computes the hash, and the digest needs the whole walk each time (decision 2) |
| **Porcelain `git log --name-only`** with `-c` pins | A global `~/.gitconfig` `log.showRoot=false` moved the edges under ADR-0029's hardening and under #1012's, and `diff.ignoreSubmodules=all` survived #1012 plus a `showRoot` pin; the population "every key porcelain honours" has no closing key (decision 2) |
| **Bound the walk by wall time** | The edge set, or whether the build succeeds, would vary with the machine's load (decision 2) |
| **Keep the edges in a database of their own**, keyed by `HEAD`, as the index is kept apart ([ADR-0022](0022-index-lives-in-its-own-database.md)) | What it would keep — ADR-0016 decision 4 and the index's freshness untouched — decision 2 keeps without it. Its price is a trace reading two databases built at different moments: the per-hop gate would judge an edge from one state against items from another, and family 8 would hold two pointers instead of one |
| **Resolve an alias or label at collection**, ADR-0038 decision 2's form | Labels name no single item, an alias's target moves with later migrations, and resolution reads canonical rows into the collector (decision 3) |
| **Drop a valid id that names no item** | The edge table would vary with withheld content, and the declaration would vanish silently (decision 3(b)) |
| **Reuse `MigrationCycleError`** for INV-6 | It names a migration-ordering cycle, carries migration ids, and its remedy edits `dependsOn` (decision 5) |
| **Bump `MIGRATION_ENGINE_VERSION`** with INV-6 | The guard changes which sets are accepted, not what an accepted operation does, so the bump would invalidate every state built from an acyclic set to buy nothing, and the whole-set guard already refuses a cyclic one at its next apply (decision 5) |
| **Check INV-6 after each migration** rather than over the set | A frozen migration that closed a cycle a later one breaks would refuse forever, with no cure but editing history |
| **Annotate trace parameters with `Literal` or an enum** | Publishes an `enum` on the served schema, #856's trigger (decision 8) |

## Compliance

**This ADR ships no behaviour.** Its enforcement is a claims pin over the facts
it states, plus the obligations below.

Landing with this pull request:

- **Amendments pointing here**, each in the record this decision moves:
  ADR-0038 decision 2 and its *Compliance* item 3 (decisions 1, 3, 4 and 6
  answer that item); ADR-0016 decision 4 (decision 2); ADR-0039 decision 1's
  eight entries (decision 8); and the INV-6 row of
  `docs/architecture/requirements-analysis.md` and roadmap §4 item 3, for
  decision 5's set.
- **`packages/theurian-core/tests/unit/test_adr_0040_claims.py`, the claims
  pin**, written by the tests step after this record. It is owed both directions
  for each fact under *Context*: a prose side that reddens when this record
  drifts from what it says, and a fact side derived from live source that
  reddens when the codebase moves. Of the facts under *Context*, F6's exact
  match, F7's two item-id endpoints, the label's lack of cross-item
  uniqueness, and the state hash's reading no Git stand; the rest are dated,
  and only F10's has a fact side. Its failure message names #1033, as the
  census units' below do; none names #1034. Since #1033 the pin also holds nine
  sentences outside *Context* as standing: decision 4's `SCHEMA_VERSION` 5,
  `schema_metadata` walk columns and `source_commit TEXT NOT NULL`, decision
  5's guard in the migration engine, the edge adapter's landing below,
  decision 2's identity sentence and shallow digest, and 3(c)'s cap and
  capped digest. Its reach is stated in its module docstring.

Rests on enforcement that already holds:

- **The one-hop gate.** `_relation_is_visible` reads both endpoints through
  `get_item_exact_metadata` (F12), the read decision 6 extends to every hop.
- **The exact-match open** (F6), which decision 4's v4/v5 refusal is.
- **ADR-0029's trailer extraction** (`keyed_lines`), which decision 3 reuses;
  decision 2's environment is #1012's (`342ea607`).
- **`_closed_value`**, the parse decision 8 requires.

Landed from item 1 below in #1033's schema-v5 pull request
([#1059](https://github.com/theurian/theurian/pull/1059)):

- **`SCHEMA_VERSION` 5 and decision 4's columns**: `test_the_schema_version_is_five`,
  `test_schema_metadata_has_the_walk_record_columns`,
  `test_traceability_edges_has_a_source_commit_column`;
- **decision 5's guard, called from `MigrationEngine.apply`**:
  `test_must_be_acyclic_has_one_caller_under_src`, and the RED drivers
  `test_a_v4_state_holding_a_supersession_cycle_is_refused_and_its_rebuild_raises`
  and `test_an_apply_with_nothing_pending_refuses_a_planted_cycle_and_publishes_nothing`;
- **the two-way open**:
  `test_a_database_of_the_other_version_is_refused_before_any_row_is_decoded`;
- **the BREAKING CHANGELOG entry, and FR-K8 restated.**

Landed from item 1 below in #1033's edge-adapter pull request: the SQLite
adapter for both port methods, which nothing under `packages/theurian-core/src`
calls yet.

- **`SqliteWriter.add_traceability_edge` and
  `SqliteCanonicalStore.list_traceability_edges`**:
  `test_a_traceability_edge_round_trips_every_field`, two edges written and
  read back equal, `source_commit` included, each `relation_type` a
  `RelationType`, one listed by its source and the other by its target; and
  `test_the_sqlite_store_is_the_one_adapter_of_the_edge_port`, by the key
  its module docstring states;
- **decision 4's `source_commit`**:
  `test_an_edge_refuses_any_other_source_commit_without_echoing_it`, nine values
  that are not a full lowercase hex commit id, each refused with the same fixed
  message;
- **decision 4's walk record**:
  `test_an_incoherent_walk_record_is_refused_by_its_own_check`, eight
  incoherent `schema_metadata` updates, each refused with SQLite naming the
  CHECK the case expects.

Landed from item 1 below in #1033's walk-bounds pull request
([#1092](https://github.com/theurian/theurian/pull/1092), `70c6cfad`), as
walk-level tests in
`packages/theurian-core/tests/integration/test_first_parent_walk.py`; what the
collector records from each is still owed below:

- **the `fetch --deepen` between the two digest reads**:
  `test_a_shallow_list_deepened_during_the_walk_refuses`, a depth-2 clone that
  a shim deepens during rev-list, diff-tree or log, refused with "the shallow
  list changed during the walk";
- **a shallow clone and its boundary commit**:
  `test_a_shallow_clone_leaves_its_boundary_commit_out_of_the_walk`, clones at
  depths 1 to 5 under sha1 and sha256 whose listed commit is left out and
  digested, and
  `test_a_chain_ending_at_a_root_keeps_it_while_the_shallow_list_cuts_another_branch`;
- **the depth bound and both budgets**, each monkeypatched small:
  `test_the_depth_bound_stops_the_walk_at_the_same_commit_and_says_so`,
  `test_the_entry_budget_counts_a_capped_commit_and_stops_at_the_commit_crossing_it`,
  `test_the_byte_budget_stops_every_stream_at_the_commit_that_would_cross_it`
  and `test_a_token_outgrowing_the_byte_budget_ends_the_read_and_is_not_read_whole`;
- **the path cap at the cap and one over it**:
  `test_a_commit_listing_more_paths_than_the_cap_lists_none`, a three-path
  commit under a cap monkeypatched to 3 and to 2; the shipped 1,000 and the
  other three bounds are `test_the_shipped_bounds_are_adr_0040_decision_2s`.

Landed from item 1 below in #1033's collection pull request
([#1097](https://github.com/theurian/theurian/pull/1097)), in
`packages/theurian-core/tests/integration/test_edge_collection_cli.py` unless
another file is named. The collector writes through
`SqliteWriter.replace_traceability_edges`, `record_walk` and
`count_edges_naming_no_item`, none of them a port method, so the paragraph
above still holds for the port's two methods, by the key
`test_the_traceability_edge_methods_are_referenced_by_the_collector_and_tests_alone`
(`packages/theurian-core/tests/unit/test_adr_0038_populations.py`) states.

- **the collector and its build report**:
  `test_an_apply_records_each_ref_once_per_changed_path_at_the_walk_commit`,
  and `test_the_apply_report_publishes_exactly_these_keys`, the whole `--json`
  key set;
- **determinism at one `HEAD`, and the `state_hash` perturbation pin**:
  `test_one_head_gives_one_edge_set_and_a_commit_outside_the_set_keeps_the_state_hash`,
  two applies at one `HEAD` reading the same rows, and a commit touching no
  migration leaving `stateHash` equal;
- **the full replace after a rebase**:
  `test_every_apply_replaces_the_edges_and_the_walk_record_in_its_one_transaction`,
  where after `commit --amend` an apply with nothing pending leaves only the
  amended commit's edge and walk commit, and a failure planted after the walk
  record rolls both back;
- **the no-echo test**: `test_no_refused_value_and_no_message_byte_leaves_the_walk`,
  seven refused values and a non-UTF-8 message, with five byte strings drawn
  from them absent from three applies' stdout and stderr, the edge rows, a
  planted failure's exception chain and the state files; it reads no log;
- **NFR-8**: `test_no_git_process_starts_while_the_write_lock_is_held`, a flock
  probe of the lock file at every git spawn of one apply, held at the replace
  and at no spawn;
- **each refusal's own remedy, with no state written**:
  `test_a_refusing_walk_prints_its_own_remedy_and_writes_no_state`, nine plants
  over eight remedies;
- **a doubled path's one edge**: `test_a_path_listed_twice_in_one_commit_gives_one_edge`;
- **3(b)'s count over two corpora, the collector's rules against the engine
  version, `UNPOPULATED_TABLES`, and *Negative*'s third item re-measured
  through its adapter**: their amendments in decision 3, decision 2 (item 12),
  *Neutral* and *Negative*.

Landed from item 1 below in #1033's prose pull request
([#1098](https://github.com/theurian/theurian/pull/1098)), in
`packages/theurian-core/tests/integration/test_edge_collection_cli.py` unless
another file is named:

- **the `TMPDIR`-inside-a-clone rehearsal pin**:
  `test_no_replay_walks_with_tmpdir_inside_a_clone_or_outside_any_repository`
  (`packages/theurian-core/tests/integration/test_relation_cycle_cli.py`): with
  `TMPDIR` inside the project's clone and then outside it, each of the three
  replays reports a null walk commit and boundary and 0 edges;
- **an unborn `HEAD` and the depth and budget flags, as the collector records
  them**: `test_the_walk_record_and_the_report_are_what_the_walk_returned`,
  cases `unborn`, `depth` and `budget`, with `walk_first_parent` stubbed, not
  a real unborn, shallow or bounded repository: the `schema_metadata` walk
  record and the `--json` report equal the stub's commit, shallow digest and
  flags, the flags null without a commit, and `unborn` records 0 edges. A real
  unborn `HEAD`'s walk equals the `unborn` stub in `test_an_unborn_head_walks_nothing`
  (`packages/theurian-core/tests/integration/test_first_parent_walk.py`);
- **3(c)'s cap and capped digest at 32 and 33 ids, as the collector records
  them**: that test's `depth` and `budget` cases, where a commit naming 32
  distinct valid ids gives 32 edges and one naming 33 gives none and enters the
  recorded capped digest; the rule and the digest's form, at the function, are
  `test_a_commit_naming_more_than_32_distinct_valid_ids_yields_no_edges_and_is_capped`
  and `test_the_capped_digest_is_over_the_sorted_newline_terminated_shas_and_null_with_none`
  (`packages/theurian-core/tests/unit/test_adr_0040_collection_claims.py`);
- **rows 21 to 25 of #1033's *Sentences this slice falsifies***, rewritten in
  the two MCP response schemas: `test_each_sentence_1033_amends_is_still_in_its_file`,
  `row-21` to `row-25` (`packages/theurian-core/tests/unit/test_adr_0040_claims.py`),
  each phrase counted 0 times in its file.

With #1092, #1097 and #1098, item 1 is discharged. "No Git history" has no
collector-level case: outside a Git repository `resolve_context` refuses
before `migrate apply` reaches the walk (`cli/context.py:279-285`), measured
with the real CLI on #1098, and
`test_init_outside_a_git_repository_fails_clearly`
(`packages/theurian-core/tests/integration/test_cli_commands.py`) holds the
refusal for `init`; a repository with no commit is the `unborn` case above.

Still owed, with the issue or slice that will satisfy it:

1. **Discharged with #1092, #1097 and #1098, as recorded above.**
   **[#1033](https://github.com/theurian/theurian/issues/1033), edge
   collection:** the collector of decisions 2 and 3 with the build report of
   3(a) to 3(c); schema v5 (decision 4); the SQLite adapter for both port
   methods; tests for determinism at one `HEAD`, each of decision 2's levers
   with its positive control; pins that every rev-list line is validated as
   OIDs, that the messages arrive in rev-list's sequence, and that diff-tree
   returns one record per commit fed, an empty commit and a path named like an
   OID included; a pin that a rehearsal with `TMPDIR` inside a clone records a
   null commit and 0 edges, against `TMPDIR` outside any repository; a pin that
   a `fetch --deepen` interleaved between the two shallow-digest reads refuses
   the build; tests for no Git history, an unborn `HEAD`, a shallow clone and
   its boundary commit, the depth bound and both budgets, the path cap at 1,000
   and 1,001 paths, 3(c)'s cap and capped digest at 32 and 33 ids, and the full
   replace after a rebase; a no-echo test: an invalid value and a non-UTF-8
   message planted, and none of their bytes in any stdout, stderr, log, report
   field or exception text, `got {value!r}` (`identifiers.py:143`) included;
   a perturbation pin that a commit changing no
   migration and no body a migration references leaves `state_hash` unchanged;
   `UNPOPULATED_TABLES`; the CHANGELOG; *Negative*'s third item re-measured
   through its adapter; and, in the same change, each sentence decision 2's
   write or decision 5's guard makes false. Census at `5ad30799`: every tracked
   text, each JSON `description` on its own, then whitespace-joined sentences;
   a hit is a unit matching, case-insensitively, both
   `immutab|changed nothing|changes nothing|change nothing|writes nothing|wrote nothing|write nothing|nothing (is )?pending|nothing to apply|\bno-?op\b|already (fully )?applied|once (it is |it's )?built|never (re)?written|read-only once`
   and `state|database|\bDB\b|appl|migrat|built|build`: 393 units in 200 files,
   read at every match (FR-K8's row is a second match).
   The 25 units it finds false are listed, file by file, under
   [#1033's *Sentences this slice falsifies*](https://github.com/theurian/theurian/issues/1033#sentences-this-slice-falsifies);
   every other hit stays true, is out of class, or is a `.theurian/` twin
   (#971).
   **Sequencing condition:** decision 5's guard, on every apply, and
   `RelationCycleError` land in one commit with the `SCHEMA_VERSION` 4→5 bump,
   on the branch and so in the squash — never earlier (under v4 a cyclic state
   built by core-v0.5.3 hashes identically and is reused; its refused re-apply
   rolls back and keeps serving it), never later (a Core built between them
   serves a v5 state no guard checked, and a rule that no core-v* tag sits
   between them leans on release ordering, which this ADR rejects). Check:
   these three each print one sha, the same one: on the branch the commit that
   bumps the version, on `main` the squash commit.

   ```sh
   git log -S'SCHEMA_VERSION: Final = 5' --format=%H -- packages/theurian-core/src/theurian/infrastructure/sqlite/schema.py
   git log -S'.must_be_acyclic(' --format=%H -- packages/theurian-core/src
   git log -S'class RelationCycleError' --format=%H -- packages/theurian-core/src
   ```

   Their positive control, `-G'^SCHEMA_VERSION: Final = '` over the same file,
   lists every commit that changed the version line, newest first; before #1033
   its first is the 3 → 4 bump, `7d7195b0`. A later revert or cherry-pick that
   moves one without the other is outside the condition. Its pull request also
   carries a
   **BREAKING** CHANGELOG entry naming the refusal; FR-K8's "a no-op, not an
   error" (`docs/architecture/requirements-analysis.md:38`), restated as scope
   over applied sets, since under the condition a cyclic set is never applied;
   and the RED driver: a state holding a cycle written without the guard, whose
   next apply, with nothing pending, is refused. The collector and the adapter
   may be their own pull request on either side of it.
2. **[#1034](https://github.com/theurian/theurian/issues/1034),
   `knowledge.trace`:** decisions 6–8, with `TraceNodeType` added to the
   governed classes of ADR-0039's scan, which is decision 9's ground; decision
   9's tests, in the same change as the tool; #845's M-4 as an acceptance
   criterion; `system.capabilities`'s `traceability` flipped to true (F11); and
   the Phase C *Tests* row's pin that every hop's visibility decision goes
   through the non-resolving, body-free read; a `SAFETY` pin on every response.
3. **[#1035](https://github.com/theurian/theurian/issues/1035), the exit
   demonstration:** a commit carrying a trailer that names one of the 24 ADR
   twins, or a twin created first (*Negative*, second item), traced from a
   module to that ADR.
4. **[#853](https://github.com/theurian/theurian/issues/853)**, before the first
   new `TraceNodeType` member (decision 8).
