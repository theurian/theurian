"""Every published input-side ``maxLength`` is a live constant, recomputed here.

The per-tool input schemas (ADR-0031 decision 1) carry length bounds that were
**hand-transcribed** from the constants the handlers enforce. A transcribed
number is a number that drifts: nothing validates a schema file against the
module it copied from, so moving ``MAX_FILTER_CHARS`` and forgetting the file
leaves two bounds that disagree -- and the disagreement is not symmetric.

* A published bound **above** the constant publishes a value the handler
  refuses: a caller reads the contract, sends a conforming request, and is
  refused below the surface. ADR-0031 decision 6 calls that a published lie.
* A published bound **below** the constant refuses, at the wire, a value the
  handler would have served, with "does not satisfy 'maxLength'" in place of the
  remedy the handler carries.

So each bound is pinned against the live constant rather than against the number
it happens to be today -- the house pattern
``test_review_allowlist.py::test_the_length_bound_this_module_enforces_is_the_one_the_schema_publishes``
uses, for the same reason: *pin derivations, not prose*.

**The population is derived and asserted by equality**, not listed. The walk
finds every ``maxLength`` anywhere in every input-side schema -- the
``*-input.schema.json`` files and every referent they ``$ref`` -- and the table
below must name exactly that set. A new bound added to a schema with no entry
here fails, and an entry naming a bound that no longer exists fails too
(``PROCESS_SPAWN_SITES``' rule: an addition *and* a removal).

Value-domain constraints with no constant behind them are deliberately not
pinned: ``minLength: 1`` on ``query`` is the schema saying "not the empty
string", and there is no module constant it could drift away from.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator
from typing import Any, Final

import pytest

from theurian.domain.identifiers import MAX_IDENTIFIER_LENGTH
from theurian.mcp.findings import MAX_FILTER_CHARS as FINDINGS_MAX_FILTER_CHARS
from theurian.mcp.review_search import MAX_FILTER_CHARS as REVIEW_SEARCH_MAX_FILTER_CHARS
from theurian.mcp.tools import MAX_AS_OF_CHARS, MAX_PROJECT_ID_CHARS, MAX_QUERY_CHARS
from theurian.mcp.validation import INPUT_SCHEMA_SUFFIX

pytestmark = pytest.mark.unit

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
MCP_SCHEMAS = REPO_ROOT / "schemas" / "mcp"

#: Each published input-side ``maxLength``, keyed by its file and its location
#: inside it, mapped to the constant it transcribes and where that constant is
#: enforced. The third element is not decoration: a bound and a constant can be
#: equal by coincidence -- ``MAX_IDENTIFIER_LENGTH`` and ``findings``'
#: ``MAX_FILTER_CHARS`` are both 200 -- and naming the enforcing code is what
#: makes a failure say *which* of the two moved.
BOUNDS: Final[dict[tuple[str, str], tuple[str, int, str]]] = {
    ("knowledge-get-input.schema.json", "properties/itemId"): (
        "MAX_IDENTIFIER_LENGTH",
        MAX_IDENTIFIER_LENGTH,
        "domain/identifiers.py -- `ItemId` refuses a longer value, so no stored id can be "
        "longer and a longer one could name nothing",
    ),
    ("knowledge-search-input.schema.json", "properties/query"): (
        "MAX_QUERY_CHARS",
        MAX_QUERY_CHARS,
        "mcp/tools.py -- the handler slices `query[:MAX_QUERY_CHARS]` before searching, so "
        "nothing past it was ever searched for",
    ),
    ("knowledge-search-input.schema.json", "properties/asOf"): (
        "MAX_AS_OF_CHARS",
        MAX_AS_OF_CHARS,
        "mcp/tools.py -- `_parse_as_of` refuses a longer value by its length rather than "
        "quoting it (#17)",
    ),
    ("review-findings-input.schema.json", "properties/reviewer"): (
        "findings.MAX_FILTER_CHARS",
        FINDINGS_MAX_FILTER_CHARS,
        "mcp/findings.py -- `_bounded` refuses a longer filter by its length",
    ),
    ("review-findings-input.schema.json", "properties/severity"): (
        "findings.MAX_FILTER_CHARS",
        FINDINGS_MAX_FILTER_CHARS,
        "mcp/findings.py -- `_bounded` refuses a longer filter by its length",
    ),
    ("review-findings-input.schema.json", "properties/family"): (
        "findings.MAX_FILTER_CHARS",
        FINDINGS_MAX_FILTER_CHARS,
        "mcp/findings.py -- `_bounded` refuses a longer filter by its length",
    ),
    ("review-findings-input.schema.json", "properties/specialist"): (
        "findings.MAX_FILTER_CHARS",
        FINDINGS_MAX_FILTER_CHARS,
        "mcp/findings.py -- `_bounded` refuses a longer filter by its length",
    ),
    ("review-findings-input.schema.json", "properties/commitSha"): (
        "findings.MAX_FILTER_CHARS",
        FINDINGS_MAX_FILTER_CHARS,
        "mcp/findings.py -- `_bounded` refuses a longer filter by its length",
    ),
    ("review-findings-input.schema.json", "properties/q"): (
        "findings.MAX_FILTER_CHARS",
        FINDINGS_MAX_FILTER_CHARS,
        "mcp/findings.py -- `_bounded` refuses a longer filter by its length",
    ),
    ("review-search-input.schema.json", "properties/repository"): (
        "review_search.MAX_FILTER_CHARS",
        REVIEW_SEARCH_MAX_FILTER_CHARS,
        "mcp/review_search.py -- `_bounded` refuses a longer filter by its length",
    ),
    ("review-search-input.schema.json", "properties/threadState"): (
        "review_search.MAX_FILTER_CHARS",
        REVIEW_SEARCH_MAX_FILTER_CHARS,
        "mcp/review_search.py -- `_bounded` refuses a longer filter by its length",
    ),
    ("review-search-input.schema.json", "properties/author"): (
        "review_search.MAX_FILTER_CHARS",
        REVIEW_SEARCH_MAX_FILTER_CHARS,
        "mcp/review_search.py -- `_bounded` refuses a longer filter by its length",
    ),
    ("review-search-input.schema.json", "properties/filePath"): (
        "review_search.MAX_FILTER_CHARS",
        REVIEW_SEARCH_MAX_FILTER_CHARS,
        "mcp/review_search.py -- `_bounded` refuses a longer filter by its length",
    ),
    ("review-search-input.schema.json", "properties/q"): (
        "review_search.MAX_FILTER_CHARS",
        REVIEW_SEARCH_MAX_FILTER_CHARS,
        "mcp/review_search.py -- `_bounded` refuses a longer filter by its length",
    ),
    ("tool-context.schema.json", "properties/projectId"): (
        "MAX_PROJECT_ID_CHARS",
        MAX_PROJECT_ID_CHARS,
        "mcp/tools.py -- `_unresolvable` reports a longer id by its length rather than "
        "echoing it, and `ProjectId` refuses one outright",
    ),
    ("tool-context.schema.json", "properties/agentId"): (
        "MAX_IDENTIFIER_LENGTH",
        MAX_IDENTIFIER_LENGTH,
        "domain/identifiers.py -- `AgentId` refuses a longer value",
    ),
    ("tool-context.schema.json", "properties/taskId"): (
        "MAX_IDENTIFIER_LENGTH",
        MAX_IDENTIFIER_LENGTH,
        "domain/identifiers.py -- `TaskId` refuses a longer value",
    ),
}


def _input_side_paths() -> list[pathlib.Path]:
    """The published input files and every schema they reference.

    Derived rather than listed: ``tool-context.schema.json`` is input-side only
    because five per-tool files ``$ref`` it, and a second referent added later
    joins this walk by being referenced rather than by being remembered.
    """
    inputs = sorted(MCP_SCHEMAS.glob(f"*{INPUT_SCHEMA_SUFFIX}"))
    assert inputs, f"no published input schemas under {MCP_SCHEMAS}"

    by_id = {
        document["$id"]: path
        for path in sorted(MCP_SCHEMAS.glob("*.schema.json"))
        for document in [json.loads(path.read_text(encoding="utf-8"))]
        if "$id" in document
    }
    referenced = {
        by_id[reference]
        for path in inputs
        for reference in _references(json.loads(path.read_text(encoding="utf-8")))
        if reference in by_id
    }
    return sorted(set(inputs) | referenced)


def _references(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        reference = node.get("$ref")
        if isinstance(reference, str):
            yield reference
        for value in node.values():
            yield from _references(value)
    elif isinstance(node, list):
        for value in node:
            yield from _references(value)


def _max_lengths(node: Any, pointer: str = "") -> Iterator[tuple[str, Any]]:
    """Every ``maxLength`` in ``node``, with the path that reaches it.

    Walks the whole document rather than its top-level ``properties``: a bound
    inside an ``items`` or a nested object is the same transcription with the
    same drift, and a walk that stopped at depth one would not see it.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{pointer}/{key}" if pointer else key
            if key == "maxLength":
                yield pointer, value
            else:
                yield from _max_lengths(value, here)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _max_lengths(value, f"{pointer}/{index}")


