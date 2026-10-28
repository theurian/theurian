# ADR-0040: The traceability representation, and where Phase C's edges come from

- Status: accepted
- Date: 2026-10-28
- Deciders: Theurian maintainers
- Requirements: FR-T2, FR-T3, INV-6, SEC-13
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
it must move this record too.

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

**The four alternatives, with their keys.** These are every source the roadmap
or `traceability.md` names that exists in the repository today; review threads
and test names, which `traceability.md` also draws, are not collected anywhere
and are out of scope.

| | Source | Measured | Verdict |
| :-- | :-- | :-- | :-- |
| (i) | `Refs: <item id>` trailer | 0 lines on `main` (F4) | **Chosen.** Explicit, per commit, reaches a path |
| (ii) | Prose mentions in commit messages, `ADR-NNNN` | 380 lines (F4) | **Rejected.** Roadmap §10 names false traceability the product's largest risk, and a mention is not a declaration: a message saying "unlike ADR-0007" mentions it. A mention names an ADR number, which needs resolving to an item (decision 3), and 16 of 40 ADRs have no twin (F1). If ever added, it is a distinct source, with a published confidence below 1.0 and field names that keep it apart from *recorded* edges |
| (iii) | Hand-authored `addRelation` migrations | 0 in the corpus (F2) | **Rejected.** Item to item only (F7). Reaching a module needs either a new operation — an `apiVersion` bump under ADR-0039 decision 3, and an owner decision — or modules entered as items, which is a new `kind`, additive under ADR-0039 decision 2 but due only after [#849](https://github.com/theurian/theurian/issues/849) and [#853](https://github.com/theurian/theurian/issues/853), and a governed item per module. `addRelation` rows stay item-to-item edges that `knowledge.trace` walks (decision 7) |
| (iv) | `scope.paths` globs | 55 of 56 match any core module (F3) | **Rejected.** An edge from every core module to nearly every item says nothing. It stays a candidate source once the corpus's globs are module-level |

### 2. Every apply collects, over first-parent history at `HEAD`; the commit is recorded, not hashed

**Collection runs inside the state build** — the write transaction `migrate
apply` already runs (`apply_migration_set`, `cli/migration_pipeline.py`) — after
the migration set applies. It walks `git log --first-parent HEAD`, reads each
commit's `Refs:` trailers (decision 3) and the paths the commit changed against
its first parent, and writes the edges into `traceability_edges`.

**Every `migrate apply` collects, including one with nothing pending,** and
replaces the whole edge set, the recorded walk commit and the boundary inside its
write transaction. It is a full replace, not an incremental walk from the stored
commit: after a rebase or a reset, the stored commit is not an ancestor of
`HEAD`. The expected-count record keeps its condition
(`cli/migration_pipeline.py:146`), because edges are not that record.

**The walk commit is recorded, not hashed.** It is §6 principle 2's "commit it
was measured against", recorded in `schema_metadata` (decision 4) and published
by every trace (decision 7). Principle 2 asks a response to state its commit,
not that every commit invalidate the state, and ADR-0016 rejected a commit sha
in the hash: "a code-only commit touching no knowledge would invalidate a valid
state". **A commit that changes no migration and no body a migration references
leaves `state_hash` unchanged.**

**A state's identity is (`state_hash`, walk commit, boundary), and two states
with one identity hold the same edges;** a state whose collection was capped
never shares an identity with one whose was not. `state_hash` names the
canonical state (ADR-0016) and, through `MIGRATION_ENGINE_VERSION`, the
collector's rules, so a change to them, the depth bound and decision 3(c)'s cap
among them, bumps it. The boundary is what the walk left uncollected: the
shallow digest, whether the depth bound stopped it, and decision 3(c)'s capped
digest. The collector reads Git and nothing else (decision 3(b)), so after an
apply the edges are a function of the walk commit and the boundary under those
rules, because the walk is deterministic:

- `--no-renames`: rename detection depends on `diff.renames` and similarity
  thresholds, which are local configuration. A renaming commit yields edges from
  both the old and the new path.
- The hardening ADR-0029's trailer reader already applies (its Amendment 1,
  D7; `infrastructure/git/trailer_source.py`): no replace objects, inherited
  `GIT_*` overrides stripped, `log.showSignature` off, NUL-framed records, and a
  commit message that is not UTF-8 contained to its own record (#496). A path
  git emits that is not UTF-8 is not collected, by the same rule.
- An edge's id and `created_at` derive from its commit — the sha, the path and
  the item id; the committer date — never from the engine's clock or an id
  generator. The evidence's required `source_uri` derives from the commit and
  the path, never from `git remote get-url origin`, which differs between
  clones and prints a filesystem path for a local remote — an operator's
  layout, the class GHSA-923w-f36f-jcfq closed in MCP error responses.
- A failure to read `HEAD`, a walk that fails or outlives the 30-second
  subprocess timeout ADR-0029's reader already applies
  (`infrastructure/git/trailer_source.py:118`), and a stream the framing cannot
  read refuse the build. None of them degrades to "no edges", because then one
  identity would name two edge sets. `current_commit` returns `None` both for an
  unborn `HEAD` and for a failure (`cli/context.py:81-93`), so the collector
  reads `HEAD` through a call that tells the two apart.

**The walk reads at most 10,000 first-parent commits, the walk commit first,**
and one that reaches the bound stops there and records it in the boundary. A
depth stops every machine at the same commit; under a time limit the edge set,
or whether `migrate apply` succeeds for any tool, would vary with the machine's
load. At `5ad30799` the first-parent history is 427 commits
(`git rev-list --first-parent --count 5ad30799`), which
`git log --first-parent --no-renames --name-only -z` read in about 30 ms here:
the bound is some 23 times this history. **The edges one apply writes are at
most 32 times the changed paths the walk reads** (decision 3(c)), a read the
depth and the 30-second timeout above bound.

**It reads `HEAD`, not ADR-0029's `refs/remotes/origin/main`.** ADR-0029 pins
public history because an embargoed finding must not enter a corpus built from
it. An edge describes the branch the state describes (ADR-0016) and stays in that
working tree's own state database.

**First-parent** is the line the branch itself records. Under this repository's
squash merges it is the whole history (`git rev-list --merges --count 5ad30799`
prints 0). In a merge workflow a side-branch commit's trailer is collected only
when the merge commit's message carries it (*Negative*).

**With no Git history, the state holds no edges and records no commit.** The
CLI refuses outside a Git repository (`cli/context.py:246-252`), so the cases
are a rehearsal's throwaway copy, which is not a Git working tree
(`cli/migration_pipeline.py:71-75`), and a repository whose `HEAD` is unborn. In
both, `schema_metadata` records the walk commit and the boundary as null, and a
trace reports the commit as null and zero edges walked.

**On a shallow clone, the walk stops at the shallow boundary.** The shallow
digest is the lowercase hex SHA-256 of the clone's shallow-commit list — the
shas in the file `git rev-parse --git-path shallow` names, sorted byte-wise,
each followed by a newline — and null when no such file exists, which is a full
clone. A commit the list names yields no edges: its parent is absent, so
`git log` diffs it against the empty tree. In a depth-2 clone at `5ad30799` the
boundary commit `54a9f673` lists 1,159 paths, its whole tree
(`git ls-tree -r --name-only 54a9f673 | wc -l`), against the 2 the full history
lists. A trace publishes the shallow flag, not the digest (decision 7).

**Not in `ingest`, and not a new command.** `ingest` writes only
`.theurian/cache/ingestion.json`, reads `HEAD`'s sha and no history, and never
opens the state database (F10). Collecting there, or in a command of its own,
gives the state database a second writer whose run is optional, so whether a
state held any edges would depend on a command nothing else requires.

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
  edge table itself would vary with withheld content. The per-hop gate withholds
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
  It does not refuse, or one hostile commit would refuse every later build.
  32 is over four times the
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

### 4. The schema: `source_commit`, and `SCHEMA_VERSION` 4 → 5

`traceability_edges` gains `source_commit TEXT NOT NULL` — every edge is
measured against a commit, roadmap §10's "mandatory commit anchor". The commit
is also inside `evidence`, but that column is a JSON array SQL cannot index or
compare, and a later source may carry evidence from several commits while
`source_commit` names the one it was measured against. `relation_type` holds a
`RelationType` value, and #1033 narrows `TraceabilityEdge.relation_type` from
`str` (F9) to `RelationType`; roadmap §4 keeps edge types closed.
`schema_metadata` gains decision 2's walk commit and boundary — the shallow
digest, the depth-bound flag and the capped digest — all null with no walk.

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

**The refusal is a new `RelationCycleError`, a `MigrationError`, not
`MigrationCycleError`.** That one means a `dependsOn` cycle among migrations, so
"no application order exists" (`domain/errors.py:113-119`); it carries migration
ids, and the CLI's remedy for it is "Break the dependency cycle shown above, then
retry." (`cli/commands.py:767-768`). A relation cycle's cure is a new migration
with a `removeRelation`, so the new error names the relation type and the
cycle's item ids, and its remedy says that. It is raised by `migrate apply` and
by the CLI's proposal-accept rehearsal; the MCP composition root wires the
rehearsal to `_accept_is_unreachable` (`mcp/tools.py:1973`), so no MCP response
carries it.

**What an existing cyclic corpus meets.** The dogfood corpus meets nothing: it
holds no relation row (F2). Corpora in other repositories cannot be measured
from here. One with a cycle meets `RelationCycleError` at its first apply under
the enforcing Core, naming the cycle; until a migration removes a relation of
it, that Core builds no state for the project.

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
(`resolve_state_hash(loaded, SCHEMA_VERSION)`, `cli/context.py:262`,
`cli/migration_pipeline.py:209`, `:362`), which only the enforcing apply builds.
A corpus already cyclic under v4 is served nothing by an enforcing Core until a
migration removes a relation of its cycle.

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
  (`schema.py:400-401`) — and each endpoint is read in the edge row's
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
| `recordedEdgesDepthBounded` | whether collection stopped at the depth bound (decision 2) |
| `recordedEdgesCapped` | whether collection skipped a commit over decision 3(c)'s cap |
| `recordedEdgesCoverage` | the constant `"not-claimed"`: the response asserts nothing about edges that were never recorded |

Descriptions say *recorded* edges and never claim completeness (Phase C
*Risks*). Evidence and confidence pass through unchanged from the stored edge. A
`knowledge_relations` hop is published with exactly the fields `knowledge.get`'s
`relations` publishes for that row after the same gate, so `trace` publishes no
field of a relation that `knowledge.get` withholds. No title, body or excerpt of
any item is in a trace response; a caller reads content through
`knowledge.get`.

**What a hop follows.** A recorded traceability edge, in either direction —
`related_to` is not in `INVERSE_RELATIONS`, so an edge stored from a path to an
item is also walked from the item — or a `knowledge_relations` row.

**Limits, as numbers.** Depth at most 4, default 2 — the chain the roadmap's
*User value* names, code → PR → spec → ADR → requirement, is four hops. At most
100 nodes and 500 edges per response. Every limit counts units that passed the
gate, so a withheld node never takes a slot; reaching one sets `truncated`. Each
endpoint is read at most once per request. The pre-gate rows a request reads are
bounded by 100 times the largest stored fan-out of a node, which is a property
of the corpus and its history, not of the request; #1034 records its measured
cost on the dogfood corpus.

**The start node.** An item id that is absent or withheld gets the refusal
`knowledge.get` gives an absent id, byte for byte. A `CODE` start is the
caller's own string and is not gated; it reaches only what its edges reach
through the gate.

### 8. Governed parameters, and `TraceNodeType` joins ADR-0039's sets

`knowledge.trace`'s edge-type, direction and node-type parameters are typed `str`
in the handler and parsed by `_closed_value` (`mcp/tools.py:1253`), never
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
  `recordedEdgesCommit` says which; nothing raises a staleness alarm. #1033
  owes the walk's measured cost per apply, at the depth bound.
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
- **ADR-0038's revision pin stays unrepresented.** ADR-0038 left to this record
  whether an edge carries the revision pin the fold drops. It does not: an edge
  carries a commit, and ADR-0038 records why a commit is not that pin.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **Prose mentions, `addRelation`, `scope.paths`** as the source | Decision 1's table, each with its key |
| **`constrained_by`** as the relation | Projects a commit-level declaration onto every changed path as a per-path constraint (decision 1) |
| **A `commit` `TraceNode`**, with edges path → commit → item | A new `TraceNodeType` member, so #853 first (decision 8); two hops per declaration against the four-hop depth limit; and the commit is what measured the edge, not what the edge is about |
| **Collect in `ingest`**, as the roadmap row planned, **or in a new command** | `ingest` writes no state (F10); either gives the state database an optional second writer (decision 2) |
| **`HEAD` in `state_hash`** | §6 principle 2 asks the response to state its commit, not that every commit invalidate the state, and ADR-0016 rejected a commit sha in the hash for invalidating a valid state on a code-only commit. A staleness alarm after every commit, the stale index label (*Two further facts*) among its faces, is §10's trust failure by another route: a signal that is always on is read as noise. Read from source, not run (decision 2) |
| **Hash a digest of the collected edges** | Every command that resolves a project computes the hash, and the digest needs the whole walk each time (decision 2) |
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
  reddens when the codebase moves. Its standing invariants are F6's exact
  match, F7's two item-id endpoints, the label's lack of cross-item
  uniqueness, and the state hash's reading no Git; every other fact is dated,
  and its fact side is how #1033 and #1034 learn this record must move.
  Decision 2's identity and digests, 3(c)'s cap and decision 5's guard have no
  fact side until #1033 builds them. Its reach is stated in its module docstring.

Rests on enforcement that already holds:

- **The one-hop gate.** `_relation_is_visible` reads both endpoints through
  `get_item_exact_metadata` (F12), the read decision 6 extends to every hop.
- **The exact-match open** (F6), which decision 4's v4/v5 refusal is.
- **ADR-0029's trailer extraction and hardening** (`keyed_lines`, the
  sanitized child environment), which decisions 2 and 3 reuse.
