"""The two-term memory model ``MAX_REQUEST_BODY_BYTES`` records, at a scaled body.

``daemon/server.py`` prices one in-flight request as two live terms: the
transport's buffers at **2x the wire bytes whatever the body holds**, and the
parse's own peak at **1x, 3x or 5x, set by the body's widest code point**. Four
measured rows sit beside that model at the cap -- 3.00x, 3.01x, 5.00x, 7.00x --
and the model is what makes them a model rather than four readings: each row is
``2 + parse``.

**This file holds the model, not the rows.** The rows are ``tracemalloc`` and
``ru_maxrss`` figures at a 26 MB body; nothing in the build computes 175.1 MiB,
so nothing here can recompute it, and a test asserting it would be a second
transcription of the same reading rather than a check on anything. What *is*
checkable is that the two terms still measure what the model says, and they do
so at any size -- so they are measured at a body a hundredth the cap, which
costs a tenth of a second instead of seconds of a gigabyte-scale allocation.

**Why the model was worth re-deriving, and why it is worth pinning.** An earlier
draft priced the parse term with ``json.loads(body)`` and measured 2·k· the wire
bytes -- a call that decodes the whole body to a ``str`` before parsing it, a
string this request path never builds. The figure it produced for an astral body
was *larger than the whole request's measured peak*, which is how the error was
caught: a term of a model cannot exceed the total it is part of. The SDK parses
with ``pydantic_core.from_json(body)`` straight from the bytes, so that is what
is measured here, and its 1x/3x/5x is the term the corrected model composes from.

Two arms, because the model has two terms and they fail for different reasons.
The parse term is measured directly. The transport term cannot be -- it is
buffers inside Starlette and the SDK, with no seam to call -- so it is measured
as a *difference*: a whole request's peak minus the parse's, on a body that
takes the same path an at-cap body takes.

**Which path matters, and this is the trap the scaled measurement walks into.**
At the cap, an at-cap body is refused by the render charge before ``jsonschema``
sees it. A merely large ASCII body is *not*: it passes the charge and reaches
``jsonschema``, whose ``maxLength`` message reprs the whole instance and adds a
term the model never claimed. Measured on a 4 MB ASCII body that comes out at
5.03x rather than the 3.00x the model predicts, and the difference is entirely
that repr. So the composition arm uses an escape-heavy body that the charge
refuses at a tenth of the cap -- the same shape
``test_input_validation_dispatch.py::test_escape_heavy_text_smaller_than_the_render_budget_still_exceeds_it``
drives -- which is the scaled body that takes the at-cap path.

Instrument: ``tracemalloc`` peak, which answers the Python-heap question. The
``ru_maxrss`` column beside the recorded rows answers a different one and is a
process high-water mark that moves with what the process already touched; it is
not reproducible enough to assert and is deliberately not asserted.
"""

from __future__ import annotations

import gc
import json
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from typing import Final

import pydantic_core
import pytest

from theurian.application.project_service import ProjectRegistry
from theurian.daemon.runner import build_server
from theurian.mcp.validation import MAX_PARAMS_RENDERED_CHARS

from mcp_wire_session import headers, open_client, payload  # isort: skip

pytestmark = pytest.mark.integration

#: A non-printable 2-byte BMP character: two raw wire bytes, six rendered
#: characters. Its ratio is what lets a body a tenth of the transport cap exceed
#: the render budget, which is the only way to reach the at-cap code path at a
#: scaled size.
WIDE_BMP: Final = "؀"

#: A printable astral character. One of these anywhere in a body takes the
#: parsed ``str`` to PEP 393's widest kind, which is the whole reason the parse
#: term is a function of the body's widest code point rather than of its length.
WIDE_ASTRAL: Final = "\U0001f600"

#: How many wire bytes the scaled parse bodies carry. Small enough to be free,
#: large enough that the per-request fixed costs -- a few hundred KB of client,
#: session and framing -- are under a percent of the peak and cannot move a
#: ratio by the tolerance below.
PARSE_BODY_BYTES: Final = 1_000_000

#: What the model says the parse peaks at, per PEP 393 kind of the parsed
#: string. One wire-byte-sized buffer for the string itself at the 1-byte kind;
#: above it, the widened string plus one further wire-byte-sized buffer held
#: while it widens.
PARSE_TERM: Final = {"one_byte_kind": 1.0, "two_byte_kind": 3.0, "four_byte_kind": 5.0}

#: What the model says the transport holds while the parse runs: a ``bytearray``
#: accumulated by ``RequestBodyLimitMiddleware`` and the ``bytes`` copy
#: ``request.body()`` hands on, both live at the parse's peak.
TRANSPORT_TERM: Final = 2.0

#: How far a measured ratio may sit from the model before the model is wrong
#: rather than the machine noisy. Measured 2026-09-15 across three body sizes
#: and two repeats: every ratio landed within **0.06** of its model value and
#: the composition difference within **0.01**, so this is an order of magnitude
#: of slack -- loose enough that a loaded machine does not redden it, tight
#: enough that a term moving by a whole multiple of the wire bytes does.
TOLERANCE: Final = 0.5


