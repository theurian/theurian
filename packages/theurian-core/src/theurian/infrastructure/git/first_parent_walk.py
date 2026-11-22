"""First-parent history at ``HEAD``, read for traceability collection (ADR-0040 decision 2).

No caller yet: #1033's C2-4 wires it into ``migrate apply``. Every read is plumbing over the
one OID ``HEAD`` resolved to, under #1012's environment and argv prefix plus decision 2's
flags; ``test_first_parent_walk.py`` drives each lever decision 2 names and shows it moving an
unhardened read but not this one. A git that fails, outlives :data:`GIT_TIMEOUT_SECONDS` or
prints a stream the framing cannot read refuses with :class:`GitWalkRefusedError`, never
degrading to fewer commits. Each spawn's timeout is a deadline, not a wait for pipe EOF
(#1092 round one): at it, git's process group and then git are killed and the walk refuses at
once. A descendant that left the group is left running; holding stdout at the deadline, it is
reported by that refusal. The binary is resolved to an absolute path on each call -- ADR-0030
clause 5's tier, because this read gates a write.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import re
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Final, Literal, NoReturn, Self, cast, final

from theurian.domain.errors import TheurianError
from theurian.infrastructure.git.environment import child_environment
from theurian.infrastructure.git.trailer_source import GIT_TIMEOUT_SECONDS

#: ADR-0040 decision 2's bounds; a commit that would cross one is not collected, nor any older.
DEPTH_BOUND: Final = 10_000
ENTRY_BUDGET: Final = 100_000
#: Counts rev-list's, diff-tree's and log's stdout; rev-parse's and the shallow file are outside.
BYTE_BUDGET: Final = 64 << 20
#: A commit listing more paths gets ``paths=None``; its entries still count.
PATH_CAP: Final = 1_000

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
_SHALLOW: Final = (
    "Run 'git rev-parse --git-path shallow' in the project's repository and rewrite the file it "
    "names to one lowercase object id per line, then retry."
)
_STAGE: Final = (
    "Run 'python3 -c \"import tempfile; print(tempfile.gettempdir())\"', free space in the "
    "directory it prints or set TMPDIR to a writable one, then retry."
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
    """The commits from ``walk_commit`` down its first parents, the walk commit first.

    A set ``*_bounded`` flag names the bound that stopped the walk before a root or the shallow
    list.
    """

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


_Bound = Literal["depth", "budget"] | None

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
    hex_length, shallow, walk_commit = _read_head(git, root)
    if walk_commit is None:
        return _NOTHING
    digest = _shallow_digest(shallow, hex_length)
    listed, spent, bound = _list_commits(git, root, walk_commit, hex_length)
    paths, spent = _read_paths(git, root, listed, spent)
    stamps = _read_messages(git, root, [sha for sha, _ in listed[: len(paths)]], spent)
    if len(stamps) < len(listed):
        bound = "budget"
    if _shallow_digest(shallow, hex_length) != digest:
        raise GitWalkRefusedError(
            "rev-parse",
            "the shallow list changed during the walk",
            "Wait for the 'git fetch' rewriting the project's shallow list to finish, then retry.",
        )
    kept = len(stamps)
    commits = tuple(
        WalkedCommit(sha, at, names, message)
        for (sha, _), names, (at, message) in zip(listed[:kept], paths[:kept], stamps, strict=True)
    )
    return FirstParentWalk(
        walk_commit,
        commits,
        digest,
        depth_bounded=bound == "depth",
        budget_bounded=bound == "budget",
    )


def _read_head(git: str, root: Path) -> tuple[int, Path, str | None]:
    """The hash length, the shallow file's path and ``HEAD``'s OID, read once; ``None`` when
    ``HEAD`` names nothing.

    One spawn answers all three, so the ``HEAD`` it reads is the repository whose root it
    prints; ``--git-path`` composes with ``--verify`` and each answer prints in argument order
    (measured, git 2.56.0).
    ``--verify --quiet`` exits 1 with no line when ``HEAD`` resolves to no object name: an
    unborn branch reads that way, and so does a loose ref holding bytes that are not an OID --
    a direct write to a ref, #1012's residual. A ``HEAD`` naming an absent object prints it and
    refuses at ``rev-list``.
    """
    args = (
        "rev-parse",
        "--show-toplevel",
        "--show-object-format",
        "--git-path",
        "shallow",
        "--verify",
        "--quiet",
        "HEAD",
    )
    with _Child(git, root, args, allowed=(0, 1)) as child:
        lines = list(child.tokens(b"\n"))
    if len(lines) != 4 - child.returncode:
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
    head = _oid(lines[3], hex_length, "rev-parse") if child.returncode == 0 else None
    return hex_length, root / os.fsdecode(lines[2]), head


def _shallow_digest(path: Path, hex_length: int) -> str | None:
    """The SHA-256 of the shallow list's lines, sorted, each newline-terminated; ``None`` with no
    list.

    git reads one OID from the head of each line and ignores the rest of it, so any other line
    shape refuses: one line holding two OIDs would digest like two lines and walk unlike them.
    """
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise GitWalkRefusedError("rev-parse", "the shallow file cannot be read", _SHALLOW) from exc
    lines = data.split(b"\n")
    if not lines[-1]:
        lines.pop()
    if not all(_is_oid(line, hex_length) for line in lines):
        raise GitWalkRefusedError(
            "rev-parse", "a shallow-file line is not exactly one object id", _SHALLOW
        )
    return hashlib.sha256(b"".join(line + b"\n" for line in sorted(lines))).hexdigest()


def _list_commits(
    git: str, root: Path, walk_commit: str, hex_length: int
) -> tuple[list[tuple[str, str | None]], list[int], _Bound]:
    """``(commit, first parent)`` per first-parent commit, the bytes read through each line, and
    the bound that stopped the list; every token on a line is an OID.

    Each line is the previous line's first parent, and an unbounded list ends at a commit git
    lists with no parent: a root, or a shallow-list commit (measured: a ``--depth=2`` clone
    lists its boundary commit alone on its line).
    """
    listed: list[tuple[str, str | None]] = []
    spent: list[int] = []
    expected: str | None = walk_commit
    used = 0
    with _Child(git, root, ("rev-list", "--first-parent", "--parents", walk_commit)) as child:
        for line in child.tokens(b"\n"):
            used += len(line) + 1
            if used > BYTE_BUDGET:
                return listed, spent, "budget"
            oids = [_oid(token, hex_length, "rev-list") for token in line.split(b" ")]
            if oids[0] != expected:
                raise _framing("rev-list", "a line is not the walk commit or the last parent")
            if len(listed) == DEPTH_BOUND:
                return listed, spent, "depth"
            expected = oids[1] if len(oids) > 1 else None
            listed.append((oids[0], expected))
            spent.append(used)
    if expected is not None:
        raise _framing("rev-list", "the list ended at a commit with a parent")
    return listed, spent, None


def _read_paths(
    git: str, root: Path, listed: Sequence[tuple[str, str | None]], spent: Sequence[int]
) -> tuple[list[tuple[str, ...] | None], list[int]]:
    """The paths each commit changed against its first parent, one diff-tree record each, up
    to the first commit that would cross a budget, and the bytes read through each commit.

    diff-tree drops a stdin commit it cannot read and still exits 0, so the records are
    matched to the feed one for one, in order.
    """
    feed = b"".join(" ".join(filter(None, pair)).encode() + b"\n" for pair in listed)
    records: list[list[str]] = []
    counts: list[int] = []
    ends: list[int] = []
    entries = used = 0
    in_path = False
    with _Child(git, root, _DIFF_TREE, feed=feed) as child:
        for token in child.tokens(b"\0"):
            used += len(token) + 1
            if in_path:
                in_path = False
                with contextlib.suppress(UnicodeDecodeError):
                    records[-1].append(token.decode("utf-8"))
            elif len(token) == 1 and records:
                in_path = True
                counts[-1] += 1
                entries += 1
            elif len(records) < len(listed) and token == listed[len(records)][0].encode():
                records.append([])
                counts.append(0)
                ends.append(0)
            else:
                raise _framing("diff-tree", "a record is extra, out of order or malformed")
            ends[-1] = used
            if entries > ENTRY_BUDGET or spent[len(ends) - 1] + used > BYTE_BUDGET:
                del records[-1], counts[-1], ends[-1]
                break
        else:
            if in_path or len(records) != len(listed):
                raise _framing("diff-tree", f"{len(records)} records for {len(listed)} commits")
    capped = zip(records, counts, strict=True)
    paths = [None if count > PATH_CAP else tuple(names) for names, count in capped]
    return paths, [before + end for before, end in zip(spent, ends, strict=False)]


def _read_messages(
    git: str, root: Path, shas: Sequence[str], spent: Sequence[int]
) -> list[tuple[datetime | None, bytes]]:
    """Each commit's committer date and raw message, in rev-list's sequence or refused, up to
    the first commit that would cross the byte budget.
    """
    if not shas:
        return []
    args = ("log", "-z", "--first-parent", f"--max-count={len(shas)}", _LOG_FORMAT, shas[0])
    stamps: list[tuple[datetime | None, bytes]] = []
    fields: list[bytes] = []
    used = 0
    with _Child(git, root, args) as child:
        for token in child.tokens(b"\0"):
            if len(stamps) == len(shas):
                raise _framing("log", "a message arrived out of rev-list's sequence")
            used += len(token) + 1
            if spent[len(stamps)] + used > BYTE_BUDGET:
                return stamps
            fields.append(token)
            if len(fields) < _LOG_FIELDS:
                continue
            sha, date, message = fields
            if sha != shas[len(stamps)].encode():
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
    if not _is_oid(token, hex_length):
        raise _framing(spawn, "a token that should be an object id is not one")
    return token.decode("ascii")


def _is_oid(token: bytes, hex_length: int) -> bool:
    return re.fullmatch(rb"[0-9a-f]{%d}" % hex_length, token) is not None


def _framing(spawn: str, reason: str) -> GitWalkRefusedError:
    return GitWalkRefusedError(spawn, reason, _REPORT)


@final
class _Child:
    """One git spawn: stdout read in chunks, killed at its deadline or when the reader leaves."""

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
        with _stdin(self._spawn, feed) as stdin:
            try:
                self._proc = subprocess.Popen(  # noqa: S603 - fixed vector but for the resolved git path, validated OIDs and log's --max-count integer
                    [git, *_PREFIX, *args],
                    cwd=root,
                    env=child_environment(os.environ),
                    stdin=stdin,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    process_group=0,
                )
            except OSError as exc:
                raise GitWalkRefusedError(
                    self._spawn, "git could not be started", _INSTALL
                ) from exc
        self._deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
        self._stdout = cast("io.BufferedReader", self._proc.stdout)
        self._ready = selectors.DefaultSelector()
        self._ready.register(self._stdout, selectors.EVENT_READ)

    def _kill(self) -> None:
        # The group, then git: a wrapper git's descendants hold stdout too, and git may have left
        # the group. Only while the leader is unreaped is its pid sure to name this group; macOS
        # answers EPERM for a group whose one member is that zombie.
        if self._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self._proc.pid, signal.SIGKILL)
            self._proc.kill()

    def _expire(self) -> NoReturn:
        self._kill()
        raise GitWalkRefusedError(
            self._spawn,
            f"git outlived the {GIT_TIMEOUT_SECONDS:g}-second bound",
            f"Run 'git gc' in the project's repository so 'git {self._spawn}' finishes "
            f"within {GIT_TIMEOUT_SECONDS:g} seconds, then retry.",
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self._kill()
        self._proc.wait()
        self._ready.close()
        self._stdout.close()

    def tokens(self, separator: bytes) -> Iterator[bytes]:
        """Each separator-terminated token, as it completes; the stream must end on one.

        A token outgrowing :data:`BYTE_BUDGET` ends the stream unfinished: it is yielded as read
        and git is left to the kill.
        """
        pieces: list[bytes] = []
        pending = 0
        while chunk := self._read():
            *done, rest = chunk.split(separator)
            for part in done:
                yield b"".join([*pieces, part])
                pieces, pending = [], 0
            pieces.append(rest)
            pending += len(rest)
            if pending > BYTE_BUDGET:
                yield b"".join(pieces)
                return
        self._settle(unterminated=any(pieces))

    def _read(self) -> bytes:
        left = self._deadline - time.monotonic()
        if left <= 0 or not self._ready.select(left):
            self._expire()
        return self._stdout.read1(_CHUNK)

    def _settle(self, *, unterminated: bool) -> None:
        try:
            self.returncode = self._proc.wait(self._deadline - time.monotonic())
        except subprocess.TimeoutExpired:
            self._expire()
        if self.returncode not in self._allowed:
            raise GitWalkRefusedError(
                self._spawn, f"git exited with status {self.returncode}", _REPAIR
            )
        if unterminated:
            raise _framing(self._spawn, "the output ended inside a record")


@contextlib.contextmanager
def _stdin(spawn: str, feed: bytes | None) -> Iterator[IO[bytes] | int]:
    """*feed* in an unlinked file, so no writer of ours waits on git reading it."""
    if feed is None:
        yield subprocess.DEVNULL
        return
    with contextlib.ExitStack() as stack:
        try:
            staged = stack.enter_context(tempfile.TemporaryFile())
            staged.write(feed)
            staged.seek(0)
        except OSError as exc:
            raise GitWalkRefusedError(
                spawn, "the commit list could not be written to a temporary file", _STAGE
            ) from exc
        yield staged
