"""The sweep section's prose, pinned against the sources it describes (#378).

`docs/contributing/orchestration.md`'s "The async red-team sweep" section states
operational facts: when the scheduled job runs, how many mutations it spends and
how many suite walks those cost,
what its two standing alarm threads are called, which label its filings land
under, what heading closes a filed body, where a deferred claim is recorded,
what the census excludes, and which release-ritual step the release-cut leg is
anchored to. Every one of those is a value that lives somewhere else and can
move without the sentence moving with it.

**Why this file and not the invocation gate.** `test_red_team_sweep_invocation.py`
answers "is the workflow still sweeping" -- its subject is one file, its failure
means the job stopped doing its work, and the remedy is a workflow edit. This
file answers "does the written record still match what runs" -- its subject is
every source one of those claims is made against, its failure means a reader is
being told something false, and the remedy is usually a prose edit by whoever
moved the value. Different subject,
different reader, different fix, so a separate module rather than a second
mandate bolted onto the first.

**What is enforced, exactly.** Each rule recomputes the expected phrasing from
the live source and looks for it in the section: the cron expression is parsed
and rendered back into the doc's own hour-and-day sentence, the mutation budget
is read out of the invocation and spelled as a word, and the titles, the label
and the automation heading are taken from the constants themselves. No *value*
here is restated from the document; a rule that restated one would agree with
the document and with nothing else. Two exceptions are deliberate and marked as
such -- `RELEASE_CUT_ITEM` and `DEFERRED_CLAIMS_GATHER` are not values but
sentences two documents have to share, and neither has a third source to be
derived from. Both are enumerated in `SHARED_SENTENCES`, which is what a third
one would have to survive rather than a sentence it has to be mentioned in: a
literal short of the text it arbitrates leaves the rules that grep for it green
over the part it dropped.

**What is not enforced.** That any run actually happened. Every rule here passes
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
from collections.abc import Callable
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

#: The template that offers the deferred-claims section to whoever starts a pull
#: request body from it -- the fact side every citation of that heading is
#: checked against. It is not how the heading reaches most bodies; the two
#: records below are.
PR_TEMPLATE = REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"

#: The two places the duty to record a deferred claim is written down: the
#: checklist the orchestrator runs a round from, and the blast-radius row for the
#: weight whose adversarial claim is the deferred one. A dispatcher gathers what
#: these two told an author to write, so a heading they name and the template
#: does not is a gather reading a section nobody was asked to fill.
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
REVIEW_CHECKLIST = "## REVIEW — running a round"
DEFERRING_ROW_LEAD = "Behaviour a trier runs, but no disclosure surface"

#: The section this file answers for. A heading rather than a line number, so
#: the anchor survives every edit above it.
ANCHOR = "### The async red-team sweep"

#: The module's first arbiter literal, and a literal on purpose.
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

#: The bolded lead of the paragraph each document carries the gather clause in.
RELEASE_GATHER_LEAD = "**The async red-team sweep's release-cut pass runs here too.**"
ORCHESTRATION_GATHER_LEAD = "**The release-cut pass.**"

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


def _raw_block(lines: list[str], flat: str) -> list[str]:
    """The document's own lines whose flattening is exactly ``flat``.

    :func:`_section_of` is where every other rule stops, and it flattens -- so it
    compares the sentence a reader is given and not the bytes a file carries.
    This is the other half, the block as one document wraps it, which is what
    makes two copies of a shared block comparable as bytes.

    The first match from a given start is the shortest window that flattens to
    ``flat``, so the blank lines around a block are excluded and the block is
    unique. Accepting a padded window would make more than one window out of
    every block, since a blank line flattens to nothing.
    """
    blocks: list[list[str]] = []
    for start, line in enumerate(lines):
        if not line.strip() or not flat.startswith(_flat(line)):
            continue
        for end in range(start + 1, len(lines) + 1):
            joined = _flat("\n".join(lines[start:end]))
            if joined == flat:
                blocks.append(lines[start:end])
                break
            if not flat.startswith(joined):
                break
    assert len(blocks) == 1, (
        f"expected one block of lines flattening to the shared clause, found {len(blocks)}"
    )
    return blocks[0]


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


def _release_gather_paragraph() -> str:
    """`release.md` §1's release-cut pass paragraph, one of the clause's two homes."""
    return _paragraph(
        _section_of(RELEASE_DOC, CORE_PREPARE, under=CORE_PREPARE_PARENT), RELEASE_GATHER_LEAD
    )


def _orchestration_gather_paragraph() -> str:
    """`orchestration.md`'s release-cut pass paragraph, the other one."""
    return _paragraph(_section(), ORCHESTRATION_GATHER_LEAD)


