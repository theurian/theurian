"""Every way review ingestion declines carries a cure a reader can run (ADR-0030).

A refusal envelope is a published document, and the failure this file exists to
stop has been caught three times on this project: a remedy constant whose test
asserts only that it is non-empty, so ``"Something went wrong."`` ships. The
assertion here is shape, not length -- a remedy must name **a command the reader
can run** and **an artefact to act on** -- and :data:`_PLACEHOLDERS` is the
can-fail companion that keeps the checker honest.

**The population is the enum, read at run time.** ``RefusalGrade`` is iterated
rather than transcribed, so a grade added without a remedy reddens here before it
can be raised anywhere.
"""

from __future__ import annotations

import re
from typing import Final

import pytest

from theurian.domain.errors import InvariantViolationError
from theurian.domain.review_ingest import (
    MAX_REFUSAL_DETAIL_CHARS,
    REMEDIES,
    RefusalEnvelope,
    RefusalGrade,
    ReviewIngestRefusedError,
)

pytestmark = pytest.mark.unit

#: A backticked command whose first word is a program this project tells people
#: to run, with at least one argument after it. ``\x60gh\x60`` alone is a noun;
#: ``\x60gh auth login\x60`` is something a reader can type.
_COMMAND = re.compile(r"`(gh|git|theurian)\s+[^`]+`")

#: A token with a path, a dotted key, or a host in it -- ``.theurian/config.yaml``,
#: ``providers.review.repositories``, ``config.yml``, ``github.com``,
#: ``https://cli.github.com``. Matched anywhere in the text, backticked or not,
#: because an artefact is named in prose as often as in code font.
_ARTEFACT = re.compile(r"[\w~-]*[./][\w~./-]*[\w/]")

#: Strings that are not remedies, so the checker above can be shown to fail.
#:
#: The first two are the shapes that actually shipped elsewhere on this project.
#: The third and fourth are the near misses: a command with no argument, and an
#: artefact with nothing to run against it.
_PLACEHOLDERS: Final[tuple[str, ...]] = (
    "Something went wrong.",
    "Try again.",
    "Run `gh`.",
    "Edit `.theurian/config.yaml`.",
)


def _names_a_remedy(text: str) -> bool:
    """Whether ``text`` names both a runnable command and an artefact to act on."""
    return _COMMAND.search(text) is not None and _ARTEFACT.search(text) is not None


@pytest.mark.parametrize("placeholder", _PLACEHOLDERS, ids=_PLACEHOLDERS)
def test_the_remedy_check_rejects_a_placeholder(placeholder: str) -> None:
    """The can-fail companion: without it the row below asserts an always-true predicate.

    A test that only ever sees real remedies cannot tell a working checker from
    one that returns ``True``. Each string here is a shape that has shipped, or
    that is one word away from shipping: a sentence with no cure at all, a
    command with no arguments, and an artefact with nothing to run against it.
    """
    assert not _names_a_remedy(placeholder), (
        f"{placeholder!r} passed the remedy check, so the check cannot fail and the "
        "assertion over `REMEDIES` proves nothing. Fix `_names_a_remedy` before "
        "trusting a green result from "
        "`test_every_grade_records_a_remedy_that_names_a_command_and_an_artefact`."
    )


def test_every_grade_records_a_remedy_that_names_a_command_and_an_artefact() -> None:
    """RED means a refusal can be raised with no cure, or with a cure nobody can run.

    The population is ``RefusalGrade`` itself, so this is not a list somebody
    keeps in step: a member added to the enum with no ``REMEDIES`` row fails the
    first assertion, and one whose remedy is a placeholder fails the second.
    """
    missing = [grade.value for grade in RefusalGrade if grade not in REMEDIES]

    assert not missing, (
        f"{missing} have no recorded remedy. Every grade names a cure, because "
        "`ReviewIngestRefusedError` looks the remedy up rather than taking one -- a "
        "grade with no row cannot be raised at all."
    )

    unusable = [grade.value for grade in RefusalGrade if not _names_a_remedy(REMEDIES[grade])]

    assert not unusable, (
        f"{unusable} record a remedy that names no runnable command, no artefact, or "
        "neither:\n"
        + "\n".join(f"  {grade}: {REMEDIES[RefusalGrade(grade)]!r}" for grade in unusable)
        + "\n\nA remedy names the thing to act on and something the reader can type. "
        "A truthy string is not a remedy."
    )


def test_a_refusal_carries_the_recorded_remedy_and_cannot_be_handed_another() -> None:
    """RED means a call site can publish its own cure, which is how a placeholder gets in."""
    for grade in RefusalGrade:
        error = ReviewIngestRefusedError(grade, "a summary")

        assert error.remedy == REMEDIES[grade], f"{grade.value} carries a remedy of its own"
        assert error.envelope.remedy == REMEDIES[grade], (
            f"{grade.value}'s envelope and its `remedy` attribute disagree"
        )
        assert error.grade is grade


def test_an_envelope_refuses_an_empty_summary() -> None:
    """A refusal that cannot say what it refused is a bug at construction, not at render."""
    with pytest.raises(InvariantViolationError, match="empty summary"):
        RefusalEnvelope(
            grade=RefusalGrade.TOOL_MISSING,
            summary="   ",
            detail="",
            remedy=REMEDIES[RefusalGrade.TOOL_MISSING],
        )


def test_an_envelope_refuses_a_detail_longer_than_the_recorded_bound() -> None:
    """The child's output is contained by the producer, and this is what says so.

    ``detail`` is the one field carrying text this process did not write. An
    envelope is published, so an unbounded child stderr reaching it would be the
    channel the containment exists to close -- and a producer that forgets to
    slice fails here rather than in somebody's terminal.
    """
    with pytest.raises(InvariantViolationError, match="bounded at"):
        RefusalEnvelope(
            grade=RefusalGrade.TOOL_FAILED,
            summary="gh failed",
            detail="x" * (MAX_REFUSAL_DETAIL_CHARS + 1),
            remedy=REMEDIES[RefusalGrade.TOOL_FAILED],
        )


def test_a_detail_exactly_at_the_bound_is_accepted() -> None:
    """The boundary, so the check above is not off by one in the refusing direction."""
    envelope = RefusalEnvelope(
        grade=RefusalGrade.TOOL_FAILED,
        summary="gh failed",
        detail="x" * MAX_REFUSAL_DETAIL_CHARS,
        remedy=REMEDIES[RefusalGrade.TOOL_FAILED],
    )

    assert len(envelope.detail) == MAX_REFUSAL_DETAIL_CHARS
