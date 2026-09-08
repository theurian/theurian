"""What the ``.writing`` temporary is, what a refusal about it says, and what survives.

The atomic publish moved the ``O_NOFOLLOW`` open off the record's own path and
onto a sibling ``<record>.writing``, and round two (R2-B) found that every
sentence around it had stayed where the open used to be. Three consequences, and
all three are about the artefact whose deletion nothing recovers:

* a link planted at the **temporary** published the **record's** cure, which
  instructs deleting a landed evidence file -- "the one instruction this module
  must never publish", by that module's own rule -- while the handler had
  already silently unlinked the plant it was describing;
* a named pipe or socket at the temporary published a **permissions** cure over
  a shape no permission explains;
* a named pipe at the **leaf** stopped being refused at all and was replaced at
  exit 0, while the ``Raises:`` clause went on promising a refusal there.

So the rows here plant at both paths, and each asserts three things a message
alone would not: which path the sentence names, what is still on disk
afterwards, and that the landed record beside it is untouched.

``tmp_path`` throughout; the store is driven directly because the question is
what *it* does with a plant, and the CLI's half of the same claim is
``tests/integration/test_review_ingest_post_seam_containment.py``.
"""

from __future__ import annotations

import os
import socket
import stat
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from theurian.domain.identifiers import ProjectId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review import ReviewEvent, ReviewParticipant
from theurian.infrastructure.review_evidence import (
    EvidenceRecord,
    IngestionRun,
    ReviewEvidenceError,
    ReviewEvidenceStore,
)

pytestmark = pytest.mark.unit

PROJECT: Final = ProjectId("demo")
PROVIDER: Final = "github"
REPOSITORY: Final = "acme/order-service"

#: The suffix the store appends. Spelled here rather than imported from the
#: private constant, so a rename that changed the temporary's name without
#: changing this file reddens instead of following it silently -- the name is
#: part of what the refusals promise a reader.
WRITING: Final = ".writing"

_NEEDS_SYMLINKS = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)


def _record(number: int = 42, title: str = "Bound the retry budget") -> EvidenceRecord:
    event = ReviewEvent(
        project_id=PROJECT,
        provider=PROVIDER,
        repository=REPOSITORY,
        number=number,
        title=title,
        body="The retry loop is now bounded.",
        author=ReviewParticipant(
            provider=PROVIDER, external_id="U_kwDO1", display_name="Reviewer One"
        ),
        created_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        url=f"https://github.com/{REPOSITORY}/pull/{number}",
        head_commit="b" * 40,
        base_commit="c" * 40,
        head_ref_name="fix/retry-budget",
        labels=(),
    )
    return EvidenceRecord(
        provider=PROVIDER,
        repository=REPOSITORY,
        anchor=SourceAnchor(
            provider=PROVIDER,
            source_uri=event.url,
            repository=REPOSITORY,
            commit_sha=event.head_commit,
            external_id=event.external_key,
        ),
        payload=event,
    )


def _run(run_id: str = "run-1", day: int = 1) -> IngestionRun:
    return IngestionRun(run_id=run_id, observed_at=datetime(2026, 8, day, tzinfo=UTC))


@pytest.fixture
def store(tmp_path: Path) -> ReviewEvidenceStore:
    root = tmp_path / "review"
    root.mkdir()
    return ReviewEvidenceStore(root)


@pytest.fixture
def bound_socket() -> Iterator[socket.socket]:
    """A ``AF_UNIX`` socket the test binds and this fixture always closes."""
    made = socket.socket(socket.AF_UNIX)
    try:
        yield made
    finally:
        made.close()


def _root(store: ReviewEvidenceStore, relative: str) -> Path:
    """The absolute path of ``relative``, without reaching into the store twice."""
    return store._root / relative


