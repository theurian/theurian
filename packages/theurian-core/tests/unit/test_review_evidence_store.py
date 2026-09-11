"""Evidence files are the source, and they land inside the project (ADR-0030 decision 3).

Five claims, and each fails on its own:

* **The round trip loses nothing.** The files *are* the record -- upstream
  comments are editable and deletable, so a field dropped on the way to disk is
  evidence no refetch recovers. The assertions compare whole frozen dataclasses
  rather than field subsets, so a field added to ``domain/review.py`` and
  forgotten in the codec fails here.
* **A refetch never deletes.** Run two writes what upstream still returns; the
  record it no longer returns keeps its file, byte for byte, carrying run one's
  stamp.
* **A hostile identifier lands contained.** The published allowlist pattern
  accepts ``../..``, so a repository name joined into a path escapes while
  satisfying the contract. Both the repository identity and the provider's own
  record id are driven with traversal-shaped values here.
* **A planted symbolic link is refused, not followed.** Containment answers where
  a path points; the route walk answers how it got there; ``O_NOFOLLOW`` answers
  the leaf. Each is driven with a plant that only it catches.
* **Two records that differ get two files, on a filesystem that folds case too.**
  macOS and Windows fold by default, so a layout and a collision guard that only
  keep byte spellings apart lose a record to a silent overwrite and then refuse
  the whole corpus. Every assertion about distinctness here is therefore made
  between *folded* names, which is the comparison those filesystems answer with.

Marked ``unit`` and writes only under ``tmp_path``. Nothing here touches this
repository's own ``.theurian/``.
"""

from __future__ import annotations

import json
import os
import string
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest

from theurian.application.project_service import ProjectPaths
from theurian.domain.enums import ReviewCommentCategory, ReviewThreadState
from theurian.domain.errors import DomainError, InvariantViolationError, PathEscapeError
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
from theurian.infrastructure.review_evidence import (
    EVIDENCE_FORMAT_VERSION,
    EvidenceKind,
    EvidenceReader,
    EvidenceRecord,
    IngestionRun,
    ReviewEvidenceError,
    ReviewEvidenceStore,
    new_ingestion_run,
    record_leaf,
    repository_directory,
)
from theurian.infrastructure.review_evidence import records as records_module
from theurian.infrastructure.review_evidence import store as store_module
from theurian.infrastructure.review_evidence.layout import (
    _FILESYSTEM_SAFE,
    _HASHED_PREFIX,
    EVIDENCE_SUFFIX,
)
from theurian.security.paths import MAX_SOURCE_FILE_BYTES

pytestmark = pytest.mark.unit

_NEEDS_SYMLINKS = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)

PROJECT = ProjectId("demo")
PROVIDER: Final = "github"
REPOSITORY: Final = "acme/order-service"

RUN_ONE: Final = IngestionRun("01K1AAAAAA01234567890ABCDE", datetime(2026, 9, 7, 9, 0, tzinfo=UTC))
RUN_TWO: Final = IngestionRun("01K1BBBBBB01234567890ABCDE", datetime(2026, 9, 8, 9, 0, tzinfo=UTC))


def _store(tmp_path: Path) -> ReviewEvidenceStore:
    """A store over a project's real ``ProjectPaths.review``.

    Composed the way an ingestion run would compose it rather than from a bare
    directory, so the containment the helper applies to ``.theurian/review``
    itself is exercised beside the per-record containment this module drives.
    """
    root = tmp_path / "repo"
    (root / ".theurian").mkdir(parents=True)
    return ReviewEvidenceStore(ProjectPaths.of(root).review)


def _review_root(tmp_path: Path) -> Path:
    return tmp_path / "repo" / ".theurian" / "review"


def _participant(name: str = "Reviewer One") -> ReviewParticipant:
    return ReviewParticipant(provider=PROVIDER, external_id="MDQ6VXNlcjE=", display_name=name)


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


def _event(number: int = 42, repository: str = REPOSITORY) -> EvidenceRecord:
    return EvidenceRecord(
        provider=PROVIDER,
        repository=repository,
        anchor=_anchor(f"https://github.com/{repository}/pull/{number}"),
        payload=ReviewEvent(
            project_id=PROJECT,
            provider=PROVIDER,
            repository=repository,
            number=number,
            title="Bound the retry budget",
            body="署名付きトークンを持つ呼び出しだけを再試行する。",
            author=_participant(),
            created_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            url=f"https://github.com/{repository}/pull/{number}",
            head_commit="b" * 40,
            base_commit="c" * 40,
            head_ref_name="fix/retry-budget",
            labels=("security", "needs-review"),
            merged=True,
            merge_commit="d" * 40,
            merged_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
            ci_successful=True,
            linked_issue_ids=("I_kwDO1", "I_kwDO2"),
            milestone="0.2.0",
        ),
    )


def _submission(external_id: str = "PRR_kwDOABCD1") -> EvidenceRecord:
    return EvidenceRecord(
        provider=PROVIDER,
        repository=REPOSITORY,
        anchor=_anchor(f"https://github.com/{REPOSITORY}/pull/42#pullrequestreview-1"),
        payload=ReviewSubmission(
            external_id=external_id,
            project_id=PROJECT,
            event_key=f"{PROVIDER}:{REPOSITORY}#42",
            author=_participant("Reviewer Two"),
            body="Approving; the budget is now bounded.",
            state="APPROVED",
            submitted_at=datetime(2026, 8, 1, 15, 0, tzinfo=UTC),
        ),
    )


def _thread(
    external_id: str = "PRRT_kwDOABCD1", file_path: str | None = "src/order.py"
) -> EvidenceRecord:
    return EvidenceRecord(
        provider=PROVIDER,
        repository=REPOSITORY,
        anchor=_anchor(f"https://github.com/{REPOSITORY}/pull/42#discussion_r1"),
        payload=ReviewThread(
            external_id=external_id,
            project_id=PROJECT,
            event_key=f"{PROVIDER}:{REPOSITORY}#42",
            file_path=file_path,
            comments=(
                ReviewComment(
                    external_id="IC_kwDO1",
                    author=_participant(),
                    body="This retries forever.",
                    created_at=datetime(2026, 8, 1, 13, 0, tzinfo=UTC),
                    category=ReviewCommentCategory.RELIABILITY_RULE,
                    line_start=10,
                    line_end=12,
                ),
                ReviewComment(
                    external_id="IC_kwDO2",
                    author=_participant("Reviewer Two"),
                    body="Fixed in b1c2d3.",
                    created_at=datetime(2026, 8, 1, 14, 0, tzinfo=UTC),
                ),
            ),
            state=ReviewThreadState.RESOLVED,
            resolution=ReviewResolution(
                state=ReviewThreadState.RESOLVED,
                resolved_by=_participant("Reviewer Two"),
                resolved_at=None,
                fix_commit="e" * 40,
            ),
            line_start=10,
            line_end=12,
            commit_sha="f" * 40,
        ),
    )


# -- the round trip -----------------------------------------------------------


def test_every_record_kind_reads_back_as_the_object_that_was_written(tmp_path: Path) -> None:
    """Owed test 4's testable half: write, read back, get the same records.

    Whole-object equality, so a field the codec forgot fails here rather than
    passing on the subset somebody remembered. The event body is Japanese on
    purpose: these files are read by people in a diff, and a codec that escaped
    non-ASCII would still round-trip while making the artifact unreadable.

    The store-rebuild half of the same obligation -- delete the derived SQLite
    store, rebuild it, and the served content reproduces -- needs slice 3's store
    and completes there.
    """
    store = _store(tmp_path)
    written = (_event(), _submission(), _thread())

    store.write(written, run=RUN_ONE)
    read_back = store.read_all()

    assert tuple(stored.record for stored in read_back) == tuple(
        sorted(written, key=lambda record: record.relative_path)
    )
    assert {stored.last_seen for stored in read_back} == {RUN_ONE}


def test_the_landed_tree_is_readable_and_names_the_repository_inside(tmp_path: Path) -> None:
    """The layout a human meets when the directory is committed.

    A hashed repository directory, a kind directory spelled as the enum spells
    it, and a leaf named after the provider's own key -- ``42.json`` for pull
    request 42. The repository's real name is inside the file, because the
    directory cannot carry it: joining ``owner/repo`` into a path is what
    ADR-0030 decision 3 forbids.
    """
    store = _store(tmp_path)

    (landed,) = store.write([_event(number=42)], run=RUN_ONE)

    assert landed.endswith("/pull-request/42.json"), landed
    document = json.loads((_review_root(tmp_path) / landed).read_text(encoding="utf-8"))
    assert document["formatVersion"] == EVIDENCE_FORMAT_VERSION
    assert document["repository"] == REPOSITORY
    assert document["kind"] == EvidenceKind.PULL_REQUEST.value
    assert REPOSITORY not in landed