def _common_prefix(left: str, right: str) -> str:
    end = 0
    while end < min(len(left), len(right)) and left[end] == right[end]:
        end += 1
    return left[:end]


def _common_edges(left: str, right: str, sentence: str) -> tuple[str, str]:
    """What two copies of ``sentence`` still agree on immediately before and after it.

    Each region is checked to carry exactly one copy first: `partition` takes the
    first occurrence, so "the text after it" is ambiguous for a sentence that
    appears twice and meaningless for the degenerate empty one, which appears
    between every pair of characters.
    """
    for region in (left, right):
        assert region.count(sentence) == 1, (
            f"expected one copy of the shared sentence here, found {region.count(sentence)}"
        )
    before_left, _, after_left = left.partition(sentence)
    before_right, _, after_right = right.partition(sentence)
    return (
        _common_prefix(before_left[::-1], before_right[::-1])[::-1],
        _common_prefix(after_left, after_right),
    )


def _pr_template_headings() -> list[str]:
    """Every heading line of the pull-request template.

    Visible lines only, so a section commented out of the template counts as
    gone. The instruction comment under a heading is not part of it: what a
    document cites is the heading a contributor sees in their own pull request
    body.
    """
    lines = _visible(PR_TEMPLATE.read_text(encoding="utf-8")).splitlines()
    headings = [line.strip() for line in lines if re.match(r"^#{1,6}\s\S", line)]
    assert headings, f"{PR_TEMPLATE.name} carries no headings at all"
    return headings


def _deferring_row() -> str:
    """CLAUDE.md's blast-radius row for the weight that defers its adversarial claim.

    Found by its first cell rather than by position, so a reworded lead fails
    loudly instead of the rule quietly answering for whichever row has come to
    sit in the middle.
    """
    rows = [
        line
        for line in _visible(CLAUDE_MD.read_text(encoding="utf-8")).splitlines()
        if line.startswith("|") and line.split("|")[1].strip() == DEFERRING_ROW_LEAD
    ]
    assert len(rows) == 1, (
        f"expected one {CLAUDE_MD.name} table row led by {DEFERRING_ROW_LEAD!r}, found "
        f"{len(rows)}; it is the row whose deferred claim the release-cut pass collects"
    )
    return _flat(rows[0])


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


def _sweep_driver() -> ModuleType:
    """`tools/sweep.py`, for the one figure the section states that is arithmetic.

    The walk budget is not a constant anywhere: `sweep.walk_budget` computes it
    from the block size and the per-file mutation count, which is what keeps the
    workflow's `timeout-minutes` comment, this section and the code from drifting
    apart. Deriving it here rather than spelling it means the sentence is checked
    against the arithmetic and not against a number somebody typed twice.
    """
    _tools_on_path()
    import sweep

    return sweep


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


#: The module's second arbiter literal, and one for the same reason as the first.
#:
#: Not a value read back out of a live source: a *sentence two documents have to
#: share*. `release.md`'s §1 and `orchestration.md`'s release-cut pass paragraph
#: each instruct whoever dispatches that pass, and there is no third source
#: to derive the clause from -- so this literal is the arbiter, and rewording
#: either copy is meant to require touching it.
#:
#: Written at the documents' own wrap points, their blank lines aside, which
#: :func:`_flat` erases along with the rest -- so a reviewer reads this literal
#: against either page line for line.
DEFERRED_CLAIMS_GATHER = _flat("""
    Whoever dispatches the pass gathers the claims that the pull requests merged since
    the last `core-v*` tag deferred to it, recorded under each PR body's
    `## Deferred claims` heading with the command that re-checks each one:
    ```sh
    set -eu
    tag=$(git describe --abbrev=0 --match 'core-v*')
    since=$(git log -1 --format=%cI "$tag")
    gh pr list --state merged --limit 500 --json number,title,body --search "merged:>=$since"
    ```
    Each section is read with the template's HTML comments stripped: `None` is a pull
    request that deferred nothing and is skipped; a section that is missing or empty
    is a recording failure to raise with its author, not a second way of saying
    `None`. The dispatch brief hands the rest to `theurian-adversarial-review` as
    claims to attack. `%cI` gives GitHub the tag commit's exact instant, offset
    included; a bare date is read as UTC midnight instead, which at four of this
    repository's first 25 `core-v*` cuts dropped 10 merged pull requests out of the
    window. `--limit` is load-bearing (`gh pr list` returns 30 without it) and
    truncates silently, so a result of exactly 500 may mean the window was cut short —
    widen it and re-run. `set -eu` is what makes a tagless checkout fail loudly: the
    shell stops at `git describe`'s own exit 128, before `gh` runs. Without it that
    status is discarded by the assignment, `git log` fails the same way inside the
    next one, and the search key degenerates to `merged:>=` — which prints `[]` and
    exits 0, byte-identical to a window in which nothing merged. Quoting `"$tag"` is
    what makes that window empty rather than HEAD's own date.
""")


