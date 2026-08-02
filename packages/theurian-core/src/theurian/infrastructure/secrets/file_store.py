"""File-backed secret storage (ADR-0011, SEC-4).

A 0600 file inside a 0700 directory. This is the fallback backend, and it is a
*supported* configuration rather than a degraded one: a headless Linux box often
has no Secret Service, and refusing to run there would be worse than storing a
loopback token in a file only its owner can read.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final, final

from theurian.domain.errors import SecurityError
from theurian.security.env_file import TOKEN_KEY as _TOKEN_KEY
from theurian.security.env_file import env_file_contents
from theurian.security.paths import ensure_private_mode, is_world_accessible

#: The token file, relative to the data directory. Re-exported from
#: :mod:`theurian.security.env_file`, which is where the application layer
#: reaches it without importing an adapter (ADR-0003).
TOKEN_KEY: Final = _TOKEN_KEY

_SECRET_MODE: Final = 0o600
_DIRECTORY_MODE: Final = 0o700


class InsecureSecretPermissionsError(SecurityError):
    """A stored secret is readable by other local users.

    Refused rather than repaired-and-used: a token that other accounts have
    already been able to read is not a credential any more, and silently
    tightening the mode would hide that fact.
    """

    def __init__(self, path: Path, mode: int) -> None:
        self.path = path
        self.mode = mode
        super().__init__(
            f"{path} has mode {mode:04o} and is readable by other users. "
            f"Rotate it with `theurian auth rotate`; tightening the mode is not "
            f"enough once it has been exposed."
        )


@final
class FileSecretStore:
    """Stores secrets as 0600 files under a 0700 directory."""

    backend_id = "file"

    def __init__(self, data_dir: Path) -> None:
        self._root = data_dir / "auth"

    def _path(self, key: str) -> Path:
        # Keys are internal constants, never user input, but a key containing a
        # separator would silently write outside the auth directory.
        if "/" in key or "\\" in key or key.startswith("."):
            msg = f"Invalid secret key: {key!r}"
            raise SecurityError(msg)
        return self._root / key

    async def get(self, key: str) -> str | None:
        """Read a secret.

        Raises:
            InsecureSecretPermissionsError: If the file is group- or
                world-accessible.
        """
        path = self._path(key)
        if not path.exists():
            return None

        if is_world_accessible(path):
            raise InsecureSecretPermissionsError(path, path.stat().st_mode & 0o777)

        return path.read_text(encoding="utf-8").strip()

    async def set(self, key: str, value: str) -> None:
        """Write a secret with restrictive permissions.

        The file is created with 0600 *before* anything is written to it. Writing
        first and chmod-ing after leaves a window in which the secret exists at
        the default umask.
        """
        self._root.mkdir(parents=True, exist_ok=True, mode=_DIRECTORY_MODE)
        ensure_private_mode(self._root, mode=_DIRECTORY_MODE)

        path = self._path(key)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _SECRET_MODE)
        try:
            os.write(descriptor, value.encode("utf-8"))
        finally:
            os.close(descriptor)

        # An existing file keeps its old mode through O_CREAT, so this covers
        # the case where the secret is being replaced rather than created.
        ensure_private_mode(path, mode=_SECRET_MODE)

    async def delete(self, key: str) -> None:
        """Remove a secret. Deleting a missing key is not an error."""
        self._path(key).unlink(missing_ok=True)


def default_data_dir() -> Path:
    """The per-user Theurian data directory.

    ``THEURIAN_DATA_DIR`` overrides it, which is what lets tests and the e2e
    suite run against a disposable profile instead of the developer's own.
    """
    override = os.environ.get("THEURIAN_DATA_DIR")
    return Path(override) if override else Path.home() / ".theurian"


__all__ = [
    "TOKEN_KEY",
    "FileSecretStore",
    "InsecureSecretPermissionsError",
    "default_data_dir",
    "env_file_contents",
]
