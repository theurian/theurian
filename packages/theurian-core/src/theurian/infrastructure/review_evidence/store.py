"""Landing review records as files, and reading them back (ADR-0030 decision 3).

The writer half is what an ingestion run calls once the gate has decided which
records may become files. The reader half is what slice 3's SQLite serving store
is built from; it ships now so that "the evidence files are the source" is a
property something already exercises rather than a sentence waiting on a
consumer. **The store-rebuild half of ADR-0030's owed test 4 -- delete the derived
store, rebuild, and the served content reproduces -- completes in slice 3, where
there is a store to delete.** What is held here is the half that does not need
one: write, read back, and get the same records.

**Refetch never deletes** (decision 3). :meth:`ReviewEvidenceStore.write` writes
the records it is given and touches no record it was not given: it never
enumerates, never diffs, and the only path it unlinks is the ``.writing``
temporary its own write opened, so a record whose upstream comment has been
deleted keeps its file and keeps the stamp of the last run that saw it. That is
the absence of any code that could do otherwise rather than a policy this class
applies, which is why the property survives an edit that adds a record kind.
``tests/unit/test_review_evidence_store.py::test_a_record_upstream_no_longer_returns_survives_the_refetch``
is what fails when it stops holding, and
``tests/unit/test_adr_0030_claims.py::test_the_evidence_package_moves_a_file_only_where_the_publish_records_it``
is what fails when a second removal appears in this package.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Final, final

from theurian.domain.errors import (
    DomainError,
    InvariantViolationError,
    PathEscapeError,
    SecurityError,
)
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review import ReviewEvent, ReviewSubmission, ReviewThread
from theurian.domain.review_ingest import bounded_echo
from theurian.infrastructure.review_evidence.codec import (
    anchor_from_json,
    anchor_to_json,
    event_from_json,
    event_to_json,
    submission_from_json,
    submission_to_json,
    thread_from_json,
    thread_to_json,
)
from theurian.infrastructure.review_evidence.errors import ReviewEvidenceError
from theurian.infrastructure.review_evidence.layout import (
    EVIDENCE_FORMAT_VERSION,
    EVIDENCE_SUFFIX,
    EvidenceKind,
    record_path,
)
from theurian.infrastructure.review_evidence.run import IngestionRun
from theurian.security.no_follow import (
    is_a_symbolic_link_refusal,
    write_text_without_following_a_link,
)
from theurian.security.paths import (
    MAX_SOURCE_FILE_BYTES,
    assert_no_symlink_escape,
    read_source_file,
    resolve_within_root,
)

#: What a reader does about a file under ``.theurian/review/`` that this build
#: cannot read. It names the artefact -- the file, by its path relative to the
#: review directory -- and a command that shows how it got that way, because the
#: fault is in the bytes and the operator has to look at them.
_UNREADABLE_CURE: Final = (
    "Open the file the message names, under `.theurian/review/`, and compare it "
    "against what this build writes -- `git log -p -- .theurian/review` shows how it "
    "got that way if the directory is committed. Review evidence is the source and "
    "not a cache, so deleting the file is data loss rather than a rebuild: an "
    "upstream comment may already have been edited or deleted, and no refetch "
    "recovers it (ADR-0030 decision 3)."
)

#: What an operator does about two records in one run that name one file. Nothing
#: was overwritten and there is no file to open: the fault is in what the
#: provider answered with, so the cure is the query that shows it.
_COLLISION_CURE: Final = (
    "Report this against the provider adapter: two records naming one file is an "
    "answer no repository should give -- either one key returned twice, or two keys "
    "a case-insensitive filesystem cannot tell apart -- and writing the second over "
    "the first would discard evidence no refetch recovers. Run the same query by "
    "hand with `gh api graphql --hostname github.com` to see what the provider "
    "returned."
)

#: What a reader does about a directory it cannot write or list. The write is the
#: last step of an ingestion run, so the run is what gets repeated.
_UNWRITABLE_CURE: Final = (
    "Make `.theurian/review/` and the directory the message names readable and "
    "writable -- `ls -ld .theurian/review` prints the mode and the owner -- then run "
    "the ingestion again."
)

#: What the bytes are written to before ``os.replace`` publishes them over the
#: record. It deliberately does **not** end in :data:`EVIDENCE_SUFFIX`, so a file
#: an interrupted run left behind is skipped by
#: :meth:`ReviewEvidenceStore._relative_paths` rather than read as a record.
_WRITING_SUFFIX: Final = ".writing"

#: What a refusal says about a record whose repository could not be read out of
#: its own file. Written once because both halves of :meth:`_read_one` publish it
#: and the two must not drift into two different sentences.
_UNNAMED_REPOSITORY: Final = "whose own repository could not be read"


def _planted_link_cure(relative: str) -> str:
    """The cure for a symbolic link where an evidence file belongs.

    **Deliberately not ``no_follow.symbolic_link_remedy``**, whose every clause
    rests on a precondition this path does not satisfy: that text says the
    artefact is derived state (ADR-0004) "that Theurian recreates, so nothing
    authored is lost". Review evidence is the opposite -- the source, with no
    replayable origin (ADR-0030 decision 3) -- so publishing that sentence here
    would tell an operator a deleted file comes back when it does not.

    The path is named **relative to the review directory**, never absolutely: a
    remedy is text a caller may paste, and an absolute one carries the machine's
    home directory with it.
    """
    return (
        f"Remove the symbolic link at `.theurian/review/{relative}` and run the "
        f"ingestion again -- `ls -l .theurian/review/{relative}` prints where it "
        f"points. Nothing was written through it: unlike every other path Theurian "
        f"refuses a link at, this one is not derived state, so a write that followed "
        f"it would have truncated whatever it names and Theurian would recreate "
        f"neither."
    )


def _relocated_directory_cure(relative: str) -> str:
    """The cure for a symbolic link standing in for a directory of records.

    Named relative to the review directory for :func:`_planted_link_cure`'s
    reason, and it does not offer to remove anything: the directory the link
    points at may already hold records an earlier run wrote through it, and
    telling an operator to delete a link over evidence is the one instruction
    this module must never publish.
    """
    return (
        f"`ls -l .theurian/review/{relative}` prints where the link points. Move the "
        f"records it already holds back under `.theurian/review/` and replace the link "
        f"with a real directory, then run the ingestion again. Do not simply remove it: "
        f"review evidence is the source rather than derived state, so whatever it "
        f"points at is not something a refetch rebuilds (ADR-0030 decision 3)."
    )


def _repository_named_in(raw: bytes) -> str:
    """Which repository a failing file claims, when its bytes still say.

    The directory a record sits in is a **hash** of the provider and the
    repository, so a refusal that names only the path tells an operator with
    several repositories ingested nothing about which one to look at. The
    repository is inside the file, and for the failures that dominate -- a field
    the domain refuses, an identity that does not match its directory -- the file
    still parses, so it can be read out and said.

    Best-effort by construction: the cases where it cannot be read are exactly
    the ones where nothing could read it (bytes that are not UTF-8, a document
    that is not JSON, a file too large or too irregular to open at all), and the
    caller says so rather than guessing.
    """
    try:
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return _UNNAMED_REPOSITORY
    if not isinstance(document, dict):
        return _UNNAMED_REPOSITORY
    named = document.get("repository")
    if not isinstance(named, str) or not named.strip():
        return _UNNAMED_REPOSITORY
    return f"which names {bounded_echo(named)}"


def _oversized_record_cure(record: EvidenceRecord) -> str:
    """The cure for a record larger than the reader that has to read it back.

    Names the **upstream** conversation rather than a file, because there is no
    file: the refusal fires before the write, so there is nothing on disk to
    open. What the operator can act on is the review the record came from, and
    the anchor is the pointer to it -- echoed through
    :func:`~theurian.domain.review_ingest.bounded_echo`, because a source URI is
    a value the provider chose and a refusal must not carry a megabyte of it.
    """
    return (
        f"Look at the review this record came from -- `{bounded_echo(record.anchor.source_uri)}` "
        f"is the pull request, and `gh api graphql --hostname github.com` re-runs the "
        f"read by hand -- then shorten or split the conversation there. Nothing was "
        f"written: the size this refuses at is the one the reader enforces, so landing "
        f"the file would have produced a record every later run refuses to read, and "
        f"review evidence has no rebuild that could clear it (ADR-0030 decision 3)."
    )


#: The payload types one record may carry. Named once so the three functions that
#: switch on it are visibly ranging over the same population, and so mypy refuses
#: a ``match`` that stops covering it.
EvidencePayload = ReviewEvent | ReviewSubmission | ReviewThread

#: The directory names :meth:`ReviewEvidenceStore._relative_paths` walks, derived
#: from the enum rather than listed, so a fourth kind is walked by the change that
#: adds it.
_KIND_DIRECTORIES: Final = frozenset(kind.value for kind in EvidenceKind)


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """One record on its way to a file, or read back out of one.

    ``kind`` and ``record_key`` are **derived** from the payload rather than
    carried beside it. A stored ``kind`` is a field that can disagree with the
    object it labels -- and the label decides the directory, so a disagreement is
    a record filed where nothing looks for it.
    """

    #: The provider that answered, ``"github"`` today.
    provider: str
    #: The repository as the provider resolved it, ``owner/name``. Written inside
    #: the file and hashed into the directory name, **never joined into a path**:
    #: the published allowlist pattern accepts ``../..``, so a joined value leaves
    #: the directory while satisfying the contract (ADR-0030 decision 3).
    repository: str
    #: FR-S3's pointer back to the upstream object. Theurian writes it, which is
    #: why the ingestion scan does not read it (decision 3's third
    #: *Controlled by* value).
    anchor: SourceAnchor
    payload: EvidencePayload

    def __post_init__(self) -> None:
        if not self.provider:
            raise InvariantViolationError("EvidenceRecord.provider must not be empty")
        if not self.repository:
            raise InvariantViolationError("EvidenceRecord.repository must not be empty")
        if self.anchor.provider != self.provider:
            raise InvariantViolationError(
                f"EvidenceRecord names provider {self.provider!r} and carries an anchor "
                f"from {self.anchor.provider!r}. The anchor is the only pointer back to "
                "material Theurian cannot re-fetch, so one that names another provider "
                "is a record that cannot be traced."
            )

    @property
    def kind(self) -> EvidenceKind:
        """Which of the three record kinds this is, from the payload's own type."""
        match self.payload:
            case ReviewEvent():
                return EvidenceKind.PULL_REQUEST
            case ReviewSubmission():
                return EvidenceKind.REVIEW_SUBMISSION
            case ReviewThread():
                return EvidenceKind.REVIEW_THREAD

    @property
    def record_key(self) -> str:
        """The provider's own identifier for this record inside its repository.

        A pull request is keyed by its **number** rather than by a node id: the
        number is what a reviewer types, it is unique inside the repository the
        directory already names, and it makes the landed tree readable to whoever
        opens the diff. A submission and a thread are keyed by their node id,
        which is the only identifier the provider gives them.
        """
        match self.payload:
            case ReviewEvent():
                return str(self.payload.number)
            case ReviewSubmission() | ReviewThread():
                return self.payload.external_id

    @property
    def relative_path(self) -> str:
        """Where this record lives, relative to ``.theurian/review/``."""
        return record_path(
            provider=self.provider,
            identity=self.repository,
            kind=self.kind,
            provider_id=self.record_key,
        )


