"""The pipeline ``migrate apply`` runs, and the dry replay ``propose accept`` runs.

A composition root (ADR-0003): one of the places allowed to name a concrete
adapter. It exists for one reason, and the reason is ADR-0027 decision 2's hard
condition -- **``accept``'s pre-check must invoke the same engine path ``migrate
apply`` invokes, differing only in the write target.** Two functions deriving
"does this set apply?" independently agree until they do not, and the
disagreement surfaces as *"accept said yes and apply said no"* with nothing to
arbitrate. So the write-transaction/writer/engine sequence lives here once, in
:func:`apply_migration_set`, and both callers reach it.

:func:`rehearse_migration_set` adds what a *dry* run needs and nothing else: a
copy of the candidate set in a throwaway directory, a fresh empty database
beside it, and the same loader, the same whole-set guards and the same engine
over both. ADR-0005 rule 8 -- applying all migrations to an empty store
reproduces the full canonical state -- is what makes that replay well-defined
rather than an approximation; a replay that disagreed with the real apply would
be a violation of rule 8 before it was a bug in ``accept``.

The copy is a real tree because the pipeline reads one: the loader re-reads each
``upsertRevision``'s body from the path its ``contentFile`` resolves to, and an
incoming proposal's bodies are not at those paths yet. Teaching the loader about
proposals would be the second implementation the ADR forbids, so the union is
staged on disk instead and the real loader reads it.
"""

from __future__ import annotations

import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from theurian.application.item_labels import CurrentItem, ItemLabels, ReplayedItems
from theurian.application.migration_engine import (
    ApplyReport,
    MigrationEngine,
    run_static_migration_guards,
)
from theurian.application.permissive_moves import PermissiveMove
from theurian.application.project_service import ProjectPaths, resolve_state_hash
from theurian.application.proposal_service import (
    CandidateMigrationSet,
    ProposalError,
    Replay,
    _their_words,
)
from theurian.cli.context import schema_root
from theurian.domain.context import RequestContext
from theurian.domain.errors import IrregularSourceFileError, TheurianError
from theurian.domain.identifiers import ItemId, ProjectId
from theurian.domain.migration import (
    MIGRATION_ENGINE_VERSION,
    current_revision_in,
    item_named_in,
)
from theurian.domain.ports import Clock
from theurian.domain.project import Project
from theurian.infrastructure.filesystem.migration_loader import load_migrations
from theurian.infrastructure.sqlite.connection import create_database, write_transaction
from theurian.infrastructure.sqlite.schema import SCHEMA_VERSION
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore, SqliteWriter
from theurian.security.paths import read_source_file, resolve_within_root

if TYPE_CHECKING:
    from theurian.domain.migration import LoadedMigrations

#: The branch a rehearsal's throwaway project row records. Nothing the replay
#: does reads it -- the engine writes knowledge rows, not repository metadata --
#: and a copy in a temporary directory is not a Git working tree to ask, so
#: asking would spend a subprocess on an answer with no reader. ``Project``
#: refuses an empty one.
_REHEARSAL_BRANCH = "main"


