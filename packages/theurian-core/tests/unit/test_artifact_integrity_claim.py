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
chain is accurate.

**Where the strength actually is.** Not in the regexes.
:func:`_returned_call` **constrains what the probe is allowed to be** -- one
unconditional return of one ``SetupStep`` whose every argument is a literal or an
enum member -- so the set of strings it can publish is decidable, and the rules
run over all of it. That replaced a scan of the function's own literals, which
was not sound: moving the retired strings into a module-level helper and calling
it from a reached arm passed all ten tests while the real CLI printed them.
:func:`_published` and the two ``release.md`` tests are exact comparisons rather
than judgement.

**Measured escapes.** Every wording below passes every rule here:

- ``"See <issue 39>."`` -- the rules forbid a false claim; nothing requires a
  useful one, so every fact can be deleted silently.
- ``"There is no published record to check against."`` -- "record" is not in
  :data:`_RECORD_NOUN`, and neither are "file", "digest" or "hash".
- ``"Nothing exists to verify against."`` -- the same claim carrying no noun.
- ``"Nothing here is checked against a manifest, and none exists."`` -- the noun
  is genuinely governed in its own clause and the false half carries none. **A
  record noun is not what makes a sentence false**, which is the family this list
  used to leave implied.
- ``"...lands in a future release."`` -- "future" is not in :data:`_SCHEDULE`.
- ``"Verification begins once the first tag is cut."`` -- the original defect's
  semantics in words the rule does not know.
- ``"the daemon hashes the artifact on start, so the gap is covered outside
  setup"`` -- **plausibility is not judged**, and this is a sentence someone
  closes #39 with.
- ``"It is not true that Theurian does not verify..."`` -- **polarity is not
  read**.

**The rules also refuse true text.** Recorded rather than chased, because each
narrowing has bought a leak somewhere else:

- :data:`_SCHEDULE` refuses ``will`` unconditionally, so "comparing the download
  against the checksums by hand will tell you whether it matches" is rejected.
- :data:`_SCHEDULE` used to refuse any two-part version number, which forbade
  "CycloneDX 1.6" and "Theurian 1.0 targets macOS"; a version now has to follow a
  scheduling preposition. "lands in 0.2.0" is still caught.
- :data:`_RECORD_NOUN`'s window used to be three words, which refused "against
  the CycloneDX 1.6 SBOM" because the version padded the preposition out of
  reach. The window is now the clause, which accepts it.
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
from theurian.application.setup_service import SetupRequest, SetupService
from theurian.application.setup_steps import probe_artifact_integrity
from theurian.domain.setup import StepStatus
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

#: A noun for the record a verifier checks an artifact against. Every one of them
#: names something that does not exist before the first ``core-v*`` tag and does
#: after, so making one the *subject* of a clause is making a claim whose truth
#: value moves at the tag.
_RECORD_NOUN: Final = re.compile(
    r"\b(?:manifests?|checksums?|sha256sums|sboms?|attestations?)\b", re.IGNORECASE
)

#: A preposition that makes the noun above an object -- "verify an artifact
#: **against** the checksums" -- **and is not immediately negated**.
#:
#: The lookahead is the correction to this rule's original premise, which was
#: that a prepositional object "asserts nothing about whether one exists". It
#: does: "Theurian ships **with no** checksums to check against" and "published
#: **with no** signed manifest yet" are prepositional objects asserting
#: non-existence, and both cleared every rule in this module.
_GOVERNING: Final = re.compile(
    r"\b(?:against|with|to)\s+(?!(?:no|not|nothing|none|never|neither)\b)", re.IGNORECASE
)

#: A promise that something will be true later. The retired ``detail`` was one,
#: and it came due the moment ``release-core.yml`` landed. Issue 39 holds the
#: schedule instead, because an issue has an owner and a string does not.
_SCHEDULE: Final = re.compile(
    r"\b(?:arrives?|arriving|will|shall|soon|planned|upcoming|milestone)\b"
    r"|\b(?:in|from|by|until|after)\s+(?:version\s+)?v?\d+\.\d+\b",
    re.IGNORECASE,
)

#: Any fenced block, whatever it claims to hold. Deliberately not limited to
#: ``json``: a block fenced ``JSON``, ``jsonc``, ``text`` or nothing at all is
#: read by a human as output just the same, and each of those escaped a scan
#: that matched one lowercase language.
_FENCE: Final = re.compile(r"```(?P<lang>[A-Za-z0-9]*)\n(?P<body>.*?)\n```", re.DOTALL)

#: Fence languages whose contents are meant to be JSON.
_JSON_LANGS: Final = frozenset({"", "json", "jsonc", "json5"})

