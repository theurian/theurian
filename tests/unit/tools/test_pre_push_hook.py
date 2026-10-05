"""``tools/hooks/pre-push`` measures pushed refs against a throwaway repository."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

TOOLS = Path(__file__).resolve().parents[3] / "tools"
HOOK = Path(os.environ.get("PRE_PUSH_HOOK", TOOLS / "hooks" / "pre-push"))
ZERO = "0" * 40


def _env(repo: Path) -> dict[str, str]:
    return {**os.environ, "GIT_CEILING_DIRECTORIES": str(repo.parent)}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed argv in a throwaway repository
        [  # noqa: S607 - git resolved via PATH
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_env(repo),
    ).stdout.strip()


def _commit(repo: Path, name: str, n: int) -> str:
    (repo / name).write_text("x\n" * n)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", name)
    return _git(repo, "rev-parse", "HEAD")


def _branch(repo: Path, name: str, n: int, start: str = "main") -> str:
    _git(repo, "checkout", "-q", "-b", name, start)
    return _commit(repo, f"{name}.txt", n)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "tools").mkdir(parents=True)
    shutil.copy(TOOLS / "pr_size.py", root / "tools")
    _git(root, "init", "-q", "-b", "main")
    _commit(root, "base.txt", 1)
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    return root


def _push(repo: Path, lines: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv in a throwaway repository
        ["/bin/sh", str(HOOK)],
        input=lines,
        check=False,
        capture_output=True,
        text=True,
        cwd=repo,
        env={**_env(repo), **env},
    )


def test_under_limit_passes_and_prints_size(repo: Path) -> None:
    sha = _branch(repo, "feat", 5)
    r = _push(repo, f"refs/heads/feat {sha} refs/heads/feat {ZERO}\n")
    assert r.returncode == 0
    assert "pr-size: commits=1 files=1 lines=5" in r.stdout


def test_over_limit_fails_with_script_error(repo: Path) -> None:
    sha = _branch(repo, "big", 1001)
    r = _push(repo, f"refs/heads/big {sha} refs/heads/big {ZERO}\n")
    assert r.returncode != 0
    assert "error: lines 1001 exceeds" in r.stdout


@pytest.mark.parametrize("typo", [None, "nope/typo"])
def test_unresolved_base_fails_closed(repo: Path, typo: str | None) -> None:
    sha = _branch(repo, "feat", 5)
    if typo is None:
        _git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    env = {} if typo is None else {"PR_SIZE_BASE": typo}
    r = _push(repo, f"refs/heads/feat {sha} refs/heads/feat {ZERO}\n", **env)
    assert r.returncode != 0
    assert f"'{typo or 'origin/main'}' does not resolve" in r.stderr
    assert "pr-size:" not in r.stdout


def test_deletion_and_tag_are_not_measured(repo: Path) -> None:
    sha = _branch(repo, "big", 1001)
    r = _push(
        repo,
        f"(delete) {ZERO} refs/heads/old {sha}\nrefs/tags/v1 {sha} refs/tags/v1 {ZERO}\n",
    )
    assert r.returncode == 0
    assert r.stdout == ""


def test_pushing_the_base_branch_is_not_measured(repo: Path) -> None:
    _git(repo, "checkout", "-q", "main")
    sha = _commit(repo, "sync.txt", 1500)
    r = _push(repo, f"refs/heads/main {sha} refs/heads/main {ZERO}\n")
    assert r.returncode == 0
    assert "pr-size:" not in r.stdout


def test_base_branch_skip_does_not_hide_another_line(repo: Path) -> None:
    _git(repo, "checkout", "-q", "main")
    sync = _commit(repo, "sync.txt", 1500)
    big = _branch(repo, "big", 1001, start="origin/main")
    r = _push(
        repo,
        f"refs/heads/main {sync} refs/heads/main {ZERO}\n"
        f"refs/heads/big {big} refs/heads/big {ZERO}\n",
    )
    assert r.returncode != 0
    assert "lines=1001 " in r.stdout


def test_stacked_base_measures_against_parent(repo: Path) -> None:
    _branch(repo, "parent", 20)
    sha = _branch(repo, "child", 3, start="parent")
    r = _push(repo, f"refs/heads/child {sha} refs/heads/child {ZERO}\n", PR_SIZE_BASE="parent")
    assert r.returncode == 0
    assert "pr-size: commits=1 files=1 lines=3" in r.stdout


def test_measures_pushed_sha_not_checkout(repo: Path) -> None:
    sha = _branch(repo, "feat", 7)
    _git(repo, "checkout", "-q", "main")
    r = _push(repo, f"refs/heads/feat {sha} refs/heads/feat {ZERO}\n")
    assert "pr-size: commits=1 files=1 lines=7" in r.stdout


def test_several_lines_one_bad_fails_push(repo: Path) -> None:
    ok = _branch(repo, "ok", 2)
    big = _branch(repo, "big", 1001, start="main")
    r = _push(
        repo,
        f"refs/heads/ok {ok} refs/heads/ok {ZERO}\nrefs/heads/big {big} refs/heads/big {ok}\n",
    )
    assert r.returncode != 0
    assert "lines=2 " in r.stdout and "lines=1001 " in r.stdout
