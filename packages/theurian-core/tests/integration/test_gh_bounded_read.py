"""The read shape behind the byte cap and the timeout (ADR-0030 clauses 7 and 10).

**The cap is half of clause 10; the read shape is the other half.** An
implementation that accumulates the whole response and measures it afterwards has
already paid for whatever the child produced, and it satisfies any test that only
asserts "a response past the cap is refused". So the discriminator here is
**time**: the child writes past the cap and then sleeps, and the assertion is
that the refusal arrives *inside a bounded wait*. An accumulating implementation
exceeds it; an incremental one refuses at the cap and returns.

The child is ``sys.executable``, and this file passes ``run_bounded`` an
environment of its own rather than the adapter's constructed one. That is
deliberate and it is the only place it happens: what is under test here is the
**read**, not the environment, and a Python interpreter is a child whose output
a test can shape byte by byte in a way ``/bin/sh`` cannot.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import time
from typing import Final

import pytest

from theurian.domain.review_ingest import RefusalGrade, ReviewIngestRefusedError
from theurian.infrastructure.github.gh_cli import run_bounded

pytestmark = pytest.mark.integration

#: How long the assertion allows for a refusal that must arrive "at the cap".
#: Generous by two orders of magnitude against the work involved, and an order of
#: magnitude below :data:`_CHILD_SLEEP_SECONDS`, so the two cannot be confused.
_BOUNDED_WAIT_SECONDS: Final = 5.0

#: How long the child stays alive after writing. Long enough that an
#: implementation waiting for the child to finish misses the wait above.
_CHILD_SLEEP_SECONDS: Final = 20.0

#: The environment the child runs under here. Not the adapter's constructed one
#: (see the module docstring): ``PATH`` is the running interpreter's so the
#: child starts at all, and nothing else is passed.
_ENV: Final[dict[str, str]] = {"PATH": os.environ.get("PATH", "")}

_OVERRUN_THEN_SLEEP = (
    "import sys, time\n"
    "sys.stdout.buffer.write(b'x' * {written})\n"
    "sys.stdout.buffer.flush()\n"
    "time.sleep({sleep})\n"
)

#: The same child, announcing its own pid first. Written before a byte of stdout,
#: so the file exists by the time the cap can possibly have been passed and the
#: assertion below never races the spawn.
_ANNOUNCE_PID_THEN_OVERRUN_THEN_SLEEP = (
    "import os, sys, time\n"
    "open({pidfile!r}, 'w').write(str(os.getpid()))\n"
    "sys.stdout.buffer.write(b'x' * {written})\n"
    "sys.stdout.buffer.flush()\n"
    "time.sleep({sleep})\n"
)


_SLEEP_WITHOUT_WRITING = "import time\ntime.sleep({sleep})\n"

#: A child that answers on stdout, exits, and leaves a **descendant** holding
#: fd 2. The grandchild's own stdout and stdin go to ``/dev/null``, so the stdout
#: pipe reaches EOF and the child is reaped normally: the only thing still open
#: is the stderr pipe, which is the one stream nothing used to bound.
_ANSWER_THEN_LEAVE_STDERR_HELD = (
    "import os, subprocess, sys\n"
    "devnull = os.open(os.devnull, os.O_WRONLY)\n"
    "subprocess.Popen(\n"
    "    [sys.executable, '-c', 'import time; time.sleep({sleep})'],\n"
    "    stdout=devnull, stdin=devnull,\n"
    ")\n"
    "sys.stdout.write('done')\n"
    "sys.stdout.flush()\n"
)


def _is_alive(pid: int) -> bool:
    """Whether ``pid`` still names a process, asked once rather than waited on.

    Signal 0 performs the permission and existence checks and delivers nothing,
    which is the whole question here. It is asked *after* ``run_bounded`` has
    returned, and ``run_bounded`` reaps the child it kills before returning -- so
    there is nothing to wait for and a poll would only hide a slow cleanup.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.asyncio
