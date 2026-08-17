"""CLI composition root helpers.

One of the three places allowed to name a concrete adapter (ADR-0003). Every
command builds its object graph here, so wiring lives in one readable place
rather than being scattered across command bodies.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from theurian.application.migration_engine import MigrationEngine
from theurian.application.project_service import (
    ProjectError,
    ProjectPaths,
    ProjectRegistry,
    derive_project_id,
    resolve_state_hash,
)
from theurian.domain.errors import SchemaUnreadableError
from theurian.domain.identifiers import ProjectId
from theurian.domain.migration import LoadedMigrations
from theurian.domain.state import StateHash
from theurian.infrastructure.determinism import SystemClock, UlidGenerator
from theurian.infrastructure.filesystem.migration_loader import load_migrations
from theurian.infrastructure.sqlite.schema import SCHEMA_VERSION

#: Timeout on every `git` invocation. An unbounded subprocess in a CLI that a
#: hook may call is a hang the user cannot explain (SEC-19).
GIT_TIMEOUT_SECONDS: Final = 5.0


def find_git_root(start: Path) -> Path | None:
    """The working tree root containing ``start``, or ``None``.

    ``--show-toplevel`` rather than walking up looking for ``.git``: in a
    worktree, ``.git`` is a *file*, and a hand-rolled walk gets that wrong.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],  # noqa: S607 - resolved via PATH
            cwd=start,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip())


def current_commit(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - resolved via PATH
            cwd=root,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def default_branch(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],  # noqa: S607 - resolved via PATH
            cwd=root,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "main"
    branch = result.stdout.strip()
    return branch or "main"


def repository_url(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],  # noqa: S607 - resolved via PATH
            cwd=root,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    url = result.stdout.strip()
    return url or None


def _schema_candidate_exists(candidate: Path) -> bool:
    """``.exists()`` on one candidate schema location, translating a probe failure.

    CPython's ``Path.exists()`` swallows ``ENOENT``/``ENOTDIR`` -- "not there"
    -- but re-raises ``EACCES`` the same way ``Path.is_dir()`` does. A
    permission problem on an ancestor of the installation directory is
    install-integrity, not a location that is simply absent, so it is not the
    ``ProjectError`` this function's caller raises when *neither* candidate
    exists -- that message says "reinstall" for the right reason only when
    reinstalling would actually help (issue #205's Class 1, applied to the
    schema-location probes rather than the read that follows them).
    """
    try:
        return candidate.exists()
    except OSError as exc:
        raise SchemaUnreadableError(str(candidate), exc.strerror or str(exc)) from exc


def schema_root() -> Path:
    """Locate the published JSON Schemas.

    The packaged copy is checked first, because that is the path an installed
    build actually takes -- preferring the source tree would mean the packaged
    path was only ever exercised by users, never by the developers.

    The source-checkout fallback lets a contributor edit a schema and see the
    effect without reinstalling.
    """
    packaged = Path(__file__).resolve().parents[1] / "schemas"
    if _schema_candidate_exists(packaged / "migrations" / "migration.schema.json"):
        return packaged

    from_source = Path(__file__).resolve().parents[5] / "schemas"
    if _schema_candidate_exists(from_source / "migrations" / "migration.schema.json"):
        return from_source

    raise ProjectError(
        "Cannot locate the published JSON Schemas. This build is incomplete; reinstall theurian."
    )


@dataclass(frozen=True, slots=True)
class CommandContext:
    """Everything a project-scoped command needs, resolved once."""

    project_id: ProjectId
    paths: ProjectPaths
    loaded: LoadedMigrations
    state_hash: StateHash
    clock: SystemClock
    ids: UlidGenerator

    @property
    def engine(self) -> MigrationEngine:
        return MigrationEngine(self.clock, self.loaded.content_by_hash)


def resolve_context(
    start: Path | None = None, project_id: ProjectId | None = None
) -> CommandContext:
    """Build a command context for the project containing ``start``.

    The id is resolved in three steps, most authoritative first: an explicit
    argument (how a user breaks a collision), then the registry keyed by root
    path, then the directory-name default. Skipping the registry lookup would
    mean a project registered under a disambiguated id was addressed by the
    colliding default on its own command line — the CLI writing to one project
    while every agent reads the other.

    The fallback to ``derive_project_id`` is only safe because the middle step
    raises rather than returning ``None`` when it cannot read an entry. ``or``
    cannot tell "no registration names this root" from "a registration might and
    is unreadable", and while it could, the second case took the fallback and
    produced exactly the misrouting the paragraph above describes
    (:meth:`ProjectRegistry.ids_for_root`). An explicit ``project_id``
    short-circuits before the lookup, which is what keeps every project on the
    machine addressable while a broken entry is being removed.

    Raises:
        ProjectError: If ``start`` is not inside a Git repository, if its root is
            registered under more than one project id, or if the registry holds
            an entry that cannot be read and no explicit ``project_id`` was
            given.
        MigrationError: If the migrations under it do not load or validate.
        SchemaUnreadableError: If probing for the installed package's JSON
            Schema raised (``schema_root``), or the schema was found but a
            read of it failed (``_validator`` in
            ``infrastructure/filesystem/migration_loader.py``). Not a
            ``MigrationError``: install-integrity, not migration content.
    """
    cwd = (start or Path.cwd()).resolve()
    root = find_git_root(cwd)
    if root is None:
        raise ProjectError(
            f"{cwd} is not inside a Git repository. Theurian scopes a project to a "
            f"Git working tree, so that branches and worktrees stay isolated.",
            remedy="Run this inside a Git repository.",
        )

    paths = ProjectPaths.of(root)
    loaded = load_migrations(paths.root, paths.migrations, schema_root())

    return CommandContext(
        project_id=project_id or registry().id_for_root(root) or derive_project_id(root),
        paths=paths,
        loaded=loaded,
        state_hash=resolve_state_hash(loaded, SCHEMA_VERSION),
        clock=SystemClock(),
        ids=UlidGenerator(),
    )


def registry() -> ProjectRegistry:
    return ProjectRegistry.default()
