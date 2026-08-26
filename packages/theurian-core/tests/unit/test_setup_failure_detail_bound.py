"""How wide the channel :func:`failure_detail` opens on the terminal is.

Withholding decides *whether* an exception's message travels: a shared report
gets the type name, the operator's own terminal gets the message in full (O-3,
SEC-6). What nothing decided was **how much** of it, and ``SetupService._probe``
catches ``Exception`` -- so the string is whatever the raising library chose to
build.

In practice the widest of these is a migration refusal, and it is short only
because PyYAML truncates its own snippet before raising: a syntax error 5,000
characters into a single line renders at 169. That is a fact about somebody
else's package, revisable in any release of it, and it is the only thing that
was standing where a recorded limit belongs.

These tests hold the limit to being Theurian's own: a bound that is stated in
the code, applied on the non-publication branch, and visible in the output when
it fires.
"""

from __future__ import annotations

import pytest

from theurian.application.setup_withholding import MAX_FAILURE_DETAIL_CHARS, failure_detail

pytestmark = pytest.mark.unit

#: Distinctive enough that no assertion passes on a coincidental substring. A
#: single repeated letter does not do: the published sentence contains most of
#: them, and "v not in published" failed on the ``v`` of "carries whatever".
_SENTINEL = "SentinelExceptionTextWWWW"


class _VerboseError(Exception):
    """An exception whose message is as long as it likes.

    Synthetic on purpose. Every message these paths carry today happens to be
    short, so a guard driven only by real inputs is a guard nothing reaches --
    which is the shape that survives its own deletion.
    """


def test_a_message_under_the_bound_reaches_the_terminal_intact() -> None:
    """The control, and it is not decoration.

    Without it, "the detail is bounded" passes for an implementation that
    truncates everything, including the 169-character parser errors this channel
    actually carries -- and the whole point of the non-publication branch is that
    the person who has to fix the file reads the message in full.
    """
    message = "m" * (MAX_FAILURE_DETAIL_CHARS - len("_VerboseError: ") - 1)

    detail = failure_detail(_VerboseError(message), for_publication=False)

    assert detail == f"_VerboseError: {message}"
    assert "truncated" not in detail


def test_a_message_exactly_at_the_bound_is_not_cut() -> None:
    """The boundary itself, where ``<= MAX_FAILURE_DETAIL_CHARS`` and ``<`` diverge.

    A detail exactly ``MAX_FAILURE_DETAIL_CHARS`` long is inside the bound and must
    reach the terminal whole. Without this case the ``<=`` -> ``<`` mutation
    survives the suite: the under-bound control above passes at one below the
    limit, and the over-bound case passes far above it, so the one length where the
    comparison's own boundary lives goes unmeasured.
    """
    message = "m" * (MAX_FAILURE_DETAIL_CHARS - len("_VerboseError: "))

    detail = failure_detail(_VerboseError(message), for_publication=False)

    assert len(detail) == MAX_FAILURE_DETAIL_CHARS
    assert detail == f"_VerboseError: {message}"
    assert "truncated" not in detail


def test_a_message_past_the_bound_is_cut_and_says_so() -> None:
    """The bound is on the whole detail, which is what the constant's name claims.

    The type name is Theurian's own and the message is not, but a reader who has
    been handed a prefix has to be told that is what it is: an error message cut
    mid-sentence and presented as complete sends someone looking for a fault at
    the point the string happens to stop.
    """
    detail = failure_detail(
        _VerboseError("v" * (MAX_FAILURE_DETAIL_CHARS * 3)), for_publication=False
    )

    assert len(detail) == MAX_FAILURE_DETAIL_CHARS
    assert detail.startswith("_VerboseError: vvv")
    assert detail.endswith(" ... [truncated]")


def test_the_published_branch_is_not_the_one_being_bounded() -> None:
    """A shared report carries no message, so there is nothing there to cut.

    Pinned because the fix touches one branch of a two-branch function, and a
    truncation applied to both would degrade the published sentence -- which is
    Theurian's own text, written to be read whole -- for no gain.
    """
    published = failure_detail(_VerboseError(_SENTINEL * 200), for_publication=True)

    assert _SENTINEL not in published
    assert "truncated" not in published
    assert published.startswith("_VerboseError. The message is withheld")
