"""Deterministic text projection (ADR-0020).

The projection is stored, chunked, and embedded, so its stability is a
correctness property rather than a formatting preference: an unstable projection
means a rebuilt index does not equal the original.
"""

from __future__ import annotations

import subprocess
import sys
import time
from typing import Final

import pytest
from visit_counting import CountsVisits

from theurian.domain.errors import InputTooLargeError
from theurian.normalization.projection import (
    DEPTH_MARKER,
    EXPANSION_MARKER,
    MAX_DEPTH,
    MAX_PROJECTION_CHARS,
    MAX_PROJECTION_NODES,
    SIZE_MARKER,
    project,
    project_checked,
    summarize_structure,
)
from theurian.security.yaml_loading import load_yaml

# -- Determinism -----------------------------------------------------------


def test_projection_is_stable_across_processes() -> None:
    """Guards against a projection that depends on `PYTHONHASHSEED`.

    Iterating a set or an unordered dict is invisible within one process and
    catastrophic across machines, so this runs in genuinely separate
    interpreters.
    """
    program = (
        "from theurian.normalization.projection import project;"
        "print(project({'b': 1, 'a': {'z': [1, 2], 'y': True}, 'c': None}))"
    )
    results = {
        subprocess.run(  # noqa: S603 - fixed argv
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            check=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
        ).stdout
        for seed in ("0", "1", "999")
    }
    assert len(results) == 1, f"projection varies with PYTHONHASHSEED: {results}"


def test_repeated_projection_is_identical() -> None:
    document = {"a": [1, {"b": "x"}], "c": {"d": None}}
    assert project(document) == project(document)


def test_document_order_is_preserved() -> None:
    """Not sorted: order is meaningful in a spec's rules and an API's parameters.

    Determinism comes from the parse being ordered, not from imposing an order.
    """
    forward = project({"zebra": 1, "apple": 2})
    reverse = project({"apple": 2, "zebra": 1})

    assert forward.splitlines() == ["zebra: 1", "apple: 2"]
    assert forward != reverse


# -- Scalar spelling -------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, "true"),
        (False, "false"),
        (None, "null"),
        (42, "42"),
        (0, "0"),
        (-7, "-7"),
        (1.5, "1.5"),
        ("text", "text"),
    ],
)
def test_scalars_render_in_the_source_format_spelling(value: object, expected: str) -> None:
    """`str()` would emit Python's `True` and `None`, making the index disagree
    with the document a user is reading."""
    assert project({"k": value}) == f"k: {expected}"


def test_bool_is_checked_before_int() -> None:
    """`bool` subclasses `int`; the wrong order renders `True` as `1`."""
    assert project({"flag": True}) == "flag: true"
    assert project({"count": 1}) == "count: 1"


def test_float_uses_repr_which_round_trips() -> None:
    assert project({"x": 0.1}) == "x: 0.1"
    assert project({"x": 1.0}) == "x: 1.0"


# -- Key paths -------------------------------------------------------------


def test_nested_values_carry_their_key_path() -> None:
    """A bare value dump would lose the context that makes it findable."""
    projected = project({"outcomes": {"failure": {"code": "CANCELLATION_NOT_ALLOWED"}}})
    assert projected == "outcomes.failure.code: CANCELLATION_NOT_ALLOWED"


def test_list_entries_are_indexed() -> None:
    assert project({"rules": ["a", "b"]}) == "rules.0: a\nrules.1: b"


def test_empty_containers_are_visible() -> None:
    """An empty list in a spec is a statement; dropping it would hide it."""
    assert project({"a": {}, "b": []}) == "a: {}\nb: []"


def test_a_bare_scalar_gets_a_placeholder_path() -> None:
    assert project("hello") == "value: hello"


def test_non_string_keys_render_through_the_scalar_function() -> None:
    """YAML permits non-string keys; Python's repr must not reach the index."""
    assert project({1: "a", None: "b", 2.5: "c"}) == "1: a\nnull: b\n2.5: c"
    assert project({True: "flag"}) == "true: flag"


# -- Limits ----------------------------------------------------------------