async def test_a_child_that_overruns_the_cap_is_refused_without_waiting_for_it_to_finish() -> None:
    """Clause 10, spelled so a buffering implementation reddens rather than hangs.

    The child writes four times the cap -- which fits a pipe buffer, so it does
    not block -- and then sleeps for twenty seconds. Reading incrementally, the
    cap is passed inside the first chunk and the refusal is immediate. Reading to
    EOF first, the refusal cannot arrive before the child exits, and the elapsed
    assertion is what says which of the two happened.
    """
    cap = 1024
    started = time.monotonic()

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await run_bounded(
            [
                sys.executable,
                "-c",
                _OVERRUN_THEN_SLEEP.format(written=cap * 4, sleep=_CHILD_SLEEP_SECONDS),
            ],
            env=_ENV,
            timeout=_CHILD_SLEEP_SECONDS * 2,
            byte_cap=cap,
        )
    elapsed = time.monotonic() - started

    assert raised.value.grade is RefusalGrade.LIMIT_EXCEEDED
    assert str(cap) in str(raised.value)
    assert elapsed < _BOUNDED_WAIT_SECONDS, (
        f"the refusal took {elapsed:.1f}s, past the {_BOUNDED_WAIT_SECONDS}s bound. "
        f"The child wrote past the cap and then slept, so an implementation that "
        f"accumulates the whole response before measuring it cannot answer inside "
        f"this window -- which is the difference clause 10 is about, and the "
        f"reason this assertion is on time and not only on the grade."
    )


@pytest.mark.asyncio
async def test_a_cap_refusal_leaves_no_child_behind(tmp_path: pathlib.Path) -> None:
    """A refusal that abandons the child hands the caller a bound and keeps the cost.

    The byte cap exists so a child cannot make this process spend what it likes.
    A refusal that returns while the child is still running has moved the cost
    rather than removed it: the caller sees a graded envelope, and the machine
    carries a process nobody is reading any more -- twenty seconds here, and
    unbounded from a real ``gh``. ``run_bounded``'s docstring says every refusal
    kills the child first, and until now the only thing holding that sentence was
    the sentence.

    The child announces its pid, overruns the cap and sleeps. After the refusal
    the pid is asked about **once**: ``run_bounded`` reaps what it kills before
    it returns, so a live process here is a live process, not a slow one.
    """
    cap = 1024
    pidfile = tmp_path / "child.pid"

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await run_bounded(
            [
                sys.executable,
                "-c",
                _ANNOUNCE_PID_THEN_OVERRUN_THEN_SLEEP.format(
                    pidfile=str(pidfile), written=cap * 4, sleep=_CHILD_SLEEP_SECONDS
                ),
            ],
            env=_ENV,
            timeout=_CHILD_SLEEP_SECONDS * 2,
            byte_cap=cap,
        )
    child = int(pidfile.read_text(encoding="utf-8"))

    assert raised.value.grade is RefusalGrade.LIMIT_EXCEEDED
    assert not _is_alive(child), (
        f"the cap refused the answer and process {child} is still running. It has "
        f"{_CHILD_SLEEP_SECONDS:g} seconds of sleeping left here and no bound at all "
        f"from a real `gh`: the refusal moved the cost off the caller rather than "
        f"stopping it."
    )


@pytest.mark.asyncio
async def test_a_child_at_the_cap_exactly_is_read_rather_than_refused() -> None:
    """The boundary, so the check above is not off by one in the refusing direction."""
    cap = 1024
    outcome = await run_bounded(
        [sys.executable, "-c", f"import sys; sys.stdout.buffer.write(b'x' * {cap})"],
        env=_ENV,
        timeout=_BOUNDED_WAIT_SECONDS * 2,
        byte_cap=cap,
    )

    assert outcome.returncode == 0
    assert len(outcome.stdout) == cap


@pytest.mark.asyncio
async def test_a_child_that_never_answers_is_stopped_at_the_recorded_timeout() -> None:
    """SEC-19, and the child is killed rather than left behind."""
    started = time.monotonic()

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await run_bounded(
            [sys.executable, "-c", _SLEEP_WITHOUT_WRITING.format(sleep=_CHILD_SLEEP_SECONDS)],
            env=_ENV,
            timeout=1.0,
        )
    elapsed = time.monotonic() - started

    assert raised.value.grade is RefusalGrade.TOOL_FAILED
    assert "timeout" in str(raised.value)
    assert elapsed < _BOUNDED_WAIT_SECONDS