@_NEEDS_SYMLINKS
def test_a_planted_link_at_the_temporary_survives_the_refusal_that_names_it(
    store: ReviewEvidenceStore, tmp_path: Path
) -> None:
    """RED means the store deletes the plant and then tells you to delete a record.

    The measured shape, in one run: a record lands; a link is planted at its
    ``.writing`` sibling; the refetch refuses. Before the fix the refusal named
    the **landed record** and its remedy said to remove it -- over a file no
    refetch rebuilds -- while the handler had already unlinked the link the
    operator would have gone looking for.

    Four assertions, because a message fix alone would pass the first two: the
    sentence names the temporary, the *record's* path is absent from the cure,
    the plant is still there to be inspected, and the landed record still holds
    the bytes it landed with.
    """
    record = _record()
    (relative,) = store.write([record], run=_run())
    landed_bytes = _root(store, relative).stat().st_size
    decoy = tmp_path / "an-operator-file.txt"
    decoy.write_text("the operator's own file\n", encoding="utf-8")
    _root(store, relative + WRITING).symlink_to(decoy)

    with pytest.raises(ReviewEvidenceError) as raised:
        store.write([_record(title="refetched")], run=_run("run-2", day=2))

    message, remedy = str(raised.value), raised.value.remedy
    assert relative + WRITING in message, f"the refusal does not name the temporary: {message}"
    assert relative + WRITING in remedy, f"the cure does not name the temporary: {remedy}"
    assert f"`.theurian/review/{relative}`" not in remedy, (
        f"the cure names the landed record, which is the one artefact it must never "
        f"tell an operator to remove: {remedy}"
    )
    assert _root(store, relative + WRITING).is_symlink(), (
        "the planted link was removed by the cleanup, so the operator cannot look at "
        "the thing the refusal is about"
    )
    assert decoy.read_text(encoding="utf-8") == "the operator's own file\n"
    assert _root(store, relative).stat().st_size == landed_bytes, (
        "the landed record did not survive a refusal about the temporary beside it"
    )


