"""First-parent history at ``HEAD``, read for traceability collection (ADR-0040 decision 2).

No caller yet: #1033's C2-4 wires it into ``migrate apply``. Every read is plumbing over the
one OID ``HEAD`` resolved to, under #1012's environment and argv prefix plus decision 2's
flags; ``test_first_parent_walk.py`` drives each lever decision 2 names and shows it moving an
unhardened read but not this one. A git that fails, outlives :data:`GIT_TIMEOUT_SECONDS` or
prints a stream the framing cannot read refuses with :class:`GitWalkRefusedError`, never
degrading to fewer commits. The binary is resolved to an absolute path on each call --
ADR-0030 clause 5's tier, because this read gates a write.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Final, Self, cast, final

from theurian.domain.errors import TheurianError
from theurian.infrastructure.git.environment import child_environment
from theurian.infrastructure.git.trailer_source import GIT_TIMEOUT_SECONDS

#: ``trailer_source``'s ``log`` prefix (#1012), plus ``--no-pager`` and decision 2's switches that
#: keep an fsmonitor hook and a multi-pack index out of every read.
_PREFIX: Final = (
    "--no-pager",
    "-c",
    "log.showSignature=false",
    "-c",
    "i18n.logOutputEncoding=UTF-8",
    "--no-optional-locks",
    "--no-replace-objects",
    "-c",
    "core.useReplaceRefs=false",
    "-c",
    "core.commitGraph=false",
    "-c",
    "advice.graftFileDeprecated=false",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.multiPackIndex=false",
)
#: One record per commit fed: ``--always`` gives an empty commit its header, ``--name-status``
#: keeps a path named like an OID from reading as one, and an explicit ``C P1`` line leaves no
#: merge-diff default to apply.
_DIFF_TREE: Final = (
    "diff-tree",
    "--stdin",
    "-r",
    "--root",
    "--no-renames",
    "--name-status",
    "-z",
    "--always",
    "--ignore-submodules=none",
)
#: #1012's fields; ``tformat:`` terminates every record, so all three streams end on a separator.
_LOG_FORMAT: Final = "--format=tformat:%H%x00%cI%x00%B"
_LOG_FIELDS: Final = 3
_HEX_LENGTHS: Final = {b"sha1": 40, b"sha256": 64}
_CHUNK: Final = 1 << 16

_INSTALL: Final = "Install git and put it on PATH so 'git --version' runs, then retry."
_REPAIR: Final = (
    "Run 'git fsck' in the project's repository and repair what it reports, then retry."
)
_REPORT: Final = (
    "Run 'git --version' and report its output with this message: git printed a stream "
    "this walk is not written against."
)


class GitWalkRefusedError(TheurianError):
    """The walk could not vouch for what git printed, so it returns nothing at all.

    ``spawn`` is the git subcommand that failed; ``reason`` is a fixed phrase or an exit
    status, never git's output, so no message byte, path or repository location reaches it.
    """

    def __init__(self, spawn: str, reason: str, remedy: str) -> None:
        self.spawn = spawn
        self.reason = reason
        self.remedy = remedy
        super().__init__(f"First-parent walk refused at git {spawn}: {reason}")


@dataclass(frozen=True, slots=True)
class WalkedCommit:
    """One first-parent commit.

    ``committed_at`` is ``None`` when git's committer date is not an offset-bearing instant
    ``datetime`` holds (a year past 9999 is one ``GIT_COMMITTER_DATE`` away). A path that is
    not UTF-8 is dropped. ``message`` is git's bytes, undecoded.
    """

    sha: str
    committed_at: datetime | None
    paths: tuple[str, ...] | None
    message: bytes


@dataclass(frozen=True, slots=True)
class FirstParentWalk:
    """The commits from ``walk_commit`` down its first parents, the walk commit first."""

    walk_commit: str | None
    commits: tuple[WalkedCommit, ...]
    shallow_digest: str | None
    depth_bounded: bool
    budget_bounded: bool

    def __post_init__(self) -> None:
        boundary = (self.shallow_digest, self.depth_bounded, self.budget_bounded)
        if self.walk_commit is None and (self.commits or boundary != (None, False, False)):
            raise ValueError("a walk without a walk commit holds no commits and no boundary")
        if self.commits and self.commits[0].sha != self.walk_commit:
            raise ValueError("the walk commit must come first")


_NOTHING: Final = FirstParentWalk(None, (), None, depth_bounded=False, budget_bounded=False)


def walk_first_parent(root: Path | None) -> FirstParentWalk:
    """The first-parent walk at ``HEAD`` of the repository rooted at ``root``.

    ``None``, or an unborn ``HEAD``, walks nothing. Raises :class:`GitWalkRefusedError`.
    """
    if root is None:
        return _NOTHING
    git = shutil.which("git", path=os.environ.get("PATH"))
    if git is None:
        raise GitWalkRefusedError("rev-parse", "git is not on PATH", _INSTALL)
    git = str(Path(git).resolve())
    hex_length, walk_commit = _read_head(git, root)
    if walk_commit is None:
        return _NOTHING
    listed = _list_commits(git, root, walk_commit, hex_length)
    paths = _read_paths(git, root, listed)
    stamps = _read_messages(git, root, [sha for sha, _ in listed])
    commits = tuple(
        WalkedCommit(sha, at, names, message)
        for (sha, _), names, (at, message) in zip(listed, paths, stamps, strict=True)
    )
    return FirstParentWalk(walk_commit, commits, None, depth_bounded=False, budget_bounded=False)


def _read_head(git: str, root: Path) -> tuple[int, str | None]:
    """The hash length and ``HEAD``'s OID, read once; ``None`` when ``HEAD`` names nothing.

    One spawn answers both, so the ``HEAD`` it reads is the repository whose root it prints.
    ``--verify --quiet`` exits 1 with no line when ``HEAD`` resolves to no object name: an
    unborn branch reads that way, and so does a loose ref holding bytes that are not an OID --
    a direct write to a ref, #1012's residual. A ``HEAD`` naming an absent object prints it and
    refuses at ``rev-list``.
    """
    args = ("rev-parse", "--show-toplevel", "--show-object-format", "--verify", "--quiet", "HEAD")
    with _Child(git, root, args, allowed=(0, 1)) as child:
        lines = list(child.tokens(b"\n"))
    if len(lines) != 3 - child.returncode:
        raise _framing("rev-parse", f"{len(lines)} lines for exit status {child.returncode}")
    if Path(os.fsdecode(lines[0])).resolve() != root.resolve():
        raise GitWalkRefusedError(
            "rev-parse",
            "the repository git finds there is rooted elsewhere",
            "Run 'git rev-parse --show-toplevel' in the project directory; the walk reads "
            "only a repository rooted exactly there.",
        )
    hex_length = _HEX_LENGTHS.get(lines[1])
    if hex_length is None:
        raise _framing("rev-parse", "the object format is neither sha1 nor sha256")
    return hex_length, _oid(lines[2], hex_length, "rev-parse") if child.returncode == 0 else None


def _list_commits(
    git: str, root: Path, walk_commit: str, hex_length: int
) -> list[tuple[str, str | None]]:
    """``(commit, first parent)`` per first-parent commit; every token on a line is an OID."""
    listed: list[tuple[str, str | None]] = []
    with _Child(git, root, ("rev-list", "--first-parent", "--parents", walk_commit)) as child:
        for line in child.tokens(b"\n"):
            oids = [_oid(token, hex_length, "rev-list") for token in line.split(b" ")]
            listed.append((oids[0], oids[1] if len(oids) > 1 else None))
    if not listed or listed[0][0] != walk_commit:
        raise _framing("rev-list", "the first line is not the walk commit")
    return listed


def _read_paths(
    git: str, root: Path, listed: Sequence[tuple[str, str | None]]
) -> list[tuple[str, ...]]:
    """The paths each commit changed against its first parent, one diff-tree record each.

    diff-tree drops a stdin commit it cannot read and still exits 0, so the records are
    matched to the feed one for one, in order.
    """
    feed = b"".join(" ".join(filter(None, pair)).encode() + b"\n" for pair in listed)
    records: list[list[str]] = []
    in_path = False
    with _Child(git, root, _DIFF_TREE, feed=feed) as child:
        for token in child.tokens(b"\0"):
            if in_path:
                in_path = False
                with contextlib.suppress(UnicodeDecodeError):
                    records[-1].append(token.decode("utf-8"))
            elif len(token) == 1 and records:
                in_path = True
            elif len(records) < len(listed) and token == listed[len(records)][0].encode():
                records.append([])
            else:
                raise _framing("diff-tree", "a record is extra, out of order or malformed")
    if in_path or len(records) != len(listed):
        raise _framing("diff-tree", f"{len(records)} records for {len(listed)} commits")
    return [tuple(names) for names in records]


def _read_messages(
    git: str, root: Path, shas: Sequence[str]
) -> list[tuple[datetime | None, bytes]]:
    """Each commit's committer date and raw message, in rev-list's sequence or refused."""
    args = ("log", "-z", "--first-parent", f"--max-count={len(shas)}", _LOG_FORMAT, shas[0])
    stamps: list[tuple[datetime | None, bytes]] = []
    fields: list[bytes] = []
    with _Child(git, root, args) as child:
        for token in child.tokens(b"\0"):
            fields.append(token)
            if len(fields) < _LOG_FIELDS:
                continue
            sha, date, message = fields
            if len(stamps) == len(shas) or sha != shas[len(stamps)].encode():
                raise _framing("log", "a message arrived out of rev-list's sequence")
            stamps.append((_instant(date), message))
            fields = []
    if fields or len(stamps) != len(shas):
        raise _framing("log", f"{len(stamps)} messages for {len(shas)} commits")
    return stamps


def _instant(date: bytes) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(date.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def _oid(token: bytes, hex_length: int, spawn: str) -> str:
    if re.fullmatch(rb"[0-9a-f]{%d}" % hex_length, token) is None:
        raise _framing(spawn, "a token that should be an object id is not one")
    return token.decode("ascii")


def _framing(spawn: str, reason: str) -> GitWalkRefusedError:
    return GitWalkRefusedError(spawn, reason, _REPORT)


@final
class _Child:
    """One git spawn: stdout read in chunks, killed at the timeout or when the reader leaves."""

    def __init__(
        self,
        git: str,
        root: Path,
        args: Sequence[str],
        *,
        feed: bytes | None = None,
        allowed: tuple[int, ...] = (0,),
    ) -> None:
        self._spawn = args[0]
        self._allowed = allowed
        self.returncode = 0
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - fixed vector; the only variable token is a validated OID
                [git, *_PREFIX, *args],
                cwd=root,
                env=child_environment(os.environ),
                stdin=subprocess.DEVNULL if feed is None else subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise GitWalkRefusedError(self._spawn, "git could not be started", _INSTALL) from exc
        self._stdout = cast("io.BufferedReader", self._proc.stdout)
        self._expired = threading.Event()
        self._timer = threading.Timer(GIT_TIMEOUT_SECONDS, self._expire)
        self._timer.start()
        self._writer: threading.Thread | None = None
        if feed is not None:
            self._writer = threading.Thread(target=_write, args=(self._proc.stdin, feed))
            self._writer.start()

    def _expire(self) -> None:
        self._expired.set()
        self._proc.kill()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        if self._proc.poll() is None:
            self._proc.kill()
        self._proc.wait()
        self._timer.cancel()
        if self._writer is not None:
            self._writer.join()
        self._stdout.close()

    def tokens(self, separator: bytes) -> Iterator[bytes]:
        """Each separator-terminated token, as it completes; the stream must end on one."""
        pieces: list[bytes] = []
        while chunk := self._stdout.read1(_CHUNK):
            *done, rest = chunk.split(separator)
            for part in done:
                yield b"".join([*pieces, part])
                pieces = []
            pieces.append(rest)
        self._settle(unterminated=any(pieces))

    def _settle(self, *, unterminated: bool) -> None:
        self.returncode = self._proc.wait()
        self._timer.cancel()
        if self._expired.is_set():
            raise GitWalkRefusedError(
                self._spawn,
                f"git outlived the {GIT_TIMEOUT_SECONDS:g}-second bound",
                f"Run 'git gc' in the project's repository so 'git {self._spawn}' finishes "
                f"within {GIT_TIMEOUT_SECONDS:g} seconds, then retry.",
            )
        if self.returncode not in self._allowed:
            raise GitWalkRefusedError(
                self._spawn, f"git exited with status {self.returncode}", _REPAIR
            )
        if unterminated:
            raise _framing(self._spawn, "the output ended inside a record")


def _write(stdin: IO[bytes], data: bytes) -> None:
    # A git that exits or is killed first closes the pipe; what it did not read is moot.
    with contextlib.suppress(OSError, ValueError), stdin:
        stdin.write(data)
