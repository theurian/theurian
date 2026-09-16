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
from pathlib import PurePosixPath
from types import ModuleType
from typing import Any, cast

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC = REPO_ROOT / "docs" / "contributing" / "orchestration.md"
RELEASE_DOC = REPO_ROOT / "docs" / "contributing" / "release.md"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "red-team.yml"

#: The section this file answers for. A heading rather than a line number, so
#: the anchor survives every edit above it.
ANCHOR = "### The async red-team sweep"

#: The one literal in this module, and a literal on purpose.
#:
#: Every other rule recomputes its expectation from a live source, because a
#: rule that restated a value would agree with the document and with nothing
#: else. This is not a value: it is a *sentence two documents have to share*.
#: `release.md`'s checklist owns the step and `orchestration.md` quotes it, and
#: the drift worth catching is a section citing a ritual step the ritual does
#: not carry. There is no third source to derive from, so the shared key is the
#: arbiter -- and rewording it in both documents is meant to require touching
#: this line.
RELEASE_CUT_ITEM = (
    "The async red-team sweep's release-cut pass has run over `origin/main` at the candidate commit"
)

#: `release.md` carries two `### 1. Prepare` headings: one under
#: `## Releasing Core`, one under `## Releasing the plugin`. The release-cut
#: pass belongs to the Core ritual, so the parent heading is part of the anchor.
#: "The string appears somewhere in the file" would be satisfied by the plugin
#: section, which is a different release on a different cadence.
CORE_PREPARE = "### 1. Prepare"
CORE_PREPARE_PARENT = "## Releasing Core"

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


def _flat(text: str) -> str:
    """One line, single-spaced.

    Markdown joins a single newline into a space, so the sentence a reader sees
    is not the bytes on disk -- "at most six\\nmutations" reads as one phrase and
    has to be matched as one. Every rule compares against what the reader gets.
    """
    return " ".join(text.split())