@pytest.mark.asyncio
async def test_a_descendant_that_holds_stderr_open_is_refused_at_the_deadline() -> None:
    """The deadline covers the stderr drain, not only the reads and the exit.

    The child writes its answer, exits, and leaves a grandchild holding fd 2. So
    stdout reaches EOF and ``child.wait()`` returns at once: every wait the
    deadline used to cover is already satisfied, and the only thing still
    outstanding is the drain. Awaited unbounded, this call returns *twenty
    seconds later with a success*; bounded, it refuses at the deadline.

    The outer ``wait_for`` is what makes the RED reading a failure rather than a
    hang -- an implementation that does not bound the drain fails here at
    ``_BOUNDED_WAIT_SECONDS`` with a ``TimeoutError``, which is not the
    ``ReviewIngestRefusedError`` this expects.
    """
    started = time.monotonic()

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await asyncio.wait_for(
            run_bounded(
                [
                    sys.executable,
                    "-c",
                    _ANSWER_THEN_LEAVE_STDERR_HELD.format(sleep=_CHILD_SLEEP_SECONDS),
                ],
                env=_ENV,
                timeout=1.0,
            ),
            timeout=_BOUNDED_WAIT_SECONDS,
        )
    elapsed = time.monotonic() - started

    assert raised.value.grade is RefusalGrade.TOOL_FAILED
    assert "timeout" in str(raised.value)
    assert elapsed < _BOUNDED_WAIT_SECONDS


@pytest.mark.asyncio
async def test_a_binary_that_cannot_be_started_is_a_graded_refusal() -> None:
    """An ``OSError`` from the spawn is an envelope with a remedy, not a traceback."""
    with pytest.raises(ReviewIngestRefusedError) as raised:
        await run_bounded(["/nonexistent/gh"], env=_ENV, timeout=1.0)

    assert raised.value.grade is RefusalGrade.TOOL_FAILED
    assert raised.value.remedy


@pytest.mark.asyncio
async def test_a_spawn_failure_with_no_strerror_does_not_publish_the_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``str()`` of an ``OSError`` appends its ``filename``; the envelope may not.

    Every ``OSError`` the operating system raises for a missing or unexecutable
    binary carries a ``strerror``, so the fallback is unreachable through a real
    spawn and this drives it through the seam instead. An ``OSError`` built with
    one argument has ``strerror is None``, which is the branch that used to
    interpolate the exception itself -- and the exception's text is the absolute
    path of the operator's ``gh``, inside their home directory.
    """
    absolute = "/Users/someone/Library/Application Support/tools/gh"

    async def refuse(*args: object, **keywords: object) -> None:
        raise OSError(f"cannot execute {absolute}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", refuse)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await run_bounded([absolute], env=_ENV, timeout=1.0)

    assert raised.value.grade is RefusalGrade.TOOL_FAILED
    assert absolute not in str(raised.value)
    assert absolute not in raised.value.envelope.detail
    assert raised.value.remedy


@pytest.mark.asyncio
async def test_a_childs_stderr_is_kept_bounded_while_still_being_drained() -> None:
    """A full stderr pipe would block the child, so the drain runs to EOF and keeps a prefix.

    Both halves matter: stopping the read at the cap would leave a chatty child
    blocked on a write and never reaching the exit ``run_bounded`` waits for, and
    keeping all of it would put an unbounded third-party string in a published
    envelope.
    """
    noisy = (
        "import sys\nsys.stderr.write('E' * 200000)\nsys.stderr.flush()\nsys.stdout.write('done')\n"
    )
    outcome = await asyncio.wait_for(
        run_bounded([sys.executable, "-c", noisy], env=_ENV, timeout=_BOUNDED_WAIT_SECONDS * 2),
        timeout=_CHILD_SLEEP_SECONDS,
    )

    assert outcome.stdout == b"done"
    assert outcome.stderr.startswith("E")
    assert len(outcome.stderr) < 200000
