"""The figures the SEC-12 records print, held to the live constants (#669).

``test_sec12_shipped_claims.py`` holds that SEC-12 *ships* -- that three records
name the shipped mechanism and that the mechanism is in the build. This file
holds the numbers those records quote, which is a different failure with a
different shape: a record can name the right seat and still print a figure the
code no longer produces, and nothing about the seat says so.

**Why this is its own module.** #669's correction touched five surfaces and was
corrected twice -- once when the transport cap landed, once when round two found
three figures that were measurements over a favourable instance written down as
bounds. The second correction was unpinned, and ADR-0031's own addendum says so:
*"What is not yet enforced is that they stay that way ... Until that lands, a
revert of these corrections is silent."* This module is that wiring.

**Every arm here recomputes**, which is what separates this module from its
other sibling. A figure is read out of a record and compared against the same
figure derived from the live constants, from the code-point space, or from the
class table -- the composed render ceiling, the worst render the pre-#669 charge
admitted, the residual's class count and factors, the charge being positive for
every leaf type. None of them is transcribed, so moving a constant moves what
the records are required to say, in the same run.

What a record must no longer *claim* has no live constant to disagree with, and
is held next door: ``test_sec12_retired_claims.py`` refuses the retired
unreachability sentence and refuses a retired figure standing without the
instance it was measured over.

**Stated reach, because a pin's silence is read as coverage.** These arms pin
the threat model's **T-11 residual paragraph**, **ADR-0031's Amendment 1** and
the **CHANGELOG's #669 entries** -- the three records that print #669's figures
-- together with the constants and the code-point space those figures are
recomputed from. They do **not** pin:

* **the ``ru_maxrss`` column** of the per-request memory table, nor the four
  at-cap rows beside it. Those are measurements of a process high-water mark
  with no live constant behind them: nothing in the build computes 175.1 MiB,
  so nothing here can recompute it, and a pin would be a second transcription
  of the same reading. What *is* pinned about that table is its **model** --
  ``tests/integration/test_request_memory_model.py`` holds the three terms and
  the three path families it composes from, at a scaled body. (Two was this
  sentence's first count, written when the model had two terms and its pin
  excluded the family the third one appears on.)
* **Amendment 1's narrative** -- its decision text, its behaviour-change
  list, its measurement-block anchors. Its *figures* are held, and all of them:
  the composed ceiling, the shipped cap's pre-fix pair, and the three
  comparisons drawn around it (the SDK default's own worst, that worst as a
  ratio, and the astral member's). An earlier version of this sentence said
  "figures, not narrative" while four of those figures were read by nothing --
  a reach claim wider than the arms under it, which is this module's own
  subject stated about itself.
* **the roadmap's SEC-12 cell**, which carries no #669 figure at all since the
  reconciliation left its *owed* list; ``test_sec12_shipped_claims.py`` holds
  what that row is still held to.
* **T-6**, which the round-two brief named and which measurably carries none of
  these figures: ``roughly 3.0x``, ``53,476,811``, ``4.25x`` and the retired
  universal appear in it zero times. Including it would look like a record held
  and be an empty read -- the reason ``test_sec12_shipped_claims.py``'s
  ``_REASSERTION_RECORDS`` gives for leaving ``schemas/README.md`` out of its
  own sweep, one file over.

**Integration, not unit**, for the reason its sibling gives: a record pin sits
beside the fact it rests on, and one of those facts is a built server. No
database, no socket, nothing written anywhere.
"""

from __future__ import annotations

import random
import re
from typing import Final

import pytest
from escape_class_sweep import sweep
from mcp.server.transport_security import DEFAULT_MAX_REQUEST_BODY_SIZE
from sec12_records import amendment_one, t11_residual_paragraph
from threat_model_claims import SPELLED_NUMBERS, prose
from wire_escape_classes import RESIDUAL_CLASSES, WIRE_CLASSES
from write_lock_claims import REPO_ROOT

from theurian.daemon.server import MAX_REQUEST_BODY_BYTES
from theurian.mcp import validation
from theurian.mcp.validation import (
    MAX_PARAMS_NODES,
    MAX_PARAMS_RENDERED_CHARS,
    _rendered_width,
)
from theurian.security.paths import MAX_SOURCE_FILE_BYTES

pytestmark = pytest.mark.integration

#: How the residual paragraph's count of over-multiplier classes is located. The
#: paragraph states two counts -- how many residual *risks* T-11 carries, and how
#: many wire-escape *classes* exceed the multiplier -- and only the second is the
#: one :data:`RESIDUAL_CLASSES` is the fact side of. Keyed on the noun, so the
#: two cannot be confused.
_RESIDUAL_COUNT_KEY: Final = re.compile(r"exactly (\w+) wire-escape classes")

