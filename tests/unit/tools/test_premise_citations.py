"""The extraction grammar's eight kinds, one match apiece, plus its two safety rules.

`premise_citations.extract_citations` is the only place a later merge's silently
invalidated premise could be caught: whatever it fails to extract from an
issue's prose is never handed to `premise_check` at all (AC1 of the
issue-premise-sweep brief, PR #771). This file pins each kind's recall on its
own representative sentence, plus the two rules the grammar's own docstring
names explicitly -- that an ambiguous bare filename resolves to nothing rather
than a guess, and that a body/comment boundary cannot be crossed by a single
token.
"""

from __future__ import annotations

import premise_citations
import pytest

pytestmark = pytest.mark.unit

# Constructed positionally throughout, matching `premise_citations`'s own
# construction style -- a keyword `token=` reads to a linter as a possible
# hardcoded secret regardless of context.
Citation = premise_citations.Citation


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        (
            "See tools/premise_citations.py for the grammar.",
            Citation("path", "tools/premise_citations.py"),
        ),
        (
            "Reported at tools/premise_check.py:42 after the merge.",
            Citation("path_line", "tools/premise_check.py:42"),
        ),
        (
            "The failing case is `test_a_thing_holds` today.",
            Citation("test_name", "test_a_thing_holds"),
        ),
        ("`FETCH_LIMIT` moved when the driver split.", Citation("constant", "FETCH_LIMIT")),
        ("Landed at af3d40a4 last night.", Citation("sha", "af3d40a4")),
        ("See ADR-0033 for the reasoning.", Citation("adr", "ADR-0033")),
        ("Filed as #729 last month.", Citation("issue_ref", "#729")),
        ("`_verify_symbol` grades it.", Citation("symbol", "_verify_symbol")),
    ),
    ids=("path", "path_line", "test_name", "constant", "sha", "adr", "issue_ref", "symbol"),
)
def test_each_of_the_eight_kinds_is_extracted_from_its_own_sentence(
    text: str, expected: premise_citations.Citation
) -> None:
    found = premise_citations.extract_citations(text, (), tracked_basenames={})

    assert found == (expected,)


def test_the_same_citation_repeated_in_body_and_comments_appears_once() -> None:
    body = "See tools/premise_citations.py twice: tools/premise_citations.py."
    comments = ("Still tools/premise_citations.py.",)

    found = premise_citations.extract_citations(body, comments, tracked_basenames={})

    assert found == (Citation("path", "tools/premise_citations.py"),)


def test_citations_of_different_kinds_come_back_sorted_by_kind_then_token_not_by_position() -> None:
    """The result is a `set` internally (module docstring); nothing about a set's
    own iteration order may reach the caller, so this is a claim about the final
    `sorted()` call and not merely a coincidence of insertion order.
    """
    body = (
        "Filed as #999 first. "
        "Then ADR-0001. "
        "Then `ZCONST`. "
        "Then af3d40a4. "
        "Then test_zzz_case. "
        "Then tools/zzz.py."
    )

    found = premise_citations.extract_citations(body, (), tracked_basenames={})

    assert found == (
        Citation("adr", "ADR-0001"),
        Citation("constant", "ZCONST"),
        Citation("issue_ref", "#999"),
        Citation("path", "tools/zzz.py"),
        Citation("sha", "af3d40a4"),
        Citation("test_name", "test_zzz_case"),
    )


@pytest.mark.parametrize(
    ("text", "token"),
    (
        ("Fixed by tools/premise_report.py.", "tools/premise_report.py"),
        ("See docs/adr/ for the format.", "docs/adr"),
    ),
    ids=("trailing-period", "trailing-slash"),
)
def test_a_sentences_trailing_punctuation_is_not_part_of_the_path(text: str, token: str) -> None:
    found = premise_citations.extract_citations(text, (), tracked_basenames={})

    assert found == (Citation("path", token),)


