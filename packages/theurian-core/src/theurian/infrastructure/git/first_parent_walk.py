"""First-parent history at ``HEAD``, read for traceability collection (ADR-0040 decision 2).

Its one caller is ``migrate apply``, before the write lock. Every read is plumbing over the
one OID ``HEAD`` resolved to, under #1012's environment and argv prefix plus decision 2's
flags; ``test_first_parent_walk.py`` drives each lever decision 2 names and shows it moving an
unhardened read but not this one. A git that fails, outlives :data:`GIT_TIMEOUT_SECONDS` or
prints a stream the framing cannot read refuses with :class:`GitWalkRefusedError`, never
degrading to fewer commits -- except the promisor read, which answers no instead, and, in a
promisor clone, diff-tree's exit 128 at a commit whose root tree or first parent's root tree the
store lacks, which is the tree bound (#1103, #1105).
Each spawn's timeout is a deadline, not a wait for pipe EOF
(#1092 round one): at it, git's process group and then git are killed and the walk refuses at
once. A descendant that left the group is left running; holding stdout at the deadline, it is
reported by that refusal. The binary is resolved to an absolute path on each call -- ADR-0030
clause 5's tier, because this read gates a write.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import itertools
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
#: Counts the listing's, diff-tree's and log's stdout; rev-parse's, the absence check's and the
#: shallow file are outside.
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
#: Whether git here would lazily fetch an absent object, by two of the keys ``promisor-remote.c``
#: reads, as its lazy fetch shows (measured, 2.56.0): any true ``remote.<name>.promisor`` in config
#: as git reads it, includes and ``config.worktree`` too, or else an ``extensions.partialClone`` in
#: the common directory's config file alone. A ``remote.<name>.partialclonefilter`` set without
#: ``promisor`` makes git fetch lazily too; this answers no there, so the walk refuses (#1108).
_PROMISOR_REMOTE: Final = (
    "config",
    "--type=bool",
    "-z",
    "--get-regexp",
    r"^remote\.(.*\.)?promisor$",
)
_PARTIAL_CLONE: Final = ("config", "--local", "-z", "--get", "extensions.partialclone")
#: #1012's fields; ``tformat:`` terminates every record, so all three streams end on a separator.
_LOG_FORMAT: Final = "--format=tformat:%H%x00%cI%x00%B"
_LOG_FIELDS: Final = 3
_HEX_LENGTHS: Final = {b"sha1": 40, b"sha256": 64}
_CHUNK: Final = 1 << 16
#: git's ``die()`` status, diff-tree's at a tree it cannot read.
_DIED: Final = 128
#: The absence check after that 128. rev-list reads each root tree's name off its commit; log
#: would die on a diff config value as diff-tree does. cat-file asks the store for nothing but
#: the name, so a present but corrupt tree answers present (measured, 2.56.0: the default
#: format answers ``missing`` for a corrupt tree, loose or packed, and ``<commit>^{tree}``
#: cannot name an absent tree).
_ROOT_TREES: Final = ("rev-list", "--no-walk=unsorted", "--format=%T", "--stdin")
_PRESENT: Final = ("cat-file", "--batch-check=%(objectname)")

_INSTALL: Final = "Install git and put it on PATH so 'git --version' runs, then retry."
_REPAIR: Final = (
    "Run 'git fsck' in the project's repository and repair what it reports, then retry."
)
#: ``_REPAIR`` in a promisor clone, whose fsck passes over an absent object the remote promises.
_FETCH: Final = (
    "Run 'git log --first-parent --name-status HEAD' in the project's repository so git fetches "
    "from its promisor remote what this walk will not, repair what it reports, then retry."
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
    """The commits from ``walk_commit`` down its first parents, the walk commit first, without
    the shallow-list commit the chain may end at: a depth-1 clone's walk holds none.

    A set ``*_bounded`` flag names the bound that stopped the walk before a root or the shallow
    list.
    """

    walk_commit: str | None
    commits: tuple[WalkedCommit, ...]
    shallow_digest: str | None
    depth_bounded: bool
    budget_bounded: bool
    tree_bounded: bool = False

    def __post_init__(self) -> None:
        boundary = (self.shallow_digest, self.depth_bounded, self.budget_bounded, self.tree_bounded)
        if self.walk_commit is None and (self.commits or boundary != (None, False, False, False)):
            raise ValueError("a walk without a walk commit holds no commits and no boundary")
        if self.commits and self.commits[0].sha != self.walk_commit:
            raise ValueError("the walk commit must come first")


_Bound = Literal["depth", "budget", "tree"] | None

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
    promisor = _promisor(git, root)
    digest, boundary = _read_shallow(shallow, hex_length)
    try:
        listed, spent, bound = _list_commits(git, root, walk_commit, hex_length, boundary)
        paths, spent, tree = _read_paths(git, root, listed, spent, promisor=promisor)
        stamps = _read_messages(git, root, [sha for sha, _ in listed[: len(paths)]], spent)
    except GitWalkRefusedError as exc:
        if promisor and exc.remedy == _REPAIR:
            raise GitWalkRefusedError(exc.spawn, exc.reason, _FETCH) from exc
        raise
    if len(stamps) < len(listed):
        bound = "tree" if tree and len(stamps) == len(paths) else "budget"
    if _read_shallow(shallow, hex_length)[0] != digest:
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
        tree_bounded=bound == "tree",
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


def _promisor(git: str, root: Path) -> bool:
    """Whether the repository is a promisor clone; a read that fails, outlives its deadline or
    ends inside a record answers no, so a tree diff-tree cannot read refuses rather than bounds.
    """
    try:
        with _Child(git, root, _PROMISOR_REMOTE, allowed=(0, 1)) as child:
            values = [token.rpartition(b"\n")[2] for token in child.tokens(b"\0")]
        if b"true" in values:
            return True
        with _Child(git, root, _PARTIAL_CLONE, allowed=(0, 1)) as child:
            list(child.tokens(b"\0"))
    except GitWalkRefusedError:
        return False
    return child.returncode == 0


def _read_shallow(path: Path, hex_length: int) -> tuple[str | None, frozenset[str]]:
    """The SHA-256 of the shallow list's lines, sorted, each newline-terminated, and the OIDs
    they name; ``None`` and none with no list.

    git reads one OID from the head of each line and ignores the rest of it, so any other line
    shape refuses: one line holding two OIDs would digest like two lines and walk unlike them.
    """
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None, frozenset()
    except OSError as exc:
        raise GitWalkRefusedError("rev-parse", "the shallow file cannot be read", _SHALLOW) from exc
    lines = data.split(b"\n")
    if not lines[-1]:
        lines.pop()
    if not all(_is_oid(line, hex_length) for line in lines):
        raise GitWalkRefusedError(
            "rev-parse", "a shallow-file line is not exactly one object id", _SHALLOW
        )
    digest = hashlib.sha256(b"".join(line + b"\n" for line in sorted(lines))).hexdigest()
    return digest, frozenset(line.decode("ascii") for line in lines)


def _list_commits(
    git: str, root: Path, walk_commit: str, hex_length: int, shallow: frozenset[str]
) -> tuple[list[tuple[str, str | None]], list[int], _Bound]:
    """``(commit, first parent)`` per first-parent commit, the bytes read through each line, and
    the bound that stopped the list; every token on a line is an OID.

    Each line is the previous line's first parent, and an unbounded list ends at a commit git
    lists with no parent: a root, or a shallow-list commit (measured: a ``--depth=2`` clone
    lists its boundary commit alone on its line). A chain end *shallow* names is left out, a
    grafted boundary or a true root, as git lists both alike: the cost is one under-collected
    commit, never a false edge, and the digest marks the walk shallow (ADR-0040 decision 2).
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
            expected = oids[1] if len(oids) > 1 else None
            if expected is None and oids[0] in shallow:
                continue
            if len(listed) == DEPTH_BOUND:
                return listed, spent, "depth"
            listed.append((oids[0], expected))
            spent.append(used)
    if expected is not None:
        raise _framing("rev-list", "the list ended at a commit with a parent")
    return listed, spent, None