def test_a_thread_with_no_file_path_round_trips_as_none(tmp_path: Path) -> None:
    """A thread on the pull request rather than on a line has no file, and says so."""
    store = _store(tmp_path)
    record = _thread(file_path=None)

    store.write([record], run=RUN_ONE)

    assert store.read_all()[0].record == record


def test_a_store_over_a_directory_that_does_not_exist_reads_as_empty(tmp_path: Path) -> None:
    """A project that has never ingested is not an error; it has no records."""
    assert _store(tmp_path).read_all() == ()


def test_a_review_path_that_exists_and_is_not_a_directory_refuses(tmp_path: Path) -> None:
    """RED means a corpus this build cannot enumerate is reported as an empty one.

    The two conditions used to be one ``is_dir``: an absent review directory and
    a review *path* occupied by something else both answered "no records". Only
    the first of those is honest. The second is a tree this build cannot walk --
    ``theurian review build`` published a store with nothing in it, ``review
    ingest``'s landed-key read answered the empty set, and both exited 0 -- while
    the reader's own ``Raises`` clause said a directory that cannot be listed
    refuses.

    The cure is held to the shape this package's cures are held to at the same
    time: it names the path, it names a command that prints what is standing
    there, and it offers a **move** rather than a deletion, because nothing at
    this seam can tell what the occupying object holds or whose it is.
    """
    root = tmp_path / "repo"
    (root / ".theurian").mkdir(parents=True)
    _review_root(tmp_path).write_text("not a directory", encoding="utf-8")
    store = ReviewEvidenceStore(ProjectPaths.of(root).review)

    with pytest.raises(ReviewEvidenceError) as refused:
        store.read_all()

    assert "not a directory" in str(refused.value)
    assert "ls -ld .theurian/review" in refused.value.remedy
    assert "Do not delete it" in refused.value.remedy
    # The verb the reader ran, not the verb the sibling cures were written for.
    # This refusal comes off the *read* walk, so `theurian review build` reaches
    # it as readily as `review ingest` does -- and a real run met it through the
    # first of those.
    assert "theurian review build" in refused.value.remedy


# -- fingerprints: what a listing can tell without reading a file -------------


def test_a_fingerprint_moves_when_a_record_is_rewritten(tmp_path: Path) -> None:
    """RED means a rebuild cannot tell a refetched record from an untouched one.

    ``ReviewSearchBuilder`` publishes a record only where the fingerprint taken
    before its read equals the one taken at the publish, so what that comparison
    can *see* is what decides whether a store can serve a body the evidence file
    no longer carries. This is the producer's half: the same path, rewritten,
    answers a different triple.

    Asserted on the whole triple rather than on ``mtime_ns`` alone, because the
    claim the builder rests on is "the fingerprints differ" and narrowing it here
    to one slot would make this test about a field rather than about that claim.
    """
    store = _store(tmp_path)
    original = _event(number=42)
    store.write([original], run=RUN_ONE)
    reader = EvidenceReader(_review_root(tmp_path))
    before = dict(reader.fingerprints())
    assert isinstance(original.payload, ReviewEvent)

    store.write(
        [replace(original, payload=replace(original.payload, title="Retitled upstream"))],
        run=RUN_TWO,
    )

    after = dict(reader.fingerprints())
    assert sorted(before) == sorted(after), "the rewrite landed somewhere else entirely"
    assert before != after, (
        "a rewritten record carries the fingerprint it had before, so a rebuild would "
        "publish the body its read took as unchanged"
    )


def test_a_fingerprint_says_when_a_leaf_stopped_being_a_regular_file(tmp_path: Path) -> None:
    """RED means the third slot is something nothing computes.

    The listing selects a leaf by the suffix of its **name**, and a directory may
    be named ``42.json`` as easily as a file may -- so the fingerprint carries
    whether the leaf is a regular file, and this is what holds that it is
    computed rather than constant. A builder-level case cannot: a directory put
    where a file was carries its own ``mtime_ns``, its own size and its own inode
    number, so the record is dropped whether the slot is honest or hardcoded
    ``True``.

    Both directions, so a producer that answered ``False`` for everything would
    fail here too.
    """
    store = _store(tmp_path)
    store.write([_event(number=42)], run=RUN_ONE)
    reader = EvidenceReader(_review_root(tmp_path))
    (path,) = reader.fingerprints()
    leaf = _review_root(tmp_path) / path

    assert reader.fingerprints()[path][3] is True, "a landed record is not a regular file"

    leaf.unlink()
    leaf.mkdir()

    assert reader.fingerprints()[path][3] is False, (
        "a directory standing where a record was still reads as a regular file, so the "
        "slot is not asked of the filesystem"
    )


def test_a_fingerprint_moves_when_a_record_is_rewritten_with_its_timestamp_restored(
    tmp_path: Path,
) -> None:
    """RED means a restored timestamp hides a rewrite, which is what the inode slot is for.

    The producer's half of the fourth slot. ``mtime_ns`` and ``size`` witness a
    rewrite only while nobody puts the timestamp back, and ``os.utime`` puts it
    back to the nanosecond -- so a same-length rewrite followed by a restoration
    is a changed file those two call unchanged, on a filesystem with the finest
    timestamps there are.

    The write shape is the one this store itself uses: a sibling temporary and a
    rename, which resolves the name to a new inode. The premise is measured rather
    than argued -- the first two slots really do come back identical here, so a
    listing without the inode really would publish this file as unchanged.
    """
    store = _store(tmp_path)
    store.write([_event(number=42)], run=RUN_ONE)
    reader = EvidenceReader(_review_root(tmp_path))
    (path,) = reader.fingerprints()
    leaf = _review_root(tmp_path) / path
    before_stat = leaf.stat()
    before = reader.fingerprints()[path]

    original = leaf.read_bytes()
    edited = original.replace(b"Bound the retry budget", b"Overwritten upstream!!")
    assert edited != original and len(edited) == before_stat.st_size, (
        "the plant has to change the bytes and keep the length, or the size slot answers "
        "and this case measures nothing about the inode"
    )
    sibling = leaf.with_name(leaf.name + ".hostile")
    sibling.write_bytes(edited)
    sibling.replace(leaf)
    os.utime(leaf, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))

    after = reader.fingerprints()[path]
    assert after[:2] == before[:2], (
        "the premise: this platform did not restore the timestamp exactly, so the case is "
        "not measuring the attack it names"
    )
    assert after != before, (
        "a rewritten record carries the fingerprint it had before, so a rebuild would "
        "publish the body its read took as unchanged"
    )


@_NEEDS_SYMLINKS
def test_an_unstattable_leaf_answers_a_fingerprint_no_real_leaf_can(tmp_path: Path) -> None:
    """RED means a leaf whose ``stat`` was refused can read as an unchanged file.

    ``_fingerprint`` answers a sentinel rather than raising, because a dangling
    symbolic link under the review directory must not turn a whole listing into a
    refusal -- that listing runs under the project's write lock. What makes
    answering safe is that the sentinel is a value **no real leaf can carry**: a
    record that is a file at one capture and unstattable at the other is then
    dropped by the same equality every other transition goes through, with no arm
    of its own.

    The control is the extreme a real leaf can actually reach rather than a
    comfortable one. ``os.utime(ns=(-1, -1))`` gives a real regular file
    ``st_mtime_ns == -1``, which is the sentinel's own first slot -- so the
    distinctness rests on the two a real leaf cannot make negative, ``st_size``
    and ``st_ino``, and it is those that are asserted. A sentinel edited to values
    a ``stat`` could answer reddens here.
    """
    store = _store(tmp_path)
    store.write([_event(number=42)], run=RUN_ONE)
    root = _review_root(tmp_path)
    reader = EvidenceReader(root)
    (record_path,) = reader.fingerprints()
    kind_directory = (root / record_path).parent
    prefix = record_path.rsplit("/", 1)[0]

    before_the_epoch = kind_directory / "43.json"
    before_the_epoch.write_bytes(b"")
    os.utime(before_the_epoch, ns=(-1, -1))
    (kind_directory / "44.json").symlink_to(root / "nothing-is-here.json")

    fingerprints = reader.fingerprints()
    extreme = fingerprints[f"{prefix}/43.json"]
    refused = fingerprints[f"{prefix}/44.json"]

    assert extreme[0] == -1, (
        "the premise: a real leaf can answer the sentinel's first slot, which is why the "
        "distinctness may not rest on the timestamp"
    )
    assert refused != extreme and refused != fingerprints[record_path], (
        "the refused leaf's fingerprint equals one a real leaf carries, so a file that "
        "became unstattable between the two captures would be published as unchanged"
    )
    for slot, name in ((1, "st_size"), (2, "st_ino")):
        assert refused[slot] < 0, f"the sentinel's {name} slot is a value a `stat` can answer"
        for key, real in fingerprints.items():
            if key != f"{prefix}/44.json":
                assert real[slot] >= 0, f"a real leaf answered a negative {name}"


