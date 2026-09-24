"""The LOCAL half of ADR-0036 slice S4's ratchet (Phase A, Compliance).

The committed baseline (``tools/eval/baseline/report.json``) must reproduce
byte-for-byte from a fresh run over the same tree -- otherwise it silently
drifts from what the harness now produces, and nobody notices until a human
happens to diff the two by hand. This is the LOCAL half only: it compares the
committed baseline against *this checkout*, at whatever commit is checked
out. The CROSS-COMMIT half -- comparing a pull request's regenerated report
against the baseline committed on ``main``, so a ranking change gets an
advisory CI comment -- is a later assignment; nothing here drives CI.

``tools/eval`` is a flat script directory, not a package: this file puts it
on ``sys.path`` itself, the way ``test_harness_pins.py`` beside it does.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.eval]

REPO_ROOT = Path(__file__).resolve().parents[3]
_HARNESS_DIR = REPO_ROOT / "tools" / "eval"
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

import run as harness_run  # noqa: E402

CORPUS = REPO_ROOT / "tests" / "fixtures" / "eval"
BASELINE_REPORT = _HARNESS_DIR / "baseline" / "report.json"


def test_a_fresh_run_over_the_frozen_corpus_reproduces_the_committed_baseline_report() -> None:
    """Regenerates ``report.json`` over the committed S3 corpus and asserts it
    is byte-identical to ``tools/eval/baseline/report.json``.

    Relies on the same determinism decision 5 and 7 pin
    (``test_two_consecutive_harness_runs_over_the_smoke_corpus_produce_a_byte_identical_report``)
    already covers: at one machine, one interpreter, one SQLite build, a run
    over unchanged corpus and code reproduces its own prior output. A red here
    means either the corpus or the harness moved since the baseline was
    committed, and the baseline needs a recorded re-measurement -- not that
    this test is wrong.
    """
    with tempfile.TemporaryDirectory(prefix="theurian-eval-baseline-check-") as out_name:
        code = harness_run.main(["--corpus", str(CORPUS), "--out", out_name])
        assert code == 0
        regenerated = (Path(out_name) / "report.json").read_bytes()

    assert regenerated == BASELINE_REPORT.read_bytes()


def test_the_documented_cli_command_reproduces_the_committed_baseline_report() -> None:
    """The instrument-equivalence pin the baseline README's own method block leans on.

    ``tools/eval/baseline/README.md``'s "Instrument" section documents
    ``uv run python tools/eval/run.py --corpus tests/fixtures/eval --out <dir>``
    as the canonical reproduction, and asserts that "the determinism pin ...
    is what makes the two paths equivalent" -- but what actually produced the
    baseline, and what the sibling pin above drives, is the in-process
    ``run.main()`` call; nothing has ever driven the DOCUMENTED subprocess
    form and shown it produces the same bytes. This drives the real
    subprocess (the same script, corpus and output flags as the README's
    ``$`` line, without its ``uv run`` wrapper -- see that README's own reach
    statement) and pins its output against the same committed baseline --
    naming the instrument on both sides rather than leaving the equivalence
    asserted prose.
    """
    with tempfile.TemporaryDirectory(prefix="theurian-eval-cli-baseline-check-") as out_name:
        result = subprocess.run(  # noqa: S603 - argv is module-owned, never user input
            [
                sys.executable,
                "tools/eval/run.py",
                "--corpus",
                "tests/fixtures/eval",
                "--out",
                out_name,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        regenerated = (Path(out_name) / "report.json").read_bytes()

    assert regenerated == BASELINE_REPORT.read_bytes()
