"""Project registration.

A Project is one Git working tree registered with the daemon. Two worktrees of
the same repository are two Projects: they can have different HEADs, therefore
different reachable migrations, therefore different knowledge (FR-P5, ADR-0007).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath

from theurian.domain.errors import InvariantViolationError
from theurian.domain.identifiers import ProjectId
from theurian.domain.values import TenantId

#: Directory holding a project's Theurian state, relative to the project root.
DEFAULT_KNOWLEDGE_DIRECTORY = PurePosixPath(".theurian")

#: Paths under ``.theurian`` that are derived and must stay out of Git (ADR-0004).
DERIVED_SUBDIRECTORIES: tuple[str, ...] = ("state", "cache", "runtime", "generated")

#: A path must have at least a directory and a child to sit in a subdirectory.
_MIN_PARTS_FOR_SUBDIRECTORY = 2

#: The exact block ``theurian init`` appends to a repository's ``.gitignore``.
#: Written between markers so re-running rewrites only Theurian's own lines and
#: never touches a rule the user wrote (SEC-18).
GITIGNORE_BLOCK_START = "# >>> theurian >>>"
GITIGNORE_BLOCK_END = "# <<< theurian <<<"


@dataclass(frozen=True, slots=True)
class GitignoreSection:
    """One labelled run of entries inside the managed ``.gitignore`` block.

    The block stopped being homogeneous in Milestone 7. Until then every entry
    was a derived artifact, and the block said so in one header comment --
    which is how "git-ignored" came to be read as "derived" (ADR-0004's
    amendment). ``.theurian/proposals-local/`` is ignored because it is
    deliberately machine-local, and nothing rebuilds it (ADR-0028), so a header
    claiming the whole list is rebuilt from Git-tracked migrations would tell a
    reader that a local proposal is safe to delete.

    The label therefore rides on the run rather than on the block: an entry
    added here has to be put under one of the two comments, which is the
    decision that would otherwise be skipped.
    """

    #: Written above the entries. A whole ``.gitignore`` comment line, ``#``
    #: included, because that is what makes it a label rather than a rule.
    comment: str
    entries: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.comment.startswith("#"):
            raise InvariantViolationError(
                f"A managed .gitignore label must start with '#', got {self.comment!r}; "
                "written into the block as-is it would ignore a path nobody chose."
            )
        if not self.entries:
            raise InvariantViolationError(
                f"The managed .gitignore label {self.comment!r} carries no entries, so it "
                "would label nothing."
            )


#: The managed block's entries, in the order they are written, grouped by what
#: makes each one ignored. Two categories, and they are not the same question:
#: `DERIVED_SUBDIRECTORIES` says what is *rebuildable*, and only the first group
#: belongs there (ADR-0028 decision 3).
GITIGNORE_SECTIONS: tuple[GitignoreSection, ...] = (
    GitignoreSection(
        comment="# Derived artifacts. Rebuilt from Git-tracked migrations (ADR-0004).",
        entries=(
            ".theurian/state/",
            ".theurian/cache/",
            ".theurian/runtime/",
            ".theurian/generated/",
            "*.sqlite",
            "*.sqlite-wal",
            "*.sqlite-shm",
        ),
    ),
    GitignoreSection(
        comment="# Authored, kept out of Git on purpose. Nothing rebuilds it (ADR-0028).",
        entries=(".theurian/proposals-local/",),
    ),
)

#: Every entry of the managed block, in written order. Derived from
#: :data:`GITIGNORE_SECTIONS` rather than restated beside it, so a label and the
#: entries it covers cannot drift apart.
GITIGNORE_ENTRIES: tuple[str, ...] = tuple(
    entry for section in GITIGNORE_SECTIONS for entry in section.entries
)


@dataclass(frozen=True, slots=True)
class Project:
    """A registered Git working tree.

    ``root_path`` is stored as an absolute, symlink-resolved path string. Every
    filesystem operation for this project is confined to it (SEC-7).
    """

    project_id: ProjectId
    root_path: str
    repository_url: str | None
    default_branch: str
    knowledge_directory: PurePosixPath
    registered_at: datetime
    last_seen_commit: str | None = None
    tenant_id: TenantId = field(default_factory=TenantId)

    def __post_init__(self) -> None:
        if not self.root_path.startswith("/"):
            raise InvariantViolationError(
                f"Project.root_path must be absolute and resolved, got {self.root_path!r}"
            )
        if self.knowledge_directory.is_absolute():
            raise InvariantViolationError(
                "Project.knowledge_directory must be relative to the project root, "
                f"got {self.knowledge_directory}"
            )
        if not self.default_branch:
            raise InvariantViolationError("Project.default_branch must not be empty")
        if self.registered_at.tzinfo is None:
            raise InvariantViolationError("registered_at must be timezone-aware")

    @property
    def migrations_directory(self) -> PurePosixPath:
        return self.knowledge_directory / "migrations"

    @property
    def knowledge_content_directory(self) -> PurePosixPath:
        return self.knowledge_directory / "knowledge"

    @property
    def specifications_directory(self) -> PurePosixPath:
        return self.knowledge_directory / "specifications"

    @property
    def proposals_directory(self) -> PurePosixPath:
        return self.knowledge_directory / "proposals"

    @property
    def proposals_local_directory(self) -> PurePosixPath:
        """Where ``theurian propose --local`` drafts instead (ADR-0028).

        Git-ignored and **not** derived: the two properties are separate, and
        only the second is what :meth:`is_derived` may grow. A local proposal is
        authored content that nothing rebuilds, so calling it derived would tell
        an operator that a force-added one is a rebuildable artifact they can
        delete.
        """
        return self.knowledge_directory / "proposals-local"

    @property
    def state_directory(self) -> PurePosixPath:
        return self.knowledge_directory / "state"

    def is_derived(self, relative_path: PurePosixPath) -> bool:
        """Whether a project-relative path is a derived artifact (ADR-0004).

        Used by ``doctor`` to warn when Git is tracking something rebuildable.
        """
        parts = relative_path.parts
        in_knowledge_dir = len(parts) >= _MIN_PARTS_FOR_SUBDIRECTORY and (
            parts[0] == self.knowledge_directory.name
        )
        if in_knowledge_dir and parts[1] in DERIVED_SUBDIRECTORIES:
            return True
        return relative_path.name.endswith((".sqlite", ".sqlite-wal", ".sqlite-shm"))