def test_read_all_answers_in_one_order_whatever_order_the_records_arrived(
    tmp_path: Path,
) -> None:
    """Determinism: slice 3 builds a store from this sequence on every machine."""
    store = _store(tmp_path)
    forwards = (_thread("PRRT_a"), _thread("PRRT_b"), _thread("PRRT_c"))

    store.write(reversed(forwards), run=RUN_ONE)

    assert [stored.relative_path for stored in store.read_all()] == sorted(
        record.relative_path for record in forwards
    )


# -- refetch ------------------------------------------------------------------


def test_a_record_upstream_no_longer_returns_survives_the_refetch(tmp_path: Path) -> None:
    """AC-5, decision 3: a later run updates what it sees and deletes nothing.

    Run one lands two threads. Run two sees only the first -- the second was
    deleted upstream, which GitHub permits and no refetch undoes. The vanished
    record keeps its file **byte for byte**, carrying run one's stamp, while the
    surviving one's stamp advances. Comparing the bytes rather than the parsed
    record is what makes "untouched" observable: a writer that rewrote the file
    with an identical payload and a new stamp would pass an equality on the
    record and fail here.
    """
    store = _store(tmp_path)
    surviving, vanishing = _thread("PRRT_survives"), _thread("PRRT_vanishes")
    store.write([surviving, vanishing], run=RUN_ONE)
    before = (_review_root(tmp_path) / vanishing.relative_path).read_bytes()

    store.write([surviving], run=RUN_TWO)

    stamps = {stored.record.relative_path: stored.last_seen for stored in store.read_all()}
    assert stamps[vanishing.relative_path] == RUN_ONE
    assert stamps[surviving.relative_path] == RUN_TWO
    assert (_review_root(tmp_path) / vanishing.relative_path).read_bytes() == before


def test_a_refetch_rewrites_a_record_whose_content_changed_upstream(tmp_path: Path) -> None:
    """The positive control on the test above: an update is an update.

    Without it, a store that wrote nothing at all on the second run would pass
    the survival test and lose every refresh.
    """
    store = _store(tmp_path)
    original = _event(number=42)
    store.write([original], run=RUN_ONE)
    assert isinstance(original.payload, ReviewEvent)
    edited = replace(original, payload=replace(original.payload, title="Retitled upstream"))

    store.write([edited], run=RUN_TWO)

    (stored,) = store.read_all()
    assert isinstance(stored.record.payload, ReviewEvent)
    assert stored.record.payload.title == "Retitled upstream"
    assert stored.last_seen == RUN_TWO


# -- containment --------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "repository"),
    (
        ("a traversal-shaped repository", "../../escape"),
        ("an absolute-looking repository", "/etc/passwd"),
        ("a repository that is a parent segment", ".."),
    ),
)
def test_a_hostile_repository_identity_lands_inside_the_review_directory(
    tmp_path: Path, label: str, repository: str
) -> None:
    """AC-4: the configured name never becomes a path, so it cannot leave one.

    The published pattern for ``providers.review.repositories`` accepts ``../..``
    -- which is why decision 3 forbids joining it -- so these are values that
    satisfy the contract and would escape a naive writer. The assertion is on the
    filesystem: every file the run created is inside the review directory, and
    the escape target does not exist.
    """
    store = _store(tmp_path)

    (landed,) = store.write([_event(number=7, repository=repository)], run=RUN_ONE)

    review = _review_root(tmp_path)
    assert ".." not in landed.split("/"), label
    written = [path for path in review.rglob("*") if path.is_file()]
    assert [path for path in written if not path.resolve().is_relative_to(review.resolve())] == []
    assert not (tmp_path / "escape").exists()
    assert (review / landed).is_file()


@pytest.mark.parametrize(
    ("label", "external_id"),
    (
        ("a traversal", "../../../etc/passwd"),
        ("a bare parent", ".."),
        ("a hidden file", ".ssh"),
        ("a separator", "a/b"),
        ("a NUL", "a\x00b"),
        ("a newline", "a\nb"),
        ("a name that is only dots", "..."),
        ("something that already looks hashed", "sha256-deadbeef"),
        ("something that looks hashed, shouted", "SHA256-deadbeef"),
    ),
)
def test_a_hostile_provider_id_lands_inside_the_review_directory(
    tmp_path: Path, label: str, external_id: str
) -> None:
    """AC-4's other half: the id the provider sent is not trusted to be a name.

    A GraphQL response is untrusted input, so an id is charset-checked before it
    is spelled into a filename and hashed when it fails. The last two cases are
    the escape-prefix rule: an id that already looks like a hash is hashed
    anyway, in either spelling, because the prefix is matched against the id's
    casefold.
    """
    store = _store(tmp_path)

    (landed,) = store.write([_thread(external_id=external_id)], run=RUN_ONE)

    review = _review_root(tmp_path)
    assert landed.count("/") == 2, f"{label}: the id opened a directory of its own"
    assert (review / landed).is_file()
    assert (review / landed).resolve().is_relative_to(review.resolve())
    assert store.read_all()[0].record.payload == _thread(external_id=external_id).payload


def test_two_ids_that_differ_only_in_case_land_as_two_files(tmp_path: Path) -> None:
    """H-E's own shape, asserted on the disk rather than on the return value.

    Before the layout carried a case tag this run reported two landings and left
    one file: the second record was written over the first by the filesystem, the
    file then named a record whose own derived path was the *other* spelling, and
    every later ``read_all`` refused the whole corpus over it. So the assertion
    that matters is the equality between what ``write`` says it landed and what is
    on the disk -- the count of files, not the count of paths returned.

    On a filesystem that folds case this reddens the moment two leaves fold
    together again; on one that does not, the file count passes on its own and
    the folded-name assertion is what carries the claim. Both are asserted so the
    test means something on either.
    """
    store = _store(tmp_path)

    landed = store.write([_thread("PRRT_kwDOAbc"), _thread("prrt_kwdoabc")], run=RUN_ONE)

    on_disk = list(_review_root(tmp_path).rglob(f"*{EVIDENCE_SUFFIX}"))
    assert len(landed) == len(on_disk) == 2
    assert len({path.casefold() for path in landed}) == 2
    assert {stored.record.record_key for stored in store.read_all()} == {
        "PRRT_kwDOAbc",
        "prrt_kwdoabc",
    }


def test_a_provider_id_that_is_already_a_name_is_spelled_out_rather_than_hashed() -> None:
    """The positive control on the hashing rule.

    Without it the rule could hash everything and pass every containment test
    while making the committed tree unreadable -- which is the property the
    verbatim arm exists for. GitHub's current node ids are the shape driven here,
    and they are mixed-case, so what the arm preserves is the *spelling*: the id
    is still there to read and to grep, followed by the case tag that keeps it
    apart from its own case-variants on a filesystem that folds them together.

    The expectations are written out rather than recomputed. A tag derived here
    from the function under test would agree with it however wrong both were.

    **The trailing newline is the charset's anchor, driven at the behaviour.**
    ``_FILESYSTEM_SAFE`` ends in ``\\Z`` and not ``$``, because Python's ``$``
    also matches immediately before a trailing newline -- so ``"PRRT_abc\\n"``
    would satisfy the verbatim arm and be spelled into a filename with a line
    break in it. ``test_the_verbatim_charset_folds_one_ascii_character_at_a_time``
    already reddens on that anchor from the charset's side (its ``following``
    count goes from 65 to 66, measured); this is the same guard stated as what
    the caller receives.
    """
    assert record_leaf("PRRT_kwDOABCD1M5abcde") == "PRRT_kwDOABCD1M5abcde~5f8f.json"
    assert record_leaf("prrt_kwdoabcd1m5abcde") == "prrt_kwdoabcd1m5abcde.json"
    assert record_leaf("42") == "42.json"
    assert record_leaf("../../etc/passwd").startswith("sha256-")
    assert record_leaf("sha256-anything").startswith("sha256-")
    assert record_leaf("PRRT_abc\n").startswith("sha256-"), (
        "an id ending in a newline was spelled into a leaf verbatim; the charset "
        "is anchored with `\\Z` precisely so that `$` cannot let one through"
    )


