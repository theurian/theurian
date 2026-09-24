"""Pure diff/render/exit-code coverage for ``tools/eval/compare_baseline.py`` (ADR-0036 decision
4, Phase A slice S4b).

The heavy regeneration itself is pinned by
``tests/integration/tools/test_baseline_current.py``; nothing here invokes the harness -- every
case below drives :func:`compare_baseline.compare` and :func:`compare_baseline.exit_code`
directly, against the committed baseline's own bytes or a perturbed copy of them.

``tools/eval`` is a flat script directory, not a package: this file puts it on ``sys.path``
itself, the way ``test_baseline_current.py`` and ``test_harness_pins.py`` beside it do.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
_HARNESS_DIR = REPO_ROOT / "tools" / "eval"
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

import compare_baseline  # noqa: E402
import run as harness_run  # noqa: E402

BASELINE_REPORT = _HARNESS_DIR / "baseline" / "report.json"


def test_the_committed_baseline_compared_against_itself_holds() -> None:
    baseline_bytes = BASELINE_REPORT.read_bytes()

    comparison = compare_baseline.compare(baseline_bytes, baseline_bytes)

    assert comparison.status is compare_baseline.Status.HOLDS
    assert comparison.differing_paths == frozenset()
    assert "holds" in compare_baseline.render_text(comparison)
    assert compare_baseline.render_github(comparison) == ()


def test_a_perturbed_copy_names_the_differing_path_and_stays_exit_0_under_advisory() -> None:
    baseline_bytes = BASELINE_REPORT.read_bytes()
    mutated = json.loads(baseline_bytes)
    mutated["aggregated"]["overall"]["mrr"] = (mutated["aggregated"]["overall"]["mrr"] or 0.0) + 1.0
    mutated_bytes = (json.dumps(mutated, sort_keys=True, indent=2) + "\n").encode("utf-8")

    comparison = compare_baseline.compare(baseline_bytes, mutated_bytes)

    assert comparison.status is compare_baseline.Status.DIFFERS
    assert comparison.differing_paths == frozenset({"aggregated.overall.mrr"})
    assert "aggregated.overall.mrr" in compare_baseline.render_text(comparison)
    assert any(
        "aggregated.overall.mrr" in line for line in compare_baseline.render_github(comparison)
    )
    assert compare_baseline.exit_code(comparison, advisory=True) == 0
    assert compare_baseline.exit_code(comparison, advisory=False) == 1


def test_bytes_differing_with_no_structural_difference_is_still_reported_as_differs() -> None:
    baseline_bytes = BASELINE_REPORT.read_bytes()
    reformatted = json.dumps(json.loads(baseline_bytes), sort_keys=True, indent=4).encode("utf-8")

    comparison = compare_baseline.compare(baseline_bytes, reformatted)

    assert comparison.status is compare_baseline.Status.DIFFERS
    assert comparison.differing_paths == frozenset()
    assert compare_baseline.render_github(comparison) != ()


def test_an_errored_comparison_exits_0_under_advisory_and_1_without_it() -> None:
    comparison = compare_baseline.Comparison(compare_baseline.Status.ERRORED, detail="boom")

    assert compare_baseline.exit_code(comparison, advisory=True) == 0
    assert compare_baseline.exit_code(comparison, advisory=False) == 1
    assert "did not run" in compare_baseline.render_github(comparison)[0]


def test_a_nonzero_harness_exit_raises_harness_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(harness_run, "main", lambda *_: 1)

    with pytest.raises(compare_baseline.HarnessError):
        compare_baseline.regenerate_report_bytes()


def test_main_never_fails_under_advisory_even_when_the_harness_cannot_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(harness_run, "main", lambda *_: 1)

    code = compare_baseline.main(["--advisory", "--format", "github"])

    assert code == 0
    assert "did not run" in capsys.readouterr().out


def test_main_reports_holds_when_the_harness_reproduces_the_baseline(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(compare_baseline, "regenerate_report_bytes", BASELINE_REPORT.read_bytes)

    code = compare_baseline.main([])

    assert code == 0
    assert "holds" in capsys.readouterr().out
