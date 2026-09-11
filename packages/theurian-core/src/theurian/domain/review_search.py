"""What a review search store holds, and the load that replaces it (ADR-0030).

The **derived** side of ADR-0030 decision 3. The evidence files under
``.theurian/review/`` are the source -- a Canonical record whose own upstream is
not re-readable -- and everything here describes a *projection* of them that a
later build can throw away and reproduce. Nothing in this module is authored by
Theurian or by a provider: every value arrives from a landed evidence file, and
the projection that fills these types is
:class:`~theurian.application.review_search_builder.ReviewSearchBuilder`.

**Two of the fields carry author-controlled text and the type says which**
(ADR-0030 decision 3's field table, decision 6's trust table).
:class:`ReviewTextFragment` is untrusted content whichever channel it came from,
and so are :attr:`ReviewSearchRecord.file_path` and
:attr:`ReviewSearchRecord.author_display_name` -- a file path in a pull request
is named by whoever authored the pull request, and a display name is chosen by
its owner. A serving surface publishes all three under
``contentClassification: untrusted-knowledge``, and **no reader may join
``file_path`` into a filesystem path** (SEC-7): the containment rule decision 3
states for the *write* side holds unchanged on the read side.

**No ranking lives here, and that is structural rather than an omission.** A
record carries text fragments and a store matches them literally; there is no
score, no term weight and no collection statistic, so the T-17a family -- a
withheld row going on pricing the rows that are returned -- has nothing to act
through. What keeps a withheld record out is that it is never written at all
(:class:`~theurian.application.review_search_builder.ReviewSearchBuildRequest`),
which is the same by-construction argument ``index_builder`` makes for an
above-ceiling item.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Final

from theurian.domain.errors import DomainError, InvariantViolationError

#: The largest ``pull_request`` a record may carry, which is the largest value the
#: column behind it can hold: SQLite's INTEGER is a signed 64-bit value, and
#: ``sqlite3`` raises ``OverflowError`` binding anything wider. No layer between
#: here and the driver converts that, so without this bound a hand-edited
#: ``eventKey`` ending ``#99999999999999999999`` reached an operator as *the store
#: could not be written*, with a cure about disk space -- measured 2026-09-10:
#: ``writing theurian-review-local.sqlite: Python int too large to convert to
#: SQLite INTEGER``.
#:
#: A **twin** of ``mcp/review_search.MAX_PULL_REQUEST`` rather than one import of
#: the other, for the reason that constant already records about its own twin: the
#: two bound different populations -- a caller's *filter* there, a stored *record*
#: here -- and what they share is a property of SQLite, not of either surface.
MAX_STORED_PULL_REQUEST: Final = 2**63 - 1


class ReviewTextChannel(StrEnum):
    """Which part of a record one searchable fragment came from.

    A ``StrEnum`` so the member and the stored column value are one thing, the
    way :class:`~theurian.infrastructure.review_evidence.layout.EvidenceKind` is
    for a directory name. It is carried back out beside an excerpt so a serving
    surface can say *where* a match was, which is the difference between "this
    thread matched" and "a comment in this thread matched".
    """

    #: A pull request's title.
    TITLE = "title"
    #: A pull request's description, or a review submission's body.
    BODY = "body"
    #: One comment inside a review thread.
    COMMENT = "comment"


@dataclass(frozen=True, slots=True)
class ReviewTextFragment:
    """One searchable piece of author-written text, and where it came from.

    ``content`` is **untrusted content** (T-3, SEC-15) held opaque: nothing in
    this codebase parses it, tokenizes it or reads it to decide anything. It is
    stored so a substring match can find the record that carries it, and it is
    served under the safety triple ADR-0030 decision 6 names.
    """

    channel: ReviewTextChannel
    content: str


@dataclass(frozen=True, slots=True)
class ReviewSearchRecord:
    """One landed evidence record, projected onto what a search needs.

    **Deliberately not a replica of the evidence file.** The file is the source
    and stays the only complete copy (ADR-0030 decision 3); this is the subset a
    structural filter and a substring match range over, plus what a result has to
    name for a reader to go and find the record. A field the evidence file holds
    and this record does not -- a pull request's labels, its milestone, its head
    branch -- is not lost, it is simply not indexed, and adding one is a schema
    change with its own version bump rather than a silent widening.

    ``relative_path`` is the record's own path under ``.theurian/review/``, which
    :meth:`~theurian.infrastructure.review_evidence.reader.EvidenceReader.read_all`
    already guarantees unique and totally ordered. It is the store's primary key
    for that reason, and it is what makes a rebuild reproduce: the same files
    produce the same keys in the same order on every machine.

    **Every field below is what the evidence file says, and the attributions name
    the route rather than the record.** ``theurian review ingest`` fills these
    from a provider's answer, but ``.theurian/review/`` is source rather than
    derived state and is not git-ignored, so a clone can carry files a repository
    author wrote and the build projects them like any other (threat-model T-24).
    A sentence here that says "the provider's own" or "written by Theurian" is
    therefore about the ingest route; on a clone-delivered record the value is
    whatever the file names, held only to the shape the reader checks and to the
    derived path it must sit at.
    """

    #: Where the evidence file sits, relative to ``.theurian/review/``, POSIX-spelled.
    relative_path: str
    #: The identifier the record carries for itself inside its repository -- a
    #: pull-request number, or a node id; the provider's own on the ingest route.
    #: This is the key a withheld set is matched against -- see
    #: ``ReviewSearchBuildRequest.withheld_record_keys`` in
    #: :mod:`theurian.application.review_search_builder`.
    record_key: str
    #: ``pull-request``, ``review-submission`` or ``review-thread``, carried as the
    #: evidence layout's own directory name rather than re-derived here.
    kind: str
    #: The provider that answered, ``github`` today.
    provider: str
    #: The repository the record names, ``owner/name`` -- as the provider
    #: resolved it on the ingest route. Provider structure there, and **never**
    #: joined into a filesystem path on either (ADR-0030 decision 3).
    repository: str
    #: The pull request this record belongs to, where one can be read from the
    #: record. ``None`` rather than a guess when it cannot. Positive and no wider
    #: than :data:`MAX_STORED_PULL_REQUEST`, refused at construction either way.
    pull_request: int | None
    #: A thread's resolution state, ``None`` for the two kinds that have none.
    thread_state: str | None
    #: The file a thread is anchored to, as received. **Author-controlled**
    #: (ADR-0030 decision 6): served as data, never used to build a path.
    file_path: str | None
    #: FR-S3's pointer back to the upstream object, written by ``theurian review
    #: ingest`` rather than received on the route that fetched the record.
    source_uri: str
    #: The record's principal author: the pull request's, the submission's, or the
    #: opening comment's. Provider structure *usually* -- ADR-0030's 2026-09-08
    #: correction records that GitHub can answer with a login here instead, which
    #: is author-chosen, so a consumer must not treat it as unforgeable identity.
    author_external_id: str
    #: The same participant's display name. **Author-controlled**, and already
    #: pseudonymised when ``theurian review ingest`` landed it and the project
    #: asked for that (R-12) -- an ingest-route control, not a serve-time one.
    author_display_name: str
    #: Every participant's ``external_id``, first appearance first, de-duplicated.
    #: What the author filter ranges over, so a reply in a thread is found by the
    #: person who wrote it and not only by whoever opened it.
    participant_ids: tuple[str, ...]
    #: The searchable text, in the record's own reading order.
    texts: tuple[ReviewTextFragment, ...]
    #: The ingestion run that last observed this record upstream, as the record
    #: states it.
    last_seen_run_id: str
    #: When that run observed it: a UTC-normalised, fixed-width ISO-8601 instant,
    #: **not** the spelling the evidence file carried. SQLite compares TEXT
    #: byte-wise, so an offset-preserving value is not a chronological key -- the
    #: #405 inversion ``findings_store.committed_at_text`` records at length. The
    #: normalisation happens in the builder, which is the layer that knows which
    #: evidence file a bad instant came from.
    last_seen_at: str

    def __post_init__(self) -> None:
        if not self.relative_path:
            raise InvariantViolationError(
                "ReviewSearchRecord.relative_path must not be empty: it is the store's "
                "primary key, and a record with no key cannot be found again."
            )
        if not self.record_key:
            raise InvariantViolationError(
                f"ReviewSearchRecord for `{self.relative_path}` carries no record key, so "
                "nothing could withhold it by key or point a reader back at it."
            )
        if self.pull_request is not None and self.pull_request < 1:
            raise InvariantViolationError(
                f"ReviewSearchRecord for `{self.relative_path}` names pull request "
                f"{self.pull_request}, and a pull-request number is positive. `None` is "
                "the value for a record whose pull request cannot be read."
            )
        # The over-range arm quotes both the bound and nothing else. The value is
        # parsed out of a landed ``eventKey`` with `#(\d+)`, so a hand-edited file
        # can carry one thousands of digits long, and rendering an integer past
        # `sys.get_int_max_str_digits()` raises *inside the refusal being built* --
        # the class `mcp/findings._digits` met (PR #504 round 1, R1-2 face ii).
        # The arm above may quote its value: it fires only below 1.
        if self.pull_request is not None and self.pull_request > MAX_STORED_PULL_REQUEST:
            raise InvariantViolationError(
                f"ReviewSearchRecord for `{self.relative_path}` names a pull request "
                f"larger than {MAX_STORED_PULL_REQUEST}, which is the widest value the "
                "store's column holds, so the record could not be stored."
            )


@dataclass(frozen=True, slots=True)
class ReviewSearchLoad:
    """Everything one build wants the store to hold, and nothing else.

    A store write takes exactly one of these and replaces the whole file with it,
    so what the load does not carry is absent from the artifact rather than
    filtered out of a read. That is the property the withholding seam rests on
    (ADR-0030 decision 6, threat-model T-17a): a record kept out of this tuple has
    no row anywhere, in any table, for any query to reach.

    Raises:
        InvariantViolationError: If two records claim one ``relative_path``. The
            store's primary key would refuse the second insert with a driver
            error naming a constraint; refusing here names the path instead, and
            it refuses before a half-assembled file exists.
    """

    records: tuple[ReviewSearchRecord, ...]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for record in self.records:
            if record.relative_path in seen:
                raise InvariantViolationError(
                    f"Two review records in one load claim `{record.relative_path}`. "
                    "An evidence file's path is its identity, so two records under one "
                    "path means one of them would be lost."
                )
            seen.add(record.relative_path)


def untransportable_reason(value: str) -> str | None:
    """Why ``value`` cannot be handed to SQLite as the text it is, or ``None``.

    The contract with SQLite is not "a Python string"; it is *a NUL-terminated
    UTF-8 byte string*, and two kinds of ``str`` are not one. Both are reachable
    from a JSON-RPC caller, because JSON carries ``\\u0000`` and an unpaired
    ``\\ud800`` -- the population
    :func:`~theurian.infrastructure.sqlite.index_query._is_transportable` already
    declines for the FTS matcher, restated here because this store's boundary is
    a bound ``LIKE`` parameter rather than a MATCH expression and the two fail
    differently:

    - a **NUL** binds without raising and then *silently truncates the
      comparison on both sides*. Measured on SQLite 3.47.1: with
      ``'with\\x00nul'`` stored, ``'%nul%'`` does not match it and
      ``'%with\\x00ZZZZ%'`` does -- so a caller's text is matched by its
      pre-NUL prefix, which is a **wrong** match rather than a smaller one. A
      filter that promises to match text literally cannot keep that promise for
      such a value, so it refuses it instead of answering with rows it did not
      ask for;
    - a **lone surrogate** cannot be encoded as UTF-8 at all, so the driver
      raises ``UnicodeEncodeError`` before SQLite is called -- an exception no
      ``except sqlite3.Error`` catches, arriving at a serving surface as a
      second refusal shape for a second kind of input (SEC-13).

    Stated as the property rather than as a list of bad characters, for the
    reason ``index_query`` records: a query is chosen by something that has just
    read untrusted content, so the safe formulation is "what can cross this
    boundary". Every other control character, the BOM and the non-characters
    cross intact and match nothing.
    """
    if "\x00" in value:
        return "it contains a NUL, which truncates the comparison SQLite makes"
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return "it contains an unpaired surrogate, which has no UTF-8 encoding"
    return None


def texts_of(instance: ReviewSearchRecord | ReviewSearchQuery) -> Iterator[tuple[str, str]]:
    """Every ``(field name, string)`` pair a record or a query carries.

    **The one population both the query's construction check and the search
    builder's build-time check range over**, derived structurally rather than
    kept as two hand-written lists that could drift apart: it walks
    :func:`dataclasses.fields` and recurses into each value by its runtime
    shape -- a plain string, a tuple of them, or a tuple of
    :class:`ReviewTextFragment` -- so a text field added to either dataclass
    tomorrow is reached by the change that adds it, on both sides, rather than
    by whoever remembers to extend a list on one of them. What each caller does
    with a string this yields -- deciding with :func:`untransportable_reason`
    whether it can cross the SQLite boundary, and raising its own error shaped
    for its own layer -- is theirs to do; this function only says which strings
    exist to check.
    """
    for field in fields(instance):
        for text in _texts_in(getattr(instance, field.name)):
            yield field.name, text


def _texts_in(value: object) -> Iterator[str]:
    """Every string reachable inside one field's value, at any of its shapes."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, ReviewTextFragment):
        yield value.content
    elif isinstance(value, tuple):
        for member in value:
            yield from _texts_in(member)


