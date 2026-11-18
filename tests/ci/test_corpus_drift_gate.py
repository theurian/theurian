"""The `corpus-drift` job blocks on push to main and stays advisory on PRs (#971).

DoR AC6 of #971, under the ruling's option (d): the checker ran `--advisory`
everywhere, so drift on main was never red. The step now passes `--advisory`
only on `pull_request`, and the job runs on every `push` whatever the paths
filter says -- the measured reason is in the rationale block above the
`corpus` filter and the job in `shared.yml`.

The condition is pinned as exact strings, not evaluated: an equivalent rewrite
must update this pin.
"""

from __future__ import annotations

import pathlib
from typing import Any, cast

import pytest
import yaml

pytestmark = pytest.mark.unit

SHARED = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "shared.yml"

JOB_IF = "github.event_name != 'pull_request' || needs.changes.outputs.corpus == 'true'"
FLAGS = "${{ github.event_name == 'pull_request' && '--advisory' || '' }}"
RUN = "uv run python tools/corpus_drift.py $DRIFT_FLAGS --format github --summary"


def _job() -> dict[str, Any]:
    document = cast(dict[str, Any], yaml.safe_load(SHARED.read_text(encoding="utf-8")))
    return cast(dict[str, Any], document["jobs"]["corpus-drift"])


def _drift_step() -> dict[str, Any]:
    steps = [s for s in _job()["steps"] if "corpus_drift.py" in str(s.get("run", ""))]
    assert len(steps) == 1
    return cast(dict[str, Any], steps[0])


def test_the_drift_step_is_advisory_only_on_pull_request() -> None:
    step = _drift_step()

    assert step["env"] == {"DRIFT_FLAGS": FLAGS}
    assert step["run"] == RUN
    assert "--advisory" not in step["run"]


def test_the_job_runs_on_every_push_and_on_a_pull_request_only_behind_the_filter() -> None:
    assert _job()["if"] == JOB_IF


def test_nothing_swallows_the_drift_exit_code() -> None:
    job, step = _job(), _drift_step()

    assert "continue-on-error" not in job
    assert "continue-on-error" not in step
    assert "||" not in step["run"]
