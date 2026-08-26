"""The git-history trailer source against real repositories (ADR-0029).

Exercises the adapter's I/O contract -- ``origin/main`` scoping (the embargo
linchpin), the loss-free mapping over live history, provenance anchoring, and a
total deterministic order -- against real ``git`` repositories, because those are
exactly the properties a pure test cannot reach.
"""

from __future__ import annotations

import os
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from theurian.domain.review_finding import (
    SEPARATOR,
    TRAILER_KEY,
    FindingSeverity,
    MalformedTrailerError,
    ReviewerToken,
)
from theurian.infrastructure.git.trailer_source import (
    GitHistoryUnavailableError,
    GitTrailerFindingSource,
)

pytestmark = pytest.mark.integration


# --- git fixture helpers ---------------------------------------------------


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607 - git resolved via PATH, args are test-controlled
        cwd=root,
        check=True,
        capture_output=True,
        env=env,
    )
    return result.stdout.decode("utf-8")


def _git_ok(root: Path, *args: str) -> bool:
    """True when ``git *args`` exits zero -- for a ref-existence probe."""
    return (
        subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607 - git resolved via PATH, args are test-controlled
            cwd=root,
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )


def _date_env(when: str) -> dict[str, str]:
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "Tester",
        "GIT_AUTHOR_EMAIL": "tester@example.com",
        "GIT_COMMITTER_NAME": "Tester",
        "GIT_COMMITTER_EMAIL": "tester@example.com",
        "GIT_AUTHOR_DATE": when,
        "GIT_COMMITTER_DATE": when,
    }


