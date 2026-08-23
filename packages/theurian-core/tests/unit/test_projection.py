"""Deterministic text projection (ADR-0020).

The projection is stored, chunked, and embedded, so its stability is a
correctness property rather than a formatting preference: an unstable projection
means a rebuilt index does not equal the original.
"""

from __future__ import annotations

import subprocess
import sys
import time
from typing import TYPE_CHECKING, override

import pytest

from theurian.domain.errors import InputTooLargeError
from theurian.normalization.projection import (
    DEPTH_MARKER,
    EXPANSION_MARKER,
    MAX_DEPTH,
    MAX_PROJECTION_CHARS,
    SIZE_MARKER,
    project,
    project_checked,
    summarize_structure,
)
from theurian.security.yaml_loading import load_yaml

if TYPE_CHECKING:
    # What `dict.items()` returns, and so what an override of it must return.
    # A type-checking name only: it is not bound at runtime.
    from _collections_abc import dict_items

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


# -- Budgets bound the walk, not only its result (issue #232) --------------


class _CountsVisits(dict[str, object]):
    """A mapping that records how often the walk descends into it.

    ``_walk`` calls ``items()`` exactly once per descent, so the count is the
    number of times this sub-object was materialised. Counting is what makes
    these tests deterministic: the alternative is a stopwatch, and a stopwatch
    on a loaded machine measures the machine.
    """

    def __init__(self, mapping: dict[str, object]) -> None:
        super().__init__(mapping)
        self.visits = 0

    @override
    def items(self) -> dict_items[str, object]:
        self.visits += 1
        return super().items()


def _shared_by(levels: int, shared: _CountsVisits) -> dict[str, object]:
    """A tree of ``2 ** levels`` paths that all end at the same sub-object.

    What PyYAML builds from nested aliases, by hand: an alias is resolved by
    sharing the object, not by copying it, so the parse is cheap and only the
    walk pays for the expansion.
    """
    node: dict[str, object] = shared
    for _ in range(levels):
        node = {"l": node, "r": node}
    return node


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
    shared = _CountsVisits({"a": "x" * 40, "b": "y" * 40})
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
    shared = _CountsVisits({"a": "x"})
    document = _shared_by(12, shared)

    projected = project(document, max_chars=10**9, max_nodes=200)

    assert projected.endswith(EXPANSION_MARKER)
    assert SIZE_MARKER not in projected, "the text fits; only the traversal did not"
    assert shared.visits <= 100, shared.visits


def test_project_checked_raises_from_the_budget_it_ran_out_of() -> None:
    """Both budgets raise, and each names the quantity it measured.

    ``limit_name`` is what ``InputTooLargeError``'s remedy speaks in, so a node
    ceiling reported as a text size would tell an author to shorten prose that
    was never the problem.
    """
    with pytest.raises(InputTooLargeError) as by_chars:
        project_checked(_shared_by(12, _CountsVisits({"a": "x" * 40})), max_chars=200)
    assert by_chars.value.limit_name == "projected text size"
    assert by_chars.value.limit == 200

    with pytest.raises(InputTooLargeError) as by_nodes:
        project_checked(_shared_by(12, _CountsVisits({"a": "x"})), max_chars=10**9, max_nodes=200)
    assert by_nodes.value.limit_name == "projected node count"
    assert by_nodes.value.limit == 200


def test_a_benign_shared_document_is_projected_in_full() -> None:
    """The false-refusal side: sharing is normal, and small is small.

    A YAML document that reuses one anchor a few times is ordinary authoring,
    and the budget must not read it as an attack. Every one of the eight paths
    to the shared node is expanded, exactly as before the budget existed.
    """
    shared = _CountsVisits({"a": "x", "b": "y"})
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
