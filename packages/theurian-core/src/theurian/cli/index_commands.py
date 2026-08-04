"""``theurian index`` — build and inspect the retrieval index (FR-R2).

A composition root: this is where the abstract builder meets the SQLite store,
the SQLite index, and the default embedder (ADR-0003).

Building writes a *new* file and then swaps a pointer. The old build stays on
disk until the next build replaces it, so a search running while a rebuild
happens keeps reading a consistent index rather than one being written
underneath it.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Annotated, Any

import typer

from theurian.application.index_builder import IndexBuilder, IndexRequest
from theurian.application.project_service import (
    INDEX_POINTER_REMEDY,
    ProjectPaths,
    read_active_index,
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
    request = IndexRequest(
        database=paths.state / active.database_filename,
        index_path=paths.index_for(index_build_id),
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

    _publish(
        paths,
        index_build_id=index_build_id,
        state_hash=str(active.state_hash),
        project_id=context.project_id.value,
        indexes_unapproved=include_unapproved,
    )
    _reclaim(paths, keep=index_build_id)
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
    available = _indexable_items(request.database, include_unapproved=request.include_unapproved)
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


def _reclaim(paths: ProjectPaths, *, keep: str) -> None:
    """Delete superseded index builds — only those the pointer does not name.

    The first version kept whatever id *this* process had built and claimed
    POSIX would keep an in-use file readable until its last handle closed. Both
    were wrong:

    - ``SqliteIndexStore`` holds no handle. It opens and closes per call, and one
      search opens several connections, so every gap between them is a window in
      which the file can vanish. Worse, ``sqlite3.connect`` then *creates* an
      empty database at the deleted path, which defeats the "no index file, fall
      back" branch and surfaces a raw `no such table` to the agent.
    - Two concurrent builds would each delete the other's file, leaving whichever
      published first pointing at nothing.
    - A build that had finished writing its file but not yet published lost it to
      whichever build published first, and then published a pointer to nothing.
      So only builds *older* than the published one are reclaimed. Index build
      ids are ULIDs, so lexical order is creation order: a greater id can only
      belong to a build that started later and has not published yet, and a file
      left behind by a crash is reclaimed by the next build to publish, whose id
      is greater still.

    So this reads the pointer and measures against what it names, rather than
    against what this process happens to have produced — and parses the whole id
    out of the filename first, because a substring comparison would treat a build
    whose id merely contains another as related to it.
    """
    published = read_active_index(paths)
    current = str(published.get("indexBuildId", "")) if published else keep

    for stale in paths.state.glob("theurian-index-*.sqlite*"):
        # `theurian-index-<id>.sqlite`, plus any `-wal` / `-shm` beside it.
        build_id = stale.name[len("theurian-index-") :].split(".", 1)[0]
        if build_id < current:
            stale.unlink(missing_ok=True)


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
