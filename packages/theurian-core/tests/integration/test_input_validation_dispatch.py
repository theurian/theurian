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
  ``413`` that names no tool. Both constants are pinned by recomputation and
  the boundary between the two tiers is driven at exact bytes, on both sides.

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
from mcp.server import MCPServer

from theurian.application.project_service import ProjectRegistry
from theurian.daemon.runner import build_server
from theurian.daemon.server import MAX_REQUEST_BODY_BYTES
from theurian.mcp.tools import _tool
from theurian.mcp.validation import (
    MAX_PARAMS_NESTING,
    MAX_PARAMS_NODES,
    MAX_PARAMS_RENDERED_CHARS,
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


def test_the_transport_body_cap_is_derived_from_the_cap_on_a_landed_file() -> None:
    """:data:`MAX_REQUEST_BODY_BYTES` is a derivation, and is pinned as one (#669).

    The largest legitimate body this daemon is sized for is a write-intent one,
    and what bounds that is its landed form:
    :data:`~theurian.security.paths.MAX_SOURCE_FILE_BYTES` (ADR-0032 decision
    3). The ``2 *`` is the worst realistic JSON wire expansion of such a body --
    every character taking a two-byte escape, or ``ensure_ascii``-escaped CJK --
    and the ``+ 1 MiB`` is headroom for the JSON-RPC envelope around it.

    Recomputed from the live constants rather than compared against the
    17,825,792 it evaluates to today. A transcribed total would stay green
    against a cap re-derived from a different base, and would have to be edited
    by hand to describe a change it was supposed to catch. The factors are also
    what decides which encodings still meet the bare ``413`` -- the residual
    recorded on the constant itself -- so moving one is a decision to re-record,
    not a number to retune.
    """
    assert MAX_REQUEST_BODY_BYTES == 2 * MAX_SOURCE_FILE_BYTES + 1024 * 1024, (
        f"MAX_REQUEST_BODY_BYTES ({MAX_REQUEST_BODY_BYTES}) is no longer "
        f"2 * MAX_SOURCE_FILE_BYTES ({MAX_SOURCE_FILE_BYTES}) + 1 MiB. The derivation is "
        f"what daemon/server.py records a rationale for: the multiplier bounds the wire "
        f"expansion of a body that lands at MAX_SOURCE_FILE_BYTES, and the addend is "
        f"JSON-RPC envelope headroom. If the base or either factor moved, the recorded "
        f"residual -- which encodings still meet the bare 413 at a landed size the store "
        f"would accept -- moved with it and owes a re-measurement, not an edit here"
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


def _raw_call_of_exactly(size: int) -> bytes:
    """A serialized ``tools/call`` whose body is exactly ``size`` bytes.

    The envelope overhead is measured from the serialized skeleton rather than
    counted by hand, and the padding is ASCII, so one character is one byte and
    the total is exact. The caller asserts the length before posting: a body
    that is merely "about" the cap cannot tell a ``>`` from a ``>=``, and that
    is the whole distinction the boundary tests below exist to pin.
    """

    def envelope(query: str) -> bytes:
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
        ).encode()

    raw = envelope(SENTINEL + "a" * (size - len(envelope(SENTINEL))))
    assert len(raw) == size, (len(raw), size)
    return raw


def test_a_body_between_the_two_caps_meets_the_bounded_refusal_over_the_wire(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """The refusal #669 made reachable, driven where only it can answer.

    13 MiB of query is past :data:`MAX_PARAMS_RENDERED_CHARS` and under
    :data:`MAX_REQUEST_BODY_BYTES`, so it is the size class that distinguishes
    the two tiers: it arrives, is framed, and meets ``validation.py``'s own
    bounded refusal. Before #669 the same request was answered ``413`` by a tier
    that knows no tools, which is why the constant's docstring could claim a
    refusal shipped code could not produce.

    Asserted on the refusal's *wording* -- the unit and the limit -- and not on
    ``isError`` alone, because every oversize axis this seam refuses sets
    ``isError``; what says the width bound is the one that answered is the text
    it interpolates. And the response carries none of what was sent: this is the
    widest body that reaches MCP framing at all, so an echo here is the largest
    amplifier the surface has.
    """
    query = SENTINEL + "a" * (13 * 1024 * 1024 - len(SENTINEL))

    with mcp_session(build_server(registry), tmp_path / "data") as call:
        answer = call("knowledge.search", {"projectId": "demo", "query": query})

    result = answer["result"]
    text = result["content"][0]["text"]
    assert result["isError"] is True, text
    assert "characters of content" in text, text
    assert str(MAX_PARAMS_RENDERED_CHARS) in text, text
    assert "knowledge.search" in text, text
    assert SENTINEL not in json.dumps(answer), text


def test_a_write_intent_sized_body_arrives_and_is_refused_by_its_schema(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """#669's own acceptance: the body ADR-0032 sizes for can reach MCP framing.

    :data:`~theurian.security.paths.MAX_SOURCE_FILE_BYTES` is the byte cap
    ADR-0032 decision 3 puts on the file a proposal lands, so a write-intent
    call carries a body of that size class -- and until #669 the SDK's
    unrecorded 4 MiB default answered it ``413`` before any tool was named. That
    is the precondition slice B4 needs, and it is asserted here as *which tier
    refuses*: the schema, naming the tool and the constraint it failed, rather
    than a transport tier that knows neither.

    ``mcp_session``'s own ``assert response.status_code == 200`` is load-bearing
    -- a ``413`` fails this test inside the helper before any assertion below
    runs -- and the schema's wording is what proves the refusal came from the
    tier that reads contracts. ``knowledge.search`` stands in for the write tool
    because it is registered today; what is under test is the wire size class,
    which is a property of the transport and not of the tool.
    """
    query = SENTINEL + "a" * (MAX_SOURCE_FILE_BYTES - len(SENTINEL))

    with mcp_session(build_server(registry), tmp_path / "data") as call:
        answer = call("knowledge.search", {"projectId": "demo", "query": query})

    result = answer["result"]
    text = result["content"][0]["text"]
    assert result["isError"] is True, text
    assert "knowledge.search" in text and "maxLength" in text, text
    assert "characters of content" not in text, text
    assert SENTINEL not in json.dumps(answer), text


def test_a_body_of_exactly_the_transport_cap_still_reaches_mcp_framing(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """The admitted side of the boundary, at the exact byte.

    ``RequestBodyLimitMiddleware`` compares ``declared_size > max_body_size``,
    so the cap itself is admitted; a test sized "near" the cap cannot tell that
    from ``>=`` and would stay green through an off-by-one that starts refusing
    a body this daemon means to serve. The answer is a framed MCP message --
    the query is far past :data:`MAX_PARAMS_RENDERED_CHARS`, so what it meets is
    this seam's bounded refusal, which is the point: at the cap the caller is
    still told which limit it passed.
    """
    raw = _raw_call_of_exactly(MAX_REQUEST_BODY_BYTES)

    with open_client(build_server(registry), tmp_path / "data") as (client, session):
        answer = client.post("/mcp", content=raw, headers=headers(session))

    assert answer.status_code == 200, (answer.status_code, answer.text[:200])
    framed = payload(answer)
    assert framed["result"]["isError"] is True, framed
    assert SENTINEL not in json.dumps(framed), framed


def test_a_body_one_byte_past_the_transport_cap_is_refused_without_echoing_it(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """The refused side of the same boundary, one byte along.

    Paired with the test above, this is what makes a silently moved cap fail a
    test rather than a comment: the two together pin
    :data:`MAX_REQUEST_BODY_BYTES` to the exact byte at which the answer stops
    being an MCP message and becomes a transport ``413``.

    What comes back is the SDK's bare ``Request body too large`` -- no tool, no
    remedy, no refusal shape -- which is inherent to a tier that runs before any
    MCP framing exists and is why the cap is set where a legitimate request does
    not meet it. The size assertion is the one that matters for #17: this is the
    largest body the process accepts at all, so a ``413`` that quoted any of it
    back would be the surface's biggest amplifier, and it is checked by
    searching the whole response for the sentinel rather than by trusting that
    the body looked short.
    """
    raw = _raw_call_of_exactly(MAX_REQUEST_BODY_BYTES + 1)

    with open_client(build_server(registry), tmp_path / "data") as (client, session):
        refused = client.post("/mcp", content=raw, headers=headers(session))

    assert refused.status_code == 413, (refused.status_code, refused.text[:200])
    assert len(refused.content) < 100, len(refused.content)
    assert SENTINEL not in refused.text, refused.text[:200]
    assert "aaaa" not in refused.text, refused.text[:200]