#: A clause boundary. A governing preposition has to be in the noun's *own*
#: clause: "Theurian compares nothing to anything, and no manifest exists yet"
#: otherwise reads as governed by a ``to`` two clauses away.
_CLAUSE_BOUNDARY: Final = re.compile(
    r"[.;:,]\s+|\s+(?:and|but|or|so|yet|while|though|because)\s+", re.IGNORECASE
)


def _clauses(text: str) -> list[str]:
    """Clauses, so a backward-looking rule cannot borrow a preposition.

    A URL survives intact: the colon in ``https://`` is not followed by
    whitespace, so it is not a boundary.
    """
    return [part for part in _CLAUSE_BOUNDARY.split(text) if part and part.strip()]


def _returned_call() -> ast.Call:
    """The one ``SetupStep(...)`` the probe is allowed to return.

    **A constraint on what the function may be, not a search of what it
    contains.** Two rounds went into making a reader see every string a probe
    could emit, and a reader cannot. Moving the retired strings into a
    module-level helper and calling it from a reached arm --

    .. code-block:: python

        if Path(_.executable).exists():
            return _legacy_artifact_step()

    -- passed all ten tests, including the one written to prevent exactly that,
    while the real CLI emitted both retired strings on all three surfaces. Eight
    further shapes hide a string as well: a module constant, a ``dict`` lookup, a
    file read, an f-string placeholder, ``+`` / ``.format`` / ``%`` / ``.join``,
    a default or keyword-only argument value, a decorator argument. A
    ``functools.wraps`` decorator hides one completely, because
    :func:`inspect.getsource` unwraps to the inner function.

    So this stops reading content. It refuses any probe that is not one
    unconditional return of one ``SetupStep`` whose every argument is a literal
    or an enum member -- which makes the set of publishable strings decidable,
    and every rule below sound over it.

    A probe that legitimately needs a branch fails here. That is the point: a
    branch is a decision, and whoever closes #39 should have to make it in the
    open rather than inherit a rule that quietly stopped covering anything.
    """
    tree = ast.parse(inspect.getsource(probe_artifact_integrity))
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef), "probe_artifact_integrity is not a function"
    assert not function.decorator_list, (
        "probe_artifact_integrity is decorated. A decorator can replace the return value "
        "outright, and functools.wraps hides that from inspect.getsource -- so nothing "
        "below would be checking the strings a user sees."
    )

    body = function.body
    first = body[0] if body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        body = body[1:]

    assert len(body) == 1, (
        f"probe_artifact_integrity is no longer a single unconditional return "
        f"({[type(node).__name__ for node in body]}). Every rule in this module then "
        f"covers one arm at most. Add the arm deliberately and widen these rules with it."
    )
    statement = body[0]
    assert isinstance(statement, ast.Return), "the probe's one statement is not a return"

    call = statement.value
    assert isinstance(call, ast.Call), "the probe does not return a directly constructed step"
    assert isinstance(call.func, ast.Name) and call.func.id == "SetupStep", (
        "the probe returns something other than a directly constructed SetupStep; a "
        "helper's strings are invisible here"
    )
    assert not call.args, "SetupStep is built with positional arguments this module cannot read"
    for keyword in call.keywords:
        assert isinstance(keyword.value, ast.Constant | ast.Attribute), (
            f"`{keyword.arg}` is computed ({type(keyword.value).__name__}), so its published "
            f"value is not decidable from the source and the rules below would miss it"
        )
    return call


def _published_literals() -> tuple[str, ...]:
    """Every string the probe can publish.

    Sound only because :func:`_returned_call` has already refused every shape in
    which this set is not decidable.
    """
    literals = tuple(
        keyword.value.value
        for keyword in _returned_call().keywords
        if isinstance(keyword.value, ast.Constant)
        and isinstance(keyword.value.value, str)
        and keyword.value.value.strip()
    )
    assert literals, "no publishable string found in the probe; the rules below check nothing"
    return literals


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


def _published(context: SetupContext) -> dict[str, object]:
    """The step exactly as ``theurian setup --json`` publishes it.

    Through :meth:`SetupService.run`, not by wrapping the probe's return value in
    a report. The two are not the same object: :meth:`SetupService._probe` zeroes
    ``paths`` for a step declared with ``apply=None``, which this one is. A
    reconstructing helper therefore published a ``paths`` value no user can ever
    see -- and would have required it to be written into ``release.md``, and then
    passed.
    """
    payload = SetupService(context).run(SetupRequest(dry_run=True)).to_json()
    steps = payload["steps"]
    assert isinstance(steps, list), "SetupReport.to_json no longer publishes a `steps` array"

    found = [
        step for step in steps if isinstance(step, dict) and step.get("id") == "artifact-integrity"
    ]
    assert len(found) == 1, f"the report carries {len(found)} artifact-integrity steps, not one"
    return found[0]


