"""What ADR-0030's record claims, held against the tree (#479).

Four claims live here, and they have one thing in common: each is a sentence a
reader takes as settled, in a document nothing was checking.

- **The Compliance section names the test that discharges each owed item.** A
  name in a governed record is a promise that the reader can go and look, and a
  name that resolves to nothing is worse than no name -- it reports coverage that
  cannot be inspected. :func:`test_every_test_the_compliance_section_names_resolves`
  resolves every ``path::test_name`` reference in that section against the live
  test tree.
- **``KnowledgeCandidate`` is constructed nowhere in ``src/``.** ``README.md``
  and ``docs/architecture/review-knowledge.md`` both state it, because it is the
  sentence that says review *ingestion* is not review *promotion*: ADR-0013's
  direction (an AI proposes, a human approves) rests on nothing in the shipped
  package building a candidate at all.
- **``.theurian/review/`` has one writer.** ``SECURITY.md``'s retention paragraph
  says an upstream deletion does not propagate *because* nothing else revisits a
  landed file, and that is a claim about the source tree rather than about a
  policy: what makes it true is that no code exists which could delete or rewrite
  a record the run did not fetch.
- **The slice-3 round record's HIGH is discharged, and the release gate it held
  is withdrawn** (#636). A round record mixes history with claims about the
  future, and only the second kind goes stale: "the 0.2.0 cut does not happen
  until it is fixed and re-verified" was true when the round closed and false the
  moment the guard was rekeyed.
  :func:`test_the_slice_three_round_record_states_the_discharge_it_owes` holds the
  discharge and refuses both withdrawn clauses.

**The first three are asserted from both sides *here*.** The document has to keep
saying it (a fragment pin, which holds spelling and is blind to truth), and the
tree has to keep making it true (a scan, which is blind to the document). Neither
half is sufficient, which is the split ``test_config_key_call_sites.py`` records
at length and the reason its rows are not folded into one test here.

**The fourth has no fact side in this module, and its reach is stated rather than
left to be inferred.** It holds the *record's* wording; what the guard is keyed
on is held by ``unit/test_review_search_builder_claims.py``, which reads the
condition out of the syntax tree, and the behaviour by
``integration/test_review_build_empty_publish.py``, which drives both faces
through the shipped CLI. Every fragment of the fourth claim would match word for
word against a build whose guard had been keyed back on the read.

**The scans' bound, stated once.** They read names out of syntax trees. A
construction reached through ``getattr``, a deletion spelled through
``os.system`` or a helper in another module, and a path rebuilt from string
literals rather than from ``ProjectPaths.review`` are all invisible. That is a
floor on the review a change gets, not a proof that the shape cannot exist --
the same bound ``test_network_call_sites.py`` and ``test_gate_call_sites.py``
record for theirs. Each scan whose expected answer is *nothing* carries a
positive control that plants the shape it claims to see, because a broken
extractor and a clean tree are indistinguishable from the outside.

Pure: it parses the shipped ``.py`` files and four documents as text, and opens
no database, no socket and no temporary directory.
"""

from __future__ import annotations

import ast
import collections
import pathlib
import re
from collections.abc import Iterator
from typing import Final

import pytest

import theurian

pytestmark = pytest.mark.unit

#: The package as *imported*, for the reason ``test_config_key_call_sites.py``
#: gives: a hand-built relative path can drift from the installed package and
#: would then scan a directory with nothing in it whatever the source did.
SRC: Final = pathlib.Path(theurian.__file__).resolve().parent

#: ``parents[4]`` is ``.../tests/unit/`` -> ``tests`` -> ``theurian-core`` ->
#: ``packages`` -> repo root.
REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]

ADR_0030: Final = REPO_ROOT / "docs" / "adr" / "0030-github-review-ingestion-spawns-gh.md"
README: Final = REPO_ROOT / "README.md"
SECURITY_MD: Final = REPO_ROOT / "SECURITY.md"
REVIEW_KNOWLEDGE: Final = REPO_ROOT / "docs" / "architecture" / "review-knowledge.md"

#: Where the Compliance section's relative paths are rooted.
#:
#: The section says so itself -- *"Paths are ``packages/theurian-core/tests/``"* --
#: and that sentence is asserted below rather than assumed, because a resolver
#: whose base is written only here would keep resolving after the document moved
#: its own base and would then be checking names against the wrong tree.
TEST_ROOT: Final = REPO_ROOT / "packages" / "theurian-core" / "tests"
TEST_ROOT_SENTENCE: Final = "Paths are `packages/theurian-core/tests/`."


def _collapsed(text: str) -> str:
    """Runs of whitespace flattened to single spaces, case preserved.

    These documents are line-wrapped Markdown, so a sentence routinely breaks
    across two source lines and a raw substring match would miss the real wording
    and pass vacuously.
    """
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# The Compliance section's test names (ADR-0030, and #603's future slice-1 fill).
# ---------------------------------------------------------------------------

