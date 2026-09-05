"""Truncating writes that refuse a symbolic link at the target (SEC-7, ADR-0004).

``Path.write_text`` and ``open(path, "w")`` follow a symbolic link and then
``O_TRUNC`` whatever it names, so a link planted where Theurian writes turns the
write into a destructive write somewhere else. That is one root cause with
several faces -- the write lock (#481), the two active pointers and the
secret-scan record (#523), the ingestion manifest (#394) and the local access
token (#371) -- and this module is the one place the flags that refuse it are
spelled.

**Two guards, and neither replaces the other.** A path derived from a contained
one must *also* be proved contained (``ProjectPaths._contained``): ``O_NOFOLLOW``
refuses the link, but a caller that never asked whether the path stays inside the
working tree has not asked the containment question at all -- and containment
alone waves through a link whose target is *inside* the tree, which is the shape
that truncates a file in the user's own checkout.

**The refusal covers the final component only**, which is the bound
``WriteLock._open`` already records for #481 and this module does not widen:
``O_NOFOLLOW`` constrains the last path component, so an ordinary directory
symlink in the prefix is followed. Every caller here ``mkdir(parents=True,
exist_ok=True)``s the parent first, which resolves that same prefix and returns
before the open, so an ``ELOOP`` arriving from the open is the final component's
-- by ordering, not by errno. Closing the prefix needs ``openat`` against a
directory descriptor at every level, which nothing in this codebase does.

A refusal arrives as a plain :class:`OSError` and is deliberately not translated
into a :class:`~theurian.domain.errors.TheurianError` here. Every caller of these
functions already grades ``OSError`` into its command's ``{error, remedy}``
envelope; a new exception type would slip past those handlers and reach a
``--json`` caller as a traceback, which is the CP-2 escape #549 closed. What the
call sites add is the *wording*: :func:`is_a_symbolic_link_refusal` tells the
link apart from the other ways a write fails, and :func:`symbolic_link_remedy`
gives it the cure.
"""

from __future__ import annotations

import errno
import os
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path

#: The flags a truncating write takes here. ``O_NOFOLLOW`` is the guard;
#: everything else reproduces what ``open(path, "w")`` does, so a caller that
#: swaps one for the other changes nothing but the refusal.
WRITE_FLAGS: Final = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW

#: The flags a *read* takes. ``O_RDONLY`` and the same guard, and no ``O_CREAT``:
#: a read of a path that is not there is a missing file, not something to make.
#:
#: A read through a planted link is not the mirror image of a write through one
#: -- nothing is destroyed -- and it is worse in the direction that matters for a
#: credential: the caller believes it is holding what Theurian stored, and it is
#: holding what the attacker chose. Measured on this branch before the read was
#: converted: ``FileSecretStore.get`` returned the attacker's value, the daemon
#: served it as its bearer token because ``ensure_token`` re-mints only when
#: there is *no* token, and ``theurian doctor`` reported the arrangement
#: satisfied (security round one, HIGH-1).
READ_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW

#: The creation mode for a derived artefact, and **deliberately not the ``0o666``
#: that** ``open(path, "w")`` **passes**.
#:
#: The two are identical under the usual ``022`` umask -- both create ``0644`` --
#: so nothing moves for almost every reader. Where they differ is a umask looser
#: than that, and there ``0o666`` creates a **world-writable** ``active.json``:
#: any local account could then repoint the state pointer, which is the
#: derived-state-trust class (GHSA-266v) reached through a permission bit instead
#: of through a commit. Theurian is one process per user per machine (ADR-0002)
#: and nothing needs group or other write on a file it rebuilds, so the
#: conversion tightens rather than reproducing.
#:
#: Found by CodeQL (``py/overly-permissive-file``) on the first push of this
#: branch, over the ``0o666`` an earlier cut carried for exact parity.
#:
#: Masked by the umask's *complement* -- the file is created with
#: ``mode & ~umask``, so a bit absent here can never be granted and a bit present
#: here can still be taken away. "ANDed with the umask" is what this note said
#: until round one, and it is the opposite operation.
#:
#: Applied only when the open *creates* the file: an artefact an older build left
#: behind keeps the mode it was created with, and nothing here chmods a file it
#: did not create.
_DEFAULT_CREATE_MODE: Final = 0o644

#: The mode :func:`open_for_reading_without_following_a_link` passes and never
#: applies. Kept beside the create mode rather than inlined, so the two are read
#: together and the difference between them is visible: this one is unreachable
#: by construction, and the comment at the call site says why it is written at all.
_SECRET_READ_MODE: Final = 0o600


def open_without_following_a_link(path: Path, *, mode: int = _DEFAULT_CREATE_MODE) -> int:
    """``os.open`` for a truncating write, refusing a link at the final component.

    Returns the descriptor; the caller owns closing it.

    Raises:
        OSError: Whatever the open refuses with. ``ELOOP`` is the symbolic link
            (:func:`is_a_symbolic_link_refusal`); every other errno means what it
            always meant and reaches the caller's existing handler unchanged.
    """
    return os.open(path, WRITE_FLAGS, mode)


