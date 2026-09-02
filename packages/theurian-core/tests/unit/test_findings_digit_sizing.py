"""How ``review.findings`` measures a number it must not render (ADR-0029).

``_digits`` is the reason a refusal for an absurd ``pullRequest`` or ``limit`` is
a refusal rather than a traceback: CPython will not render an integer past
``sys.get_int_max_str_digits()``, so the size of a number is *measured* from its
bit length instead of read off its string.

The measurement is an estimate plus one correction, and the whole file is about
that correction. Every input the tool-level tests reach it with is a power of ten
(``10 ** (digits - 1)``), which is exactly where the estimate is already right --
so the correction was live, load-bearing and driven by nothing. The cases here are
non-powers of ten straddling a decade boundary, where the estimate overshoots and
the correction is the only thing that makes the answer true.

The oracle is ``len(str(...))``, deliberately: that is the answer ``_digits``
exists to reproduce without paying for it, and every value here is small enough to
render. A re-derivation of the bit-length arithmetic would be the implementation
checking itself.
"""

from __future__ import annotations

import pytest

from theurian.mcp.findings import (
    _EXACT_DIGIT_BITS,
    _LOG10_OF_2,
    MAX_ECHOED_DIGITS,
    _digits,
    _sized,
)

pytestmark = pytest.mark.unit


def _estimate(magnitude: int) -> int:
    """The uncorrected estimate ``_digits`` starts from, for the tests below.

    Spelled here rather than imported because ``_digits`` no longer exposes it:
    what these tests need is the value *before* the correction, to assert that the
    correction moved it.
    """
    return int(magnitude.bit_length() * _LOG10_OF_2) + 1


#: Values whose estimate overshoots, so the down-correction decides the answer.
#:
#: Chosen at the decade boundaries where ``floor(bits * log10(2)) + 1`` is one too
#: large. ``2**66`` and ``8 * 10**19`` and ``10**20 - 1`` share bit length 67,
#: whose estimate is 21 while all three have 20 digits; ``10**21 - 1`` is the same
#: shape one decade up. ``9`` is the smallest value in the whole domain that needs
#: the correction, and it is the case a reader can check by eye.
_OVERSHOOTS = (
    9,
    99,
    2**66,
    8 * 10**19,
    10**20 - 1,
    10**21 - 1,
)

#: Values whose estimate is already exact, so the correction must leave them
#: alone. The powers of ten are what every existing test uses; the others are
#: neighbours that must not be dragged down with them.
_EXACT = (
    1,
    10,
    10**19,
    10**20,
    10**20 + 1,
    5 * 10**20,
    2**70,
    2**63 - 1,
)


@pytest.mark.parametrize("magnitude", _OVERSHOOTS, ids=str)
def test_the_down_correction_decides_a_value_the_estimate_overshoots(magnitude: int) -> None:
    """The live arm, driven by a value that actually needs it.

    Both halves are asserted. The answer must be right -- ``len(str(...))``, the
    thing ``_digits`` stands in for -- and the *estimate* must have been wrong, so
    the case is really exercising the correction rather than passing through a
    branch that never fired. Without the second assertion a fixture that drifted
    onto a power of ten would keep this test green while testing nothing, which is
    how the correction went undriven in the first place.
    """
    assert _estimate(magnitude) == len(str(magnitude)) + 1, (
        "this case no longer overshoots, so it does not drive the correction any "
        "more -- pick a value whose bit length straddles a decade boundary"
    )
    assert _digits(magnitude) == len(str(magnitude))


@pytest.mark.parametrize("magnitude", _EXACT, ids=str)
def test_a_value_the_estimate_already_gets_right_is_left_alone(magnitude: int) -> None:
    """The other direction: a correction that fired too eagerly would move these.

    The powers of ten here are the inputs every tool-level refusal test uses, so
    this is also the pin that says those tests are exercising an *uncorrected*
    path -- and therefore why the cases above had to be added separately.
    """
    assert _estimate(magnitude) == len(str(magnitude))
    assert _digits(magnitude) == len(str(magnitude))


@pytest.mark.parametrize("value", [0, 1, -1, -9, -(10**20 - 1), -(2**63)])
def test_the_sign_and_the_zero_are_measured_by_magnitude(value: int) -> None:
    """``0`` is one digit, and a negative number is measured by its magnitude.

    The minus sign is not a digit: ``_sized`` quotes the number itself below the
    echo bound, so the count is about how big the value is, not how long its
    rendering is.
    """
    assert _digits(value) == len(str(abs(value)))


