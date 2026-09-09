"""SQLite schema for the review search store (ADR-0030 decisions 3 and 6, ADR-0004).

**The fourth database file under ``.theurian/state/``, versioned independently.**
The canonical state store (``schema.py``, ``SCHEMA_VERSION``), the retrieval index
(``index_schema.py``, ``INDEX_SCHEMA_VERSION``) and the review-finding store
(``findings_schema.py``, ``FINDINGS_SCHEMA_VERSION``) already version separately,
and ``findings_schema.py`` records the reason this file follows: "they version
separately because they are rebuilt separately". A change to the DDL below bumps
:data:`REVIEW_SEARCH_SCHEMA_VERSION` and touches none of the other three.

**What layer this is, and it is not the layer the evidence files are.** ADR-0030
decision 3 puts review evidence in **Canonical** with no replayable source --
upstream comments are editable and deletable, so deleting an evidence file is
data loss. This store is the row below that in the same table: **Index /
derived**, rebuilt wholesale from those files by ``theurian review build``, and
deleting it costs a rebuild rather than a record. That is the one artifact in the
review family ADR-0004's ordinary promise applies to unchanged.

**No FTS5, no BM25, no ranking, and the absence is a control rather than a
simplification.** ADR-0030 decision 6 names the inherited constraint: *if review
evidence is ever served through a ranked surface, the withheld-row exclusion is a
physical purge (T-17a), not a result-set filter*, because a ``fusedScore`` is
priced over collection statistics computed at index-build time. This schema
carries no external-content table, no tokenizer and no statistic over rows, so
there is no quantity for a row to price and nothing a filter would have to clean.
The v1 read is a filtered, ordered ``SELECT`` with a ``LIKE`` -- an exact
substring test, not retrieval -- and a ranked surface is a later slice that owes
that purge and its own round.

**Withholding is physical here too, and it is the builder's rather than this
file's.** A record whose key a build withholds is never handed to the writer, so
it has no row in **any** of the three content tables below -- not a flag, not a
soft-delete, not a filter a read has to remember. That is the same
by-construction argument ``index_builder`` makes for an above-ceiling item, and
what makes it checkable here is that the tables have no column that could express
"present but hidden".

**Three content tables rather than one, because two of the filters are
set-valued.** A thread has many participants and many comments; folding either
into a column on the record row would make the author filter mean "whoever opened
it" and the text filter mean "the first comment", which are different questions
from the ones ADR-0030 decision 6 describes.
"""

from __future__ import annotations

from typing import Final

#: Bump for ANY change to how this file is written -- the DDL below, and equally
#: the *encoding* of a column's value, because a reader that mis-decodes a column
#: is as wrong as one that misses a table. Independent of ``SCHEMA_VERSION``,
#: ``INDEX_SCHEMA_VERSION`` and ``FINDINGS_SCHEMA_VERSION``: the four artifacts
#: version separately because they are rebuilt separately.
#:
#: - **1** -- the first landing (ADR-0030 slice 3).
REVIEW_SEARCH_SCHEMA_VERSION: Final = 1

