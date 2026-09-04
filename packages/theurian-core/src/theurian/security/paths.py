"""Path containment (SEC-7, T-4, T-5).

Migrations reference content files by relative path, and those migrations come
from a repository that may have been written by anyone. A path such as
``../../../../.ssh/id_ed25519`` or a symlink pointing at ``/etc/shadow`` must be
refused, not read.

The only correct check resolves symlinks *first* and compares the result against
a resolved root. String prefix matching on unresolved paths is defeated by a
symlink; ``os.path.normpath`` alone is defeated by one too.
"""

from __future__ import annotations

import os
import stat
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


def assert_no_symlink_escape(root: Path, *, base: Path, requested: str | PurePosixPath) -> None:
    """Assert nothing on the way to ``requested`` leaves ``root`` via a symlink.

    ``resolve_within_root`` already rejects a final target outside the root. This
    function additionally rejects the case where an *intermediate* component is a
    symlink that leaves the root, even when the final resolved path happens to
    come back inside. That shape is never legitimate in a knowledge repository,
    and allowing it would mean the set of readable files depends on symlink
    topology rather than on the directory tree.

    **The walk is over the path as the caller named it, never over a resolved
    one.** Walking a resolved path is what made the sentence above false for the
    whole of this function's first life (issue #288): ``Path.resolve()`` has
    already replaced every link with its destination, so the loop only ever
    visited real directories and no component it saw could be a link at all. A
    ``hop -> outside`` / ``outside/back -> root/real`` chain read 60 bytes
    through this guard, and ``migrate validate`` exited 0 on a migration naming
    it. Passing the unresolved path in was measured not to be enough on its own,
    because the old body re-resolved its own argument.

    Args:
        root: The permitted root. Every symlink the walk meets must resolve
            inside it, and so must the destination.
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
        PathEscapeError: If any component of ``requested`` is a symlink resolving
            outside ``root``, or if the walk ends outside ``root``.

    ``base`` and ``requested`` are keyword-only because ``root`` and ``base`` are
    both directories: a silent transposition of the two would disable the check
    while every type still fitted, which is the one mistake at this call site
    that no reviewer and no type checker would see.
    """
    resolved_root = root.resolve()
    current = base.resolve()

    for part in PurePosixPath(requested).parts:
        if part == "..":
            # `current` is fully resolved at every step, so its real parent is
            # what `..` means from here. Reading it lexically instead would undo
            # a link this loop had just followed.
            current = current.parent
            continue
        candidate = current / part
        if candidate.is_symlink():
            current = candidate.resolve()
            if not current.is_relative_to(resolved_root):
                raise PathEscapeError(str(requested), str(resolved_root))
            continue
        current = candidate

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
            outside it, or by traversing an intermediate symlink that leaves it
            (:func:`assert_no_symlink_escape`, issue #288).
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
