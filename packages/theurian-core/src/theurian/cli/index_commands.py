"""``theurian index`` — build and inspect the retrieval index (FR-R2).

A composition root: this is where the abstract builder meets the SQLite store,
the SQLite index, and the default embedder (ADR-0003).

Building writes a *new* file and then swaps a pointer, and the old build stays on
disk until `theurian index gc` reclaims it.

**That sentence was false from Milestone 5 until now, and it is worth saying
which part.** It claimed a search running across a rebuild "keeps reading a
consistent index rather than one being written underneath it". Publishing reaped
every build the pointer did not name, so the old file was gone before the command
returned; and `SqliteIndexStore` opened a connection per call, so there was no
reader for the old file to stay consistent *for*. The prose described a guarantee
before the mechanism existed to make it true, which is how it survived the
amendment that withdrew it (ADR-0022 point 6).

Both halves now exist. Publishing no longer deletes, and reclaiming is
`theurian index gc`; a search holds one read connection for the duration of a
request (`SqliteIndexStore.session`, ADR-0024 point 7), so a request already in
flight finishes against the build it started on even if `gc` unlinks it
underneath.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Annotated, Any, Final

import typer

from theurian.application.index_builder import IndexBuilder, IndexRequest
from theurian.application.project_service import (
    INDEX_POINTER_REMEDY,
    ProjectPaths,
    read_active_index_pointer,
)
from theurian.domain.context import RequestContext
from theurian.domain.enums import SURFACEABLE_STATUSES, KnowledgeStatus
from theurian.domain.errors import TheurianError
from theurian.domain.state import ActiveState
from theurian.infrastructure.embedding import HashingEmbedding
from theurian.infrastructure.sqlite.index_schema import INDEX_SCHEMA_VERSION
from theurian.infrastructure.sqlite.index_store import SqliteIndexStore, fts5_available
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore

index_app = typer.Typer(help="Build and inspect the retrieval index.", no_args_is_help=True)

JsonOption = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")]


@index_app.command("build")
def index_build(
    include_unapproved: Annotated[
        bool,
        typer.Option(
            "--include-unapproved",
            help="Also index drafts and proposals. Never indexes rejected knowledge.",
        ),
    ] = False,
    no_embeddings: Annotated[
        bool,
        typer.Option("--no-embeddings", help="Build lexical search only."),
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """Build a retrieval index from this project's canonical state.

    The index is derived and disposable (ADR-0004). Deleting it costs a rebuild,
    never data — which is why it is a separate file from the canonical store.

    A build that finds nothing to index while the canonical state holds knowledge
    is refused rather than published. Publishing it would put a *correct-looking*
    empty index in place: every later search answers ``count: 0`` with
    ``indexed: true``, and ``theurian index status`` reports nothing to do. That
    is the shape a project-id mismatch takes, and this refusal is what turns it
    from silent into a message naming the ids involved.
    """
    from theurian.cli.commands import _emit, _require_project  # noqa: PLC0415 - cycle

    # The second value is the state database, not the repository root. The
    # context already carries resolved paths, so deriving them again from a
    # database path is both redundant and wrong.
    context, _ = _require_project(as_json)
    paths = context.paths
    active = _require_buildable_state(paths, as_json)
    if active is None:
        return

    # The context already carries the generator every other command uses, so
    # index build ids sort alongside migration and revision ids.
    index_build_id = context.ids.new_ulid().value
    builder = IndexBuilder(
        store_factory=SqliteCanonicalStore,
        index_factory=SqliteIndexStore,
        embedder=None if no_embeddings else HashingEmbedding(),
    )
    # Built under a name `theurian index gc` does not reap, then renamed into
    # place. `gc` reclaims every build the pointer does not name, and a build in
    # progress is not yet named by it, so without this the two race -- and the
    # loser is the build, silently, mid-write. `os.replace` is atomic on POSIX,
    # so a file under the completed name is complete by construction. The purge
    # writes under the same discipline (`index_purge.purge_into`).
    final_path = paths.index_for(index_build_id)
    request = IndexRequest(
        database=paths.state / active.database_filename,
        index_path=Path(f"{final_path}.building"),
        project_id=context.project_id.value,
        state_hash=str(active.state_hash),
        index_build_id=index_build_id,
        include_unapproved=include_unapproved,
    )

    report = _run_build(builder, request, active, as_json=as_json)
    if report is None or _refuse_if_empty(
        report, request, context.project_id.value, as_json=as_json
    ):
        return
    os.replace(request.index_path, final_path)  # noqa: PTH105 - the atomic primitive
    report = {**report, "indexPath": str(final_path)}

    _publish(
        paths,
        index_build_id=index_build_id,
        state_hash=str(active.state_hash),
        project_id=context.project_id.value,
        indexes_unapproved=include_unapproved,
    )
    # Publishing does not reclaim (ADR-0024 point 6). Reaping the previous build
    # here is what made ADR-0022's "the previous build is not deleted" false, and
    # measured against a reader it cost 2,627 errors against 40 answered searches
    # in 1.5 seconds. `theurian index gc` reclaims, explicitly.
    _emit({**report, "published": True}, as_json=as_json)


def _require_buildable_state(paths: ProjectPaths, as_json: bool) -> ActiveState | None:
    """Preconditions for a build: applied canonical state, and FTS5 support.

    Split out so `index_build` reads as build-then-publish, with everything
    that can refuse the attempt before it starts gathered in one place.
    """
    from theurian.cli.commands import _fail, _read_active  # noqa: PLC0415 - cycle

    # `_read_active`, not `read_active_state`: an unreadable pointer and an
    # absent one need different cures, and this function's own refusal below --
    # "run `theurian migrate apply` first" -- is the wrong one for a file that
    # has to be deleted before applying can help.
    active = _read_active(paths, as_json)
    if active is None:
        _fail(
            "This project has no built knowledge state, so there is nothing to index.",
            remedy="Run `theurian migrate apply` first.",
            as_json=as_json,
            code=1,
        )
        return None

    if not fts5_available():
        _fail(
            "This Python's SQLite was built without FTS5, so lexical search is unavailable.",
            remedy="Install Python from python.org or your distribution's python3 package.",
            as_json=as_json,
            code=1,
        )
        return None

    return active


def _run_build(
    builder: IndexBuilder, request: IndexRequest, active: ActiveState, *, as_json: bool
) -> dict[str, Any] | None:
    """Run the build, converting a broken canonical store into ``{error, remedy}``.

    A half-built index is worse than none: it looks complete and is not, and
    only the build knows it failed. Nothing is published on either failure
    branch below, so retrieval keeps using the previous build -- which is why
    the partial file is unlinked before reporting, not after.
    """
    from theurian.cli.commands import _fail  # noqa: PLC0415 - cycle

    try:
        return builder.build(request)
    except (TheurianError, sqlite3.Error, OSError) as exc:
        # A corrupt or unreadable state database used to escape as a raw
        # `sqlite3.DatabaseError`: exit 1, empty stdout, a traceback at the user,
        # and none of the `{"error", "remedy"}` shape every other command
        # promises (CP-2) -- in exactly the situation that is recoverable,
        # because canonical state rebuilds from Git-tracked migrations.
        request.index_path.unlink(missing_ok=True)
        _fail(
            f"Building the index failed: {exc}",
            remedy=(
                f"Nothing was published, so retrieval still uses the previous index. If the "
                f"state database is unreadable, delete .theurian/state/"
                f"{active.database_filename} and run `theurian migrate apply`, then retry; "
                f"canonical state rebuilds from Git-tracked migrations."
            ),
            as_json=as_json,
            code=1,
        )
        return None
    except Exception:
        request.index_path.unlink(missing_ok=True)
        raise


def _refuse_if_empty(
    report: dict[str, Any], request: IndexRequest, project_id: str, *, as_json: bool
) -> bool:
    """Refuse to publish a build that indexed nothing while the store holds
    knowledge, and report whether it was refused.

    Publishing it would put a *correct-looking* empty index in place: every
    later search answers ``count: 0`` with ``indexed: true``, and ``theurian
    index status`` reports nothing to do -- the shape a project-id mismatch
    takes, indistinguishable from a project that simply has no knowledge yet.
    """
    from theurian.cli.commands import _fail  # noqa: PLC0415 - cycle

    if report["chunks"] != 0:
        return False
    try:
        available = _indexable_items(
            request.database, include_unapproved=request.include_unapproved
        )
    except TheurianError as exc:
        # A second read session over the same file, so `_run_build`'s conversion
        # one function above does not cover it -- and it reaches rows the build
        # itself never touches. Measured against the real CLI on a project whose
        # only knowledge is `draft`, so the build indexes zero chunks and this
        # line runs: damaging `projects.registered_at` or `projects.root_path`
        # ended `theurian index build --json` in a Rich traceback, exit 1, empty
        # stdout, and the cell published through `__cause__` -- the same escape
        # the `migrate` commands were just converted for.
        request.index_path.unlink(missing_ok=True)
        _fail(
            f"This build indexed nothing, and the canonical state could not say whether "
            f"that is correct: {exc}",
            remedy=(
                "Nothing was published, so retrieval still uses the previous index. Delete "
                ".theurian/state/ and run `theurian migrate apply`, then retry; canonical "
                "state rebuilds from Git-tracked migrations."
            ),
            as_json=as_json,
            code=1,
        )
        return True
    if not available:
        return False
    request.index_path.unlink(missing_ok=True)
    _fail(
        f"Indexing produced no chunks, but the canonical state holds knowledge "
        f"({_render_counts(available)}). This build indexed {project_id!r}.",
        remedy=_empty_build_remedy(project_id, available),
        as_json=as_json,
        code=1,
    )
    return True


@index_app.command("status")
def index_status(as_json: JsonOption = False) -> None:
    """Report whether the index still reflects the project's knowledge.

    A stale index is a correctness problem wearing the costume of a relevance
    problem: searches keep working, and quietly answer from knowledge that has
    changed.

    Three hashes matter and they can all differ, so all three are reported:

    - ``currentStateHash`` — derived from the migrations and their content,
      i.e. what the knowledge *is* right now;
    - ``builtStateHash`` — what the canonical database actually holds;
    - ``indexStateHash`` — what the index was built from.

    Comparing only the last two would call an index fresh whenever the database
    was equally out of date, which is precisely when a person most needs to be
    told otherwise.

    A pointer file that exists but does not name a usable build -- truncated
    JSON, a JSON array, an object without ``indexBuildId``, arbitrary bytes --
    is reported as ``indexPointerCorrupt`` rather than folded into "never
    built". ``knowledge.search`` already tells an agent this exact file is
    unreadable and names it for deletion (``index-pointer-invalid``); reporting
    "no built state" here for the same file would send a person to run
    ``theurian migrate apply`` for state that was never the problem, while the
    corrupt file sat on disk the whole time.
    """
    from theurian.cli.commands import _emit, _read_active, _require_project  # noqa: PLC0415 - cycle

    context, _ = _require_project(as_json)
    paths = context.paths
    # Converted here as well as inside `_require_project`, which has already read
    # the same file: the two reads straddle a window in which `migrate apply` --
    # or the very deletion this command's remedy asks for -- replaces the
    # pointer, and a raise landing in it would cost the whole payload.
    active = _read_active(paths, as_json)
    pointer = read_active_index_pointer(paths)
    published = dict(pointer.payload) if pointer.payload is not None else None

    current = str(context.state_hash)
    built = str(active.state_hash) if active else None
    indexed = (published or {}).get("stateHash")

    needs_apply = built != current
    # An index whose schema this build does not understand is unusable no matter
    # how fresh its state hash is, and retrieval already falls back for it. Left
    # out of `stale`, this command would answer "fresh, nothing to do" for the
    # very file a search had just refused to read.
    schema = _index_schema_version(paths, published)
    # Chunks are stamped with the project id that built them, so an index built
    # for another id answers every query with nothing while reporting itself
    # indexed. A pointer written before this field existed cannot be checked, so
    # it counts as orphaned: one rebuild makes it verifiable, and claiming
    # freshness that was never established is what this command exists to avoid.
    index_project = (published or {}).get("projectId")
    orphaned = published is not None and index_project != context.project_id.value
    stale = published is None or indexed != current or schema != INDEX_SCHEMA_VERSION or orphaned

    _emit(
        {
            "built": published is not None,
            "indexPointerCorrupt": pointer.unreadable,
            "indexBuildId": (published or {}).get("indexBuildId"),
            "indexStateHash": indexed,
            "builtStateHash": built,
            "currentStateHash": current,
            "projectId": context.project_id.value,
            "indexProjectId": index_project,
            "indexSchemaVersion": schema,
            "expectedIndexSchemaVersion": INDEX_SCHEMA_VERSION,
            "stale": stale,
            "orphaned": orphaned,
            "knowledgeNotApplied": needs_apply,
            "remedy": _remedy(
                stale=stale,
                needs_apply=needs_apply,
                orphaned=orphaned,
                pointer_corrupt=pointer.unreadable,
            ),
        },
        as_json=as_json,
    )


def _index_schema_version(paths: ProjectPaths, published: dict[str, Any] | None) -> int | None:
    """The schema version of the published build, or ``None`` if there is none.

    Never raises. A pointer naming a path outside the project, or a file that has
    since been deleted, is a status to report rather than a command to fail.
    """
    if published is None:
        return None
    try:
        path = paths.index_for(str(published.get("indexBuildId", "")))
    except TheurianError:
        return 0
    return SqliteIndexStore(path).schema_version() if path.is_file() else 0


def _remedy(*, stale: bool, needs_apply: bool, orphaned: bool, pointer_corrupt: bool) -> str:
    """The next command to run, in the order it has to be run in.

    A corrupt pointer is named first, ahead of even ``orphaned``: it is the one
    case where "run `theurian index build`" alone understates what happened, and
    it is the exact remedy ``knowledge.search`` already gives an agent for the
    same file (:data:`~theurian.application.project_service.INDEX_POINTER_REMEDY`)
    -- the two surfaces must agree, not merely both suggest a rebuild.

    Indexing before applying would build from a database that is itself behind,
    producing a fresh-looking index of stale knowledge. An orphaned index is
    named next because the rebuild it asks for subsumes both other remedies.
    """
    if pointer_corrupt:
        return INDEX_POINTER_REMEDY
    if orphaned:
        return (
            "This index was built for a different project id. Run `theurian index build`; "
            "if it refuses, the canonical rows carry the other id too -- delete "
            ".theurian/state/ and run `theurian migrate apply` first."
        )
    if needs_apply:
        return "Run `theurian migrate apply`, then `theurian index build`."
    if stale:
        return "Run `theurian index build`."
    return ""


#: Filenames `theurian index gc` will consider at all.
#:
#: **The prefix is the whole safety property, not a tidiness convention.**
#: `theurian-state-<hash>.sqlite` -- the canonical store, which is *not* derived
#: and *not* disposable -- lives in the same directory. A glob that lost this
#: prefix would reclaim it, and the suite stayed green when exactly that mutation
#: was applied. ADR-0022 point 2 says the prefix is load-bearing for the same
#: reason, one layer up: "a glob that could not tell them apart would hand a
#: retrieval index to the canonical store".
INDEX_FILENAME_PREFIX: Final = "theurian-index-"


@index_app.command("gc")
def index_gc(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report what would be reclaimed, and reclaim nothing."),
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """Delete index builds the published pointer does not name.

    Publishing a build no longer deletes the one it replaced (ADR-0024 point 6),
    so builds accumulate: measured on an 800-document corpus, ten publishes leave
    ten files and 246.0 MB where one build is 24.5 MB. This is what reclaims
    them, and it is explicit for the reason ADR-0007 and ADR-0017 already give
    for state databases -- automatic deletion of a file something may still be
    reading is the failure this command exists to avoid, not the service it
    provides.

    **What a search in flight is guaranteed, and from when.** A request that has
    already entered `SqliteIndexStore.session` holds an open descriptor, and on
    POSIX that keeps the file readable after its name is gone: measured, one
    request of four index reads with the unlink landing after the first answered
    4 of 4 inside a session against 1 of 4 with a connection per call. The
    guarantee starts at session acquisition, not at the moment the request
    arrived -- a request reaped between resolving the pointer and acquiring its
    connection has no descriptor to protect, and degrades to the substring-scan
    fallback rather than failing. A request that *starts* after the reap resolves
    the pointer to the published build, which is never reclaimed here.

    Four things are never reclaimed:

    - the build the pointer names, and a `gc` run at all is refused when that
      build's file is missing -- a pointer aimed at nothing is a broken project,
      and treating every real build as unreferenced is the worst possible reading
      of it;
    - any build whose id sorts **above** the published one, which is a build that
      started later and has not published yet;
    - anything under a `.building` suffix, which is a writer that has not renamed
      into place -- or the leftovers of one that crashed, which this cannot tell
      apart and so reports rather than deletes;
    - everything, when the pointer cannot be read at all, because a pointer this
      command cannot parse is not evidence that any particular build is
      unreferenced.
    """
    from theurian.cli.commands import _emit, _fail, _require_project  # noqa: PLC0415 - cycle

    context, _ = _require_project(as_json)
    paths = context.paths
    pointer = read_active_index_pointer(paths)
    if pointer.unreadable:
        _fail(
            "This project's active index pointer cannot be read, so nothing can be shown to "
            "be unreferenced.",
            remedy=INDEX_POINTER_REMEDY,
            as_json=as_json,
            code=1,
        )
        return

    published = str((pointer.payload or {}).get("indexBuildId", ""))
    if published and not paths.index_for(published).is_file():
        _fail(
            f"The published index pointer names build {published}, and that build's file is "
            f"not there. Nothing was reclaimed.",
            remedy=(
                "Run `theurian index build` to publish a build that exists. Reclaiming now "
                "would delete every build on disk, because none of them is the published one."
            ),
            as_json=as_json,
            code=1,
        )
        return

    reclaimable = _reclaimable(paths, published=published)
    stranded = sorted(path.name for path in paths.state.glob(f"{INDEX_FILENAME_PREFIX}*.building"))
    freed = sum(path.stat().st_size for path in reclaimable if path.is_file())
    if not dry_run and not _reclaim(reclaimable, as_json=as_json):
        return

    _emit(
        {
            "publishedIndexBuildId": published or None,
            "reclaimed": sorted(path.name for path in reclaimable),
            "bytesReclaimed": freed,
            # Reported, never deleted. A `.building` file is a live writer or a
            # crash's leftovers and this cannot tell them apart, so telling the
            # operator what is stranded is the honest half of the job; reclaiming
            # it needs an age or liveness heuristic that does not exist yet.
            "strandedBuilding": stranded,
            "dryRun": dry_run,
        },
        as_json=as_json,
    )


def _reclaim(paths_to_remove: list[Path], *, as_json: bool) -> bool:
    """Unlink each file, converting an OS refusal into this command's contract.

    Returns whether the run may report success.

    An unguarded loop is the CP-2 escape `_refuse_if_empty` was built for: a
    read-only state directory raised a bare `PermissionError` through Typer as a
    Rich traceback, exit 1, and **empty stdout under `--json`** -- for a
    condition whose remedy is one `chmod`. `IsADirectoryError` did the same for a
    directory named like a build.
    """
    from theurian.cli.commands import _fail  # noqa: PLC0415 - cycle

    for path in paths_to_remove:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            _fail(
                f"Reclaiming {path.name} failed: {exc.strerror or exc}.",
                remedy=(
                    "Check that .theurian/state/ is writable and holds no directory named "
                    "like an index build, then run `theurian index gc` again. Nothing else "
                    "was reclaimed."
                ),
                as_json=as_json,
                code=1,
            )
            return False
    return True


def _reclaimable(paths: ProjectPaths, *, published: str) -> list[Path]:
    """Index files the pointer does not name, and their sidecars.

    **Two mechanisms, because neither covers the other's window**, and keeping
    only the second is what a review caught:

    - the `.building` suffix covers a writer that has not finished. Both the
      builder and the purge write there and `os.replace` into position, so a file
      under the completed name is complete by construction. The glob is
      `*.sqlite` and not `*.sqlite*` for exactly this reason.
    - **ULID ordering covers a writer that has finished and not yet published.**
      `index_build` renames into place and *then* writes the pointer, and a `gc`
      landing between those two reclaimed the file, after which the pointer named
      nothing. Reproduced against the real CLI: 8 of 12 runs published a pointer
      to a reclaimed build. `.building` cannot see this window -- the file is
      already under its final name -- so the rule the old `_reclaim` carried is
      restored beside it.

    **Neither is complete alone, and in the shipped default there is no residual
    at all.** The daemon serialises every write through one lock (ADR-0018), so
    only one build or purge runs at a time and the ULID rule is exact: a finished
    build's id is strictly above the published one until it publishes. A residual
    appears only under the *unsupported* configuration of concurrent direct-CLI
    builds against one project, and even then it is narrow -- a build reclaimed in
    the write-to-publish window must have an id sorting **below** the published
    one, which means another build minted a later id and published first while
    this one was between its `os.replace` and its `_publish`. The only writers are
    `index build` and the purge, both of which take the `.building` path, so an
    unfinished build is never a candidate regardless. Recorded rather than closed
    because the supported deployment does not reach it, and closing it for the
    unsupported one is the single-writer interface ADR-0018 still owes the index.

    Sidecars are matched explicitly rather than by widening the glob, because
    `-wal` and `-shm` are the only two that exist and `*` would take `.building`
    with them.

    An empty `published` reclaims nothing. A project with no pointer has no
    build that is *known* to be unreferenced -- every file on disk might be the
    one a rebuild is about to name -- and deleting on that basis is how a `gc`
    turns "you have not built an index yet" into "your index is gone".
    """
    if not published:
        return []

    reclaimable: list[Path] = []
    for build in sorted(paths.state.glob(f"{INDEX_FILENAME_PREFIX}*.sqlite")):
        build_id = build.name[len(INDEX_FILENAME_PREFIX) : -len(".sqlite")]
        if build_id >= published:
            # `>=` rather than `!=`: equal is the published build, and greater is
            # a build that started later and may be about to publish.
            continue
        reclaimable.append(build)
        reclaimable.extend(
            sidecar for sidecar in (Path(f"{build}-wal"), Path(f"{build}-shm")) if sidecar.exists()
        )
    return reclaimable


def _publish(
    paths: ProjectPaths,
    *,
    index_build_id: str,
    state_hash: str,
    project_id: str,
    indexes_unapproved: bool,
) -> None:
    """Point retrieval at a finished build, atomically.

    Write-to-temp then ``os.replace``, which is atomic on POSIX. A reader must
    never observe a half-written pointer, because that would send it to an index
    that does not exist (the same reasoning as ADR-0007).
    """
    pointer = paths.active_index_pointer
    pointer.parent.mkdir(parents=True, exist_ok=True)
    temporary = pointer.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(
            {
                "indexBuildId": index_build_id,
                "stateHash": state_hash,
                # Every chunk in the file is stamped with the project id that
                # built it, and nothing else records which one that was. Without
                # it an index orphaned by an id change is indistinguishable from
                # a project that simply has no knowledge: `count: 0`,
                # `indexed: true`, and a status saying nothing needs doing.
                "projectId": project_id,
                # Recorded so a search can say *why* `includeUnapproved=True`
                # returned nothing, instead of looking like an empty result.
                "indexesUnapproved": indexes_unapproved,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, pointer)  # noqa: PTH105 - os.replace is the atomic primitive


def _indexable_items(database: Path, *, include_unapproved: bool) -> dict[str, int]:
    """How many items each project in the canonical store offered this build.

    Deliberately repeats ``IndexBuilder``'s selection rule rather than sharing
    it. The builder reports what it indexed; this reports what was there to be
    indexed, and the whole value of the pair is in noticing when the two
    disagree — which one shared implementation could not do.

    Keyed by project id and ordered by it, because the answer reaches an error
    message that has to read the same way twice.
    """
    counts: dict[str, int] = {}
    with SqliteCanonicalStore(database) as store:
        for project in store.list_projects():
            offered = sum(
                1
                for item in store.list_items(RequestContext(project_id=project.project_id))
                if item.current_revision_id is not None
                and item.status in SURFACEABLE_STATUSES
                and (include_unapproved or item.status is KnowledgeStatus.APPROVED)
            )
            if offered:
                counts[project.project_id.value] = offered
    return counts


def _render_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{pid!r}: {n} item{'' if n == 1 else 's'}" for pid, n in counts.items())


def _empty_build_remedy(project_id: str, available: dict[str, int]) -> str:
    """Name the cause, which is almost always an id that changed under the data.

    The store keeps its rows per project id, so knowledge applied under one id is
    invisible to a build running under another. Re-applying does not fix it:
    ``migrate apply`` is idempotent per project, and the revision rows it would
    need are already spoken for by the first id.
    """
    others = [pid for pid in available if pid != project_id]
    if others:
        return (
            f"This repository's knowledge was applied under "
            f"{', '.join(repr(pid) for pid in others)}. Register the repository under one "
            f"id, delete .theurian/state/, and run `theurian migrate apply` followed by "
            f"`theurian index build`."
        )
    return (
        "Items exist under this id but none of them resolved to a revision, which means "
        "the state database was written under another project id. Delete .theurian/state/ "
        "and run `theurian migrate apply`, then retry."
    )
