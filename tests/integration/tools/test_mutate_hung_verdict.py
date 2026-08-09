"""A suite that never finishes must be reported HUNG, not a bare ERROR.

Issue #68 (the HUNG-verdict patch): Milestone 6 met a mutation whose suite
hangs rather than goes red -- the batch reported ``ERROR (the run proves
nothing)`` with the digests dropped, so nothing said the mutation had even
applied. A hang is the same *class* of finding as SURVIVED (the suite cannot
go RED for that mutation), so it is now its own verdict, carrying digests like
every other non-KILLED outcome.

These tests spawn a real subprocess: ``_uv()`` resolves to a throwaway shell
script on ``PATH`` that hangs past the harness's own timeout, so the timeout,
the ``subprocess.TimeoutExpired``, and its conversion into ``SuiteHungError``
are all exercised for real -- nothing about ``_run_suite`` itself is faked.
"""

from __future__ import annotations

import os
from pathlib import Path

import mutate
import pytest
from mutate_edits import Mutation

pytestmark = pytest.mark.integration

_HANGING_UV = "#!/bin/sh\nexec sleep 300\n"


@pytest.fixture
def hanging_uv_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Put a `uv` that never returns ahead of the real one on PATH."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    script = fake_bin / "uv"
    script.write_text(_HANGING_UV, encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")


def _options(tmp_path: Path, *, timeout: int) -> mutate.Options:
    return mutate.Options(
        workers=1,
        fail_fast=True,
        control=False,
        timeout=timeout,
        keep_trees=False,
        json_path=None,
        work_dir=None,
    )


def test_run_suite_converts_a_real_timeout_into_suitehungerror(
    tmp_path: Path, hanging_uv_on_path: None
) -> None:
    """The one-line fix: ``TimeoutExpired`` must become ``SuiteHungError``.

    Not a bare ``HarnessError`` -- ``_run_one`` only special-cases the
    subclass, so if this regressed to the base class the HUNG branch below
    would never trigger and every hang would silently fall back to a bare
    ERROR with no digests, which is the exact defect issue #68 reports.
    """
    with pytest.raises(mutate.SuiteHungError):
        mutate._run_suite(tmp_path, _options(tmp_path, timeout=1), tmp_path / "uvcache")


def test_run_one_reports_hung_with_digests_and_restores_the_file(
    tmp_path: Path, hanging_uv_on_path: None
) -> None:
    """A hung mutation's file must come back exactly as it was, verdict HUNG.

    Why it matters: the whole point of the fix is that "did it apply" stays
    answerable even when the suite never finishes -- a hang must not leave a
    mutated file behind for whatever borrows this tree next.
    """
    target = tmp_path / "target.py"
    original = "VALUE = 1\n"
    target.write_text(original, encoding="utf-8")
    mutation = Mutation(label="hangs", path="target.py", old="VALUE = 1", new="VALUE = 2")

    outcome = mutate._run_one(
        tmp_path, mutation, _options(tmp_path, timeout=1), tmp_path / "uvcache"
    )

    assert outcome.verdict == "HUNG"
    assert outcome.digests, "a HUNG outcome must still carry apply/restore digests"
    assert target.read_text(encoding="utf-8") == original
