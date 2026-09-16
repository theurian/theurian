"""The sweep section's prose, pinned against the sources it describes (#378).

`docs/contributing/orchestration.md`'s "The async red-team sweep" section states
operational facts: when the nightly job runs, how many mutations it spends, what
its two standing alarm threads are called, and which label its filings land
under. Every one of those is a value that lives somewhere else and can move
without the sentence moving with it.

**Why this file and not the invocation gate.** `test_red_team_sweep_invocation.py`
answers "is the workflow still sweeping" -- its subject is one file, its failure
means the job stopped doing its work, and the remedy is a workflow edit. This
file answers "does the written record still match what runs" -- its subject is
three files, its failure means a reader is being told something false, and the
remedy is usually a prose edit by whoever moved the value. Different subject,
different reader, different fix, so a separate module rather than a second
mandate bolted onto the first.

**What is enforced, exactly.** Each rule recomputes the expected phrasing from
the live source and looks for it in the section: the cron expression is parsed
and rendered back into the doc's own "HH:MM UTC daily" form, the mutation budget
is read out of the invocation and spelled as a word, and the two titles and the
label are taken from the constants themselves. Nothing here restates a literal
that the doc also states; a rule that did would agree with the doc and with
nothing else.

**What is not enforced.** That any night actually ran. Every rule here passes
against a workflow whose schedule fires and a workflow whose schedule is
disabled at the repository level, and passes on a tracker with no issues in it
because there were no runs. The section says as much in its own last paragraph,
and the residual gap -- a timeout cancellation, which `failure()` does not fire
on -- is [#724](https://github.com/theurian/theurian/issues/724). This file
narrows the section's *claims*, not the sweep's *liveness*.
"""

from __future__ import annotations

import pathlib
import re
import sys
from types import ModuleType
from typing import Any, cast

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC = REPO_ROOT / "docs" / "contributing" / "orchestration.md"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "red-team.yml"

#: The section this file answers for. A heading rather than a line number, so
#: the anchor survives every edit above it.
ANCHOR = "### The async red-team sweep"

#: Small integers as the doc spells them. Only the range a mutation budget can
#: plausibly take: a value outside it fails loudly, which is correct, because a
#: budget of twelve needs the sentence reworded and not just re-derived.
_SPELLED: dict[int, str] = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
}


def _section() -> str:
    """The section's text, with its line wrapping flattened.

    Flattened because markdown joins a single newline into a space, so the
    rendered sentence a reader sees is not the bytes on disk -- "at most six\\n
    mutations" reads as one phrase and must be matched as one. Every rule below
    compares against what the reader gets.
    """
    lines = DOC.read_text(encoding="utf-8").splitlines()
    assert ANCHOR in lines, f"{DOC} no longer carries the anchor {ANCHOR!r}"
    start = lines.index(ANCHOR) + 1
    body: list[str] = []
    for line in lines[start:]:
        if re.match(r"^#{1,6}\s", line):
            break
        body.append(line)
    assert body, f"the section under {ANCHOR!r} is empty"
    return " ".join(" ".join(body).split())


def _workflow() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))


def _triggers(document: dict[str, Any]) -> dict[str, Any]:
    """The `on:` block.

    YAML 1.1 reads a bare `on` as the boolean true, which is what `yaml.safe_load`
    implements, so the key is `True` and not `"on"`. Both are accepted here so
    that a future loader on YAML 1.2 rules does not turn this into a failure
    about nothing.
    """
    for key in (True, "on"):
        if key in document:
            return cast(dict[str, Any], document[key])
    raise AssertionError(f"{WORKFLOW} has no `on:` block at all")


def _sweep_step_run() -> str:
    """The shell of the one step that invokes the driver."""
    document = _workflow()
    runs = [
        str(step.get("run", ""))
        for job in cast(dict[str, Any], document["jobs"]).values()
        for step in job.get("steps", [])
        if "python tools/sweep.py" in str(step.get("run", ""))
    ]
    assert len(runs) == 1, f"expected exactly one step invoking the driver, found {len(runs)}"
    return runs[0]


