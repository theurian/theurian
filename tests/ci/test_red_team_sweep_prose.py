"""The sweep section's prose, pinned against the sources it describes (#378).

`docs/contributing/orchestration.md`'s "The async red-team sweep" section states
operational facts: when the nightly job runs, how many mutations it spends, what
its two standing alarm threads are called, which label both its producers file
under, what heading closes a filed body, what the census excludes, and which
release-ritual step the second leg is anchored to. Every one of those is a value
that lives somewhere else and can move without the sentence moving with it.

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
is read out of the invocation and spelled as a word, and the titles, the label
and the automation heading are taken from the constants themselves. No *value*
here is restated from the document; a rule that restated one would agree with
the document and with nothing else. The single exception is deliberate and
marked as such -- `RELEASE_CUT_ITEM` is not a value but a sentence two
documents have to share, and it has no third source to be derived from.

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

#: The checklist the release-cut item is an item *of*. Its Core and plugin
#: halves are bolded leads inside one `##` section rather than headings of their
#: own, so `under=` cannot separate them the way it separates the two
#: `### 1. Prepare` sections -- the plugin half is cut off by its lead instead.
RELEASE_CHECKLIST = "## Release checklist"
PLUGIN_CHECKLIST_LEAD = "**Plugin**"

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


def _visible(text: str) -> str:
    """The document with its HTML comments removed.

    Markdown hides `<!-- ... -->` from the reader while leaving every byte in the
    file, so a section commented out wholesale still satisfies any rule that
    greps the raw text -- the adversarial round wrapped this one and watched
    every pin pass while the published page carried nothing. Stripping first
    means a hidden section reaches the heading-not-found assertion instead,
    which is the honest failure: the claims are not on the page.

    The `\\Z` alternative carries the other half. CommonMark ends a comment at
    `-->` **or at end of document**, so one unclosed `<!--` line above the
    section hides every byte after it from every rendered reader -- and
    `orchestration.md` contains no `-->` anywhere to re-close it. A pattern
    that required the closing delimiter stripped nothing in that case and left
    the section, and so every pin below it, green against an invisible page.
    """
    return re.sub(r"<!--.*?(?:-->|\Z)", "", text, flags=re.DOTALL)


def _flat(text: str) -> str:
    """One line, single-spaced.

    Markdown joins a single newline into a space, so the sentence a reader sees
    is not the bytes on disk -- "at most six\\nmutations" reads as one phrase and
    has to be matched as one. Every rule compares against what the reader gets.
    """
    return " ".join(text.split())


def _section_lines(path: pathlib.Path, anchor: str, *, under: str | None = None) -> list[str]:
    """The visible lines under ``anchor``, optionally only the one below ``under``.

    ``under`` is for documents that repeat a heading. `release.md` has two
    `### 1. Prepare` sections, and a rule that accepted either would let the
    plugin release satisfy a claim about the Core one.
    """
    lines = _visible(path.read_text(encoding="utf-8")).splitlines()
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
    return body


def _section_of(path: pathlib.Path, anchor: str, *, under: str | None = None) -> str:
    """:func:`_section_lines`, flattened to the one line a reader sees."""
    return _flat("\n".join(_section_lines(path, anchor, under=under)))


def _core_release_checklist_items() -> list[str]:
    """`release.md`'s Core checklist, one flattened string per `- [ ]` item.

    Three things the raw whole-file read this replaces could not tell apart, all
    of which leave the citing section describing a gate nobody is held to: the
    item moved down into the plugin checklist, the item demoted from a checkbox
    to a prose aside, and the item commented out. Items are cut at the plugin
    lead and returned individually so each of those is a miss rather than a hit
    somewhere else in the file.
    """
    lines = _section_lines(RELEASE_DOC, RELEASE_CHECKLIST)
    leads = [i for i, line in enumerate(lines) if line.startswith(PLUGIN_CHECKLIST_LEAD)]
    assert len(leads) == 1, (
        f"expected one {PLUGIN_CHECKLIST_LEAD!r} lead splitting {RELEASE_CHECKLIST!r} into its "
        f"Core and plugin halves, found {len(leads)}; without it the Core half cannot be told "
        "from the plugin one and this rule would accept either"
    )

    items: list[str] = []
    for line in lines[: leads[0]]:
        if line.startswith("- [ ] "):
            items.append(line)
        elif items and line.startswith(" "):
            items[-1] += " " + line
    assert items, f"the Core half of {RELEASE_CHECKLIST!r} carries no checklist items at all"
    return [_flat(item) for item in items]


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


def test_a_commented_out_section_is_invisible_to_every_rule_in_this_module() -> None:
    """Every rule below greps the section, and raw bytes survive being commented.

    The adversarial round wrapped this section in `<!-- -->` and watched all of
    them pass while the published page carried nothing.
    """
    assert "hidden" not in _visible("before <!-- hidden --> after")


def test_an_unclosed_comment_hides_every_byte_after_it_as_well() -> None:
    """CommonMark ends a comment at `-->` *or* at end of document.

    The second half of that rule is what a pattern requiring the closing
    delimiter misses, and `orchestration.md` carries no `-->` anywhere to
    re-close one: a single `<!--` line above the heading hid the section from
    every rendered reader while every pin below it stayed green.
    """
    assert "hidden" not in _visible("<!--\nhidden")


def test_both_documents_carry_the_release_cut_step_word_for_word() -> None:
    """A citation is only worth anything if the cited document carries the step.

    The section tells a reader that the release ritual gates on the sweep's
    release-cut pass, and points at `release.md` for it. If that checklist item
    is reworded or dropped, the section goes on describing a gate that nobody is
    held to -- and the failure is silent in the direction that matters, because
    the release still ships and the tracker still looks quiet.

    Each direction is one assertion against the same shared key, and each is
    scoped to where the claim has to live: the Core checklist for the item, the
    sweep section for the citation. A raw whole-file read stood on the release
    side and accepted the sentence anywhere in `release.md` -- the plugin
    checklist, a prose aside, or an HTML comment all satisfied it.
    """
    assert any(RELEASE_CUT_ITEM in item for item in _core_release_checklist_items())
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
    assert len(schedule) == 1, (
        f"{WORKFLOW} carries {len(schedule)} cron entries; the section describes one nightly "
        "run, and its budget arithmetic and its clearance of security.yml are both written "
        "against one. Rewrite the section rather than re-deriving from the first entry"
    )
    minute, hour, day, month, weekday = str(schedule[0]["cron"]).split()
    assert (day, month, weekday) == ("*", "*", "*"), (
        f"the cron {schedule[0]['cron']!r} is not daily, so the section's wording needs "
        "rewriting rather than re-deriving"
    )

    assert f"at {int(hour):02d}:{int(minute):02d} UTC daily" in _section()


def test_the_section_states_the_mutation_budget_the_workflow_actually_passes() -> None:
    """The invocation gate pins that `--max-mutations` is present, not what it says.

    The number is the sweep's whole cost model -- six mutations plus the control
    is the seven full suite walks the section counts, and the job's
    `timeout-minutes` is set against that arithmetic. Raising the budget without
    touching the prose leaves a documented bound that bounds nothing.

    The ceiling is referred to and not quoted. This docstring used to quote a
    figure the workflow had already moved past -- the failure the module exists
    to catch, one level in. A number written into prose beside the source that
    owns it goes stale exactly as quietly, including here.
    """
    run = _sweep_step_run()

    found = re.search(r"--max-mutations\s+(\d+)", run)
    assert found is not None, f"the driver is invoked without --max-mutations:\n{run}"
    budget = int(found.group(1))
    assert budget in _SPELLED, f"a budget of {budget} needs the section reworded, not re-derived"

    assert f"at most {_SPELLED[budget]} mutations" in _section()


def test_the_section_names_the_heading_every_filed_body_ends_with() -> None:
    """The ratchet's own anchor, and the fifth constant the section quotes.

    "Every filed body ends with a 'Proposed automation' heading" is what tells a
    triager where the closing obligation is written down. Renaming
    `AUTOMATION_HEADING` left every rule here green while the section went on
    naming a heading no issue carries -- and the reader sent looking for it finds
    a body that appears to have no obligation attached at all.

    The leading hashes are stripped because the constant is markdown syntax and
    the section quotes the heading's text.
    """
    heading = _sweep_filing().AUTOMATION_HEADING.lstrip("# ")

    assert heading in _section()


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