def test_excessive_nesting_is_marked_not_dropped() -> None:
    """A projection that silently omits content would make search miss text the
    document actually contains."""
    deep: dict[str, object] = {"leaf": "bottom"}
    for _ in range(MAX_DEPTH + 5):
        deep = {"n": deep}

    projected = project(deep)
    assert DEPTH_MARKER in projected


def test_oversized_output_is_truncated_with_a_marker() -> None:
    projected = project({"k": "x" * 5000}, max_chars=200)
    assert SIZE_MARKER in projected
    assert len(projected) <= 200 + len(SIZE_MARKER) + 1


def test_truncation_cuts_on_a_line_boundary() -> None:
    """A half-rendered value would read as real content."""
    document = {f"key{i}": "value" * 10 for i in range(200)}
    projected = project(document, max_chars=300)
    lines = projected.splitlines()
    assert lines[-1] == SIZE_MARKER
    for line in lines[:-1]:
        assert ": " in line, "a truncated line would lack its separator"


def test_project_checked_raises_instead_of_truncating() -> None:
    """Used where indexing a fraction of a document would be worse than
    reporting that it does not fit."""
    with pytest.raises(InputTooLargeError):
        project_checked({"k": "x" * 5000}, max_chars=100)


def test_project_checked_passes_within_the_limit() -> None:
    assert project_checked({"k": "v"}, max_chars=1000) == "k: v"


# -- The emit invariant: chars == len("\n".join(lines)) --------------------
#
# `_Spend.emit` charges `len(line) + (1 if lines else 0)` and stops on
# `chars > max_chars`. Three ways to write that are each one token away from
# what ships, all three passed the whole suite when introduced as mutations,
# and each moves the boundary between "fits" and "refused" by one character:
#
#   `>` -> `>=`                      refuses a document that fits exactly
#   `+ (1 if lines else 0)` dropped  accepts a document one separator over
#   `+ 1` unconditionally            refuses a document that fits exactly
#
# The document below is small enough to state its projection whole, so the
# boundary is byte-exact rather than approximately right.

#: Projects to exactly ``"aa: 1\nbb: 2\ncc: 3"`` -- three five-character lines
#: and two separators, 17 characters.
_EXACTLY_SEVENTEEN: Final = {"aa": "1", "bb": "2", "cc": "3"}
_SEVENTEEN: Final = 17


def test_a_projection_that_fits_exactly_is_neither_marked_nor_refused() -> None:
    """The lower edge of the budget, where an off-by-one is a false refusal.

    ``project_checked`` refuses a document Theurian cannot faithfully index, and
    a document whose projection is exactly ``max_chars`` characters long *is*
    faithfully indexable. Charging the separator for the first line too, or
    stopping on ``>=`` instead of ``>``, makes this exact document raise -- and
    at the shipped 2 MiB that is a refusal an author cannot see the cause of.
    """
    unbounded = project(_EXACTLY_SEVENTEEN, max_chars=10**9)

    assert unbounded == "aa: 1\nbb: 2\ncc: 3"
    assert len(unbounded) == _SEVENTEEN, "the fixture is the assertion here"

    assert project_checked(_EXACTLY_SEVENTEEN, max_chars=_SEVENTEEN) == unbounded
    assert project(_EXACTLY_SEVENTEEN, max_chars=_SEVENTEEN) == unbounded
    assert SIZE_MARKER not in project(_EXACTLY_SEVENTEEN, max_chars=_SEVENTEEN)


def test_one_character_over_is_refused_and_the_separator_is_what_makes_it_over() -> None:
    """The upper edge: the separator is real text, so it is real spend.

    17 characters against a 16-character budget is over by exactly the one
    separator ``"\\n".join`` inserts, and nothing else. An ``emit`` that charges
    only ``len(line)`` counts this document at 15 and hands
    ``project_checked`` -- the path ingest uses where truncation would be wrong
    -- all 17 characters with no refusal at all (measured 2026-08-24 against that
    mutation: ``project_checked(..., max_chars=16)`` returned ``'aa: 1\\nbb:
    2\\ncc: 3'``).

    ``project`` is *not* where this shows, which is why the assertion is on
    ``project_checked``: it re-measures ``len(text)`` after the walk and
    truncates, so an under-charging ``emit`` costs it work and never returns
    more than its budget. Only the raising entry point publishes the mistake.

    ``observed`` is asserted because it is what
    :class:`~theurian.domain.errors.InputTooLargeError`'s remedy prints, and a
    remedy reading "0 exceeds the limit of 16" tells an author nothing about how
    much to cut.
    """
    with pytest.raises(InputTooLargeError) as exc:
        project_checked(_EXACTLY_SEVENTEEN, max_chars=_SEVENTEEN - 1)

    assert exc.value.limit_name == "projected text size"
    assert exc.value.limit == _SEVENTEEN - 1
    assert exc.value.observed == _SEVENTEEN, (
        "the spend at the stop, separator included -- 15 means the separator was free"
    )


