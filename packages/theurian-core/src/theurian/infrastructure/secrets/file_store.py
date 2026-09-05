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
    open_for_reading_without_following_a_link,
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
    """The secret's path is a symbolic link, so neither writing nor reading it is safe.

    **Raised by both sides, because closing one leaves the whole attack running.**

    *Write.* ``FileSecretStore.set`` opened with ``O_WRONLY | O_CREAT | O_TRUNC``
    and no ``O_NOFOLLOW``, so an attacker who could write to ``<data_dir>/auth``
    at the moment a token was minted planted a dangling link there and received
    the freshly minted token in a file of their choosing -- ``theurian setup``'s
    own ``apply_token`` doing the writing, and ``theurian doctor`` afterwards
    reporting ``satisfied`` because the file it stat'ed was 0600 (#371, measured).
    Tightening ``auth`` to 0700 first does not defend it: the tightening happens
    after the attacker's write bit has already been used.

    *Read.* The same plant pointed at a file the attacker had **already written**
    needs no minting at all. ``get`` followed the link and handed back the
    attacker's value; ``daemon.runner.ensure_token`` re-mints only when there is
    no token, so it accepted that value and the daemon served it as its bearer
    token; and ``probe_token_storage`` reported satisfied, since ``is_file()``
    follows the link and every mode it stats afterwards is the attacker's own.
    Measured on this branch with the write side already fixed -- which made it
    *worse* in one respect, because ``theurian auth rotate`` then refused the link
    too and nothing an operator could run replaced it (security round one,
    HIGH-1).

    Refused rather than repaired, on both sides. Unlinking the link and writing
    the real file would put the token where it belongs but leave the operator
    unaware that something with write access to their data directory had been
    waiting for it; the refusal is what makes that visible, and it is the posture
    :class:`InsecureSecretPermissionsError` beside it already takes about a mode.

    **Not the whole of #371**, and the two remainders are different shapes:

    * the *substitution* face -- an attacker unlinking ``mcp-token`` and leaving
      their own 0600 regular file, which ``set`` then truncates and overwrites and
      ``get`` reads without complaint. It is not a symbolic link, so no
      ``O_NOFOLLOW`` sees it; what it needs is ``setup`` declining to mint into a
      group- or other-writable ``auth`` directory at all
      ([#573](https://github.com/theurian/theurian/issues/573)), a different guard
      in a different place;
    * the *prefix* face -- a link at ``auth/`` rather than at the secret's own
      name. ``O_NOFOLLOW`` constrains the final component, so both sides follow it
      ([#577](https://github.com/theurian/theurian/issues/577)).

    The message says "writing" nowhere: one type serves both call sites, and a
    sentence naming the wrong verb is what a reader would act on.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        # "put it there" and not "put it there to receive the token": the same
        # link serves both the write attack and the read one, and the earlier
        # wording described only the first. An operator meeting this from `get`
        # is holding a token somebody else chose, which is the reverse direction.
        #
        # The cure names `theurian auth rotate`, which is correct because
        # `TOKEN_KEY` is the only key this store ever holds -- `git grep -n
        # "\.set(\|\.get(" -- packages/theurian-core/src` over the secret-store
        # callers returns `TOKEN_KEY` at every one (2026-09-05). A second key
        # would make this remedy name a command that does not rotate it, so a
        # `SecretStore` gaining one has to revisit this line.
        self.remedy = (
            f"Remove the symbolic link at {path} and run `theurian auth rotate` to mint a "
            f"fresh token. Something with write access to {path.parent} put it there, so "
            f"check that directory's permissions (it should be 0700) before rotating -- and "
            f"treat any token in use since it appeared as compromised."
        )
        super().__init__(
            f"{path.name} is a symbolic link, not a secret file. Theurian refuses to touch "
            f"it: writing would send the token through the link to whatever it names, and "
            f"reading would hand back whatever somebody else put there."
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
        """Read a secret, refusing to read one through a symbolic link.

        **The read twin of :meth:`set`, and it is not decoration** (security round
        one, HIGH-1). Guarding only the write left the whole attack standing with
        the credential still in play: measured with a link at this path naming an
        attacker-owned 0600 file, ``get`` returned the attacker's value,
        ``daemon.runner.ensure_token`` therefore never re-minted -- it mints only
        when there is *no* token -- so the daemon served that value as its bearer
        token, and ``probe_token_storage`` reported the whole arrangement
        *satisfied*, because ``is_file()`` follows the link and every mode it then
        stats is the one the attacker chose. The write refusal made it worse
        rather than better in one respect: ``theurian auth rotate`` declines the
        link too, so nothing the operator can run replaces it.

        ``O_NOFOLLOW`` rather than an ``is_symlink()`` probe, for the reason
        :meth:`set` gives: a probe decides about a path before the call that acts
        on it, and this directory's write bit is exactly what the attacker has.

        The existing checks keep their order and their meaning. ``exists()`` still
        answers "no secret yet" with ``None`` -- and note it answers ``False`` for
        a *dangling* link, so that shape is a missing token rather than a refusal,
        which is correct: nothing was stored and nothing was read. It also answers
        ``False`` for a **prefix loop**, because ``pathlib`` swallows ``ELOOP``
        among the errnos it treats as "not there", and that is what keeps a loop
        above this file from reaching the open at all -- the read side's whole
        ELOOP attribution, recorded on :mod:`theurian.security.no_follow` and
        pinned by ``test_no_follow_writes.py``.

        **The two refusals are ordered, and the order decides which one a planted
        link produces** (round two, security M-5). ``is_world_accessible`` runs
        *before* the open, and it follows the link -- so a link naming a
        world-readable target raises :class:`InsecureSecretPermissionsError`, not
        the symbolic-link refusal, and that error reports the **target's** mode as
        though it were the token's. It is the safer of the two orders (a secret
        other accounts can already read is refused whatever else is wrong with the
        path) and it is not the more informative one; the arrangement is recorded
        rather than reordered, because moving the link check first would let a
        world-readable *regular* token file through on the same code path if the
        symlink arm ever grew a non-refusing branch.

        **The final-component bound applies here exactly as it does to**
        :meth:`set`: ``O_NOFOLLOW`` refuses a link at the secret's own name, and a
        symbolic link at ``auth/`` itself is followed
        ([#577](https://github.com/theurian/theurian/issues/577)).

        Raises:
            InsecureSecretPermissionsError: If the file -- or, through a link, the
                file it names -- is group- or world-accessible. Checked first, so
                it pre-empts the refusal below.
            SecretPathIsASymbolicLinkError: If the secret's own name is a symbolic
                link and its target is not world-accessible.
            OSError: For every other way the open or the read can fail.
        """
        path = self._path(key)
        if not path.exists():
            return None

        if is_world_accessible(path):
            raise InsecureSecretPermissionsError(path, path.stat().st_mode & 0o777)

        try:
            descriptor = open_for_reading_without_following_a_link(path)
        except OSError as exc:
            if is_a_symbolic_link_refusal(exc):
                raise SecretPathIsASymbolicLinkError(path) from exc
            raise
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            return handle.read().strip()

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