def test_no_provider_id_can_be_made_to_name_another_ids_file() -> None:
    """``layout.py``'s escape-prefix rule, driven from the impostor's side.

    Every other case here drives one id at a time and so holds only one half of
    that claim: they say a hostile id is hashed, never that a second id cannot be
    spelled to land on the first one's file.

    The attack the claim forbids is exactly that. Read the leaf a hashed id
    landed under, hand that leaf back to the store as some other record's
    provider id, and see where it goes: it is inside the verbatim arm's charset
    and length, so the **escape prefix alone** is what sends it to be hashed. The
    ``lookalike`` control is the same string with the prefix spelled one digit
    off, which the verbatim arm accepts -- so a failure here is the prefix rule
    and not the charset.

    The shouted spelling is the same attack one ``casefold`` further out, and it
    is the face that was live: ``SHA256-<hex>`` passed the charset, missed a
    case-sensitive prefix test, and was written out verbatim, which on macOS and
    Windows is the hashed leaf's own file. The comparison is between *folded*
    names for that reason -- byte inequality is not what those two filesystems
    answer.

    Two ids naming one file is not caught downstream: the store refuses a
    collision only *within* one run, and these two arrive in different ones.
    """
    victim = record_leaf("../../etc/passwd")
    digest = victim.removesuffix(EVIDENCE_SUFFIX).removeprefix("sha256-")
    impostor = f"sha256-{digest}"
    shouting = f"SHA256-{digest}"
    lookalike = f"sha257-{digest}"

    landed = record_leaf(impostor)

    assert landed != victim, "an id spelled as another id's leaf named that id's file"
    assert landed.startswith("sha256-"), "the impostor was not sent down the hashing arm"
    assert record_leaf(shouting).casefold() != victim.casefold(), (
        "a shouted hashed prefix named the hashed leaf's own file where case folds"
    )
    assert record_leaf(shouting).startswith(_HASHED_PREFIX), (
        "a shouted hashed prefix took the verbatim arm, so a leaf outside the hashed "
        "family folds onto the hashed prefix and the module's argument across the families "
        "stops holding"
    )
    assert record_leaf(lookalike) == f"{lookalike}{EVIDENCE_SUFFIX}", (
        "the control: this shape is one the verbatim arm accepts, so what sent the "
        "impostor to be hashed was the prefix rather than its charset or its length"
    )


#: Ids whose leaves must stay apart on a filesystem that folds case, covering
#: every pair the layout's argument ranges over. Verbatim against verbatim (the
#: numbers), tagged against tagged and against its own folded spelling (the H-E
#: face), hashed against hashed, and each family against the other two --
#: including an id spelled as another id's *tagged* leaf, which the separator is
#: chosen to make impossible to reach.
#:
#: The last pair looks arbitrary and is not: those two spellings share the first
#: eight hex digits of their SHA-256, so they are what a ``sha256[:8]`` case tag
#: would merge. They were found in 0.01 s by walking the case variants of
#: ``PRRT_kwDOABCD1M5abcde`` -- a 32-bit birthday search -- which is why the tag
#: is an exact mask and not a short digest. Their presence here is what makes
#: that a tested property rather than a remembered argument.
_IDS_THAT_MUST_NOT_SHARE_A_LEAF: Final = (
    "PRRT_kwDOAbc",
    "prrt_kwdoabc",
    "PRRT_KWDOABC",
    "pRRT_kwDOAbC",
    "PRRT_kwDOAbc~38f",
    "PRRT_kwDOAbc~38f.json",
    "42",
    "43",
    "sha256-deadbeef",
    "SHA256-deadbeef",
    "Sha256-DeadBeef",
    "sha257-deadbeef",
    "SHA257-deadbeef",
    "../../etc/passwd",
    "..",
    "a/b",
    "Prrt_kWDoabcd1M5abcde",
    "Prrt_KwDoabCd1M5abcde",
)


def test_no_two_ids_share_a_leaf_on_a_case_insensitive_filesystem() -> None:
    """The layout's universal, keyed on the population above.

    ``record_leaf`` is injective on bytes and always was; what H-E showed is that
    macOS and Windows do not compare bytes. So the assertion is on the *folded*
    leaves, which is the equality those filesystems answer with, and it holds on
    a case-sensitive one too -- there it is simply the weaker statement.

    The second assertion is the one the *stated argument* rests on rather than
    the outcome: the module separates the hashed family from the other two by
    the prefix, and a reader -- or slice 3 -- that keys on ``sha256-`` is
    entitled to that. A shouted spelling left in the verbatim arm would fold onto
    the prefix while not being a hash, which the distinctness assertion above
    does not notice because the case tag keeps such a name distinct anyway.

    The third is the control on the population rather than on the function: a
    sweep that happened to drive one arm would pass while saying nothing about
    the pairs that cross two.
    """
    leaves = [record_leaf(identifier) for identifier in _IDS_THAT_MUST_NOT_SHARE_A_LEAF]

    assert len({leaf.casefold() for leaf in leaves}) == len(_IDS_THAT_MUST_NOT_SHARE_A_LEAF)
    assert [leaf for leaf in leaves if leaf.casefold().startswith(_HASHED_PREFIX)] == [
        leaf for leaf in leaves if _leaf_family(leaf) == "hashed"
    ]
    assert {_leaf_family(leaf) for leaf in leaves} == {"hashed", "verbatim", "tagged"}


def _leaf_family(leaf: str) -> str:
    """Which of the three shapes a leaf is, read off the name the way a reader is."""
    if leaf.startswith(_HASHED_PREFIX):
        return "hashed"
    return "tagged" if "~" in leaf else "verbatim"


#: Characters whose fold a positional case tag could not record, spelled by code
#: point because a literal one is easy to read as its ASCII neighbour in a diff.
#: The first three fold to two characters where they were one, so no mask over
#: positions could restore them; the last two fold *into* ASCII from outside it,
#: so a name carrying one would fold onto a name that never did. All five are
#: outside the verbatim charset, which is why the tag never meets them.
_FOLDS_THE_CASE_TAG_COULD_NOT_RECORD: Final = (
    ("LATIN SMALL LETTER SHARP S", 0x00DF),
    ("LATIN SMALL LIGATURE FI", 0xFB01),
    ("LATIN CAPITAL LETTER I WITH DOT ABOVE", 0x0130),
    ("KELVIN SIGN", 0x212A),
    ("LATIN SMALL LETTER LONG S", 0x017F),
)


def test_the_verbatim_charset_folds_one_ascii_character_at_a_time() -> None:
    """The key under the case tag being exact rather than probable.

    The tag records *which positions* folding changes, and that recovers the id
    only while folding is one character to one character over the charset the
    verbatim arm admits. It is, because that charset is ASCII. The characters
    that would break it are real and are driven below: three fold to two
    characters where they were one, and two fold *into* the ASCII range from
    outside it. An id carrying any of them takes the hashing arm instead, which
    needs no tag at all.

    The counts are the key. They fail on a widened charset, which is the change
    that would otherwise make the tag inexact without saying so.
    """
    leading = [chr(code) for code in range(0x80) if _FILESYSTEM_SAFE.match(chr(code))]
    following = [chr(code) for code in range(0x80) if _FILESYSTEM_SAFE.match(f"a{chr(code)}")]

    assert (len(leading), len(following)) == (63, 65)
    assert all(len(character.casefold()) == 1 for character in {*leading, *following})
    assert [c for c in following if c != c.casefold()] == list(string.ascii_uppercase)
    for name, code in _FOLDS_THE_CASE_TAG_COULD_NOT_RECORD:
        assert _FILESYSTEM_SAFE.match(chr(code)) is None, f"{name} reached the verbatim arm"


def test_the_longest_leaf_this_layout_can_emit_fits_a_path_component() -> None:
    """The arithmetic ``_FILESYSTEM_SAFE`` states, measured instead of trusted.

    The worst case is an id at the charset's own bound with every character
    tagged: 120 characters, a 30-hex-digit mask, the separator and the suffix.
    """
    longest = record_leaf("A" * 120)

    assert len(longest) == 156
    assert len(longest.encode("utf-8")) <= 255


def test_two_repositories_never_share_a_directory() -> None:
    """The identity hash separates repositories, which is what keeps keys unique.

    A pull request is keyed by its number, and every repository has a pull
    request 1; the directory is the only thing that tells those apart. The
    separation has to survive a filesystem that folds case, and here it does
    without an argument: the name is a prefix and lowercase hex, so it is its own
    fold.
    """
    one = repository_directory(PROVIDER, "acme/one")
    two = repository_directory(PROVIDER, "acme/two")

    assert one != two
    assert repository_directory("github", REPOSITORY) != repository_directory("gitlab", REPOSITORY)
    assert (one.casefold(), two.casefold()) == (one, two)