def test_project_truncates_the_same_document_project_checked_refuses() -> None:
    """The two entry points must agree on *which* documents are over the line.

    ``project`` truncates where ``project_checked`` raises, so a document that
    raises above must carry a marker here: the ingest path calls one and the
    validate path the other, and a document accepted whole by one and marked
    incomplete by the other would make the index disagree with the report.
    """
    truncated = project(_EXACTLY_SEVENTEEN, max_chars=_SEVENTEEN - 1)

    assert truncated == f"aa: 1\nbb: 2\n{SIZE_MARKER}"
    assert len(truncated) <= (_SEVENTEEN - 1) + len(SIZE_MARKER) + 1


#: The two markers ``project`` appends *after* the walk, and so the two that sit
#: outside the character budget rather than being charged through ``emit``. The
#: bound below has to allow for the longer of them: it was written against
#: :data:`SIZE_MARKER` alone, and :data:`EXPANSION_MARKER` is five characters
#: longer, so the node path exceeded a bound the character path satisfied
#: (measured 2026-08-24 at ``max_chars=1, max_nodes=1``: 28 characters returned
#: against a stated bound of 25).
_LONGEST_TRUNCATION_MARKER: Final = max(len(SIZE_MARKER), len(EXPANSION_MARKER))

#: The sweep's two axes, named so the coverage check below runs the same grid the
#: bound is asserted over rather than a hand-copied twin of it.
_SWEEP_CHARS: Final = (1, 16, 17, 18, 115, 200, 1438)
_SWEEP_NODES: Final = (1, 2, 3, 201, 10**9)

#: Two hundred lines of roughly 57 characters each. Wide rather than deep, so a
#: small ``max_nodes`` stops it with text already emitted instead of at the root.
_SWEEP_DOCUMENT: Final = {f"key{i}": "value" * 10 for i in range(200)}


@pytest.mark.parametrize("max_nodes", _SWEEP_NODES)
@pytest.mark.parametrize("max_chars", _SWEEP_CHARS)
def test_a_truncated_projection_never_exceeds_its_budget_plus_the_marker(
    max_chars: int, max_nodes: int
) -> None:
    """The invariant that holds at every budget, not only at the interesting one.

    ``project`` is the ingest path's entry point and it cannot raise, so this
    bound is the only thing between a caller and an unbounded string. Swept
    across the character boundary the tests above pin (16/17/18) and out to a
    budget deep inside this document, because the tight case is not the large
    one: at ``max_chars=1`` there is no line boundary to cut on, so the return is
    ``max_chars`` plus the marker plus its separator exactly, and every larger
    budget has slack.

    **``max_nodes`` is swept too, and that is what makes the bound honest.** With
    the node ceiling left at its default the second truncation path never ran
    here, and the bound was stated in :data:`SIZE_MARKER` -- which
    :data:`EXPANSION_MARKER` exceeds by five characters. Both markers are
    appended after the walk rather than charged through ``emit``, so both sit
    outside the budget and the bound must allow for the longer.
    """
    projected = project(_SWEEP_DOCUMENT, max_chars=max_chars, max_nodes=max_nodes)

    assert len(projected) <= max_chars + _LONGEST_TRUNCATION_MARKER + 1, (
        f"a {max_chars}-character, {max_nodes}-node budget returned {len(projected)} characters"
    )


