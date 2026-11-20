"""``walk_first_parent`` reads first-parent history at ``HEAD`` and nothing ambient (ADR-0040 d2).

Every repository is built under ``tmp_path``. Each lever is first shown moving an unhardened
read -- its positive control -- and then shown leaving the walk unchanged.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest
from git_isolation_fixtures import (
    TRACE_CASES,
    clone,
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
from theurian.infrastructure.git.trailer_source import GitTrailerFindingSource

pytestmark = pytest.mark.integration

MAPPING = '[submodule "m"]\n\tpath = m\n\turl = ./m\n'
IGNORED = MAPPING + "\tignore = all\n"
NOTHING = FirstParentWalk(None, (), None, depth_bounded=False, budget_bounded=False)
#: Signs and "verifies" alike: git needs only the status line to sign, and log shows its stderr.
GPG = (
    "#!/bin/sh\ncat >/dev/null\nprintf '\\n[GNUPG:] SIG_CREATED \\n' >&2\n"
    "printf -- '-----BEGIN PGP SIGNATURE-----\\n\\nx\\n-----END PGP SIGNATURE-----\\n'\n"
)


@pytest.fixture(autouse=True)
def _no_developer_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The positive controls read config as git finds it, so the developer's own is moved away."""
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)


def _history(repo: Path) -> Path:
    """Newest first: an empty signed commit; a Latin-1 message adding an OID-named path,
    ``z.txt`` and a non-UTF-8 path; a submodule bump; its addition, whose committed
    ``.gitmodules`` ignores it (the worktree's does not); a side-branch merge; ``main.txt``; the
    root."""
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
    gpg = _write(repo.parent / "gpg", GPG)
    gpg.chmod(0o755)
    git(repo, "-c", f"gpg.program={gpg}", "commit", "-qS", "--allow-empty", "-m", "empty")
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
    # Unparseable, so git exits: a parsed key the prefix pins would lose to its later -c.
    "env-GIT_CONFIG_PARAMETERS": lambda _r, _t, mp: mp.setenv("GIT_CONFIG_PARAMETERS", "'"),
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
    "log-showSignature": lambda r, t, mp: _config(
        ("log.showSignature", "true"), ("gpg.program", str(r.parent / "gpg"))
    )(r, t, mp),
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
    """A ``git`` first on PATH that runs *action* for a spawn the case pattern *spawn* matches
    and execs the real git otherwise."""
    real = shutil.which("git")
    assert real is not None
    shim = _write(
        tmp_path / "shim" / "git",
        f'#!/bin/sh\nREAL="{real}"\n'
        "for a; do case $a in rev-parse|rev-list|diff-tree|log) break;; esac; done\n"
        f'case $a in {spawn}) {action};; esac\nexec "$REAL" "$@"\n',
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim.parent}{os.pathsep}{os.environ['PATH']}")


