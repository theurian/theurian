"""``theurian review build`` over an empty load, driven through the shipped CLI (#636).

The empty-publish guard has two faces, and each is a whole command's worth of
behaviour rather than a branch: what the operator's exit code is, which document
reaches which channel, and what the store is serving when the process is gone.
``test_review_search_builder.py`` drives the guard's decision table over injected
collaborators; this module drives the two faces #636 reported through
``cli/review_commands.rebuild_search_store`` -- the real evidence reader, the real
directory listing, the real SQLite store, the real provenance record and the
project's own write lock -- and reads the answer off ``theurian review build``
itself.

* **Face 1.** A build that read *nothing* while a first landing was in flight
  used to publish the empty store over the rows that landing had just built, at
  exit 0. It refuses now, and refusing is what leaves those rows serving.
* **Face 2.** An operator deleting every evidence file inside another build's
  read-to-publish window used to be *refused*, and the store went on serving the
  records they had just removed -- under a cure telling them to wait for a
  concurrent run. That build publishes the empty store now, which is what honours
  the deletion.

**The interleaving is produced by a barrier at the read seam, not by a race, and
that is a decision with a reason.** What both faces need is for the directory to
change between a build's two looks at it, and the window is what has to be held
open. Three ways to hold it open were available:

1. two real ``theurian review build`` processes, one stopped with ``SIGSTOP``
   while the other lands and publishes. It is how #636 was first reproduced, and
   it is what ``tests/e2e/`` is for -- CI's required checks run ``pytest -m "not
   e2e"``, so such a test gates nothing on a pull request, which is the same
   reason ``test_migrate_apply_lock_confinement.py`` exists beside
   ``tests/e2e/test_migrate_apply_concurrency.py``. It also needs a *timing*
   premise nothing here can measure -- whether the stopped process had reached
   its read -- and for face 2 that premise is unobservable from the outside: a
   build that read nothing over an emptied corpus publishes exactly what a build
   that read three records over one does, so a mistimed run would pass while
   exercising a different row of the table;
2. two threads contending the project's own lock file, which contends for real
   (a lock is held per open file description, not per process) but leaves the
   same unmeasurable timing premise;
3. this: the read is wrapped so that the concurrent writer's work -- the landing,
   or the deletion -- happens after the read has returned its entries and before
   the build enters its write section. Nothing else is substituted, the window is
   exactly the one the production code leaves open, and **what the read returned
   is captured and asserted**, so each case states on the record which row of the
   guard's table it drove rather than hoping the clock cooperated.

The cost is stated rather than hidden: this module does not demonstrate that the
window is reachable across two OS processes. That is #636's own reproduction and
``review_search_builder.build``'s read/write split is where the reasoning lives.

Marked ``integration``. ``HOME`` and ``THEURIAN_DATA_DIR`` are redirected into
``tmp_path`` and the command runs against a project under it, so nothing here
touches this repository's own ``.theurian/`` or the developer's own machine; no
``setup``, ``uninstall`` or daemon command is reachable from anything below.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Callable, Iterator
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from typer.testing import CliRunner

from theurian.application.project_service import REVIEW_SEARCH_STORE_ID, ProjectPaths
from theurian.cli import review_commands
from theurian.cli.main import app
from theurian.domain.identifiers import ProjectId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review import ReviewEvent, ReviewParticipant
from theurian.infrastructure.review_evidence import (
    EvidenceRecord,
    IngestionRun,
    ReviewEvidenceStore,
)

pytestmark = pytest.mark.integration

runner = CliRunner()

PROJECT: Final = ProjectId("demo")
PROVIDER: Final = "github"
REPOSITORY: Final = "acme/order-service"
RUN: Final = IngestionRun("01K1AAAAAA01234567890ABCDE", datetime(2026, 9, 11, 9, 0, tzinfo=UTC))

#: How many records the landing in each case writes. Three rather than one, so a
#: store that came back holding *something* cannot be mistaken for the one this
#: landing built.
LANDED_RECORDS: Final = 3


def _git(root: Path, *args: str) -> None:
    """One git command under a configuration this module, not the developer, owns."""
    subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607 - git resolved via PATH, args are test-controlled
        cwd=root,
        check=True,
        capture_output=True,
        env={
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        },
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A resolvable project with this test's own ``HOME`` and data directory.

    Both are redirected in the same fixture that changes the working directory,
    because the command resolves its project from ``Path.cwd()`` and records its
    build provenance under ``THEURIAN_DATA_DIR`` (falling back to ``~/.theurian``)
    -- so a redirect of one without the other still writes outside ``tmp_path``.
    """
    root = tmp_path / "demo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    (root / ".theurian").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.chdir(root)
    yield root