def test_the_budget_sweep_reaches_both_truncation_paths() -> None:
    """The sweep above is worth its cases only if both markers occur in it.

    A grid that never exhausts the nodes asserts the character path twice over
    and calls it coverage -- which is exactly the state this file was in, and why
    the marker in the bound went unnoticed. Asserted over the same two axis
    constants the sweep is parametrized on, so widening one cannot leave this
    checking a grid nobody runs.
    """
    markers = {
        marker
        for max_chars in _SWEEP_CHARS
        for max_nodes in _SWEEP_NODES
        for marker in (SIZE_MARKER, EXPANSION_MARKER)
        if project(_SWEEP_DOCUMENT, max_chars=max_chars, max_nodes=max_nodes).endswith(marker)
    }

    assert markers == {SIZE_MARKER, EXPANSION_MARKER}


# -- Budgets bound the walk, not only its result (issue #232) --------------


def _shared_by(levels: int, shared: CountsVisits) -> dict[str, object]:
    """A tree of ``2 ** levels`` paths that all end at the same sub-object.

    What PyYAML builds from nested aliases, by hand: an alias is resolved by
    sharing the object, not by copying it, so the parse is cheap and only the
    walk pays for the expansion.
    """
    node: dict[str, object] = shared
    for _ in range(levels):
        node = {"l": node, "r": node}
    return node


def _shared_by_sequences(levels: int, shared: CountsVisits) -> list[object]:
    """The same tree, built out of lists instead of mappings.

    Issue #232's own reproduction was a YAML *list* bomb -- ``a1: &a1 [*a0, *a0,
    ...]`` -- while every budget fixture above builds mappings, so the sequence
    branch of the walk had no budget-shaped test at all. Removing the budget
    propagation from ``_walk_sequence`` survived the whole suite for that reason.

    Both budgets need a case here, and they are not one case twice: the character
    side shows in how often the shared node is materialised, while the node side
    leaves that count -- and ``project``'s whole output -- unchanged and moves
    only ``InputTooLargeError.observed``. Each has its own test below, with the
    measurement that says which field carries it.
    """
    node: list[object] = [shared, shared]
    for _ in range(levels - 1):
        node = [node, node]
    return node


def test_the_character_budget_stops_a_sequence_walk_too() -> None:
    """Issue #232's own shape: the alias bomb it measured was a list of lists.

    ``_walk_sequence`` propagates a spent budget by returning ``False`` from the
    middle of its loop, exactly as ``_walk_mapping`` does. A version that
    finished its loop instead kept the *result* bounded -- ``project`` truncates
    afterwards either way -- while spending every one of the 4096 paths through
    the shared node, which is the cost, not the string, that issue #232 is about.

    The count is the assertion for the reason
    ``test_the_character_budget_stops_the_walk_rather_than_the_join`` gives: a
    stopwatch on a loaded machine measures the machine.
    """
    shared = CountsVisits({"a": "x" * 40, "b": "y" * 40})
    document = _shared_by_sequences(12, shared)

    projected = project(document, max_chars=200)

    assert SIZE_MARKER in projected
    assert shared.visits <= 8, (
        f"the walk materialised the shared sub-object {shared.visits} times "
        f"for a 200-character budget, through a sequence rather than a mapping"
    )


def test_a_sequence_that_is_not_a_bomb_is_projected_in_full() -> None:
    """The false-refusal side of the sequence branch.

    A propagation fix that returned ``False`` unconditionally would pass the test
    above and truncate every list in the corpus. Eight paths, all expanded.
    """
    shared = CountsVisits({"a": "x", "b": "y"})
    projected = project(_shared_by_sequences(3, shared))

    assert SIZE_MARKER not in projected
    assert EXPANSION_MARKER not in projected
    assert len(projected.splitlines()) == 16, "eight copies of the shared node, two lines each"
    assert shared.visits == 8


def test_the_character_budget_stops_the_walk_rather_than_the_join() -> None:
    """Issue #232: the cap bounded what was kept, not what was spent.

    ``MAX_PROJECTION_CHARS`` was checked after ``_walk`` had built and joined
    every line, so a shared sub-object was materialised once per path to it --
    4096 times here, and 2 ** 23 times for the 405 B document the issue
    measured at 19.76 s and 2.8 GB while returning a 2 MiB string.

    The count, not a stopwatch, is the assertion: with the budget threaded
    through the walk it is a handful, and with the budget back at the join it is
    every path in the tree.
    """
    shared = CountsVisits({"a": "x" * 40, "b": "y" * 40})
    document = _shared_by(12, shared)

    projected = project(document, max_chars=200)

    assert SIZE_MARKER in projected
    assert shared.visits <= 8, (
        f"the walk materialised the shared sub-object {shared.visits} times "
        f"for a 200-character budget"
    )


