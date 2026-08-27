"""A request in flight survives `theurian index gc` unlinking its build (ADR-0024).

**The acceptance test for decision 7, and the reason decision 6 is safe.**
Publishing no longer reaps, so builds accumulate and `gc` reclaims them — which
means an unlink can now land at any moment, including partway through a search.
Two mechanisms have to hold together for that to be survivable, and neither alone
is enough:

- `gc` never reclaims the build the pointer names, so a request that resolves the
  pointer *after* the reap opens a file that is still there;
- a request that resolved it *before* the reap holds one read connection for its
  duration (`SqliteIndexStore.session`), so the unlink takes the name and leaves
  the inode, and the request finishes against the build it started on.

Measured before this was a test, one request of four index calls with the unlink
landing after the first: **1 of 4 answered** with a connection per call, leaving
an empty database recreated at the reaped path, against **4 of 4** inside a
session, recreating nothing. That measurement was a timing loop; here the unlink
is forced between two specific calls, so the result does not depend on how fast
anything runs.

**Both halves of the old failure are asserted, because fixing one hid the
other.** The reads failing is the visible half. The *file reappearing* is the one
that made it permanent: `sqlite3.connect` on a deleted path creates an empty
database there, so the "no index file, fall back to the substring scan" branch
stopped firing and every later request against that project failed identically,
with `no such table: chunks_fts` at the agent rather than a fallback reason.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from theurian.domain.chunking import Chunk, IndexableChunk
from theurian.domain.enums import Sensitivity
from theurian.infrastructure.sqlite.index_store import IndexUnreadableError, SqliteIndexStore

pytestmark = pytest.mark.integration

#: The disclosure grant every retriever call in this file runs under: all four
#: levels, which is what "this deployment serves everything" means once the
#: retrievers take the axis as a WHERE predicate (#119 phase 4). Spelled out
#: rather than read from ``StaticAuthorizationProvider``'s shipped default, which
#: a later phase narrows -- a file that inherited it would start withholding its
#: own fixtures silently, turning these tests into tests of something else.
EVERY_SENSITIVITY = frozenset(Sensitivity)


PROJECT = "demo"
QUERY = "retention"

#: Index reads one `RetrievalService.search` makes: two retrievers through the
#: depth loop, then `chunk_texts`. Four is that plus the `is_searchable` check the
#: MCP path runs first -- the point being that a request is *several* reads, and
#: the window between them is what `gc` can land in.
CALLS_PER_REQUEST = 4


def _indexable(chunk_id: str) -> IndexableChunk:
    return IndexableChunk(
        chunk=Chunk(
            chunk_id=chunk_id,
            ordinal=0,
            text="Retention and isolation are decided per namespace.",
            heading="",
        ),
        project_id=PROJECT,
        item_id="architecture.retention",
        revision_id=chunk_id.split("#", 1)[0],
        served_content_sha256=f"body-of-{chunk_id.split('#', 1)[0]}",
        status="approved",
        sensitivity="internal",
        trust_level="reviewed",
    )


@pytest.fixture
def build(tmp_path: Path) -> Path:
    path = tmp_path / "theurian-index-01K1AAAAAA.sqlite"
    store = SqliteIndexStore(path)
    store.create(index_build_id="01K1AAAAAA", state_hash="s")
    store.add_chunks([_indexable(f"r{n}#0") for n in range(8)])
    return path


def _read(store: SqliteIndexStore) -> int:
    return len(
        store.search_lexical(
            QUERY,
            project_id=PROJECT,
            limit=50,
            include_unapproved=False,
            visible_sensitivities=EVERY_SENSITIVITY,
        ).rows
    )


def _request(store: SqliteIndexStore, build: Path) -> int:
    """One request of `CALLS_PER_REQUEST` reads, with `gc` reaping after the first."""
    answered = 0
    for call in range(CALLS_PER_REQUEST):
        try:
            _read(store)
            answered += 1
        except (IndexUnreadableError, sqlite3.Error):
            pass
        if call == 0:
            build.unlink(missing_ok=True)
    return answered


def test_a_request_inside_a_session_finishes_against_the_build_it_started_on(
    build: Path,
) -> None:
    """Decision 7, stated as the thing a caller experiences.

    The unlink lands after the first of four reads. Inside a session the
    remaining three still answer, because the connection was opened before the
    name went and an open descriptor keeps the inode readable.
    """
    store = SqliteIndexStore(build)

    with store.session():
        answered = _request(store, build)

    assert answered == CALLS_PER_REQUEST, (
        f"a request already in flight must finish against the build it started on; "
        f"{answered} of {CALLS_PER_REQUEST} reads answered after `gc` unlinked it"
    )
    assert not build.exists(), (
        "the reaped build must stay reaped: a read that recreated the file would leave an "
        "empty database where every later request looks for a real one"
    )


def test_without_a_session_the_same_request_loses_its_remaining_reads(build: Path) -> None:
    """The control, and the measurement this test was built from.

    Without it the test above is satisfied by an index nothing ever reaps. This
    is the shipped behaviour before decision 7: a connection per call, so the
    first read after the unlink opens a path that is no longer there.

    It is not marked `xfail` and it is not a regression guard -- it pins the cost
    of *not* holding the connection, so that anyone who removes `session()` as an
    optimisation sees what it was for.
    """
    store = SqliteIndexStore(build)

    answered = _request(store, build)

    assert answered == 1, (
        f"a connection per call must lose every read after the unlink; {answered} answered, "
        f"which means either the session is being opened somewhere it should not be or the "
        f"unlink is not landing where this test puts it"
    )


def test_a_read_of_a_reaped_build_never_recreates_it(build: Path) -> None:
    """`mode=ro`, and the half of the old defect that made it permanent.

    `sqlite3.connect` on a missing path creates an empty database there. That is
    what turned a reaped build from "fall back to the substring scan" into "every
    request against this project fails with `no such table: chunks_fts`", because
    after the first attempt there *was* a file, and the missing-file branch never
    ran again.
    """
    build.unlink()
    store = SqliteIndexStore(build)

    with pytest.raises(IndexUnreadableError):
        _read(store)

    assert not build.exists(), "the read conjured the database it could not find"
    assert store.schema_version() == 0, "an unreadable build reports 0 rather than raising"
    assert store.is_searchable() is False


def test_a_request_that_starts_after_the_reap_reads_the_published_build(
    build: Path, tmp_path: Path
) -> None:
    """The other half of why decision 6 is safe, and the one no session provides.

    `gc` never reclaims the build the pointer names, so a request arriving after
    a reap resolves the pointer to a file that is still there. Without that,
    holding a connection would only postpone the failure to the next request.
    """
    published = tmp_path / "theurian-index-01K1BBBBBB.sqlite"
    store = SqliteIndexStore(published)
    store.create(index_build_id="01K1BBBBBB", state_hash="s")
    store.add_chunks([_indexable(f"r{n}#0") for n in range(8)])

    build.unlink()  # `gc` reclaims the superseded build

    with SqliteIndexStore(published).session() as fresh:
        assert _read(fresh) > 0
