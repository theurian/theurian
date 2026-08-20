"""Packaging and accepting change proposals (ADR-0013 §4).

Two operations, and the boundary between them is the product:

``draft`` writes ``.theurian/proposals/<proposal-id>/`` -- a schema-valid,
directly applicable migration, the body in its native format, and the evidence a
reviewer reads. It writes nowhere else.

``accept`` moves those files into place. **It automates the file moves and not
the judgement**: it does not validate the change, does not apply it, and above
all does not approve it. Approval is a human merging a pull request, and there
is no code path here that stands in for one.

Both are driven by a composition root -- the CLI today, Milestone 7's
write-intent MCP tools next -- which is why the schema check arrives as an
injected callable rather than by importing the loader (ADR-0003).

The two moves ``accept`` performs are deliberately asymmetric, and that
asymmetry is the whole of what #89 measured:

* The **migration** file must never land on an existing name. Its name carries
  its ULID, so a collision means that migration is already in place. When two
  proposals both wrote ``migration.yaml``, the second acceptance replaced the
  first and reported nothing; validation then found one migration and applying
  it applied only that one, with the first change gone from the set and its body
  file left in ``.theurian/knowledge/`` with nothing pointing at it.
* The **body** file may replace what is at its ``contentFile`` path -- but only
  where no migration already in place pins those bytes. A body a migration
  references is immutable: the loader compares it against the pinned
  ``contentSha256`` on every load, so replacing it would make an applied
  migration's pin wrong and take the whole set out of ``migrate validate``. A
  generated proposal never reaches that case, because its ``contentFile`` carries
  a fresh revision id; a hand-authored one that reuses an existing body's path is
  refused, and the honest way to change a body is a new revision at a new path.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, NoReturn

import yaml

from theurian.application.project_service import ProjectPaths
from theurian.domain.enums import KnowledgeKind, KnowledgeStatus, Sensitivity, TrustLevel
from theurian.domain.errors import InputTooLargeError, PathEscapeError, TheurianError
from theurian.domain.identifiers import ItemId, MigrationId, ProposalId, RevisionId
from theurian.domain.knowledge import AUTHORED_IN_THEURIAN, SourceAnchor
from theurian.domain.migration import (
    MIGRATION_API_VERSION,
    CreateItem,
    Migration,
    UpsertRevision,
)
from theurian.domain.ports import Clock, IdGenerator
from theurian.domain.proposal import (
    Evidence,
    body_relative_path,
    is_generated_body_file_name,
    is_migration_file_name,
    kebab_slug,
    migration_file_name,
    require_evidence,
)
from theurian.domain.values import ContentHash, MediaType
from theurian.security.paths import (
    assert_no_symlink_escape,
    read_source_file,
    resolve_within_root,
)
from theurian.security.yaml_loading import load_yaml_mapping

#: The evidence file's name. Fixed, unlike the migration's: nothing moves it, so
#: it cannot collide with anything, and a reviewer looking for the reasoning
#: behind a proposal should not have to work out what it was called.
EVIDENCE_FILE: Final = "evidence.json"

#: How many proposal-directory names one refusal lists before it stops counting.
#: The directory is committed input, so its file count is the contributor's:
#: 50,000 files produced a 600 KB error string in 1.5 s before this bound.
_MAX_NAMES_LISTED: Final = 5

#: Checks a built migration document against the published JSON Schema, raising
#: on failure. Supplied by the composition root, because locating and reading
#: ``schemas/`` is an adapter's job (ADR-0003).
MigrationDocumentValidator = Callable[[Mapping[str, object]], None]

#: Returns an item's current revision in approved canonical state, or ``None`` if
#: it does not exist. Injected so the generator can require ``--expected-revision``
#: on a known item without opening the state database: the CLI derives it from
#: the loaded migration set (:func:`current_revision_in`), and Milestone 7's MCP
#: tools supply their own view of the same state.
CurrentRevisionLookup = Callable[[ItemId], RevisionId | None]

#: Returns the migration filed under an id in the project's approved set, or
#: ``None`` if none is. Injected -- ``MigrationSet.get`` from the *same*
#: ``MigrationSet`` ``resolve_context`` already loaded -- so the accept path reads
#: exactly the set ``migrate validate``/``apply`` read (keyed by inner id in
#: ``MigrationSet._by_id``) rather than re-detecting landed migrations from the
#: filesystem. That the loader is not imported here is ADR-0003, the same reason
#: the schema check and the current-revision lookup arrive this way.
LandedMigrationLookup = Callable[[MigrationId], Migration | None]


class ProposalError(TheurianError):
    """A proposal could not be drafted or accepted.

    Carries ``remedy`` for the same reason :class:`ProjectError` does: a caller
    reporting the failure must not have to infer the cure from the exception's
    type, and these failures have genuinely different cures -- an unknown
    proposal id, a migration already in place, a body file that is not there.
    """

    def __init__(self, message: str, *, remedy: str = "") -> None:
        self.remedy = remedy
        super().__init__(message)


class ChangeAlreadyInPlaceError(ProposalError):
    """``.theurian/migrations/`` already holds the migration this move would make.

    Its own family rather than a message a caller matches on: these are the
    refusals that say something about the project's *knowledge state* rather
    than about the invocation, so a caller reports them under the exit code it
    reserves for that, and a reworded message cannot silently move them.

    The two members differ in what they say about *this* proposal, and their
    remedies differ with them. :class:`ProposalAlreadyAcceptedError` says this
    proposal's own migration has landed, so drafting it again would duplicate a
    change already in history (#89). :class:`MigrationNameTakenError` says the
    *name* is taken, which is that same case seen from the other side or a
    genuinely different change that collided -- so its remedy sends the reader to
    read what is there first, and drafting again is right only in the second
    case. What they share, and what the exit code carries, is that neither is a
    refusal a caller may retry unchanged.
    """


class MigrationNameTakenError(ChangeAlreadyInPlaceError):
    """``.theurian/migrations/`` already holds a file of that name.

    The name carries the migration's id, so a collision means that migration is
    already in place. Reached with the proposal's own migration file still in its
    directory, which is what separates it from
    :class:`ProposalAlreadyAcceptedError`.
    """


class ProposalAlreadyAcceptedError(ChangeAlreadyInPlaceError):
    """This proposal's own migration has already been moved out of its directory.

    The evidence-only shape :func:`_no_migration_error` diagnoses: nothing is
    left to move, because a previous ``accept`` moved it.
    """


@dataclass(frozen=True, slots=True)
class ProposalRequest:
    """One proposed change, before any identifier or path has been chosen.

    Deliberately free of ids and paths: those are the service's to mint, so that
    an MCP tool and the CLI cannot disagree about them. ``expected_revision`` is
    the one field that changes the shape of the result -- present, this is an
    update and the migration states which revision it replaces (ADR-0006, #210).
    """

    item_id: ItemId
    title: str
    kind: KnowledgeKind
    owner: str
    author: str
    description: str
    body: str
    content_type: MediaType
    evidence: Evidence
    source_anchors: tuple[SourceAnchor, ...] = ()
    labels: tuple[str, ...] = ()
    scope_paths: tuple[str, ...] = ()
    #: Absent means "not stated": the field is left out of the migration and the
    #: loader applies the schema default. A value is never invented here, because
    #: `unverified`/`internal` written into every draft would assert a judgement
    #: the caller did not make (#249).
    trust_level: TrustLevel | None = None
    sensitivity: Sensitivity | None = None
    namespace: str | None = None
    expected_revision: RevisionId | None = None

    def __post_init__(self) -> None:
        # ADR-0013 point 5, on the generation path itself rather than only on
        # the value it was handed. `Evidence` enforces the same rule, and one of
        # the two is always the redundant one -- which is the point: neither is
        # the place a future caller happens not to go through.
        require_evidence(self.evidence)
        for name, value in (
            ("title", self.title),
            ("owner", self.owner),
            ("author", self.author),
            ("description", self.description),
        ):
            if not value.strip():
                raise ProposalError(
                    f"A proposal needs a non-empty {name}.",
                    remedy=f"Pass --{name}.",
                )
        if not self.body.strip():
            raise ProposalError(
                "A proposal with an empty body proposes nothing.",
                remedy="Write the knowledge into a file and pass it as --body-file.",
            )
        # INV-8, enforced here rather than left to `migrate apply`. A revision
        # with neither is schema-valid and then exits 4 with "has no source
        # anchor" (measured on the shipped sample project, #36) -- after a human
        # has reviewed the proposal and merged the pull request.
        if not self.source_anchors and AUTHORED_IN_THEURIAN not in self.labels:
            raise ProposalError(
                "A revision needs at least one source anchor, or the "
                f"{AUTHORED_IN_THEURIAN!r} label to declare it originates here.",
                remedy=(
                    "Pass --source-uri with where this came from, or --authored-here "
                    "if the knowledge originates in Theurian."
                ),
            )

    @property
    def resolved_namespace(self) -> str:
        """The namespace to record, defaulting to the item id's own."""
        return self.item_id.namespace if self.namespace is None else self.namespace


@dataclass(frozen=True, slots=True)
class DraftedProposal:
    """What one call to :meth:`ProposalService.draft` wrote, and where."""

    proposal_id: ProposalId
    directory: Path
    migration_id: MigrationId
    migration_file: Path
    revision_id: RevisionId
    expected_revision: RevisionId | None
    body_file: Path
    evidence_file: Path
    #: As written into the migration: relative to ``.theurian/migrations/``,
    #: which is where the migration file lives *after* acceptance.
    content_file: str
    content_sha256: ContentHash
    #: Where ``accept`` will put the body. Reported so a caller can say what the
    #: change would touch without reading the migration back.
    body_destination: Path


@dataclass(frozen=True, slots=True)
class MovedFile:
    """One file ``accept`` relocated, and whether it landed on something."""

    source: Path
    destination: Path
    replaced: bool


@dataclass(frozen=True, slots=True)
class AcceptedProposal:
    """What one call to :meth:`ProposalService.accept` moved."""

    proposal_id: ProposalId
    migration: MovedFile
    bodies: tuple[MovedFile, ...] = ()


class ProposalService:
    """Writes proposal directories, and moves accepted ones into place."""

    def __init__(  # noqa: PLR0913 -- injected dependencies, all keyword-only (ADR-0003)
        self,
        *,
        paths: ProjectPaths,
        clock: Clock,
        ids: IdGenerator,
        validate: MigrationDocumentValidator,
        current_revision: CurrentRevisionLookup,
        landed_migration: LandedMigrationLookup,
    ) -> None:
        self._paths = paths
        self._clock = clock
        self._ids = ids
        self._validate = validate
        self._current_revision = current_revision
        self._landed_migration = landed_migration

    # -- generation --------------------------------------------------------

    def draft(self, request: ProposalRequest) -> DraftedProposal:
        """Write one proposal directory, and nothing outside it.

        Every identifier is fresh: the proposal, the migration, and the
        revision. A revision id names one item for the life of a project, and
        reusing an applied one is accepted by ``migrate validate`` and then
        refused by ``migrate apply`` -- after the pull request has merged.

        The body path carries the revision id for a reason measured on this
        branch (see :func:`body_relative_path`): one path per item made the
        second accepted proposal invalidate the first migration's pinned digest,
        and the project stopped validating entirely.

        An update states which revision it replaces, or it is refused here rather
        than at ``migrate apply`` after the pull request has merged (#210). The
        generator does not have to be told the item already exists: it derives
        the item's current revision from the approved migration set (which is the
        canonical state), so ``--expected-revision`` is required exactly when the
        item is real and forbidden when it is not.

        Raises:
            ProposalError: If the request cannot be packaged, if an update omits
                or misplaces its ``expectedRevision``, or if the built migration
                does not satisfy the published schema. Nothing is written in any
                case.
        """
        self._check_expected_revision(request)
        proposal_id = ProposalId(self._ids.new_ulid().value)
        migration_id = MigrationId(self._ids.new_ulid().value)
        revision_id = RevisionId(self._ids.new_ulid().value)

        relative_body = body_relative_path(request.item_id, revision_id, request.content_type)
        # `..` and the sibling's name rather than `os.path.relpath`: both
        # directories are children of `.theurian/`, so the relative path is
        # exact by construction and stays POSIX-shaped on any platform.
        content_file = PurePosixPath("..", self._paths.knowledge.name, relative_body)
        body_bytes = request.body.encode("utf-8")
        digest = ContentHash.of_bytes(body_bytes)

        document = _migration_document(
            request,
            migration_id=migration_id,
            revision_id=revision_id,
            created_at=self._clock.now().replace(microsecond=0).isoformat(),
            content_file=content_file.as_posix(),
            digest=digest,
        )
        self._validate(document)

        directory = self._paths.proposals / proposal_id.value
        directory.mkdir(parents=True)
        # The body mirrors the sub-path it will occupy under `knowledge/`, not a
        # flat leaf name. Two content files that differ only in namespace --
        # `../knowledge/alpha/notes.md` and `../knowledge/beta/notes.md` -- share
        # the leaf `notes.md`, and a flat layout made `accept` find one file for
        # both: it consumed the first and raised a bare `FileNotFoundError` on
        # the second, leaving an orphan in `knowledge/`. Mirroring the sub-path
        # here is what lets `accept` find each body by its full relative path.
        body_file = directory / relative_body
        body_file.parent.mkdir(parents=True, exist_ok=True)
        body_file.write_bytes(body_bytes)
        evidence_file = directory / EVIDENCE_FILE
        evidence_file.write_text(
            json.dumps(
                _evidence_document(request.evidence, proposal_id, migration_id, request.item_id),
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        migration_file = directory / migration_file_name(
            migration_id, kebab_slug(request.title, fallback=_last_segment(request.item_id))
        )
        migration_file.write_text(_to_yaml(document), encoding="utf-8")

        return DraftedProposal(
            proposal_id=proposal_id,
            directory=directory,
            migration_id=migration_id,
            migration_file=migration_file,
            revision_id=revision_id,
            expected_revision=request.expected_revision,
            body_file=body_file,
            evidence_file=evidence_file,
            content_file=content_file.as_posix(),
            content_sha256=digest,
            body_destination=self._paths.knowledge / relative_body,
        )

    def _check_expected_revision(self, request: ProposalRequest) -> None:
        """Refuse an update with no guard, and a first revision with a stale one.

        ``expectedRevision`` is optimistic concurrency (ADR-0006): present, it
        must equal the item's current revision; absent, the revision is the
        item's first. Both are checkable at generation from the approved set,
        and checking here is what stops #210's unguarded update -- a second
        proposal for an existing item with no ``--expected-revision`` -- from
        validating and then failing at ``migrate apply`` after the pull request
        has merged.
        """
        current = self._current_revision(request.item_id)
        expected = request.expected_revision
        if current is None:
            if expected is not None:
                raise ProposalError(
                    f"{request.item_id.value} does not exist yet, so its first revision "
                    f"cannot replace {expected.value}.",
                    remedy="Drop --expected-revision to create the item, or correct --item-id.",
                )
            return
        if expected is None:
            raise ProposalError(
                f"{request.item_id.value} already exists at revision {current.value}; an "
                "update must state which revision it replaces, or it validates and then "
                "fails at apply after the pull request has merged.",
                remedy=f"Pass --expected-revision {current.value} to update it, or a new "
                "--item-id to create a different item.",
            )
        if expected != current:
            raise ProposalError(
                f"{request.item_id.value} is at revision {current.value}, but "
                f"--expected-revision names {expected.value}; the update would conflict at "
                "apply.",
                remedy=f"Pass --expected-revision {current.value}.",
            )

    # -- acceptance --------------------------------------------------------

    def accept(self, proposal_id: ProposalId) -> AcceptedProposal:
        """Move a proposal's migration and body into place. Nothing else.

        Every file this reads is proved to be a regular file inside the project
        with no symlink anywhere in its chain, and every file it writes lands
        inside ``.theurian/knowledge/`` (a body) or ``.theurian/migrations/``
        (the migration). A proposal directory is committed and so arrives
        through a contributor's pull request (ADR-0013 point 7) -- it is
        untrusted input, and a hand-authored ``contentFile`` or a symlinked
        body would otherwise make ``accept`` read a file outside the project or
        write one outside ``knowledge/``. The reads route through
        :func:`read_source_file` (SEC-7, T-5, and the size cap SEC-8); the
        writes use ``O_NOFOLLOW`` with an explicit mode, so no source bit and no
        planted destination symlink survives the move.

        The bytes that land are bounded by what a *committed* proposal can carry.
        A hardlink is the one channel this does not close -- ``O_NOFOLLOW`` does
        not see one -- but Git cannot commit a live hardlink (a fresh clone gets
        a distinct inode holding the committed blob), so the documented channel
        cannot deliver one; reaching it needs local write access at accept time,
        where the secret is already readable. Recorded as an accepted residual
        under the local-write boundary rather than closed with an ``st_nlink``
        check, which would refuse legitimate files.

        Raises:
            ProposalAlreadyAcceptedError: If this proposal's own migration is
                already in ``.theurian/migrations/``. A composition root reports
                this and :class:`MigrationNameTakenError` under the exit code it
                reserves for knowledge state, so both are named here rather than
                left inside "a migration already in place".
            MigrationNameTakenError: If the migration's name is taken, whether by
                a previous acceptance of this proposal or by a different change
                that collided.
            ProposalError: If the proposal is unknown, ambiguous, incomplete,
                could not be fully examined -- including a directory or a file
                in it the filesystem refuses to list, stat or read -- or names a
                file the security layer refuses. Both types above are
                subclasses, so a caller that catches only this still catches
                everything.
            PathEscapeError: If a ``contentFile`` resolves outside
                ``.theurian/knowledge/``.
            InputTooLargeError: If a file the accept path reads exceeds SEC-8's
                size cap.
        """
        try:
            directory = self._require_directory(proposal_id)
            migration_file = self._require_migration(directory, proposal_id)
            migration_bytes = self._read_within_project(migration_file)
            document = _parse_migration(migration_bytes, migration_file)
            destination = self._paths.migrations / migration_file.name
            # "Already in place" is the harder stop and is reported first; the
            # filename/id agreement is checked next, on a name nothing holds.
            self._refuse_if_migration_present(destination)
            _require_filename_matches_id(migration_file, document)

            moves = tuple(self._body_moves(directory, document))
            self._refuse_if_a_replacement_breaks_an_existing_pin(moves)
        except OSError as exc:
            # Every line above probes or reads a directory whose permissions are
            # a contributor's, through raw `is_symlink`/`is_file`/`exists`/`stat`
            # and read calls, and not one of them translated its own `OSError`:
            # the failure left `accept` as a bare `PermissionError`, so `--json`
            # published no `{error, remedy}` document at all (CP-2, #227). Which
            # call fires is an accident of the mode -- `0o000` on the directory
            # reaches the evidence probe, `0o444` the `is_symlink` above it, and
            # an unreadable migration file the read inside the security layer --
            # so the clause spans the examination phase rather than any one of
            # them.
            #
            # It stops there deliberately. `_commit` below keeps its own
            # `except OSError`, because a failed write must roll the
            # destinations back before it reports; a clause spanning both would
            # swallow that and describe a half-written tree as an unreadable
            # proposal.
            raise self._unreadable(proposal_id, exc) from exc

        return self._commit(proposal_id, moves, migration_file, migration_bytes, destination)

    def _require_directory(self, proposal_id: ProposalId) -> Path:
        # Built from a validated ULID, so no caller-supplied text reaches the
        # path: `ProposalId` cannot spell a separator, let alone a traversal.
        # But the name being safe says nothing about what it resolves *to*: a
        # committed proposal directory that is itself a symlink to somewhere out
        # of the project would pull that target's `*.yaml` into the accept path.
        # `is_dir()` follows the link, so it is checked separately.
        directory = self._paths.proposals / proposal_id.value
        if directory.is_symlink():
            raise ProposalError(
                f"Proposal {proposal_id.value} is a symlink, not a directory.",
                remedy="A proposal is a real directory under .theurian/proposals/. "
                "Remove the link and commit the directory itself.",
            )
        if not directory.is_dir():
            raise ProposalError(
                f"No proposal {proposal_id.value} under .theurian/proposals/.",
                remedy="List .theurian/proposals/ to see which proposals are waiting.",
            )
        return directory

    def _require_migration(self, directory: Path, proposal_id: ProposalId) -> Path:
        # The migration is identified by its `<ulid>-<slug>.yaml` name, not by
        # any `*.yaml`: a YAML or YML *body* is a `*.yaml` too, so matching the
        # suffix counted the body as a second migration and a YAML-bodied
        # proposal could never be accepted ("holds two or more migration
        # files"). Body files also mirror their `knowledge/` sub-path and so sit
        # in a subdirectory, while the migration is always at the top level --
        # another reason a top-level, name-matched lookup finds exactly the
        # migration. The name pattern is anchored on a ULID, so listing the
        # directory and filtering by name selects exactly what `glob("*.yaml")`
        # used to.
        #
        # `iterdir()` and not `glob()`: `Path.glob` runs its `scandir` inside a
        # `try` that swallows every `OSError`, so a proposal directory this
        # process cannot read yielded *nothing* and the migration sitting in it
        # read as absent -- the silent false negative #214 fixed on the loader's
        # own enumeration, here handing an unreadable directory to the
        # already-accepted diagnosis below. `iterdir()` raises instead, and
        # `accept` translates it (#227).
        entries = sorted(path for path in directory.iterdir() if is_migration_file_name(path.name))
        # `is_file()` follows a symlink, so a name-matching link to an
        # out-of-project file would otherwise count as the migration and have its
        # target's bytes read into a tracked file. It is rejected by name rather
        # than filtered, so the reader is told about the link, not sent to draft
        # again over a "missing" migration.
        symlinked = [path.name for path in entries if path.is_symlink()]
        if symlinked:
            raise ProposalError(
                f"Proposal {proposal_id.value} holds a symlinked migration file: "
                f"{_names(symlinked)}.",
                remedy="A proposal's migration is a real file. Remove the link and commit "
                "the migration itself.",
            )
        candidates = [path for path in entries if path.is_file()]
        if not candidates:
            raise self._no_migration_error(directory, proposal_id)
        if len(candidates) > 1:
            raise ProposalError(
                f"Proposal {proposal_id.value} holds two or more migration files: "
                f"{_names([path.name for path in candidates])}.",
                remedy="One proposal is one change. Split them, or delete the extra file.",
            )
        return candidates[0]

    def _no_migration_error(self, directory: Path, proposal_id: ProposalId) -> ProposalError:
        """Diagnose a proposal directory that holds no migration file.

        The question is whether this proposal has already been accepted, and the
        two answers have opposite remedies: re-drafting an accepted proposal
        mints a second migration for a change already in history (#89), while
        telling the author of an interrupted draft that no action is needed
        discards work that exists nowhere else (#253).

        **This is a best-effort diagnosis over untrusted input, not a
        tamper-proof fact.** A proposal directory is committed and arrives through
        a contributor's pull request (ADR-0013 point 7), so everything read here
        -- ``evidence.json`` included -- is contributor-controlled. A migration id
        recorded in ``evidence.json`` is a *claim*, not proof: removing it moves
        the answer, and it can name another proposal's landed migration (a forge).
        Two things keep the diagnosis safe despite that:

        * **A recorded id is cross-checked by item id.** The landed migration it
          names must also operate on the item this proposal's own record declares
          (:meth:`_landed_migration_matching`), which reduces the forge to landing
          a migration for the same item -- indistinguishable from a genuine
          acceptance.
        * **Safety is by remedy, not by detection.** No branch here concludes with
          an unconditional "no action is needed" or "draft it again". Every answer
          that could be wrong sends the reader to ``.theurian/migrations/`` first,
          and no remedy instructs discarding work that might exist.

        A read failure is answered as *indeterminate*, never as an answer: a
        ``evidence.json`` present but unreadable is a different fact from an absent
        one, and concluding "accepted" or "draft again" from "could not read it"
        is how a permission slip or a tamper becomes a wrong verdict.
        """
        document = self._read_evidence_record(directory, proposal_id)
        if document is None:
            # No evidence.json at all: a legacy proposal (the 26 committed before
            # the record existed), an accepted one whose evidence was removed, an
            # interrupted draft that never reached its evidence write, or a bare
            # directory. None carries a claim to check, so the answer is inferred
            # and points at the migration set first.
            return self._inferred_answer(directory, proposal_id)
        recorded = _migration_id_or_none(document.get("migrationId"))
        if recorded is None:
            # Present, readable evidence, but no usable migration id: a legacy
            # file (no such key) or a malformed value. No claim to check, so it is
            # inferred exactly like an absent record.
            return self._inferred_answer(directory, proposal_id)
        landed = self._landed_state(recorded, document)
        if landed is not None and landed.confirmed:
            return ProposalAlreadyAcceptedError(
                f"Proposal {proposal_id.value} appears to have been accepted: the migration it "
                f"records, {_names([landed.name])}, is in .theurian/migrations/ and "
                "operates on the item this proposal names.",
                remedy="Confirm that is the change you intended, then review it and open a pull "
                "request. If it is not, the record is stale -- read what is in "
                ".theurian/migrations/ before drafting again.",
            )
        if landed is not None:
            # The migration IS in place under the recorded id, so nothing here is
            # "nothing landed" -- returning exit 1 (whose contract is "re-draft")
            # would tell the author to mint a duplicate of a change on disk (#89,
            # adversarial M1). The item could not be cross-checked -- the record
            # names none, or names one this migration does not operate on -- so
            # this is read-before-acting, not an assertion that it is this change.
            return ProposalAlreadyAcceptedError(
                f"Proposal {proposal_id.value} records migration {recorded.value}, and a "
                f"migration with that id, {_names([landed.name])}, is in "
                ".theurian/migrations/ -- but this proposal's record does not name an item that "
                "migration operates on, so it cannot confirm that is the same change.",
                remedy="Read the migration under that id in .theurian/migrations/. If it is this "
                "change, review it and open a pull request. If it is not, this proposal's record "
                "is wrong -- correct evidence.json rather than re-drafting, which would mint a "
                "second migration.",
            )
        return ProposalError(
            f"Proposal {proposal_id.value} records migration {recorded.value}, but no migration "
            "with that id is in .theurian/migrations/, and no file named "
            f"{recorded.value}-<slug>.yaml is in this proposal directory. Nothing it drafted has "
            "been accepted.",
            remedy="Look in .theurian/migrations/ for the migration this proposal drafted -- it "
            "may be present under a different name. If it is genuinely gone, draft the change "
            "again with theurian propose.",
        )

    def _inferred_answer(self, directory: Path, proposal_id: ProposalId) -> ProposalError:
        """The answer when no usable migration id is recorded.

        Reached for a proposal that records no ``migrationId`` -- the 26 committed
        before the field existed (2026-08-20 at ``9873272``), one whose
        ``evidence.json`` is absent, or one whose recorded id is malformed. There
        is no claim to check, so the answer is inferred from the directory and
        both branches point at the migration set first.

        The inference reads only files of the shape ``draft`` writes
        (:func:`is_generated_body_file_name`), which is what keeps a ``Thumbs.db``
        or a reviewer's notes from deciding it. It is best-effort over a
        contributor-controlled directory, wrong in *both* directions and safe only
        by remedy:

        * a generated-shape body hand-planted in an accepted directory reads as
          unfinished; and
        * an interrupted draft whose body was hand-authored under a non-generated
          name -- a hand-written ``contentFile`` bypasses ``body_relative_path``
          -- leaves no generated-shape body and reads as accepted, though nothing
          landed.

        Neither misreads into a lost change: the unfinished branch checks the
        migration set before re-drafting, and the accepted branch never says an
        unconditional "no action is needed".
        """
        unmoved = _unmoved_generated_bodies(directory, proposal_id)
        if unmoved:
            return ProposalError(
                f"Proposal {proposal_id.value} looks unfinished: it still holds the body file "
                f"{_names(unmoved)} but no migration file, and records no migration id to check "
                "against .theurian/migrations/.",
                remedy="Look in .theurian/migrations/ for a migration naming that body. If it is "
                "there, this proposal was accepted -- review it and open a pull request. If it is "
                "not, nothing landed: draft the change again with theurian propose.",
            )
        return ProposalAlreadyAcceptedError(
            f"Proposal {proposal_id.value} appears to have been accepted, but this is inferred, "
            f"not proven: no drafted body file is left beside its {EVIDENCE_FILE} and it records "
            "no migration id, so the directory cannot say for certain.",
            remedy="Confirm the migration this proposal drafted is in .theurian/migrations/. If "
            "it is, no further action is needed beyond reviewing it and opening a pull request. "
            "If it is not, nothing landed -- draft the change again with theurian propose.",
        )

    def _read_evidence_record(
        self, directory: Path, proposal_id: ProposalId
    ) -> Mapping[str, object] | None:
        """Parse ``evidence.json``, or ``None`` if it is genuinely absent.

        The split that matters, and the one #253's third round named: *absent* and
        *present-but-unreadable* are different facts. An absent record is a legacy
        or interrupted proposal and is answered by inference; a present record
        that cannot be read is answered as indeterminate, because concluding
        anything from "could not read it" is exactly the collapse that let a
        permission slip read as an acceptance. ``draft`` writes the body, then the
        evidence, then the migration, so an interruption can leave no evidence at
        all -- the absent case is real.

        Every failure ``_read_within_project`` and ``json.loads`` can raise is
        caught and turned into indeterminate: an ``OSError`` (a directory named
        ``evidence.json``, an unreadable one), a decode or JSON error, a
        ``RecursionError`` from a deeply nested document, and the ``TheurianError``
        family the security layer raises -- a symlinked ``evidence.json`` (T-5),
        one over SEC-8's size cap, one whose path escapes the root. None may fall
        through to an answer.
        """
        evidence = directory / EVIDENCE_FILE
        if not evidence.exists() and not evidence.is_symlink():
            return None
        try:
            document = json.loads(self._read_within_project(evidence))
        except (OSError, UnicodeDecodeError, ValueError, RecursionError, TheurianError) as exc:
            raise self._evidence_indeterminate(proposal_id, exc) from exc
        if isinstance(document, Mapping):
            return document
        # Present and parseable, but not an object: it records no fields at all,
        # so it cannot prove acceptance. Indeterminate rather than "no record",
        # which would drop to inference and could conclude accepted.
        raise self._evidence_indeterminate(proposal_id, None)

    def _evidence_indeterminate(
        self, proposal_id: ProposalId, error: BaseException | None
    ) -> ProposalError:
        """An indeterminate verdict: the record is present but could not be read.

        The reason is derived from the *type* of failure, never from ``str(exc)``:
        an ``OSError``'s text carries the absolute path, which is the machine's
        home directory and not the proposal's, and interpolating it here would
        leak it (the same reason :func:`_within` exists). A category reason says
        enough without that.
        """
        return ProposalError(
            f"Proposal {proposal_id.value} could not be examined: its {EVIDENCE_FILE} is present "
            f"but could not be read ({_evidence_failure_reason(error)}), so whether it has been "
            "accepted cannot be answered.",
            remedy=f"Make .theurian/proposals/{proposal_id.value}/{EVIDENCE_FILE} readable and "
            "well-formed, then run theurian propose accept again. If it cannot be recovered, look "
            "in .theurian/migrations/ for the migration this proposal drafted before re-drafting.",
        )

    def _unreadable(self, proposal_id: ProposalId, error: OSError) -> ProposalError:
        """The answer when the filesystem refuses a probe or a read on the accept path.

        Raised from the single clause in :meth:`accept` that spans the
        examination phase, so *which* raw call the mode happens to select does
        not change the answer the caller gets.

        The reason is ``strerror`` -- the OS's own category for the failure --
        and never ``str(exc)``, whose text carries the absolute filename and with
        it the developer's home directory. :meth:`_evidence_indeterminate` records
        that discipline; :func:`_project_relative` is what names the file without
        it, and :func:`_names` quotes the result, because a proposal directory's
        filenames are the contributor's (ADR-0013 point 7).

        **The remedy never sends the reader to draft again.** A read the
        filesystem refused says nothing about whether this proposal's migration
        has already landed -- the two facts are unrelated -- and re-drafting an
        accepted proposal mints a second migration for a change already in
        history (#89). So it points at ``.theurian/migrations/`` first, exactly
        as the indeterminate-evidence remedy above does. What it *can* state
        outright is that nothing moved: every write on this path is in
        :meth:`_commit`, which the clause in :meth:`accept` deliberately
        excludes.
        """
        return ProposalError(
            f"Proposal {proposal_id.value} could not be examined: "
            f"{error.strerror or 'it could not be read'} at "
            f"{_names([_project_relative(error.filename, self._paths.root)])}. Nothing has been "
            "moved, and whether it can be accepted cannot be answered without reading it.",
            remedy=f"Make .theurian/proposals/{proposal_id.value} and the files in it readable "
            "-- chmod u+rX on the proposal directory -- then run theurian propose accept again. "
            "If they cannot be recovered, look in .theurian/migrations/ for the migration this "
            "proposal drafted before re-drafting: a refused read is not evidence that nothing "
            "landed.",
        )

    def _landed_state(
        self, migration_id: MigrationId, evidence: Mapping[str, object]
    ) -> _LandedMigration | None:
        """What the project's approved migration set holds under the recorded id.

        **Closure (the class this closes).** This reads the single ``MigrationSet``
        ``resolve_context`` already loaded -- the same set ``migrate validate`` and
        ``migrate apply`` read, keyed by *inner* id in ``MigrationSet._by_id`` --
        through the injected :data:`LandedMigrationLookup`. So it cannot disagree
        with the loader about whether a migration with the recorded inner id is in
        place: ``absent`` is returned exactly when ``_by_id`` holds no migration
        with that id, and no filename shape, ULID prefix, or symlink can make one
        reader see landed while the other sees absent. A previous version had its
        *own* landed-detector -- a ``self._paths.migrations`` filename glob keyed on
        the id's ULID *prefix*, plus a symlink skip -- and it disagreed with the
        loader twice: a symlinked landed migration read as absent (round five), and
        a landed migration renamed off its ULID prefix read as absent (round six),
        each an exit-1 "nothing landed" over a change on disk, each a duplicate-mint
        (#89). **Residual, same class, not fixed here:** ``_pinned_digest_at`` is
        the other member that re-detects landed migrations from the filesystem
        (a ``glob("*.yaml")`` with a symlink skip); it is issue #234's box, a
        security-sensitive pin guard with its own review, to be closed the same
        way -- wire it to the loaded set -- in its own slot.

        ``None`` when nothing is filed under the id. Otherwise a
        :class:`_LandedMigration` whose ``confirmed`` says whether it also operates
        on the item this proposal's record names -- the cross-check that turns "a
        migration with this id exists" into "*this proposal's* change is in place",
        because a recorded id alone is a contributor claim (``evidence.json`` is
        committed input, ADR-0013 point 7). The items are read from the *loaded*
        migration's typed operations, which the loader already parsed through the
        symlink-escape guard, so no link is followed here; a landed migration of a
        shape a generated proposal does not produce degrades to ``present``
        ("cannot confirm"), which is safe -- never ``absent``.
        """
        migration = self._landed_migration(migration_id)
        if migration is None:
            return None
        name = Path(migration.source_path).name if migration.source_path else migration_id.value
        claimed = _evidence_item_ids(evidence)
        return _LandedMigration(name=name, confirmed=bool(claimed & _migration_item_ids(migration)))

    def _refuse_if_migration_present(self, destination: Path) -> None:
        if destination.exists() or destination.is_symlink():
            raise MigrationNameTakenError(
                f"{destination.name} is already in .theurian/migrations/. The name "
                "carries the migration's id, so that migration is already in place.",
                remedy=(
                    "Read the migration that is already there. If this proposal is a "
                    "different change, draft it again to mint a new migration id; if it "
                    "is the same one, delete the proposal directory."
                ),
            )

    def _body_moves(self, directory: Path, document: Mapping[str, object]) -> Iterable[_BodyMove]:
        """Pair each ``contentFile`` the migration names with the file to move.

        The destination is resolved against ``.theurian/migrations/`` because
        that is where the migration file will be once this call finishes -- the
        same resolution the loader performs -- and it is confined to
        ``.theurian/knowledge/``. The body is then found in the proposal
        directory at the **same sub-path** it will occupy under ``knowledge/``,
        not by its leaf name: two content files that differ only in namespace
        share a leaf, and a leaf lookup found one file for both. The source is
        read through the security layer here, once, so the bytes written later
        are the bytes that were checked.
        """
        knowledge = self._paths.knowledge.resolve()
        for content_file in _content_files(document):
            destination = self._destination_of(content_file)
            tail = destination.relative_to(knowledge)
            source = directory / tail
            if not source.exists():
                raise ProposalError(
                    f"The migration names {_names([content_file])}, but "
                    f"{_names([tail.as_posix()])} is not in the proposal directory.",
                    remedy="Restore the body file, or draft the proposal again.",
                )
            data = self._read_within_project(source)
            yield _BodyMove(
                source=source,
                destination=destination,
                data=data,
                replaced=destination.exists(),
            )

    def _read_within_project(self, path: Path) -> bytes:
        """Read one accept-path file, or refuse it.

        A regular file, inside the project root, under SEC-8's size cap, with no
        symlink *anywhere* in its chain below the root. :func:`read_source_file`
        enforces the size cap and rejects a symlink that escapes the root, but it
        *follows* an intermediate symlink that stays in-project -- and a proposal
        directory is a contributor's, so a namespaced body reached through an
        in-project directory symlink would read a file the proposal never
        authored. The chain is therefore walked and every symlink component is
        refused, so 'no symlink anywhere in its chain' is literally true, the
        same stance :meth:`_require_directory` takes on the proposal directory.
        """
        self._reject_symlink_in_chain(path)
        return read_source_file(self._paths.root, PurePosixPath(path.relative_to(self._paths.root)))

    def _reject_symlink_in_chain(self, path: Path) -> None:
        """Refuse ``path`` if any component below the project root is a symlink.

        Only the portion below the root is walked: what sits above it (a
        ``/tmp`` that is itself a link on macOS, say) is the environment's, not
        the proposal's. A symlink component *inside* the proposal is never
        legitimate -- a committed proposal is real files and directories.
        """
        root = self._paths.root
        try:
            relative = path.relative_to(root)
        except ValueError:  # pragma: no cover - accept paths are built under the root
            raise PathEscapeError(str(path), str(root)) from None
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ProposalError(
                    f"{_names([current.relative_to(root).as_posix()])} is a symlink; a "
                    "proposal's files and directories must all be real.",
                    remedy="Remove the link and commit the real file or directory.",
                )

    def _refuse_if_a_replacement_breaks_an_existing_pin(self, moves: Iterable[_BodyMove]) -> None:
        """Refuse a body replacement that would invalidate an applied migration.

        The invariant ``accept`` must hold is that it never leaves the migration
        set unable to validate. A body file a migration references is immutable:
        the loader re-reads it and compares it against the ``contentSha256`` that
        migration pinned. Replacing that body with different bytes makes the pin
        wrong, and ``migrate validate`` / ``apply`` / ``status`` then all exit 4
        for the whole project -- reproduced: *"hashes to abc7cdb70713 but the
        migration pins 4f9c5503e198"*, with no undo command.

        A generated proposal never reaches this: its ``contentFile`` carries a
        fresh revision id, so no two of them target one path. What reaches it is
        a hand-authored ``contentFile`` that reuses an existing body's path --
        exactly the manual flow -- and refusing there breaks nothing legitimate,
        because the honest way to change a body is a new revision at a new path.
        """
        for move in moves:
            if not move.replaced:
                continue
            pin = self._pinned_digest_at(move.destination)
            if pin is None:
                continue
            current = ContentHash.of_bytes(move.destination.read_bytes()).value
            replacement = ContentHash.of_bytes(move.data).value
            # A pin that already fails to match its body is a project that does
            # not validate now; only refuse where a *currently valid* pin would
            # be broken. And a byte-identical replacement changes nothing.
            if pin == current and replacement != current:
                relative = move.destination.relative_to(self._paths.knowledge.resolve())
                raise ProposalError(
                    f"Accepting this would overwrite {_names([relative.as_posix()])}, whose "
                    "bytes an existing migration pins with contentSha256; the whole set "
                    "would then fail to validate.",
                    remedy="Draft this as a new revision -- a fresh contentFile -- rather than "
                    "a replacement of an existing body.",
                )

    def _pinned_digest_at(self, destination: Path) -> str | None:
        """The ``contentSha256`` an existing migration pins for ``destination``, if any.

        Reads the migrations already in ``.theurian/migrations/`` -- not the
        proposal -- so it can say whether landing this body would break one. A
        malformed existing migration is skipped: it fails ``migrate validate``
        regardless, and is not this command's to diagnose.
        """
        resolved_destination = destination.resolve()
        for migration in sorted(self._paths.migrations.glob("*.yaml")):
            if migration.is_symlink() or not migration.is_file():
                continue
            try:
                document = load_yaml_mapping(migration.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError):
                continue
            for content_file, pin in _pinned_content_files(document):
                if (self._paths.migrations / content_file).resolve() == resolved_destination:
                    return pin
        return None

    def _commit(
        self,
        proposal_id: ProposalId,
        moves: tuple[_BodyMove, ...],
        migration_file: Path,
        migration_bytes: bytes,
        migration_destination: Path,
    ) -> AcceptedProposal:
        """Write every body and the migration, or leave the tree as it was.

        Atomic in the sense that matters here: either the migration and all its
        bodies land, or ``.theurian/migrations/`` and ``.theurian/knowledge/``
        are exactly as they were. The move is a copy-then-delete -- every
        destination is written first, and the proposal's own files are removed
        only once the migration has landed, so a failure part way through rolls
        the destinations back and leaves the proposal directory whole. The
        migration is written last and with ``O_EXCL``: if its name was taken in
        the window since the check, the bodies already written are rolled back
        rather than left as orphans a previous version stranded in
        ``knowledge/``.
        """
        self._paths.migrations.mkdir(parents=True, exist_ok=True)
        created: list[Path] = []
        restored: list[tuple[Path, bytes]] = []
        try:
            for move in moves:
                move.destination.parent.mkdir(parents=True, exist_ok=True)
                if move.replaced:
                    restored.append((move.destination, move.destination.read_bytes()))
                else:
                    created.append(move.destination)
                _write_file(move.destination, move.data, exclusive=False)
            try:
                _write_file(migration_destination, migration_bytes, exclusive=True)
            except FileExistsError as exc:
                raise MigrationNameTakenError(
                    f"{migration_destination.name} appeared in .theurian/migrations/ while "
                    "this proposal was being accepted, so accepting it would overwrite that "
                    "migration.",
                    remedy="Read what is there, then draft this proposal again for a new id.",
                ) from exc
            created.append(migration_destination)
        except MigrationNameTakenError:
            _roll_back(created, restored)
            raise
        except OSError as exc:
            _roll_back(created, restored)
            raise ProposalError(
                f"accept could not write a file: {exc}.",
                remedy="Check the contentFile the migration names, then accept it again.",
            ) from exc

        for move in moves:
            move.source.unlink(missing_ok=True)
        migration_file.unlink(missing_ok=True)
        return AcceptedProposal(
            proposal_id=proposal_id,
            migration=MovedFile(
                source=migration_file, destination=migration_destination, replaced=False
            ),
            bodies=tuple(
                MovedFile(source=m.source, destination=m.destination, replaced=m.replaced)
                for m in moves
            ),
        )

    def _destination_of(self, content_file: str) -> Path:
        """Where one ``contentFile`` points, proved to be inside ``knowledge/``.

        The ``..`` in ``../knowledge/x.md`` is legitimate and load-bearing, so
        containment cannot be a check on the string: the path is resolved first
        -- symlinks and all -- and the resolved result is what must stay inside
        ``.theurian/knowledge/`` (SEC-7, T-4, T-5). The boundary is
        ``knowledge/`` and not the project root, because a body has no
        legitimate destination outside it: a generated ``contentFile`` is always
        ``../knowledge/...``, and confining to the root instead would let a
        hand-authored ``../../.git/hooks/pre-commit`` write an executable git
        hook that runs on the maintainer's next commit.
        """
        knowledge = self._paths.knowledge.resolve()
        resolved = (self._paths.migrations / content_file).resolve()
        try:
            relative = resolved.relative_to(knowledge)
        except ValueError as exc:
            raise PathEscapeError(content_file, str(knowledge)) from exc
        destination = resolve_within_root(self._paths.knowledge, PurePosixPath(relative))
        assert_no_symlink_escape(self._paths.knowledge, destination)
        return destination


@dataclass(frozen=True, slots=True)
class _BodyMove:
    """One body the accept path will write, with the bytes it already checked."""

    source: Path
    destination: Path
    data: bytes
    replaced: bool


@dataclass(frozen=True, slots=True)
class _LandedMigration:
    """A migration filed under a recorded id, and whether its item cross-checks.

    ``confirmed`` is the difference between "this proposal's change is in place"
    and "a migration with this id is in place but may be someone else's" -- the
    two carry different messages and different remedies, but neither is "nothing
    landed", so both exit on the knowledge-state code rather than the re-draft one.
    ``name`` is the loaded migration's filename (from ``Migration.source_path``),
    for the message, never a value the diagnosis reasons *over*.
    """

    name: str
    confirmed: bool


def _last_segment(item_id: ItemId) -> str:
    return item_id.value.rpartition(".")[2]


def _unmoved_generated_bodies(directory: Path, proposal_id: ProposalId) -> tuple[str, ...]:
    """Body files of the generator's own shape still in a proposal directory.

    Relative POSIX paths, sorted, so a message naming them reads the same on
    every run and every platform. A symlink counts without being followed: what
    a name-shaped entry *is* does not change the fact that the generator's file
    has not been moved out, and a symlinked body is refused by name later.

    Deliberately not "every file that is not the evidence". ``accept`` removes
    only the bodies its migration names, so anything else a proposal directory
    carries -- a reviewer's notes, an editor's backup, ``Thumbs.db`` -- survives
    a *successful* acceptance and is committed with it (ADR-0013 point 7).
    Counting those made an accepted proposal read as unfinished, whose remedy
    mints a duplicate migration.

    Raises:
        ProposalError: If any part of the directory cannot be read. An
            unreadable subdirectory is the one case where "no body is left" and
            "no body could be seen" are different facts, and concluding the
            first from the second is how an untouched draft gets reported as
            accepted. ``rglob`` swallows that error silently, which is why this
            walks with an error callback instead.
    """

    def refuse(error: OSError) -> NoReturn:
        raise ProposalError(
            f"Proposal {proposal_id.value} could not be examined: {error.strerror or error} at "
            f"{_names([_within(error.filename, directory)])}. Whether it has been accepted "
            "cannot be answered without reading it.",
            remedy="Make the path above readable -- chmod u+rx on the directory -- then run "
            "theurian propose accept again.",
        )

    unmoved: list[str] = []
    for parent, _directories, files in directory.walk(on_error=refuse):
        for name in files:
            if is_generated_body_file_name(name):
                unmoved.append((parent / name).relative_to(directory).as_posix())
    return tuple(sorted(unmoved))


def _within(filename: object, directory: Path) -> str:
    """``filename`` from an ``OSError``, relative to the proposal directory.

    The absolute path is the machine's and not the proposal's: it carries the
    developer's home directory into a message. Falls back to whatever the error
    carried when the path is not under ``directory``, because a message naming
    nothing is worse than one naming an odd path.
    """
    if not isinstance(filename, str):
        return "an unreadable path"
    try:
        return Path(filename).relative_to(directory).as_posix() or "."
    except ValueError:
        return filename


def _project_relative(filename: object, root: Path) -> str:
    """``filename`` from an ``OSError``, relative to the project root.

    :func:`_within`'s sibling, for the accept path, where the refused call can be
    anywhere the command reached rather than only inside one proposal directory:
    a probe under ``.theurian/proposals/``, a body under ``.theurian/knowledge/``,
    the migration destination. The absolute path is never returned, for the reason
    :func:`_within` records -- it is the machine's home directory, not the
    proposal's.

    Both spellings of the root are tried because this path produces both. A probe
    built as ``self._paths.root / ...`` carries the root as the project was
    configured, while a read through :func:`read_source_file` carries the
    *resolved* one, and the two differ wherever the root is reached through a
    symlink (``/var`` against ``/private/var`` on macOS is the case this suite
    runs in). A path under neither is named by a phrase and not by its own text:
    that is where this is deliberately stricter than :func:`_within`, whose odd
    path is at least known to be inside a committed proposal directory.
    """
    if not isinstance(filename, str):
        return "an unreadable path"
    candidate = Path(filename)
    for base in (root, root.resolve()):
        try:
            return candidate.relative_to(base).as_posix() or "."
        except ValueError:
            continue
    return "a path outside the project"


def _names(names: Sequence[str]) -> str:
    """Untrusted names, quoted and bounded, for an *error message*.

    ``repr`` and never the raw name. A proposal directory is committed input
    (ADR-0013 point 7), so its filenames are the contributor's: one carrying
    ``ESC [ 2 K CR`` erases the line a terminal has already drawn and prints its
    own in place of it -- T-3's injection at the CLI edge rather than in indexed
    content. This quotes such a name into readable escapes and bounds the count.

    **This is the error path only, and it is not what closes the class.** The
    class -- every proposal-derived string that reaches a terminal raw -- is
    closed at the *output sink*: ``cli.commands._render`` and ``_fail`` escape
    control characters in every value they print, so a name that skips ``_names``
    (the exit-0 success payload did, before round three) still cannot rewrite a
    line. ``_names`` remains because quoting and a five-name cap are readability
    a blanket control-escape does not give: 50,000 files produced a 600 KB error
    string in 1.5 s before the cap.

    The remaining interpolations in this module are constrained rather than
    quoted -- and the sink escapes any control that slips through regardless.
    Measured on 2026-08-20:

    * a migration file's own name, at the two "already in .theurian/migrations/"
      refusals and in :func:`_parse_migration`, and its inner ``id`` in
      :func:`_require_filename_matches_id` -- each reached its message only after
      matching ``_MIGRATION_FILE_NAME``, whose charset is a ULID, ASCII
      lowercase, hyphens and ``.yaml``;
    * identifiers -- ``ProposalId``, ``MigrationId``, ``ItemId``, ``RevisionId``
      -- validated on construction against anchored patterns;
    * ``OSError`` text in :meth:`ProposalService._commit`: CPython formats the
      filename with ``repr`` inside ``OSError.__str__``, so
      ``[Errno 2] No such file or directory: '/tmp/x\\x1b[2K\\rfake'`` is what
      arrives;
    * ``yaml.YAMLError`` text in :func:`_parse_migration`, which does echo a
      source line -- but PyYAML's reader refuses ``ESC`` outright ("unacceptable
      character #x001b") and normalises ``CR`` as a line break, so neither
      primitive that rewrites a drawn line survives the parse.
    """
    shown = ", ".join(repr(name) for name in names[:_MAX_NAMES_LISTED])
    remaining = len(names) - _MAX_NAMES_LISTED
    return shown if remaining <= 0 else f"{shown}, and {remaining} more"


def _require_filename_matches_id(migration_file: Path, document: Mapping[str, object]) -> None:
    """Refuse a migration whose filename ULID is not its own ``id``.

    ``.theurian/migrations/`` names files ``<id>-<slug>.yaml`` and the ULID is
    authoritative (``migrations.md``), but the loader keys migrations by the
    *inner* ``id``. A file named for one ULID carrying another lands here as its
    filename and is then read by the loader as its inner id -- so the "already in
    place" check (which sees the filename) misses a real collision on the inner
    id, and the set fails downstream with a duplicate-id error. The two must
    agree, and ``_require_migration`` has already proved the name is
    ``<ulid>-<slug>.yaml``.
    """
    inner = document.get("id")
    prefix = migration_file.name.split("-", 1)[0]
    if not isinstance(inner, str) or inner != prefix:
        raise ProposalError(
            f"The migration file is named for {prefix} but its id is {inner!r}; the "
            "filename ULID must equal the migration id.",
            remedy="Rename the file to <id>-<slug>.yaml, or correct the id inside it.",
        )


def _parse_migration(data: bytes, path: Path) -> Mapping[str, object]:
    """Parse an accepted proposal's migration, for its ``contentFile`` alone.

    Deliberately not a validation pass. ``accept`` moves files; whether the
    migration is well-formed is ``migrate validate``'s question and whether it
    applies is ``migrate apply``'s, and answering either here would imply this
    command had checked something it has not.
    """
    try:
        return load_yaml_mapping(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
        raise ProposalError(
            f"{path.name} could not be read as a migration: {exc}",
            remedy="Fix the migration file in the proposal directory, then accept it again.",
        ) from exc


def _migration_id_or_none(value: object) -> MigrationId | None:
    """Parse a recorded ``migrationId`` as a ULID, or ``None`` for anything else.

    ``evidence.json`` is untrusted (ADR-0013 point 7), so a ``migrationId`` that
    is not a string, or a string that is not a ULID, is treated as *no usable id*
    rather than as a value or a crash: the diagnosis falls to inference, which is
    safe. This is also what keeps caller text out of the glob a matched id feeds
    -- a ULID cannot spell a metacharacter.
    """
    if not isinstance(value, str):
        return None
    try:
        return MigrationId.parse(value)
    except TheurianError:
        return None


def _evidence_item_ids(evidence: Mapping[str, object]) -> frozenset[str]:
    """Item ids this proposal's own record claims, for the acceptance cross-check.

    ``draft`` records a single ``itemId`` string; a hand-written ``evidence.json``
    may carry a list. Both are read as a set. A value of any other shape yields
    the empty set, which the cross-check reads as "cannot confirm" -- so a record
    that names no item never confirms an acceptance, which is the safe default.
    """
    value = evidence.get("itemId")
    if isinstance(value, str):
        return frozenset({value})
    if isinstance(value, list):
        return frozenset(item for item in value if isinstance(item, str))
    return frozenset()


def _migration_item_ids(migration: Migration) -> frozenset[str]:
    """The item ids a *loaded* migration's operations name, for the cross-check.

    Read from the loader's typed operations, not a re-parse of the file: the
    loader already read the id and operations through ``read_source_file``'s
    symlink-escape guard, so nothing here follows a link. Only ``createItem`` and
    ``upsertRevision`` are consulted -- the two operations a generated proposal
    produces, and the pair whose ``item_id`` names the change this proposal made.
    A landed migration of any other shape contributes no id, so the cross-check
    reads it as ``present`` ("cannot confirm") rather than ``confirmed``, which is
    the safe direction: it never turns a landed migration into ``absent``.
    """
    return frozenset(
        op.item_id.value
        for op in migration.operations
        if isinstance(op, CreateItem | UpsertRevision)
    )


#: A path-free, control-free reason per read-failure class. By exception type
#: rather than ``str(exc)``: an ``OSError``'s text carries the absolute filename
#: (a home-directory leak) and a decode error's echoes bytes the file chose, so a
#: fixed phrase per class leaks neither. ``UnicodeDecodeError`` precedes
#: ``ValueError`` because it is one -- and it is reachable, not dead:
#: ``_read_within_project`` returns *bytes*, and ``json.loads`` on non-UTF-8
#: bytes raises ``UnicodeDecodeError`` before it reaches JSON syntax, so it must
#: come first to be reported as UTF-8 rather than as JSON (pinned by
#: ``test_a_non_utf8_evidence_file_is_indeterminate_as_bad_utf8``).
#: ``InputTooLargeError``/``PathEscapeError`` precede ``ProposalError`` because
#: ``TheurianError`` is their common base but only the symlink refusal is a plain
#: ``ProposalError``.
_EVIDENCE_FAILURE_REASONS: Final = (
    (InputTooLargeError, "it is larger than the size cap"),
    (PathEscapeError, "its path escapes the project"),
    (ProposalError, "it is, or is reached through, a symlink"),
    (RecursionError, "it is nested too deeply to parse"),
    (UnicodeDecodeError, "it is not valid UTF-8"),
    (ValueError, "it is not valid JSON"),
)


def _evidence_failure_reason(error: BaseException | None) -> str:
    """A path-free, control-free reason an ``evidence.json`` could not be read."""
    for kind, reason in _EVIDENCE_FAILURE_REASONS:
        if isinstance(error, kind):
            return reason
    if isinstance(error, OSError):
        return (error.strerror or "it could not be read").lower()
    return "it is not a JSON object"


def _content_files(document: Mapping[str, object]) -> Iterable[str]:
    operations = document.get("operations")
    if not isinstance(operations, list):
        return
    for operation in operations:
        if not isinstance(operation, Mapping):
            continue
        content_file = operation.get("contentFile")
        if isinstance(content_file, str) and content_file:
            yield content_file


def _pinned_content_files(document: Mapping[str, object]) -> Iterable[tuple[str, str]]:
    """Every ``(contentFile, contentSha256)`` an existing migration pins."""
    operations = document.get("operations")
    if not isinstance(operations, list):
        return
    for operation in operations:
        if not isinstance(operation, Mapping):
            continue
        content_file = operation.get("contentFile")
        pin = operation.get("contentSha256")
        if isinstance(content_file, str) and content_file and isinstance(pin, str) and pin:
            yield content_file, pin


def _roll_back(created: Iterable[Path], restored: Iterable[tuple[Path, bytes]]) -> None:
    """Undo a partial commit: remove what was created, restore what was replaced.

    Best effort, and deliberately silent on its own failures: it runs while an
    exception is already propagating, and a raise here would mask the failure
    the caller is trying to report.
    """
    for path in created:
        try:
            path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - defensive; the file was just written
            continue
    for path, data in restored:
        try:
            _write_file(path, data, exclusive=False)
        except OSError:  # pragma: no cover - defensive; the file was just read
            continue


def _write_file(destination: Path, data: bytes, *, exclusive: bool) -> None:
    """Write ``data`` to ``destination`` with an explicit mode, never following a link.

    ``O_NOFOLLOW`` on the final component: a destination that is a symlink is
    refused rather than written through, so a body cannot be redirected out of
    ``knowledge/`` by planting a link at its path. The write carries an explicit
    ``0o644`` and never a rename, so a body chmod 0755 in the proposal directory
    does not land executable in ``knowledge/``. ``O_EXCL`` additionally refuses a
    destination that exists at all, for the migration, whose name must never land
    on another file.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
    flags |= os.O_EXCL if exclusive else os.O_TRUNC
    handle = os.open(destination, flags, 0o644)
    with os.fdopen(handle, "wb") as opened:
        opened.write(data)


def _migration_document(  # noqa: PLR0913 -- the fields a migration has; all keyword-only
    request: ProposalRequest,
    *,
    migration_id: MigrationId,
    revision_id: RevisionId,
    created_at: str,
    content_file: str,
    digest: ContentHash,
) -> dict[str, object]:
    """Build the migration a human will review and then apply.

    ``status: approved`` is right even though nobody has approved it yet: the
    file applies only once a human has merged it, and ``draft`` would land
    knowledge that ``theurian index build`` leaves out. ``trustLevel`` and
    ``sensitivity`` are written only when the caller set them (``--trust-level``,
    ``--sensitivity``): left unset they are absent from the file and the loader
    applies its defaults, ``unverified`` and ``internal``. Stamping those
    defaults in unasked-for would assert a judgement the caller never made, so
    the omission is surfaced in the CLI's next steps instead of written here
    (#249).
    """
    metadata: dict[str, object] = {
        "title": request.title,
        "contentType": request.content_type.value,
        "kind": request.kind.value,
        "namespace": request.resolved_namespace,
        "status": KnowledgeStatus.APPROVED.value,
        "owner": request.owner,
    }
    if request.trust_level is not None:
        metadata["trustLevel"] = request.trust_level.value
    if request.sensitivity is not None:
        metadata["sensitivity"] = request.sensitivity.value
    if request.labels:
        metadata["labels"] = list(request.labels)
    if request.scope_paths:
        metadata["scope"] = {"paths": list(request.scope_paths)}
    if request.source_anchors:
        metadata["sourceAnchors"] = [_anchor_document(a) for a in request.source_anchors]

    upsert: dict[str, object] = {
        "op": "upsertRevision",
        "itemId": request.item_id.value,
        "revisionId": revision_id.value,
    }
    # Absent means "this creates the first revision", so an update states which
    # revision it replaces or `migrate apply` reports a conflict (#210).
    if request.expected_revision is not None:
        upsert["expectedRevision"] = request.expected_revision.value
    upsert["contentFile"] = content_file
    upsert["contentSha256"] = digest.value
    upsert["metadata"] = metadata

    return {
        "apiVersion": MIGRATION_API_VERSION,
        "id": migration_id.value,
        "createdAt": created_at,
        "author": request.author,
        "description": request.description,
        "operations": [
            {
                "op": "createItem",
                "itemId": request.item_id.value,
                "kind": request.kind.value,
                "namespace": request.resolved_namespace,
                "owner": request.owner,
            },
            upsert,
        ],
    }


def _anchor_document(anchor: SourceAnchor) -> dict[str, object]:
    """An anchor as the schema spells it, with absent fields left out."""
    fields = {
        "provider": anchor.provider,
        "sourceUri": anchor.source_uri,
        "repository": anchor.repository,
        "commitSha": anchor.commit_sha,
        "blobSha": anchor.blob_sha,
        "filePath": anchor.file_path,
        "lineStart": anchor.line_start,
        "lineEnd": anchor.line_end,
        "externalId": anchor.external_id,
    }
    return {name: value for name, value in fields.items() if value is not None}


def _evidence_document(
    evidence: Evidence,
    proposal_id: ProposalId,
    migration_id: MigrationId,
    item_id: ItemId,
) -> dict[str, object]:
    """The origin record ADR-0013 point 5 requires, for a human to read.

    Written for a human, with two fields ``propose accept`` reads back:
    ``migrationId`` and ``itemId``. Together they let it ask "has this proposal
    been accepted?" -- a migration with that id, operating on that item, is in
    ``.theurian/migrations/`` or it is not -- rather than inferring the answer
    from which files are left in the directory, which got #253 wrong in both
    directions.

    **Neither field is authority; both are a contributor's claim.** The proposal
    directory is committed and arrives through a pull request (ADR-0013 point 7),
    so this file is untrusted input like any other in it. ``migrationId`` alone
    would let a never-accepted proposal name another proposal's landed migration
    and read as accepted; ``itemId`` is what the accept path cross-checks against
    that landed migration's operations, reducing the forge to landing a migration
    for the same item. See :meth:`ProposalService._landed_migration_matching`.

    The anchors here are *not* the ones ``migrate apply`` enforces -- those are
    ``metadata.sourceAnchors`` on the revision (INV-8) -- and neither list
    substitutes for the other.
    """
    return {
        "proposalId": proposal_id.value,
        "migrationId": migration_id.value,
        "itemId": item_id.value,
        "agentId": evidence.agent_id.value,
        "taskId": evidence.task_id.value,
        "model": evidence.model,
        "reasoning": evidence.reasoning,
        "sourceAnchors": [_anchor_document(anchor) for anchor in evidence.anchors],
    }


def _to_yaml(document: Mapping[str, object]) -> str:
    """Serialise a migration, in the order it was built.

    ``sort_keys=False`` because the built order is the order a reviewer reads:
    what this is, who owns it, why, then what it does. ``allow_unicode`` so a
    Japanese title stays legible rather than becoming escape sequences.
    """
    return str(yaml.safe_dump(dict(document), sort_keys=False, allow_unicode=True, width=100))
