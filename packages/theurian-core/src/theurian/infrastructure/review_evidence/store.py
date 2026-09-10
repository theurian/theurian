"""Landing review records as files, and reading them back (ADR-0030 decision 3).

The writer half is what an ingestion run calls once the gate has decided which
records may become files. The reader half is what slice 3's SQLite serving store
is built from; it ships now so that "the evidence files are the source" is a
property something already exercises rather than a sentence waiting on a
consumer. **The store-rebuild half of ADR-0030's owed test 4 -- delete the derived
store, rebuild, and the served content reproduces -- completes in slice 3, where
there is a store to delete.** What is held here is the half that does not need
one: write, read back, and get the same records.

**The reading itself is next door.** This module passed the project's 800-line
ceiling, so the read half moved to
:mod:`theurian.infrastructure.review_evidence.reader`, the record types both
halves name to :mod:`theurian.infrastructure.review_evidence.records`, and the
case-fold pair to :mod:`theurian.infrastructure.review_evidence.spellings`.
:meth:`ReviewEvidenceStore.read_all` delegates, so the class is still both halves
to a caller.

**Refetch never deletes** (decision 3). :meth:`ReviewEvidenceStore.write` writes
the records it is given and touches no record it was not given: it never
enumerates, never diffs, and the one path it unlinks is a **regular file** at
the ``.writing`` name its own write opens, so a record whose upstream comment
has been deleted keeps its file and keeps the stamp of the last run that saw it.

That sentence used to say "the temporary its own write opened", which was the
claim and not the behaviour: the cleanup ran on every failure including the ones
where the open had refused *because* something else was at that name, so a
symbolic link an operator planted was silently removed before the refusal
describing it was published (round two, R2-B).
:meth:`ReviewEvidenceStore._discard_the_temporary` is where the ``lstat`` that
makes it true now lives, and three things fail when it stops holding:
``tests/unit/test_review_evidence_store.py::test_a_record_upstream_no_longer_returns_survives_the_refetch``,
``tests/unit/test_review_evidence_writing_temporary.py::test_a_planted_link_at_the_temporary_survives_the_refusal_that_names_it``,
and
``tests/unit/test_adr_0030_claims.py::test_the_evidence_package_moves_a_file_only_where_the_publish_records_it``
when a second removal appears in this package.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Any, Final, final

from theurian.domain.errors import TheurianError
from theurian.domain.review import ReviewEvent, ReviewSubmission, ReviewThread
from theurian.domain.review_ingest import bounded_echo, bounded_quote
from theurian.infrastructure.review_evidence.codec import (
    anchor_to_json,
    event_to_json,
    submission_to_json,
    thread_to_json,
)
from theurian.infrastructure.review_evidence.cures import (
    COLLISION_CURE,
    UNWRITABLE_CURE,
    folded_component_cure,
    oversized_record_cure,
    planted_artefact_cure,
    planted_link_cure,
    planted_temporary_cure,
    relocated_directory_cure,
    unwritable_record_cure,
)
from theurian.infrastructure.review_evidence.errors import ReviewEvidenceError
from theurian.infrastructure.review_evidence.layout import EVIDENCE_FORMAT_VERSION
from theurian.infrastructure.review_evidence.reader import EvidenceReader
from theurian.infrastructure.review_evidence.records import (
    EvidencePayload,
    EvidenceRecord,
    StoredRecord,
)
from theurian.infrastructure.review_evidence.run import IngestionRun
from theurian.infrastructure.review_evidence.spellings import OnDiskSpellings
from theurian.security.no_follow import (
    is_a_symbolic_link_refusal,
    write_text_without_following_a_link,
)
from theurian.security.paths import (
    MAX_SOURCE_FILE_BYTES,
    assert_no_symlink_escape,
    resolve_within_root,
)
from theurian.security.regular_file import (
    IrregularArtefactError,
    shape_that_is_not_a_regular_file,
)

#: What the bytes are written to before ``os.replace`` publishes them over the
#: record. It deliberately does **not** end in
#: :data:`~theurian.infrastructure.review_evidence.layout.EVIDENCE_SUFFIX`, so a
#: file an interrupted run left behind is skipped by
#: :meth:`~theurian.infrastructure.review_evidence.reader.EvidenceReader.relative_paths`
#: rather than read as a record.
_WRITING_SUFFIX: Final = ".writing"


def _partial_landing(landed: int) -> str:
    """What a write-side refusal says about the run it interrupted.

    One spelling, appended by :meth:`ReviewEvidenceStore.write` to every refusal
    this store raises rather than written at each site, because a per-record
    guard does not know the run's count and a sentence copied into eight places
    is eight places for it to drift.

    **"Nothing was written" is what these said until round two, and it was
    false.** ``os.replace`` publishes each record whole or not at all, so the
    write is atomic *per record* and not across a run: the records before the
    refused one are on disk and nothing rolls them back. An operator whose
    evidence has no rebuild reading "nothing was written" goes looking for a
    rollback that never happened, and may re-run against a directory they
    believe is empty.
    """
    return (
        f"This record was not written; the {bounded_echo(landed)} record(s) this run "
        f"wrote before it stay on disk."
    )


def _planted_shape(exc: OSError, writing: Path) -> str | None:
    """What is standing at ``writing``, when something is; ``None`` otherwise.

    Two sources, in the order of how much they can be trusted. An
    ``IrregularArtefactError`` was measured from the **descriptor** the open
    returned, so it describes the object this call actually got and there is no
    window between the answer and the thing it is about. Everything else has to
    be asked of the name afterwards, because those shapes refuse the open before
    a descriptor exists.

    ``lstat`` rather than ``stat``: a symbolic link is one of the shapes being
    named, and following it would report whatever it points at instead.
    """
    if isinstance(exc, IrregularArtefactError):
        return exc.shape
    try:
        mode = os.lstat(writing).st_mode
    except OSError:
        # The path is gone, or unreachable for the same reason the open was. The
        # caller falls back to the errno sentence rather than guessing a shape.
        return None
    if stat.S_ISLNK(mode):
        return "a symbolic link"
    return shape_that_is_not_a_regular_file(mode)


@final
class ReviewEvidenceStore:
    """The evidence files under one project's ``.theurian/review/``.

    Args:
        review_root: ``ProjectPaths.review``, already proved contained inside the
            project. Every path this class builds is resolved against it again,
            because containment of the *directory* says nothing about a symbolic
            link planted **under** it -- the two guards close different halves and
            neither implies the other (#237, #523).
    """

    def __init__(self, review_root: Path) -> None:
        self._root = review_root

    def write(self, records: Iterable[EvidenceRecord], *, run: IngestionRun) -> tuple[str, ...]:
        """Land ``records``, stamped with ``run``, and answer where each went.

        **The collision guard keys on the path the *filesystem* would collide
        on**, which is the casefolded one. macOS and Windows fold case by
        default, so a guard comparing byte spellings passes two records the disk
        then merges into one, and "a silently overwritten record is a lost one"
        would be a promise this method breaks on the platform most of this
        project's development happens on. The layout keeps two ids' leaves apart
        after folding
        (:mod:`theurian.infrastructure.review_evidence.layout`), so the only
        thing it emits that reaches this refusal is one record key returned
        twice; the guard does not *rest* on that, because a store that inherited
        the layout's injectivity as an assumption would go quiet the day the
        layout changed.
        ``tests/unit/test_review_evidence_store.py::test_two_records_a_folding_filesystem_would_merge_are_refused``
        is what fails when the key stops being the folded one, and it drives the
        guard with a deliberately colliding layout because the shipped one has no
        input that reaches it.

        **This is the landing seam, and what it holds is stated over an
        observable rather than over an exception family** (round two, R2-A). The
        observable: *whatever a provider answered with, a run ends with one of
        the two documents ``theurian review ingest`` publishes -- never a
        traceback.* The CLI's catch is ``except TheurianError``, so the
        population that breaks it is precisely **the complement of that class**,
        and :meth:`_landing_refusal` keys on exactly that complement rather than
        on a list of members. An enumeration would be the wrong key here and the
        reason is measurable: the members are not raise sites. Two of them are
        ordinary calls that are not total over a Python ``str``, and this key
        finds them where a ``raise``-grep cannot::

            git grep -n -P '\\.encode\\(|json\\.dumps\\(|os\\.replace\\(' -- \\
                packages/theurian-core/src/theurian/infrastructure/review_evidence/

        It answers **six** lines: the four calls, and two inside this
        paragraph -- the pattern is line-shaped and cannot tell prose from code,
        so a paragraph naming the calls matches itself. Its output used to be
        pasted here whole, which made the answer eight and put four line numbers
        in a docstring: they read 134, 389, 441 and 590, and every one of them
        had since moved. So the *call* is what is recorded and never the line.
        ``layout._hashed`` writes ``value.encode("utf-8")``, :meth:`_write_one`
        writes ``document.encode("utf-8")``, :meth:`_publish` renames through
        ``os.replace``, and :func:`_document` serialises through ``json.dumps``.

        ``json.loads`` decodes ``\\ud800`` into a lone surrogate, which UTF-8
        cannot encode: one anywhere in a record -- a thread id, a comment body --
        left the first two of those as a bare ``UnicodeEncodeError``, so
        ``review ingest`` published **no document at all** while the records
        before it had already landed. The third is guarded by
        :meth:`_write_one`'s own ``OSError`` arms; the fourth cannot raise on
        input this store can hold, and is covered anyway by keying on the
        complement rather than on the list.

        **Atomic per record, not across a run**, and every refusal here says so.
        :meth:`_write_one` publishes by rename, so a record is on disk whole or
        not at all; the records *before* the refused one are on disk and stay
        there, which is what the caller must be told rather than left to infer
        from a sentence that says "nothing was written".

        Returns:
            Each record's path relative to the review directory, in the order the
            records were given.

        Raises:
            ReviewEvidenceError: If two records in one call name one file -- a
                silently overwritten record is a lost one -- if a record would
                land larger than :meth:`read_all` will read back, if a symbolic
                link or another planted artefact sits where a record or its
                temporary belongs, if the directory cannot be written, or if the
                record cannot be turned into bytes at all. **Every one of them
                carries** :func:`_partial_landing`, appended here rather than
                written per site: a refusal knows what happened to *its* record
                and only this loop knows what happened to the run.
            PathEscapeError: If a record's derived path resolves outside the
                review directory, or reaches it through a route that leaves.
                Deliberately the one write-side refusal that does **not** gain
                that sentence: it is a containment refusal carrying its own
                remedy about where a path points, its own exit code, and no claim
                about a record at all, so rewording it here would relabel it as
                this store's. The residual is recorded rather than hidden -- an
                operator who plants an escaping link mid-run is told about the
                link and not about the records already landed.
        """
        landed: list[str] = []
        # Keyed by the folded path and valued by the spelling that claimed it, so
        # the refusal can name both: told only its own path, an operator on a
        # folding filesystem would go looking for a file spelled the other way.
        claimed: dict[str, str] = {}
        # One index per call rather than per record: see `OnDiskSpellings`.
        spellings = OnDiskSpellings(self._root)
        for record in records:
            try:
                relative = record.relative_path
                earlier = claimed.get(relative.casefold())
                if earlier is not None:
                    raise ReviewEvidenceError(
                        f"Two records in one ingestion run name one file: "
                        f"{record.kind.value} {bounded_quote(record.record_key)} of "
                        f"{bounded_quote(record.repository)} claims `{relative}`, and "
                        f"`{earlier}` was already written by this run.",
                        remedy=COLLISION_CURE,
                    )
                claimed[relative.casefold()] = relative
                self._write_one(record, relative, run, spellings)
            except ReviewEvidenceError as exc:
                raise ReviewEvidenceError(
                    f"{exc} {_partial_landing(len(landed))}", remedy=exc.remedy
                ) from exc
            except TheurianError:
                # A graded refusal that is not this store's -- containment's --
                # and it is re-raised whole for the reason the `Raises:` clause
                # above gives.
                raise
            except Exception as exc:
                raise self._landing_refusal(record, exc, landed=len(landed)) from exc
            landed.append(relative)
        return tuple(landed)

    def _landing_refusal(
        self, record: EvidenceRecord, exc: Exception, *, landed: int
    ) -> ReviewEvidenceError:
        """Grade whatever the landing of one record raised that was not graded.

        **The class name and never the exception's own text.** ``review ingest``
        is an operator surface that reports identities and counts and no evidence
        content, and ``repr`` of the measured member carries the *whole* string
        that failed to encode -- for a comment body that is the record's entire
        JSON document. ``type(exc).__name__`` locates the fault without
        publishing what the fault was in, which is the division
        ``_EXEMPT_EXPRESSIONS`` in ``tests/unit/test_review_ingest_refusals.py``
        already records for the same shape one package over.

        **The identity is quoted rather than echoed, and the reason written here
        for two rounds was false.** It said an echoed lone surrogate would raise
        a second ``UnicodeEncodeError`` out of ``cli.commands._fail``'s non-JSON
        branch. It would not: ``sys.stderr`` has carried
        ``errors="backslashreplace"`` since CPython 3.5, so that write cannot
        raise and renders a lone surrogate as ``\\ud800`` whichever helper
        produced it -- which is also why a test asserting ``\\ud800`` in the
        published text passed for ``bounded_echo`` too, and proved nothing.

        What :func:`~theurian.domain.review_ingest.bounded_quote` actually buys
        is the class ``cli/output.escape_terminal_controls`` does **not** cover.
        That function escapes C0, C1 and DEL; U+202E is none of the three, so an
        echoed one reaches the terminal raw and reorders every line printed
        around it. ``repr`` renders it as ``\\u202e``. A record key and a
        repository are both provider-chosen, so both are quoted.
        """
        return ReviewEvidenceError(
            f"{record.kind.value} {bounded_quote(record.record_key)} of "
            f"{bounded_quote(record.repository)} could not be turned into the bytes of a "
            f"file: {type(exc).__name__}. {_partial_landing(landed)}",
            remedy=unwritable_record_cure(record.anchor.source_uri),
        )

    def read_all(self) -> tuple[StoredRecord, ...]:
        """Every record on disk, in a total order.

        The reading is
        :class:`~theurian.infrastructure.review_evidence.reader.EvidenceReader`'s,
        and so is everything this method used to say about it: what one
        unreadable file costs the whole run, and why the read seam is keyed on
        the complement of ``TheurianError`` rather than on a list of families.
        """
        return EvidenceReader(self._root).read_all()

    def _write_one(
        self,
        record: EvidenceRecord,
        relative: str,
        run: IngestionRun,
        spellings: OnDiskSpellings,
    ) -> None:
        """Write one record, having proved its path first.

        The order is the guard: containment runs before the directory is created,
        so a planted ``review/<hash> -> /elsewhere`` is refused rather than filled
        in. ``resolve_within_root`` answers where the path points,
        ``assert_no_symlink_escape`` answers how it got there, and
        :meth:`_refuse_a_relocated_directory` answers the shape neither sees.

        **The write targets the *unresolved* join, and that is not a style
        choice.** ``resolve_within_root``'s answer has already replaced a leaf
        symlink with its destination, so opening *that* leaves ``O_NOFOLLOW``
        nothing to refuse: the first cut of this method wrote through a planted
        ``<leaf> -> review/decoy.txt`` at exit 0 and truncated the decoy, which is
        ``read_source_file``'s "pass the path as the caller wrote it" lesson
        arriving on the write side. The resolved form is kept for the containment
        proof and is deliberately not the thing opened.

        **The record is published by rename, not by truncation.** The bytes go to
        a sibling ``.writing`` file inside the same proved directory and
        ``os.replace`` moves it over the record -- the discipline the active
        pointer already uses. What it buys here is different from what it buys
        there: a pointer interrupted mid-write is rebuilt, while an evidence file
        interrupted mid-write is gone, because the upstream comment it copied may
        already have been edited or deleted (ADR-0030 decision 3). An
        ``O_TRUNC`` open followed by a crash left an empty file where the
        previous copy had been; a rename leaves the previous copy whole.

        A leftover ``.writing`` file is invisible to :meth:`read_all`, whose walk
        selects on
        :data:`~theurian.infrastructure.review_evidence.layout.EVIDENCE_SUFFIX`,
        so an interruption cannot make a half-written document read back as a
        record either.

        **What the rename survives is a process death, not a power loss**, and
        the difference is recorded here rather than closed (round two). Nothing
        ``fsync``s the temporary before the rename or the directory after it, so
        on a crash or a power cut the filesystem may have the rename and not the
        bytes -- a zero-length or truncated record where the previous copy had
        been, which is exactly the outcome the rename was adopted to prevent, one
        failure mode further out. It is recorded rather than fixed because the
        cure is two ``fsync`` calls *per record* on a path that writes one file
        per thread, per submission and per pull request, and nobody has measured
        what that costs on a 500-pull-request window. Closing it means measuring
        first; claiming it is closed without measuring is what this paragraph
        exists to stop.

        **``O_NOFOLLOW`` now guards the temporary, so the record's own leaf gets
        its own check.** ``rename(2)`` operates on the link rather than through
        it, so a link planted at the record's name is *replaced* and whatever it
        pointed at is never opened -- the property ``planted_link_cure`` states
        holds by construction now rather than by the open refusing. The refusal
        is kept because an operator whose evidence path is a symbolic link needs
        to be told, and it is an ``lstat`` immediately before the rename: losing
        that race costs the report and destroys nothing, which is the opposite of
        the truncation the same race used to cost.

        **Moving the open moved which artefact each refusal is about, and the
        sentences stayed behind** (round two, R2-B). Two paths are now touched
        here and they are two different things to an operator: the temporary,
        which this store creates and renames away inside this call and which
        holds no evidence, and the record, which is the source and has no
        rebuild. So the failure handling is split by *which step failed* rather
        than left as one arm reading the errno:

        * ``mkdir`` and the temporary's own open are :meth:`_temporary_refusal`'s,
          and it names ``<record>.writing``. Publishing the record's cure here
          told an operator to delete a **landed** evidence file over a link
          planted at the temporary beside it -- the one instruction this package
          must never publish, and it did so while having already removed the
          plant it was describing.
        * the rename is :meth:`_publish`'s, and its refusals name the record.

        **The cleanup removes a regular file and nothing else.** The plants that
        make the open refuse -- a symbolic link, a named pipe, a socket, a device
        -- are exactly the shapes that are not regular files, and the open
        declines *before* creating anything when it meets one, so an unlink there
        deletes the operator's own artefact and hides the plant the refusal is
        about. An ``lstat`` in front of it is what tells the two apart; what it
        still cannot tell apart is a regular file somebody planted at that name
        from litter an interrupted run left, and the second is what the name is
        for.

        **The writer's cap is the reader's cap, imported rather than restated.**
        :meth:`read_all` reads through ``read_source_file``, which refuses a file
        above ``MAX_SOURCE_FILE_BYTES`` (SEC-8), and an unbounded writer in front
        of a bounded reader lands a record no later run can read: every
        subsequent ``review ingest`` then refuses the whole corpus before it
        fetches anything. The record's own caps do not close this -- the adapter
        allows 100 comments of GitHub's own 65,536-character limit, which in
        CJK is roughly 19 MB in one thread -- so the size is measured on the
        bytes that would land and refused before the ``open``.

        Raises:
            ReviewEvidenceError: For every fault at this seam, naming the path
                that was actually opened.
            PathEscapeError: If the derived path leaves the review directory.
        """
        resolve_within_root(self._root, PurePosixPath(relative))
        assert_no_symlink_escape(self._root, base=self._root, requested=PurePosixPath(relative))
        self._refuse_a_relocated_directory(relative)
        self._refuse_a_folded_spelling(relative, spellings)
        target = self._root / PurePosixPath(relative)
        writing = self._root / PurePosixPath(f"{relative}{_WRITING_SUFFIX}")
        document = _document(record, run)
        landing = len(document.encode("utf-8"))
        if landing > MAX_SOURCE_FILE_BYTES:
            raise ReviewEvidenceError(
                f"{record.kind.value} {bounded_quote(record.record_key)} of "
                f"{bounded_quote(record.repository)} would land as {landing} bytes, and "
                f"a review evidence file is read back through a {MAX_SOURCE_FILE_BYTES}-byte "
                "limit, so it was refused before the write.",
                remedy=oversized_record_cure(record.anchor.source_uri),
            )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            write_text_without_following_a_link(writing, document)
        except BaseException as exc:
            # `BaseException`, because an interrupt between the open and the
            # return leaves the same litter an error does.
            self._discard_the_temporary(writing)
            if isinstance(exc, OSError):
                raise self._temporary_refusal(relative, writing, exc) from exc
            raise
        try:
            self._publish(writing, target, relative)
        except BaseException as exc:
            self._discard_the_temporary(writing)
            if isinstance(exc, OSError):
                raise ReviewEvidenceError(
                    f"`{relative}` could not be published under the review directory: "
                    f"{exc.strerror or 'the rename was refused'}. Whatever was already at that "
                    f"path is unchanged.",
                    remedy=UNWRITABLE_CURE,
                ) from exc
            raise

    def _discard_the_temporary(self, writing: Path) -> None:
        """Remove the temporary this write opened, and never something else.

        The ``lstat`` is the whole of it. Every refusal that brings us here may
        be *about* the path being something this store did not create -- a
        symbolic link, a named pipe, a socket -- and the open declines those
        before it creates anything, so an unlink would delete an operator's
        artefact and leave the refusal describing something that is no longer
        there. Only a regular file is discarded, which is the only shape this
        store's own open leaves behind.

        ``suppress(OSError)``, because the faults that reach this arm make the
        ``lstat`` and the ``unlink`` fail too: an ordinary file where the kind
        directory belongs answers ``ENOTDIR`` to the ``mkdir``, the ``lstat`` and
        the ``unlink`` alike. Removing litter must not change what the caller is
        told.
        """
        with suppress(OSError):
            if stat.S_ISREG(os.lstat(writing).st_mode):
                writing.unlink()

    def _temporary_refusal(self, relative: str, writing: Path, exc: OSError) -> ReviewEvidenceError:
        """Grade a fault at the record's ``.writing`` temporary, naming that path.

        **Keyed on what is at the path, not on the errno or the exception class**,
        and that is the correction round two forced. Keying on
        ``IrregularArtefactError`` is keying on the *descriptor's* answer, which
        exists only where the open succeeded -- true of a named pipe with a
        reader attached and false of every other planted shape: a socket refuses
        the open outright with ``ENOTSUP`` (measured on macOS 26.6), a
        reader-less pipe with ``ENXIO``, a directory with ``EISDIR``. All of
        those fell into the general arm and published ``UNWRITABLE_CURE``, which
        tells an operator to check a **permission** over an artefact no
        permission explains.

        So :func:`_planted_shape` is asked directly, and the errno is used for
        the one answer it gives without a race: ``O_NOFOLLOW``'s ``ELOOP`` is the
        kernel's verdict about *this* call, while an ``lstat`` afterwards
        describes whatever is there now. The residual is that a plant removed
        between the two falls back to the errno sentence -- correct about the
        failure, silent about the cause -- which costs a better sentence rather
        than a guarantee.

        **The shape reaches the cure and not only the message**, which is the
        defect this seam carried on its own. ``planted_temporary_cure`` closes
        with "removing it loses nothing" -- true of the link, the pipe and the
        socket this arm was written for, false of a **directory** holding an
        operator's files, and a directory is one of the shapes
        :func:`_planted_shape` answers. The routing lives in the cure rather than
        here, so this site chooses no sentence at all.
        """
        opened = f"{relative}{_WRITING_SUFFIX}"
        shape = (
            "a symbolic link" if is_a_symbolic_link_refusal(exc) else _planted_shape(exc, writing)
        )
        if shape is not None:
            return ReviewEvidenceError(
                f"`{opened}`, the temporary this write opens before it publishes "
                f"`{relative}`, is {shape} rather than a regular file.",
                remedy=planted_temporary_cure(opened, shape),
            )
        return ReviewEvidenceError(
            f"`{opened}`, the temporary this write opens before it publishes "
            f"`{relative}`, could not be written under the review directory: "
            f"{exc.strerror or 'the write was refused'}.",
            remedy=UNWRITABLE_CURE,
        )

    def _publish(self, writing: Path, target: Path, relative: str) -> None:
        """Move the written temporary over the record, refusing a planted leaf.

        The rename is what makes the write atomic; the two checks in front of it
        are what keep the refusals an operator used to get from the
        ``O_NOFOLLOW`` open, which now guards the temporary instead. They answer
        different questions from the rename, so they are here: ``os.replace``
        would silently replace a planted link or a planted pipe -- destroying
        nothing, since it follows neither -- and a store that silently repaired a
        planted evidence path would leave the operator with no reason to look at
        how it got there.

        **The shape check is a restoration rather than an addition** (round two,
        R2-B). Moving the open to the temporary took ``O_NOFOLLOW``'s and
        ``assert_a_regular_file``'s answers off the *leaf* with it, so a named
        pipe planted at a record's own path stopped being refused and was
        replaced at exit 0 -- while the ``Raises:`` clause on :meth:`write` went
        on promising a refusal there. One ``lstat`` answers both questions, and
        it is taken immediately before the rename for the reason the link check
        is: losing that race costs the report and destroys nothing.

        **Graded here rather than raised as an ``IrregularArtefactError``**, and
        the clause that said otherwise is gone with the behaviour it described.
        That class is an ``OSError`` and deliberately not a ``TheurianError``,
        which is exactly the family ``theurian review ingest``'s handler does not
        catch: raising one from this seam would end the run with a traceback and
        break the observable ``review_ingest_service.py`` states. Its own
        vocabulary is kept -- :func:`shape_that_is_not_a_regular_file` names the
        shape, and the cure names it back -- so nothing is lost but the
        ungraded type.

        **A directory gets a different cure from the other shapes, and this site
        no longer decides that.** ``planted_artefact_cure`` closes with "removing
        it loses nothing", which is true of a pipe, a socket and a device node
        and false of a directory: that one holds other names, and the ones under
        it may be an operator's own files. The split used to be a ``S_ISDIR``
        branch *here*, and one seam over -- ``_temporary_refusal``, which has no
        such branch -- the same sentence shipped over a directory holding a file
        somebody wrote. The shape now goes to the cure and the cure routes it,
        so a seam that never heard of the split still gets it right.

        Raises:
            ReviewEvidenceError: If a symbolic link sits at the record's own
                path, or a named pipe, socket, device or directory does.
        """
        if target.is_symlink():
            raise ReviewEvidenceError(
                f"A symbolic link sits where `{relative}` belongs.",
                remedy=planted_link_cure(relative),
            )
        try:
            mode = os.lstat(target).st_mode
        except FileNotFoundError:
            # The ordinary case -- a record landing for the first time -- and the
            # only errno that means "nothing is in the way". Every other one is a
            # fault the caller's own `OSError` arm grades.
            mode = None
        if mode is not None and (shape := shape_that_is_not_a_regular_file(mode)) is not None:
            raise ReviewEvidenceError(
                f"`{relative}` is {shape} rather than a regular file, so the record was "
                f"not published over it.",
                remedy=planted_artefact_cure(relative, shape),
            )
        os.replace(writing, target)  # noqa: PTH105 - os.replace is the atomic primitive

    def _refuse_a_relocated_directory(self, relative: str) -> None:
        """Refuse a record whose *directory* is a link, however contained it is.

        ``O_NOFOLLOW`` constrains the final component only, and both path guards
        wave through a directory link whose target is inside the tree -- the bound
        recorded as [#577](https://github.com/theurian/theurian/issues/577), where
        ``.theurian/cache -> ../docs`` was measured relocating the ingestion
        manifest at exit 0. **Review evidence is the first artefact behind that
        bound that is not rebuildable**: everything #577 enumerates is derived
        state (ADR-0004) that a later run recreates, and an evidence file is the
        source (ADR-0030 decision 3), so a relocated write is a record filed
        under a repository's directory that is not that repository's.

        The closure #577 owes is ``openat`` against a directory descriptor at
        every level, which nothing in this codebase does. This is the cheap half
        of it and is not that: ``resolve_within_root`` already answers where the
        directory *points*, so comparing its answer against the plain join says
        whether anything on the way was a link, for two ``resolve()`` calls and
        no new primitive. The root is resolved on both sides, so a symlinked
        ``/tmp`` -- macOS ships one -- is not a false refusal.

        It runs **after** the other two guards on purpose. A link that leaves the
        tree, and one that leaves and returns, are already refused by them with
        their own containment messages; what reaches here is the in-root shape
        alone, which is the one nothing else sees.
        """
        parent = PurePosixPath(relative).parent
        resolved = resolve_within_root(self._root, parent)
        if resolved != self._root.resolve() / parent:
            raise ReviewEvidenceError(
                f"A directory on the way to `{relative}` is a symbolic link, so the "
                "record would have been written somewhere other than where it belongs.",
                remedy=relocated_directory_cure(str(parent)),
            )

    def _refuse_a_folded_spelling(self, relative: str, spellings: OnDiskSpellings) -> None:
        """Refuse a record whose path the disk already spells another way.

        The write half of round two's R2-C, and it runs **before** the ``mkdir``
        for the reason the containment guards run before it: once a directory has
        been created into, or a rename has landed, the record is already under a
        name this build did not choose and every later read is looking somewhere
        else.

        It covers the record's three components with one rule, because the
        population is *every* component of that path and not the two a reviewer
        happened to plant: the repository hash, the kind directory and the leaf
        all reach the filesystem through calls that resolve a name, and a rule
        written for the two directories would have left ``42.JSON`` -- measured
        swallowing a record's bytes and keeping its own spelling -- outside it.

        **The temporary is a fourth derived name, and it was outside the rule.**
        :meth:`_write_one` builds two paths under the root, not one, and the
        ``.writing`` sibling reaches the filesystem through an ``open`` that
        creates and truncates. Measured on APFS with a regular
        ``42.json.WRITING`` planted beside the derived name: the open resolved to
        the operator's file, truncated it, wrote the record into it and renamed
        it away, at exit 0, with nothing said. So the loop below ranges over the
        derived names ``_write_one`` actually constructs, and
        ``tests/unit/test_review_evidence_path_case.py::test_the_guard_covers_every_path_the_write_builds_under_the_root``
        recomputes both sides from this file's own syntax rather than comparing
        against a list somebody keeps in step.

        The record's own path is checked first, so a run that would collide on
        both is told about the record rather than about a temporary the operator
        has never seen.
        """
        for spelling in (relative, f"{relative}{_WRITING_SUFFIX}"):
            found = spellings.differently_spelled(spelling)
            if found is None:
                continue
            on_disk, derived = found
            raise ReviewEvidenceError(
                f"`{spelling}` cannot be written: `{on_disk}` is already on disk where "
                f"this build derives `{derived}`, and the two differ only in case. On a "
                f"filesystem that folds case they are one name, so the write would open "
                f"the file that is there rather than the one it derived.",
                remedy=folded_component_cure(on_disk, derived),
            )


def _payload_to_json(payload: EvidencePayload) -> dict[str, Any]:
    """One payload as the object that lands, keyed by its own type.

    A ``match`` rather than a table keyed on
    :class:`~theurian.infrastructure.review_evidence.layout.EvidenceKind`: mypy
    refuses a non-exhaustive one, so a fourth payload type cannot reach the writer
    without an encoder, and no ``Any``-typed callable table is needed to express
    it.
    """
    match payload:
        case ReviewEvent():
            return event_to_json(payload)
        case ReviewSubmission():
            return submission_to_json(payload)
        case ReviewThread():
            return thread_to_json(payload)


def _document(record: EvidenceRecord, run: IngestionRun) -> str:
    """One record as the bytes that land, newline-terminated.

    ``ensure_ascii=False`` so a Japanese comment body is stored as itself rather
    than as escape sequences -- these files are read by people in a diff, and the
    corpus this product is built for is not English-only.
    """
    document: dict[str, Any] = {
        "formatVersion": EVIDENCE_FORMAT_VERSION,
        "kind": record.kind.value,
        "provider": record.provider,
        "repository": record.repository,
        "recordKey": record.record_key,
        "sourceAnchor": anchor_to_json(record.anchor),
        "lastSeenRun": {
            "runId": run.run_id,
            "observedAt": run.observed_at.isoformat(),
        },
        "record": _payload_to_json(record.payload),
    }
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
