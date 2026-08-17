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
* The **body** file may replace what is at its ``contentFile`` path, because on
  an update to existing knowledge that is exactly the intent. What keeps the
  replacement a stated one is the ``contentSha256`` and ``expectedRevision``
  that :meth:`ProposalService.draft` pins.
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
    kebab_slug,
    migration_file_name,
    require_evidence,
)
from theurian.domain.values import ContentHash, MediaType
from theurian.security.paths import assert_no_symlink_escape, resolve_within_root
from theurian.security.yaml_loading import load_yaml_mapping

#: The evidence file's name. Fixed, unlike the migration's: nothing moves it, so
#: it cannot collide with anything, and a reviewer looking for the reasoning
#: behind a proposal should not have to work out what it was called.
EVIDENCE_FILE: Final = "evidence.json"

#: Checks a built migration document against the published JSON Schema, raising
#: on failure. Supplied by the composition root, because locating and reading
#: ``schemas/`` is an adapter's job (ADR-0003).
MigrationDocumentValidator = Callable[[Mapping[str, object]], None]


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
    ) -> None:
        self._paths = paths
        self._clock = clock
        self._ids = ids
        self._validate = validate

    # -- generation --------------------------------------------------------

    def draft(self, request: ProposalRequest) -> DraftedProposal:
        """Write one proposal directory, and nothing outside it.

        Every identifier is fresh: the proposal, the migration, and the
        revision. A revision id names one item for the life of a project, and
        reusing an applied one is accepted by ``migrate validate`` and then
        refused by ``migrate apply`` -- after the pull request has merged.

        Raises:
            ProposalError: If the request cannot be packaged, or if the built
                migration does not satisfy the published schema. Nothing is
                written in either case.
        """
        proposal_id = ProposalId(self._ids.new_ulid().value)
        migration_id = MigrationId(self._ids.new_ulid().value)
        revision_id = RevisionId(self._ids.new_ulid().value)

        relative_body = body_relative_path(request.item_id, request.content_type)
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
        body_file = directory / relative_body.name
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

    # -- acceptance --------------------------------------------------------

    def accept(self, proposal_id: ProposalId) -> AcceptedProposal:
        """Move a proposal's migration and body into place. Nothing else.

        The order is chosen for what a partial failure leaves behind. The
        collision check runs first, so a refusal touches nothing at all. Bodies
        move next and the migration last: a failure part-way leaves
        ``.theurian/migrations/`` exactly as it was, so ``migrate validate``
        still loads. Moving the migration first and failing on a body would
        publish a migration whose ``contentFile`` does not exist, which breaks
        every migration command in the project rather than this one change.

        Raises:
            ProposalError: If the proposal is unknown, ambiguous, incomplete, or
                names a migration already in place.
            PathEscapeError: If a ``contentFile`` resolves outside the project.
        """
        directory = self._require_directory(proposal_id)
        migration_file = self._require_migration(directory, proposal_id)
        destination = self._paths.migrations / migration_file.name
        if destination.exists():
            raise ProposalError(
                f"{destination.name} is already in .theurian/migrations/. The name "
                "carries the migration's id, so that migration is already in place.",
                remedy=(
                    "Read the migration that is already there. If this proposal is a "
                    "different change, draft it again to mint a new migration id; if it "
                    "is the same one, delete the proposal directory."
                ),
            )

        document = _read_document(migration_file)
        moves = tuple(self._body_moves(directory, document))

        self._paths.migrations.mkdir(parents=True, exist_ok=True)
        bodies = tuple(_replace_body(move) for move in moves)
        return AcceptedProposal(
            proposal_id=proposal_id,
            migration=_move_without_replacing(migration_file, destination),
            bodies=bodies,
        )

    def _require_directory(self, proposal_id: ProposalId) -> Path:
        # Built from a validated ULID, so no caller-supplied text reaches the
        # path: `ProposalId` cannot spell a separator, let alone a traversal.
        directory = self._paths.proposals / proposal_id.value
        if not directory.is_dir():
            raise ProposalError(
                f"No proposal {proposal_id.value} under .theurian/proposals/.",
                remedy="List .theurian/proposals/ to see which proposals are waiting.",
            )
        return directory

    @staticmethod
    def _require_migration(directory: Path, proposal_id: ProposalId) -> Path:
        candidates = sorted(path for path in directory.glob("*.yaml") if path.is_file())
        if not candidates:
            raise ProposalError(
                f"Proposal {proposal_id.value} holds no migration file.",
                remedy="A proposal directory holds one <migration-id>-<slug>.yaml. "
                "Draft the proposal again.",
            )
        if len(candidates) > 1:
            raise ProposalError(
                f"Proposal {proposal_id.value} holds two or more migration files: "
                f"{', '.join(path.name for path in candidates)}.",
                remedy="One proposal is one change. Split them, or delete the extra file.",
            )
        return candidates[0]

    def _body_moves(self, directory: Path, document: Mapping[str, object]) -> Iterable[MovedFile]:
        """Pair each ``contentFile`` the migration names with the file to move.

        Resolved against ``.theurian/migrations/`` rather than against the
        proposal directory, because that is where the migration file will be
        once this call finishes -- and the same resolution the loader performs.
        """
        for content_file in _content_files(document):
            destination = self._destination_of(content_file)
            source = directory / PurePosixPath(content_file).name
            if not source.is_file():
                raise ProposalError(
                    f"The migration names {content_file}, but {source.name} is not in "
                    f"the proposal directory.",
                    remedy="Restore the body file, or draft the proposal again.",
                )
            yield MovedFile(source=source, destination=destination, replaced=destination.exists())

    def _destination_of(self, content_file: str) -> Path:
        """Where one ``contentFile`` points, proved to be inside the project.

        The ``..`` in ``../knowledge/x.md`` is legitimate and load-bearing, so
        containment cannot be a check on the string: the path is resolved first
        -- symlinks and all -- and the resolved result is what must stay inside
        the root (SEC-7, T-4, T-5). ``relative_to`` raising is the escape, which
        is why it is converted rather than propagated.
        """
        resolved = (self._paths.migrations / content_file).resolve()
        root = self._paths.root.resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError as exc:
            raise PathEscapeError(content_file, str(root)) from exc
        destination = resolve_within_root(self._paths.root, PurePosixPath(relative))
        assert_no_symlink_escape(self._paths.root, destination)
        return destination


