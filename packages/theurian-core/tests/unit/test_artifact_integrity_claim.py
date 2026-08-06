"""What the artifact-integrity step says about *why* it verified nothing.

The step reports ``NOT_APPLICABLE`` and will until
https://github.com/theurian/theurian/issues/39 lands. What it said about *why*
was a pair of claims whose truth value flips at the first ``core-v*`` tag::

    summary  "No signed release manifest exists yet; nothing to verify against."
    detail   "Artifact verification arrives with the first tagged release (OSS-7, T-16)."

``release-core.yml`` builds ``SHA256SUMS`` over every artifact and a
reproducible CycloneDX SBOM and attaches both to the release it cuts. Once a tag
is cut, a record exists -- so the summary would tell every user there is nothing
to check against something they *could* check by hand, which is the only
mitigation they have until the control lands, and the detail would be an overdue
promise with nobody named to keep it. Both sentences were true when they were
written. **One function ships on both sides of that boundary**, so the only
premise that survives it is one that never mentions it: the step is not
applicable because *Theurian verifies nothing*, not because there is nothing to
verify.

Nothing pinned either string. Rewriting both to the empty string left the whole
suite green, on the surfaces every user reaches -- ``theurian setup``,
``theurian doctor`` and ``doctor --report``.

**What this module is, exactly.** A tripwire for the two retired phrasings and
for the shapes nearest them. It is emphatically **not** a check that the claim is
true, and reading it as one is how the next author gets caught: the rules are
regexes over English, and no regex decides whether a sentence about a supply
chain is accurate. Measured escapes, all of which pass every assertion here:

===========================================================  ==========================
Wording                                                      Why it survives
===========================================================  ==========================
``"See <issue 39>."``                                        the rules forbid false
                                                             claims; nothing requires
                                                             the useful ones
``"There is no published record to check against."``         "record" is not in
                                                             :data:`_RECORD_NOUN`
``"Nothing exists to verify against."``                      the same claim carrying
                                                             no noun at all
``"...lands in a future release."``                          "future" is not in
                                                             :data:`_SCHEDULE`
``"Verification begins once the first tag is cut."``         the original defect's
                                                             semantics in new words
``"the daemon hashes the artifact on start, so the gap is    a plausible sentence
covered outside setup"``                                     someone closes #39 with
``"It is not true that Theurian does not verify..."``        polarity is not read
===========================================================  ==========================

**The rules refuse true sentences too, and more readily than false ones.**
Measured, on text nobody should have to fight the suite to write:

- "Compare your download **against the CycloneDX 1.6 SBOM** published with it"
  is REFUSED. :data:`_RECORD_NOUN`'s window is three words, and the version
  number pads the preposition out of reach of the noun.
- "against the newly published reproducible CycloneDX **checksums**" is REFUSED
  for the same reason -- adjectives cost window.
- "against the **checksums**" is allowed. In practice the rule demands the
  preposition sit within three words of the noun, which is a constraint on
  *style*, not on truth.

Widening the window trades that away for a real leak: at six words, "Theurian
compares nothing **to** anything, and no manifest exists yet" passes. Three is
chosen because the defect being guarded is a record noun in subject position,
and that is where the false negative would matter more than the false positive.

:data:`_SCHEDULE` was the worse offender and has been narrowed. It used to match
any two-part version number anywhere, which forbade "CycloneDX 1.6" and
"Theurian 1.0 targets macOS" outright; a version now has to follow a scheduling
preposition. Both pass; "lands in 0.2.0" is still caught.

Three things here are not regexes over English, and they are the load-bearing
part:

- :func:`_published_literals` reads **every** string literal in the probe out of
  its AST, so a rule applies to every arm rather than to whichever one a fixture
  reaches. Measured: a branch returning the retired strings when
  ``context.executable`` resolves -- true in essentially every real run, since
  ``_executable()`` falls back to ``sys.argv[0]`` -- survived the whole suite
  while the real CLI printed them.
- :func:`test_the_literal_scan_sees_what_the_step_actually_returns` ties that
  reading back to the product, so the scan cannot quietly stop finding anything.
- The two ``release.md`` tests are exact text, not judgement.
"""

