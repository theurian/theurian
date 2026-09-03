"""Project and migration CLI commands.

Every command supports ``--json``. The JSON shape is a published contract that
the Claude Code plugin depends on (CP-2), validated by ``tests/contract/``.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Final

import typer

from theurian.application.forest_builder import ForestBuilder
from theurian.application.ingestion_service import (
    IngestionRequest,
    IngestionService,
    manifest_from,
)
from theurian.application.migration_alias_guards import alias_item_collision_violations
from theurian.application.migration_body_guards import duplicate_content_file_violations
from theurian.application.migration_engine import (
    MigrationEngine,
    WithdrawalCandidate,
    run_static_migration_guards,
    unenforceable_scope_violations,
    verify_no_applied_migration_changed,
    verify_no_applied_migration_removed,
)
from theurian.application.project_service import (
    ACTIVE_POINTER_REMEDY,
    BuildProvenance,
    ProjectPaths,
    ensure_gitignore,
    entry_root,
    initialize_project,
    read_active_index,
    read_active_state,
    write_active_state,
)
from theurian.application.withdrawal_purge import (
    UNTRUSTED_SOURCE_INDEX,
    WithdrawalPurge,
    make_forest_recompute,
    publish_purge_for_withdrawal,
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
from theurian.cli.index_status_report import index_staleness
from theurian.cli.migration_pipeline import apply_migration_set
from theurian.cli.output import escape_terminal_controls
from theurian.domain.errors import (
    AliasItemCollisionError,
    DuplicateContentFileError,
    MigrationChecksumMismatchError,
    MigrationCycleError,
    MigrationError,
    MigrationHistoryMissingError,
    PathEscapeError,
    RevisionConflictError,
    TheurianError,
    UnenforceableScopeError,
)
from theurian.domain.extras import (
    DAEMON_EXTRA,
    DAEMON_EXTRA_REMEDY,
    provided_by_daemon_extra,
)
from theurian.domain.identifiers import MigrationId, ProjectId
from theurian.domain.migration import MIGRATION_ENGINE_VERSION, MigrationSet
from theurian.domain.ports import SourceParser
from theurian.domain.project import DEFAULT_KNOWLEDGE_DIRECTORY, Project
from theurian.domain.state import ActiveState
from theurian.domain.values import MediaType
from theurian.infrastructure.embedding import HashingEmbedding
from theurian.infrastructure.filesystem.parsers.registry import ParserRegistry, detect_media_type
from theurian.infrastructure.raptor.extractive import ExtractiveSummarizer
from theurian.infrastructure.sqlite.connection import (
    SchemaVersionMismatchError,
    StateDatabaseUnreadableError,
    WriteLock,
    WriteLockTimeoutError,
    create_database,
    write_transaction,
)
from theurian.infrastructure.sqlite.index_store import SqliteIndexStore
from theurian.infrastructure.sqlite.schema import SCHEMA_VERSION
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore, SqliteWriter

#: Exit code for a knowledge-state problem the user must resolve: a checksum
#: mismatch, a revision conflict, a dependency cycle. Distinct from 1 so a script
#: can tell "your knowledge needs attention" from "the command broke".
#:
#: **SEC-8's input-cap refusals report 1 rather than this, and the load path is
#: therefore not uniformly graded.** `InputTooLargeError` and
#: `IrregularSourceFileError` (#215) are `SecurityError`s that no branch of
#: :func:`_require_project` names, so they take its generic `except
#: TheurianError` branch at 1, while `PathEscapeError` and every `MigrationError`
#: subtype -- `MigrationContentUnreadableError` among them -- are named there and
#: take this code, on refusals that can come from the same `read_source_file`
#: call. That is the shipped contract: 0.1.0.dev9 grades an oversized
#: `contentFile` as 1, and the branch structure that decides it is unchanged
#: since that cut. It is left alone deliberately -- which code a refusal reports
#: is a published contract, and moving one is a compatibility change with its own
#: note, not a detail of adding a refusal.
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
        safe_key = escape_terminal_controls(key)
        if isinstance(value, dict):
            sys.stdout.write(f"{pad}{safe_key}:\n")
            _render(value, indent=indent + 1)
        elif isinstance(value, list):
            sys.stdout.write(f"{pad}{safe_key}:\n")
            for entry in value:
                sys.stdout.write(f"{pad}  - {escape_terminal_controls(entry)}\n")
        else:
            sys.stdout.write(f"{pad}{safe_key}: {escape_terminal_controls(value)}\n")


def _fail(message: str, *, remedy: str, as_json: bool, code: int) -> None:
    """Report a failure on stderr, keeping stdout a clean machine channel."""
    if as_json:
        sys.stderr.write(json.dumps({"error": message, "remedy": remedy}, indent=2) + "\n")
    else:
        sys.stderr.write(
            f"error: {escape_terminal_controls(message)}\n{escape_terminal_controls(remedy)}\n"
        )
    raise typer.Exit(code)


#: Cure for a canonical state database this build cannot open or interpret.
#:
#: State is derived and git-ignored (ADR-0004) and is never migrated in place
#: (ADR-0017), so the answer is always to discard the file and replay the
#: Git-tracked migrations. Repeats what ``StateDatabaseUnreadableError`` already
#: says in its own message on purpose: ``remedy`` is a field a caller reads
#: without parsing ``error``, and a contract field that is sometimes empty is one
#: every caller eventually stops checking.
STATE_REBUILD_REMEDY: Final = (
    "Delete `.theurian/state/` and run `theurian migrate apply` to rebuild it from the "
    "Git-tracked migrations. Nothing authored is lost."
)

#: Cure for an OS or driver fault met *at* the state database -- the class
#: ``(OSError, sqlite3.Error)`` names and ``StateDatabaseUnreadableError`` does
#: not (#484).
#:
#: **What reaches it, measured rather than reasoned.** The two clauses that
#: publish this wrap a whole ``write_transaction`` call, so their reach is every
#: statement inside it and not the open alone -- an earlier draft of this comment
#: said "the file was never opened", and the contention fault below arrived after
#: the open, after ``_prepare`` and after ``BEGIN IMMEDIATE``, which is what
#: falsified it. The members measured on this branch are open-time: a directory
#: sitting at the path an ADR-0004 doctored `.theurian/state/` can deliver, and a
#: state directory the process cannot write, both of which fail in
#: ``sqlite3.connect`` itself -- outside ``_prepare``'s conversion, which is why
#: they arrive as the driver's own ``SQLITE_CANTOPEN`` rather than as
#: ``StateDatabaseUnreadableError``.
#:
#: **What must never reach it is a transient fault**, and that is now structural
#: rather than a matter of wording here: a write conflict is converted at its
#: source into ``WriteTransactionBusyError``, a ``TheurianError`` caught by the
#: clause *above* each of these, carrying a wait-and-retry cure. This constant
#: instructs deleting derived state, so every fault that keeps it has to be one
#: that leaves the state genuinely unusable.
#:
#: Composed from :data:`STATE_REBUILD_REMEDY` rather than restating it, so the
#: sentence that deletes something has one spelling in this module. The
#: precondition leads and the rebuild trails, the shape
#: ``FindingsStoreError``'s write remedy and ``_LOCK_ACQUIRE_REMEDY`` both take:
#: a cure that opens with "delete your state" for what is really a permissions
#: problem sends the reader past the thing that is actually wrong.
_STATE_DATABASE_FAULT_REMEDY: Final = (
    "Check that `.theurian/state/` is writable, on a supported filesystem (not NFS; "
    f"ADR-0018), and holds a state database rather than something else. {STATE_REBUILD_REMEDY}"
)

#: Cure for `UnenforceableScopeError` when the offending revision has not yet
#: been applied (issue #63). Editing the migration file is unconditionally
#: safe here: nothing has recorded a checksum for it, so nothing can trip
#: FR-K5's tamper check. Named once and shared by `migrate validate` and
#: `migrate apply`'s "unapplied" branch, which is what keeps the fix they
#: point at the same -- the remedy field is a contract callers read without
#: parsing `error`.
UNENFORCEABLE_SCOPE_REMEDY_UNAPPLIED: Final = (
    "Set tenantId to 'local' and aclGroup to 'default' in the revision's metadata, "
    "then retry. A later milestone lifts this refusal once a hosted deployment has "
    "a real AuthorizationProvider to enforce other values against (issue #63)."
)

#: Cure for `UnenforceableScopeError` when the offending revision was already
#: applied -- only reachable on a project built by a version older than this
#: refusal (`0.1.0.dev0`, `0.1.0.dev1`). Editing the field in place would
#: change the migration file's checksum and trip FR-K5's tamper check instead
#: of this one, and that check's own remedy says to restore the file --
#: looping the reader between two remedies with no documented way out (issue
#: #63's HIGH-1). The only procedure verified end to end: edit the field(s),
#: then rebuild state from empty rather than try to reconcile it in place.
UNENFORCEABLE_SCOPE_REMEDY_APPLIED: Final = (
    "This revision was already applied, by an earlier build that did not refuse it. "
    "Edit tenantId to 'local' and aclGroup to 'default' in every migration naming "
    "another value, delete `.theurian/state/`, then run `theurian migrate apply` to "
    "rebuild canonical state from the edited migrations -- state is fully "
    "reconstructible from the Git-tracked migrations (FR-K4). This discards FR-K5's "
    "tamper-evidence for every migration applied before that point, so do this once, "
    "deliberately, not as a routine fix (issue #63)."
)


#: Cure for `DuplicateContentFileError` (issue #210). One string rather than
#: the applied/unapplied pair `UnenforceableScopeError` needs: the fix here is
#: an edit to the *later* migration, and a later migration that has already
#: been applied is only reachable from a build older than this refusal, so the
#: rebuild procedure is named as the second half of one remedy instead of being
#: selected by a store read. Naming it is not optional -- editing an applied
#: migration trips FR-K5's checksum guard, whose own remedy says to restore the
#: file, and a remedy that stopped at "edit it" would loop the reader between
#: the two the way issue #63's HIGH-1 did.
DUPLICATE_CONTENT_FILE_REMEDY: Final = (
    "Give the later revision a body file of its own: copy the body to a new path under "
    "`.theurian/knowledge/` and point that migration's contentFile at it, then retry. "
    "Pin both bodies with contentSha256 while you are there, so a later edit to either is "
    "refused rather than silently adopted. If that migration was already applied, editing "
    "it also trips the applied-migration checksum guard -- delete `.theurian/state/` after "
    "the edit and run `theurian migrate apply`, which rebuilds canonical state from the "
    "corrected migrations (FR-K4)."
)


#: Cure for `AliasItemCollisionError` (SEC-13, T-21). An alias key and an item id
#: must not be the same string, or a lookup for the retired id resolves to the
#: item the alias points at and surfaces content the retired item withholds. The
#: one shape this allows is a rename: deprecate the old item first, then alias it.
#: Names the rebuild path as the second half of one remedy for the same reason
#: `DUPLICATE_CONTENT_FILE_REMEDY` does -- editing an applied migration trips
#: FR-K5's checksum guard, so a remedy that stopped at "edit it" would loop the
#: reader between the two (issue #63's HIGH-1).
ALIAS_ITEM_COLLISION_REMEDY: Final = (
    "Remove the addAlias, or give the item a distinct id -- an alias key and an item id must "
    "not be the same string. If this is a rename, deprecate the old item first "
    "(deprecateItem), the one shape this allows: the retired id then resolves to its "
    "successor without exposing withheld content. If the offending migration was already "
    "applied, editing it also trips the applied-migration checksum guard -- delete "
    "`.theurian/state/` after the edit and run `theurian migrate apply`, which rebuilds "
    "canonical state from the corrected migrations (FR-K4)."
)


#: Fallback cure for a whole-set guard refusal :func:`_refuse_a_set_a_static_guard_rejects`
#: has no dedicated branch for -- a *fourth* guard added to
#: :func:`~theurian.application.migration_engine.run_static_migration_guards` whose
#: error type this function does not name. Generic on purpose: only the guard that
#: raised knows the exact fix, so the new error's own ``.remedy`` is preferred when
#: it carries one, and this is the honest floor when it does not.
_UNNAMED_GUARD_REFUSAL_REMEDY: Final = (
    "Fix the migration set the guard refused, then retry. `theurian migrate validate` "
    "reports what can be checked without touching state."
)


#: Every canonical-state database this project has ever built. Excludes
#: `theurian-index-*.sqlite`, which lives in the same directory
#: (`ProjectPaths.state`) but is a different schema entirely.
_STATE_DATABASE_GLOB: Final = "theurian-state-*.sqlite"


def _applied_migration_ids(paths: ProjectPaths, project_id: ProjectId) -> frozenset[MigrationId]:
    """Migration ids recorded as applied in *any* canonical-state database on disk.

    Read-only, and never the authority for whether a migration is safe to
    apply -- that is :meth:`MigrationEngine.plan`'s job, inside a real write
    transaction, against the one database currently being written to. Used
    only to choose which `UnenforceableScopeError` remedy to print (issue
    #63's HIGH-1): editing a migration nobody has ever applied is a routine
    fix; editing one some database already recorded trips FR-K5's checksum
    guard instead, and needs a different, honest procedure.

    **Checks every database in `paths.state`, not only the one for the
    current state hash (HIGH-1, recurred).** A revision applied under an
    earlier, unrefusing build was recorded in the database for the state hash
    *at that time*. Adding any further migration afterward shifts the state
    hash (ADR-0016) -- `database_for(current_hash)` then names a database
    that has never been built, and checking only that one database made an
    applied revision read as unapplied the moment anything else was added on
    top of it: exactly the ordinary shape of issue #63's own upgrade path,
    not an edge case.

    A migration id counts as applied here if *any* still-on-disk database
    recorded it -- deliberately broader than what FR-K5 itself checks
    (`_verify_history` guards only the *active* database, via the
    active-state pointer, not every database on disk). That breadth is a
    **safe over-correction, not an exact correspondence with FR-K5**: it
    cannot pick the unapplied-case remedy for a migration genuinely recorded
    somewhere, which is the direction that matters -- reopening HIGH-1's
    loop. The risk it takes on instead is the harmless one: offering the
    always-valid, always-safe applied-case procedure (edit, delete state,
    rebuild) to a revision whose only on-disk record sits in some
    long-abandoned, non-active database, where the simpler unapplied-case
    edit would in principle have sufficed. That costs a reader one
    unnecessary `rm -rf .theurian/state/` on a contrived history; it never
    costs a false "nothing was ever applied".

    Any failure opening one database is treated as "nothing recorded there"
    rather than surfaced, and the remaining databases are still checked: the
    caller is already reporting a different failure (`UnenforceableScopeError`),
    and replacing it with an unrelated crash from a code path whose only job
    is choosing a string would be worse than picking the more common remedy.
    """
    if not paths.state.is_dir():
        return frozenset()
    ids: set[MigrationId] = set()
    for database in sorted(paths.state.glob(_STATE_DATABASE_GLOB)):
        try:
            with SqliteCanonicalStore(database) as store:
                ids.update(migration_id for migration_id, _ in store.applied_migrations(project_id))
        except (SchemaVersionMismatchError, StateDatabaseUnreadableError):
            continue
    return frozenset(ids)


def _unenforceable_scope_remedy(
    exc: UnenforceableScopeError, paths: ProjectPaths, project_id: ProjectId
) -> str:
    """Which of the two `UnenforceableScopeError` remedies applies (issue #63)."""
    if exc.migration_id in _applied_migration_ids(paths, project_id):
        return UNENFORCEABLE_SCOPE_REMEDY_APPLIED
    return UNENFORCEABLE_SCOPE_REMEDY_UNAPPLIED


def _refuse_a_set_a_static_guard_rejects(context: CommandContext, *, as_json: bool) -> None:
    """Report whichever whole-set guard refuses, identically at both commands.

    **The guards are not listed here.** They come from
    :func:`run_static_migration_guards`, which is the one place that decides
    which rules are decidable from the migration files alone and in what order.
    ``migrate validate`` and ``migrate apply`` used to name them -- the scope
    refusal inline with its own ``try``, the other two through a wrapper each --
    while ``_check_migrations`` in ``cli/setup_commands.py``, the checker
    `doctor` consults, kept a third list. Nothing pinned the lists to one
    another, so a fourth guard would reach some of them and be found missing from
    the rest only once the two commands disagreed about the same directory:
    issue #36's class, and the shape #91 already found between `doctor` and
    `migrate validate`.

    What stays here is the **translation**, which is the CLI's own and not the
    guards': each refusal keeps the exit code and the remedy this command already
    published, and ``UnenforceableScopeError``'s remedy is still chosen by
    whether the offending migration has been applied (issue #63). A probe cannot
    use any of it -- ``_fail`` exits the process -- which is exactly why the
    verdict half had to live somewhere both could call.

    One function rather than a ``try``/``except`` in each command, so `apply`
    keeps its own return-statement budget while refusing before
    ``create_database`` runs and leaving no state database behind (issue #210,
    T-21).
    """
    try:
        run_static_migration_guards(context.loaded.migration_set)
    except UnenforceableScopeError as exc:
        _fail(
            str(exc),
            remedy=_unenforceable_scope_remedy(exc, context.paths, context.project_id),
            as_json=as_json,
            code=EXIT_STATE_ERROR,
        )
    except DuplicateContentFileError as exc:
        _fail(
            str(exc),
            remedy=DUPLICATE_CONTENT_FILE_REMEDY,
            as_json=as_json,
            code=EXIT_STATE_ERROR,
        )
    except AliasItemCollisionError as exc:
        _fail(
            str(exc),
            remedy=ALIAS_ITEM_COLLISION_REMEDY,
            as_json=as_json,
            code=EXIT_STATE_ERROR,
        )
    except MigrationError as exc:
        # Terminal net for the guard set. A *fourth* whole-set guard added to
        # `run_static_migration_guards` raises a `MigrationError` subclass this
        # function has no dedicated branch for, and without this it would escape as
        # a Rich traceback -- exit 1, empty stdout, no `{error, remedy}` document
        # even under `--json`, the CP-2 shape every branch above exists to avoid.
        # The three guard errors are each a *direct* `MigrationError` subclass with
        # no mutual inheritance, so the specific branches above always win for the
        # three that exist; this catches only a type they do not name. It prefers
        # the new error's own `.remedy` over the generic pointer, since only the
        # guard that raised knows the exact fix.
        _fail(
            str(exc),
            remedy=exc.remedy or _UNNAMED_GUARD_REFUSAL_REMEDY,
            as_json=as_json,
            code=EXIT_STATE_ERROR,
        )


def _refused_migration_ids(migration_set: MigrationSet) -> list[str]:
    """Every migration id `migrate validate`/`apply` would refuse, for `status`.

    Three gating rules feed `refusedIds`: the tenant/ACL scope rule (issue #63),
    the one-body-one-revision rule (issue #210), and the alias/item-id collision
    rule (SEC-13, T-21). Each has a non-throwing enumerator split from its
    throwing refusal precisely so `status` can report without gating -- reporting
    only some of them told a reader `refusedIds: []` for a set `validate`/`apply`
    exit 4 on. Deduplicated in first-seen order (a migration can carry more than
    one fault), so the field is stable.
    """
    seen: dict[str, None] = {}
    for migration_id in (
        *unenforceable_scope_violations(migration_set),
        *duplicate_content_file_violations(migration_set),
        *alias_item_collision_violations(migration_set),
    ):
        seen.setdefault(str(migration_id), None)
    return list(seen)


def _state_remedy(exc: TheurianError) -> str:
    """What to run after a command failed while reaching the canonical store.

    **Called from a catch-all, because a guard's promise reaches only as far as
    the exception is caught.** ``StateDatabaseUnreadableError`` keeps the
    corrupted cell out of its own message and keeps the real exception on
    ``__cause__``; Typer then renders a Rich traceback that prints ``__cause__``
    one line below it. Measured against the real CLI with one cell overwritten,
    ``theurian migrate status --json`` and ``theurian migrate apply --json``
    published it from six positions that way -- ``DomainError: ContentHash must
    be 64 lowercase hex characters, got '<the cell>'`` -- each with exit 1 and an
    empty stdout where ``--json`` promises a document (CP-2). Converting the
    escape is what makes the guard's withholding worth anything, so the ``except``
    is over ``TheurianError`` rather than over the types known to arrive today.

    Three unrelated families reach it: a file this build cannot interpret,
    another process holding the write lock, and a migration set the store
    refused. One cure for all three would send two of the three callers to the
    wrong file -- and :data:`STATE_REBUILD_REMEDY` is the one that deletes
    something, so it is the one that must never be the default.

    A fourth arrived with #481 -- a symbolic link at the lock path, whose cure is
    to remove that link -- and it is answered by ``exc.remedy`` rather than by a
    fifth ``isinstance``, which is the #205 rule ``_context_remedy`` already
    applies: a self-describing subtype carries its own cure, including subtypes
    this function has never heard of. It sits at the tail, below the two branches
    above, so the recorded decision that ``STATE_REBUILD_REMEDY`` is never a
    default is untouched -- and it changes nothing for the types named above,
    neither of which sets a remedy. ``WriteLockTimeoutError`` does set one, and
    it is byte-identical to its branch here (#404 R1-5).
    """
    if isinstance(exc, StateDatabaseUnreadableError | SchemaVersionMismatchError):
        return STATE_REBUILD_REMEDY
    if isinstance(exc, WriteLockTimeoutError):
        return "Wait for the other `theurian` process to finish, then retry."
    return exc.remedy or (
        "Fix the migration set, then retry. `theurian migrate validate` reports what can "
        "be checked without touching state."
    )


def _context_remedy(exc: TheurianError, *, default: str) -> str:
    """The remedy that matches why resolving the project actually failed.

    ``resolve_context`` does three things — find the Git working tree, ask the
    registry which project this root is, and load and validate every migration —
    so a fixed "run this inside a Git repository" told a user with a malformed
    migration to go looking for a ``.git`` directory that was already there.

    A non-empty ``exc.remedy`` wins over everything below it, checked first
    rather than per type. Before ``TheurianError.remedy`` existed, this
    function hand-enumerated every self-describing subtype —
    ``isinstance(exc, ProjectError)``, then a second, growing
    ``isinstance(exc, MigrationContentUnreadableError |
    MigrationFileUnreadableError)`` added when the second one joined the
    first — a list that had to be remembered and extended at every new
    self-describing error (issue #205). Checking the attribute once replaces
    an open-ended list with a property every such error already satisfies by
    construction, including ones this function has never heard of, like
    ``SchemaUnreadableError``.
    """
    if exc.remedy:
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

        On the resolved branch of ``project status``, its only caller, this is
        emitted beside a ``registered`` of ``None`` and never alone: a payload
        that says "cannot know" without saying why is a status a user cannot act
        on.

        **The unresolved branch does not reach it**, so that pairing is a
        property of one branch rather than of the field.
        :func:`_unresolved_status` publishes ``exc``'s own ``reason`` and
        ``remedy``, and a ``registered: null`` there arrives with the
        *resolution* failure's prose beside it and nothing about the registry --
        which is the gap, recorded rather than closed here because the cure is a
        payload change and this is a docstring: issue #381.

        Not the only reason ``registered`` can be ``None`` -- see
        :meth:`holds_root`, whose other case is explained by the ``unreadable``
        list instead.
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

    def holds_root(self, root: Path) -> bool | None:
        """Whether any entry registers this root, or ``None`` where it cannot say.

        **The single rule behind every ``registered`` field ``project status``
        emits from inside a Git working tree, on both of its branches.** Outside
        one this is never called: :func:`_unresolved_status` short-circuits on
        ``find_git_root`` and publishes the literal ``False`` it initialised the
        payload with, because a directory in no working tree is not a project
        whatever the registry says, and no entry could be about it -- so even a
        registry that does not parse leaves that ``False`` standing. Every rule
        below is a rule about the in-tree case.

        It replaced a pair of id-keyed answers, and each was wrong in its own
        direction.

        The unresolved branch asked "is *anything* registered?" -- there is no id
        to ask about when ``resolve_context`` has failed -- and a healthy
        registry answers that ``False``. Since that failure is usually nothing to
        do with registration (an unreadable ``.theurian/migrations``, a malformed
        migration, a state schema that will not parse), the command contradicted
        ``project list`` about the same file in the same breath and told a user
        to run ``project register`` in a repository already registered.

        The resolved branch asked about ``context.project_id``, which is only a
        *proposal*: with no registry entry for the root, ``resolve_context``
        falls back to the directory name. Two teams checking out ``api`` were
        enough -- an unregistered ``team-two/api`` reported ``registered: true``
        off ``team-one``'s entry. That is the misrouting
        :meth:`ProjectRegistry.ids_for_root` exists to refuse, arrived at by the
        one field that was not asking it. Both issue #226.

        The root is normalised through :func:`entry_root`, the predicate
        :meth:`ProjectRegistry.load` and :meth:`ProjectRegistry.ids_for_root`
        already partition the file on, rather than by a second
        ``Path(...).resolve()`` here: two surfaces deriving one fact separately
        is how they come to disagree about it.

        **An unreadable entry overrules a match**, of either kind
        :meth:`ProjectRegistry.unreadable_ids` reports. One that names no root
        cannot be compared against this one, so it cannot be ruled out as a
        *second* registration of this same directory, and answering ``True``
        would settle a question the missing field was the only thing that could
        have settled -- the refusal :meth:`ProjectRegistry.ids_for_root` makes,
        for the same reason, and why it refuses every root rather than only the
        plausible ones. One keyed by an id no consumer accepts *does* name a
        root, and this is deliberately broader there: ``ids_for_root`` can see
        that such an entry belongs to some other directory, while
        :attr:`entries` holds what ``load`` kept and ``load`` kept neither kind,
        so there is nothing here to reason from. "Cannot say" about a partly
        illegible registry is the conservative direction, and :attr:`unreadable`
        names the entry to remove. An id-keyed rule had no equivalent of any of
        this: "is this id a key of this file" stays decidable whatever else is
        wrong.

        **What that costs the resolved branch, which is more than a race.** A
        commit body on this branch claimed a resolved payload could only meet an
        unreadable entry through the window between ``resolve_context``'s
        registry read and this command's own, since ``ids_for_root`` refuses on
        any unreadable entry. It does not refuse on the second kind: an unusable
        key over an absolute ``rootPath`` naming another directory is neither
        rootless nor an unusable id among the entries naming *this* root, so
        resolution succeeds and this returns ``None`` deterministically, with
        ``projectId`` and ``root`` beside it. That is the design above, not an
        accident, and it is pinned by
        ``test_the_resolved_branch_reaches_the_same_null_with_nothing_racing_it``.

        The race is real as well, and it does not only degrade toward ``None``:
        a registration landing mid-window yields ``True`` here beside a
        ``projectId`` derived before it existed. Both are the same property
        stated once -- **this answers about the registry as of its own read,
        while ``projectId`` and ``root`` describe resolution time** -- and the
        two instants are not reconciled, deliberately: making them one read is a
        change to ``resolve_context``'s contract, not to this method.
        """
        if self.failure is not None:
            return None
        if self.unreadable:
            return None
        wanted = root.resolve()
        return any(entry_root(entry) == wanted for entry in self.entries.values())


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


def _unlink_database_and_sidecars(database: Path) -> None:
    """Delete a state database file, its `-wal` and its `-shm`, if present.

    A WAL-mode database (`PRAGMA journal_mode = WAL`) carries committed data in a
    `-wal` sidecar and its shared-memory index in a `-shm` one, so deleting the
    main file alone could leave a poisoned WAL to be replayed against whatever
    is written in its place -- the same replay the read side opens `mode=ro` to
    avoid. All three go; `missing_ok` covers both callers' ordinary cases: a
    clean checkpoint already removed the sidecars, or nothing was ever written
    at all.

    Two callers, two different reasons, both wanting the identical operation
    rather than two copies of it: :func:`_discard_untrusted_state` removes a
    database this installation did not build; `migrate_apply`'s own locked
    section removes whatever its own failed attempt at `create_database` left
    behind (issue #468, the `index_commands.py::_run_build` precedent -- "a
    half-built X is worse than none").
    """
    for sidecar in ("", "-wal", "-shm"):
        database.with_name(f"{database.name}{sidecar}").unlink(missing_ok=True)


def _discard_untrusted_state(database: Path) -> None:
    """Delete a state database this installation did not build, sidecars and all.

    A database file at the name this build would use, that this installation
    did not produce -- a doctored `.theurian/state/` shipped in the repository
    past its ADR-0004 ignore. Applying migrations *into* it would leave its
    injected rows untouched (an idempotent replay writes nothing) and then
    stamp the result as this install's build. `git rm --cached` is still the
    user's job for a tracked copy (the refusal on the serve side names it);
    this only stops the untrusted bytes from being adopted here.
    """
    _unlink_database_and_sidecars(database)


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

    try:
        created = initialize_project(context.paths)
    except TheurianError as exc:
        # `initialize_project` now refuses a `.theurian` subtree that a tracked
        # symlink points outside the working tree (#237), rather than `mkdir`-ing
        # the knowledge directories at the link's target. Its `.remedy` names the
        # cure; without this `except` it arrived as a Typer traceback, since the
        # only guard here wrapped `resolve_context`.
        _fail(
            str(exc),
            remedy=_context_remedy(exc, default="Repair the .theurian layout, then re-run."),
            as_json=as_json,
            code=1,
        )
        return

    try:
        gitignore_changed, _ = ensure_gitignore(context.paths.root)
    except TheurianError as exc:
        # Markers that do not delimit one block, which `ensure_gitignore`
        # refuses rather than guessing at -- the .gitignore half of #128. It
        # arrived here as a Typer traceback with the remedy buried in it,
        # because the only `except` in this command wraps `resolve_context`.
        # The directories above are already created and are not undone: they are
        # `.theurian/` and nothing else, and a re-run after the repair adds the
        # ignore block to them.
        _fail(
            str(exc),
            remedy=_context_remedy(exc, default="Repair the .gitignore block, then re-run."),
            as_json=as_json,
            code=1,
        )
        return

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
    pointer read has no equivalent of :meth:`_RegistryRead.holds_root` -- there
    is one value to lose and no membership question to answer about it.
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
    problem only the user can fix, and ``exc.remedy`` is the only place
    the ``project unregister`` invocations that fix it are named. Dropping it
    left the command a confused user reaches for *first* reporting a problem with
    no way out. Exit code stays 0; the remedy travels into the payload instead,
    where both a human reading the rendered output and a script reading JSON can
    find it.

    ``if exc.remedy:``, not ``isinstance(exc, ProjectError) and exc.remedy``:
    the narrower check was this function's own copy of the enumeration
    :func:`_context_remedy` was rewritten to stop needing, missed when that
    refactor landed because this is a *third*, separate caller of
    ``resolve_context`` with its own handling rather than a call to
    `_context_remedy` -- so `MigrationsDirectoryUnreadableError` and its
    siblings reached this payload with `reason` but no `remedy`, narrower than
    every other command surfacing the same exception (issue #205).

    ``statePointerCorrupt`` and ``indexStale`` are deliberately absent here
    rather than ``false``. Nothing on this branch has resolved a project, so no
    state pointer was read and no state hash was computed, and emitting either
    field would answer a question this branch never asked -- the same reason
    ``registered`` refuses to be ``False`` above. ``indexStale: false`` was
    published from Milestone 1 (introduced in ``5513d84``, 2026-08-02) until
    ``2789ef9``, in every core release up to and including 0.1.0.dev11, and it
    said "your index is up to date" about a directory Theurian had not looked
    at. The two spellings are not interchangeable here, because ``null`` is this
    payload's "asked, and the answer is unknowable" (``registered`` on a
    registry nobody can read) and absence is "never asked".
    """
    payload: dict[str, Any] = {"registered": False, "reason": str(exc)}
    if exc.remedy:
        payload["remedy"] = exc.remedy

    # `resolve_context` never got as far as asking the registry whether this root
    # is registered -- for any reason, a broken migration included -- so the
    # question is put to the registry directly, keyed by root because there is no
    # id to key it by. Asking "is anything registered at all?" instead was issue
    # #226: a readable registry holding this very root answered `False`, so a
    # `chmod 000 .theurian/migrations` deregistered the project as far as this
    # payload was concerned, while `theurian project list` went on listing it.
    #
    # `find_git_root` is checked apart from the registry so a plain "not inside a
    # Git repository" -- which has nothing to do with the registry -- keeps its
    # honest `False` rather than being dragged into an ambiguity that could not
    # possibly be about it. That directory is not a project whatever the registry
    # says, and `theurian project list` is the surface that reports the file.
    read = _read_registry()
    root = find_git_root(Path.cwd())
    if root is not None:
        payload["registered"] = read.holds_root(root)
    # Stays a list even when the file did not parse, because a caller that
    # iterates it must not have to branch first. Inside a working tree, that the
    # set of ids is *unknown* rather than empty is then carried by `registered:
    # None` -- and only inside one: the branch above short-circuits outside a
    # tree, so a corrupt registry there publishes an empty `unreadable` beside a
    # literal `registered: false` and nothing in this payload says the file could
    # not be read. That combination is honest about the directory (no entry could
    # be about it) and silent about the file, and `theurian project list` is the
    # surface that reports the file.
    #
    # `reason` does not carry it either: `reason` is `exc`'s and need not be
    # about the registry at all, since `resolve_context` loads and validates the
    # migrations *before* it asks the registry which project this root is, so a
    # broken migration raises first and this payload pairs a `registered: null`
    # with migration prose. Issue #381 owns closing that; nothing here may be
    # read as though it were closed.
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

    # The one place outside `_require_project` that asks where the state
    # database is, and so the one place its containment refusal has to be graded
    # separately (#483). This command does not go through `_require_project` at
    # all -- it answers at exit 0 for a directory that is not a project -- so the
    # fix there could not reach it, and it published the same Rich traceback with
    # an empty stdout.
    #
    # A refusal, not a degradation, and that is the difference from
    # `pointer_failure` above. An unreadable pointer costs this payload one
    # field, so the command answers with the rest and says which field is
    # missing. A state-database path that resolves outside the project root is
    # `.theurian/state/` doctored past ADR-0004's ignore, and no partial answer
    # about a project in that condition is worth publishing: it takes
    # `_require_project`'s grading for the same exception, `EXIT_STATE_ERROR`
    # with the error's own remedy.
    try:
        database = context.paths.database_for(context.state_hash)
    except TheurianError as exc:
        _fail(str(exc), remedy=exc.remedy, as_json=as_json, code=EXIT_STATE_ERROR)
        return

    _emit(
        {
            "projectId": context.project_id.value,
            "root": str(context.paths.root),
            # Keyed by the root published on the line above, never by the id
            # beside it: `context.project_id` is `resolve_context`'s *proposal*,
            # and for an unregistered repository that proposal is the directory
            # name. Two teams checking out `api` made this field report
            # `team-one`'s registration as `team-two`'s -- `true` for a root no
            # entry names, while `project list`, `project register` and `setup`
            # all answered by root and said unregistered (issue #226).
            #
            # `None` is this command's "cannot know", and both branches now get
            # it from `holds_root` rather than each deciding for itself -- which
            # is how the resolved one came to answer `False` where the unresolved
            # one answered `None` for the same file.
            "registered": read.holds_root(context.paths.root),
            "initialized": context.paths.knowledge_dir.is_dir(),
            "stateHash": str(context.state_hash),
            "activeStateHash": None if active is None else str(active.state_hash),
            "stateBuilt": database.exists(),
            # The index's own verdict, from the one function `theurian index
            # status` publishes it from (issue #100). It used to be computed
            # here, from the *canonical* pointer -- `active is None or
            # active.state_hash != context.state_hash` -- which asks whether
            # `migrate apply` is up to date and never opened the index pointer
            # at all. That old question is not lost: it is `activeStateHash`
            # against `stateHash`, two fields above, both still published.
            "indexStale": index_staleness(
                context.paths,
                project_id=context.project_id.value,
                current_state_hash=str(context.state_hash),
            ).stale,
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
    """Report applied and pending migrations.

    Unlike `migrate validate` and `migrate apply`, this command never refuses on
    a *statically decidable* rule (issue #63's MEDIUM-3): its contract is
    observation, so a set `validate`/`apply` would refuse is still reported in
    full, with `refusedIds` naming the migrations they refuse. Both such rules
    feed it -- a revision naming a tenant or ACL group nothing can enforce (issue
    #63), and two revisions backing one body file (issue #210) -- so neither
    property goes invisible on the one consumer that keeps going.

    The FR-K5 tamper guards are the exception, and deliberately so: an applied
    migration that was edited or deleted (issue #116) is detected by
    `_verify_history` inside :func:`_require_project`, *before* this body runs, so
    `status` exits non-zero there too. Tamper-evidence is not an observation to
    report past -- a history the store cannot vouch for is not a set whose
    `applied`/`pending` counts mean anything.
    """
    context, database = _require_project(as_json)
    refused_ids = _refused_migration_ids(context.loaded.migration_set)

    if not database.exists():
        _emit(
            {
                "stateHash": str(context.state_hash),
                "stateBuilt": False,
                "total": len(context.loaded.migration_set),
                "applied": 0,
                "pending": len(context.loaded.migration_set),
                "pendingIds": [str(m.migration_id) for m in context.loaded.migration_set],
                "refusedIds": refused_ids,
            },
            as_json=as_json,
        )
        return

    # The `try` wraps `write_transaction` itself, not merely the body of the
    # `with`. Opening a connection interprets the file -- `_prepare` runs the
    # PRAGMA loop and `int()`s `schema_metadata.schema_version` -- so a guard
    # around the body alone, which is what stood here, could not see the failure
    # that arrives first.
    try:
        with write_transaction(database, context.paths.write_lock) as connection:
            writer = SqliteWriter(connection)
            engine = MigrationEngine(context.clock, context.loaded.content_by_hash)
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
    except TheurianError as exc:
        _fail(str(exc), remedy=_state_remedy(exc), as_json=as_json, code=EXIT_STATE_ERROR)
        return
    except (OSError, sqlite3.Error) as exc:
        # The backstop `migrate apply`'s section A has carried since #478 and
        # this command did not (#484). `_prepare` converts what it can, but it
        # only runs once `sqlite3.connect` has *returned* -- a fault opening the
        # file at all is a bare `sqlite3.OperationalError` or `OSError`, neither
        # of which is a `TheurianError`, so it escaped `--json` as a Rich
        # traceback carrying absolute source paths with nothing on the machine
        # channel. Measured with a directory planted at the state-database path,
        # which is the artefact an ADR-0004 doctored `.theurian/state/` delivers.
        #
        # Nothing is cleaned up here, unlike section A: this command creates no
        # database and never owns the file it failed to open.
        _fail(
            str(exc),
            remedy=_STATE_DATABASE_FAULT_REMEDY,
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
            "refusedIds": refused_ids,
        },
        as_json=as_json,
    )


@migrate_app.command("validate")
def migrate_validate(as_json: JsonOption = False) -> None:
    """Parse, schema-check, and order every migration without applying anything.

    Loading has already happened by the time this runs, so reaching here means
    the set parses, validates, resolves its content files inside the project
    root, and has a valid application order.

    Also calls :func:`refuse_unenforceable_scope` directly, on the same
    `MigrationSet` `migrate apply` would see -- `MigrationEngine.apply` calls
    the identical function internally. A document that names a tenant or ACL
    group nothing can yet enforce (issue #63) is refused by both commands or
    neither, *for this one statically decidable rule*. That is not a general
    guarantee that validate cannot pass a document apply will reject: it still
    cannot see every invariant domain construction enforces -- INV-8's
    source-anchor requirement is one. The sample project now satisfies that
    invariant; custom migrations without anchors can still validate and then be
    rejected by `migrate apply` (issue #36).

    Published ``unpinnedRevisions`` until ADR-0027, warning about a revision
    that declared no ``contentSha256``. The pin is schema-required now, so the
    list was empty for every input that got this far -- and a permanently empty
    published field claims its condition is still reachable. The absence is a
    schema error at load instead, which is a refusal rather than a warning.
    """
    context, _ = _require_project(as_json)

    _refuse_a_set_a_static_guard_rejects(context, as_json=as_json)

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


#: The remedy for a residual fault reaching one of the two backstops inside
#: `migrate apply`'s single critical section (issue #468 round two): section
#: A's `(OSError, sqlite3.Error)` around `create_database`, and section B's
#: `OSError` around `record_state`/`write_active_state`. Both sections are
#: inside one continuous `WriteLock` hold spanning creation through publish,
#: so the process-level race #468 measured is closed at the source and
#: neither backstop is reachable through that race any more; each is what
#: turns an UNRELATED fault -- a filesystem problem, or the advisory lock's
#: own documented NFS caveat (ADR-0018 Negative consequence) -- into the same
#: `_fail` envelope every other command failure gets, rather than a raw
#: traceback. Never leads with lock-contention advice: a lock *timeout* is
#: `WriteLockTimeoutError`, caught by the outer `except TheurianError`
#: wrapping the whole critical section (below), and cannot reach either of
#: these two backstops.
_LOCKED_WRITE_FAULT_REMEDY = (
    "Check that `.theurian/state/` is writable and on a supported filesystem (not NFS; "
    "ADR-0018), then retry `theurian migrate apply`."
)


@migrate_app.command("apply")
def migrate_apply(  # noqa: PLR0911 -- one early return per distinguishable failure shape; the single critical section (#468) is kept as one function on purpose, so "does everything really sit under the one lock" stays answerable by reading top to bottom rather than by trusting a call graph
    as_json: JsonOption = False,
) -> None:
    """Apply pending migrations to the canonical store.

    Idempotent: applying an unchanged set again reports zero applied and changes
    nothing (FR-K8).

    **One critical section spans every write this command makes (issue #468,
    round two).** The first shape shipped here held `create_database` and
    `write_active_state` under two separate acquire/release cycles of the
    same `WriteLock`, sequential rather than nested -- and measurement found
    a real gap in the window *between* them: `provenance.record_state` ran
    outside any lock, after the pointer publish, so a slower process racing a
    faster one could observe `has_state == False` for a database the faster
    process had *already built and published*. The untrusted-state discard
    branch, reading that as a doctored `.theurian/state/`, then deleted and
    rebuilt a live, just-published database out from under the process that
    built it -- 13/78 raced pairs measured both processes `databaseCreated:
    true`. That design's own docstring called the re-check "FR-K8's
    idempotent-noop path pulled one step earlier"; it was false. A loser
    under it did not observe the winner's work, it destroyed it.

    The fix (Codex, consulted on the smallest correct shape) holds ONE
    `WriteLock` across the whole sequence below: the discard/create decision,
    `create_database`, the migration transaction, `provenance.record_state`,
    and `write_active_state`, in that order. `record_state` moves *before*
    the pointer publish, so by the time `active.json` names a state hash,
    that hash's provenance is already on record -- there is no window where
    the pointer names a build the serve-side provenance gate has not yet
    been told about. The doctored-state discard check stays inside this one
    section, and that is what makes it correct rather than merely
    convenient: under one continuous hold, no *other* writer's intermediate
    state is ever visible here, so `has_state == False` on a database that
    exists can only mean what the check exists to catch -- state this
    installation never built, committed and shipped past ADR-0004's ignore --
    never a live build still in flight.

    Mechanically, `apply_migration_set` (and `write_transaction` beneath it)
    gained an `already_locked` parameter, so this command's own acquisition
    can cover the transaction too without a second, nested acquisition of the
    same lock file: `flock` locks an *open file description*, not a process
    or a path, so a second `Path.open()` + `flock` on a file this same
    process already holds self-blocks for the full timeout, exactly as a
    second OS process would. `already_locked` defaults to `False` everywhere
    else -- `migrate status`'s own `write_transaction` call, and `propose
    accept`'s rehearsal against a throwaway lock file no other process can
    contend for -- so every other caller keeps the self-contained acquisition
    it always had.
    """
    context, database = _require_project(as_json)

    # Checked before `create_database` below, so a refused apply leaves no
    # database file behind (issue #63, #210, T-21): `migrate validate` already
    # costs nothing on refusal, and `apply` should not cost more just because it
    # would otherwise have created state. `MigrationEngine.apply` runs the same
    # guards through the same function, but only once a write transaction is open
    # and `create_database` has already run.
    _refuse_a_set_a_static_guard_rejects(context, as_json=as_json)

    # This installation's record of the state it built, out of the repository
    # tree (ADR-0004, SEC-7). Used twice below: to refuse to *apply into* a
    # database this install did not build, and to record the one it does.
    provenance = BuildProvenance.default()

    # Built before the lock, not inside it: every field here is read-only git
    # metadata (`repository_url`, `default_branch`, `current_commit` -- each a
    # subprocess call), and the round-one measurement traced part of the
    # original defect's window to exactly these calls sitting between two
    # separate lock holds. There is only one hold now, but nothing here
    # writes anything the lock protects, so this stays outside it -- the same
    # reason `write_transaction`'s own NFR-8 note gives for content hashing:
    # no external I/O inside a hold that does not need it.
    project = Project(
        project_id=context.project_id,
        root_path=str(context.paths.root),
        repository_url=repository_url(context.paths.root),
        default_branch=default_branch(context.paths.root),
        knowledge_directory=DEFAULT_KNOWLEDGE_DIRECTORY,
        registered_at=context.clock.now(),
        last_seen_commit=current_commit(context.paths.root),
    )

    created = False
    try:
        with WriteLock(context.paths.write_lock).held():
            # -- Section A: discard/create decision + create_database -------
            # Re-checked here, not only inside `create_database`'s own
            # `if database_path.exists()` guard: a naive design can have two
            # processes both pass this shape of check before either holds
            # the lock, so it has to be asked again once the lock *is* held,
            # or the loser calls `create_database` on a file that already
            # exists and gets `FileExistsError` instead of observing it.
            # Under this one continuous hold spanning through the
            # transaction, record and publish below, no other writer's
            # intermediate state is ever visible here -- see this command's
            # own docstring for why that is what makes the discard branch
            # correct now (#468 round two).
            try:
                if database.exists() and not provenance.has_state(
                    context.paths.root, str(context.state_hash)
                ):
                    _discard_untrusted_state(database)

                if not database.exists():
                    create_database(database, str(context.state_hash), MIGRATION_ENGINE_VERSION)
                    created = True
            except (OSError, sqlite3.Error) as exc:
                # A half-built database is worse than none
                # (`index_commands.py::_run_build`'s precedent): nothing below
                # this point has run yet, so whatever `create_database`
                # partially wrote is unlinked before reporting, not after.
                # Best-effort: a cleanup failure -- `database` names a
                # directory, not a file, which `unlink` refuses too -- must
                # not replace the original error with a less informative one.
                with contextlib.suppress(OSError):
                    _unlink_database_and_sidecars(database)
                _fail(
                    str(exc),
                    remedy=_LOCKED_WRITE_FAULT_REMEDY,
                    as_json=as_json,
                    code=EXIT_STATE_ERROR,
                )
                return

            # -- The transaction ----------------------------------------------
            try:
                # The one definition of what applying a migration set is (ADR-0027
                # decision 2). `propose accept`'s pre-check replays through this
                # same function against a throwaway database, so an acceptance and
                # this command cannot disagree about whether a set is usable.
                # `already_locked=True`: this command already holds `write_lock`
                # above, so `write_transaction` must not try to acquire it again.
                report = apply_migration_set(
                    database=database,
                    write_lock=context.paths.write_lock,
                    project=project,
                    loaded=context.loaded,
                    clock=context.clock,
                    database_created=created,
                    already_locked=True,
                )
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
                        "Two changes targeted the same item. Read both revisions, decide "
                        "which is correct, and write a new migration with the right "
                        "expectedRevision. Theurian does not merge knowledge automatically."
                    ),
                    as_json=as_json,
                    code=EXIT_STATE_ERROR,
                )
                return
            except UnenforceableScopeError as exc:
                # Defense in depth, not the primary path: the pre-check above
                # already refuses before `create_database` runs, for every input
                # this command can receive. `MigrationEngine.apply` enforces the
                # same rule on its own, so this stays correct even for a caller
                # that invokes it directly without going through this command.
                # Caught ahead of the generic `MigrationError` clause below,
                # since it is one.
                _fail(
                    str(exc),
                    remedy=_unenforceable_scope_remedy(exc, context.paths, context.project_id),
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
            except TheurianError as exc:
                _fail(str(exc), remedy=_state_remedy(exc), as_json=as_json, code=EXIT_STATE_ERROR)
                return
            except (OSError, sqlite3.Error) as exc:
                # The backstop sections A and B carry and the section between
                # them did not (#484). Which section meets a filesystem fault at
                # the database is decided by the provenance record: with none,
                # `_discard_untrusted_state`/`create_database` meet it in section
                # A; with one -- every project after its first apply -- both are
                # skipped and `sqlite3.connect` inside `apply_migration_set` meets
                # it here, where a `sqlite3.OperationalError` is none of the five
                # clauses above and escaped `--json` as a Rich traceback.
                #
                # **No cleanup, deliberately, and that is the difference from
                # section A.** Section A unlinks because `create_database` may
                # have left a file it wrote part of. Here the database is very
                # often one that already existed and that this installation built
                # and provenanced -- `created` is False for it -- and deleting a
                # live state because an unrelated fault interrupted a write would
                # turn a failed command into data loss, with `active.json` still
                # naming the hash it removed. The `created is True` residue needs
                # no branch of its own either: `_open_transaction` has already
                # rolled the transaction back and closed the connection, so what
                # survives is exactly what `create_database` wrote -- a valid,
                # empty database, unprovenanced (section B never ran) and
                # unpublished, which the serve-side gate stands aside and the
                # next apply's own discard branch removes.
                _fail(
                    str(exc),
                    remedy=_STATE_DATABASE_FAULT_REMEDY,
                    as_json=as_json,
                    code=EXIT_STATE_ERROR,
                )
                return

            # -- Section B: record BEFORE publish -----------------------------
            # `record_state` before `write_active_state`, both still under the
            # same hold: by the time `active.json` names this state hash, its
            # provenance is already on record, so there is no window where the
            # pointer names a build the serve-side provenance gate has not yet
            # been told about -- the ordering the round-one design got
            # backwards (#468 round two).
            try:
                provenance.record_state(context.paths.root, str(context.state_hash))
                active = write_active_state(
                    context.paths,
                    context.state_hash,
                    len(context.loaded.migration_set),
                    context.clock,
                )
            except OSError as exc:
                # `sqlite3.Error` is unreachable here: neither `record_state`
                # nor `write_active_state` opens a database connection.
                _fail(
                    str(exc),
                    remedy=_LOCKED_WRITE_FAULT_REMEDY,
                    as_json=as_json,
                    code=EXIT_STATE_ERROR,
                )
                return
    except TheurianError as exc:
        # Reached by the `with` statement itself, above every section, and by
        # three arrivals rather than the one this comment used to name:
        #
        # 1. `ProjectError`, from resolving `context.paths.write_lock` -- that
        #    expression is evaluated here, inside the `try`, and goes through
        #    `ProjectPaths._contained`, which refuses a lock path that leaves
        #    the tree (#237, T-5).
        # 2. `WriteLockUnusableError`, when the lock path is a symbolic link the
        #    open refuses rather than writing through (#481).
        # 3. `WriteLockTimeoutError`, when another holder keeps `write_lock` past
        #    `WRITE_LOCK_TIMEOUT_SECONDS`.
        #
        # Nothing *inside* the `with` block adds a fourth: `already_locked=True`
        # means the transaction never attempts its own acquisition, so this is
        # still the one place a lock failure for the whole critical section is
        # caught. Each of the three describes itself, and the branch leans on
        # that: `_state_remedy` falls through to `exc.remedy` for the first two,
        # and answers the third from its own `isinstance` arm with text that is
        # byte-identical to that type's `.remedy` (#404 R1-5). So a fourth
        # arrival that sets its own remedy needs no new branch there.
        _fail(str(exc), remedy=_state_remedy(exc), as_json=as_json, code=EXIT_STATE_ERROR)
        return

    # After the lock has released, never inside it: `purge_into` is a
    # whole-file backup, delete and verify, and holding that across a write
    # transaction blocks every other writer (NFR-8,
    # ADR-0018 point 5). The purge reads the *published index*, not canonical
    # state, so the committed withdrawal is all it needs -- and it is the same
    # application-layer use case a future daemon write path calls (ADR-0024
    # decision 5). A withdrawal-free apply skips it inside the use case.
    purge = _purge_withdrawal(context, report.withdrawn_candidates, provenance)

    # A withdrawal purge publishes a *new* index build (a copy with the withdrawn
    # revisions removed), so its build id must be recorded as this install's or
    # the serve path would stand the fresh, correct index aside as unbuilt. Only a
    # purge that ran over a source this installation built reaches here published
    # (`_purge_withdrawal` declines an unprovenanced source), so this never
    # provenances a copy laundered out of a committed, doctored index.
    if purge.published and purge.index_build_id is not None:
        provenance.record_index(context.paths.root, purge.index_build_id)

    _emit(
        {
            "stateHash": str(active.state_hash),
            "databaseCreated": created,
            "applied": [str(m) for m in report.applied],
            "skipped": [str(m) for m in report.skipped],
            "operationsApplied": report.operations_applied,
            "changed": report.changed,
            "indexPurge": _purge_fields(purge),
        },
        as_json=as_json,
    )


def _purge_withdrawal(
    context: CommandContext,
    withdrawal_candidates: Sequence[WithdrawalCandidate],
    provenance: BuildProvenance,
) -> WithdrawalPurge:
    """Purge the published index for a withdrawal, but only if this install built it.

    The purge copies the currently published index forward with the withdrawn
    rows removed, and the caller records the copy as this installation's build.
    That is sound only when the source it copies was itself built here. On a fresh
    clone the published build can be a committed, doctored ``theurian-index-*``
    shipped past its ADR-0004 ignore, and it is *unprovenanced*: copying it
    forward and recording the copy would launder its surviving injected rows into
    a build the serve path then trusts, when the serve-side ``has_index`` gate
    (``mcp.search._UNBUILT_INDEX``) would otherwise have stood the doctored file
    aside to the canonical scan. So the purge runs only over a provenanced source;
    an unprovenanced published source is left in place for that serve-side gate to
    degrade to the canonical state this apply just rebuilt and provenanced
    (ADR-0004, SEC-7).

    Gated here at the composition root, beside the ``has_state`` gate in
    ``migrate_apply``: every provenance check in this codebase lives at a
    composition root or a serve entry point, never inside a use case, so the
    application-layer purge stays free of the installation's build record. A
    withdrawal-free apply reads no pointer here -- it has nothing to launder -- and
    the empty ``source_build_id`` (no pointer, or a pointer naming no build) falls
    through to the use case, which reports the benign ``no-published-index`` state.
    """
    source_build_id = str((read_active_index(context.paths) or {}).get("indexBuildId", ""))
    if (
        withdrawal_candidates
        and source_build_id
        and not provenance.has_index(context.paths.root, source_build_id)
    ):
        return WithdrawalPurge(published=False, reason=UNTRUSTED_SOURCE_INDEX)
    return publish_purge_for_withdrawal(
        context.paths,
        withdrawal_candidates=withdrawal_candidates,
        ids=context.ids,
        index_factory=SqliteIndexStore,
        # Re-derive each affected scope's forest over the surviving rows, so a
        # purged build's trees equal a never-held corpus's (ADR-0008 decision 9).
        # Composed here, the composition root, because the callback closes over
        # the extractive summariser and the hashing embedder -- adapters the
        # application-layer purge may not name (ADR-0003). Over a chunk-only index
        # it is a no-op, so a build without `--raptor` keeps today's delete-only
        # purge. The embedder is passed whatever the build's flavor; the purge
        # embeds re-derived nodes only when the build being purged already carried
        # chunk embeddings, so a `--no-embeddings` forest stays vector-free.
        recompute=make_forest_recompute(
            store_factory=SqliteIndexStore,
            forest_builder=ForestBuilder(summarizer=ExtractiveSummarizer()),
            embedder=HashingEmbedding(),
        ),
    )


def _purge_fields(purge: WithdrawalPurge) -> dict[str, object]:
    """What the withdrawal-triggered index purge did, for the apply's payload.

    Reported rather than swallowed so a purge that *failed* is visible: the
    withdrawal is committed, but the still-published build holds the withdrawn
    rows until a rebuild, and a caller acting on the answer has to be able to see
    that (ADR-0024 decision 5). ``published: false`` with a benign ``reason`` is
    the ordinary case -- no withdrawal, or no index to purge.
    """
    return {
        "published": purge.published,
        "indexBuildId": purge.index_build_id,
        "removed": purge.removed,
        "reason": purge.reason,
        "failed": purge.failed,
        "remedy": purge.remedy,
    }


daemon_app = typer.Typer(help="Manage the local Theurian daemon.", no_args_is_help=True)

# The daemon commands import uvicorn and the MCP SDK lazily. Measured: importing
# them at module scope takes `theurian --version` from 170 ms to 600 ms, which
# alone exceeds the SessionStart p95 budget of 300 ms (NFR-2) -- and the hook
# runs on every session while never touching these commands.
#
# The laziness has a second consequence nobody chose: on an install without the
# `daemon` extra the import fails at *call* time, so the failure arrives as a
# traceback from inside a command rather than at startup. `_require_daemon_extra`
# below is what turns that back into an answer.


def _require_daemon_extra(exc: ModuleNotFoundError, *, as_json: bool) -> None:
    """Report a missing ``daemon`` extra, or re-raise something else (#78).

    ``uv tool install theurian`` -- the command every install surface names --
    leaves ``uvicorn`` out, so the very next step of the documented flow ended in
    ``ModuleNotFoundError: No module named 'uvicorn'``: a package the reader
    never asked for, with no command to fix it. The packaging split itself is
    sound and stays (ADR-0014).

    **Re-raising is the load-bearing half.** A bare ``except ModuleNotFoundError``
    would answer a broken Theurian -- a renamed module, a truncated wheel -- with
    "install the daemon extra", sending the user to reinstall the very package
    that contains the broken file. So the exception's own ``name`` decides, and
    anything Theurian's own is left to propagate.
    """
    if not provided_by_daemon_extra(exc.name):
        raise exc
    _fail(
        f"`theurian daemon` needs Theurian's `{DAEMON_EXTRA}` extra, which is not "
        f"installed: {exc}.",
        remedy=DAEMON_EXTRA_REMEDY,
        as_json=as_json,
        code=1,
    )


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
    try:
        from theurian.daemon.runner import serve  # noqa: PLC0415 - see the note above
    except ModuleNotFoundError as exc:
        _require_daemon_extra(exc, as_json=as_json)
        return

    if not foreground:
        _start_detached(port=port, as_json=as_json)
        return

    try:
        check = serve(port=port)
    except TheurianError as exc:
        # A start that Theurian itself refused -- today, a deployment serving
        # profile that cannot be honoured (#119). Its own `remedy` names the file
        # and the four words that belong in it; `theurian doctor` would send the
        # reader looking somewhere else entirely.
        _fail(str(exc), remedy=exc.remedy or "Run `theurian doctor`.", as_json=as_json, code=1)
        return
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
    from theurian.daemon.instance import LOCK_FILENAME, probe_health  # noqa: PLC0415
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

    Ingestion records a content-hash manifest and stores no body: parsed
    documents live in memory for the run, and the only file written is
    ``.theurian/cache/ingestion.json``. It never writes approved
    knowledge; promotion runs through a migration and a human (ADR-0013).

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
    """Fail if an already-applied migration has been edited or deleted (FR-K5, ADR-0005).

    Checked against the *previously active* state, not the one being built.
    Editing *or deleting* a migration changes the state hash (ADR-0016), so the
    next command would otherwise open a fresh empty database, find nothing
    applied, and report everything as fine -- silently losing the guarantee
    precisely when it fires. The edit check (a present file disagreeing with its
    recorded checksum) and the delete check (a recorded migration with no file
    left, issue #116) are the two directions of that one guarantee.

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
            # Kept in application order (``ORDER BY sequence``) for the removal
            # check below, which names the earliest gone; the edit check reads
            # it as a dict for O(1) lookup while iterating the migration set.
            applied = store.applied_migrations(context.project_id)
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
        verify_no_applied_migration_changed(dict(applied), context.loaded.migration_set)
        # Reverse direction (issue #116): an edit is a file that disagrees with
        # its recorded checksum, and the check above binds only files that still
        # exist -- so a *deleted* applied migration, the strongest tampering,
        # slipped past it entirely. Checked here against the same previously
        # active history, so `validate`, `apply` and `status` refuse it alike.
        verify_no_applied_migration_removed(applied, context.loaded.migration_set)
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
    except MigrationHistoryMissingError as exc:
        _fail(
            str(exc),
            remedy=(
                "Restore the deleted migration file (recover it from Git). An applied "
                "migration must never be removed: its recorded history is what makes the "
                "built knowledge trustworthy, and its canonical rows remain until you do."
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
    # `exc.remedy or "..."` rather than a separate `except` for every
    # self-describing subtype: `MigrationContentUnreadableError`,
    # `MigrationFileUnreadableError`, and `MigrationsDirectoryUnreadableError`
    # all set their own `.remedy`, and the previous shape -- a growing
    # `isinstance`/`except` tuple that had to be extended at every new one --
    # is exactly the enumeration `TheurianError.remedy` exists to replace
    # (issue #205). Every branch in *this* `try` is graded `EXIT_STATE_ERROR`
    # -- that is `_require_project`'s own grading, not a claim about every
    # consumer of these types: `init` and `project register` reach the same
    # exceptions through their own direct `resolve_context()` calls and exit
    # 1 via `_context_remedy`'s generic `except TheurianError` branch, and
    # `project status` reaches exit 0 through `_unresolved_status`. An
    # unreadable migration is a knowledge-state problem the user must fix in
    # `_require_project`'s callers -- nine as of 2026-08-20; re-count with
    # `grep -rn '_require_project(as_json)$' packages/theurian-core/src/theurian/cli/`
    # rather than trusting this number -- the same family as a checksum
    # mismatch or a dependency cycle above -- what varies between commands is
    # the exit code their own contract already assigns to "could not resolve
    # a project", not a re-grading of the exception itself.
    except MigrationError as exc:
        _fail(
            str(exc),
            remedy=exc.remedy or "Fix the migration file, then retry.",
            as_json=as_json,
            code=EXIT_STATE_ERROR,
        )
        raise
    # `PathEscapeError` is a `SecurityError`, not a `MigrationError`, so it fell
    # past the branch above into the generic `TheurianError` one below and
    # exited 1 with that branch's "run this inside an initialised Theurian
    # project" default -- printed to a user who was already inside one, because
    # a `.theurian/migrations` symlinked outside the project is what raised it
    # (issue #233). It reaches `resolve_context` from the same load path as
    # every branch above -- `load_migrations`'s directory probe, or `_load_one`
    # reading a `*.yaml` entry -- and is the same kind of thing: a
    # knowledge-state problem the user must fix, not a broken command. So it
    # takes the same grading, and its own `.remedy` rather than a default,
    # since this class always sets one.
    except PathEscapeError as exc:
        _fail(str(exc), remedy=exc.remedy, as_json=as_json, code=EXIT_STATE_ERROR)
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
    # Outside the `try` above, because `resolve_context` is not what raises here
    # -- and outside every handler in it, which is the defect (#483).
    # `database_for` routes through `ProjectPaths._contained` -> `_contain`,
    # which raises `ProjectError` when the state-database path resolves outside
    # the project root: a symlink under the git-ignored `.theurian/state/` that a
    # repository contributor force-added past ADR-0004's ignore. Being the last
    # statement of this function, that refusal escaped every caller of it at
    # once and reached a `--json` caller as a Rich traceback carrying absolute
    # source paths, with nothing on the machine channel. Measured over
    # `CLI_SWEEP`: six of the seven swept commands that resolve the path,
    # `project status` being the seventh through its own direct call.
    #
    # Graded exactly as the `PathEscapeError` branch above grades the same kind
    # of thing -- `EXIT_STATE_ERROR` and the error's own remedy -- because it is
    # the same kind of thing: a knowledge-state problem the user must fix, not a
    # broken command. `exc.remedy` bare rather than with a default, for that
    # branch's reason: `_contain` is the only raiser reachable from here and it
    # sets `KNOWLEDGE_DIR_ESCAPE_REMEDY` on both of its raise sites.
    try:
        database = context.paths.database_for(context.state_hash)
    except TheurianError as exc:
        _fail(str(exc), remedy=exc.remedy, as_json=as_json, code=EXIT_STATE_ERROR)
        raise
    return context, database


__all__ = [
    "EXIT_STATE_ERROR",
    "daemon_app",
    "ingest_command",
    "init_command",
    "migrate_app",
    "project_app",
]