#: The addend the derivation records, as the paragraph spells it. One MiB, named
#: here so the multiplier can be recovered from the live cap by arithmetic.
_ENVELOPE_HEADROOM: Final = 1024 * 1024

#: How the residual paragraph's remedy-less class is located. The ``because`` is
#: load-bearing and not decoration: the paragraph says "no remedy" twice -- once
#: of the class, once of the bare ``413``'s own shape ("naming no tool, carrying
#: no remedy, having no refusal shape") -- so a key on the bare phrase stays
#: green with the class clause deleted entirely. Driven: deleting that clause
#: leaves the bare key matching the 413 sentence and reports nothing.
_NO_REMEDY_KEY: Final = "no remedy because"

#: How the residual paragraph exempts the one class a reader most expects to be
#: residual. Keyed on the negation itself, because the failure this guards
#: against is a silent inversion: *"are in that set"* is one word away from
#: *"are not in that set"* and reads as fluently.
_ASTRAL_EXEMPTION: Final = "astral characters are not in that set"


#: What a record must print where it claims the composed render ceiling, and
#: which records must print it. Set equality rather than "at least one", so a
#: record that drops the figure fails here rather than going quiet -- and a
#: record that gains one joins the population by being listed.
_COMPOSED_CEILING_RECORDS: Final = frozenset(
    {
        "docs/adr/0031-mcp-input-is-schema-validated-in-middleware.md",
        "docs/security/threat-model.md",
        "packages/theurian-core/CHANGELOG.md",
        "packages/theurian-core/src/theurian/daemon/server.py",
        "packages/theurian-core/src/theurian/mcp/validation.py",
    }
)

#: The records that print the worst render the pre-#669 charge admitted. Fewer,
#: because the figure belongs to the decision rather than to the constants: the
#: two source docstrings describe the charge that replaced it and do not price
#: what it used to allow.
_PRE_FIX_REACH_RECORDS: Final = frozenset(
    {
        "docs/adr/0031-mcp-input-is-schema-validated-in-middleware.md",
        "packages/theurian-core/CHANGELOG.md",
    }
)

#: The records that print the composed ceiling as a *ratio* over the constant
#: alone. Three of the five, and the other two are not an oversight: the
#: CHANGELOG entry and ``daemon/server.py`` quote the total and defer the
#: composition -- and with it the ratio -- to ``MAX_PARAMS_RENDERED_CHARS``,
#: which is where it is derived. Equality rather than a subset, so a record
#: gaining the ratio joins this list by decision instead of by accident.
_COMPOSED_RATIO_RECORDS: Final = frozenset(
    {
        "docs/adr/0031-mcp-input-is-schema-validated-in-middleware.md",
        "docs/security/threat-model.md",
        "packages/theurian-core/src/theurian/mcp/validation.py",
    }
)

#: One non-empty value per leaf type a parsed JSON request can put in front of
#: the charge, plus the two only a future parser would (``bytes``, and whatever
#: an unknown type falls through to). The record's universal is "every leaf", so
#: the fact side has to be a population and not a sample -- and ``float`` and
#: ``None``, the two that broke it, are the two an enumeration written from the
#: JSON grammar is most likely to forget.
#:
#: Non-empty on purpose. ``""`` is charged zero and *should* be: the invariant
#: is that a leaf is charged at least what it contributes to the render, and an
#: empty string contributes nothing. It is asserted separately below rather than
#: dropped silently, because "every leaf is charged something" and "every leaf
#: is charged at least its contribution" differ exactly there, and the second is
#: the one the records state.
#: How many characters of punctuation an instance spends per node, beyond what
#: its leaves are charged. The records state it as "no more than 4", and
#: :func:`test_the_per_node_punctuation_cost_the_composed_ceiling_uses_is_measured_here`
#: is what keeps it from being a figure copied out of a record into a test.
_PER_NODE_PUNCTUATION: Final = 4

_LEAF_VALUES: Final = (
    "text",
    0,
    -1,
    12345,
    True,
    False,
    1.5,
    float("1e308"),
    None,
    b"bytes",
)


