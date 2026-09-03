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

**Populated ONLY by rebuild-from-git; it adds no authority beyond git history *as
a matter of who actually calls it*.** :meth:`replace_all` is the one write, and
its sole *shipped* caller is the standalone rebuild service, which feeds it a
:class:`FindingLoad` it got from the git source. There is deliberately no
append-one.

**Exactly one sanctioned serving reader, and it is :meth:`serve_findings`**
(ADR-0029 phase-2 slice-3). The invariant this port used to hold -- "there is
nothing here to *serve* a finding *from*" -- is no longer true and is replaced by
a narrower one that is: a finding reaches a caller through this method or through
nothing. What that buys is a single place where the serving controls live, rather
than a per-surface argument that a later surface inherits by accident:

* it reads the ``findings`` table and **never** ``rejected_trailers``, so no
  serving path can hand out a rejected trailer's ``raw_line`` or ``reason`` --
  both author-controlled untrusted text with no reviewed serving surface (see
  :class:`StoredRejection`). The read cannot be *asked* for one either: no member
  of :class:`FindingQuery` selects a rejection;
* it is bounded by construction in **both** dimensions -- :class:`FindingQuery`
  requires a positive ``limit`` and the method requires a positive ``text_chars``,
  so the signature can express neither an unbounded row count nor an unbounded
  fetch of the one column whose size the corpus, not the caller, decides;
* it refuses a store whose stamp is not current, in the same connection that
  reads the rows.

:meth:`dump` remains the verification read it always was, and is not that
reader: it takes no predicate, returns the whole store including the rejected
rows, and exists so a test can compare the projection against its source.
``tests/unit/test_findings_store_is_unreachable.py`` and
``tests/integration/test_findings_tool_registry.py`` hold the "exactly one"
structurally, over the shipped source and over the built server.

The write end is **not** structurally closed the same way: :meth:`replace_all`
takes a :class:`FindingLoad` built from public domain types
(:class:`~theurian.domain.review_finding.ReviewFinding`,
:class:`~theurian.domain.knowledge.SourceAnchor`), and neither this port nor its
adapter verifies that a given ``commit_sha`` names a commit that actually exists
in this repository's history -- a caller can construct a ``ReviewFinding`` with a
fabricated sha and provider ``"git"`` and land it via ``replace_all`` (measured).
What holds today is narrower and behavioural, not structural: exactly one shipped
caller reaches this method, and it writes only what
:class:`~theurian.domain.ports.review_finding_source.ReviewFindingSource` returned
from a real git read. A future writer that skips the git source is a change this
port's type signature does not prevent.

The write end is bounded by *who calls it* rather than by a type, and that
asymmetry is deliberate: the serving end is where a wrong answer reaches a
caller, so it is the end that carries a structural bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from theurian.domain.errors import DomainError
from theurian.domain.review_finding import FindingLoad, FindingSeverity, ReviewerToken


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
    #:
    #: **Whole from :meth:`ReviewFindingStore.dump`, cut from
    #: :meth:`ReviewFindingStore.serve_findings`.** The dump is a verification read
    #: and hands back what was stored; the serving read takes a ``text_chars``
    #: bound and returns at most that many characters, because a trailer line has
    #: no length limit and a serving surface that fetched the whole column would
    #: have paid for a planted one before it could clamp it. So a value from
    #: ``serve_findings`` is byte-preserved *up to its bound* -- an equality
    #: against the source belongs on a dump, not on a serve.
    finding_text: str
    provider: str
    source_uri: str
    #: The committer date as a stored ISO-8601 string, **normalised to UTC at a
    #: fixed width** (``2026-02-01T00:00:00.000000+00:00``) rather than kept in the
    #: committer's own offset (#405). It round-trips the *instant* of
    #: ``ReviewFinding.date``, not its spelling: ``datetime.fromisoformat`` of this
    #: value equals that field, while the two strings differ whenever the committer
    #: was not on UTC. The encoding is what makes the column a sort key at all --
    #: SQLite compares TEXT byte-wise, and byte order over mixed offsets is not
    #: chronological, so a ``+14:00`` commit earlier in real time sorted after a
    #: ``-11:00`` commit that was later. A reader may therefore ``ORDER BY
    #: committed_at``; a reader that wants the committer's local offset back has to
    #: get it from git, because the store does not keep it.
    committed_at: str
    pull_request: int | None
    family: str | None
    specialist: str | None


