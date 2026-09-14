"""What one in-flight request costs, pinned at the worst member of each path family.

``daemon/server.py`` prices a request as up to **three** terms -- the transport's
buffers, the parse's own peak, and ``jsonschema``'s message construction -- and
records that *which* of them exist depends on the path the request takes. Three
families follow: **(i) charge-refused** shapes, where ``_unbounded`` answers
before ``iter_errors`` is ever called and nothing renders; **(ii)
``jsonschema``-answered** shapes, which pass the charge gate and then fail a
keyword that interpolates ``{instance!r}``; and **(iii) valid** shapes, where
nothing fails and nothing renders.

**This file exists in the shape it does because its first version was wrong, and
wrong in the way that matters.** It pinned a *two*-term model, and its fixture
guard asserted that the body was charge-refused -- which reads as rigour and is
the opposite: it structurally excluded family (ii), the one family whose peak
that model does not predict. A legal 3 MB request measures **38.04x** against
its 7.00x. The premise written above that guard -- *"at the cap, an at-cap body
is refused by the render charge before jsonschema sees it"* -- was false for
every printable multi-byte body, which is charged one character per code point
and passes. And the 5.03x family (ii) produces on plain ASCII had already been
*measured* and written into this module's own prose, as a reason to steer away
from it. So: the families are enumerated first, each family's worst member is
the fixture, and the parts are checked against the measured whole.

**One expression bounds every family**, which is what makes it a model rather
than an arithmetic that fits one row. A request's peak is the larger of the two
moments it passes through::

    peak = max(
        2*wire + parse_peak,                           # the parse moment
        2*wire + code_points*kind + 2*rendered*kind,    # the render moment
    )

Family (i) never reaches the second moment, so its peak is the first -- the
two-term composition the four recorded at-cap rows check. Family (ii) reaches
both, and for its ratio-worst member the second is five times the first. Family
(iii) is family (i) without a refusal.

**Read the expression as an upper bound, not as a prediction**, which is how
``daemon/server.py`` now records it. It is tight -- within **+0.12x**, and
always above, by the request's fixed overhead -- over the five shapes this
module pins, and those five share a scope: each is a **single large leaf** whose
**widest code point is printable or 1-byte**. Outside that scope it
over-predicts rather than failing, because the render moment's ``kind`` is the
*parsed* string's while the strings it prices sit at the width of the ``repr``
*output* -- and ``repr`` escapes every non-printable code point to ASCII, so
only a code point that survives it raw can widen the result. A body of
non-printable astral characters measures 8.11x where the expression says 23.00x.
Swept over the code point space the expression never under-predicts, which is
why it is the bound the daemon can be held to. The arms below drive the shapes
inside the scope; the over-prediction outside it is recorded on the constant,
not asserted here.

**The recorded rows themselves stay unpinned, on purpose.** Nothing in the build
computes 175.1 MiB or 114.1 MiB, so a test asserting one would transcribe the
same reading a second time. What is checkable is the model, and the model holds
at any size -- so it is measured at a body a hundredth of the cap.

Instrument: ``tracemalloc`` peak, the Python-heap question. The ``ru_maxrss``
column beside the recorded rows answers a different one, moves with what the
process already touched, and is deliberately not asserted.

This module's other subject is the *other* recorded memory ceiling this package
carries: ``mcp/validation.py``'s chunked-transient bound, driven at its own
worst instance. It lives here rather than beside the charge tests because it is
a ``tracemalloc`` measurement, and keeping that instrument in one module keeps
the unit tests free of it.
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
from theurian.mcp.validation import (
    _CHUNK_CODE_POINTS,
    MAX_PARAMS_RENDERED_CHARS,
    _chunked_width,
    _rendered_width,
)

from mcp_wire_session import headers, open_client, payload  # isort: skip

pytestmark = pytest.mark.integration

#: A non-printable 2-byte BMP character: two raw wire bytes, six rendered
#: characters. That ratio is what lets a body a tenth of the transport cap
#: exceed the render budget, which is the only way to reach family (i) at a
#: scaled size.
WIDE_BMP: Final = "؀"

#: A printable astral character. One anywhere in a body takes the parsed ``str``
#: to PEP 393's widest kind -- and, on a rendering path, the repr and the
#: message with it.
WIDE_ASTRAL: Final = "\U0001f600"

#: A *non-printable* astral character: ``repr`` spells it ``\\UXXXXXXXX``, ten
#: characters per code point, the widest output the charge's fallback builds.
NARROW_ASTRAL: Final = "\U000e0001"

#: DEL. One raw wire byte, four rendered characters -- the highest
#: rendered-characters-per-wire-byte ratio any code point reaches, which is what
#: makes it the ratio-worst member of family (ii).
DEL: Final = "\x7f"

#: How many wire bytes the scaled bodies carry. Small enough to be free, large
#: enough that the per-request fixed costs -- a few hundred KB of client, session
#: and framing -- stay under a percent of the peak and cannot move a ratio by the
#: tolerance below.
BODY_BYTES: Final = 1_000_000

#: What the model says the parse peaks at, per PEP 393 kind of the parsed
#: string, for a body whose code point count is about its wire byte count.
PARSE_TERM: Final = {"one_byte_kind": 1.0, "two_byte_kind": 3.0, "four_byte_kind": 5.0}

#: What the model says the transport holds while the parse runs: a ``bytearray``
#: accumulated by ``RequestBodyLimitMiddleware`` and the ``bytes`` copy it makes
#: itself (``bytes(received_body)``), both live at the parse's peak.
TRANSPORT_TERM: Final = 2.0

#: How far a measured ratio may sit from the model before the model is wrong
#: rather than the machine noisy. Measured 2026-09-15 across two scales and five
#: shapes: every whole-request ratio landed within **0.12** of the expression
#: above, every isolated term within 0.06. Several times the observed spread,
#: and far inside the gap between any two families -- the nearest pair differ by
#: 3.00x, the widest by 33.00x.
#:
#: **0.5 against a 0.12 spread is declared slack**, not a measurement: it buys
#: room for a loaded machine at the cost of not noticing a term that moved by
#: less than half the wire bytes. Tightening it toward the observed spread is
#: gap 3's neighbourhood on
#: https://github.com/theurian/theurian/issues/697; what is here is chosen to
#: fail on a whole-multiple change and to survive contention.
TOLERANCE: Final = 0.5


def _raw_call(query: str) -> bytes:
    """A serialized ``tools/call`` carrying ``query``, raw UTF-8.

    Raw rather than ``ensure_ascii``-escaped because the body's *widest code
    point* is a variable under test, and escaping erases it: an escaped
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
    process already held -- the correction that turned a first measurement of
    5.11x into the 1.00x the model predicts.

    A ``gc.collect`` first, because a collection landing inside the window frees
    memory the peak has already counted and makes the reading depend on when the
    last test happened to allocate.
    """
    gc.collect()
    tracemalloc.start()
    tracemalloc.reset_peak()
    held = work()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del held
    return peak


def _kind_bytes(query: str) -> int:
    """How many bytes per code point PEP 393 gives the parsed ``str``.

    Its *widest* member decides it, which is the whole reason one astral
    character in a 26 MB body quadruples the parse.
    """
    widest = max(map(ord, query))
    return 4 if widest > 0xFFFF else 2 if widest > 0x7F else 1


def _render_moment(query: str, wire: int) -> int:
    """What is live when ``jsonschema`` builds a ``{instance!r}`` message.

    Four things at once: the transport's two buffers, the parsed string, the
    ``repr`` of it, and the message that repr is interpolated into. The last two
    are the third term, and both sit at the parsed string's PEP 393 width --
    which is why one printable astral character multiplies them by four.

    ``len(repr(query)) - 2`` rather than ``_rendered_width(query)``: the quantity
    wanted is what ``repr`` actually builds, and taking it from the charge under
    test would make the prediction agree with the implementation by
    construction. That the two *are* equal is the charge's own invariant, and it
    is asserted where it belongs rather than assumed here.
    """
    kind = _kind_bytes(query)
    return 2 * wire + len(query) * kind + 2 * (len(repr(query)) - 2) * kind


#: Family (ii), enumerated, with the ratio-worst member first and named.
#:
#: A shape reaches ``iter_errors`` when its charge is under
#: :data:`~theurian.mcp.validation.MAX_PARAMS_RENDERED_CHARS` and it then fails a
#: keyword that interpolates the instance -- here ``maxLength``, which every
#: query past 2,000 characters fails. Two ways in, and both are represented:
#:
#: * **charged cheaply**, so the body may be large. Printable text is charged one
#:   character per code point whatever its UTF-8 length, which is the clause the
#:   withdrawn premise missed entirely.
#: * **charged expensively but small**, which is where the worst *ratio* lives.
#:   ``DEL`` costs one wire byte and renders four characters, the highest ratio
#:   any code point reaches; add one printable astral and the repr and the
#:   message both go to four bytes a character. That is 2 x 4 x 4 = 32x the wire
#:   bytes in the third term alone, and it is the shape the record measures at
#:   114.1 MiB / 38.04x.
#:
#: ``printable_cjk`` is carried because its parse moment *exceeds* its render
#: moment -- the one member here whose peak the third term does not decide. A
#: family enumerated only by its expensive members would not show that the
#: ``max`` in the model is doing work.
JSONSCHEMA_ANSWERED: Final = {
    "ratio_worst_del_plus_astral": lambda n: DEL * (n - 4) + WIDE_ASTRAL,
    "printable_two_byte": lambda n: "д" * (n // 2),
    "printable_cjk": lambda n: "日" * (n // 3),
    "printable_cjk_plus_astral": lambda n: "日" * (n // 3 - 2) + WIDE_ASTRAL,
    "plain_ascii": lambda n: "a" * n,
}

#: The one member of family (ii) whose third term dominates everything else, and
#: the fixture any "the model holds" claim has to survive. Named so the arm
#: below can assert it is *in* the family rather than leaving that to the reader.
RATIO_WORST: Final = "ratio_worst_del_plus_astral"


# -- The terms, in isolation ---------------------------------------------------


@pytest.mark.parametrize("kind", sorted(PARSE_TERM))
def test_the_parse_term_is_the_multiple_of_the_wire_bytes_the_model_records(kind: str) -> None:
    """The model's second term, measured where the SDK actually parses.

    PEP 393 sizes a ``str`` by its widest member, so the same million wire bytes
    cost one megabyte of heap as ASCII, three with one 2-byte character anywhere
    in them, and five with one astral character -- a function of the body's
    *widest code point*, not of its length. That phrasing is correct **of this
    term** and was withdrawn as a claim about the whole request, where family
    (ii)'s third term is uncorrelated with either.

    **Scoped to a single large leaf**, which is the shape this cap is sized for
    and the shape these bodies are. A body of many small values instead pays
    CPython's per-object overhead -- ~46 bytes a value, taking 98,900 short
    strings to 4.87x where one leaf of the same size parses at 1.00x -- which
    these ratios do not include and which
    :data:`~theurian.mcp.validation.MAX_PARAMS_NODES` bounds absolutely rather
    than as a multiple of the body.

    One character decides each row, and that is deliberate: the bodies are
    identical but for their last two or four bytes, so a ratio that failed to
    move would mean the widening is not happening rather than that the body is
    different.

    Measured through ``pydantic_core.from_json`` on the raw bytes, which is the
    call ``streamable_http.py`` makes. Measuring ``json.loads`` instead is what
    produced the withdrawn figure that exceeded the whole request's own peak --
    a term of a model cannot exceed the total it is part of.
    """
    filler = "a" * BODY_BYTES
    widest = {"one_byte_kind": "", "two_byte_kind": WIDE_BMP, "four_byte_kind": WIDE_ASTRAL}[kind]
    raw = _raw_call(filler[: BODY_BYTES - len(widest.encode())] + widest)

    ratio = _peak_of(lambda: pydantic_core.from_json(raw)) / len(raw)

    assert abs(ratio - PARSE_TERM[kind]) <= TOLERANCE, (
        f"parsing a {len(raw)}-byte body whose widest code point puts the result in the "
        f"{kind.replace('_', ' ')} peaks at {ratio:.2f}x the wire bytes, not the "
        f"{PARSE_TERM[kind]:.2f}x MAX_REQUEST_BODY_BYTES's model records. That term is one "
        f"of the three the recorded rows compose from, so a term that moved makes every one "
        f"of those rows a reading with no model behind it"
    )


@pytest.mark.parametrize("widest", ["", WIDE_ASTRAL])
def test_a_charge_refused_request_holds_twice_the_wire_bytes_beyond_its_parse(
    widest: str, tmp_path: Path
) -> None:
    """Family (i)'s transport term, measured as the difference the model says it is.

    There is no seam to call for this one: the two buffers are
    ``RequestBodyLimitMiddleware``'s ``bytearray`` and the ``bytes`` copy it
    makes itself, both inside code this daemon does not own. So it is measured
    the way the model states it -- what a whole request costs *beyond* its parse
    -- which is also the arithmetic that has to hold for the four recorded rows
    to be ``2 + parse`` rather than four unrelated numbers.

    **The fixture is charge-refused because that is what family (i) *is*, not
    to keep a model alive.** The distinction is the whole reason this module was
    rewritten: an earlier version asserted the same guard while claiming to pin
    *the* memory model, which made a family-specific measurement read as a
    general one and hid the family where it fails. Here the guard is the
    family's definition, the arm's name says which family it holds, and family
    (ii) is pinned separately below.

    :data:`WIDE_BMP` is charged six characters per two wire bytes, so a body a
    tenth of the cap is refused by the charge exactly as an at-cap body is --
    which is what makes this a scaled measurement of the at-cap path rather than
    of a different one.

    Parametrized over the widest code point so the term is shown to be
    *independent* of it: the transport holds 2x whatever the body carries, and
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
        "this fixture is no longer charge-refused, so it is not a member of family (i) and "
        "this arm is measuring a path it does not describe -- the render term would be live "
        "and unaccounted for"
    )
    assert abs(transport - TRANSPORT_TERM) <= TOLERANCE, (
        f"a charge-refused request peaks at {whole / len(raw):.2f}x its {len(raw)} wire bytes "
        f"and its parse alone at {parse / len(raw):.2f}x, leaving {transport:.2f}x for the "
        f"transport's own buffers -- not the {TRANSPORT_TERM:.2f}x the model records. Either "
        f"a buffer was added or removed between the socket and the parse, or the terms no "
        f"longer compose, in which case the recorded rows are readings rather than a model"
    )


