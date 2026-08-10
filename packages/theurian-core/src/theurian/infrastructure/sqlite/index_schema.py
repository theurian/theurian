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

**Version 3 adds `chunks.derived` and `chunk_derivation` for a feature that does
not exist yet.** ADR-0024 decision 8: withdrawal is transitive over derived
content, because a purge can delete a chunk and cannot delete a sentence out of a
summary built from it. RAPTOR (ADR-0008) is what will write those rows; the
columns and the purge's traversal land first, so that the day a summary node
exists it inherits a purge that already carries it rather than one designed a
second time under pressure.

Bumping the version means every index built under 2 reports
`index-schema-mismatch` and falls back to the substring scan until
`theurian index build` runs. That is ADR-0022 point 3 working as designed — an
index schema change costs an index rebuild and nothing else — not a regression.
"""

from __future__ import annotations

from typing import Final

#: Bump for ANY change to the DDL below. Independent of the canonical store's
#: version: they version separately because they are rebuilt separately.
INDEX_SCHEMA_VERSION: Final = 3

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
CREATE TABLE chunks (
    chunk_id     TEXT PRIMARY KEY,
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
    -- Today only `status` is filtered on. `sensitivity`, `trust_level`, and
    -- `namespace` are carried for the scope filtering #119 adds (Milestone 6)
    -- and are read by no query yet -- said plainly here because a comment that implies
    -- an access control which does not exist is how the next person concludes
    -- it is already handled. `namespace` is not even populated by the builder.
    status       TEXT    NOT NULL,
    sensitivity  TEXT    NOT NULL,
    trust_level  TEXT    NOT NULL,
    namespace    TEXT    NOT NULL DEFAULT '',
    -- Whether this row's text was *derived* from other rows rather than read
    -- from a revision. Nothing writes 1 yet -- RAPTOR (ADR-0008) is the first
    -- thing that will -- and the column exists ahead of it because withdrawal
    -- has to be transitive from the first build that has anything to be
    -- transitive over (ADR-0024 decision 8). A summary is not withdrawn by
    -- deleting the chunk it summarises: the sentence is still in the summary.
    derived      INTEGER NOT NULL DEFAULT 0 CHECK (derived IN (0, 1))
);

CREATE INDEX chunks_by_project ON chunks (project_id, status);
CREATE INDEX chunks_by_revision ON chunks (revision_id);

-- Provenance of derived rows -------------------------------------------------
-- One edge per (derived node, chunk it was built from). The purge walks this
-- transitively: a node built from a withdrawn chunk holds that chunk's content
-- and must go with it, and so must a node built from *that* node (ADR-0024
-- decision 8).
--
-- `ON DELETE CASCADE` on `source_chunk_id` removes the *edge* when a source
-- goes, which is not the same as removing the node -- that is why the purge
-- deletes through a recursive query rather than trusting the cascade. The
-- cascade is here so an edge can never outlive the row it points at, which is
-- what makes "a derived node with no surviving edges" a state worth testing for
-- rather than an artifact of bookkeeping.
CREATE TABLE chunk_derivation (
    node_chunk_id   TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    source_chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    PRIMARY KEY (node_chunk_id, source_chunk_id)
);

CREATE INDEX chunk_derivation_by_source ON chunk_derivation (source_chunk_id);

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
CREATE TABLE embeddings (
    chunk_id   TEXT PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    dimension  INTEGER NOT NULL,
    vector     BLOB    NOT NULL
);
"""
