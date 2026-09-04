"""Path containment (SEC-7, T-4, T-5).

Migrations reference content files by relative path, and those migrations come
from a repository that may have been written by anyone. A path such as
``../../../../.ssh/id_ed25519`` or a symlink pointing at ``/etc/shadow`` must be
refused, not read.

Containment is proved by resolving symlinks *first* and comparing the result
against a resolved root. String prefix matching on unresolved paths is defeated
by a symlink; ``os.path.normpath`` alone is defeated by one too.

That check answers *where the path points*, and it is deliberately not the only
one here, because it cannot answer *how the path got there*: a request can
resolve to a file inside the root having travelled out of it through a link on
the way. So a second check walks the components the caller named, before any
resolution flattens them (:func:`assert_no_symlink_escape`, issue #288). The two
want opposite inputs -- one the resolved path, one the unresolved one -- which is
why they are separate functions and why feeding the first one's output to the
second silently disables it.
"""

from __future__ import annotations

import os
import stat
from collections import deque
from pathlib import Path, PurePosixPath

from theurian.domain.errors import (
    InputTooLargeError,
    IrregularSourceFileError,
    PathDepthExceededError,
    PathEscapeError,
)

#: Maximum bytes a single source file may occupy (SEC-8). Knowledge is prose and
#: structured documents; anything larger is a mistake or an attack.
MAX_SOURCE_FILE_BYTES = 8 * 1024 * 1024

#: Maximum path depth below a project root. Guards pathological trees and the
#: quadratic behaviour some parsers show on deeply nested inputs.
MAX_PATH_DEPTH = 32


def resolve_within_root(root: Path, relative: str | PurePosixPath) -> Path:
    """Resolve ``relative`` against ``root`` and prove the result stays inside.

    Args:
        root: The permitted root. Resolved before comparison, so a symlinked
            root (``/tmp`` on macOS is one) does not produce a false rejection.
        relative: A path relative to ``root``.

    Returns:
        The resolved absolute path.

    Raises:
        PathEscapeError: If ``relative`` is absolute, traverses above ``root``,
            or resolves outside ``root`` through a symlink.
        PathDepthExceededError: If ``relative`` nests past
            :data:`MAX_PATH_DEPTH`. A :class:`PathEscapeError` subclass, so
            every caller catching the base type still catches it -- but with
            its own message, because such a path need never have left ``root``
            and "escapes the permitted root" was false for it (issue #233).

    The check uses ``Path.resolve()`` followed by ``is_relative_to``, which
    operates on the fully resolved forms of both paths. Every symlink in the
    chain has already been followed by the time the comparison happens, so a
    symlink escape cannot slip past it.
    """
    resolved_root = root.resolve()

    candidate = Path(relative)
    if candidate.is_absolute():
        raise PathEscapeError(str(relative), str(resolved_root))

    if len(candidate.parts) > MAX_PATH_DEPTH:
        raise PathDepthExceededError(str(relative), str(resolved_root), limit=MAX_PATH_DEPTH)

    # `strict=False` so a not-yet-existing file still resolves; the containment
    # check is about *where* the path points, not whether it exists yet.
    resolved = (resolved_root / candidate).resolve()

    if not resolved.is_relative_to(resolved_root):
        raise PathEscapeError(str(relative), str(resolved_root))

    return resolved


#: How many symlinks one requested path may be expanded through before the walk
#: refuses. Matches the ``ELOOP`` ceiling Linux applies to a single pathname, so
#: a chain this walk refuses is one no ``open()`` would have followed either. It
#: is also what bounds the walk's cost: see :func:`assert_no_symlink_escape`.
MAX_SYMLINK_HOPS = 40


def _step(current: Path, part: str) -> Path:
    """Where one *named* component of the requested path puts the walk.

    Pure path arithmetic over a position the caller has already expanded, so
    ``..`` here is the real parent and not a lexical guess.

    "Already expanded" is what ``Path.is_symlink()`` answered, and it reports
    ``False`` for a link it could not stat at all -- an unreadable parent
    directory, a component past ``NAME_MAX``. Such a position is stepped over as
    though it were a real directory. That is the pre-existing behaviour of every
    check in this module rather than something this walk introduces (issue
    #543), and it costs a refusal, never a read outside the root:
    ``resolve_within_root`` proves the destination separately, and a component
    the kernel cannot stat is one no ``open()`` will traverse either.

    ``part`` is a POSIX component, because ``requested`` is parsed as a
    ``PurePosixPath``. A Windows-style ``a\\b`` therefore arrives as one
    component rather than two; ``resolve_within_root`` catches the forms that
    matter on the platforms this ships to, and CI has no Windows leg to measure
    the rest.
    """
    if part == "/":
        return Path(current.anchor or "/")
    if part == "..":
        return current.parent
    return current / part