def apply_migration_set(  # noqa: PLR0913 -- everything that differs between a real apply and a replay, all keyword-only
    *,
    database: Path,
    write_lock: Path,
    project: Project,
    loaded: LoadedMigrations,
    clock: Clock,
    database_created: bool,
    already_locked: bool = False,
) -> ApplyReport:
    """Apply ``loaded`` into ``database``, inside one write transaction.

    The single definition of what applying a migration set *is*: ``migrate
    apply`` calls it against the project's own state database, and
    :func:`rehearse_migration_set` calls it against a throwaway one. Everything
    that differs between the two arrives as an argument, so there is no branch
    here that a replay takes and a real apply does not (ADR-0027 decision 2).

    ``database_created`` is the caller's answer to "did this run create the
    database?", and it is half the condition on the surfaceable-count record
    below. The other half is ``report.changed``, which only exists once the
    apply has run, which is why the condition is evaluated here rather than by
    the caller.

    ``already_locked`` is forwarded to :func:`write_transaction` unchanged and
    defaults to ``False`` for the same reason it does there: this function does
    not know, and must not guess, whether its caller already holds
    ``write_lock``. ``migrate apply``'s own composition-root code passes
    ``True`` (#468), because its critical section acquires the lock itself
    before calling in here, to cover the database creation and pointer publish
    this function never sees. :func:`rehearse_migration_set` below passes
    neither -- it takes the default, against a throwaway lock file no other
    process can contend for.

    Raises:
        MigrationChecksumMismatchError: If an applied migration's file changed.
        RevisionConflictError: If an operation's ``expectedRevision`` does not
            match what the store holds.
        UnenforceableScopeError, DuplicateContentFileError,
            AliasItemCollisionError, RelationCycleError: From the engine's own whole-set guards.
        StateDatabaseNotAFileError, StateDatabaseUnreadableError,
            StateDirectoryUnwritableError: From the transaction's own opener.
            The first refuses a named pipe, socket, device or directory at the
            database path before anything is opened (#526); the third refuses when the
            directory holding the database will not accept the write preparing a
            connection needs (#530). Neither says the file is damaged, and
            neither cure deletes state.
        WriteLockTimeoutError, WriteLockRefusedError, WriteLockUnusableError:
            From the lock, so none of them can be raised when ``already_locked``
            is ``True`` -- no acquisition happens here then.
            ``WriteLockUnusableError`` is any refusal met taking the lock -- a
            symbolic link at its path (#481), a directory there, a mode that
            denies this process, a directory above it that cannot be created
            (#520), or a named pipe where the lock file belongs (#526).
            ``WriteLockRefusedError`` is the other half of the timeout: ``flock``
            refusing the call rather than reporting a holder, which waiting
            cannot clear (#423).
        WriteTransactionBusyError: If another writer holds the database when
            this transaction tries to begin or commit. Raised whatever
            ``already_locked`` says: it comes from the transaction, not from the
            advisory lock, which cannot mediate a writer outside Theurian.
    """
    with write_transaction(database, write_lock, already_locked=already_locked) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(project)
        engine = MigrationEngine(clock, loaded.content_by_hash)
        report = engine.apply(writer, project.project_id, loaded.migration_set)
        if database_created or report.changed:
            # What a reader should be able to see after this apply, recorded by
            # the writer that produced it and inside its transaction (#30 PR2).
            # Three MCP tools compare their own live count against it and
            # disclose a difference as damage.
            #
            # **Not on every apply, and the condition is the honest half.**
            # `migrate apply` is step one of the remedy those tools publish. An
            # apply with nothing pending writes nothing, so re-recording there
            # would take the count *from the damaged state* and clear the signal
            # while the damage stands -- the remedy would manufacture the
            # all-clear it was run to earn. Recording only when this apply
            # created the database or applied a migration leaves the signal up
            # until the remedy's second step (delete `.theurian/state/`, apply
            # again) rebuilds the database, which is the step that actually
            # cures it.
            #
            # `database_created` is what keeps the other direction sound: a
            # project with no migrations at all still gets a record, so "no row"
            # can mean damage rather than "nothing has been applied yet".
            #
            # The residual, recorded rather than closed: an apply that *does*
            # have a migration to apply re-records over whatever the state holds
            # at that moment, so damage already present becomes the new
            # expectation and the signal clears. It is the same shape as the
            # pointer's -- a count is not a checksum, and the writer can only
            # record what it can read. Curing it needs an expectation that does
            # not live in the file it describes.
            writer.record_expected_surfaceable_count(project.project_id)
    return report


