"""Path containment (SEC-7, T-4, T-5).

Migrations reference content files by relative path, and those migrations come
from a repository that may have been written by anyone. A path such as
``../../../../.ssh/id_ed25519`` or a symlink pointing at ``/etc/shadow`` must be
refused, not read.

Containment is proved by resolving symlinks *first* and comparing the result
against a resolved root. String prefix matching on unresolved paths is defeated
by a symlink; ``os.path.normpath`` alone is defeated by one too -- and so is
any lexical collapse *inside* this module, which is why the route walk below
queues a link target's components rather than normalising them.

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
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from theurian.domain.errors import (
    InputTooLargeError,
    IrregularSourceFileError,
    PathDepthExceededError,
    PathEscapeError,
    SymlinkBudgetExceededError,
    UnanchoredLinkTargetError,
    UnreadableLinkError,
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


class _EndOfTarget:
    """Queued after an absolute link target, to close the approach it opened.

    An object rather than a reserved string: every ``str`` in the queue is a path
    component the caller supplied, and a sentinel drawn from the same alphabet
    is one crafted component away from being mistaken for its own marker.
    """

    __slots__ = ()


_END_OF_TARGET: Final = _EndOfTarget()


def _anchors(root: Path, base: Path) -> tuple[tuple[Path, Path, bool], ...]:
    """The spellings an absolute link target may be anchored at, and their latch.

    **Derived from the walk's own two arguments, never hand-listed.** Each of
    ``root`` and ``base`` contributes the form it was passed in and the form it
    resolves to -- four entries, of which duplicates are ordinary (at
    ``read_source_file`` all four are the same directory). A hand list would make
    a fifth spelling of the root part of the *escape* space; deriving it makes a
    fifth spelling join the anchor set instead, which is the direction that fails
    closed.

    Each entry is ``(spelling, position, armed)``. The **spelling** is what a
    target is matched against, lexically; the **position** is where the walk
    then stands, and it is always the resolved directory -- an as-passed
    spelling names a real directory by another name, and walking from the name
    rather than the directory would refuse it on the very first containment
    check, which is the mistake this pair exists to prevent.

    ``True`` means a target anchored here is walked **armed**: a root spelling
    names a position already inside the root, so its tail may never leave.
    ``False`` means **permissive-until-entry**: ``base`` may legitimately sit
    outside the root -- ``_destination_of`` walks a ``contentFile`` from
    ``migrations/`` -- so a target anchored there is judged the way that approach
    is. Root spellings come first, so where the two coincide the stricter latch
    is the one that applies.
    """
    resolved_root, resolved_base = root.resolve(), base.resolve()
    return (
        (resolved_root, resolved_root, True),
        (root, resolved_root, True),
        (resolved_base, resolved_base, False),
        (base, resolved_base, False),
    )


def _admit(
    target: PurePosixPath, anchors: tuple[tuple[Path, Path, bool], ...]
) -> tuple[Path, bool, tuple[str, ...]] | None:
    """``(anchor, armed, tail)`` for an absolute ``target``, or ``None`` to refuse.

    Purely lexical, deliberately: the question is whether this *spelling* is one
    the walk can judge, and resolving it first would answer a different question
    -- the one about where it ends, which is what two earlier shapes of this
    guard each got wrong.
    """
    for spelling, position, armed in anchors:
        lexical = PurePosixPath(spelling)
        if target.is_relative_to(lexical):
            return position, armed, target.relative_to(lexical).parts
    return None


@dataclass(frozen=True, slots=True)
class _Walk:
    """The context one walk carries into every step it takes.

    Grouped rather than threaded as four parameters: these three never change
    during a walk, and passing them separately made the one function that needs
    all of them wider than this codebase's argument budget.
    """

    #: The caller's own string, for the refusal. Never rendered (`PathEscapeError`).
    requested: str | PurePosixPath
    resolved_root: Path
    anchors: tuple[tuple[Path, Path, bool], ...]


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
    link: Path, *, pending: deque[str | _EndOfTarget], hops: int, walk: _Walk
) -> tuple[Path, int, bool | None]:
    """Follow ``link`` by exactly **one** hop, and say where that lands.

    One hop, never ``Path.resolve()``. Resolving collapses a component's whole
    chain and reports only where it ended, so a link whose own target leaves the
    root and comes back is indistinguishable from one that never left -- the
    mechanism behind five of the six traversal spellings that reached ``migrate
    validate`` exit 0 in round 1 (R1-A). The caller re-enters this function until
    the position is not a link, so a chain is checked at every one of its joints
    rather than at its end.

    **A relative target is queued and walked** from the link's own directory, so
    its components -- ``..`` included -- are judged like any others.

    **An absolute target is admitted only if it is lexically anchored** at a
    known spelling of ``root`` or ``base`` (:func:`_anchors`). Its tail is then
    queued and walked under the latch that anchor implies: a root anchor is
    *armed*, because the target names a position already inside the root; a base
    anchor is *permissive-until-entry*, because ``base`` may legitimately sit
    outside. An **unanchored** absolute target is refused outright -- even if its
    route would re-enter the root -- because there is no anchor under which its
    prefix could be judged, and a route this walk cannot judge is not a route it
    will walk.

    That admission rule is round 2's ruling, and it exists because the obvious
    alternatives are each wrong in one direction. Taking an absolute target
    *lexically* (``os.path.normpath``) let an eighth spelling of the in-and-out
    route through every call site, and simultaneously refused a legitimate
    in-project link spelled through a symlinked ancestor. Walking an absolute
    target under a *reset* latch forgave every absolute-target escape instead:
    measured, the seven-spelling refusal matrix fell to three of seven, because
    almost every symlink on disk stores an absolute target and the reset was one
    hop away for any of them. Walking it under the *inherited* armed latch
    refuses every absolute in-root link, whose prefix begins at ``/``.
    """
    if hops >= MAX_SYMLINK_HOPS:
        # Not an escape: every link in such a chain may point inside the root,
        # and a 41-link chain living entirely in `knowledge/` was measured being
        # told to find the link that leaves the project (#233's family, round 2).
        raise SymlinkBudgetExceededError(
            str(walk.requested), str(walk.resolved_root), limit=MAX_SYMLINK_HOPS
        )
    try:
        target = PurePosixPath(link.readlink())
    except OSError as exc:
        # `is_symlink()` said there was a link and the read of it failed: the
        # directory turned unreadable, or the entry went away mid-walk. Refused
        # rather than stepped over, because this function's whole output is a
        # proof of containment and an unread link is a component it cannot prove
        # anything about. Graded, so `--json` still publishes a document.
        raise UnreadableLinkError(str(walk.requested), str(walk.resolved_root)) from exc
    if target.is_absolute():
        admitted = _admit(target, walk.anchors)
        if admitted is None:
            raise UnanchoredLinkTargetError(str(walk.requested), str(walk.resolved_root))
        anchor, armed, tail = admitted
        # The anchor becomes the position and only the tail is queued, so the
        # approach this target opens starts where the anchor says it is rather
        # than at `/` -- which is outside every root, and would refuse each
        # absolute link that points inside one.
        pending.appendleft(_END_OF_TARGET)
        pending.extendleft(reversed(tail))
        return anchor, hops + 1, armed
    pending.extendleft(reversed(target.parts))
    return link.parent, hops + 1, None


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

    * Judging *every* position under one latch, with an absolute target's
      components walked from ``/`` (round 2, R2-H1), is not satisfiable at all.
      Inherit the armed latch and every legitimate absolute in-root link is
      refused, because its prefix begins outside the root by construction; reset
      it and every absolute-target escape is forgiven, because almost every
      symlink on disk stores an absolute target. Measured: the seven-spelling
      matrix fell to three of seven.

    **The closure argument, which is what this function is for:** *every position
    the walk stands on is judged under an anchor-appropriate latch, and a route
    we cannot judge is refused -- no walked position is ever unjudged, no
    unjudgeable route is ever walked.*

    A position is **reached by** exactly one of four kinds, and each names the
    latch it is judged under:

    * a **component step** -- judged under the current approach's latch;
    * a **``..`` step** -- the same, which is the arm R1-A left unchecked;
    * a **relative-target hop** -- the same, walked from the link's directory;
    * an **absolute-target hop** -- admitted only if lexically anchored at a
      known spelling of ``root`` or ``base`` (:func:`_anchors`), then walked from
      that anchor under the latch the anchor implies: **armed** for a root
      spelling, **permissive-until-entry** for a base spelling. Unanchored, it is
      refused outright, because no anchor exists under which its prefix could be
      judged.

    **The latch is what lets ``base`` sit outside ``root``.** An approach is
    permissive until it first steps inside the root and total from then on. That
    is what keeps ``_destination_of`` working, whose ``contentFile`` starts in
    ``migrations/`` and is outside ``knowledge/`` for its first two components,
    while still refusing anything that leaves once it has arrived.

    **The recorded residual, which fails closed.** An absolute target anchored at
    a *third* machine-specific alias of the root -- neither the spelling the
    caller passed nor the one it resolves to -- is refused although its route is
    legitimate. Reaching it takes a committed symlink whose target names a path
    specific to one machine, and the failure is a refusal with a remedy rather
    than a read, so the direction is the safe one. The alternative -- judging an
    absolute target only by where it ends -- leaves open the exact route five
    published documents promise against.

    Cost is bounded by construction rather than by trust in the input: one
    ``lstat`` per position, where positions are the requested path's components
    plus, for each of at most :data:`MAX_SYMLINK_HOPS` hops, that target's own
    components. A target's length is bounded by ``PATH_MAX`` and not by the
    schema, so the schema's 1024-character ``contentFile`` bounds only the first
    term -- an earlier sentence here named it as the whole bound and understated
    the loop by about 24x (13,524 iterations, 512 ms, for one legal
    ``contentFile``). The shape this replaced called ``Path.resolve()`` per
    component, defeating realpath's own memoisation: 276 ms for a single
    schema-legal path over a 900-link chain, rising linearly with no cap at all.

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
            inside it, if it ends outside ``root``, if an absolute link target is
            not anchored at a known spelling of ``root`` or ``base``, if a link
            cannot be read, or if it would traverse more than
            :data:`MAX_SYMLINK_HOPS` links.

    ``base`` and ``requested`` are keyword-only because ``root`` and ``base`` are
    both directories: a silent transposition of the two would disable the check
    while every type still fitted, which is the one mistake at this call site
    that no reviewer and no type checker would see.
    """
    resolved_root = root.resolve()
    current = base.resolve()
    # One latch per *approach*. An approach is permissive until it first steps
    # inside the root and total from then on, which is what lets `base` sit
    # outside `root` -- `_destination_of` walks a `contentFile` from
    # `migrations/` towards `knowledge/`, and every position before it arrives is
    # outside. An absolute link target opens a nested approach, because it too
    # restarts from outside the root by construction; when its components are
    # spent the approach closes and the position it landed on is judged by the
    # approach that met the link. That is what tells the two absolute shapes
    # apart: a target that enters the root and then leaves it is refused inside
    # its own approach, while one that merely starts outside and walks in is not.
    approaches = [current.is_relative_to(resolved_root)]
    walk = _Walk(requested, resolved_root, _anchors(root, base))

    pending: deque[str | _EndOfTarget] = deque(PurePosixPath(requested).parts)
    hops = 0
    while True:
        # Links first, and *whether or not* components remain: the last
        # component's chain is walked to its end too. Expanding it only while
        # more parts were pending left the tail of a trailing link unchecked,
        # which is R1-A's mechanism (i) moved rather than fixed.
        if current.is_symlink():
            current, hops, opened = _expand(current, pending=pending, hops=hops, walk=walk)
            if opened is not None:
                approaches.append(opened)
        elif pending:
            item = pending.popleft()
            if isinstance(item, _EndOfTarget):
                # Never the outermost: that one is the caller's own approach and
                # nothing closes it but the end of the walk.
                if len(approaches) > 1:
                    approaches.pop()
            else:
                current = _step(current, item)
        else:
            break
        now_inside = current.is_relative_to(resolved_root)
        if approaches[-1] and not now_inside:
            raise PathEscapeError(str(walk.requested), str(walk.resolved_root))
        approaches[-1] = approaches[-1] or now_inside

    if not current.is_relative_to(resolved_root):
        raise PathEscapeError(str(walk.requested), str(walk.resolved_root))


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
