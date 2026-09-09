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
parked thread's eventual ``release`` is a no-op rather than an error.

**Reclaiming without a second bound is worse than the disease, and the first cut
of this module shipped exactly that** (round two, H-1). A semaphore is
*self-limiting*: a permit that is never released is never re-issued, so four
parked threads is all four parked threads can ever be. Reclaiming deletes that
property, and a reclaim with no ceiling replaces a bounded leak with an unbounded
one -- measured at the shipped constants, four parked holders per
30-second window, accumulating: at t=323 s all **40** tokens of anyio's worker
pool were parked and the daemon stopped serving *every* synchronous MCP tool,
``system.capabilities`` included, which takes no permit from either gate. The
prose that shipped with it said "over-admits by exactly one permit, once", which
was true of one holder and false of the sequence.

So the ceiling is the second half of the design, not a refinement of it:
**a reclaimed-but-unreleased token stays counted in :attr:`AdmissionGate._reclaimed`,
and no further hold is reclaimed while that set is full.** The two sets partition
the outstanding threads -- a thread is in exactly one of them until it releases --
so the worst case is

    ``len(_held) + len(_reclaimed) <= 2 * permits``

**per gate**, which at the shipped ``MAX_CONCURRENT_SEARCHES`` is **8 threads**
each -- the 2x figure T-6 records. Across the gates ``mcp/tools.py::register``
builds it multiplies: three of them since ``review.search`` landed, so 12 threads
of concurrent occupancy and 24 counting parked holders, against the 40-token
anyio worker pool that bounds them all. The per-gate bound is this class's; the
aggregate is a property of how many gates the tool surface registers, and
``register``'s own comments carry that arithmetic beside the gates themselves.

**What it costs, stated rather than implied, and it is not a stall in every
case.** Two regimes, and the honest sentence names both:

* while fewer than ``permits`` reclaimed tokens are outstanding, a parked open
  costs a **bounded stall**: the permit returns after
  :data:`MAX_PERMIT_HOLD_SECONDS` and the gate keeps serving;
* once ``permits`` of them are outstanding -- ``permits`` threads parked in an
  open that never returns -- the ceiling stops reclaiming, and the gate **wedges
  for the duration of the residual that parked them**, exactly as a semaphore
  would have. That is the trade taken deliberately: a wedged gate refuses
  callers with a constant message, while an unbounded reclaim drains the
  process-wide worker pool and takes every other tool down with it.

In-flight work may therefore exceed ``MAX_CONCURRENT_SEARCHES`` by up to
``permits``, and no further. A reclaimed holder is by construction one that has
spent longer than any measured search; a parked open consumes no CPU and no GIL,
which is the resource T-6's cap protects.

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
#: so this is **531x** that median -- the ratio, rather than the "roughly three
#: orders of magnitude" this note claimed until round two, which would have been
#: 56.5 s and is not the number below. A hold that crosses it is not a search
#: finishing. The ceiling: it is the *stall* a parked open costs the gate while
#: the reclaim ceiling has room, so a larger value buys nothing and a smaller one
#: starts reclaiming permits from work that is merely slow.
#: ``test_admission_gate.py::test_the_shipped_hold_bound_is_far_above_a_measured_search``
#: asserts the ratio, not a round number a rewording could drift past.
MAX_PERMIT_HOLD_SECONDS: Final = 30.0