@dataclass(frozen=True, slots=True)
class StoredRecord:
    """One record as it sits on disk, with the run that last observed it."""

    record: EvidenceRecord
    last_seen: IngestionRun
    #: Where it was read from, relative to the review directory and POSIX-spelled,
    #: so a report of what a run found reads identically on every platform.
    relative_path: str


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

        Returns:
            Each record's path relative to the review directory, in the order the
            records were given.

        Raises:
            ReviewEvidenceError: If two records in one call name one file -- a
                silently overwritten record is a lost one -- if a record would
                land larger than :meth:`read_all` will read back, if a symbolic
                link sits where a record belongs, or if the directory cannot be
                written.
            PathEscapeError: If a record's derived path resolves outside the
                review directory, or reaches it through a route that leaves.
            IrregularArtefactError: If a named pipe, socket or device sits at the
                leaf. Not translated: it names the artefact and its shape, and
                re-labelling it as a write fault would send the operator to look
                at a permission.
        """
        landed: list[str] = []
        # Keyed by the folded path and valued by the spelling that claimed it, so
        # the refusal can name both: told only its own path, an operator on a
        # folding filesystem would go looking for a file spelled the other way.
        claimed: dict[str, str] = {}
        for record in records:
            relative = record.relative_path
            earlier = claimed.get(relative.casefold())
            if earlier is not None:
                raise ReviewEvidenceError(
                    f"Two records in one ingestion run name one file: "
                    f"{record.kind.value} {record.record_key!r} of {record.repository!r} "
                    f"claims `{relative}`, and `{earlier}` was already written by this run.",
                    remedy=_COLLISION_CURE,
                )
            claimed[relative.casefold()] = relative
            self._write_one(record, relative, run)
            landed.append(relative)
        return tuple(landed)

    def read_all(self) -> tuple[StoredRecord, ...]:
        """Every record on disk, in a total order.

        Sorted by relative path, which is total because no two records share one
        (:meth:`write` refuses a collision). A caller building a derived store
        from these -- slice 3's -- therefore sees the same sequence on every
        machine.

        **One unreadable file stops every repository's ingest, and that is the
        chosen behaviour rather than an oversight.** This reads the whole
        directory, so ``review ingest`` on repository B refuses before it fetches
        anything when a single file under repository A's hashed directory cannot
        be read -- ``ReviewIngestService`` calls this to learn what is already
        landed. The alternative is to skip the file, and a skipped file is a
        corpus quietly smaller than the disk: the run would report *kept* counts
        computed over a set the operator cannot see, and slice 3's derived store
        would be built from that same partial set. Evidence has no rebuild that
        would later correct it, so a refusal an operator must act on is the
        smaller harm than a silent omission nobody is told about. What makes the
        blast radius payable is the message: it names the file **and the
        repository the file itself claims**, because the directory is a hash and
        says neither.

        Raises:
            ReviewEvidenceError: If a file under the review directory is not a
                record this build can read: the wrong format version, not JSON, a
                field of the wrong shape, a field the *domain* refuses (a thread
                with no comments, a submission whose state is blank, an
                identifier of the wrong form), a record whose own identity does
                not match where it sits, a file above the reader's own size limit
                or one that is not a regular file, or a file the filesystem
                refuses to hand over. Refused rather than skipped -- a run that
                ignored a file it could not parse would report a corpus smaller
                than the one on disk and give no reason.
            PathEscapeError: If a file's path leaves the review directory. Passed
                through rather than translated: it carries its own remedy about
                *where the path points*, which is not a fault in the bytes.
        """
        return tuple(self._read_one(relative) for relative in sorted(self._relative_paths()))

    def _relative_paths(self) -> list[str]:
        """Every ``.json`` leaf exactly two directories below the review root.

        Two levels exactly, because that is the layout
        :func:`~theurian.infrastructure.review_evidence.layout.record_path`
        writes: repository, kind, leaf. Anything at another depth, or under a
        directory that is not one of the record kinds, is left alone rather than
        read -- a project may keep a ``README`` beside its evidence, and refusing
        one would make the directory this product's rather than the project's.
        """
        if not self._root.is_dir():
            return []
        try:
            # `kind_directory` rather than `kind`: the name `kind` is an
            # `EvidenceKind` everywhere else in this package, and a `Path` bound
            # to it here made `kind.name` read as the enum's member name when it
            # is the directory's.
            return [
                f"{repository.name}/{kind_directory.name}/{leaf.name}"
                for repository in self._root.iterdir()
                if repository.is_dir()
                for kind_directory in repository.iterdir()
                if kind_directory.is_dir() and kind_directory.name in _KIND_DIRECTORIES
                for leaf in kind_directory.iterdir()
                if leaf.name.endswith(EVIDENCE_SUFFIX)
            ]
        except OSError as exc:
            raise ReviewEvidenceError(
                "The review directory could not be listed: "
                f"{exc.strerror or 'the read was refused'}.",
                remedy=_UNWRITABLE_CURE,
            ) from exc

    def _write_one(self, record: EvidenceRecord, relative: str, run: IngestionRun) -> None:
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
        selects on :data:`EVIDENCE_SUFFIX`, so an interruption cannot make a
        half-written document read back as a record either.

        **``O_NOFOLLOW`` now guards the temporary, so the record's own leaf gets
        its own check.** ``rename(2)`` operates on the link rather than through
        it, so a link planted at the record's name is *replaced* and whatever it
        pointed at is never opened -- the property ``_planted_link_cure`` states
        holds by construction now rather than by the open refusing. The refusal
        is kept because an operator whose evidence path is a symbolic link needs
        to be told, and it is an ``lstat`` immediately before the rename: losing
        that race costs the report and destroys nothing, which is the opposite of
        the truncation the same race used to cost.

        **The writer's cap is the reader's cap, imported rather than restated.**
        :meth:`read_all` reads through ``read_source_file``, which refuses a file
        above ``MAX_SOURCE_FILE_BYTES`` (SEC-8), and an unbounded writer in front
        of a bounded reader lands a record no later run can read: every
        subsequent ``review ingest`` then refuses the whole corpus before it
        fetches anything. The record's own caps do not close this -- the adapter
        allows 100 comments of GitHub's own 65,536-character limit, which in
        CJK is roughly 19 MB in one thread -- so the size is measured on the
        bytes that would land and refused before the ``open``.
        """
        resolve_within_root(self._root, PurePosixPath(relative))
        assert_no_symlink_escape(self._root, base=self._root, requested=PurePosixPath(relative))
        self._refuse_a_relocated_directory(relative)
        target = self._root / PurePosixPath(relative)
        writing = self._root / PurePosixPath(f"{relative}{_WRITING_SUFFIX}")
        document = _document(record, run)
        landing = len(document.encode("utf-8"))
        if landing > MAX_SOURCE_FILE_BYTES:
            raise ReviewEvidenceError(
                f"{record.kind.value} {bounded_echo(record.record_key)} of "
                f"{bounded_echo(record.repository)} would land as {landing} bytes, and "
                f"a review evidence file is read back through a {MAX_SOURCE_FILE_BYTES}-byte "
                "limit, so it was not written.",
                remedy=_oversized_record_cure(record),
            )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            write_text_without_following_a_link(writing, document)
            self._publish(writing, target, relative)
        except BaseException as exc:
            # The temporary is this method's own litter and never a record, so
            # removing it must not change what the caller is told: `suppress`,
            # because the arm that discards it is reached by faults that make the
            # unlink fail too -- an ordinary file where the kind directory belongs
            # answers `ENOTDIR` to the `mkdir` and to the `unlink` alike.
            # `BaseException`, because an interrupt between the write and the
            # rename leaves the same litter an error does.
            with suppress(OSError):
                writing.unlink()
            if isinstance(exc, OSError):
                if is_a_symbolic_link_refusal(exc):
                    raise ReviewEvidenceError(
                        f"A symbolic link sits where `{relative}` belongs, so the record "
                        "was not written.",
                        remedy=_planted_link_cure(relative),
                    ) from exc
                raise ReviewEvidenceError(
                    f"`{relative}` could not be written under the review directory: "
                    f"{exc.strerror or 'the write was refused'}.",
                    remedy=_UNWRITABLE_CURE,
                ) from exc
            raise

    def _publish(self, writing: Path, target: Path, relative: str) -> None:
        """Move the written temporary over the record, refusing a link at its name.

        The rename is what makes the write atomic; the check in front of it is
        what keeps the refusal an operator used to get from the ``O_NOFOLLOW``
        open. They answer different questions, so both are here: ``os.replace``
        would silently replace a planted link (destroying nothing -- it never
        follows one), and a store that silently repaired a planted evidence path
        would leave the operator with no reason to look at how it got there.
        """
        if target.is_symlink():
            raise ReviewEvidenceError(
                f"A symbolic link sits where `{relative}` belongs, so the record was not written.",
                remedy=_planted_link_cure(relative),
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
                remedy=_relocated_directory_cure(str(parent)),
            )

    def _read_one(self, relative: str) -> StoredRecord:
        """Read one record back, translating every way its bytes can be wrong.

        **Three exception families reach this method and only one of them is a
        ``ValueError``**, which is what the first cut of these two catch clauses
        assumed. The codec refuses a value of the wrong *shape* with a
        ``ValueError``; the domain types it hands every value to refuse an
        impossible *record* with a ``DomainError``, which is not a subclass of
        one; and ``read_source_file`` refuses a file that is too large or is not
        a regular file with a ``SecurityError``, which is not either. All three
        say the same thing to an operator -- this file under
        ``.theurian/review/`` is not one this build can read -- and a landed
        file carrying any of them left ``review ingest`` as the traceback
        ADR-0030 clause 9 forbids, naming no path and offering no cure.

        The clauses key on those **families** rather than on their members, so a
        domain invariant or a security limit added later is graded by the change
        that adds it rather than by the next round that meets it. That is also
        why the decode is not named beside them: a file whose bytes are not UTF-8
        raises ``UnicodeDecodeError``, which *is* a ``ValueError``, and listing it
        would suggest the tuple were an enumeration of members.

        ``PathEscapeError`` is the one member deliberately re-raised. It already
        carries its own remedy, and it is not a fault in the bytes at all:
        re-labelling a containment refusal as an unreadable record would send
        the operator to inspect a file whose problem is where it points.

        **Every message names the repository the file claims**, or says it could
        not be read. The path alone is a hash of provider and repository, so on a
        project ingesting several it identifies the file without identifying what
        the file is about -- and this refusal is the one that stops every
        repository's run (:meth:`read_all`).
        """
        try:
            raw = read_source_file(self._root, PurePosixPath(relative))
        except PathEscapeError:
            raise
        except OSError as exc:
            raise ReviewEvidenceError(
                f"`{relative}`, {_UNNAMED_REPOSITORY}, was listed under the review "
                f"directory and could not be read: "
                f"{exc.strerror or 'the read was refused'}.",
                remedy=_UNREADABLE_CURE,
            ) from exc
        except SecurityError as exc:
            raise ReviewEvidenceError(
                f"`{relative}`, {_UNNAMED_REPOSITORY}, was listed under the review "
                f"directory and this build refused to read it: {exc}",
                remedy=_UNREADABLE_CURE,
            ) from exc

        try:
            return _stored(raw.decode("utf-8"), relative)
        except (ValueError, DomainError) as exc:
            raise ReviewEvidenceError(
                f"`{relative}`, {_repository_named_in(raw)}, is not a review evidence "
                f"record this build can read: {exc}",
                remedy=_UNREADABLE_CURE,
            ) from exc


