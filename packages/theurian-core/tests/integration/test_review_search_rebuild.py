"""Throw the review search store away and get it back (ADR-0030 slice 3).

ADR-0004 puts this store in **Index / derived**: deleting it costs a rebuild and
not a record, and the whole of that promise is the property this module drives.
It is the second half of ADR-0030's owed test 4 -- slice 2 held "write a record,
read it back, get the same record", and this is the half that needed a store to
delete -- extended in the three directions a real operator reaches it from:

* **A deleted store comes back.** Not "a dump compares equal", which is the
  claim the builder's own module already makes, but *the served answers* -- what
  a caller actually receives -- reproduce across the delete.
* **A grown corpus is reflected, and an unchanged record is not disturbed.** A
  rebuild is wholesale, so a new evidence file must appear and the records
  already there must come back byte-identical in their served form. Both halves,
  because a build that threw everything away and reprojected it satisfies the
  first alone.
* **Order is not information.** The evidence reader answers in a total order and
  the store serves in a total order, so *when a record arrived* must not reach
  the artifact. Driven at both seams -- the arrival order of evidence files, and
  the order of the records inside one load -- because they are two different
  places the property could be lost.

And two lifecycle cases that are not about content at all:

* **A rebuild that fails must leave the previous store serving**, because the
  alternative is an operator losing a working store to a bad record.
* **A rebuild that *lands* mid-call must not split the call across two stores.**
  ``SqliteReviewSearchStore.search`` states that as a property of ``mode=ro``
  plus publish-by-``os.replace``: the stamp and the rows come from one file, and
  the worst a concurrent rebuild does is answer from the immediately previous
  store, one publish behind. It is a claim about POSIX unlink semantics, so it is
  measured rather than reasoned about.

Marked ``integration``: every case lands real evidence files and opens a real
SQLite database. Writes only under ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, override

import pytest

from theurian.application.project_service import ProjectPaths
from theurian.application.review_search_builder import (
    ReviewSearchBuilder,
    ReviewSearchBuildRequest,
)
from theurian.cli.review_commands import evidence_entries
from theurian.domain.enums import ReviewThreadState
from theurian.domain.identifiers import ProjectId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review import (
    ReviewComment,
    ReviewEvent,
    ReviewParticipant,
    ReviewResolution,
    ReviewSubmission,
    ReviewThread,
)
from theurian.domain.review_search import (
    ReviewSearchLoad,
    ReviewSearchQuery,
    ReviewSearchRecord,
    ReviewTextChannel,
    ReviewTextFragment,
)
from theurian.infrastructure.review_evidence import (
    EvidenceRecord,
    IngestionRun,
    ReviewEvidenceStore,
)
from theurian.infrastructure.sqlite.review_search_store import (
    ReviewSearchStoreError,
    SqliteReviewSearchStore,
)

pytestmark = pytest.mark.integration

PROJECT: Final = ProjectId("rebuild")
PROVIDER: Final = "github"
REPOSITORY: Final = "acme/order-service"

#: Two runs, because "the corpus grew" means a *later* run observed a record the
#: first one did not, and ``last_seen_run_id`` is a stored column: a growth case
#: that reused one run would not notice a rebuild that dropped the stamp.
#:
#: Crockford base32 -- no ``I``, ``L``, ``O`` or ``U``.
FIRST_RUN: Final = IngestionRun(
    "01K1RB1D00000000000000AAAA", datetime(2026, 9, 7, 9, 0, tzinfo=UTC)
)
SECOND_RUN: Final = IngestionRun(
    "01K1RB1D00000000000000BBBB", datetime(2026, 9, 8, 9, 0, tzinfo=UTC)
)

#: The cut every comparison below reads excerpts at. Wide enough that no fixture
#: text reaches it, so a difference in a served answer is a difference in the
#: record rather than in where the read happened to cut.
TEXT_CHARS: Final = 400


def _participant(external_id: str, display_name: str = "Reviewer One") -> ReviewParticipant:
    return ReviewParticipant(provider=PROVIDER, external_id=external_id, display_name=display_name)


def _anchor(uri: str) -> SourceAnchor:
    return SourceAnchor(provider=PROVIDER, source_uri=uri, repository=REPOSITORY)


def _pull_request(*, number: int, body: str) -> EvidenceRecord:
    return EvidenceRecord(
        provider=PROVIDER,
        repository=REPOSITORY,
        anchor=_anchor(f"https://github.com/{REPOSITORY}/pull/{number}"),
        payload=ReviewEvent(
            project_id=PROJECT,
            provider=PROVIDER,
            repository=REPOSITORY,
            number=number,
            title=f"Pull request {number}",
            body=body,
            author=_participant("USER_A"),
            created_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            url=f"https://github.com/{REPOSITORY}/pull/{number}",
            head_commit="b" * 40,
            base_commit="c" * 40,
            head_ref_name="fix/retry-budget",
            labels=("security",),
        ),
    )


def _submission(*, external_id: str, number: int, body: str) -> EvidenceRecord:
    return EvidenceRecord(
        provider=PROVIDER,
        repository=REPOSITORY,
        anchor=_anchor(f"https://github.com/{REPOSITORY}/pull/{number}#review-{external_id}"),
        payload=ReviewSubmission(
            external_id=external_id,
            project_id=PROJECT,
            event_key=f"{PROVIDER}:{REPOSITORY}#{number}",
            author=_participant("USER_B", "Reviewer Two"),
            body=body,
            state="APPROVED",
        ),
    )


def _thread(*, external_id: str, number: int, bodies: Sequence[str]) -> EvidenceRecord:
    return EvidenceRecord(
        provider=PROVIDER,
        repository=REPOSITORY,
        anchor=_anchor(f"https://github.com/{REPOSITORY}/pull/{number}#thread-{external_id}"),
        payload=ReviewThread(
            external_id=external_id,
            project_id=PROJECT,
            file_path="src/order.py",
            event_key=f"{PROVIDER}:{REPOSITORY}#{number}",
            comments=tuple(
                ReviewComment(
                    external_id=f"IC_{external_id}_{ordinal}",
                    # Two authors, so the participant table holds more than one
                    # row per record and its `position` column has an order that a
                    # rebuild could get wrong.
                    author=_participant(
                        "USER_A" if ordinal % 2 == 0 else "USER_B",
                        "Reviewer One" if ordinal % 2 == 0 else "Reviewer Two",
                    ),
                    body=body,
                    created_at=datetime(2026, 8, 1, 13, ordinal, tzinfo=UTC),
                )
                for ordinal, body in enumerate(bodies)
            ),
            state=ReviewThreadState.RESOLVED,
            resolution=ReviewResolution(
                state=ReviewThreadState.RESOLVED,
                # A third person, who commented on nothing. A thread's participants
                # are every commenter *plus* whoever resolved it, so this is what
                # makes the participant table's `position` ordering non-trivial.
                resolved_by=_participant("USER_C", "Reviewer Three"),
            ),
        ),
    )


def _corpus() -> tuple[EvidenceRecord, ...]:
    """One record of each kind, so a rebuild is compared across all three."""
    return (
        _pull_request(number=42, body="署名付きトークンを持つ呼び出しだけを再試行する。"),
        _submission(external_id="PRR_kwDOA1", number=42, body="Approving; the budget is bounded."),
        _thread(
            external_id="PRRT_kwDOA1",
            number=42,
            bodies=("This retries forever.", "Fixed in b1c2d3.", "Thanks."),
        ),
    )


@dataclass(frozen=True, slots=True)
class _Project:
    """One project's evidence store and the search store built from it."""

    paths: ProjectPaths
    evidence: ReviewEvidenceStore
    store: SqliteReviewSearchStore

    def land(self, records: Sequence[EvidenceRecord], *, run: IngestionRun) -> None:
        self.evidence.write(tuple(records), run=run)

    def build(self) -> dict[str, object]:
        builder = ReviewSearchBuilder(
            read_evidence=evidence_entries(self.evidence), write=self.store.replace_all
        )
        return builder.build(ReviewSearchBuildRequest(withheld_record_keys=frozenset()))


