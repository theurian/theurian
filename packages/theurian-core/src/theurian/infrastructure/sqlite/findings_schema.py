"""SQLite schema for the canonical review-finding store (ADR-0029, ADR-0004).

**A separate database file, versioned independently.** The canonical state store
(``schema.py``, ``SCHEMA_VERSION``) and the retrieval index (``index_schema.py``,
``INDEX_SCHEMA_VERSION``) are already two files with their own version constants,
DDLs and rebuild paths -- "they version separately because they are rebuilt
separately" (``index_schema.py``). Findings are a third such artifact: this file
owns :data:`FINDINGS_SCHEMA_VERSION`, and a change to the DDL below bumps only it,
touching neither of the other two. That is the precedent this split rests on --
*not* any prohibition in ADR-0029's own docs-only PR, which constrained that PR,
not this implementation.

**What layer this is.** ADR-0029's layer table places the parsed finding record in
the **Canonical** layer -- the normalized record of truth, carrying a
``SourceAnchor`` (FR-S3). So this is a Canonical-layer store, not an index/derived
one. What makes discarding its file a cache miss rather than data loss is a
different property, the one ADR-0004 gives every projection: the source of truth is
**git history** (ADR-0029 D7's verified authority), and this file is reconstructed
wholesale by replaying the git source -- exactly as the canonical state database is
reconstructible by replaying its Git-tracked YAML migrations (``schema.py``). A
deleted store therefore rebuilds identically (AC-6).

**The version is a forcing function, like the index's -- for the consumer that
checks it.** A store written under an earlier :data:`FINDINGS_SCHEMA_VERSION`, or
by an earlier trailer grammar (its recorded ``parser_stamp`` no longer equal to
:data:`~theurian.domain.review_finding.PARSER_STAMP`), is *detectable* as stale
via :meth:`~theurian.domain.ports.review_finding_store.ReviewFindingStore.is_current`.
There is no in-place migration for a file that costs one ``git log`` to
recreate (ADR-0004) -- but this phase-2 slice ships no consumer that reads that
signal: its one writer, ``findings build``, rebuilds wholesale on every run
regardless of staleness, so nothing here is served stale today because nothing
here is served at all. The detection is real; the rebuild-on-detection path is
owed to the serving slice that arrives later.

**No FTS, no triggers, no serving apparatus.** Three plain tables and nothing that
scores or ranks. A findings *search* is a later slice with its own disclosure
round; this schema deliberately carries none of the retrieval machinery
``index_schema.py`` does. That absence of machinery is not, by itself, what stands
between a caller and a finding -- the schema cannot refuse a query issued against
it. The guarantee that nothing *reaches* this schema at all is enforced by
``tests/unit/test_findings_store_is_unreachable.py`` (AC-7), not by this file.
"""

from __future__ import annotations

from typing import Final

#: Bump for ANY change to the DDL below. Independent of ``SCHEMA_VERSION`` and
#: ``INDEX_SCHEMA_VERSION``: the three artifacts version separately because they
#: are rebuilt separately. **1** is the first landing (ADR-0029 phase-2 slice-2).
FINDINGS_SCHEMA_VERSION: Final = 1

FINDINGS_DDL: Final = """
-- Store identity -------------------------------------------------------------
-- One row. Carries the schema version AND the parser-grammar stamp the rows were
-- produced under, so a file parsed by a superseded grammar is detected and
-- rebuilt wholesale (ADR-0029 AC-4) rather than trusted. `built_at` records when.
CREATE TABLE findings_metadata (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    findings_schema_version INTEGER NOT NULL,
    parser_stamp            TEXT    NOT NULL,
    built_at                TEXT    NOT NULL
);

-- Accepted findings ----------------------------------------------------------
-- One row per accepted trailer. `commit_sha` is git's own %H (never forgeable);
-- `position` is the store's key within a commit, assigned in the source's total
-- order so several findings on one commit stay distinct and ordered. Together
-- they are the idempotency key: a rebuild over unchanged history re-lands the
-- same (commit_sha, position) rows (AC-2).
--
-- `finding_text` is untrusted authored content (ADR-0029 D3), byte-preserved from
-- the trailer. It is stored opaque and never parsed for structure here.
--
-- `pull_request`, `family` and `specialist` are the three derived fields the
-- trailer does not carry (ADR-0029 decision 1): NULL in this slice, columns now so
-- the derivation lands as an UPDATE-shaped change rather than a schema break.
CREATE TABLE findings (
    commit_sha    TEXT    NOT NULL,
    position      INTEGER NOT NULL,
    reviewer      TEXT    NOT NULL,
    severity      TEXT    NOT NULL,
    finding_text  TEXT    NOT NULL,
    provider      TEXT    NOT NULL,
    source_uri    TEXT    NOT NULL,
    committed_at  TEXT    NOT NULL,
    pull_request  INTEGER,
    family        TEXT,
    specialist    TEXT,
    PRIMARY KEY (commit_sha, position)
);

-- Rejected trailers ----------------------------------------------------------
-- A DISTINCT table, never mixed into `findings`: a malformed keyed line or an
-- unrepresentable committer date is captured here so the corpus stays loss-free
-- without one bad line becoming a fake finding (ADR-0029 D3, AC-5). `commit_sha`
-- is git's %H; `position` keys multiple rejections on one commit.
--
-- `raw_line` is INERT bytes at rest: author-controlled, untrusted commit text,
-- byte-preserved verbatim and NEVER re-parsed or interpreted -- not by the store,
-- not by the builder, not by a later reader. A finding is never derived from it.
--
-- `reason` is untrusted too, not product-generated: the parser builds it by
-- interpolating the offending token straight from the line (repr-escaped, one
-- token), so it carries arbitrary-length author-controlled Unicode. A later
-- reader must give it the same untrusted-content handling `raw_line` gets
-- (SEC-15), not treat it as safe because it reads like a diagnostic message.
CREATE TABLE rejected_trailers (
    commit_sha  TEXT    NOT NULL,
    position    INTEGER NOT NULL,
    raw_line    TEXT    NOT NULL,
    reason      TEXT    NOT NULL,
    PRIMARY KEY (commit_sha, position)
);
"""