#: A ``path::test_name`` reference, with the path optional.
#:
#: The bare form is real prose rather than an edge case: the section writes
#: ``...py::test_a`` once and then ``::test_b`` for each sibling in the same file,
#: which is how a reader reads it too. :func:`_named_tests` resolves a bare
#: reference against the last file named, so a sibling list inherits its file the
#: way the paragraph means it to.
_REFERENCE: Final = re.compile(
    r"(?:(?P<path>[A-Za-z0-9_./-]*test_[A-Za-z0-9_]+\.py))?::(?P<name>test_[a-z0-9_]+)"
)

#: The heading the section opens with, and the one that would end it.
#:
#: Sliced by heading rather than read to end of file, so a section appended after
#: Compliance does not silently join it -- and so an empty slice fails loudly at
#: :func:`test_the_compliance_section_is_findable_and_names_its_own_test_root`
#: rather than reporting no references and reading as compliance.
_COMPLIANCE_HEADING: Final = "\n## Compliance\n"
_NEXT_HEADING: Final = re.compile(r"^## ", re.MULTILINE)


def _compliance_section(text: str) -> str:
    """ADR-0030's Compliance section, from its heading to the next one."""
    start = text.find(_COMPLIANCE_HEADING)
    if start < 0:
        return ""
    body = text[start + len(_COMPLIANCE_HEADING) :]
    following = _NEXT_HEADING.search(body)
    return body[: following.start()] if following else body


def _named_tests(section: str) -> tuple[tuple[str, str], ...]:
    """Every ``(path, test name)`` the section names, bare references resolved.

    Paths are returned as the document spells them, relative to
    :data:`TEST_ROOT`. A bare ``::name`` takes the last path seen, and a bare one
    with no path before it is returned with an empty path so the caller reports it
    by name rather than resolving it against something arbitrary.
    """
    found: list[tuple[str, str]] = []
    current = ""
    for match in _REFERENCE.finditer(_collapsed(section)):
        if match.group("path"):
            current = match.group("path")
        found.append((current, match.group("name")))
    return tuple(found)