def _project(base: Path, name: str = "repo") -> _Project:
    root = base / name
    (root / ".theurian").mkdir(parents=True)
    paths = ProjectPaths.of(root)
    return _Project(
        paths=paths,
        evidence=ReviewEvidenceStore(paths.review),
        store=SqliteReviewSearchStore(paths.review_search_for("local")),
    )


#: The queries a rebuild is compared over. Each reaches the store through a
#: different predicate -- none, a structural column, a set-valued filter and a
#: substring -- because a rebuild can reproduce the record table and still lose the
#: participant or fragment rows those last two range over.
COMPARED: Final[tuple[tuple[str, ReviewSearchQuery], ...]] = (
    ("unfiltered", ReviewSearchQuery(limit=50)),
    ("by-pull-request", ReviewSearchQuery(limit=50, pull_request=42)),
    ("by-participant", ReviewSearchQuery(limit=50, author="USER_B")),
    ("by-thread-state", ReviewSearchQuery(limit=50, thread_state="resolved")),
    ("by-text", ReviewSearchQuery(limit=50, text_contains="署名付きトークン")),
    ("by-text-in-a-later-comment", ReviewSearchQuery(limit=50, text_contains="Fixed in b1c2d3")),
    ("first-slot-only", ReviewSearchQuery(limit=1)),
)


