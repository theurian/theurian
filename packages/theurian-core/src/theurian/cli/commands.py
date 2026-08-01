"""Project and migration CLI commands.

Every command supports ``--json``. The JSON shape is a published contract that
the Claude Code plugin depends on (CP-2), validated by ``tests/contract/``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from theurian.application.migration_engine import (
    MigrationEngine,
    verify_no_applied_migration_changed,
)
from theurian.application.project_service import (
    ensure_gitignore,
    initialize_project,
    read_active_state,
    write_active_state,
)
from theurian.cli.context import (
    CommandContext,
    current_commit,
    default_branch,
    registry,
    repository_url,
    resolve_context,
)
from theurian.domain.errors import (
    MigrationChecksumMismatchError,
    MigrationCycleError,
    MigrationError,
    RevisionConflictError,
    TheurianError,
)
from theurian.domain.identifiers import ProjectId
from theurian.domain.migration import MIGRATION_ENGINE_VERSION
from theurian.domain.project import DEFAULT_KNOWLEDGE_DIRECTORY, Project
from theurian.infrastructure.sqlite.connection import create_database, write_transaction
from theurian.infrastructure.sqlite.schema import SCHEMA_VERSION
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore, SqliteWriter

#: Exit code for a knowledge-state problem the user must resolve: a checksum
#: mismatch, a revision conflict, a dependency cycle. Distinct from 1 so a script
#: can tell "your knowledge needs attention" from "the command broke".
EXIT_STATE_ERROR = 4

JsonOption = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")]

project_app = typer.Typer(help="Register and inspect projects.", no_args_is_help=True)
migrate_app = typer.Typer(help="Validate and apply knowledge migrations.", no_args_is_help=True)


def _emit(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
        return
    _render(payload, indent=0)


def _render(payload: dict[str, Any], *, indent: int) -> None:
    pad = "  " * indent
    for key, value in payload.items():
        if isinstance(value, dict):
            sys.stdout.write(f"{pad}{key}:\n")
            _render(value, indent=indent + 1)
        elif isinstance(value, list):
            sys.stdout.write(f"{pad}{key}:\n")
            for entry in value:
                sys.stdout.write(f"{pad}  - {entry}\n")
        else:
            sys.stdout.write(f"{pad}{key}: {value}\n")


def _fail(message: str, *, remedy: str, as_json: bool, code: int) -> None:
    """Report a failure on stderr, keeping stdout a clean machine channel."""
    if as_json:
        sys.stderr.write(json.dumps({"error": message, "remedy": remedy}, indent=2) + "\n")
    else:
        sys.stderr.write(f"error: {message}\n{remedy}\n")
    raise typer.Exit(code)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def init_command(as_json: JsonOption = False) -> None:
    """Create ``.theurian/`` in the current repository.

    Never overwrites an existing file. Re-running reports an empty change set.
    """
    try:
        context = resolve_context()
    except TheurianError as exc:
        _fail(str(exc), remedy="Run this inside a Git repository.", as_json=as_json, code=1)
        return

    created = initialize_project(context.paths)
    gitignore_changed, _ = ensure_gitignore(context.paths.root)

    _emit(
        {
            "projectId": context.project_id.value,
            "root": str(context.paths.root),
            "knowledgeDirectory": str(DEFAULT_KNOWLEDGE_DIRECTORY),
            "createdPaths": list(created),
            "gitignoreUpdated": gitignore_changed,
            "changed": bool(created) or gitignore_changed,
        },
        as_json=as_json,
    )


# ---------------------------------------------------------------------------
# project
# ---------------------------------------------------------------------------


@project_app.command("register")
def project_register(
    path: Annotated[
        Path | None, typer.Argument(help="Project root. Defaults to the current directory.")
    ] = None,
    as_json: JsonOption = False,
) -> None:
    """Register a Git working tree as a project.

    One worktree is one project: two worktrees of the same repository can sit on
    different branches and therefore hold different knowledge (FR-P5).
    """
    try:
        context = resolve_context(path)
    except TheurianError as exc:
        _fail(str(exc), remedy="Run this inside a Git repository.", as_json=as_json, code=1)
        return

    project = Project(
        project_id=context.project_id,
        root_path=str(context.paths.root),
        repository_url=repository_url(context.paths.root),
        default_branch=default_branch(context.paths.root),
        knowledge_directory=DEFAULT_KNOWLEDGE_DIRECTORY,
        registered_at=context.clock.now(),
        last_seen_commit=current_commit(context.paths.root),
    )

    changed = registry().register(project)
    _emit(
        {
            "projectId": project.project_id.value,
            "root": project.root_path,
            "repositoryUrl": project.repository_url,
            "defaultBranch": project.default_branch,
            "initialized": context.paths.knowledge_dir.is_dir(),
            "changed": changed,
        },
        as_json=as_json,
    )


@project_app.command("unregister")
def project_unregister(
    project_id: Annotated[str, typer.Argument(help="Project id to remove.")],
    as_json: JsonOption = False,
) -> None:
    """Remove a project registration.

    Removes the registration and nothing else. Git-tracked knowledge under
    ``.theurian/`` is untouched.
    """
    try:
        removed = registry().unregister(ProjectId(project_id))
    except TheurianError as exc:
        _fail(
            str(exc),
            remedy="Check the project id with `theurian project list`.",
            as_json=as_json,
            code=1,
        )
        return

    _emit(
        {
            "projectId": project_id,
            "removed": removed,
            "knowledgePreserved": True,
            "note": "Git-tracked knowledge under .theurian/ was not touched.",
        },
        as_json=as_json,
    )


@project_app.command("list")
def project_list(as_json: JsonOption = False) -> None:
    """List registered projects."""
    entries = registry().load()
    _emit(
        {
            "count": len(entries),
            "projects": [{"projectId": pid, **entry} for pid, entry in sorted(entries.items())],
        },
        as_json=as_json,
    )


@project_app.command("status")
def project_status(as_json: JsonOption = False) -> None:
    """Report registration, state hash, and index freshness for this repository."""
    try:
        context = resolve_context()
    except TheurianError as exc:
        _emit(
            {"registered": False, "reason": str(exc), "indexStale": False},
            as_json=as_json,
        )
        return

    entries = registry().load()
    active = read_active_state(context.paths)
    database = context.paths.database_for(context.state_hash)

    _emit(
        {
            "projectId": context.project_id.value,
            "root": str(context.paths.root),
            "registered": context.project_id.value in entries,
            "initialized": context.paths.knowledge_dir.is_dir(),
            "stateHash": str(context.state_hash),
            "activeStateHash": None if active is None else str(active.state_hash),
            "stateBuilt": database.exists(),
            "indexStale": active is None or active.state_hash != context.state_hash,
            "migrationCount": len(context.loaded.migration_set),
            "headCommit": current_commit(context.paths.root),
            "schemaVersion": SCHEMA_VERSION,
            "engineVersion": MIGRATION_ENGINE_VERSION,
        },
        as_json=as_json,
    )


# ---------------------------------------------------------------------------
# migrate
# ---------------------------------------------------------------------------


@migrate_app.command("status")
def migrate_status(as_json: JsonOption = False) -> None:
    """Report applied and pending migrations."""
    context, database = _require_project(as_json)

    if not database.exists():
        _emit(
            {
                "stateHash": str(context.state_hash),
                "stateBuilt": False,
                "total": len(context.loaded.migration_set),
                "applied": 0,
                "pending": len(context.loaded.migration_set),
                "pendingIds": [str(m.migration_id) for m in context.loaded.migration_set],
            },
            as_json=as_json,
        )
        return

    with write_transaction(database, context.paths.write_lock) as connection:
        writer = SqliteWriter(connection)
        engine = MigrationEngine(context.clock, context.loaded.content_by_hash)
        try:
            plan = engine.plan(writer, context.project_id, context.loaded.migration_set)
        except MigrationChecksumMismatchError as exc:
            _fail(
                str(exc),
                remedy=(
                    "Restore the original migration file, or write a new migration. "
                    "Never edit an applied migration, and never 'fix' the recorded checksum."
                ),
                as_json=as_json,
                code=EXIT_STATE_ERROR,
            )
            return

    _emit(
        {
            "stateHash": str(context.state_hash),
            "stateBuilt": True,
            "total": plan.total,
            "applied": len(plan.already_applied),
            "pending": len(plan.pending),
            "pendingIds": [str(m.migration_id) for m in plan.pending],
        },
        as_json=as_json,
    )


@migrate_app.command("validate")
def migrate_validate(as_json: JsonOption = False) -> None:
    """Parse, schema-check, and order every migration without applying anything.

    Loading has already happened by the time this runs, so reaching here means
    the set parses, validates, resolves its content files inside the project
    root, and has a valid application order.
    """
    context, _ = _require_project(as_json)

    _emit(
        {
            "valid": True,
            "migrationCount": len(context.loaded.migration_set),
            "contentFileCount": len(context.loaded.content_checksums),
            "stateHash": str(context.state_hash),
            "applicationOrder": [str(m.migration_id) for m in context.loaded.migration_set],
        },
        as_json=as_json,
    )


@migrate_app.command("apply")
def migrate_apply(as_json: JsonOption = False) -> None:
    """Apply pending migrations to the canonical store.

    Idempotent: applying an unchanged set again reports zero applied and changes
    nothing (FR-K8).
    """
    context, database = _require_project(as_json)

    created = False
    if not database.exists():
        create_database(database, str(context.state_hash), MIGRATION_ENGINE_VERSION)
        created = True

    project = Project(
        project_id=context.project_id,
        root_path=str(context.paths.root),
        repository_url=repository_url(context.paths.root),
        default_branch=default_branch(context.paths.root),
        knowledge_directory=DEFAULT_KNOWLEDGE_DIRECTORY,
        registered_at=context.clock.now(),
        last_seen_commit=current_commit(context.paths.root),
    )

    try:
        with write_transaction(database, context.paths.write_lock) as connection:
            writer = SqliteWriter(connection)
            writer.register_project(project)
            engine = MigrationEngine(context.clock, context.loaded.content_by_hash)
            report = engine.apply(writer, context.project_id, context.loaded.migration_set)
    except MigrationChecksumMismatchError as exc:
        _fail(
            str(exc),
            remedy=(
                "Restore the original migration file, or write a new migration. "
                "Never edit an applied migration."
            ),
            as_json=as_json,
            code=EXIT_STATE_ERROR,
        )
        return
    except RevisionConflictError as exc:
        _fail(
            str(exc),
            remedy=(
                "Two changes targeted the same item. Read both revisions, decide which "
                "is correct, and write a new migration with the right expectedRevision. "
                "Theurian does not merge knowledge automatically."
            ),
            as_json=as_json,
            code=EXIT_STATE_ERROR,
        )
        return
    except (MigrationCycleError, MigrationError) as exc:
        _fail(
            str(exc),
            remedy="Fix the migration set, then retry.",
            as_json=as_json,
            code=EXIT_STATE_ERROR,
        )
        return

    active = write_active_state(
        context.paths, context.state_hash, len(context.loaded.migration_set), context.clock
    )

    _emit(
        {
            "stateHash": str(active.state_hash),
            "databaseCreated": created,
            "applied": [str(m) for m in report.applied],
            "skipped": [str(m) for m in report.skipped],
            "operationsApplied": report.operations_applied,
            "changed": report.changed,
        },
        as_json=as_json,
    )


def _verify_history(context: CommandContext, as_json: bool) -> None:
    """Fail if an already-applied migration has been edited (FR-K5, ADR-0005).

    Checked against the *previously active* state, not the one being built.
    Editing a migration changes the state hash (ADR-0016), so the next command
    would otherwise open a fresh empty database, find nothing applied, and report
    everything as fine -- silently losing the guarantee precisely when it fires.
    """
    active = read_active_state(context.paths)
    if active is None or active.state_hash == context.state_hash:
        return

    previous = context.paths.state / active.database_filename
    if not previous.exists():
        return

    try:
        with SqliteCanonicalStore(previous) as store:
            recorded = dict(store.applied_migrations(context.project_id))
    except TheurianError:
        # A previous state written by another schema version tells us nothing
        # about this one. Not an error: it is simply not evidence (ADR-0017).
        return

    try:
        verify_no_applied_migration_changed(recorded, context.loaded.migration_set)
    except MigrationChecksumMismatchError as exc:
        _fail(
            str(exc),
            remedy=(
                "Restore the original migration file, or record the change as a new "
                "migration. Never edit an applied migration, and never adjust the "
                "recorded checksum to match."
            ),
            as_json=as_json,
            code=EXIT_STATE_ERROR,
        )


def _require_project(as_json: bool) -> tuple[CommandContext, Path]:
    """Resolve a project context, or exit with an actionable message."""
    try:
        context = resolve_context()
    except MigrationChecksumMismatchError as exc:  # pragma: no cover - defensive
        _fail(
            str(exc),
            remedy="Restore the original migration file.",
            as_json=as_json,
            code=EXIT_STATE_ERROR,
        )
        raise
    except MigrationCycleError as exc:
        _fail(
            str(exc),
            remedy="Break the dependency cycle shown above, then retry.",
            as_json=as_json,
            code=EXIT_STATE_ERROR,
        )
        raise
    except MigrationError as exc:
        _fail(
            str(exc),
            remedy="Fix the migration file, then retry.",
            as_json=as_json,
            code=EXIT_STATE_ERROR,
        )
        raise
    except TheurianError as exc:
        _fail(
            str(exc),
            remedy="Run this inside an initialised Theurian project.",
            as_json=as_json,
            code=1,
        )
        raise

    _verify_history(context, as_json)
    return context, context.paths.database_for(context.state_hash)


__all__ = [
    "EXIT_STATE_ERROR",
    "init_command",
    "migrate_app",
    "project_app",
]
