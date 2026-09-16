"""The nightly sweep has to actually sweep, and nothing else checks that (#378).

`tools/sweep.py` is built so that a night which cannot answer says so loudly:
an untrusted harness files, a missing record fails the run, a substituted
harness is announced on every path. Every one of those guards is inside the
driver, and every one of them is bypassed by a word added to the invocation.

`--dry-run` is the whole failure in one flag. Added to the workflow's run step
the driver still selects a target, still generates mutations, still runs seven
full-suite walks, still builds the payload -- and then prints it and exits 0.
The workflow goes green. The artifact uploads. The alarm never fires, because
nothing failed. What stops is the filing, and the only evidence is an issue
that was never opened, which is not a thing anybody notices. `--mutate-cmd` is
the same shape one level down: the verdicts stop coming from this repository's
suite, and "0 findings" from a substituted harness reads exactly like the real
thing.

Neither is a hypothetical edit. Both are the natural thing to add while
debugging the workflow, and the natural thing to forget to remove.

This is #378's own ratchet: CLAUDE.md requires an adversarial or security
finding to propose its automation before it closes, and the automation for
"the invocation is unguarded" is a test that reads the invocation. It asserts
the flags the night needs and the flags it must not have -- not the whole
command, which would fail on every honest edit and teach the next person to
delete it.
"""

from __future__ import annotations

import pathlib
from typing import Any, cast

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "red-team.yml"

#: How the driver is *invoked*, not merely named. The alarm step at the end of
#: the workflow quotes `tools/sweep.py` in the issue body it writes, so matching
#: on the bare path selects two steps and the flag rules then read the wrong one.
DRIVER = "python tools/sweep.py"

#: Flags without which a night cannot be reproduced or read: the date decides
#: the target, the budget decides the cost, and the record is the only evidence
#: an artifact can carry.
REQUIRED_FLAGS = ("--date", "--max-mutations", "--json")

#: Flags that turn the night into a rehearsal while leaving it green.
FORBIDDEN_FLAGS = ("--dry-run", "--mutate-cmd")


def _sweep_step() -> dict[str, Any]:
    """The one step in the workflow that runs the driver."""
    document = cast(dict[str, Any], yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))
    steps = [
        step
        for job in cast(dict[str, Any], document["jobs"]).values()
        for step in job.get("steps", [])
        if DRIVER in str(step.get("run", ""))
    ]
    assert len(steps) == 1, f"expected exactly one step running {DRIVER}, found {len(steps)}"
    return cast(dict[str, Any], steps[0])


def test_the_workflow_still_has_a_step_that_runs_the_sweep() -> None:
    """The premise every other rule here depends on.

    A workflow that stopped invoking the driver -- renamed, commented out,
    replaced by a placeholder -- would satisfy "contains no `--dry-run`"
    perfectly, and would be the same silent stop by a shorter route.
    """
    assert DRIVER in str(_sweep_step()["run"])


@pytest.mark.parametrize("flag", REQUIRED_FLAGS)
def test_the_night_is_invoked_with_the_arguments_it_cannot_work_without(flag: str) -> None:
    """Three flags, three different things that break without them.

    Without `--date` the driver refuses outright. Without `--max-mutations` it
    falls back to its own default, which is currently the same six but is not
    the workflow's decision to inherit silently -- the two-hour budget and the
    job's timeout are set against that number. Without `--json` the driver
    refuses, and the artifact the alarm reads would be empty.
    """
    assert flag in str(_sweep_step()["run"])


@pytest.mark.parametrize("flag", FORBIDDEN_FLAGS)
def test_the_night_is_not_quietly_turned_into_a_rehearsal(flag: str) -> None:
    """The defect this file exists for, one flag at a time.

    Both leave a green workflow that has stopped doing its job, and neither
    produces a failure, a warning, or an artifact anybody would look at twice.
    `--dry-run` stops the filing; `--mutate-cmd` stops the verdicts being about
    this repository.
    """
    assert flag not in str(_sweep_step()["run"])