def test_the_node_ceiling_stops_a_walk_whose_text_stays_small() -> None:
    """The second budget, on the shape the first one cannot price.

    A non-empty container emits nothing of its own, so a document can spend
    visits without spending characters. Here the character budget is far out of
    reach and the node ceiling is what fires, with a marker of its own -- the
    size marker would be a false statement about a projection well under the
    size limit.
    """
    shared = CountsVisits({"a": "x"})
    document = _shared_by(12, shared)

    projected = project(document, max_chars=10**9, max_nodes=200)

    assert projected.endswith(EXPANSION_MARKER)
    assert SIZE_MARKER not in projected, "the text fits; only the traversal did not"
    assert shared.visits <= 100, shared.visits


def test_the_node_ceiling_stops_a_sequence_walk_too() -> None:
    """The node ceiling's own sequence-side quantity, which nothing else sees.

    ``_walk_sequence`` propagates a spent budget by returning ``False`` from the
    middle of its loop, and both budgets ride that one ``return``. The
    character-budget twin above holds it *through the character budget only*: it
    asserts how often the shared sub-object is materialised, which is a count the
    node ceiling does not move.

    Measured 2026-08-24 against ``proj-sequence-no-propagate`` -- the loop
    finished rather than propagated -- on the two documents these two tests use.
    The twin goes red at 4,096 materialisations against its bound of 8. This one
    goes red on a different number entirely: at the node ceiling the shared node
    is entered 64 times either way and ``project`` returns the same 1,884
    characters either way, because the walk stops *descending* the moment the
    ceiling is passed and the mutant only keeps *charging* the siblings already
    on the unwound stack. Each of those is one more visit and no descent, so the
    spend at the stop is the only thing that moves: 201 shipped, 209 mutated.
    ``project_checked`` is the only entry point that can see it and ``observed``
    the only field -- "unobservable on this side" was recorded here and is false.

    ``201`` exactly, not ``> 200``: the ceiling is charged on entry and stops the
    whole walk, so the first over-visit is the last one -- the same ``limit + 1``
    the shipped defaults publish as
    ``InputTooLargeError('projected node count', 1000000, 1000001)``. A ``>``
    here would pass with the propagation removed.
    """
    shared = CountsVisits({"a": "x"})
    document = _shared_by_sequences(12, shared)

    with pytest.raises(InputTooLargeError) as exc:
        project_checked(document, max_chars=10**9, max_nodes=200)

    assert exc.value.limit_name == "projected node count"
    assert exc.value.limit == 200
    assert exc.value.observed == 201, (
        "a sequence that keeps charging its siblings after the ceiling has "
        "fired reports 209 here; only this field moves"
    )


def test_project_checked_raises_from_the_budget_it_ran_out_of() -> None:
    """Both budgets raise, and each names the quantity it measured.

    ``limit_name`` is what ``InputTooLargeError``'s remedy speaks in, so a node
    ceiling reported as a text size would tell an author to shorten prose that
    was never the problem.

    ``observed`` is the other half of that remedy -- it prints "N exceeds the
    limit of M" -- and it was unasserted here, so an ``_Exhausted`` built with a
    constant ``0`` for either budget passed the whole suite. It is a *lower*
    bound on what finishing would have cost, and both assertions below are
    written as that: strictly past the limit, never zero.
    """
    with pytest.raises(InputTooLargeError) as by_chars:
        project_checked(_shared_by(12, CountsVisits({"a": "x" * 40})), max_chars=200)
    assert by_chars.value.limit_name == "projected text size"
    assert by_chars.value.limit == 200
    assert by_chars.value.observed > 200, (
        f"the spend at the stop must pass the budget it stopped on, not be "
        f"{by_chars.value.observed}"
    )

    with pytest.raises(InputTooLargeError) as by_nodes:
        project_checked(_shared_by(12, CountsVisits({"a": "x"})), max_chars=10**9, max_nodes=200)
    assert by_nodes.value.limit_name == "projected node count"
    assert by_nodes.value.limit == 200
    assert by_nodes.value.observed == 201, (
        "the node ceiling is charged on entry and stops the whole walk, so the "
        "first over-visit is the last: exactly limit + 1, the same +1 the shipped "
        "defaults produce as InputTooLargeError('projected node count', 1000000, 1000001)"
    )