@dataclass(frozen=True, slots=True)
class ReviewSearchQuery:
    """What one search asks for: predicates over stored columns, and a bound.

    Every member is a predicate on something the store holds. **There is
    deliberately no member that orders the result and none that reaches
    metadata**: what a search may ask for is exactly this, and a surface wanting
    more asks for a change here rather than assembling a query of its own.

    ``limit`` has **no default and must be positive**, so the type cannot express
    an unbounded read -- the T-6 shape
    :class:`~theurian.domain.ports.review_finding_store.FindingQuery` is bounded
    at the type for, and for the same reason: a caller that forgets the bound
    gets a construction error rather than a whole-store scan.

    ``text_contains`` is a substring test over the stored fragments, **never a
    query language**. There is no FTS5 table behind it and no tokenizer, so
    ``*``, ``OR``, ``NEAR`` and ``"`` are ordinary characters with no meaning at
    all, and ``%`` and ``_`` -- the two LIKE gives meaning to -- are escaped by
    the adapter before the pattern is bound. Case folding, and its bound, are the
    adapter's to state.

    Raises:
        DomainError: If ``limit`` is not positive, or if any text this query
            carries cannot cross the SQLite boundary as the text it is (see
            :func:`untransportable_reason`). Refused at construction so an
            impossible query never exists -- and so the refusal is about the
            *caller's own input*, which is why it says nothing about what the
            store holds.
    """

    limit: int
    repository: str | None = None
    pull_request: int | None = None
    thread_state: str | None = None
    author: str | None = None
    file_path: str | None = None
    text_contains: str | None = None

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise DomainError(
                f"ReviewSearchQuery.limit must be at least 1, got {self.limit}. A query "
                "with no positive bound is an unbounded read of the store, which no "
                "serving surface may issue."
            )
        # Ranges over `texts_of`, not a hand-written tuple of field names: the
        # two used to be spelled separately here and in
        # `review_search_builder._refuse_untransportable`, and a field added to
        # this dataclass without also joining the tuple would cross the SQLite
        # boundary unchecked while every existing test stayed green. One walk
        # now backs both checks.
        for field_name, text in texts_of(self):
            reason = untransportable_reason(text)
            if reason is not None:
                raise DomainError(
                    f"ReviewSearchQuery.{field_name} cannot be matched as the text it "
                    f"is: {reason}. Send the value without it, or search for a "
                    "substring that does not span it."
                )


