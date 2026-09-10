"""The review search store's own records, held to the tree they describe (#479).

Two claims live here, and they are the two halves of one correction.

**The race carrier.** ``SqliteReviewSearchStore.search``'s docstring used to say
that ``mode=ro`` "binds this connection to the file that existed when it opened",
and that a rebuild landing mid-call therefore cannot split the call across two
stores. The property is real; the mechanism named was not. PR #630 round 1 ran
the same race against a plain read-write connect and it held identically, so the
sentence named a flag that could be dropped without the behaviour moving -- and a
reader auditing the race would have gone to the wrong line. The docstring now
names the two things that do carry it: the stamp and the rows read on one
connection, and publish by ``os.replace`` leaving an open descriptor on the inode
it already holds. A regression to the ``mode=ro`` attribution is what this
catches, and it catches it in both directions -- the correction lost, and the
correction's own wording lost.

**The citations that correction leaned on.** The same docstring stopped arguing
and started pointing: three tests, named, one per half plus the guarantee
``mode=ro`` really does carry. A name in a durable record is a promise that the
reader can go and look, and a name left behind by a rename resolves to nothing
while reading exactly like a discharged obligation -- the failure
``test_adr_0030_claims.py`` catches for ADR-0030's Compliance section and
``test_review_ingest_changelog_claims.py`` records for the changelog. Nothing
was resolving these, and T-19's sidecar paragraph cites a fourth test the same
way. All four are resolved here, out of one population, because the failure is
the same failure and splitting it would leave whichever half nobody remembered.

**Both prose arms carry a positive control.** A pin whose expected answer is
"the fragment is still there" and a pin that has stopped looking read
identically from the outside, so each row's own reversion is planted into a copy
and the same checker is asked about it. Nothing in ``src/`` is written.

**What this does not hold.** That the race property is *true* -- that is
behaviour, and it is measured by
``tests/integration/test_review_search_rebuild.py::``
``test_a_rebuild_that_lands_mid_call_answers_from_the_store_the_call_opened``
and its rename sibling, which are two of the names resolved below. A docstring
saying the right thing about a store that had stopped doing it would pass every
arm here.

**And a bound on the resolver**, recorded rather than implied: it resolves a
citation by its **file name**, and additionally requires the written directory
part -- when there is one -- to be a suffix of where the file was found. It
cannot tell whether the named test tests what the sentence beside it says it
tests. No mechanical check reaches that.

Pure: it reads two documents and one module's docstrings, and parses the test
tree's ``.py`` files for their function names -- no database, socket or temporary
directory.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import re
from collections.abc import Iterator
from typing import Final

import pytest
from threat_model_claims import entry
from write_lock_claims import REPO_ROOT

from theurian.infrastructure.sqlite.review_search_store import SqliteReviewSearchStore

pytestmark = pytest.mark.unit

#: Where a citation's file name is resolved from.
TEST_ROOT: Final = REPO_ROOT / "packages" / "theurian-core" / "tests"

#: A ``file.py::test_name`` citation, with the path optional and whitespace
#: allowed after the ``::``.
#:
#: Both slacknesses are real prose rather than edge cases. A docstring wraps its
#: citation across two code spans -- ``test_x.py::`` on one line and the test name
#: on the next -- so after collapsing there is a space in the middle; and a
#: sibling in the same file is written ``::test_y``, which is how a reader reads
#: it too. :func:`_citations` resolves a bare reference against the last file
#: named, the way the paragraph means it.
_CITATION: Final = re.compile(
    r"(?:(?P<path>[A-Za-z0-9_./-]*test_[A-Za-z0-9_]+\.py))?::\s*(?P<name>test_[a-z0-9_]+)"
)

#: The threat-model entry whose sidecar paragraph cites the fourth test.
_SIDECAR_THREAT_ID: Final = "T-19"


def _flattened(text: str) -> str:
    """*text* with its soft wraps flattened, markup and case both preserved.

    Not ``write_lock_claims.collapsed``, which also lowercases: a test name and a
    file path are case-sensitive, and a citation whose case changed is a citation
    that no longer resolves -- so lowercasing would hide the very drift the
    resolver below exists to report.

    Flattening is not optional either. Both records soft-wrap, and every sentence
    and citation held here breaks across two or three source lines: an unflattened
    search misses the real wording and passes over a record that says the
    opposite. The first draft of this module matched raw text and three of its
    four rows passed only because their sentences happened to fit on one line.
    """
    return " ".join(text.split())


def _stripped(text: str) -> str:
    """*text* flattened with its code spans' backticks removed.

    Backticks go because every citation in both sources is written inside a code
    span, and a reference split across two spans -- ``test_x.py::`` then the name
    -- is one reference to a reader.
    """
    return _flattened(text.replace("`", ""))


def _search_docstring() -> str:
    """``SqliteReviewSearchStore.search``'s docstring, raw.

    Read off the imported class rather than out of the file, so a second
    definition of the method -- a subclass, a monkeypatch at import, a module
    reorganised so the shipped symbol comes from somewhere else -- is what this
    reads, which is what a caller gets.
    """
    doc = inspect.getdoc(SqliteReviewSearchStore.search)

    assert doc, (
        "`SqliteReviewSearchStore.search` has no docstring, so every arm below would "
        "scan an empty string and report the absence of a regression it never looked "
        "for. The method's contract -- its bounds, its refusals and the race it does "
        "not have -- is written nowhere else"
    )
    return doc


def _citations(text: str) -> tuple[tuple[str, str], ...]:
    """Every ``(file name, test name)`` *text* cites, bare references resolved.

    The file is returned as the source spells it. A bare ``::name`` takes the
    last path seen, and one with no path before it comes back with an empty file
    so the caller reports it by name rather than resolving it against something
    arbitrary.
    """
    found: list[tuple[str, str]] = []
    current = ""
    for match in _CITATION.finditer(_stripped(text)):
        if match.group("path"):
            current = match.group("path")
        found.append((current, match.group("name")))
    return tuple(found)


def _defined_tests(path: pathlib.Path) -> frozenset[str]:
    """Every test function name *path* defines, at module level or in a class.

    An AST lookup rather than an import, for the reason ``test_adr_0030_claims``
    gives: importing a test module to ask what it defines runs its module-level
    code inside a test whose subject is a docstring, and a collection error in an
    unrelated module would then read as a broken citation here.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    return frozenset(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    )


