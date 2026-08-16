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
#:
#: **3** adds the `project_integrity` table below and makes the item -> revision
#: pointer a composite foreign key (#24). Neither is reachable by reinterpreting
#: an older file: neither a version-1 nor a version-2 database holds an expected
#: count to compare against, and the detector that reads one treats a missing
#: record as damage (#30 PR2). That is only sound because :func:`is_supported` is
#: exact match -- it compares ``schema_version == SCHEMA_VERSION``, so *every*
#: other version is refused outright and every database this build opens was
#: written by a build that records. A compatibility window here would turn "no
#: record" into an ambiguity between "damaged" and "old", which is the
#: distinction the whole signal exists to make.
SCHEMA_VERSION: Final = 3

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
-- `revision_id` alone is the primary key, so it is unique across every project
-- sharing this database -- while every read of the table is scoped by
-- `project_id` as well, and `knowledge_items` below is keyed
-- `(project_id, item_id)`. The two used not to meet: a project id changing over
-- an unchanged root left revisions stranded under the old id, behind a foreign
-- key on `revision_id` alone that `PRAGMA foreign_key_check` reported as
-- satisfied (https://github.com/theurian/theurian/issues/24).
--
-- The unique index below is what lets `knowledge_items.current_revision_id`
-- reference `(project_id, revision_id)` as a composite parent key instead, which
-- closes it. Measured on SQLite 3.51.2 against the stranding UPDATE -- a
-- revision's `project_id` moved while an item still points at it:
--
--   * before: the writer's own connection (`foreign_keys = ON`) accepted it and
--     `PRAGMA foreign_key_check` returned `[]`, while the item's project-scoped
--     revision read returned nothing;
--   * after: the same UPDATE is refused, and forced through with foreign keys
--     off it is reported as `('knowledge_items', <rowid>, 'knowledge_revisions',
--     0)`.
--
-- `revision_id` stays the primary key: it is what `source_anchors` references
-- and what `idx_revisions_migration` serves, and the index this adds costs one
-- b-tree over columns two reads already filter on together.
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

-- The parent key of the composite pointer foreign key below. SQLite requires the
-- parent columns to carry a UNIQUE index of their own; `revision_id` being the
-- primary key does not supply one over the pair.
CREATE UNIQUE INDEX idx_revisions_project_revision
    ON knowledge_revisions(project_id, revision_id);

-- The mutable pointer. `current_revision_id` is the one thing that moves.
--
-- The foreign key is composite so that the pointer is scoped the same way every
-- read of it is (#24, and the comment above knowledge_revisions). A NULL
-- `current_revision_id` still satisfies it -- an item exists before its first
-- revision is upserted -- because a composite child key with a NULL component
-- imposes no constraint in SQLite.
CREATE TABLE knowledge_items (
    item_id             TEXT NOT NULL,
    project_id          TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    namespace           TEXT NOT NULL,
    kind                TEXT NOT NULL,
    status              TEXT NOT NULL,
    current_revision_id TEXT,
    owner               TEXT NOT NULL,
    trust_level         TEXT NOT NULL,
    sensitivity         TEXT NOT NULL,
    tenant_id           TEXT NOT NULL DEFAULT 'local',
    acl_group           TEXT NOT NULL DEFAULT 'default',
    valid_from          TEXT NOT NULL,
    valid_to            TEXT,
    PRIMARY KEY (project_id, item_id),
    FOREIGN KEY (project_id, current_revision_id)
        REFERENCES knowledge_revisions (project_id, revision_id)
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

-- Integrity -----------------------------------------------------------------
-- What the writer expects a reader to be able to see, recorded by the writer
-- that produced it (#30 PR2). One row per project, written inside `migrate
-- apply`'s transaction from the rows that transaction just wrote, so nothing on
-- a query path ever computes it.
--
-- `expected_surfaceable_count` counts `knowledge_items` whose status is
-- surfaceable, which is the population `knowledge.status` already publishes a
-- breakdown of. It is deliberately not a count of everything the table holds: a
-- total would say how many rows a caller may *not* read (SEC-13, T-17), and this
-- record is read back on every request by three tools.
--
-- A count and not a checksum, and the reason is scope and cost, not soundness.
-- Writer-exclusivity would make a checksum *sound*, not unsound: this record is
-- written only inside `migrate apply`'s transaction, so any later change to these
-- rows is damage or a legitimate re-record, never innocent drift. Two other
-- things rule a checksum out. Scope: the count exists to catch a row leaving the
-- surfaceable population -- a corrupt `project_id`, or a `status` that leaves
-- `SURFACEABLE_STATUSES` -- which is exactly what changes it. Detecting a change
-- to a content column a caller reads directly (`title`, `body`) is a different
-- detector, out of this record's scope. Cost: the count is one covering-index
-- seek per request (`idx_items_status`); a checksum instead reads and serializes
-- every surfaceable row on each request, and its writer and every reader must
-- agree on serialization order and type affinity byte-for-byte -- a drift between
-- them reports damage on a row nothing touched.
--
-- The residual is a class, not an oversight: a *type-valid, in-scope* corruption
-- moves neither count and is invisible here. Its measured members -- a well-formed
-- foreign `knowledge_items.item_id` (the row keeps its `project_id` and `status`,
-- so it is still counted while its pointer chain is broken), and a `status` moved
-- *within* `SURFACEABLE_STATUSES` (approved <-> draft), which the population counts
-- either way. A count is not a checksum, and this is the shape a count cannot see.
-- The one member that used to belong and no longer does is the `current_revision_id`
-- pointer face, equally type-valid and count-neutral: it left this class through the
-- read-back guard that refuses a revision not belonging to its item (61747b3,
-- INV-2), never through this record.
CREATE TABLE project_integrity (
    project_id                 TEXT PRIMARY KEY REFERENCES projects(project_id) ON DELETE CASCADE,
    expected_surfaceable_count INTEGER NOT NULL
);
"""


def is_supported(schema_version: int) -> bool:
    """Whether this build can read a database recorded at ``schema_version``.

    Exact match only. There is deliberately no compatibility window: a state
    database is rebuildable in seconds, so supporting N versions would multiply
    the test matrix to buy nothing (ADR-0017).
    """
    return schema_version == SCHEMA_VERSION
