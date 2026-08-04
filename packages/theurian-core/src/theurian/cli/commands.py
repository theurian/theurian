"""Project and migration CLI commands.

Every command supports ``--json``. The JSON shape is a published contract that
the Claude Code plugin depends on (CP-2), validated by ``tests/contract/``.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

import typer

from theurian.application.ingestion_service import (
    IngestionRequest,
    IngestionService,
    manifest_from,
)
from theurian.application.migration_engine import (
    MigrationEngine,
    verify_no_applied_migration_changed,
)
from theurian.application.project_service import (
    ACTIVE_POINTER_REMEDY,
    ProjectError,
    ProjectPaths,
    ensure_gitignore,
    initialize_project,
    read_active_state,
    write_active_state,
)
from theurian.cli.context import (
    CommandContext,
    current_commit,
    default_branch,
    find_git_root,
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
from theurian.domain.ports import SourceParser
from theurian.domain.project import DEFAULT_KNOWLEDGE_DIRECTORY, Project
from theurian.domain.state import ActiveState
from theurian.domain.values import MediaType
from theurian.infrastructure.filesystem.parsers.registry import ParserRegistry, detect_media_type
from theurian.infrastructure.sqlite.connection import (
    SchemaVersionMismatchError,
    create_database,
    write_transaction,
)
from theurian.infrastructure.sqlite.schema import SCHEMA_VERSION
from theurian.infrastructure.sqlite.store import (
    SqliteCanonicalStore,
    SqliteWriter,
    StateDatabaseUnreadableError,
)

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


def _context_remedy(exc: TheurianError, *, default: str) -> str:
    """The remedy that matches why resolving the project actually failed.

    ``resolve_context`` does three things — find the Git working tree, ask the
    registry which project this root is, and load and validate every migration —
    so a fixed "run this inside a Git repository" told a user with a malformed
    migration to go looking for a ``.git`` directory that was already there.
    """
    if isinstance(exc, ProjectError) and exc.remedy:
        return exc.remedy
    if isinstance(exc, MigrationCycleError):
        return "Break the dependency cycle shown above, then retry."
    if isinstance(exc, MigrationError):
        return "Fix the migration file, then retry."
    return default


@dataclass(frozen=True, slots=True)
class _RegistryRead:
    """What the registry could say, including that it could say nothing.

    A file whose top level does not parse holds no set of ids, so ``unreadable``
    -- which names *which* entries are broken -- cannot be computed at all, and
    ``failure`` carries the refusal in its place. Both arrive as values rather
    than as an exception because ``project status`` answers at exit 0: it reports
    for repositories that are not projects at all, and a raise from the registry
    read replaced its entire ``--json`` payload with a Rich traceback.
    """

    entries: dict[str, dict[str, str]]
    unreadable: tuple[str, ...]
    failure: TheurianError | None
    path: Path

    @property
    def failure_fields(self) -> dict[str, str]:
        """Why the registry could not be read and what cures it, or nothing.

        Emitted beside a ``registered`` of ``None``, never alone: a payload that
        says "cannot know" without saying why is a status a user cannot act on.

        Not the only reason ``registered`` can be ``None`` -- see :meth:`holds`,
        whose other case is explained by the ``unreadable`` list instead.
        """
        if self.failure is None:
            return {}
        return {
            "reason": str(self.failure),
            "remedy": _context_remedy(
                self.failure,
                default=f"Inspect {self.path}, or delete it and re-register each project.",
            ),
        }

    def holds(self, project_id: str | None) -> bool | None:
        """Whether this id is registered, or ``None`` where the file cannot say.

        **The single rule behind every ``registered`` field ``project status``
        emits, because its two branches disagreed.** The unresolved branch
        answered ``None`` whenever the registry held anything it could not read;
        the resolved branch consulted only :attr:`failure` and so answered
        ``False`` for an id whose *own* entry had become unreadable between
        ``resolve_context``'s read and this one -- the exact guess
        :meth:`ProjectRegistry.ids_for_root` refuses to make, made by the other
        half of the same command.

        ``None`` for an unknown id is the unresolved-status case: with no id to
        look up there is nothing to check membership of, so any entry the file
        holds and :meth:`ProjectRegistry.load` skips leaves the question open.

        Deliberately not "``None`` whenever anything is unreadable". An id
        present in :attr:`entries` is registered whatever some *other* entry
        looks like, and answering "cannot know" about something known is its own
        false report.
        """
        if self.failure is not None:
            return None
        if project_id is None:
            return None if self.unreadable else False
        if project_id in self.entries:
            return True
        # Absent from `entries` is not absent from the *file*: `load` skips an
        # entry that names no root path, and its id is still a key. `False` here
        # would tell a user their project is unregistered while `project
        # register` refuses to reuse the id and `project unregister` can still
        # remove it.
        return None if project_id in self.unreadable else False


def _read_registry() -> _RegistryRead:
    """Read the registry, turning a file it cannot parse into a value.

    The failure converted here is raised by ``ProjectRegistry._raw_entries``,
    which *every* reader reaches -- ``load``, ``unreadable_ids``,
    ``ids_for_root``, ``register`` and ``unregister`` alike. Auditing the callers
    of ``load`` alone therefore misses it, and that is precisely how
    ``project status`` came to call ``unreadable_ids()`` from inside the
    ``except`` block handling the raise ``ids_for_root`` had just made: exit 1
    with an empty stdout, from a command whose contract is exit 0 and a payload.
    """
    reg = registry()
    try:
        return _RegistryRead(
            entries=reg.load(), unreadable=reg.unreadable_ids(), failure=None, path=reg.path
        )
    except TheurianError as exc:
        return _RegistryRead(entries={}, unreadable=(), failure=exc, path=reg.path)


def _read_active(paths: ProjectPaths, as_json: bool) -> ActiveState | None:
    """The active state pointer, or an ``{error, remedy}`` exit if it is broken.

    The registry's sibling, and it exists for the same reason ``_read_registry``
    does: ``read_active_state`` raises, and a raise from a state file that
    nothing converts is a Rich traceback with an empty stdout, from a command
    whose contract is a JSON payload (CP-2). Measured before this existed, an
    ``active.json`` holding four bytes of text failed ``theurian migrate
    status``, ``migrate validate``, ``migrate apply``, ``ingest``, ``index
    build`` and ``index status`` that way in one go -- all six through the single
    unguarded read in ``_verify_history``.

    Not folded into ``_require_project``'s ``except`` chain, because the raise
    happens *after* it, in ``_verify_history``; and deliberately reachable from
    ``cli.index_commands`` too, so the two composition roots cannot print
    different cures for the same file.
    """
    try:
        return read_active_state(paths)
    except TheurianError as exc:
        _fail(
            str(exc),
            remedy=_context_remedy(exc, default=ACTIVE_POINTER_REMEDY),
            as_json=as_json,
            code=1,
        )
        raise


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
        _fail(
            str(exc),
            remedy=_context_remedy(exc, default="Run this inside a Git repository."),
            as_json=as_json,
            code=1,
        )
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
    project_id: Annotated[
        str | None,
        typer.Option(
            "--project-id",
            help="Override the id. Needed when another project already holds the default.",
        ),
    ] = None,
    as_json: JsonOption = False,
) -> None:
    """Register a Git working tree as a project.

    One worktree is one project: two worktrees of the same repository can sit on
    different branches and therefore hold different knowledge (FR-P5).

    The id defaults to the directory name, which is not unique across a machine.
    A clash is refused rather than silently resolved -- see
    :meth:`ProjectRegistry.register` -- and ``--project-id`` is how it is broken.

    ``--project-id`` is not a rename. A repository that already has an id is
    refused a second one, because the id is stamped into canonical rows and index
    chunks when they are written: a second registration would produce a project
    that is addressable and empty.
    """
    # Parsed apart from `resolve_context`, and failed apart from it: a malformed
    # `--project-id` and an unresolvable working tree are different problems with
    # different cures, and folding both into one `except TheurianError` sent an
    # invalid `--project-id` through `_context_remedy`'s default -- "run this
    # inside a Git repository" -- which is true and answers nothing, since the
    # command was already inside one.
    #
    # `if project_id:` on purpose, not `is not None`: an empty string is treated
    # the same as an omitted flag below and falls back to the derived id, and
    # that fallback must stay reachable rather than turning into a parse failure.
    parsed_id: ProjectId | None = None
    if project_id:
        try:
            parsed_id = ProjectId(project_id)
        except TheurianError as exc:
            _fail(
                str(exc),
                remedy=(
                    "--project-id must be lowercase kebab-case (letters, digits, and hyphens), "
                    "e.g. `team-two-api`."
                ),
                as_json=as_json,
                code=1,
            )
            return

    try:
        context = resolve_context(path, parsed_id)
    except TheurianError as exc:
        _fail(
            str(exc),
            remedy=_context_remedy(exc, default="Run this inside a Git repository."),
            as_json=as_json,
            code=1,
        )
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

    try:
        changed = registry().register(project)
    except TheurianError as exc:
        _fail(
            str(exc),
            remedy=_context_remedy(
                exc,
                default=(
                    "Choose a distinct id with `--project-id`, or unregister the other project."
                ),
            ),
            as_json=as_json,
            code=1,
        )
        return

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

    This is the terminal command of the remedy chain: ``project list``,
    ``project status``, ``setup``'s registry probe, ``project register`` and
    every MCP tool answer an unreadable registry by naming it. So the remedy it
    prints when it *itself* fails is the last thing a user reads before running
    out of instructions -- and a fixed "check the project id" told a user whose
    registry file does not parse at all to go and check an id that was never the
    problem, in a listing that fails on the same file. ``_context_remedy`` keeps
    the cure attached to the refusal that actually happened, exactly as ``init``,
    ``project register`` and ``_require_project`` already do.

    **The argument is not validated, on purpose.** It used to be parsed as a
    :class:`ProjectId` first, which made this command refuse exactly the ids it
    exists to remove: a registry key is whatever a hand edit left behind, and
    ``theurian project unregister 'Team One/API'`` failed with "check the project
    id with `theurian project list`" -- the listing that had just printed it.
    That is the closed loop this whole remedy chain is built to break. Removing a
    key needs no id semantics; only writing one does, and writing goes through
    ``project register``.
    """
    try:
        removed = registry().unregister(project_id)
    except TheurianError as exc:
        _fail(
            str(exc),
            remedy=_context_remedy(
                exc, default="Check the project id with `theurian project list`."
            ),
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


def _listed_project(project_id: str, entry: dict[str, str]) -> dict[str, str]:
    """One row of ``project list``: the whole entry, under the id that keys it.

    The id is the registry *key*, and any ``projectId`` the entry itself carries
    is dropped rather than merged. ``{"projectId": pid, **entry}`` published the
    entry's own value instead, because a later key wins -- and the registry is
    hand-editable, which is the premise ``unreadable`` exists for. The result was
    not a cosmetic mislabel. Measured against two registered projects with one
    hand-edited line, ``alpha``'s entry claiming ``projectId: beta``: this
    command listed ``beta`` twice, ``alpha`` appeared on no surface at all so the
    ``theurian project unregister <id>`` that every remedy in this module names
    could not be typed for it, and the id that *was* shown against ``alpha``'s
    row unregistered ``beta``.

    Kept whole rather than narrowed to the fields a caller acts on, which is
    where this deliberately differs from the ``project.list`` MCP tool: this is
    the surface a user reads to see what the registry actually holds, a field
    Theurian itself never writes included.
    """
    return {"projectId": project_id, **{k: v for k, v in entry.items() if k != "projectId"}}


@project_app.command("list")
def project_list(as_json: JsonOption = False) -> None:
    """List registered projects.

    ``unreadable`` names the ids whose entries the registry could not parse.
    They are reported here rather than only where they break something, because
    this is the command a user runs to find out what is registered -- and a
    skipped entry that only this command could show was a project that vanished
    with nothing said. The id is also the argument
    ``theurian project unregister`` needs, which is the remedy every other
    surface prints for them, so hiding it here made that remedy untypable.

    Always emitted, empty list included: a consumer that has to branch on whether
    a key is present will eventually forget to.

    The failure this command cannot recover from -- a file that is not JSON at
    all, so there is no set of ids to partition -- is reported rather than
    raised. It used to escape as a Rich traceback, which mattered more here than
    anywhere else: this is the command every other surface names when it wants a
    user to go and look, `project unregister`'s remedy included, so the one place
    `_registry_reset_remedy` was written for was the one place it never reached.
    """
    reg = registry()
    try:
        entries = reg.load()
        unreadable = reg.unreadable_ids()
    except TheurianError as exc:
        _fail(
            str(exc),
            remedy=_context_remedy(
                exc, default=f"Inspect {reg.path}, or delete it and re-register each project."
            ),
            as_json=as_json,
            code=1,
        )
        return
    _emit(
        {
            "count": len(entries),
            "projects": [_listed_project(pid, entry) for pid, entry in sorted(entries.items())],
            "unreadable": list(unreadable),
            **(
                {
                    "remedy": (
                        "Remove them with `theurian project unregister <id>`. Until then, "
                        "commands that resolve a project from the current directory refuse "
                        "rather than guess."
                    )
                }
                if unreadable
                else {}
            ),
        },
        as_json=as_json,
    )


def _pointer_failure_fields(failure: TheurianError | None) -> dict[str, str]:
    """Why the state pointer could not be read and what cures it, or nothing.

    The shape :attr:`_RegistryRead.failure_fields` uses, for the other file this
    command reads. Kept as a function rather than a second dataclass because the
    pointer read has no equivalent of ``holds`` -- there is one value to lose and
    no membership question to answer about it.
    """
    if failure is None:
        return {}
    return {
        "reason": str(failure),
        "remedy": _context_remedy(failure, default=ACTIVE_POINTER_REMEDY),
    }


def _unresolved_status(exc: TheurianError) -> dict[str, Any]:
    """``project status`` for a repository whose project could not be resolved.

    Exit 0 is kept deliberately: unlike every other command here, ``status``
    answers for repositories that are not yet a project at all, and "not
    registered" -- for any reason, including "not inside a Git repository" -- is
    a legitimate status rather than a command failure. Switching to the
    ``{error, remedy}`` / non-zero contract the other commands use would make a
    routine "you haven't set this up" status look like a crash.

    But an ambiguous registry is not a routine "nothing here yet": it is a
    problem only the user can fix, and ``ProjectError.remedy`` is the only place
    the ``project unregister`` invocations that fix it are named. Dropping it
    left the command a confused user reaches for *first* reporting a problem with
    no way out. Exit code stays 0; the remedy travels into the payload instead,
    where both a human reading the rendered output and a script reading JSON can
    find it.

    ``statePointerCorrupt`` is deliberately absent here rather than ``false``.
    Nothing on this branch has resolved a project, so no state pointer was
    looked at, and emitting the field would answer a question this branch never
    asked -- the same reason ``registered`` refuses to be ``False`` above.
    """
    payload: dict[str, Any] = {"registered": False, "reason": str(exc), "indexStale": False}
    if isinstance(exc, ProjectError) and exc.remedy:
        payload["remedy"] = exc.remedy

    # `resolve_context` never got as far as asking the registry whether this root
    # is registered -- for any reason, a broken migration included -- while the
    # registry cannot answer for itself either: it holds an entry that cannot be
    # read, or it does not parse at all. `registered: False` would then be the
    # same guess `ids_for_root` refuses to make: an unreadable entry names no
    # root, so there is no way to tell whether it is *this* directory's own, and
    # a file that does not parse does not even have entries to ask about.
    #
    # `find_git_root` is checked apart from the registry so a plain "not inside a
    # Git repository" -- which has nothing to do with the registry -- keeps its
    # honest `False` rather than being dragged into an ambiguity that could not
    # possibly be about it. That directory is not a project whatever the registry
    # says, and `theurian project list` is the surface that reports the file.
    read = _read_registry()
    if find_git_root(Path.cwd()) is not None:
        payload["registered"] = read.holds(None)
    # Stays a list even when the file did not parse, because a caller that
    # iterates it must not have to branch first. That the set of ids is *unknown*
    # rather than empty is carried by `registered: None` and by `reason`, which
    # is the registry's own refusal here: `resolve_context` consults the registry
    # before it loads migrations, so a file-level failure is what raised above.
    payload["unreadable"] = list(read.unreadable)
    return payload


@project_app.command("status")
def project_status(as_json: JsonOption = False) -> None:
    """Report registration, state hash, and index freshness for this repository."""
    try:
        context = resolve_context()
    except TheurianError as exc:
        _emit(_unresolved_status(exc), as_json=as_json)
        return

    # Read once, through the reader that cannot raise. Reaching here means the
    # registry parsed a moment ago -- `resolve_context` asked it which project
    # this root is -- but "a moment ago" is not "now": the file lives in the
    # user's home directory, another `theurian` process shares it, and a hand
    # edit lands between the two reads. A raise at that point would cost this
    # command its whole payload for a file it only consults for one field.
    read = _read_registry()

    # The pointer gets the same treatment for the same reason, and it needs it
    # more: this command reads it on every run, not only in a race, and an
    # unreadable one used to end the whole command in a traceback with an empty
    # stdout. `_read_active` is not usable here -- it exits, and this command
    # answers at exit 0 even for a directory that is not a project at all.
    pointer_failure: TheurianError | None = None
    active: ActiveState | None = None
    try:
        active = read_active_state(context.paths)
    except TheurianError as exc:
        pointer_failure = exc

    database = context.paths.database_for(context.state_hash)

    _emit(
        {
            "projectId": context.project_id.value,
            "root": str(context.paths.root),
            # `None` is this command's "cannot know", and both branches now get
            # it from `holds` rather than each deciding for itself -- which is
            # how the resolved one came to answer `False` where the unresolved
            # one answered `None` for the same file.
            "registered": read.holds(context.project_id.value),
            "initialized": context.paths.knowledge_dir.is_dir(),
            "stateHash": str(context.state_hash),
            "activeStateHash": None if active is None else str(active.state_hash),
            "stateBuilt": database.exists(),
            "indexStale": active is None or active.state_hash != context.state_hash,
            # `activeStateHash: null` alone cannot say which of two things
            # happened, and the two have opposite cures: `migrate apply` for a
            # project that has never been applied, and *delete this file, then*
            # apply for one whose pointer is corrupt. That is exactly the pair
            # `ActiveIndexPointer` was split to distinguish for the index
            # pointer, and `index status` publishes it as `indexPointerCorrupt`;
            # this is the same statement about the file beside it, under a name
            # that matches. Always present, `false` included.
            "statePointerCorrupt": pointer_failure is not None,
            "migrationCount": len(context.loaded.migration_set),
            "headCommit": current_commit(context.paths.root),
            "schemaVersion": SCHEMA_VERSION,
            "engineVersion": MIGRATION_ENGINE_VERSION,
            # Always present, empty list included (`project list`'s model):
            # entries elsewhere in the registry that could not be read. Normally
            # empty here -- `resolve_context` above would have raised -- but a
            # field that only sometimes exists is a field a caller eventually
            # forgets to check for.
            "unreadable": list(read.unreadable),
            # `reason`/`remedy` is this command's one vocabulary for "why part of
            # this answer is missing", spoken by `_unresolved_status` and by
            # `failure_fields` already. Two files can be unreadable at once, so
            # the pair is filled by the pointer first and the registry second,
            # and the registry wins: `registered` degrades to `None` when the
            # registry fails, and `failure_fields` exists precisely because a
            # "cannot know" with no reason beside it is unactionable. Nothing is
            # lost when it wins -- `statePointerCorrupt` above carries the
            # pointer failure whatever happens, and its cure is a fixed string
            # both this command and `theurian index status` print.
            **_pointer_failure_fields(pointer_failure),
            **read.failure_fields,
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


daemon_app = typer.Typer(help="Manage the local Theurian daemon.", no_args_is_help=True)

# The daemon commands import uvicorn and the MCP SDK lazily. Measured: importing
# them at module scope takes `theurian --version` from 170 ms to 600 ms, which
# alone exceeds the SessionStart p95 budget of 300 ms (NFR-2) -- and the hook
# runs on every session while never touching these commands.


@daemon_app.command("start")
def daemon_start(
    foreground: Annotated[
        bool, typer.Option("--foreground", help="Run in this process rather than detaching.")
    ] = False,
    port: Annotated[int, typer.Option("--port", help="Port to bind on 127.0.0.1.")] = 7419,
    as_json: JsonOption = False,
) -> None:
    """Start the single local daemon.

    Reusing an already-running daemon is a success, not an error: one process
    per user per machine is the guarantee, and a second starter confirming the
    first is healthy has done its job (ADR-0002).
    """
    from theurian.daemon.runner import serve  # noqa: PLC0415 - see the note above

    if not foreground:
        _start_detached(port=port, as_json=as_json)
        return

    try:
        check = serve(port=port)
    except RuntimeError as exc:
        _fail(str(exc), remedy="Run `theurian doctor`.", as_json=as_json, code=1)
        return

    _emit({"decision": check.decision.value, "detail": check.detail}, as_json=as_json)


def _start_detached(*, port: int, as_json: bool) -> None:
    """Ask the OS service manager to start the daemon.

    Theurian never daemonises itself. launchd and systemd already do supervision,
    restart-on-failure, and log redirection correctly, and a hand-rolled
    double-fork would be a second, worse implementation of all three.

    Starting an *unregistered* service is refused rather than improvised. That
    refusal is what keeps the SessionStart hook honest: a hook may resume a
    service the user already approved, but it must never be the thing that
    installs one (FR-L3).
    """
    import asyncio  # noqa: PLC0415 - see the note above

    from theurian.cli.setup_commands import _executable  # noqa: PLC0415
    from theurian.daemon.instance import probe_health  # noqa: PLC0415
    from theurian.domain.ports.daemon_manager import ServiceState  # noqa: PLC0415
    from theurian.infrastructure.services import detect_manager  # noqa: PLC0415

    if probe_health(port=port) is not None:
        _emit({"decision": "reuse", "detail": "A daemon is already running."}, as_json=as_json)
        return

    service = detect_manager(executable=_executable())
    if service is None:
        _fail(
            "This platform has no user-scoped service manager.",
            remedy="Run `theurian daemon start --foreground`.",
            as_json=as_json,
            code=1,
        )
        return

    status = asyncio.run(service.status())
    if status.state is ServiceState.NOT_INSTALLED:
        _fail(
            "No Theurian service is registered, so there is nothing to start.",
            remedy="Run `theurian setup` once. Starting is not an install.",
            as_json=as_json,
            code=1,
        )
        return

    asyncio.run(service.start())
    _emit(
        {
            "decision": "start",
            "detail": f"Asked {service.platform_id} to start {status.service_identifier}.",
        },
        as_json=as_json,
    )


@daemon_app.command("stop")
def daemon_stop(
    as_json: JsonOption = False,
) -> None:
    """Stop the daemon by asking the service manager that owns it.

    Deliberately not a PID-based kill. This design uses an advisory lock rather
    than a PID file precisely because PIDs are recycled, so a stale PID can name
    a live unrelated process -- and signalling one of those is exactly the kind
    of damage a convenience command should not be able to do (ADR-0002).

    A daemon started with `--foreground` is stopped with Ctrl-C, by whoever
    started it.
    """
    import asyncio  # noqa: PLC0415 - see the note above

    from theurian.cli.setup_commands import _executable  # noqa: PLC0415
    from theurian.domain.ports.daemon_manager import ServiceState  # noqa: PLC0415
    from theurian.infrastructure.services import detect_manager  # noqa: PLC0415

    service = detect_manager(executable=_executable())
    if service is None:
        _fail(
            "This platform has no user-scoped service manager.",
            remedy="Stop a foreground daemon with Ctrl-C in the terminal running it.",
            as_json=as_json,
            code=1,
        )
        return

    status = asyncio.run(service.status())
    if status.state is ServiceState.NOT_INSTALLED:
        _fail(
            "No Theurian service is registered, so there is nothing to stop.",
            remedy="A daemon started with `--foreground` is stopped with Ctrl-C.",
            as_json=as_json,
            code=1,
        )
        return

    asyncio.run(service.stop())
    _emit(
        {"stopped": True, "service": status.service_identifier},
        as_json=as_json,
    )


@daemon_app.command("status")
def daemon_status(
    port: Annotated[int, typer.Option("--port")] = 7419,
    as_json: JsonOption = False,
) -> None:
    """Report whether a daemon is running, and which one.

    Side-effect-free and cheap: this is what the SessionStart hook calls, so it
    must stay well inside the latency budget (NFR-2).
    """
    import asyncio  # noqa: PLC0415 - see above

    from theurian.cli.setup_commands import _executable  # noqa: PLC0415
    from theurian.daemon.instance import probe_health  # noqa: PLC0415
    from theurian.daemon.runner import LOCK_FILENAME  # noqa: PLC0415
    from theurian.domain.ports.daemon_manager import ServiceState  # noqa: PLC0415
    from theurian.infrastructure.secrets.file_store import (  # noqa: PLC0415
        default_data_dir,
    )
    from theurian.infrastructure.services import detect_manager  # noqa: PLC0415

    data_dir = default_data_dir()
    health = probe_health(port=port)

    # A live daemon is the strongest evidence there is; nothing a service
    # manager reports would change the answer, so this asks first and cheaply.
    #
    # When nothing answers, the two remaining states demand opposite responses
    # from the SessionStart hook: a registered-but-stopped service may be
    # started (a user-approved service resuming), while an absent one must send
    # the user to `/theurian:setup` rather than have a hook install anything
    # (FR-L3). Only the service manager can tell them apart, so `unknown` is
    # reserved for the platform that has none.
    state = ServiceState.RUNNING
    service_id: str | None = None
    if health is None:
        service = detect_manager(executable=_executable())
        if service is None:
            state = ServiceState.UNKNOWN
        else:
            status = asyncio.run(service.status())
            state = status.state
            service_id = status.service_identifier

    _emit(
        {
            "state": state.value,
            "listening": health is not None,
            "version": (health or {}).get("version"),
            "dataDir": str(data_dir),
            "lockFile": str(data_dir / LOCK_FILENAME),
            "endpoint": f"http://127.0.0.1:{port}/mcp",
            "service": service_id,
        },
        as_json=as_json,
    )


class _Resolver:
    """Adapts the parser registry to the application's ``ParserResolver``.

    Keeps media-type detection and parser lookup behind one object, so the
    application layer names neither the registry nor the detector (ADR-0003).
    """

    def __init__(self) -> None:
        self._registry = ParserRegistry()

    def detect(self, path: PurePosixPath, data: bytes) -> MediaType | None:
        return detect_media_type(path, data)

    def for_media_type(self, media_type: MediaType) -> SourceParser | None:
        return self._registry.for_media_type(media_type)


def ingest_command(as_json: JsonOption = False) -> None:
    """Parse and normalize this project's knowledge and specification sources.

    Ingestion stores *evidence*, never approved knowledge. Promotion still runs
    through a migration and a human (ADR-0013).

    A parse failure fails one document, not the run: a malformed file among two
    hundred must not make the other 199 unavailable. The exit code reflects
    whether every document parsed, so a script can tell a clean run from a
    partial one.
    """
    context, _ = _require_project(as_json)

    manifest_path = context.paths.knowledge_dir / "cache" / "ingestion.json"
    previous: dict[str, str] = {}
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            # The manifest is a derived cache. A corrupt one costs a full
            # reparse, which is the correct price -- refusing to run would make
            # a disposable file able to block the command.
            #
            # Which is what it did for two of the three ways it can be corrupt.
            # `JSONDecodeError` alone let a manifest of arbitrary bytes end
            # `theurian ingest` in a Rich traceback with an empty stdout -- the
            # disposable file blocking the command this comment says it must not
            # be able to block. Same file, same price, same reparse.
            #
            # Bounded to *reading* it on purpose. A manifest this process cannot
            # write still ends the run, at the `write_text` below, and that is a
            # different family with more members than this line -- every atomic
            # write in `project_service` is in it. One of them fixed here would
            # be a family half-closed, which is worse than a family named: it is
            # tracked for Milestone 6 with the canonical-store read failures.
            previous = {}

    service = IngestionService(_Resolver())
    report = service.ingest(
        IngestionRequest(
            project_root=context.paths.root,
            knowledge_dir=context.paths.knowledge_dir,
            known_hashes=previous,
            commit_sha=current_commit(context.paths.root),
            repository=repository_url(context.paths.root),
        )
    )

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest_from(report, previous), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    _emit(
        {
            "ingested": len(report.documents),
            "unchanged": len(report.unchanged),
            "skipped": len(report.skipped),
            "failed": len(report.failures),
            "succeeded": report.succeeded,
            "documents": [
                {
                    "path": d.path,
                    "title": d.title,
                    "contentType": str(d.content_type),
                    "parser": d.parser_id,
                    "structured": d.structured is not None,
                }
                for d in report.documents
            ],
            "failures": [
                {"path": f.path, "reason": f.reason, "mediaType": f.media_type}
                for f in report.failures
            ],
            "warnings": [
                {"code": w.code, "message": w.message, "location": w.location}
                for w in report.warnings
            ],
        },
        as_json=as_json,
    )

    if not report.succeeded:
        # Documents that did parse are still ingested and the manifest is still
        # written; the non-zero code says "not everything got in", which is a
        # different thing from "the command broke".
        raise typer.Exit(EXIT_STATE_ERROR)


def _verify_history(context: CommandContext, as_json: bool) -> None:
    """Fail if an already-applied migration has been edited (FR-K5, ADR-0005).

    Checked against the *previously active* state, not the one being built.
    Editing a migration changes the state hash (ADR-0016), so the next command
    would otherwise open a fresh empty database, find nothing applied, and report
    everything as fine -- silently losing the guarantee precisely when it fires.

    An unreadable pointer -- or a previous database this build cannot read -- is
    refused rather than treated as "no previous state". The early returns below
    are for the cases where there is genuinely nothing to check against; a file
    that exists and cannot be read is not one of them, and swallowing it would
    drop the FR-K5 check for every command routed through
    :func:`_require_project` without saying so.
    """
    active = _read_active(context.paths, as_json)
    if active is None or active.state_hash == context.state_hash:
        return

    previous = context.paths.state / active.database_filename
    if not previous.exists():
        return

    try:
        with SqliteCanonicalStore(previous) as store:
            recorded = dict(store.applied_migrations(context.project_id))
    except SchemaVersionMismatchError:
        # A previous state written by another schema version tells us nothing
        # about this one. Not an error: it is simply not evidence (ADR-0017).
        return
    except StateDatabaseUnreadableError as exc:
        # Caught apart from the mismatch above rather than sharing one `except
        # TheurianError`, because the two say opposite things. A state at
        # another schema version is *not evidence*. A state this build cannot
        # read is evidence it could not reach -- and returning here reported a
        # clean history, with a zero exit, for every command routed through
        # `_require_project`: exactly the silence this docstring forbids.
        _fail(
            f"Theurian cannot confirm that no applied migration has been edited "
            f"(FR-K5): the previously active state database is unreadable. {exc}",
            remedy=(
                "Delete `.theurian/state/` and run `theurian migrate apply` to rebuild it "
                "from the Git-tracked migrations. The rebuilt history records the files as "
                "they are now, so an edit made before that point stops being detectable."
            ),
            as_json=as_json,
            code=EXIT_STATE_ERROR,
        )
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
            remedy=_context_remedy(exc, default="Run this inside an initialised Theurian project."),
            as_json=as_json,
            code=1,
        )
        raise

    _verify_history(context, as_json)
    return context, context.paths.database_for(context.state_hash)


__all__ = [
    "EXIT_STATE_ERROR",
    "daemon_app",
    "ingest_command",
    "init_command",
    "migrate_app",
    "project_app",
]
