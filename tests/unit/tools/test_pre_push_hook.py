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
    env = {k: v for k, v in os.environ.items() if k != "PR_SIZE_BASE"}
    return {
        **env,
        "GIT_CEILING_DIRECTORIES": str(repo.parent),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }


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


def _push(
    repo: Path, lines: str, hook: Path = HOOK, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv in a throwaway repository
        ["/bin/sh", str(hook)],
        input=lines,
        check=False,
        capture_output=True,
        text=True,
        cwd=repo,
        env={**_env(repo), **(env or {})},
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
    r = _push(repo, f"refs/heads/feat {sha} refs/heads/feat {ZERO}\n", env=env)
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


@pytest.mark.parametrize("local", ["HEAD", "sha"])
def test_a_push_typed_as_head_or_sha_is_measured(repo: Path, local: str) -> None:
    sha = _branch(repo, "big", 1001)
    r = _push(repo, f"{'HEAD' if local == 'HEAD' else sha} {sha} refs/heads/big {ZERO}\n")
    assert r.returncode != 0
    assert "lines=1001 " in r.stdout


def test_a_local_base_with_a_slash_does_not_skip_a_branch_named_like_its_tail(
    repo: Path,
) -> None:
    _git(repo, "branch", "feat/parent", "main")
    sha = _branch(repo, "parent", 1001)
    r = _push(
        repo,
        f"refs/heads/parent {sha} refs/heads/parent {ZERO}\n",
        env={"PR_SIZE_BASE": "feat/parent"},
    )
    assert r.returncode != 0
    assert "lines=1001 " in r.stdout


def test_a_remote_base_with_a_slash_skips_only_its_own_branch(repo: Path) -> None:
    _git(repo, "update-ref", "refs/remotes/origin/fix/x", "main")
    sha = _branch(repo, "big", 1001)
    env = {"PR_SIZE_BASE": "origin/fix/x"}
    skipped = _push(repo, f"refs/heads/big {sha} refs/heads/fix/x {ZERO}\n", env=env)
    measured = _push(repo, f"refs/heads/big {sha} refs/heads/x {ZERO}\n", env=env)
    assert (skipped.returncode, skipped.stdout) == (0, "")
    assert measured.returncode != 0
    assert "lines=1001 " in measured.stdout


def test_an_installed_copy_refuses_a_tree_without_tools(repo: Path) -> None:
    installed = repo / ".git" / "hooks" / "pre-push"
    shutil.copy(HOOK, installed)
    _git(repo, "checkout", "-q", "-b", "notools", "main")
    _git(repo, "rm", "-rq", "tools")
    _git(repo, "commit", "-qm", "drop tools")
    sha = _git(repo, "rev-parse", "HEAD")
    r = _push(repo, f"refs/heads/notools {sha} refs/heads/notools {ZERO}\n", hook=installed)
    assert not (repo / "tools").exists()
    assert r.returncode != 0


def test_stacked_base_measures_against_parent(repo: Path) -> None:
    _branch(repo, "parent", 20)
    sha = _branch(repo, "child", 3, start="parent")
    r = _push(
        repo, f"refs/heads/child {sha} refs/heads/child {ZERO}\n", env={"PR_SIZE_BASE": "parent"}
    )
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