def rehearse_migration_set(candidate: CandidateMigrationSet, *, clock: Clock) -> Replay:
    """Prove ``candidate`` survives the pipeline ``migrate apply`` runs, or raise.

    Writes nothing outside the temporary directory it creates and removes: the
    project ``candidate`` names is only ever *read* from. Returning normally is
    the answer to "would this apply?"; what comes back is the items the replay
    left, read through the store's own item reader before the store is deleted,
    with that apply's permissive-move rows and the order it applied the set in,
    for the one caller that reads them (``ProposalService.accept``).

    The stages are ADR-0027 decision 2's, in its order: the real loader over the
    copy (schema, document limits, ``apiVersion``, and the digest verification it
    performs when it re-reads each referenced body), then the three whole-set
    guards ``migrate validate`` runs, then the apply itself. The guards run
    ahead of the apply rather than being left to the engine's own copies of them
    for the reason ``migrate apply`` runs them there too: a statically decidable
    refusal should not need a database to have been created first (#63).

    Raises:
        TheurianError: Whatever the loader, the guards or the engine raise. The
            caller translates; nothing is caught here, because a pre-check that
            swallowed a fault would answer "it applies" for a set that does not.
        OSError: If the copy cannot be staged.
    """
    with tempfile.TemporaryDirectory(prefix="theurian-rehearsal-") as scratch:
        root = _materialize(candidate, Path(scratch))
        paths = ProjectPaths.of(root, candidate.knowledge_directory)
        loaded = load_migrations(paths.root, paths.migrations, schema_root())

        run_static_migration_guards(loaded.migration_set)

        state_hash = resolve_state_hash(loaded, SCHEMA_VERSION)
        database = paths.database_for(state_hash)
        create_database(database, str(state_hash), MIGRATION_ENGINE_VERSION)
        report = apply_migration_set(
            database=database,
            write_lock=paths.write_lock,
            project=_rehearsal_project(candidate, paths, clock),
            loaded=loaded,
            clock=clock,
            database_created=True,
        )
        return Replay(
            _read_items(database, candidate.project_id),
            tuple(report.permissive_moves),
            tuple(report.applied),
            tuple(report.overwrites),
        )


def current_item_in(
    loaded: LoadedMigrations,
    item_id: ItemId,
    *,
    paths: ProjectPaths,
    project_id: ProjectId,
    clock: Clock,
) -> CurrentItem | None:
    """The item an update of ``item_id`` would replace, as the engine leaves it.

    The revision is :func:`current_revision_in`, the answer the drafter has always
    used. The labels are what :func:`apply_migration_set` -- the pipeline ``migrate
    apply`` runs -- leaves in a throwaway store, read back through the store's own
    item reader: a second fold of ``KnowledgeItem.with_revision`` here could
    disagree with the engine about what a later migration did to a label (ADR-0027
    decision 2). An item no migration creates answers ``None`` without a replay,
    which is one first-revision case; the other, an item created and never
    revised, answers its labels with ``revision_id=None``.

    Raises:
        ProposalError: If the set does not replay, so no label can be read. The
            message carries the engine's own words and the remedy points at
            ``.theurian/migrations/``: ``migrate validate``'s verdict does not
            rest on a replay, so it reports valid on exactly such a set. Also if
            the replay's scratch directory cannot be created, with a remedy that
            points at the temporary directory instead.
    """
    if not item_named_in(loaded.migration_set, item_id):
        return None
    revision = current_revision_in(loaded.migration_set, item_id)
    try:
        scratch = tempfile.TemporaryDirectory(prefix="theurian-labels-")
    except OSError as exc:
        raise ProposalError(
            f"{item_id.value} exists, but no scratch directory could be created to replay "
            f"its current labels: {exc.strerror or type(exc).__name__}",
            remedy="Nothing has been written. Free space in the system temporary directory, "
            "or set TMPDIR to a writable directory with room, then run this command again.",
        ) from exc
    try:
        with _scratch_database(
            loaded, scratch, paths=paths, project_id=project_id, clock=clock
        ) as (database, write_lock, project):
            apply_migration_set(
                database=database,
                write_lock=write_lock,
                project=project,
                loaded=loaded,
                clock=clock,
                database_created=True,
            )
            items = _read_items(database, project_id)
    except (TheurianError, OSError) as exc:
        raise ProposalError(
            f"{item_id.value} exists, but the approved migration set does not replay, so "
            f"its current labels cannot be read: {_their_words(exc)}",
            remedy="Nothing has been written. The fault is in .theurian/migrations/ -- read "
            "what the message names there and correct it, then draft again. "
            "`theurian migrate apply` runs the same replay.",
        ) from exc
    labels = items.get(item_id)
    if labels is None:
        raise ProposalError(
            f"{item_id.value} is created in the approved migration set but the replay "
            "holds no such item.",
            remedy="Nothing has been written. Run `theurian migrate apply`, which replays the "
            "same set and reports what stops it; if it applies cleanly, the replay reader is at "
            "fault and this is a Theurian defect to report.",
        )
    return CurrentItem(revision, labels)


