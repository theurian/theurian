"""The gate returns a permit whose holder never comes back (#586).

``threading.BoundedSemaphore`` stood in ``mcp/tools.py`` until this module's
subject replaced it. It counts, so a thread parked inside an ``open`` -- the
residual PR #585 recorded and could not close, because ``sqlite3.connect`` takes
a path and no descriptor -- held its permit for the life of the process and the
gate's capacity dropped by one permanently.

Every wait here is bounded and every clock reading is the gate's own
``time.monotonic``: the reclaim tests use a hold bound of a fraction of a second
rather than :data:`~theurian.mcp.admission.MAX_PERMIT_HOLD_SECONDS`, so the
property is driven at speed and the shipped constant is pinned separately, in
``tests/integration/test_search_concurrency_cap.py``.
"""

from __future__ import annotations

import threading
import time
from typing import Final

import pytest

from theurian.mcp.admission import MAX_PERMIT_HOLD_SECONDS, AdmissionGate

pytestmark = pytest.mark.unit

#: The wait for an acquisition whose answer is already decided -- the gate is
#: either empty or full. Non-zero so a scheduling hiccup does not read as a
#: refusal, and short enough that a handful cost nothing.
_A_MOMENT: Final = 0.2

#: The hold bound the reclaim tests run at. Long enough that an ordinary
#: acquire/release pair inside one cannot expire by accident, short enough that
#: waiting it out twice is imperceptible.
_A_SHORT_HOLD: Final = 0.3

#: The ceiling on any wait a *thread* in this file makes, so a lost wake-up
#: fails the test instead of hanging the suite.
_WAIT_BOUND: Final = 5.0


def test_a_gate_admits_up_to_its_permits_and_refuses_the_next() -> None:
    """The counting behaviour the semaphore had, unchanged."""
    gate = AdmissionGate(2)
    first = gate.acquire(_A_MOMENT)
    second = gate.acquire(_A_MOMENT)
    assert first is not None
    assert second is not None
    assert gate.acquire(_A_MOMENT) is None
    gate.release(first)
    assert gate.acquire(_A_MOMENT) is not None


def test_a_refused_acquisition_waits_for_its_timeout_and_no_longer() -> None:
    """The refusal is on a clock, so a saturated gate answers rather than parks."""
    gate = AdmissionGate(1)
    held = gate.acquire(_A_MOMENT)
    assert held is not None
    started = time.monotonic()
    assert gate.acquire(_A_MOMENT) is None
    elapsed = time.monotonic() - started
    # The upper bound discriminates "honoured the caller's timeout" from "waited a
    # hold bound", which is two orders of magnitude away; it is deliberately not
    # tight. A tight one measures the runner's load, and CI's macOS box ran this
    # suite in 894 s.
    assert _A_MOMENT <= elapsed < _A_MOMENT * 25, (
        f"the refusal took {elapsed:.3f}s against a {_A_MOMENT}s timeout, so the wait is "
        f"not the caller's"
    )


def test_a_waiting_caller_is_admitted_as_soon_as_a_holder_releases() -> None:
    """A release notifies, so the wait is not a poll on the timeout.

    Without the notification a freed permit would sit idle until the waiter's own
    timeout elapsed -- correct, and an admission wave slower than it needs to be.
    """
    gate = AdmissionGate(1)
    held = gate.acquire(_A_MOMENT)
    assert held is not None
    admitted: list[int | None] = []

    def wait_for_it() -> None:
        admitted.append(gate.acquire(_WAIT_BOUND))

    waiter = threading.Thread(target=wait_for_it)
    waiter.start()
    time.sleep(_A_MOMENT / 4)
    started = time.monotonic()
    gate.release(held)
    waiter.join(_WAIT_BOUND)
    assert not waiter.is_alive(), "the waiter never returned after a permit was released"
    assert admitted and admitted[0] is not None, (
        f"the waiter was refused rather than admitted after the release: {admitted}"
    )
    assert time.monotonic() - started < _WAIT_BOUND / 2, (
        "the waiter was admitted only when its own timeout elapsed, so the release did not wake it"
    )