def _alarm_title() -> str:
    """The title the workflow's own failure alarm files under."""
    document = _workflow()
    titles = [
        str(step["env"]["ALARM_TITLE"])
        for job in cast(dict[str, Any], document["jobs"]).values()
        for step in job.get("steps", [])
        if isinstance(step.get("env"), dict) and "ALARM_TITLE" in step["env"]
    ]
    assert len(titles) == 1, f"expected exactly one step carrying ALARM_TITLE, found {len(titles)}"
    return titles[0]


def _sweep_filing() -> ModuleType:
    """`tools/sweep_filing.py`, imported the way the tools tests import it.

    `tools/` is a flat script directory rather than a package, and no conftest
    puts it on the path for `tests/ci/`, so this file does it itself rather than
    depending on another directory's conftest having been imported first -- that
    ordering holds under a full run and breaks under `pytest tests/ci`.
    """
    tools = str(REPO_ROOT / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import sweep_filing

    return sweep_filing


def test_the_section_states_the_hour_the_workflow_is_actually_scheduled_for() -> None:
    """A schedule that moves silently is the section's own failure mode, one level up.

    Deleting the `schedule:` block entirely leaves every other test in this
    repository green: the workflow still parses, the invocation gate still finds
    its step, and nothing runs at night. The sentence "runs `tools/sweep.py` at
    01:17 UTC daily" then describes a job that never starts, which is exactly the
    reading the section warns about -- a quiet tracker that means no runs rather
    than no findings.

    The expected phrase is rendered back out of the cron expression rather than
    written down, so moving the cron moves what this looks for.
    """
    schedule = _triggers(_workflow()).get("schedule")

    assert schedule, f"{WORKFLOW} has no `schedule:`; the section says the sweep runs nightly"
    minute, hour, day, month, weekday = str(schedule[0]["cron"]).split()
    assert (day, month, weekday) == ("*", "*", "*"), (
        f"the cron {schedule[0]['cron']!r} is not daily, so the section's wording needs "
        "rewriting rather than re-deriving"
    )

    assert f"at {int(hour):02d}:{int(minute):02d} UTC daily" in _section()


def test_the_section_states_the_mutation_budget_the_workflow_actually_passes() -> None:
    """The invocation gate pins that `--max-mutations` is present, not what it says.

    The number is the sweep's whole cost model -- six mutations plus the control
    is seven full suite walks, which is what the job's 150-minute ceiling and the
    section's "a full suite walk each" are both written against. Raising it in
    the workflow without touching the prose leaves a documented budget that no
    longer bounds anything.
    """
    run = _sweep_step_run()

    found = re.search(r"--max-mutations\s+(\d+)", run)
    assert found is not None, f"the driver is invoked without --max-mutations:\n{run}"
    budget = int(found.group(1))
    assert budget in _SPELLED, f"a budget of {budget} needs the section reworded, not re-derived"

    assert f"at most {_SPELLED[budget]} mutations" in _section()


def test_the_section_quotes_the_standing_thread_title_the_driver_files_under() -> None:
    """A thread title is a search key, and the doc is where a triager gets it.

    `sweep_filing.UNTRUSTED_TITLE` is constant precisely so that every untrusted
    night lands on one thread. Renaming it splits that thread -- and a reader
    searching the tracker for the title the doc quotes finds the old thread,
    which stopped collecting, and reads its silence as nothing having gone wrong.
    """
    assert _sweep_filing().UNTRUSTED_TITLE in _section()


def test_the_section_quotes_the_title_the_workflows_own_alarm_files_under() -> None:
    """The other thread, owned by the workflow rather than by the driver.

    These two are deliberately different threads: one collects nights the driver
    ran and could not stand behind, the other collects nights the job died before
    the driver could file. A reader who is given the wrong name for either cannot
    tell those apart, and they have opposite remedies.
    """
    assert _alarm_title() in _section()


def test_the_section_names_the_label_both_producers_file_under() -> None:
    """The label is what makes the two producers one queue for triage.

    Matched with its backticks, which is what distinguishes the label from the
    alarm title that begins with the same characters -- ``async-sweep:`` inside a
    quoted title would satisfy a bare substring check even with every mention of
    the label itself deleted.
    """
    label = _sweep_filing().LABEL

    assert f"`{label}`" in _section()
