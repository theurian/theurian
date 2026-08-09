"""Every job in every workflow carries an explicit `timeout-minutes` (#104).

Why this file exists
---------------------
Before #104, none of the 24 job blocks across `.github/workflows/*.yml`
carried a job-level `timeout-minutes`, so a hung job fell back to GitHub's
360-minute default -- six hours of billed runner time for one silent
regression, reported as a red X with no failing assertion to read. The
repository runs no actionlint, zizmor or yamllint in CI (see
`tests/release/test_release_publication_guards.py`'s own docstring for the
same gap against `release-core.yml` specifically), so nothing else notices a
25th job landing without one. This file is that notice.

What is asserted, and what is not
-----------------------------------
Presence only, not value. Whether ten minutes or thirty is the right ceiling
for a given job is a judgement call made once, from that job's observed run
history, and recorded in the pull request that set it (#109) -- pinning a
specific number here would fail every time an honestly-changed runtime
justifies a different one, for no safety this file exists to buy. What must
never regress is silence: a job with no `timeout-minutes` key at all is
exactly the shape of the defect #104 reported, whether it is job 1 or job 25.

A job whose top level carries `uses:` instead of `runs-on:` calls a reusable
workflow, and GitHub does not accept `timeout-minutes` on that caller job --
the timeout has to live inside the reusable workflow's own job definitions
instead. No such job exists in this repository today (confirmed by this
file's own `test_no_job_currently_calls_a_reusable_workflow`), so the
exemption below is currently untested by omission; it is here so that the
day one is added, this file fails on the *reusable workflow's* jobs rather
than refusing to parse the caller.
"""

from __future__ import annotations

import pathlib
from typing import Any, cast

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"


def _workflow_files() -> list[pathlib.Path]:
    paths = sorted(WORKFLOWS_DIR.glob("*.yml"))
    assert paths, f"no workflow files found under {WORKFLOWS_DIR}; is the glob still correct?"
    return paths


def _jobs(path: pathlib.Path) -> dict[str, dict[str, Any]]:
    document = cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))
    jobs = document.get("jobs", {})
    assert isinstance(jobs, dict), f"{path.name}: 'jobs:' did not parse as a mapping"
    return cast(dict[str, dict[str, Any]], jobs)


@pytest.mark.parametrize("workflow", _workflow_files(), ids=lambda p: p.name)
def test_every_job_carries_an_explicit_timeout(workflow: pathlib.Path) -> None:
    """A job with no `timeout-minutes` inherits GitHub's 360-minute default --
    the exact shape of #104. A job calling a reusable workflow (`uses:` at the
    job level, no `runs-on:`) is exempt: GitHub does not accept
    `timeout-minutes` there, and the reusable workflow's own jobs carry it
    instead.
    """
    missing = [
        job_id
        for job_id, job in _jobs(workflow).items()
        if "runs-on" in job and "timeout-minutes" not in job
    ]

    assert missing == [], (
        f"{workflow.name}: job(s) with no timeout-minutes, so a hang falls back "
        f"to GitHub's 360-minute default: {missing}. Size one from that job's "
        "observed run history (roughly 3-4x p95, minimum 10) -- see #104 and #109."
    )


def test_no_job_currently_calls_a_reusable_workflow() -> None:
    """Documents the boundary the exemption above relies on. If this starts
    failing, a job now has `uses:` instead of `runs-on:`, and the timeout for
    it belongs in the called workflow's own job definitions, not here.
    """
    reusable_callers = [
        f"{workflow.name}:{job_id}"
        for workflow in _workflow_files()
        for job_id, job in _jobs(workflow).items()
        if "uses" in job
    ]

    assert reusable_callers == []