def _payload_to_json(payload: EvidencePayload) -> dict[str, Any]:
    """One payload as the object that lands, keyed by its own type.

    A ``match`` rather than a table keyed on :class:`EvidenceKind`: mypy refuses a
    non-exhaustive one, so a fourth payload type cannot reach the writer without
    an encoder, and no ``Any``-typed callable table is needed to express it.
    """
    match payload:
        case ReviewEvent():
            return event_to_json(payload)
        case ReviewSubmission():
            return submission_to_json(payload)
        case ReviewThread():
            return thread_to_json(payload)


def _payload_from_json(kind: EvidenceKind, value: object, where: str) -> EvidencePayload:
    """One payload read back, keyed by the kind the document declares.

    :func:`_payload_to_json`'s mirror, and exhaustive for the same reason: the
    round trip is only a round trip while both halves cover the same set.
    """
    match kind:
        case EvidenceKind.PULL_REQUEST:
            return event_from_json(value, where)
        case EvidenceKind.REVIEW_SUBMISSION:
            return submission_from_json(value, where)
        case EvidenceKind.REVIEW_THREAD:
            return thread_from_json(value, where)


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


def _stored(text: str, relative: str) -> StoredRecord:
    """One record read back out of its own bytes.

    The last check is the one worth naming: the record's *derived* path is
    compared against where the file actually sits. A record moved between
    directories -- by hand, or by a build that keyed paths differently -- would
    otherwise read back as a record about a repository the directory does not
    name, and the derived store slice 3 builds would carry it under the wrong
    identity.

    Raises:
        ValueError: For every way the document can be the wrong shape. The caller
            turns these into a refusal naming the file, so no message here
            repeats the path.
    """
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError(f"the document is a {type(parsed).__name__}, not an object")
    document: dict[str, Any] = parsed

    version = document.get("formatVersion")
    if version != EVIDENCE_FORMAT_VERSION:
        raise ValueError(
            f"formatVersion is {version!r} and this build writes {EVIDENCE_FORMAT_VERSION}"
        )
    kind_value = document.get("kind")
    if not isinstance(kind_value, str) or kind_value not in _KIND_DIRECTORIES:
        raise ValueError(f"kind is {kind_value!r}, which is not a record kind this build writes")

    stamp = document.get("lastSeenRun")
    if not isinstance(stamp, dict):
        raise ValueError(f"lastSeenRun is a {type(stamp).__name__}, not an object")
    run: dict[str, Any] = stamp

    record = EvidenceRecord(
        provider=_required_string(document, "provider"),
        repository=_required_string(document, "repository"),
        anchor=anchor_from_json(document.get("sourceAnchor"), "sourceAnchor"),
        payload=_payload_from_json(EvidenceKind(kind_value), document.get("record"), "record"),
    )
    if record.relative_path != relative:
        raise ValueError(
            f"the record inside names `{record.relative_path}` and the file sits at "
            f"`{relative}`, so one of the two is not what this build wrote"
        )
    return StoredRecord(
        record=record,
        last_seen=IngestionRun(
            run_id=_required_string(run, "runId"),
            observed_at=_moment(run, "observedAt"),
        ),
        relative_path=relative,
    )


def _required_string(source: dict[str, Any], key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str):
        raise ValueError(f"`{key}` is a {type(value).__name__}, not a string")
    return value


def _moment(source: dict[str, Any], key: str) -> datetime:
    """One instant out of the run stamp, refused when it carries no zone."""
    text = _required_string(source, key)
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"`{key}` carries no time zone")
    return parsed