def _one_answer(store: SqliteReviewSearchStore, query: ReviewSearchQuery) -> object:
    """One query's outcome: its hits, or the refusal it raised.

    A refusal is folded into the value rather than allowed to escape, so a
    comparison between two stores reports *which* of them stopped serving instead
    of dying at whichever call happened to come first. That matters here: a
    rebuild that destroyed the store it was replacing is exactly a case where one
    side refuses and the other answers.
    """
    try:
        return [asdict(hit) for hit in store.search(query, text_chars=TEXT_CHARS)]
    except ReviewSearchStoreError as exc:
        return {"refusal": str(exc)}


def _served(store: SqliteReviewSearchStore) -> str:
    """Every query in :data:`COMPARED`, answered and serialised into one string.

    The comparison is over what a **caller receives**, not over the store's
    verification dump: a dump takes no predicate and applies no bound, so it
    cannot notice a rebuild that reproduced every row and lost the ordering, the
    excerpt or the channel a served hit carries.

    ``sort_keys`` so the string is a function of the values rather than of dict
    insertion order; ``ensure_ascii=False`` so a CJK excerpt compares as the text
    it is.
    """
    return json.dumps(
        {name: _one_answer(store, query) for name, query in COMPARED},
        sort_keys=True,
        ensure_ascii=False,
    )


def _assert_every_query_answered(served: str) -> None:
    """Refuse to compare two strings that record nothing being served.

    Two refusals are equal, and so are two empty answers, so an equality over
    :func:`_served` holds for any implementation unless somebody checks that the
    store really answered. This is that check, and every comparison below runs it
    on its baseline before comparing.
    """
    answers: dict[str, object] = json.loads(served)

    assert set(answers) == {name for name, _query in COMPARED}, (
        "the comparison must range over every query in COMPARED"
    )
    for name, answer in answers.items():
        assert isinstance(answer, list), f"`{name}` refused rather than answering: {answer}"
        assert answer, f"`{name}` answered with no rows, so comparing it proves nothing"


def _delete_store(store: SqliteReviewSearchStore) -> None:
    """Remove the database and both its companions.

    A SQLite database is three names, and leaving a write-ahead log beside a
    deleted main file is not the fresh start a rebuild case is about.
    """
    store.path.unlink()
    for sidecar in ("-wal", "-shm"):
        store.path.with_name(store.path.name + sidecar).unlink(missing_ok=True)
    assert not store.path.exists()


# -- a deleted store comes back ----------------------------------------------