@dataclass(frozen=True, slots=True)
class StoredRejection:
    """One rejected trailer as it rests in the store, kept apart from the findings.

    ``raw_line`` is **inert bytes at rest**: author-controlled, untrusted commit
    text (ADR-0029 D3), copied through and never re-parsed or interpreted by the
    store or its builder. A later reader must not "helpfully" parse it. What the
    *source* hands over differs by rejection kind and only one kind is verbatim: a
    grammar rejection's own trailer line, byte-preserved; a date rejection's git
    ``%cI``; and, for a message whose bytes are not valid UTF-8, a **bounded,
    replacement-decoded excerpt** of that message rather than its bytes (#496).

    ``reason`` **may carry author-controlled text, so it is untrusted too** -- and
    the reason to say so is not that every reason is authored, but that a consumer
    holding one cannot tell which kind it has. Measured 2026-09-03 with two keys,
    each stated with the population it ranges over, since neither count means
    anything without one:

    Every key below excludes this file, because a docstring that quotes a key is
    matched by it -- which is exactly how the previous pair came to state 3 and 6
    while returning 4 and 8.

    - ``git grep -nF "RejectedTrailer(" -- packages/theurian-core/src
      ':!*review_finding_store.py'`` -> **3** construction sites, all in the git
      adapter, which is therefore this column's only writer. One passes a parser
      reason straight through; the other two build their own.
    - ``git grep -nF "raise MalformedTrailerError(" -- packages/theurian-core/src
      ':!*review_finding_store.py'`` -> **6** raise sites, all in the parser, every
      one of them reachable through that pass-through. Dropping the ``raise`` from
      the key returns **7** -- the extra is the class's own definition, not a
      seventh site.

    So **8 reason-producing sites**, of which **3 interpolate the offending token
    straight from the line** (``f"unknown reviewer {token!r}"``,
    ``f"unknown severity {token!r}"``, ``f"...got {prefix!r}"``) and carry
    arbitrary-length author-controlled Unicode -- repr-escaped, but not uniformly
    to one token: the first two interpolate a single split token, while the third
    embeds the whole pre-separator prefix, itself potentially several
    space-separated words. None of the three is otherwise bounded or sanitized.
    The other five are product-generated: three constant parser reasons, and the
    adapter's two record-level ones, which interpolate git's own ``%cI`` and a
    ``UnicodeDecodeError``'s render -- a codec name, a position, and the offending
    byte as its **hexadecimal value** (``0x80``: four ASCII characters, and the map
    from byte to text is not injective, since every byte with that value renders
    the same four). The byte itself never reaches the string, so what an author
    controls here is which of 256 renders appears, not any text they wrote.

    A serving-slice implementer must not conclude this field is safe to render or
    index without the same untrusted-content discipline ``raw_line`` already
    carries (SEC-15). The safe reading is the 3, not the 5.
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
    read differently now. That staleness is *detectable* by comparing this value
    (AC-4), and :meth:`ReviewFindingStore.serve_findings` is the consumer that
    acts on it -- refusing rather than serving rows a superseded grammar produced.
    It does not do so by calling :meth:`ReviewFindingStore.is_current`: that would
    be a second open, and a rebuild landing between the two would have the check
    pass on one file and the rows come from another. The comparison it makes is
    this one; where it makes it is inside its own read.

    The store's one writer, ``findings build``, still rebuilds wholesale on every
    run regardless of what this stamp says -- strictly stronger than rebuilding on
    a detected mismatch -- so nothing reads this value to decide whether to
    *write*.
    """

    findings_schema_version: int
    parser_stamp: str


