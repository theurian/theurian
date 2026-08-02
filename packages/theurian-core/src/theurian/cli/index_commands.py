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
from typing import Annotated, Any

import typer

from theurian.application.project_service import ProjectPaths, read_active_state
from theurian.application.retrieval_service import IndexBuilder, IndexRequest
from theurian.infrastructure.embedding import HashingEmbedding
from theurian.infrastructure.sqlite.index_store import SqliteIndexStore, fts5_available
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore

index_app = typer.Typer(help="Build and inspect the retrieval index.", no_args_is_help=True)

JsonOption = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")]


@index_app.command("build")
def index_build(
    include_unapproved: Annotated[
        bool,
        typer.Option("--include-unapproved", help="Also index drafts. Off by default."),
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
    """
    from theurian.cli.commands import _emit, _fail, _require_project  # noqa: PLC0415 - cycle

    # The second value is the state database, not the repository root. The
    # context already carries resolved paths, so deriving them again from a
    # database path is both redundant and wrong.
    context, _ = _require_project(as_json)
    paths = context.paths
    active = read_active_state(paths)

    if active is None:
        _fail(
            "This project has no built knowledge state, so there is nothing to index.",
            remedy="Run `theurian migrate apply` first.",
            as_json=as_json,
            code=1,
        )
        return

    if not fts5_available():
        _fail(
            "This Python's SQLite was built without FTS5, so lexical search is unavailable.",
            remedy="Install Python from python.org or your distribution's python3 package.",
            as_json=as_json,
            code=1,
        )
        return

    # The context already carries the generator every other command uses, so
    # index build ids sort alongside migration and revision ids.
    index_build_id = context.ids.new_ulid().value
    builder = IndexBuilder(
        store_factory=SqliteCanonicalStore,
        index_factory=SqliteIndexStore,
        embedder=None if no_embeddings else HashingEmbedding(),
    )

    report = builder.build(
        IndexRequest(
            database=paths.state / active.database_filename,
            index_path=paths.index_for(index_build_id),
            project_id=context.project_id.value,
            state_hash=str(active.state_hash),
            index_build_id=index_build_id,
            include_unapproved=include_unapproved,
        )
    )

    _publish(paths, index_build_id=index_build_id, state_hash=str(active.state_hash))
    _emit({**report, "published": True}, as_json=as_json)


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
    """
    from theurian.cli.commands import _emit, _require_project  # noqa: PLC0415 - cycle

    context, _ = _require_project(as_json)
    paths = context.paths
    active = read_active_state(paths)
    published = read_active_index(paths)

    current = str(context.state_hash)
    built = str(active.state_hash) if active else None
    indexed = (published or {}).get("stateHash")

    needs_apply = built != current
    stale = published is None or indexed != current

    _emit(
        {
            "built": published is not None,
            "indexBuildId": (published or {}).get("indexBuildId"),
            "indexStateHash": indexed,
            "builtStateHash": built,
            "currentStateHash": current,
            "stale": stale,
            "knowledgeNotApplied": needs_apply,
            "remedy": _remedy(stale=stale, needs_apply=needs_apply),
        },
        as_json=as_json,
    )


def _remedy(*, stale: bool, needs_apply: bool) -> str:
    """The next command to run, in the order it has to be run in.

    Indexing before applying would build from a database that is itself behind,
    producing a fresh-looking index of stale knowledge.
    """
    if needs_apply:
        return "Run `theurian migrate apply`, then `theurian index build`."
    if stale:
        return "Run `theurian index build`."
    return ""


def read_active_index(paths: ProjectPaths) -> dict[str, Any] | None:
    """The published index pointer, or ``None``.

    A missing or unreadable pointer means "no index", never an error: the index
    is derived, so the remedy is always a rebuild rather than a repair.
    """
    pointer = paths.active_index_pointer
    if not pointer.is_file():
        return None
    try:
        loaded = json.loads(pointer.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _publish(paths: ProjectPaths, *, index_build_id: str, state_hash: str) -> None:
    """Point retrieval at a finished build, atomically.

    Write-to-temp then ``os.replace``, which is atomic on POSIX. A reader must
    never observe a half-written pointer, because that would send it to an index
    that does not exist (the same reasoning as ADR-0007).
    """
    pointer = paths.active_index_pointer
    pointer.parent.mkdir(parents=True, exist_ok=True)
    temporary = pointer.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"indexBuildId": index_build_id, "stateHash": state_hash}, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, pointer)  # noqa: PTH105 - os.replace is the atomic primitive
