"""Turn a hang into a test failure (issue #215).

A guard whose absence makes a call block *forever* cannot be pinned by an
ordinary test: deleting the guard would hang the suite rather than turn it RED,
so the guard would survive its own deletion -- exactly the failure the
"every guard gets a driving test" rule exists to prevent. A FIFO with no writer
is that shape: ``open()`` on it never returns.

``SIGALRM`` converts the hang into an exception after a bounded wait, so a suite
run without the guard fails in seconds and says what it was waiting for. With
the guard in place the timer is cancelled microseconds later and costs nothing.

POSIX only. ``signal.SIGALRM`` does not exist on Windows -- and neither does
``os.mkfifo``, so anything needing this needs that skip as well.
"""

from __future__ import annotations

import signal
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType

#: Whether this platform can interrupt a blocked syscall on a timer. Tests that
#: would otherwise hang skip on it rather than risk stalling the suite.
CAN_INTERRUPT_A_HANG = hasattr(signal, "SIGALRM")


@contextmanager
def fails_rather_than_hanging(seconds: float, *, waiting_for: str) -> Iterator[None]:
    """Raise :class:`TimeoutError` if the block does not finish within ``seconds``.

    The handler *raises* rather than setting a flag, which is what makes the
    interruption escape the syscall: since PEP 475 Python retries a syscall
    interrupted by a signal whose handler returns normally, so a flag-setting
    handler would leave ``open()`` blocked exactly as before.

    Both the timer and the previous handler are restored in ``finally``, so a
    test that fails inside the block does not leave a timer armed for whatever
    runs next.
    """

    def fire(signum: int, frame: FrameType | None) -> None:
        raise TimeoutError(f"{waiting_for} did not return within {seconds}s")

    previous = signal.signal(signal.SIGALRM, fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