def _expand(
    link: Path,
    *,
    pending: deque[str],
    hops: int,
    requested: str | PurePosixPath,
    resolved_root: Path,
) -> tuple[Path, int]:
    """Follow ``link`` by exactly **one** hop, and say where that lands.

    One hop, never ``Path.resolve()``. Resolving collapses a component's whole
    chain and reports only where it ended, so a link whose own target leaves the
    root and comes back is indistinguishable from one that never left -- the
    mechanism behind five of the six traversal spellings that reached ``migrate
    validate`` exit 0 in round 1 (R1-A). The caller re-enters this function until
    the position is not a link, so a chain is checked at every one of its joints
    rather than at its end.

    A **relative** target is queued rather than joined: its components go back
    through :func:`_step`, so a ``..`` inside it is checked like any other
    position. An **absolute** target is taken whole, and that asymmetry is
    deliberate -- walking its components would stand the walk at ``/`` first and
    refuse every legitimate absolute link that points inside the root. The
    residual is narrow and recorded rather than hidden: ``..`` *inside* an
    absolute target is collapsed lexically, so a target that climbs through a
    symlinked directory names a position the kernel would not.
    ``resolve_within_root`` still proves the true destination independently at
    every call site, so what such a construction can buy is a missed refusal on
    the route, never a read outside the root.
    """
    if hops >= MAX_SYMLINK_HOPS:
        raise PathEscapeError(str(requested), str(resolved_root))
    try:
        target = PurePosixPath(link.readlink())
    except OSError as exc:
        # `is_symlink()` said there was a link and the read of it failed: the
        # directory turned unreadable, or the entry went away mid-walk. Refused
        # rather than stepped over, because this function's whole output is a
        # proof of containment and an unread link is a component it cannot prove
        # anything about. Graded, so `--json` still publishes a document.
        raise PathEscapeError(str(requested), str(resolved_root)) from exc
    if target.is_absolute():
        return Path(os.path.normpath(str(target))), hops + 1
    pending.extendleft(reversed(target.parts))
    return link.parent, hops + 1