from __future__ import annotations

import ast
import inspect
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

ISSUE_URL: Final = "https://github.com/theurian/theurian/issues/39"

#: The two retired claims, verbatim. Held as literals rather than left to the
#: rules below, because a rule is a guess about how the next author will phrase
#: it and these two are what was actually shipped.
RETIRED_CLAIMS: Final = (
    "No signed release manifest exists yet; nothing to verify against.",
    "Artifact verification arrives with the first tagged release (OSS-7, T-16).",
)

#: A noun for the record a verifier checks an artifact against, with up to three
#: preceding words captured. Every one of them names something that does not
#: exist before the first ``core-v*`` tag and does after, so making one of them
#: the *subject* of a sentence is making a claim whose truth value moves at the
#: tag. As the object of a preposition -- "verify an artifact **against** the
#: checksums" -- it asserts nothing about whether one exists.
#:
#: Applied one sentence at a time (:func:`_sentences`), because the three-word
#: window otherwise reaches backwards past a full stop: the shipped summary's
#: terminal "from." governed the first words of the *next field* while the two
#: were scanned as one joined string, which admitted a ``detail`` opening "No
#: manifest exists yet".
_RECORD_NOUN: Final = re.compile(
    r"(?P<lead>(?:\S+\s+){0,3})\b(?:manifests?|checksums?|sha256sums|sboms?|attestations?)\b",
    re.IGNORECASE,
)

#: A preposition that makes the noun above an object rather than a subject.
#:
#: Deliberately three and not more. An earlier version also accepted ``of``,
#: which let through "In the absence **of** a signed manifest, Theurian does not
#: verify..." -- the retired claim's most natural rephrasing, approved by a test
#: whose name says it rejects exactly that.
_GOVERNED: Final = re.compile(r"\b(?:against|with|to)\b", re.IGNORECASE)

#: A promise that something will be true later. The retired ``detail`` was one,
#: and it came due the moment ``release-core.yml`` landed. Issue 39 holds the
#: schedule instead, because an issue has an owner and a string does not.
#:
#: The version clause requires a scheduling preposition in front of it. Bare
#: ``\d+\.\d+`` forbade "CycloneDX 1.6" -- a true and useful thing to write --
#: which is the rule biting harder on accurate text than on false text.
_SCHEDULE: Final = re.compile(
    r"\b(?:arrives?|arriving|will|shall|soon|planned|upcoming|milestone)\b"
    r"|\b(?:in|from|by|until|after)\s+(?:version\s+)?v?\d+\.\d+\b",
    re.IGNORECASE,
)

#: Fenced JSON in a Markdown document.
_JSON_BLOCK: Final = re.compile(r"```json\n(?P<body>.*?)\n```", re.DOTALL)

#: End of a sentence, for confining :data:`_RECORD_NOUN`'s backward window.
_SENTENCE_END: Final = re.compile(r"(?<=[.;:])\s+")


def _sentences(text: str) -> list[str]:
    """Sentences, so a backward-looking rule cannot reach across a full stop.

    A URL survives intact: the colon in ``https://`` is not followed by
    whitespace, so it is not a boundary.
    """
    return [part for part in _SENTENCE_END.split(text) if part.strip()]


def _published_literals() -> tuple[str, ...]:
    """Every string literal ``probe_artifact_integrity`` could show a user.

    Read out of the AST rather than off one return value. A rule applied to one
    invocation checks whichever arm the fixture reaches, and a probe gains arms:
    adding a branch that returns the retired strings when ``context.executable``
    resolves survived the whole suite, and that condition is true in essentially
    every real run because ``_executable()`` falls back to ``sys.argv[0]``.

    The function's own docstring is excluded. It quotes the retired claims on
    purpose, and a docstring is not something a user is shown.
    """
    tree = ast.parse(inspect.getsource(probe_artifact_integrity))
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef), "probe_artifact_integrity is no longer a function"

    body = function.body
    first = body[0] if body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        body = body[1:]

    return tuple(
        node.value
        for statement in body
        for node in ast.walk(statement)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.strip()
    )


