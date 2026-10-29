"""The project root and its descriptive reads come from the repository the work tree is in (#981).

An inherited ``GIT_DIR``/``GIT_WORK_TREE`` names one repository's object store and another's
work tree; the CLI keeps that environment for the whole command, so a root chosen under it
would have the deciding reads answer for a repository the project is not in. Each case builds
real repositories under ``tmp_path``: ``R`` is the one the environment names, ``X`` the one the
work tree is actually inside.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from git_isolation_fixtures import (
    TRACE_CASES,
    commit_file,
    git,
    inherit_tracing,
    new_repo,
    tracing_reaches_stdout,
)

from theurian.cli.context import current_commit, default_branch, find_git_root, repository_url
from theurian.domain.review import FixCommitVerdict
from theurian.infrastructure.git.committed_check import CommittedMigrationCheck, HeadComparison
from theurian.infrastructure.git.fix_commit_check import FixCommitCheck
from theurian.infrastructure.git.trailer_source import GitTrailerFindingSource

pytestmark = pytest.mark.integration

REL = "m/0001.sql"
SQL = b"CREATE TABLE t (x INTEGER);\n"
R_URL = "https://example.invalid/r.git"
X_URL = "https://example.invalid/x.git"


def _repo(path: Path, *, branch: str, url: str, message: str) -> tuple[Path, str]:
    repo = new_repo(path)
    git(repo, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
    sha = commit_file(repo, REL, SQL.decode(), message)
    git(repo, "remote", "add", "origin", url)
    return repo, sha


@pytest.fixture
def two_repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, str, str]:
    """``R`` (named by ``GIT_DIR``) and ``X`` (holding the work tree); the environment is set."""
    r, r_sha = _repo(tmp_path / "R", branch="rbranch", url=R_URL, message="r\n\nno trailer")
    x, x_sha = _repo(
        tmp_path / "X",
        branch="xbranch",
        url=X_URL,
        message="x\n\nReview-Finding: code-review HIGH — from X",
    )
    git(x, "update-ref", "refs/remotes/origin/main", x_sha)
    (x / "w").mkdir()
    monkeypatch.setenv("GIT_DIR", str(r / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(x / "w"))
    return r, x, r_sha, x_sha


def test_an_inherited_git_dir_does_not_move_the_root_into_another_repository(
    two_repos: tuple[Path, Path, str, str],
) -> None:
    """Root discovery must not honour the inherited ``GIT_DIR`` the deciding spawns strip."""
    _r, x, _r_sha, _x_sha = two_repos

    root = find_git_root(x / "w")

    assert root is not None
    assert root.resolve() == x.resolve()


def test_the_deciding_reads_answer_for_the_repository_the_work_tree_is_in(
    two_repos: tuple[Path, Path, str, str],
) -> None:
    """Under the same environment all three deciding reads answer for X, none for R."""
    _r, x, _r_sha, x_sha = two_repos
    root = find_git_root(x / "w")
    assert root is not None

    committed = CommittedMigrationCheck(root).compare_to_head(REL, SQL)
    fix = FixCommitCheck(root).verify(x_sha, REL)
    texts = [f.finding_text for f in GitTrailerFindingSource(root).load_findings().accepted]

    assert (committed, fix, texts) == (
        HeadComparison.COMMITTED,
        FixCommitVerdict.VERIFIED,
        ["from X"],
    )


def test_the_descriptive_reads_name_the_chosen_root_not_the_repository_git_dir_names(
    two_repos: tuple[Path, Path, str, str],
) -> None:
    """Head, branch and url recorded for the project must be X's, as the root is."""
    _r, x, r_sha, x_sha = two_repos
    assert r_sha != x_sha
    root = find_git_root(x / "w")
    assert root is not None

    described = (current_commit(root), default_branch(root), repository_url(root))

    assert described == (x_sha, "xbranch", X_URL)


def test_a_ceiling_directory_stops_root_discovery_below_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ceiling may only shorten the search; losing it finds a root the operator fenced off."""
    x = new_repo(tmp_path / "X")
    (x / "w").mkdir()
    control = find_git_root(x / "w")
    assert control is not None and control.resolve() == x.resolve()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(x))

    assert find_git_root(x / "w") is None


@pytest.mark.parametrize("case", TRACE_CASES)
def test_inherited_tracing_does_not_change_the_root_or_the_descriptive_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """Trace lines written to the captured stdout must not become a sha, a branch or a url."""
    repo, sha = _repo(tmp_path / "X", branch="xbranch", url=X_URL, message="x")
    (repo / "w").mkdir()
    inherit_tracing(monkeypatch, tmp_path, case)
    assert tracing_reaches_stdout(repo), "the tracing does not reach an unisolated git"

    root = find_git_root(repo / "w")

    assert root is not None and root.resolve() == repo.resolve()
    assert (current_commit(root), default_branch(root), repository_url(root)) == (
        sha,
        "xbranch",
        X_URL,
    )