@dataclass(frozen=True, slots=True)
class MoveReport:
    """``migrate validate``'s permissive-move report: its rows, or why its replay failed."""

    moves: tuple[PermissiveMove, ...] | None
    unavailable: str | None

    def __post_init__(self) -> None:
        if (self.moves is None) == (self.unavailable is None):
            raise ValueError("a report carries either its rows or why it has none")


def permissive_moves_in(
    loaded: LoadedMigrations, *, paths: ProjectPaths, project_id: ProjectId, clock: Clock
) -> MoveReport:
    """Replay ``loaded`` into a throwaway database for the moves the whole set makes.

    ``migrate validate``'s verdict does not rest on this replay, so a Theurian, OS
    or SQLite error from it becomes :attr:`MoveReport.unavailable` rather than
    propagating: its own words, bounded and redacted as a refusal quotes them.
    """
    try:
        with _scratch_database(
            loaded,
            tempfile.TemporaryDirectory(prefix="theurian-report-"),
            paths=paths,
            project_id=project_id,
            clock=clock,
        ) as (database, write_lock, project):
            report = apply_migration_set(
                database=database,
                write_lock=write_lock,
                project=project,
                loaded=loaded,
                clock=clock,
                database_created=True,
            )
    except (TheurianError, OSError, sqlite3.Error) as exc:
        return MoveReport(None, _their_words(exc))
    return MoveReport(tuple(report.permissive_moves), None)


@contextmanager
def _scratch_database(
    loaded: LoadedMigrations,
    scratch: tempfile.TemporaryDirectory[str],
    *,
    paths: ProjectPaths,
    project_id: ProjectId,
    clock: Clock,
) -> Iterator[tuple[Path, Path, Project]]:
    """A fresh, empty state database for ``loaded`` in ``scratch``, its lock and its project.

    ``scratch`` is removed on exit. The caller creates it, so it decides what a
    failure to create one is reported as.

    The caller applies through :func:`apply_migration_set` itself (ADR-0027 decision
    2): ``test_the_accept_replay_and_migrate_apply_reach_one_apply_function`` reads
    that name in ``current_item_in``'s own code.
    """
    with scratch as directory:
        scratch_path = Path(directory)
        state_hash = resolve_state_hash(loaded, SCHEMA_VERSION)
        project = Project(
            project_id=project_id,
            root_path=str(scratch_path.resolve()),
            repository_url=None,
            default_branch=_REHEARSAL_BRANCH,
            knowledge_directory=PurePosixPath(
                paths.knowledge_dir.relative_to(paths.root).as_posix()
            ),
            registered_at=clock.now(),
        )
        database = scratch_path / "replay.sqlite3"
        create_database(database, str(state_hash), MIGRATION_ENGINE_VERSION)
        yield database, scratch_path / "replay.lock", project


def _read_items(database: Path, project_id: ProjectId) -> ReplayedItems:
    """Every item ``database`` holds, read through the store's own item reader."""
    with SqliteCanonicalStore(database) as store:
        items = store.list_items(RequestContext(project_id=project_id))
    return {
        item.item_id: ItemLabels(item.sensitivity, item.trust_level, item.namespace, item.status)
        for item in items
    }