def _context(
    root: pathlib.Path, *, port: int = 7419, for_publication: bool = False
) -> SetupContext:
    """A context the step is required to ignore entirely.

    Fully populated, and built from a caller-supplied root and port so that two
    of them differ in every field. With both built from one ``tmp_path`` the
    comparison below proved almost nothing: a step interpolating
    ``context.executable`` into its summary stayed green.
    """
    root.mkdir(parents=True, exist_ok=True)
    return SetupContext(
        home=root,
        data_dir=root / "data",
        port=port,
        project_root=root / "repo",
        connection=ConnectionSpec(port=port),
        mcp_config=FakeMcpConfig(),
        secrets=FileSecretStore(root / "data"),
        health=lambda: None,
        service=FakeService(),
        executable=str(root / "theurian"),
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


def _json_blocks(document: str) -> list[tuple[str, dict[str, object]]]:
    """Every fenced JSON block that parses to an object, as (raw text, parsed).

    A block that does not parse -- an elided example, a top-level array -- is
    skipped rather than raising. Unguarded, one future edit to ``release.md``
    failed this module with a ``JSONDecodeError`` from a line no reader would
    connect to the artifact-integrity step.
    """
    blocks: list[tuple[str, dict[str, object]]] = []
    for match in _JSON_BLOCK.finditer(document):
        body = match.group("body")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            blocks.append((body, parsed))
    return blocks


# -- What #39 rewrites ------------------------------------------------------
#
# Both tests below become false when verification exists, and that is deliberate:
# a status and a premise are decisions, and closing #39 should have to state them
# rather than inherit them. Everything in the next section is written to survive
# that change, so #39 rewrites two tests and inherits six.


def test_the_step_reports_not_applicable_while_nothing_is_verified(
    tmp_path: pathlib.Path,
) -> None:
    """Reporting ``SATISFIED`` without checking would be a false assurance (T-16).

    Only the step's stated premise was ever wrong; its status was right. This is
    what stops an edit from "fixing" the strings by claiming the check instead.
    """
    assert probe_artifact_integrity(_context(tmp_path)).status is StepStatus.NOT_APPLICABLE


def test_the_not_applicable_step_names_theurian_as_what_does_not_verify(
    tmp_path: pathlib.Path,
) -> None:
    """The premise, pinned by its subject and its verb rather than its sentence.

    "Theurian does not verify" is true before a tag, at one, and after it. Two
    substrings is all this is -- "Theurian does not verify the artifact; no
    manifest exists yet." passes it, and catching that second clause is
    :func:`test_the_step_never_makes_a_release_record_the_subject_of_a_claim`'s
    job, not this one's.
    """
    summary = probe_artifact_integrity(_context(tmp_path)).summary.lower()

    assert "theurian" in summary, f"the summary no longer names who fails to verify: {summary}"
    assert "does not verify" in summary, f"the summary no longer states the gap: {summary}"


# -- What #39 inherits ------------------------------------------------------


def test_the_literal_scan_sees_what_the_step_actually_returns(tmp_path: pathlib.Path) -> None:
    """Ties :func:`_published_literals` to the product it claims to cover.

    Every rule below reads the source. If the strings move behind a module
    constant, an f-string built elsewhere, or a helper, the scan silently starts
    checking nothing -- and every rule keeps passing. This is what fails instead.
    """
    step = probe_artifact_integrity(_context(tmp_path))
    literals = _published_literals()

    assert step.summary in literals, "the summary is no longer a literal in the probe"
    assert step.detail in literals, "the detail is no longer a literal in the probe"


def test_no_arm_of_the_step_can_assert_a_retired_claim() -> None:
    for literal in _published_literals():
        for retired in RETIRED_CLAIMS:
            assert retired not in literal, f"an arm asserts a retired claim again: {retired}"


def test_the_step_never_makes_a_release_record_the_subject_of_a_claim() -> None:
    """A checksum may be something to check *against*, never something that exists.

    One grammar, applied per sentence and per literal. It catches every phrasing
    of the retired summary that keeps its noun -- "no manifest exists", "there is
    no SHA256SUMS yet", "in the absence of a signed manifest" -- and nothing
    about a phrasing that drops it. That is recorded rather than chased; the
    module docstring lists what escapes.
    """
    for literal in _published_literals():
        for sentence in _sentences(literal):
            for match in _RECORD_NOUN.finditer(sentence):
                assert _GOVERNED.search(match.group("lead")), (
                    f"an arm makes a release record the subject of a claim, which is a "
                    f"claim that turns at the first core-v* tag: {match.group(0)!r}"
                )


def test_no_arm_of_the_step_promises_a_schedule() -> None:
    """A string cannot own a date. The retired ``detail`` tried and came due."""
    for literal in _published_literals():
        promises = [match.group(0) for match in _SCHEDULE.finditer(literal)]
        assert not promises, f"an arm promises a schedule nobody owns: {promises} in {literal!r}"


def test_the_step_names_the_issue_that_owns_the_gap(tmp_path: pathlib.Path) -> None:
    """Where a reader goes for the schedule the strings deliberately refuse.

    The URL rather than the bare number, because the audience is whoever is
    reading ``theurian setup`` output on their own terminal, and "#39" names
    nothing to them.
    """
    assert ISSUE_URL in probe_artifact_integrity(_context(tmp_path)).detail


def test_the_step_reads_the_same_in_a_report_bound_for_a_public_issue(
    tmp_path: pathlib.Path,
) -> None:
    """No context field reaches these strings, which is what lets one function
    be true on both sides of a tag.

    The two contexts differ in every field -- root, data directory, port,
    repository, executable path and the publication flag -- because when both
    came from one ``tmp_path`` this passed for a step that interpolated
    ``context.executable`` into its summary.
    """
    private = probe_artifact_integrity(_context(tmp_path / "private", port=7419))
    public = probe_artifact_integrity(
        _context(tmp_path / "public", port=7420, for_publication=True)
    )

    assert private == public


# -- The document that quotes it --------------------------------------------


def test_the_release_document_quotes_what_setup_actually_publishes(
    tmp_path: pathlib.Path,
) -> None:
    """``release.md`` says the values are byte-identical. This is that check.

    Compared as **text**, not as parsed values: ``json.loads(block) ==
    published`` normalises escapes and ignores key order, so a re-escaped or
    reordered block would satisfy a word the document does not mean.
    """
    document = RELEASE_DOC.read_text(encoding="utf-8")
    quoted = [
        raw for raw, block in _json_blocks(document) if block.get("id") == "artifact-integrity"
    ]

    assert len(quoted) == 1, (
        f"docs/contributing/release.md no longer quotes exactly one artifact-integrity "
        f"step; it quotes {len(quoted)}"
    )
    published = _published(probe_artifact_integrity(_context(tmp_path)))
    assert quoted[0] == json.dumps(published, indent=2, sort_keys=True)


def test_no_json_block_in_the_release_document_holds_a_retired_claim() -> None:
    """The equality above binds one block, found by its ``id``.

    A second fenced block under any other id -- ``artifact-integrity-legacy``
    was the measured one -- carried both retired strings verbatim and survived
    the whole suite. Prose in that file quotes them deliberately, to say what
    they used to be; a code block is read as output.
    """
    document = RELEASE_DOC.read_text(encoding="utf-8")

    for raw, _ in _json_blocks(document):
        for retired in RETIRED_CLAIMS:
            assert retired not in raw, (
                f"a JSON block in docs/contributing/release.md presents a retired claim "
                f"as output: {retired}"
            )