@dataclass(frozen=True, slots=True)
class FindingQuery:
    """What one serving read asks for: filters over stored columns, and a bound.

    Every member is a predicate on a column of the ``findings`` table. **There is
    deliberately no member that selects a rejected trailer**, no member that
    orders the result, and no member that reaches metadata: what a serving read
    may ask for is exactly this, and a surface wanting more asks for a change
    here, in the open, rather than assembling a query of its own.

    ``limit`` has **no default and must be positive**, so the type cannot express
    an unbounded read: a caller that forgets the bound gets a construction error
    rather than a whole-store scan. That is the T-6 shape this type is for --
    "the caller supplies work the daemon must do" is bounded at the type, not by
    each caller remembering to pass a number.

    ``reviewer`` and ``severity`` are the governed vocabularies as *enum members*,
    not strings, so an unknown token cannot reach the store at all: it fails at
    the boundary that converts a caller's text into one of these, and the store
    is never asked to decide what a valid reviewer is (ADR-0029 decision 3).

    ``text_contains`` is a substring test over the untrusted ``finding_text``,
    never a query language: the value is matched literally, with no wildcard, no
    pattern, and no tokenizer. Case folding, and the bound on it, are the
    implementation's to state -- see
    :meth:`ReviewFindingStore.serve_findings`.
    """

    limit: int
    reviewer: ReviewerToken | None = None
    severity: FindingSeverity | None = None
    family: str | None = None
    specialist: str | None = None
    commit_sha: str | None = None
    pull_request: int | None = None
    text_contains: str | None = None

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise DomainError(
                f"FindingQuery.limit must be at least 1, got {self.limit}. A query with no "
                "positive bound is an unbounded read of the store, which no serving surface "
                "may issue."
            )


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

    **One write, two metadata reads, one verification dump, and exactly one
    serving read.** :meth:`serve_findings` is that last one, and the module
    docstring above says what it is bound by; nothing else here returns finding
    content to a caller.
    """

    def replace_all(self, load: FindingLoad) -> None:
        """Rebuild the store to hold exactly ``load``, wholesale and idempotently.

        The one write. Clears whatever the store held and re-lands every accepted
        finding and every rejected trailer in one atomic step, then stamps the file
        with the current schema version and parser stamp -- so two calls over the
        same load leave a logically identical store (AC-2), and a rebuild from git
        is a pure function of the source (AC-1/AC-6).

        **Atomic against a concurrent reader, and that is a promise of this port,
        not an implementation detail of one adapter** (#404). A caller reading the
        store while this runs observes the whole previous content or the whole new
        content -- never a missing store, never a partial one -- and a rebuild that
        fails leaves the previous content intact rather than destroying it. An
        implementation assembles elsewhere and publishes in one indivisible step;
        one that wrote in place would satisfy every other clause here while giving
        the serving slice a window in which the corpus reads as empty.

        Serialising two *writers* is the caller's, though: this method is handed a
        destination, not a project, so a shipped writer takes the project's write
        lock across the call (``application/findings_builder.py``).

        ``load`` is expected to be one a git :class:`ReviewFindingSource` resolved --
        but that is a fact about the one shipped caller, not a guarantee this port
        enforces: neither the port nor its adapter verifies that a given
        ``commit_sha`` names a commit that exists in this repository's history, and
        nothing here checks a commit's signature either, so a ``FindingLoad`` built
        from a fabricated sha lands the same as a real one (see the module
        docstring above for the measured detail).
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

        ``False`` for a missing, stale-schema, or stale-parser store.

        **Deliberately not what the serving read calls.** The staleness *decision*
        belongs to :meth:`serve_findings`, which makes the same comparison inside
        the connection it reads rows through; asking this method first would be a
        second open of the file, and a rebuild landing between them would leave
        the check answering for a store the rows did not come from. This stays as
        the standalone question -- what a diagnostic surface asks about a file it
        is not about to serve from.
        """
        ...

    def dump(self) -> FindingsDump:
        """Every stored row, in ``(commit_sha, position)`` order, for verification.

        Not a serving read: it takes no content predicate and returns the whole
        store, so a test can assert the projection equals its git source. A missing
        store dumps empty.
        """
        ...

    def serve_findings(self, query: FindingQuery, *, text_chars: int) -> tuple[StoredFinding, ...]:
        """The accepted findings ``query`` selects, newest first, at most ``limit``.

        **The one sanctioned serving read** (module docstring). Five properties
        are promises of this port rather than of one adapter, because each of
        them is what a serving surface above would otherwise have to re-argue:

        1. **Accepted findings only.** The ``rejected_trailers`` table is not
           read. A rejected trailer's ``raw_line`` and ``reason`` are
           author-controlled untrusted text with no reviewed serving surface
           (:class:`StoredRejection`), and this method is why a caller cannot
           reach one: not by a filter, not by a limit, not by an empty query.
        2. **Bounded in rows.** At most ``query.limit`` rows, and
           :class:`FindingQuery` refuses a non-positive one, so no call issues an
           unbounded read.
        3. **Bounded in text, and the bound is applied by the read itself.** Each
           row's ``finding_text`` comes back cut to at most ``text_chars``
           characters. The parameter has **no default**, for the reason
           ``FindingQuery.limit`` has none: ``finding_text`` is byte-preserved
           authored commit text, a commit-message line has no length limit, and a
           caller that forgot the bound would materialise whatever a contributor
           planted -- per row, per concurrent call -- before any surface above
           could clamp it. Bounded at the signature, not by each caller
           remembering. An implementation applies it *in the read* rather than
           trimming what it fetched; trimming afterwards satisfies the wording and
           none of the point. It bounds the **projection only**: a
           ``text_contains`` predicate is still matched against the whole stored
           value, so a substring past the bound still selects its row rather than
           reading as absent.
        4. **Current, or nothing.** A store whose recorded stamp is not the
           current (schema version, parser stamp) pair raises rather than
           answering: rows parsed by a superseded grammar are not served as
           though they were current. An implementation checks the stamp **in the
           same connection** it reads the rows through, so a rebuild landing
           mid-call cannot have the check pass on one store and the rows come
           from another.
        5. **A total, deterministic order** -- most recently committed first,
           ties broken by ``(commit_sha, position)``, which is unique. Two calls
           over one store return the same rows in the same order, so ``limit``
           truncates a defined sequence rather than an arbitrary one.

        A **missing** store is a raise too, not an empty tuple: "nothing has been
        built here" and "the build found nothing" are different answers, and a
        caller that cannot tell them apart reports one as the other.

        Raises:
            DomainError: If ``text_chars`` is not positive -- the same shape
                :class:`FindingQuery` refuses a non-positive ``limit`` with, and
                for the same reason: a bound that is not a bound is a wrong
                answer rather than a smaller one.
            TheurianError: If the store is missing, stale, or unreadable. The
                implementation's error carries the rebuild remedy; the store is a
                projection of git history (ADR-0004), so rebuilding it is the cure
                for every one of those.
        """
        ...