def test_a_benign_shared_document_is_projected_in_full() -> None:
    """The false-refusal side: sharing is normal, and small is small.

    A YAML document that reuses one anchor a few times is ordinary authoring,
    and the budget must not read it as an attack. Every one of the eight paths
    to the shared node is expanded, exactly as before the budget existed.
    """
    shared = CountsVisits({"a": "x", "b": "y"})
    projected = project(_shared_by(3, shared))

    assert SIZE_MARKER not in projected
    assert EXPANSION_MARKER not in projected
    assert len(projected.splitlines()) == 16, "eight copies of the shared node, two lines each"
    assert shared.visits == 8


def test_a_real_alias_bomb_is_projected_within_seconds() -> None:
    """The same guard, driven by PyYAML rather than by a hand-built graph.

    Issue #232's own input: 405 bytes, seven alias levels, nine references per
    level. Measured on this branch, both with the truncating ``project`` the
    ingest path calls: 19.76 seconds and 2.8 GB of RSS before the budget was
    threaded through the walk, 0.09 seconds and 43 MB after -- for byte-identical
    output, since the budget changes the work and not the projection.

    The bound is wall clock because the guard's failure mode is *time*, and it
    is set two orders of magnitude above the measured cost and four times below
    the unguarded one, so it separates the two without measuring the machine.
    """
    lines = ["a0: &a0 [x, y, z]"]
    for level in range(1, 8):
        lines.append(f"a{level}: &a{level} [{', '.join([f'*a{level - 1}'] * 9)}]")
    lines.append("top: *a7")
    document = load_yaml("\n".join(lines) + "\n")

    started = time.monotonic()
    projected = project(document)
    elapsed = time.monotonic() - started

    assert SIZE_MARKER in projected
    assert len(projected) <= MAX_PROJECTION_CHARS + len(SIZE_MARKER) + 1
    assert elapsed < 5, f"the alias bomb took {elapsed:.2f}s"


# -- The published numbers -------------------------------------------------


def test_the_projection_bounds_are_the_numbers_the_threat_model_publishes() -> None:
    """The live half of a claim whose other half is prose (SEC-8, T-6, #232).

    ``docs/security/threat-model.md``'s Controls table names both symbols and
    both values --

        ``normalization/projection.py::MAX_PROJECTION_CHARS`` (2 MiB) and
        ``MAX_PROJECTION_NODES`` (1,000,000), threaded through ``_walk``

    -- and ``docs/architecture/source-normalization.md``'s bounds table repeats
    them. Prose cannot notice when a constant moves: raising the characters to 4
    MiB and the nodes to 1e10 were each measured surviving the whole suite, which
    would leave the threat model asserting a bound the code stopped enforcing.
    Per the anchor-counts convention, the live claim is this test and the
    documents are its restatement.

    Relaxing either number is a SEC-8 decision, not a tuning change: it must move
    here, in the Controls table, and in the bounds table together, with the new
    figure measured.
    """
    assert MAX_PROJECTION_CHARS == 2 * 1024 * 1024
    assert MAX_PROJECTION_NODES == 1_000_000


# -- Structural summary ----------------------------------------------------


def test_summary_describes_the_top_level() -> None:
    summary = summarize_structure({"info": {"a": 1}, "paths": [1, 2, 3], "openapi": "3.1.0"})
    assert summary == ("info/ (1 keys)", "paths[] (3 items)", "openapi")


def test_summary_is_bounded() -> None:
    summary = summarize_structure({f"k{i}": i for i in range(100)}, max_entries=5)
    assert len(summary) == 6
    assert summary[-1] == "..."


def test_summary_of_a_non_mapping_is_empty() -> None:
    assert summarize_structure([1, 2, 3]) == ()