def test_a_permit_whose_holder_never_returns_is_reclaimed() -> None:
    """The whole reason this class exists.

    A parked holder is simulated by acquiring and simply never releasing, which
    is exactly what a thread inside an unbounded ``open`` does to the gate.
    Before the reclaim, the gate's capacity was gone until the process ended.
    """
    gate = AdmissionGate(1, max_hold_seconds=_A_SHORT_HOLD)
    parked = gate.acquire(_A_MOMENT)
    assert parked is not None
    assert gate.acquire(_A_MOMENT) is None, "the gate admitted a second caller against one permit"

    reclaimed = gate.acquire(_A_SHORT_HOLD * 4)
    assert reclaimed is not None, (
        "the parked permit was never reclaimed, so a thread inside an open still costs this "
        "gate a permit permanently"
    )


def test_the_reclaimed_holder_cannot_hand_the_gate_a_permit_it_lost() -> None:
    """The stale release, which is the half a counting semaphore cannot have.

    The parked thread does eventually return -- when the artefact is removed, or
    when whatever it was waiting for arrives -- and its ``release`` then refers to
    a permit the gate has already re-issued. A ``BoundedSemaphore`` would raise
    ``ValueError``; a plain ``Semaphore`` would silently raise the cap. This does
    neither: the stale release frees the **reclaim slot** its token occupies, and
    no permit.

    **Asserted through :attr:`AdmissionGate.outstanding`, and the first version of
    this test asserted it through another ``acquire``, which raced.** That
    acquisition waits, and while it waits ``reissued``'s own hold can cross
    ``max_hold_seconds`` and be reclaimed -- correctly, since the stale release
    has just freed the slot to reclaim into. CI caught it on macOS, where the
    suite took 894 s and that hold expired inside a 0.2 s window; the gate had
    done the right thing and the assertion said it had not. ``outstanding`` reads
    the two sets under the lock and reclaims nothing, so it answers about the
    state the release left rather than about a state a later expiry may have
    moved on from.
    """
    gate = AdmissionGate(1, max_hold_seconds=_A_SHORT_HOLD)
    parked = gate.acquire(_A_MOMENT)
    assert parked is not None
    reissued = gate.acquire(_A_SHORT_HOLD * 4)
    assert reissued is not None
    assert gate.outstanding == 2, (
        "the reclaim did not leave two outstanding threads -- one holding, one reclaimed "
        "and not yet returned -- so the state this test is about was never reached"
    )

    gate.release(parked)  # the parked thread, waking up late

    assert gate.outstanding == 1, (
        "the stale release did not retire its own reclaimed token, so a thread that has "
        "returned still counts against the ceiling"
    )


def test_a_waiter_is_woken_by_a_reclaim_and_not_only_by_a_release() -> None:
    """The wait's sleep is capped by the next expiry, not by the caller's timeout.

    Nothing notifies when a hold expires, so a waiter that slept for its whole
    remaining timeout would be refused while a reclaimable permit sat there. The
    timeout here is many hold bounds long: an admission that arrives near the
    hold bound proves the sleep was the shorter of the two.
    """
    gate = AdmissionGate(1, max_hold_seconds=_A_SHORT_HOLD)
    parked = gate.acquire(_A_MOMENT)
    assert parked is not None

    started = time.monotonic()
    admitted = gate.acquire(_A_SHORT_HOLD * 20)
    elapsed = time.monotonic() - started

    assert admitted is not None, "the waiter was refused while a reclaimable permit existed"
    # Half the caller's own timeout, which is what separates "woke on the expiry"
    # from "slept the whole timeout and then found it". Anything tighter is a
    # measurement of the runner rather than of the wake-up.
    assert elapsed < _A_SHORT_HOLD * 10, (
        f"the waiter took {elapsed:.3f}s to be admitted against a {_A_SHORT_HOLD}s hold "
        f"bound, so it slept past the expiry it was entitled to"
    )


@pytest.mark.parametrize(("permits", "hold"), [(0, 1.0), (-1, 1.0), (1, 0.0), (1, -1.0)])
def test_a_gate_that_could_not_bound_anything_refuses_to_exist(permits: int, hold: float) -> None:
    """Invariants at construction, not at the first acquire.

    A gate with no permits admits nobody and one with a non-positive hold bound
    reclaims every permit the instant it is issued -- both are configurations
    whose failure would show up as a daemon that answers nothing, far from the
    line that chose the number.
    """
    with pytest.raises(ValueError, match="permit"):
        AdmissionGate(permits, max_hold_seconds=hold)


