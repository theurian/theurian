"""Rebuilding the review search store from the evidence files (ADR-0030 slice 3).

A **standalone** rebuild service, ``f(evidence files) -> store``, shaped like
:class:`~theurian.application.findings_builder.FindingsBuilder`: collaborators by
injection, so a build is exercised without naming a filesystem layout or SQLite
(ADR-0003). What it projects is decision 3's source -- the JSON records under
``.theurian/review/`` -- onto decision 6's derived, searchable form.

**The direction of the dependency is the whole point of this module's shape.**
The evidence files are the source and this layer may not name the package that
reads them, so the build takes three callables -- :data:`ReadEvidence`,
:data:`ListEvidenceFingerprints` and :data:`WriteReviewSearchStore` -- and the
composition root binds ``ReviewEvidenceStore.read_all``,
``EvidenceReader.fingerprints`` and ``SqliteReviewSearchStore.replace_all`` to
them in that order -- the shape ``review_ingest_service`` already uses for the
landing seam, and the reason :class:`EvidenceEntry` exists rather than the
store's own record type.

**Withholding is physical, and it is decided by the caller.**
:attr:`ReviewSearchBuildRequest.withheld_record_keys` has **no default** for the
reason ``IndexRequest.visible_sensitivities`` has none: "nothing is withheld" is
the state that must never be implicit, and a default parameter is exactly how it
would come back. A record whose key is in that set is not written -- no row in
any table, no flag, no filter a later read has to remember -- which is the
by-construction argument ``index_builder`` makes for an above-ceiling item and the
one ADR-0030 decision 6 inherits from T-17a.

**Nothing here derives that set from author content.** The two shipped callers
pass ``frozenset()``: v1 *ingests* only public allowlisted repositories, so
nothing it fetches is advisory-private and there is nothing for it to withhold,
and the real setter is
[#575](https://github.com/theurian/theurian/issues/575)'s, computed at ingestion
from advisory state. That is a claim about the ingest route rather than about
this build's input: ``.theurian/review/`` is source and is not git-ignored, so
the files read below may equally have arrived with a clone (threat-model T-24),
and withholding was never the control for those. A label, a category or a body
must never decide it --
ADR-0030 decision 3 discharges ADR-0019 exactly there, because a design that let a
label decide what is withheld would hand the withholding decision to whoever
opened the pull request.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, final

from theurian.application.findings_builder import WriteSection
from theurian.application.review_landing_gate import ReviewRecordPayload
from theurian.domain.errors import InvariantViolationError, TheurianError
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review import ReviewEvent, ReviewParticipant, ReviewSubmission, ReviewThread
from theurian.domain.review_search import (
    MAX_STORED_PULL_REQUEST,
    ReviewSearchLoad,
    ReviewSearchRecord,
    ReviewTextChannel,
    ReviewTextFragment,
    texts_of,
    untransportable_reason,
)

#: Every landed evidence record, in the reader's own total order. A *callable*
#: rather than a port object, for :data:`~theurian.application
#: .review_ingest_service.ReadLandedKeys`' reason: this layer may not name the
#: package that owns the files, and the composition root is where the two meet.
ReadEvidence = Callable[[], tuple["EvidenceEntry", ...]]

#: What a listing says about one evidence file: ``(mtime_ns, size, whether it is
#: a regular file)``. Compared for equality and never interpreted here, so this
#: layer names no filesystem concept beyond "the same file, unchanged".
#:
#: **Three slots and not one**, because the transition each closes is a different
#: one: ``mtime_ns`` and ``size`` say the bytes were rewritten, and the flag says
#: the leaf stopped being a file at all -- a directory put in its place. The flag
#: is what makes that last transition a fingerprint change *by construction*
#: rather than one that depends on a timestamp having moved as well.
EvidenceFingerprint = tuple[int, int, bool]

#: What is on disk *right now*, keyed by the same relative paths
#: :attr:`EvidenceEntry.relative_path` carries. A listing and never a read: it is
#: called with the project's write lock held, where a parse per file is exactly
#: what :meth:`ReviewSearchBuilder.build`'s read/write split exists to keep out.
#: A ``stat`` per leaf is not a parse -- it answers from the inode and opens
#: nothing.
#:
#: Bound to the walk the record read goes through rather than to one written for
#: this check, so the set a publish drops against is the set a re-read would
#: enumerate. Two walks with their own opinions about which directories count
#: would drop a live record the moment they drifted.
ListEvidenceFingerprints = Callable[[], Mapping[str, EvidenceFingerprint]]

#: How a built load becomes durable. Bound to a store already addressed to a path,
#: so this layer never names a file: an application service that knew where the
#: store lives would be deciding a filesystem layout it has no business deciding.
WriteReviewSearchStore = Callable[[ReviewSearchLoad], None]

#: The trailing ``#<number>`` of an event key. ``ReviewEvent.external_key`` is
#: ``f"{provider}:{repository}#{number}"`` and is the **only** producer of the
#: ``event_key`` a submission or a thread carries.
#:
#: The population, with the search that answers it. **The pathspec excludes this
#: file**, and it is not decoration: the command appears in this comment, so a
#: search without it matches its own two occurrences here and answers six where
#: the population is four --
#: ``git grep -n 'event_key=' -- packages/theurian-core/src ':!*review_search_builder.py'``.
#:
#: Those four are ``review_provider.py``'s ``ReviewThread(...)``, which binds
#: ``event.external_key`` directly; ``response.py``'s ``ReviewSubmission(...)``
#: inside ``submission()``, whose one caller hands it the same
#: ``event.external_key`` **positionally** and is therefore not itself among the
#: four; and the two ``codec.py`` reads that load the value back out of a landed
#: file. So the parse below is exact for every record the shipped writer lands.
#:
#: ASCII-anchored (``re.ASCII``) rather than ``str.isdigit``: that predicate is
#: true of ``٣`` and ``int`` accepts it, so a non-ASCII digit would become a
#: pull-request number no provider issued.
_EVENT_KEY_NUMBER = re.compile(r"#(\d+)\Z", re.ASCII)

#: How many decimal digits a pull-request number's run may span, derived from the
#: bound rather than chosen: :data:`MAX_STORED_PULL_REQUEST` is nineteen digits
#: wide, so a longer run is out of range whatever it spells.
#:
#: The check is on the run's **length** because ``int`` itself is not total:
#: CPython refuses to convert a decimal string past
#: ``sys.get_int_max_str_digits()`` -- 4,300 digits by default -- and raises
#: ``ValueError``, which is neither a ``TheurianError`` nor anything ``theurian
#: review build`` grades. Measured 2026-09-10, before this arm existed: a landed
#: file whose ``eventKey`` ended in 4,301 nines ended the build in
#: ``ValueError: Exceeds the limit (4300 digits) for integer string conversion``
#: and a traceback. The same family as ``mcp/findings._digits`` (PR #504 round 1,
#: R1-2 face ii), met on the *write* side: the refusal about an unrenderable
#: number must not itself render it.
#:
#: **The whole run is measured, leading zeros included**, and that is the width
#: CPython measures too: ``int("0" * 4301 + "1")`` raises the identical
#: ``ValueError`` -- ``value has 4302 digits`` -- for a run that spells 1
#: (measured 2026-09-10, Python 3.13). Measuring after ``lstrip("0")`` was #630's
#: HIGH-1: the guard saw one significant digit, passed, and handed ``int`` the
#: whole run anyway. What the wider key costs is a zero-padded run spelling an
#: in-range number, refused where it used to be stored -- and the run in a landed
#: key is rendered from an ``int`` by
#: :meth:`~theurian.domain.review.ReviewEvent.external_key`, the one producer
#: :data:`_EVENT_KEY_NUMBER`'s note found, whose ``str`` carries no leading zero
#: at all. A hand-edited one is what this refuses, by the file it names.
_MAX_NUMBER_DIGITS: Final = len(str(MAX_STORED_PULL_REQUEST))


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
    #: The identifier this record carries for itself inside its repository -- the
    #: provider's own on the ingest route, whatever a clone-delivered file names
    #: on the other (T-24). The key
    #: :attr:`ReviewSearchBuildRequest.withheld_record_keys` is matched against.
    record_key: str
    #: ``pull-request``, ``review-submission`` or ``review-thread``.
    kind: str
    provider: str
    repository: str
    #: FR-S3's pointer back to the upstream object, written by ``theurian review
    #: ingest`` on the route that fetched the record.
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
        # No default, for `withheld_record_keys`' reason turned the other way up:
        # a builder that could be constructed without this one revalidates against
        # nothing and republishes a deleted or rewritten record, which is the
        # defect the parameter exists to close. Every construction site therefore
        # states its listing, and every test that builds runs the check over a
        # real directory.
        list_evidence_fingerprints: ListEvidenceFingerprints,
        write: WriteReviewSearchStore,
        # `nullcontext`, so a test driving a builder against a private temporary
        # path gets the same behaviour without inventing a lock file. The shipped
        # composition root always passes the project's real one.
        write_section: WriteSection = nullcontext,
    ) -> None:
        self._read_evidence = read_evidence
        self._list_evidence_fingerprints = list_evidence_fingerprints
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

        Two rebuilds, or a rebuild and a concurrent ``review ingest``, can
        therefore touch the directory at different instants, and the one that read
        *earlier* may publish *later*. So the build fingerprints the directory
        **before** the read and again inside the section, and publishes only the
        records whose fingerprint did not move.

        **The order is load-bearing, and it is the whole reason the first call
        sits where it does.** A fingerprint taken *after* the read describes the
        directory a change has already happened to: for a change that landed
        while the read was in flight -- the widest of this build's three windows,
        since the read is a parse per evidence file -- it would match the one
        taken at the publish, the comparison would say nothing moved, and the
        build would publish the body the read took before the change. That is the
        defect this closes rather than one it introduces. Fingerprint, then read,
        then fingerprint again; ``test_review_search_builder.py``'s
        ``test_a_record_rewritten_between_the_read_and_the_publish_is_not_republished``
        is what reddens when the two calls swap places, and it is the only case
        there that does -- a change made after the read returns is caught
        whichever side of the read the first capture sits on.

        **Every transition a file can make across one build, and what each one
        gets. The enumeration is the closure** -- it is over the *states* a path
        can be in at the two captures, so it has no residue by construction, and
        it replaces a two-direction sentence that named deletion and addition and
        silently left the middle three out:

        * **present -> absent.** Revalidated: the record is dropped. Deleting a
          file is the retention remedy ADR-0030 decision 3 leaves an operator,
          since ``.theurian/review/`` is source and no refetch rebuilds it, so a
          record republished from a stale read puts back content somebody took
          out -- with both commands exiting 0 and nothing saying so.
        * **absent -> present.** One refetch behind, by design. Noticing a file
          that landed after the first capture is not enough -- it would have to be
          *read*, and that is the parse this must not hold -- so it arrives with
          the next rebuild, which converges.
        * **present -> different content.** Revalidated by the fingerprint: the
          record is dropped rather than published with the body the read took. A
          store one rebuild behind costs a rebuild; a store serving the
          pre-update body of a record somebody has already corrected upstream
          costs the correction.
        * **present -> not a regular file.** Revalidated by the fingerprint's
          third slot, which is why that slot is in it: a leaf replaced by a
          directory is a changed fingerprint whatever its timestamp says.
        * **present -> same content.** No-op: the fingerprints are equal and the
          record is published, which is every ordinary build.

        **Over-dropping is the failure direction this chooses, and it is
        reachable.** A benign refetch that rewrites a record with byte-identical
        content still moves ``mtime_ns``, so its record is dropped from *this*
        build even though nothing about it changed. That is fail-closed and it
        converges: the record is absent until the next rebuild, and ``review
        ingest`` runs one itself after every landing.
        ``test_review_search_builder.py``'s
        ``test_a_refetch_that_rewrites_identical_bytes_is_dropped_and_returns_next_build``
        asserts that behaviour rather than leaving it described.

        **The check is a listing, not a second read**, which is what keeps all of
        the above payable: :data:`ListEvidenceFingerprints` opens no file and
        decodes nothing, so each hold is a directory walk plus a ``stat`` per leaf
        and an equality test over records already projected in memory. Re-reading
        here would put the parse per file back under the lock, which is the thing
        the split exists to avoid -- and hashing the content, which is what would
        close the mtime-granularity residual
        :meth:`~theurian.infrastructure.review_evidence.reader.EvidenceReader.fingerprints`
        records, is a read of every file by another name.

        Raises:
            ReviewSearchBuildError: If a landed record carries a value this build
                cannot store -- text with no UTF-8 encoding, a last-seen instant
                that cannot be expressed in UTC, or a value one of
                :class:`~theurian.domain.review_search.ReviewSearchRecord`'s own
                invariants refuses, which is where a hand-edited ``eventKey``
                naming pull request ``0``, or one wider than the store's column
                holds, arrives. Each names the evidence file. Raised before the
                write section is entered, so a corpus this build cannot project
                never takes the project's write lock at all.
            TheurianError: Whatever the reader, the listing or the writer raises,
                unchanged. Each carries its own remedy about its own artefact, and
                the read side's in particular is about a file this layer never
                opened. A listing that refuses ends the build with nothing
                written, which is the direction to fail in: publishing without
                knowing what is on disk is how the deletion above comes back.
        """
        # **Before the read**, and the docstring's ordering paragraph is why: a
        # fingerprint taken after it describes a directory a change made *during*
        # the read has already happened to, matches the one taken at the publish,
        # and lets the pre-update body through as unchanged.
        before_the_read = self._list_evidence_fingerprints()
        entries = self._read_evidence()
        kept = tuple(
            entry for entry in entries if entry.record_key not in request.withheld_record_keys
        )
        projected = tuple(_projected(entry) for entry in kept)
        with self._write_section():
            # Inside the section and immediately before the write, so what is
            # published is keyed on what is on disk at publish time rather than at
            # read time. A record withheld above never reaches here at all, so a
            # key that is both withheld and changed is out for the first reason.
            at_the_publish = self._list_evidence_fingerprints()
            load = ReviewSearchLoad(
                records=tuple(
                    record
                    for record in projected
                    if _unchanged(record.relative_path, before_the_read, at_the_publish)
                )
            )
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
            # What was published, which is the read's records minus the withheld
            # ones minus any whose file moved between the two fingerprints. So the
            # two counts stop summing to what the read found exactly when the
            # revalidation above drops something -- a window narrow enough that no
            # third key is published for it, and wide enough that this note is
            # cheaper than the next reader deriving the missing one.
            "records": len(load.records),
            # The count of records this build was given and did not write. It is a
            # function of the caller's own withheld set and of files on the
            # caller's own disk, so it discloses nothing to whoever runs the
            # command; the shipped callers pass an empty set, so it is always 0.
            "withheld": len(entries) - len(kept),
        }


