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
"""

from __future__ import annotations

from typing import Final

#: Bump for ANY change to the DDL below. Independent of the canonical store's
#: version: they version separately because they are rebuilt separately.
INDEX_SCHEMA_VERSION: Final = 1

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
    -- Denormalised from the canonical store so that FR-R1 filtering happens
    -- before ranking rather than after. Filtering after ranking would let a
    -- caller learn that a document they may not read exists, by watching how
    -- many results disappeared.
    status       TEXT    NOT NULL,
    sensitivity  TEXT    NOT NULL,
    trust_level  TEXT    NOT NULL,
    namespace    TEXT    NOT NULL DEFAULT ''
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
