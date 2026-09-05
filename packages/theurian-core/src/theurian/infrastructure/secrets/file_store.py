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
from theurian.security.no_follow import (
    is_a_symbolic_link_refusal,
    open_without_following_a_link,
)
from theurian.security.paths import ensure_private_mode, is_world_accessible

#: The token file, relative to the data directory. Re-exported from
#: :mod:`theurian.security.env_file`, which is where the application layer
#: reaches it without importing an adapter (ADR-0003).
TOKEN_KEY: Final = _TOKEN_KEY

_SECRET_MODE: Final = 0o600
_DIRECTORY_MODE: Final = 0o700


class SecretPathIsASymbolicLinkError(SecurityError):
    """The secret's path is a symbolic link, so writing would write through it.

    ``FileSecretStore.set`` opened with ``O_WRONLY | O_CREAT | O_TRUNC`` and no
    ``O_NOFOLLOW``, so an attacker who could write to ``<data_dir>/auth`` at the
    moment a token was minted planted a dangling link there and received the
    freshly minted token in a file of their choosing -- ``theurian setup``'s own
    ``apply_token`` doing the writing, and ``theurian doctor`` afterwards
    reporting ``satisfied`` because the file it stat'ed was 0600 (#371, measured).
    Tightening ``auth`` to 0700 first does not defend it: the tightening happens
    after the attacker's write bit has already been used.

    Refused rather than repaired. Unlinking the link and writing the real file
    would put the token where it belongs but leave the operator unaware that
    something with write access to their data directory had been waiting for it;
    the refusal is what makes that visible, and it is the posture
    :class:`InsecureSecretPermissionsError` beside it already takes about a mode.

    **Not the whole of #371.** The *substitution* face -- an attacker unlinking
    ``mcp-token`` and leaving their own 0600 regular file, which ``set`` then
    truncates and overwrites -- is not a symbolic link and nothing here refuses
    it. What that face needs is ``setup`` declining to mint into a group- or
    other-writable ``auth`` directory at all, which is a different guard in a
    different place.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.remedy = (
            f"Remove the symbolic link at {path} and run `theurian auth rotate` to mint a "
            f"fresh token. Something with write access to {path.parent} put it there to "
            f"receive the token Theurian was about to write, so check that directory's "
            f"permissions (it should be 0700) before rotating."
        )
        super().__init__(
            f"{path.name} is a symbolic link, not a secret file. Writing the token would "
            f"send it through the link to whatever it names, so Theurian refuses to write "
            f"it at all."
        )


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
        """Write a secret with restrictive permissions, refusing a symbolic link.

        The file is created with 0600 *before* anything is written to it. Writing
        first and chmod-ing after leaves a window in which the secret exists at
        the default umask.

        ``O_NOFOLLOW`` is what keeps that mode meaningful (#371): without it the
        open followed a link planted at the secret's name and created the 0600
        file wherever the link pointed, so every guarantee this method makes was
        about a file in somebody else's directory. The refusal is the open itself
        rather than an ``is_symlink()`` check beside it, because a check is a
        decision taken before the call it describes and the window between the two
        is one an attacker with the directory's write bit picks.

        It covers the final component only, the bound
        :mod:`theurian.security.no_follow` records: a symlinked ``auth``
        directory is still followed. No containment check applies here at all --
        this is the per-user data directory, not a project tree -- so the link at
        the leaf is the whole of what is refused.

        Raises:
            SecretPathIsASymbolicLinkError: If the secret's own name is a
                symbolic link.
            OSError: For every other way the open or the write can fail, which is
                what it was before.
        """
        self._root.mkdir(parents=True, exist_ok=True, mode=_DIRECTORY_MODE)
        ensure_private_mode(self._root, mode=_DIRECTORY_MODE)

        path = self._path(key)
        try:
            descriptor = open_without_following_a_link(path, mode=_SECRET_MODE)
        except OSError as exc:
            if is_a_symbolic_link_refusal(exc):
                raise SecretPathIsASymbolicLinkError(path) from exc
            raise
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


# `env_file_contents` used to be re-exported from here, and this adapter never
# called it. A whole-file renderer reachable as public API is #128 waiting for
# its next caller: the defect was that the file was rendered rather than merged,
# and the function that renders it is correct only where there is no file yet.
# It lives in `theurian.security.env_file` beside `merge_env_file`, which is
# where a reader meets the choice between them.
__all__ = [
    "TOKEN_KEY",
    "FileSecretStore",
    "InsecureSecretPermissionsError",
    "SecretPathIsASymbolicLinkError",
    "default_data_dir",
]