def test_two_identity_pairs_that_split_one_string_land_in_different_directories() -> None:
    """The join takes a separator neither argument can carry (#605 item 1).

    ``repository_directory`` joins its two arguments with a NUL for one stated
    reason: a separator either side could carry would let two different pairs
    hash to one directory, and a shared directory is one repository's pull
    request 1 sitting where another's belongs.

    The pairs here are the **ambiguous splits** of a single string, which is the
    shape the test above cannot reach -- it varies one argument at a time, so it
    holds just as well for a scheme that joins with ``/``.

    ``ReviewIngestService`` composes ``provider="github"`` and nothing else
    today, so the first argument is not a value a caller chooses; what this pins
    is the **function's** contract, which is what the second provider is built
    against.
    """
    assert repository_directory("github/acme", "one") != repository_directory("github", "acme/one")
    assert repository_directory("github", "acme/order-service") != repository_directory(
        "github/acme", "order-service"
    )


@_NEEDS_SYMLINKS
def test_a_repository_directory_symlinked_out_of_the_project_is_refused(tmp_path: Path) -> None:
    """A route that leaves the tree is refused rather than followed.

    The plant is made *after* a first successful run, so the directory name comes
    off the disk rather than out of a hash this test recomputed -- an expectation
    computed from the thing under test would agree with it however wrong both
    were.
    """
    store = _store(tmp_path)
    store.write([_event(number=1)], run=RUN_ONE)
    review = _review_root(tmp_path)
    (planted,) = list(review.iterdir())
    outside = tmp_path / "outside"
    outside.mkdir()
    for path in sorted(planted.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    planted.rmdir()
    planted.symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathEscapeError):
        store.write([_event(number=2)], run=RUN_TWO)

    assert list(outside.iterdir()) == []