def _random_instances(count: int) -> list[dict[str, object]]:
    """``count`` argument-shaped instances from a fixed seed.

    Seeded rather than sampled fresh, for the reason the house rule gives about
    dict order and wall-clock: an arm whose population changes per run reports a
    different bound on the day it fails, and the bound is the thing under test.
    """
    source = random.Random(7)  # noqa: S311 -- shapes for a bound, not keys for a secret
    shapes: list[dict[str, object]] = []
    for _ in range(count):
        shape: dict[str, object] = {}
        for _ in range(source.randint(1, 6)):
            key = "".join(source.choice("abcde") for _ in range(source.randint(1, 4)))
            roll = source.random()
            if roll < 0.3:
                shape[key] = [source.randint(0, 9) for _ in range(source.randint(0, 4))]
            elif roll < 0.6:
                shape[key] = "".join(source.choice("xyz") for _ in range(source.randint(0, 5)))
            elif roll < 0.8:
                shape[key] = {"z": source.randint(0, 999)}
            else:
                shape[key] = source.choice([None, True, 1.5, 0])
        shapes.append(shape)
    return shapes


def _composed_render_ceiling() -> int:
    """The whole render a request can reach, from the two live constants.

    ``MAX_PARAMS_RENDERED_CHARS`` bounds the charged leaves; the punctuation
    between nodes is a fixed per-node cost the records measure at no more than
    four characters. The sum is the figure every record quotes, and it is
    recomputed here so that moving either constant moves what the records are
    required to say.
    """
    return MAX_PARAMS_RENDERED_CHARS + MAX_PARAMS_NODES * _PER_NODE_PUNCTUATION