def test_a_socket_at_the_temporary_names_its_shape_rather_than_a_permission(
    store: ReviewEvidenceStore, bound_socket: socket.socket, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED means the cure sends an operator to check a mode over a planted socket.

    A socket refuses the open outright -- ``ENOTSUP``, "Operation not supported
    on socket" -- so no descriptor exists and ``IrregularArtefactError`` is never
    raised: a fix keyed on that class passes a *reader-attached pipe* and leaves
    this one publishing "make the directory readable and writable", which is a
    non-cause. That is why the store asks the **path** what is standing there
    rather than reading the errno, and why this row plants the shape the class
    cannot see.

    The bind is done from **inside** the kind directory with a relative name.
    ``AF_UNIX`` bounds a path at 104 bytes on macOS, and this layout's absolute
    path -- a pytest ``tmp_path`` plus a 71-character hashed directory -- is past
    it, so binding the absolute form fails with ``OSError: AF_UNIX path too
    long`` before the store is ever called.
    """
    record = _record()
    relative = record.relative_path
    leaf = _root(store, relative)
    leaf.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(leaf.parent)
    bound_socket.bind(leaf.name + WRITING)

    with pytest.raises(ReviewEvidenceError) as raised:
        store.write([record], run=_run())

    message, remedy = str(raised.value), raised.value.remedy
    assert "a socket" in message, f"the refusal does not name the shape: {message}"
    assert relative + WRITING in message
    assert "readable and writable" not in remedy, (
        f"the cure still sends the reader to a permission, which is not the cause: {remedy}"
    )
    assert stat.S_ISSOCK(os.lstat(_root(store, relative + WRITING)).st_mode), (
        "the planted socket was removed by the cleanup"
    )


def test_a_directory_at_the_temporary_is_not_told_to_be_deleted(
    store: ReviewEvidenceStore,
) -> None:
    """RED means an operator is told that removing their own directory loses nothing.

    The seam the record-path split did not reach. ``_publish`` chose a cure per
    shape and a **directory** at the record's own path got one that offers a
    move; ``_temporary_refusal`` passed the shape into the *message* and
    published ``planted_temporary_cure`` whatever the shape was. That text is
    true of a link, a pipe and a socket at ``<record>.writing`` -- "it holds no
    review evidence and removing it loses nothing" -- and false of a directory,
    whose entries may be an operator's own files and which nothing at this seam
    can tell the ownership of.

    The plant carries a file for exactly that reason: a directory that could be
    removed harmlessly would make the wrong cure look right.

    Three phrases rather than one, spelled here rather than imported from
    ``test_review_evidence_cures.py``'s pattern: a cure whose costless claim
    survived under a different wording is the drift this row exists to catch,
    and the cure it is about says the claim twice.
    """
    record = _record()
    relative = record.relative_path
    planted = _root(store, relative + WRITING)
    planted.mkdir(parents=True)
    (planted / "quarterly-notes.md").write_text("bytes an operator wrote\n", encoding="utf-8")

    with pytest.raises(ReviewEvidenceError) as raised:
        store.write([record], run=_run())

    message, remedy = str(raised.value), raised.value.remedy
    assert relative + WRITING in message, f"the refusal does not name the temporary: {message}"
    assert "a directory" in message, f"the refusal does not name the shape: {message}"
    for claim in ("loses nothing", "holds no bytes", "holds no review evidence"):
        assert claim not in remedy, (
            f"the cure tells an operator that removing this directory costs nothing, at "
            f"`{claim}`: {remedy}"
        )
    assert "ls -la" in remedy, (
        f"the cure does not print what is *inside* the directory, which is the question "
        f"that decides whether removing it is safe: {remedy}"
    )
    assert (planted / "quarterly-notes.md").is_file(), (
        "the operator's own file under the planted directory did not survive the refusal"
    )


def test_a_named_pipe_at_the_leaf_is_refused_rather_than_replaced(
    store: ReviewEvidenceStore,
) -> None:
    """RED means a planted pipe at a record's own path is published over at exit 0.

    Moving the open to the temporary took ``O_NOFOLLOW`` and the descriptor's
    shape check off the leaf with it, and ``os.replace`` follows neither a link
    nor a pipe: the rename simply succeeded. Nothing was destroyed -- which is
    why this is a reporting defect rather than a write escape -- but a store that
    silently repairs a planted evidence path leaves the operator no reason to
    look at how it got there, and the ``Raises:`` clause promising a refusal was
    false for the whole of round one's fix arc.
    """
    record = _record()
    relative = record.relative_path
    _root(store, relative).parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(_root(store, relative))

    with pytest.raises(ReviewEvidenceError) as raised:
        store.write([record], run=_run())

    message, remedy = str(raised.value), raised.value.remedy
    assert "named pipe" in message, f"the refusal does not name the shape: {message}"
    assert relative in message
    assert "loses nothing" in remedy, f"the cure does not say the removal is safe: {remedy}"
    assert stat.S_ISFIFO(os.lstat(_root(store, relative)).st_mode), (
        "the planted pipe was replaced rather than refused"
    )


def test_an_interrupted_writes_own_litter_is_still_discarded(
    store: ReviewEvidenceStore,
) -> None:
    """The other direction: the cleanup still removes what it is for.

    The ``lstat`` narrows what may be unlinked to a regular file, and a guard
    narrowed too far is a leak: a ``.writing`` file left by an interrupted run
    would then accumulate one per failed write. Driven by making the *publish*
    fail after the temporary has been written -- a directory at the record's own
    path, which ``os.replace`` refuses -- so the temporary exists and is this
    store's own.
    """
    record = _record()
    relative = record.relative_path
    _root(store, relative).mkdir(parents=True)

    with pytest.raises(ReviewEvidenceError):
        store.write([record], run=_run())

    assert not _root(store, relative + WRITING).exists(), (
        "the temporary this write opened was left behind, so a failing run litters "
        "one file per record"
    )


def test_every_write_side_refusal_says_what_the_run_had_already_landed(
    store: ReviewEvidenceStore, tmp_path: Path
) -> None:
    """RED means an operator is told a rollback happened that did not.

    The population is every refusal reachable from ``write`` with one record
    already landed in the same call, driven one plant per row rather than
    asserted over the source: what has to hold is the *published sentence*, and
    a structural walk over message expressions would pass a site that composed
    the same sentence differently.

    One record is landed first in every case, so ``the 1 record(s)`` is the
    honest answer and ``Nothing was written`` -- what these said before round two
    -- would be a false statement about a file on disk that no refetch rebuilds.
    """
    blocked = _record(number=96)
    _root(store, blocked.relative_path).parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(_root(store, blocked.relative_path))
    unwritable = _record(number=94)
    _root(store, unwritable.relative_path).mkdir(parents=True)

    calls: dict[str, list[EvidenceRecord]] = {
        # The same record twice, so exactly one has landed when the second is refused.
        "collision": [_record(number=99), _record(number=99)],
        # Refused before its own write, but after the record ahead of it landed.
        "oversize": [_record(number=97), _record(number=98, title="x" * 9_000_000)],
        # A planted pipe at the second record's own leaf.
        "planted artefact": [_record(number=95), blocked],
        # A directory at the second record's own leaf: the rename refuses.
        "unpublishable": [_record(number=93), unwritable],
    }

    for label, records in calls.items():
        with pytest.raises(ReviewEvidenceError) as raised:
            store.write(records, run=_run(f"run-{label.replace(' ', '-')}"))

        assert "the 1 record(s) this run wrote before it" in str(raised.value), (
            f"the {label} refusal does not name what already landed: {raised.value}"
        )
        assert "Nothing was written" not in str(raised.value), (
            f"the {label} refusal claims a rollback that did not happen: {raised.value}"
        )