def test_a_deleted_store_rebuilds_to_the_same_answers_from_the_same_evidence(
    tmp_path: Path,
) -> None:
    """ADR-0004, ADR-0030 decision 3's owed test 4. The derived-state promise.

    Compared over the **served answers** rather than over a dump. The builder's
    own module already compares dumps, and a dump is a whole-table read with no
    predicate, no bound and no cut -- so it cannot see a rebuild that reproduced
    every row and lost the participant rows a set-valued filter ranges over, the
    fragment ordering an excerpt is picked by, or the order ``LIMIT`` truncates.

    The store file's *bytes* are deliberately not compared: two SQLite files
    holding identical logical content legitimately differ, and an assertion over
    bytes would fail for reasons that are not defects. What is byte-compared is
    the serialised answer, which is the thing a caller can observe.

    The equality is guarded by its own precondition: the corpus must actually
    answer something, or "the answers reproduce" is a statement about two empty
    strings.
    """
    project = _project(tmp_path)
    project.land(_corpus(), run=FIRST_RUN)

    first_report = project.build()
    before = _served(project.store)
    _delete_store(project.store)
    second_report = project.build()

    _assert_every_query_answered(before)
    assert _served(project.store).encode("utf-8") == before.encode("utf-8"), (
        "a store rebuilt from the same evidence files answered differently from the one that "
        "was deleted -- the derived-state promise is that this costs a rebuild, not a record"
    )
    assert second_report == first_report == {"records": 3, "withheld": 0}
    assert project.store.dump() == project.store.dump(), "and two dumps of one store agree"


def test_a_rebuild_after_the_evidence_grew_carries_the_new_record_and_disturbs_no_old_one(
    tmp_path: Path,
) -> None:
    """A rebuild is wholesale, and both halves of that have to be true.

    A build that reflected the new record while quietly reprojecting the old ones
    differently would satisfy "the corpus grew" and still be a defect: the records
    that did not change must come back identical, down to the run that last
    observed them. So the new record is asserted present *and* every previously
    served hit is asserted unchanged, which is why the second landing uses a
    second :class:`IngestionRun` -- ``last_seen_run_id`` is a stored column, and a
    growth case that reused one run could not tell a preserved stamp from a
    rewritten one.
    """
    project = _project(tmp_path)
    project.land(_corpus(), run=FIRST_RUN)
    project.build()
    before = {
        hit.relative_path: asdict(hit)
        for hit in project.store.search(ReviewSearchQuery(limit=50), text_chars=TEXT_CHARS)
    }

    project.land(
        (_pull_request(number=43, body="A second pull request, landed later."),), run=SECOND_RUN
    )
    report = project.build()

    after = {
        hit.relative_path: asdict(hit)
        for hit in project.store.search(ReviewSearchQuery(limit=50), text_chars=TEXT_CHARS)
    }
    assert len(before) == 3, "the first build must have served the whole corpus"
    assert report == {"records": 4, "withheld": 0}
    assert set(after) - set(before), "the rebuild did not reflect the record that was added"
    assert {path: after[path] for path in before} == before, (
        "a rebuild after the corpus grew changed a record that did not: every field of every "
        "previously served hit must survive, including the run that last observed it"
    )
    assert after[next(iter(set(after) - set(before)))]["last_seen_run_id"] == SECOND_RUN.run_id, (
        "and the new record must carry the run that observed it, not the first one"
    )


def test_a_rebuild_after_an_evidence_file_was_removed_no_longer_serves_it(
    tmp_path: Path,
) -> None:
    """The other direction of wholesale, driven from the evidence files.

    The store adapter's own module holds this at the *load* seam -- a record the
    load does not carry has no row. This holds it one layer up, where an operator
    actually acts: the file under ``.theurian/review/`` is what they can remove,
    and the question is whether the next rebuild still serves what it described.

    A removal here is a **test** of derived state and not a recommendation. The
    evidence directory is Canonical with no replayable source (ADR-0030 decision
    3), which is why ``_record_cure`` tells an operator to edit a bad record and
    never to delete it.
    """
    project = _project(tmp_path)
    project.land(_corpus(), run=FIRST_RUN)
    project.build()
    doomed = sorted(project.paths.review.rglob("*.json"))[0]
    still_served = {
        hit.relative_path
        for hit in project.store.search(ReviewSearchQuery(limit=50), text_chars=TEXT_CHARS)
    }

    doomed.unlink()
    report = project.build()

    survivors = {
        hit.relative_path
        for hit in project.store.search(ReviewSearchQuery(limit=50), text_chars=TEXT_CHARS)
    }
    assert len(still_served) == 3, "the first build must have served the whole corpus"
    assert report == {"records": 2, "withheld": 0}
    assert survivors == still_served - {doomed.relative_to(project.paths.review).as_posix()}, (
        "a rebuild kept serving a record whose evidence file is gone, so the store is no "
        "longer a projection of the directory it claims to be derived from"
    )