def _raw_call(query: str) -> bytes:
    """A serialized ``tools/call`` carrying ``query``, raw UTF-8.

    Raw rather than ``ensure_ascii``-escaped because the body's *widest code
    point* is the variable under test, and escaping erases it: an escaped
    ``\\uXXXX`` body is pure ASCII on the wire whatever it decodes to.
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
        ensure_ascii=False,
    ).encode()


def _peak_of(work: Callable[[], object]) -> int:
    """Python-heap peak, in bytes, allocated while ``work`` runs.

    ``tracemalloc.start`` clears the traced set, so what comes back is the peak
    of what *this* call allocated rather than a figure standing on whatever the
    process already held -- which is the correction that turned a first
    measurement of 5.11x into the 1.00x the model predicts.

    A ``gc.collect`` first, because a collection landing inside the window
    frees memory the peak has already counted and makes the reading depend on
    when the last test happened to allocate.
    """
    gc.collect()
    tracemalloc.start()
    tracemalloc.reset_peak()
    held = work()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del held
    return peak


@pytest.mark.parametrize("kind", sorted(PARSE_TERM))
def test_the_parse_term_is_the_multiple_of_the_wire_bytes_the_model_records(kind: str) -> None:
    """The model's second term, measured where the SDK actually parses.

    PEP 393 sizes a ``str`` by its widest member, so the same million wire bytes
    cost one megabyte of heap as ASCII, three with one 2-byte character
    anywhere in them, and five with one astral character -- a function of the
    body's *widest code point*, not of its length, which is the model's whole
    claim and the reason the four recorded rows differ at all.

    One character decides each row, and that is deliberate: the bodies here are
    identical but for their last two or four bytes, so a ratio that failed to
    move would mean the widening is not happening rather than that the body is
    different.

    Measured through ``pydantic_core.from_json`` on the raw bytes, which is the
    call ``streamable_http.py`` makes. Measuring ``json.loads`` instead is what
    produced the withdrawn figure that exceeded the whole request's peak.
    """
    filler = "a" * PARSE_BODY_BYTES
    widest = {"one_byte_kind": "", "two_byte_kind": WIDE_BMP, "four_byte_kind": WIDE_ASTRAL}[kind]
    raw = _raw_call(filler[: PARSE_BODY_BYTES - len(widest.encode())] + widest)

    ratio = _peak_of(lambda: pydantic_core.from_json(raw)) / len(raw)

    assert abs(ratio - PARSE_TERM[kind]) <= TOLERANCE, (
        f"parsing a {len(raw)}-byte body whose widest code point puts the result in the "
        f"{kind.replace('_', ' ')} peaks at {ratio:.2f}x the wire bytes, not the "
        f"{PARSE_TERM[kind]:.2f}x MAX_REQUEST_BODY_BYTES's model records. That term is one "
        f"of two the four measured at-cap rows compose from, so a term that moved makes "
        f"every one of those rows a reading with no model behind it -- and the recorded "
        f"per-request ceiling wrong by the same multiple of a 26 MB body"
    )


@pytest.mark.parametrize("widest", ["", WIDE_ASTRAL])
def test_the_transport_holds_twice_the_wire_bytes_on_top_of_the_parse(
    widest: str, tmp_path: Path
) -> None:
    """The model's first term, measured as the difference the model says it is.

    There is no seam to call for this one: the two buffers are
    ``RequestBodyLimitMiddleware``'s ``bytearray`` and the ``bytes`` copy
    ``request.body()`` hands on, both inside code this daemon does not own. So
    it is measured the way the model states it -- as what a whole request costs
    *beyond* its parse -- which is also the arithmetic that has to hold for the
    four recorded rows to be ``2 + parse`` rather than four unrelated numbers.

    **The body is escape-heavy on purpose.** A large *ASCII* body of this size
    passes the render charge and reaches ``jsonschema``, whose ``maxLength``
    message reprs the whole instance -- measured 5.03x rather than the model's
    3.00x, all of the difference being that repr. An at-cap body never does
    that, because the charge refuses it first. :data:`WIDE_BMP` is charged six
    characters per two wire bytes, so a body a tenth of the cap is refused by
    the charge exactly as an at-cap body is, and the scaled measurement is taken
    on the path the model describes.

    Parametrized over the widest code point so the difference is shown to be
    *independent* of it: the transport term is 2x whatever the body holds, and
    the parse term is what varies. Asserting it once would not distinguish the
    two.
    """
    query = WIDE_BMP * (MAX_PARAMS_RENDERED_CHARS // 6 + 1 - len(widest)) + widest
    raw = _raw_call(query)
    del query

    with open_client(build_server(ProjectRegistry(path=tmp_path / "p.json")), tmp_path) as (
        client,
        session,
    ):
        client.post("/mcp", content=_raw_call("warm"), headers=headers(session))
        whole = _peak_of(lambda: client.post("/mcp", content=raw, headers=headers(session)))
        answer = client.post("/mcp", content=raw, headers=headers(session))

    parse = _peak_of(lambda: pydantic_core.from_json(raw))
    transport = (whole - parse) / len(raw)

    assert payload(answer)["result"]["isError"] is True, payload(answer)
    assert "characters of content" in payload(answer)["result"]["content"][0]["text"], (
        "the scaled body no longer meets the render charge, so this request takes the "
        "jsonschema path an at-cap request does not and its peak carries a repr term the "
        "model never claimed -- the measurement below is of the wrong path"
    )
    assert abs(transport - TRANSPORT_TERM) <= TOLERANCE, (
        f"a whole request peaks at {whole / len(raw):.2f}x its {len(raw)} wire bytes and its "
        f"parse alone at {parse / len(raw):.2f}x, leaving {transport:.2f}x for the "
        f"transport's own buffers -- not the {TRANSPORT_TERM:.2f}x "
        f"MAX_REQUEST_BODY_BYTES's model records. Either a buffer was added or removed "
        f"between the socket and the parse, or the two terms no longer compose, in which "
        f"case the four at-cap rows recorded on that constant are four readings rather than "
        f"a model and the per-request ceiling cannot be predicted from a body's shape"
    )
