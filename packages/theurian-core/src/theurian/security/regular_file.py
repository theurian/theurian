"""Reads that cannot wait on an artefact somebody planted (#526, #586).

``Path.read_text`` and a bare ``os.open`` for reading both block without bound
when the name they were given holds a **named pipe**: the open waits for a
writer, and nothing in the call says how long. *Waiting* is the whole of what
this module bounds -- a **regular** file of any size is read entire, and
:func:`read_text_from_a_regular_file` records what that costs and who carries
the size question instead. At a path a local account can
write -- the derived pointers under ``.theurian/state/``, the token under
``<data_dir>/auth/`` -- that turns a read into an indefinite stall on whichever
command reached it, publishing nothing to either channel while it waits.

**Two questions, two mechanisms, and one does not answer the other.**
``O_NONBLOCK`` bounds the *open*, so a FIFO hands back a descriptor at once
instead of parking. It says nothing about *what* was opened, and the ``read``
that follows is then the thing that waits. :func:`assert_a_regular_file` is what
decides that second question, and it puts it to the **descriptor** rather than to
the name -- the form ``connection.WriteLock._open`` records for the write lock,
and the one ``security/paths.py::read_source_file`` names as the way to close its
own stat-then-read race. A name can be re-pointed between an answer about it and
the open that follows; ``os.fstat`` asks the kernel about the object this call
actually returned, so there is no window to pick.

This module is deliberately narrow. It does not resolve, contain or follow
anything: containment is ``security/paths.py``'s, and refusing a **symbolic
link** at the final component is ``security/no_follow.py``'s. Both of those are
questions about *where a name points*; this one is about *what a descriptor is*,
and a caller usually wants more than one of the three.
"""

from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING

from theurian.security.paths import unbounded_shape

if TYPE_CHECKING:
    from pathlib import Path


class IrregularArtefactError(OSError):
    """An open landed on something that is not a regular file.

    An ``OSError`` and deliberately not a
    :class:`~theurian.domain.errors.TheurianError`, for the reason
    :mod:`theurian.security.no_follow` records about its own refusals: every
    caller of these openers already grades ``OSError`` into its command's
    ``{error, remedy}`` envelope, and a new exception type would slip past those
    handlers and reach a ``--json`` caller as a traceback -- the CP-2 escape #549
    closed.

    ``errno`` is ``None``, because no errno describes this: the open *succeeded*
    and the fault is what it opened. That also keeps
    :func:`~theurian.security.no_follow.is_a_symbolic_link_refusal` answering
    ``False`` for it, which is what it should say.

    The message names the **leaf** and not the whole path. It reaches an MCP
    client through more than one caller's envelope, and an absolute path there is
    the operator's machine layout rather than the reader's business (GHSA-97q9).
    The path travels on :attr:`path` for the remedy a caller builds from it.
    """

    def __init__(self, path: Path, shape: str) -> None:
        self.path = path
        self.shape = shape
        super().__init__(f"{path.name} is {shape}, not a regular file")


def shape_that_is_not_a_regular_file(mode: int) -> str | None:
    """Name what ``mode`` describes when it is not a regular file, ``None`` otherwise.

    **The verdict vocabulary, and it is not
    :func:`~theurian.security.paths.unbounded_shape`.** That one answers *"will a
    read of this wait, or stream without end?"*, and a **directory** is correctly
    absent from it: a directory descriptor opens at once and the ``read`` refuses
    it with ``EISDIR``, which is the fault #520's branch publishes best. This one
    answers a different question -- *"is a file what is at this name?"* -- and
    for that a directory is as much a wrong answer as a pipe.

    Reaching for the stall vocabulary where a verdict was wanted is exactly what
    round two found (H-3): ``setup_steps``' token probes asked
    ``unbounded_shape``, so a directory at ``<data_dir>/auth/mcp-token`` -- one
    ``mkdir`` for the actor who could plant the pipe -- fell through to
    ``is_file()`` and was published as ``missing``, "No local access token yet",
    over an artefact sitting at that path. ``MISSING`` is the status that makes
    ``setup`` *act*.

    A pure function of ``st_mode``, like both of its neighbours, so a caller
    chooses whether to ask ``stat`` or ``lstat`` or ``fstat`` and this stays out
    of that decision.
    """
    if stat.S_ISDIR(mode):
        return "a directory"
    return unbounded_shape(mode)