# -- order is not information ------------------------------------------------


def test_two_builds_agree_whatever_order_the_evidence_records_arrived_in(
    tmp_path: Path,
) -> None:
    """Determinism at the landing seam: arrival order must not reach the artifact.

    The evidence reader sorts by relative path, so its answer is already total.
    What this pins is that the **build** preserves that -- a projection that
    numbered participants or fragments by the order records were handed to it, or
    a store whose served order fell back on insertion order, would make one
    corpus produce two artifacts depending on the sequence a fetch happened to
    return.

    Two projects rather than two builds of one, because re-landing into one
    project cannot express "arrived in the other order": the files are already
    there. The comparison is over the served answers *and* the dump, because the
    two see different things -- the dump carries every participant and fragment
    in position order, and the served answers carry the ordering and the excerpt
    choice.
    """
    forward = _project(tmp_path, "forward")
    backward = _project(tmp_path, "backward")
    corpus = _corpus()
    for record in corpus:
        forward.land((record,), run=FIRST_RUN)
    for record in reversed(corpus):
        backward.land((record,), run=FIRST_RUN)

    forward_report = forward.build()
    backward_report = backward.build()

    _assert_every_query_answered(_served(forward.store))
    assert forward_report == backward_report == {"records": 3, "withheld": 0}
    assert forward.store.dump() == backward.store.dump(), (
        "two corpora that differ only in the order their records were landed produced "
        "different stores"
    )
    assert _served(forward.store) == _served(backward.store), "and answered a caller differently"


def test_the_served_order_does_not_depend_on_the_order_records_were_written(
    tmp_path: Path,
) -> None:
    """The same property at the other seam: inside one load.

    ``SEARCH_ORDER`` is a SQL ``ORDER BY`` over a unique key, so what a caller
    receives is a property of the corpus. This drives it directly: one load and
    its reverse are written to two stores, and both the dump and every served
    answer must agree. An implementation that let SQLite return rows in whatever
    order it stored them -- which it is free to do without an ``ORDER BY`` -- would
    separate here and nowhere in the builder's own tests, because those never
    write one load twice in two orders.

    Built as a hand-made load rather than through the builder, because the builder
    cannot produce a reversed one: it projects the reader's total order.
    """
    load = ReviewSearchLoad(
        records=tuple(
            ReviewSearchRecord(
                relative_path=f"a/review-thread/{leaf}.json",
                record_key=f"T{leaf}",
                kind="review-thread",
                provider=PROVIDER,
                repository=REPOSITORY,
                pull_request=42,
                thread_state="resolved",
                file_path="src/order.py",
                source_uri=f"https://github.com/{REPOSITORY}/pull/42#{leaf}",
                author_external_id="USER_A",
                author_display_name="Reviewer One",
                participant_ids=("USER_A", "USER_B"),
                texts=(
                    ReviewTextFragment(
                        channel=ReviewTextChannel.COMMENT, content=f"comment {leaf} one"
                    ),
                    ReviewTextFragment(
                        channel=ReviewTextChannel.COMMENT, content=f"comment {leaf} two"
                    ),
                ),
                last_seen_run_id=FIRST_RUN.run_id,
                last_seen_at="2026-09-07T09:00:00.000000+00:00",
            )
            for leaf in ("a", "b", "c")
        )
    )
    reversed_load = ReviewSearchLoad(records=tuple(reversed(load.records)))
    forward = SqliteReviewSearchStore(tmp_path / "forward" / "theurian-review-local.sqlite")
    backward = SqliteReviewSearchStore(tmp_path / "backward" / "theurian-review-local.sqlite")

    forward.replace_all(load)
    backward.replace_all(reversed_load)

    assert load.records != reversed_load.records, "the two loads must actually differ in order"
    assert forward.dump() == backward.dump() == load.records, (
        "the store's own order must be its records' key order, whatever order they were written in"
    )
    assert _served(forward) == _served(backward), (
        "and a caller must receive one sequence whatever order the rows were inserted in"
    )


