"""SEC-12's structural claims, driven at dispatch (ADR-0031 decisions 2, 4, 5).

``test_input_validation_wire.py`` holds what the middleware refuses about a
*request*. This file holds the two claims that are about the registered **set**
rather than about any one request, plus the bounds that must hold before
``jsonschema`` is handed anything:

* **Fail-closed by structure** (decision 5). A tool registered without a
  published input schema is *unreachable* -- refused at dispatch, its handler
  never entered. Asserted through a real ``tools/call`` against a server
  carrying exactly that tool, with a sentinel the handler would set if it ran.
* **The population is derived, not listed** (decision 5's owed sweep). Every
  tool the built server registers resolves to a loaded schema, and every loaded
  schema names a registered tool -- set equality in both directions, so a schema
  published for a tool that no longer exists fails here too. The precedent is
  ``test_network_call_sites.py``'s ``PROCESS_SPAWN_SITES``, asserted by equality
  so it fails on an addition *and* on a removal.
* **The untrusted-document bounds are applied at this boundary** (ADR-0031's
  *Still owed*, the ``#291``/``#245`` caps). ``test_input_validation.py`` drives
  each bound against a synthetic schema set; what is owed here is that a real
  inbound ``tools/call`` past each bound is refused over the wire, and that the
  refusal is bounded -- it does not echo the oversized payload back.
* **The two caps a request meets are ordered, and the order is driven** (#669).
  ``daemon/server.py``'s :data:`MAX_REQUEST_BODY_BYTES` is the byte bound the
  transport answers ``413`` past, and ``mcp/validation.py``'s
  :data:`MAX_PARAMS_RENDERED_CHARS` is the character bound this seam refuses
  past. The first sits above the second, so a request between them arrives,
  is framed, and is told which limit it passed instead of meeting a bare
  ``413`` that names no tool. The transport cap's *derivation* is pinned by
  recomputation, and the boundary between the two tiers is driven at exact
  bytes on both sides -- the pair pins the comparison and the wiring, while a
  moved constant is what the derivation pin catches.
* **The two caps count different things, and neither implies the other**
  (#669, round one). Bytes on the wire and characters under ``{instance!r}``
  are not the same quantity in either direction: a body that lands at the file
  cap takes three times its landed bytes on the wire once ``ensure_ascii`` has
  escaped it, and a body whose *byte* count is a third of the character budget
  renders past that budget once ``repr`` has escaped it. Both directions are
  driven here, each at the size class that shows it.

Everything here goes through the transport for the reason
``test_input_validation_wire.py`` records: ``server.call_tool`` is the SDK's
tool dispatcher and sits *below* the ``ServerMiddleware`` tier, so an
in-process call never reaches the seat under test.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

import pytest
from escape_class_sweep import expected_width
from mcp.server import MCPServer
from wire_escape_classes import COVERED_CLASSES, WIRE_CLASSES

from theurian.application.project_service import ProjectRegistry
from theurian.daemon.runner import build_server
from theurian.daemon.server import MAX_REQUEST_BODY_BYTES
from theurian.mcp.tools import _tool
from theurian.mcp.validation import (
    MAX_ECHOED_FRAGMENT_CHARS,
    MAX_PARAMS_NESTING,
    MAX_PARAMS_NODES,
    MAX_PARAMS_RENDERED_CHARS,
    MAX_REFUSAL_CHARS,
    _rendered_width,
)
from theurian.security.paths import MAX_SOURCE_FILE_BYTES

from mcp_server_probe import loaded_input_schemas, registered_tool_names  # isort: skip
from mcp_wire_session import headers, mcp_session, open_client, payload  # isort: skip

pytestmark = pytest.mark.integration

#: A tool name no published schema declares, and no ``@_tool`` in
#: ``mcp/tools.py`` registers. Checked against both, below, so this file cannot
#: quietly start testing a tool that grew a contract.
UNPUBLISHED_TOOL: Final = "test.without.a.schema"

#: A value no message in this codebase could produce. Searched for across the
#: whole response, so "the refusal does not echo what the caller sent" is
#: checkable without trusting that a test looked at the right field.
SENTINEL: Final = "sentinel-value-3b7e41af"

#: The classes the acceptance below is driven over: **every class the cap's
#: multiplier is derived for**, taken from the table rather than re-typed here.
#:
#: Derived, and that is the point. A list written out in this file is a list
#: someone keeps in step, and it drifts in the direction nobody notices -- an
#: astral representative replaced by an ASCII one leaves four cases that read
#: like four classes and exercise one expansion factor. Reading
#: ``COVERED_CLASSES`` off the table means a class added to the derivation joins
#: this acceptance by existing, a class moved into the residual leaves it, and a
#: representative that stops representing its class fails in
#: ``tests/unit/test_transport_body_cap.py`` where the integrity of the table is
#: held. What is driven here is the consequence: a body of each, landing at the
#: file cap, still arrives framed.
SCRIPT_CLASSES: Final = {name: WIRE_CLASSES[name].character for name in sorted(COVERED_CLASSES)}

#: How many characters ``repr`` renders each class's representative as, from
#: ``escape_class_sweep``'s rule rather than from ``_rendered_width``. The tier
#: assertion below needs a prediction *independent* of the charge it is
#: checking: an expectation computed by calling the decider agrees with the
#: decider whatever either does.
#:
#: Derived, not written out, and the first draft of this table is why. It listed
#: ``json_escapable`` at 2 -- the factor JSON's ``\"`` costs on the *wire* --
#: where ``repr`` never escapes a double quote at all and renders it as itself.
#: The two tables answer different questions about the same character, which is
#: the confusion ``MAX_REQUEST_BODY_BYTES``'s docstring ends on, and hand-copying
#: one into the other reproduced it inside the arm meant to be independent.
RENDER_WIDTH: Final = {
    name: expected_width(character) for name, character in SCRIPT_CLASSES.items()
}

#: A character ``repr`` renders as a six-character ``\uXXXX`` escape while JSON
#: sends it raw as its own two UTF-8 bytes -- U+0600 ARABIC NUMBER SIGN, a
#: format character ``str.isprintable`` rejects. Six rendered characters per
#: code point is the most any BMP character costs, and three rendered characters
#: per *wire byte* is what makes the gap this transport cannot bound: the seam
#: :func:`~theurian.mcp.validation._rendered_width` charges for exists because
#: no byte cap implies a render cap.
SIX_CHARACTER_RENDER: Final = "؀"


@pytest.fixture
def registry(tmp_path: Path) -> ProjectRegistry:
    return ProjectRegistry(path=tmp_path / "projects.json")


@contextmanager
def _server_with_an_unpublished_tool(
    registry: ProjectRegistry,
) -> Iterator[tuple[MCPServer, list[str]]]:
    """A real built server carrying one extra tool that has no published schema.

    Registered through ``_tool`` -- the one registration seam -- rather than
    through ``server.tool`` directly, so the extra tool is the same kind of
    object every real tool is and the difference under test is *only* the
    missing schema.

    **Neither seam pin is tripped by this, and that is a property of what they
    read rather than of this comment.** Both
    ``test_tool_error_type_contract.py::test_every_tool_is_registered_through_the_one_seam``
    (which parses ``register``'s AST) and
    ``test_mcp_tools.py::test_every_registered_tool_goes_through_the_forwarding_seam``
    (which calls ``build_server`` itself and asks *that* server) constrain the
    production registration set. This registration happens on a server object
    this test built and holds, after ``build_server`` returned, so neither sees
    it -- and going through ``_tool`` means it would satisfy both if they did.

    Yields the server and the list the handler appends to. The list stays empty
    unless the handler runs, which is the assertion: a guard no data reaches
    survives its own deletion, so non-execution is what is asserted rather than
    the presence of a refusal alone.
    """
    server = build_server(registry)
    entered: list[str] = []

    @_tool(
        server,
        name=UNPUBLISHED_TOOL,
        description="A tool with no published input schema. Registered by a test only.",
    )
    def without_a_schema(marker: str) -> dict[str, str]:
        entered.append(marker)
        return {"marker": marker}

    yield server, entered


def test_a_tool_with_no_published_schema_is_refused_at_dispatch(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """ADR-0031 decision 5: a tool with no published input schema is unreachable.

    This is the clause that makes SEC-12 survive the next contributor. A control
    that applies to the tools somebody remembered to enumerate is one a future
    tool leaves by omission, and the omission is silent in the direction that
    matters -- the new tool is served, unvalidated, and nothing says so.

    **The assertion is that the handler did not run**, not that a refusal came
    back. A refusal assertion alone would still pass against a build that
    refused *after* dispatching, which is the same disclosure with a worse
    error message.
    """
    with (
        _server_with_an_unpublished_tool(registry) as (server, entered),
        mcp_session(server, tmp_path / "data") as call,
    ):
        answer = call(UNPUBLISHED_TOOL, {"marker": SENTINEL})

    assert entered == [], (
        f"the handler for {UNPUBLISHED_TOOL!r} ran with {entered!r}, so a tool "
        f"registered without a published input schema is reachable and SEC-12 "
        f"applies only to the tools someone remembered to publish"
    )
    assert answer["result"]["isError"] is True, answer
    assert "publishes no input schema" in answer["result"]["content"][0]["text"], answer
    assert SENTINEL not in json.dumps(answer), answer


def test_an_ordinary_tool_is_still_served_on_that_same_server(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """The positive control the refusal above is worthless without.

    A refusal test with no served counterpart passes for a server that refuses
    everything, which is the failure mode ADR-0031 names when it owes this pair.
    Same server, same session, same middleware -- only the tool differs.
    """
    with (
        _server_with_an_unpublished_tool(registry) as (server, _entered),
        mcp_session(server, tmp_path / "data") as call,
    ):
        refused = call(UNPUBLISHED_TOOL, {"marker": SENTINEL})
        served = call("project.list", {})

    assert refused["result"]["isError"] is True, refused
    assert served["result"]["isError"] is False, served
    assert served["result"]["structuredContent"]["projects"] == [], served


def test_the_unpublished_tool_name_is_one_no_build_artifact_claims(
    registry: ProjectRegistry,
) -> None:
    """The premise the two tests above rest on, checked rather than assumed.

    If ``UNPUBLISHED_TOOL`` ever became a real tool name -- or a schema were
    published under it -- the refusal test would be asserting that a *published*
    tool is refused, which is the opposite claim, and it would still be green.
    """
    server = build_server(registry)

    assert UNPUBLISHED_TOOL not in loaded_input_schemas(server).tool_names
    assert UNPUBLISHED_TOOL not in registered_tool_names(server)


def test_every_registered_tool_resolves_to_a_published_input_schema(
    registry: ProjectRegistry,
) -> None:
    """The derived population, in both directions (ADR-0031's owed sweep).

    Walked off the **built** server rather than off a list in this file, so a
    tool added later joins the sweep by existing -- the shape
    ``test_mcp_tools.py::test_no_registered_tool_can_reach_a_canonical_write``
    uses, and for the same reason: a list is a thing someone updates, a walk is
    a property.

    **Equality, not containment.** A schema published for a tool this build no
    longer registers is a finding too: it is a wire contract a client may read
    and code against, describing a call that answers "no such tool". The
    precedent is ``PROCESS_SPAWN_SITES`` in ``test_network_call_sites.py``,
    asserted by equality so it fails on an addition *and* on a removal.
    """
    server = build_server(registry)
    registered = registered_tool_names(server)
    published = set(loaded_input_schemas(server).tool_names)

    assert registered, "an empty tool list would pass this test vacuously"
    assert registered == published, (
        f"registered with no published input schema (refused at dispatch, so "
        f"unreachable): {sorted(registered - published)}; published for a tool "
        f"this build does not register (a contract describing a call that "
        f"answers 'no such tool'): {sorted(published - registered)}"
    )


def test_every_published_schema_names_the_file_it_came_from(registry: ProjectRegistry) -> None:
    """The sweep above compares names; this is what makes a failure locatable.

    Without it, ``registered == published`` failing names a tool and not the
    artifact to edit. ``origins`` is the loader's own record of which file
    claimed which tool, so it is asserted to cover the same population rather
    than left as an unchecked convenience.
    """
    schemas = loaded_input_schemas(build_server(registry))

    assert set(schemas.origins) == set(schemas.tool_names)
    for tool_name, origin in schemas.origins.items():
        assert origin.name.endswith("-input.schema.json"), (tool_name, origin)
        assert origin.is_file(), (tool_name, origin)


def _nested(depth: int) -> dict[str, Any]:
    """Arguments whose deepest value sits at ``depth``, counting the arguments
    object as level 1 -- the way :func:`~theurian.mcp.validation._unbounded`
    counts.

    ``{"a": 1}`` is depth 2: the mapping is 1 and the value inside it is 2. So
    ``depth`` levels need ``depth - 2`` intermediate mappings under the root.
    """
    innermost: Any = 1
    for _ in range(depth - 2):
        innermost = {"a": innermost}
    return {"a": innermost}


def test_arguments_past_the_nesting_bound_are_refused_over_the_wire(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """The #291/#245 caps apply at the MCP boundary, not only in the loader.

    ``jsonschema`` renders a failing instance with ``{instance!r}`` while
    building its own message, and that render recurses -- so past CPython's C
    recursion budget the library cannot build even its refusal, and the
    ``RecursionError`` that escapes is indistinguishable from a broken schema.
    The bound is checked *before* the document is handed over, which is what
    this drives: one level past the cap, refused, over a real ``tools/call``.
    """
    with mcp_session(build_server(registry), tmp_path / "data") as call:
        answer = call("knowledge.search", _nested(MAX_PARAMS_NESTING + 1))

    text = answer["result"]["content"][0]["text"]
    assert answer["result"]["isError"] is True, answer
    assert "levels of nesting" in text, text
    assert str(MAX_PARAMS_NESTING) in text, text


def test_arguments_at_the_nesting_bound_reach_the_schema(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """The positive control for the bound above: at the cap, the schema decides.

    Without it, the refusal test passes against a boundary that refuses every
    nested document -- and the two answers are told apart by *which* refusal
    arrives, so the assertion is on the schema's wording rather than on
    ``isError``. A document at the cap is still invalid input (it carries an
    unknown key ``a``), and being refused *by the schema* is the proof it got
    that far.
    """
    with mcp_session(build_server(registry), tmp_path / "data") as call:
        answer = call("knowledge.search", _nested(MAX_PARAMS_NESTING))

    text = answer["result"]["content"][0]["text"]
    assert answer["result"]["isError"] is True, answer
    assert "published input schema" in text, text
    assert "levels of nesting" not in text, text


def test_arguments_past_the_node_bound_are_refused_over_the_wire(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """A wide document costs per-node work before any keyword runs.

    Nesting is not the only unbounded axis: a flat list of a million elements
    never recurses and still walks a million values, in this module's own walk
    and again in ``jsonschema``'s. Sized one node past the cap rather than
    generously -- this is a correctness pin, not a load test.
    """
    # `discovered` starts at 1 for the arguments object itself, and the two keys
    # and the list below are 3 more, so a list of MAX_PARAMS_NODES elements
    # crosses the bound by exactly the elements' own count.
    arguments = {"projectId": "demo", "values": [0] * MAX_PARAMS_NODES}

    with mcp_session(build_server(registry), tmp_path / "data") as call:
        answer = call("knowledge.search", arguments)

    text = answer["result"]["content"][0]["text"]
    assert answer["result"]["isError"] is True, answer
    assert "more values than this daemon will validate" in text, text
    assert str(MAX_PARAMS_NODES) in text, text


def test_the_widest_body_the_schema_tier_can_see_is_refused_without_echoing_it(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """One node, unbounded width: the axis a node count cannot see.

    A single string is one node and renders to as many characters as it holds,
    which is what ``MAX_PARAMS_NODES`` is blind to. Driven at the widest value
    that still reaches ``jsonschema`` -- :data:`MAX_PARAMS_RENDERED_CHARS`, this
    seam's own bound, which since #669 is the *lower* of the two caps a request
    meets and therefore the one that decides how wide a string the schema ever
    sees. The assertion is decision 4's: the answer names a key path and a
    constraint, and is orders of magnitude smaller than the request. A refusal
    that quoted the value back would make this boundary a ~1x amplifier of the
    caller's own bytes (#17).

    The sizing was ``DEFAULT_MAX_REQUEST_BODY_SIZE - 500`` while the SDK's 4 MiB
    default was the first ceiling an inbound body met. ``build_app`` now passes
    its own :data:`MAX_REQUEST_BODY_BYTES`, so that default governs nothing this
    daemon serves and a size derived from it would be a number with no live
    meaning -- re-anchored here to the constant that does govern.

    **The value opens with the sentinel**, so "nothing of what the caller sent
    came back" is checked by searching the whole response rather than by reading
    the one field this test remembered to look at -- and a *bounded* echo, the
    120-character one ``_echo`` would produce, fails it exactly as an unbounded
    one does. A payload of one repeated character would survive that mutation:
    the response is still small, and a prefix assertion on ``"aaa..."`` only
    catches an echo longer than whatever prefix was guessed.
    """
    # Room inside the rendered-character budget for the rest of the arguments --
    # the keys and `projectId` are charged against it too -- so what answers is
    # the schema and not this seam's own width refusal, which the test below
    # drives on purpose.
    oversized = SENTINEL + "a" * (MAX_PARAMS_RENDERED_CHARS - 500 - len(SENTINEL))

    with mcp_session(build_server(registry), tmp_path / "data") as call:
        answer = call("knowledge.search", {"projectId": "demo", "query": oversized})

    result = answer["result"]
    text = result["content"][0]["text"]
    assert result["isError"] is True, text
    assert "query" in text and "maxLength" in text, text
    assert SENTINEL not in json.dumps(answer), text
    assert len(json.dumps(answer)) < len(oversized) // 1000, len(json.dumps(answer))


def test_an_oversized_key_is_echoed_back_cut_rather_than_withheld(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """Decision 4 names the offending *key*, and bounds the naming.

    The two claims a refusal makes about caller-written text pull opposite ways.
    A refused **value** is never echoed, because a value is arbitrary payload
    (the tests above). A refused **key** *is* echoed, because a caller who sent
    the wrong field name cannot act on a refusal that will not say which one --
    and what keeps that from becoming an amplifier is
    :data:`MAX_ECHOED_FRAGMENT_CHARS`, not silence.

    So the designed behaviour is a *bounded* echo, and a test that asserted the
    key was absent would be asserting the opposite of the decision while looking
    like a stricter version of it. Driven in both directions here: the opening
    :data:`SENTINEL` comes back, so the echo exists; ten thousand characters of
    padding do not, so it is cut.

    A run of exactly :data:`MAX_ECHOED_FRAGMENT_CHARS` padding characters is the
    negative: the fragment cap is what stops the echo before one can form, so
    removing the cut leaves the whole 10,000-character key in the message and
    that run appears. Asserting on the run rather than on the whole key catches
    a cut that was merely loosened, which "the key is not in the message" would
    not.
    """
    padding = "b" * 10_000
    key = SENTINEL + padding

    with mcp_session(build_server(registry), tmp_path / "data") as call:
        answer = call("knowledge.search", {"projectId": "demo", "query": "x", key: 1})

    text = answer["result"]["content"][0]["text"]
    assert answer["result"]["isError"] is True, text
    assert SENTINEL in text, (
        f"the refusal no longer names the key the caller got wrong: {text!r}. ADR-0031 "
        f"decision 4 echoes the key path precisely so a caller can act on the refusal; a "
        f"refusal that withholds it is not a stricter version of that decision, it is a "
        f"different one and is not recorded anywhere"
    )
    assert "b" * MAX_ECHOED_FRAGMENT_CHARS not in text, (
        f"a run of {MAX_ECHOED_FRAGMENT_CHARS} padding characters from a {len(key)}-character "
        f"key reached the refusal, so the echo is no longer cut at "
        f"MAX_ECHOED_FRAGMENT_CHARS and this boundary is an amplifier of the caller's own "
        f"bytes (#17)"
    )
    assert len(text) <= MAX_REFUSAL_CHARS, len(text)


def test_the_transport_body_cap_is_derived_from_the_cap_on_a_landed_file() -> None:
    """:data:`MAX_REQUEST_BODY_BYTES` is a derivation, and is pinned as one (#669).

    The largest legitimate body this daemon is sized for is a write-intent one,
    and what bounds that is its landed form:
    :data:`~theurian.security.paths.MAX_SOURCE_FILE_BYTES` (ADR-0032 decision
    3). The ``3 *`` is the worst ratio of wire bytes to landed UTF-8 bytes that
    any non-control text reaches, and the ``+ 1 MiB`` is headroom for the
    JSON-RPC envelope around it.

    **The authority for the ``3`` is an enumeration, not this line.** It is the
    maximum of the ``ensure_ascii`` column over every wire-escape class, one
    representative per UTF-8 byte length, measured in
    ``tests/unit/test_transport_body_cap.py`` -- which also asserts that the
    classes exceeding it are exactly the two control rows the constant records
    as its residual. What this pins is that the *formula* is still the one that
    derivation feeds: a cap edited to a literal, or re-derived from some other
    base, fails here.

    Recomputed from the live constants rather than compared against the
    26,214,400 it evaluates to today. A transcribed total would stay green
    against a cap re-derived from a different base, and would have to be edited
    by hand to describe a change it was supposed to catch.
    """
    assert MAX_REQUEST_BODY_BYTES == 3 * MAX_SOURCE_FILE_BYTES + 1024 * 1024, (
        f"MAX_REQUEST_BODY_BYTES ({MAX_REQUEST_BODY_BYTES}) is no longer "
        f"3 * MAX_SOURCE_FILE_BYTES ({MAX_SOURCE_FILE_BYTES}) + 1 MiB. The derivation is "
        f"what daemon/server.py records a rationale for: the multiplier is the worst wire "
        f"expansion of a body that lands at MAX_SOURCE_FILE_BYTES, measured per escape class "
        f"in tests/unit/test_transport_body_cap.py, and the addend is JSON-RPC envelope "
        f"headroom. If the base or either factor moved, the recorded residual -- which "
        f"encodings still meet the bare 413 at a landed size the store would accept -- moved "
        f"with it and owes a re-measurement of that whole table, not an edit here"
    )


def test_the_transport_body_cap_sits_above_the_rendered_character_bound() -> None:
    """The ordering #669 decided, pinned from both live constants.

    A request meets two caps, at two tiers. Past
    :data:`MAX_REQUEST_BODY_BYTES` the SDK's ``RequestBodyLimitMiddleware``
    answers a bare ``413 Request body too large`` before any MCP framing exists
    -- it names no tool, carries no remedy, and has no refusal shape. Past
    :data:`MAX_PARAMS_RENDERED_CHARS` ``validation.py`` answers a framed refusal
    that names the tool and the limit passed. Which one a caller gets is decided
    entirely by which constant is larger, so the ordering is the behaviour and
    is asserted rather than described.

    Both directions have teeth. Lowering the transport cap under
    :data:`MAX_PARAMS_RENDERED_CHARS` makes the rendered-width refusal
    unreachable again and re-opens #669's class -- that is the state this
    project shipped in until #669, and the wire tests below would then be
    driving a ``413`` while claiming a framed refusal. Raising
    :data:`MAX_PARAMS_RENDERED_CHARS` above the transport cap does the same from
    the other side.
    """
    assert MAX_REQUEST_BODY_BYTES > MAX_PARAMS_RENDERED_CHARS, (
        f"the transport's body cap ({MAX_REQUEST_BODY_BYTES} bytes) no longer sits above "
        f"this daemon's rendered-character bound ({MAX_PARAMS_RENDERED_CHARS}), so a "
        f"request wide enough to pass the second one is answered by the bare 413 of a "
        f"tier that knows no tools, and mcp/validation.py's bounded refusal for that "
        f"axis is unreachable over the shipped transport -- #669's class, re-opened. "
        f"Whichever constant moved, the decision to re-record is which refusal a caller "
        f"between the two bounds receives"
    )


def _raw_call_carrying(query: str, *, ensure_ascii: bool) -> bytes:
    """A serialized ``tools/call`` carrying ``query``, encoded the caller's way.

    ``ensure_ascii`` is a required keyword rather than a default because the
    encoding *is* the variable under test in this file. A body's wire size is a
    function of it -- 2-byte script text costs 3.0x escaped and 1.0x raw -- and
    ``TestClient``'s own ``json=`` kwarg serialises with ``ensure_ascii=False``
    (``httpx2._content``), which is the encoding an escaping client does *not*
    send. A test that went through that kwarg would post the cheap form and pass
    against a cap that cannot carry the expensive one.
    """
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "knowledge.search",
                "arguments": {"projectId": "demo", "query": query},
            },
        },
        separators=(",", ":"),
        ensure_ascii=ensure_ascii,
    ).encode()


def _raw_call_of_exactly(size: int) -> bytes:
    """A serialized ``tools/call`` whose body is exactly ``size`` bytes.

    The envelope overhead is measured from the serialized skeleton rather than
    counted by hand, and the padding is ASCII, so one character is one byte and
    the total is exact. The caller asserts the length before posting: a body
    that is merely "about" the cap cannot tell a ``>`` from a ``>=``, and that
    is the whole distinction the boundary tests below exist to pin.
    """

    def envelope(query: str) -> bytes:
        return _raw_call_carrying(query, ensure_ascii=True)

    raw = envelope(SENTINEL + "a" * (size - len(envelope(SENTINEL))))
    assert len(raw) == size, (len(raw), size)
    return raw


def _landing_at(character: str, size: int) -> str:
    """Text that lands as exactly ``size`` UTF-8 bytes, mostly ``character``.

    The landed side is what :data:`MAX_SOURCE_FILE_BYTES` bounds, so the size
    class under test is a byte count and not a character count -- and a
    character whose UTF-8 length does not divide ``size`` is padded with ASCII
    rather than rounded, because "about the file cap" is not the body ADR-0032
    sizes for. Opens with :data:`SENTINEL` so an echo of any length is caught by
    searching the response rather than by reading one field.
    """
    remaining = size - len(SENTINEL.encode())
    width = len(character.encode())
    text = SENTINEL + character * (remaining // width) + "a" * (remaining % width)

    assert len(text.encode()) == size, (len(text.encode()), size)
    return text


def test_a_body_between_the_two_caps_meets_the_bounded_refusal_over_the_wire(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """The refusal #669 made reachable, driven where only it can answer.

    A query sized halfway between the two caps is past
    :data:`MAX_PARAMS_RENDERED_CHARS` and under
    :data:`MAX_REQUEST_BODY_BYTES`, so it is the size class that distinguishes
    the two tiers: it arrives, is framed, and meets ``validation.py``'s own
    bounded refusal. Before #669 the same request was answered ``413`` by a tier
    that knows no tools, which is why the constant's docstring could claim a
    refusal shipped code could not produce.

    **The midpoint is computed from the two live constants**, not written as a
    literal. A hard-coded 13 MiB sat between them only because both constants
    happened to straddle it; once the transport cap moved from 17,825,792 to
    26,214,400 that literal stopped being the midpoint of anything and would
    have gone on passing while testing a size class nobody had chosen. Derived,
    it stays between the two caps wherever either one goes -- and if they ever
    cross, the ordering pin above is what fails, which is the right place for
    that failure.

    Asserted on the refusal's *wording* -- the unit and the limit -- and not on
    ``isError`` alone, because every oversize axis this seam refuses sets
    ``isError``; what says the width bound is the one that answered is the text
    it interpolates. And the response carries none of what was sent: this is the
    widest body that reaches MCP framing at all, so an echo here is the largest
    amplifier the surface has, and the ratio is asserted as a ratio rather than
    trusted to a sentinel search -- an echo of something the caller sent that
    this test forgot to name still shows up as a response within three orders of
    magnitude of the request.
    """
    midpoint = (MAX_PARAMS_RENDERED_CHARS + MAX_REQUEST_BODY_BYTES) // 2
    assert MAX_PARAMS_RENDERED_CHARS < midpoint < MAX_REQUEST_BODY_BYTES, midpoint
    query = SENTINEL + "a" * (midpoint - len(SENTINEL))

    with mcp_session(build_server(registry), tmp_path / "data") as call:
        answer = call("knowledge.search", {"projectId": "demo", "query": query})

    result = answer["result"]
    text = result["content"][0]["text"]
    assert result["isError"] is True, text
    assert "characters of content" in text, text
    assert str(MAX_PARAMS_RENDERED_CHARS) in text, text
    assert "knowledge.search" in text, text
    assert SENTINEL not in json.dumps(answer), text
    assert len(json.dumps(answer)) < len(query) // 1000, len(json.dumps(answer))


@pytest.mark.parametrize("script", sorted(SCRIPT_CLASSES))
def test_a_write_intent_sized_body_arrives_and_is_refused_by_its_schema(
    registry: ProjectRegistry, tmp_path: Path, script: str
) -> None:
    """#669's own acceptance: the body ADR-0032 sizes for can reach MCP framing.

    :data:`~theurian.security.paths.MAX_SOURCE_FILE_BYTES` is the byte cap
    ADR-0032 decision 3 puts on the file a proposal lands, so a write-intent
    call carries a body of that size class -- and until #669 the SDK's
    unrecorded 4 MiB default answered it ``413`` before any tool was named. That
    is the precondition slice B4 needs, and it is asserted here as *which tier
    refuses*: one that names the tool and the limit it passed, rather than a
    transport tier that knows neither.

    **Parametrized over every class the multiplier is derived for, because the
    wire size is a function of the class.** An ASCII-only version of this test
    was green against a cap of ``2 * MAX_SOURCE_FILE_BYTES + 1 MiB`` while the
    same landed byte count in Cyrillic -- or in any other 2-byte script, or in
    astral characters -- expanded to 3.0x under ``ensure_ascii`` and met the
    bare ``413``.

    **Which refusal each class meets is derived, not asserted flat.** Widening
    the population from four remembered scripts to the table's six covered
    classes turned up a case the flat assertion had no room for: a body of
    newlines lands at the file cap, arrives framed, and is refused by the
    *render charge* rather than by the schema, because ``repr`` spells each one
    ``\\n`` and the charge is therefore 2x the code points -- 16,777,216 against
    a 12,582,912 budget. That is the behaviour
    :data:`~theurian.mcp.validation.MAX_PARAMS_RENDERED_CHARS` records in so
    many words (*"'Can', not 'does' ... a plain one may satisfy a published
    ``maxLength`` first"*), so the expected tier is computed from the live
    charge instead of being one of the two written here. Asserting ``maxLength``
    for all six would have been wrong about the newline class and would have
    read as a defect in the daemon rather than as a defect in the test.

    Posted as raw bytes serialised with ``ensure_ascii=True`` rather than
    through ``TestClient``'s ``json=`` kwarg, which serialises with
    ``ensure_ascii=False``: the escaped form is the expensive one and the only
    one that exercises the multiplier. ``status_code == 200`` is asserted
    directly for the same reason -- a ``413`` here is the regression, so it is
    named rather than left to a helper.

    ``knowledge.search`` stands in for the write tool because it is registered
    today; what is under test is the wire size class, which is a property of the
    transport and not of the tool.
    """
    query = _landing_at(SCRIPT_CLASSES[script], MAX_SOURCE_FILE_BYTES)
    raw = _raw_call_carrying(query, ensure_ascii=True)
    # Derived from the class's own arithmetic -- its landed byte count times the
    # render width its row records -- rather than from `_rendered_width`, which
    # is the thing deciding the outcome: an expectation read off the decider
    # agrees with it however wrong both are. The `+ 24` is the rest of the
    # arguments object, whose keys and `projectId` are charged against the same
    # budget; it is two orders of magnitude below the margin at this size.
    per_code_point = RENDER_WIDTH[script]
    predicted = MAX_SOURCE_FILE_BYTES // len(SCRIPT_CLASSES[script].encode()) * per_code_point + 24
    expected = "characters of content" if predicted > MAX_PARAMS_RENDERED_CHARS else "maxLength"
    charged = _rendered_width(query)

    with open_client(build_server(registry), tmp_path / "data") as (client, session):
        answer = client.post("/mcp", content=raw, headers=headers(session))

    assert answer.status_code == 200, (
        f"a {script} body landing at MAX_SOURCE_FILE_BYTES ({MAX_SOURCE_FILE_BYTES} bytes) "
        f"takes {len(raw)} bytes on the wire under ensure_ascii and was answered "
        f"{answer.status_code} by the transport tier, past MAX_REQUEST_BODY_BYTES "
        f"({MAX_REQUEST_BODY_BYTES}). The write-intent body ADR-0032 sizes for meets a bare "
        f"413 that names no tool in this class -- #669's defect, re-opened for every "
        f"encoding the cap's multiplier does not cover"
    )
    framed = payload(answer)
    result = framed["result"]
    text = result["content"][0]["text"]
    assert result["isError"] is True, text
    assert "knowledge.search" in text, text
    # The two derivations agree exactly only below the budget. Above it
    # `_chunked_width` stops as soon as the running total passes what the caller
    # can accept -- the rest of the leaf is never read -- so the charge comes
    # back a partial sum. That is the behaviour, not a discrepancy, and it is
    # why the agreement is asserted as "both put it on the same side" rather
    # than as equality: the independent arithmetic found this, and an
    # expectation read off `_rendered_width` could not have.
    if predicted > MAX_PARAMS_RENDERED_CHARS:
        assert charged > MAX_PARAMS_RENDERED_CHARS, (
            f"a {script} body landing at the file cap renders {predicted} characters by its "
            f"class's own arithmetic -- past the {MAX_PARAMS_RENDERED_CHARS} budget -- yet "
            f"the charge returns {charged}, inside it. The charge is under-counting this "
            f"class against what its recorded render width says it costs"
        )
    else:
        assert abs(charged - predicted) < MAX_PARAMS_RENDERED_CHARS // 100, (
            f"a {script} body landing at the file cap is charged {charged} rendered "
            f"characters, while its class's own arithmetic -- {MAX_SOURCE_FILE_BYTES} landed "
            f"bytes / {len(SCRIPT_CLASSES[script].encode())} bytes per code point x "
            f"{per_code_point} rendered characters -- predicts {predicted}. The two "
            f"derivations have parted company, so the tier expectation below is no longer a "
            f"check on the charge"
        )
    assert expected in text, (
        f"a {script} body landing at the file cap renders {predicted} characters by its "
        f"class's own arithmetic, against a {MAX_PARAMS_RENDERED_CHARS} budget, so the tier "
        f"that should answer it is the one naming {expected!r}. It answered: {text}"
    )
    assert SENTINEL not in json.dumps(framed), text


def test_escape_heavy_text_smaller_than_the_render_budget_still_exceeds_it(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """The render budget is held by the charge, not by the byte cap above it.

    The premise ``MAX_PARAMS_RENDERED_CHARS`` was first written under -- *"a
    request's rendered width never exceeds the bytes the caller sent"*, a
    sentence ``mcp/validation.py`` now carries only to warn against -- is false,
    and this is the wire shape that shows it.
    ``jsonschema`` renders a failing instance with ``{instance!r}``, and
    ``repr`` escapes: :data:`SIX_CHARACTER_RENDER` costs a caller two UTF-8
    bytes and renders as six characters. So a body **smaller in bytes than
    ``MAX_PARAMS_RENDERED_CHARS`` itself** -- asserted here, and nowhere near
    the transport cap -- is already past that budget, and no ordering of the two
    caps could have refused it.

    What refuses it is :func:`~theurian.mcp.validation._rendered_width` charging
    every leaf at least what ``repr`` renders it as. Charged its own length
    instead, this body is counted at a sixth of its true render and reaches
    ``jsonschema``, which then builds the message the budget exists to prevent
    -- and the test sees the *schema's* ``maxLength`` refusal rather than this
    seam's, which is why the assertion is on which tier answered and not on
    ``isError``.

    Posted raw rather than ``ensure_ascii``-escaped: escaped, the same character
    costs six wire bytes and the gap disappears. The cheap encoding is the
    hostile one here, which is the reverse of the acceptance above, and is why
    both encodings are driven in this file rather than one.
    """
    query = SENTINEL + SIX_CHARACTER_RENDER * (MAX_PARAMS_RENDERED_CHARS // 6 + 1)
    raw = _raw_call_carrying(query, ensure_ascii=False)
    assert len(raw) < MAX_PARAMS_RENDERED_CHARS, (len(raw), MAX_PARAMS_RENDERED_CHARS)

    with open_client(build_server(registry), tmp_path / "data") as (client, session):
        answer = client.post("/mcp", content=raw, headers=headers(session))

    assert answer.status_code == 200, (answer.status_code, answer.text[:200])
    framed = payload(answer)
    text = framed["result"]["content"][0]["text"]
    assert framed["result"]["isError"] is True, text
    assert "characters of content" in text, (
        f"a body of {len(raw)} wire bytes that renders to "
        f"{6 * (MAX_PARAMS_RENDERED_CHARS // 6 + 1)} characters was not refused by the "
        f"rendered-character bound ({MAX_PARAMS_RENDERED_CHARS}); it reached jsonschema, "
        f"which answered {text!r}. _rendered_width is charging this leaf less than repr "
        f"renders it as, so the budget is enforced against a count that is not the render"
    )
    assert str(MAX_PARAMS_RENDERED_CHARS) in text, text
    assert "knowledge.search" in text, text
    assert SENTINEL not in json.dumps(framed), text
    assert SIX_CHARACTER_RENDER not in json.dumps(framed, ensure_ascii=False), text


def test_a_body_of_exactly_the_transport_cap_still_reaches_mcp_framing(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """The admitted side of the boundary, at the exact byte.

    **What this pins is the comparison and the wiring, not the constant.**
    ``RequestBodyLimitMiddleware`` compares ``declared_size > max_body_size``,
    so the cap itself is admitted; a test sized "near" the cap cannot tell that
    from ``>=`` and would stay green through an off-by-one that starts refusing
    a body this daemon means to serve. It equally pins that ``build_app`` is
    still passing ``max_request_body_size`` at all -- drop the kwarg and the
    SDK's own 4 MiB default answers this ``413``. What it does *not* pin is
    where the constant sits: moving the constant moves both this body and the
    cap it is compared against, so both sides of this pair follow it silently.
    The derivation pin above is what catches a moved constant, which a
    perturbation confirmed.

    The answer is a framed MCP message, and *which* framed message is asserted:
    the query is far past :data:`MAX_PARAMS_RENDERED_CHARS`, so at the transport
    cap the caller still gets this seam's bounded refusal naming the limit it
    passed. Without that tier identity the assertion is ``isError`` alone, which
    any framed refusal satisfies -- including one from a tier that had no
    business seeing a body this size.
    """
    raw = _raw_call_of_exactly(MAX_REQUEST_BODY_BYTES)

    with open_client(build_server(registry), tmp_path / "data") as (client, session):
        answer = client.post("/mcp", content=raw, headers=headers(session))

    assert answer.status_code == 200, (answer.status_code, answer.text[:200])
    framed = payload(answer)
    text = framed["result"]["content"][0]["text"]
    assert framed["result"]["isError"] is True, framed
    assert "characters of content" in text, text
    assert str(MAX_PARAMS_RENDERED_CHARS) in text, text
    assert SENTINEL not in json.dumps(framed), framed


def test_a_body_one_byte_past_the_transport_cap_is_refused_without_echoing_it(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """The refused side of the same boundary, one byte along.

    Paired with the test above, this is what pins the *comparison*: the two
    together say the answer stops being an MCP message and becomes a transport
    ``413`` between ``MAX_REQUEST_BODY_BYTES`` and one byte more. Both bodies
    are sized from the constant, so a moved constant moves both and neither
    fails -- what catches that is the derivation pin, and saying so here is the
    correction a round-one review earned: this pair was described as pinning the
    constant, which it never did.

    What comes back is the SDK's bare ``Request body too large`` -- no tool, no
    remedy, no refusal shape -- which is inherent to a tier that runs before any
    MCP framing exists and is why the cap is set where a legitimate request does
    not meet it. The size assertion is the one that matters for #17: this is the
    largest body the process accepts at all, so a ``413`` that quoted any of it
    back would be the surface's biggest amplifier. Asserted at the measured
    length of that bare reason rather than under a round 100 -- 78 spare bytes
    is room for a quoted fragment, and the whole claim is that there is no room
    at all.
    """
    raw = _raw_call_of_exactly(MAX_REQUEST_BODY_BYTES + 1)

    with open_client(build_server(registry), tmp_path / "data") as (client, session):
        refused = client.post("/mcp", content=raw, headers=headers(session))

    assert refused.status_code == 413, (refused.status_code, refused.text[:200])
    assert len(refused.content) == len(b"Request body too large"), refused.text[:200]
    assert SENTINEL not in refused.text, refused.text[:200]
    assert "aaaa" not in refused.text, refused.text[:200]
