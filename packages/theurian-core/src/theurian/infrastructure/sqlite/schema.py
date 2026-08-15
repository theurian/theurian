"""SQLite schema for the derived canonical store (ADR-0004, ADR-0017).

This database is a projection. Every byte in it is reconstructible by replaying
Git-tracked YAML migrations into an empty file, which is what makes discarding
it on a schema change a cache miss rather than data loss.

``SCHEMA_VERSION`` is an input to the state hash, so bumping it invalidates
every existing state database. Bump it for any change to the DDL below, and for
a correctness fix that must not trust state an earlier build already derived --
the bump is what forces such a database to be rebuilt rather than read in place.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final
from urllib.parse import quote

#: Bump for any change that must invalidate every existing state database: a
#: change to the DDL below (ADR-0017), or a correctness fix in how state is
#: derived, since a bump is the mechanism that forces an older file to be rebuilt
#: from its Git-tracked migrations rather than read in place.
SCHEMA_VERSION: Final = 2

#: Applied to every connection. `foreign_keys` is per-connection in SQLite, not
#: per-database, so forgetting it on one connection silently disables referential
#: integrity for that connection's writes.
CONNECTION_PRAGMAS: Final = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA synchronous = NORMAL",
)


def read_only_uri(path: Path) -> str:
    """``file:`` URI for a path opened `mode=ro`, with the path escaped.

    **A filename is not a URI, and the difference is the access mode.** SQLite
    parses everything after ``?`` as query parameters, so a path that happens to
    contain one is read as a mode the caller did not ask for -- measured on
    SQLite 3.47.1, a path ending ``?mode=rwc`` turns ``?mode=ro`` into
    ``no such access mode: rwc?mode=ro``, and on other builds it wins outright
    and opens read-write. Either way the `mode=ro` guarantee stops holding for a
    filename the operator chose.

    Shared by both readers -- `index_store._open_read` and `index_purge._copy`'s
    source -- so a path a purge copies from is escaped the same way a query
    reads one. `quote` with no safe characters but `/`, because `?`, `#` and `%`
    are all legal in a POSIX filename and each changes how the URI parses.
    """
    return f"file:{quote(str(path), safe='/')}?mode=ro"


DDL: Final = """
-- Schema identity -----------------------------------------------------------
-- Stored inside the database as well as encoded in its filename, so a mismatch
-- is detectable even if the file is renamed or copied (ADR-0017).
CREATE TABLE schema_metadata (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    schema_version  INTEGER NOT NULL,
    engine_version  INTEGER NOT NULL,
    state_hash      TEXT    NOT NULL,
    created_at      TEXT    NOT NULL
);

-- Projects ------------------------------------------------------------------
CREATE TABLE projects (
    project_id          TEXT PRIMARY KEY,
    root_path           TEXT NOT NULL,
    repository_url      TEXT,
    default_branch      TEXT NOT NULL,
    knowledge_directory TEXT NOT NULL,
    tenant_id           TEXT NOT NULL DEFAULT 'local',
    registered_at       TEXT NOT NULL,
    last_seen_commit    TEXT
);

-- Knowledge -----------------------------------------------------------------
-- Revisions are immutable (ADR-0006). There is no UPDATE path for this table in
-- the store adapter; corrections append a new row.
--
-- `revision_id` alone is the key, so it is unique across every project sharing
-- this database -- while every read of the table is scoped by `project_id` as
-- well, and `knowledge_items` below is keyed `(project_id, item_id)`. The two
-- do not meet: a project id changing over an unchanged root leaves revisions
-- stranded under the old id, behind a foreign key that `PRAGMA
-- foreign_key_check` reports as satisfied. Filed for Milestone 6 as
-- https://github.com/theurian/theurian/issues/24, which also owns the four
-- `# pragma: no cover` branches whose "the pointer is a foreign key"
-- justification this falsifies.
CREATE TABLE knowledge_revisions (
    revision_id     TEXT PRIMARY KEY,
    item_id         TEXT NOT NULL,
    project_id      TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    migration_id    TEXT NOT NULL,
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    content_type    TEXT NOT NULL,
    content_sha256  TEXT NOT NULL,
    kind            TEXT NOT NULL,
    namespace       TEXT NOT NULL,
    status          TEXT NOT NULL,
    trust_level     TEXT NOT NULL,
    sensitivity     TEXT NOT NULL,
    owner           TEXT NOT NULL,
    tenant_id       TEXT NOT NULL DEFAULT 'local',
    acl_group       TEXT NOT NULL DEFAULT 'default',
    labels          TEXT NOT NULL DEFAULT '[]',
    scope_paths     TEXT NOT NULL DEFAULT '[]',
    structured      TEXT,
    valid_from      TEXT NOT NULL,
    valid_to        TEXT,
    author          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    source_commit   TEXT,
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE INDEX idx_revisions_item ON knowledge_revisions(project_id, item_id, created_at);
CREATE INDEX idx_revisions_migration ON knowledge_revisions(migration_id);

-- The mutable pointer. `current_revision_id` is the one thing that moves.
CREATE TABLE knowledge_items (
    item_id             TEXT NOT NULL,
    project_id          TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    namespace           TEXT NOT NULL,
    kind                TEXT NOT NULL,
    status              TEXT NOT NULL,
    current_revision_id TEXT REFERENCES knowledge_revisions(revision_id),
    owner               TEXT NOT NULL,
    trust_level         TEXT NOT NULL,
    sensitivity         TEXT NOT NULL,
    tenant_id           TEXT NOT NULL DEFAULT 'local',
    acl_group           TEXT NOT NULL DEFAULT 'default',
    valid_from          TEXT NOT NULL,
    valid_to            TEXT,
    PRIMARY KEY (project_id, item_id)
);

CREATE INDEX idx_items_namespace ON knowledge_items(project_id, namespace);
CREATE INDEX idx_items_status ON knowledge_items(project_id, status);

CREATE TABLE knowledge_aliases (
    alias      TEXT NOT NULL,
    item_id    TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, alias),
    CHECK (alias <> item_id)
);