def test_the_estimate_is_never_smaller_than_the_answer_it_corrects() -> None:
    """The one-sidedness the up-correction arm was deleted on.

    ``_digits`` carried an ``if magnitude >= 10**estimate: estimate += 1`` arm
    that fired for no input in the domain the correction runs on -- a branch
    indistinguishable from its own deletion. What licenses removing it is that the
    estimate is never *low*: ``magnitude < 2**bits`` bounds the true digit count
    above by ``floor(bits * log10(2)) + 1``, and ``_LOG10_OF_2`` rounds up.

    Walked here over a spread that mixes the shapes where an off-by-one would show
    -- powers of two, powers of ten, and both of their neighbours -- because a
    sampled sweep is what this assertion can afford; the exhaustive statement over
    every bit length up to :data:`_EXACT_DIGIT_BITS` is recorded in ``_digits``'s
    own docstring as a measurement, not carried as a test that would take minutes.
    """
    magnitudes = [
        candidate
        for exponent in range(1, 220)
        for candidate in (2**exponent - 1, 2**exponent, 2**exponent + 1)
    ]
    magnitudes += [candidate for k in range(1, 66) for candidate in (10**k - 1, 10**k, 10**k + 1)]

    low = [m for m in magnitudes if _estimate(m) < len(str(m))]

    assert not low, (
        f"the bit-length estimate came out BELOW the true digit count for "
        f"{len(low)} value(s), starting at {low[0] if low else None}. The deleted "
        f"up-correction arm existed for exactly this case, so its absence is now a "
        f"defect rather than dead-code removal"
    )
    assert any(_estimate(m) > len(str(m)) for m in magnitudes), (
        "no sampled value overshoots, so this sweep would also pass against a "
        "`_digits` with no correction at all -- the positive control failed"
    )


def test_the_ceiling_leaves_the_estimate_standing_within_one_digit() -> None:
    """Past :data:`_EXACT_DIGIT_BITS` the correction is skipped, by design.

    The correction's cost is the ``10 ** estimate`` it builds, which is why it
    stops; what it must not do is stop *silently wrong by more than one*. This
    drives the skipped path with a value one bit past the ceiling and asserts the
    documented guarantee -- within one digit, never below -- rather than exactness.

    Built by shifting rather than by ``10 **`` so the test does not pay the cost
    the ceiling exists to avoid.
    """
    beyond = 1 << (_EXACT_DIGIT_BITS + 1)

    estimate = _digits(beyond)

    assert estimate == _estimate(beyond), "the correction ran past its own ceiling"
    assert 0 <= estimate - _exact_digits_of_power_of_two(_EXACT_DIGIT_BITS + 1) <= 1, (
        "past the ceiling the estimate is documented as within one digit and never "
        "below the true count; it is now outside that band"
    )


#: ``log10(2)`` truncated to fifty decimal places, as an integer over ``10**50``.
#:
#: Deliberately *not* :data:`_LOG10_OF_2`: a check that re-used the constant under
#: test would agree with it by construction. Integer arithmetic rather than a
#: float, so the comparison below is exact -- the truncation understates the true
#: value by less than 1e-50, which cannot move a floor for any exponent this file
#: reaches.
_LOG10_OF_2_NUMERATOR = 30102999566398119521373889472449302676818988146210
_LOG10_SCALE = 10**50


def _exact_digits_of_power_of_two(exponent: int) -> int:
    """The exact digit count of ``2**exponent``, without rendering or building it.

    ``2**n`` is never a power of ten for ``n >= 1``, so its digit count is exactly
    ``floor(n * log10(2)) + 1``. Computed in exact integers from the rational above
    -- building ``2**1048577`` and dividing by ten repeatedly would cost more than
    the ceiling this test is about.
    """
    return (exponent * _LOG10_OF_2_NUMERATOR) // _LOG10_SCALE + 1


def test_a_number_past_the_echo_bound_is_described_and_never_quoted() -> None:
    """``_sized``'s two arms, over a value the down-correction decides.

    The boundary case that matters: a number whose *estimate* is one past the echo
    bound while its true digit count is exactly at it. Without the correction this
    value would be described by size rather than quoted, so a caller who mistyped
    a plausible pull-request number would not see the number they sent.
    """
    at_the_bound = 10**MAX_ECHOED_DIGITS - 1

    assert len(str(at_the_bound)) == MAX_ECHOED_DIGITS
    assert _estimate(at_the_bound) == MAX_ECHOED_DIGITS + 1, (
        "this value no longer overshoots, so it stopped being the boundary case"
    )
    assert _sized(at_the_bound) == str(at_the_bound)
    assert _sized(10**MAX_ECHOED_DIGITS) == f"a {MAX_ECHOED_DIGITS + 1}-digit number"