def _origin_and_clone(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin.git"
    clone = tmp_path / "clone"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", str(origin), str(clone))
    _git(clone, "config", "user.name", "Tester")
    _git(clone, "config", "user.email", "tester@example.com")
    return origin, clone


def _commit(clone: Path, subject: str, *trailers: str, when: str = "2026-03-01T12:00:00") -> str:
    message = subject if not trailers else subject + "\n\n" + "\n".join(trailers)
    _git(clone, "commit", "--allow-empty", "-m", message, env=_date_env(when))
    return _git(clone, "rev-parse", "HEAD").strip()


def _publish(clone: Path) -> None:
    """Push main and refresh the ``origin/main`` tracking ref the adapter reads."""
    _git(clone, "push", "origin", "main")
    _git(clone, "fetch", "origin")


# --- AC-3: the source reads only public origin/main (the embargo linchpin) --


def test_source_reads_only_origin_main_not_local_branches(tmp_path: Path) -> None:
    """A trailer on a local, unpushed branch is not ingested -- only origin/main.

    This is decision 6's structural embargo protection: the embargoed finding
    lives off public ``main``, so it must not reach the source. The unpushed
    branch is reachable via ``git log --all`` and via ``HEAD`` while checked out,
    so an adapter that read either would leak it; reading ``origin/main`` does not.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit(clone, "fix: public change (#5)", "Review-Finding: security HIGH — a public finding")
    _publish(clone)

    _git(clone, "checkout", "-b", "embargo")
    _commit(
        clone,
        "fix: embargoed change (#6)",
        "Review-Finding: security CRITICAL — an embargoed finding",
    )
    # Deliberately NOT published. Leave HEAD on the embargo branch to prove the
    # adapter pins origin/main rather than reading the current branch.

    findings = GitTrailerFindingSource(clone).load_findings()
    texts = [f.finding_text for f in findings]
    assert "a public finding" in texts
    assert "an embargoed finding" not in texts


def test_all_would_have_leaked_the_local_branch(tmp_path: Path) -> None:
    """The control for AC-3: the embargoed commit really is reachable via --all.

    Without this, the scoping test could pass merely because the branch commit
    was unreachable for an unrelated reason. Reading every ref finds it; the
    adapter's ``origin/main`` does not.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit(clone, "fix: public (#5)", "Review-Finding: security HIGH — a public finding")
    _publish(clone)
    _git(clone, "checkout", "-b", "embargo")
    _commit(
        clone, "fix: embargoed (#6)", "Review-Finding: security CRITICAL — an embargoed finding"
    )

    all_bodies = _git(clone, "log", "--all", "--format=%b")
    assert "an embargoed finding" in all_bodies  # --all sees it; the adapter must not


# --- AC-4: each record anchors to its commit -------------------------------


def test_each_finding_anchors_to_its_commit(tmp_path: Path) -> None:
    _origin, clone = _origin_and_clone(tmp_path)
    sha = _commit(clone, "fix: a change (#7)", "Review-Finding: adversarial MEDIUM — a finding")
    _publish(clone)

    (finding,) = GitTrailerFindingSource(clone).load_findings()
    assert finding.anchor.provider == "git"
    assert finding.provider == "git"
    assert finding.anchor.commit_sha == sha
    assert finding.commit_sha == sha
    assert finding.pull_request == 7


# --- AC-6: a total, stable order; two runs are byte-identical ---------------


def test_two_runs_produce_a_byte_identical_sequence(tmp_path: Path) -> None:
    """Determinism, and a total order that is not merely git's emission order.

    The commits are made oldest-to-newest and git emits newest-first, so an
    adapter that returned git's order would produce ``[newer, older]``. The total
    sort key orders oldest-first, which is what this asserts -- so dropping the
    sort is a killable mutation, not a no-op against already-deterministic output.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    older = _commit(
        clone, "fix: older (#1)", "Review-Finding: security LOW — older", when="2026-01-01T00:00:00"
    )
    newer = _commit(
        clone,
        "fix: newer (#2)",
        "Review-Finding: security HIGH — newer",
        when="2026-06-01T00:00:00",
    )
    _publish(clone)

    source = GitTrailerFindingSource(clone)
    first = source.load_findings()
    second = source.load_findings()
    assert first == second  # byte-identical sequence across runs
    assert [f.commit_sha for f in first] == [older, newer]  # oldest-first total order
    assert [f.finding_text for f in first] == ["older", "newer"]


def test_multiple_trailers_on_one_commit_all_map_in_body_order(tmp_path: Path) -> None:
    """Loss-free within a commit: N trailers on one commit map to N records.

    Their positions within the commit break the (date, sha) tie, so the order is
    the order they were authored -- a co-located-trailer commit is the standard
    shape here (17 on one commit in real history), not an edge case.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    sha = _commit(
        clone,
        "fix: a cluster (#9)",
        "Review-Finding: code-review HIGH — first",
        "Review-Finding: security MEDIUM — second",
        "Review-Finding: adversarial LOW — third",
    )
    _publish(clone)

    findings = GitTrailerFindingSource(clone).load_findings()
    assert [f.finding_text for f in findings] == ["first", "second", "third"]
    assert {f.commit_sha for f in findings} == {sha}


# --- no silent drop, and an unreachable ref is an error --------------------


def test_a_keyed_but_malformed_trailer_fails_the_load(tmp_path: Path) -> None:
    """A line carrying the key but not the grammar is refused, never dropped.

    The loss-free guarantee (AC-1) forbids silently skipping a keyed line, so a
    malformed one surfaces as an error rather than a missing record.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit(clone, "fix: bad trailer (#3)", "Review-Finding: reviewer-x HIGH — text")
    _publish(clone)

    with pytest.raises(MalformedTrailerError):
        GitTrailerFindingSource(clone).load_findings()


def test_a_missing_public_ref_raises(tmp_path: Path) -> None:
    """A repo with no origin/main is an error with a fetch remedy, not empty output."""
    repo = tmp_path / "repo"
    _git(tmp_path, "init", "-b", "main", str(repo))
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "config", "user.email", "tester@example.com")
    _commit(repo, "chore: local only", "Review-Finding: security LOW — unreachable")

    with pytest.raises(GitHistoryUnavailableError) as caught:
        GitTrailerFindingSource(repo).load_findings()
    assert "fetch" in caught.value.remedy


# --- AC-1: the live population maps loss-free ------------------------------


def _repo_root() -> Path:
    here = Path(__file__).resolve().parent
    return Path(_git(here, "rev-parse", "--show-toplevel").strip())


def _origin_main_present(repo: Path) -> bool:
    return _git_ok(repo, "rev-parse", "--verify", "--quiet", "origin/main")


def _remainder(trailer_line: str) -> str:
    """The finding text as computed independently of the adapter."""
    body = trailer_line[len(TRAILER_KEY) :]
    if body.startswith(" "):
        body = body[1:]
    return body.split(SEPARATOR, 1)[1]


def _trailing_pr(subject: str) -> int | None:
    """The trailing ``(#N)``, computed independently of the domain helper."""
    stripped = subject.rstrip()
    if not stripped.endswith(")"):
        return None
    open_paren = stripped.rfind("(#")
    if open_paren == -1:
        return None
    token = stripped[open_paren + 2 : -1]
    return int(token) if token.isdigit() else None


def test_live_origin_main_maps_every_trailer_loss_free() -> None:
    """AC-1: one record per live trailer, byte-identical text, all fields set.

    Counts the live population from a raw ``git log`` and asserts a total mapping
    against it, rather than hard-coding the current count -- the number rises with
    every review round, and the test must track it.

    This test is deliberately non-hermetic: it reads whatever ``origin/main`` holds
    now, which is what makes it the canary ADR-0029 decision 2 wants. It reddens if
    a novel reviewer or severity spelling lands on public ``main`` that the parser's
    accepted vocabulary does not yet cover -- exactly how the ``code`` alias
    surfaced (a merged PR abbreviated ``code-review`` to ``code``). When it reddens
    the fix is to widen the parser's accepted set (a recorded grammar change), not
    to loosen this assertion: the parser's vocabulary must stay a superset of the
    live installed base.
    """
    repo = _repo_root()
    if not _origin_main_present(repo):
        pytest.skip("origin/main is not present in this checkout")

    findings = GitTrailerFindingSource(repo).load_findings()

    raw = _git(repo, "log", "origin/main", "--format=%b")
    trailer_lines = [ln for ln in raw.split("\n") if ln.startswith(TRAILER_KEY)]

    # Total mapping: one record per keyed line, and there really are some.
    assert len(findings) == len(trailer_lines) > 0

    # Byte-identity of the opaque remainder, as a multiset.
    assert Counter(f.finding_text for f in findings) == Counter(
        _remainder(ln) for ln in trailer_lines
    )

    # Every decision-1 field is populated; family/specialist stay derived (None).
    subjects: dict[str, str] = {}
    for finding in findings:
        assert isinstance(finding.reviewer, ReviewerToken)
        assert isinstance(finding.severity, FindingSeverity)
        assert finding.finding_text
        assert finding.provider == "git"
        assert finding.commit_sha
        assert finding.date.tzinfo is not None
        assert finding.family is None
        assert finding.specialist is None
        subjects.setdefault(finding.commit_sha, "")

    # pullRequest cross-checks against each commit's own trailing (#N),
    # computed by an independent helper so the adapter's rule is really tested.
    for sha in subjects:
        subjects[sha] = _git(repo, "log", "-1", "--format=%s", sha).strip()
    for finding in findings:
        assert finding.pull_request == _trailing_pr(subjects[finding.commit_sha])