def _read_paths(
    git: str,
    root: Path,
    listed: Sequence[tuple[str, str | None]],
    spent: Sequence[int],
    *,
    promisor: bool,
) -> tuple[list[tuple[str, ...] | None], list[int], bool]:
    """The paths each commit changed against its first parent, a root against the empty tree,
    one diff-tree record each, up to the first commit that would cross a budget or, in a
    promisor clone, the tree bound; the bytes read through each commit; and whether the tree
    bound stopped the records.

    diff-tree drops a stdin commit it cannot read and still exits 0, so the records are
    matched to the feed one for one, in order. At a tree it cannot read it exits 128 after
    the whole records before that commit and none of that commit's own (measured, 2.56.0: a
    treeless clone's walk commit, its parent's tree absent, gives no record at all).
    """
    feed = b"".join(" ".join(filter(None, pair)).encode() + b"\n" for pair in listed)
    records: list[list[str]] = []
    counts: list[int] = []
    ends: list[int] = []
    entries = used = 0
    in_path = bounded = False
    allowed = (0, _DIED) if promisor else (0,)
    with _Child(git, root, _DIFF_TREE, feed=feed, allowed=allowed) as child:
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
            bounded = child.returncode == _DIED
            if in_path or (len(records) < len(listed)) != bounded:
                raise _framing("diff-tree", f"{len(records)} records for {len(listed)} commits")
    if bounded and not _root_tree_absent(git, root, listed[len(records)]):
        raise GitWalkRefusedError("diff-tree", f"git exited with status {_DIED}", _REPAIR)
    capped = zip(records, counts, strict=True)
    paths = [None if count > PATH_CAP else tuple(names) for names, count in capped]
    return paths, [before + end for before, end in zip(spent, ends, strict=False)], bounded


def _root_tree_absent(git: str, root: Path, pair: tuple[str, str | None]) -> bool:
    """Whether the store lacks the root tree of *pair*'s commit or of its first parent."""
    commits = [sha for sha in pair if sha is not None]
    feed = b"".join(sha.encode() + b"\n" for sha in commits)
    with _Child(git, root, _ROOT_TREES, feed=feed) as child:
        lines = list(itertools.islice(child.tokens(b"\n"), 2 * len(commits) + 1))
    headers = [b"commit " + sha.encode() for sha in commits]
    if len(lines) != 2 * len(commits) or lines[::2] != headers:
        raise _framing("rev-list", "a root tree is not framed under its commit")
    names = [_oid(line, len(pair[0]), "rev-list").encode() for line in lines[1::2]]
    with _Child(git, root, _PRESENT, feed=b"".join(name + b"\n" for name in names)) as child:
        answers = list(itertools.islice(child.tokens(b"\n"), len(names) + 1))
    if len(answers) != len(names) or any(
        answer not in (name, name + b" missing")
        for answer, name in zip(answers, names, strict=True)
    ):
        raise _framing("cat-file", "an answer is not its tree's")
    return any(answer.endswith(b" missing") for answer in answers)


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
