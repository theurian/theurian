"""``theurian findings`` -- rebuild the review-finding store (ADR-0029 phase-2 slice-2).

A composition root: where the abstract :class:`FindingsBuilder` meets the concrete
git source and the SQLite store (ADR-0003).

``findings build`` is a **write/maintenance** path, like ``index build`` and
``index gc``: it rebuilds a derived artifact -- a projection of the repo's public
git history -- and reports counts. It returns **no finding content**. There is no
serving here; a findings search is a later slice with its own disclosure round, so
this command is the write boundary and nothing on it hands a caller a finding.
"""

from __future__ import annotations

from typing import Annotated, Final

import typer

from theurian.application.findings_builder import FindingsBuilder, FindingsBuildRequest
from theurian.domain.errors import TheurianError
from theurian.infrastructure.git.trailer_source import GitTrailerFindingSource
from theurian.infrastructure.sqlite.findings_store import SqliteReviewFindingStore

findings_app = typer.Typer(help="Rebuild the review-finding store.", no_args_is_help=True)

JsonOption = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")]

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
    """
    from theurian.cli.commands import _emit, _fail, _require_project  # noqa: PLC0415 - cycle

    context, _ = _require_project(as_json)
    paths = context.paths
    builder = FindingsBuilder(
        # The project root is the git working tree the trailers are read from.
        source=GitTrailerFindingSource(paths.root),
        store_factory=SqliteReviewFindingStore,
    )
    try:
        # `findings_for` is inside the try too: it resolves the store path through
        # `ProjectPaths._contained`, which can itself raise a `ProjectError` (a
        # `TheurianError`) -- an earlier cut left it outside, so that escape
        # bypassed this handler just like the write-path escape below.
        request = FindingsBuildRequest(store_path=paths.findings_for(_FINDINGS_STORE_ID))
        report = builder.build(request)
    except TheurianError as exc:
        # Each failure carries its own remedy: a path that cannot be contained
        # names the escape, unreachable git history (a fresh clone) names the
        # fetch, and a store write failure names what write failures are actually
        # caused by (see `FindingsStoreError`'s write/read remedy split) --
        # never a remedy that points back at this same command.
        _fail(str(exc), remedy=exc.remedy or "Run `theurian doctor`.", as_json=as_json, code=1)
        return
    _emit({**report, "built": True}, as_json=as_json)
