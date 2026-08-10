"""Purging a still-published index the moment a withdrawal lands (ADR-0024 decision 5).

The gate takes a withdrawn row out of a *result*; it cannot take it out of the
BM25 collection statistics the surviving rows are scored against, so while a
published build still holds the row the visible ranking moves with content no
caller may read (threat model T-17a). Closing that window is a *removal from the
published build*, and the only quantity under this module's control is how long
the stale build stays published: the swap protects the next request, never one
already served (ADR-0024 decision 5).

**A purge is a build, and it goes through the same pointer swap `index build`
does** -- a new file, then an atomic rename of ``active-index.json``. Nothing
writes to the file the pointer already names (ADR-0024 point 1), so a search
in flight keeps reading the build it started on.

**Run after the write transaction commits, never inside it.** `purge_into` is a
whole-file backup, delete and verify; holding that across a write transaction
would block every other writer for its duration, which NFR-8 and ADR-0018 point 5
forbid. The purge reads the *published index*, not canonical state, so it needs
only the withdrawal committed -- not the lock that committed it.

**Everything the published build already declared is preserved, nothing is
overwritten.** The state hash, the ``projectId``, and the draft coverage all come
off the pointer being purged, not off the caller. The state hash because a purge
removes rows and does not add the content a later migration in the same apply may
have introduced, so stamping the new canonical state would report a build still
missing that content as fresh -- the silent-staleness failure this codebase
refuses. The ``projectId`` because a build's chunks are stamped with the id that
wrote them; a purge that adopted the caller's id would flip the pointer's after a
rename and make ``knowledge.search`` answer ``count: 0, indexed: true`` for
content that is really there. The T-17a property is unaffected -- it is that the
*ranking* of the visible rows no longer counts the withdrawn ones, which the FTS5
delete establishes regardless of the metadata (ADR-0024's measurement compares
chunk ids and scores).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from theurian.application.project_service import (
    ProjectPaths,
    read_active_index_pointer,
    write_active_index_pointer,
)
from theurian.domain.ports.determinism import IdGenerator


class PurgeableIndex(Protocol):
    """What this use case asks of a published build.

    Narrower than :class:`~theurian.domain.ports.index_store.IndexStore` on
    purpose: the use case decides whether the build can be read at all
    (``is_searchable``), whether it holds anything worth purging
    (``holds_any_revision``), and, if so, derives a purged copy
    (``derive_purged``). A narrow protocol is what keeps the concrete SQLite
    adapter named only at the composition root (ADR-0003), never here.
    """

    def is_searchable(self) -> bool: ...

    def holds_any_revision(self, revision_ids: Sequence[str]) -> bool: ...

    def derive_purged(
        self,
        target: Path,
        *,
        revision_ids: Sequence[str],
        index_build_id: str,
        state_hash: str,
    ) -> int: ...


#: A purge did not run, and the reason, in the vocabulary a command reports.
#:
#: These are states, not failures: a project with no index, or a withdrawal whose
#: revisions no published build holds, has nothing to purge and is correct.
NO_WITHDRAWAL: str = "no-withdrawal"
NO_PUBLISHED_INDEX: str = "no-published-index"
INDEX_UNUSABLE: str = "index-unusable"
NOTHING_TO_PURGE: str = "nothing-to-purge"

#: What an operator does when the purge itself failed. The index is derived
#: (ADR-0004), so the cure is always a rebuild -- and it is the load-bearing half
#: of the failure report, because the current build still holds the withdrawn
#: rows until it runs.
PURGE_FAILED_REMEDY: str = (
    "Nothing was published, so retrieval still uses the current index -- which "
    "still holds the withdrawn rows. Run `theurian index build` to produce a "
    "clean build; the index is derived, so nothing authored is lost."
)


@dataclass(frozen=True, slots=True)
class WithdrawalPurge:
    """What the withdrawal-triggered purge did, for the command to report.

    ``published`` is the security-relevant bit: a purge that was *needed* -- a
    withdrawal against a readable published build that holds the rows -- and did
    not publish means the stale build is still serving the withdrawn rows'
    statistics, and the operator has to know to rebuild. ``failed`` distinguishes
    "there was nothing to do" from "there was, and it did not complete", and
    carries ``remedy`` because only the second needs one.
    """

    published: bool
    index_build_id: str | None = None
    removed: int = 0
    #: Empty when a purge published; otherwise why it did not, which is either a
    #: benign state above or ``failed`` with a purge that raised.
    reason: str = ""
    failed: bool = False
    #: The command to run when ``failed``; empty otherwise.
    remedy: str = ""

    def __post_init__(self) -> None:
        if self.published and (self.reason or self.failed):
            msg = "a published purge carries no reason and did not fail"
            raise ValueError(msg)
        if self.published and self.index_build_id is None:
            msg = "a published purge names the build it published"
            raise ValueError(msg)
        if self.remedy and not self.failed:
            msg = "only a failed purge carries a remedy"
            raise ValueError(msg)


def publish_purge_for_withdrawal(  # noqa: PLR0911 - one early return per benign no-op state
    paths: ProjectPaths,
    *,
    withdrawn_revision_ids: Sequence[str],
    ids: IdGenerator,
    index_factory: Callable[[Path], PurgeableIndex],
) -> WithdrawalPurge:
    """Publish a copy of the current index with ``withdrawn_revision_ids`` removed.

    Does nothing, cheaply, when there is nothing to do: an empty withdrawal, no
    published build, or a published build that holds none of the withdrawn
    revisions. That last case is the common one -- ``migrate apply`` replays the
    whole set whenever the state hash shifts (ADR-0016), so a project with any
    past withdrawal would otherwise copy its whole index on every apply -- and it
    is caught by ``holds_any_revision`` before any file is copied, so a no-op
    apply pays a bounded read rather than a whole-file copy.

    All-or-nothing. `purge_into` unlinks its partial output on any failure, and
    this function publishes the pointer only after ``derive_purged`` returns a
    non-zero count, so a purge that raises -- or that would republish an identical
    build -- leaves the previously published build serving. A failure is reported
    through ``failed`` (with a remedy) so the operator can rebuild rather than
    discovering the still-withheld rows in a leak.
    """
    deduped = tuple(dict.fromkeys(withdrawn_revision_ids))
    if not deduped:
        return WithdrawalPurge(published=False, reason=NO_WITHDRAWAL)

    published = read_active_index_pointer(paths).payload
    if published is None:
        # No build, or a pointer that names none: nothing holds the withdrawn
        # rows. A corrupt pointer takes this branch too -- its remedy is a
        # rebuild, which produces a clean build without the withdrawn rows anyway.
        return WithdrawalPurge(published=False, reason=NO_PUBLISHED_INDEX)

    build_id = str(published.get("indexBuildId", ""))
    # Everything below is read off the pointer being purged, never the caller --
    # see the module docstring on why the state hash and the project id are
    # preserved rather than restamped.
    source_state_hash = str(published.get("stateHash", ""))
    source_project_id = str(published.get("projectId", ""))
    indexes_unapproved = bool(published.get("indexesUnapproved", False))

    try:
        source = paths.index_for(build_id)
        if not source.is_file():
            # The pointer outlived its file. Nothing published holds the rows, so
            # this is a benign state, not a failure to purge.
            return WithdrawalPurge(published=False, reason=INDEX_UNUSABLE)
        current = index_factory(source)
        if not current.is_searchable():
            # An unreadable file or a schema this build does not understand is one
            # retrieval already falls back past (mcp.search), so it never scores
            # the withdrawn rows and carries no T-17a channel. Purging it would
            # only fail on the missing tables; a rebuild is the standing remedy.
            return WithdrawalPurge(published=False, reason=INDEX_UNUSABLE)
        if not current.holds_any_revision(deduped):
            # The published build holds none of the withdrawn revisions: already
            # purged, or built after the withdrawal. Copying it to delete nothing
            # and republishing an identical build is pure churn. For the shipped
            # product this is exactly `derive_purged` would return 0, because the
            # only chunks are current-revision chunks; when derived content lands
            # (ADR-0024 decision 8) this pre-check must widen with the transitive
            # purge, and the `removed == 0` guard below stays as the backstop.
            return WithdrawalPurge(published=False, reason=NOTHING_TO_PURGE)

        # No new index-write lock is taken. Safety against a concurrent producer
        # rests on the same two mechanisms `index build` uses (ADR-0022, #113): a
        # fresh ULID sorts above the published one so `theurian index gc` never
        # reaps this build before it publishes, and `purge_into` writes under a
        # `.building` name and `os.replace`s into position, so a file under the
        # final name is complete by construction. The single index-writer
        # interface ADR-0018 point 1 still owes the index is entangled with this
        # purge -- both are "productions of a new build" -- and is tracked in
        # issue #15's follow-through rather than opened here.
        new_id = ids.new_ulid().value
        target = paths.index_for(new_id)
        removed = current.derive_purged(
            target=target,
            revision_ids=deduped,
            index_build_id=new_id,
            state_hash=source_state_hash,
        )
        if removed == 0:
            # A race, or bookkeeping the pre-check could not see: nothing left the
            # copy, so publishing it is churn. Drop the orphan rather than swap
            # the pointer to a build identical to the one it names.
            _discard(target)
            return WithdrawalPurge(published=False, reason=NOTHING_TO_PURGE)

        write_active_index_pointer(
            paths,
            index_build_id=new_id,
            state_hash=source_state_hash,
            project_id=source_project_id,
            indexes_unapproved=indexes_unapproved,
        )
    except Exception as exc:  # fail closed: any adapter's failure leaves the old build serving
        # The withdrawal is already committed to canonical state; only the index
        # follow-up failed. Report it rather than raising, so the command that
        # applied the migration does not report the apply itself as failed -- and
        # so the still-published stale build is named as a thing to rebuild, not
        # left silent (ADR-0024 decision 5). The type name, not the message: an
        # `IndexPurgeError` and a `sqlite3.OperationalError` name different
        # repairs, but the message carries the operator's absolute paths, which
        # `index_purge` is careful to keep out of a reply and this must not put
        # back (the remedy is the actionable half).
        return WithdrawalPurge(
            published=False,
            reason=f"purge-failed: {type(exc).__name__}",
            failed=True,
            remedy=PURGE_FAILED_REMEDY,
        )

    return WithdrawalPurge(published=True, index_build_id=new_id, removed=removed)


def _discard(build: Path) -> None:
    """Unlink a build file that will not be published, and its WAL sidecars.

    A purge that removed nothing wrote a complete copy under the final name (via
    `os.replace`); leaving it would strand a file `theurian index gc` does not
    reap, because its id sorts above the published one.
    """
    build.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(build) + suffix).unlink(missing_ok=True)


__all__ = [
    "INDEX_UNUSABLE",
    "NOTHING_TO_PURGE",
    "NO_PUBLISHED_INDEX",
    "NO_WITHDRAWAL",
    "PURGE_FAILED_REMEDY",
    "PurgeableIndex",
    "WithdrawalPurge",
    "publish_purge_for_withdrawal",
]