def _section_of(path: pathlib.Path, anchor: str, *, under: str | None = None) -> str:
    """The flattened body under ``anchor``, optionally only the one below ``under``.

    ``under`` is for documents that repeat a heading. `release.md` has two
    `### 1. Prepare` sections, and a rule that accepted either would let the
    plugin release satisfy a claim about the Core one.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    parent: str | None = None
    body: list[str] | None = None
    for line in lines:
        if body is None and re.match(r"^#{1,2}\s", line):
            parent = line
        if body is not None:
            if re.match(r"^#{1,6}\s", line):
                break
            body.append(line)
        elif line == anchor and (under is None or parent == under):
            body = []
    assert body is not None, f"{path.name} carries no {anchor!r}" + (
        f" under {under!r}" if under is not None else ""
    )
    assert any(line.strip() for line in body), f"the section under {anchor!r} is empty"
    return _flat("\n".join(body))


def _section() -> str:
    """The sweep section of `orchestration.md`."""
    return _section_of(DOC, ANCHOR)


def _paragraph(section: str, lead: str) -> str:
    """One bolded paragraph of the section, up to the next bolded lead.

    Scoping matters wherever the section states a fact more than once: a rule
    that searched the whole section would be satisfied by a different sentence
    making a different claim, and would stay green with the one it meant deleted.
    """
    assert lead in section, f"the section no longer carries the paragraph {lead!r}"
    rest = section[section.index(lead) + len(lead) :]
    following = re.search(r"\*\*[A-Z][^*]{0,80}\.\*\*", rest)
    return rest[: following.start()] if following else rest


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


def _tools_on_path() -> None:
    """Put `tools/` on `sys.path`.

    `tools/` is a flat script directory rather than a package, and no conftest
    puts it on the path for `tests/ci/`, so this file does it itself rather than
    depending on another directory's conftest having been imported first -- that
    ordering holds under a full run and breaks under `pytest tests/ci`.
    """
    tools = str(REPO_ROOT / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)


def _sweep_census() -> ModuleType:
    """`tools/sweep_census.py`, imported the way :func:`_sweep_filing` imports its own."""
    _tools_on_path()
    import sweep_census

    return sweep_census


def _alarm_labels() -> list[str]:
    """Every `--label` the workflow's own alarm step passes to `gh`.

    Read the way :func:`_alarm_title` reads its env: the alarm is the *second*
    producer filing under the sweep's label, and it names that label in shell
    text rather than importing a constant. Both of its `gh` calls are returned,
    because the list call and the create call can drift apart -- one finding no
    thread while the other opens one.
    """
    document = _workflow()
    runs = [
        str(step.get("run", ""))
        for job in cast(dict[str, Any], document["jobs"]).values()
        for step in job.get("steps", [])
        if isinstance(step.get("env"), dict) and "ALARM_TITLE" in step["env"]
    ]
    assert len(runs) == 1, f"expected exactly one alarm step, found {len(runs)}"
    labels = re.findall(r"--label\s+(\S+)", runs[0])
    assert labels, f"the alarm step passes no --label at all:\n{runs[0]}"
    return labels


def _sweep_filing() -> ModuleType:
    """`tools/sweep_filing.py`, imported the way the tools tests import it.

    `tools/` is a flat script directory rather than a package, and no conftest
    puts it on the path for `tests/ci/`, so this file does it itself rather than
    depending on another directory's conftest having been imported first -- that
    ordering holds under a full run and breaks under `pytest tests/ci`.
    """
    _tools_on_path()
    import sweep_filing

    return sweep_filing


def test_both_documents_carry_the_release_cut_step_word_for_word() -> None:
    """A citation is only worth anything if the cited document carries the step.

    The section tells a reader that the release ritual gates on the sweep's
    release-cut pass, and points at `release.md` for it. If that checklist item
    is reworded or dropped, the section goes on describing a gate that nobody is
    held to -- and the failure is silent in the direction that matters, because
    the release still ships and the tracker still looks quiet.

    Both directions are one assertion each against the same shared key, so
    deleting it from either document reddens this.
    """
    assert RELEASE_CUT_ITEM in _flat(RELEASE_DOC.read_text(encoding="utf-8"))
    assert RELEASE_CUT_ITEM in _section()


def test_the_core_prepare_step_names_the_agent_that_runs_the_pass() -> None:
    """The checklist says the pass has run; §1 is where it says who runs it.

    A checklist item with no step behind it is an instruction with no procedure,
    and whoever is cutting the tag has to invent one. The agent's name is the
    procedure -- it is what makes "has run" checkable rather than aspirational.

    Anchored under `## Releasing Core` rather than "somewhere in release.md":
    the plugin release has its own `### 1. Prepare`, and a rule that accepted
    either would stay green with the Core step deleted.
    """
    prepare = _section_of(RELEASE_DOC, CORE_PREPARE, under=CORE_PREPARE_PARENT)

    assert "theurian-adversarial-review" in prepare


#: Top-level directories the section names as outside the census. Each is
#: checked against the live census root rather than taken on trust.
OUTSIDE_THE_CENSUS = ("tools", "tests", "docs")


def test_the_section_names_directories_the_census_really_does_exclude() -> None:
    """The scope claim is the one sentence in the section that bounds the sweep.

    "`tools/`, `tests/` and `docs/` sit outside it entirely" is what tells a
    reader that a green nightly says nothing about the tooling, the suite or the
    documentation -- and it is true only because the census root is a path inside
    `packages/`. Widen that root and the sentence quietly inverts: the sweep
    would start sampling files the section promised it never touches, and the
    reader's model of what a clean night covers would be wrong in the direction
    that grants false comfort.

    Checked by asking the live root whether each directory is under it, not by
    comparing strings: a root of `""` or `"."` reads as the whole repository and
    is exactly the widening this catches. A widening that keeps all three
    outside -- to `packages/`, say -- stays green, and correctly so: what is
    pinned is the sentence's truth, not the root's exact value. Freezing the
    root string here instead would restate a literal and catch nothing the
    constant does not already say about itself.

    The round's measured figures in the same paragraph -- the census size, the
    barren count, the median wait -- are deliberately **not** pinned here. They
    are dated observations of one round against one tree, not invariants, and a
    rule that froze them would fail on every honest commit that adds a module.
    """
    root = PurePosixPath(_sweep_census().CENSUS_ROOT)
    section = _section()

    for name in OUTSIDE_THE_CENSUS:
        assert not PurePosixPath(name).is_relative_to(root), (
            f"{name}/ now sits inside the census root {root}; the section's scope "
            "sentence has become false and needs rewriting, not re-deriving"
        )
        assert f"`{name}/`" in section


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

    Anchored inside the "Where it lands" paragraph rather than the section as a
    whole. The section mentions the label twice, so a rule scoped to the whole
    section stays green with the sentence that actually makes the claim deleted
    -- it would be satisfied by the Ratchet paragraph describing how a finding
    closes, which is a different statement.

    Matched with its backticks, which is what separates the label from the alarm
    title beginning with the same characters: ``async-sweep:`` inside a quoted
    title satisfies a bare substring check with every real mention gone.
    """
    label = _sweep_filing().LABEL

    assert f"`{label}`" in _paragraph(_section(), "**Where it lands.**")


def test_the_workflows_own_alarm_files_under_the_same_label() -> None:
    """ "Both file under the `async-sweep` label" is a claim about two producers.

    Only one of them was checked. The driver's label is a constant this module
    reads; the alarm step's is shell text in the workflow, and changing it left
    every pin green -- the section would go on promising one queue while the
    nights the job died landed in another, invisible to a triager filtering on
    the label and to a reader of this file.

    Both of the alarm's `gh` calls are checked, because the list call and the
    create call can drift apart: one finds no thread, the other opens one, and
    the standing alarm thread silently becomes a new issue every failure.
    """
    label = _sweep_filing().LABEL

    assert _alarm_labels() == [label, label]
