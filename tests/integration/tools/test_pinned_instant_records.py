"""``run.PINNED_NOW`` against the records that restate it and its reason (ADR-0036).

The baseline README's "The pinned instant" section copies the value, and
``run.py`` and the README give as its reason the instant #798 measured the
baseline at. Each is held here, so moving ``PINNED_NOW`` alone goes RED.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Final

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
_HARNESS_DIR = REPO_ROOT / "tools" / "eval"
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

import run as harness_run  # noqa: E402

BASELINE_README = _HARNESS_DIR / "baseline" / "README.md"

#: Key: every backtick-delimited ``PINNED_NOW = <value>`` span in the README,
#: fenced or not, case-sensitive. A restatement spelled any other way is not read.
_README_RESTATEMENT: Final = re.compile(r"`PINNED_NOW = ([^`]+)`")

#: The ``date`` ``git show 60d10164:tools/eval/baseline/timings.json`` prints.
#: A literal rather than a git read, because a CI checkout may be shallow; and
#: not the working-tree ``timings.json``, whose ``date`` every re-measurement
#: rewrites while the reason stays true.
BASELINE_798_MEASURED_AT: Final = datetime.fromisoformat("2026-09-24T07:13:08.322675+00:00")


def test_the_baseline_readme_states_the_harness_pinned_instant() -> None:
    stated = _README_RESTATEMENT.findall(BASELINE_README.read_text(encoding="utf-8"))

    assert stated
    assert {datetime.fromisoformat(value) for value in stated} == {harness_run.PINNED_NOW}, stated


def test_the_pinned_instant_is_the_instant_798s_baseline_was_measured_at() -> None:
    assert BASELINE_798_MEASURED_AT.replace(microsecond=0) == harness_run.PINNED_NOW, (
        "PINNED_NOW no longer equals the instant #798's baseline was measured at; update "
        "the reason in tools/eval/run.py and the baseline README's 'The pinned instant', "
        "and this constant, together."
    )
