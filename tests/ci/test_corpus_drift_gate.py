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


def _document() -> dict[Any, Any]:
    return cast(dict[Any, Any], yaml.safe_load(SHARED.read_text(encoding="utf-8")))


def _job() -> dict[str, Any]:
    return cast(dict[str, Any], _document()["jobs"]["corpus-drift"])


def _drift_step() -> dict[str, Any]:
    steps = [s for s in _job()["steps"] if "corpus_drift.py" in str(s.get("run", ""))]
    assert len(steps) == 1
    return cast(dict[str, Any], steps[0])


def test_the_drift_step_is_advisory_only_on_pull_request() -> None:
    step = _drift_step()

    assert step["env"] == {"DRIFT_FLAGS": FLAGS}
    assert step["run"] == RUN


def test_the_drift_step_carries_no_key_that_could_skip_or_swallow_it() -> None:
    """An `if:` or `continue-on-error` on the step skips or swallows a push run."""
    assert set(_drift_step()) == {"name", "env", "run"}


def test_the_job_runs_on_every_push_and_on_a_pull_request_only_behind_the_filter() -> None:
    assert _job()["if"] == JOB_IF


def test_the_job_does_not_swallow_its_exit_code() -> None:
    assert "continue-on-error" not in _job()


def test_a_push_to_main_still_triggers_the_workflow_and_reaches_the_job() -> None:
    """A skipped `changes` job skips `corpus-drift`; `on:` parses as the key `True`."""
    document = _document()

    assert document[True]["push"]["branches"] == ["main"]
    assert _job()["needs"] == "changes"
    assert "if" not in document["jobs"]["changes"]
