"""The two MATCH builders' floors, and the one-sided queries between them (ADR-0023).

`SqliteIndexStore.search_summaries` gives up on a query only when it forms
*neither* an FTS term nor a trigram, and section 6 of
`tests/integration/test_forest_store_retrieval.py` drives one side of that
branch: a two-character Japanese noun, which forms a term and no trigram. It
leaves the mirror side undriven on a stated universal -- a non-empty trigram
expression implies a non-empty match expression, so no query reaches it.

That sentence is load-bearing: it is the whole reason the mirror branch has no
test, and this file is what goes red when it stops being true. ADR-0023 defers
per-term work on `to_trigram_expression` (the CJK bigram path) that would touch
exactly this relation.
"""

from __future__ import annotations

from typing import Final

import pytest
from hypothesis import given, seed, settings
from hypothesis import strategies as st
from hypothesis.strategies import DrawFn

from theurian.infrastructure.sqlite.index_query import (
    MAX_QUERY_CHARS,
    MAX_QUERY_TERMS,
    to_match_expression,
    to_trigram_expression,
)

pytestmark = pytest.mark.unit

#: Tokens that form a trigram today, one per shape that can reach the floor:
#: at it in ASCII and in Japanese, above it, and two that only reach it once
#: ``_FTS_SPECIAL`` is stripped and ``"`` is split on. Four of the five reduce to
#: exactly three characters, so a match floor raised past the trigram floor
#: leaves those queries with no match expression at all -- the failure this file
#: exists to catch, and one a population of long tokens cannot reach.
_TRIGRAM_FORMING: Final = ("api", "認証局", "tokenreview", "(api)", '"api"')

#: One worst member per remaining shape ``_query_terms`` sorts on, mixed in
#: around the anchor: under the trigram floor in both scripts, stripped under it,
#: stripped away entirely, untransportable in both ways the transport check
#: covers (SEC-8), and a punctuation mark that is a match term and no trigram.
_OTHER_SHAPES: Final = (
    "e",
    "ok",
    "鍵",
    "認証",
    "(ok)",
    "***",
    "tok\x00en",
    # Built with `chr` rather than written as `"token\ud800"`: mypy infers a
    # `Literal` type for a string literal in a `Final` tuple and crashes writing
    # it to its cache ("surrogates not allowed", mypy 2.3.1).
    "token" + chr(0xD800),
    "。",
)

#: Distinct tokens, every one exactly at the trigram floor, drawn in bulk so the
#: population crosses :data:`MAX_QUERY_TERMS`. There are more of them than that
#: bound admits, so a draw taking enough of them forces the longest-first
#: ``[:64]`` cut to run -- and that cut is half of why the two floors' term sets
#: stay nested, so a population that never reached the bound would leave the
#: subset assertion below resting on a branch nothing takes. It is reached: 85 of
#: :data:`_FUZZ_SETTINGS`' 400 examples carry more than ``MAX_QUERY_TERMS`` terms
#: eligible at ``min_length=1``, peaking at 90 -- counted on that eligible set
#: and not on a raw token split, which also counts the terms ``_query_terms``
#: drops and answered 98 and 91 here (measured 2026-09-18).
_CROWD: Final = tuple(f"q{index:02d}" for index in range(MAX_QUERY_TERMS + 16))

#: ``@seed(742)`` is what holds the population identical on every machine: it
#: overrides ``derandomize`` and sets ``database=None`` itself (hypothesis
#: ``core.py``'s ``seed`` and ``get_random_for_wrapped_test``), so both of those
#: are inert here while it stands, and are kept only as the belt if it is ever
#: removed. 400 examples run in 0.40s (measured 2026-09-18).
_FUZZ_SETTINGS = settings(deadline=None, derandomize=True, database=None, max_examples=400)


@st.composite
def _queries(draw: DrawFn) -> str:
    """A query carrying one trigram-forming term, with arbitrary noise around it.

    No single token has to survive the two bounds. ``_query_terms`` sorts
    longest-first, so only a term at or above the trigram floor can displace the
    anchor from the kept 64, and the eligible set cannot truncate to nothing;
    the anchor leads the string, so the character bound cannot cut it either.
    That is what lets the crowd and the padding carry the query past
    :data:`MAX_QUERY_TERMS` and :data:`MAX_QUERY_CHARS` -- both truncations run
    inside the population rather than being assumed harmless.
    """
    anchor = draw(st.sampled_from(_TRIGRAM_FORMING))
    noise = draw(
        st.lists(
            st.one_of(st.sampled_from(_OTHER_SHAPES), st.text(max_size=12)),
            max_size=80,
        )
    )
    crowd = draw(st.integers(min_value=0, max_value=len(_CROWD)))
    padding = draw(st.sampled_from(("", "x" * MAX_QUERY_CHARS)))
    return " ".join([anchor, *noise, *_CROWD[:crowd], padding])


def _terms(expression: str) -> set[str]:
    """The terms an expression carries, read back off the form both builders emit.

    Both join with ``" OR "`` and wrap each term in double quotes, and neither
    can ever produce a term containing either: ``_query_terms`` splits on
    whitespace, and ``"`` is replaced by a space before that split. So this
    round-trip is exact rather than a best-effort parse.
    """
    return {quoted.strip('"') for quoted in expression.split(" OR ")} - {""}


@seed(742)
@_FUZZ_SETTINGS
@given(query=_queries())
def test_a_query_that_forms_a_trigram_expression_always_forms_a_match_one(query: str) -> None:
    """Both builders draw their terms from ``_query_terms`` and differ only in
    ``min_length`` -- 1 against 3 -- and the longest-first ``[:64]`` truncation
    keeps the higher floor's terms a subset of the lower floor's. That is why the
    implication holds by construction rather than by observation, and relaxing
    either floor on its own is what would end it.
    """
    trigram = to_trigram_expression(query)
    match = to_match_expression(query)

    assert trigram, (
        "precondition: every generated query carries a trigram-forming term, so "
        "an empty expression means the population stopped reaching the branch "
        f"under test, not that the implication held; query={query[:120]!r}"
    )
    assert match, (
        "a query that forms a trigram expression must form a match expression "
        "too -- the branch `search_summaries` has no fixture for, because no "
        f"query is supposed to be able to reach it; query={query[:120]!r}"
    )
    assert _terms(trigram) <= _terms(match), (
        "the trigram builder selects from the match builder's terms at a higher "
        "floor, so its terms are a subset of theirs -- the mechanism the "
        "implication rests on, and what a truncation that stopped taking the "
        f"longest first would break; query={query[:120]!r}"
    )
