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

**Why the copied build's state hash is preserved rather than advanced to the
post-withdrawal state.** A purge removes rows; it does not add the content a
later migration in the same apply may have introduced, so a purged build equals a
fresh build of the new state only when the apply was a pure withdrawal. Stamping
the new canonical state would make ``theurian index status`` report a build that
still lacks freshly-added content as fresh -- the silent-staleness failure this
codebase refuses everywhere else. So the purged build keeps the state hash of the
build it was derived from, and ``stale`` stays true until a real rebuild: honest,
because a pruned older build is not a fresh build of the current state. The T-17a
property is unaffected -- it is that the *ranking* of the visible rows no longer
counts the withdrawn ones, which the FTS5 delete establishes regardless of the
stamp (ADR-0024's measurement compares chunk ids and scores, not build metadata).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from theurian.application.project_service import (
    ProjectPaths,
    read_active_index_pointer,
    write_active_index_pointer,
)
from theurian.domain.errors import TheurianError
from theurian.domain.ports.determinism import IdGenerator


class PurgeableIndex(Protocol):
    """The two things this use case asks of a published build.

    Narrower than :class:`~theurian.domain.ports.index_store.IndexStore` on
    purpose: the use case decides whether the build can be read at all
    (``is_searchable``) and, if so, derives a purged copy (``derive_purged``).
    A narrow protocol is what lets the concrete adapter stay named only at the
    composition root (ADR-0003) while this module names neither SQLite nor a file.
    """

    def is_searchable(self) -> bool: ...

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
#: revisions no published build ever held, has nothing to purge and is correct.
NO_WITHDRAWAL: str = "no-withdrawal"
NO_PUBLISHED_INDEX: str = "no-published-index"
INDEX_UNUSABLE: str = "index-unusable"


@dataclass(frozen=True, slots=True)
class WithdrawalPurge:
    """What the withdrawal-triggered purge did, for the command to report.

    ``published`` is the security-relevant bit: a purge that was *needed* -- a
    withdrawal against a readable published build -- and did not publish means the
    stale build is still serving the withdrawn rows' statistics, and the operator
    has to know to rebuild. ``failed`` distinguishes "there was nothing to do"
    from "there was, and it did not complete".
    """

    published: bool
    index_build_id: str | None = None
    removed: int = 0
    #: Empty when a purge published; otherwise why it did not, which is either a
    #: benign state above or ``failed`` with a purge that raised.
    reason: str = ""
    failed: bool = False

    def __post_init__(self) -> None:
        if self.published and (self.reason or self.failed):
            msg = "a published purge carries no reason and did not fail"
            raise ValueError(msg)
        if self.published and self.index_build_id is None:
            msg = "a published purge names the build it published"
            raise ValueError(msg)


def publish_purge_for_withdrawal(
    paths: ProjectPaths,
    *,
    project_id: str,
    withdrawn_revision_ids: Sequence[str],
    ids: IdGenerator,
    index_factory: Callable[[Path], PurgeableIndex],
) -> WithdrawalPurge:
    """Publish a copy of the current index with ``withdrawn_revision_ids`` removed.

    Idempotent in the sense that matters: a withdrawal whose revisions no
    published build holds still runs the copy, and the FTS5 delete simply removes
    nothing -- so a re-run publishes an identical build rather than erroring
    (ADR-0024). The caller is expected to skip the empty-withdrawal case; this
    function also refuses it, so no other caller has to remember to.

    All-or-nothing. `purge_into` unlinks its partial output on any failure, and
    this function publishes the pointer only after ``derive_purged`` returns, so a
    purge that raises leaves the previously published build serving -- with the
    withdrawn rows still in its statistics, reported through ``failed`` so the
    operator can rebuild rather than discovering it in a leak.
    """
    deduped = tuple(dict.fromkeys(withdrawn_revision_ids))
    if not deduped:
        return WithdrawalPurge(published=False, reason=NO_WITHDRAWAL)

    published = read_active_index_pointer(paths).payload
    if published is None:
        # No build, or a pointer that names none: there is nothing holding the
        # withdrawn rows, so there is nothing to purge. A corrupt pointer takes
        # this branch too -- the remedy for it is a rebuild, which produces a
        # clean build without the withdrawn rows anyway.
        return WithdrawalPurge(published=False, reason=NO_PUBLISHED_INDEX)

    build_id = str(published.get("indexBuildId", ""))
    # The copied build keeps the state hash and draft coverage it already had --
    # see the module docstring on why the state hash is preserved rather than
    # advanced.
    source_state_hash = str(published.get("stateHash", ""))
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

        new_id = ids.new_ulid().value
        removed = current.derive_purged(
            target=paths.index_for(new_id),
            revision_ids=deduped,
            index_build_id=new_id,
            state_hash=source_state_hash,
        )
        write_active_index_pointer(
            paths,
            index_build_id=new_id,
            state_hash=source_state_hash,
            project_id=project_id,
            indexes_unapproved=indexes_unapproved,
        )
    except (TheurianError, sqlite3.Error, OSError) as exc:
        # The withdrawal is already committed to canonical state; only the index
        # follow-up failed. Report it rather than raising, so the command that
        # applied the migration does not report the apply itself as failed -- and
        # so the still-published stale build is named as a thing to rebuild, not
        # left silent (ADR-0024 decision 5).
        return WithdrawalPurge(published=False, reason=f"purge-failed: {exc}", failed=True)

    return WithdrawalPurge(published=True, index_build_id=new_id, removed=removed)


__all__ = [
    "INDEX_UNUSABLE",
    "NO_PUBLISHED_INDEX",
    "NO_WITHDRAWAL",
    "PurgeableIndex",
    "WithdrawalPurge",
    "publish_purge_for_withdrawal",
]