def test_both_documents_carry_the_deferred_claims_gather_clause_word_for_word() -> None:
    """The clause is the dispatcher's only instruction for what the pass attacks.

    Without it the pass attacks the tree rather than the claims deferred to it,
    and nothing downstream notices: it still runs, still files, still reads as a
    clean cut. One copy drifting -- a raised `--limit`, a rewritten search key --
    is the same failure with two dispatchers disagreeing about what they
    gathered ([#735](https://github.com/theurian/theurian/issues/735)).

    Each side is scoped to the paragraph a dispatcher is reading rather than to
    the section around it. Measured on the release side, where the rule used to
    be section-wide: the block moved to the top of `### 1. Prepare`, above the
    release-cut pass paragraph entirely, left it green. The release side is
    additionally anchored under `## Releasing Core`, since the plugin release has
    its own `### 1. Prepare` and a rule accepting either stays green with the
    Core copy gone.
    """
    prepare = _release_gather_paragraph()
    release_cut = _orchestration_gather_paragraph()

    assert DEFERRED_CLAIMS_GATHER in prepare
    assert DEFERRED_CLAIMS_GATHER in release_cut


def test_the_two_copies_of_the_gather_clause_are_the_same_bytes() -> None:
    """Flattened agreement is not agreement, and a re-wrap is how drift starts.

    `DEFERRED_CLAIMS_GATHER` is flattened, so the rule above passes on two copies
    whose line breaks differ: measured, moving one line break in `release.md`'s
    copy left every other rule in this module green with the two blocks no longer
    the same bytes. That is the state the next edit lands in -- it hits one
    document's wrap and not the other's, and the diff that would have shown the
    two copies parting company shows a re-wrap instead.

    Compared as each document's own lines, fence and blank lines included, with
    the block located by the shared literal.
    """
    prepare = _raw_block(
        _section_lines(RELEASE_DOC, CORE_PREPARE, under=CORE_PREPARE_PARENT),
        DEFERRED_CLAIMS_GATHER,
    )
    release_cut = _raw_block(_section_lines(DOC, ANCHOR), DEFERRED_CLAIMS_GATHER)

    assert prepare == release_cut


def test_both_documents_cite_a_record_heading_the_template_really_carries() -> None:
    """A gather is only as good as the carrier it names, and nothing read the carrier.

    The clause sends a dispatcher to a heading in each PR body. What puts it
    there is the recorded duty -- CLAUDE.md's blast-radius row and
    `orchestration.md`'s REVIEW checklist -- and not
    `.github/PULL_REQUEST_TEMPLATE.md`, which only reaches whoever starts from
    the template: measured in this PR's round, 0 of the last 60 merged bodies
    carry the heading, because a body written free-form never sees the template
    at all. The template is still the fact side every one of those citations is
    checked against, and until this rule nothing in the repository read it
    (`git grep -l PULL_REQUEST_TEMPLATE` returned nothing), so renaming or
    dropping the section left both documents sending dispatchers to a heading no
    pull request carries. The failure is silent in the worst direction: the
    gather still returns a full window of merged pull requests, and every one of
    them has nothing recorded in it.

    Derived rather than written down a third time: the heading is whichever of
    the template's own headings both paragraphs cite. It goes red from either
    end -- the template renaming the section, or a document's citation drifting
    off it.
    """
    prepare = _release_gather_paragraph()
    release_cut = _orchestration_gather_paragraph()

    cited = [h for h in _pr_template_headings() if f"`{h}`" in prepare and f"`{h}`" in release_cut]

    assert len(cited) == 1, (
        f"the two gather paragraphs cite {len(cited)} of {PR_TEMPLATE.name}'s headings, and the "
        "clause names exactly one as where a deferred claim is recorded; a citation no template "
        "section answers to sends a dispatcher looking for a record nobody was asked to write"
    )


