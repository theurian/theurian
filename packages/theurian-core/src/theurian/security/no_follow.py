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
#: ANDed with the umask, and applied only when the open *creates* the file: an
#: artefact an older build left behind keeps the mode it was created with, and
#: nothing here chmods a file it did not create.
_DEFAULT_CREATE_MODE: Final = 0o644


def open_without_following_a_link(path: Path, *, mode: int = _DEFAULT_CREATE_MODE) -> int:
    """``os.open`` for a truncating write, refusing a link at the final component.

    Returns the descriptor; the caller owns closing it.

    Raises:
        OSError: Whatever the open refuses with. ``ELOOP`` is the symbolic link
            (:func:`is_a_symbolic_link_refusal`); every other errno means what it
            always meant and reaches the caller's existing handler unchanged.
    """
    return os.open(path, WRITE_FLAGS, mode)


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
    """
    return exc.errno == errno.ELOOP


def symbolic_link_remedy(path: Path) -> str:
    """The cure for a symbolic link found at a derived path Theurian writes.

    One spelling, shared by every face of the class: the artefact is derived
    (ADR-0004), so removing the link costs nothing that is not rebuilt, and a
    repository carrying one has force-added it past that ignore. Naming the leaf
    rather than the directory holding it is what the final-component bound above
    buys -- unlike
    :func:`~theurian.application.project_service.derived_escape_remedy`, which
    answers a *containment* refusal where the culprit can sit anywhere between
    the derived subdirectory and the leaf and so can only name the directory.
    """
    return (
        f"Remove the symbolic link at {path} and retry. It is derived state "
        f"(ADR-0004) that Theurian recreates, so nothing authored is lost -- and "
        f"a repository that carries one has committed it past that ignore."
    )


__all__ = [
    "WRITE_FLAGS",
    "is_a_symbolic_link_refusal",
    "open_without_following_a_link",
    "symbolic_link_remedy",
    "write_text_without_following_a_link",
]
