"""Rebuilding the review-finding store from git history (ADR-0029 phase-2 slice-2).

A **standalone** rebuild service, ``f(source) -> store``: it reads every
``Review-Finding:`` trailer a :class:`ReviewFindingSource` resolves and lands them
wholesale in a :class:`ReviewFindingStore`. Shaped like
:class:`~theurian.application.index_builder.IndexBuilder` -- collaborators by
injection, testable without git or SQLite -- but a **sibling of it, never a hook
into its guts**: it touches no ``IndexBuilder`` internals and no ``INDEX_DDL``,
because findings are their own artifact rebuilt on their own path (WatchDog ruling
1). The two may be invoked by one top-level rebuild command as *distinct* steps;
they do not entangle.

**The findings store adds no authority beyond git history.** This builder is the
store's only writer, and it writes exactly the load the git source resolved -- so
there is no path here for a finding that did not come from a signed commit. A write
path that admitted off-git findings, or any serving read, is a future lane
(ADR-0029's serving/deriving arm), deliberately absent from this slice.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import final

from theurian.domain.ports.review_finding_source import ReviewFindingSource
from theurian.domain.ports.review_finding_store import ReviewFindingStore
from theurian.domain.review_finding import PARSER_STAMP


@dataclass(frozen=True, slots=True)
class FindingsBuildRequest:
    """Where to write the rebuilt store.

    One field, deliberately: findings are a repo-global read of git history, so
    unlike an index build there is no project id, state hash or build flavour to
    carry. The path is the only thing that varies.
    """

    store_path: Path


@final
class FindingsBuilder:
    """Rebuilds the review-finding store from a git source, wholesale.

    Takes its source and a store factory by injection (ADR-0003), so a build is
    exercised against a hermetic git repository and a real SQLite store without
    naming either concretely.
    """

    def __init__(
        self,
        *,
        source: ReviewFindingSource,
        store_factory: Callable[[Path], ReviewFindingStore],
    ) -> None:
        self._source = source
        self._store_factory = store_factory

    def build(self, request: FindingsBuildRequest) -> dict[str, object]:
        """Read the source and land its findings in a fresh store at ``store_path``.

        Wholesale by construction: :meth:`ReviewFindingStore.replace_all` rebuilds
        the file from empty, so a rebuild over unchanged history is idempotent
        (AC-2) and a rebuild after history grows converges to the new full set with
        nothing lost or duplicated (AC-3). The load is exactly what the git source
        resolved, so a deleted store rebuilds identically and holds nothing git does
        not (AC-6).
        """
        load = self._source.load_findings()
        store = self._store_factory(request.store_path)
        store.replace_all(load)
        return {
            "storePath": str(request.store_path),
            "findings": len(load.accepted),
            "rejected": len(load.rejected),
            "parserStamp": PARSER_STAMP,
        }


__all__ = ["FindingsBuildRequest", "FindingsBuilder"]
