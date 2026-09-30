"""``run.PINNED_NOW`` against the two records that restate it (ADR-0036).

The baseline README's "The pinned instant" section copies the value, and gives
as its reason that it is the ``date`` the committed ``timings.json`` recorded.
Both copies drift from their source silently, so each is recomputed here.
"""

from __future__ import annotations

import json
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
BASELINE_TIMINGS = _HARNESS_DIR / "baseline" / "timings.json"

#: Key: every backtick-delimited ``PINNED_NOW = <value>`` span in the README,
#: fenced or not, case-sensitive. A restatement spelled any other way is not read.
_README_RESTATEMENT: Final = re.compile(r"`PINNED_NOW = ([^`]+)`")


def test_the_baseline_readme_states_the_harness_pinned_instant() -> None:
    stated = _README_RESTATEMENT.findall(BASELINE_README.read_text(encoding="utf-8"))

    assert stated
    assert {datetime.fromisoformat(value) for value in stated} == {harness_run.PINNED_NOW}, stated


def test_the_pinned_instant_is_the_committed_baseline_timings_date() -> None:
    """RED when the baseline is re-measured: the reason ``run.py``'s comment and
    the README give for ``PINNED_NOW`` then names a date the committed
    ``timings.json`` no longer carries."""
    recorded = datetime.fromisoformat(
        json.loads(BASELINE_TIMINGS.read_text(encoding="utf-8"))["date"]
    )

    assert recorded.replace(microsecond=0) == harness_run.PINNED_NOW
