"""ReviewFindingStore port: the canonical landing of parsed findings (ADR-0029).

The store where the findings a
:class:`~theurian.domain.ports.review_finding_source.ReviewFindingSource`
resolves come to rest. ADR-0029's layer table places the *parsed finding record*
in the **Canonical** layer -- "the record of truth, normalized, carrying a
``SourceAnchor`` (FR-S3)" -- so this is a Canonical-layer artifact, not an
index/derived one. What makes deleting its file a cache miss rather than data loss
is a different property: the source of truth is **git history** (ADR-0029 D7's
verified authority), and the file is a wholesale *projection* of it, reconstructed
by replaying the source -- exactly as the canonical state database is a projection
of its Git-tracked YAML migrations (``infrastructure/sqlite/schema.py``). That is
why AC-6 holds: a deleted store rebuilds identically from git.

**Populated ONLY by rebuild-from-git in this slice; it adds no authority beyond
git history.** :meth:`replace_all` is the one write, and its sole caller is the
standalone rebuild service, which feeds it a :class:`FindingLoad` it got from the
git source. There is deliberately no append-one, no arbitrary-write, and no
serving read: a write path that admits findings *not* sourced from git, and any
query-by-content or retrieval-shaped read, are future lanes (ADR-0029's
serving/deriving arm). The two ends of that boundary are held structurally --
there is nothing here to write an off-git finding *with*, and nothing here to
*serve* one *from*.

The one read this port exposes, :meth:`dump`, is a whole-table verification dump
in a fixed total order: not a query (no content predicate, no ranking, no
filtering, no limit), so it is not a serving surface. It is here so a test can
assert the projection equals its source; a serving read is a separate capability
this port does not carry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from theurian.domain.review_finding import FindingLoad


@dataclass(frozen=True, slots=True)
class StoredFinding:
    """One accepted finding as it rests in the store, in verification form.

    Mirrors :class:`~theurian.domain.review_finding.ReviewFinding`'s fields flat,
    with the derived ``pull_request``/``family``/``specialist`` carried as the
    ``None`` they are in this slice. ``position`` is the store's own key, assigned
    per commit in the source's total order (a commit can carry several findings);
    it is *not* a field the source hands out, so an equality against the source is
    taken over the other fields (AC-1/AC-6).
    """

    commit_sha: str
    position: int
    reviewer: str
    severity: str
    #: Untrusted authored content, byte-preserved from the trailer (ADR-0029 D3).
    finding_text: str
    provider: str
    source_uri: str
    #: The committer date as a stored ISO-8601 string (round-trips
    #: ``ReviewFinding.date.isoformat()``).
    committed_at: str
    pull_request: int | None
    family: str | None
    specialist: str | None


@dataclass(frozen=True, slots=True)
class StoredRejection:
    """One rejected trailer as it rests in the store, kept apart from the findings.

    ``raw_line`` is **inert bytes at rest**: author-controlled, untrusted commit
    text (ADR-0029 D3), byte-preserved verbatim and never re-parsed or interpreted
    by the store or its builder. A later reader must not "helpfully" parse it.
    """

    commit_sha: str
    position: int
    raw_line: str
    reason: str


@dataclass(frozen=True, slots=True)
class FindingsStamp:
    """The recorded identity of the parser and schema that produced a store.

    A store whose stamp no longer equals the current build's is stale: a schema
    change or a parser-grammar change (ADR-0029 decision 2) means the file would be
    read differently now, so it is rebuilt wholesale rather than trusted (AC-4).
    """

    findings_schema_version: int
    parser_stamp: str


@dataclass(frozen=True, slots=True)
class FindingsDump:
    """The whole store, read back in a fixed total order, for verification only.

    Both tuples are ordered by ``(commit_sha, position)`` so two dumps of the same
    logical content compare equal by value -- the comparison AC-2/AC-6 make,
    against a SQLite *file* whose raw bytes legitimately drift under identical
    logical content (refinement A: logical identity, never a file hash).
    """

    findings: tuple[StoredFinding, ...]
    rejected: tuple[StoredRejection, ...]


@runtime_checkable
class ReviewFindingStore(Protocol):
    """Lands the findings a source resolved into a wholesale-rebuilt store (ADR-0029).

    **No serving read, by construction.** The methods are a wholesale write, two
    metadata reads, and a verification dump -- never a query-by-content or a
    retrieval-shaped read. The disclosure round for served finding content is a
    later slice; this port keeps that surface structurally absent.
    """

    def replace_all(self, load: FindingLoad) -> None:
        """Rebuild the store to hold exactly ``load``, wholesale and idempotently.

        The one write. Clears whatever the store held and re-lands every accepted
        finding and every rejected trailer in one atomic step, then stamps the file
        with the current schema version and parser stamp -- so two calls over the
        same load leave a logically identical store (AC-2), and a rebuild from git
        is a pure function of the source (AC-1/AC-6).

        ``load`` must be one a git :class:`ReviewFindingSource` resolved: this port
        adds no authority beyond git history, so there is no path here for a finding
        that did not come from a signed commit.
        """
        ...

    def stamp(self) -> FindingsStamp | None:
        """The recorded (schema version, parser stamp), or ``None`` if unreadable.

        ``None`` covers a store that does not exist, carries no metadata row, or
        cannot be read -- all of which mean the same thing operationally: there is
        no trustworthy stamp, so a rebuild is owed.
        """
        ...

    def is_current(self) -> bool:
        """Whether this store's stamp matches the build that would rebuild it now.

        ``False`` for a missing, stale-schema, or stale-parser store -- the signal
        a caller acts on to rebuild wholesale (AC-4).
        """
        ...

    def dump(self) -> FindingsDump:
        """Every stored row, in ``(commit_sha, position)`` order, for verification.

        Not a serving read: it takes no content predicate and returns the whole
        store, so a test can assert the projection equals its git source. A missing
        store dumps empty.
        """
        ...
