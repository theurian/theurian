"""Rebuilding the review search store from the evidence files (ADR-0030 slice 3).

A **standalone** rebuild service, ``f(evidence files) -> store``, shaped like
:class:`~theurian.application.findings_builder.FindingsBuilder`: collaborators by
injection, so a build is exercised without naming a filesystem layout or SQLite
(ADR-0003). What it projects is decision 3's source -- the JSON records under
``.theurian/review/`` -- onto decision 6's derived, searchable form.

**The direction of the dependency is the whole point of this module's shape.**
The evidence files are the source and this layer may not name the package that
reads them, so the build takes a :data:`ReadEvidence` callable and a
:data:`WriteReviewSearchStore` callable, and the composition root binds
``ReviewEvidenceStore.read_all`` to the first and
``SqliteReviewSearchStore.replace_all`` to the second -- the shape
``review_ingest_service`` already uses for the landing seam, and the reason
:class:`EvidenceEntry` exists rather than the store's own record type.

**Withholding is physical, and it is decided by the caller.**
:attr:`ReviewSearchBuildRequest.withheld_record_keys` has **no default** for the
reason ``IndexRequest.visible_sensitivities`` has none: "nothing is withheld" is
the state that must never be implicit, and a default parameter is exactly how it
would come back. A record whose key is in that set is not written -- no row in
any table, no flag, no filter a later read has to remember -- which is the
by-construction argument ``index_builder`` makes for an above-ceiling item and the
one ADR-0030 decision 6 inherits from T-17a.

**Nothing here derives that set from author content.** The two shipped callers
pass ``frozenset()``: v1's scope is public allowlisted repositories, so there is
nothing to withhold, and the real setter is
[#575](https://github.com/theurian/theurian/issues/575)'s, computed at ingestion
from advisory state. A label, a category or a body must never decide it --
ADR-0030 decision 3 discharges ADR-0019 exactly there, because a design that let a
label decide what is withheld would hand the withholding decision to whoever
opened the pull request.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from contextlib import nullcontext
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from typing import final

from theurian.application.findings_builder import WriteSection
from theurian.application.review_landing_gate import ReviewRecordPayload
from theurian.domain.errors import TheurianError
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review import ReviewEvent, ReviewParticipant, ReviewSubmission, ReviewThread
from theurian.domain.review_search import (
    ReviewSearchLoad,
    ReviewSearchRecord,
    ReviewTextChannel,
    ReviewTextFragment,
    untransportable_reason,
)

#: Every landed evidence record, in the reader's own total order. A *callable*
#: rather than a port object, for :data:`~theurian.application
#: .review_ingest_service.ReadLandedKeys`' reason: this layer may not name the
#: package that owns the files, and the composition root is where the two meet.
ReadEvidence = Callable[[], tuple["EvidenceEntry", ...]]

#: How a built load becomes durable. Bound to a store already addressed to a path,
#: so this layer never names a file: an application service that knew where the
#: store lives would be deciding a filesystem layout it has no business deciding.
WriteReviewSearchStore = Callable[[ReviewSearchLoad], None]

#: The trailing ``#<number>`` of an event key. ``ReviewEvent.external_key`` is
#: ``f"{provider}:{repository}#{number}"`` and is the **only** producer of the
#: ``event_key`` a submission or a thread carries -- ``git grep -n 'event_key='
#: -- packages/theurian-core/src`` answers four lines: the two codec reads that
#: load the value back out of a file, and the two writers, which are
#: ``response.submission(node, project_id, event.external_key)`` and
#: ``event_key=event.external_key``. So the parse below is exact for every record
#: the shipped writer lands.
#:
#: ASCII-anchored (``re.ASCII``) rather than ``str.isdigit``: that predicate is
#: true of ``٣`` and ``int`` accepts it, so a non-ASCII digit would become a
#: pull-request number no provider issued.
_EVENT_KEY_NUMBER = re.compile(r"#(\d+)\Z", re.ASCII)


class ReviewSearchBuildError(TheurianError):
    """One evidence record could not be projected into the search store.

    Always names the evidence file, because that is the artefact an operator has
    to look at: the store is derived and rebuilding it will fail the same way
    until the record is fixed. Carries a remedy naming that file and the command
    to re-run once it is.
    """

    def __init__(self, detail: str, *, remedy: str) -> None:
        self.remedy = remedy
        super().__init__(detail)


def _record_cure(relative_path: str) -> str:
    """The cure for a landed record this build cannot project.

    Names the file, says how to look at it, and only then offers the re-run --
    a retry on its own would be circular, since the build fails on the same
    record every time until somebody changes it. It deliberately does not say
    "delete it": ``.theurian/review/`` is the source and has no rebuild (ADR-0030
    decision 3), so a deleted record is evidence no refetch recovers.
    """
    return (
        f"Open .theurian/review/{relative_path} and correct the value the message "
        f"names, then run `theurian review build` again. That file is review "
        f"evidence and is the only copy, so edit it -- do not delete it."
    )


@dataclass(frozen=True, slots=True)
class EvidenceEntry:
    """One landed evidence record, as this layer reads it.

    **Deliberately not the evidence store's own record type**, for the reason
    :class:`~theurian.application.review_ingest_service.LandedRecord` is not
    either: that type's identity includes the filesystem layout it lands under --
    it derives its own path -- so an application service importing it would depend
    on a store's on-disk shape. The composition root maps one onto the other in
    nine field copies.

    ``record_key`` and ``kind`` are copied across rather than re-derived here.
    They are properties of the stored record, computed from its payload by the
    package that wrote it, and computing them a second time in this layer would be
    a second definition of the same rule -- which is exactly the drift that makes a
    withheld key match one spelling and miss the other.
    """

    #: The file's path under ``.theurian/review/``, POSIX-spelled. Unique across
    #: one read, and the store's primary key.
    relative_path: str
    #: The provider's own identifier for this record inside its repository. The
    #: key :attr:`ReviewSearchBuildRequest.withheld_record_keys` is matched against.
    record_key: str
    #: ``pull-request``, ``review-submission`` or ``review-thread``.
    kind: str
    provider: str
    repository: str
    #: FR-S3's pointer back to the upstream object, written by Theurian.
    anchor: SourceAnchor
    payload: ReviewRecordPayload
    last_seen_run_id: str
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewSearchBuildRequest:
    """What one build is asked to hold, and what it is asked to leave out.

    One field, and it has **no default**. See this module's docstring for why:
    "nothing is withheld" must be a decision every caller states rather than one a
    signature can supply by omission.
    """

    #: Record keys this build must not write. A record whose
    #: :attr:`EvidenceEntry.record_key` is in this set contributes no row to any
    #: table -- it is never handed to the writer at all.
    #:
    #: **The key is the record key alone, and that is deliberately coarse.** A
    #: record key is unique inside its *repository*, not across a project, so a
    #: pull-request number in this set withholds that number in every repository
    #: the project has ingested. That is over-broad rather than under-broad: it
    #: can withhold more than the caller meant and never less, so the error is in
    #: the fail-closed direction. Recorded rather than narrowed because the
    #: shipped callers pass an empty set and the real setter (#575) lands with the
    #: repository scoping the question needs.
    withheld_record_keys: frozenset[str]


@final
class ReviewSearchBuilder:
    """Rebuilds the review search store from the landed evidence files, wholesale."""

    def __init__(
        self,
        *,
        read_evidence: ReadEvidence,
        write: WriteReviewSearchStore,
        # `nullcontext`, so a test driving a builder against a private temporary
        # path gets the same behaviour without inventing a lock file. The shipped
        # composition root always passes the project's real one.
        write_section: WriteSection = nullcontext,
    ) -> None:
        self._read_evidence = read_evidence
        self._write = write
        self._write_section = write_section

    def build(self, request: ReviewSearchBuildRequest) -> dict[str, object]:
        """Read every landed record and land the ones this build may hold.

        **Wholesale, so a rebuild reproduces.** The reader answers in a total
        order (sorted by relative path) and the projection below is a pure
        function of each record, so building twice over unchanged evidence
        produces the same load, and building over a *deleted* store from the same
        files produces a store equal to the one that was deleted -- ADR-0030's
        owed test 4, whose write-and-read-back half slice 2 already held.

        **The withheld records never reach the writer.** They are dropped here,
        before a load exists, so nothing downstream is asked to remember a filter
        and nothing in the store can tell a withheld key from one that never
        existed.

        **The read happens outside the critical section, the write inside it.**
        Reading the evidence directory touches nothing the write lock protects and
        can take a parse per file, so holding the project's single writer lock
        across it would block ``migrate apply`` for the length of a directory walk
        for no guarantee -- the same split ``FindingsBuilder.build`` makes for its
        git read, and one continuous hold over the publish rather than two
        sequential holds (#468).

        That leaves one ordering the lock deliberately does not fix: two rebuilds
        can read the directory at different instants and the one that read
        *earlier* may publish *later*, so the surviving store can be one refetch
        behind. It is a whole, self-consistent store either way, and the next
        rebuild converges.

        Raises:
            ReviewSearchBuildError: If a landed record carries a value this build
                cannot store -- text with no UTF-8 encoding, or a last-seen instant
                that cannot be expressed in UTC. Each names the evidence file.
            TheurianError: Whatever the reader or the writer raises, unchanged.
                Both carry their own remedy about their own artefact, and the read
                side's in particular is about a file this layer never opened.
        """
        entries = self._read_evidence()
        kept = tuple(
            entry for entry in entries if entry.record_key not in request.withheld_record_keys
        )
        load = ReviewSearchLoad(records=tuple(_projected(entry) for entry in kept))
        with self._write_section():
            self._write(load)
        # Two counts and no third. A per-repository count was drafted here and
        # dropped: its dict key would have been the string `"repositories"`, which
        # is the published spelling of `providers.review.repositories`, and
        # `test_config_key_call_sites.py`'s reader scan reads a bare string
        # constant of that spelling as a second module opening
        # `.theurian/config.yaml`. The count was worth less than the false
        # positive, and a renamed key would have been a name chosen to dodge a
        # scan rather than to describe a number.
        return {
            "records": len(load.records),
            # The count of records this build was given and did not write. It is a
            # function of the caller's own withheld set and of files on the
            # caller's own disk, so it discloses nothing to whoever runs the
            # command; the shipped callers pass an empty set, so it is always 0.
            "withheld": len(entries) - len(kept),
        }


def _projected(entry: EvidenceEntry) -> ReviewSearchRecord:
    """One landed record as the row set the store holds.

    Pure, and a total function of ``entry`` alone -- no clock, no filesystem, no
    ordering that depends on what else was in the corpus -- which is what makes a
    rebuild over unchanged evidence reproduce.
    """
    author, participants, texts = _people_and_text(entry.payload)
    record = ReviewSearchRecord(
        relative_path=entry.relative_path,
        record_key=entry.record_key,
        kind=entry.kind,
        provider=entry.provider,
        repository=entry.repository,
        pull_request=_pull_request_of(entry.payload),
        thread_state=_thread_state_of(entry.payload),
        file_path=_file_path_of(entry.payload),
        source_uri=entry.anchor.source_uri,
        author_external_id=author.external_id,
        author_display_name=author.display_name,
        participant_ids=participants,
        texts=texts,
        last_seen_run_id=entry.last_seen_run_id,
        last_seen_at=_instant_text(entry.last_seen_at, entry.relative_path),
    )
    _refuse_untransportable(record)
    return record


def _people_and_text(
    payload: ReviewRecordPayload,
) -> tuple[ReviewParticipant, tuple[str, ...], tuple[ReviewTextFragment, ...]]:
    """The principal author, every participant, and the searchable text.

    A ``match`` rather than a table, so mypy refuses one that stops covering the
    three payload types: a fourth kind cannot reach the store without somebody
    deciding what its author, its participants and its text are.

    **A thread's participants are every commenter plus whoever resolved it**, not
    just the opener. The filter answers "who took part in this", which is the
    question a reviewer looking for their own threads is asking; keying it on the
    opening comment alone would silently drop every thread somebody only replied
    to. De-duplicated in first-appearance order, so the order is a property of the
    record rather than of a set's iteration.
    """
    match payload:
        case ReviewEvent():
            return (
                payload.author,
                (payload.author.external_id,),
                (
                    ReviewTextFragment(channel=ReviewTextChannel.TITLE, content=payload.title),
                    ReviewTextFragment(channel=ReviewTextChannel.BODY, content=payload.body),
                ),
            )
        case ReviewSubmission():
            return (
                payload.author,
                (payload.author.external_id,),
                (ReviewTextFragment(channel=ReviewTextChannel.BODY, content=payload.body),),
            )
        case ReviewThread():
            people = [comment.author for comment in payload.comments]
            if payload.resolution is not None and payload.resolution.resolved_by is not None:
                people.append(payload.resolution.resolved_by)
            return (
                # A thread has at least one comment -- `ReviewThread.__post_init__`
                # refuses one with none -- so the opener always exists.
                payload.comments[0].author,
                _distinct(person.external_id for person in people),
                tuple(
                    ReviewTextFragment(channel=ReviewTextChannel.COMMENT, content=comment.body)
                    for comment in payload.comments
                ),
            )


def _distinct(values: Iterable[str]) -> tuple[str, ...]:
    """``values`` with repeats removed, keeping the first appearance's position."""
    return tuple(dict.fromkeys(values))


def _pull_request_of(payload: ReviewRecordPayload) -> int | None:
    """Which pull request a record belongs to, where the record can say.

    A :class:`~theurian.domain.review.ReviewEvent` carries its own number. The
    other two carry an ``event_key``, and reading the number back out of that
    string is a parse -- which
    :class:`~theurian.application.review_landing_gate.ReviewRecordIdentity`
    deliberately avoids by carrying the number beside the payload instead.

    That avoidance does not transfer here and the difference is worth stating:
    the gate is downstream of the adapter and can ask it what it knew, while this
    builder reads a **file**, where the key is the only thing that names the pull
    request at all. The alternative is ``None`` for two of the three kinds, which
    would make the pull-request filter miss exactly the records -- threads -- a
    reviewer searches for. So it is parsed, against the one format
    :data:`_EVENT_KEY_NUMBER` records as the only producer, and a key that does
    not match answers ``None`` rather than a guess.
    """
    match payload:
        case ReviewEvent():
            return payload.number
        case ReviewSubmission() | ReviewThread():
            found = _EVENT_KEY_NUMBER.search(payload.event_key)
            return None if found is None else int(found.group(1))


def _thread_state_of(payload: ReviewRecordPayload) -> str | None:
    """A thread's resolution state; ``None`` for the two kinds that have none.

    A submission's ``state`` is deliberately **not** folded in here. It is the
    provider's own review verdict -- ``APPROVED``, ``CHANGES_REQUESTED`` -- mapped
    onto no closed set of this model's (see
    :class:`~theurian.domain.review.ReviewSubmission`), while a thread state is
    this model's own word derived from two booleans. One column holding both would
    make a ``thread_state="resolved"`` filter answer over a vocabulary its name
    does not describe, and a caller could not tell which kind a value came from.
    """
    match payload:
        case ReviewThread():
            return payload.state.value
        case ReviewEvent() | ReviewSubmission():
            return None


def _file_path_of(payload: ReviewRecordPayload) -> str | None:
    """The file a thread is anchored to, as received.

    **Author-controlled** (ADR-0030 decision 6): whoever opened the pull request
    named it. It is carried as data for filtering and display, and no reader may
    join it into a filesystem path -- the containment rule decision 3 states for
    the write side, unchanged on the read side.
    """
    match payload:
        case ReviewThread():
            return payload.file_path
        case ReviewEvent() | ReviewSubmission():
            return None


def _instant_text(moment: datetime, relative_path: str) -> str:
    """One instant as the fixed-width UTC text the store keeps.

    Normalised and fixed-width for the reason
    ``findings_store.committed_at_text`` records in full: SQLite compares TEXT
    byte-wise, so an offset-preserving ISO-8601 string is not a chronological key
    and a sub-second value would sort against a whole-second one on the byte at
    offset 19.

    ``astimezone`` can raise on a max-year negative-offset instant, and that is
    reachable: the evidence reader accepts any aware ``datetime``
    ``datetime.fromisoformat`` produces, so a hand-edited ``observedAt`` of
    ``9999-12-31T23:59:59-11:00`` overflows here. It is graded rather than left to
    crash, and the message names the file, because that is what an operator has to
    open.
    """
    try:
        return moment.astimezone(UTC).isoformat(timespec="microseconds")
    except (ValueError, OverflowError) as exc:
        raise ReviewSearchBuildError(
            f"`{relative_path}` records a last-seen time of {moment.isoformat()}, which "
            f"is out of range once converted to UTC, so it cannot be stored.",
            remedy=_record_cure(relative_path),
        ) from exc


def _refuse_untransportable(record: ReviewSearchRecord) -> None:
    """Refuse a record carrying text SQLite cannot be handed as the text it is.

    The population is **every string the record carries**, reached by reflection
    over :func:`dataclasses.fields` rather than by a list somebody keeps in step:
    a field added to :class:`ReviewSearchRecord` is covered by the change that
    adds it, whether it is a plain string, a tuple of them, or a tuple of
    fragments. ``test_review_search_builder.py``'s
    ``test_the_transportability_check_reaches_every_string_a_record_carries``
    is what fails when a shape stops being reached.

    Why it is here rather than at the store: an unpaired surrogate reaches the
    driver as ``UnicodeEncodeError``, which the store's write arm grades but can
    only describe as "writing <file>.sqlite" -- while the artefact an operator
    must open is the *evidence* file, which only this layer knows the name of.
    A surrogate gets that far because ``json.loads`` decodes a ``\\ud800`` escape
    into one, so a hand-edited record can carry a value the writer that landed it
    could never have produced.
    """
    for field in fields(record):
        for text in _strings_in(getattr(record, field.name)):
            reason = untransportable_reason(text)
            if reason is not None:
                raise ReviewSearchBuildError(
                    # "a value in `<field>`", not "a `<field>`: the field names are
                    # a mix of singulars and plurals (`file_path`, `texts`,
                    # `participant_ids`), and the article-plus-name form printed
                    # "carries a texts this build cannot store" against a real
                    # record on 2026-09-10. The wrapper reads for every member.
                    f"`{record.relative_path}` carries a value in `{field.name}` this "
                    f"build cannot store: {reason}.",
                    remedy=_record_cure(record.relative_path),
                )


def _strings_in(value: object) -> Iterator[str]:
    """Every string reachable inside one record field, at any of its shapes."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, ReviewTextFragment):
        yield value.content
    elif isinstance(value, tuple):
        for member in value:
            yield from _strings_in(member)


__all__ = [
    "EvidenceEntry",
    "ReadEvidence",
    "ReviewSearchBuildError",
    "ReviewSearchBuildRequest",
    "ReviewSearchBuilder",
    "WriteReviewSearchStore",
]