def test_both_duty_records_cite_the_heading_the_template_really_carries() -> None:
    """The gather reads what the duty asked an author to write, and they can part.

    The reading side of this pair is already pinned; the writing side is these
    two records -- `orchestration.md`'s REVIEW checklist and CLAUDE.md's
    blast-radius row -- and nothing read either. Measured before this rule: the
    template's section renamed with the two gather paragraphs and the literal all
    updated to match left the suite green with the REVIEW bullet still naming the
    old heading. Authors are then told to record under one heading and the gather
    reads another, which surfaces as a pass with nothing in its window rather
    than as an error.

    Derived from the same template headings as the carrier rule rather than a
    third copy of the string: what is asserted is that one heading the template
    really carries is named by both records.
    """
    review = _section_of(DOC, REVIEW_CHECKLIST)
    row = _deferring_row()

    headings = _pr_template_headings()
    cited = [h for h in headings if f"`{h}`" in review and f"`{h}`" in row]

    assert len(cited) == 1, (
        f"{REVIEW_CHECKLIST!r} cites {[h for h in headings if f'`{h}`' in review]} and "
        f"{CLAUDE_MD.name}'s blast-radius row cites "
        f"{[h for h in headings if f'`{h}`' in row]} of {PR_TEMPLATE.name}'s headings; both "
        "record the duty to write a deferred claim down, so a heading they name and the template "
        "does not is a gather reading a section no author was asked to fill"
    )


def _release_cut_item_copies() -> tuple[str, str]:
    """`RELEASE_CUT_ITEM`'s two copies: the checklist item, and the section's quote of it."""
    items = [item for item in _core_release_checklist_items() if RELEASE_CUT_ITEM in item]
    assert len(items) == 1, (
        f"expected one Core checklist item carrying the release-cut step, found {len(items)}"
    )
    return items[0], _section()


def _deferred_claims_gather_copies() -> tuple[str, str]:
    """`DEFERRED_CLAIMS_GATHER`'s two copies, each in the paragraph a dispatcher reads."""
    return _release_gather_paragraph(), _orchestration_gather_paragraph()


#: Every arbiter literal -- a sentence two records have to share -- with the two
#: regions it arbitrates between. An anchor or a path constant is not one of
#: these; this tuple grows only for a sentence that has no third source to be
#: derived from, and the rule below is what such a third one has to survive.
SHARED_SENTENCES: tuple[tuple[str, str, Callable[[], tuple[str, str]]], ...] = (
    ("RELEASE_CUT_ITEM", RELEASE_CUT_ITEM, _release_cut_item_copies),
    ("DEFERRED_CLAIMS_GATHER", DEFERRED_CLAIMS_GATHER, _deferred_claims_gather_copies),
)


@pytest.mark.parametrize(
    ("name", "sentence", "copies"), SHARED_SENTENCES, ids=[name for name, _, _ in SHARED_SENTENCES]
)
def test_a_shared_sentence_covers_the_whole_of_what_its_two_copies_share(
    name: str, sentence: str, copies: Callable[[], tuple[str, str]]
) -> None:
    """A literal short of the shared text arbitrates only the part it still covers.

    The containment rules above pass on any document carrying the literal,
    whatever else that document lost, so a shrunken literal keeps them green and
    says nothing about the tail it dropped -- while still reading, in the diff,
    as the pin it used to be.
    Before this rule, `DEFERRED_CLAIMS_GATHER = ""`, a truncation of it to its
    lead fragment, and `RELEASE_CUT_ITEM = ""` each left `tests/ci` green, the
    middle one with the gather command pinned in neither document.

    A length floor cannot do it, whatever the number: the lead fragment that
    survived is longer than `RELEASE_CUT_ITEM` entire, so a floor high enough to
    reject the one rejects the other outright. What is asserted instead is
    maximality -- the two copies may agree on the punctuation and spacing
    abutting the sentence, but on no word beyond it. A word either side that both
    copies still share is shared text the arbiter has stopped covering, which is
    what every truncation looks like from here; the empty literal fails the count
    in :func:`_common_edges` before that.

    That margin is one word wide, so red has two causes and two remedies: the
    literal stopped short of text both copies carry, and is extended; or the two
    copies came to share a word abutting it, which an ordinary rewording of
    either document's next sentence does, and one copy's abutting prose is
    rewritten. The message below carries both, because only the second is a
    false alarm and the reader has to be able to tell.
    """
    assert sentence.strip(), f"{name} is empty, so every rule that greps for it passes on anything"

    left, right = copies()
    before, after = _common_edges(left, right, sentence)

    assert not re.search(r"\w", before + after), (
        f"{name}'s two copies still share {before!r} before it and {after!r} after it. Either the "
        "literal stopped short of text both copies carry, and has to be extended or it stops "
        "arbitrating the part it dropped; or the two copies came to share a word abutting it by "
        "coincidence, which a rewording of either document's next sentence is enough to do, and "
        "then the remedy is to reword one copy's abutting prose rather than this literal"
    )


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
    reader that a green run says nothing about the tooling, the suite or the
    documentation -- and it is true only because the census root is a path inside
    `packages/`. Widen that root and the sentence quietly inverts: the sweep
    would start sampling files the section promised it never touches, and the
    reader's model of what a clean run covers would be wrong in the direction
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


