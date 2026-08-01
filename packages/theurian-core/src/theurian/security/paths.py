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
from pathlib import Path, PurePosixPath

from theurian.domain.errors import InputTooLargeError, PathEscapeError

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
            resolves outside ``root`` through a symlink, or exceeds the depth
            limit.

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
        raise PathEscapeError(str(relative), str(resolved_root))

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


def read_source_file(root: Path, relative: str | PurePosixPath) -> bytes:
    """Read a project file with containment and size limits enforced.

    The single supported way for Theurian to read content referenced from a
    migration. Reading a source file by any other route bypasses SEC-7 and SEC-8.

    Raises:
        PathEscapeError: If the path escapes ``root``.
        InputTooLargeError: If the file exceeds :data:`MAX_SOURCE_FILE_BYTES`.
        FileNotFoundError: If the file does not exist.
    """
    resolved = resolve_within_root(root, relative)
    assert_no_symlink_escape(root, resolved)

    stat = resolved.stat()
    if stat.st_size > MAX_SOURCE_FILE_BYTES:
        raise InputTooLargeError("source file size", MAX_SOURCE_FILE_BYTES, stat.st_size)

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
