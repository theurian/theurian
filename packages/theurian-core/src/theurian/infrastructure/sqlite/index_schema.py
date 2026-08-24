"""SQLite schema for the derived retrieval index (ADR-0004, FR-R2).

**A separate database file from the canonical store, on purpose.**

The canonical store's :data:`SCHEMA_VERSION` is an input to the state hash
(ADR-0017), so putting index tables beside it would make every change to the
*index* invalidate every existing *canonical* state — a rebuild of the thing that
is authoritative, to accommodate the thing that is disposable. Exactly backwards.

Keeping the index in its own file buys three things:

- deleting it is a cache miss, never data loss (layer rule 3);
- a rebuild can be written to a new file and swapped in, which is what blue/green
  index builds in Milestone 6 need;
- an index schema change costs an index rebuild and nothing else.

The file is named for the index build id, not the state hash, because two index
builds over one canonical state are a normal thing to have — a re-embedding with
a different model changes nothing canonical.

**Version 6 changes no column and is still a real break: from it on, a build
consults the deployment's disclosure ceiling and writes no row for an item above
it** (#119, ADR-0025 part 1). The DDL moves only in `chunks.sensitivity`'s
comment, which said the column was read by no query -- and that sentence has to
change, because the column is now the record of a decision the *build* made
rather than a label nothing acted on. That alone earns the bump under this file's
own rule.

**Phase 4 moves that comment again and the version stays at 6, which is an
exception to the rule above and is argued rather than assumed.** The read-side
predicate (`_scope`, `_node_scope`) reads `chunks.sensitivity` and
`nodes.sensitivity`, columns every version-6 file already has, and writes nothing
new -- so there is no file the new code could misread, which is the whole of what
the version gate is for. Every file that could disagree with this DDL text is
version 5 or lower and is already rejected. Version 6 has never left this branch:
`main` pins 5 and the released artifacts pin 2, so "a version-6 build made by the
phase-3 code" is the only kind that exists, and the phase-4 predicate serves it
correctly by construction. Bumping to 7 would order a rebuild of files that need
none, and would split one unreleased change across two versions.

The forcing function is what makes the bump the point rather than the paperwork.
Every version-5 index predates the exclusion, so it may hold an above-ceiling
document's text -- and `chunks_fts` and `chunks_trigram` score what they return
against collection statistics computed over every row in the file, so those rows
price the visible ones whether or not any query can return them (T-17a on the
sensitivity axis). A serve path that merely *filtered* such a file would inherit
that. Bumping makes it structural: every pre-enforcement build reports
`index-schema-mismatch` on the first search and is rebuilt, under a ceiling, by
the same command that has always been the remedy. The pointer's
`indexedSensitivities` then keeps a *post*-enforcement build from being served
under a ceiling it was not built for; the version bump is what covers the builds
that recorded no ceiling at all.

**Version 5 gives `chunks` a `kind` column, so a purge can re-derive the forest
from the index's own surviving rows.** The withdrawal purge re-derives each
affected scope's trees from the published build rather than from canonical state
(ADR-0008 decision 9, ADR-0024) -- it reads the surviving chunks back and hands
them to `ForestBuilder`, which forms Domain trees by `kind` within a scope
(ADR-0008 decision 2). Version 4 kept `kind` only on the in-memory
`IndexableChunk`, so a re-derivation reading the index had no way to reconstruct
it; a `nodes`-only read cannot recover it either, since a node records its scope
and provenance but not the leaf `kind` its tree was keyed on. `DEFAULT ''` so a
chunk written before a builder set it, or by a caller that omits it, is a
buildable row rather than a failed insert -- and so the manual `INSERT`s in the
purge suite, which name the columns explicitly, do not each have to grow one.
A build under version 4 reports `index-schema-mismatch` and is rebuilt (ADR-0022
point 3), which is what puts the column under every forest a purge will read.

**Version 4 gives RAPTOR summary nodes their own tables, `nodes` and
`node_derivation`, rather than storing them as `chunks` rows.** Version 3 added
`chunks.derived` and `chunk_derivation` for a writer that did not exist yet
(ADR-0024 decision 8); the amendment to ADR-0008 decision 5 revisits that once
RAPTOR is the feature actually landing. `chunks_fts` and `chunks_trigram` are
external-content FTS5 tables scored against collection statistics computed over
*every* row in `chunks` — `N`, `avgdl`, and the per-term document frequencies —
and a RAPTOR summary systematically repeats the terms of the children it was
built from, so a derived row sharing either table would move all three under
every ordinary leaf query the caller never asked a node about. `nodes` carries
its own `nodes_fts` and `nodes_trigram`, tables neither leaf retriever reads, so
a summary's text can never move a leaf's score — and its own `node_embeddings`,
because `embeddings` is keyed on `chunk_id REFERENCES chunks` and a node id is
not a chunk id. `chunks.derived` and `chunk_derivation` are dropped
rather than kept beside the new tables: a column nothing will ever write serves
nothing, and keeping it would leave two provenance mechanisms of which one is
permanently dead.

The purge's reading of derived content (ADR-0024 decision 8) moves with the
storage: it reads `nodes` and `node_derivation` instead of `chunks` rows with
`derived = 1` and `chunk_derivation`. What it *does* with them — a node survives
only if every derivation path below it terminates at a surviving chunk — is
`_DOOMED`'s own docstring in `index_purge.py`, and is not restated here, because
a second telling of a predicate is a second thing to keep true.
`IndexStore.holds_any_revision`'s pre-check moves with it too, because it names
`node_derivation` as an executed SQL predicate on the withdrawal path
(`application/withdrawal_purge.py`), not only on the purge path.

Bumping the version means every index built under an *earlier* version reports
`index-schema-mismatch` and falls back to the substring scan until
`theurian index build` runs. That is 2 and 3 alike, not 3 alone: 3 exists only
on `main`, and 2 is what every released artifact ships — `core-v0.1.0.dev0` and
`core-v0.1.0.dev1` both pin `INDEX_SCHEMA_VERSION = 2` — so an installed
Theurian meets this bump from 2. That is ADR-0022 point 3 working as designed —
an index schema change costs an index rebuild and nothing else, never an
in-place migration of the file — not a regression.
"""

