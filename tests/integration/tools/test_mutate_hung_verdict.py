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
from collections.abc import Callable
from pathlib import Path

import mutate_run
import pytest
from mutate_edits import Applied, Edit, Mutation
from mutate_run import Options, SuiteHungError, _run_one, _run_suite

pytestmark = pytest.mark.integration

_HANGING_UV = "#!/bin/sh\nexec sleep 300\n"

# A hang that has already printed something before it was killed -- the
# common case, and the one MEDIUM-2 says used to be thrown away.
_HANGING_UV_WITH_PARTIAL_OUTPUT = (
    "#!/bin/sh\nprintf 'tests/integration/test_x.py .....\\n'\nexec sleep 300\n"
)


def _install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir(exist_ok=True)
    target = fake_bin / "uv"
    target.write_text(script, encoding="utf-8")
    target.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")


@pytest.fixture
def hanging_uv_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Put a `uv` that never returns ahead of the real one on PATH."""
    _install(tmp_path, monkeypatch, _HANGING_UV)


def _options(tmp_path: Path, *, timeout: int) -> Options:
    return Options(
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
    with pytest.raises(SuiteHungError):
        _run_suite(tmp_path, _options(tmp_path, timeout=1), tmp_path / "uvcache")


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

    outcome = _run_one(tmp_path, mutation, _options(tmp_path, timeout=1), tmp_path / "uvcache")

    assert outcome.verdict == "HUNG"
    assert outcome.digests, "a HUNG outcome must still carry apply/restore digests"
    assert target.read_text(encoding="utf-8") == original


def test_a_hung_control_reports_error_with_an_accurate_duration(
    tmp_path: Path, hanging_uv_on_path: None
) -> None:
    """LOW-1: the control branch must catch a hang, not just the mutation branch.

    Before this fix, a hung control was not caught by ``_run_one`` at all --
    ``SuiteHungError`` propagated out, was caught by ``_execute``'s generic
    ``except HarnessError``, and reported with ``seconds=0.0`` since that
    handler has no ``started`` timestamp to measure from. Catching it here
    keeps the elapsed time honest.
    """
    control = Mutation(label="__control__", path=None, old="", new="")

    outcome = _run_one(tmp_path, control, _options(tmp_path, timeout=1), tmp_path / "uvcache")

    assert outcome.verdict == "ERROR"
    assert outcome.seconds > 0.5, "a hang timed out at 1s must not report ~0.0s"


def test_run_suite_carries_partial_output_into_suitehungerror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MEDIUM-2: whatever the suite printed before it was killed must survive.

    ``subprocess.TimeoutExpired.output`` carries partial output as bytes even
    with ``text=True`` (confirmed against 3.13) -- a hang costs the full
    timeout to reproduce, so this was the most expensive thing the harness
    could discard.
    """
    _install(tmp_path, monkeypatch, _HANGING_UV_WITH_PARTIAL_OUTPUT)

    with pytest.raises(SuiteHungError) as excinfo:
        _run_suite(tmp_path, _options(tmp_path, timeout=1), tmp_path / "uvcache")

    assert "tests/integration/test_x.py" in excinfo.value.output


def test_a_hung_mutation_surfaces_partial_output_in_the_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MEDIUM-2: the partial output must reach ``Outcome.summary``/``.detail``.

    Re-obtaining a hang costs the timeout again -- up to 1800s by default --
    so whatever the suite had printed before it was killed must not be
    dropped on the floor a second time, on the way from the exception into
    the outcome that gets reported and persisted to ``--json``.
    """
    _install(tmp_path, monkeypatch, _HANGING_UV_WITH_PARTIAL_OUTPUT)
    target = tmp_path / "target.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    mutation = Mutation(
        label="hangs-with-output", path="target.py", old="VALUE = 1", new="VALUE = 2"
    )

    outcome = _run_one(tmp_path, mutation, _options(tmp_path, timeout=1), tmp_path / "uvcache")

    assert outcome.verdict == "HUNG"
    assert "tests/integration/test_x.py" in outcome.summary
    assert "tests/integration/test_x.py" in outcome.detail


def test_a_hung_composite_mutation_restores_the_tree_with_one_restore_pass(
    tmp_path: Path, hanging_uv_on_path: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MEDIUM-1: the HUNG path must call ``_restore_all`` exactly once.

    It used to call it twice for every hang -- once to build its own
    digests, again in ``finally`` -- which for a composite mutation briefly
    wrote the intermediate mutated state back to disk in between two calls
    that both landed on the same correct final content. Counting the calls
    is the only way to observe that the redundant write is actually gone;
    the final state alone cannot distinguish the two.
    """
    calls = 0
    # `_restore_all` is imported into `mutate_run` from `mutate_edits`, not
    # declared there, so mypy's `no_implicit_reexport` treats a direct
    # attribute access as reaching outside the module's public surface;
    # `getattr` reaches the same object without tripping that check.
    _restore_all_ref = getattr(mutate_run, "_restore_all")  # noqa: B009
    real_restore_all: Callable[[tuple[Applied, ...], Path], dict[str, str]] = _restore_all_ref

    def _counting_restore_all(landed: tuple[Applied, ...], tree: Path) -> dict[str, str]:
        nonlocal calls
        calls += 1
        return real_restore_all(landed, tree)

    monkeypatch.setattr(mutate_run, "_restore_all", _counting_restore_all)
    target = tmp_path / "file.py"
    original = "AAA\nBBB\nCCC\n"
    target.write_text(original, encoding="utf-8")
    mutation = Mutation(
        label="hangs-composite",
        path="file.py",
        old="AAA",
        new="XXX",
        also=(Edit(path="file.py", old="BBB", new="YYY"),),
    )

    outcome = _run_one(tmp_path, mutation, _options(tmp_path, timeout=1), tmp_path / "uvcache")

    assert outcome.verdict == "HUNG"
    assert calls == 1
    assert target.read_text(encoding="utf-8") == original
