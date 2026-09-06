"""The default timeout must outlast a green walk at the worker count in use (#566).

A timeout shorter than a green suite does not report "slow". It reports HUNG
for every survivor and takes the control down with it, and both read exactly
like findings. PR #581's adversarial round lost roughly 90 minutes to that
shape: at the then-default ``--workers 4`` the green walk of 5498 tests ran
past the flat 1800 s bound, so every mutation came back HUNG above a control
that had produced no verdict and named no failing test.

The two anchors below are the measurements from that round, written as literals
rather than imported, so they stay a claim about *this machine's suite* that the
implementation cannot satisfy by construction. Re-measure and move them; do not
reason about them.
"""

from __future__ import annotations

import pytest
from mutate import _DEFAULT_TIMEOUT_SECONDS, _default_timeout

pytestmark = pytest.mark.unit

#: Seconds a green full walk took, by ``--workers`` -- Apple silicon laptop,
#: 5498 tests, 2026-09-06, recorded on issue #566.
_MEASURED_GREEN_WALK: dict[int, int] = {2: 1400, 3: 1640}

#: The same round's third observation, which has no second-precision figure:
#: at four workers the green walk ran past 1800 s.
_WALK_EXCEEDED_AT_FOUR_WORKERS = 1800


@pytest.mark.parametrize(("workers", "walk"), sorted(_MEASURED_GREEN_WALK.items()))
def test_the_default_timeout_clears_a_measured_green_walk_with_room_to_spare(
    workers: int, walk: int
) -> None:
    """A mutation may legitimately slow the suite; the bound must absorb that.

    Clearing the walk by a hair is not enough. The number being bounded is a
    *green* walk, and the runs that actually hit the timeout are mutated ones,
    which can be slower for reasons that are the finding rather than a hang.
    A 20% margin is the weakest form of that claim, and a flat 1800 s fails it
    at three workers -- which is where this went wrong.
    """
    assert _default_timeout(workers) >= walk * 1.2


def test_the_default_timeout_clears_the_walk_that_broke_the_flat_bound() -> None:
    """At four workers -- the shipped default -- the old bound was under the walk.

    This is the defect itself, stated as an assertion: the harness's own default
    worker count must not come with a timeout the suite walks straight through.
    """
    assert _default_timeout(4) > _WALK_EXCEEDED_AT_FOUR_WORKERS


def test_the_default_timeout_rises_with_every_added_worker() -> None:
    """What grows is contention between concurrent suites on one machine.

    A flat default cannot be right for both ends of ``--workers``, and the two
    measured points are 240 s apart for one added worker. Monotonicity is the
    part of the model that does not depend on the fitted numbers, so it is
    asserted separately from them.
    """
    defaults = [_default_timeout(workers) for workers in (1, 2, 3, 4, 5)]

    assert defaults == sorted(defaults)
    assert defaults[-1] > defaults[0]


@pytest.mark.parametrize("workers", [1, 2, 3, 4, 8])
def test_no_worker_count_shortens_the_bound_the_harness_already_had(workers: int) -> None:
    """Scaling may only ever grant more time than 1800 s, never less.

    The fitted line predicts 1160 s at one worker, and adopting that would
    make a single-worker run *more* likely to report a false HUNG than before
    this change -- a fix that regresses the case it was not aimed at. The floor
    is what stops that, and it is invisible in any assertion above.
    """
    assert _default_timeout(workers) >= _DEFAULT_TIMEOUT_SECONDS


def test_a_worker_count_below_one_does_not_collapse_the_bound() -> None:
    """``--workers 0`` is not rejected upstream, so the model must survive it.

    Nothing here validates the flag, and a linear model evaluated at zero or a
    negative is how a scaled default becomes shorter than an unscaled one.
    """
    assert _default_timeout(0) >= _DEFAULT_TIMEOUT_SECONDS
    assert _default_timeout(-3) >= _DEFAULT_TIMEOUT_SECONDS