def _unresolved(source: str, text: str) -> Iterator[tuple[str, str, str]]:
    """Each citation in *text* that does not resolve, with the reason."""
    for written, name in _citations(text):
        if not written:
            yield source, name, "the citation names no file and none precedes it"
            continue
        candidates = sorted(TEST_ROOT.rglob(pathlib.PurePosixPath(written).name))
        if len(candidates) != 1:
            yield source, name, f"{written} matches {len(candidates)} files under tests/"
            continue
        found = candidates[0].relative_to(REPO_ROOT).as_posix()
        if not found.endswith(written):
            yield source, name, f"{written} is written as a path, and the file is at {found}"
        elif name not in _defined_tests(candidates[0]):
            yield source, name, f"{written} defines no such test"


def _assert_the_record_states(label: str, text: str, fragment: str) -> None:
    """The prose check, in one place so its positive control drives the same path.

    A helper rather than an inline assertion, the shape
    ``test_review_ingest_changelog_claims.py`` arrived at: the control below
    exercises this exact comparison and this exact message, instead of a
    re-derived approximation that could pass while the real one had stopped
    looking.
    """
    assert fragment in text, (
        f"{label}: the record no longer states:\n\n  {fragment}\n\n"
        f"This is the RECORD half and it is blind to behaviour -- it matches spelling "
        f"and nothing else, so a RED here says the sentence drifted, not that the "
        f"store did.\n\n"
        f"The behaviour half is `tests/integration/test_review_search_rebuild.py`'s "
        f"mid-call race and its rename sibling. If they are GREEN, nothing moved and "
        f"the wording is what gets restored -- never by relaxing this row."
    )