CREATE TABLE knowledge_relations (
    project_id     TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    source_item_id TEXT NOT NULL,
    relation_type  TEXT NOT NULL,
    target_item_id TEXT NOT NULL,
    note           TEXT,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (project_id, source_item_id, relation_type, target_item_id),
    CHECK (source_item_id <> target_item_id)
);

CREATE INDEX idx_relations_target ON knowledge_relations(project_id, target_item_id);

-- Provenance: where canonical content came from.
CREATE TABLE source_anchors (
    anchor_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    revision_id  TEXT REFERENCES knowledge_revisions(revision_id) ON DELETE CASCADE,
    project_id   TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    provider     TEXT NOT NULL,
    source_uri   TEXT NOT NULL,
    repository   TEXT,
    commit_sha   TEXT,
    blob_sha     TEXT,
    file_path    TEXT,
    line_start   INTEGER,
    line_end     INTEGER,
    external_id  TEXT,
    CHECK (line_start IS NULL OR line_start >= 1),
    CHECK (line_end IS NULL OR line_start IS NOT NULL),
    CHECK (line_end IS NULL OR line_end >= line_start)
);

CREATE INDEX idx_anchors_revision ON source_anchors(revision_id);

-- Evidence: why a claim is believed, as opposed to where its text came from.
CREATE TABLE knowledge_evidence (
    evidence_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    item_id      TEXT NOT NULL,
    provider     TEXT NOT NULL,
    source_uri   TEXT NOT NULL,
    repository   TEXT,
    commit_sha   TEXT,
    file_path    TEXT,
    line_start   INTEGER,
    line_end     INTEGER,
    external_id  TEXT,
    description  TEXT NOT NULL,
    confidence   REAL NOT NULL,
    created_at   TEXT NOT NULL,
    CHECK (confidence BETWEEN 0.0 AND 1.0),
    UNIQUE (project_id, item_id, source_uri)
);

-- Specifications ------------------------------------------------------------
CREATE TABLE specifications (
    spec_id        TEXT NOT NULL,
    project_id     TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    revision_id    TEXT NOT NULL REFERENCES knowledge_revisions(revision_id),
    title          TEXT NOT NULL,
    status         TEXT NOT NULL,
    content_format TEXT NOT NULL,
    source_uri     TEXT NOT NULL,
    structured     TEXT NOT NULL DEFAULT '{}',
    valid_from     TEXT NOT NULL,
    valid_to       TEXT,
    superseded_by  TEXT,
    PRIMARY KEY (project_id, spec_id)
);

CREATE TABLE traceability_edges (
    edge_id       TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    source_type   TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    target_type   TEXT NOT NULL,
    target_id     TEXT NOT NULL,
    evidence      TEXT NOT NULL DEFAULT '[]',
    confidence    REAL NOT NULL,
    created_at    TEXT NOT NULL,
    CHECK (confidence BETWEEN 0.0 AND 1.0)
);

CREATE INDEX idx_edges_source ON traceability_edges(project_id, source_type, source_id);
CREATE INDEX idx_edges_target ON traceability_edges(project_id, target_type, target_id);

-- Migration history ---------------------------------------------------------
-- The checksum is what makes editing an applied migration detectable. Without
-- it, a silently edited migration would apply cleanly and the store would
-- disagree with Git about what happened (ADR-0005).
CREATE TABLE migration_history (
    migration_id TEXT NOT NULL,
    project_id   TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    checksum     TEXT NOT NULL,
    applied_at   TEXT NOT NULL,
    sequence     INTEGER NOT NULL,
    PRIMARY KEY (project_id, migration_id)
);

CREATE INDEX idx_migration_history_sequence ON migration_history(project_id, sequence);
"""


def is_supported(schema_version: int) -> bool:
    """Whether this build can read a database recorded at ``schema_version``.

    Exact match only. There is deliberately no compatibility window: a state
    database is rebuildable in seconds, so supporting N versions would multiply
    the test matrix to buy nothing (ADR-0017).
    """
    return schema_version == SCHEMA_VERSION
