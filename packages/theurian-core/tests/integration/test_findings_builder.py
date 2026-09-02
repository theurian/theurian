"""The standalone findings rebuild service, source to store (ADR-0029 phase-2 slice-2).

Drives :class:`FindingsBuilder` against a **hermetic** git repository this file
authors -- a bare origin and a working clone, with ``Review-Finding:`` trailers
pushed to ``refs/remotes/origin/main`` -- read through the *real*
:class:`GitTrailerFindingSource` and landed in the *real*
:class:`SqliteReviewFindingStore`. So these tests exercise the whole git-to-store
path, not a fake of it, while their oracle is the source's own load rather than a
re-derivation of the store's algorithm.

**Hermetic means every git invocation ignores the developer's real
configuration.** ``_git`` pins ``GIT_CONFIG_GLOBAL``/``GIT_CONFIG_SYSTEM`` to
``os.devnull`` for every call, including ``init`` and ``clone``, not only the
identity-bearing ``commit``. Without it, every call here merged ``**os.environ``
and nothing else, so a developer's real ``~/.gitconfig`` with
``commit.gpgsign = true`` made this file's own fixture commits sign with their
live key -- a passphrase or hardware-token prompt with no test invoking one.

Three acceptance criteria live here, none pinned to a live count (the corpus
grows), all against a repo authored in the test:

- **AC-1 completeness**: the store holds exactly what the source returns --
  set-equality (a multiset over content, excluding the store-assigned position),
  both accepted and rejected.
- **AC-3 convergence**: history gains a commit and the rebuild converges to the
  new full set, the prior findings neither lost nor duplicated.
- **AC-6 derived reproducibility (ADR-0004)**: a deleted store rebuilds logically
  identically from git, and the rebuilt store contains *nothing that was not in
  git* and loses nothing that was -- a full two-way set-equality, so "adds no
  authority beyond git history" is pinned, not merely "loses nothing".
"""

from __future__ import annotations

import os
import subprocess
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytest

from theurian.application.findings_builder import FindingsBuilder, FindingsBuildRequest
from theurian.domain.ports.review_finding_store import (
    FindingQuery,
    FindingsDump,
    StoredFinding,
    StoredRejection,
)
from theurian.domain.review_finding import FindingLoad, RejectedTrailer, ReviewFinding
from theurian.infrastructure.git.trailer_source import GitTrailerFindingSource
from theurian.infrastructure.sqlite.findings_store import SqliteReviewFindingStore

pytestmark = pytest.mark.integration

#: The content of a finding excluding the store-assigned position: everything the
#: source hands out. Set-equality is taken over this, since the source has no
#: position to compare against.
#:
#: The committer date enters as a ``datetime`` on **both** sides -- the source's
#: field, and the stored TEXT parsed back -- so the comparison is over the instant
#: rather than its spelling (#405). The store deliberately does not keep the
#: committer's own offset: it writes a UTC-normalised, fixed-width instant so the
#: column is a chronological sort key. Comparing the strings would therefore fail
#: for a reason that is not a loss, and re-deriving the store's encoding here would
#: make this oracle agree with the store's algorithm by construction, which is the
#: shape this file's own header rules out.
_FindingContent = tuple[str | int | datetime | None, ...]
_RejectedContent = tuple[str, str, str]


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    """Run one git command as this fixture's isolated actor.

    ``GIT_CONFIG_GLOBAL``/``GIT_CONFIG_SYSTEM`` are pinned to ``os.devnull`` for
    every call here, not only the identity-bearing ``commit`` -- ``init`` and
    ``clone`` read global config too (a ``core.hooksPath`` or a clone template
    would otherwise run under the developer's real settings). Applied after
    ``env`` is merged, so it cannot be overridden by a caller that forgets it;
    the same pattern ``tests/integration/test_propose_cli.py`` and
    ``tests/unit/test_command_population.py`` use for the identical reason.
    """
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607 - git resolved via PATH, args are test-controlled
        cwd=root,
        check=True,
        capture_output=True,
        env={
            **(env if env is not None else os.environ),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        },
    )
    return result.stdout.decode("utf-8")


def _identity_env(when: str) -> dict[str, str]:
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "Tester",
        "GIT_AUTHOR_EMAIL": "tester@example.com",
        "GIT_COMMITTER_NAME": "Tester",
        "GIT_COMMITTER_EMAIL": "tester@example.com",
        "GIT_AUTHOR_DATE": when,
        "GIT_COMMITTER_DATE": when,
    }


def _origin_and_clone(tmp_path: Path) -> Path:
    """A bare origin and a working clone; returns the clone the source reads."""
    origin = tmp_path / "repo-origin.git"
    clone = tmp_path / "repo-clone"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", str(origin), str(clone))
    _git(clone, "config", "user.name", "Tester")
    _git(clone, "config", "user.email", "tester@example.com")
    return clone


def _commit(clone: Path, subject: str, *trailers: str, when: str = "2026-03-01T12:00:00") -> None:
    message = subject if not trailers else subject + "\n\n" + "\n".join(trailers)
    _git(clone, "commit", "--allow-empty", "-m", message, env=_identity_env(when))