#: Each sentence the ``search`` docstring has to keep, and the reversion it
#: guards against.
#:
#: **Whole clauses, not keywords**, for the reason the changelog pins record: the
#: failure guarded here is a *reword* that keeps the object spelled and loses what
#: is said about it, and a keyword match passes exactly that.
#:
#: The ``drift`` column is not decoration -- it is the specific reversion each row
#: exists to catch, and it is what the positive control plants. The first row's
#: drift is the sentence that was really there before PR #630 round 1, quoted
#: rather than invented, because a branch sha is orphaned by the squash that
#: merges it and a quotation survives.
_DOCSTRING_CLAIMS: Final[tuple[tuple[str, str, str], ...]] = (
    (
        "the race is not attributed to mode=ro",
        "Two things carry that, and neither of them is ``mode=ro``.",
        (
            "``mode=ro`` binds this connection to the file that existed when it opened, "
            "and :meth:`replace_all` publishes by ``os.replace`` onto that name."
        ),
    ),
    (
        "the first carrier is one connection for the stamp and the rows",
        "**the stamp and the rows are read on one connection**",
        "``mode=ro`` keeps this connection on the file it opened",
    ),
    (
        "the second carrier is publish by rename",
        "**publishes by ``os.replace``**",
        "the previous file stays readable because nothing truncates it",
    ),
    (
        "the correction records that it was measured",
        "a plain read-write connect holds the same property (PR #630 round 1, 2026-09-10)",
        "``mode=ro`` is what makes this safe, and it is not negotiable",
    ),
)


@pytest.mark.parametrize(
    ("label", "fragment", "drift"),
    _DOCSTRING_CLAIMS,
    ids=[case[0] for case in _DOCSTRING_CLAIMS],
)
def test_the_search_docstring_still_names_the_real_race_carriers(
    label: str, fragment: str, drift: str
) -> None:
    """RED means the store is back to crediting ``mode=ro`` for a race it does not carry.

    This is not a wording preference. ``mode=ro``'s real job is stated separately
    and is real -- it stops a serving read conjuring an empty database at a path
    whose file is gone -- so a docstring that credits it with the race as well
    sends the next reader to a line that has nothing to do with the property, and
    invites a change that drops the mode as redundant while leaving the race
    argument standing on it.

    The four rows are the correction's four load-bearing sentences: the denial,
    each carrier, and the measurement that settled it. The denial alone would let
    a rewrite delete both carriers; the carriers alone would let the ``mode=ro``
    attribution come back beside them.

    ``drift`` is unused here and is the subject of the control below; it is
    carried in the same row so the claim and the reversion it guards are read in
    one place.
    """
    docstring = _flattened(_search_docstring())

    _assert_the_record_states(label, docstring, fragment)


@pytest.mark.parametrize(
    ("label", "fragment", "drift"),
    _DOCSTRING_CLAIMS,
    ids=[case[0] for case in _DOCSTRING_CLAIMS],
)
def test_a_drifted_search_docstring_is_reported_by_the_same_checker(
    label: str, fragment: str, drift: str
) -> None:
    """The positive control: each row above has to be capable of going RED.

    Widen a fragment until every draft contains it, or normalise the text until
    the comparison is trivially true, and the row passes forever -- and a green
    that cannot go red looks exactly like a docstring nobody has touched.

    So each row's own reversion is planted into a copy of the docstring and the
    same checker is asked about it. The plant is in memory; ``src/`` is never
    written, because a pin that had to edit the tree to prove it works would be a
    worse instrument than no pin.

    Two guards before the plant, because a substitution that does not land
    reports its own no-op as a pass: the drift must not itself contain the
    fragment, and the replacement must actually change the text.
    """
    docstring = _flattened(_search_docstring())

    drifted = docstring.replace(fragment, drift)

    assert fragment not in drift, (
        f"{label}: the drift contains the fragment it is supposed to displace, so this "
        f"control would assert nothing. Rewrite the drift as the sentence the reversion "
        f"would leave behind."
    )
    assert drifted != docstring, (
        f"{label}: planting the drift changed nothing, so this control passed without "
        f"exercising the checker. Either the fragment is absent -- in which case "
        f"`test_the_search_docstring_still_names_the_real_race_carriers` is the RED that "
        f"matters -- or the drift is byte-identical to it."
    )
    with pytest.raises(AssertionError, match=re.escape(label)):
        _assert_the_record_states(label, drifted, fragment)


