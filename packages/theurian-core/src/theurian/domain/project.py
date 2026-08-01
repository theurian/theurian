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
GITIGNORE_ENTRIES: tuple[str, ...] = (
    ".theurian/state/",
    ".theurian/cache/",
    ".theurian/runtime/",
    ".theurian/generated/",
    "*.sqlite",
    "*.sqlite-wal",
    "*.sqlite-shm",
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