def _publish(clone: Path) -> None:
    """Push main and refresh ``refs/remotes/origin/main``, the one ref the source reads."""
    _git(clone, "push", "origin", "main")
    _git(clone, "fetch", "origin")


def _finding_content(finding: ReviewFinding) -> _FindingContent:
    return (
        finding.commit_sha,
        finding.reviewer.value,
        finding.severity.value,
        finding.finding_text,
        finding.provider,
        finding.anchor.source_uri,
        finding.date,
        finding.pull_request,
        finding.family,
        finding.specialist,
    )


def _stored_finding_content(stored: StoredFinding) -> _FindingContent:
    return (
        stored.commit_sha,
        stored.reviewer,
        stored.severity,
        stored.finding_text,
        stored.provider,
        stored.source_uri,
        datetime.fromisoformat(stored.committed_at),
        stored.pull_request,
        stored.family,
        stored.specialist,
    )


def _rejected_content(entry: RejectedTrailer) -> _RejectedContent:
    return (entry.commit_sha, entry.raw_line, entry.reason)


def _stored_rejection_content(stored: StoredRejection) -> _RejectedContent:
    return (stored.commit_sha, stored.raw_line, stored.reason)


def _source_multisets(
    load: FindingLoad,
) -> tuple[Counter[_FindingContent], Counter[_RejectedContent]]:
    return (
        Counter(_finding_content(f) for f in load.accepted),
        Counter(_rejected_content(r) for r in load.rejected),
    )


def _store_multisets(
    dump: FindingsDump,
) -> tuple[Counter[_FindingContent], Counter[_RejectedContent]]:
    return (
        Counter(_stored_finding_content(f) for f in dump.findings),
        Counter(_stored_rejection_content(r) for r in dump.rejected),
    )


def _build(clone: Path, store_path: Path) -> SqliteReviewFindingStore:
    builder = FindingsBuilder(
        source=GitTrailerFindingSource(clone),
        store_factory=SqliteReviewFindingStore,
    )
    builder.build(FindingsBuildRequest(store_path=store_path))
    return SqliteReviewFindingStore(store_path)


# --- #404: one continuous hold, and the git read outside it -----------------


def test_the_build_publishes_inside_one_continuous_write_section(tmp_path: Path) -> None:
    """#404: exactly one hold, ``replace_all`` inside it, the git read before it.

    The sequence is the assertion, not merely that a lock was taken. Three
    distinguishable defects fail here and each has a shipped precedent:

    - **no hold at all** -- the shape this replaced -- yields ``["load", "write"]``,
      and two rebuilds then assemble at one working name;
    - **two sequential holds**, one around the assembly and one around the publish,
      yields ``[..., "enter", "exit", "enter", ...]``. That is #468's measured
      defect exactly: the window *between* two holds let a racing process act on
      state the first hold had half-built;
    - **the git read moved inside** yields ``["enter", "load", ...]``, which holds
      the project's single writer lock across a 30-second-bounded subprocess and
      blocks ``migrate apply`` for the length of a ``git log`` -- the reason
      ``migrate apply`` builds its own ``Project`` outside its hold.

    Driven with a recording section and a recording store rather than the real
    advisory lock, because a lock file cannot report *when* it was entered relative
    to the calls around it; the real lock's cross-process behaviour is
    ``test_findings_build_cli.py``'s concurrent-process race. This file names no
    lock class for that reason, which also keeps it out of
    ``test_connection_claims.py``'s exact one-file population -- that key admits a
    prose mention, and a member that only writes about the lock would make the
    population say something it does not mean.
    """
    events: list[str] = []

    @contextmanager
    def recording_section() -> Iterator[None]:
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    class RecordingSource:
        def load_findings(self) -> FindingLoad:
            events.append("load")
            return FindingLoad(accepted=(), rejected=())

    class RecordingStore:
        def __init__(self, path: Path) -> None:
            self.path = path

        def replace_all(self, load: FindingLoad) -> None:  # noqa: ARG002 - the port's signature
            events.append("write")

        def stamp(self) -> None:
            return None

        def is_current(self) -> bool:
            return False

        def dump(self) -> FindingsDump:
            return FindingsDump(findings=(), rejected=())

        def serve_findings(
            self,
            query: FindingQuery,  # noqa: ARG002 - the port's signature
            *,
            text_chars: int,  # noqa: ARG002 - the port's signature
        ) -> tuple[StoredFinding, ...]:
            # Present because the port declares it. The builder is a write path
            # and never serves, so this returns nothing rather than recording an
            # event; `events` below is the assertion that the build reached the
            # write and nothing else.
            return ()

    builder = FindingsBuilder(
        source=RecordingSource(),
        store_factory=RecordingStore,
        write_section=recording_section,
    )
    builder.build(FindingsBuildRequest(store_path=tmp_path / "unused.sqlite"))

    assert events == ["load", "enter", "write", "exit"]


