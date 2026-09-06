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
    assert _A_MOMENT <= elapsed < _A_MOMENT * 10, (
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
    neither.
    """
    gate = AdmissionGate(1, max_hold_seconds=_A_SHORT_HOLD)
    parked = gate.acquire(_A_MOMENT)
    assert parked is not None
    reissued = gate.acquire(_A_SHORT_HOLD * 4)
    assert reissued is not None

    gate.release(parked)  # the parked thread, waking up late

    assert gate.acquire(_A_MOMENT) is None, (
        "the stale release handed the gate a permit it had already re-issued, so the cap "
        "inflated to two against one"
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
    assert elapsed < _A_SHORT_HOLD * 5, (
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


def test_the_shipped_hold_bound_is_far_above_a_measured_search() -> None:
    """The constant's own claim, checked as an ordering rather than as a value.

    ``MAX_PERMIT_HOLD_SECONDS``'s docstring justifies 30 s against a 56.5 ms
    measured median search. The value itself is pinned in
    ``test_search_concurrency_cap.py``; what this asserts is the relationship the
    docstring argues from, so a future edit that drops the bound to something a
    real search can cross fails here rather than in production.
    """
    measured_median_search_seconds = 0.0565
    assert measured_median_search_seconds * 100 < MAX_PERMIT_HOLD_SECONDS, (
        f"the hold bound {MAX_PERMIT_HOLD_SECONDS}s is within two orders of magnitude of a "
        f"measured search, so an ordinary holder can now be reclaimed mid-answer"
    )