def test_parked_holders_plateau_at_twice_the_permits() -> None:
    """Round two's H-1: the reclaim was unbounded in **aggregate**.

    Reclaiming without a ceiling deletes the one property a semaphore had for
    free -- a permit never released is never re-issued -- so parked holders
    accumulate one *cohort per hold window* rather than stopping at the cap. At
    the shipped constants the adversarial round measured four per 30 s window
    until all 40 anyio worker-pool tokens were parked at t=323 s and every
    synchronous MCP tool stopped answering.

    This drives the same shape at test speed: wave after wave of holders that
    acquire and never release. RED before the ceiling -- ``outstanding`` climbs
    without limit, one cohort per wave.
    """
    permits = 3
    gate = AdmissionGate(permits, max_hold_seconds=_A_SHORT_HOLD)
    parked = 0
    peak = 0

    for _ in range(6):
        for _ in range(permits):
            if gate.acquire(_A_MOMENT) is not None:
                parked += 1
        peak = max(peak, gate.outstanding)
        time.sleep(_A_SHORT_HOLD * 1.5)

    assert peak <= 2 * permits, (
        f"the gate had {peak} outstanding permits against a cap of {permits}; reclaiming "
        f"with no ceiling re-issues a slot every hold window, so parked threads accumulate "
        f"until the process-wide worker pool is gone (round two H-1)"
    )
    assert parked <= 2 * permits, (
        f"{parked} holders were admitted and none released; the aggregate bound is "
        f"2 x {permits}, and anything above it is the accumulation this test exists for"
    )


def test_the_reclaim_ceiling_refuses_once_it_is_full() -> None:
    """The accumulation test's mechanism, driven directly.

    ``2 * permits`` parked acquisitions fill both sets; the next one must be
    refused however long it waits, because reclaiming again would put a
    ``2 * permits + 1``-th thread in flight. RED before the ceiling: the wait
    outlives a hold window, so the sweep hands out another slot.
    """
    permits = 2
    gate = AdmissionGate(permits, max_hold_seconds=_A_SHORT_HOLD)

    admitted = []
    for _ in range(2 * permits):
        token = gate.acquire(_A_SHORT_HOLD * 4)
        assert token is not None, "the gate refused inside its own aggregate bound"
        admitted.append(token)

    assert gate.outstanding == 2 * permits, (
        f"outstanding is {gate.outstanding}, so the two sets are not partitioning the "
        f"holders the way the aggregate bound is computed from"
    )
    assert gate.acquire(_A_SHORT_HOLD * 4) is None, (
        "the gate admitted a caller past its aggregate bound: with `permits` threads "
        "parked and `permits` already reclaimed, reclaiming again is what drained the "
        "worker pool in round two's measurement"
    )

    # And the wedge lifts the moment a parked thread actually returns, which is
    # what makes this a ceiling rather than a terminal state.
    gate.release(admitted[0])
    assert gate.acquire(_A_SHORT_HOLD * 4) is not None, (
        "a parked thread returned and freed a reclaim slot, and the gate still refused"
    )


def test_the_shipped_hold_bound_is_far_above_a_measured_search() -> None:
    """The constant's own claim, checked as an ordering rather than as a value.

    ``MAX_PERMIT_HOLD_SECONDS``'s docstring justifies 30 s against a 56.5 ms
    measured median search. The value itself is pinned in
    ``test_search_concurrency_cap.py``; what this asserts is the relationship the
    docstring argues from, so a future edit that drops the bound to something a
    real search can cross fails here rather than in production.

    **The margin asserted is the one the docstring states**, which it was not
    until round two: this allowed 5.66 s -- a hundred medians -- while the note
    claimed "roughly three orders of magnitude", so every value in between
    satisfied the test and falsified the prose. Both now say 531x, and the
    assertion is a floor just under it rather than a round number, so a reduction
    fails here and a re-measurement of the median is what moves it.
    """
    measured_median_search_seconds = 0.0565
    stated_ratio = 531
    measured_ratio = MAX_PERMIT_HOLD_SECONDS / measured_median_search_seconds
    assert measured_ratio > stated_ratio - 1, (
        f"the hold bound {MAX_PERMIT_HOLD_SECONDS}s is {measured_ratio:.0f}x a measured "
        f"search where its own docstring claims {stated_ratio}x, so either the bound "
        f"dropped far enough to reclaim an ordinary holder mid-answer or the note is "
        f"now false"
    )
