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
recreate (ADR-0004), and the consumer that acts on the signal now ships:
:meth:`~theurian.domain.ports.review_finding_store.ReviewFindingStore.serve_findings`
refuses a store whose stamp is not this build's rather than serving rows a
superseded grammar produced (slice-3). It makes that comparison inside its own
read rather than by calling ``is_current`` -- see that method for why two opens
would be worse than one. The writer is unchanged: ``findings build`` rebuilds
wholesale on every run regardless of staleness, which is strictly stronger than
rebuilding on a detected mismatch.

**No FTS, no triggers, no ranking.** Three plain tables and nothing that scores.
The serving read slice-3 landed is a filtered, ordered ``SELECT`` over the
``findings`` table -- an exact-match query, not retrieval -- so this schema still
carries none of the machinery ``index_schema.py`` does, and a *ranked* findings
surface (with the T-17a collection-statistics problem that comes with it) remains
a later slice with its own round.

What stands between a caller and a finding is therefore no longer an absence, and
this file is not where it lives: it is the port's one sanctioned serving read
(accepted rows only, bounded, stale-refusing) and the structural pins in
``tests/unit/test_findings_store_is_unreachable.py`` and
``tests/integration/test_findings_tool_registry.py`` (AC-7), which now assert that
exactly one serving path reaches this schema rather than that none does. That
guarantee is scoped to AC-7's own declared population (``SERVING_MODULES``:
``mcp/``, ``daemon/`` and ``review/`` walked wholesale, plus a hand-picked list
covering ``application/``, ``cli/``, the index read-side, and the canonical-store
adapter), not a scan of the whole package.
"""

from __future__ import annotations

from typing import Final

#: Bump for ANY change to how this file is written -- the DDL below, and equally
#: the *encoding* of a column's value, because a reader that mis-decodes a column
#: is as wrong as one that misses a table. Independent of ``SCHEMA_VERSION`` and
#: ``INDEX_SCHEMA_VERSION``: the three artifacts version separately because they
#: are rebuilt separately.
#:
#: - **1** -- the first landing (ADR-0029 phase-2 slice-2).
#: - **2** -- ``committed_at`` became a UTC-normalised, fixed-width instant rather
#:   than the committer's own offset-preserving ISO-8601 (#405,
#:   :func:`~theurian.infrastructure.sqlite.findings_store.committed_at_text`).
#:   The DDL text did not change; the meaning of the bytes in that column did,
#:   which is exactly the case the widened rule above exists to catch. No
#:   migration: the store is a wholesale projection of git history (ADR-0004) and
#:   ``findings build`` rebuilds it unconditionally, so a version-1 file is
#:   replaced rather than upgraded -- and no reader exists to be handed one in the
#:   meantime (``theurian findings build`` is the only shipped consumer).
FINDINGS_SCHEMA_VERSION: Final = 2

FINDINGS_DDL: Final = """
-- Store identity -------------------------------------------------------------
-- One row. Carries the schema version AND the parser-grammar stamp the rows were
-- produced under, so a file parsed by a superseded grammar is *detectable* via
-- that stamp (ADR-0029 AC-4) -- see the module docstring above for what reads it
-- today and what does not. `built_at` records when.
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
-- `committed_at` is a UTC-normalised, fixed-width ISO-8601 instant, NOT the
-- committer's own offset-preserving `%cI` (#405, `findings_store.committed_at_text`).
-- SQLite compares TEXT byte-wise, so an offset-preserving value is not a sort key:
-- a `+14:00` commit earlier in real time sorted after a `-11:00` commit that was
-- later. Normalising makes byte order and instant order the same relation, which
-- is what lets a reader `ORDER BY committed_at` at all. The same bug class PR #112
-- recorded for the canonical store (`schema.py`).
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
-- interpolating the offending token straight from the line, at three sites --
-- two repr-escape a single split token, one repr-escapes the whole pre-separator
-- prefix (itself potentially several words) -- so it carries arbitrary-length
-- author-controlled Unicode. A later reader must give it the same
-- untrusted-content handling `raw_line` gets (SEC-15), not treat it as safe
-- because it reads like a diagnostic message.
CREATE TABLE rejected_trailers (
    commit_sha  TEXT    NOT NULL,
    position    INTEGER NOT NULL,
    raw_line    TEXT    NOT NULL,
    reason      TEXT    NOT NULL,
    PRIMARY KEY (commit_sha, position)
);
"""
