"""The descriptor-shape guard behind every bounded derived-state read (#586).

``security/regular_file.py`` is what the pointer reads, the token accessors and
the ``no_follow`` openers all reach for the same question: *is the thing this
open returned a regular file?* The integration tests in
``tests/integration/test_derived_read_bounds.py`` drive it through the real CLI
in killed children; this module drives the primitive itself, where a socket and a
device are plantable and a CLI fixture would not reach them.

**Every blocking probe here is bounded by a killed child**, for the reason
``test_state_database_faults.py`` records: ``SIGALRM`` does not escape every
blocked read, and a regression must be a red test rather than a stalled suite.
"""

from __future__ import annotations

import os
import socket
import stat
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from theurian.security.regular_file import (
    IrregularArtefactError,
    assert_a_regular_file,
    read_text_from_a_regular_file,
)

pytestmark = pytest.mark.unit

#: ``os.mkfifo`` is POSIX-only, and a named pipe is the one shape whose read is
#: *unbounded* rather than merely wrong.
_CAN_MAKE_A_NAMED_PIPE: Final = hasattr(os, "mkfifo")

#: Present on both platforms this project builds on, and the character-device
#: member of the population -- the shape a path check would have to enumerate and
#: an ``fstat`` simply answers.
_A_CHARACTER_DEVICE: Final = Path("/dev/zero")


def test_a_regular_file_reads_back_exactly_what_was_written(tmp_path: Path) -> None:
    """The drop-in half: same bytes, same decoding, no surprises for the normal case."""
    path = tmp_path / "pointer.json"
    path.write_text('{"stateHash": "abc"}\n', encoding="utf-8")
    assert read_text_from_a_regular_file(path) == '{"stateHash": "abc"}\n'


def test_a_symbolic_link_to_a_regular_file_is_still_read(tmp_path: Path) -> None:
    """The link question belongs to ``no_follow``, and this reader does not answer it.

    Pinned rather than left implicit: the module's docstring says this reader
    changes what a read *waits on* and not what it *resolves*, and a future edit
    adding ``O_NOFOLLOW`` here would silently change the refusal an operator
    meets at a derived pointer without touching the guard that owns links.
    """
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    assert read_text_from_a_regular_file(link) == "{}"


@pytest.mark.skipif(not _CAN_MAKE_A_NAMED_PIPE, reason="os.mkfifo is POSIX-only")
def test_a_named_pipe_is_refused_by_shape_rather_than_waited_on(tmp_path: Path) -> None:
    """The member the whole module exists for.

    ``O_NONBLOCK`` alone would return a descriptor here and leave the ``read`` to
    wait; what refuses is the ``fstat``. The refusal names the shape and the leaf,
    and carries the path for whatever remedy the caller builds.
    """
    pipe = tmp_path / "active.json"
    os.mkfifo(pipe)
    with pytest.raises(IrregularArtefactError) as raised:
        read_text_from_a_regular_file(pipe)
    assert raised.value.shape == "a named pipe (FIFO)"
    assert raised.value.path == pipe
    assert str(raised.value) == "active.json is a named pipe (FIFO), not a regular file"
    assert raised.value.errno is None, (
        "an errno would make this refusal answer `is_a_symbolic_link_refusal` and the "
        "other errno-keyed handlers as though the kernel had reported something"
    )


def test_a_socket_descriptor_is_refused_by_shape(tmp_path: Path) -> None:
    """A socket, which is bounded and wrong rather than unbounded.

    Kept beside the pipe because the guard's population is *not a regular file*
    and not *a named pipe*: a check that enumerated the blocking shape alone
    would pass this test's absence and read a socket as a pointer.

    Driven through the socket's **own** descriptor rather than through a bound
    path, and the reason is that the path route does not reach this branch: an
    ``open`` of a bound ``AF_UNIX`` node is refused by the kernel before any
    ``fstat`` runs (``ENXIO`` on Linux, ``EOPNOTSUPP`` on macOS). Both routes are
    bounded refusals; only this one exercises the shape name.
    """
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as endpoint:
        with pytest.raises(IrregularArtefactError) as raised:
            assert_a_regular_file(endpoint.fileno(), tmp_path / "active.json")
        assert raised.value.shape == "a socket"


