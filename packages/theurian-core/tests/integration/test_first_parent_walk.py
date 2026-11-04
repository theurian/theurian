"""``walk_first_parent`` reads first-parent history at ``HEAD`` and nothing ambient (ADR-0040 d2).

Every repository is built under ``tmp_path``. Each lever is first shown moving an unhardened
read -- its positive control -- and then shown leaving the walk unchanged.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from git_isolation_fixtures import (
    TRACE_CASES,
    commit_file,
    forge_commit_graph,
    git,
    inherit_tracing,
    new_repo,
)

from theurian.infrastructure.git import first_parent_walk
from theurian.infrastructure.git.first_parent_walk import (
    FirstParentWalk,
    GitWalkRefusedError,
    WalkedCommit,
    walk_first_parent,
)

pytestmark = pytest.mark.integration

MAPPING = '[submodule "m"]\n\tpath = m\n\turl = ./m\n'
IGNORED = MAPPING + "\tignore = all\n"
NOTHING = FirstParentWalk(None, (), None, depth_bounded=False, budget_bounded=False)


@pytest.fixture(autouse=True)
def _no_developer_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The positive controls read config as git finds it, so the developer's own is moved away."""
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)


def _history(repo: Path) -> Path:
    """Newest first: an empty commit; a Latin-1 message adding an OID-named path, ``z.txt`` and
    a non-UTF-8 path; a submodule bump; its addition, whose committed ``.gitmodules`` ignores it
    (the worktree's does not); a side-branch merge; ``main.txt``; the root."""
    new_repo(repo)
    root = commit_file(repo, "a.txt", "a\n", "root")
    git(repo, "checkout", "-q", "-b", "side")
    commit_file(repo, "side.txt", "s\n", "side")
    git(repo, "checkout", "-q", "main")
    main = commit_file(repo, "main.txt", "m\n", "main")
    git(repo, "merge", "-q", "--no-ff", "side", "-m", "merge")
    (repo / ".gitmodules").write_text(IGNORED, encoding="utf-8")
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{root},m")
    git(repo, "add", ".gitmodules")
    git(repo, "commit", "-q", "-m", "add submodule")
    git(repo, "update-index", "--cacheinfo", f"160000,{main},m")
    git(repo, "commit", "-q", "-m", "bump submodule")
    for name in (root, "z.txt"):
        (repo / name).write_text("z\n", encoding="utf-8")
    git(repo, "add", root, "z.txt")
    blob = git(repo, "rev-parse", ":z.txt").strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{os.fsdecode(b'\xff.txt')}")
    latin = repo.parent / "latin.msg"
    latin.write_bytes(b"caf\xe9\n")
    git(repo, "-c", "i18n.commitEncoding=ISO-8859-1", "commit", "-q", "-F", str(latin))
    git(repo, "commit", "-q", "--allow-empty", "-m", "empty")
    (repo / ".gitmodules").write_text(MAPPING, encoding="utf-8")
    return repo


def _naive(repo: Path) -> bytes:
    """The walk's reads with no hardening, under whatever the process environment holds."""

    def run(*args: str, feed: bytes | None = None) -> bytes:
        return subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=repo,
            input=feed,
            capture_output=True,
            check=False,
        ).stdout

    revs = run("rev-list", "--first-parent", "HEAD")
    paths = run("diff-tree", "--stdin", "-r", "--root", "--name-only", "-z", feed=revs)
    log = run("log", "--first-parent", "--name-only", "-z", "--format=%H%x00%B", "HEAD")
    return b"\1".join((revs, paths, log))


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _refused(root: Path) -> GitWalkRefusedError:
    with pytest.raises(GitWalkRefusedError) as caught:
        walk_first_parent(root)
    refusal = caught.value
    assert "'git " in refusal.remedy, f"the remedy names nothing to run: {refusal.remedy!r}"
    text = str(refusal) + refusal.remedy
    assert not [leak for leak in (str(root), ".txt", "caf") if leak in text], text
    return refusal