# -- Family (ii): the path the two-term model does not predict -----------------


@pytest.mark.parametrize("shape", sorted(JSONSCHEMA_ANSWERED))
def test_a_jsonschema_answered_request_peaks_where_the_three_term_model_says(
    shape: str, tmp_path: Path
) -> None:
    """Family (ii), every member, against the one expression that covers all three.

    A body charged under the render budget is *admitted*, and then a keyword
    that interpolates ``{instance!r}`` renders it: a second string at the parsed
    string's PEP 393 width, and a third holding the message. That term exists on
    no family-(i) path, is uncorrelated with the size of the request that
    triggers it, and is what takes a legal 3 MB request to 38.04x while the
    26 MB rows top out at 7.00x.

    Predicted as ``max(parse moment, render moment)`` -- not as a sum, because a
    peak is a maximum over time and the parse's transient buffers are gone
    before the render begins. ``printable_cjk`` is the member that proves the
    ``max`` is load-bearing: its parse moment is 5.00x and its render moment
    4.00x, so its peak is decided by the term the other members' peaks are not.

    **Three of these five members have no third-term teeth**, and that is
    declared rather than implied: where the ``max`` picks the parse moment, this
    arm would stay green with the render term removed. Only
    ``ratio_worst_del_plus_astral`` and ``plain_ascii`` fail without it --
    driven. The other three hold the *composition*, not the third term, and
    giving them teeth is gap 3 on
    https://github.com/theurian/theurian/issues/697.

    The render moment's inputs are the body's own code point count, its PEP 393
    kind and ``len(repr(...))`` -- none of them read from the charge this daemon
    applies, so the prediction cannot agree with the implementation by
    construction.

    Asserted on the tier too, and that assertion is the point of the arm: if a
    fixture stops reaching ``jsonschema`` it has left the family, and a model
    checked against the wrong family is what this module was rewritten for.
    """
    query = JSONSCHEMA_ANSWERED[shape](BODY_BYTES)
    raw = _raw_call(query)
    charged = _rendered_width(query)

    with open_client(build_server(ProjectRegistry(path=tmp_path / "p.json")), tmp_path) as (
        client,
        session,
    ):
        client.post("/mcp", content=_raw_call("warm"), headers=headers(session))
        whole = _peak_of(lambda: client.post("/mcp", content=raw, headers=headers(session)))
        answer = client.post("/mcp", content=raw, headers=headers(session))

    parse = _peak_of(lambda: pydantic_core.from_json(raw))
    text = payload(answer)["result"]["content"][0]["text"]
    predicted = max(2 * len(raw) + parse, _render_moment(query, len(raw))) / len(raw)

    assert charged <= MAX_PARAMS_RENDERED_CHARS, (
        f"{shape} is charged {charged} against a {MAX_PARAMS_RENDERED_CHARS} budget, so the "
        f"charge refuses it and it is a member of family (i), not this one. A family-(ii) "
        f"fixture that drifts into family (i) is exactly how the third term went unmeasured"
    )
    assert "characters of content" not in text, (
        f"{shape} met the charge refusal rather than jsonschema, so no instance was rendered "
        f"and the third term is absent from this measurement: {text}"
    )
    assert "maxLength" in text, (
        f"{shape} was not answered by the keyword this arm needs -- only a keyword that "
        f"interpolates the instance builds the string being priced: {text}"
    )
    assert abs(whole / len(raw) - predicted) <= TOLERANCE, (
        f"a {shape} request peaks at {whole / len(raw):.2f}x its {len(raw)} wire bytes; the "
        f"model predicts {predicted:.2f}x from max(parse moment "
        f"{(2 * len(raw) + parse) / len(raw):.2f}x, render moment "
        f"{_render_moment(query, len(raw)) / len(raw):.2f}x). Either jsonschema stopped "
        f"rendering the instance, or it renders more of it than the record says, or a buffer "
        f"moved -- and the recorded per-request ceiling moves with it"
    )


