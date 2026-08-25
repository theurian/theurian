"""``remedy_for``'s purge-failed arm names its own cure (GHSA-97q9-xxfg-33r6).

``theurian index status`` prints one command for the operator to run, chosen by
``cli.index_status_report.remedy_for`` from one keyword per axis. A purge-failed
build and an ordinary stale build both end in "run ``theurian index build``", but
they are *not* the same message: the purge-failed arm has to say **why** the
index is unusable -- it still holds rows a withdrawal removed from canonical
state -- because the bare rebuild the plain ``stale`` arm prints reads as a
routine refresh, and an operator who reads "refresh" does not learn that their
build was serving withheld rows through a visible sibling's ``raptorPath``.

**Why this is pinned here as a unit call rather than left to the integration
tests.** The integration tests in
``tests/integration/test_purge_failed_build_is_not_served.py`` assert only
``"index build" in status["remedy"]`` -- and the generic stale remedy
(``"Run `theurian index build`."``) satisfies that too. That is a borrowed-
strength assertion: deleting the ``if purge_failed:`` arm entirely leaves them
green, because a purge-failed build is always stale on the state-hash axis and
falls straight through to the ``stale`` arm, whose string also contains "index
build". The integration assertions are deliberately weak that way -- they are
about the ``stale`` and ``purgeFailed`` *fields*, not the remedy's wording, and
their docstrings say so -- so the remedy's own text is pinned here instead, where
the two arms can be compared directly with nothing else moving.

A pure function with no I/O, so it lives under ``tests/unit`` beside the other
call-site pins rather than paying an integration test's cost to exercise one
``if``.
"""

from __future__ import annotations

import pytest

from theurian.cli.index_status_report import remedy_for

pytestmark = pytest.mark.unit

#: A substring of the purge-failed remedy and of no other arm's. Written out as a
#: literal rather than imported from the source, because the point of the test is
#: to hold the *published wording* to a specific phrase: importing the string it
#: is checked against would make any rewording -- including one that deleted the
#: distinction -- pass by construction.
_PURGE_FAILED_PHRASE = "the purge that follows a withdrawal did not complete"


def _remedy(*, stale: bool, purge_failed: bool) -> str:
    """``remedy_for`` with every axis but ``stale`` and ``purge_failed`` cleared.

    The other four keywords each name a remedy that precedes both arms under test
    (an unreadable profile, a corrupt pointer, an orphaned build, a pending
    apply), so any of them left set would short-circuit ``remedy_for`` before it
    reached the two branches this file is about.
    """
    return remedy_for(
        stale=stale,
        needs_apply=False,
        orphaned=False,
        pointer_corrupt=False,
        purge_failed=purge_failed,
        profile_remedy="",
    )


def test_the_purge_failed_remedy_differs_from_the_plain_stale_remedy() -> None:
    """A failed purge and an ordinary stale build must not get the same message.

    The two arms are reached with identical inputs except ``purge_failed``, so a
    difference between the returned strings is attributable to that one axis and
    nothing else. If the ``if purge_failed:`` arm were deleted (or its guard made
    unreachable), a purge-failed build would fall through to the ``stale`` arm and
    the two calls would return the *same* string -- which is exactly the state
    this asserts against, and the reason the integration tests' ``"index build"
    in remedy`` check cannot see it.
    """
    purge_failed = _remedy(stale=True, purge_failed=True)
    plain_stale = _remedy(stale=True, purge_failed=False)

    assert purge_failed != plain_stale, (
        "a purge-failed build was handed the plain stale remedy, so an operator whose index "
        "still holds withdrawn rows is told only to refresh it: "
        f"{purge_failed!r} == {plain_stale!r}"
    )


def test_only_the_purge_failed_remedy_carries_its_own_reason() -> None:
    """The purge-failed arm carries a phrase the plain stale arm does not.

    This is the assertion the borrowed-strength one could not make: it holds the
    purge-failed remedy to wording that names *why* the build is unusable, and
    holds the plain stale remedy to *not* carrying it. Deleting or disabling the
    ``if purge_failed:`` arm makes the first assertion fail -- the fall-through
    stale string does not contain the phrase -- so this test goes red on the exact
    mutation the integration coverage sleeps through.
    """
    purge_failed = _remedy(stale=True, purge_failed=True)
    plain_stale = _remedy(stale=True, purge_failed=False)

    assert _PURGE_FAILED_PHRASE in purge_failed, (
        f"the purge-failed remedy no longer explains that the index still holds withdrawn "
        f"rows, so it is indistinguishable from an ordinary refresh: {purge_failed!r}"
    )
    assert _PURGE_FAILED_PHRASE not in plain_stale, (
        f"the plain stale remedy carries the purge-failed reason, so the phrase no longer "
        f"distinguishes the two arms: {plain_stale!r}"
    )
