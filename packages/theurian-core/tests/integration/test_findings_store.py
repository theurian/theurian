"""The SQLite review-finding store (ADR-0029 phase-2 slice-2).

Drives the store adapter directly, against hand-built ``FindingLoad``s whose
oracle is a value this file wrote -- never a re-derivation of the store's own
algorithm. Three acceptance criteria live here:

- **AC-2 idempotency**: two rebuilds over the same load leave a *logically*
  identical store -- the ordered content dump, never a SQLite file hash
  (refinement A: identical logical content legitimately drifts the raw bytes).
- **AC-4 version stamp**: a store whose recorded schema version or parser stamp no
  longer matches the current build is detected, and a rebuild restamps it.
- **AC-5 rejects distinct**: a rejected trailer lands in its own table, queryable
  apart, and never as an accepted finding -- and its ``raw_line`` is inert even
  when it looks exactly like a valid finding (refinement B).
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest

from theurian.domain.knowledge import SourceAnchor
from theurian.domain.ports.review_finding_store import (
    FindingsDump,
    FindingsStamp,
    ReviewFindingStore,
)
from theurian.domain.review_finding import (
    PARSER_STAMP,
    FindingLoad,
    FindingSeverity,
    RejectedTrailer,
    ReviewerToken,
    ReviewFinding,
)
from theurian.infrastructure.sqlite.findings_schema import FINDINGS_SCHEMA_VERSION
from theurian.infrastructure.sqlite.findings_store import SqliteReviewFindingStore

pytestmark = pytest.mark.integration


def _sha(seed: str) -> str:
    """A 40-hex commit sha built from one character, so tests read declaratively."""
    return seed * 40


def _finding(
    sha: str,
    *,
    reviewer: ReviewerToken = ReviewerToken.CODE_REVIEW,
    severity: FindingSeverity = FindingSeverity.HIGH,
    text: str = "a finding",
    when: str = "2026-08-27T12:00:00+00:00",
) -> ReviewFinding:
    return ReviewFinding(
        reviewer=reviewer,
        severity=severity,
        finding_text=text,
        anchor=SourceAnchor(provider="git", source_uri=sha, commit_sha=sha),
        pull_request=None,
        date=datetime.fromisoformat(when),
    )


def _store(tmp_path: Path) -> SqliteReviewFindingStore:
    # Under a `state/` that does not exist yet, so `replace_all` is proved to
    # create its parent -- the first `findings build` runs before `state/` exists.
    return SqliteReviewFindingStore(tmp_path / "state" / "theurian-findings-test.sqlite")


def test_the_sqlite_store_satisfies_the_review_finding_store_port(tmp_path: Path) -> None:
    """The concrete adapter satisfies the runtime-checkable port structurally."""
    assert isinstance(_store(tmp_path), ReviewFindingStore)


def test_a_missing_store_is_not_current_and_dumps_empty(tmp_path: Path) -> None:
    """A store whose file was never written reads as stale, and dumps nothing.

    ``None`` and "not current" mean the same thing operationally -- a rebuild is
    owed -- so a caller's staleness check is total over "no file yet".
    """
    store = _store(tmp_path)
    assert store.stamp() is None
    assert not store.is_current()
    assert store.dump() == FindingsDump(findings=(), rejected=())


def test_double_rebuild_is_logically_identical(tmp_path: Path) -> None:
    """AC-2: two rebuilds over one load leave a logically identical store.

    Compared by the ordered content dump, not a file hash: a SQLite file's raw
    bytes drift under identical logical content (WAL frames, freelist), so byte
    identity is the wrong oracle. The load carries two findings on one commit to
    exercise the ``(commit_sha, position)`` key that keeps them distinct.
    """
    store = _store(tmp_path)
    load = FindingLoad(
        accepted=(
            _finding(_sha("a"), text="first on a"),
            _finding(_sha("a"), text="second on a", severity=FindingSeverity.LOW),
            _finding(_sha("b"), text="only on b"),
        ),
        rejected=(RejectedTrailer(_sha("c"), "Review-Finding: bogus line", "unknown reviewer"),),
    )

    store.replace_all(load)
    first = store.dump()
    store.replace_all(load)
    second = store.dump()

    assert first == second
    # Not vacuously equal: the store actually holds the content.
    assert len(first.findings) == 3
    assert len(first.rejected) == 1
    # Two findings on one commit are kept distinct by position, in source order.
    on_a = [f for f in first.findings if f.commit_sha == _sha("a")]
    assert [f.position for f in on_a] == [0, 1]
    assert [f.finding_text for f in on_a] == ["first on a", "second on a"]


def test_a_stale_parser_stamp_forces_a_rebuild(tmp_path: Path) -> None:
    """AC-4: a superseded parser grammar is detected, and a rebuild restamps.

    A grammar change flips :data:`PARSER_STAMP`; a store built by the old grammar
    carries the old value. Simulated by overwriting the recorded stamp (no way to
    mutate the grammar mid-test), which is exactly what an old build looks like.
    """
    store = _store(tmp_path)
    store.replace_all(FindingLoad(accepted=(_finding(_sha("a")),), rejected=()))

    assert store.is_current()
    assert store.stamp() == FindingsStamp(
        findings_schema_version=FINDINGS_SCHEMA_VERSION, parser_stamp=PARSER_STAMP
    )

    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("UPDATE findings_metadata SET parser_stamp = 'superseded' WHERE id = 1")
        connection.commit()

    assert not store.is_current()
    stale = store.stamp()
    assert stale is not None
    assert stale.parser_stamp == "superseded"

    store.replace_all(FindingLoad(accepted=(_finding(_sha("a")),), rejected=()))
    assert store.is_current()
    restamped = store.stamp()
    assert restamped is not None
    assert restamped.parser_stamp == PARSER_STAMP


def test_a_stale_schema_version_forces_a_rebuild(tmp_path: Path) -> None:
    """AC-4, the schema-version face: an older FINDINGS_SCHEMA_VERSION is stale too.

    The parser stamp and the schema version are independent forcing functions; a
    file at the wrong schema version is rebuilt regardless of its parser stamp.
    """
    store = _store(tmp_path)
    store.replace_all(FindingLoad(accepted=(_finding(_sha("a")),), rejected=()))
    assert store.is_current()

    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute(
            "UPDATE findings_metadata SET findings_schema_version = ? WHERE id = 1",
            (FINDINGS_SCHEMA_VERSION - 1,),
        )
        connection.commit()

    assert not store.is_current()
    store.replace_all(FindingLoad(accepted=(_finding(_sha("a")),), rejected=()))
    assert store.is_current()


def test_a_rejected_trailer_is_stored_distinctly_not_as_a_finding(tmp_path: Path) -> None:
    """AC-5: a rejected trailer is queryable apart and never an accepted finding."""
    store = _store(tmp_path)
    rejected = RejectedTrailer(
        _sha("c"), "Review-Finding: nonsense line", "unknown reviewer 'nonsense'"
    )
    store.replace_all(FindingLoad(accepted=(_finding(_sha("a")),), rejected=(rejected,)))

    dump = store.dump()

    assert len(dump.rejected) == 1
    assert dump.rejected[0].commit_sha == _sha("c")
    assert dump.rejected[0].raw_line == "Review-Finding: nonsense line"
    assert dump.rejected[0].reason == "unknown reviewer 'nonsense'"
    # The one accepted finding is the real one; the rejected line raised no finding.
    assert len(dump.findings) == 1
    assert dump.findings[0].commit_sha == _sha("a")
    assert all(f.commit_sha != _sha("c") for f in dump.findings)


def test_rejected_raw_line_is_inert_even_when_it_looks_like_a_valid_finding(
    tmp_path: Path,
) -> None:
    """AC-5 / refinement B: ``raw_line`` is never re-parsed into a finding.

    The rejected ``raw_line`` here is a syntactically *valid* Review-Finding line.
    If the store (or anything downstream) re-parsed stored ``raw_line`` it would
    become an accepted finding -- a false record from untrusted author bytes
    (ADR-0029 D3). It must stay a rejected row, byte-for-byte.
    """
    store = _store(tmp_path)
    poison = "Review-Finding: security CRITICAL — a line that would parse cleanly"
    store.replace_all(
        FindingLoad(
            accepted=(),
            rejected=(RejectedTrailer(_sha("d"), poison, "record-level date rejection"),),
        )
    )

    dump = store.dump()

    assert dump.findings == ()
    assert len(dump.rejected) == 1
    assert dump.rejected[0].raw_line == poison


def test_a_rebuild_with_new_content_leaves_no_stale_rows(tmp_path: Path) -> None:
    """AC-2/AC-6: ``replace_all`` is wholesale -- a prior load's rows do not survive.

    Idempotency (rebuild the *same* load twice) cannot see a non-wholesale replace:
    the same rows land either way. The property that a *superseded* row is gone is
    only visible when the second rebuild carries *different* content. So the store
    is rebuilt over load A, then over a disjoint load B, and the dump is asserted to
    equal B exactly -- no finding from A, no rejected row from A. If ``replace_all``
    ever cleared incrementally (an upsert, an append, a DELETE the mutation drops)
    a stale row from A would surface here; wholesale-from-empty is what this pins.
    """
    store = _store(tmp_path)
    load_a = FindingLoad(
        accepted=(
            _finding(_sha("a"), text="A first on a"),
            _finding(_sha("a"), text="A second on a"),
        ),
        rejected=(RejectedTrailer(_sha("c"), "Review-Finding: A bogus", "A reason"),),
    )
    load_b = FindingLoad(
        accepted=(_finding(_sha("b"), text="B only on b"),),
        rejected=(),
    )

    store.replace_all(load_a)
    store.replace_all(load_b)
    dump = store.dump()

    # Exactly load B: nothing from A survived the second rebuild.
    assert [f.finding_text for f in dump.findings] == ["B only on b"]
    assert [f.commit_sha for f in dump.findings] == [_sha("b")]
    assert dump.rejected == ()
    # Not vacuous -- load A really did land rows the wholesale rebuild had to clear.
    store.replace_all(load_a)
    assert len(store.dump().findings) == 2
    assert len(store.dump().rejected) == 1


def test_dump_orders_by_commit_then_position(tmp_path: Path) -> None:
    """The dump's fixed total order is ``(commit_sha, position)``, not insertion order.

    The port promises a fixed total order so two dumps of the same logical content
    compare equal (AC-2/AC-6). That promise is the ``ORDER BY commit_sha, position``
    in :meth:`dump`; without it a dump returns rows in insertion (rowid) order,
    which is *not* sorted here on purpose. The load is authored with commit ``b``'s
    rows inserted *before* commit ``a``'s, so insertion order (b, b, a, a) differs
    from the required order (a, a, b, b): a dropped or reversed ``ORDER BY`` returns
    the wrong sequence and this fails. Both tables are checked, since both order.
    """
    store = _store(tmp_path)
    # Insertion order is b, b, a, a -- the opposite of the (commit_sha, position)
    # order the dump must impose, so rowid order and sorted order diverge.
    load = FindingLoad(
        accepted=(
            _finding(_sha("b"), text="b0"),
            _finding(_sha("b"), text="b1"),
            _finding(_sha("a"), text="a0"),
            _finding(_sha("a"), text="a1"),
        ),
        rejected=(
            RejectedTrailer(_sha("b"), "b-rejected-0", "r"),
            RejectedTrailer(_sha("a"), "a-rejected-0", "r"),
        ),
    )

    store.replace_all(load)
    dump = store.dump()

    assert [(f.commit_sha, f.position) for f in dump.findings] == [
        (_sha("a"), 0),
        (_sha("a"), 1),
        (_sha("b"), 0),
        (_sha("b"), 1),
    ]
    # The text rides with its key, so a mis-order would carry the wrong content too.
    assert [f.finding_text for f in dump.findings] == ["a0", "a1", "b0", "b1"]
    assert [r.commit_sha for r in dump.rejected] == [_sha("a"), _sha("b")]
