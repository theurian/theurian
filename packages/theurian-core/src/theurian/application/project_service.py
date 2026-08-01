"""Project registration, ``.theurian/`` initialisation, and state resolution.

The registry is per-user (``~/.theurian/projects.json``) rather than per-project,
because one daemon serves many projects (ADR-0002). Everything under a project's
``.theurian/`` belongs to the project and travels with it in Git.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from theurian.domain.errors import TheurianError
from theurian.domain.identifiers import ProjectId
from theurian.domain.migration import LoadedMigrations
from theurian.domain.ports import Clock
from theurian.domain.project import (
    DEFAULT_KNOWLEDGE_DIRECTORY,
    GITIGNORE_BLOCK_END,
    GITIGNORE_BLOCK_START,
    GITIGNORE_ENTRIES,
    Project,
)
from theurian.domain.state import ActiveState, StateHash, compute_state_hash, state_inputs_from

#: Directories `theurian init` creates. The derived ones are created too, so a
#: fresh clone has somewhere to put state without a later mkdir race.
INITIAL_DIRECTORIES: Final = (
    "knowledge/architecture",
    "knowledge/domain",
    "knowledge/operations",
    "knowledge/security",
    "knowledge/testing",
    "migrations",
    "specifications",
    "evaluations",
    "proposals",
    "schema",
    "state",
    "cache",
    "runtime",
    "generated",
)

_SLUG_INVALID: Final = re.compile(r"[^a-z0-9]+")


class ProjectError(TheurianError):
    """A project could not be registered, resolved, or initialised."""


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    """Absolute paths derived from a project root.

    Centralised so no caller assembles a state path by string concatenation and
    quietly disagrees with another caller about where state lives.
    """

    root: Path
    knowledge_dir: Path

    @property
    def migrations(self) -> Path:
        return self.knowledge_dir / "migrations"

    @property
    def knowledge(self) -> Path:
        return self.knowledge_dir / "knowledge"

    @property
    def specifications(self) -> Path:
        return self.knowledge_dir / "specifications"

    @property
    def state(self) -> Path:
        return self.knowledge_dir / "state"

    @property
    def runtime(self) -> Path:
        return self.knowledge_dir / "runtime"

    @property
    def active_pointer(self) -> Path:
        return self.state / "active.json"

    @property
    def write_lock(self) -> Path:
        return self.runtime / "write.lock"

    def database_for(self, state_hash: StateHash) -> Path:
        return self.state / state_hash.database_filename

    @classmethod
    def of(cls, root: Path, knowledge_directory: PurePosixPath | None = None) -> ProjectPaths:
        directory = knowledge_directory or DEFAULT_KNOWLEDGE_DIRECTORY
        resolved = root.resolve()
        return cls(root=resolved, knowledge_dir=resolved / str(directory))


def derive_project_id(root: Path) -> ProjectId:
    """Derive a stable, readable project id from a directory name.

    Deliberately derived from the *name* rather than the absolute path: moving a
    repository must not change its identity, and a path-derived id would leak a
    machine-specific value into a shared registry.
    """
    slug = _SLUG_INVALID.sub("-", root.resolve().name.lower()).strip("-")
    if not slug:
        raise ProjectError(f"Cannot derive a project id from {root}")
    return ProjectId(slug)


def initialize_project(paths: ProjectPaths) -> tuple[str, ...]:
    """Create the ``.theurian/`` layout.

    Never overwrites. Returns the project-relative paths it created, so setup can
    report exactly what changed rather than claiming success vaguely (§34).
    """
    created: list[str] = []

    for relative in INITIAL_DIRECTORIES:
        directory = paths.knowledge_dir / relative
        if not directory.exists():
            directory.mkdir(parents=True)
            created.append(str(Path(paths.knowledge_dir.name) / relative))

    # `.gitkeep` only where Git must carry an otherwise-empty directory. Derived
    # directories are git-ignored, so marking them would commit a path that is
    # supposed to be absent from the repository (ADR-0004).
    for relative in ("migrations", "specifications", "proposals"):
        keep = paths.knowledge_dir / relative / ".gitkeep"
        if not keep.exists():
            keep.touch()
            created.append(str(Path(paths.knowledge_dir.name) / relative / ".gitkeep"))

    return tuple(created)


def ensure_gitignore(root: Path) -> tuple[bool, str]:
    """Append Theurian's ignore block to ``.gitignore`` if it is missing.

    Written between markers so a re-run rewrites only Theurian's own lines and
    never touches a rule the user wrote (SEC-18).

    Returns:
        ``(changed, rendered_block)``.
    """
    block = "\n".join(
        [
            GITIGNORE_BLOCK_START,
            "# Derived artifacts. Rebuilt from Git-tracked migrations (ADR-0004).",
            *GITIGNORE_ENTRIES,
            GITIGNORE_BLOCK_END,
        ]
    )

    gitignore = root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""

    if GITIGNORE_BLOCK_START in existing:
        start = existing.index(GITIGNORE_BLOCK_START)
        end_marker = existing.find(GITIGNORE_BLOCK_END, start)
        if end_marker == -1:
            raise ProjectError(
                f"{gitignore} has an unterminated Theurian block. "
                f"Add {GITIGNORE_BLOCK_END!r} or remove the block, then retry."
            )
        end = end_marker + len(GITIGNORE_BLOCK_END)
        if existing[start:end] == block:
            return False, block
        updated = existing[:start] + block + existing[end:]
    else:
        separator = "" if existing.endswith("\n") or not existing else "\n"
        updated = f"{existing}{separator}\n{block}\n" if existing else f"{block}\n"

    gitignore.write_text(updated, encoding="utf-8")
    return True, block


def resolve_state_hash(loaded: LoadedMigrations, schema_version: int) -> StateHash:
    """Compute the state hash for a loaded migration set (ADR-0016)."""
    return compute_state_hash(
        state_inputs_from(loaded.migration_set, loaded.content_checksums, schema_version)
    )


def read_active_state(paths: ProjectPaths) -> ActiveState | None:
    """Read the active state pointer, or ``None`` if there is none."""
    pointer = paths.active_pointer
    if not pointer.exists():
        return None
    try:
        return ActiveState.from_json(json.loads(pointer.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TheurianError) as exc:
        raise ProjectError(
            f"{pointer} is unreadable: {exc}. Delete it to rebuild; it is derived."
        ) from exc


def write_active_state(
    paths: ProjectPaths, state_hash: StateHash, migration_count: int, clock: Clock
) -> ActiveState:
    """Publish a new active state, atomically.

    Write-to-temp then ``os.replace``, which is atomic on POSIX. A reader must
    never observe a half-written pointer, because that would send it to a
    database that does not exist (ADR-0007).
    """
    active = ActiveState(
        state_hash=state_hash,
        database_filename=state_hash.database_filename,
        migration_count=migration_count,
        updated_at=clock.now().isoformat(),
    )

    paths.state.mkdir(parents=True, exist_ok=True)
    temporary = paths.active_pointer.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(active.to_json(), indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, paths.active_pointer)  # noqa: PTH105 -- atomic replace
    return active


@dataclass(frozen=True, slots=True)
class ProjectRegistry:
    """The per-user record of which projects exist.

    Separate from any state database: a project must be listable without opening
    -- or building -- its state.
    """

    path: Path

    @classmethod
    def default(cls, data_dir: Path | None = None) -> ProjectRegistry:
        base = data_dir or Path(os.environ.get("THEURIAN_DATA_DIR", Path.home() / ".theurian"))
        return cls(path=base / "projects.json")

    def load(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        try:
            loaded: dict[str, dict[str, str]] = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProjectError(f"{self.path} is not valid JSON: {exc}") from exc
        return loaded

    def register(self, project: Project) -> bool:
        """Add or update a registration.

        Returns:
            ``True`` if anything changed. Re-registering an identical project is
            a no-op, so setup can run repeatedly without churn (FR-L2).

        ``registeredAt`` records when the project was *first* registered and is
        preserved across re-registration. Refreshing it would make every re-run
        report a change and defeat the idempotence FR-L2 requires.
        """
        entries = self.load()
        existing = entries.get(project.project_id.value)

        entry = {
            "rootPath": project.root_path,
            "repositoryUrl": project.repository_url or "",
            "defaultBranch": project.default_branch,
            "knowledgeDirectory": str(project.knowledge_directory),
            "registeredAt": (
                existing["registeredAt"]
                if existing and "registeredAt" in existing
                else project.registered_at.isoformat()
            ),
        }
        if existing == entry:
            return False

        entries[project.project_id.value] = entry
        self._write(entries)
        return True

    def unregister(self, project_id: ProjectId) -> bool:
        entries = self.load()
        if project_id.value not in entries:
            return False
        del entries[project_id.value]
        self._write(entries)
        return True

    def _write(self, entries: dict[str, dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)  # noqa: PTH105 -- atomic replace
