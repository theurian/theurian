"""``theurian findings`` -- rebuild the review-finding store (ADR-0029 phase-2 slice-2).

A composition root: where the abstract :class:`FindingsBuilder` meets the concrete
git source and the SQLite store (ADR-0003).

``findings build`` is a **write/maintenance** path, like ``index build`` and
``index gc``: it rebuilds a derived artifact -- a projection of the repo's public
git history -- and reports counts. It returns **no finding content**. There is no
serving here; a findings search is a later slice with its own disclosure round, so
this command is the write boundary and nothing on it hands a caller a finding.

**A premise this slice inherits, recorded here rather than left implicit:** on a
clone of the private fork used for embargoed disclosure work, ``origin/main`` *is*
that fork's own main, so an embargoed trailer already committed there lands in
this command's local artifact same as any other -- unchanged reach, since the same
bytes already sit readable in ``.git`` on that clone, but a premise the findings
*serving* slice must revisit before it decides what ``origin`` is trusted to mean.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Final

import typer

from theurian.application.findings_builder import (
    FindingsBuilder,
    FindingsBuildRequest,
    WriteSection,
)
from theurian.domain.errors import TheurianError
from theurian.infrastructure.git.trailer_source import GitTrailerFindingSource
from theurian.infrastructure.sqlite.connection import WriteLock
from theurian.infrastructure.sqlite.findings_store import (
    FindingsStoreError,
    SqliteReviewFindingStore,
)

findings_app = typer.Typer(help="Rebuild the review-finding store.", no_args_is_help=True)

JsonOption = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")]

#: The remedy for an OS refusal *acquiring* the write lock (#404 R1-2). Entering
#: ``WriteLock.held`` runs ``mkdir`` + ``open("w")`` on ``.theurian/runtime/``,
#: which raise a bare ``OSError`` -- not a ``TheurianError`` -- so on a first build
#: whose state area is unwritable the raw traceback escaped the command's handler.
#: Names the precondition to fix first (a writable ``.theurian``), with the retry
#: as the trailing clause, the same shape ``FindingsStoreError``'s write remedy
#: takes.
_LOCK_ACQUIRE_REMEDY: Final = (
    "Check that .theurian/ is writable and there is free disk space, then retry "
    "`theurian findings build`."
)


def _lock_write_section(lock_path: Path) -> WriteSection:
    """A write-section factory whose lock-acquisition ``OSError`` arrives graded.

    ``WriteLock(lock_path).held`` is the real section, but its ``__enter__``
    ``mkdir``/``open`` raise a bare ``OSError`` the command's ``except
    TheurianError`` cannot see (#404 R1-2). This converts *only* that -- an
    ``OSError`` escaping the acquisition -- into a :class:`FindingsStoreError` a
    ``TheurianError`` handler catches. It cannot mislabel a body fault: the one
    thing run inside is ``replace_all``, which converts its own ``(sqlite3.Error,
    OSError)`` before any escapes, and a ``WriteLockTimeoutError`` is a
    ``TheurianError``, not an ``OSError``, so it passes straight through with the
    lock-specific remedy #404 R1-5 gave it.
    """

    @contextmanager
    def section() -> Iterator[None]:
        try:
            with WriteLock(lock_path).held():
                yield
        except OSError as exc:
            raise FindingsStoreError(
                f"acquiring the write lock at {lock_path.name}: {exc}",
                remedy=_LOCK_ACQUIRE_REMEDY,
            ) from exc

    return section


#: One stable store per project. Findings are a wholesale projection of the repo's
#: public history, so a rebuild overwrites a single artifact rather than minting a
#: new build id per run. There is no findings pointer yet (no serving), so this id
#: is a trusted constant supplied here, not a value read from a mutable file --
#: which is why ``findings_for`` contains it through ``_contained`` rather than the
#: state-scoped check ``index_for`` owes an untrusted pointer.
_FINDINGS_STORE_ID: Final = "local"


@findings_app.command("build")
def findings_build(as_json: JsonOption = False) -> None:
    """Rebuild this project's review-finding store from public git history.

    Reads every ``Review-Finding:`` trailer on ``refs/remotes/origin/main`` and
    lands them wholesale, so the store is a pure function of git history and
    deleting it costs a rebuild, not data (ADR-0004). A fresh clone that has not
    fetched the public ref yet is refused with a fetch remedy rather than an empty
    store.

    **The rebuild is a write, so it takes the project's writer lock** (#404,
    ADR-0018). Two concurrent rebuilds serialise instead of assembling at one
    working name, and a rebuild racing ``migrate apply`` waits for it. A holder
    that keeps the lock past the timeout is a ``WriteLockTimeoutError`` -- a
    ``TheurianError`` that sets its own lock-specific remedy in its ``__init__``
    (#404 R1-5), so ``_fail`` below carries "wait for the other process", never the
    generic doctor cure and never a raw traceback.
    """
    from theurian.cli.commands import _emit, _fail, _require_project  # noqa: PLC0415 - cycle

    context, _ = _require_project(as_json)
    paths = context.paths
    try:
        # The whole composition is inside the try, not only the build. Both
        # `findings_for` and `write_lock` resolve through
        # `ProjectPaths._contained`, which can itself raise a `ProjectError` (a
        # `TheurianError`) -- an earlier cut left `findings_for` outside, so that
        # escape bypassed this handler just like the write-path escape below, and
        # a `write_lock` composed outside would have re-opened it at a new path.
        request = FindingsBuildRequest(store_path=paths.findings_for(_FINDINGS_STORE_ID))
        builder = FindingsBuilder(
            # The project root is the git working tree the trailers are read from.
            source=GitTrailerFindingSource(paths.root),
            store_factory=SqliteReviewFindingStore,
            # The project's one writer lock (ADR-0018), passed as the factory the
            # builder enters once around the store write. `_lock_write_section`
            # wraps `WriteLock(...).held` so an OSError from the lock's own
            # `mkdir`/`open` at acquisition arrives graded, not as a raw traceback
            # (#404 R1-2). Named here and only here: this is the composition root,
            # and `application/` may not reach for a concrete adapter (ADR-0003).
            write_section=_lock_write_section(paths.write_lock),
        )
        report = builder.build(request)
    except TheurianError as exc:
        # Each failure carries its own remedy: a path that cannot be contained
        # names the escape, unreachable git history (a fresh clone) names the
        # fetch, a lock timeout names the other writer, and a store write failure
        # names the precondition to fix FIRST -- writable state, free disk space --
        # with a retry of this same command only as the trailing clause, never
        # offered as the cure on its own (see `FindingsStoreError`'s write/read
        # remedy split).
        _fail(str(exc), remedy=exc.remedy or "Run `theurian doctor`.", as_json=as_json, code=1)
        return
    _emit({**report, "built": True}, as_json=as_json)