@_NEEDS_SYMLINKS
def test_a_repository_directory_reached_by_leaving_the_tree_and_returning_is_refused(
    tmp_path: Path,
) -> None:
    """The route walk's own half of the pair, with the destination contained.

    ``_write_one`` proves two different things and the store's own docstring says
    neither implies the other: ``resolve_within_root`` answers *where* the path
    points and ``assert_no_symlink_escape`` answers *how it got there*. The plant
    above cannot tell them apart -- it lands outside the review directory, so the
    first check refuses it whether or not the second one runs.

    This plant can. The repository directory is a link whose target climbs out of
    the project and comes back to a directory **inside** the review directory, so
    the resolution is contained and only the route walk objects. That shape is
    never legitimate: it makes the set of writable paths depend on symlink
    topology rather than on the tree.

    The assertion that carries it is the destination, not the exception: without
    the route check the write succeeds *through* the link, so an empty
    destination is what says nothing was written.
    """
    store = _store(tmp_path)
    store.write([_event(number=1)], run=RUN_ONE)
    review = _review_root(tmp_path)
    (planted,) = list(review.iterdir())
    destination = review / "returned-to"
    destination.mkdir()
    for path in sorted(planted.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    planted.rmdir()
    # Read from the link's own directory: three steps up stands on `tmp_path`,
    # outside the review root, and the rest walks back down into it.
    planted.symlink_to(
        f"../../../repo/.theurian/review/{destination.name}", target_is_directory=True
    )

    with pytest.raises(PathEscapeError):
        store.write([_event(number=2)], run=RUN_TWO)

    assert list(destination.rglob("*")) == [], "the write followed a route that left the project"


@_NEEDS_SYMLINKS
def test_a_leaf_symlinked_at_a_file_inside_the_project_is_refused(tmp_path: Path) -> None:
    """Containment passes an in-review link; the publish step is what refuses it.

    The plant resolves *inside the review directory*, so both path guards are
    satisfied by construction -- a decoy one level higher, in the project root,
    is refused by ``resolve_within_root`` instead and would drive the wrong
    guard. This is the half neither path guard catches, and the assertion is that
    the decoy still holds its own bytes.

    **The mechanism moved when the write became a rename.** ``O_NOFOLLOW`` used
    to answer this because the record's own name was what got opened; the bytes
    now go to a ``.writing`` sibling and ``os.replace`` publishes them, and
    ``rename(2)`` never follows a link at its destination. So the decoy is safe
    by construction here and the refusal comes from the ``lstat`` in
    ``_publish`` -- which is what keeps an operator told, rather than having the
    planted link quietly replaced.
    """
    store = _store(tmp_path)
    (landed,) = store.write([_event(number=1)], run=RUN_ONE)
    review = _review_root(tmp_path)
    decoy = review / "decoy.txt"
    decoy.write_text("not evidence", encoding="utf-8")
    leaf = review / landed
    leaf.unlink()
    leaf.symlink_to(decoy)

    with pytest.raises(ReviewEvidenceError) as raised:
        store.write([_event(number=1)], run=RUN_TWO)

    assert decoy.read_text(encoding="utf-8") == "not evidence"
    assert landed in raised.value.remedy
    assert "derived state" not in raised.value.remedy.replace("not derived state", ""), (
        "the refusal reuses `no_follow.symbolic_link_remedy`, whose text says the "
        "artefact is rebuilt -- which is exactly what review evidence is not"
    )
    assert leaf.is_symlink(), "the refusal replaced the link instead of reporting it"


@_NEEDS_SYMLINKS
def test_a_directory_link_inside_the_review_root_does_not_relocate_the_write(
    tmp_path: Path,
) -> None:
    """The shape both path guards wave through: a link whose target is in-tree.

    ``O_NOFOLLOW`` constrains the final component, so an ordinary directory link
    in the *prefix* is followed -- the bound recorded as #577, measured there
    relocating the ingestion manifest at exit 0. Everything that issue enumerates
    is derived state a later run rebuilds; an evidence file is the source, so a
    relocated write files a record under a directory that is not the one its
    repository hashes to.

    The plant is the in-root shape on purpose. A link that leaves the tree is
    refused by ``resolve_within_root`` and one that leaves and returns by
    ``assert_no_symlink_escape`` -- the two tests above -- so only a target
    *inside* the review directory reaches the guard this drives, which is why the
    exception type is asserted: a ``PathEscapeError`` here would mean one of the
    other two fired and this plant proved nothing new.

    The directory name comes off the disk after a first successful run rather
    than out of a hash this test recomputed, for the reason its neighbours give.
    """
    store = _store(tmp_path)
    store.write([_event(number=1)], run=RUN_ONE)
    review = _review_root(tmp_path)
    (planted,) = [path for path in review.iterdir() if path.is_dir()]
    elsewhere = review / "elsewhere"
    elsewhere.mkdir()
    decoy = elsewhere / "keep.txt"
    decoy.write_text("not evidence", encoding="utf-8")
    for path in sorted(planted.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    planted.rmdir()
    # A *relative* target, next to the link. An absolute one would be spelled
    # against however the machine gives out temporary directories, and on a
    # platform where that path is itself a link (macOS gives `/var` for
    # `/private/var`) the route walk refuses it as unanchored -- which is a
    # different guard, and would make this test pass while driving nothing.
    planted.symlink_to(elsewhere.name, target_is_directory=True)

    with pytest.raises(ReviewEvidenceError) as raised:
        store.write([_event(number=2)], run=RUN_TWO)

    assert not isinstance(raised.value, PathEscapeError)
    assert list(elsewhere.iterdir()) == [decoy], "the write followed the link"
    assert decoy.read_text(encoding="utf-8") == "not evidence"
    assert "ls -l" in raised.value.remedy


def test_an_interrupted_write_leaves_the_previous_record_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refetch that dies mid-write costs the refresh, never the record.

    The truncating open was the fault: it emptied the record's own file and then
    the process had to survive long enough to fill it again. An evidence file has
    no rebuild -- the upstream comment may already be edited or deleted -- so the
    window was one where a crash destroyed the only copy.

    Modelled by replacing the write with one that truncates its target and then
    dies, which is what an ``O_TRUNC`` open followed by an interrupt does. The
    assertion is on the *bytes*: the previous copy is byte-identical and still
    carries run one's stamp, and the temporary the interrupted run opened is not
    left behind to be read as a record.
    """
    store = _store(tmp_path)
    (landed,) = store.write([_event(number=42)], run=RUN_ONE)
    path = _review_root(tmp_path) / landed
    before = path.read_bytes()

    def _truncate_then_die(target: Path, text: str, **_: object) -> None:
        target.write_text("", encoding="utf-8")
        raise KeyboardInterrupt("interrupted between the open and the write")

    monkeypatch.setattr(store_module, "write_text_without_following_a_link", _truncate_then_die)

    with pytest.raises(KeyboardInterrupt):
        store.write([_event(number=42)], run=RUN_TWO)

    assert path.read_bytes() == before
    assert list(_review_root(tmp_path).rglob("*.writing")) == []
    monkeypatch.undo()
    assert store.read_all()[0].last_seen == RUN_ONE


# -- refusals -----------------------------------------------------------------


def test_two_records_claiming_one_path_in_one_run_are_refused(tmp_path: Path) -> None:
    """Overwriting the first with the second would discard evidence silently."""
    store = _store(tmp_path)

    with pytest.raises(ReviewEvidenceError) as raised:
        store.write([_thread("PRRT_same"), _thread("PRRT_same")], run=RUN_ONE)

    assert "PRRT_same" in str(raised.value)
    assert "gh api graphql" in raised.value.remedy


def _colliding_record_path(
    *, provider: str, identity: str, kind: EvidenceKind, provider_id: str
) -> str:
    """A layout that spells a safe id out with no case tag -- the one H-E was found in."""
    return f"{repository_directory(provider, identity)}/{kind.value}/{provider_id}{EVIDENCE_SUFFIX}"


def test_two_records_a_folding_filesystem_would_merge_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard's own case, driven with a layout that collides on purpose.

    The shipped layout keeps two ids' leaves apart after folding, so nothing it
    emits reaches this refusal -- which would leave the guard a line no input
    could fail, and a guard nothing reaches survives its own deletion. What it
    defends is the *store's* promise rather than the layout's: "a silently
    overwritten record is a lost one" is a claim about the disk, and the disk on
    macOS and Windows compares folded names. The layout installed here is
    exactly the one this store must not depend on being gone.

    Both spellings are named in the refusal, because an operator told only the
    second one would go looking for a file that is on disk under the first.
    """
    store = _store(tmp_path)
    monkeypatch.setattr(records_module, "record_path", _colliding_record_path)

    with pytest.raises(ReviewEvidenceError) as raised:
        store.write([_thread("PRRT_kwDOAbc"), _thread("prrt_kwdoabc")], run=RUN_ONE)

    assert "PRRT_kwDOAbc.json" in str(raised.value)
    assert "prrt_kwdoabc.json" in str(raised.value)
    assert "case-insensitive filesystem" in raised.value.remedy
    assert "gh api graphql" in raised.value.remedy
    assert len(list(_review_root(tmp_path).rglob(f"*{EVIDENCE_SUFFIX}"))) == 1


@pytest.mark.parametrize(
    ("label", "mangle"),
    (
        ("not JSON at all", lambda _text: "{"),
        ("a JSON array", lambda _text: "[]"),
        (
            "a format version this build does not write",
            lambda text: text.replace('"formatVersion": 1', '"formatVersion": 99'),
        ),
        ("a kind nothing writes", lambda text: text.replace('"pull-request"', '"invented"')),
        ("a field of the wrong type", lambda text: text.replace('"number": 42', '"number": "42"')),
        ("a naive timestamp", lambda text: text.replace("+00:00", "")),
        ("no run stamp", lambda text: text.replace('"lastSeenRun"', '"seenRun"')),
        (
            "a record that names another repository",
            lambda text: text.replace(
                '"repository": "acme/order-service"', '"repository": "acme/other"'
            ),
        ),
    ),
)
def test_a_file_this_build_cannot_read_is_refused_with_a_cure(
    tmp_path: Path, label: str, mangle: object
) -> None:
    """Refused, never skipped: a corpus quietly smaller than the disk is worse.

    The last case is the identity check. A record moved between directories reads
    back as a record about a repository its own directory does not name, and the
    derived store slice 3 builds would carry it under the wrong identity.
    """
    store = _store(tmp_path)
    (landed,) = store.write([_event(number=42)], run=RUN_ONE)
    path = _review_root(tmp_path) / landed
    assert callable(mangle)
    path.write_text(mangle(path.read_text(encoding="utf-8")), encoding="utf-8")

    with pytest.raises(ReviewEvidenceError) as raised:
        store.read_all()

    assert landed in str(raised.value), label
    assert ".theurian/review/" in raised.value.remedy
    assert "git log -p" in raised.value.remedy


def _in_record(document: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """The same document with ``record`` fields replaced, never mutated."""
    return {**document, "record": {**document["record"], **fields}}


def _thread_carrying(body: str) -> EvidenceRecord:
    """The thread fixture with its first comment body replaced, and nothing else."""
    record = _thread()
    payload = record.payload
    assert isinstance(payload, ReviewThread)
    return replace(
        record,
        payload=replace(payload, comments=(replace(payload.comments[0], body=body),)),
    )


@pytest.mark.parametrize(
    ("label", "record", "edit"),
    (
        (
            "a thread with no comments",
            _thread(),
            lambda document: _in_record(document, comments=[]),
        ),
        (
            "a submission whose state is only whitespace",
            _submission(),
            lambda document: _in_record(document, state="   "),
        ),
        (
            "a participant with no external id",
            _event(),
            lambda document: _in_record(
                document, author={**document["record"]["author"], "externalId": ""}
            ),
        ),
        (
            "a project id of the wrong form",
            _event(),
            lambda document: _in_record(document, projectId="Not Kebab"),
        ),
        (
            "a pull request numbered zero",
            _event(),
            lambda document: _in_record(document, number=0),
        ),
        (
            "an empty provider",
            _event(),
            lambda document: {**document, "provider": ""},
        ),
        (
            "an empty run id",
            _event(),
            lambda document: {
                **document,
                "lastSeenRun": {**document["lastSeenRun"], "runId": ""},
            },
        ),
        (
            "an anchor whose line numbering starts at zero",
            _thread(),
            lambda document: {
                **document,
                "sourceAnchor": {**document["sourceAnchor"], "lineStart": 0},
            },
        ),
    ),
)
def test_a_landed_file_the_domain_refuses_is_graded_and_names_the_file(
    tmp_path: Path, label: str, record: EvidenceRecord, edit: object
) -> None:
    """The read path's second exception family, driven one invariant at a time.

    A ``DomainError`` is not a ``ValueError``, so a catch tuple naming only the
    latter let every case here out of ``read_all`` as a bare traceback carrying
    no path and no cure -- the ADR-0030 clause 9 escape, arriving through a file
    rather than through a provider. The ``__cause__`` assertion is what makes
    each case drive *that* clause: without it a case whose edit happened to
    produce a shape fault instead would pass on the ``ValueError`` arm that was
    always there, and the test would hold nothing.

    The eight cases range over both ``DomainError`` subclasses and over every
    object the codec builds -- payload, participant, anchor, identifier, the
    record wrapper and the run stamp -- because the family is what is caught and
    a single member would not show that.
    """
    store = _store(tmp_path)
    (landed,) = store.write([record], run=RUN_ONE)
    path = _review_root(tmp_path) / landed
    assert callable(edit)
    path.write_text(json.dumps(edit(json.loads(path.read_text(encoding="utf-8")))), "utf-8")

    with pytest.raises(ReviewEvidenceError) as raised:
        store.read_all()

    assert isinstance(raised.value.__cause__, DomainError), (
        f"{label}: this case is graded by the ValueError arm, so it drives nothing new"
    )
    assert landed in str(raised.value), label
    assert "git log -p" in raised.value.remedy


@pytest.mark.parametrize(
    ("label", "record", "spelled"),
    (
        ("a pull request's number", _event(number=42), '"number": 42'),
        ("a thread's line start", _thread(), '"lineStart": 10'),
    ),
    ids=("number", "lineStart"),
)
def test_a_boolean_where_an_integer_belongs_is_refused_rather_than_read_as_one(
    tmp_path: Path, label: str, record: EvidenceRecord, spelled: str
) -> None:
    """``bool`` is an ``int`` subclass, and the codec's guard against that.

    ``isinstance(True, int)`` is ``True`` in Python, so a codec checking only
    ``isinstance(value, int)`` reads ``"lineStart": true`` as line 1 and
    ``"number": true`` as pull request 1 -- a file that says something different
    from what it appears to say, landing in the derived store slice 3 builds. The
    ``isinstance(value, bool)`` arm is the whole guard and nothing drove it.

    Two positions because two helpers reach it: ``_integer`` directly, and
    ``_optional_integer``, whose ``is None`` early-out has to fall through rather
    than treat ``true`` as absent.
    """
    store = _store(tmp_path)
    (landed,) = store.write([record], run=RUN_ONE)
    path = _review_root(tmp_path) / landed
    text = path.read_text(encoding="utf-8")
    key, _value = spelled.split(":", maxsplit=1)
    assert spelled in text, (
        f"{label}: the fixture does not spell {spelled!r}, so this drives nothing"
    )
    path.write_text(text.replace(spelled, f"{key}: true"), encoding="utf-8")

    with pytest.raises(ReviewEvidenceError) as raised:
        store.read_all()

    assert "not an integer" in str(raised.value), label
    assert landed in str(raised.value)


def test_an_unreadable_record_names_the_repository_its_own_file_claims(tmp_path: Path) -> None:
    """The directory is a hash, so the refusal has to say which repository it is.

    One unreadable file stops every repository's ingest -- ``read_all`` reads the
    whole directory and refuses rather than skipping -- so an operator with
    several repositories landed is sent to a ``sha256-...`` directory that
    identifies the file without identifying what it is about. The repository is
    inside the file, and for a record the *domain* refuses the file still parses.
    """
    store = _store(tmp_path)
    (landed,) = store.write([_event(number=42)], run=RUN_ONE)
    path = _review_root(tmp_path) / landed
    path.write_text(
        json.dumps(_in_record(json.loads(path.read_text(encoding="utf-8")), number=0)), "utf-8"
    )

    with pytest.raises(ReviewEvidenceError) as raised:
        store.read_all()

    assert REPOSITORY in str(raised.value)
    assert REPOSITORY not in landed, "the directory named the repository, so this proved nothing"


def test_a_file_too_broken_to_name_its_repository_says_so_rather_than_guessing(
    tmp_path: Path,
) -> None:
    """The honest half of the same message: bytes nothing can read name nothing.

    Without it the hint could be filled from the record the store was *asked* to
    write, or from the last one it read, and a refusal that names a repository it
    did not read out of the failing file is worse than one that names none.
    """
    store = _store(tmp_path)
    (landed,) = store.write([_event(number=42)], run=RUN_ONE)
    (_review_root(tmp_path) / landed).write_text("{", encoding="utf-8")

    with pytest.raises(ReviewEvidenceError) as raised:
        store.read_all()

    assert REPOSITORY not in str(raised.value)
    assert "repository could not be read" in str(raised.value)


def test_a_landed_file_nested_past_the_decoder_is_graded_and_names_the_file(
    tmp_path: Path,
) -> None:
    """RED means one landed file ends every repository's ingest with no document.

    ``json.loads`` answers a document nested past the decoder's own limit with
    ``RecursionError`` -- a ``RuntimeError`` subclass, so outside ``_read_one``'s
    ``(ValueError, DomainError)`` **and** outside the ``except TheurianError``
    ``review ingest`` publishes through. Measured before the fix: this plant left
    ``read_all`` as a bare ``RecursionError``.

    The second half is what a fix at ``_stored`` alone does not buy.
    ``repository_named_in`` re-parses the same bytes to say which repository the
    file claims, from **inside** the arm composing this refusal, so it met the
    identical limit a second time. Asserting the refusal carries the
    could-not-be-read clause is what drives that: an ungraded escape there never
    reaches this line.
    """
    store = _store(tmp_path)
    (landed,) = store.write([_event(number=42)], run=RUN_ONE)
    (_review_root(tmp_path) / landed).write_text("[" * 20_000 + "]" * 20_000, encoding="utf-8")

    with pytest.raises(ReviewEvidenceError) as raised:
        store.read_all()

    assert landed in str(raised.value)
    assert "nested too deeply" in str(raised.value), (
        f"the refusal names the fault by class rather than by cause: {raised.value}"
    )
    assert "repository could not be read" in str(raised.value), (
        "the repository clause is missing, so composing it raised rather than "
        "answering -- the second face of the same limit"
    )
    assert "git log -p" in raised.value.remedy


def test_a_directory_where_a_record_belongs_is_not_told_to_be_deleted(tmp_path: Path) -> None:
    """RED means an operator is told that deleting their own files loses nothing.

    ``shape_that_is_not_a_regular_file`` answers ``"a directory"``, and the
    refusal published ``planted_artefact_cure`` over it -- whose closing clause
    reads "which holds no bytes, so removing it loses nothing". True of a pipe, a
    socket and a device node; false of a directory, which is a container of other
    names, and nothing at this seam can tell whose they are.

    The plant carries a file for exactly that reason: a directory that could be
    removed harmlessly would make the wrong cure look right.
    """
    store = _store(tmp_path)
    record = _event(number=42)
    planted = _review_root(tmp_path) / record.relative_path
    planted.mkdir(parents=True)
    (planted / "notes.md").write_text("bytes an operator wrote\n", encoding="utf-8")

    with pytest.raises(ReviewEvidenceError) as raised:
        store.write([record], run=RUN_ONE)

    remedy = raised.value.remedy
    assert "a directory" in str(raised.value), (
        f"the refusal does not name the shape: {raised.value}"
    )
    assert "loses nothing" not in remedy, f"the cure says the removal costs nothing: {remedy}"
    assert "holds no bytes" not in remedy, f"the cure says the artefact holds no bytes: {remedy}"
    assert "ls -la" in remedy, f"the cure does not print what is inside it: {remedy}"
    assert (planted / "notes.md").is_file(), "the write reached inside the planted directory"


def test_a_folded_read_names_a_rename_that_is_not_a_no_op(tmp_path: Path) -> None:
    """RED means the cure's own instruction is the no-op it warns about.

    ``folded_component_cure`` composes ``Rename <on disk> to <derived>``, and the
    read side handed it the two **whole paths**: on a filesystem that folds case
    ``mv sha256-abc/Pull-Request/42.json sha256-abc/pull-request/42.json`` renames
    a file onto itself. The write side has always passed a component, because
    ``OnDiskSpellings`` finds one; this is the read side reaching the same shape.

    Runs on every filesystem: the plant is created under the variant spelling and
    the refusal keys on casefold equality, not on what the disk does.
    """
    store = _store(tmp_path)
    record = _event(number=42)
    repository, _kind, leaf = record.relative_path.split("/")
    directory = _review_root(tmp_path) / repository / "Pull-Request"
    directory.mkdir(parents=True)
    (directory / leaf).write_text(store_module._document(record, RUN_ONE), encoding="utf-8")

    with pytest.raises(ReviewEvidenceError) as raised:
        store.read_all()

    remedy = raised.value.remedy
    assert "Rename `Pull-Request` to `pull-request`" in remedy, (
        f"the cure does not name the component pair that differs: {remedy}"
    )
    assert repository not in remedy, (
        f"the cure names the whole path, so the rename it composes is a no-op on the "
        f"filesystem it is written for: {remedy}"
    )


def test_a_landed_provider_name_is_bounded_in_the_identity_refusal(tmp_path: Path) -> None:
    """RED means one landed file can publish megabytes of its own bytes.

    ``EvidenceRecord.__post_init__`` names both providers when they disagree, and
    ``_stored`` builds that object out of a document -- so the value is a landed
    file's, bounded only by ``MAX_SOURCE_FILE_BYTES``. Measured with ``!r`` alone:
    a 2,000,000-character ``provider`` produced a 2,000,394-character refusal.

    The same class round two closed for ``formatVersion`` and ``kind`` one
    function over. It stayed open here because the published-sentence walk did
    not reach a ``raise`` outside a refusal constructor;
    ``test_review_ingest_refusals.py``'s key now does.
    """
    store = _store(tmp_path)
    (landed,) = store.write([_event(number=42)], run=RUN_ONE)
    path = _review_root(tmp_path) / landed
    document = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**document, "provider": "P" * 2_000_000}), encoding="utf-8")

    with pytest.raises(ReviewEvidenceError) as raised:
        store.read_all()

    assert len(str(raised.value)) < 4_000, (
        f"the refusal is {len(str(raised.value)):,} characters, so a landed file chooses "
        f"how much of itself reaches an operator's terminal"
    )
    # 2,000,002 and not 2,000,000: the marker reports the length of the
    # *rendering*, which is what the sentence pays for, and `repr` adds the two
    # quotes -- `bounded_quote`'s own recorded behaviour.
    assert "cut from 2000002 characters" in str(raised.value), (
        f"the value was shortened without saying so: {str(raised.value)[:300]}"
    )