def test_unique_basenames_keeps_only_the_one_unambiguous_entry() -> None:
    """Two files can share a basename; a mapping that picked the first or last of
    them would resolve every future mention to a file the issue may never have
    meant.
    """
    mapping = premise_citations.unique_basenames(("a/dup.py", "b/dup.py", "c/unique.py"))

    assert mapping == {"unique.py": "c/unique.py"}


def test_a_bare_filename_resolves_to_its_one_tracked_path() -> None:
    basenames = premise_citations.unique_basenames(("packages/theurian-core/src/theurian/foo.py",))

    found = premise_citations.extract_citations(
        "See foo.py for details.", (), tracked_basenames=basenames
    )

    assert found == (Citation("path", "packages/theurian-core/src/theurian/foo.py"),)


def test_an_ambiguous_bare_filename_resolves_to_nothing_rather_than_a_guess() -> None:
    """`unique_basenames` already dropped the entry; this is the caller-side half
    of the same rule -- a citation is left uncited rather than pointed at a file
    the issue may not have meant.
    """
    basenames = premise_citations.unique_basenames(("a/dup.py", "b/dup.py"))

    found = premise_citations.extract_citations(
        "See dup.py for details.", (), tracked_basenames=basenames
    )

    assert found == ()


def test_a_citation_in_a_comment_is_found_even_when_the_body_has_none() -> None:
    """A later correction posted as a comment cites what the issue is really
    about (module docstring); dropping comments would grade against a premise
    the thread itself already retracted.
    """
    found = premise_citations.extract_citations(
        "", ("Actually this moved to tools/premise_report.py.",), tracked_basenames={}
    )

    assert found == (Citation("path", "tools/premise_report.py"),)


def test_a_token_cannot_be_assembled_across_the_body_comment_boundary() -> None:
    """Body and comments are joined on a blank line, not concatenated directly
    (module docstring). Concatenating "test_" from the body onto "thing now."
    from the comment would read as `test_thing`, a citation about a function
    neither piece of text actually names on its own.
    """
    found = premise_citations.extract_citations(
        "Running test_", ("thing now.",), tracked_basenames={}
    )

    assert found == ()


# --------------------------------------------------------------------------
# Round 1 HIGH-1: a test_name (or path) match already covered by a path
# citation is dropped rather than emitted as its own -- refuting either would
# reintroduce a citation the issue never made, graded DANGLING against
# premise_check's own allowed-kinds rule.
# --------------------------------------------------------------------------


def test_a_prefixed_test_file_path_yields_only_the_path_never_its_embedded_test_name() -> None:
    """`tests/.../test_premise_report.py` is one path citation; the
    `test_premise_report` substring it contains is not the issue citing a
    test function, and emitting it too would grade a citation the issue never
    made (round 1 HIGH-1, face A).
    """
    found = premise_citations.extract_citations(
        "Failing case lives in tests/unit/tools/test_premise_report.py forever.",
        (),
        tracked_basenames={},
    )

    assert found == (Citation("path", "tests/unit/tools/test_premise_report.py"),)


def test_a_bare_test_file_name_yields_only_the_resolved_path_never_its_embedded_test_name() -> None:
    """The bare-filename form of the same face: once `unique_basenames`
    resolves it to a tracked path, the embedded `test_premise_report` must
    still be dropped -- the resolution path (`elif _has_known_extension`) is
    different code from the slash-prefixed case above, so face A needs its
    own pin on this branch too.
    """
    basenames = premise_citations.unique_basenames(("tests/unit/tools/test_premise_report.py",))

    found = premise_citations.extract_citations(
        "Failing case lives in test_premise_report.py forever.", (), tracked_basenames=basenames
    )

    assert found == (Citation("path", "tests/unit/tools/test_premise_report.py"),)


def test_a_path_token_truncated_by_a_glob_metacharacter_yields_no_path_citation() -> None:
    """`tools/premise_*.py` is a glob a comment used to describe a family of
    files, not a citation of the file `tools/premise_`; resolving the
    truncated token would grade DANGLING against a path the issue never
    named (round 1 HIGH-1, face B).
    """
    found = premise_citations.extract_citations(
        "See tools/premise_*.py for all of them.", (), tracked_basenames={}
    )

    assert found == ()