def test_the_ratio_worst_member_of_family_two_exceeds_every_charge_refused_row() -> None:
    """The claim the withdrawn two-term model made false, stated as arithmetic.

    Family (i)'s four recorded rows top out at **7.00x**, and the earlier version
    of this module pinned that composition while excluding the family that
    breaks it. This arm is the one that would have caught it: the ratio-worst
    member of family (ii) is predicted -- from its own code points, kind and
    repr, with no measurement in the loop -- to exceed *every* family-(i) ratio
    several times over.

    Prediction only, deliberately. It runs no request and needs no daemon, so it
    cannot be made to pass by a fixture that quietly stops reaching
    ``jsonschema``; it fails if the arithmetic of the third term stops being
    what the record says. The measured counterpart is the parametrized arm
    above.
    """
    query = JSONSCHEMA_ANSWERED[RATIO_WORST](BODY_BYTES)
    wire = len(_raw_call(query))

    predicted = _render_moment(query, wire) / wire
    worst_charge_refused = max(PARSE_TERM.values()) + TRANSPORT_TERM

    assert RATIO_WORST in JSONSCHEMA_ANSWERED, RATIO_WORST
    assert predicted > 5 * worst_charge_refused, (
        f"the ratio-worst family-(ii) shape is predicted at {predicted:.2f}x the wire bytes "
        f"and the worst charge-refused composition at {worst_charge_refused:.2f}x, a factor "
        f"of {predicted / worst_charge_refused:.1f}. The record states that gap -- 114.1 MiB "
        f"at 3 MB against 175.1 MiB at 26 MB -- and a model that no longer produces it is "
        f"back to describing the memory cost as a function of the body's widest code point "
        f"alone, which is the claim family (ii) is the counterexample to"
    )