@pytest.mark.parametrize(
    ("digits", "reads_back"),
    [(401, True), (4_301, False)],
    ids=["401-digits", "4301-digits"],
)
def test_what_bounds_a_line_number_read_out_of_a_landed_file(
    tmp_path: Path, digits: int, reads_back: bool
) -> None:
    """The read side's bound on ``lineStart``, recorded rather than assumed.

    A verdict pass expected a 401-digit line number to be refused as not a
    record. It is not: ``SourceAnchor``'s three guards are about ordering and
    1-basedness, not magnitude, so it reads back at whatever size the file
    carries. What does bound it is one level down -- ``json.loads`` applies the
    interpreter's own 4,300-digit limit while parsing, and the ``ValueError``
    that raises is graded by ``_read_one`` into a refusal naming the file.

    Both rows are here because either alone would be a claim about the wrong
    thing: the first says the domain does not bound this, the second says
    something does and where. Nothing this slice publishes renders the value --
    ``landed_keys`` reads a path and a repository -- so the bound that matters
    today is the one that keeps ``str()`` total; slice 3's store is where the
    magnitude becomes a question of its own.
    """
    store = _store(tmp_path)
    (landed,) = store.write([_thread()], run=RUN_ONE)
    path = _review_root(tmp_path) / landed
    document = json.loads(path.read_text(encoding="utf-8"))
    anchor = {**document["sourceAnchor"], "lineStart": None, "lineEnd": None}
    path.write_text(
        json.dumps({**document, "sourceAnchor": anchor}).replace(
            '"lineStart": null', f'"lineStart": {"1" + "0" * (digits - 1)}'
        ),
        encoding="utf-8",
    )

    if reads_back:
        (stored,) = store.read_all()
        assert stored.record.anchor.line_start == 10 ** (digits - 1), (
            "a line number the domain does not bound came back changed"
        )
        return

    with pytest.raises(ReviewEvidenceError) as raised:
        store.read_all()

    assert landed in str(raised.value)
    assert "4300 digits" in str(raised.value), (
        f"the refusal does not name the limit that refused it: {raised.value}"
    )