def open_for_reading_without_following_a_link(path: Path) -> int:
    """``os.open`` for a read, refusing a link at the final component.

    The read twin, and it exists because the write guard alone leaves a
    credential readable through a plant (security round one, HIGH-1). Returns the
    descriptor; the caller owns closing it.

    Raises:
        OSError: ``ELOOP`` for the link (:func:`is_a_symbolic_link_refusal`),
            ``ENOENT`` for a path that is not there, and whatever else the open
            refuses with.
    """
    # The mode is passed and is never applied: `READ_FLAGS` carries no `O_CREAT`,
    # so this call cannot create a file and the argument reaches nothing. It is
    # written out because `os.open`'s own default is `0o777`, and a reader -- a
    # person or a static analyser, and CodeQL's `py/overly-permissive-file` did --
    # takes an omitted mode for the mode this call would create with. Spelling the
    # restrictive one costs nothing and says which answer is intended if a future
    # edit ever adds the flag that would use it.
    return os.open(path, READ_FLAGS, _SECRET_READ_MODE)


def write_text_without_following_a_link(
    path: Path, text: str, *, mode: int = _DEFAULT_CREATE_MODE
) -> None:
    """Replace ``path``'s contents with ``text``, refusing a link at the target.

    The drop-in for ``Path.write_text(text, encoding="utf-8")`` at a path an
    attacker can plant. UTF-8 like every other file Theurian writes, and
    ``newline=""`` so the bytes on disk are the bytes given: these targets are
    JSON documents another process parses, and a platform that rewrote ``\\n``
    would make one machine's pointer differ from another's for no reason a reader
    could see.

    Raises:
        OSError: As :func:`open_without_following_a_link`, plus whatever the
            write itself refuses with.
    """
    descriptor = open_without_following_a_link(path, mode=mode)
    try:
        handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="")
    except BaseException:
        # `os.fdopen` takes ownership of the descriptor only once it returns, so
        # this arm is the one place the descriptor would leak.
        os.close(descriptor)
        raise
    with handle:
        handle.write(text)


def is_a_symbolic_link_refusal(exc: OSError) -> bool:
    """Whether ``exc`` is ``O_NOFOLLOW`` declining a symbolic link.

    POSIX mandates ``ELOOP`` when ``O_NOFOLLOW`` is set and the final component
    is a symbolic link, and that is what this platform returns (measured on
    macOS 26.6: errno 62). Keyed on the errno rather than on an ``is_symlink()``
    probe beside the open, because a probe is a decision taken before the call it
    describes and the window between the two is a window an attacker picks; the
    kernel's own answer for *this* call has no such window.

    **Two platforms this project does not build on answer the same condition with
    a different errno**, recorded so nobody reads the check as portable: FreeBSD
    returns ``EMLINK`` and NetBSD ``EFTYPE``. Neither is measured here -- there is
    no such machine on this project -- and neither is in CI, whose matrix is
    ubuntu and macOS. A port to either needs this predicate widened, and would
    otherwise degrade silently: the refusal still happens (the open still
    declines), but it would be graded as an ordinary write fault and publish the
    wrong cure.
    """
    return exc.errno == errno.ELOOP


def symbolic_link_remedy(path: Path) -> str:
    """The cure for a symbolic link found at a **derived** path Theurian writes.

    One spelling shared by the derived-path faces -- the two active pointers, the
    secret-scan record and the ingestion manifest -- because the artefact is
    derived (ADR-0004), removing the link costs nothing that is not rebuilt, and a
    repository carrying one has force-added it past that ignore.

    **Not every face of the class**, and the exception is the one where those
    three sentences are all false: the local access token
    (:class:`~theurian.infrastructure.secrets.file_store.SecretPathIsASymbolicLinkError`)
    is not derived, is not rebuilt by anything, and reaches no repository -- so it
    carries a cure of its own that names ``theurian auth rotate`` and the
    directory's permissions instead.

    Naming the leaf rather than the directory holding it is what the
    final-component bound above buys. The *containment* refusal is the other way
    round: its culprit can sit anywhere between the derived subdirectory and the
    leaf, so it can only name the directory, and the application layer spells that
    one beside the helper that raises it.
    """
    return (
        f"Remove the symbolic link at {path} and retry. It is derived state "
        f"(ADR-0004) that Theurian recreates, so nothing authored is lost -- and "
        f"a repository that carries one has committed it past that ignore."
    )


__all__ = [
    "READ_FLAGS",
    "WRITE_FLAGS",
    "is_a_symbolic_link_refusal",
    "open_for_reading_without_following_a_link",
    "open_without_following_a_link",
    "symbolic_link_remedy",
    "write_text_without_following_a_link",
]