- **`_closed_value`**, the parse decision 8 requires.

Still owed, with the issue or slice that will satisfy it:

1. **[#1033](https://github.com/theurian/theurian/issues/1033), edge
   collection:** the collector of decisions 2 and 3 with the build report of
   3(a) to 3(c); schema v5 (decision 4); the SQLite adapter for both port
   methods; tests for determinism at one `HEAD`, for no Git history, for an
   unborn `HEAD`, for a shallow clone and its boundary commit, for the depth
   bound, for 3(c)'s cap and capped digest at 32 and at 33 ids, and for the full
   replace after a rebase; a perturbation pin that a commit changing no
   migration and no body a migration references leaves `state_hash` unchanged; `UNPOPULATED_TABLES`; the CHANGELOG; the measured
   cost of *Negative*'s third item; and, in the same change, each sentence its
   nothing-pending write makes false — at `5ad30799`,
   `cli/migration_pipeline.py:153-154`, `cli/commands.py:1135-1136` and
   `:2532-2533` (`migrate apply`'s `--help`),
   `docs/security/threat-model.md:4749-4750`, and
   `docs/adr/0018-single-writer-synchronous-in-m1.md:36-38` and `:262-264`. Key:
   `git grep -n -i -E 'nothing pending|writes nothing|changes nothing|a no-op' 5ad30799 -- docs packages/theurian-core/src`,
   read hit by hit, plus a whitespace-joined read for wrapped phrases; sentences
   about the expected-count record, which keeps its condition, stay true.
   **Sequencing condition:** INV-6 enforcement at apply lands in the same PR as
   the SCHEMA_VERSION 4→5 bump — never earlier (under v4 a cyclic state built by
   core-v0.5.3 hashes identically and is reused; its refused re-apply rolls back
   and keeps serving it), never later ((c), a closure leaning on release
   ordering, rejected in R2). So the pull request that bumps `SCHEMA_VERSION`
   also carries decision 5's guard, on every apply; `RelationCycleError`; a
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
   through the non-resolving, body-free read.
3. **[#1035](https://github.com/theurian/theurian/issues/1035), the exit
   demonstration:** a commit carrying a trailer that names one of the 24 ADR
   twins, or a twin created first (*Negative*, second item), traced from a
   module to that ADR.
4. **[#853](https://github.com/theurian/theurian/issues/853)**, before the first
   new `TraceNodeType` member (decision 8).
