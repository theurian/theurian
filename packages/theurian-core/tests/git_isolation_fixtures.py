"""Real-repository builders and spies for the deciding-``git``-spawn isolation tests (#981).

Every repository lives under the caller's ``tmp_path``; every ``git`` call here runs
with the developer's global and system config pinned away. Nothing is written to a
shared ``.git/config``: the partial-clone settings go on a temporary origin only.
"""

from __future__ import annotations

import functools
import os
import re
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
    inherited = {k: v for k, v in os.environ.items() if k != "GIT_NO_LAZY_FETCH"}
    return {
        **inherited,
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


def parse_git_version(text: str) -> tuple[int, int]:
    match = re.search(r"git version (\d+)\.(\d+)", text)
    assert match, text
    return int(match[1]), int(match[2])


@functools.cache
def git_version() -> tuple[int, int]:
    return parse_git_version(git(Path.cwd(), "--version"))


def forge_commit_graph(
    repo: Path, sha: str, *, tree: str | None = None, parent: str | None = None
) -> None:
    """Write a commit-graph, then overwrite *sha*'s root tree and/or first parent in it (SHA-1)."""
    tips = git(repo, "rev-list", "--all") + "".join(f"{x}\n" for x in (sha, parent) if x)
    subprocess.run(
        ["git", "commit-graph", "write", "--stdin-commits"],  # noqa: S607
        cwd=repo,
        input=tips,
        text=True,
        check=True,
        env=_env(),
    )
    path = repo / ".git" / "objects" / "info" / "commit-graph"
    data = bytearray(path.read_bytes())
    assert data[:4] == b"CGPH" and data[5] == 1, "SHA-1 commit-graph only"
    chunks = {
        bytes(data[8 + 12 * i : 12 + 12 * i]): int.from_bytes(data[12 + 12 * i : 20 + 12 * i])
        for i in range(data[6] + 1)
    }
    count = int.from_bytes(data[chunks[b"OIDF"] + 1020 : chunks[b"OIDF"] + 1024])
    oids = [data[chunks[b"OIDL"] + 20 * i :][:20].hex() for i in range(count)]
    record = chunks[b"CDAT"] + 36 * oids.index(sha)
    if tree:
        data[record : record + 20] = bytes.fromhex(tree)
    if parent:
        data[record + 20 : record + 24] = oids.index(parent).to_bytes(4)
    path.chmod(0o644)
    path.write_bytes(data)