def test_a_landed_file_whose_bytes_are_not_utf8_is_graded_and_names_the_file(
    tmp_path: Path,
) -> None:
    """The decode is inside the ``ValueError`` family rather than beside it.

    ``_read_one``'s tuple stopped naming ``UnicodeDecodeError`` when it started
    naming families, on the grounds that it is a ``ValueError``. This is what
    fails if that reading is ever wrong.
    """
    store = _store(tmp_path)
    (landed,) = store.write([_event(number=42)], run=RUN_ONE)
    (_review_root(tmp_path) / landed).write_bytes(b"\xff\xfe not utf-8")

    with pytest.raises(ReviewEvidenceError) as raised:
        store.read_all()

    assert landed in str(raised.value)
    assert "git log -p" in raised.value.remedy


def test_a_record_larger_than_the_reader_accepts_is_refused_before_it_is_written(
    tmp_path: Path,
) -> None:
    """The writer's cap is the reader's, so the store cannot write what it cannot read.

    ``read_all`` reads through ``read_source_file``, which refuses a file above
    ``MAX_SOURCE_FILE_BYTES``. An unbounded writer in front of it lands a record
    that makes every later ``review ingest`` exit before it fetches anything --
    and review evidence has no rebuild, so the file cannot simply be deleted. The
    refusal therefore has to happen *before* the write, which is what the empty
    directory below asserts: an exception raised after a partial write would
    satisfy ``pytest.raises`` and leave the unreadable file behind.
    """
    store = _store(tmp_path)
    oversized = _thread_carrying("x" * MAX_SOURCE_FILE_BYTES)

    with pytest.raises(ReviewEvidenceError) as raised:
        store.write([oversized], run=RUN_ONE)

    assert "PRRT_kwDOABCD1" in str(raised.value)
    assert str(MAX_SOURCE_FILE_BYTES) in str(raised.value)
    assert "gh api graphql" in raised.value.remedy
    assert list(_review_root(tmp_path).rglob("*.json")) == [], (
        "the refusal fired after the write, so the unreadable file is on disk anyway"
    )
    assert store.read_all() == ()


def test_a_record_the_reader_accepts_still_lands_when_it_is_large(tmp_path: Path) -> None:
    """The positive control on the cap: it refuses above the limit, not merely large.

    Without it the guard could refuse every record whose body is not tiny and
    still pass the refusal test above.
    """
    store = _store(tmp_path)
    large = _thread_carrying("x" * (MAX_SOURCE_FILE_BYTES // 8))

    (landed,) = store.write([large], run=RUN_ONE)

    assert (_review_root(tmp_path) / landed).is_file()
    assert store.read_all()[0].record == large


def test_a_landed_file_too_large_to_read_is_graded_rather_than_escaping(tmp_path: Path) -> None:
    """The other half of the size seam: a file that grew past the cap on disk.

    ``InputTooLargeError`` is a ``SecurityError`` and neither an ``OSError`` nor a
    ``ValueError``, so it left ``read_all`` ungraded -- with its own remedy, which
    tells the reader to "shrink or split" a file that is a *record*, not an input
    anyone can edit down. The cure here is the one that names the file and the
    command that shows how it got that way.
    """
    store = _store(tmp_path)
    (landed,) = store.write([_event(number=42)], run=RUN_ONE)
    (_review_root(tmp_path) / landed).write_bytes(b"x" * (MAX_SOURCE_FILE_BYTES + 1))

    with pytest.raises(ReviewEvidenceError) as raised:
        store.read_all()

    assert landed in str(raised.value)
    assert "git log -p" in raised.value.remedy
    assert "shrink or split" not in raised.value.remedy


def test_a_write_the_filesystem_refuses_names_the_record_and_a_command(tmp_path: Path) -> None:
    """The write's own fault is translated, so no bare ``OSError`` reaches a caller.

    Provoked without ``chmod``, which denies nothing to root and so cannot be
    driven in the offline CI job: an ordinary file where the kind directory
    belongs makes ``mkdir`` fail on every platform.
    """
    store = _store(tmp_path)
    record = _event(number=42)
    review = _review_root(tmp_path)
    repository, kind, _leaf = record.relative_path.split("/")
    (review / repository).mkdir(parents=True)
    (review / repository / kind).write_text("not a directory", encoding="utf-8")

    with pytest.raises(ReviewEvidenceError) as raised:
        store.write([record], run=RUN_ONE)

    assert record.relative_path in str(raised.value)
    assert "ls -ld .theurian/review" in raised.value.remedy


def test_a_file_that_is_not_a_record_is_left_alone(tmp_path: Path) -> None:
    """The directory is the project's; a README beside the evidence is not an error.

    Three positions, and the third is the one nothing drove. The walk selects on
    :data:`EVIDENCE_SUFFIX` at the leaf, so a file **inside a kind directory**
    that is not a ``.json`` is the case where that suffix test is the only thing
    standing between an operating system's litter and a refusal that stops every
    repository's ingest. ``.DS_Store`` is the one a macOS checkout of a committed
    evidence tree actually produces, and ``read_all`` refuses whatever it lists
    and cannot parse -- so admitting it would turn opening the directory in
    Finder into a broken ``review ingest``.

    The leftover ``.writing`` temporary is the same rule from the other side and
    is why the suffix is what the walk keys on rather than "not a directory".
    """
    store = _store(tmp_path)
    (landed,) = store.write([_event(number=42)], run=RUN_ONE)
    review = _review_root(tmp_path)
    (review / "README.md").write_text("committed on purpose", encoding="utf-8")
    (repository,) = [path for path in review.iterdir() if path.is_dir()]
    (repository / "notes").mkdir()
    (repository / "notes" / "scratch.json").write_text("{}", encoding="utf-8")
    kind_directory = (review / landed).parent
    (kind_directory / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
    (kind_directory / "42.json.writing").write_text("half a record", encoding="utf-8")

    stored = store.read_all()

    assert [record.relative_path for record in stored] == [landed], (
        "a file inside a kind directory that is not a record was listed, and "
        "`read_all` refuses what it lists and cannot parse"
    )


# -- the run stamp ------------------------------------------------------------


def test_a_run_stamp_with_no_time_zone_is_refused() -> None:
    """A naive instant compares wrong against one written on another machine."""
    with pytest.raises(InvariantViolationError):
        IngestionRun("01K1AAAAAA01234567890ABCDE", datetime(2026, 9, 7, 9, 0))  # noqa: DTZ001


def test_a_run_stamp_comes_from_the_injected_ports() -> None:
    """Determinism (ADR-0007): the stamp is a port's answer, not the wall clock.

    A writer that called ``datetime.now`` could not be pinned by the refetch test
    above, which is the test the whole survival guarantee rests on.
    """
    moment = datetime(2026, 1, 1, tzinfo=UTC)

    class _Clock:
        def now(self) -> datetime:
            return moment

    class _Ids:
        def new_ulid(self) -> str:
            return "01K1CCCCCC01234567890ABCDE"

    run = new_ingestion_run(clock=_Clock(), ids=_Ids())  # type: ignore[arg-type]

    assert run == IngestionRun("01K1CCCCCC01234567890ABCDE", moment)


def test_an_anchor_from_another_provider_is_refused() -> None:
    """The anchor is the only pointer back to material Theurian cannot re-fetch."""
    with pytest.raises(InvariantViolationError):
        EvidenceRecord(
            provider=PROVIDER,
            repository=REPOSITORY,
            anchor=SourceAnchor(provider="gitlab", source_uri="https://example.invalid/1"),
            payload=_event().payload,
        )
