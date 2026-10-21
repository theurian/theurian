"""Real-repository builders and spies for the deciding-``git``-spawn isolation tests (#981).

Every repository lives under the caller's ``tmp_path``; every ``git`` call here runs
with the developer's global and system config pinned away. Nothing is written to a
shared ``.git/config``: the partial-clone settings go on a temporary origin only.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

_IDENTITY = (
    "-c",
    "user.email=test@example.com",
    "-c",
    "user.name=Test",
    "-c",
    "commit.gpgsign=false",
)


def _env(extra: dict[str, str] | None = None) -> dict[str, str]:
    return {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        **(extra or {}),
    }


def git(cwd: Path, *args: str, extra_env: dict[str, str] | None = None) -> str:
    """One hermetic fixture ``git`` call; answers stdout."""
    return subprocess.run(  # noqa: S603
        ["git", *_IDENTITY, *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env=_env(extra_env),
    ).stdout


def new_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "-c", "init.defaultBranch=main", "init", "-q")
    return path


def commit_file(repo: Path, relative: str, body: str, message: str = "change") -> str:
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    git(repo, "add", relative)
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


def serving_origin(path: Path) -> Path:
    """A repository a ``--filter`` clone and a fetch-by-sha can be served from."""
    repo = new_repo(path)
    git(repo, "config", "uploadpack.allowFilter", "true")
    git(repo, "config", "uploadpack.allowAnySHA1InWant", "true")
    return repo


def clone(origin: Path, destination: Path, *flags: str) -> Path:
    git(origin.parent, "clone", "-q", *flags, f"file://{origin}", str(destination))
    return destination


def missing_objects(repo: Path) -> int:
    """Objects the repository references and does not hold; never fetches to count."""
    listing = git(
        repo,
        "rev-list",
        "--objects",
        "--all",
        "--missing=print",
        extra_env={"GIT_NO_LAZY_FETCH": "1"},
    )
    return sum(1 for line in listing.splitlines() if line.startswith("?"))


def traced_fetches[T](
    monkeypatch: pytest.MonkeyPatch, trace: Path, call: Callable[[], T]
) -> tuple[T, list[str]]:
    """Run *call* under ``GIT_TRACE`` and answer its result with every fetch it spawned.

    ``child_environment`` does not strip ``GIT_TRACE``, so a lazy fetch -- whether
    the built-in or a child process -- is visible as a ``fetch`` trace line.
    """
    monkeypatch.setenv("GIT_TRACE", str(trace))
    try:
        result = call()
    finally:
        monkeypatch.delenv("GIT_TRACE")
    lines = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []
    return result, [
        line for line in lines if "fetch" in line and ("run_command" in line or "built-in" in line)
    ]
