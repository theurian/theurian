"""Evidence files are the source, and they land inside the project (ADR-0030 decision 3).

Four claims, and each fails on its own:

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

Marked ``unit`` and writes only under ``tmp_path``. Nothing here touches this
repository's own ``.theurian/``.
"""

from __future__ import annotations

import json
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
    EvidenceRecord,
    IngestionRun,
    ReviewEvidenceError,
    ReviewEvidenceStore,
    new_ingestion_run,
    record_leaf,
    repository_directory,
)
from theurian.infrastructure.review_evidence.layout import EVIDENCE_SUFFIX
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
    ),
)
def test_a_hostile_provider_id_lands_inside_the_review_directory(
    tmp_path: Path, label: str, external_id: str
) -> None:
    """AC-4's other half: the id the provider sent is not trusted to be a name.

    A GraphQL response is untrusted input, so an id is charset-checked before it
    is spelled into a filename and hashed when it fails. The last case is the
    escape-prefix rule: an id that already looks like a hash is hashed anyway, so
    a verbatim leaf and a hashed leaf can never name the same file.
    """
    store = _store(tmp_path)

    (landed,) = store.write([_thread(external_id=external_id)], run=RUN_ONE)

    review = _review_root(tmp_path)
    assert landed.count("/") == 2, f"{label}: the id opened a directory of its own"
    assert (review / landed).is_file()
    assert (review / landed).resolve().is_relative_to(review.resolve())
    assert store.read_all()[0].record.payload == _thread(external_id=external_id).payload


def test_a_provider_id_that_is_already_a_name_is_spelled_out_rather_than_hashed() -> None:
    """The positive control on the hashing rule.

    Without it the rule could hash everything and pass every containment test
    while making the committed tree unreadable -- which is the property the
    verbatim arm exists for. GitHub's current node ids are the shape driven here.
    """
    assert record_leaf("PRRT_kwDOABCD1M5abcde") == "PRRT_kwDOABCD1M5abcde.json"
    assert record_leaf("42") == "42.json"
    assert record_leaf("../../etc/passwd").startswith("sha256-")
    assert record_leaf("sha256-anything").startswith("sha256-")


def test_no_provider_id_can_be_made_to_name_another_ids_file() -> None:
    """``layout.py``'s disjointness claim, driven from the impostor's side.

    The module records that the verbatim and the hashed leaf names are disjoint
    sets "by construction rather than by improbability", so that "no id can be
    made to name another id's file". Every other case here drives one id at a
    time and so holds only one half of that: they say a hostile id is hashed,
    never that a second id cannot be spelled to land on the first one's file.

    The attack the claim forbids is exactly that. Read the leaf a hashed id
    landed under, hand that leaf back to the store as some other record's
    provider id, and see where it goes: it is inside the verbatim arm's charset
    and length, so the **escape prefix alone** is what sends it to be hashed. The
    control below is the same string with the prefix spelled one digit off, which
    the verbatim arm accepts -- so a failure here is the prefix rule and not the
    charset.

    Two ids naming one file is not caught downstream: the store refuses a
    collision only *within* one run, and these two arrive in different ones.
    """
    victim = record_leaf("../../etc/passwd")
    impostor = victim.removesuffix(EVIDENCE_SUFFIX)
    lookalike = f"sha257-{impostor.removeprefix('sha256-')}"

    landed = record_leaf(impostor)

    assert landed != victim, "an id spelled as another id's leaf named that id's file"
    assert landed.startswith("sha256-"), "the impostor was not sent down the hashing arm"
    assert record_leaf(lookalike) == f"{lookalike}{EVIDENCE_SUFFIX}", (
        "the control: this shape is one the verbatim arm accepts, so what sent the "
        "impostor to be hashed was the prefix rather than its charset or its length"
    )


def test_two_repositories_never_share_a_directory() -> None:
    """The identity hash separates repositories, which is what keeps keys unique.

    A pull request is keyed by its number, and every repository has a pull
    request 1; the directory is the only thing that tells those apart.
    """
    assert repository_directory(PROVIDER, "acme/one") != repository_directory(PROVIDER, "acme/two")
    assert repository_directory("github", REPOSITORY) != repository_directory("gitlab", REPOSITORY)


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
    """Containment passes an in-review link; ``O_NOFOLLOW`` is what refuses it.

    The plant resolves *inside the review directory*, so both path guards are
    satisfied by construction -- a decoy one level higher, in the project root,
    is refused by ``resolve_within_root`` instead and would drive the wrong
    guard. This is the half only the write's own flags catch, and the assertion
    is that the decoy still holds its own bytes.
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


# -- refusals -----------------------------------------------------------------


def test_two_records_claiming_one_path_in_one_run_are_refused(tmp_path: Path) -> None:
    """Overwriting the first with the second would discard evidence silently."""
    store = _store(tmp_path)

    with pytest.raises(ReviewEvidenceError) as raised:
        store.write([_thread("PRRT_same"), _thread("PRRT_same")], run=RUN_ONE)

    assert "PRRT_same" in str(raised.value)
    assert "gh api graphql" in raised.value.remedy


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
    """The directory is the project's; a README beside the evidence is not an error."""
    store = _store(tmp_path)
    store.write([_event(number=42)], run=RUN_ONE)
    review = _review_root(tmp_path)
    (review / "README.md").write_text("committed on purpose", encoding="utf-8")
    (repository,) = [path for path in review.iterdir() if path.is_dir()]
    (repository / "notes").mkdir()
    (repository / "notes" / "scratch.json").write_text("{}", encoding="utf-8")

    assert len(store.read_all()) == 1


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