#: Cron's own weekday order, index 0 being Sunday.
_DAY_NAMES = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


def _weekday_phrase(field: str) -> str | None:
    """How the section words a cron weekday field, or `None` if it has no wording.

    Matched whole, never by prefix or containment. A field of `0,3` contains
    the Sunday spelling and fires twice a week, so anything looser renders "on
    Sunday" for a schedule that also runs on Wednesdays -- green, and wrong in
    the direction that overstates how little the job runs.

    `0`, `7` and `SUN` are one day in cron's own vocabulary, so rewriting the
    field from one spelling into another must not read here as a schedule
    change. A list, a range or a step is a schedule the section has no sentence
    for at all, and the caller turns the `None` into a demand that the sentence
    be rewritten rather than re-rendered.
    """
    if field == "*":
        return "daily"
    for index, day in enumerate(_DAY_NAMES):
        spellings = {str(index), day[:3].upper()} | ({"7"} if index == 0 else set())
        if field.upper() in spellings:
            return f"on {day}"
    return None


@pytest.mark.parametrize(
    ("field", "phrase"),
    (
        ("*", "daily"),
        ("0", "on Sunday"),
        ("7", "on Sunday"),
        ("SUN", "on Sunday"),
        ("sun", "on Sunday"),
        ("3", "on Wednesday"),
        ("wed", "on Wednesday"),
        ("0,3", None),
        ("1-5", None),
        ("*/2", None),
    ),
)
def test_a_weekday_field_renders_only_the_shapes_the_section_has_a_sentence_for(
    field: str, phrase: str | None
) -> None:
    """The rule below exercises one shape -- whichever the live cron holds.

    Every other branch of `_weekday_phrase` is therefore unmeasured until someone
    moves the schedule, and the branch that matters most is the one that returns
    nothing: `0,3` *contains* the Sunday spelling, so a matcher loosened to a
    prefix or a substring renders "on Sunday" for a job that also runs on
    Wednesdays -- green, and wrong in the direction that understates how often
    the job runs. The equivalent spellings are the other side: cron reads `0`,
    `7` and `SUN` as one day, so re-spelling the field must not read here as a
    schedule change and send somebody rewriting a sentence that was already true.
    """
    assert _weekday_phrase(field) == phrase


