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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

import yaml

from theurian.application.project_service import ProjectPaths
from theurian.domain.enums import KnowledgeKind, KnowledgeStatus
from theurian.domain.errors import PathEscapeError, TheurianError
from theurian.domain.identifiers import ItemId, MigrationId, ProposalId, RevisionId
from theurian.domain.knowledge import AUTHORED_IN_THEURIAN, SourceAnchor
from theurian.domain.migration import MIGRATION_API_VERSION
from theurian.domain.ports import Clock, IdGenerator
from theurian.domain.proposal import (
    Evidence,
    body_relative_path,
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


class MigrationNameTakenError(ProposalError):
    """``.theurian/migrations/`` already holds a file of that name.

    Its own type rather than a message a caller matches on: this is the one
    refusal here that says something about the project's *knowledge state* --
    that migration is already in place -- so a caller reports it under the exit
    code it reserves for that, and a reworded message cannot silently move it.
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

    def __init__(
        self,
        *,
        paths: ProjectPaths,
        clock: Clock,
        ids: IdGenerator,
        validate: MigrationDocumentValidator,
        current_revision: CurrentRevisionLookup,
    ) -> None:
        self._paths = paths
        self._clock = clock
        self._ids = ids
        self._validate = validate
        self._current_revision = current_revision

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
            json.dumps(_evidence_document(request.evidence, proposal_id), indent=2) + "\n",
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
            ProposalError: If the proposal is unknown, ambiguous, incomplete,
                names a migration already in place, or names a file the security
                layer refuses.
            PathEscapeError: If a ``contentFile`` resolves outside
                ``.theurian/knowledge/``.
            InputTooLargeError: If a file the accept path reads exceeds SEC-8's
                size cap.
        """
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

        return self._commit(proposal_id, moves, migration_file, migration_bytes, destination)

    def _require_directory(self, proposal_id: ProposalId) -> Path:
        # Built from a validated ULID, so no caller-supplied text reaches the
        # path: `ProposalId` cannot spell a separator, let alone a traversal.
        # But the name being safe says nothing about what it resolves *to*: a
        # committed proposal directory that is itself a symlink to somewhere out
        # of the project would pull that target's `*.yaml` into the accept path.
        # `is_dir()` follows the link, so it is checked separately.
        directory = self._paths.proposals / proposal_id.value
        try:
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
        except OSError as exc:
            raise ProposalError(
                f"Proposal directory for {proposal_id.value} could not be accessed: "
                f"{exc.strerror or exc}.",
                remedy=f"Grant read and execute permissions on {directory} and its contents.",
            ) from exc
        return directory

    @staticmethod
    def _require_migration(directory: Path, proposal_id: ProposalId) -> Path:
        # The migration is identified by its `<ulid>-<slug>.yaml` name, not by
        # globbing `*.yaml`: a YAML or YML *body* is a `*.yaml` too, so globbing
        # counted the body as a second migration and a YAML-bodied proposal could
        # never be accepted ("holds two or more migration files"). Body files
        # also mirror their `knowledge/` sub-path and so sit in a subdirectory,
        # while the migration is always at the top level -- another reason a
        # top-level, name-matched lookup finds exactly the migration.
        try:
            entries = sorted(
                path
                for path in directory.iterdir()
                if path.name.endswith(".yaml") and is_migration_file_name(path.name)
            )
            # `is_file()` follows a symlink, so a name-matching link to an
            # out-of-project file would otherwise count as the migration and have its
            # target's bytes read into a tracked file. It is rejected by name rather
            # than filtered, so the reader is told about the link, not sent to draft
            # again over a "missing" migration.
            symlinked = [path.name for path in entries if path.is_symlink()]
            if symlinked:
                raise ProposalError(
                    f"Proposal {proposal_id.value} holds a symlinked migration file: "
                    f"{', '.join(symlinked)}.",
                    remedy="A proposal's migration is a real file. Remove the link and commit "
                    "the migration itself.",
                )
            candidates = [path for path in entries if path.is_file()]
            if not candidates:
                # An accepted proposal keeps its `evidence.json` and loses its
                # migration to `.theurian/migrations/`, so that shape is "already
                # accepted", not "draft it again" -- which would mint a second
                # migration for a change that has already landed.
                if (directory / EVIDENCE_FILE).is_file():
                    raise ProposalError(
                        f"Proposal {proposal_id.value} appears to have been accepted already: "
                        "its migration has been moved into .theurian/migrations/.",
                        remedy="No action is needed. Review the change and open a pull request.",
                    )
                raise ProposalError(
                    f"Proposal {proposal_id.value} holds no <migration-id>-<slug>.yaml file.",
                    remedy="A proposal directory holds one migration named <ulid>-<slug>.yaml. "
                    "Draft the proposal again.",
                )
            if len(candidates) > 1:
                raise ProposalError(
                    f"Proposal {proposal_id.value} holds two or more migration files: "
                    f"{', '.join(path.name for path in candidates)}.",
                    remedy="One proposal is one change. Split them, or delete the extra file.",
                )
            return candidates[0]
        except OSError as exc:
            raise ProposalError(
                f"Proposal directory for {proposal_id.value} could not be read: "
                f"{exc.strerror or exc}.",
                remedy=f"Grant read and execute permissions on {directory} and its contents.",
            ) from exc

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
                    f"The migration names {content_file}, but {tail.as_posix()} is not in "
                    f"the proposal directory.",
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
                    f"{current.relative_to(root).as_posix()} is a symlink; a proposal's "
                    "files and directories must all be real.",
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
                    f"Accepting this would overwrite {relative.as_posix()}, whose bytes an "
                    "existing migration pins with contentSha256; the whole migration set "
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


def _last_segment(item_id: ItemId) -> str:
    return item_id.value.rpartition(".")[2]


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
    ``sensitivity`` are deliberately absent -- claiming ``reviewed`` on
    something an agent drafted asserts a review that has not happened, and a
    reviewer can add either.
    """
    metadata: dict[str, object] = {
        "title": request.title,
        "contentType": request.content_type.value,
        "kind": request.kind.value,
        "namespace": request.resolved_namespace,
        "status": KnowledgeStatus.APPROVED.value,
        "owner": request.owner,
    }
    if request.labels:
        metadata["labels"] = list(request.labels)
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


def _evidence_document(evidence: Evidence, proposal_id: ProposalId) -> dict[str, object]:
    """The origin record ADR-0013 point 5 requires, for a human to read.

    Core never reads this file. The anchors here are *not* the ones
    ``migrate apply`` enforces -- those are ``metadata.sourceAnchors`` on the
    revision (INV-8) -- and neither list substitutes for the other.
    """
    return {
        "proposalId": proposal_id.value,
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