def assert_no_symlink_escape(root: Path, *, base: Path, requested: str | PurePosixPath) -> None:
    """Assert the walk to ``requested`` never stands outside ``root`` once inside it.

    ``resolve_within_root`` rejects a *destination* outside the root. This
    function rejects a **route** that leaves it, however the path is spelled and
    whether or not it comes back: a request that resolves to a file genuinely
    inside the root, reached by stepping out through a link and returning, is
    refused. That shape is never legitimate in a knowledge repository, and
    allowing it would mean the set of readable files depends on symlink topology
    rather than on the directory tree.

    **Positions, not endpoints -- and the difference is the whole guard.** Two
    earlier shapes of this function each checked only where a resolution *landed*
    and so enforced nothing much:

    * Walking ``Path.resolve()``'s output (issue #288) meant the loop only ever
      visited real directories, because resolution had already replaced every
      link with its destination. Deleting the guard kept the suite green.
    * Walking the requested components but resolving each one whole (round 1,
      R1-A) still collapsed a component's entire chain before comparing, and
      never checked the ``..`` arm at all. Five of six spellings of one
      in-and-out traversal reached ``migrate validate`` exit 0; only the
      two-component spelling the fix had been written against was refused.

    So a symlink is expanded **one hop at a time** (:func:`_advance`), each hop's
    landing checked, and every component's position checked -- ``..`` included.

    **The latch is what lets ``base`` sit outside ``root``.** The walk is
    permissive until it first steps inside the root and total from then on. That
    is what keeps ``_destination_of`` working, whose ``contentFile`` starts in
    ``migrations/`` and is outside ``knowledge/`` for its first two components,
    while still refusing anything that leaves once it has arrived.

    Cost is bounded by construction rather than by trust in the input: at most
    one ``lstat`` per component plus one per hop, with components capped by the
    published schema's 1024-character ``contentFile`` and hops by
    :data:`MAX_SYMLINK_HOPS`. The shape it replaces called ``Path.resolve()`` per
    component, which defeats realpath's own memoisation and measured 276 ms for a
    single schema-legal path over a 900-link chain, rising linearly with no cap.

    Args:
        root: The permitted root. No position the walk stands on, after the first
            that is inside it, may be outside it.
        base: The directory ``requested`` is interpreted from, and where the walk
            starts -- so ``base``'s own components are never examined, which is
            what keeps a checkout under a symlinked ``/tmp`` from refusing
            itself. It is always a directory Theurian computed (a project root,
            ``.theurian/migrations/``), never a caller-supplied string, and it
            may legitimately sit outside ``root``: a ``contentFile`` is written
            relative to the migration file and reaches ``knowledge/`` by climbing
            out of ``migrations/``.
        requested: The path relative to ``base``, exactly as the caller wrote it.

    Raises:
        PathEscapeError: If the walk stands outside ``root`` after having been
            inside it, if it ends outside ``root``, if a link cannot be read, or
            if it would traverse more than :data:`MAX_SYMLINK_HOPS` links.

    ``base`` and ``requested`` are keyword-only because ``root`` and ``base`` are
    both directories: a silent transposition of the two would disable the check
    while every type still fitted, which is the one mistake at this call site
    that no reviewer and no type checker would see.
    """
    resolved_root = root.resolve()
    current = base.resolve()
    # `base` may legitimately sit outside `root`: `_destination_of` walks a
    # `contentFile` from `migrations/` towards `knowledge/`, and every position
    # before it arrives is outside. So the walk is permissive until it first
    # steps inside, and total from then on -- once a position is in the root, no
    # later position may be out of it. Checking only the ends instead is what
    # let five of six traversal spellings through (round 1, R1-A).
    entered = current.is_relative_to(resolved_root)

    pending = deque(PurePosixPath(requested).parts)
    hops = 0
    while True:
        # Links first, and *whether or not* components remain: the last
        # component's chain is walked to its end too. Expanding it only while
        # more parts were pending left the tail of a trailing link unchecked,
        # which is R1-A's mechanism (i) moved rather than fixed.
        if current.is_symlink():
            current, hops = _expand(
                current,
                pending=pending,
                hops=hops,
                requested=requested,
                resolved_root=resolved_root,
            )
        elif pending:
            current = _step(current, pending.popleft())
        else:
            break
        now_inside = current.is_relative_to(resolved_root)
        if entered and not now_inside:
            raise PathEscapeError(str(requested), str(resolved_root))
        entered = entered or now_inside

    if not current.is_relative_to(resolved_root):
        raise PathEscapeError(str(requested), str(resolved_root))


def _unbounded_shape(mode: int) -> str | None:
    """Name the file type whose read ``st_size`` does not bound, or ``None``.

    The size cap below is computed from ``st_size``, which says what a read will
    return only for a regular file. Every type named here reports ``st_size`` 0
    and then either blocks (a FIFO with no writer, a device waiting on hardware)
    or returns bytes without end (``/dev/zero``), so the cap it passed was never
    a bound on anything (issue #215).

    A directory is not named, and so is not refused here: ``open()`` rejects one
    with ``EISDIR`` before a byte is read, which can neither block nor stream,
    and ``_read_failure_remedy``'s ``EISDIR`` branch already answers it with a
    remedy naming that exact fault. Widening this function to cover directories
    would take that refusal away from the branch that says it best.

    The residual branch is what keeps the check total: a type this build has
    never met -- a Solaris door, a whiteout entry -- is refused rather than
    read, because "not a regular file" is the property that matters and the
    enumeration above is only about *saying* which one it was.
    """
    if stat.S_ISREG(mode) or stat.S_ISDIR(mode):
        return None
    if stat.S_ISFIFO(mode):
        return "a named pipe (FIFO)"
    if stat.S_ISSOCK(mode):
        return "a socket"
    if stat.S_ISCHR(mode):
        return "a character device"
    if stat.S_ISBLK(mode):
        return "a block device"
    return "a special file"


