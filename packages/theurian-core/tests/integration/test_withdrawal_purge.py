"""The withdrawal-triggered purge use case (ADR-0024 decision 5, issue #15).

`test_index_purge.py` holds the property a *purge* has -- that a purged build
answers as if the withdrawn rows had never been indexed. This file holds the
orchestration around it: resolving the published pointer, refusing an index it
cannot read, publishing the purged build with an atomic swap, and -- the half a
review has to see -- leaving the previously published build serving when the
purge raises, because the withdrawal is already committed and the apply must not
report itself failed.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

import pytest

from theurian.application.project_service import (
    ProjectPaths,
    read_active_index_pointer,
    write_active_index_pointer,
)
from theurian.application.withdrawal_purge import (
    INDEX_UNUSABLE,
    NO_PUBLISHED_INDEX,
    NO_WITHDRAWAL,
    NOTHING_TO_PURGE,
    PurgeableIndex,
    WithdrawalPurge,
    publish_purge_for_withdrawal,
)
from theurian.domain.chunking import Chunk, IndexableChunk
from theurian.infrastructure.determinism import UlidGenerator
from theurian.infrastructure.sqlite.index_purge import IndexPurgeError
from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

pytestmark = pytest.mark.integration

PROJECT = "demo"

#: A withheld document ten times the corpus mean, so its removal moves `avgdl`
#: measurably -- the length-normalisation channel T-17a rests on (ADR-0024).
ORDINARY_BODY = (
    "Retention and isolation are decided per namespace. Authentication tokens "
    "rotate on restart. The quarantine ledger records every attempt. "
)
LONG_BODY = ORDINARY_BODY * 10
ORDINARY = 40
WITHDRAWN = 6
QUERIES = ("retention isolation", "authentication token", "quarantine ledger")

BUILD_ID = "01K1SRCAAA01234567890ABCDE"
STATE_HASH = "a" * 64


def _indexable(revision: str, text: str) -> IndexableChunk:
    return IndexableChunk(
        chunk=Chunk(chunk_id=f"{revision}#0", ordinal=0, text=text, heading=""),
        project_id=PROJECT,
        item_id=f"architecture.{revision}",
        revision_id=revision,
        status="approved",
        sensitivity="internal",
        trust_level="reviewed",
    )


def _corpus(*, include_withdrawn: bool) -> tuple[list[IndexableChunk], list[str]]:
    chunks = [
        _indexable(f"keep-{n:03d}", f"{ORDINARY_BODY} paragraph {n}.") for n in range(ORDINARY)
    ]
    withdrawn: list[str] = []
    if include_withdrawn:
        for n in range(WITHDRAWN):
            revision = f"gone-{n:03d}"
            withdrawn.append(revision)
            chunks.append(_indexable(revision, f"{LONG_BODY} paragraph {n}."))
    # Sorted by chunk id so both corpora insert the shared rows in the same order:
    # FTS5 keys on the implicit rowid, so a different order would make two indexes
    # differ for a reason that is not the purge.
    return sorted(chunks, key=lambda c: c.chunk.chunk_id), withdrawn


def _build(path: Path, *, include_withdrawn: bool, build_id: str = BUILD_ID) -> list[str]:
    store = SqliteIndexStore(path)
    store.create(index_build_id=build_id, state_hash=STATE_HASH)
    chunks, withdrawn = _corpus(include_withdrawn=include_withdrawn)
    store.add_chunks(chunks)
    return withdrawn


def _paths(tmp_path: Path) -> ProjectPaths:
    paths = ProjectPaths.of(tmp_path)
    paths.state.mkdir(parents=True, exist_ok=True)
    return paths


def _publish_source(paths: ProjectPaths, *, include_withdrawn: bool) -> list[str]:
    """Build a source index holding the corpus, and point the project at it."""
    withdrawn = _build(paths.index_for(BUILD_ID), include_withdrawn=include_withdrawn)
    write_active_index_pointer(
        paths,
        index_build_id=BUILD_ID,
        state_hash=STATE_HASH,
        project_id=PROJECT,
        indexes_unapproved=False,
    )
    return withdrawn


def _ranking(store: SqliteIndexStore, query: str) -> list[tuple[str, float]]:
    return [
        (row.chunk_id, round(row.score, 10))
        for row in store.search_lexical(
            query, project_id=PROJECT, limit=100_000, include_unapproved=False
        ).rows
    ]


def _published_store(paths: ProjectPaths) -> SqliteIndexStore:
    payload = read_active_index_pointer(paths).payload
    assert payload is not None
    return SqliteIndexStore(paths.index_for(str(payload["indexBuildId"])))


def test_a_purge_publishes_a_build_that_answers_as_if_the_rows_were_never_indexed(
    tmp_path: Path,
) -> None:
    """The T-17a property, reached through the use case rather than `purge_into`.

    A stale index that still holds the withdrawn rows reorders the visible ones
    against a fresh index that never did -- that is the leak. After the purge the
    published build must rank identically to the fresh one, chunk ids and scores.
    The `stale` control (the pre-purge source) is asserted *different*, or the
    equality is satisfied by a purge that deleted nothing.
    """
    paths = _paths(tmp_path)
    withdrawn = _publish_source(paths, include_withdrawn=True)
    fresh = SqliteIndexStore(tmp_path / "fresh.sqlite")
    fresh.create(index_build_id="01K1FRSHAA01234567890ABCDE", state_hash=STATE_HASH)
    fresh.add_chunks(_corpus(include_withdrawn=False)[0])
    stale_source = SqliteIndexStore(paths.index_for(BUILD_ID))

    outcome = publish_purge_for_withdrawal(
        paths,
        withdrawn_revision_ids=withdrawn,
        ids=UlidGenerator(),
        index_factory=SqliteIndexStore,
    )

    assert outcome.published is True
    assert outcome.removed == WITHDRAWN
    purged = _published_store(paths)
    for query in QUERIES:
        assert _ranking(purged, query) == _ranking(fresh, query), (
            f"the purged build must answer {query!r} as the fresh one does"
        )
        assert _ranking(stale_source, query) != _ranking(fresh, query), (
            f"or the {query!r} control never moved and the equality proves nothing"
        )


def test_the_pointer_swaps_to_the_new_build_and_the_old_file_stays(tmp_path: Path) -> None:
    """A purge is a build: a new file, then an atomic pointer swap (ADR-0024).

    The old build's file is not deleted here -- publishing never reclaims
    (ADR-0024 point 6) -- so a request already reading it finishes. The pointer,
    though, must now name the new build, and it must preserve the source's state
    hash rather than advance it.
    """
    paths = _paths(tmp_path)
    withdrawn = _publish_source(paths, include_withdrawn=True)

    outcome = publish_purge_for_withdrawal(
        paths,
        withdrawn_revision_ids=withdrawn,
        ids=UlidGenerator(),
        index_factory=SqliteIndexStore,
    )

    payload = read_active_index_pointer(paths).payload
    assert payload is not None
    assert payload["indexBuildId"] == outcome.index_build_id != BUILD_ID
    assert paths.index_for(BUILD_ID).is_file(), "the superseded build is not reclaimed by a purge"
    assert payload["stateHash"] == STATE_HASH, (
        "a purge removes rows; it does not rebuild, so the state hash is preserved"
    )
    assert payload["projectId"] == PROJECT
    assert payload["indexesUnapproved"] is False


def test_an_empty_withdrawal_publishes_nothing(tmp_path: Path) -> None:
    """The caller is expected to skip this, and the use case refuses it anyway."""
    paths = _paths(tmp_path)
    _publish_source(paths, include_withdrawn=True)

    outcome = publish_purge_for_withdrawal(
        paths,
        withdrawn_revision_ids=[],
        ids=UlidGenerator(),
        index_factory=SqliteIndexStore,
    )

    assert outcome == WithdrawalPurge(published=False, reason=NO_WITHDRAWAL)
    assert read_active_index_pointer(paths).payload == {
        "indexBuildId": BUILD_ID,
        "stateHash": STATE_HASH,
        "projectId": PROJECT,
        "indexesUnapproved": False,
    }


def test_no_published_index_is_a_state_not_a_failure(tmp_path: Path) -> None:
    """A project that never built an index has nothing holding the withdrawn rows."""
    paths = _paths(tmp_path)

    outcome = publish_purge_for_withdrawal(
        paths,
        withdrawn_revision_ids=["gone-000"],
        ids=UlidGenerator(),
        index_factory=SqliteIndexStore,
    )

    assert outcome == WithdrawalPurge(published=False, reason=NO_PUBLISHED_INDEX)


def test_a_pointer_naming_a_missing_or_unreadable_build_is_unusable(tmp_path: Path) -> None:
    """A schema this build cannot read is one retrieval already falls back past.

    Such a file never scores the withdrawn rows, so it carries no T-17a channel;
    trying to purge it would only fail on its missing tables. The pointer is left
    exactly as it was, and the standing remedy -- a rebuild -- produces a clean
    build without the withdrawn rows.
    """
    paths = _paths(tmp_path)
    # A pointer naming a build whose file was never written.
    write_active_index_pointer(
        paths,
        index_build_id=BUILD_ID,
        state_hash=STATE_HASH,
        project_id=PROJECT,
        indexes_unapproved=False,
    )

    outcome = publish_purge_for_withdrawal(
        paths,
        withdrawn_revision_ids=["gone-000"],
        ids=UlidGenerator(),
        index_factory=SqliteIndexStore,
    )

    assert outcome == WithdrawalPurge(published=False, reason=INDEX_UNUSABLE)
    assert read_active_index_pointer(paths).payload is not None, "the pointer is left as it was"


def test_a_build_holding_none_of_the_withdrawn_revisions_is_left_alone(tmp_path: Path) -> None:
    """The common replay case, made cheap and made not to churn (HIGH-2).

    ``migrate apply`` replays the whole set on any state-hash shift, so a project
    with a past withdrawal asks this on every apply. If the published build holds
    none of the withdrawn revisions -- already purged, or built after the
    withdrawal -- there is nothing to do: no copy, no pointer swap. Otherwise a
    restored item is deleted on one apply and a rebuild brings it back on the
    next, forever, and every apply republishes an identical build.
    """
    paths = _paths(tmp_path)
    # A build that never held the withdrawn revisions at all.
    _publish_source(paths, include_withdrawn=False)
    pointer_before = read_active_index_pointer(paths).payload

    outcome = publish_purge_for_withdrawal(
        paths,
        withdrawn_revision_ids=["gone-000", "gone-001"],
        ids=UlidGenerator(),
        index_factory=SqliteIndexStore,
    )

    assert outcome == WithdrawalPurge(published=False, reason=NOTHING_TO_PURGE)
    assert read_active_index_pointer(paths).payload == pointer_before, "the pointer is untouched"
    assert len(list(paths.state.glob("theurian-index-*.sqlite"))) == 1, (
        "and no copy was left behind"
    )


def test_the_purge_preserves_the_published_project_id_not_the_callers(tmp_path: Path) -> None:
    """HIGH-3. A rename must not make the purge orphan the index.

    A build's chunks are stamped with the project id that wrote them, and the
    pointer records it. After a rename (`project unregister` then `register
    --project-id new`) the canonical store is addressed by the new id, but the
    published build is still the old one's. A purge that stamped the *new* id onto
    the pointer would make `knowledge.search` answer `count: 0, indexed: true` for
    content that is really there. So the purge carries the pointer's own project
    id forward, exactly as it does the state hash.
    """
    paths = _paths(tmp_path)
    withdrawn = _build(paths.index_for(BUILD_ID), include_withdrawn=True)
    write_active_index_pointer(
        paths,
        index_build_id=BUILD_ID,
        state_hash=STATE_HASH,
        project_id="the-original-id",
        indexes_unapproved=True,
    )

    outcome = publish_purge_for_withdrawal(
        paths,
        withdrawn_revision_ids=withdrawn,
        ids=UlidGenerator(),
        index_factory=SqliteIndexStore,
    )

    assert outcome.published is True
    payload = read_active_index_pointer(paths).payload
    assert payload is not None
    assert payload["projectId"] == "the-original-id", "the build's own id, not the caller's"
    assert payload["indexesUnapproved"] is True, "draft coverage is preserved across the purge too"


class _RaisingIndex:
    """A published build the use case can read but cannot purge."""

    def is_searchable(self) -> bool:
        return True

    def holds_any_revision(self, _revision_ids: Sequence[str]) -> bool:
        return True

    def derive_purged(self, *_args: object, **_kwargs: object) -> int:
        raise IndexPurgeError("the copy could not be read at /home/someone/secret/path.sqlite")


def test_a_purge_that_raises_leaves_the_old_build_serving(tmp_path: Path) -> None:
    """All-or-nothing (ADR-0024). The withdrawal is committed; only the follow-up failed.

    So the apply must not report itself failed -- the use case returns rather than
    raising -- and the still-published stale build is named through `failed`, with
    a remedy, so the operator rebuilds rather than discovering it in a leak. The
    reason carries the exception *type* only: the message would leak the
    operator's absolute paths, which `index_purge` deliberately keeps out.
    """
    paths = _paths(tmp_path)
    withdrawn = _publish_source(paths, include_withdrawn=True)

    def factory(_path: Path) -> PurgeableIndex:
        return _RaisingIndex()

    outcome = publish_purge_for_withdrawal(
        paths,
        withdrawn_revision_ids=withdrawn,
        ids=UlidGenerator(),
        index_factory=factory,
    )

    assert outcome.published is False
    assert outcome.failed is True
    assert outcome.reason == "purge-failed: IndexPurgeError"
    assert "path.sqlite" not in outcome.reason, "the exception message must not leak a path"
    assert outcome.remedy, "a failed purge names the rebuild"
    assert "index build" in outcome.remedy
    payload = read_active_index_pointer(paths).payload
    assert payload is not None
    assert payload["indexBuildId"] == BUILD_ID, "the old build must still be published"


def test_a_non_sqlite_adapter_failure_also_fails_closed(tmp_path: Path) -> None:
    """The all-or-nothing contract holds for any adapter, not only SQLite.

    The use case fails closed on *any* exception a `PurgeableIndex` raises, not a
    hard-coded `sqlite3.Error` tuple -- otherwise a future non-SQLite adapter's
    exception would escape and crash `migrate apply` with a traceback, breaking
    the contract for everything but SQLite.
    """
    paths = _paths(tmp_path)
    withdrawn = _publish_source(paths, include_withdrawn=True)

    class _NonSqliteFailure:
        def is_searchable(self) -> bool:
            return True

        def holds_any_revision(self, _revision_ids: Sequence[str]) -> bool:
            return True

        def derive_purged(self, *_args: object, **_kwargs: object) -> int:
            raise RuntimeError("a bespoke adapter blew up")

    outcome = publish_purge_for_withdrawal(
        paths,
        withdrawn_revision_ids=withdrawn,
        ids=UlidGenerator(),
        index_factory=lambda _path: _NonSqliteFailure(),
    )

    assert outcome.failed is True
    assert outcome.reason == "purge-failed: RuntimeError"
    assert read_active_index_pointer(paths).payload["indexBuildId"] == BUILD_ID  # type: ignore[index]


def test_a_schema_mismatched_build_is_unusable_not_purged(tmp_path: Path) -> None:
    """The is_searchable() branch, exercised by a real corrupt build.

    A build whose schema this process does not understand is one retrieval falls
    back past, so it never scores the withdrawn rows; the purge leaves it and its
    pointer alone rather than failing on its missing tables.
    """
    paths = _paths(tmp_path)
    withdrawn = _build(paths.index_for(BUILD_ID), include_withdrawn=True)
    write_active_index_pointer(
        paths,
        index_build_id=BUILD_ID,
        state_hash=STATE_HASH,
        project_id=PROJECT,
        indexes_unapproved=False,
    )
    # Corrupt the build's schema version so `is_searchable()` returns False.
    with closing(sqlite3.connect(paths.index_for(BUILD_ID))) as connection:
        connection.execute("UPDATE index_metadata SET index_schema_version = -1 WHERE id = 1")
        connection.commit()

    outcome = publish_purge_for_withdrawal(
        paths,
        withdrawn_revision_ids=withdrawn,
        ids=UlidGenerator(),
        index_factory=SqliteIndexStore,
    )

    assert outcome == WithdrawalPurge(published=False, reason=INDEX_UNUSABLE)
    assert read_active_index_pointer(paths).payload is not None, "the pointer is left as it was"
