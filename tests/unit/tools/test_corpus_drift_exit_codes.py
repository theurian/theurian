"""`--advisory` downgrades drift, and must never downgrade "compared nothing".

The asymmetry in :func:`corpus_drift.exit_code` is the whole operational
argument for shipping this check as advisory (#263, commit 1d10b97): drift is a
maintenance signal on a normal action -- somebody improved an ADR -- so failing
the build on it trains people to bypass the job, while a run that compared
nothing is not a signal about the corpus at all. It is the tool reporting that
it has stopped working.

A single character makes those two collapse into each other. ``return 0 if
advisory else 2`` in the ``NOTHING_COMPARED`` branch turns "the committed corpus
is gone" and "git could not be asked" into a green tick on every pull request,
and the CI job that runs this deliberately carries neither ``continue-on-error``
nor ``|| true`` precisely so that nothing else can launder it either. This file
is the only thing standing between that character and a check that has silently
gone quiet.

The table is pinned in both directions on purpose: three tests assert that
``advisory`` *does* change the answer, and three that it does not. A guard that
only ever asserts the strict outcome passes just as happily against
``exit_code`` ignoring its keyword entirely.
"""

from __future__ import annotations

import corpus_drift
import pytest
from corpus_drift import Comparison, Report, Status, Verdict

pytestmark = pytest.mark.unit

_MIGRATION = ".theurian/migrations/01MB4V3XKQ7ZPYE8R2NGT5HW6A-adr-0001-example.yaml"
_DOCUMENT = "docs/adr/0001-example.md"

_MATCHED = Comparison(
    migration=_MIGRATION,
    item_id="architecture.example",
    revision_id="01MB4V3XKQ7ZPYE8R2NGT5HW6B",
    file_path=_DOCUMENT,
    verdict=Verdict.MATCHED,
    expected="a" * 64,
    actual="a" * 64,
    detail=f"{_DOCUMENT} still hashes to aaaaaaaaaaaa",
)

_DRIFTED = Comparison(
    migration=_MIGRATION,
    item_id="architecture.example",
    revision_id="01MB4V3XKQ7ZPYE8R2NGT5HW6B",
    file_path=_DOCUMENT,
    verdict=Verdict.DRIFTED,
    expected="a" * 64,
    actual="b" * 64,
    detail=f"{_DOCUMENT} now hashes to bbbbbbbbbbbb, and the corpus pins aaaaaaaaaaaa",
)

_UNCHECKABLE = Comparison(
    migration=_MIGRATION,
    item_id="architecture.example",
    revision_id="01MB4V3XKQ7ZPYE8R2NGT5HW6B",
    file_path=_DOCUMENT,
    verdict=Verdict.UNCHECKABLE,
    detail="carries a line range",
)

CLEAN = Report((_MATCHED,), Status.CLEAN, "no drift -- compared 1 anchor(s).")
DRIFTED = Report((_DRIFTED,), Status.DRIFTED, "1 drifted -- compared 1 anchor(s).")
NOTHING_COMPARED = Report(
    (_UNCHECKABLE,), Status.NOTHING_COMPARED, "all 1 anchor(s) were uncheckable."
)


def test_a_clean_run_exits_zero() -> None:
    """The ordinary pass. Without it, "always 2" would satisfy every other test here."""
    assert corpus_drift.exit_code(CLEAN, advisory=False) == 0


def test_advisory_leaves_a_clean_run_alone() -> None:
    """`--advisory` is a downgrade, not an override: it must not invent a failure."""
    assert corpus_drift.exit_code(CLEAN, advisory=True) == 0


def test_drift_fails_a_run_that_did_not_ask_for_advisory() -> None:
    """Run bare -- a local `uv run python tools/corpus_drift.py` -- drift is exit 1.

    The CI job passes `--advisory`; a maintainer checking before a re-seed does
    not, and wants a non-zero status they can chain on.
    """
    assert corpus_drift.exit_code(DRIFTED, advisory=False) == 1


def test_advisory_downgrades_drift_to_a_pass() -> None:
    """The reason the job is advisory: an ADR edit must not redden its own pull request.

    The finding is not actionable in the same change -- re-seeding runs
    `theurian propose`, which is its own commit -- so the annotation is the
    delivery mechanism and the exit status stays 0.
    """
    assert corpus_drift.exit_code(DRIFTED, advisory=True) == 0


def test_a_run_that_compared_nothing_exits_two_not_zero() -> None:
    """Zero comparisons is a finding, not a clean bill of health.

    An empty population reads as "no drift found" to anything that only looks
    at the status, which is exactly the failure a checker that has stopped
    checking produces.
    """
    assert corpus_drift.exit_code(NOTHING_COMPARED, advisory=False) == 2


def test_advisory_does_not_downgrade_a_run_that_compared_nothing() -> None:
    """The load-bearing case: advisory mode must not launder a silent check into a pass.

    `--advisory` exists to stop drift reddening a documentation pull request.
    Extending it over `NOTHING_COMPARED` would mean the committed corpus
    disappearing, or `git ls-files` failing in the CI container, produces the
    same green tick as a corpus in perfect health -- and the job wired in
    1d10b97 has no other guard, by design.
    """
    assert corpus_drift.exit_code(NOTHING_COMPARED, advisory=True) == 2
