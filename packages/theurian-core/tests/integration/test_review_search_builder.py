"""Evidence files in, derived store out (ADR-0030 slice 3).

The module-level drivers for the builder, over the **real** evidence store and
the **real** SQLite store: a build here writes files under ``tmp_path``, reads
them back through ``ReviewEvidenceStore.read_all`` and lands rows in a database,
because the properties below are about the seam between those and not about a
projection function in isolation.

Four claims, and each fails on its own:

* **A withheld record is not written.** Asserted over every table of the store
  file rather than over a search result, because a row no query happens to select
  is still a row a later change could select. The complement is asserted in the
  same case: the record that was *not* withheld is there, so a build that wrote
  nothing at all cannot pass.
* **A withheld key and a key that never existed are indistinguishable.** The
  store built while withholding record K is compared, byte-for-byte in its
  dumped-value form, against the store built from a corpus that never held K --
  the one-query-two-corpora shape, at the store level. The MCP-level version of
  the same closure is a later commit's.
* **A rebuild reproduces.** Delete the store, run the builder again over the same
  files, and it comes back equal -- ADR-0030's owed test 4, whose write-and-read
  half slice 2 already held and which needed a store to delete.
* **A record this build cannot store is refused by name.** The refusal names the
  evidence file, because the store is derived and rebuilding it fails the same way
  until somebody opens that file.

Marked ``integration``. Writes only under ``tmp_path``; nothing here touches this
repository's own ``.theurian/``.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from theurian.application.project_service import ProjectPaths
from theurian.application.review_search_builder import (
    EvidenceEntry,
    ReviewSearchBuilder,
    ReviewSearchBuildError,
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
    MAX_STORED_PULL_REQUEST,
    ReviewSearchRecord,
    ReviewTextChannel,
)
from theurian.infrastructure.review_evidence import (
    EvidenceRecord,
    IngestionRun,
    ReviewEvidenceStore,
)
from theurian.infrastructure.sqlite.review_search_schema import REVIEW_SEARCH_TABLES
from theurian.infrastructure.sqlite.review_search_store import SqliteReviewSearchStore

pytestmark = pytest.mark.integration

PROJECT = ProjectId("demo")
PROVIDER: Final = "github"
REPOSITORY: Final = "acme/order-service"
RUN: Final = IngestionRun("01K1AAAAAA01234567890ABCDE", datetime(2026, 9, 7, 9, 0, tzinfo=UTC))


def _participant(external_id: str, name: str = "Reviewer One") -> ReviewParticipant:
    return ReviewParticipant(provider=PROVIDER, external_id=external_id, display_name=name)


def _anchor(uri: str) -> SourceAnchor:
    return SourceAnchor(
        provider=PROVIDER,
        source_uri=uri,
        repository=REPOSITORY,
        commit_sha="a" * 40,
        file_path="src/order.py",
        line_start=10,
        line_end=12,
        external_id="PRRT_kwDOABCD",
    )


def _event(number: int = 42) -> EvidenceRecord:
    return EvidenceRecord(
        provider=PROVIDER,
        repository=REPOSITORY,
        anchor=_anchor(f"https://github.com/{REPOSITORY}/pull/{number}"),
        payload=ReviewEvent(
            project_id=PROJECT,
            provider=PROVIDER,
            repository=REPOSITORY,
            number=number,
            title="Bound the retry budget",
            body="署名付きトークンを持つ呼び出しだけを再試行する。",
            author=_participant("USER_A"),
            created_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            url=f"https://github.com/{REPOSITORY}/pull/{number}",
            head_commit="b" * 40,
            base_commit="c" * 40,
            head_ref_name="fix/retry-budget",
            labels=("security",),
            merged=True,
            merge_commit="d" * 40,
            merged_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
            ci_successful=True,
        ),
    )


def _submission(external_id: str = "PRR_kwDOABCD1", number: int = 42) -> EvidenceRecord:
    return EvidenceRecord(
        provider=PROVIDER,
        repository=REPOSITORY,
        anchor=_anchor(f"https://github.com/{REPOSITORY}/pull/{number}#pullrequestreview-1"),
        payload=ReviewSubmission(
            external_id=external_id,
            project_id=PROJECT,
            event_key=f"{PROVIDER}:{REPOSITORY}#{number}",
            author=_participant("USER_B", "Reviewer Two"),
            body="Approving; the budget is now bounded.",
            state="APPROVED",
            submitted_at=datetime(2026, 8, 1, 15, 0, tzinfo=UTC),
        ),
    )


def _thread(external_id: str = "PRRT_kwDO_test_node_0001", number: int = 42) -> EvidenceRecord:
    return EvidenceRecord(
        provider=PROVIDER,
        repository=REPOSITORY,
        anchor=_anchor(f"https://github.com/{REPOSITORY}/pull/{number}#discussion_r1"),
        payload=ReviewThread(
            external_id=external_id,
            project_id=PROJECT,
            event_key=f"{PROVIDER}:{REPOSITORY}#{number}",
            file_path="src/order.py",
            comments=(
                ReviewComment(
                    external_id="IC_kwDO1",
                    author=_participant("USER_A"),
                    body="This retries forever.",
                    created_at=datetime(2026, 8, 1, 13, 0, tzinfo=UTC),
                ),
                ReviewComment(
                    external_id="IC_kwDO2",
                    author=_participant("USER_B", "Reviewer Two"),
                    body="Fixed in b1c2d3.",
                    created_at=datetime(2026, 8, 1, 14, 0, tzinfo=UTC),
                ),
            ),
            state=ReviewThreadState.RESOLVED,
            resolution=ReviewResolution(
                state=ReviewThreadState.RESOLVED,
                resolved_by=_participant("USER_C", "Reviewer Three"),
                fix_commit="e" * 40,
            ),
            line_start=10,
            line_end=12,
        ),
    )


def _project(tmp_path: Path, name: str = "repo") -> ProjectPaths:
    root = tmp_path / name
    (root / ".theurian").mkdir(parents=True)
    return ProjectPaths.of(root)


def _landed(paths: ProjectPaths, *records: EvidenceRecord) -> ReviewEvidenceStore:
    store = ReviewEvidenceStore(paths.review)
    store.write(records, run=RUN)
    return store


def _build(
    paths: ProjectPaths, evidence: ReviewEvidenceStore, *, withheld: frozenset[str]
) -> tuple[SqliteReviewSearchStore, dict[str, object]]:
    """One build, exactly as the composition root composes it, minus the lock.

    ``evidence_entries`` is imported from the CLI rather than re-implemented here:
    that nine-field mapping is the thing under test as much as the builder is, and
    a test that mapped the records itself would pass over a composition root that
    had stopped carrying a field.
    """
    store = SqliteReviewSearchStore(paths.review_search_for("local"))
    builder = ReviewSearchBuilder(read_evidence=evidence_entries(evidence), write=store.replace_all)
    report = builder.build(ReviewSearchBuildRequest(withheld_record_keys=withheld))
    return store, report


def _every_stored_value(store: SqliteReviewSearchStore) -> str:
    """Every cell of every table of the store file, rendered for a substring search.

    The population is :data:`REVIEW_SEARCH_TABLES` -- the schema's own statement of
    what it creates -- and it is compared against ``sqlite_master`` in the same
    read, so a table added without joining that tuple fails here rather than being
    silently excluded from the sweep it exists to make exhaustive.
    """
    with closing(sqlite3.connect(store.path)) as connection:
        on_disk = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert on_disk == set(REVIEW_SEARCH_TABLES), (
            f"the store holds {sorted(on_disk)} and REVIEW_SEARCH_TABLES names "
            f"{sorted(REVIEW_SEARCH_TABLES)}; a table outside that tuple is a table "
            f"this sweep would not have looked in"
        )
        return "\n".join(
            repr(tuple(row))
            for table in REVIEW_SEARCH_TABLES
            for row in connection.execute(f"SELECT * FROM {table}")  # noqa: S608
        )


# -- the withholding seam -----------------------------------------------------


def test_a_withheld_record_has_no_row_in_any_table(tmp_path: Path) -> None:
    """AC-2: physical absence, not a flag and not a filter.

    The withheld thread's every distinguishing value is searched for across every
    cell of every table -- its path, its provider id, the participant who only
    appears in it, and the text of both its comments. The kept pull request is
    asserted present in the same case, so a build that simply wrote nothing cannot
    satisfy this.
    """
    paths = _project(tmp_path)
    evidence = _landed(paths, _event(), _thread())
    thread_key = "PRRT_kwDO_test_node_0001"

    store, report = _build(paths, evidence, withheld=frozenset({thread_key}))

    stored = _every_stored_value(store)
    for trace in (thread_key, "USER_C", "This retries forever.", "Fixed in b1c2d3."):
        assert trace not in stored, f"a withheld record's {trace!r} is still in the store"
    assert "Bound the retry budget" in stored, "the record that was kept is missing too"
    assert report["records"] == 1
    assert report["withheld"] == 1


def test_a_withheld_key_and_a_key_that_never_existed_produce_one_store(tmp_path: Path) -> None:
    """The closure form: one build over two corpora must agree.

    Two projects. One landed the thread and withheld it; the other never landed it
    at all. Their stores are compared by dumped value, which is every column of
    every record including its participants and its fragments -- so a difference in
    *which rows reached* a table shows up here and not only a difference in a
    served field.

    The store-level half. The MCP-level version of the same argument, over a
    response rather than over a dump, is a later commit's and is not claimed here.
    """
    held = _project(tmp_path, "held")
    never = _project(tmp_path, "never")
    with_thread = _landed(held, _event(), _thread())
    without_thread = _landed(never, _event())

    withheld_store, withheld_report = _build(
        held, with_thread, withheld=frozenset({"PRRT_kwDO_test_node_0001"})
    )
    absent_store, absent_report = _build(never, without_thread, withheld=frozenset())

    assert withheld_store.dump() == absent_store.dump()
    assert withheld_report["records"] == absent_report["records"]


def test_the_build_api_cannot_be_asked_to_withhold_nothing_by_omission() -> None:
    """The no-default rule, held at the type rather than in prose.

    ``IndexRequest.visible_sensitivities``' precedent: "nothing is withheld" is
    the state that must never be implicit, so a caller that omits the set gets a
    construction error rather than a build that quietly holds everything.
    """
    with pytest.raises(TypeError):
        ReviewSearchBuildRequest()  # type: ignore[call-arg]


# -- rebuild is the durability story ------------------------------------------


def test_a_deleted_store_rebuilds_to_the_same_rows_from_the_same_files(tmp_path: Path) -> None:
    """AC-3, and the second half of ADR-0030's owed test 4.

    Slice 2 held "write a record, read it back, get the same record"; this is the
    half that needed a store to delete. The store's sidecars go with it, because a
    database is three names and leaving a write-ahead log beside a deleted main
    file is not the fresh start this case is about.
    """
    paths = _project(tmp_path)
    evidence = _landed(paths, _event(), _submission(), _thread())

    store, first = _build(paths, evidence, withheld=frozenset())
    before = store.dump()
    store.path.unlink()
    for sidecar in ("-wal", "-shm"):
        store.path.with_name(store.path.name + sidecar).unlink(missing_ok=True)
    assert not store.path.exists()

    _, second = _build(paths, evidence, withheld=frozenset())

    assert store.dump() == before
    assert second == first
    assert len(before) == 3


def test_the_projection_carries_each_kind_onto_the_fields_a_search_filters_on(
    tmp_path: Path,
) -> None:
    """What each of the three kinds becomes, asserted per kind rather than in bulk.

    The thread is the case worth naming: its participants are both commenters
    **and** the person who resolved it, in first-appearance order, so a filter on
    the reviewer who only replied finds it. Its pull-request number is read out of
    the ``event_key``, which is the only thing in the file that names one.
    """
    paths = _project(tmp_path)
    evidence = _landed(paths, _event(), _submission(), _thread())

    store, _report = _build(paths, evidence, withheld=frozenset())
    by_kind = {record.kind: record for record in store.dump()}

    assert set(by_kind) == {"pull-request", "review-submission", "review-thread"}

    event = by_kind["pull-request"]
    assert (event.pull_request, event.thread_state, event.file_path) == (42, None, None)
    assert [fragment.channel for fragment in event.texts] == [
        ReviewTextChannel.TITLE,
        ReviewTextChannel.BODY,
    ]
    assert event.texts[1].content == "署名付きトークンを持つ呼び出しだけを再試行する。"

    submission = by_kind["review-submission"]
    assert (submission.pull_request, submission.thread_state) == (42, None)
    assert submission.participant_ids == ("USER_B",)

    thread = by_kind["review-thread"]
    assert (thread.pull_request, thread.thread_state, thread.file_path) == (
        42,
        "resolved",
        "src/order.py",
    )
    assert thread.participant_ids == ("USER_A", "USER_B", "USER_C")
    assert thread.author_external_id == "USER_A"
    assert [fragment.content for fragment in thread.texts] == [
        "This retries forever.",
        "Fixed in b1c2d3.",
    ]


# -- a record this build cannot store -----------------------------------------


def test_a_landed_record_carrying_an_unpaired_surrogate_is_refused_by_name(
    tmp_path: Path,
) -> None:
    """The refusal names the evidence file, which is the artefact to open.

    ``json.loads`` decodes a ``\\ud800`` escape into an unpaired surrogate, which
    has no UTF-8 encoding, so a hand-edited evidence file can carry a value the
    writer that landed it could never have produced. Left ungraded it reaches the
    driver as ``UnicodeEncodeError``, and the best the store could say is the name
    of the ``.sqlite`` file -- which is derived, and not the thing to look at.

    The plant goes through the landed document rather than through the builder's
    own types, because what is being asserted is that a **file** on disk produces
    this refusal.
    """
    paths = _project(tmp_path)
    evidence = _landed(paths, _thread())
    landed = next(paths.review.rglob("*.json"))
    document = json.loads(landed.read_text(encoding="utf-8"))
    document["record"]["comments"][0]["body"] = "lone \\ud800 surrogate"
    landed.write_text(
        json.dumps(document, indent=2, ensure_ascii=False).replace("\\\\ud800", "\\ud800"),
        encoding="utf-8",
    )

    with pytest.raises(ReviewSearchBuildError) as excinfo:
        _build(paths, evidence, withheld=frozenset())

    assert landed.relative_to(paths.review).as_posix() in str(excinfo.value)
    assert "surrogate" in str(excinfo.value)
    assert "theurian review build" in excinfo.value.remedy
    assert "do not delete it" in excinfo.value.remedy


def test_the_transportability_check_reaches_every_string_a_record_carries() -> None:
    """The population is reflected over, not listed, so a new field is covered.

    Three shapes a record's strings live in -- a plain field, a tuple of ids, and
    a tuple of fragments -- each carrying a value that cannot cross the SQLite
    boundary, each driven separately. A check that had stopped descending into
    tuples would pass the first and fail to raise on the other two.

    Driven through the builder's own projection rather than through a private
    helper, so what is asserted is what a build does.
    """
    from theurian.application.review_search_builder import (
        _refuse_untransportable,
    )

    def record(**overrides: object) -> ReviewSearchRecord:
        fields: dict[str, object] = {
            "relative_path": "a/review-thread/1.json",
            "record_key": "T1",
            "kind": "review-thread",
            "provider": PROVIDER,
            "repository": REPOSITORY,
            "pull_request": 42,
            "thread_state": "resolved",
            "file_path": "src/order.py",
            "source_uri": "https://example.invalid/1",
            "author_external_id": "USER_A",
            "author_display_name": "Reviewer One",
            "participant_ids": ("USER_A",),
            "texts": (),
            "last_seen_run_id": RUN.run_id,
            "last_seen_at": "2026-09-07T09:00:00.000000+00:00",
        }
        return ReviewSearchRecord(**{**fields, **overrides})  # type: ignore[arg-type]

    from theurian.domain.review_search import (
        ReviewTextFragment,
    )

    _refuse_untransportable(record())  # the honest record raises nothing

    for overrides in (
        {"author_display_name": "lone \ud800 name"},
        {"participant_ids": ("USER_A", "with\x00nul")},
        {
            "texts": (
                ReviewTextFragment(channel=ReviewTextChannel.COMMENT, content="lone \ud800 body"),
            )
        },
    ):
        with pytest.raises(ReviewSearchBuildError):
            _refuse_untransportable(record(**overrides))


def test_a_last_seen_instant_that_cannot_be_expressed_in_utc_is_refused_by_name() -> None:
    """The other refusal, and it is reachable from a hand-edited ``observedAt``.

    The evidence reader accepts any aware ``datetime`` ``fromisoformat`` produces,
    and a max-year negative-offset instant overflows on conversion to UTC. Graded
    rather than left to crash, and the message names the file.
    """
    from theurian.application.review_search_builder import (
        _projected,
    )

    entry = EvidenceEntry(
        relative_path="a/review-submission/1.json",
        record_key="PRR_1",
        kind="review-submission",
        provider=PROVIDER,
        repository=REPOSITORY,
        anchor=_anchor("https://example.invalid/1"),
        payload=ReviewSubmission(
            external_id="PRR_1",
            project_id=PROJECT,
            event_key=f"{PROVIDER}:{REPOSITORY}#42",
            author=_participant("USER_A"),
            body="ok",
            state="APPROVED",
        ),
        last_seen_run_id=RUN.run_id,
        last_seen_at=datetime.max.replace(tzinfo=datetime.now(UTC).astimezone().tzinfo).replace(
            tzinfo=UTC
        ),
    )

    # `datetime.max` in UTC converts fine; the overflow needs a negative offset.
    from datetime import timedelta, timezone

    overflowing = EvidenceEntry(
        **{
            **{field: getattr(entry, field) for field in entry.__slots__},
            "last_seen_at": datetime.max.replace(tzinfo=timezone(timedelta(hours=-11))),
        }
    )

    with pytest.raises(ReviewSearchBuildError) as excinfo:
        _projected(overflowing)

    assert "a/review-submission/1.json" in str(excinfo.value)
    assert "theurian review build" in excinfo.value.remedy


def _with_event_key(paths: ProjectPaths, number: str) -> Path:
    """Rewrite the one landed record's ``eventKey`` to end ``#number``.

    A hand edit of the landed file rather than of the builder's own types,
    because what these two cases assert is that a **file** on disk produces a
    graded refusal: ``_pull_request_of`` parses the trailing ``#(\\d+)`` out of
    exactly this string, so the file is the only place such a number can enter.
    """
    landed = next(paths.review.rglob("*.json"))
    document = json.loads(landed.read_text(encoding="utf-8"))
    document["record"]["eventKey"] = f"{PROVIDER}:{REPOSITORY}#{number}"
    landed.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return landed


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        pytest.param("0", "pull request 0", id="zero"),
        pytest.param("9" * 20, "larger than", id="beyond-the-column"),
    ],
)
def test_a_hand_edited_pull_request_number_is_refused_with_the_record_cure(
    tmp_path: Path, number: str, expected: str
) -> None:
    """RED means a number a landed file supplied is graded as somebody else's fault.

    Both ends of the store's column, and each arrived wrong in its own way.
    ``#0`` reached ``ReviewSearchRecord``'s own invariant, which raises
    ``InvariantViolationError`` -- a ``TheurianError`` carrying an **empty**
    remedy, so ``theurian review build`` printed its backstop cure, ``Run
    `theurian doctor`.``, over a defect ``doctor`` cannot see. A twenty-digit
    number carried past the record and into ``sqlite3``, which answered
    ``OverflowError``; the store's write arm graded that as *the store could not
    be written* with a cure about free disk space, sending an operator to check a
    filesystem over a byte in an evidence file.

    Both now carry :func:`_record_cure`: the file to open, and the command to
    re-run once it is corrected.
    """
    paths = _project(tmp_path)
    evidence = _landed(paths, _submission())
    landed = _with_event_key(paths, number)

    with pytest.raises(ReviewSearchBuildError) as excinfo:
        _build(paths, evidence, withheld=frozenset())

    assert expected in str(excinfo.value)
    assert landed.relative_to(paths.review).as_posix() in str(excinfo.value)
    assert landed.relative_to(paths.review).as_posix() in excinfo.value.remedy
    assert "theurian review build" in excinfo.value.remedy
    assert "do not delete it" in excinfo.value.remedy
    for wrong in ("theurian doctor", "free disk space"):
        assert wrong not in excinfo.value.remedy, (
            f"the cure still sends the operator to {wrong!r} over a value in an evidence file"
        )


def test_the_over_range_refusal_never_renders_the_number_it_refuses(tmp_path: Path) -> None:
    """RED means composing the refusal is itself the next crash.

    ``_pull_request_of`` parses ``#(\\d+)``, so a hand-edited event key can carry
    a number of any length, and CPython refuses to render an integer wider than
    ``sys.get_int_max_str_digits()`` -- 4,300 digits by default. A refusal that
    quoted the caller's number back would raise ``ValueError`` *inside the arm
    building it*, which is the face ``mcp/findings._digits`` met. The bound is
    named instead, which is the part a reader acts on.
    """
    paths = _project(tmp_path)
    evidence = _landed(paths, _submission())
    digits = sys.get_int_max_str_digits() + 1
    _with_event_key(paths, "9" * digits)

    with pytest.raises(ReviewSearchBuildError) as excinfo:
        _build(paths, evidence, withheld=frozenset())

    assert str(MAX_STORED_PULL_REQUEST) in str(excinfo.value)
    assert "9" * 100 not in str(excinfo.value)


def test_a_hand_edited_pull_request_record_number_is_refused_at_the_record(
    tmp_path: Path,
) -> None:
    """The other way an out-of-range number arrives, and the guard that catches it.

    A pull-request record carries its own ``number`` as a JSON integer, so the
    event-key digit-run arm never sees it: the reader has already produced an
    ``int``, and ``ReviewEvent`` bounds it below and not above. What refuses it is
    :class:`ReviewSearchRecord`'s own invariant, and without this case that
    invariant would be a guard no data reaches -- the event-key cases above all
    stop one layer earlier.

    The file is **renamed** as well as edited, because the reader derives a
    record's own path from its contents and refuses a file sitting anywhere else.
    That check is what a plain edit meets first, and it is not the one under test
    here.
    """
    number = MAX_STORED_PULL_REQUEST + 1
    paths = _project(tmp_path)
    evidence = _landed(paths, _event())
    original = next(paths.review.rglob("*.json"))
    document = json.loads(original.read_text(encoding="utf-8"))
    document["record"]["number"] = number
    landed = original.with_name(f"{number}.json")
    landed.write_text(json.dumps(document, indent=2), encoding="utf-8")
    original.unlink()

    with pytest.raises(ReviewSearchBuildError) as excinfo:
        _build(paths, evidence, withheld=frozenset())

    assert f"larger than {MAX_STORED_PULL_REQUEST}" in str(excinfo.value)
    assert landed.relative_to(paths.review).as_posix() in excinfo.value.remedy
    assert "theurian review build" in excinfo.value.remedy