def _published_bounds() -> dict[tuple[str, str], Any]:
    return {
        (path.name, pointer): value
        for path in _input_side_paths()
        for pointer, value in _max_lengths(json.loads(path.read_text(encoding="utf-8")))
    }


def test_the_pinned_population_is_every_published_input_side_bound() -> None:
    """The table names exactly the bounds the tree publishes, both directions.

    Without this, a ``maxLength`` added to a new tool's schema is simply not
    pinned, and the parametrized test below reports a clean sweep over the
    subset somebody remembered. The precedent is ``PROCESS_SPAWN_SITES`` in
    ``test_network_call_sites.py``, asserted by equality so it fails on an
    addition *and* on a removal.
    """
    published = set(_published_bounds())

    assert published == set(BOUNDS), (
        f"published with no pin here (a hand-transcribed number nothing holds to its "
        f"constant): {sorted(published - set(BOUNDS))}; pinned here and no longer "
        f"published (the pin now holds nothing): {sorted(set(BOUNDS) - published)}"
    )


@pytest.mark.parametrize(
    ("location", "expected"), sorted(BOUNDS.items()), ids=[f"{f}:{p}" for f, p in sorted(BOUNDS)]
)
def test_a_published_bound_is_the_constant_its_handler_enforces(
    location: tuple[str, str], expected: tuple[str, int, str]
) -> None:
    """RED means the wire contract and the code that enforces it disagree.

    Which direction the disagreement runs decides who it hurts, and both are
    named in the failure: a published bound above the constant promises a call
    that is refused below the surface; one below it refuses, at the wire, a value
    the handler would have served -- and replaces the handler's remedy with
    "does not satisfy 'maxLength'".
    """
    filename, pointer = location
    constant_name, constant_value, enforced_by = expected
    published = _published_bounds()[location]

    assert published == constant_value, (
        f"{filename} publishes maxLength {published!r} at {pointer}, and "
        f"{constant_name} is {constant_value}, enforced in {enforced_by}.\n\n"
        f"Nothing validates a published schema against the module it was transcribed "
        f"from, so the two enforce nothing together unless they are the same number. "
        f"Move both in the same change."
    )


def test_the_walk_reaches_a_bound_below_the_top_level() -> None:
    """The premise the population equality rests on: the walk sees a nested bound.

    Every published bound today is a top-level ``properties/<name>``, so a walk
    simplified to *only* that shape would still be green -- and the population
    equality above, which is what makes a new unpinned bound fail, would silently
    stop covering the first ``maxLength`` written inside an ``items`` or a nested
    object. ADR-0032 decision 3's table already assigns the write-intent surface
    an array property (``uniqueItems`` on ``labels[]``), so a per-element bound
    beside it is the next schema's shape rather than a hypothetical one.

    Driven against a synthetic document, because no published schema has that
    shape yet and a premise check that waits for one is a premise check that
    fails open.
    """
    nested = {
        "properties": {
            "labels": {"type": "array", "items": {"type": "string", "maxLength": 7}},
            "nested": {"type": "object", "properties": {"inner": {"maxLength": 9}}},
        }
    }

    assert dict(_max_lengths(nested)) == {
        "properties/labels/items": 7,
        "properties/nested/properties/inner": 9,
    }