def _materialize(candidate: CandidateMigrationSet, target: Path) -> Path:
    """Write ``candidate`` out as a project tree under ``target``.

    Landed files are copied first and the incoming ones written over them, which
    is what makes the result the *union* rather than the two sets side by side:
    a body this proposal replaces is replaced here too.

    Every read goes through :func:`read_source_file`, so the copy inherits its
    containment, file-shape and size checks (SEC-7, SEC-8).

    **It does not inherit the whole of the loader's route check, and that is
    recorded rather than implied.** The body half of ``candidate.landed`` is the
    loader's own ``resolved_content_path`` -- a path ``resolve()`` has already
    flattened -- so the symlink-route walk inside :func:`read_source_file` has no
    link left to see there. What bounds these reads is the
    :func:`~theurian.security.paths.assert_no_symlink_escape` the loader made on
    the author's ``contentFile`` when the landed set was read: upstream, and on
    the one string that could still carry a route. The migration-file half is
    ``migration.source_path``, which comes from a directory listing and is not
    flattened, so for those entries the walk here is live. An earlier version of
    this paragraph claimed the copy read "under the same checks" as the loader;
    it reads under one fewer, and which one is the point. Every write is
    proved to stay under ``target`` before it happens: the relative paths come
    from the loader, which already contained them, and re-proving a *write* is
    cheaper than reasoning about whether that stays true.

    Two fidelity limits, stated because the closure argument depends on them:

    * The copy reproduces the tree's **path structure**, not its inode sharing.
      Two landed bodies hardlinked to one inode become two files here, so a
      duplicate-body collision that the real project would show through
      ``st_ino`` need not show here. Git cannot commit a live hardlink -- a
      fresh clone gets a distinct inode holding the committed blob -- so the
      documented channel cannot deliver that pair; the same residual
      ``ProposalService.accept`` already records for its own reads.
    * Case sensitivity is the filesystem's, and the temporary directory need not
      be on the same filesystem as the project. Two spellings that reach one
      file on a case-insensitive project could reach two here, or the reverse.
      This is *assumed* harmless rather than measured: the system temporary
      directory and a user's checkout are expected to share case semantics on the
      platforms this ships to, but nothing here probes it. It does not have to,
      because the real accept path keys duplicate-body detection on filesystem
      identity (``st_dev``/``st_ino``) against the *actual* project, not this copy
      (issue #210) -- so a case-fidelity mismatch here at worst costs the
      rehearsal a collision the real move still catches, never the reverse.

    And the copy is taken *after* the caller loaded the set it describes, so a
    landed file edited in between is replayed as it is now rather than as it was
    read. That is the examine-to-move window ADR-0027 decision 2 records as its
    third residue, not a new one: the window exists because the accept path's
    moves are not under the write lock, and this lengthens it rather than
    opening it.

    **A landed read that refuses an irregular file names it** (the
    ``application/proposal_service.py::_commit`` shape, #400). A landed body
    can be swapped for a FIFO, socket or device between the caller's load and
    this copy -- the same window the paragraph above already records for an
    edited one -- and left bare,
    :class:`~theurian.domain.errors.IrregularSourceFileError` would propagate
    saying only that *a* file is irregular, naming none of ``candidate.landed``.
    ``relative`` is safe to attach: it is not an authored string but
    ``candidate.landed``'s own entries, which :meth:`ProposalService._candidate`
    builds from what the loader itself recorded when the landed set was
    originally read (``migration.source_path``,
    ``operation.resolved_content_path``), already project-relative and already
    proved contained then.
    """
    for relative in candidate.landed:
        try:
            data = read_source_file(candidate.root, PurePosixPath(relative))
        except IrregularSourceFileError as exc:
            raise IrregularSourceFileError(exc.shape, referrer=relative) from exc
        _write(target, relative, data)
    for relative, data in candidate.incoming:
        _write(target, relative, data)
    return target


def _write(target: Path, relative: str, data: bytes) -> None:
    """Write one file into the copy, at a path proved to stay inside it.

    ``resolve_within_root`` returns the path with every symlink already
    followed, and the write goes to *that*, so a link planted inside the copy
    cannot redirect the write out of it -- it is refused if it points outside
    and irrelevant if it points inside. Planting one at all means write access to
    a ``tempfile`` directory created ``0o700`` and owned by this process, which
    is the same boundary the whole accept path already sits behind.
    """
    destination = resolve_within_root(target, PurePosixPath(relative))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def _rehearsal_project(
    candidate: CandidateMigrationSet, paths: ProjectPaths, clock: Clock
) -> Project:
    """The project row the replay's own store needs before any knowledge can land.

    Carries the real project's id, because the engine writes every row under it
    and a replay under a different id would exercise a different key. Everything
    else describes the copy: its root is the temporary directory, and it has no
    repository, because it is not a Git working tree.
    """
    return Project(
        project_id=candidate.project_id,
        root_path=str(paths.root),
        repository_url=None,
        default_branch=_REHEARSAL_BRANCH,
        knowledge_directory=candidate.knowledge_directory,
        registered_at=clock.now(),
    )