def _defined_tests(path: pathlib.Path) -> frozenset[str]:
    """Every test function name ``path`` defines, at module level or in a class.

    An AST lookup rather than an import, deliberately. Importing a test module to
    ask what it defines runs its module-level code -- fixtures, ``CliRunner``
    construction, path resolution -- inside a test whose subject is a document,
    and a collection error in an unrelated module would then read as a broken
    reference here.

    Parametrisation is invisible to this and does not need to be visible: the
    section names functions, and a parametrised function is one function whatever
    ids pytest generates for it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    return frozenset(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    )


def _unresolved(section: str) -> Iterator[tuple[str, str, str]]:
    """Each reference in ``section`` that does not resolve, with the reason."""
    for relative, name in _named_tests(section):
        if not relative:
            yield "", name, "the reference names no file and none precedes it"
            continue
        path = TEST_ROOT / relative
        if not path.is_file():
            yield relative, name, f"{path.relative_to(REPO_ROOT)} does not exist"
            continue
        if name not in _defined_tests(path):
            yield relative, name, f"{relative} defines no such test"


def test_the_compliance_section_is_findable_and_names_its_own_test_root() -> None:
    """The population control, asserted before anything is resolved.

    :func:`test_every_test_the_compliance_section_names_resolves` reports its
    result as an empty list of failures, and a section that could not be located
    -- renamed heading, moved file, a slice that ends at the wrong place --
    produces exactly the same empty list. So the section is asserted to exist and
    to carry references before the absence of failures is allowed to mean
    anything.

    The base path is asserted for the same reason from the other side. The
    resolver joins each relative path onto ``packages/theurian-core/tests/``
    because the section says that is where they are rooted; a document that moved
    its base without moving that sentence would have every reference resolved
    against the wrong tree, and the failures -- or the passes -- would be about
    files nobody named.
    """
    section = _compliance_section(ADR_0030.read_text(encoding="utf-8"))

    assert section, (
        "ADR-0030's `## Compliance` section could not be located, so the resolver "
        "below would report no broken references over no text at all. The heading "
        "moved, or the file did."
    )
    references = _named_tests(section)
    assert references, (
        "ADR-0030's Compliance section names no `path::test_name` reference at "
        "all. Slice 2 recorded five items with the test that discharges each, so "
        "an empty result means the section was rewritten to name none -- which is "
        "the state this pin exists to notice, not a reason to delete it."
    )
    assert TEST_ROOT_SENTENCE in _collapsed(section), (
        f"ADR-0030's Compliance section no longer states {TEST_ROOT_SENTENCE!r}. "
        f"That sentence is what tells a reader -- and this resolver -- where the "
        f"relative paths beside each item are rooted. Restore it, or move it and "
        f"`TEST_ROOT` in the same change."
    )


def test_every_test_the_compliance_section_names_resolves() -> None:
    """ADR-0030: a named discharge must be one a reader can open (#479, #603).

    The section moved slice 2's five owed items from *owed* to *landed* and named
    the test that discharges each, which is the form this project uses to make an
    ADR checkable rather than aspirational. That form has one failure mode, and it
    is silent: a name that never existed, a name left behind by a rename, or a
    name spelled from memory reads exactly like a discharged obligation. Nobody
    notices, because the only way to notice is to go and look -- which is what
    this does, every run.

    It matters beyond slice 2. Slice 1's list is currently marked shipped with its
    discharges *not yet recorded* ([#603](https://github.com/theurian/theurian/issues/603)),
    so the next change to this section is a fill of eleven or so names written
    against a tree they were not read from. This resolver is what that fill lands
    against.

    Resolved by parsing rather than by importing, and the reference grammar
    follows the paragraph's own: a bare ``::sibling`` belongs to the file last
    named. What is *not* asserted: that the named test actually tests what the
    bullet says it tests. No mechanical check reaches that, and pretending
    otherwise is the over-claim this docstring refuses to make.
    """
    section = _compliance_section(ADR_0030.read_text(encoding="utf-8"))

    broken = tuple(_unresolved(section))

    assert broken == (), (
        "ADR-0030's Compliance section names tests that do not resolve:\n"
        + "\n".join(f"  {relative}::{name} -- {why}" for relative, name, why in broken)
        + "\n\nA discharge named in a governed record is a promise that the reader "
        "can open the test and read it. Fix the reference, or -- if the test was "
        "deliberately removed -- move the item back to *owed* with the slice that "
        "will take it, which is what the section says a slice does with an item it "
        "did not reach."
    )


def test_the_resolver_would_report_a_name_no_test_file_defines() -> None:
    """The positive control: a clean result above has to be capable of being dirty.

    The assertion beside this one expects an empty tuple, which is what a resolver
    that extracted nothing, resolved nothing, or silently swallowed a missing file
    would also produce. So the same functions are run over a synthetic section
    carrying one reference that resolves and three that cannot, and each failure
    shape is asserted to be reported: a file that does not exist, a name the file
    does not define, and a bare sibling reference inheriting a file that does.

    The resolving reference is real on purpose. A control built only from broken
    inputs proves the resolver can say no and not that it can say yes, and a
    resolver that reported *everything* would make the pin above red for the wrong
    reason on its first honest failure.
    """
    section = (
        "Paths are `packages/theurian-core/tests/`. "
        "`unit/test_adr_0030_claims.py::test_every_test_the_compliance_section_names_resolves` "
        "and `::test_a_sibling_that_was_never_written`, with "
        "`unit/test_a_module_nobody_wrote.py::test_anything_at_all` and "
        "`unit/test_adr_0030_claims.py::test_a_name_this_file_does_not_define`."
    )

    broken = {(relative, name) for relative, name, _why in _unresolved(section)}

    assert broken == {
        ("unit/test_adr_0030_claims.py", "test_a_sibling_that_was_never_written"),
        ("unit/test_a_module_nobody_wrote.py", "test_anything_at_all"),
        ("unit/test_adr_0030_claims.py", "test_a_name_this_file_does_not_define"),
    }, (
        f"the resolver reported {sorted(broken)} over a synthetic section with one "
        f"resolving reference and three that cannot resolve. It has stopped "
        f"discriminating, so a green result from "
        f"`test_every_test_the_compliance_section_names_resolves` means nothing "
        f"until this is fixed."
    )


# ---------------------------------------------------------------------------
# The slice-3 round record: a HIGH discharged, and the release gate it withdrew.
# ---------------------------------------------------------------------------
#
# The Compliance section is a *round record*, and a round record has two kinds of
# sentence in it. What was true when the round closed -- round five under the
# fixed-round-budget ruling, CRITICAL zero throughout, one HIGH filed rather than
# fixed -- is history and stays in the past tense whatever happens next. What the
# round said about the *future* is not history: "the 0.2.0 cut does not happen
# until it is fixed and re-verified" and "is the first post-merge item" were
# present-tense claims about a release that had not been cut, and they went stale
# the moment the HIGH was discharged in this same pull request.
#
# A stale gate in a governed record is worse than an absent one. It is what a
# release manager reads to decide whether 0.2.0 may be cut, so a paragraph still
# naming an open blocker holds a release over a defect that was fixed -- and the
# same paragraph is what a rebase against a branch taken before the fix restores
# without a conflict, because nothing else in the file moved.


#: What the round record has to say now that the HIGH is discharged.
#:
#: Two fragments, because each carries a different half and each can be lost on
#: its own: the *disposition* (the gate is withdrawn, with the pull request that
#: withdrew it) and the *substance* (what the guard is now keyed on). A paragraph
#: carrying only the first tells a reader the blocker is gone and nothing about
#: what replaced it.
_SLICE_THREE_DISCHARGE: Final[tuple[str, ...]] = (
    "**That HIGH is discharged, so the 0.2.0 cut is no longer waiting on it**",
    (
        "The guard is keyed on `at_the_publish` — the listing taken inside the write "
        "section — together with the withholding outcome, and on nothing the read "
        "alone saw"
    ),
)

#: The two present-tense claims the discharge withdrew, quoted from `1751236a`
#: rather than invented -- the citation rule
#: ``test_review_ingest_changelog_claims.py`` records, since a branch sha is
#: orphaned by the squash that merges it and a quotation survives.
#:
#: Each is spelled as the **clause**, not as the whole sentence, because the
#: sentence around them was rewritten rather than deleted: the live paragraph
#: still names #636, still says it was filed rather than fixed, and still says it
#: touched none of the discharges. What changed is the tense and these two
#: clauses, so a fragment spanning more than the clause would be reporting the
#: rewrite instead of the reversion.
_WITHDRAWN_GATES: Final[tuple[tuple[str, str], ...]] = (
    (
        "the release gate",
        "the 0.2.0 cut does not happen until it is fixed and re-verified",
    ),
    (
        "the post-merge ordering claim",
        "is the first post-merge item",
    ),
)


def _assert_the_round_record_states_the_discharge(section: str) -> None:
    """The check, in one place so both positive controls drive the same path.

    A helper rather than an inline pair of loops, for the reason
    ``test_review_ingest_changelog_claims.py`` records: a control that re-derived
    an approximation of the comparison could pass while the real one had stopped
    looking.
    """
    for fragment in _SLICE_THREE_DISCHARGE:
        assert fragment in section, (
            f"ADR-0030's Compliance section no longer states:\n\n  {fragment}\n\n"
            f"This is the RECORD half and it is blind to behaviour. Without it the "
            f"slice-3 round record describes a HIGH that is still open and still "
            f"gating the 0.2.0 cut, which is what a release manager reads the section "
            f"to find out.\n\n"
            f"The behaviour half is `unit/test_review_search_builder_claims.py::"
            f"test_the_empty_publish_guard_is_keyed_on_the_publish_time_capture` and "
            f"the two faces in `integration/test_review_build_empty_publish.py`. "
            f"GREEN there means the guard is right and this wording is what gets "
            f"restored."
        )
    for label, withdrawn in _WITHDRAWN_GATES:
        assert withdrawn not in section, (
            f"ADR-0030's Compliance section carries {label} again:\n\n  {withdrawn}\n\n"
            f"That clause was a present-tense claim about a release that had not been "
            f"cut, and the HIGH it names was discharged in PR #637. Left standing it "
            f"holds 0.2.0 over a defect that is fixed. If the guard really was keyed "
            f"back on the read, the behaviour half named above is RED too and the code "
            f"is what gets restored -- not this paragraph."
        )


def test_the_slice_three_round_record_states_the_discharge_it_owes() -> None:
    """RED means the round record still gates a release on a discharged HIGH.

    **What this holds is the record's wording, and only that.** Every fragment
    here would match against a build whose guard had been keyed back on the read,
    and the paragraph would then be a true-looking sentence about a defect that
    had come back. The behaviour is held elsewhere and deliberately so:
    ``unit/test_review_search_builder_claims.py`` reads the guard's condition out
    of the syntax tree, and ``integration/test_review_build_empty_publish.py``
    drives both faces through the shipped CLI.

    Both directions, because either can move on its own. The discharge has to be
    stated, and neither withdrawn gate may come back -- and the second is the
    likelier failure, since the paragraph they lived in was rewritten in place
    rather than deleted, so a rebase against a pre-fix branch restores them with
    no conflict to notice.

    The premise comes first. The section is located by the same resolver the
    reference arms use, and a section that could not be found is an empty string
    -- in which every ``not in`` above passes and every ``in`` fails for a reason
    that has nothing to do with the document's wording.
    """
    section = _compliance_section(ADR_0030.read_text(encoding="utf-8"))

    assert section, (
        "ADR-0030's `## Compliance` section could not be located, so the discharge "
        "below would be checked against an empty string"
    )

    _assert_the_round_record_states_the_discharge(_collapsed(section))


@pytest.mark.parametrize(
    ("label", "withdrawn"),
    _WITHDRAWN_GATES,
    ids=[case[0] for case in _WITHDRAWN_GATES],
)
def test_the_round_record_pin_reports_each_withdrawn_gate(label: str, withdrawn: str) -> None:
    """The positive control for the absence direction: each clause can go RED.

    A row asserting a sentence is *absent* is satisfied by a document nobody is
    reading, by a clause that was never spelled the way the document spells it,
    and by a checker that stopped looking -- all three silently, and all three
    most convincingly at the moment they stop working.

    So each withdrawn clause is planted into a copy of the live section and the
    same checker is asked about it. Nothing under ``docs/`` is written. The guard
    before the plant is what makes the row mean something: the clause must not
    already be there, or the plant is a no-op and the RED below would be the pin's
    own arm firing on the real document.
    """
    live = _collapsed(_compliance_section(ADR_0030.read_text(encoding="utf-8")))

    assert withdrawn not in live, (
        f"{label}: the section already carries this clause, so the plant below changes "
        f"nothing -- and `test_the_slice_three_round_record_states_the_discharge_it_owes` "
        f"is the RED that matters"
    )
    with pytest.raises(AssertionError, match="carries"):
        _assert_the_round_record_states_the_discharge(f"{live} {withdrawn}")


def test_the_round_record_pin_reports_a_missing_discharge() -> None:
    """The positive control for the presence direction, over the real paragraph.

    The other half of the same worry. An assertion that a sentence is present
    passes against a checker that stopped comparing, and the section is read from
    a file that could have been reshaped under it -- so the live text is used and
    each discharge fragment is removed from it in turn, which is what a revert of
    the paragraph to its pre-#637 form would leave behind.

    Driven per fragment rather than over both at once: a checker that had come to
    require only the first would stay green against a paragraph that had lost the
    second, and the second is the one carrying what the guard is keyed on.
    """
    live = _collapsed(_compliance_section(ADR_0030.read_text(encoding="utf-8")))

    for fragment in _SLICE_THREE_DISCHARGE:
        reverted = live.replace(fragment, "")

        assert reverted != live, (
            f"removing {fragment!r} changed nothing, so this control exercised the "
            f"checker over the document unmodified"
        )
        with pytest.raises(AssertionError, match="no longer states"):
            _assert_the_round_record_states_the_discharge(reverted)


# ---------------------------------------------------------------------------
# `KnowledgeCandidate` is constructed nowhere in `src/` (ADR-0013, FR-V2/V3).
# ---------------------------------------------------------------------------

#: The domain type whose absence from the shipped call graph is the claim.
CANDIDATE_TYPE: Final = "KnowledgeCandidate"

#: The two documents that state it, and that must move when it stops being true.
#:
#: Both are named in the failure message rather than only here, because whoever
#: lands candidate generation reads the message and not this constant.
CANDIDATE_DOCUMENTS: Final = ("README.md", "docs/architecture/review-knowledge.md")


def _construction_sites(source: str, module: str) -> Iterator[tuple[str, int]]:
    """Every call to :data:`CANDIDATE_TYPE` in ``source``, as ``(module, line)``.

    Both spellings a construction takes: the bare name after
    ``from ... import KnowledgeCandidate``, and the attribute form
    ``review.KnowledgeCandidate(...)`` after a module import. A call and not a
    mention -- the type appears in a docstring in ``review/__init__.py`` and in
    its own ``class`` statement, and neither builds one.
    """
    for node in ast.walk(ast.parse(source, filename=module)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        named = (isinstance(func, ast.Name) and func.id == CANDIDATE_TYPE) or (
            isinstance(func, ast.Attribute) and func.attr == CANDIDATE_TYPE
        )
        if named:
            yield module, node.lineno


def test_nothing_in_the_shipped_package_constructs_a_knowledge_candidate() -> None:
    """ADR-0013: the shipped package proposes nothing, and this is the fact side.

    ``README.md`` reaches its "AI proposes, humans approve" row through this
    sentence: the fetch and landing halves of ADR-0030 write *review evidence*,
    never approved knowledge, and the evidence for that is that no code in
    ``src/`` builds a :class:`~theurian.domain.review.KnowledgeCandidate` at all.
    ``docs/architecture/review-knowledge.md`` says the same in its own words --
    "no code path generates a candidate".

    That is a claim about a call graph, and it is precisely the claim ADR-0030
    slice 2 puts under pressure: the ingestion path now runs, lands files, and
    sits one import away from the domain type it must not build. The day
    candidate generation arrives (FR-V2, FR-V3), both documents become false in
    the same commit -- and the point of this test is that the commit cannot land
    without meeting them.

    The type's *definition* and the docstrings naming it are not constructions and
    are deliberately not counted; what is counted is a call.
    """
    sites = tuple(
        site
        for path in sorted(SRC.rglob("*.py"))
        for site in _construction_sites(
            path.read_text(encoding="utf-8"), path.relative_to(SRC).as_posix()
        )
    )

    assert sites == (), (
        f"`{CANDIDATE_TYPE}` is constructed in the shipped package:\n"
        + "\n".join(f"  {module}:{line}" for module, line in sites)
        + "\n\nTwo documents say it is not, and each of them reaches a promise "
        "through that sentence:\n"
        + "\n".join(f"  {document}" for document in CANDIDATE_DOCUMENTS)
        + "\n\nREADME.md's `AI proposes, humans approve` row says the ingestion "
        "path writes review evidence and never approved knowledge, quoting the "
        "grep that answers nothing; review-knowledge.md says no code path "
        "generates a candidate. If candidate generation has landed (FR-V2, "
        "FR-V3), both move in this change -- and ADR-0013's direction needs "
        "restating with whatever now stands between a generated candidate and "
        "approved state."
    )


def test_the_construction_scan_sees_both_spellings_of_a_construction() -> None:
    """The positive control for a pin whose expected answer is nothing.

    A scan that resolved no calls at all would report the same empty tuple as a
    package that builds no candidate, and the difference is the whole value of the
    pin. Both call spellings are planted here, and the three shapes that are
    *not* constructions -- the class statement, a docstring naming the type, and
    an import of it -- are asserted invisible, because a scan that flagged those
    would be red on a clean tree and the only way back to green would be to stop
    naming the type in the documents that describe it.
    """
    seen = {
        module: tuple(line for _module, line in _construction_sites(source, module))
        for module, source in (
            ("imported.py", f"from theurian.domain.review import {CANDIDATE_TYPE}\n"),
            ("bare_call.py", f"candidate = {CANDIDATE_TYPE}(body=body)\n"),
            ("attribute_call.py", f"candidate = review.{CANDIDATE_TYPE}(body=body)\n"),
            ("definition.py", f"class {CANDIDATE_TYPE}:\n    pass\n"),
            ("mentioned.py", f'"""The {CANDIDATE_TYPE} type lives in domain/review.py."""\n'),
        )
    }

    assert seen == {
        "imported.py": (),
        "bare_call.py": (1,),
        "attribute_call.py": (1,),
        "definition.py": (),
        "mentioned.py": (),
    }, (
        f"the construction scan read the planted sources as {seen}. It is the "
        f"scanner that is broken, not the product: fix `_construction_sites` "
        f"before trusting a green result from "
        f"`test_nothing_in_the_shipped_package_constructs_a_knowledge_candidate`, "
        f"which would keep passing with a scanner that sees nothing."
    )


# ---------------------------------------------------------------------------
# `.theurian/review/` has one writer (ADR-0030 decision 3, R-12, SECURITY.md).
# ---------------------------------------------------------------------------

#: The package that owns every write under the review directory.
EVIDENCE_PACKAGE: Final = "infrastructure/review_evidence"

#: Where each module that reaches ``ProjectPaths.review`` sits, and what it does.
#:
#: **The honest output of an attribute scan, not a curated list of writers.** The
#: scan matches the name ``review`` in an attribute position and cannot tell a
#: paths object from any other, so a third member is not automatically a second
#: writer -- what it is, is a module somebody has to explain:
#:
#: * ``application/project_service.py`` **defines** the property. It composes the
#:   path and proves it contained; it opens nothing.
#: * ``cli/review_commands.py`` is the composition root, and the one place the
#:   directory is handed to anything: it constructs ``ReviewEvidenceStore`` with
#:   it and hands the store's methods to the service as opaque callables.
#: * ``domain/specification.py``'s ``self.review`` is a different ``review``
#:   entirely -- a ``PolicyRequirement`` field on a project specification, which
#:   never was a path. It is recorded rather than filtered because a filter would
#:   need the semantics this scan refuses, and because a rule that hid it would
#:   hide a future member for the same reason.
REVIEW_PATH_SITES: Final[tuple[tuple[str, str], ...]] = (
    ("application/project_service.py", "defines `ProjectPaths.review` and proves it contained"),
    ("cli/review_commands.py", "hands it to `ReviewEvidenceStore` and to nothing else"),
    ("domain/specification.py", "a `PolicyRequirement` field of the same name, not a path"),
)

#: The names a module would use to remove or move a landed file.
#:
#: Read as **names in any position**, not as resolved calls, so ``path.unlink``
#: passed as a callable counts as much as ``path.unlink()``. Over-broad in the RED
#: direction on purpose: every use has to be explained below, and a false RED on
#: an innocent identifier costs a read while a false green costs the claim.
_REMOVAL_NAMES: Final = frozenset(
    {"unlink", "rmdir", "rmtree", "remove", "removedirs", "rename", "replace", "truncate"}
)

#: The removal-shaped names the evidence package uses, and what each one moves.
#:
#: **The list was empty until the write became atomic**, and the change that made
#: it non-empty is the one that had to fill it in: the scan's own failure message
#: says so, because a rename that publishes a record is still a second way a
#: landed file changes and SECURITY.md's retention paragraph has to describe it.
#:
#: **Counted rather than listed.** A second ``unlink`` in the same module is a
#: second place a landed file can go, and a table keyed on the name alone would
#: carry it silently -- which is the whole failure mode this scan exists to catch.
PUBLISH_NAMES: Final[tuple[tuple[str, str, int, str], ...]] = (
    (
        "infrastructure/review_evidence/store.py",
        "replace",
        1,
        "`os.replace` publishes the temporary over the record. A rename onto the "
        "record's own path, which is decision 3's best-effort refresh and not a "
        "removal: nothing the run did not fetch is touched, and the previous copy "
        "survives an interrupted run because it is never truncated.",
    ),
    (
        "infrastructure/review_evidence/store.py",
        "unlink",
        1,
        "the `.writing` temporary is discarded when the write it belongs to does "
        "not publish. It removes the writer's own litter and never a record: the "
        "path is the one the write was handed and carries `_WRITING_SUFFIX` "
        "rather than `EVIDENCE_SUFFIX`, and since round two's R2-B an `lstat` in "
        "`_discard_the_temporary` narrows it further to a **regular file** -- so "
        "a link, pipe or socket somebody planted at that name survives the "
        "refusal that is about it instead of being removed before the operator "
        "can look at it.",
    ),
)


def _removal_names(source: str, module: str) -> Iterator[tuple[str, str, int]]:
    """Every removal-shaped name ``source`` uses, as ``(module, name, line)``."""
    for node in ast.walk(ast.parse(source, filename=module)):
        if not isinstance(node, ast.Attribute | ast.Name):
            continue
        name = node.attr if isinstance(node, ast.Attribute) else node.id
        if name in _REMOVAL_NAMES:
            yield module, name, node.lineno


def _names_the_review_path(node: ast.AST) -> bool:
    """Whether ``node`` names ``review`` in a position a paths object reaches it by.

    Two shapes, and the second is not an afterthought: ``paths.review`` is how a
    caller reaches the directory, and ``def review`` is where the path is
    *composed* -- a second definition anywhere would be a second composition of
    it, which is the same claim from the other end.
    """
    if isinstance(node, ast.Attribute):
        return node.attr == "review"
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return node.name == "review"
    return False


def _review_path_sites() -> tuple[str, ...]:
    """Every module of the shipped package naming ``review`` in one of those positions."""
    return tuple(
        sorted(
            {
                path.relative_to(SRC).as_posix()
                for path in sorted(SRC.rglob("*.py"))
                for node in ast.walk(
                    ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
                )
                if _names_the_review_path(node)
            }
        )
    )


def test_the_review_directory_is_reached_from_the_recorded_modules_only() -> None:
    """SECURITY.md's retention paragraph rests on there being one writer (R-12).

    The paragraph tells an operator that a comment deleted upstream *stays* in
    ``.theurian/review/`` as the run that fetched it landed it, and that "nothing
    else revisits a landed file, so a record no later run fetches does not
    change". An operator reads that as a retention guarantee and plans a deletion
    procedure around it -- one step today, delete the file.

    That guarantee is not enforced by a policy anywhere. It is true because of
    what the tree does *not* contain, and this is the half of that which can be
    measured: which modules reach the directory at all. Three do, and only one of
    them hands it to anything --
    :data:`REVIEW_PATH_SITES` records what each is.

    An equality, so it fails in both directions. A fourth module reaching the
    review root is a second place a landed file can be opened, and SECURITY.md's
    sentence is then a claim about a tree that has changed under it. A member
    disappearing means the composition root stopped reaching the directory, which
    is a different product.

    **What this cannot see**, and it is the honest bound rather than a hedge: a
    module that rebuilds ``.theurian/review`` from literals instead of asking
    ``ProjectPaths`` for it names no ``review`` attribute and is invisible here.
    ``test_project_paths_containment.py`` covers whether the path is contained;
    nothing covers whether somebody assembled their own.
    """
    sites = _review_path_sites()
    recorded = tuple(module for module, _why in REVIEW_PATH_SITES)

    assert sites == recorded, (
        f"the modules naming a `review` attribute are {list(sites)}, and the "
        f"recorded set is {list(recorded)}:\n"
        + "\n".join(f"  {module} -- {why}" for module, why in REVIEW_PATH_SITES)
        + "\n\nA NEW module: it reaches `.theurian/review/` -- or it names an "
        "unrelated `review` attribute, and this scan cannot tell them apart, "
        "which is why a new row states which it is. If it reaches the directory, "
        "SECURITY.md's `Nothing else revisits a landed file` now describes a tree "
        "with a second reacher in it; say what the new module does to a landed "
        "record before recording the site.\n\n"
        "A MISSING module: the composition root no longer hands the directory to "
        "the evidence store, so either review evidence is landing somewhere else "
        "or it is not landing at all."
    )


def test_the_evidence_package_moves_a_file_only_where_the_publish_records_it() -> None:
    """Decision 3's *refetch never deletes*, as the near-absence it actually is.

    ``ReviewEvidenceStore.write`` writes the records it is given and touches no
    record it was not given -- it never enumerates a repository's landed files to
    diff them, and it unlinks nothing but the temporary its own write opened.
    That is a claim about the package's contents and therefore checkable.

    It is the mechanism under a promise made to a person. SECURITY.md tells an
    operator that a comment edited or deleted on GitHub stays as it landed, and
    that removing an ingested comment is a manual step they perform. A refetch
    that quietly reconciled the directory against upstream would delete evidence
    of a review that a person is entitled to have kept -- data loss no refetch
    recovers, since the file *is* the record.

    Names in any position, so a removal handed round as a callable counts.
    :data:`PUBLISH_NAMES` carries the two the atomic publish needs and what each
    moves; an equality against it, so a **third** name and a **second** use of
    either recorded one both fail, and so does a recorded one that disappears.
    """
    package = SRC / EVIDENCE_PACKAGE
    found = collections.Counter(
        (module, name)
        for path in sorted(package.rglob("*.py"))
        for module, name, _line in _removal_names(
            path.read_text(encoding="utf-8"), path.relative_to(SRC).as_posix()
        )
    )
    recorded = collections.Counter(
        {(module, name): count for module, name, count, _why in PUBLISH_NAMES}
    )

    assert found == recorded, (
        f"`{EVIDENCE_PACKAGE}` moves or removes a file somewhere the publish does "
        f"not record. Found {dict(found)} against:\n"
        + "\n".join(
            f"  {module} -- {name} x{count}: {why}" for module, name, count, why in PUBLISH_NAMES
        )
        + "\n\nADR-0030 decision 3 is that a refetch updates what upstream still "
        "returns and never deletes what it no longer does, and SECURITY.md tells "
        "an operator so. If this is a rename used to publish a record atomically "
        "rather than a deletion, it is still a way a landed file changes and the "
        "retention paragraph has to describe it -- record the name in "
        "`PUBLISH_NAMES` with what it does, in the change that adds it."
    )


def test_the_removal_scan_sees_a_deletion_however_it_is_spelled() -> None:
    """The positive control, because the pin above expects to find nothing.

    Three spellings a deletion takes are planted -- a method call, the same
    method passed as a callable without being called, and the module-level
    ``os.remove`` form -- and one shape that is not a deletion is asserted
    invisible. Without this, a scan that had stopped resolving attributes would
    report the evidence package as clean forever, which is exactly what a package
    that deletes nothing also reports.
    """
    seen = {
        module: tuple(name for _module, name, _line in _removal_names(source, module))
        for module, source in (
            ("called.py", "target.unlink()\n"),
            ("passed.py", "cleanup(target.unlink)\n"),
            ("module_level.py", "os.remove(str(target))\n"),
            ("shutil_tree.py", "shutil.rmtree(directory)\n"),
            ("innocent.py", "document = json.dumps(payload)\n"),
        )
    }

    assert seen == {
        "called.py": ("unlink",),
        "passed.py": ("unlink",),
        "module_level.py": ("remove",),
        "shutil_tree.py": ("rmtree",),
        "innocent.py": (),
    }, (
        f"the removal scan read the planted sources as {seen}. Fix `_removal_names` "
        f"before trusting a green result from "
        f"`test_the_evidence_package_moves_a_file_only_where_the_publish_records_it`."
    )


# ---------------------------------------------------------------------------
# The prose half: the sentences those three scans are the fact side of.
# ---------------------------------------------------------------------------

#: Each claim's document and the wording that carries it.
#:
#: Spelling and nothing else, which is the split this project keeps separate on
#: purpose: every fragment here would match word for word against a tree that had
#: stopped making it true, and every scan above would stay green against a
#: document that had stopped saying it. Neither half is worth having alone -- the
#: first three rounds of #198 are the worked example, where four documents
#: described a control that did not run.
CLAIM_SURFACES: Final[tuple[tuple[str, pathlib.Path, tuple[str, ...]], ...]] = (
    (
        "README.md (AI proposes, humans approve)",
        README,
        (
            "`KnowledgeCandidate` is constructed nowhere in `src/`",
            '(`git grep -n "KnowledgeCandidate(" -- packages/theurian-core/src` answers nothing)',
            # The bound beside the claim: what the two shipped halves *do* write.
            # Dropping it leaves "constructed nowhere" reading as "the ingestion
            # path writes nothing", which is false since slice 2.
            (
                "what they write is review evidence under `.theurian/review/`, never "
                "approved knowledge"
            ),
        ),
    ),
    (
        "docs/architecture/review-knowledge.md (nothing collects into it yet)",
        REVIEW_KNOWLEDGE,
        ("no code path generates a candidate",),
    ),
    (
        "SECURITY.md (R-12 retention)",
        SECURITY_MD,
        (
            (
                "a later run refreshes what upstream still returns and **never deletes** "
                "what it no longer does"
            ),
            (
                "Nothing else revisits a landed file, so a record no later run fetches "
                "does not change."
            ),
            # The remediation the paragraph promises, which is only one step
            # because there is one writer and one copy. A rewrite that drops it
            # leaves the retention claim with no stated way out of it.
            "Today it is one step: delete that record's file under `.theurian/review/`.",
        ),
    ),
)


@pytest.mark.parametrize(
    ("label", "document", "sentences"),
    CLAIM_SURFACES,
    ids=[case[0] for case in CLAIM_SURFACES],
)
def test_each_document_still_states_the_claim_its_scan_holds(
    label: str, document: pathlib.Path, sentences: tuple[str, ...]
) -> None:
    """The prose half of the three scans above, in the shape #198 arrived at.

    A scan holds a property of the tree and is blind to what any document says
    about it. The failure that costs something is the other direction: the tree
    stays correct, the sentence is quietly deleted or softened in a rewrite, and
    the next reader has no reason to believe the property holds at all -- so the
    change after that removes it, and nothing objects.

    Both directions therefore have to be pinned separately, and this is the half
    that keeps the words. If a claim genuinely stopped being true, its scan above
    is what says so first; a fragment failing here while the scans are green means
    a document drifted, and the document is what gets fixed.
    """
    normalized = _collapsed(document.read_text(encoding="utf-8"))

    for sentence in sentences:
        assert sentence in normalized, (
            f"{label} no longer states {sentence!r}.\n\n"
            "This sentence is what a reader takes the property from -- that the "
            "shipped package builds no knowledge candidate, or that a landed "
            "review record is revisited by nothing. The fact side is in this same "
            "module and is green, so nothing in the tree moved: restore the "
            "wording, or move it together with the scan it belongs to."
        )
