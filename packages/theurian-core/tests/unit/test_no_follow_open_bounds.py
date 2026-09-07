"""``no_follow``'s openers cannot wait, and still refuse what they always did (#586).

``WRITE_FLAGS`` and ``READ_FLAGS`` gained ``O_NONBLOCK``, and both openers gained
an ``fstat`` behind it. The flag is not the guard on its own: a named pipe *with
a reader attached* takes ``O_WRONLY | O_NONBLOCK`` without complaint, and an
``O_RDONLY | O_NONBLOCK`` open of a pipe succeeds and leaves the ``read`` to
wait. The descriptor check is what refuses both.

**What must not move** is everything the flag set already bought: the create
mode, the truncation, the ``ELOOP`` at a symbolic link, the ``EISDIR`` at a
directory, and the reader-less pipe's ``ENXIO`` -- which is the refusal #569's
write side is written around and ``daemon/instance.py`` names in its own comment.
Each is pinned below rather than argued.

``tests/unit/test_no_follow_writes.py`` owns the *population* claim -- which
writers reach these openers at all -- and is already at its size limit; this file
owns what one open does.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from typing import Final

import pytest

from theurian.security.no_follow import (
    READ_FLAGS,
    WRITE_FLAGS,
    open_for_reading_without_following_a_link,
    open_without_following_a_link,
    write_text_without_following_a_link,
)
from theurian.security.regular_file import IrregularArtefactError

pytestmark = pytest.mark.unit

#: ``os.mkfifo`` is POSIX-only, and every plant in this file is a named pipe.
_CAN_MAKE_A_NAMED_PIPE: Final = hasattr(os, "mkfifo")

_A_SECRET_MODE: Final = 0o600


def test_both_flag_sets_carry_the_two_guards() -> None:
    """The flags themselves, asserted so a rewrite cannot quietly drop one.

    ``O_NOFOLLOW`` and ``O_NONBLOCK`` answer different questions -- what the name
    resolves to, and whether the open waits -- and the behavioural tests below
    reach each through a plant. This one is the cheap direct pin, so that a flag
    removed by a merge is named here rather than discovered as a hang.
    """
    for flags, label in ((WRITE_FLAGS, "WRITE_FLAGS"), (READ_FLAGS, "READ_FLAGS")):
        assert flags & os.O_NOFOLLOW, f"{label} no longer refuses a symbolic link"
        assert flags & os.O_NONBLOCK, f"{label} can wait on a named pipe again"


def test_a_fresh_write_still_creates_at_the_mode_it_was_given(tmp_path: Path) -> None:
    """``O_NONBLOCK`` must not have cost the 0600 the token file depends on."""
    path = tmp_path / "mcp-token"
    descriptor = open_without_following_a_link(path, mode=_A_SECRET_MODE)
    os.close(descriptor)
    assert stat.S_IMODE(path.stat().st_mode) == _A_SECRET_MODE


def test_a_write_still_truncates_what_was_there(tmp_path: Path) -> None:
    """``O_TRUNC`` still applies, so a shorter value does not leave a longer one's tail."""
    path = tmp_path / "active.json"
    path.write_text("0123456789", encoding="utf-8")
    write_text_without_following_a_link(path, "ab")
    assert path.read_text(encoding="utf-8") == "ab"


def test_a_symbolic_link_is_still_refused_on_both_sides(tmp_path: Path) -> None:
    """``ELOOP``, unchanged, and it is what ``is_a_symbolic_link_refusal`` keys on."""
    target = tmp_path / "target"
    target.write_text("x", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target)

    for opener in (open_without_following_a_link, open_for_reading_without_following_a_link):
        with pytest.raises(OSError) as raised:
            opener(link)
        assert raised.value.errno == errno.ELOOP, (
            f"{opener.__name__} no longer refuses a symbolic link with ELOOP, so the cure "
            f"keyed on that errno stops being published"
        )


@pytest.mark.skipif(not _CAN_MAKE_A_NAMED_PIPE, reason="os.mkfifo is POSIX-only")
def test_a_reader_less_pipe_still_refuses_the_write_with_enxio(tmp_path: Path) -> None:
    """#569's write-side semantics, pinned rather than assumed to have survived.

    ``O_WRONLY | O_NONBLOCK`` on a pipe nobody is reading refuses with ``ENXIO``
    *before* a descriptor exists -- which is the refusal ``LOCK_OPEN_FLAGS`` and
    ``daemon/instance.py`` are built on. It arrives as a plain ``OSError`` rather
    than as :class:`IrregularArtefactError`, because there is no descriptor to
    ``fstat``; a caller that wants to name the artefact has to look the shape up
    from the path, which is what ``FileSecretStore.set`` does.
    """
    pipe = tmp_path / "mcp-token"
    os.mkfifo(pipe)
    with pytest.raises(OSError) as raised:
        open_without_following_a_link(pipe, mode=_A_SECRET_MODE)
    assert raised.value.errno == errno.ENXIO
    assert not isinstance(raised.value, IrregularArtefactError), (
        "the reader-less pipe is now reported as a descriptor-shape refusal, which means "
        "an open that should never have returned did"
    )


@pytest.mark.skipif(not _CAN_MAKE_A_NAMED_PIPE, reason="os.mkfifo is POSIX-only")
def test_a_pipe_with_a_reader_is_refused_by_the_descriptor_not_by_the_flag(
    tmp_path: Path,
) -> None:
    """The case ``O_NONBLOCK`` lets through, and the whole reason the ``fstat`` is there.

    With a reader attached the write open **succeeds** -- measured 2026-09-06 --
    so without the descriptor check the token would be written into somebody
    else's pipe and this module's promise would be about a file that was never
    opened.
    """
    pipe = tmp_path / "mcp-token"
    os.mkfifo(pipe)
    reader = os.open(pipe, os.O_RDONLY | os.O_NONBLOCK)
    try:
        # The positive control on the plant: the raw open must succeed here, or
        # the test below is passing for the reader-less reason instead.
        raw = os.open(pipe, WRITE_FLAGS, _A_SECRET_MODE)
        os.close(raw)

        with pytest.raises(IrregularArtefactError) as raised:
            open_without_following_a_link(pipe, mode=_A_SECRET_MODE)
        assert raised.value.shape == "a named pipe (FIFO)"
    finally:
        os.close(reader)


@pytest.mark.skipif(not _CAN_MAKE_A_NAMED_PIPE, reason="os.mkfifo is POSIX-only")
def test_a_pipe_is_refused_on_the_read_side_rather_than_waited_on(tmp_path: Path) -> None:
    """The read side's own shape, and it has no ``ENXIO`` half.

    ``O_RDONLY | O_NONBLOCK`` on a pipe succeeds whether or not anyone is
    writing, so the flag alone moves the wait from the open into the ``read``.
    The ``fstat`` is the only thing that refuses it.
    """
    pipe = tmp_path / "mcp-token"
    os.mkfifo(pipe)
    with pytest.raises(IrregularArtefactError) as raised:
        open_for_reading_without_following_a_link(pipe)
    assert raised.value.shape == "a named pipe (FIFO)"


def test_a_directory_still_refuses_the_write_with_eisdir(tmp_path: Path) -> None:
    """``O_WRONLY`` answers a directory before the ``fstat`` can be reached.

    Pinned because the shape namer deliberately does **not** name a directory:
    if that ever changed, this open would start publishing a shape where #520's
    branch publishes ``EISDIR``, and nothing else would notice.
    """
    with pytest.raises(IsADirectoryError):
        open_without_following_a_link(tmp_path)
