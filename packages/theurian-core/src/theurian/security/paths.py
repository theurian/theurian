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


def assert_no_symlink_escape(root: Path, target: Path) -> None:
    """Assert no component of ``target`` leaves ``root`` via a symlink.

    ``resolve_within_root`` already rejects a final target outside the root. This
    function additionally rejects the case where an *intermediate* component is a
    symlink that leaves the root, even when the final resolved path happens to
    come back inside. That shape is never legitimate in a knowledge repository,
    and allowing it would mean the set of readable files depends on symlink
    topology rather than on the directory tree.
    """
    resolved_root = root.resolve()
    current = resolved_root

    try:
        relative = target.resolve().relative_to(resolved_root)
    except ValueError as exc:
        raise PathEscapeError(str(target), str(resolved_root)) from exc

    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            link_target = current.resolve()
            if not link_target.is_relative_to(resolved_root):
                raise PathEscapeError(str(target), str(resolved_root))


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

    Raises:
        PathEscapeError: If the path escapes ``root``.
        IrregularSourceFileError: If the file is one whose read ``st_size`` does
            not bound -- a FIFO, a socket, a device (issue #215).
        InputTooLargeError: If the file exceeds :data:`MAX_SOURCE_FILE_BYTES`.
        FileNotFoundError: If the file does not exist.
    """
    resolved = resolve_within_root(root, relative)
    assert_no_symlink_escape(root, resolved)

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
        # opening with `O_NONBLOCK` and reading through the descriptor, and the
        # attacker this layer defends against is whoever authored the repository
        # (SEC-7, T-5), not a local process racing two adjacent syscalls.
        raise IrregularSourceFileError(str(relative), shape)
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
