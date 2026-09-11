"""Reading the landed evidence files back (ADR-0030 decision 3).

Split out of :mod:`theurian.infrastructure.review_evidence.store` when that
module passed this project's 800-line ceiling. The seam is the one that module's
own first paragraph already described -- the writer an ingestion run calls, and
the reader slice 3's SQLite serving store is built from -- and
:meth:`~theurian.infrastructure.review_evidence.store.ReviewEvidenceStore.read_all`
delegates here, so a caller still has both halves on one class.
"""

from __future__ import annotations

import json
import stat
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Final, final

from theurian.domain.errors import (
    DomainError,
    PathEscapeError,
    SecurityError,
    TheurianError,
)
from theurian.domain.review_ingest import bounded_quote
from theurian.infrastructure.review_evidence.codec import (
    anchor_from_json,
    event_from_json,
    submission_from_json,
    thread_from_json,
)
from theurian.infrastructure.review_evidence.cures import (
    MISPLACED_ROOT_CURE,
    UNNAMED_REPOSITORY,
    UNREADABLE_CURE,
    UNWRITABLE_CURE,
    folded_component_cure,
    repository_named_in,
)
from theurian.infrastructure.review_evidence.errors import ReviewEvidenceError
from theurian.infrastructure.review_evidence.layout import (
    EVIDENCE_FORMAT_VERSION,
    EVIDENCE_SUFFIX,
    EvidenceKind,
)
from theurian.infrastructure.review_evidence.records import (
    EvidencePayload,
    EvidenceRecord,
    StoredRecord,
)
from theurian.infrastructure.review_evidence.run import IngestionRun
from theurian.infrastructure.review_evidence.spellings import first_differing_component
from theurian.security.paths import read_source_file

#: The directory names :meth:`EvidenceReader.fingerprints` walks, derived from
#: the enum rather than listed, so a fourth kind is walked by the change that
#: adds it.
_KIND_DIRECTORIES: Final = frozenset(kind.value for kind in EvidenceKind)

#: What a listing records about one evidence file: ``(mtime_ns, size, whether it
#: is a regular file)``. Three values a ``stat`` answers, and nothing a parse
#: would -- see :meth:`EvidenceReader.fingerprints` for what it is compared for
#: and what it deliberately cannot tell apart.
#:
#: The application layer names this shape too
#: (``review_search_builder.EvidenceFingerprint``) and neither module imports the
#: other, because the composition root is where a port and its adapter meet
#: (ADR-0003). What keeps the two from drifting about *which slot is which* is
#: not a shared import but the binding itself: ``review_commands.
#: evidence_fingerprints`` is annotated with the application's alias and returns
#: this method, so a slot that changed type or arity on one side is a mypy error
#: at that line.
EvidenceFingerprint = tuple[int, int, bool]

#: The fingerprint of a leaf whose ``stat`` was refused: no ``mtime_ns``, no
#: size, and not a regular file.
#:
#: **Distinct from every fingerprint a real file can carry**, which is what makes
#: it safe to compare rather than special-case: ``st_mtime_ns`` and ``st_size``
#: are non-negative for anything that exists, so a leaf answering this at one
#: capture and a real triple at another is a *change* by the same equality test
#: every other transition goes through.
#:
#: Answered rather than raised because a dangling symbolic link under the review
#: directory would otherwise turn a whole listing into a refusal -- and that
#: listing runs under the project's write lock, where the honest answer is "this
#: leaf is not something to publish from" rather than "the corpus cannot be
#: read". :meth:`_read_one` is what refuses such a file, by name, on the read
#: side.
_UNSTATTABLE: Final[EvidenceFingerprint] = (-1, -1, False)


class _FoldedPathError(ValueError):
    """A file sits under a name that folds to the one this build derives.

    A ``ValueError`` so it stays inside the family
    :meth:`EvidenceReader._read_one` already grades: a caller that does not
    know about this class still publishes a refusal rather than a traceback. What
    knowing about it buys is the **cure** -- the fault is a directory name and
    not the bytes, so "open the file and compare it against what this build
    writes" would send the reader to inspect a document that is entirely correct.

    ``derived`` is the spelling this build would have written, carried on the
    exception because the caller has the on-disk one and needs both to name a
    rename.
    """

    def __init__(self, derived: str) -> None:
        self.derived = derived
        super().__init__(f"the record inside names `{derived}`, which differs only in case")