def test_the_walk_reads_each_first_parent_commit_with_its_paths_and_raw_message(
    tmp_path: Path,
) -> None:
    repo = _history(tmp_path / "repo")
    shas = git(repo, "rev-list", "--first-parent", "HEAD").split()

    walk = walk_first_parent(repo)

    assert (walk.walk_commit, [commit.sha for commit in walk.commits]) == (shas[0], shas)
    assert [commit.paths for commit in walk.commits] == [
        (),
        (shas[-1], "z.txt"),
        ("m",),
        (".gitmodules", "m"),
        ("side.txt",),
        ("main.txt",),
        ("a.txt",),
    ]
    assert [commit.message for commit in walk.commits[:3]] == [
        b"empty\n",
        "café\n".encode(),
        b"bump submodule\n",
    ]
    assert all(commit.committed_at and commit.committed_at.tzinfo for commit in walk.commits)
    assert (walk.shallow_digest, walk.depth_bounded, walk.budget_bounded) == (None, False, False)
    assert walk_first_parent(repo) == walk


Lever = Callable[[Path, Path, pytest.MonkeyPatch], object]


def _config(*pairs: tuple[str, str]) -> Lever:
    return lambda repo, _t, _mp: [git(repo, "config", key, value) for key, value in pairs]


HOSTILE = (
    IGNORED + "[diff]\n\tignoreSubmodules = all\n[log]\n\tshowRoot = false\n"
    "[i18n]\n\tlogOutputEncoding = ISO-8859-1\n"
)


def _rev(repo: Path, name: str) -> str:
    return git(repo, "rev-parse", name).strip()


def _system_config(_repo: Path, tmp: Path, mp: pytest.MonkeyPatch) -> None:
    mp.setenv("GIT_CONFIG_SYSTEM", str(_write(tmp / "system", HOSTILE)))
    mp.delenv("GIT_CONFIG_NOSYSTEM")


def _repo_config(repo: Path, _tmp: Path, _mp: pytest.MonkeyPatch) -> None:
    with (repo / ".git" / "config").open("a", encoding="utf-8") as config:
        config.write(HOSTILE)


def _forge_multi_pack_index(repo: Path, _tmp: Path, _mp: pytest.MonkeyPatch) -> None:
    """Swap the walk commit's and its parent's pack offsets in a multi-pack index."""
    git(repo, "repack", "-adq")
    git(repo, "multi-pack-index", "write")
    path = repo / ".git" / "objects" / "pack" / "multi-pack-index"
    data = bytearray(path.read_bytes())
    chunks = {
        bytes(data[12 + 12 * i : 16 + 12 * i]): int.from_bytes(data[16 + 12 * i : 24 + 12 * i])
        for i in range(data[6])
    }
    count = int.from_bytes(data[chunks[b"OIDF"] + 1020 : chunks[b"OIDF"] + 1024])
    oids = [data[chunks[b"OIDL"] + 20 * i :][:20].hex() for i in range(count)]
    a, b = (chunks[b"OOFF"] + 8 * oids.index(_rev(repo, name)) for name in ("HEAD", "HEAD~1"))
    data[a : a + 8], data[b : b + 8] = data[b : b + 8], data[a : a + 8]
    path.chmod(0o644)
    path.write_bytes(data)


def _fsmonitor(repo: Path, tmp: Path, mp: pytest.MonkeyPatch) -> None:
    source = _write(tmp / "ignored.gitmodules", IGNORED)
    hook = _write(
        tmp / "fsmonitor.sh",
        f'#!/bin/sh\ntouch "{tmp / "fsmonitor-ran"}"\ncp "{source}" .gitmodules\n'
        "printf 'token\\000/\\000'\n",
    )
    hook.chmod(0o755)
    _config(("core.fsmonitor", str(hook)), ("core.fsmonitorHookVersion", "2"))(repo, tmp, mp)


def _gitmodules_head(repo: Path, _tmp: Path, _mp: pytest.MonkeyPatch) -> None:
    (repo / ".gitmodules").unlink()
    git(repo, "rm", "-q", "--cached", ".gitmodules")


