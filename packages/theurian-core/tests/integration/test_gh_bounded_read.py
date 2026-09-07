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
import contextlib
import os
import pathlib
import signal
import sys
import time
from typing import Final

import pytest

from theurian.domain.review_ingest import (
    MAX_REFUSAL_DETAIL_CHARS,
    RefusalGrade,
    ReviewIngestRefusedError,
)
from theurian.infrastructure.github import gh_cli, limits
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

#: How much of a child's stderr the drain keeps in memory, and how much of it an
#: envelope may publish -- both **written out here and never imported**.
#:
#: The pair is what makes the memory bound observable at all, so a test that read
#: either constant would move with it and pass whatever the relationship became.
RECORDED_STDERR_BYTES: Final = 4_096
RECORDED_DETAIL_CHARS: Final = 2_000

#: One four-byte UTF-8 character, U+1F600. The two bounds above are counted in
#: different units -- bytes and characters -- and over ASCII the character bound
#: is always the tighter of the two, which is what hides a drain that keeps
#: everything. At four bytes each the byte cap decides first.
_WIDE_CHARACTER: Final = b"\xf0\x9f\x98\x80"

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

#: A child whose stderr is ``count`` copies of a multi-byte character, written as
#: raw bytes so the count on the wire is exact whatever the child's own encoding
#: settings are.
_WIDE_STDERR = (
    "import sys\n"
    "sys.stderr.buffer.write({unit!r} * {count})\n"
    "sys.stderr.buffer.flush()\n"
    "sys.stdout.write('done')\n"
)

#: A child that answers on stdout, exits, and leaves a **descendant** holding
#: fd 2. The grandchild's own stdout and stdin go to ``/dev/null``, so the stdout
#: pipe reaches EOF and the child is reaped normally: the only thing still open
#: is the stderr pipe, which is the one stream nothing used to bound.
#:
#: The grandchild writes its pid down before the child answers, because **nobody
#: else can end it**. It is not this process's child, so the loop knows nothing
#: about it, and ``run_bounded`` deliberately walks no process tree -- so a test
#: that spawns one and does not reap it leaves it sleeping for
#: :data:`_CHILD_SLEEP_SECONDS` after the run has finished, once per run.
_ANSWER_THEN_LEAVE_STDERR_HELD = (
    "import os, subprocess, sys\n"
    "devnull = os.open(os.devnull, os.O_WRONLY)\n"
    "held = subprocess.Popen(\n"
    "    [sys.executable, '-c', 'import time; time.sleep({sleep})'],\n"
    "    stdout=devnull, stdin=devnull,\n"
    ")\n"
    "open({pidfile!r}, 'w').write(str(held.pid))\n"
    "sys.stdout.write('done')\n"
    "sys.stdout.flush()\n"
)

#: A reap short enough to assert on. :data:`~theurian.infrastructure.github.gh_cli._REAP_SECONDS`
#: is 5 seconds, and the tests below assert that a cancelled call waits for the
#: whole of it -- at the shipped value that is five seconds of suite time to
#: learn something a fraction of a second demonstrates just as well.
_PATCHED_REAP_SECONDS: Final = 0.5


def _reap_descendant(pidfile: pathlib.Path) -> None:
    """Kill the grandchild a child left holding the stderr pipe.

    Called from a ``finally`` rather than after the assertions, so a failing
    assertion does not also leak a process. Silent when the file was never
    written or the process is already gone: both mean there is nothing to end.
    """
    if not pidfile.exists():
        return
    with contextlib.suppress(ProcessLookupError, ValueError):
        os.kill(int(pidfile.read_text(encoding="utf-8")), signal.SIGKILL)


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
async def test_a_descendant_that_holds_stderr_open_is_refused_at_the_deadline(
    tmp_path: pathlib.Path,
) -> None:
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
    pidfile = tmp_path / "grandchild.pid"
    started = time.monotonic()

    try:
        with pytest.raises(ReviewIngestRefusedError) as raised:
            await asyncio.wait_for(
                run_bounded(
                    [
                        sys.executable,
                        "-c",
                        _ANSWER_THEN_LEAVE_STDERR_HELD.format(
                            sleep=_CHILD_SLEEP_SECONDS, pidfile=str(pidfile)
                        ),
                    ],
                    env=_ENV,
                    timeout=1.0,
                ),
                timeout=_BOUNDED_WAIT_SECONDS,
            )
        elapsed = time.monotonic() - started
    finally:
        _reap_descendant(pidfile)

    assert raised.value.grade is RefusalGrade.TOOL_FAILED
    assert "timeout" in str(raised.value)
    assert elapsed < _BOUNDED_WAIT_SECONDS