def read_source_file(root: Path, relative: str | PurePosixPath) -> bytes:
    """Read a project file with containment and size limits enforced.

    The single supported way for Theurian to read content referenced from a
    migration. Reading a source file by any other route bypasses SEC-7 and SEC-8.

    **Pass ``relative`` as the caller wrote it, not as ``resolve()`` returned
    it.** The symlink-escape half of the containment check walks these
    components, and ``Path.resolve()`` has already replaced every link with its
    destination -- so a caller that flattens first still gets the size cap, the
    shape check and the destination containment, and silently gets nothing from
    the symlink walk. That failure is silent by construction: it removes a
    refusal, and no test that reads a legitimate file can notice. A caller that
    can only hold a flattened form therefore owes an
    :func:`assert_no_symlink_escape` call of its own, made upstream on the
    string its author actually wrote;
    ``migration_loader.py::_parse_upsert`` is the worked example (issue #288).

    Raises:
        PathEscapeError: If the path escapes ``root`` -- either by resolving
            outside it, or by taking a route that leaves it and returns, however
            that route is spelled (:func:`assert_no_symlink_escape`, issue #288).
        IrregularSourceFileError: If the file is one whose read ``st_size`` does
            not bound -- a FIFO, a socket, a device (issue #215).
        InputTooLargeError: If the file exceeds :data:`MAX_SOURCE_FILE_BYTES`.
        FileNotFoundError: If the file does not exist.
    """
    resolved = resolve_within_root(root, relative)
    # `relative`, not `resolved`: the guard's whole subject is the components the
    # caller named, and handing it the resolved form is what left it dead (#288).
    assert_no_symlink_escape(root, base=root, requested=relative)

    info = resolved.stat()
    shape = _unbounded_shape(info.st_mode)
    if shape is not None:
        # Refused before the size cap, because for these types the size is the
        # lie: a FIFO reports 0, passes the cap, and then blocks in `open()`
        # until a writer appears -- measured against the real CLI as `migrate
        # validate --json` never returning, with an empty stdout where `--json`
        # promises a document. `stat` is what makes the refusal reachable at
        # all: it answers from the directory entry and never opens anything, so
        # unlike every check that follows it cannot be the thing that hangs.
        #
        # Residual, recorded rather than closed: the file could be replaced by a
        # FIFO between this `stat` and the read below -- the same window the
        # post-read size re-check covers for a file that grows. Closing it means
        # opening with `O_NONBLOCK` and reading through the descriptor.
        #
        # It is graded on the *actor*, not on an equivalence to the parked case.
        # A FIFO left in place is exactly what this branch refuses, so "they
        # could leave one there instead" -- which an earlier version of this note
        # said -- is the opposite of true. What holds is narrower: winning the
        # race takes local write access to the working tree at the instant of the
        # read, and an actor with that reaches the same availability outcome by
        # means this guard was never between them and (truncate the file, delete
        # it, make the directory unreadable). A `git clone` carries no FIFO
        # either -- Git stores no such mode (100644, 100755, 120000, 160000,
        # 040000) -- so the race is reachable from the machine and not from the
        # repository. The outcome is availability, never disclosure.
        #
        # `relative` is deliberately not passed: it is the caller's own string,
        # unnormalized and attacker-influenceable, and this branch runs after
        # containment rather than before it (see `IrregularSourceFileError`, and
        # the no-echo test that pins every raise site in this module).
        raise IrregularSourceFileError(shape)
    if info.st_size > MAX_SOURCE_FILE_BYTES:
        raise InputTooLargeError("source file size", MAX_SOURCE_FILE_BYTES, info.st_size)

    data = resolved.read_bytes()

    # Re-check post-read: the file could have grown between stat and read.
    if len(data) > MAX_SOURCE_FILE_BYTES:
        raise InputTooLargeError("source file size", MAX_SOURCE_FILE_BYTES, len(data))

    return data


def is_world_accessible(path: Path) -> bool:
    """Whether ``path`` grants any permission to group or other.

    Used to refuse a token file that other local users can read (SEC-4). A token
    that is readable by anyone is not a credential.
    """
    return bool(path.stat().st_mode & 0o077)


def ensure_private_mode(path: Path, *, mode: int = 0o600) -> bool:
    """Tighten ``path`` to ``mode`` if it is more permissive.

    Returns:
        ``True`` if the mode was changed, so setup can report the correction
        rather than fixing it silently (§34: setup logs what it changed).
    """
    current = path.stat().st_mode & 0o777
    if current == mode:
        return False
    os.chmod(path, mode)  # noqa: PTH101 -- Path.chmod would follow a symlink here
    return True