# -- The other recorded ceiling: the charge's own chunked transient -------------


def test_the_chunked_transient_stays_under_its_ceiling_at_the_worst_leaf() -> None:
    """``_CHUNK_CODE_POINTS * (10 + 1) * 4``, driven where both its terms are live.

    The bound has two terms because the line builds two strings per slice: the
    concatenation ``'"' + value[...]`` and the ``repr`` of it. Both can sit in
    PEP 393's 4-byte kind at once, so the ceiling is eleven characters per code
    point at four bytes each -- not the ``* 10 * 4`` an earlier record priced,
    which counted the repr output and omitted the concatenation.

    **The worst leaf needs two different characters, and that is why the old
    corroborations could not find the ceiling.** Non-printable *astral* filler
    makes ``repr`` emit ten characters per code point; one *printable* astral
    forces both strings into the widest kind. The figures that used to
    corroborate the bound -- 0.04 and 0.15 MiB -- were taken over dense U+007F,
    which renders four characters per code point rather than ten and lands at
    **0.11x of this ceiling**. A fixture nine times under the bound cannot say
    whether the bound is right.

    Recomputed from the live constant, so raising ``_CHUNK_CODE_POINTS`` raises
    what this admits by exactly the factor the record says it should. The margin
    is the two ``str`` object headers the payload arithmetic does not count.
    """
    leaf = NARROW_ASTRAL * (_CHUNK_CODE_POINTS * 40 - 1) + WIDE_ASTRAL
    ceiling = _CHUNK_CODE_POINTS * (10 + 1) * 4

    peak = _peak_of(lambda: _chunked_width(leaf, MAX_PARAMS_RENDERED_CHARS))

    assert peak <= ceiling + 4096, (
        f"the chunked transient peaks at {peak} bytes over a leaf of non-printable astral "
        f"characters carrying one printable astral, past the {ceiling}-byte ceiling "
        f"`_CHUNK_CODE_POINTS * (10 + 1) * 4` records ({peak / ceiling:.2f}x). That leaf is "
        f"the worst the fallback can be handed -- ten rendered characters per code point, "
        f"both strings in the 4-byte kind -- so a ceiling it exceeds is not a ceiling"
    )
    assert peak > ceiling * 0.9, (
        f"the chunked transient peaks at {peak} bytes, only {peak / ceiling:.2f}x the "
        f"{ceiling}-byte ceiling, over what is supposed to be the worst leaf the fallback "
        f"can be handed. A fixture well under the bound cannot tell a correct ceiling from "
        f"one that is too generous -- which is how `* 10 * 4` survived on a dense-U+007F "
        f"leaf measuring 0.11x of it"
    )