@pytest.mark.asyncio
async def test_a_cancelled_call_waits_for_the_reap_it_is_bounded_by(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled call unwinds through ``_end``, and ``_end``'s awaits complete.

    Both halves of that sentence are the assertion, and both were stated
    wrongly. ``_end``'s docstring said an ``await`` inside a ``finally`` entered
    by cancellation raises immediately -- which would make the reap, the drain
    join and ``_release`` on the last line all unreachable on this path, and
    ``_release`` is the fix that stops a held file descriptor per abandoned
    child. The elapsed floor below is what says otherwise: if those awaits raised
    at once the cancellation would return in milliseconds, and it does not.

    **The child is the shape that reaches the ceiling.** It leaves a descendant
    holding fd 2, so ``Process.wait()`` -- which waits for the exit *and* the
    pipes -- cannot return, and the reap runs its full length. That is also what
    the caller pays: cancelling this call is not free, it costs
    ``_REAP_SECONDS``, and the number is recorded rather than incidental. The
    constant is patched down here because five seconds of suite time demonstrates
    nothing half a second does not.

    The trade is deliberate and it replaced a worse one: the same cancellation
    used to return at once and leave a live child and a pending drain task
    behind.
    """
    monkeypatch.setattr(gh_cli, "_REAP_SECONDS", _PATCHED_REAP_SECONDS)
    pidfile = tmp_path / "grandchild.pid"
    call = asyncio.ensure_future(
        run_bounded(
            [
                sys.executable,
                "-c",
                _ANSWER_THEN_LEAVE_STDERR_HELD.format(
                    sleep=_CHILD_SLEEP_SECONDS, pidfile=str(pidfile)
                ),
            ],
            env=_ENV,
            timeout=_CHILD_SLEEP_SECONDS,
        )
    )

    try:
        # Wait for the grandchild to exist rather than sleeping a guessed amount:
        # what this measures is the unwind, and a cancel that arrives before the
        # child has spawned would measure the spawn instead.
        deadline = time.monotonic() + _BOUNDED_WAIT_SECONDS
        while not pidfile.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        started = time.monotonic()
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        elapsed = time.monotonic() - started
    finally:
        _reap_descendant(pidfile)

    assert elapsed >= _PATCHED_REAP_SECONDS, (
        f"the cancellation returned after {elapsed:.3f}s and the reap it should have "
        f"waited out is {_PATCHED_REAP_SECONDS}s. An `await` in a `finally` entered by "
        f"cancellation completes -- it does not raise at once -- and `_end` depends on "
        f"that for its own last line."
    )
    assert elapsed < _BOUNDED_WAIT_SECONDS, (
        f"the cancellation took {elapsed:.1f}s, so the wait is not bounded by "
        f"`_REAP_SECONDS` at all"
    )


@pytest.mark.asyncio
async def test_a_binary_that_cannot_be_started_is_a_graded_refusal() -> None:
    """An ``OSError`` from the spawn is an envelope with a remedy, not a traceback."""
    with pytest.raises(ReviewIngestRefusedError) as raised:
        await run_bounded(["/nonexistent/gh"], env=_ENV, timeout=1.0)

    assert raised.value.grade is RefusalGrade.TOOL_FAILED
    assert raised.value.remedy


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "argument",
    ("pass\x00", "pass\ud800"),
    ids=("an embedded NUL", "an unpaired surrogate"),
)
async def test_an_argument_that_cannot_be_spawned_is_a_graded_refusal(argument: str) -> None:
    """The spawn seam declines on **types**, so every argv element is covered at once.

    ``create_subprocess_exec`` refuses an argument for two reasons and neither is
    an ``OSError``: a NUL raises a bare ``ValueError``, and an unpaired surrogate
    raises ``UnicodeEncodeError`` -- a ``ValueError`` -- while the vector is
    encoded for ``execve``. A catch on ``OSError`` alone covers neither, which is
    how a response-supplied pagination cursor left the adapter as a traceback.

    Both arguments here are real: no seam is patched, and the exception comes out
    of the spawn itself. The high surrogate is the one that raises -- ``\\udc80``
    and its neighbours are what ``surrogateescape`` maps back to raw bytes, so
    they encode silently and are refused a layer up, where the cursor is read.
    """
    with pytest.raises(ReviewIngestRefusedError) as raised:
        await run_bounded([sys.executable, "-c", argument], env=_ENV, timeout=1.0)

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


def test_the_two_stderr_bounds_this_file_drives_are_the_ones_the_code_enforces() -> None:
    """The restated numbers and the enforced ones are two things, so they are compared.

    Both are needed by the test below, and it is their *relationship* that makes
    it able to fail at all: the drain's byte cap has to be small enough that the
    envelope's character bound does not reach first. Restating them here without
    checking them would drive a boundary neither constant holds any more.
    """
    assert limits.MAX_CHILD_STDERR_BYTES == RECORDED_STDERR_BYTES
    assert MAX_REFUSAL_DETAIL_CHARS == RECORDED_DETAIL_CHARS


@pytest.mark.asyncio
async def test_the_stderr_the_drain_keeps_is_bounded_in_bytes_not_only_when_published() -> None:
    """The drain's cap is a **memory** bound, and the envelope's slice hides it in ASCII.

    ``_drain_capped`` keeps a prefix of at most
    :data:`~theurian.infrastructure.github.limits.MAX_CHILD_STDERR_BYTES` while
    reading the rest to EOF; what it returns is then sliced again to
    :data:`~theurian.domain.review_ingest.MAX_REFUSAL_DETAIL_CHARS`. Over ASCII
    the second slice is the tighter of the two, so a drain that kept **every**
    byte a child produced returns exactly the same string -- the process holds a
    megabyte where it should hold four kilobytes, and no published value moves.
    That is why the surviving mutation survived: the observable was masked, not
    absent.

    A four-byte character unmasks it, because the two bounds are counted in
    different units. 4,096 bytes is 1,024 of these characters, which is under the
    2,000-character slice -- so the byte cap is the bound that decides, and the
    length of the published detail is what it decided. Keep every byte instead
    and the same child produces the full 2,000 characters.

    This is the **memory** half; ``test_a_childs_stderr_is_kept_bounded_while_still_being_drained``
    above is the *drain-to-EOF* half, where the child writes past a pipe buffer
    and would block if the read stopped at the cap.
    """
    kept_characters = RECORDED_STDERR_BYTES // len(_WIDE_CHARACTER)
    assert len(_WIDE_CHARACTER) == 4, "the fixture is not a four-byte character"
    assert kept_characters < RECORDED_DETAIL_CHARS, (
        f"{RECORDED_STDERR_BYTES} bytes of this character is {kept_characters} "
        f"characters, which the {RECORDED_DETAIL_CHARS}-character envelope slice "
        f"would cut first -- and then a capped drain and an uncapped one return "
        f"the same string again. Widen the character or re-take the bounds."
    )

    outcome = await asyncio.wait_for(
        run_bounded(
            [
                sys.executable,
                "-c",
                _WIDE_STDERR.format(unit=_WIDE_CHARACTER, count=RECORDED_DETAIL_CHARS * 2),
            ],
            env=_ENV,
            timeout=_BOUNDED_WAIT_SECONDS * 2,
        ),
        timeout=_CHILD_SLEEP_SECONDS,
    )

    assert outcome.stdout == b"done"
    assert outcome.stderr == _WIDE_CHARACTER.decode("utf-8") * kept_characters, (
        f"the drain kept {len(outcome.stderr)} characters of the child's stderr and "
        f"the byte cap allows {kept_characters}. A drain that keeps everything and "
        f"lets the envelope's {RECORDED_DETAIL_CHARS}-character slice do the cutting "
        f"publishes an identical string over ASCII while holding whatever the child "
        f"produced -- the cap is a memory bound, and this is where it is observable."
    )