def _unchanged(
    relative_path: str,
    before_the_read: Mapping[str, EvidenceFingerprint],
    at_the_publish: Mapping[str, EvidenceFingerprint],
) -> bool:
    """Whether one record's file is the same file, unmoved, at both captures.

    **One predicate over the two captures rather than a check per transition**,
    which is what makes :meth:`ReviewSearchBuilder.build`'s enumeration a closure
    and not a list: a transition nobody thought of is a fingerprint that differs,
    and a fingerprint that differs is a drop. Adding a slot to
    :data:`EvidenceFingerprint` therefore widens what this sees without touching
    this function.

    Both directions of *absence* are drops, and they are not the same case. A path
    absent at the publish is a file that went away -- the deletion this exists for.
    A path absent *before the read* is a file that landed inside the build's own
    window: it was read and projected, but nothing observed it before the read, so
    there is no fingerprint the publish-time one can be compared against and
    publishing it would be publishing a record on the strength of one observation.
    It arrives with the next rebuild, which is the same one-refetch-behind
    treatment a file that lands after the read gets.

    ``at_the_publish.get`` returning ``None`` never compares equal to a real
    fingerprint, so the vanished case falls out of the equality rather than
    needing an arm of its own -- but the *before* side does need one, because two
    ``None``\\ s would compare equal and publish a record neither capture saw.
    """
    captured = before_the_read.get(relative_path)
    return captured is not None and at_the_publish.get(relative_path) == captured


