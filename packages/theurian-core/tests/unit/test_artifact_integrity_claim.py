"""What the artifact-integrity step says about *why* it verified nothing.

The step has always reported ``NOT_APPLICABLE`` and will until
https://github.com/theurian/theurian/issues/39 lands. What it said about *why*
was a pair of claims whose truth value flips at the first ``core-v*`` tag::

    summary  "No signed release manifest exists yet; nothing to verify against."
    detail   "Artifact verification arrives with the first tagged release (OSS-7, T-16)."

``release-core.yml`` writes ``SHA256SUMS`` over every artifact and a
reproducible CycloneDX SBOM, and attaches both to the GitHub release. From the
first tag a record exists -- so the summary tells every user there is nothing to
check against something they *could* have checked by hand, which is the only
mitigation they have until the control lands, and the detail becomes an overdue
promise with nobody named to keep it. Both sentences were true when they were
written. **One function ships on both sides of that boundary**, so the only
premise that survives it is one that never mentions it: the step is not
applicable because *Theurian verifies nothing*, not because there is nothing to
verify.

Nothing pinned either string. Rewriting both to the empty string left the whole
suite green, on two surfaces every user reaches -- ``theurian setup`` and
``theurian doctor``.

**What this module is.** A regression test over the wordings this claim has
taken, one grammar rule, and one mechanical equality. It is not a closure
argument: an author who writes the same claim in a shape :data:`_RECORD_NOUN`
does not know passes here. The equality against ``docs/contributing/release.md``
is the part that cannot be argued with -- that file quotes the step's published
JSON and asserts the values are byte-identical, so this checks that they are.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Final

from fakes.setup import FakeMcpConfig, FakeService

from theurian.application.setup_context import SetupContext
from theurian.application.setup_steps import probe_artifact_integrity
from theurian.domain.setup import SetupReport, SetupState, SetupStep, StepStatus
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.secrets.file_store import FileSecretStore

REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]
RELEASE_DOC: Final = REPO_ROOT / "docs" / "contributing" / "release.md"

#: The two retired claims, verbatim. Held as literals rather than left to the
#: rules below, because a rule is a guess about how the next author will phrase
#: it and these two are what was actually shipped.
RETIRED_CLAIMS: Final = (
    "No signed release manifest exists yet; nothing to verify against.",
    "Artifact verification arrives with the first tagged release (OSS-7, T-16).",
)

#: A noun for the record a verifier checks an artifact against, with up to three
#: preceding words captured. Every one of them names something that does not
#: exist before the first ``core-v*`` tag and does after, so a sentence making
#: one of them a *subject* is a sentence whose truth value moves at the tag.
#: As the object of a preposition -- "verify an artifact **against** the
#: checksums" -- it asserts nothing about whether one exists.
_RECORD_NOUN: Final = re.compile(
    r"(?P<lead>(?:\S+\s+){0,3})\b(?:manifests?|checksums?|sha256sums|sboms?|attestations?)\b",
    re.IGNORECASE,
)

#: A preposition that makes the noun above an object rather than a subject.
_GOVERNED: Final = re.compile(r"\b(?:against|with|to|from|in|by|of)\b", re.IGNORECASE)

#: A promise that something will be true later. The retired ``detail`` was one,
#: and it came due the moment ``release-core.yml`` landed. Issue 39 holds the
#: schedule instead, because an issue has an owner and a string does not.
_SCHEDULE: Final = re.compile(
    r"\b(?:arrives?|arriving|will|shall|soon|planned|upcoming|milestone)\b|\bv?\d+\.\d+\b",
    re.IGNORECASE,
)

#: Fenced JSON in a Markdown document.
_JSON_BLOCK: Final = re.compile(r"```json\n(?P<body>.*?)\n```", re.DOTALL)


def _context(tmp_path: pathlib.Path, *, for_publication: bool = False) -> SetupContext:
    """A context the step is required to ignore entirely.

    Fully populated rather than minimal, so that the two-context comparison
    below is comparing something: a step reading any of these fields would have
    to produce the same strings from a real service, a real repository root and
    a publication flag, or fail there.
    """
    return SetupContext(
        home=tmp_path,
        data_dir=tmp_path / "data",
        port=7419,
        project_root=tmp_path / "repo",
        connection=ConnectionSpec(port=7419),
        mcp_config=FakeMcpConfig(),
        secrets=FileSecretStore(tmp_path / "data"),
        health=lambda: None,
        service=FakeService(),
        executable=str(tmp_path / "theurian"),
        for_publication=for_publication,
    )


def _published(step: SetupStep) -> dict[str, object]:
    """The step exactly as ``theurian setup --json`` publishes it.

    Routed through :meth:`SetupReport.to_json` rather than read off the
    dataclass, because the published field set is that method's decision and a
    document quoting the output quotes those keys.
    """
    payload = SetupReport(state=SetupState.PLAN_BUILT, steps=(step,), dry_run=True).to_json()
    steps = payload["steps"]
    assert isinstance(steps, list), "SetupReport.to_json no longer publishes a `steps` array"
    published = steps[0]
    assert isinstance(published, dict)
    return published


def _claim(tmp_path: pathlib.Path) -> str:
    step = probe_artifact_integrity(_context(tmp_path))
    return f"{step.summary} {step.detail}"


# -- The claim itself -------------------------------------------------------


def test_the_step_still_reports_not_applicable_rather_than_satisfied(
    tmp_path: pathlib.Path,
) -> None:
    """The reasoning that does *not* move: nothing here checks anything.

    Reporting ``SATISFIED`` would be a false assurance about supply chain
    integrity (T-16). Only the step's stated premise was wrong; its status was
    always right, and this is what stops a later edit from "fixing" the strings
    by claiming the check instead.
    """
    step = probe_artifact_integrity(_context(tmp_path))

    assert step.status is StepStatus.NOT_APPLICABLE
    assert step.action == "", "a report-only step must not offer setup an action"
    assert step.paths == (), "this step writes nothing, so it names no path"


def test_the_step_blames_theurian_rather_than_the_absence_of_a_record(
    tmp_path: pathlib.Path,
) -> None:
    """The premise, pinned by its subject and its verb rather than its sentence.

    "Theurian does not verify" is true before the first tag, at it, and after
    it. "There is nothing to verify against" is true only before, and it is the
    half that costs a user something: it tells them not to bother checking a
    file that is sitting on the release page.
    """
    summary = probe_artifact_integrity(_context(tmp_path)).summary.lower()

    assert "theurian" in summary, f"the summary no longer names who fails to verify: {summary}"
    assert "does not verify" in summary, f"the summary no longer states the gap: {summary}"


def test_neither_retired_claim_can_return(tmp_path: pathlib.Path) -> None:
    claim = _claim(tmp_path)

    for retired in RETIRED_CLAIMS:
        assert retired not in claim, f"the step is asserting a retired claim again: {retired}"


def test_the_step_never_makes_the_release_record_the_subject_of_a_claim(
    tmp_path: pathlib.Path,
) -> None:
    """A checksum may be something to check *against*, never something that exists.

    This is the shape rule, and it is one grammar: a record noun has to arrive
    governed by a preposition. It catches every phrasing of the retired summary
    that keeps its noun -- "no manifest exists", "there is no SHA256SUMS yet",
    "the checksums are not published" -- and nothing about a phrasing that drops
    it. That is recorded rather than chased; :data:`RETIRED_CLAIMS` is what
    holds the wording that actually shipped.
    """
    claim = _claim(tmp_path)

    for match in _RECORD_NOUN.finditer(claim):
        assert _GOVERNED.search(match.group("lead")), (
            f"the step makes a release record the subject of a claim, which is "
            f"a claim that turns at the first core-v* tag: {match.group(0)!r}"
        )


def test_the_step_promises_no_schedule(tmp_path: pathlib.Path) -> None:
    """A string cannot own a date. The retired ``detail`` tried and came due."""
    claim = _claim(tmp_path)

    promises = [match.group(0) for match in _SCHEDULE.finditer(claim)]
    assert not promises, f"the step promises a schedule nobody owns: {promises} in {claim!r}"


def test_the_step_names_the_issue_that_owns_the_gap(tmp_path: pathlib.Path) -> None:
    """Where a reader goes for the schedule the strings deliberately refuse.

    The URL rather than the bare number, because the audience is whoever is
    reading ``theurian setup`` output on their own terminal, and "#39" names
    nothing to them.
    """
    detail = probe_artifact_integrity(_context(tmp_path)).detail

    assert "https://github.com/theurian/theurian/issues/39" in detail


def test_the_step_reads_the_same_in_a_report_bound_for_a_public_issue(
    tmp_path: pathlib.Path,
) -> None:
    """No field of the context reaches these strings, which is what makes one
    function able to be true on both sides of the tag."""
    private = probe_artifact_integrity(_context(tmp_path))
    public = probe_artifact_integrity(_context(tmp_path, for_publication=True))

    assert private == public


# -- The document that quotes it --------------------------------------------


def test_the_release_document_quotes_what_setup_actually_publishes(
    tmp_path: pathlib.Path,
) -> None:
    """``release.md`` says "the values are byte-identical". This is that check.

    That document is where a maintainer goes to find out what a release does and
    does not establish, and it reproduces this step's published JSON to show that
    the verifying half of T-16 is absent. A quotation that drifts from the code
    is the same defect as the strings themselves, one surface further out.
    """
    document = RELEASE_DOC.read_text(encoding="utf-8")
    blocks = [json.loads(match.group("body")) for match in _JSON_BLOCK.finditer(document)]
    quoted = [block for block in blocks if block.get("id") == "artifact-integrity"]

    assert len(quoted) == 1, (
        f"docs/contributing/release.md no longer quotes the artifact-integrity step; "
        f"it quotes {[block.get('id') for block in blocks]}"
    )
    assert quoted[0] == _published(probe_artifact_integrity(_context(tmp_path)))