def _fenced_blocks(document: str) -> list[tuple[str, str]]:
    """Every fenced block, as (language, body). Language is lowercased."""
    return [
        (match.group("lang").lower(), match.group("body")) for match in _FENCE.finditer(document)
    ]


def _json_objects(document: str) -> list[tuple[str, dict[str, object]]]:
    """Fenced blocks that claim to be JSON and parse to an object.

    A block that does not parse -- an elision, a top-level array -- is skipped
    here and still read as raw text by
    :func:`test_no_fenced_block_in_the_release_document_holds_a_retired_claim`,
    so ``...`` in the shipped block cannot be used to hide one.
    """
    objects: list[tuple[str, dict[str, object]]] = []
    for lang, body in _fenced_blocks(document):
        if lang not in _JSON_LANGS:
            continue
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            objects.append((body, parsed))
    return objects


# -- The shape everything else rests on -------------------------------------


def test_the_probe_keeps_a_shape_whose_published_strings_are_decidable() -> None:
    """The closure argument for this module, as an assertion.

    Read :func:`_returned_call` for why a content scan cannot be that argument.
    """
    arguments = {keyword.arg for keyword in _returned_call().keywords}

    assert {"step_id", "status", "summary", "detail"} <= arguments


# -- What #39 rewrites ------------------------------------------------------
#
# Both tests below become false when verification exists, and that is deliberate:
# a status and a premise are decisions, and closing #39 should have to state them
# rather than inherit them. Everything in the next section is written to survive
# that change -- so #39 rewrites two tests and inherits seven, and the shape pin
# above is what makes it notice.


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


def test_the_published_strings_are_the_ones_the_rules_read(tmp_path: pathlib.Path) -> None:
    """Ties :func:`_published_literals` to what the step actually returns."""
    step = probe_artifact_integrity(_context(tmp_path))
    literals = _published_literals()

    assert step.summary in literals, "the summary is not among the probe's decidable strings"
    assert step.detail in literals, "the detail is not among the probe's decidable strings"


def test_the_step_cannot_assert_a_retired_claim() -> None:
    for literal in _published_literals():
        for retired in RETIRED_CLAIMS:
            assert retired not in literal, f"the step asserts a retired claim again: {retired}"


def test_the_step_never_makes_a_release_record_the_subject_of_a_claim() -> None:
    """A checksum may be something to check *against*, never something that exists.

    One grammar, applied per clause. It catches every phrasing of the retired
    summary that keeps its noun -- "no manifest exists", "there is no SHA256SUMS
    yet", "in the absence of a signed manifest", "ships with no checksums" -- and
    nothing about a phrasing that drops it, or that puts the false half in a
    clause carrying no noun at all. The module docstring lists what escapes.
    """
    for literal in _published_literals():
        for clause in _clauses(literal):
            for match in _RECORD_NOUN.finditer(clause):
                assert _GOVERNING.search(clause[: match.start()]), (
                    f"a release record is the subject of a claim, which is a claim that "
                    f"turns at the first core-v* tag: {clause!r}"
                )


def test_the_step_promises_no_schedule() -> None:
    """A string cannot own a date. The retired ``detail`` tried and came due."""
    for literal in _published_literals():
        promises = [match.group(0) for match in _SCHEDULE.finditer(literal)]
        assert not promises, f"the step promises a schedule nobody owns: {promises}"


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
        raw for raw, block in _json_objects(document) if block.get("id") == "artifact-integrity"
    ]

    assert len(quoted) == 1, (
        f"docs/contributing/release.md no longer quotes exactly one parseable "
        f"artifact-integrity step; it quotes {len(quoted)}"
    )
    assert quoted[0] == json.dumps(_published(_context(tmp_path)), indent=2, sort_keys=True)


def test_no_fenced_block_in_the_release_document_holds_a_retired_claim() -> None:
    """The equality above binds one block, found by its ``id`` and its language.

    Everything else escaped it: a second block under another id
    (``artifact-integrity-legacy``), a ``JSON`` or ``jsonc`` or bare fence, a
    ``text`` fence, and a block carrying ``...`` so that it no longer parses.
    This reads **every** fenced block as raw text. Prose in that file quotes the
    retired claims deliberately, to say what they used to be; a fenced block is
    read as output.
    """
    document = RELEASE_DOC.read_text(encoding="utf-8")

    for lang, body in _fenced_blocks(document):
        for retired in RETIRED_CLAIMS:
            assert retired not in body, (
                f"a fenced `{lang or 'plain'}` block in docs/contributing/release.md "
                f"presents a retired claim as output: {retired}"
            )
