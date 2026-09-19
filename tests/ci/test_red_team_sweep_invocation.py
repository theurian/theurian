"""The scheduled sweep has to actually sweep, and nothing else checks that (#378).

`tools/sweep.py` is built so that a run which cannot answer says so loudly:
an untrusted harness files, a missing record fails the run, a substituted
harness is announced on every path. Every one of those guards is inside the
driver, and every one of them is bypassed by a word added to the invocation.

`--dry-run` is the whole failure in one flag. Added to the workflow's run step
the driver still selects a block, still generates mutations, still runs up to
seven full-suite walks, still builds the payload -- and then prints it and exits
0.
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
the flags the run needs and the flags it must not have -- not the whole
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

#: Flags without which a run cannot be reproduced or read: the date decides the
#: block, the block size decides the cost, and the record is the only evidence
#: an artifact can carry.
REQUIRED_FLAGS = ("--date", "--block-size", "--json")

#: Flags that turn the night into a rehearsal while leaving it green.
FORBIDDEN_FLAGS = ("--dry-run", "--mutate-cmd")

#: The workflow expression naming the commit the job checked out, with its
#: whitespace normalised away so `${{github.sha}}` and `${{ github.sha }}` are
#: the same answer. Nothing else identifies the tree the night ran against:
#: `github.ref` is a moving branch and a literal is stale the day it is typed.
COMMIT_EXPRESSION = "${{github.sha}}"


def _squeezed(text: str) -> str:
    return "".join(text.split())


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
def test_the_run_is_invoked_with_the_arguments_it_cannot_work_without(flag: str) -> None:
    """Three flags, three different things that break without them.

    Without `--date` the driver refuses outright. Without `--block-size` it falls
    back to its own default, which is currently the same six but is not the
    workflow's decision to inherit silently -- the walk budget, and so the job's
    `timeout-minutes`, is one control plus one walk per block member. Without
    `--json` the driver refuses, and the artifact the alarm reads would be empty.
    """
    assert flag in str(_sweep_step()["run"])


def test_the_sweep_step_passes_the_commit_it_ran_against() -> None:
    """A run with no `--commit` files a reproduction instruction that is false.

    The block is a function of the date, the census SIZE and each file's own
    contents -- not the date alone. `sweep_filing.py` records the measurement:
    over one week of this repository's growth, 130 modules to 139, **0 of 30
    dates resolved to the same file**. So an issue whose Reproduce block says
    only "run it with this date" sends the next reader at a different file
    within days of being filed, and the surviving mutation it describes cannot
    be reproduced at all. The driver renders `git checkout <sha>` when it is
    given one, and silently cannot when it is not.

    Two properties, because the flag alone is not the claim. It must carry
    `github.sha` -- the tree this job actually checked out -- and it must arrive
    through the environment rather than being interpolated into the script,
    which is the same discipline the dispatch date is held to. `github.sha` is
    server-side metadata and not attacker-shaped; the rule is here so that the
    trusted and the untrusted value cannot be told apart by how they are
    written, which is what makes an unsafe one visible when it appears.
    """
    step = _sweep_step()
    run = str(step["run"])
    env = cast(dict[str, Any], step.get("env", {}))

    carriers = [name for name, value in env.items() if _squeezed(str(value)) == COMMIT_EXPRESSION]
    assert carriers, (
        f"no environment variable on the sweep step holds {COMMIT_EXPRESSION}, so the "
        f"filed issue cannot name the tree it ran against; the step's env is {sorted(env)}"
    )
    assert COMMIT_EXPRESSION not in _squeezed(run), (
        f"{COMMIT_EXPRESSION} is interpolated straight into the run script. Pass it "
        "through the environment like the dispatch date, so that no value in this "
        "step has to be trusted by inspection."
    )
    accepted = sorted(
        {form for name in carriers for form in (f'--commit "${name}"', f'--commit "${{{name}}}"')}
    )
    assert any(form in run for form in accepted), (
        "the sweep step does not pass --commit from the environment variable holding "
        f"{COMMIT_EXPRESSION}, so the night files a Reproduce block with no `git "
        f"checkout` line. Accepted forms: {accepted}"
    )


@pytest.mark.parametrize("flag", FORBIDDEN_FLAGS)
def test_the_run_is_not_quietly_turned_into_a_rehearsal(flag: str) -> None:
    """The defect this file exists for, one flag at a time.

    Both leave a green workflow that has stopped doing its job, and neither
    produces a failure, a warning, or an artifact anybody would look at twice.
    `--dry-run` stops the filing; `--mutate-cmd` stops the verdicts being about
    this repository.
    """
    assert flag not in str(_sweep_step()["run"])