@pytest.mark.parametrize(
    ("spawn", "action"),
    [
        pytest.param("rev-list", '"$REAL" "$@" | sed 2s/^/g/; exit', id="rev-list-non-oid-line"),
        pytest.param(
            "rev-list", '"$REAL" "$@" | sed \'1s/$/ g/\'; exit', id="rev-list-non-oid-last-token"
        ),
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
        pytest.param(
            "rev-list",
            '"$REAL" "$@" | awk \'NR>1{print p}{p=$0}END{printf "%s", p}\'; exit',
            id="rev-list-unterminated",
        ),
        pytest.param("rev-list", '"$REAL" "$@"; printf g; exit', id="rev-list-trailing-fragment"),
        pytest.param("rev-list", '"$REAL" "$@" | sed 2d; exit', id="rev-list-skips-a-commit"),
        pytest.param("rev-list", '"$REAL" "$@" | sed \\$d; exit', id="rev-list-drops-the-root"),
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
def test_a_spawn_outliving_the_timeout_refuses_within_the_bound_and_names_itself(
    spawn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shim is a non-exec wrapper whose sleeping child holds stdout: killing the wrapper
    alone left the read blocked until that child exited (#1058 round one, X1)."""
    repo = new_repo(tmp_path / "repo")
    commit_file(repo, "a.txt", "a\n")
    monkeypatch.setattr(first_parent_walk, "GIT_TIMEOUT_SECONDS", 1.0)
    _shim(tmp_path, monkeypatch, spawn, "sleep 30; exit")
    started = time.monotonic()

    refusal = _refused(repo)

    assert time.monotonic() - started < 3, "the walk waited past its bound for git"
    assert (refusal.spawn, refusal.reason) == (spawn, "git outlived the 1-second bound")


def test_the_walk_spawns_trailer_sources_log_prefix_unchanged_plus_decision_2s_switches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B9 (#1033). Each side's argv is captured at its spawn call, not read off its source, so a
    flag added at the call site rather than in the prefix constant is compared too."""
    spawned: list[list[str]] = []

    def run(args: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        spawned.append(args)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    def popen(args: list[str], **_: object) -> NoReturn:
        spawned.append(args)
        raise OSError

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", popen)

    GitTrailerFindingSource(tmp_path).load_findings()
    with pytest.raises(GitWalkRefusedError):
        walk_first_parent(tmp_path)

    trailer, walk = spawned
    assert walk[1 : walk.index("rev-parse")] == [
        "--no-pager",
        *trailer[1 : trailer.index("log")],
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.multiPackIndex=false",
    ]


@pytest.mark.parametrize(
    ("planted", "reason"),
    [
        pytest.param(False, "git is not on PATH", id="not-on-path"),
        pytest.param(True, "git could not be started", id="exec-format-error"),
    ],
)
def test_a_git_that_cannot_run_refuses(
    planted: bool, reason: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = new_repo(tmp_path / "repo")
    commit_file(repo, "a.txt", "a\n")
    if planted:
        _write(tmp_path / "bin" / "git", "").chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))

    refusal = _refused(repo)

    assert (refusal.spawn, refusal.reason) == ("rev-parse", reason)


def test_a_relative_path_entry_runs_the_git_beside_the_process_not_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0030 clause 5: the binary is resolved before the spawn, whose cwd is the repository."""
    repo = new_repo(tmp_path / "repo")
    sha = commit_file(repo, "a.txt", "a\n")
    for where, body in ((tmp_path, f'exec "{shutil.which("git")}" "$@"'), (repo, "exit 3")):
        _write(where / "bin" / "git", f"#!/bin/sh\n{body}\n").chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", f"bin{os.pathsep}{os.environ['PATH']}")

    assert [commit.sha for commit in walk_first_parent(repo).commits] == [sha]


def test_an_unborn_head_walks_nothing(tmp_path: Path) -> None:
    assert walk_first_parent(new_repo(tmp_path / "repo")) == NOTHING


@pytest.mark.parametrize(
    ("expected", "break_head"),
    [
        pytest.param(
            ("rev-parse", "git exited with status 128"),
            _config(("core.repositoryformatversion", "1"), ("extensions.unheardOf", "true")),
            id="git-cannot-open-the-repository",
        ),
        pytest.param(
            ("rev-list", "git exited with status 128"),
            lambda r, _t, _mp: _write(r / ".git" / "refs" / "heads" / "main", "1" * 40 + "\n"),
            id="head-names-an-absent-object",
        ),
        pytest.param(
            ("rev-list", "a line is not the walk commit or the last parent"),
            lambda r, _t, _mp: [
                git(r, "tag", "-a", "t", "-m", "t"),
                _write(r / ".git" / "refs" / "heads" / "main", _rev(r, "t") + "\n"),
            ],
            id="head-names-a-tag-not-a-commit",
        ),
    ],
)
def test_a_head_git_cannot_read_refuses(
    expected: tuple[str, str],
    break_head: Lever,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The first two make git print ``fatal:`` lines, which must not reach the caller's stderr."""
    repo = new_repo(tmp_path / "repo")
    commit_file(repo, "a.txt", "a\n")
    break_head(repo, tmp_path, monkeypatch)

    refusal = _refused(repo)

    assert (refusal.spawn, refusal.reason) == expected
    assert capfd.readouterr().err == ""


def test_a_sha256_repository_walks_with_a_nested_path_whole(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "--object-format=sha256")
    sha = commit_file(repo, "docs/adr/x.md", "x\n")

    walk = walk_first_parent(repo)

    assert [(commit.sha, commit.paths) for commit in walk.commits] == [(sha, ("docs/adr/x.md",))]


def test_a_stream_read_seven_bytes_at_a_time_walks_alike(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git flushes each record whole, so at the shipped chunk no token spans two reads (measured
    on 1,500 commits)."""
    repo = _history(tmp_path / "repo")
    walk = walk_first_parent(repo)
    monkeypatch.setattr(first_parent_walk, "_CHUNK", 7)

    assert walk_first_parent(repo) == walk


def test_a_committer_date_datetime_cannot_hold_reads_as_none_not_a_refusal(
    tmp_path: Path,
) -> None:
    repo = new_repo(tmp_path / "repo")
    git(
        repo,
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "far",
        extra_env={"GIT_COMMITTER_DATE": "@253402300800 +0000"},
    )
    assert git(repo, "log", "-1", "--format=%cI").strip() == "10000-01-01T00:00:00Z"

    assert [commit.committed_at for commit in walk_first_parent(repo).commits] == [None]


def _linear(repo: Path, *sizes: int) -> list[str]:
    """One commit per size, oldest first, adding that many paths; the shas newest first."""
    new_repo(repo)
    for n, size in enumerate(sizes):
        for k in range(size):
            _write(repo / str(n) / f"{k}.txt", "x\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", f"c{n}")
    return git(repo, "rev-list", "HEAD").split()


def _digest(*oids: str) -> str:
    return hashlib.sha256("".join(f"{oid}\n" for oid in sorted(oids)).encode()).hexdigest()


def test_the_shallow_digest_is_over_lines_and_a_line_holding_two_oids_refuses(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    shas = _linear(repo, *[1] * 5)
    deep, near = shas[2], shas[1]
    shallow = repo / ".git" / "shallow"
    walked = {}
    descending = "".join(f"{oid}\n" for oid in sorted((deep, near), reverse=True))
    for shape, text in (("one line", f"{deep} {near}\n"), ("two lines", descending)):
        shallow.write_text(text, encoding="utf-8")
        walked[shape] = git(repo, "rev-list", "--first-parent", "HEAD").split()
    assert walked["one line"] != walked["two lines"], "git reads both shapes alike: no control"

    walk = walk_first_parent(repo)
    shallow.write_text(f"{deep} {near}\n", encoding="utf-8")

    assert ([c.sha for c in walk.commits], walk.shallow_digest) == (shas[:2], _digest(deep, near))
    assert _refused(repo).reason == "a shallow-file line is not exactly one object id"


@pytest.mark.parametrize(
    "line",
    [
        pytest.param(str.upper, id="uppercase"),
        pytest.param(lambda oid: oid[1:], id="short"),
        pytest.param(lambda oid: f"{oid}\r", id="carriage-return"),
        pytest.param(lambda _oid: "", id="blank"),
    ],
)
def test_a_shallow_line_that_is_not_one_lowercase_oid_of_the_hash_length_refuses(
    line: Callable[[str], str], tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    shas = _linear(repo, 1, 1, 1)
    (repo / ".git" / "shallow").write_text(f"{shas[1]}\n{line(shas[2])}\n", encoding="utf-8")

    refusal = _refused(repo)

    assert (refusal.spawn, refusal.reason) == (
        "rev-parse",
        "a shallow-file line is not exactly one object id",
    )


def test_a_shallow_path_that_cannot_be_read_refuses(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _linear(repo, 1, 1)
    (repo / ".git" / "shallow").mkdir()

    assert _refused(repo).reason == "the shallow file cannot be read"


def test_a_shallow_list_deepened_during_the_walk_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin = tmp_path / "origin"
    _linear(origin, *[1] * 4)
    repo = clone(origin, tmp_path / "repo", "--depth=2")
    walk = walk_first_parent(repo)
    assert (len(walk.commits), walk.shallow_digest) == (2, _digest(walk.commits[1].sha))
    deepen = 'GIT_ALLOW_PROTOCOL=file "$REAL" fetch -q --deepen=1 origin >/dev/null'
    _shim(tmp_path, monkeypatch, "rev-list", deepen)

    assert _refused(repo).reason == "the shallow list changed during the walk"
    shallow = (repo / ".git" / "shallow").read_text(encoding="utf-8")
    assert shallow != f"{walk.commits[1].sha}\n", "the shim did not deepen the clone"


@pytest.mark.parametrize(("bound", "kept"), [(4, 4), (3, 3)])
def test_the_depth_bound_stops_the_walk_at_the_same_commit_and_says_so(
    bound: int, kept: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    shas = _linear(repo, *[1] * 4)
    monkeypatch.setattr(first_parent_walk, "DEPTH_BOUND", bound)

    walk = walk_first_parent(repo)

    assert [commit.sha for commit in walk.commits] == shas[:kept]
    assert (walk.depth_bounded, walk.budget_bounded) == (kept < len(shas), False)
    assert walk_first_parent(repo) == walk


@pytest.mark.parametrize(("budget", "kept"), [(6, 3), (5, 2)])
def test_the_entry_budget_counts_a_capped_commit_and_stops_at_the_commit_crossing_it(
    budget: int, kept: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Newest first the commits list 1, 3 and 2 paths; the 3-path commit is over the cap."""
    repo = tmp_path / "repo"
    shas = _linear(repo, 2, 3, 1)
    monkeypatch.setattr(first_parent_walk, "PATH_CAP", 2)
    monkeypatch.setattr(first_parent_walk, "ENTRY_BUDGET", budget)

    walk = walk_first_parent(repo)

    assert [commit.sha for commit in walk.commits] == shas[:kept]
    assert walk.commits[1].paths is None
    assert (walk.depth_bounded, walk.budget_bounded) == (False, kept < len(shas))
    assert walk_first_parent(repo) == walk


@pytest.mark.parametrize(("cap", "paths"), [(3, ("0/0.txt", "0/1.txt", "0/2.txt")), (2, None)])
def test_a_commit_listing_more_paths_than_the_cap_lists_none(
    cap: int, paths: tuple[str, ...] | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _linear(repo, 3)
    monkeypatch.setattr(first_parent_walk, "PATH_CAP", cap)

    assert [commit.paths for commit in walk_first_parent(repo).commits] == [paths]


def _stream_bytes(repo: Path) -> tuple[list[int], list[int], list[int]]:
    """Each first-parent commit's bytes on rev-list's, diff-tree's and log's stdout, newest first.

    For a linear history the single-commit reads print what the walk's streams do (measured).
    """
    lines = git(repo, "rev-list", "--first-parent", "--parents", "HEAD").splitlines(keepends=True)
    shas = [line.split()[0] for line in lines]
    diff = ("diff-tree", "-r", "--root", "--no-renames", "--name-status", "-z", "--always")
    log = ("log", "-z", "--max-count=1", "--format=tformat:%H%x00%cI%x00%B")
    return (
        [len(line) for line in lines],
        [len(git(repo, *diff, sha)) for sha in shas],
        [len(git(repo, *log, sha)) for sha in shas],
    )


Budget = Callable[[list[int], list[int], list[int]], int]


@pytest.mark.parametrize(
    ("budget", "kept", "fed"),
    [
        pytest.param(lambda rl, _d, _l: rl[0] - 1, 0, (0, None), id="rev-list-crosses"),
        pytest.param(lambda rl, _d, _l: rl[0], 0, (1, None), id="rev-list-reaches"),
        pytest.param(
            lambda rl, dt, _l: sum(rl[:2]) + sum(dt[:2]) - 1, 1, (3, 1), id="diff-tree-crosses"
        ),
        pytest.param(
            lambda rl, dt, _l: sum(rl[:2]) + sum(dt[:2]), 1, (3, 2), id="diff-tree-reaches"
        ),
        pytest.param(
            lambda rl, dt, lg: sum(rl[:2] + dt[:2] + lg[:2]) - 1, 1, (3, 3), id="log-crosses"
        ),
        pytest.param(lambda *s: sum(map(sum, s)) - 1, 2, (3, 3), id="the-last-commit-crosses"),
        pytest.param(lambda *s: sum(map(sum, s)), 3, (3, 3), id="nothing-crosses"),
    ],
)
def test_the_byte_budget_stops_every_stream_at_the_commit_that_would_cross_it(
    budget: Budget,
    kept: int,
    fed: tuple[int, int | None],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*fed* is how many commits diff-tree and log were handed, ``None`` for a log never run."""
    repo = tmp_path / "repo"
    shas = _linear(repo, 2, 3, 1)
    monkeypatch.setattr(first_parent_walk, "BYTE_BUDGET", budget(*_stream_bytes(repo)))
    record = tmp_path / "record"
    record.mkdir()
    keep = f'echo "$@" >"{record}/$a"; cat >"{record}/$a.in"; "$REAL" "$@" <"{record}/$a.in"; exit'
    _shim(tmp_path, monkeypatch, "diff-tree|log", keep)

    walk = walk_first_parent(repo)

    log = record / "log"
    counted = re.search(r"--max-count=(\d+)", log.read_text()) if log.exists() else None
    feed = (record / "diff-tree.in").read_text().splitlines()
    assert (len(feed), int(counted[1]) if counted else None) == fed
    assert [commit.sha for commit in walk.commits] == shas[:kept]
    assert (walk.depth_bounded, walk.budget_bounded) == (False, kept < len(shas))
    assert walk_first_parent(repo) == walk


def test_a_token_outgrowing_the_byte_budget_ends_the_read_and_is_not_read_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    sha = _linear(repo, 1)[0]
    monkeypatch.setattr(first_parent_walk, "BYTE_BUDGET", 1 << 16)
    monkeypatch.setattr(first_parent_walk, "GIT_TIMEOUT_SECONDS", 10.0)
    _shim(tmp_path, monkeypatch, "log", "while :; do printf %0512d 0; done")
    started = time.monotonic()

    walk = walk_first_parent(repo)

    assert time.monotonic() - started < 3, "the walk left git writing until the timeout"
    assert walk == FirstParentWalk(sha, (), None, depth_bounded=False, budget_bounded=True)


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
    (tmp_path / "link").symlink_to(inner)

    assert (_refused(inside).spawn, _refused(outside).spawn) == ("rev-parse", "rev-parse")
    assert [commit.sha for commit in walk_first_parent(inner).commits] == [inner_sha]
    assert [commit.sha for commit in walk_first_parent(tmp_path / "link").commits] == [inner_sha]
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