def _tracing(case: str) -> Lever:
    return lambda _r, tmp, mp: inherit_tracing(mp, tmp, case)


LEVERS: dict[str, Lever] = {
    **{f"env-{case}": _tracing(case) for case in TRACE_CASES},
    "env-GIT_DIR": lambda _r, t, mp: mp.setenv("GIT_DIR", str(new_repo(t / "other") / ".git")),
    "env-GIT_GRAFT_FILE": lambda r, t, mp: mp.setenv(
        "GIT_GRAFT_FILE", str(_write(t / "grafts", _rev(r, "HEAD~1") + "\n"))
    ),
    "env-GIT_SHALLOW_FILE": lambda r, t, mp: mp.setenv(
        "GIT_SHALLOW_FILE", str(_write(t / "shallow", _rev(r, "HEAD~1") + "\n"))
    ),
    "env-GIT_CONFIG_PARAMETERS": lambda _r, _t, mp: mp.setenv(
        "GIT_CONFIG_PARAMETERS", "'log.showroot'='false'"
    ),
    "config-system": _system_config,
    "config-global": lambda _r, t, _mp: _write(t / "home" / ".gitconfig", HOSTILE),
    "config-repo": _repo_config,
    "replace-ref": lambda r, _t, _mp: git(r, "replace", _rev(r, "HEAD~1"), _rev(r, "HEAD~6")),
    "info-grafts": lambda r, _t, _mp: _write(
        r / ".git" / "info" / "grafts", _rev(r, "HEAD~1") + "\n"
    ),
    "commit-graph": lambda r, _t, _mp: forge_commit_graph(
        r, _rev(r, "HEAD"), parent=_rev(r, "HEAD~2")
    ),
    "multi-pack-index": _forge_multi_pack_index,
    "fsmonitor-hook": _fsmonitor,
    "submodule-ignore-config": _config(("submodule.m.ignore", "all")),
    "gitmodules-worktree": lambda r, _t, _mp: _write(r / ".gitmodules", IGNORED),
    "gitmodules-index": lambda r, _t, _mp: (r / ".gitmodules").unlink(),
    "gitmodules-head": _gitmodules_head,
    "diff-ignoreSubmodules": _config(("diff.ignoreSubmodules", "all")),
    "log-showRoot": _config(("log.showRoot", "false")),
    "i18n-logOutputEncoding": _config(("i18n.logOutputEncoding", "ISO-8859-1")),
}


@pytest.mark.parametrize("lever", sorted(LEVERS))
def test_a_lever_that_moves_an_unhardened_read_leaves_the_walk_unchanged(
    lever: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _history(tmp_path / "repo")
    naive, walked = _naive(repo), walk_first_parent(repo)
    LEVERS[lever](repo, tmp_path, monkeypatch)

    after = walk_first_parent(repo)

    assert not (tmp_path / "fsmonitor-ran").exists(), "the walk ran the fsmonitor hook"
    assert _naive(repo) != naive, "the lever does not reach an unhardened read: no control"
    assert after == walked


def _shim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawn: str, action: str) -> None:
    """A ``git`` first on PATH that runs *action* for *spawn* and execs the real git otherwise."""
    real = shutil.which("git")
    assert real is not None
    shim = _write(
        tmp_path / "shim" / "git",
        f'#!/bin/sh\nREAL="{real}"\n'
        "for a; do case $a in rev-parse|rev-list|diff-tree|log) break;; esac; done\n"
        f'if [ "$a" = {spawn} ]; then {action}; fi\nexec "$REAL" "$@"\n',
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim.parent}{os.pathsep}{os.environ['PATH']}")


