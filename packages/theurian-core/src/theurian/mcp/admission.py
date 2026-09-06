"""The concurrency gate the sync tools admit callers through (T-6, SEC-8, #26).

``threading.BoundedSemaphore`` stood here until #586. It counts permits and has
no opinion about how long one is held, which is right for every ordinary caller
and wrong for the one that never comes back: a thread parked inside an ``open``
holds its permit for the life of the process, so the gate's capacity drops by one
**permanently** and nothing short of a restart returns it.

That is not a hypothetical. PR #585 measured it against the state database --
four workers opening the path while a fifth swapped a regular file and a named
pipe at 4.7 swaps a second: one worker was inside the open when the run ended,
and was still there thirty seconds after the artefact had been removed and a
healthy database put back. #585's shape refusals close the *planted* case at
every opener; what they cannot close is the window between the check and the
open, because ``sqlite3.connect`` takes a path and no descriptor, so there is no
``fstat`` to move the question onto the way a lock opener can.

**So the bound is on the hold, not on the open, and that distinction is the whole
design.** The open cannot be bounded from the thread that issues it: SQLite
retries a call a signal interrupts (measured on #585's branch -- a ``SIGALRM`` at
3 s left the process inside ``__open`` at 150 s), and moving the connect to a
helper thread would mean handing every connection across threads with
``check_same_thread=False``, which trades a bounded stall for the loss of the
driver's own misuse check on every read in the package.

What this gate does instead is refuse to believe a permit is still in use forever.
A hold older than :data:`MAX_PERMIT_HOLD_SECONDS` is **reclaimed**, and the
parked thread's eventual ``release`` is a no-op rather than an error. The reach
of a parked open therefore drops from *permanent capacity loss* to *a stall of at
most that long*.

**What it costs, stated rather than implied.** A reclaimed permit can coexist
with the thread that took it, so in-flight work may briefly exceed
``MAX_CONCURRENT_SEARCHES``. Two things bound that cost. The reclaimed holder is
by construction one that has spent longer than any measured search -- a parked
open consumes no CPU and no GIL, which is the resource T-6's cap protects -- and
a *legitimately* slow search that crosses the bound over-admits by exactly one
permit, once. Against that, the state being repaired is a gate that has already
lost the permit for good.

This is deliberately **not** the per-query timeout ``MAX_CONCURRENT_SEARCHES``
records as not taken. Nothing here cancels a query or refuses a caller who has
been admitted: a sync tool's thread cannot be cancelled, and this gate does not
try. The permit is an accounting token, and the only thing reclaimed is the
token.
"""

from __future__ import annotations

import threading
import time
from itertools import count
from typing import Final, final

#: How long one permit may be held before the gate stops counting it.
#:
#: 30 seconds, and the number is chosen against two measurements rather than
#: tuned. The floor: a real ``knowledge.search`` against a 900-item visible
#: corpus has a solo median of 56.5 ms (`mcp/tools.py`'s own frame, 2026-08-31),
#: so this is roughly three orders of magnitude above what a holder needs, and a
#: hold that crosses it is not a search finishing. The ceiling: it is the *stall*
#: a parked open costs the gate, so a larger value buys nothing and a smaller one
#: starts reclaiming permits from work that is merely slow.
MAX_PERMIT_HOLD_SECONDS: Final = 30.0


@final
class AdmissionGate:
    """A fixed number of permits, none of which can be lost forever.

    The surface ``BoundedSemaphore`` offered, minus the one behaviour that made a
    parked holder permanent: :meth:`acquire` returns a token or ``None``, and
    :meth:`release` takes the token back. A token the gate has already reclaimed
    is released harmlessly, which is what lets a parked thread finish -- whenever
    that is -- without handing the gate a permit it does not own. ``BoundedSemaphore``
    cannot have that property: it counts, so it has nothing to tell a stale
    release from an honest one and raises ``ValueError`` on the over-release
    either way.

    Thread-safe by one ``Condition``. Every field below is read and written under
    it, including the reclamation sweep, so a reclaim cannot race an honest
    release into handing out the same slot twice.
    """

    def __init__(self, permits: int, *, max_hold_seconds: float = MAX_PERMIT_HOLD_SECONDS) -> None:
        if permits < 1:
            msg = f"an admission gate needs at least one permit, not {permits}"
            raise ValueError(msg)
        if max_hold_seconds <= 0:
            msg = f"a permit hold bound must be positive, not {max_hold_seconds}"
            raise ValueError(msg)
        self._permits = permits
        self._max_hold = max_hold_seconds
        self._condition = threading.Condition()
        #: Token to the monotonic instant its hold stops counting.
        self._held: dict[int, float] = {}
        #: Tokens are never reused, so a release cannot be mistaken for a later
        #: caller's. `count()` is not thread-safe on its own; every `next` here
        #: happens under the condition's lock.
        self._tokens = count()

    def acquire(self, timeout: float) -> int | None:
        """Take a permit, waiting up to ``timeout`` seconds. ``None`` if none frees.

        The wait is bounded by whichever comes first: the caller's ``timeout``, a
        holder releasing, or the oldest hold reaching the bound. That third one is
        why the sleep below is not simply the caller's remaining time -- nothing
        *notifies* when a hold expires, so a waiter that slept for the full
        remainder would miss a reclaim it was entitled to.
        """
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                self._reclaim_expired()
                if len(self._held) < self._permits:
                    token = next(self._tokens)
                    self._held[token] = time.monotonic() + self._max_hold
                    return token
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(min(remaining, self._until_the_next_expiry()))

    def release(self, token: int) -> None:
        """Give a permit back. Releasing a reclaimed token does nothing.

        The no-op arm is the point rather than a tolerance: the thread whose
        permit was reclaimed is still running, and when it finally returns it must
        not add a permit to a gate that has already re-issued its slot.
        """
        with self._condition:
            if self._held.pop(token, None) is not None:
                self._condition.notify()

    def _reclaim_expired(self) -> None:
        """Drop every hold past the bound. Caller holds the condition's lock."""
        now = time.monotonic()
        expired = [token for token, expires in self._held.items() if expires <= now]
        for token in expired:
            del self._held[token]

    def _until_the_next_expiry(self) -> float:
        """Seconds until the oldest hold can be reclaimed. Lock held.

        The gate is full whenever this is called -- :meth:`acquire` reaches it
        only after the capacity test failed -- so ``self._held`` is non-empty and
        ``min`` has something to answer about. Floored at zero, because a hold
        that expired between the sweep above and this call would otherwise ask
        for a negative wait.
        """
        return max(0.0, min(self._held.values()) - time.monotonic())


__all__ = ["MAX_PERMIT_HOLD_SECONDS", "AdmissionGate"]