# -- a rebuild that fails ----------------------------------------------------


def test_a_rebuild_that_fails_leaves_the_previous_store_serving_and_no_working_file(
    tmp_path: Path,
) -> None:
    """#404's publish-by-rename, from the operator's side rather than the adapter's.

    A rebuild assembles at :attr:`SqliteReviewSearchStore.building_path` and is
    published by ``os.replace``, so a build that dies partway must leave the
    previous store exactly where it was -- the alternative is an operator losing a
    working store to one bad record. It must also leave no working file behind: a
    surviving ``.building`` is what a later run would open and extend, landing a
    dead build's rows in a live store.

    The failure is a load carrying an unpaired surrogate, which reaches the driver
    as ``UnicodeEncodeError`` -- neither ``sqlite3.Error`` nor ``OSError``, and
    therefore the case that says the write arm's guard really is the complement of
    what the CLI grades rather than a list of families. The builder refuses that
    value upstream by name; this is the backstop underneath it.
    """
    project = _project(tmp_path)
    project.land(_corpus(), run=FIRST_RUN)
    project.build()
    before = _served(project.store)
    _assert_every_query_answered(before)
    poisoned = ReviewSearchLoad(
        records=(
            ReviewSearchRecord(
                relative_path="a/review-thread/1.json",
                record_key="T1",
                kind="review-thread",
                provider=PROVIDER,
                repository=REPOSITORY,
                pull_request=42,
                thread_state="resolved",
                file_path="src/order.py",
                source_uri="https://example.invalid/1",
                author_external_id="USER_A",
                author_display_name="Reviewer One",
                participant_ids=("USER_A",),
                texts=(
                    ReviewTextFragment(
                        channel=ReviewTextChannel.COMMENT, content="lone \ud800 surrogate"
                    ),
                ),
                last_seen_run_id=FIRST_RUN.run_id,
                last_seen_at="2026-09-07T09:00:00.000000+00:00",
            ),
        )
    )

    with pytest.raises(ReviewSearchStoreError) as excinfo:
        project.store.replace_all(poisoned)

    assert _served(project.store) == before, (
        "a failed rebuild replaced the store that was serving: the publish is by rename, so "
        "a build that never finished must never have been published"
    )
    assert not project.store.building_path.exists(), (
        "a failed rebuild left its working file behind, and the next build would open and "
        "extend it rather than starting from empty"
    )
    assert ".theurian/state" in excinfo.value.remedy, (
        "the write-path remedy names the precondition to fix, not just a retry"
    )


# -- a rebuild that lands mid-read -------------------------------------------

#: The query the raced call is driven with. One call, deliberately: the hook below
#: fires once, so a comparison over several queries would race the first and read
#: the *new* store for the rest -- which is a mixture this file would then be
#: asserting rather than refusing.
RACED: Final = ReviewSearchQuery(limit=50)


def _reap_sidecars(path: Path) -> None:
    """Remove a database's ``-wal``/``-shm`` companions, as ``replace_all`` does.

    The publish reaps them *before* the rename, not after, so the publish name
    never briefly holds a new main file beside the previous one's log. The hook
    below imitates the real publish rather than only its rename, or the thing
    being raced would not be the thing that ships.
    """
    for sidecar in ("-wal", "-shm"):
        path.with_name(path.name + sidecar).unlink(missing_ok=True)