def test_the_section_states_the_hour_and_day_the_workflow_is_actually_scheduled_for() -> None:
    """A schedule that moves silently is the section's own failure mode, one level up.

    Deleting the `schedule:` block entirely leaves every other test in this
    repository green: the workflow still parses, the invocation gate still finds
    its step, and nothing ever runs. The section's sentence then describes a job
    that never starts, which is exactly the reading it warns about -- a quiet
    tracker that means no runs rather than no findings.

    Hour and day are both rendered back out of the cron rather than written
    down, so moving either moves what this looks for. The day was a fixed
    `* * *` shape assertion until the cadence moved to Sunday; re-rendering it
    rather than re-freezing the new shape keeps the rule failing in both
    directions, where relaxing it to the hour alone would leave every later day
    change invisible.

    What is compared is the section's whole statement, not a rendering looked
    for inside it, so prose that states the time twice -- one sentence updated
    and one not -- goes red rather than green on the half that still agrees.
    """
    schedule = _triggers(_workflow()).get("schedule")

    assert schedule, f"{WORKFLOW} has no `schedule:` at all; the section states when it runs"
    assert len(schedule) == 1, (
        f"{WORKFLOW} carries {len(schedule)} cron entries; the section describes one scheduled "
        "run, and its seven-walks-per-run arithmetic is written against one. Rewrite the section "
        "rather than re-deriving from the first entry"
    )
    minute, hour, day, month, weekday = str(schedule[0]["cron"]).split()
    assert (day, month) == ("*", "*") and hour.isdigit() and minute.isdigit(), (
        f"the cron {schedule[0]['cron']!r} fires at more than one time of day, or only in some "
        "months, and the section has wording for neither: rewrite the sentence, do not re-derive"
    )
    phrase = _weekday_phrase(weekday)
    assert phrase is not None, (
        f"the cron weekday field {weekday!r} is neither `*` nor a single day, so the section's "
        "sentence needs rewriting rather than re-rendering"
    )

    expected = f"at {int(hour):02d}:{int(minute):02d} UTC {phrase}"
    stated = re.findall(r"at \d{1,2}:\d{2} UTC (?:daily|on \w+)", _section())

    assert stated == [expected], (
        f"the section states {stated} as when the sweep runs, and the cron "
        f"{schedule[0]['cron']!r} renders as {expected!r}: either the schedule moved without the "
        "sentence, or the sentence moved without the schedule, or the section now states it in "
        "more than one place and every copy has to be kept in step with the one cron"
    )


def test_the_section_states_the_mutation_budget_the_workflow_actually_passes() -> None:
    """The invocation gate pins that `--block-size` is present, not what it says.

    The number is the sweep's whole cost model, and the block form moved what it
    counts: `--block-size 6` at one mutation per module is six mutations across
    six modules, plus the one shared control, which is the seven full suite walks
    the section counts and the arithmetic the job's `timeout-minutes` is set
    against. Raising the block size without touching the prose leaves a
    documented bound that bounds nothing.

    **Both halves are derived, and the second one is arithmetic.** The mutation
    count comes out of the invocation; the walk count comes out of
    `sweep.walk_budget`, which computes it from the block size and
    `sweep_mutations.MUTATIONS_PER_FILE` rather than storing it. A section that
    restated either would agree with itself and with nothing else -- and the
    walk figure is the one a reader converts into an expectation about how long
    the job may run.

    The ceiling is referred to and not quoted. This docstring used to quote a
    figure the workflow had already moved past -- the failure the module exists
    to catch, one level in. A number written into prose beside the source that
    owns it goes stale exactly as quietly, including here.
    """
    run = _sweep_step_run()

    found = re.search(r"--block-size\s+(\d+)", run)
    assert found is not None, f"the driver is invoked without --block-size:\n{run}"
    block_size = int(found.group(1))
    assert block_size in _SPELLED, f"a block of {block_size} needs the section reworded"
    walks = _sweep_driver().walk_budget(block_size)
    assert walks in _SPELLED, f"a budget of {walks} walks needs the section reworded"

    assert f"at most {_SPELLED[block_size]} mutations" in _section()
    assert f"{_SPELLED[walks]} walks" in _section()


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

    These two are deliberately different threads: one collects the runs the
    driver made and could not stand behind, the other the runs the job died
    before the driver could file. A reader who is given the wrong name for
    either cannot tell those apart, and they have opposite remedies.
    """
    assert _alarm_title() in _section()


def test_the_landing_paragraph_names_the_label_the_driver_really_files_under() -> None:
    """The label is what makes the section's legs one queue for triage.

    The reach is one leg and one paragraph: the driver's `LABEL` constant,
    quoted where the section makes the landing claim. The workflow alarm's own
    label is the rule below; the two agent passes file by hand, and no rule here
    can reach a label a human types.

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
    """The landing claim covers every leg; this rule reaches the second machine one.

    The rule above reads the driver's label out of `sweep_filing.py`. The alarm
    step's is shell text in the workflow, and changing it left every pin green
    -- the section would go on promising one queue while the runs the job died
    landed in another, invisible to a triager filtering on the label and to a
    reader of this file.

    Both of the alarm's `gh` calls are checked, because the list call and the
    create call can drift apart: one finds no thread, the other opens one, and
    the standing alarm thread silently becomes a new issue every failure.
    """
    label = _sweep_filing().LABEL

    assert _alarm_labels() == [label, label]