@final
class EvidenceReader:
    """The read half of the evidence files under one project's ``.theurian/review/``.

    Args:
        review_root: the same ``ProjectPaths.review``
            :class:`~theurian.infrastructure.review_evidence.store.ReviewEvidenceStore`
            is built on, already proved contained inside the project.
            :meth:`_read_one` resolves against it again through
            ``read_source_file``, for the reason that class's own ``Args`` gives.
    """

    def __init__(self, review_root: Path) -> None:
        self._root = review_root

    def read_all(self) -> tuple[StoredRecord, ...]:
        """Every record on disk, in a total order.

        Sorted by relative path, which is total because no two records share one
        (:meth:`ReviewEvidenceStore.write` refuses a collision). A caller building
        a derived store from these -- slice 3's -- therefore sees the same sequence
        on every machine.

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

        **This is the read seam, and it is keyed the way
        :meth:`ReviewEvidenceStore.write` is: on the complement of
        ``TheurianError``, not on a list of families.** The two
        arms below used to name ``(ValueError, DomainError)`` and
        ``(OSError, SecurityError)``, and a ``RecursionError`` -- a
        ``RuntimeError``, so outside both lists *and* outside the CLI's own
        ``except TheurianError`` -- came out of ``json.loads`` on a landed file of
        20,000 nested arrays and ended ``review ingest`` with a traceback and no
        document (measured). :meth:`_ungraded_read` is that key.

        The **class** is *an exception arm keyed on an enumeration rather than on
        the complement*, and its population was searched rather than reasoned
        about. The key, over this package and the two application modules and the
        command that consume it::

            git grep -n -P '^\\s+except ' -- \\
                packages/theurian-core/src/theurian/infrastructure/review_evidence/ \\
                packages/theurian-core/src/theurian/application/review_ingest_service.py \\
                packages/theurian-core/src/theurian/application/review_landing_gate.py \\
                packages/theurian-core/src/theurian/cli/review_commands.py

        That key is line-shaped, so this docstring's own mention of it is a
        self-hit and ``layout.py`` carries one more in prose; the count is
        therefore not pasted here. What is pasted is the *verdict per handler*,
        and it is pasted where it can fail:
        ``tests/unit/test_review_evidence_exception_keys.py`` re-runs the same
        population as an AST walk and reddens on a handler with no recorded
        verdict. Two members of that search were fixed rather than justified --
        this seam, and ``cures.repository_named_in``, whose parse is the failed
        one **repeated inside the arm grading it**, so the same plant raised a
        second ``RecursionError`` out of the refusal being composed.

        Raises:
            ReviewEvidenceError: If a file under the review directory is not a
                record this build can read: the wrong format version, not JSON, a
                field of the wrong shape, a field the *domain* refuses (a thread
                with no comments, a submission whose state is blank, an
                identifier of the wrong form), a record whose own identity does
                not match where it sits, a file above the reader's own size limit
                or one that is not a regular file, or a file the filesystem
                refuses to hand over, a document nested past the decoder's own
                recursion limit, **or a fault this build does not recognise at
                all** -- the last named by class rather than by message, since
                nothing graded it and so nothing bounded its text. Refused rather
                than skipped -- a run that ignored a file it could not parse would
                report a corpus smaller than the one on disk and give no reason.
            PathEscapeError: If a file's path leaves the review directory. Passed
                through rather than translated: it carries its own remedy about
                *where the path points*, which is not a fault in the bytes.
                :meth:`_read_one`'s ``except TheurianError`` arm re-raises on the
                same grounds, after the ``SecurityError`` and ``DomainError`` arms
                above it have taken their own members.
        """
        return tuple(self._read_one(relative) for relative in sorted(self.relative_paths()))

    def relative_paths(self) -> frozenset[str]:
        """Which ``.json`` leaves exist, without what :meth:`fingerprints` says about them.

        The membership view of the one walk, kept as its own name because that is
        all :meth:`read_all` asks: a ``frozenset`` because the strings are unique
        by construction anyway -- one directory cannot hold two entries of one
        name, so no composition of three of them repeats -- and :meth:`read_all`
        sorts it back into the total order it promises.

        **Derived from :meth:`fingerprints` rather than walking again**, which is
        the rule :data:`~theurian.application.review_search_builder
        .ListEvidenceFingerprints` states from the other side: two walks with
        their own opinions about which directories count would drop a live record
        the moment they drifted. Everything the walk decides -- the depth, the
        kind directories, the suffix, the case folding -- is stated once, there.

        Raises:
            ReviewEvidenceError: Whatever :meth:`fingerprints` raises, unchanged.
        """
        return frozenset(self.fingerprints())

    def fingerprints(self) -> Mapping[str, EvidenceFingerprint]:
        """Every ``.json`` leaf exactly two directories below the review root, stat'd.

        Two levels exactly, because that is the layout
        :func:`~theurian.infrastructure.review_evidence.layout.record_path`
        writes: repository, kind, leaf. Anything at another depth, or under a
        directory that is not one of the record kinds, is left alone rather than
        read -- a project may keep a ``README`` beside its evidence, and refusing
        one would make the directory this product's rather than the project's.

        **Public because a derived build has to ask what is on disk without
        reading it.**
        :meth:`~theurian.application.review_search_builder.ReviewSearchBuilder.build`
        reads the evidence outside the project's write lock and publishes inside
        it, so it calls this once on each side of the read and publishes only the
        records whose fingerprint did not move -- a record whose file was deleted,
        rewritten or replaced in between must not be republished from the copy the
        read took. This is the seam that makes that check ask the *same* walk
        :meth:`read_all` reads through, rather than a second enumeration with its
        own opinion about which directories count and which suffix is a record.

        **It opens no file and decodes nothing**, which is what makes it callable
        under that lock at all: the calls below that touch the filesystem are
        ``Path.exists``, ``Path.is_dir``, ``Path.iterdir`` and one ``Path.stat``
        per leaf -- every one of them an answer from the directory entry or the
        inode, and none of them a read of a byte of content.
        ``tests/unit/test_review_evidence_exception_keys.py`` records this
        method's ``OSError`` arm and :func:`_fingerprint`'s against that list.

        **``Path.stat`` per leaf rather than ``os.scandir``**, which looks like
        the cheaper spelling and is not on the platforms this ships to: CPython
        fills a ``DirEntry``'s ``is_dir``/``is_file`` from the directory entry's
        own ``d_type``, but ``DirEntry.stat()`` issues the same ``stat`` syscall
        ``Path.stat`` does everywhere except Windows -- and ``mtime_ns`` and
        ``size`` are exactly what no ``d_type`` carries. The walk therefore keeps
        the ``iterdir`` shape it already had, and the added cost is one ``stat``
        per evidence file against the parse per file :meth:`read_all` does.

        **What a fingerprint can and cannot tell apart.** ``(mtime_ns, size,
        is a regular file)`` distinguishes a file from anything that replaced it
        at the same path in every case this build's own writer can produce:
        ``ReviewEvidenceStore`` publishes by ``os.replace`` from a sibling
        temporary, so a refetch moves ``mtime_ns`` whatever it does to the bytes.
        Measured 2026-09-11 on APFS: five back-to-back rewrites of one path with
        *identical* bytes answered five distinct ``st_mtime_ns`` values
        (``…813929295``, ``…813994210``, ``…814066292``, ``…814131540``,
        ``…814195539``). What it cannot see is a same-size rewrite that lands
        inside one tick of a filesystem whose timestamps are coarser than the
        interval -- a one-second-resolution filesystem is where that is
        reachable -- and no content hash is taken here because hashing every
        landed file is a read, which is the thing this method exists not to do.

        **Finding folds; accepting does not** (round two, R2-C). Both selections
        here used to be byte comparisons against a filesystem that folds case, so
        a hand-made ``Pull-Request/`` beside the derived ``pull-request/`` was
        one directory to the disk and two names to this walk: every record the
        store wrote into it landed and then was **invisible** to every later
        read. A run reported ``new=1, kept=0`` on every invocation for ever, and
        slice 3's derived store would have been built from a corpus quietly
        smaller than the disk.

        Folding the two selections is deliberately *only* half the answer: it
        makes the file visible, and :func:`_stored` then refuses it by name
        because the spelling on disk is not the spelling this build derives. That
        split -- fold to find, byte-compare to accept -- is what keeps one
        spelling on disk without making this store accept a name it did not
        choose. The other half of the same rule is at the write, where
        :class:`OnDiskSpellings` refuses before a record can land into such a
        directory at all.

        Raises:
            ReviewEvidenceError: If the review directory cannot be listed, **or
                if something that is not a directory is standing where it
                belongs**. The second arm is the one an absent directory used to
                be answered by: a path that does not exist is an empty corpus,
                which is the honest answer before a first run, but a path that
                *does* exist and is a regular file is a corpus this build cannot
                enumerate and reporting it as empty published a store with no
                records in it and exit 0. The caller under the write lock
                publishes nothing in either case, which is the direction to fail
                in: a build that could not learn what is on disk and wrote anyway
                would be the one that reverts a deletion.
        """
        if not self._root.exists():
            # An absent review directory is an empty corpus -- the honest answer
            # before a first ingestion run, and the one `review build` must give
            # rather than refusing a project that has simply not ingested
            # anything. `exists` swallows its own `OSError`, so a root behind an
            # unreadable parent reads as absent here too; that is the same
            # residual `is_dir` carried before it.
            return {}
        if not self._root.is_dir():
            raise ReviewEvidenceError(
                "The review directory is not a directory: something else is standing "
                "at `.theurian/review`, so no evidence record can be listed.",
                remedy=MISPLACED_ROOT_CURE,
            )
        try:
            # `kind_directory` rather than `kind`: the name `kind` is an
            # `EvidenceKind` everywhere else in this package, and a `Path` bound
            # to it here made `kind.name` read as the enum's member name when it
            # is the directory's.
            return {
                f"{repository.name}/{kind_directory.name}/{leaf.name}": _fingerprint(leaf)
                for repository in self._root.iterdir()
                if repository.is_dir()
                for kind_directory in repository.iterdir()
                if kind_directory.is_dir() and kind_directory.name.casefold() in _KIND_DIRECTORIES
                for leaf in kind_directory.iterdir()
                if leaf.name.casefold().endswith(EVIDENCE_SUFFIX)
            }
        except OSError as exc:
            raise ReviewEvidenceError(
                "The review directory could not be listed: "
                f"{exc.strerror or 'the read was refused'}.",
                remedy=UNWRITABLE_CURE,
            ) from exc

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

        **Three families are still three names, and a name is an enumeration.**
        Each block therefore ends on the pair :meth:`ReviewEvidenceStore.write`
        ends on: a
        ``TheurianError`` arm that re-raises what something else already graded,
        and an ``Exception`` arm -- :meth:`_ungraded_read` -- that is the
        complement carrying the observable. The measured escape was a
        ``RecursionError`` from ``json.loads``, which none of the three names
        covers; the arms above buy the better *sentence* for their own members,
        and the complement is what buys the document.

        ``PathEscapeError`` is named ahead of them all rather than left to the
        ``TheurianError`` arm, because it must not be re-labelled by the
        ``SecurityError`` clause it would otherwise reach first: it carries its
        own remedy, and it is not a fault in the bytes at all, so grading it as
        an unreadable record would send the operator to inspect a file whose
        problem is where it points.

        **Every message names the repository the file claims**, or says it could
        not be read. The path alone is a hash of provider and repository, so on a
        project ingesting several it identifies the file without identifying what
        the file is about -- and this refusal is the one that stops every
        repository's run (:meth:`read_all`).

        :class:`_FoldedPathError` is caught **before** its own ``ValueError``
        base, and only to change the cure. It is not a fault in the bytes at all:
        the file parses, the record inside is whole, and the two paths differ by
        case alone -- so ``UNREADABLE_CURE``'s "open the file and compare it
        against what this build writes" sends the reader to inspect a document
        that is perfectly correct. What they have to act on is a directory name,
        and :func:`folded_component_cure` names both spellings because on a
        folding filesystem they reach one object.

        **The cure is handed a component pair, not the two whole paths.** It
        composes ``mv <on disk> <derived>``, and on a folding filesystem
        ``mv sha256-abc/Pull-Request/42.json sha256-abc/pull-request/42.json``
        renames a file onto itself -- the no-op the cure's own two-step note
        exists to warn about, published as the instruction. The write side has
        always passed a component, because :class:`OnDiskSpellings` finds one;
        :func:`first_differing_component` is what gives the read side the same
        shape.
        """
        try:
            raw = read_source_file(self._root, PurePosixPath(relative))
        except PathEscapeError:
            raise
        except OSError as exc:
            raise ReviewEvidenceError(
                f"`{relative}`, {UNNAMED_REPOSITORY}, was listed under the review "
                f"directory and could not be read: "
                f"{exc.strerror or 'the read was refused'}.",
                remedy=UNREADABLE_CURE,
            ) from exc
        except SecurityError as exc:
            raise ReviewEvidenceError(
                f"`{relative}`, {UNNAMED_REPOSITORY}, was listed under the review "
                f"directory and this build refused to read it: {exc}",
                remedy=UNREADABLE_CURE,
            ) from exc
        except TheurianError:
            # A graded refusal that is not this store's, re-raised whole for the
            # reason `PathEscapeError` above is: it carries its own remedy about
            # something other than these bytes.
            raise
        except Exception as exc:
            raise self._ungraded_read(relative, exc, None) from exc

        try:
            return _stored(raw.decode("utf-8"), relative)
        except _FoldedPathError as exc:
            on_disk, derived = first_differing_component(relative, exc.derived)
            raise ReviewEvidenceError(
                f"`{relative}`, {repository_named_in(raw)}, sits under a name that "
                f"differs only in case from the one this build derives, `{exc.derived}`. "
                f"On a filesystem that folds case those are one file, so the record is "
                f"reachable under a spelling nothing looks for.",
                remedy=folded_component_cure(on_disk, derived),
            ) from exc
        except (ValueError, DomainError) as exc:
            raise ReviewEvidenceError(
                f"`{relative}`, {repository_named_in(raw)}, is not a review evidence "
                f"record this build can read: {exc}",
                remedy=UNREADABLE_CURE,
            ) from exc
        except TheurianError:
            raise
        except Exception as exc:
            raise self._ungraded_read(relative, exc, raw) from exc

    def _ungraded_read(
        self, relative: str, exc: Exception, raw: bytes | None
    ) -> ReviewEvidenceError:
        """Grade whatever a read of one landed file raised that nothing else did.

        :meth:`ReviewEvidenceStore._landing_refusal`'s twin on the read side, and
        it exists for the same reason: the CLI's catch is ``except
        TheurianError``, so the
        population that ends a run with no document at all is precisely **the
        complement of that class**, and an arm keyed on a list of families cannot
        be that population. The measured member was a ``RecursionError`` out of
        ``json.loads`` -- a ``RuntimeError``, outside both ``ValueError`` and
        ``TheurianError`` -- but the arm is deliberately not written for it:
        :func:`_stored` catches that one at its own call for the *cure*, and this
        stays the key.

        **The class name and never the exception's own text**, exactly as
        :meth:`ReviewEvidenceStore._landing_refusal` argues: what reaches here was
        not graded by anything, so nothing bounds its ``str()`` and a landed file
        is up to
        ``MAX_SOURCE_FILE_BYTES`` of material this store may not publish.

        ``raw`` is the file's bytes where the read got that far and ``None``
        where it did not -- the bytes rather than a rendered clause, so the one
        interpolation here is an expression
        ``tests/unit/test_review_ingest_refusals.py`` can hold a *shape* against.
        Handing this method the clause instead put a bare ``repository`` name in
        front of that walk, which is the shape it caught a caller's ``limit``
        under.
        """
        return ReviewEvidenceError(
            f"`{relative}`, "
            f"{UNNAMED_REPOSITORY if raw is None else repository_named_in(raw)}, "
            f"was listed under the review directory and reading it back failed in a "
            f"way this build does not recognise: {type(exc).__name__}.",
            remedy=UNREADABLE_CURE,
        )


def _fingerprint(leaf: Path) -> EvidenceFingerprint:
    """One leaf's ``(mtime_ns, size, is a regular file)``, or :data:`_UNSTATTABLE`.

    ``Path.stat`` follows symbolic links, matching ``read_source_file``'s own
    ``stat``: what the third slot answers is therefore what the reader would
    *find* at that path, not what the directory entry is. A link to a regular
    file fingerprints as a regular file because that is what a read of it gets.

    The ``OSError`` arm is what keeps a dangling link from turning the whole
    listing into a refusal -- see :data:`_UNSTATTABLE`. It is deliberately the
    only per-leaf arm: an ``OSError`` raised by ``iterdir`` is about the
    *directory* and belongs to the caller's own arm, which names it.
    """
    try:
        status = leaf.stat()
    except OSError:
        return _UNSTATTABLE
    return (status.st_mtime_ns, status.st_size, stat.S_ISREG(status.st_mode))


def _payload_from_json(kind: EvidenceKind, value: object, where: str) -> EvidencePayload:
    """One payload read back, keyed by the kind the document declares.

    ``store._payload_to_json``'s mirror, and exhaustive for the same reason: the
    round trip is only a round trip while both halves cover the same set.
    """
    match kind:
        case EvidenceKind.PULL_REQUEST:
            return event_from_json(value, where)
        case EvidenceKind.REVIEW_SUBMISSION:
            return submission_from_json(value, where)
        case EvidenceKind.REVIEW_THREAD:
            return thread_from_json(value, where)


def _stored(text: str, relative: str) -> StoredRecord:
    """One record read back out of its own bytes.

    The last check is the one worth naming: the record's *derived* path is
    compared against where the file actually sits, **as bytes**. A record moved
    between directories -- by hand, or by a build that keyed paths differently --
    would otherwise read back as a record about a repository the directory does
    not name, and the derived store slice 3 builds would carry it under the wrong
    identity.

    **The comparison stays byte-wise on a filesystem that folds case, and that
    is the decision rather than the oversight it looks like** (round two, R2-C).
    Folding it would make this function *accept* a spelling this build did not
    derive, and the path it returns is an opaque key: ``ReviewIngestService``
    compares what :meth:`ReviewEvidenceStore.read_all` answers against what
    :meth:`ReviewEvidenceStore.write` answers to compute ``new``, ``updated`` and
    ``kept``, and slice 3's store is keyed on the same string. Two spellings for
    one record would make that arithmetic wrong wherever it was not folded too.
    Refusing keeps one spelling on disk and gives the operator a rename to
    perform; the difference is only ever *reported* differently, which is what
    :class:`_FoldedPathError` is for.

    **Two of these messages name a value out of the file, and both are bounded**
    (round two). ``formatVersion`` and ``kind`` are the only fields whose *value*
    is worth printing -- every other message names the field and the type it
    found -- and each arrives from a document whose only bound is
    ``MAX_SOURCE_FILE_BYTES``, so an 8 MiB ``kind`` string in a landed file
    became an 8 MiB refusal. ``ReviewEvidenceError`` has no cut of its own the way
    ``RefusalEnvelope`` does, so the cut has to happen here.

    Raises:
        _FoldedPathError: If the two paths differ by case alone. A ``ValueError``,
            so a caller that does not distinguish it still grades it.
        ValueError: For every other way the document can be the wrong shape,
            **including a document nested past the decoder's own recursion
            limit**. The caller turns these into a refusal naming the file, so no
            message here repeats the path.
    """
    try:
        parsed = json.loads(text)
    except RecursionError as exc:
        # `RecursionError` is a `RuntimeError` and so outside both `ValueError`
        # and `TheurianError`: a landed file of 20,000 nested arrays left
        # `read_all` uncaught and ended `review ingest` with a traceback and no
        # document at all (measured). Raised as the `ValueError` every other
        # shape fault here already is, which is the shape
        # `security/yaml_loading.py` and `parsers/openapi.py` already chose for
        # the identical call -- the caller's own key is the complement of
        # `TheurianError` now, so this arm buys the *cure* rather than the
        # grading: without it the refusal names a class instead of saying the
        # document is unreadably deep.
        raise ValueError("the document is nested too deeply to parse") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"the document is a {type(parsed).__name__}, not an object")
    document: dict[str, Any] = parsed

    version = document.get("formatVersion")
    if version != EVIDENCE_FORMAT_VERSION:
        raise ValueError(
            f"formatVersion is {bounded_quote(version)} and this build writes "
            f"{EVIDENCE_FORMAT_VERSION}"
        )
    kind_value = document.get("kind")
    if not isinstance(kind_value, str) or kind_value not in _KIND_DIRECTORIES:
        raise ValueError(
            f"kind is {bounded_quote(kind_value)}, which is not a record kind this build writes"
        )

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
        if record.relative_path.casefold() == relative.casefold():
            raise _FoldedPathError(record.relative_path)
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