@pytest.mark.parametrize(
    ("spawn", "action"),
    [
        pytest.param("rev-list", '"$REAL" "$@" | sed 2s/^/g/; exit', id="rev-list-non-oid-line"),
        pytest.param(
            "diff-tree",
            f'sed "\\$s/^[0-9a-f]*/{"1" * 40}/" | "$REAL" "$@"; exit',
            id="diff-tree-drops-an-unknown-commit-with-status-0",
        ),
        pytest.param("diff-tree", 'sed p | "$REAL" "$@"; exit', id="diff-tree-extra-record"),
        pytest.param(
            "diff-tree",
            'awk \'NR==1{f=$0;next}{print}END{print f}\' | "$REAL" "$@"; exit',
            id="diff-tree-out-of-order",
        ),
        pytest.param("log", 'exec "$REAL" "$@" --reverse', id="log-out-of-sequence"),
        pytest.param("log", 'exec "$REAL" "$@" --max-count=1', id="log-missing-messages"),
    ],
)
def test_a_stream_the_framing_cannot_read_refuses(
    spawn: str, action: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _history(tmp_path / "repo")
    assert walk_first_parent(repo).commits, "the unshimmed walk must read this history"
    _shim(tmp_path, monkeypatch, spawn, action)

    refusal = _refused(repo)

    assert (refusal.spawn, "git --version" in refusal.remedy) == (spawn, True)


@pytest.mark.parametrize("spawn", ["rev-parse", "rev-list", "diff-tree", "log"])
def test_a_spawn_outliving_the_timeout_refuses_and_names_itself(
    spawn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = new_repo(tmp_path / "repo")
    commit_file(repo, "a.txt", "a\n")
    monkeypatch.setattr(first_parent_walk, "GIT_TIMEOUT_SECONDS", 0.2)
    _shim(tmp_path, monkeypatch, spawn, "exec sleep 5")
    started = time.monotonic()

    refusal = _refused(repo)

    assert time.monotonic() - started < 4, "the walk waited for git instead of killing it"
    assert (refusal.spawn, refusal.reason) == (spawn, "git outlived the 0.2-second bound")


def test_an_unborn_head_walks_nothing(tmp_path: Path) -> None:
    assert walk_first_parent(new_repo(tmp_path / "repo")) == NOTHING


@pytest.mark.parametrize(
    ("spawn", "break_head"),
    [
        pytest.param(
            "rev-parse",
            _config(("core.repositoryformatversion", "1"), ("extensions.unheardOf", "true")),
            id="git-cannot-open-the-repository",
        ),
        pytest.param(
            "rev-list",
            lambda r, _t, _mp: _write(r / ".git" / "refs" / "heads" / "main", "1" * 40 + "\n"),
            id="head-names-an-absent-object",
        ),
    ],
)
def test_a_head_git_cannot_read_refuses(
    spawn: str, break_head: Lever, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = new_repo(tmp_path / "repo")
    commit_file(repo, "a.txt", "a\n")
    break_head(repo, tmp_path, monkeypatch)

    assert _refused(repo).spawn == spawn


def test_the_walk_reads_no_repository_but_the_one_rooted_at_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A throwaway directory inside a clone discovers that clone; the walk refuses instead."""
    monkeypatch.delenv("GIT_CEILING_DIRECTORIES", raising=False)
    outer = new_repo(tmp_path / "outer")
    commit_file(outer, "o.txt", "o\n")
    inside = outer / "tmp" / "scratch"
    inside.mkdir(parents=True)
    inner = new_repo(outer / "tmp" / "inner")
    inner_sha = commit_file(inner, "i.txt", "i\n")
    outside = tmp_path / "plain"
    outside.mkdir()

    assert (_refused(inside).spawn, _refused(outside).spawn) == ("rev-parse", "rev-parse")
    assert [commit.sha for commit in walk_first_parent(inner).commits] == [inner_sha]
    assert walk_first_parent(None) == NOTHING


@pytest.mark.parametrize(
    "fields",
    [
        (None, (WalkedCommit("a" * 40, None, (), b""),), None, False, False),
        (None, (), "0" * 64, False, False),
        ("b" * 40, (WalkedCommit("a" * 40, None, (), b""),), None, False, False),
    ],
)
def test_an_impossible_walk_cannot_be_built(
    fields: tuple[str | None, tuple[WalkedCommit, ...], str | None, bool, bool],
) -> None:
    with pytest.raises(ValueError, match="walk commit"):
        FirstParentWalk(*fields)