@final
class AdmissionGate:
    """A fixed number of permits, none lost forever and none conjured either.

    The surface ``BoundedSemaphore`` offered, minus the one behaviour that made a
    parked holder permanent and plus the ceiling that keeps removing it safe:
    :meth:`acquire` returns a token or ``None``, and :meth:`release` takes the
    token back. A token the gate has already reclaimed is released harmlessly,
    which is what lets a parked thread finish -- whenever that is -- without
    handing the gate a permit it does not own. ``BoundedSemaphore`` cannot have
    that property: it counts, so it has nothing to tell a stale release from an
    honest one and raises ``ValueError`` on the over-release either way.

    **Two sets, and they partition the outstanding threads.** A thread that has
    acquired is in :attr:`_held` until it releases or its hold is reclaimed, and
    in :attr:`_reclaimed` from a reclaim until it releases -- never in both,
    never in neither. Each is capped at ``permits``, so
    ``len(_held) + len(_reclaimed) <= 2 * permits`` is the whole aggregate bound,
    and it is what the module docstring's 8-thread worst case is computed from.
    ``test_admission_gate.py::test_parked_holders_plateau_at_twice_the_permits``
    is what makes it a measured claim rather than an argument.

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
        #: Tokens reclaimed from a hold that outlived the bound, whose threads
        #: have not returned. Capped at ``permits``: this set *is* the aggregate
        #: bound, and dropping a token from it early would re-open the
        #: accumulation round two measured (H-1).
        self._reclaimed: set[int] = set()
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

        ``None`` is returned for two different states, and the caller cannot tell
        them apart because it does not need to: the gate is genuinely busy, or the
        reclaim ceiling is full and the gate is wedged behind ``permits`` threads
        that never returned. Both are "no capacity, retry" to a caller, and the
        refusal message is a constant either way (SEC-13).
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
        """Give a permit back, or retire a reclaimed one. Neither raises.

        Two arms, and the second is what the ceiling needs. An honest release
        frees a **permit**. A release from a thread whose hold was reclaimed
        frees a **reclaim slot** instead -- it must not add a permit to a gate
        that has already re-issued its own, but it does mean one fewer
        outstanding thread, so the gate may reclaim again. Both notify, because a
        waiter can be admitted by either.

        A token this gate never issued is ignored, which is the same answer it
        gives a token it has already retired.
        """
        with self._condition:
            if self._held.pop(token, None) is not None:
                self._condition.notify()
            elif token in self._reclaimed:
                self._reclaimed.discard(token)
                self._condition.notify()

    @property
    def outstanding(self) -> int:
        """Threads that took a permit and have not released it.

        The quantity the aggregate bound is about: holds plus reclaimed-but-not-
        returned tokens, which partition those threads. Never above
        ``2 * permits``. Published so a test can assert the invariant against the
        gate rather than inferring it from thread counts, and so an operator
        reading a stalled daemon has one number to look at.
        """
        with self._condition:
            return len(self._held) + len(self._reclaimed)

    def _reclaim_expired(self) -> None:
        """Reclaim holds past the bound, up to the ceiling. Lock held.

        **The ceiling is checked per token, before each reclaim**, so the sweep
        stops the moment ``_reclaimed`` is full rather than draining every expired
        hold and capping afterwards -- the difference is whether the invariant
        ever transiently exceeds ``2 * permits``, and a reader checking it under
        the lock must never see it do so.

        Oldest first, which ``dict`` gives for free: holds are inserted in
        acquisition order, so iteration order is age order and which token gets
        the last reclaim slot is deterministic rather than a function of hashing.
        """
        now = time.monotonic()
        for token, expires in list(self._held.items()):
            if len(self._reclaimed) >= self._permits:
                return
            if expires <= now:
                del self._held[token]
                self._reclaimed.add(token)

    def _until_the_next_expiry(self) -> float:
        """Seconds until the oldest hold can be reclaimed. Lock held.

        ``inf`` when nothing the clock does would help: with the reclaim ceiling
        full, every hold in ``_held`` can expire without a single one being
        reclaimable, and a waiter that woke on those expiries would spin through
        its whole timeout re-taking the lock for an answer that cannot change.
        The caller takes ``min`` with its own remaining time, so ``inf`` reads as
        "sleep until your timeout or until somebody releases".

        The gate is full whenever this is called -- :meth:`acquire` reaches it
        only after the capacity test failed -- so ``self._held`` is non-empty and
        ``min`` has something to answer about. Floored at zero, because a hold
        that expired between the sweep above and this call would otherwise ask
        for a negative wait.
        """
        if len(self._reclaimed) >= self._permits:
            return float("inf")
        return max(0.0, min(self._held.values()) - time.monotonic())


__all__ = ["MAX_PERMIT_HOLD_SECONDS", "AdmissionGate"]