def assert_a_regular_file(descriptor: int, path: Path) -> None:
    """Raise :class:`IrregularArtefactError` unless ``descriptor`` is a regular file.

    ``path`` is carried for the message and the caller's remedy only; the
    question is answered entirely from the descriptor, so passing a stale name
    cannot change the verdict.

    The caller owns the descriptor either way: this function neither closes it
    nor takes it. A refusal therefore arrives with the descriptor still open,
    which is why each caller wraps the check in
    ``except BaseException: os.close(...)`` -- an interrupt landing between the
    open and the check would otherwise leak one.

    A **directory** is not a member, because
    :func:`~theurian.security.paths.unbounded_shape` does not name one: a
    directory descriptor opens without waiting, and the *read* refuses it with
    ``EISDIR``, which is the answer the callers' handlers already publish.
    """
    shape = unbounded_shape(os.fstat(descriptor).st_mode)
    if shape is not None:
        raise IrregularArtefactError(path, shape)


def read_text_from_a_regular_file(path: Path, *, newline: str | None = None) -> str:
    """``Path.read_text(encoding="utf-8")`` that cannot wait on a planted artefact.

    The drop-in for a small UTF-8 file at a path a local account can write.

    **"Small" is the caller's promise, not this function's** (#586 round two,
    M-4). What is bounded here is *waiting*: the shape refusal removes the open
    that never returns. Nothing bounds the number of bytes a **regular** file
    hands back, and a regular file is exactly what passes the check -- measured,
    an 8 GiB sparse file at a pointer path read for 15 seconds and took 16 GB of
    resident memory before it returned. Every caller today reads a file Theurian
    itself writes and keeps small (the two state pointers, ``<data_dir>/env``,
    the ingestion manifest), so no cap is imposed and none is claimed; a caller
    that reads something an author controls wants
    ``security/paths.py::read_source_file`` instead, which carries SEC-8's
    ``MAX_SOURCE_FILE_BYTES``.

    Measured 2026-09-06 before this existed, with a named pipe at
    ``.theurian/state/active.json``: ``migrate status``, ``project status``,
    ``index status`` and ``findings build`` each sat inside ``read()`` until a
    12-second kill fired, with zero bytes on stdout and on stderr.

    ``newline`` is forwarded to the decoder untouched and defaults to
    ``Path.read_text``'s own: universal translation. ``<data_dir>/env`` passes
    ``""``, because half of a byte-for-byte promise lives on the *read* -- with
    translation on, every ``\\r\\n`` in a file Theurian only partly owns becomes
    ``\\n`` before the merge sees it, and a ``\\r`` inside a quoted value comes
    back as a newline that splits the assignment in two
    (``setup_steps._read_env_file`` records the measurement). A drop-in that
    silently dropped this argument would rewrite the line endings of lines
    nobody asked it to touch.

    **Symbolic links are followed, exactly as ``Path.read_text`` follows them.**
    This function changes what a read *waits on*, not what it *resolves*; a link
    at a derived pointer is a different guard with a different remedy, and
    ``O_NOFOLLOW`` here would refuse one under this function's wording.
    :func:`~theurian.security.no_follow.open_for_reading_without_following_a_link`
    is the opener that answers the link question.

    Raises:
        IrregularArtefactError: If the path holds a named pipe, a socket or a
            device.
        IsADirectoryError: If it holds a directory -- from the ``read``, which is
            where ``Path.read_text`` raised it before.
        UnicodeDecodeError: If the bytes are not UTF-8, as before.
        OSError: For every other way the open or the read can fail.
    """
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        assert_a_regular_file(descriptor, path)
    except BaseException:
        os.close(descriptor)
        raise
    with os.fdopen(descriptor, encoding="utf-8", newline=newline) as handle:
        return handle.read()


__all__ = [
    "IrregularArtefactError",
    "assert_a_regular_file",
    "read_text_from_a_regular_file",
    "shape_that_is_not_a_regular_file",
]