def _record(number: int) -> EvidenceRecord:
    """One pull-request record, distinguishable from its siblings by its number."""
    event = ReviewEvent(
        project_id=PROJECT,
        provider=PROVIDER,
        repository=REPOSITORY,
        number=number,
        title=f"Bound the retry budget ({number})",
        body="署名付きトークンを持つ呼び出しだけを再試行する。",
        author=ReviewParticipant(
            provider=PROVIDER, external_id="USER_A", display_name="Reviewer One"
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
        anchor=SourceAnchor(provider=PROVIDER, source_uri=event.url, repository=REPOSITORY),
        payload=event,
    )


def _land(root: Path) -> None:
    """Land :data:`LANDED_RECORDS` records through the store the product itself uses."""
    ReviewEvidenceStore(ProjectPaths.of(root).review).write(
        [_record(number) for number in range(1, LANDED_RECORDS + 1)], run=RUN
    )


def _delete_every_evidence_file(root: Path) -> None:
    """The retention remedy ADR-0030 decision 3 leaves an operator, applied whole."""
    for landed in ProjectPaths.of(root).review.rglob("*.json"):
        landed.unlink()


def _stored_rows(root: Path) -> int:
    """How many records the derived store is serving right now.

    Counted off the database rather than through a search, because what these
    cases are about is whether a build *replaced* a store -- and a row no query
    happens to select is still a row.
    """
    path = ProjectPaths.of(root).review_search_for(REVIEW_SEARCH_STORE_ID)
    if not path.exists():
        return 0
    with closing(sqlite3.connect(path)) as connection:
        return int(connection.execute("SELECT count(*) FROM review_records").fetchone()[0])


class _TheReadThatWas:
    """What the build under test actually read, captured as it returned.

    Held so each case can assert **which row of the guard's table it drove**
    rather than describe one. A build that read nothing and a build that read
    three records behave identically over an emptied corpus -- same empty store,
    same exit code -- so without this the face-2 case below would be green either
    way, including against an interleaving that never widened the window.
    """

    def __init__(self) -> None:
        self.ran = False
        self.entries: tuple[object, ...] | None = None

    @property
    def count(self) -> int:
        assert self.entries is not None, (
            "the build never called its evidence read, so the act that widens its "
            "read-to-publish window never ran and this case drove nothing"
        )
        return len(self.entries)


def _with_a_writer_between_the_read_and_the_publish(
    monkeypatch: pytest.MonkeyPatch, act: Callable[[], None]
) -> _TheReadThatWas:
    """Run ``act`` once, after the build's read returns and before it publishes.

    Wraps the composition root's own read binder rather than replacing the read,
    so the entries the build carries are the ones
    ``ReviewEvidenceStore.read_all`` produced and every other collaborator is the
    shipped one.

    **Once**, and the flag is set before ``act`` runs rather than after: face 1's
    act is itself a full ``rebuild_search_store``, which reaches this same binder,
    so a wrapper that armed itself again on the way in recurses without bound.
    """
    live = review_commands.evidence_entries
    observed = _TheReadThatWas()

    def binder(store: ReviewEvidenceStore) -> Callable[[], tuple[Any, ...]]:
        read = live(store)

        def read_then() -> tuple[Any, ...]:
            entries = read()
            if not observed.ran:
                observed.ran = True
                observed.entries = entries
                act()
            return entries

        return read_then

    monkeypatch.setattr(review_commands, "evidence_entries", binder)
    return observed


def test_a_build_that_read_nothing_refuses_rather_than_emptying_a_store_a_landing_just_filled(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#636 face 1. RED before the guard was rekeyed onto the publish-time capture.

    The shape a first ingestion run delivers. An operator starts ``theurian review
    build`` against a project that has not ingested anything yet, a ``review
    ingest`` lands its records and rebuilds while that build is in flight, and the
    earlier build publishes last. Keyed on what the *read* found, the guard let it
    through: a load of nothing over a corpus of nothing looked lawful, so the
    build replaced a store that had just been filled with one holding no rows and
    exited 0. Nothing on the read side marks a store stale, so the operator's next
    ``review.search`` answered "no results" over evidence that was on disk.

    Keyed on the listing taken **inside the write section**, it is a refusal: the
    corpus is there at the publish and this build kept none of it. Refusing is
    what leaves the landing's rows serving, because this layer publishes by
    replacement and never by emptying.

    Four assertions, each failing on its own. The command exits 1 with the
    ``{error, remedy}`` envelope on stderr and nothing on stdout, which is how a
    ``--json`` caller tells a refusal from a build that did nothing. The message
    is the *read-nothing* arm -- a build with no count to give must not be handed
    a sentence about what it read, and "0 of them" would dress it up as a read
    gone stale. The cure names the directory and the command to re-run. And the
    rows the concurrent build published are still there, which is the whole point
    of refusing.
    """

    def a_first_landing_and_the_rebuild_that_follows_it() -> None:
        """What ``theurian review ingest`` does: land, then rebuild from what landed.

        The rebuild is the shipped composition root, not a store write arranged
        here, so what the build under test finds at its publish is a store some
        other run really produced.
        """
        _land(project)
        review_commands.rebuild_search_store(ProjectPaths.of(project))

    observed = _with_a_writer_between_the_read_and_the_publish(
        monkeypatch, a_first_landing_and_the_rebuild_that_follows_it
    )

    result = runner.invoke(app, ["review", "build", "--json"], catch_exceptions=False)

    assert observed.count == 0, (
        f"this build read {observed.count} records, so it is not the read-nothing face "
        f"the guard's third row is about"
    )
    assert result.exit_code == 1, result.stdout + (result.stderr or "")
    assert result.stdout == "", (
        "the refusal published a document on stdout, so a caller scripting `--json` "
        "reads a build that refused as one that succeeded with no records"
    )
    payload = json.loads(result.stderr)
    assert "read no evidence records" in payload["error"], (
        f"the refusal does not say this build read nothing: {payload['error']!r}. A build "
        f"that started in front of a landing has no count to give, and a sentence about "
        f"what it read would describe a stale read it never took"
    )
    assert "of them" not in payload["error"], (
        f"the refusal reports a count over a read that found nothing: {payload['error']!r}"
    )
    assert ".theurian/review/" in payload["remedy"]
    assert "theurian review build" in payload["remedy"]
    assert _stored_rows(project) == LANDED_RECORDS, (
        f"the store holds {_stored_rows(project)} records where the concurrent build "
        f"published {LANDED_RECORDS}: this build emptied a store it had nothing to put "
        f"in, at the one moment nothing downstream can tell that from an empty project"
    )


def test_a_corpus_an_operator_emptied_inside_the_window_is_published_rather_than_refused(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#636 face 2. RED before the guard was rekeyed onto the publish-time capture.

    Deleting evidence files is the retention remedy ADR-0030 decision 3 leaves an
    operator: ``.theurian/review/`` is source, no refetch rebuilds it, and there is
    no other way to take a landed record out. Deleting *all* of them while a build
    was between its read and its publish met a refusal keyed on that build's own
    read -- it had found three records and could keep none of them -- and the store
    went on serving every record the operator had just removed, under a cure
    telling them to wait for a concurrent run that did not exist.

    Keyed on the publish-time listing, the corpus is gone and the empty store is
    published, which is what honours the deletion. It cannot smuggle a survivor
    past it either: an empty listing fails the unchanged-fingerprint test for every
    record the read took, so an empty capture forces an empty load by construction.

    **The premise is measured, not assumed.** A build that read *nothing* over an
    already-emptied corpus publishes the same empty store at the same exit code, so
    the read's own count is asserted: without it this case would be green against
    an interleaving that never widened the window at all.
    """
    _land(project)
    seeded = runner.invoke(app, ["review", "build", "--json"], catch_exceptions=False)
    assert seeded.exit_code == 0, seeded.stderr
    assert _stored_rows(project) == LANDED_RECORDS, (
        "the store did not come up holding the landed records, so the publish below "
        "would not be observably a replacement"
    )

    observed = _with_a_writer_between_the_read_and_the_publish(
        monkeypatch, lambda: _delete_every_evidence_file(project)
    )

    result = runner.invoke(app, ["review", "build", "--json"], catch_exceptions=False)

    assert observed.count == LANDED_RECORDS, (
        f"this build read {observed.count} records where the corpus held "
        f"{LANDED_RECORDS}, so the deletion did not land inside its window and this "
        f"case drove the read-nothing row rather than the deletion row"
    )
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    published = json.loads(result.stdout)
    assert published["records"] == 0
    assert published["built"] is True
    assert _stored_rows(project) == 0, (
        f"the store still serves {_stored_rows(project)} records whose evidence files "
        f"an operator deleted, which is decision 3's only retention remedy silently "
        f"undone -- and the build that undid it refused, so nothing said so"
    )