def test_a_character_device_is_refused_by_shape() -> None:
    """``/dev/zero`` returns bytes without end, so the read must not start."""
    with pytest.raises(IrregularArtefactError) as raised:
        read_text_from_a_regular_file(_A_CHARACTER_DEVICE)
    assert raised.value.shape == "a character device"


def test_a_directory_is_refused_by_the_read_and_not_by_the_shape(tmp_path: Path) -> None:
    """The one shape the guard deliberately does not name.

    ``unbounded_shape`` excludes a directory because ``read`` answers ``EISDIR``,
    which is the fault the callers' handlers already publish. Pinned here so that
    widening the shape namer -- which two openers in ``infrastructure/sqlite``
    do for themselves -- is a decision rather than an accident.
    """
    with pytest.raises(IsADirectoryError):
        read_text_from_a_regular_file(tmp_path)


def test_the_descriptor_is_closed_when_the_shape_is_refused(tmp_path: Path) -> None:
    """A refusal must not leak the descriptor it refused.

    Measured by descriptor count rather than by reading the source: the
    ``except BaseException`` arm is the only thing closing it, and a rewrite that
    drops it leaves a file descriptor per refused read -- which a daemon meeting a
    planted artefact on every request exhausts.
    """
    pipe = tmp_path / "active.json"
    if not _CAN_MAKE_A_NAMED_PIPE:  # pragma: no cover - POSIX only
        pytest.skip("os.mkfifo is POSIX-only")
    os.mkfifo(pipe)

    def open_descriptors() -> int:
        probe = os.open(os.devnull, os.O_RDONLY)
        os.close(probe)
        return probe

    before = open_descriptors()
    for _ in range(20):
        with pytest.raises(IrregularArtefactError):
            read_text_from_a_regular_file(pipe)
    assert open_descriptors() <= before + 1, (
        "the refused reads leaked descriptors: the lowest free descriptor number moved, "
        "which is what a missing close on the refusal path looks like"
    )


def test_assert_a_regular_file_answers_the_descriptor_and_not_the_path(tmp_path: Path) -> None:
    """The two questions differ on a real input, so the choice of one is measurable.

    The name is re-pointed behind an already-open descriptor: the *path* now names
    a regular file and the *descriptor* is still the pipe. A guard keyed on the
    path would answer "regular file" here, which is exactly the swap an attacker
    with write access to the directory arranges.
    """
    if not _CAN_MAKE_A_NAMED_PIPE:  # pragma: no cover - POSIX only
        pytest.skip("os.mkfifo is POSIX-only")
    pipe = tmp_path / "active.json"
    os.mkfifo(pipe)
    descriptor = os.open(pipe, os.O_RDONLY | os.O_NONBLOCK)
    try:
        pipe.unlink()
        (tmp_path / "active.json").write_text("{}", encoding="utf-8")
        assert stat.S_ISREG((tmp_path / "active.json").stat().st_mode), (
            "the positive control failed: the path must now hold a regular file, or this "
            "test proves nothing about which question was asked"
        )
        with pytest.raises(IrregularArtefactError) as raised:
            assert_a_regular_file(descriptor, tmp_path / "active.json")
        assert raised.value.shape == "a named pipe (FIFO)"
    finally:
        os.close(descriptor)


#: The child that proves the bound is the *code* and not the harness: it reads a
#: named pipe with the plain ``Path.read_text`` this module replaced, so a run
#: that is not killed would mean the platform never blocked and every assertion
#: above is about nothing.
_BLOCKING_CONTROL: Final = (
    "import os, pathlib, sys\n"
    "path = pathlib.Path(sys.argv[1])\n"
    "os.mkfifo(path)\n"
    "print(path.read_text(encoding='utf-8'))\n"
)


@pytest.mark.skipif(not _CAN_MAKE_A_NAMED_PIPE, reason="os.mkfifo is POSIX-only")
def test_the_plain_read_this_replaces_really_does_block(tmp_path: Path) -> None:
    """The positive control on the whole module.

    Without it, every "is refused rather than waited on" assertion above would
    still pass on a platform where reading a named pipe returned immediately --
    green, and about nothing. Run in a child and killed, because the thing being
    demonstrated is a hang.
    """
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run(  # noqa: S603
            [sys.executable, "-c", _BLOCKING_CONTROL, str(tmp_path / "control")],
            capture_output=True,
            timeout=3.0,
            check=False,
        )