REVIEW_SEARCH_DDL: Final = """
-- Store identity -------------------------------------------------------------
-- One row. Carries this schema's version AND the evidence format version the
-- rows were projected from, so a store built by a superseded schema or from a
-- superseded record format is *detectable* rather than served as current. A
-- rebuild is always the cure: the store is derived (ADR-0004) and the evidence
-- files it is derived from are still on disk. `built_at` records when.
CREATE TABLE review_search_metadata (
    id                           INTEGER PRIMARY KEY CHECK (id = 1),
    review_search_schema_version INTEGER NOT NULL,
    evidence_format_version      INTEGER NOT NULL,
    built_at                     TEXT    NOT NULL
);

-- The records ----------------------------------------------------------------
-- One row per evidence file this build was given. `relative_path` is that file's
-- own path under `.theurian/review/` and is the primary key: the reader that
-- produced it refuses two records under one path, and the writer refuses a load
-- that carries a duplicate, so the key is unique before SQLite is asked.
--
-- Trust classes, per ADR-0030 decision 3's field table. `provider`,
-- `repository`, `record_key`, `kind`, `pull_request`, `thread_state` and
-- `source_uri` are provider structure or Theurian's own writes.
-- `author_display_name` and `file_path` are AUTHOR-CONTROLLED: a display name is
-- chosen by its owner and a file path in a pull request is named by whoever
-- authored it. `file_path` is stored so a reader can filter and display it and
-- SHALL NOT be joined into a filesystem path (SEC-7).
--
-- `author_external_id` is provider structure in the common case and not always:
-- ADR-0030's 2026-09-08 correction records that GitHub answers with the author's
-- own *login* where no node id exists, and a login is chosen by its owner. A
-- consumer must not read this column as unforgeable identity.
--
-- `last_seen_at` is a UTC-normalised, fixed-width ISO-8601 instant and NOT the
-- offset-preserving spelling the evidence file carried. SQLite compares TEXT
-- byte-wise, so an offset-preserving value is not a sort key -- the #405
-- inversion `findings_store.committed_at_text` records in full.
CREATE TABLE review_records (
    relative_path       TEXT    NOT NULL PRIMARY KEY,
    record_key          TEXT    NOT NULL,
    kind                TEXT    NOT NULL,
    provider            TEXT    NOT NULL,
    repository          TEXT    NOT NULL,
    pull_request        INTEGER,
    thread_state        TEXT,
    file_path           TEXT,
    source_uri          TEXT    NOT NULL,
    author_external_id  TEXT    NOT NULL,
    author_display_name TEXT    NOT NULL,
    last_seen_run_id    TEXT    NOT NULL,
    last_seen_at        TEXT    NOT NULL
);

-- Participants ---------------------------------------------------------------
-- Every participant of a record, so the author filter finds a reply by whoever
-- wrote it and not only the person who opened the thread. De-duplicated by the
-- builder in first-appearance order, which is what makes `position` a stable key
-- across rebuilds rather than a count of comments.
--
-- `ON DELETE CASCADE` is a statement of what these rows are -- they exist only
-- as part of their record -- rather than a mechanism this build relies on: the
-- one writer replaces the whole file and deletes nothing.
CREATE TABLE review_participants (
    relative_path TEXT    NOT NULL,
    position      INTEGER NOT NULL,
    external_id   TEXT    NOT NULL,
    PRIMARY KEY (relative_path, external_id),
    FOREIGN KEY (relative_path) REFERENCES review_records (relative_path) ON DELETE CASCADE
);

-- The searchable text --------------------------------------------------------
-- One row per fragment of author-written text, in the record's own reading
-- order. `content` is UNTRUSTED CONTENT (T-3, SEC-15) held opaque: it is stored
-- byte-preserved, never parsed, never tokenized, and never read to decide
-- anything. `channel` says which part of the record it came from, so a match can
-- be reported as "in a comment" rather than only "in this thread".
--
-- No FTS5 mirror of this table exists, deliberately (see the module docstring).
CREATE TABLE review_texts (
    relative_path TEXT    NOT NULL,
    position      INTEGER NOT NULL,
    channel       TEXT    NOT NULL,
    content       TEXT    NOT NULL,
    PRIMARY KEY (relative_path, position),
    FOREIGN KEY (relative_path) REFERENCES review_records (relative_path) ON DELETE CASCADE
);
"""

#: Every table :data:`REVIEW_SEARCH_DDL` creates, in creation order.
#:
#: Spelled here rather than derived from ``sqlite_master`` at use time so a test
#: asserting "no row derived from a withheld record exists in ANY table" ranges
#: over a population this module states, and a fourth table added without joining
#: that population fails the test that compares the two.
REVIEW_SEARCH_TABLES: Final = (
    "review_search_metadata",
    "review_records",
    "review_participants",
    "review_texts",
)