def test_every_test_the_review_search_records_cite_by_name_resolves() -> None:
    """RED means a durable record points at a test nobody can open.

    Both records stopped arguing and started pointing, which is the form this
    project uses to make a claim checkable rather than aspirational -- and that
    form has exactly one failure mode, and it is silent. A name that never
    existed, a name left behind by a rename, or a name spelled from memory reads
    like a discharged obligation. The only way to notice is to go and look.

    Two sources, one population, because the failure is one failure: the
    ``search`` docstring's three, and the fourth that T-19's sidecar paragraph
    cites for the measurement behind its "load-bearing rather than redundant"
    conclusion. Splitting them would leave whichever half nobody remembered.

    The premises come first. This reports its result as an empty list of
    failures, which is also what a resolver that extracted nothing produces, so
    the citations are asserted to have been found and to number what the two
    records carry -- and the count is asserted as a *floor* rather than an
    equality, because a record gaining a citation is a good thing that should not
    be a RED.
    """
    sources = (
        ("`SqliteReviewSearchStore.search`'s docstring", _search_docstring()),
        (f"the threat model's {_SIDECAR_THREAT_ID} sidecar paragraph", entry(_SIDECAR_THREAT_ID)),
    )

    for label, text in sources:
        assert _citations(text), (
            f"{label} cites no `<file>.py::test_name` at all, so the resolver below "
            f"would report no broken citations over nothing. That record stopped "
            f"pointing at the tests that hold it, which is the state this pin exists to "
            f"notice rather than a reason to delete it"
        )

    broken = tuple(failure for label, text in sources for failure in _unresolved(label, text))

    assert broken == (), (
        "a review search record cites tests that do not resolve:\n"
        + "\n".join(f"  {source} -- {name}: {why}" for source, name, why in broken)
        + "\n\nA name in a durable record is a promise that the reader can open the "
        "test and read it. Fix the citation, or -- if the test was deliberately "
        "removed -- say in the record what now holds the claim, because a sentence "
        "that cites nothing is back to being an argument."
    )


def test_the_citation_resolver_reports_a_name_no_test_file_defines() -> None:
    """The positive control: a clean result above has to be capable of being dirty.

    The assertion beside this one expects an empty tuple, which is what a
    resolver that extracted nothing, resolved nothing, or silently swallowed a
    missing file would also produce.

    So the same functions are run over synthetic text carrying one citation that
    resolves and three that cannot: a file no test tree has, a name the file does
    not define, and a bare sibling citation inheriting a file that does exist.
    The resolving citation is real on purpose -- a control built only from broken
    inputs proves the resolver can say no and not that it can say yes, and a
    resolver that reported *everything* would make the pin above red for the wrong
    reason on its first honest failure.
    """
    text = (
        "``test_review_search_store_claims.py::``\n"
        "``test_the_citation_resolver_reports_a_name_no_test_file_defines`` and "
        "``::test_a_sibling_that_was_never_written``, with "
        "``test_a_module_nobody_wrote.py::test_anything_at_all`` and "
        "``test_review_search_store_claims.py::test_a_name_this_file_does_not_define``."
    )

    broken = {(name, why) for _source, name, why in _unresolved("synthetic", text)}

    assert {name for name, _why in broken} == {
        "test_a_sibling_that_was_never_written",
        "test_anything_at_all",
        "test_a_name_this_file_does_not_define",
    }, (
        f"the resolver reported {sorted(broken)} over synthetic text with one resolving "
        f"citation and three that cannot resolve. It has stopped discriminating, so a "
        f"green result from "
        f"`test_every_test_the_review_search_records_cite_by_name_resolves` means "
        f"nothing until this is fixed."
    )