def _last_segment(item_id: ItemId) -> str:
    return item_id.value.rpartition(".")[2]


def _read_document(path: Path) -> Mapping[str, object]:
    """Parse an accepted proposal's migration, for its ``contentFile`` alone.

    Deliberately not a validation pass. ``accept`` moves files; whether the
    migration is well-formed is ``migrate validate``'s question and whether it
    applies is ``migrate apply``'s, and answering either here would imply this
    command had checked something it has not.
    """
    try:
        return load_yaml_mapping(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
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


def _replace_body(move: MovedFile) -> MovedFile:
    """Move a body onto its destination, replacing whatever is there.

    The permissive half of the asymmetry: a second revision of an item targets
    the same ``contentFile``, so refusing here would make an update impossible.
    """
    move.destination.parent.mkdir(parents=True, exist_ok=True)
    move.source.replace(move.destination)
    return move


def _move_without_replacing(source: Path, destination: Path) -> MovedFile:
    """Move a file onto a name nothing holds, refusing rather than overwriting.

    ``O_EXCL`` and not an ``exists()`` check followed by a rename: a rename
    replaces silently, which is the exact failure this refusal exists to
    prevent, and the check-then-rename form still does so whenever anything
    creates the name in between.
    """
    try:
        handle = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError as exc:
        raise ProposalError(
            f"{destination.name} appeared in .theurian/migrations/ while this proposal "
            "was being accepted, so accepting it would overwrite that migration.",
            remedy="Read what is there, then draft this proposal again for a new id.",
        ) from exc
    with os.fdopen(handle, "wb") as opened:
        opened.write(source.read_bytes())
    source.unlink()
    return MovedFile(source=source, destination=destination, replaced=False)


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