def test_a_builder_can_run_twice_because_the_section_is_a_factory(tmp_path: Path) -> None:
    """#404: ``write_section`` is a factory, so a reused builder takes the hold twice.

    The real section is a generator-based context manager (the advisory lock's
    ``held``): entering the *same* instance twice raises, so passing a context
    manager rather than a factory would work once and fail on a builder's second
    build. Nothing in the shipped CLI reuses a builder today, which is exactly why
    this needs pinning rather than assuming.
    """
    entries: list[int] = []

    @contextmanager
    def counting_section() -> Iterator[None]:
        entries.append(len(entries))
        yield

    clone = _origin_and_clone(tmp_path)
    _commit(clone, "fix: a change (#1)", "Review-Finding: security HIGH — a finding")
    _publish(clone)

    builder = FindingsBuilder(
        source=GitTrailerFindingSource(clone),
        store_factory=SqliteReviewFindingStore,
        write_section=counting_section,
    )
    store_path = tmp_path / "state" / "theurian-findings-twice.sqlite"
    builder.build(FindingsBuildRequest(store_path=store_path))
    builder.build(FindingsBuildRequest(store_path=store_path))

    assert entries == [0, 1]
    assert len(SqliteReviewFindingStore(store_path).dump().findings) == 1


def test_store_holds_exactly_the_source_findings(tmp_path: Path) -> None:
    """AC-1: the rebuilt store holds exactly the accepted + rejected the source returns.

    Set-equality (a content multiset), not a count -- so it cannot rot as the
    corpus grows, and it holds for the rejected table too, which is populated here
    by a malformed trailer whose unknown reviewer the grammar refuses.
    """
    clone = _origin_and_clone(tmp_path)
    _commit(
        clone,
        "fix: first change (#1)",
        "Review-Finding: security HIGH — a security finding",
        "Review-Finding: adversarial LOW — an adversarial finding",
    )
    _commit(
        clone,
        "fix: second change (#2)",
        "Review-Finding: bogus MEDIUM — a line whose reviewer is unknown",
    )
    _publish(clone)

    source_load = GitTrailerFindingSource(clone).load_findings()
    store = _build(clone, tmp_path / "state" / "theurian-findings-x.sqlite")

    assert _store_multisets(store.dump()) == _source_multisets(source_load)
    # Not vacuous: the accepted and the rejected are both non-empty here.
    assert len(source_load.accepted) == 2
    assert len(source_load.rejected) == 1


def test_rebuild_converges_when_history_grows(tmp_path: Path) -> None:
    """AC-3: a new commit converges the store to the new full set, no loss or dup."""
    clone = _origin_and_clone(tmp_path)
    _commit(
        clone,
        "fix: change one (#1)",
        "Review-Finding: code-review HIGH — finding one",
        "Review-Finding: security MEDIUM — finding two",
    )
    _publish(clone)

    store_path = tmp_path / "state" / "theurian-findings-x.sqlite"
    store = _build(clone, store_path)
    before = store.dump()
    assert len(before.findings) == 2

    _commit(clone, "fix: change two (#2)", "Review-Finding: adversarial CRITICAL — finding three")
    _publish(clone)

    grown_load = GitTrailerFindingSource(clone).load_findings()
    store = _build(clone, store_path)
    after = store.dump()

    # Converged to the new full set exactly.
    assert _store_multisets(after) == _source_multisets(grown_load)
    assert len(after.findings) == 3
    # The prior findings survived, each exactly once (neither lost nor duplicated).
    before_texts = Counter(f.finding_text for f in before.findings)
    after_texts = Counter(f.finding_text for f in after.findings)
    for text, count in before_texts.items():
        assert after_texts[text] == count


def test_a_deleted_store_rebuilds_logically_identically(tmp_path: Path) -> None:
    """AC-6: a deleted store rebuilds identically, adding no authority beyond git.

    Standalone -- no index rebuild is involved. The rebuilt store is asserted equal
    to the first build (logical dump, not a file hash) *and* to the git source in
    both directions: it loses nothing git holds, and holds nothing git does not.
    """
    clone = _origin_and_clone(tmp_path)
    _commit(
        clone,
        "fix: a change (#1)",
        "Review-Finding: code-review HIGH — finding one",
        "Review-Finding: security LOW — finding two",
    )
    _commit(clone, "fix: another (#2)", "Review-Finding: adversarial MEDIUM — finding three")
    _publish(clone)

    store_path = tmp_path / "state" / "theurian-findings-x.sqlite"
    store = _build(clone, store_path)
    first_dump = store.dump()

    store_path.unlink()
    assert not store_path.exists()

    source_load = GitTrailerFindingSource(clone).load_findings()
    store = _build(clone, store_path)
    rebuilt_dump = store.dump()

    # Logically identical to the first build (refinement A: content, not bytes).
    assert rebuilt_dump == first_dump

    store_findings, store_rejected = _store_multisets(rebuilt_dump)
    source_findings, source_rejected = _source_multisets(source_load)
    # Loses nothing git holds ...
    assert source_findings - store_findings == Counter()
    assert source_rejected - store_rejected == Counter()
    # ... and holds nothing git does not (adds no authority beyond git history).
    assert store_findings - source_findings == Counter()
    assert store_rejected - source_rejected == Counter()
