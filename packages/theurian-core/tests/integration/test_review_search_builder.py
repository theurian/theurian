"""Evidence files in, derived store out (ADR-0030 slice 3).

The module-level drivers for the builder, over the **real** evidence store and
the **real** SQLite store: a build here writes files under ``tmp_path``, reads
them back through ``ReviewEvidenceStore.read_all`` and lands rows in a database,
because the properties below are about the seam between those and not about a
projection function in isolation.

Five claims, and each fails on its own:

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
* **A record whose file moved between the read and the publish is not
  republished.** The evidence read is outside the project's write lock, so a
  rebuild can hold a corpus older than the disk; the fingerprint comparison
  around the read is what keeps a deletion -- ADR-0030 decision 3's only
  retention remedy -- from being silently undone, and what keeps a record that
  was *rewritten* in that window out of the store with the body the read took.
  Every transition a path can make across one build has a case in this section:
  present to absent, absent to present, present to different content, present to
  a directory, and present to the same content. The transitions are enumerated
  over *states*; what a build detects them **through** is a fingerprint, which is
  a witness of state and not the state -- so the one rewrite shape a ``stat``
  cannot witness has a case of its own here too, asserting the behaviour the
  reader records as a residual rather than wishing it away. And a build that can
  keep none of what it read refuses instead of publishing an empty store, which
  is that section's whole-corpus end.
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
import os
import sqlite3
import sys
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager, nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from theurian.application.findings_builder import WriteSection
from theurian.application.project_service import ProjectPaths
from theurian.application.review_search_builder import (
    EvidenceEntry,
    ReadEvidence,
    ReviewSearchBuilder,
    ReviewSearchBuildError,
    ReviewSearchBuildRequest,
    _unchanged,
)
from theurian.cli.review_commands import evidence_entries, evidence_fingerprints
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


#: One act around the evidence read, already bound to the instant it runs at.
#: The two constructors below are the two instants, and a case names the one it
#: means rather than passing an act and a flag -- which is what kept
#: :func:`_build`'s signature from growing a parameter per instant.
_ReadHook = Callable[[ReadEvidence], ReadEvidence]


def _while_the_read_is_in_flight(act: Callable[[], None]) -> _ReadHook:
    """Run ``act`` after the read has its bytes and before it returns.

    **The instant the fingerprint ordering is about, and the only one that tells
    the two orders apart.** A change made after the read returns is caught
    whichever side of the read the first fingerprint sits on, because both
    fingerprints are then taken around it. A change that lands *while the read is
    in flight* is not: the entries carry the pre-change bytes and the disk carries
    the post-change ones the moment the read returns, so a fingerprint taken
    after the read already describes the changed file and matches the one taken
    at the publish.

    That window is also the widest of the build's three -- the read is a parse per
    evidence file, where the other two phases are a directory walk and a store
    write -- so it is where a concurrent ``review ingest`` is most likely to land.
    """

    def hook(read: ReadEvidence) -> ReadEvidence:
        def read_then() -> tuple[EvidenceEntry, ...]:
            entries = read()
            act()
            return entries

        return read_then

    return hook


def _after_the_first_capture(act: Callable[[], None]) -> _ReadHook:
    """Run ``act`` after the first fingerprint and before the read enumerates.

    The third instant, and the only one that puts a record in the build's hands
    that the **first** fingerprint never saw: the capture is taken before the read
    is called, so a file landing here is read and projected while
    ``before_the_read`` has no entry for it at all.
    :func:`_while_the_read_is_in_flight`'s act runs one step later and cannot
    produce that state, because by then the read has already enumerated.
    """

    def hook(read: ReadEvidence) -> ReadEvidence:
        def read_after() -> tuple[EvidenceEntry, ...]:
            act()
            return read()

        return read_after

    return hook


def _build(
    paths: ProjectPaths,
    evidence: ReviewEvidenceStore,
    *,
    withheld: frozenset[str],
    write_section: WriteSection = nullcontext,
    read_hook: _ReadHook | None = None,
) -> tuple[SqliteReviewSearchStore, dict[str, object]]:
    """One build, exactly as the composition root composes it, minus the lock file.

    ``evidence_entries`` and ``evidence_fingerprints`` are imported from the CLI
    rather than re-implemented here: that nine-field mapping is the thing under
    test as much as the builder is, and the second binds the same directory walk
    the read goes through, so a test that listed the files itself would pass over
    a composition root that had stopped carrying a field or started listing a
    different set.

    ``write_section`` and ``read_hook`` are the three instants the race cases
    below need, and none of them is interchangeable with another: the section
    changes the directory *inside* the write section, and the two hook
    constructors -- :func:`_while_the_read_is_in_flight` and
    :func:`_after_the_first_capture` -- change it while the read is in flight and
    between the first capture and the read. Each records which claim it can drive.
    """
    store = SqliteReviewSearchStore(paths.review_search_for("local"))
    read: ReadEvidence = evidence_entries(evidence)
    builder = ReviewSearchBuilder(
        read_evidence=read if read_hook is None else read_hook(read),
        list_evidence_fingerprints=evidence_fingerprints(paths.review),
        write=store.replace_all,
        write_section=write_section,
    )
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


# -- the window between the read and the publish ------------------------------


def _write_section_that(act: Callable[[], None]) -> WriteSection:
    """A write section that runs ``act`` the instant the build enters it.

    The race, made deterministic and placed at the *latest* instant it can happen:
    the build has already read the evidence, and the directory changes before
    anything is published. A change made around the call instead would prove
    nothing about where the builder checks, since the read is the first thing
    ``build`` does and the write is the last.

    Passed as the real ``write_section`` collaborator rather than by patching, so
    what these two cases drive is the seam the composition root fills with the
    project's write lock.
    """

    @contextmanager
    def section() -> Iterator[None]:
        act()
        yield

    return section


def _landed_by_kind(paths: ProjectPaths) -> dict[str, Path]:
    """The one landed file per record kind, keyed by the directory it sits in.

    The layout is ``<repository hash>/<kind>/<leaf>.json``, so the parent's name is
    the kind. Unambiguous only while a corpus lands one record of each kind, which
    the assertion below is what keeps true rather than assumes.
    """
    by_kind = {landed.parent.name: landed for landed in paths.review.rglob("*.json")}
    assert len(by_kind) == len(list(paths.review.rglob("*.json"))), (
        "two records of one kind landed, so keying by kind picks an arbitrary file"
    )
    return by_kind


def test_a_record_deleted_between_the_read_and_the_publish_is_not_republished(
    tmp_path: Path,
) -> None:
    """RED means a rebuild silently undoes a deletion.

    The evidence read happens outside the project's write lock on purpose -- a
    parse per file must not hold the single writer -- so of two concurrent
    rebuilds the one that read *earlier* can publish *later*. Without a membership
    check at the publish, that build writes back a record whose file was deleted
    in between, and both commands exit 0.

    Deleting a file is the retention remedy ADR-0030 decision 3 leaves an
    operator: ``.theurian/review/`` is source, no refetch rebuilds it, and there
    is no other way to take a landed record out. So the resurrected row is the
    remediation reverted, not a store one refetch behind.

    Three records land and two vanish at the publish. The submission is the case
    the fix is about -- nothing withheld it, and it is gone because its file is.
    The thread is the interaction: it is **both** withheld and deleted, and it
    stays out for the first reason, counted once. The pull request is the
    complement, so a build that published nothing at all cannot pass here.
    """
    paths = _project(tmp_path)
    evidence = _landed(paths, _event(), _submission(), _thread())
    thread_key = "PRRT_kwDO_test_node_0001"
    landed = _landed_by_kind(paths)

    def delete_two_of_the_three() -> None:
        landed["review-submission"].unlink()
        landed["review-thread"].unlink()

    store, report = _build(
        paths,
        evidence,
        withheld=frozenset({thread_key}),
        write_section=_write_section_that(delete_two_of_the_three),
    )

    stored = _every_stored_value(store)
    for trace in ("PRR_kwDOABCD1", "Approving; the budget is now bounded."):
        assert trace not in stored, (
            f"a deleted record's {trace!r} was republished from a read taken before "
            f"the file went away"
        )
    for trace in (thread_key, "USER_C", "This retries forever."):
        assert trace not in stored, f"a withheld and deleted record's {trace!r} is in the store"
    assert "Bound the retry budget" in stored, "the record that survived is missing too"
    assert report == {"records": 1, "withheld": 1}


def _retitled(number: int = 42) -> EvidenceRecord:
    """The same pull-request record with one field edited upstream.

    ``dataclasses.replace`` on the payload rather than a second constructor call,
    so everything that decides the record's *path* -- provider, repository, kind,
    number -- is carried over by construction. A refetch has to land on the same
    file for the race below to be a race at all, and building a second record by
    hand is how that silently stops being true.
    """
    original = _event(number)
    assert isinstance(original.payload, ReviewEvent)
    return replace(original, payload=replace(original.payload, title="Retitled upstream"))


def test_a_record_rewritten_between_the_read_and_the_publish_is_not_republished(
    tmp_path: Path,
) -> None:
    """RED means the store serves a body the evidence file no longer carries.

    The update face of the same window the deletion case above covers, and the
    one a membership check cannot see: the file is present at the publish under
    the identical path, so "is it still on disk" answers yes while the bytes
    behind it are somebody's correction. ``ReviewEvidenceStore`` *updates* a
    record whose content changed upstream -- ``review ingest`` does exactly this
    on every refetch -- so the writer this races against is the product's own.

    Both directions, because either alone would pass over the wrong fix. The
    pre-update title must be absent from this build's store: publishing it is
    serving a body that exists nowhere on disk, under a ``lastSeenRun`` stamp
    that says it was observed. And the post-update title must be there after the
    **next** build: a revalidation that dropped the record for ever would trade
    one defect for a store that never converges.

    **The edit lands while the read is in flight**, which is what makes this the
    case that holds the fingerprint *ordering* and not only the comparison: see
    :func:`_while_the_read_is_in_flight`. Moving the build's first capture to after the read
    leaves every other case here green and turns this one red.

    The thread is the complement in both builds, so a build that published
    nothing at all cannot pass here.
    """
    paths = _project(tmp_path)
    evidence = _landed(paths, _event(), _thread())

    def edit_the_pull_request() -> None:
        evidence.write((_retitled(),), run=RUN)

    store, report = _build(
        paths,
        evidence,
        withheld=frozenset(),
        read_hook=_while_the_read_is_in_flight(edit_the_pull_request),
    )

    stored = _every_stored_value(store)
    assert "Bound the retry budget" not in stored, (
        "the store serves the title the read took, which the evidence file no longer "
        "carries -- the record was rewritten before anything was published"
    )
    assert "Retitled upstream" not in stored, (
        "the post-update body was published without being read, which needs a parse "
        "under the write lock"
    )
    assert "This retries forever." in stored, "the record nothing touched is missing too"
    assert report == {"records": 1, "withheld": 0}

    rebuilt, second = _build(paths, evidence, withheld=frozenset())

    stored_again = _every_stored_value(rebuilt)
    assert "Retitled upstream" in stored_again, (
        "the next rebuild did not converge on the edited record, so the revalidation "
        "drops it for ever rather than for one build"
    )
    assert "Bound the retry budget" not in stored_again
    assert second == {"records": 2, "withheld": 0}


def test_a_leaf_replaced_by_a_directory_between_the_read_and_the_publish_is_dropped(
    tmp_path: Path,
) -> None:
    """RED means a record survives its file stopping being a file.

    The walk that lists evidence selects a leaf by the **suffix of its name**, and
    a directory may be named ``42.json`` as easily as a file may. So a path that
    is present at both captures, and is a directory at the second, is a path a
    membership check reports as still there -- which is why the fingerprint
    carries whether the leaf is a regular file and not only when it last changed.

    The thread is the complement, so a build that published nothing cannot pass.
    """
    paths = _project(tmp_path)
    evidence = _landed(paths, _event(), _thread())
    landed = _landed_by_kind(paths)

    def replace_the_leaf_with_a_directory() -> None:
        landed["pull-request"].unlink()
        landed["pull-request"].mkdir()

    store, report = _build(
        paths,
        evidence,
        withheld=frozenset(),
        write_section=_write_section_that(replace_the_leaf_with_a_directory),
    )

    stored = _every_stored_value(store)
    assert "Bound the retry budget" not in stored, (
        "a record whose leaf is now a directory was published from the copy the read "
        "took, so the listing saw a name and not a file"
    )
    assert "This retries forever." in stored, "the record nothing touched is missing too"
    assert report == {"records": 1, "withheld": 0}


def test_the_fingerprint_slot_that_says_regular_file_decides_on_its_own() -> None:
    """RED means the case above rests on a timestamp having moved as well.

    The integration case cannot hold this: a directory put where a file was
    carries its own ``mtime_ns``, its own size and its own inode number, so the
    record is dropped whether or not the flag exists. What makes ``present -> not
    a regular file`` a revalidated transition **by construction** rather than by
    accident of three other values is that the flag is part of the fingerprint,
    and the only way to demonstrate that is to hold the other three still -- the
    inode included, because an inode number is the filesystem's to reallocate once
    a name is unlinked and nothing promises the directory gets a fresh one.

    ``_unchanged`` is reached by name deliberately: it is the predicate the
    publish applies, so a fix that moved the check back out of it would redden
    here rather than leaving this passing over a helper nothing calls.
    """
    path = "sha256-abc/pull-request/42.json"
    a_file = {path: (1_700_000_000_000_000_000, 512, 4_242, True)}
    not_a_file = {path: (1_700_000_000_000_000_000, 512, 4_242, False)}

    assert _unchanged(path, a_file, a_file), (
        "an unchanged fingerprint is not publishable, so the predicate drops every record"
    )
    assert not _unchanged(path, a_file, not_a_file), (
        "the same mtime, size and inode at a path that stopped being a regular file "
        "read as unchanged, so the regular-file slot decides nothing"
    )
    assert not _unchanged(path, a_file, {}), "a vanished path read as unchanged"
    assert not _unchanged(path, {}, a_file), (
        "a path nothing observed before the read was published on the strength of one observation"
    )


def test_the_fingerprint_slot_that_says_how_big_the_file_is_decides_on_its_own() -> None:
    """RED means a length change rides through on the other three slots agreeing.

    The size slot is the one an in-place rewrite cannot hide: a write through the
    same descriptor keeps the inode and ``os.utime`` puts the timestamp back, so
    for a rewrite that *changes the length* the size is the only slot left saying
    anything. Holding the other three still is the only way to show that it
    decides, and no builder-level case can -- every plant that changes a file's
    size through this store's own writer moves the timestamp and the inode too.

    ``_unchanged`` is reached by name for the reason its sibling case gives: it is
    the predicate the publish applies.
    """
    path = "sha256-abc/pull-request/42.json"
    at_one_length = {path: (1_700_000_000_000_000_000, 512, 4_242, True)}
    at_another = {path: (1_700_000_000_000_000_000, 513, 4_242, True)}

    assert _unchanged(path, at_one_length, at_one_length), (
        "an unchanged fingerprint is not publishable, so the predicate drops every record"
    )
    assert not _unchanged(path, at_one_length, at_another), (
        "the same mtime, the same inode and a different size read as unchanged, so the "
        "size slot decides nothing"
    )


def test_a_file_that_landed_inside_the_read_and_went_away_before_the_publish_is_dropped(
    tmp_path: Path,
) -> None:
    """RED means a record **neither** capture ever saw is published.

    The arm ``_unchanged``'s ``captured is not None`` exists for, and the one the
    other absence cases cannot reach. A file that lands after the first
    fingerprint and before the read is read and projected, so it is in the
    builder's hands; delete it before the publish and *both* captures have no
    entry for it. Two ``None``\\ s compare equal, so without that guard the record
    is published on the strength of no observation at all -- from a file that was
    on disk for less than one build and is not there now.

    The complement is asserted in the same case, so a build that published nothing
    cannot pass, and the predicate's own arm is asserted beside the behaviour
    because deleting the guard is what makes both of them wrong at once.
    """
    paths = _project(tmp_path)
    evidence = _landed(paths, _event())

    def land_a_thread() -> None:
        evidence.write((_thread(),), run=RUN)

    def delete_the_thread() -> None:
        _landed_by_kind(paths)["review-thread"].unlink()

    store, report = _build(
        paths,
        evidence,
        withheld=frozenset(),
        read_hook=_after_the_first_capture(land_a_thread),
        write_section=_write_section_that(delete_the_thread),
    )

    stored = _every_stored_value(store)
    assert "This retries forever." not in stored, (
        "a record that neither fingerprint observed was published: it landed inside the "
        "read's own window and was deleted before the publish, so nothing ever saw it as "
        "a file on disk at a moment this build could publish from"
    )
    assert "Bound the retry budget" in stored, "the record nothing touched is missing too"
    assert report == {"records": 1, "withheld": 0}
    assert not _unchanged("sha256-abc/pull-request/42.json", {}, {}), (
        "the predicate calls a path neither capture holds unchanged, which is two `None`s "
        "compared for equality"
    )


def test_a_refetch_that_rewrites_identical_bytes_is_dropped_and_returns_next_build(
    tmp_path: Path,
) -> None:
    """The documented over-drop: fail-closed, and it converges.

    A refetch that finds a record unchanged upstream still *rewrites* its file,
    and ``mtime_ns`` moves even where every byte is the same -- asserted here on
    the bytes rather than assumed, so a store that started writing a differing
    stamp would not let this case quietly become the update case above.

    The fingerprint cannot tell that rewrite from a content change without
    reading the file, which is the thing the read/write split exists to keep out
    from under the lock. So the record is dropped, and this test records that
    behaviour rather than wishing it away: it costs one rebuild, and ``review
    ingest`` runs one itself after every landing.

    RED in the second half means the over-drop is permanent, which is a different
    defect from the one it was accepted as.
    """
    paths = _project(tmp_path)
    evidence = _landed(paths, _event(), _thread())
    landed = _landed_by_kind(paths)["pull-request"]
    before = landed.read_bytes()

    def refetch_the_same_record() -> None:
        evidence.write((_event(),), run=RUN)
        assert landed.read_bytes() == before, (
            "the refetch changed the bytes, so this case is the content-change case "
            "and no longer measures the benign one"
        )

    store, report = _build(
        paths,
        evidence,
        withheld=frozenset(),
        write_section=_write_section_that(refetch_the_same_record),
    )

    assert "Bound the retry budget" not in _every_stored_value(store), (
        "a record rewritten with identical bytes was published, so the revalidation "
        "is reading content it must not read under the lock"
    )
    assert report == {"records": 1, "withheld": 0}

    rebuilt, second = _build(paths, evidence, withheld=frozenset())

    assert "Bound the retry budget" in _every_stored_value(rebuilt), (
        "the over-drop is permanent rather than one rebuild long"
    )
    assert second == {"records": 2, "withheld": 0}


#: The one edit both timestamp-restoration cases plant, and its replacement. The
#: two are the **same length** so the size slot cannot answer, and the helper
#: below asserts that rather than trusting this line to stay true.
_ORIGINAL_TITLE: Final = b"Bound the retry budget"
_REWRITTEN_TITLE: Final = b"Overwritten upstream!!"


def _rewrite_with_the_timestamp_restored(leaf: Path, *, in_place: bool) -> None:
    """Rewrite ``leaf`` to the same length and put its timestamps back exactly.

    The attack ``mtime_ns`` and ``size`` cannot see. A timestamp is not a value
    only the clock sets: ``os.utime`` sets it to the nanosecond, so a rewrite that
    restores the captured one is invisible to those two slots on a filesystem with
    the finest timestamps there are -- no coarse clock needed.

    ``in_place`` chooses the **write shape**, which is what decides whether the
    inode moves, and both are asserted here rather than described: a rename-based
    write -- the shape this product's own evidence store uses -- publishes a new
    inode, and an in-place write keeps the one it had. That difference is the
    whole of what the two cases below drive, and it is the premise of each.
    """
    before = leaf.stat()
    original = leaf.read_bytes()
    edited = original.replace(_ORIGINAL_TITLE, _REWRITTEN_TITLE)
    assert edited != original and len(edited) == before.st_size, (
        "the plant has to change the bytes without changing the length, or the size slot "
        "answers and neither case measures what it says it does"
    )

    if in_place:
        with leaf.open("r+b") as handle:
            handle.write(edited)
    else:
        sibling = leaf.with_name(leaf.name + ".hostile")
        sibling.write_bytes(edited)
        sibling.replace(leaf)
    os.utime(leaf, ns=(before.st_atime_ns, before.st_mtime_ns))

    after = leaf.stat()
    assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size), (
        "the premise: this platform did not restore the timestamp exactly, so the plant is "
        "visible to the two slots these cases exist to defeat"
    )
    assert (after.st_ino == before.st_ino) is in_place, (
        "the premise: a rename-based write must move the inode and an in-place write must "
        "keep it, and this platform did the other thing"
    )


def test_a_rewrite_that_restores_the_timestamp_is_dropped_because_the_inode_moved(
    tmp_path: Path,
) -> None:
    """RED means a rewritten body is republished past ``mtime_ns`` and ``size``.

    The rewrite case above rests on the timestamp moving, and a timestamp moves
    only while nobody puts it back. ``os.utime`` puts it back to the nanosecond,
    so a same-length rewrite followed by a restoration is a changed file that two
    of the four slots call unchanged -- and the record would be published with the
    body the read took, which is the very defect the revalidation exists to stop,
    wearing a different plant.

    What catches it is ``st_ino``: the write lands through a sibling temporary and
    ``os.replace``, which is how this product's own evidence store publishes, and
    every rename-based write resolves the name to a **new inode**. The helper
    asserts that premise on this platform rather than assuming it.

    The complement is asserted in the same case, so a build that published nothing
    at all cannot pass, and the next build converges on the rewritten body -- the
    drop is one build long, as it is for every other transition here.
    """
    paths = _project(tmp_path)
    evidence = _landed(paths, _event(), _thread())
    landed = _landed_by_kind(paths)["pull-request"]

    store, report = _build(
        paths,
        evidence,
        withheld=frozenset(),
        write_section=_write_section_that(
            lambda: _rewrite_with_the_timestamp_restored(landed, in_place=False)
        ),
    )

    stored = _every_stored_value(store)
    assert "Bound the retry budget" not in stored, (
        "the store serves the body the read took, past a rewrite that restored the "
        "timestamp -- the fingerprint saw the same mtime and the same size and published"
    )
    assert "This retries forever." in stored, "the record nothing touched is missing too"
    assert report == {"records": 1, "withheld": 0}

    rebuilt, second = _build(paths, evidence, withheld=frozenset())

    assert "Overwritten upstream!!" in _every_stored_value(rebuilt)
    assert second == {"records": 2, "withheld": 0}


def test_an_in_place_rewrite_that_restores_the_timestamp_is_published_as_recorded(
    tmp_path: Path,
) -> None:
    """The residual, asserted rather than implied -- and it is not a wish.

    A fingerprint is a witness of state and not the state. The one rewrite shape
    no ``stat`` witnesses is an **in-place** write -- ``open("r+b")``, or ``cp -p``
    over an existing destination -- followed by a timestamp restoration: the bytes
    change, the length does not, the timestamp is put back and the inode is the
    one it always was. All four slots agree, and the record is published with the
    body the read took while the file on disk carries another.

    That is what this case asserts, because a residual nobody writes down is read
    as absent. Closing it means hashing every landed file, which is a read of the
    whole corpus under the project's write lock -- the one thing the read/write
    split exists to keep out.

    **Its grading is threat-model T-24, not a new class.** Reaching it needs write
    access to ``.theurian/review/``, and an actor with that can author an evidence
    record outright: the directory is *source*, is not git-ignored, and may arrive
    with a clone. So this behaviour sits inside an accepted, recorded residual.

    RED here means the residual moved -- most likely because somebody added a
    content hash. That is a welcome change and it makes this case a lie, so the
    case has to be rewritten with it rather than deleted quietly.
    """
    paths = _project(tmp_path)
    evidence = _landed(paths, _event(), _thread())
    landed = _landed_by_kind(paths)["pull-request"]

    store, report = _build(
        paths,
        evidence,
        withheld=frozenset(),
        write_section=_write_section_that(
            lambda: _rewrite_with_the_timestamp_restored(landed, in_place=True)
        ),
    )

    assert _REWRITTEN_TITLE in landed.read_bytes(), (
        "the premise: the file on disk has to carry the edited body, or there is no "
        "divergence between the store and the disk to record"
    )
    assert "Bound the retry budget" in _every_stored_value(store), (
        "the store no longer serves the body the read took, so the recorded residual has "
        "moved -- re-measure it and rewrite this case, which is documentation"
    )
    assert report == {"records": 2, "withheld": 0}


def test_a_file_that_lands_after_the_read_arrives_with_the_next_build(tmp_path: Path) -> None:
    """The other direction, and it stays one rebuild behind on purpose.

    The publish-time check can only drop, never add: noticing a new file inside
    the write section is not enough, because holding it would mean *reading* it,
    and a parse per file under the single writer lock is what the read/write split
    exists to keep out.

    So a record that lands after the read is absent from this build's store and
    present in the next one. RED here means either that the check started reading
    inside the lock, or that a rebuild stopped converging.
    """
    paths = _project(tmp_path)
    evidence = _landed(paths, _event())

    def land_a_thread() -> None:
        evidence.write((_thread(),), run=RUN)

    store, first = _build(
        paths,
        evidence,
        withheld=frozenset(),
        write_section=_write_section_that(land_a_thread),
    )

    assert first == {"records": 1, "withheld": 0}
    assert "This retries forever." not in _every_stored_value(store), (
        "a file that landed after the read was published, which needs a parse under the lock"
    )

    rebuilt, second = _build(paths, evidence, withheld=frozenset())

    assert second == {"records": 2, "withheld": 0}
    assert "This retries forever." in _every_stored_value(rebuilt), (
        "the next rebuild did not converge on the file the first one was too early to see"
    )


# -- a build that can keep nothing it read -------------------------------------


def test_a_build_that_can_keep_none_of_what_it_read_refuses_rather_than_emptying_the_store(
    tmp_path: Path,
) -> None:
    """RED means a stale rebuild replaces a serving store with an empty one, at exit 0.

    The window every case above is about, taken to its whole-corpus end. The read
    is outside the write lock, so a concurrent ``review ingest`` can rewrite
    *every* landed record while this build is reading them -- which is what a
    refetch of a repository whose records all changed upstream does -- and the
    revalidation then correctly drops all of them. Dropping all of them is not the
    defect; publishing the result is. The build would replace a store that was
    serving with one holding no rows and exit 0, and no reader can tell that store
    from a project that has no evidence at all.

    So the build refuses, inside the write section and before the write, which is
    what leaves the previous store exactly where it was: this layer publishes by
    replacement and never by emptying.

    Three assertions, because each of them fails on its own. The refusal is
    graded and carries the retry -- a plain ``TheurianError`` is what both CLI
    arms already publish. The store still answers with the rows it had, which is
    the whole point of refusing. And the *next* build converges on the rewritten
    corpus, so the refusal costs one build rather than wedging a project whose
    evidence legitimately moved.
    """
    paths = _project(tmp_path)
    evidence = _landed(paths, _event(), _thread())
    store, first = _build(paths, evidence, withheld=frozenset())
    assert first == {"records": 2, "withheld": 0}
    before = _every_stored_value(store)

    def refetch_every_record() -> None:
        evidence.write((_retitled(), _thread()), run=RUN)

    with pytest.raises(ReviewSearchBuildError) as excinfo:
        _build(
            paths,
            evidence,
            withheld=frozenset(),
            write_section=_write_section_that(refetch_every_record),
        )

    assert "2 of them" in str(excinfo.value), (
        f"the refusal does not say how much it read and could not keep: {excinfo.value}"
    )
    assert ".theurian/review/" in str(excinfo.value)
    assert "theurian review build" in excinfo.value.remedy
    assert "review ingest" in excinfo.value.remedy, (
        "the cure does not name the concurrent writer, so an operator reads a corrupted "
        "corpus where the real cause is two runs overlapping"
    )
    assert _every_stored_value(store) == before, (
        "the build that refused changed what is stored: refusing is what keeps the "
        "previous store serving, and it must not have published on the way out"
    )

    rebuilt, second = _build(paths, evidence, withheld=frozenset())

    assert second == {"records": 2, "withheld": 0}, (
        "the next build did not converge on the corpus the refusal was about, so the "
        "guard wedges a project rather than costing it one build"
    )
    assert "Retitled upstream" in _every_stored_value(rebuilt)


def test_a_corpus_emptied_on_purpose_publishes_the_empty_store(tmp_path: Path) -> None:
    """The lawful arm of the same guard, and the reason it is keyed on the read.

    Deleting evidence files is the retention remedy ADR-0030 decision 3 leaves an
    operator, and deleting *all* of them is a corpus of nothing -- which a build
    must publish, or the store goes on serving records whose files an operator
    took out. That is the case the guard must not catch: the refusal above is
    about a build whose own read is entirely stale, and a read that finds zero
    records is not stale, it is an answer about the disk.

    Written as the second half of a build that had published rows, so the empty
    publish is observably a *replacement* and not a first build that happened to
    have nothing to do.
    """
    paths = _project(tmp_path)
    evidence = _landed(paths, _event(), _thread())
    store, first = _build(paths, evidence, withheld=frozenset())
    assert first == {"records": 2, "withheld": 0}
    for landed in paths.review.rglob("*.json"):
        landed.unlink()

    emptied, report = _build(paths, evidence, withheld=frozenset())

    assert report == {"records": 0, "withheld": 0}
    assert emptied.dump() == (), (
        "the store still serves records whose evidence files an operator deleted, which "
        "is the retention remedy decision 3 leaves them, silently undone"
    )
    assert "Bound the retry budget" not in _every_stored_value(store)


def test_a_build_that_keeps_some_of_what_it_read_publishes_the_rest(tmp_path: Path) -> None:
    """The guard's other boundary: a partial drop is not a refusal.

    Two records, one of them rewritten inside the write section. The rewritten one
    is dropped -- that is
    ``test_a_record_rewritten_between_the_read_and_the_publish_is_not_republished``'s
    claim -- and the *other* one must still be published, because a guard that
    fired on any drop at all would refuse every ordinary build that raced a
    single-record refetch and leave a store frozen at whatever it last held.

    RED here means the refusal's condition is "something was dropped" rather than
    "nothing survived".
    """
    paths = _project(tmp_path)
    evidence = _landed(paths, _event(), _thread())

    def refetch_one_record() -> None:
        evidence.write((_retitled(),), run=RUN)

    store, report = _build(
        paths,
        evidence,
        withheld=frozenset(),
        write_section=_write_section_that(refetch_one_record),
    )

    assert report == {"records": 1, "withheld": 0}
    stored = _every_stored_value(store)
    assert "This retries forever." in stored, "the record nothing touched was dropped too"
    assert "Bound the retry budget" not in stored


def test_a_build_that_withheld_everything_it_read_publishes_the_empty_store(
    tmp_path: Path,
) -> None:
    """RED means the refusal itself says that a withheld record exists.

    The guard counts what survived *withholding*, not what was read, and this is
    the case that difference is for. A build asked to withhold every record it
    read has nothing to publish for a reason the caller chose; refusing there
    would make the refusal a signal that there was something to withhold --
    exactly the bit :attr:`ReviewSearchBuildRequest.withheld_record_keys`' physical
    absence exists to keep out of the store, arriving through an error instead.

    So this build publishes the empty store, and what it publishes is
    indistinguishable from the empty corpus above: same rows, same counts but for
    ``withheld``, which is a function of the caller's own set.
    """
    paths = _project(tmp_path)
    evidence = _landed(paths, _event(), _thread())
    # A pull request is keyed by its number and a thread by its node id --
    # `EvidenceRecord.record_key`'s two arms, spelled here rather than derived so
    # a key that changed shape reddens the premise below rather than quietly
    # withholding nothing.
    every_key = frozenset({"42", "PRRT_kwDO_test_node_0001"})

    store, report = _build(paths, evidence, withheld=every_key)

    assert report == {"records": 0, "withheld": 2}, (
        "the premise: both records have to be withheld, or this is not the all-withheld "
        "case the guard has to let through"
    )
    assert store.dump() == ()


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
        pytest.param("9" * 20, "wider than", id="beyond-the-column"),
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


#: Event keys whose trailing run is digits to Unicode and not to ASCII, with what
#: an ``int`` would make of each. Every one is matched by ``\\d`` without
#: ``re.ASCII``, and ``int`` converts all four -- so a pattern that dropped the
#: flag would store a pull-request number **no provider ever issued**, silently
#: and with no refusal anywhere.
#:
#: Four rather than one, because they are four different Unicode decimal families
#: and a narrowing that admitted only some of them is the same defect:
#: Arabic-Indic at one digit and at three, fullwidth Latin, and Lepcha.
_NON_ASCII_DIGIT_KEYS: Final[tuple[tuple[str, str, int], ...]] = (
    ("arabic-indic-one-digit", "٣", 3),
    # RUF001 is suppressed rather than obeyed: the fullwidth digits are
    # *confusable with ASCII ones*, which is the property under test rather than a
    # typo, and an escape would hide the very thing an operator sees in a
    # hand-edited file.
    ("fullwidth", "１２", 12),  # noqa: RUF001
    ("arabic-indic-three-digits", "٩٨٧", 987),
    ("lepcha", "᱇", 7),
)


@pytest.mark.parametrize(
    ("suffix", "would_be"),
    [(case[1], case[2]) for case in _NON_ASCII_DIGIT_KEYS],
    ids=[case[0] for case in _NON_ASCII_DIGIT_KEYS],
)
def test_a_non_ascii_digit_in_an_event_key_names_no_pull_request(
    tmp_path: Path, suffix: str, would_be: int
) -> None:
    """``_EVENT_KEY_NUMBER``'s ``re.ASCII`` flag, which nothing drove.

    The pattern's own note says why the flag is there: ``\\d`` matches every
    Unicode decimal digit without it, ``int`` accepts them all, and the result
    would be a pull-request number no provider issued -- attached to a real
    record, filterable, and indistinguishable on the wire from one GitHub gave.
    The premise is measured rather than argued: each suffix really does convert,
    so a pattern without the flag really would produce that number.

    A landed file's ``eventKey`` is hand-edited, for the reason the sibling cases
    give: that string is the only place such a number can enter, because the
    shipped writer builds the key from an ``int``.

    The correct answer is ``None`` rather than a refusal. A key that does not match
    the one format the shipped writer produces names no pull request, and the
    record is still review evidence worth serving under every other filter -- so
    the build succeeds and the column is empty.
    """
    assert int(suffix) == would_be, (
        "the premise: `int` really converts this run, so a pattern without `re.ASCII` "
        "really would store it as a pull-request number"
    )
    paths = _project(tmp_path)
    evidence = _landed(paths, _submission())
    _with_event_key(paths, suffix)

    store, report = _build(paths, evidence, withheld=frozenset())

    assert report == {"records": 1, "withheld": 0}, (
        "the build must still succeed: a key that names no pull request is not a damaged "
        "record, and refusing it would drop real review evidence"
    )
    (stored,) = store.dump()
    assert stored.pull_request is None, (
        f"a non-ASCII digit run became pull request {stored.pull_request}, a number no "
        f"provider issued -- and one a caller can filter on as though it had"
    )


@pytest.mark.parametrize(
    "run",
    [
        pytest.param("9" * (sys.get_int_max_str_digits() + 1), id="all-nines"),
        pytest.param("0" * (sys.get_int_max_str_digits() + 1) + "1", id="leading-zeros"),
    ],
)
def test_the_over_range_refusal_never_renders_the_number_it_refuses(
    tmp_path: Path, run: str
) -> None:
    """RED means composing the refusal is itself the next crash.

    ``_pull_request_of`` parses ``#(\\d+)``, so a hand-edited event key can carry
    a number of any length, and CPython refuses to render an integer wider than
    ``sys.get_int_max_str_digits()`` -- 4,300 digits by default. A refusal that
    quoted the caller's number back would raise ``ValueError`` *inside the arm
    building it*, which is the face ``mcp/findings._digits`` met. The bound is
    named instead, which is the part a reader acts on.

    **Two runs of the same width, and the second is #630's HIGH-1.** The guard
    measured ``digits.lstrip("0")`` while ``int`` counts every character in the
    run, so a run of 4,301 zeros and a ``1`` presented the guard with one
    significant digit, passed it, and reached ``int`` whole: ``ValueError:
    Exceeds the limit (4300 digits) for integer string conversion``, outside
    ``TheurianError`` and therefore outside every arm ``theurian review ingest``
    and ``theurian review build`` grade. Both runs now stop at the same line,
    which is why the refusal names the **width** rather than a magnitude -- a run
    of zeros is not larger than anything.
    """
    paths = _project(tmp_path)
    evidence = _landed(paths, _submission())
    _with_event_key(paths, run)

    with pytest.raises(ReviewSearchBuildError) as excinfo:
        _build(paths, evidence, withheld=frozenset())

    assert f"run of {len(run)} digits" in str(excinfo.value), (
        f"the refusal does not say how wide the run was: {excinfo.value}"
    )
    assert str(MAX_STORED_PULL_REQUEST) in str(excinfo.value)
    for rendered in ("9" * 100, "0" * 100):
        assert rendered not in str(excinfo.value), (
            "the refusal rendered the run it refuses, which is the crash it exists to avoid"
        )


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