@dataclass(frozen=True, slots=True)
class ReviewSearchHit:
    """One record a search selected, with a bounded excerpt of why.

    Every column but :attr:`excerpt` comes back whole; :attr:`excerpt` is cut by
    the read itself. A serving surface deciding what a cut *means* to a client
    asks for one character more than it will publish and marks the row when that
    character arrives, the way ``mcp/findings._bounded_text`` already does for a
    finding.

    **The excerpt is not the only field the corpus sizes**, which is what an
    earlier revision of this docstring got wrong when it called the rest "short,
    provider-shaped" values. :attr:`file_path` and :attr:`author_display_name`
    are author-controlled and arrive at whatever length they were stored at; the
    excerpt is merely the one whose bound can be applied *by the read*, because
    it is the one field a surface publishes an excerpt of rather than the value
    of. What bounds a whole response is the serving surface's own budget
    (``mcp/review_search.MAX_REVIEW_SEARCH_RESPONSE_CHARS``), plus at most one
    record that alone exceeds it -- the page's **first**, served whole and alone
    and bounded by ``MAX_SOURCE_FILE_BYTES`` at landing rather than by that
    budget, while a later over-budget record is not served at all.

    :attr:`file_path`, :attr:`author_display_name` and :attr:`excerpt` are
    **author-controlled untrusted content** (ADR-0030 decision 6) and are
    published under the safety triple. ``file_path`` in particular is served as
    data and is never joined into a filesystem path.
    """

    relative_path: str
    record_key: str
    kind: str
    provider: str
    repository: str
    pull_request: int | None
    thread_state: str | None
    file_path: str | None
    source_uri: str
    author_external_id: str
    author_display_name: str
    last_seen_run_id: str
    last_seen_at: str
    #: The first fragment this record has that satisfies the query's text filter,
    #: cut to the read's ``text_chars``. ``None`` only when the record carries no
    #: fragment at all, which no shipped projection produces -- every kind
    #: contributes at least one.
    excerpt: str | None
    #: Which channel :attr:`excerpt` came from, so a surface can say *where* the
    #: match was rather than only that there was one.
    excerpt_channel: str | None


__all__ = [
    "MAX_STORED_PULL_REQUEST",
    "ReviewSearchHit",
    "ReviewSearchLoad",
    "ReviewSearchQuery",
    "ReviewSearchRecord",
    "ReviewTextChannel",
    "ReviewTextFragment",
    "texts_of",
    "untransportable_reason",
]
