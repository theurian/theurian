"""The project root and its descriptive reads come from the repository discovered from
the working directory (#981).

An inherited ``GIT_DIR``/``GIT_WORK_TREE`` names one repository's object store and another's
work tree; the CLI keeps that environment for the whole command, so a root chosen under it
would have the deciding reads answer for a repository the project is not in. Each case builds
real repositories under ``tmp_path``: ``R`` is the one the environment names, ``X`` the one the
work tree is actually inside. The rows after them pin the bytes of git's stdout (#1039).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
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


TRAILER = "x\n\nReview-Finding: code-review HIGH — from the sibling"


def _with_origin_main(path: Path, message: str) -> Path:
    repo = new_repo(path)
    sha = commit_file(repo, REL, SQL.decode(), message)
    git(repo, "update-ref", "refs/remotes/origin/main", sha)
    return repo


@pytest.mark.parametrize("suffix", [" ", "\t", "\n", "\r", "\u00a0", "\u3000"])
def test_a_project_directory_with_trailing_whitespace_is_not_mistaken_for_its_sibling(
    tmp_path: Path, suffix: str
) -> None:
    """Stripping git's output renames ``proj `` to ``proj``, adopting the sibling's findings."""
    project = _with_origin_main(tmp_path / f"proj{suffix}", "x\n\nno trailer")
    _with_origin_main(tmp_path / "proj", TRAILER)

    root = find_git_root(project)

    assert root is not None
    assert root.resolve() == project.resolve()
    assert GitTrailerFindingSource(root).load_findings().accepted == ()


_LOCALE_CHILD = textwrap.dedent(
    """
    import json, locale, sys
    from pathlib import Path
    from theurian.cli.context import find_git_root
    from theurian.infrastructure.git.trailer_source import GitTrailerFindingSource

    root = find_git_root(Path(sys.argv[1]))
    accepted = GitTrailerFindingSource(root).load_findings().accepted if root else []
    print(json.dumps({
        "encoding": locale.getencoding(),
        "utf8_mode": sys.flags.utf8_mode,
        "root": None if root is None else root.as_posix().encode("utf-8", "surrogateescape").hex(),
        "accepted": len(accepted),
    }))
    """
)


def test_a_non_utf8_locale_does_not_turn_the_project_root_into_its_mojibake_sibling(
    tmp_path: Path,
) -> None:
    """The root is git's bytes decoded as the filesystem does, whatever codec the locale names.

    UTF-8 mode is forced off in the child: under it (``PYTHONUTF8=1``, default from 3.15) a
    locale-decoding implementation is accidentally correct and the row could not fail.
    """
    project = _with_origin_main(tmp_path / "proj-é", "x\n\nno trailer")
    _with_origin_main(tmp_path / "proj-é".encode().decode("iso8859-1"), TRAILER)
    env = {**os.environ, "LC_ALL": "en_US.ISO8859-1", "LANG": "en_US.ISO8859-1", "PYTHONUTF8": "0"}

    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _LOCALE_CHILD, str(project)],
        env=env,
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout
    report = json.loads(out)

    assert report["utf8_mode"] == 0
    if report["encoding"].lower().replace("-", "").replace("_", "") not in {"iso88591", "latin1"}:
        pytest.skip(
            f"locale en_US.ISO8859-1 is not available here (child encoding {report['encoding']})"
        )
    assert bytes.fromhex(report["root"]).decode("utf-8") == project.resolve().as_posix()
    assert report["accepted"] == 0


def test_a_branch_name_that_is_not_utf8_does_not_make_default_branch_raise(
    tmp_path: Path,
) -> None:
    """git ref names are bytes; an undecodable one must degrade, not abort the command."""
    repo = new_repo(tmp_path / "X")
    git(repo, "symbolic-ref", "HEAD", os.fsdecode(b"refs/heads/caf\xe9"))

    assert default_branch(repo) == "caf\ufffd"


@pytest.mark.parametrize("url", ["https://example.invalid/x ", "https://example.invalid/x\n"])
def test_a_remote_url_with_trailing_whitespace_is_reported_as_stored(
    tmp_path: Path, url: str
) -> None:
    """Only git's own newline is a terminator; the url's trailing whitespace is part of it."""
    repo = new_repo(tmp_path / "X")
    git(repo, "config", "remote.origin.url", url)

    assert repository_url(repo) == url


def test_a_detached_head_reports_main_as_the_default_branch(tmp_path: Path) -> None:
    """With no branch to name, the recorded default stays ``main`` rather than an empty string."""
    repo = new_repo(tmp_path / "X")
    commit_file(repo, REL, SQL.decode())
    git(repo, "checkout", "-q", "--detach")

    assert default_branch(repo) == "main"


def test_a_repository_without_an_origin_reports_no_url(tmp_path: Path) -> None:
    """No remote is ``None``, not an empty string recorded as the repository url."""
    repo = new_repo(tmp_path / "X")

    assert repository_url(repo) is None