def _worst_pre_fix_render() -> tuple[int, int, int]:
    """``(render, raw wire bytes, width)`` for the worst body the old charge admitted.

    The pre-#669 charge counted a string's code points, so a caller could send
    ``MAX_PARAMS_RENDERED_CHARS`` of them and be charged exactly the budget --
    unless the transport ran out first, which it does for a character costing
    more than one wire byte. The worst render is therefore a maximisation of
    ``min(budget, cap // wire) * width`` over the ``(wire, width)`` pairs a
    legal-raw code point can present, and those pairs come from the sweep rather
    than from the three characters the records name.

    Both caps are live, which is the property the records' figure rests on:
    ADR-0031 prints this pair under the shipped cap's name precisely because an
    earlier draft printed it under the interim one's.
    """
    return max(
        (min(MAX_PARAMS_RENDERED_CHARS, MAX_REQUEST_BODY_BYTES // wire) * width, wire, width)
        for wire, width in sweep().reach_pairs
    )


def test_the_t11_residual_names_as_many_classes_as_the_unit_module_pins() -> None:
    """RED means the record's residual and the measured residual disagree.

    The absence arm above refuses the retired sentence; this is the positive
    half, and it is the one that fires when the *measurement* moves rather than
    when the prose does. T-11 now states the residual as a count and two
    factors -- exactly *n* wire-escape classes exceed the multiplier, all at one
    ratio, one of them with a raw-UTF-8 remedy and one without -- and every one
    of those figures is the fact side of
    ``tests/unit/test_transport_body_cap.py``'s class table, which holds the
    residual set *equal* to the measured over-multiplier set.

    So the count and the factors are read off that table rather than typed here.
    Add a ninth wire class that expands past the multiplier and the unit module
    goes RED on its own set equality while this arm goes RED on the record that
    still says two; change a residual class's raw factor and the remedy figure
    the record prints stops being the measured one.

    **What this does not hold is which class the record attaches each factor
    to.** The record names them in English -- "C0 characters other than
    ``\\b`` ``\\t`` ``\\n`` ``\\f`` ``\\r``", "DEL (U+007F)" -- and the module
    keys them by name, so no mechanical comparison joins the two without
    transcribing one into the other, which is the failure this whole module
    exists to avoid. The count, the shared over-multiplier ratio, the remedy
    ratio and the existence of a remedy-less class are what *are* mechanical,
    and they are what is asserted.
    """
    paragraph = prose(t11_residual_paragraph())
    factors = {WIRE_CLASSES[name].escaped for name in RESIDUAL_CLASSES}
    remedied = {
        name for name in RESIDUAL_CLASSES if WIRE_CLASSES[name].raw < WIRE_CLASSES[name].escaped
    }

    stated = _RESIDUAL_COUNT_KEY.search(paragraph)
    assert stated is not None, (
        f"T-11's residual paragraph no longer states how many wire-escape classes "
        f"exceed the multiplier, so nothing here can disagree with the "
        f"{len(RESIDUAL_CLASSES)} `tests/unit/test_transport_body_cap.py` measures. "
        f"The sentence this arm reads is the one carrying `wire-escape "
        f"classes`:\n\n{paragraph[:600]}"
    )
    assert SPELLED_NUMBERS.get(stated.group(1)) == len(RESIDUAL_CLASSES), (
        f"T-11's residual says `{stated.group(0)}` exceed the multiplier; "
        f"`tests/unit/test_transport_body_cap.py` measures "
        f"{len(RESIDUAL_CLASSES)} ({sorted(RESIDUAL_CLASSES)}). A class that joined "
        f"can meet the bare 413 at a landed size the store would accept and the record "
        f"does not say so; a class that left is a residual the record still warns about"
    )

    assert len(factors) == 1, (
        f"the residual classes no longer share one over-multiplier ratio "
        f"({sorted(factors)}), so `both at N.0x` is not a sentence the record can "
        f"carry and this arm cannot check the one it does"
    )
    shared = next(iter(factors))
    assert f"both at {shared}.0x" in paragraph, (
        f"T-11's residual does not say `both at {shared}.0x` -- the ratio the residual "
        f"classes measure at -- so the reach of the gap it records is not the reach that "
        f"was measured. Anchored to the clause rather than to the bare substring: the "
        f"paragraph prints `3.0x` for the astral exemption two sentences later, so a bare "
        f"`{shared}.0x` search would pass on a paragraph that had stopped stating the "
        f"residual's own ratio:\n\n{paragraph[:600]}"
    )

    for factor in sorted({WIRE_CLASSES[name].raw for name in remedied}):
        assert f"{factor}.0x" in paragraph, (
            f"T-11's residual does not print the {factor}.0x a caller pays by sending "
            f"{sorted(remedied)} raw instead of ensure_ascii-escaped. A remedy stated "
            f"without its cost sends someone to retry a request whose new size they "
            f"cannot predict"
        )
    if RESIDUAL_CLASSES - remedied:
        assert _NO_REMEDY_KEY in paragraph, (
            f"T-11's residual no longer says that {sorted(RESIDUAL_CLASSES - remedied)} "
            f"has no remedy, and why. Its raw form costs exactly what its escaped form "
            f"does, because raw C0 is illegal JSON -- a record that drops the clause "
            f"leaves a reader to assume the remedy it states for the other class "
            f"applies to both"
        )


def test_the_figures_the_t11_residual_prints_are_the_live_constants() -> None:
    """The fact leg under the two prose arms: the record's numbers are the build's.

    Every arm above holds a *record*. This one holds the thing recorded -- and
    it is what makes the pair something other than a paragraph agreeing with
    itself. T-11's residual prints four figures about the two caps: the
    transport cap's byte total, the multiplier its derivation uses, the addend,
    and the render budget in MiB. Each is extracted from the paragraph and
    compared against the live constant, so a constant that moves without its
    record goes RED here with the prose arms green, which is the direction that
    says *the product moved*.

    Extracted, not transcribed. A pin that asserted ``"26,214,400" in
    paragraph`` would be a second copy of the number, correct on the day it is
    typed and silent afterwards -- this module's own subject matter one level
    up.

    The derivation itself is pinned elsewhere and not re-implemented here:
    ``test_input_validation_dispatch.py::test_the_transport_body_cap_is_derived_from_the_cap_on_a_landed_file``
    holds the formula, ``::test_the_transport_body_cap_sits_above_the_rendered_character_bound``
    holds the ordering, and ``tests/unit/test_transport_body_cap.py`` holds the
    measurement the multiplier *is*. What is asserted here is only the premise
    those figures need to be comparable at all -- that the cap really is a whole
    multiple of the landed-file cap plus the recorded addend -- and then the
    comparison.
    """
    paragraph = prose(t11_residual_paragraph())

    assert (MAX_REQUEST_BODY_BYTES - _ENVELOPE_HEADROOM) % MAX_SOURCE_FILE_BYTES == 0, (
        f"MAX_REQUEST_BODY_BYTES ({MAX_REQUEST_BODY_BYTES}) less the "
        f"{_ENVELOPE_HEADROOM}-byte addend is not a whole multiple of "
        f"MAX_SOURCE_FILE_BYTES ({MAX_SOURCE_FILE_BYTES}), so "
        f"`derived as N * MAX_SOURCE_FILE_BYTES + 1 MiB` is not a description this record "
        f"can carry and the multiplier below cannot be recovered from the cap. The "
        f"multiplier's own value is not asserted here -- that is "
        f"test_input_validation_dispatch.py's derivation pin, and repeating the literal "
        f"here would make this arm a transcription of the formula rather than the premise "
        f"under the comparison"
    )
    assert MAX_REQUEST_BODY_BYTES > MAX_PARAMS_RENDERED_CHARS, (
        f"the transport cap ({MAX_REQUEST_BODY_BYTES}) no longer sits above the render "
        f"budget ({MAX_PARAMS_RENDERED_CHARS}), so T-11's residual describes the two "
        f"tiers in an order this build does not have"
    )

    assert MAX_PARAMS_RENDERED_CHARS % _ENVELOPE_HEADROOM == 0, (
        f"MAX_PARAMS_RENDERED_CHARS ({MAX_PARAMS_RENDERED_CHARS}) is no longer a whole "
        f"number of MiB, so the MiB spelling the record uses would round and this arm "
        f"would compare the record against a figure it never meant"
    )

    multiplier = (MAX_REQUEST_BODY_BYTES - _ENVELOPE_HEADROOM) // MAX_SOURCE_FILE_BYTES
    printed_totals = re.findall(r"(\d{1,3}(?:,\d{3})+) bytes", paragraph)
    printed_multipliers = re.findall(r"(\d+) max_source_file_bytes", paragraph)

    assert printed_totals == [f"{MAX_REQUEST_BODY_BYTES:,}"], (
        f"T-11's residual prints {printed_totals} as the transport cap; the build "
        f"carries {MAX_REQUEST_BODY_BYTES:,}. A record quoting a byte figure this "
        f"daemon does not enforce tells a reader which bodies arrive, and is wrong "
        f"about it:\n\n{paragraph[:600]}"
    )
    assert printed_multipliers == [str(multiplier)], (
        f"T-11's residual derives the cap with {printed_multipliers}; recovered from "
        f"the live constants the multiplier is {multiplier}. That figure is the worst "
        f"wire expansion the cap covers, so it also decides which encodings the "
        f"residual below it names"
    )
    assert f"{MAX_PARAMS_RENDERED_CHARS // (1024 * 1024)} mib render budget" in paragraph, (
        f"T-11's residual does not call it a "
        f"{MAX_PARAMS_RENDERED_CHARS // (1024 * 1024)} MiB render budget, which is what "
        f"MAX_PARAMS_RENDERED_CHARS ({MAX_PARAMS_RENDERED_CHARS}) is. Either the "
        f"constant moved without the record, or the record stopped naming the figure "
        f"whose reachability the sentence is about:\n\n{paragraph[:600]}"
    )


def test_the_residual_says_astral_is_covered_exactly_when_the_measurement_does() -> None:
    """RED means the record's one named exemption stopped matching the table.

    T-11's residual exempts one class by name: *"Astral characters are **not**
    in that set; they expand at 3.0x and the cap covers them."* That sentence
    exists because astral text is the case a reader expects to be expensive --
    four bytes, a twelve-byte surrogate pair -- and is not, and a reader who
    assumed otherwise would size a client around a residual that does not apply
    to them.

    A sentence like that inverts silently: *"are in that set"* is one word away
    and reads as fluently. So it is asserted in both directions off the
    measurement -- present while astral is covered, absent if astral ever became
    residual -- and beside the factor the same sentence prints, recomputed from
    the row rather than transcribed.

    Grammar-bound, and that is the cost of pinning a claim a table cannot make:
    the exemption is prose. A reword fails here and the fix is to move the key
    with the sentence, which is a judgement, not a retune.
    """
    paragraph = prose(t11_residual_paragraph())
    covered = "astral" not in RESIDUAL_CLASSES

    if covered:
        assert _ASTRAL_EXEMPTION in paragraph, (
            f"the astral class is not in the measured residual "
            f"({sorted(RESIDUAL_CLASSES)}), and T-11's residual no longer says so. A reader "
            f"who takes the residual list as complete concludes four-byte text is expensive "
            f"on this wire; it is 3.0x, the same as the two-byte class the cap is sized "
            f"for:\n\n{paragraph[:600]}"
        )
        assert f"{WIRE_CLASSES['astral'].escaped}.0x" in paragraph, (
            f"T-11's residual does not print the "
            f"{WIRE_CLASSES['astral'].escaped}.0x the astral class measures at, so its "
            f"exemption is an assertion with no figure behind it"
        )
    else:
        assert _ASTRAL_EXEMPTION not in paragraph, (
            f"the astral class is now in the measured residual "
            f"({sorted(RESIDUAL_CLASSES)}), and T-11's residual still exempts it. That is a "
            f"record telling a caller an encoding is covered when the measurement says it "
            f"can meet the bare 413"
        )


# -- The figures the records print, recomputed ---------------------------------


def test_every_record_that_claims_the_composed_ceiling_prints_the_live_one() -> None:
    """RED means a constant moved and the records kept quoting the old total.

    ``MAX_PARAMS_RENDERED_CHARS`` alone under-states the render a request can
    reach: an instance is its leaves *plus* the punctuation between them, and a
    container is charged zero because the walk descends into it. Five records
    print the composed figure for that reason, and each prints it as a literal.

    The searched string is computed from the live constants, so this is not the
    tautology it would be if the literal were typed here: move
    ``MAX_PARAMS_NODES`` and the figure this looks for changes, and every record
    still carrying the old one fails at once, naming itself.

    Set equality over which files carry it. "At least one record prints it"
    passes for a tree where four of the five have quietly dropped the figure --
    which is the direction a partial revert goes.
    """
    composed = _composed_render_ceiling()

    carrying = {
        path
        for path in _COMPOSED_CEILING_RECORDS
        if f"{composed:,}" in (REPO_ROOT / path).read_text(encoding="utf-8")
    }

    assert carrying == _COMPOSED_CEILING_RECORDS, (
        f"the composed render ceiling is {composed:,} characters "
        f"(MAX_PARAMS_RENDERED_CHARS {MAX_PARAMS_RENDERED_CHARS:,} + MAX_PARAMS_NODES "
        f"{MAX_PARAMS_NODES:,} x 4), and these records do not print it: "
        f"{sorted(_COMPOSED_CEILING_RECORDS - carrying)}. Either a constant moved and the "
        f"records were not re-derived -- in which case they are telling a reader a ceiling "
        f"this build does not have -- or a record dropped the composed figure and now quotes "
        f"only the constant, which under-states the real one by "
        f"{composed - MAX_PARAMS_RENDERED_CHARS:,} characters"
    )


def test_the_composed_ceiling_is_the_ratio_over_the_constant_the_records_print() -> None:
    """The second half of the same figure: how much the constant under-states by.

    The records quote *"1.032x the constant alone"* beside the total, and that
    ratio is what tells a reader whether the difference matters -- a ceiling
    1.001x the constant would not be worth a sentence, and one at 2x would
    change how a client sizes a request. Recomputed to three decimals, the way
    every record that carries it spells it.

    Three of the five records carry it, and equality is what says which: the
    CHANGELOG entry and ``daemon/server.py`` quote the total and defer the
    composition to ``MAX_PARAMS_RENDERED_CHARS``. A subset assertion would let
    all three drop the ratio and stay green.
    """
    ratio = f"{_composed_render_ceiling() / MAX_PARAMS_RENDERED_CHARS:.3f}x"

    carrying = {
        path
        for path in _COMPOSED_CEILING_RECORDS
        if ratio in (REPO_ROOT / path).read_text(encoding="utf-8")
    }

    assert carrying == _COMPOSED_RATIO_RECORDS, (
        f"the composed ceiling is {ratio} the constant alone. Records that state the "
        f"composition and no longer print that ratio: "
        f"{sorted(_COMPOSED_RATIO_RECORDS - carrying)}. Records that gained it without "
        f"being listed here: {sorted(carrying - _COMPOSED_RATIO_RECORDS)}. A ratio that "
        f"stopped matching means a constant moved and the records were not re-derived"
    )


def test_the_charge_is_positive_for_every_leaf_type_the_records_call_every_leaf() -> None:
    """The fact side of the records' universal, asserted as a population.

    Three records now say the charge covers *"every leaf at least the number of
    characters that leaf contributes to the render"*. The claim before it said
    "every leaf what ``repr`` renders it as" and was false: ``float`` and
    ``None`` fell through to a ``return 0``, so a request pairing a string at
    the budget with 99,995 full-precision floats was charged 12,582,906 and
    admitted while rendering 15,182,796 characters.

    A charge of zero is what made that possible, so zero is what is refused --
    for every leaf type a parsed JSON request can hold, and for the two that
    only a future parser would. Containers are excluded and must be, because the
    walk descends into them and charging one here would double-count its
    members; the record says so, and the exclusion is asserted rather than
    assumed.

    **Two zeroes are correct, and both are asserted as such rather than left
    out of the population.** A container's is the deliberate one above. The
    other is the empty string, which contributes nothing to a render because
    there is nothing to render -- so the invariant the records state is *at
    least what it contributes*, not *something*, and the difference between
    those two readings is exactly this value. An arm that quietly dropped ``""``
    from its fixture would be asserting the weaker claim while reading like the
    stronger one.
    """
    # Keyed by type-and-value, not by value: `False == 0` and `True == 1`, so a
    # plain dict comprehension silently folds the two booleans into the two
    # integers and this population of ten reports as eight -- with `bool`, one of
    # the arms the records single out, never checked.
    charged = {
        f"{type(value).__name__}:{value!r}": _rendered_width(value) for value in _LEAF_VALUES
    }
    uncharged = {label: width for label, width in charged.items() if width <= 0}

    assert len(charged) == len(_LEAF_VALUES), (
        f"the population of {len(_LEAF_VALUES)} leaves collapsed to {len(charged)} distinct "
        f"labels, so at least one value is not being charged at all: {sorted(charged)}"
    )

    assert _rendered_width("") == 0, (
        f"the empty string is charged {_rendered_width('')} rather than 0. It contributes "
        f"nothing to a render, so anything else is an over-charge -- and the records' "
        f"invariant is 'at least what the leaf contributes', which for this leaf is nothing"
    )

    assert uncharged == {}, (
        f"these leaves are charged nothing against MAX_PARAMS_RENDERED_CHARS: {uncharged}. "
        f"A leaf charged zero renders for free, so a request can carry MAX_PARAMS_NODES of "
        f"them past a budget that never sees them -- which is exactly how 15,182,796 "
        f"characters were admitted under a 12,582,912 budget. Every record that says 'every "
        f"leaf' is false the moment one of these returns 0"
    )

    for container in ({"a": 1}, [1], (1,), {1}, frozenset({1})):
        assert _rendered_width(container) == 0, (
            f"{container!r} is charged {_rendered_width(container)} rather than 0. A "
            f"container is the one deliberate zero -- `_iter_nodes` descends into it and "
            f"charges every member -- so charging it here counts its members twice and "
            f"refuses requests the budget was sized to admit"
        )


def test_the_worst_render_the_old_charge_admitted_is_what_the_records_print() -> None:
    """RED means either cap moved and the shipped-cap pair stayed where it was.

    ADR-0031 and the CHANGELOG both price what the pre-#669 charge would admit
    *at the cap this release ships* -- 75,497,472 characters, 6.00x the budget
    -- and the pair is load-bearing twice over: it is the severity of the defect
    that was fixed, and it is the figure an earlier draft got wrong by printing
    the interim cap's measurement under the shipped cap's name.

    Recomputed from both live constants and from the code-point space, so it
    moves when either cap moves. The maximisation runs over the ``(raw wire
    bytes, render width)`` pairs the sweep found rather than over the three
    characters the records name, which is what makes "the worst over every
    escape class" a checked claim instead of a comparison of three.
    """
    render, wire, width = _worst_pre_fix_render()
    ratio = f"{render / MAX_PARAMS_RENDERED_CHARS:.2f}x"

    missing = {
        path
        for path in _PRE_FIX_REACH_RECORDS
        if f"{render:,}" not in (REPO_ROOT / path).read_text(encoding="utf-8")
        or ratio not in (REPO_ROOT / path).read_text(encoding="utf-8")
    }

    assert missing == set(), (
        f"the worst render the pre-#669 charge admitted at the shipped caps is {render:,} "
        f"characters, {ratio} the budget -- reached by a character costing {wire} raw wire "
        f"byte(s) and rendering {width} characters, at "
        f"{min(MAX_PARAMS_RENDERED_CHARS, MAX_REQUEST_BODY_BYTES // wire):,} of them. These "
        f"records do not print that pair: {sorted(missing)}. Moving either cap moves this "
        f"figure, and a record still printing the old one is describing a breach at a size "
        f"this build does not have"
    )


# -- The retired figures' own corpus and slicing -------------------------------


def test_the_per_node_punctuation_cost_the_composed_ceiling_uses_is_measured_here() -> None:
    """The ``4`` in the composed ceiling, held structurally rather than transcribed.

    :func:`_composed_render_ceiling` multiplies :data:`MAX_PARAMS_NODES` by a
    literal ``4``, and every arm above compares records against the product. So
    the product is only as good as that factor, and the factor was a figure
    copied out of a record into a test -- the shape this module exists to
    refuse, one level down.

    What it measures is the *excess*: what ``repr`` of an instance costs beyond
    the sum of the charges its leaves are given. That excess is the punctuation
    no leaf is charged for -- braces, brackets, ``, ``, ``: ``, and the quotes
    around each string -- and it is a fixed cost per node, so ``excess <= 4 *
    nodes`` is the claim.

    **The bound is provable and the measurement says where it is tight.** A
    container contributes its two delimiters plus a separator per member beyond
    the first, and a string two quotes, giving ``E <= 4N - 4`` for any instance
    with at least one container and one member; the empty container is the one
    case outside it (``E = 2``, ``N = 1``) and is carried below so the arm is
    honest about its own edge. The families that converge on 4 from below reach
    **3.98** at 200 members, and a seeded random search over 30,000 shapes peaks
    at **3.69** -- so ``4`` is correct and ``3`` would be wrong, which is the
    pair of facts a transcribed constant cannot tell anyone.

    The random half is seeded, not sampled fresh: an arm whose population
    changes per run reports a different bound on the day it fails.
    """
    families: dict[str, object] = {
        "dict_of_string_keys": {f"k{index}": f"v{index}" for index in range(50)},
        "list_of_strings": [f"s{index}" for index in range(200)],
        "flat_integers": list(range(500)),
        "nested_mixed": {"a": [1, 2, {"b": "c"}], "d": None, "e": 1.5},
        "empty_container": {},
    }
    families.update(
        {f"random_{index}": shape for index, shape in enumerate(_random_instances(30_000))}
    )

    ratios = {}
    for name, instance in families.items():
        nodes = list(validation._iter_nodes(instance))
        excess = len(repr(instance)) - sum(validation._rendered_width(v) for v, _ in nodes)
        ratios[name] = excess / (1 + len(nodes))

    over = {name: ratio for name, ratio in ratios.items() if ratio > _PER_NODE_PUNCTUATION}
    converging = max(ratios[name] for name in ("dict_of_string_keys", "list_of_strings"))

    assert over == {}, (
        f"these shapes spend more than {_PER_NODE_PUNCTUATION} characters of punctuation per "
        f"node: {over}. The composed render ceiling multiplies MAX_PARAMS_NODES by that "
        f"figure, so a shape above it renders past the ceiling every record prints"
    )
    assert converging > _PER_NODE_PUNCTUATION - 0.1, (
        f"the families that converge on the per-node cost from below now peak at "
        f"{converging:.2f}, well under the {_PER_NODE_PUNCTUATION} the ceiling uses. Either "
        f"the charge changed or the render did; a factor nothing approaches is slack in a "
        f"figure five records print as a ceiling"
    )


def test_the_amendment_s_other_derived_figures_are_recomputable_too() -> None:
    """The three comparisons Amendment 1 draws around the pre-fix reach.

    The reach arm above holds the shipped cap's pair. The amendment states three
    more figures in the same paragraph, each a comparison the severity argument
    rests on, and an earlier draft of this module's reach block described them as
    *covered* while nothing read them:

    * **16,777,216 characters, 1.33x the budget** -- the worst the same
      under-charge already reached under the SDK's own ``DEFAULT_MAX_REQUEST_BODY_SIZE``,
      which is the sentence that says the breach predates this release rather
      than being created by raising the cap.
    * **4.50x the SDK default's worst** -- the shipped cap's reach over that one,
      which is how much raising the cap widened it.
    * **5.21x** -- the astral member, quoted to say the 2-byte class is the worst
      and not merely an example.

    All four come out of the same two inputs the shipped pair does: the
    ``(raw wire bytes, render width)`` pairs the code-point sweep found, and a
    byte cap. Swapping the cap for the SDK's own default is the whole difference,
    so naming them out of this module's reach would have been recording a gap
    that did not need to exist.
    """
    shipped, _wire, _width = _worst_pre_fix_render()
    default_worst = max(
        min(MAX_PARAMS_RENDERED_CHARS, DEFAULT_MAX_REQUEST_BODY_SIZE // wire) * width
        for wire, width in sweep().reach_pairs
    )
    astral_reach = min(MAX_PARAMS_RENDERED_CHARS, MAX_REQUEST_BODY_BYTES // 4) * 10
    amendment = prose(amendment_one())

    expected = {
        "the SDK default's worst render": f"{default_worst:,}",
        "that worst as a ratio of the budget": f"{default_worst / MAX_PARAMS_RENDERED_CHARS:.2f}x",
        "the shipped cap's reach over it": f"{shipped / default_worst:.2f}x",
        "the astral member's ratio": f"{astral_reach / MAX_PARAMS_RENDERED_CHARS:.2f}x",
    }
    missing = {name: figure for name, figure in expected.items() if figure not in amendment}

    assert missing == {}, (
        f"Amendment 1 no longer prints these, recomputed from the live caps and the "
        f"code-point sweep: {missing}. Each is a comparison its severity argument rests on "
        f"-- that the breach predates the cap raise, how much the raise widened it, and that "
        f"the 2-byte class is the worst rather than one example among three"
    )
