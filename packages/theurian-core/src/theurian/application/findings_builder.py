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

**This builder adds no authority beyond git history -- and history here means
reachability from ``refs/remotes/origin/main``, not a verified signature.** It is
the store's only writer, and in normal operation it writes exactly the load
:class:`~theurian.domain.ports.review_finding_source.ReviewFindingSource` resolved
from that ref. But "signed commit" overstates what is checked: nothing in this
builder, its source, or the port it writes through verifies a commit's GPG
signature -- measured, an *unsigned* commit's trailer is accepted the same as a
signed one (``git verify-commit`` returns 1 and ``%G?`` reports ``N``) -- and a
non-git-resolved :class:`~theurian.domain.review_finding.FindingLoad` handed to
:meth:`build` lands through the same path if one is constructed and passed in.
Signing is enforced by branch protection on the public ``origin``, not by this
code. A write path that admitted findings unreachable from that ref, or any
serving read, is a future lane (ADR-0029's serving/deriving arm), deliberately
absent from this slice.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import final

from theurian.domain.ports.review_finding_source import ReviewFindingSource
from theurian.domain.ports.review_finding_store import ReviewFindingStore
from theurian.domain.review_finding import PARSER_STAMP

#: A factory for the critical section :meth:`FindingsBuilder.build` publishes
#: inside. A *factory*, not a context manager: an advisory lock's hold is
#: single-use, so a builder reused across two builds must be able to take it
#: twice. ``WriteLock(path).held`` in the CLI composition root is exactly one of
#: these, named there and nowhere in this layer (ADR-0003).
WriteSection = Callable[[], AbstractContextManager[None]]


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
        write_section: WriteSection = nullcontext,
    ) -> None:
        self._source = source
        self._store_factory = store_factory
        self._write_section = write_section

    def build(self, request: FindingsBuildRequest) -> dict[str, object]:
        """Read the source and land its findings in a fresh store at ``store_path``.

        Wholesale by construction: :meth:`ReviewFindingStore.replace_all` rebuilds
        the file from empty, so a rebuild over unchanged history is idempotent
        (AC-2) and a rebuild after history grows converges to the new full set with
        nothing lost or duplicated (AC-3). The load is exactly what the git source
        resolved, so a deleted store rebuilds identically and holds nothing git does
        not (AC-6).

        **One continuous hold covers the whole critical section, and the git read
        sits outside it** (#404, and #468's recorded lesson). The section this
        serialises is exactly the store write: two rebuilds assembling at one
        ``.building`` name would corrupt each other, and the ``os.replace`` that
        publishes is inside the same hold as the assembly that fed it -- never two
        sequential holds, which is the shape #468 measured leaving a window worse
        than the race it closed. ``load_findings`` runs before the hold because it
        reads *git*, touching nothing the lock protects, and it is a subprocess
        with a 30-second bound; holding a project's single writer lock across it
        would block ``migrate apply`` for the length of a git log, for no guarantee
        (the same reason ``migrate apply`` builds its ``Project`` outside its own
        hold).

        That leaves one ordering the lock deliberately does not fix: two rebuilds
        can read git at different instants, and the one that read *earlier* may
        publish *later*, so the surviving store can be a snapshot one commit
        behind. It is a whole, self-consistent, correctly stamped store either way
        -- which is what the lock is for -- and the ref it projects is moving, so
        no lock could make "the latest" mean anything here. The next rebuild
        converges.

        ``write_section`` defaults to :func:`contextlib.nullcontext`, so a test
        driving a builder against a private temporary path gets the same behaviour
        without inventing a lock file. The shipped composition root always passes
        the project's real one.
        """
        load = self._source.load_findings()
        store = self._store_factory(request.store_path)
        with self._write_section():
            store.replace_all(load)
        return {
            "storePath": str(request.store_path),
            "findings": len(load.accepted),
            "rejected": len(load.rejected),
            "parserStamp": PARSER_STAMP,
        }


__all__ = ["FindingsBuildRequest", "FindingsBuilder", "WriteSection"]