from __future__ import annotations

from typing import Final

#: Bump for ANY change to the DDL below. Independent of the canonical store's
#: version: they version separately because they are rebuilt separately.
INDEX_SCHEMA_VERSION: Final = 6

#: FTS5 is a compile-time option. It ships with the python.org, Homebrew, and
#: Debian builds, but not with every distribution's, so its absence is detected
#: and reported rather than crashing mid-build with a syntax error.
FTS5_PROBE: Final = "CREATE VIRTUAL TABLE temp.theurian_fts5_probe USING fts5(body)"

INDEX_DDL: Final = """
-- Index identity -------------------------------------------------------------
CREATE TABLE index_metadata (
    id                   INTEGER PRIMARY KEY CHECK (id = 1),
    index_schema_version INTEGER NOT NULL,
    index_build_id       TEXT    NOT NULL,
    state_hash           TEXT    NOT NULL,
    embedding_model      TEXT    NOT NULL DEFAULT '',
    embedding_dimension  INTEGER NOT NULL DEFAULT 0,
    built_at             TEXT    NOT NULL
);

-- Chunks ---------------------------------------------------------------------
-- One row per retrievable passage. `revision_id` is what makes a hit resolvable
-- back to the canonical store: the index is never authoritative, so every row
-- has to point at something that is (FR-R5).
--
-- `NOT NULL` is spelled out because `PRIMARY KEY` does not imply it here. Only
-- an INTEGER primary key is a rowid alias SQLite refuses NULL for; a TEXT one
-- admits a single NULL row. That is not a tidiness point, and the reason is
-- what this column was found to have broken: every orphan and dangling check in
-- `index_purge` was then written `x NOT IN (SELECT ...)`, and SQL's `NOT IN`
-- against a set holding one NULL answers NULL -- falsy -- for *every* row, so a
-- single NULL id here silently disabled two of them rather than failing one.
-- Measured on 3.51.2: the column took two NULLs, and
-- `'a' NOT IN (SELECT id FROM t)` then answered NULL.
--
-- All four of those are `NOT EXISTS` today -- the orphaned embedding, the
-- unprovenanced node, the dangling derivation edge, and the orphaned node
-- embedding -- so the constraint is no longer the only thing standing between
-- one NULL and a check that stops checking. (They are four of `_verify`'s six
-- post-conditions; the withdrawn-row count and the cycle count are neither
-- orphan nor dangling checks and ask no such subquery.) It stays because two
-- defences against a state nothing should ever write is the right number when
-- the failure is silent in the direction of keeping withdrawn content.
CREATE TABLE chunks (
    chunk_id     TEXT PRIMARY KEY NOT NULL,
    project_id   TEXT    NOT NULL,
    item_id      TEXT    NOT NULL,
    revision_id  TEXT    NOT NULL,
    ordinal      INTEGER NOT NULL,
    heading      TEXT    NOT NULL DEFAULT '',
    text         TEXT    NOT NULL,
    token_estimate INTEGER NOT NULL,
    -- Denormalised from the canonical store so that filtering can happen in the
    -- same statement as the match, before ranking (FR-R1). Filtering after
    -- ranking would let a caller learn that a document they may not read exists,
    -- by watching how many results disappeared.
    --
    -- `project_id`, `status` and `sensitivity` are all filtered on by a *query*.
    -- `sensitivity` stopped being inert at v6: every build now consults the
    -- deployment's disclosure ceiling and writes no row at all for an item above
    -- it (#119, ADR-0025 part 1), so this column records the class a row was
    -- admitted under rather than a label nothing acted on, and `_scope` /
    -- `_node_scope` emit an `IN` predicate over it beside the other two (#119
    -- phase 4). Which of those two is the control matters: the build is. Against
    -- a file built under the grant now in force the predicate excludes nothing,
    -- because every row in it was already admitted under that grant; it answers
    -- for a file built under a wider one. And it cannot take back what such a
    -- file's FTS5 collection statistics have already priced. A document
    -- reclassified upward *after* a build is a third case again, withheld by the
    -- canonical re-check on its current class rather than by anything here, since
    -- this column still says what was true when the row was written.
    --
    -- `trust_level` and `namespace` are read by no query, and #119 did not change
    -- that: it enforced the sensitivity axis and left these two where they were.
    -- `namespace` is populated as of the RAPTOR builder, which partitions the
    -- forest by the scope tuple this column is a component of; it is still read
    -- by no query.
    --
    -- `kind` is the one exception to "read by no query": no *retrieval* reads it,
    -- but the withdrawal purge's re-derivation does (v5). A Domain tree is keyed
    -- by `kind` within a scope (ADR-0008 decision 2), and re-deriving from the
    -- index's surviving rows means reconstructing each chunk's `kind` -- which
    -- lives nowhere else in the file once a build has finished, since a summary
    -- node records its scope but not the leaf kind its tree was clustered on.
    status       TEXT    NOT NULL,
    sensitivity  TEXT    NOT NULL,
    trust_level  TEXT    NOT NULL,
    namespace    TEXT    NOT NULL DEFAULT '',
    kind         TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX chunks_by_project ON chunks (project_id, status);
CREATE INDEX chunks_by_revision ON chunks (revision_id);

-- Lexical index --------------------------------------------------------------
-- `content=` makes this an external-content table: FTS5 stores only the index
-- and reads the text from `chunks`, rather than keeping a second copy of every
-- document. The triggers below are what keep the two in step; without them the
-- index silently drifts from the content it claims to describe.
--
-- unicode61 with `remove_diacritics 2` folds accents, so "résumé" and "resume"
-- match. Deliberately not `porter`: stemming English would mangle the
-- identifiers and code fragments that fill engineering knowledge, turning
-- `parsing` and `parse` into one term while breaking `parses_json`.
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    heading,
    text,
    content='chunks',
    content_rowid='rowid',
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TRIGGER chunks_fts_insert AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, heading, text) VALUES (new.rowid, new.heading, new.text);
END;

CREATE TRIGGER chunks_fts_delete AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, heading, text)
    VALUES ('delete', old.rowid, old.heading, old.text);
END;

CREATE TRIGGER chunks_fts_update AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, heading, text)
    VALUES ('delete', old.rowid, old.heading, old.text);
    INSERT INTO chunks_fts(rowid, heading, text) VALUES (new.rowid, new.heading, new.text);
END;

-- Substring index -----------------------------------------------------------
-- A second lexical index, tokenized by trigram.
--
-- `unicode61` splits on whitespace and punctuation only, so an entire Japanese
-- sentence becomes one token: `トークン` does not match `署名付きトークン`, and a
-- Japanese knowledge base is absent from search entirely except where a query
-- happens to equal a heading. This project's own knowledge is written in
-- Japanese, so that is not an edge case.
--
-- Trigram indexing is what makes substring matching work for a script with no
-- word boundaries. It costs disk on a file that is derived and disposable, and
-- it is kept separate rather than replacing `unicode61` because trigrams are
-- worse at what word tokenization is good at -- an exact term like `parses_json`
-- should rank on the term, not on its overlapping fragments.
CREATE VIRTUAL TABLE chunks_trigram USING fts5(
    heading,
    text,
    content='chunks',
    content_rowid='rowid',
    tokenize="trigram"
);

CREATE TRIGGER chunks_trigram_insert AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_trigram(rowid, heading, text) VALUES (new.rowid, new.heading, new.text);
END;

CREATE TRIGGER chunks_trigram_delete AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_trigram(chunks_trigram, rowid, heading, text)
    VALUES ('delete', old.rowid, old.heading, old.text);
END;

CREATE TRIGGER chunks_trigram_update AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_trigram(chunks_trigram, rowid, heading, text)
    VALUES ('delete', old.rowid, old.heading, old.text);
    INSERT INTO chunks_trigram(rowid, heading, text) VALUES (new.rowid, new.heading, new.text);
END;

-- Dense index ----------------------------------------------------------------
-- Vectors as raw little-endian float32 blobs, scanned in Python. A local
-- knowledge base is thousands of chunks, not millions, and an exact scan over
-- thousands is both fast enough and exactly reproducible -- which an ANN index
-- is not (FR-R7). Swapping in sqlite-vec or faiss later is a VectorStore
-- adapter change and nothing more (ADR-0003, ADR-0009).
--
-- `NOT NULL` for the reason `chunks.chunk_id` carries it, one step removed: a
-- foreign key constraint does not apply to a NULL value, so a NULL here is
-- accepted by both the primary key and the reference, and nothing below this
-- line would refuse it. It escaped `_verify`'s orphan count when that count was
-- written `chunk_id NOT IN (SELECT chunk_id FROM chunks)`, because NULL is not
-- "not in" anything; the count asks `NOT EXISTS` now and sees such a row, which
-- makes this constraint the difference between a build that is refused and one
-- that was never writable.
CREATE TABLE embeddings (
    chunk_id   TEXT PRIMARY KEY NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    dimension  INTEGER NOT NULL,
    vector     BLOB    NOT NULL
);

-- RAPTOR summary nodes --------------------------------------------------------
-- One row per summary, in its own table rather than in `chunks` (ADR-0008
-- decision 5's amendment, ADR-0024 decision 8's amendment). The fourteen
-- provenance columns are decision 5's own list -- a summary whose model or
-- prompt hash differs from the current configuration is stale by definition and
-- rebuilt, no guessing. `project_id`, `sensitivity` and `status` are carried
-- denormalised for the same reason `chunks` carries its own copies: filtering
-- has to happen in the same statement as the match, before ranking (FR-R1).
-- `project_id` is the future single-point predicate #119 adds; `sensitivity` is
-- today a published label and not a control (see the Context amendment to
-- ADR-0008); `status` is the build-flavor column a node-table predicate will
-- filter on once one exists. `tree_id` already encodes the full six-component
-- scope tuple `(project, tenant, sensitivity, acl_group, namespace, status)`, so
-- these three are read, not derived from it, at query time.
--
-- `theurian index build --raptor` writes these rows, through
-- `IndexStore.add_nodes`; a build without the flag writes none (ADR-0008
-- decision 10). Nothing reads one back at query time yet -- every retriever
-- names `chunks` -- so a forest is written, purged, and not yet retrieved from.
-- The table and the purge's traversal over it landed before the writer did, so
-- that the day a summary node existed it inherited a purge that already carried
-- it rather than one designed a second time under pressure.
--
-- `node_id` is `NOT NULL` for the reason `chunks.chunk_id` is: a TEXT primary
-- key admits one NULL. When the purge asked about nodes with `NOT IN`, one NULL
-- here made that answer unconditionally NULL and the check inert; it asks
-- `NOT EXISTS` now, so what this constraint buys today is that `_delete` cannot
-- meet an id it has no `DELETE` for.
--
-- `level` is bounded because ADR-0008 decision 2 builds exactly three -- Document
-- Tree, Domain Tree, Global Catalog Tree, numbered upward from the leaves; which
-- name a number carries is `node_type`'s business, not this constraint's. A row
-- claiming level 0 or 4 is a writer that has invented a tier the forest does not
-- have, and it is cheaper to refuse it here than to meet it later as a `tree_id`
-- whose depth nothing agrees on.
--
-- **It does not bound the depth of the derivation graph, and must not be read as
-- doing so.** `level` is a label on a row; the closure in
-- `index_purge._CYCLIC_NODES` walks `node_derivation`, and nothing ties an edge's
-- endpoints to a level difference. Measured: 2,000 nodes all at level 1, chained
-- 2,000 deep through `source_node_id`, satisfy this CHECK completely and take
-- that closure 3.6 s. The shallow shape its cost argument rests on is a property
-- of `application/forest_builder.py`, which builds each tier from the one below
-- it and nothing else -- not of any column here. A row written by anything else
-- is bounded by this CHECK and by nothing deeper.
CREATE TABLE nodes (
    node_id                   TEXT PRIMARY KEY NOT NULL,
    tree_id                   TEXT    NOT NULL,
    level                     INTEGER NOT NULL CHECK (level BETWEEN 1 AND 3),
    node_type                 TEXT    NOT NULL,
    text                      TEXT    NOT NULL,
    content_hash              TEXT    NOT NULL,
    summary_model             TEXT    NOT NULL,
    summary_model_revision    TEXT    NOT NULL,
    summary_prompt_hash       TEXT    NOT NULL,
    embedding_model           TEXT    NOT NULL,
    embedding_model_revision  TEXT    NOT NULL,
    embedding_dimension       INTEGER NOT NULL,
    source_revision_id        TEXT    NOT NULL,
    index_build_id            TEXT    NOT NULL,
    project_id                TEXT    NOT NULL,
    sensitivity               TEXT    NOT NULL,
    status                    TEXT    NOT NULL
);

CREATE INDEX nodes_by_project ON nodes (project_id, status);
CREATE INDEX nodes_by_tree ON nodes (tree_id);

-- Provenance of node rows -----------------------------------------------------
-- One row per (node, thing it was built from). A Document-tree node is built
-- from chunks; a Domain-tree node is built from Document-tree nodes (ADR-0008
-- decision 2's three levels) -- so an edge names exactly one of a chunk or
-- another node, never both, which the CHECK below enforces per row rather than
-- trusting every writer to keep the two columns consistent.
--
-- The purge walks this transitively, exactly as it walked `chunk_derivation` at
-- v3: a node built from a withdrawn chunk holds that chunk's content and must
-- go with it, and so must a node built from *that* node (ADR-0024 decision 8).
--
-- `ON DELETE CASCADE` on both source columns removes the *edge* when the thing
-- it points at goes, which is not the same as removing the node that has the
-- edge -- that is why the purge deletes through a recursive query rather than
-- trusting the cascade. The cascade is here so an edge can never outlive the row
-- it points at, which is what makes "a node with no surviving edges" a state
-- worth testing for rather than an artifact of bookkeeping.
--
-- The second CHECK refuses a node that names *itself* as a source. The first
-- says an edge names exactly one kind of thing; it says nothing about which one,
-- so `('n1', NULL, 'n1')` satisfied it. A self edge is the smallest provenance
-- cycle, and a cycle is the shape the purge's well-founded reading has to treat
-- as never-grounded -- refusing it here means the traversal meets one fewer
-- shape it can only answer by giving up on.
CREATE TABLE node_derivation (
    node_id         TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    source_chunk_id TEXT REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    source_node_id  TEXT REFERENCES nodes(node_id) ON DELETE CASCADE,
    CHECK (
        (source_chunk_id IS NOT NULL AND source_node_id IS NULL)
        OR (source_chunk_id IS NULL AND source_node_id IS NOT NULL)
    ),
    CHECK (source_node_id IS NULL OR source_node_id <> node_id)
);

-- Two partial unique indexes, not one over all three columns. The three-column
-- version never fired: the CHECK above guarantees one of the two source columns
-- is NULL in every row, SQL's UNIQUE treats a NULL as distinct from every other
-- NULL including itself, and so an index containing an always-NULL column
-- compares equal to nothing. Measured: three byte-identical rows inserted
-- through it. Each partial index is restricted to the rows where its own source
-- column is populated, which is exactly where the comparison means something.
CREATE UNIQUE INDEX node_derivation_chunk_edge
    ON node_derivation (node_id, source_chunk_id) WHERE source_chunk_id IS NOT NULL;
CREATE UNIQUE INDEX node_derivation_node_edge
    ON node_derivation (node_id, source_node_id) WHERE source_node_id IS NOT NULL;
-- A partial index cannot answer a lookup that does not imply its WHERE clause,
-- so the two above do not serve `WHERE node_id = ?` -- which the three-column
-- index they replace did, by accident of being its leftmost column. Without this
-- one, "a node with no derivation edge at all" degrades to a scan of the whole
-- edge table per node: measured on this schema at 227.8 ms over 1,100 nodes and
-- 5.78 s over 5,500, against 0.29 ms and 1.42 ms with it on the same builds.
CREATE INDEX node_derivation_by_node ON node_derivation (node_id);
CREATE INDEX node_derivation_by_source_chunk ON node_derivation (source_chunk_id);
CREATE INDEX node_derivation_by_source_node ON node_derivation (source_node_id);

-- Lexical index over node text -------------------------------------------------
-- External-content FTS5 over `nodes(text)`, deliberately its own table rather
-- than sharing `chunks_fts`. `bm25` scores every visible row against collection
-- statistics computed over *every* row in the table it is asked about -- `N`,
-- `avgdl`, and the per-term document frequencies -- and a RAPTOR summary
-- systematically repeats the terms of the children it summarises. A derived row
-- sharing `chunks_fts` would move all three under every ordinary leaf query the
-- caller never asked a node about, so a visible leaf's rank would become a
-- function of the forest's shape. Keeping node text in its own FTS5 table makes
-- that channel structurally absent rather than merely unexercised.
CREATE VIRTUAL TABLE nodes_fts USING fts5(
    text,
    content='nodes',
    content_rowid='rowid',
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TRIGGER nodes_fts_insert AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TRIGGER nodes_fts_delete AFTER DELETE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;

CREATE TRIGGER nodes_fts_update AFTER UPDATE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
    INSERT INTO nodes_fts(rowid, text) VALUES (new.rowid, new.text);
END;

-- Substring index over node text ----------------------------------------------
-- `chunks_trigram`'s counterpart, and it exists for the same reason: `unicode61`
-- splits on whitespace and punctuation only, so a Japanese summary is one token
-- and matches nothing short of itself. This project's own knowledge is written
-- in Japanese, so a summary of it would be absent from substring search
-- entirely without this table.
--
-- Its own table rather than a third and fourth column on `chunks_trigram`, for
-- the reason `nodes_fts` is separate from `chunks_fts`: `bm25` scores a visible
-- row against statistics computed over every row of the table it reads, and a
-- summary repeats its children's terms by construction.
CREATE VIRTUAL TABLE nodes_trigram USING fts5(
    text,
    content='nodes',
    content_rowid='rowid',
    tokenize="trigram"
);

CREATE TRIGGER nodes_trigram_insert AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_trigram(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TRIGGER nodes_trigram_delete AFTER DELETE ON nodes BEGIN
    INSERT INTO nodes_trigram(nodes_trigram, rowid, text) VALUES ('delete', old.rowid, old.text);
END;

CREATE TRIGGER nodes_trigram_update AFTER UPDATE ON nodes BEGIN
    INSERT INTO nodes_trigram(nodes_trigram, rowid, text) VALUES ('delete', old.rowid, old.text);
    INSERT INTO nodes_trigram(rowid, text) VALUES (new.rowid, new.text);
END;

-- Dense index over node text ---------------------------------------------------
-- `embeddings`' counterpart. `embeddings` cannot hold a summary's vector: it is
-- keyed on `chunk_id REFERENCES chunks`, and a node id is not a chunk id, so
-- without this table a RAPTOR summary has nowhere to store one and dense search
-- over the forest cannot exist. Same shape, same raw little-endian float32 blob,
-- same `ON DELETE CASCADE` -- which is what keeps a purged node's vector from
-- outliving the node, exactly as it does for a purged chunk's.
CREATE TABLE node_embeddings (
    node_id    TEXT PRIMARY KEY NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    dimension  INTEGER NOT NULL,
    vector     BLOB    NOT NULL
);
"""
