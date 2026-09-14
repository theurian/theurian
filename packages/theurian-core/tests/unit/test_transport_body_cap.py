"""The wire-expansion population ``MAX_REQUEST_BODY_BYTES``'s derivation rests on (#669).

``daemon/server.py`` does not pick its transport body cap; it derives it, as
``3 * MAX_SOURCE_FILE_BYTES + 1 MiB``. The multiplier is a **measurement** -- how
many bytes a body that lands at :data:`~theurian.security.paths.MAX_SOURCE_FILE_BYTES`
takes on the wire once ``json.dumps`` has escaped it -- and the constant's
docstring states that measurement as a complete table: one row per UTF-8 byte
length, each with its ``ensure_ascii`` and its raw-UTF-8 factor, a worst
non-control ratio of 3.0x, and a residual of exactly two control rows at 6.0x.

**That table is a population claim, and this file is what makes it falsifiable.**
An earlier draft of the constant sized the cap at ``2 *`` from a sample of
encodings someone judged realistic -- quote, backslash, newline, tab, CJK -- all
of which measure 2.0x, none of which is the worst case. Ordinary Cyrillic,
Greek, Hebrew and Arabic prose expands at 3.0x, because the ratio is a property
of a character's UTF-8 length and not of how ordinary its script is, and a body
of it landing at the file cap met the bare ``413``. So the table below is built
per *wire-escape class* rather than per remembered script: every UTF-8 byte
length, both control classes, and the ASCII characters JSON must escape, each
asserted against the factor its own encoding derives rather than against a
shared number.

A ratio moving out from under the recorded decision goes RED here. That is the
signal to re-measure and re-record the residual on the constant, not to retune a
number: which encodings can still meet the bare ``413`` at a landed size the
store would accept is exactly what these factors decide.

The wire-side counterpart is
``tests/integration/test_input_validation_dispatch.py``, which pins the formula
itself, drives a landed-size body of each script class through the transport,
and drives the ``413`` boundary at exact bytes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from fractions import Fraction
from typing import Final

import pytest

from theurian.daemon.server import MAX_REQUEST_BODY_BYTES
from theurian.security.paths import MAX_SOURCE_FILE_BYTES

pytestmark = pytest.mark.unit

#: How many copies of each representative character a sample holds. Any count
#: gives the same ratio -- every character here escapes independently of its
#: neighbours -- so this is large enough to read as text and small enough to be
#: free.
REPEAT: Final = 1024

#: The addend ``MAX_REQUEST_BODY_BYTES`` sets aside for the JSON-RPC frame
#: around a body. Named here so the multiplier can be recovered from the live
#: constant by arithmetic rather than read off its source.
ENVELOPE_HEADROOM: Final = 1024 * 1024

#: The characters JSON renders as a two-character escape whatever the encoder is
#: set to: the quote, the backslash, and the five short control escapes.
JSON_SHORT_ESCAPES: Final = frozenset('"\\\b\t\n\f\r')


@dataclass(frozen=True, slots=True)
class WireClass:
    """One wire-escape class, its representative, and the factors recorded for it.

    ``escaped`` and ``raw`` are the two columns
    :data:`~theurian.daemon.server.MAX_REQUEST_BODY_BYTES`'s docstring states as
    wire bytes over landed UTF-8 bytes, under ``ensure_ascii=True`` (the stdlib
    default, and what the escaping clients emit) and under raw UTF-8 (what the
    official Python and JS clients emit).
    """

    character: str
    escaped: int
    raw: int


#: Every class a character can fall into on this wire, one representative each.
#: Partitioned by what decides the ratio -- the character's UTF-8 length and
#: whether JSON or ``ensure_ascii`` must escape it -- so the population is
#: complete by construction rather than by whoever last remembered a script.
#: Which of these rows the constant records as its residual is
#: :data:`RESIDUAL_CLASSES`, kept apart from the table so the split is asserted
#: against the measurement rather than declared alongside it.
WIRE_CLASSES: Final = {
    "printable_ascii": WireClass("a", escaped=1, raw=1),
    "json_escapable": WireClass('"', escaped=2, raw=2),
    "short_control_escape": WireClass("\n", escaped=2, raw=2),
    "c0_other": WireClass("\x01", escaped=6, raw=6),
    "delete": WireClass("\x7f", escaped=6, raw=1),
    "two_byte": WireClass("д", escaped=3, raw=1),
    "three_byte": WireClass("日", escaped=2, raw=1),
    "astral": WireClass("\U0001f600", escaped=3, raw=1),
}

#: The classes the constant records as a residual it has not closed: the two
#: control rows, and only those. Asserted as a set below rather than checked one
#: at a time, so a third class rising past the multiplier fails too.
RESIDUAL_CLASSES: Final = frozenset({"c0_other", "delete"})

#: The classes the ``3 *`` multiplier is derived over -- everything the residual
#: does not name. A knowledge store lands text, not control bytes, which is why
#: the cap is sized for this population and the residual is recorded rather than
#: paid for.
COVERED_CLASSES: Final = frozenset(WIRE_CLASSES) - RESIDUAL_CLASSES


def _landed_bytes(sample: str) -> int:
    """What ``sample`` costs as a file on disk -- the side
    :data:`MAX_SOURCE_FILE_BYTES` bounds (ADR-0032 decision 3)."""
    return len(sample.encode("utf-8"))


def _wire_bytes(sample: str, *, ensure_ascii: bool) -> int:
    """What ``sample`` costs inside a JSON string, its delimiting quotes excluded.

    The quotes are two bytes per string however long the string is -- envelope,
    not expansion -- so charging them here would make every ratio depend on the
    sample size and none of them come out at the whole number the constant
    records. They are covered by that constant's ``+ 1 MiB`` addend, together
    with the rest of the JSON-RPC frame, which is what the addend is for.

    ``ensure_ascii`` is required rather than defaulted: :func:`_ratio` already
    carries the default, and a second one here would be a value no call reaches
    -- perturbing it changes nothing, which a mutation run confirmed.
    """
    return len(json.dumps(sample, ensure_ascii=ensure_ascii).encode("utf-8")) - 2


def _ratio(sample: str, *, ensure_ascii: bool = True) -> Fraction:
    """Wire bytes over landed bytes, exactly -- ``Fraction`` rather than
    ``float`` so an assertion of "exactly 3.0x" is not an assertion about
    binary floating point."""
    return Fraction(_wire_bytes(sample, ensure_ascii=ensure_ascii), _landed_bytes(sample))


def _derived_escaped_factor(character: str) -> Fraction:
    """What ``ensure_ascii`` must cost this character, from its encoding alone.

    Built from the JSON grammar and the character's own UTF-8/UTF-16 lengths,
    with no call to ``json.dumps``, so comparing it against the measurement is a
    check and not a restatement. Three rules, in the order the encoder applies
    them: the seven characters JSON gives a short escape cost two whatever else
    is true of them; printable ASCII costs its own one byte; everything else
    becomes one six-character ``\\uXXXX`` per UTF-16 code unit, and the ratio is
    that over the UTF-8 bytes the same character lands as.

    Takes a single character -- ``ord`` in the raw twin says so -- because a
    class is represented by one, and a mixed string has no single factor.
    """
    if character in JSON_SHORT_ESCAPES:
        return Fraction(2, len(character.encode("utf-8")))
    if character.isascii() and character.isprintable():
        return Fraction(1, 1)
    units = len(character.encode("utf-16-le")) // 2
    return Fraction(6 * units, len(character.encode("utf-8")))


def _derived_raw_factor(character: str) -> Fraction:
    """What raw UTF-8 must cost this character, from its encoding alone.

    JSON mandates escaping only ``"``, ``\\`` and U+0000-U+001F, so a raw
    encoder leaves everything else at its own bytes; the five short escapes cost
    two characters and the rest of C0 costs six, whatever ``ensure_ascii`` is
    set to. That last row is why the C0 residual has no remedy, and
    :func:`test_an_unescaped_c0_control_character_is_not_json_a_parser_accepts`
    is what says a caller cannot get under it by hand-building the body.
    """
    if character in JSON_SHORT_ESCAPES:
        return Fraction(2, 1)
    if ord(character) < 0x20:
        return Fraction(6, 1)
    return Fraction(1, 1)


@pytest.mark.parametrize("name", sorted(WIRE_CLASSES))
def test_each_escape_class_expands_on_the_wire_by_the_factor_recorded_for_it(name: str) -> None:
    """The ``ensure_ascii`` column of the constant's table, measured per class.

    This is the assertion the ``2 *`` draft could not make. It asserted one
    shared factor over a sample of five encodings that happened to agree at
    2.0x, so the classes that do not agree -- the 2-byte scripts at 3.0x, and
    both control rows -- were outside the population the multiplier was derived
    over and outside the test that was supposed to notice. Each class is now
    checked against *its own* recorded factor, so a class moving is a failure
    naming that class rather than an average nobody computes.
    """
    recorded = WIRE_CLASSES[name]

    expansion = _ratio(recorded.character * REPEAT)

    assert expansion == recorded.escaped, (
        f"{name} ({recorded.character!r}) now expands {float(expansion)}x under ensure_ascii, "
        f"not the {recorded.escaped}.0x MAX_REQUEST_BODY_BYTES's table records for its class. "
        f"Its escape is {json.dumps(recorded.character)}. Whichever direction it moved, the "
        f"reach of the gap between a landed-size cap and this transport cap moved with it: "
        f"re-measure the whole table and re-record the decision on the constant rather than "
        f"editing this number"
    )


@pytest.mark.parametrize("name", sorted(WIRE_CLASSES))
def test_each_escape_class_sent_as_raw_utf8_costs_the_factor_recorded_for_it(name: str) -> None:
    """The raw-UTF-8 column, which is where the one recorded remedy lives.

    The constant tells a caller meeting the ``413`` on DEL-dense text to send
    the body raw rather than ``ensure_ascii``-escaped, and says the
    ``c0_other`` row has no such way out. Both halves are measured here, because
    a remedy that stopped working is worse than no remedy: it sends someone to
    retry a request that will be refused again. ``delete`` is the row that drops
    to 1.0x; ``c0_other`` stays at 6.0x under a raw encoder, which is the
    absence of a remedy stated as a number.

    The other six rows are measured for a different reason: the constant states
    this column for every class, and it is the column a caller using a raw-UTF-8
    client actually pays. That it never exceeds 2.0x outside the control rows is
    asserted by
    :func:`test_the_multiplier_is_the_worst_wire_ratio_any_non_control_class_reaches`.
    """
    recorded = WIRE_CLASSES[name]

    expansion = _ratio(recorded.character * REPEAT, ensure_ascii=False)

    assert expansion == recorded.raw, (
        f"{name} ({recorded.character!r}) now costs {float(expansion)}x as raw UTF-8, not the "
        f"{recorded.raw}.0x MAX_REQUEST_BODY_BYTES's table records. A row that fell means a "
        f"remedy exists and is not recorded; a row that rose means a recorded remedy no longer "
        f"removes the expansion it claims to"
    )


def test_every_recorded_factor_is_what_the_character_s_own_encoding_derives() -> None:
    """The table is a derivation over classes, not a list of anecdotes.

    A per-class table is only better than a shared factor if the classes
    partition something. These do: the ratio is decided by the character's UTF-8
    length, its UTF-16 length, and whether JSON or ``ensure_ascii`` must escape
    it -- nothing else -- so each recorded factor is recomputed here from those
    three alone, without ``json.dumps``. A row added later with a hand-picked
    number fails here even if CPython agrees with it, which is the check that
    keeps the population honest: the two tests above ask whether CPython matches
    the table, and this one asks whether the table matches the grammar.
    """
    derived = {
        name: (_derived_escaped_factor(record.character), _derived_raw_factor(record.character))
        for name, record in WIRE_CLASSES.items()
    }

    recorded = {name: (Fraction(r.escaped), Fraction(r.raw)) for name, r in WIRE_CLASSES.items()}

    disagreements = {
        name: {"derived": tuple(map(str, derived[name])), "recorded": tuple(map(str, pair))}
        for name, pair in recorded.items()
        if derived[name] != pair
    }
    assert disagreements == {}, (
        f"a recorded factor is not what its character's own encoding derives: {disagreements}. "
        f"Either the class is misfiled -- its representative does not belong to the class it "
        f"names -- or the number was picked rather than derived, which is the defect the 2x "
        f"draft shipped"
    )


def test_the_multiplier_is_the_worst_wire_ratio_any_non_control_class_reaches() -> None:
    """Where the ``3`` in ``3 * MAX_SOURCE_FILE_BYTES`` comes from.

    The multiplier is not a safety margin and not a round number: it is the
    maximum of the ``ensure_ascii`` column over every class the residual does
    not name, and the cap is that maximum times the landed-byte bound plus the
    envelope addend. Asserted as both -- the worst ratio *is* 3.0x, and the
    constant *is* that ratio applied -- so a cap edited to some other multiple
    fails here even while the per-class ratios stay put, and a class that rises
    past 3.0x fails here even while the constant stays put.

    The raw column is asserted alongside it because the constant states it as
    part of the same claim: an escaping client is the worst case, and a client
    sending raw UTF-8 never costs more than twice its landed bytes.
    """
    worst_escaped = max(_ratio(WIRE_CLASSES[name].character * REPEAT) for name in COVERED_CLASSES)
    worst_raw = max(
        _ratio(WIRE_CLASSES[name].character * REPEAT, ensure_ascii=False)
        for name in COVERED_CLASSES
    )

    assert worst_escaped == 3, (
        f"the worst ensure_ascii expansion over the classes the cap is derived for is now "
        f"{float(worst_escaped)}x, not the 3.0x MAX_REQUEST_BODY_BYTES's multiplier is. The "
        f"multiplier is this measurement, so it is now describing a population it no longer "
        f"covers -- which is #669's defect in the shape it shipped in"
    )
    assert worst_raw == 2, (
        f"raw UTF-8 now costs {float(worst_raw)}x at worst over those same classes, not the "
        f"2.0x the constant records; the claim that an escaping client is the worst case owes "
        f"a re-measurement"
    )
    assert worst_escaped * MAX_SOURCE_FILE_BYTES + ENVELOPE_HEADROOM == MAX_REQUEST_BODY_BYTES, (
        f"MAX_REQUEST_BODY_BYTES ({MAX_REQUEST_BODY_BYTES}) is not the measured worst "
        f"expansion ({float(worst_escaped)}x) applied to MAX_SOURCE_FILE_BYTES "
        f"({MAX_SOURCE_FILE_BYTES}) plus the {ENVELOPE_HEADROOM}-byte envelope addend, so the "
        f"cap is a number somebody chose rather than the derivation its docstring describes"
    )


def test_the_classes_that_exceed_the_multiplier_are_exactly_the_two_the_residual_names() -> None:
    """The residual is a closed set, asserted by equality in both directions.

    Set equality rather than a check per row: a *third* class rising past the
    multiplier is the finding this file exists to produce, and a test that
    asserted only "``c0_other`` is 6.0x and ``delete`` is 6.0x" would stay green
    through it. Equality fails on an addition and on a removal alike -- gone, and
    the constant is warning about something that no longer happens; joined, and
    an encoding the cap was sized for can meet the bare ``413`` at a landed size
    the store would accept, unrecorded.
    """
    multiplier = Fraction(MAX_REQUEST_BODY_BYTES - ENVELOPE_HEADROOM, MAX_SOURCE_FILE_BYTES)

    exceeding = {
        name
        for name, record in WIRE_CLASSES.items()
        if _ratio(record.character * REPEAT) > multiplier
    }

    assert exceeding == RESIDUAL_CLASSES, (
        f"the classes expanding past the cap's own {float(multiplier)}x multiplier are "
        f"{sorted(exceeding)}, not the {sorted(RESIDUAL_CLASSES)} MAX_REQUEST_BODY_BYTES "
        f"records as its residual. A class that joined can meet the bare 413 at a landed size "
        f"the store would accept and the constant does not say so; a class that left is a "
        f"warning the constant no longer needs to carry"
    )


def test_an_unescaped_c0_control_character_is_not_json_a_parser_accepts() -> None:
    """Why the ``c0_other`` row's 6.0x has no remedy, driven rather than asserted.

    The remedy the constant offers the other expensive rows is "send it raw",
    and the reason it withholds that remedy from C0 is that JSON forbids an
    unescaped control character in a string -- so the 6.0x of ``\\u0001`` is the
    *cheapest* legal form, not merely the one ``json.dumps`` happens to emit.
    That is a claim about the grammar, so it is driven against a hand-built body
    a caller could post rather than inferred from the encoder's output: the
    encoder escaping it proves only what the encoder does.

    The positive control is in the same test: DEL, one code point higher than
    the C0 block, round-trips raw. Without it this would pass against a parser
    that rejected every non-ASCII byte.
    """
    hand_built_c0 = '"' + "\x01" * 4 + '"'
    hand_built_del = '"' + "\x7f" * 4 + '"'

    with pytest.raises(json.JSONDecodeError):
        json.loads(hand_built_c0)

    assert json.loads(hand_built_del) == "\x7f" * 4, (
        "an unescaped DEL is no longer accepted by a JSON parser, so the raw-form remedy "
        "MAX_REQUEST_BODY_BYTES records for the DEL row sends a caller to a request that "
        "will be refused again"
    )


def test_a_body_landing_at_the_file_cap_fits_the_transport_cap_with_the_envelope_headroom() -> None:
    """What the measured factors buy, recomputed from the whole population.

    This is the derivation's consequence and the reason #669's fix is sized the
    way it is: at the worst expansion any class the cap is derived for produces,
    a body that lands at :data:`MAX_SOURCE_FILE_BYTES` -- the cap ADR-0032
    decision 3 puts on the file a proposal writes -- still arrives, and what is
    left over is exactly the 1 MiB the constant sets aside for the JSON-RPC
    frame around it.

    **The equality is no longer true by construction, and that is the change
    here.** While the population was five encodings that all measured 2.0x, the
    worst of them was the multiplier by arithmetic and this test could not fail
    for the reason it was written to catch. The population is now every wire
    class, so the maximum is a measurement over eight rows of which two disagree
    with each other and one -- ``two_byte`` -- is the one that sets it. Take a
    class out of ``COVERED_CLASSES``, or move a factor, and the left-hand side
    moves while the constant does not.

    Equality on the headroom rather than ``>=``: "there is some room left"
    passes for a cap ten times too large and would not notice the addend being
    changed.
    """
    worst = max(_ratio(WIRE_CLASSES[name].character * REPEAT) for name in COVERED_CLASSES)

    on_the_wire = worst * MAX_SOURCE_FILE_BYTES

    assert on_the_wire <= MAX_REQUEST_BODY_BYTES, (
        f"a body landing at MAX_SOURCE_FILE_BYTES ({MAX_SOURCE_FILE_BYTES}) takes "
        f"{on_the_wire} bytes on the wire at the worst covered expansion ({float(worst)}x), "
        f"past the transport cap ({MAX_REQUEST_BODY_BYTES}), so the write-intent body "
        f"ADR-0032 sizes for meets a bare 413 that names no tool -- #669's defect, re-opened"
    )
    assert MAX_REQUEST_BODY_BYTES - on_the_wire == ENVELOPE_HEADROOM, (
        f"the headroom left for the JSON-RPC envelope is "
        f"{MAX_REQUEST_BODY_BYTES - on_the_wire} bytes, not the {ENVELOPE_HEADROOM} "
        f"MAX_REQUEST_BODY_BYTES's addend records. Either the addend moved or the worst "
        f"covered expansion did; the constant's docstring states both, so one of them is "
        f"now wrong there"
    )
