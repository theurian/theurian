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
from theurian.application.project_service import (
    FINDINGS_STORE_ID,
    BuildProvenance,
    ProjectPathEscapeError,
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
#: ``WriteLock.held`` runs ``mkdir`` + ``os.open`` on ``.theurian/runtime/``,
#: which raise a bare ``OSError`` -- not a ``TheurianError`` -- so on a first build
#: whose state area is unwritable the raw traceback escaped the command's handler.
#: **Nothing in the acquisition reaches this constant any more.** Both calls
#: ``WriteLock.held`` makes before it has a descriptor now convert their own
#: ``OSError`` into ``WriteLockUnusableError`` -- the ``open`` since #520, the
#: ``mkdir`` beside it -- each carrying a cure that names the lock file or the
#: directory holding it, so both reach the command's ``except TheurianError``
#: below rather than this text. It is kept as the backstop for the release
#: clauses ``_lock_write_section`` also spans, and for a future acquisition step
#: that forgets to convert; a remedy nothing can currently produce is recorded
#: here rather than deleted, because deleting it is what makes the next bare
#: ``OSError`` a traceback again.
#: Names the precondition to fix first (a writable ``.theurian``), with the retry
#: as the trailing clause, the same shape ``FindingsStoreError``'s write remedy
#: takes.
_LOCK_ACQUIRE_REMEDY: Final = (
    "Check that .theurian/ is writable and there is free disk space, then retry "
    "`theurian findings build`."
)

#: The remedy for an OS refusal *recording* the build in this installation's
#: provenance file. That file lives in ``THEURIAN_DATA_DIR`` -- outside the
#: repository, which is the whole point of it (ADR-0004, SEC-7) -- so the
#: precondition to fix is a different directory than every other failure this
#: command reports, and naming ``.theurian/`` here would send a reader to the
#: wrong one.
_PROVENANCE_REMEDY: Final = (
    "Check that the Theurian data directory (THEURIAN_DATA_DIR, or ~/.theurian) is "
    "writable and there is free disk space, then retry `theurian findings build`."
)


def _lock_write_section(lock_path: Path) -> WriteSection:
    """A write-section factory whose lock-acquisition ``OSError`` arrives graded.

    ``WriteLock(lock_path).held`` is the real section, and its ``__enter__``
    ``mkdir`` raised a bare ``OSError`` the command's ``except TheurianError``
    could not see (#404 R1-2). The ``except OSError`` below spans the whole
    ``with`` -- acquisition, body **and** release -- and converts any bare
    ``OSError`` from it into a :class:`FindingsStoreError` a ``TheurianError``
    handler catches.

    **The acquisition no longer produces one**, so this handler now guards the
    other two phases and a future third acquisition step. Each phase is quiet for
    its own reason:

    - **Acquisition.** Both calls ``held`` makes before it has a descriptor
      convert their own ``OSError`` into ``WriteLockUnusableError`` -- the
      ``open`` since #520, the ``mkdir`` in ``_prepare_the_directory`` beside it.
      Each is a ``TheurianError`` naming the lock file or the directory holding
      it, so it passes this handler untouched and is graded by the command's own
      ``except TheurianError`` with a better cure than
      :data:`_LOCK_ACQUIRE_REMEDY` could give it.

    - **Body.** The one thing run inside is ``replace_all``, which converts its own
      ``(sqlite3.Error, OSError)`` before any escapes, and a
      ``WriteLockTimeoutError`` is a ``TheurianError``, not an ``OSError``, so it
      passes straight through with the lock-specific remedy #404 R1-5 gave it.
    - **Release.** ``held``'s ``finally`` clauses (``flock(LOCK_UN)`` then
      ``os.close(fileno)``) run *after* ``replace_all``'s ``os.replace`` has
      already published the artifact. A bare ``OSError`` there would be
      mislabelled "acquiring the write lock" with a make-writable remedy --
      accepted, not overlooked: closing a lock file nothing was written to on a
      still-held descriptor does not raise on a local filesystem, and the rebuild
      is durable by then, so retrying re-derives the identical store and the
      remedy is harmless. The broad scope is kept -- narrowing it to ``__enter__`` alone
      means driving the context manager by hand, more machinery than an
      unreachable, already-durable residue earns -- and the residue is recorded
      here instead.
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
    from theurian.cli.commands import (  # noqa: PLC0415 - cycle
        _emit,
        _fail,
        _fail_a_path_escape,
        _require_project,
    )

    context, _ = _require_project(as_json)
    paths = context.paths
    try:
        # The whole composition is inside the try, not only the build. Both
        # `findings_for` and `write_lock` resolve through
        # `ProjectPaths._contained`, which can itself raise a `ProjectError` (a
        # `TheurianError`) -- an earlier cut left `findings_for` outside, so that
        # escape bypassed this handler just like the write-path escape below, and
        # a `write_lock` composed outside would have re-opened it at a new path.
        # `FINDINGS_STORE_ID`, not a constant spelled here: `review.findings`
        # reads the store this command writes, and the two surfaces have to name
        # one file. A second spelling would leave the reader opening a path
        # nothing writes -- reported as a missing store for a project that has
        # one, which is a silent wrong answer rather than a loud failure.
        request = FindingsBuildRequest(store_path=paths.findings_for(FINDINGS_STORE_ID))
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
        # Record that *this installation* built this store, out of the repository
        # tree, the instant the rebuild returns (ADR-0004, SEC-7). `review.findings`
        # stands aside a store it does not find here, so this call is what makes the
        # store just built servable -- and what keeps one that arrived with a clone,
        # force-added past ADR-0004's ignore, unservable however well-formed it is.
        #
        # Inside the `try` and graded below rather than left to escape: a store on
        # disk that this file does not vouch for is a store the serving surface
        # refuses, so a failure here is a failed build reported with the precondition
        # to fix -- not a success whose artifact nothing will serve.
        BuildProvenance.default().record_findings(paths.root, FINDINGS_STORE_ID)
    except ProjectPathEscapeError as exc:
        # Both `findings_for` and `write_lock` are composed inside this `try` and
        # both route through the containment chokepoint, so a doctored
        # `.theurian/state/` or `.theurian/runtime/` refuses here. Measured at
        # exit 1 until this arm, against 4 for the same tree under any swept
        # command; this command is outside `CLI_SWEEP` because it reads
        # `refs/remotes/origin/main`, which is why the sweep could not see it.
        _fail_a_path_escape(exc, as_json=as_json)
        return
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
    except OSError as exc:
        # Only the provenance write raises a bare `OSError` here: `replace_all`
        # converts its own, and the lock's are converted by `_lock_write_section`.
        _fail(
            f"The store was rebuilt, but this installation could not record that it "
            f"built it ({exc}), so `review.findings` will refuse to serve it.",
            remedy=_PROVENANCE_REMEDY,
            as_json=as_json,
            code=1,
        )
        return
    _emit({**report, "built": True}, as_json=as_json)
