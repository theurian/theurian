"""Deterministic text projection (ADR-0020).

The projection is stored, chunked, and embedded, so its stability is a
correctness property rather than a formatting preference: an unstable projection
means a rebuilt index does not equal the original.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from theurian.domain.errors import InputTooLargeError
from theurian.normalization.projection import (
    DEPTH_MARKER,
    MAX_DEPTH,
    SIZE_MARKER,
    project,
    project_checked,
    summarize_structure,
)

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