def _projected(entry: EvidenceEntry) -> ReviewSearchRecord:
    """One landed record as the row set the store holds.

    Pure, and a total function of ``entry`` alone -- no clock, no filesystem, no
    ordering that depends on what else was in the corpus -- which is what makes a
    rebuild over unchanged evidence reproduce.
    """
    author, participants, texts = _people_and_text(entry.payload)
    try:
        record = ReviewSearchRecord(
            relative_path=entry.relative_path,
            record_key=entry.record_key,
            kind=entry.kind,
            provider=entry.provider,
            repository=entry.repository,
            pull_request=_pull_request_of(entry.payload, entry.relative_path),
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
    except InvariantViolationError as exc:
        # Every one of these invariants is about a value a **landed file**
        # supplied, and `InvariantViolationError` carries no remedy: an
        # `eventKey` hand-edited to end `#0` reached an operator as `Run
        # `theurian doctor`.`, the CLI's backstop for an error that describes no
        # cure -- over a defect `doctor` cannot see and would not mention.
        #
        # Caught as the **class** rather than by re-checking a chosen field here:
        # the population is every invariant `ReviewSearchRecord` states, so a
        # fourth one added there arrives graded and naming the file by the change
        # that adds it. The detail is `str(exc)` unchanged because those messages
        # already open with the record's own path.
        raise ReviewSearchBuildError(str(exc), remedy=_record_cure(entry.relative_path)) from exc
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


def _pull_request_of(payload: ReviewRecordPayload, relative_path: str) -> int | None:
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

    ``relative_path`` is taken for :func:`_instant_text`'s reason: the digit run
    below is refused by a message that has to name the evidence file, and this
    function is the layer that is looking at the string.

    Raises:
        ReviewSearchBuildError: If the key's digit run is wider than the store's
            column -- the **whole** run, leading zeros included -- which is a
            refusal on its **length** and therefore before any ``int``. See
            :data:`_MAX_NUMBER_DIGITS`.
    """
    match payload:
        case ReviewEvent():
            # No length arm: the number arrived as a JSON integer through the
            # codec, so `int` has already been applied by the reader and its own
            # failures are graded there. `ReviewSearchRecord` refuses an
            # out-of-range one at construction, which is the arm this reaches.
            return payload.number
        case ReviewSubmission() | ReviewThread():
            found = _EVENT_KEY_NUMBER.search(payload.event_key)
            if found is None:
                return None
            digits = found.group(1)
            # The whole run, not `digits.lstrip("0")`: the significant-digit
            # spelling measured something narrower than what it was guarding, and
            # `int` below counts every character in the run. See
            # `_MAX_NUMBER_DIGITS`.
            if len(digits) > _MAX_NUMBER_DIGITS:
                raise ReviewSearchBuildError(
                    f"`{relative_path}` names a pull request as a run of "
                    f"{len(digits)} digits, wider than the {_MAX_NUMBER_DIGITS} "
                    f"digits of {MAX_STORED_PULL_REQUEST}, the widest value the "
                    f"store's column holds. The whole run is measured, leading "
                    f"zeros included, and refused unconverted: the width is what "
                    f"this refuses, not the value it spells.",
                    remedy=_record_cure(relative_path),
                )
            return int(digits)


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

    The population is **every string the record carries**, reached through
    :func:`~theurian.domain.review_search.texts_of` rather than by a list
    somebody keeps in step here: a field added to :class:`ReviewSearchRecord`
    is covered by the change that adds it, whether it is a plain string, a
    tuple of them, or a tuple of fragments -- and the same walk backs
    :class:`~theurian.domain.review_search.ReviewSearchQuery`'s own
    construction check, so the two cannot drift into checking different
    populations. ``test_review_search_builder.py``'s
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
    for field_name, text in texts_of(record):
        reason = untransportable_reason(text)
        if reason is not None:
            raise ReviewSearchBuildError(
                # "a value in `<field>`", not "a `<field>`: the field names are
                # a mix of singulars and plurals (`file_path`, `texts`,
                # `participant_ids`), and the article-plus-name form printed
                # "carries a texts this build cannot store" against a real
                # record on 2026-09-10. The wrapper reads for every member.
                f"`{record.relative_path}` carries a value in `{field_name}` this "
                f"build cannot store: {reason}.",
                remedy=_record_cure(record.relative_path),
            )


__all__ = [
    "EvidenceEntry",
    "EvidenceFingerprint",
    "ListEvidenceFingerprints",
    "ReadEvidence",
    "ReviewSearchBuildError",
    "ReviewSearchBuildRequest",
    "ReviewSearchBuilder",
    "WriteReviewSearchStore",
]