def _publishing_connect(*, source: Path, target: Path, fired: list[str]) -> Any:
    """A ``sqlite3.connect`` that publishes ``source`` over ``target`` mid-read.

    The deterministic stand-in for a concurrent ``theurian review build``, and it
    is a *scheduler* rather than a fake: the connection is a real
    ``sqlite3.Connection``, the file operations are the real ones a publish
    performs, and the only thing injected is **when** the publish lands.

    It lands after the connection's first ``execute``, which for
    :meth:`SqliteReviewSearchStore.search` is the stamp read. That is the window
    the claim is about: if the connection followed the directory entry rather than
    the inode it opened, the stamp would have come from one store and the rows
    from another -- a call split across two publishes. A replace landing before
    the open would not distinguish that from "the whole call read the new store",
    which is why the hook is on the first read and not on the connect.

    A thread would have raced the same window without pinning it, and a race whose
    interleaving is not pinned is a test that passes on the runs where the
    interleaving did not happen.
    """
    real_connect = sqlite3.connect

    class _PublishingAfterTheFirstRead(sqlite3.Connection):
        @override
        def execute(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
            cursor = super().execute(*args, **kwargs)
            if not fired:
                fired.append(str(args[0]))
                _reap_sidecars(target)
                os.replace(source, target)  # noqa: PTH105 - the atomic primitive under test
            return cursor

    def connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        return real_connect(*args, factory=_PublishingAfterTheFirstRead, **kwargs)

    return connect


def test_a_rebuild_that_lands_mid_call_answers_from_the_store_the_call_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``SqliteReviewSearchStore.search``'s ``mode=ro`` claim, measured not reasoned.

    The method's docstring asserts a property of the operating system, not of this
    codebase: ``mode=ro`` binds the connection to the file that existed when it
    opened, ``replace_all`` publishes by ``os.replace``, which swaps a directory
    entry and leaves an open descriptor reading the inode it already holds -- so a
    rebuild landing mid-call cannot split the call across two stores, and the worst
    it does is answer from the immediately previous store, whole and one publish
    behind. That is a claim about POSIX unlink semantics, and a claim about the
    platform is exactly the kind that must be run rather than argued.

    Measured on macOS 26.6.2 (arm64), SQLite 3.47.1, CPython 3.13.3: the publish
    lands immediately after the stamp read, the in-flight call returns the previous
    store's whole answer with no error and no mixture, and the next call returns
    the successor's. This test is what keeps that measurement true -- of a store
    opened ``mode=rwc``, of a publish that stopped being a rename, and of a read
    that acquired a second connection between the stamp and the rows.

    Four assertions, in the order that makes a failure readable: the two stores
    must really differ (or the race compares one store with itself); the publish
    must really have landed *between* the stamp read and the row read (or the
    window was never entered); the raced call must answer from the store it
    opened; and the call after it must see the successor (or the publish never
    happened and the third assertion holds for any implementation).
    """
    project = _project(tmp_path, "live")
    project.land(_corpus(), run=FIRST_RUN)
    project.build()
    successor = _project(tmp_path, "successor")
    successor.land((*_corpus(), _pull_request(number=43, body="the next publish.")), run=SECOND_RUN)
    successor.build()
    before = _one_answer(project.store, RACED)
    published = _one_answer(successor.store, RACED)
    fired: list[str] = []

    with monkeypatch.context() as patched:
        patched.setattr(
            sqlite3,
            "connect",
            _publishing_connect(
                source=successor.store.path, target=project.store.path, fired=fired
            ),
        )
        during = _one_answer(project.store, RACED)

    assert before != published and isinstance(before, list) and before, (
        "the two stores must answer differently and both must answer something, or the race "
        "below compares one store against itself"
    )
    assert fired and "review_search_schema_version" in fired[0], (
        f"the publish must land between the stamp read and the row read, and it fired after "
        f"{fired!r} instead -- so the window this case is about was never entered"
    )
    assert during == before, (
        "a rebuild landing mid-call changed what the call answered: the read followed the "
        "directory entry rather than the inode it opened, so a caller can be served a stamp "
        "from one store and rows from another"
    )
    assert _one_answer(project.store, RACED) == published, (
        "the next call must see the store the publish landed, or nothing was published and "
        "the equality above holds for any implementation"
    )
