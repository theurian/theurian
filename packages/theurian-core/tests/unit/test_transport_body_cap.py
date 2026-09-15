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
of it landing at the file cap met the bare ``413``. So the table is built per
*wire-escape class* rather than per remembered script: every UTF-8 byte length,
both control classes, and the ASCII characters JSON must escape, each asserted
against the factor its own encoding derives rather than against a shared number.

The table itself is ``wire_escape_classes``, beside the other shared test
helpers, because the threat model's T-11 residual states the same count and the
same factors in prose and
``test_the_t11_residual_names_as_many_classes_as_the_unit_module_pins``, one
of the SEC-12 record pins, reads them from there rather than transcribing them. What stays here is
everything that *checks* the table: the measurement through ``json.dumps``, and
the independent derivation from each character's own encoding.

**A representative is a claim about its class, and that claim is checked here
too.** Measuring a table's representatives cannot notice a representative filed
under the wrong name: put a Cyrillic character in the ``three_byte`` row and the
measurement and the derivation agree with each other, because both read the same
wrong character. Five mutations of exactly that shape survived a round of
review. Three things close it, and each is a separate arm below -- the row's own
byte length must be the one its *name* asserts; the representatives must be
pairwise distinct and sit inside the ranges their names describe; and the
factors must hold over **every member of the class**, swept from the code-point
space by ``escape_class_sweep`` rather than from the table's own rows.

A ratio moving out from under the recorded decision goes RED here. That is the
signal to re-measure and re-record the residual on the constant, not to retune a
number: which encodings can still meet the bare ``413`` at a landed size the
store would accept is exactly what these factors decide.

The wire-side counterpart is
``tests/integration/test_input_validation_dispatch.py``, which pins the formula
itself, drives a landed-size body of each script class through the transport,
and drives the ``413`` boundary at exact bytes.

The same body-cap comment also narrates *how many* tools that surface holds --
``mcp/tools.py:N``, the answer to its own ``git grep -c '^    @_tool($'`` -- and
that ``N`` went stale from 7 to 9 in silence when slice B4 registered the two
write-intent tools, because nothing recomputed it from the tree. It is a
derivation like the multiplier is, so
:func:`test_the_server_comment_states_the_live_at_tool_registration_count` pins it
like one: it recomputes the count from ``mcp/tools.py``'s own decorators and holds
the comment's ``N`` equal to it, from the tree rather than from the prose.
"""

from __future__ import annotations

import ast
import json
import re
from fractions import Fraction
from json.encoder import encode_basestring, encode_basestring_ascii
from typing import Final

import pytest
from escape_class_sweep import CODE_POINTS, SURROGATES, class_of, landed_bytes, sweep
from wire_escape_classes import COVERED_CLASSES, RESIDUAL_CLASSES, WIRE_CLASSES
from write_lock_claims import REPO_ROOT

from theurian.daemon.server import MAX_REQUEST_BODY_BYTES
from theurian.security.paths import MAX_SOURCE_FILE_BYTES

pytestmark = pytest.mark.unit

#: How many UTF-8 bytes each class's *name* says its representative lands as.
#: Held equal to :data:`WIRE_CLASSES`'s own keys below, so a row added later
#: cannot join the table without someone stating the length its name asserts --
#: which is the moment the misfiling this guards against would happen.
NAMED_BYTE_LENGTH: Final = {
    "printable_ascii": 1,
    "json_escapable": 1,
    "short_control_escape": 1,
    "c0_other": 1,
    "delete": 1,
    "two_byte": 2,
    "three_byte": 3,
    "astral": 4,
}

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
#: set to: the quote, the backslash, and the five short control escapes. Here
#: rather than beside the table in ``wire_escape_classes``, because only the
#: derivations below read it: it is this module's model of the grammar, which is
#: the thing the table is checked *against*, and moving the two together would
#: let a wrong model and a wrong table agree.
JSON_SHORT_ESCAPES: Final = frozenset('"\\\b\t\n\f\r')


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

    **A surrogate is refused rather than encoded.** ``repr`` renders a lone
    surrogate perfectly well, so one can reach a width table; UTF-8 carries
    none, so ``str.encode`` raises ``UnicodeEncodeError`` on it. Filed as a wire
    representative it would fail here with an encoder's error four frames deep
    instead of a sentence naming what went wrong, and the reader would go
    looking at the encoding rather than at the table.
    """
    if ord(character) in SURROGATES:
        raise ValueError(
            f"U+{ord(character):04X} is a surrogate and cannot be a wire-class "
            f"representative: UTF-8 carries no surrogate, so the class has no landed byte "
            f"count to take a ratio against. `repr` renders one, which is why the render "
            f"width table does cover it -- see test_rendered_width_charge.py."
        )
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


# -- Representative integrity: the table's rows are what their names claim ------


def test_every_class_name_asserts_a_byte_length_and_the_table_covers_exactly_those() -> None:
    """The premise under the row arms: every row has a stated length to honour.

    :data:`NAMED_BYTE_LENGTH` is the reading of each class name -- ``two_byte``
    says two, ``astral`` says four, the five one-byte rows say one. A row added
    to :data:`WIRE_CLASSES` without an entry here would slip past the per-row
    arm below silently, which is the same omission the representatives
    themselves were vulnerable to. Equality, so a row removed fails too.
    """
    assert set(NAMED_BYTE_LENGTH) == set(WIRE_CLASSES), (
        f"the byte lengths the class names assert cover {sorted(NAMED_BYTE_LENGTH)} while "
        f"the table holds {sorted(WIRE_CLASSES)}. A row with no stated length is a row the "
        f"per-row arm below cannot check, and a stated length with no row is a reading of a "
        f"name nothing carries"
    )


@pytest.mark.parametrize("name", sorted(WIRE_CLASSES))
def test_every_representative_lands_as_the_byte_count_its_class_name_asserts(name: str) -> None:
    """RED means a row's representative does not belong to the class it names.

    This is the arm that survives nothing. A class name in this table is a claim
    about UTF-8 length, and the factors recorded beside it are only true of
    characters of that length -- so swapping the ``three_byte`` row's CJK
    character for a Cyrillic one makes the row's measured factor 3.0x while the
    row records 2.0x, *and* leaves nothing measuring three-byte text at all.
    The second half is the silent one: the measurement arms above would then
    agree with each other about a class that has no member under test.

    Length only, deliberately. Which character of that length stands for the
    class does not matter, because
    :func:`test_every_member_of_every_class_measures_the_factors_its_row_records`
    holds the factors over every member; what matters is that the character is
    *of* the class.
    """
    representative = WIRE_CLASSES[name].character

    landed = len(representative.encode("utf-8"))

    assert landed == NAMED_BYTE_LENGTH[name], (
        f"the {name} row's representative {representative!r} lands as {landed} UTF-8 bytes, "
        f"not the {NAMED_BYTE_LENGTH[name]} its class name asserts. Either the character was "
        f"replaced by one from another class -- in which case this row now records another "
        f"class's factors and its own class has no member under test -- or the row was "
        f"renamed without moving its representative"
    )


def test_every_representative_sits_in_the_range_its_class_name_describes() -> None:
    """RED means a control row's representative drifted out of its own range.

    Byte length alone does not separate the five one-byte rows: a C0 control, a
    DEL, a quote and an ``a`` are all one byte, so the arm above passes for any
    permutation of them. What separates them is where they sit, and the two
    control rows are the ones whose position carries the recorded residual --
    ``c0_other`` is C0 minus the five characters JSON short-escapes, and
    ``delete`` is U+007F exactly.

    Asserted through the partition rather than by re-testing the ranges here:
    :func:`~escape_class_sweep.class_of` computes a character's class from the
    character, so requiring every representative to classify as its own row is
    the whole integrity claim in one line, for all eight rows at once.
    """
    misfiled = {
        name: (record.character, class_of(record.character))
        for name, record in WIRE_CLASSES.items()
        if class_of(record.character) != name
    }

    assert misfiled == {}, (
        f"these rows hold a representative the partition puts in another class: {misfiled}. "
        f"The class a character belongs to is computed from the character, so a row whose "
        f"representative classifies elsewhere is recording another class's factors under "
        f"this name -- and leaving its own class with no member under test"
    )


def test_no_two_classes_share_a_representative() -> None:
    """RED means two rows are measuring the same character.

    Distinctness is not implied by the arms above: two one-byte rows can both
    hold ``a`` and both classify... they cannot, since the partition is a
    function -- which is exactly why this arm is about *sharing* rather than
    about correctness. A shared representative means one class is unmeasured
    while the table still looks complete, and it is the state a careless
    copy-paste of a row leaves behind.
    """
    representatives = [record.character for record in WIRE_CLASSES.values()]

    assert len(set(representatives)) == len(representatives), (
        f"two classes share a representative: {sorted(representatives)}. The table then has "
        f"as many rows as classes and fewer characters, so one class is measured twice and "
        f"another not at all"
    )


# -- Exhaustiveness: the factors hold over the space, not over the rows ---------


def test_the_sweep_encodes_the_way_json_dumps_does() -> None:
    """The premise under every swept arm: the fast encoder is ``json.dumps``'s own.

    :mod:`escape_class_sweep` calls :func:`json.encoder.encode_basestring_ascii`
    and :func:`~json.encoder.encode_basestring` directly, and derives a
    character's landed byte count by arithmetic rather than by encoding it --
    three shortcuts, taken because a million ``json.dumps`` calls is most of a
    sweep's runtime. Each is a claim about CPython, and a wrong one would make
    every ratio below wrong in the same direction with nothing to say so.

    So all three are held over the whole space, which costs about a second and
    removes the only reason to distrust the sweep.
    """
    disagreements = [
        code_point
        for code_point in range(CODE_POINTS)
        if code_point not in SURROGATES
        and (
            encode_basestring_ascii(chr(code_point)) != json.dumps(chr(code_point))
            or encode_basestring(chr(code_point)) != json.dumps(chr(code_point), ensure_ascii=False)
            or landed_bytes(code_point) != len(chr(code_point).encode("utf-8"))
        )
    ]

    assert disagreements == [], (
        f"{len(disagreements)} code points encode differently through the sweep's shortcuts "
        f"than through json.dumps and str.encode, the first being "
        f"{[f'U+{c:04X}' for c in disagreements[:8]]}. Every wire ratio this module measures "
        f"is taken through those shortcuts, so they are wrong together and silently"
    )


def test_every_class_the_space_produces_is_a_row_of_the_table() -> None:
    """RED means the table and the partition disagree about what the classes are.

    The partition is total over the non-surrogate space, so the classes it
    produces are the classes that exist. A name it produces with no row is a
    class of characters this cap was never sized for; a row with no name it
    produces is a row measuring something the space cannot present. Deleting the
    ``two_byte`` row is the first of those, and it is the mutation that used to
    survive: nothing noticed a whole UTF-8 length going unrecorded, because
    every remaining row still measured correctly.
    """
    produced = set(sweep().factors)

    assert produced == set(WIRE_CLASSES), (
        f"the code-point space produces {sorted(produced)} and the table records "
        f"{sorted(WIRE_CLASSES)}. Produced with no row: "
        f"{sorted(produced - set(WIRE_CLASSES))} -- characters whose wire cost this cap's "
        f"derivation never accounted for. Recorded with no producer: "
        f"{sorted(set(WIRE_CLASSES) - produced)} -- a row measuring a class no character "
        f"falls into"
    )


@pytest.mark.parametrize("name", sorted(WIRE_CLASSES))
def test_every_member_of_every_class_measures_the_factors_its_row_records(name: str) -> None:
    """The exhaustiveness claim, asserted over members rather than over one member.

    A row records two factors for a whole class. Measuring its representative
    checks them for one character; this checks them for every character the
    partition puts in the class -- 954,464 of them for ``astral``, one for
    ``delete`` -- so a row whose factors are right about its representative and
    wrong about its class fails here.

    That is what makes the recorded table a statement about the wire rather than
    about eight characters, and it is the arm the constant's *"swept over every
    non-control code point rather than sampled inside the classes, so the class
    boundaries are measured too"* actually rests on.
    """
    recorded = WIRE_CLASSES[name]

    measured = {(Fraction(*escaped), Fraction(*raw)) for escaped, raw in sweep().factors[name]}

    assert measured == {(Fraction(recorded.escaped), Fraction(recorded.raw))}, (
        f"the {name} class's members do not all measure the "
        f"({recorded.escaped}, {recorded.raw}) its row records: the space produces "
        f"{sorted((str(e), str(r)) for e, r in measured)}. More than one pair means the "
        f"class is not a class -- the partition is putting characters of different wire cost "
        f"together, and the row's single pair describes only some of them"
    )


def test_the_worst_covered_ratio_and_the_residual_set_come_out_of_the_space() -> None:
    """The multiplier and the residual, recomputed from members rather than rows.

    :func:`test_the_multiplier_is_the_worst_wire_ratio_any_non_control_class_reaches`
    and
    :func:`test_the_classes_that_exceed_the_multiplier_are_exactly_the_two_the_residual_names`
    ask the same two questions of the table's eight representatives. This asks
    them of all 1,112,064 characters UTF-8 can carry, which is the form the
    constant's docstring states them in -- and the form that notices a class
    whose representative is mild while its members are not.
    """
    swept = {
        name: {(Fraction(*escaped), Fraction(*raw)) for escaped, raw in pairs}
        for name, pairs in sweep().factors.items()
    }
    worst_escaped = max(e for name in COVERED_CLASSES for e, _ in swept[name])
    worst_raw = max(r for name in COVERED_CLASSES for _, r in swept[name])

    exceeding = {name for name in swept if max(e for e, _ in swept[name]) > worst_escaped}

    assert worst_escaped == 3, (
        f"swept over the whole space, the worst ensure_ascii expansion among the classes the "
        f"cap is derived for is {float(worst_escaped)}x, not the 3.0x its multiplier is. The "
        f"table's representatives may still say 3.0x while some other member of their class "
        f"does not -- which is the difference between a sampled claim and this one"
    )
    assert worst_raw == 2, (
        f"swept over the whole space, raw UTF-8 costs {float(worst_raw)}x at worst over those "
        f"same classes, not the 2.0x the constant records"
    )
    assert exceeding == RESIDUAL_CLASSES, (
        f"swept over the whole space, the classes expanding past {float(worst_escaped)}x are "
        f"{sorted(exceeding)}, not the {sorted(RESIDUAL_CLASSES)} recorded as the residual"
    )


def test_a_surrogate_cannot_be_filed_as_a_wire_representative() -> None:
    """RED means the derivation would fail on an encoder error instead of a sentence.

    ``repr`` renders a lone surrogate, so one is a legitimate member of the
    *render width* table and could be copied into this one by a reader working
    from that table. UTF-8 carries no surrogate, so the wire ratio it would need
    does not exist -- and without a guard the failure is a
    ``UnicodeEncodeError`` raised inside ``str.encode``, which reads as a bug in
    the measurement rather than as a character that does not belong.

    Both halves are asserted: the refusal fires, and it says why.
    """
    with pytest.raises(ValueError, match="surrogate") as caught:
        _derived_escaped_factor("\ud800")

    assert "U+D800" in str(caught.value), str(caught.value)
    assert "UTF-8 carries no surrogate" in str(caught.value), str(caught.value)


def test_no_two_rows_record_the_same_pair_of_factors() -> None:
    """RED means a boundary in the partition stopped being observable.

    The exhaustiveness arms above ask, for each class, whether every member
    measures the row's factors. They cannot ask whether the *boundary* between
    two classes is in the right place when both sides record the same pair: move
    the line between ``json_escapable`` and ``short_control_escape`` -- both
    ``(2, 2)`` -- and every member still measures its row's factors, because the
    rows are indistinguishable by what they record.

    So this arm does not assert that there are no collisions -- there are two,
    and the point is that both are *named*, with what covers each written down:

    * ``json_escapable`` / ``short_control_escape``, both ``(2, 2)``. Same wire
      cost, different reason: JSON spells ``"`` and ``\\`` one way and the five
      short control escapes another. Nothing measurable separates them, and
      nothing needs to -- they are one wire class split for readability.
    * ``two_byte`` / ``astral``, both ``(3, 1)``. Two classes of different UTF-8
      length that cost the same on the wire. A *representative* filed in the
      wrong one of these is caught -- by
      :func:`test_every_representative_lands_as_the_byte_count_its_class_name_asserts`
      and :func:`test_every_representative_sits_in_the_range_its_class_name_describes`,
      which read UTF-8 length rather than cost.

    **A drift in :func:`~escape_class_sweep.class_of` itself is a different
    thing, and nothing here sees it.** This pin reads the table; the arms above
    read the representatives. Move the boundary inside the *function* between two
    same-pair rows and every arm in this module stays green: each row's
    representative still classifies as its own row, because both sit outside the
    region that moved, and every member still measures its row's factors, because
    the factors are identical on both sides. Round 4 drove exactly that, twice,
    and both mutations survived -- verdict-neutral by measurement, and unguarded.
    Closing it needs an arm driving ``class_of`` over the space against an
    independently stated rule, which is gap 1 on
    https://github.com/theurian/theurian/issues/697. Recorded here rather than
    claimed closed: what this arm contributes is that the *set* of collisions
    stays the two that are known, so a new unobservable boundary cannot appear
    unannounced.

    A collision that *appears* needs that treatment: someone has to say which
    boundary has stopped being observable and what holds it instead. A collision
    that disappears means a factor moved, which is its own finding.
    """
    by_pair: dict[tuple[int, int], list[str]] = {}
    for name, record in WIRE_CLASSES.items():
        by_pair.setdefault((record.escaped, record.raw), []).append(name)

    colliding = {pair: sorted(names) for pair, names in by_pair.items() if len(names) > 1}

    assert colliding == {
        (2, 2): ["json_escapable", "short_control_escape"],
        (3, 1): ["astral", "two_byte"],
    }, (
        f"the rows sharing a factor pair are {colliding}, not the two collisions recorded. "
        f"A pair shared by two rows means the sweep cannot tell a misplaced boundary between "
        f"them from a correct one -- every member measures its row's factors either way -- so "
        f"the boundary rests entirely on `class_of`. A new collision needs that said out "
        f"loud; a collision that disappeared means a factor moved"
    )


# -- The tool count the comment states is the live registration count ----------

_TOOLS_MODULE: Final = REPO_ROOT / "packages/theurian-core/src/theurian/mcp/tools.py"
_SERVER_MODULE: Final = REPO_ROOT / "packages/theurian-core/src/theurian/daemon/server.py"

#: The count ``daemon/server.py``'s body-cap comment prints for the answer to its
#: own ``git grep -c '^    @_tool($' -- packages/theurian-core/src``, captured out
#: of the ``mcp/tools.py:<N>`` it writes it as.
_STATED_TOOL_COUNT: Final = re.compile(r"mcp/tools\.py:(\d+)")


def _live_at_tool_decorator_count() -> int:
    """How many ``@_tool(...)`` registrations ``mcp/tools.py`` carries, from its tree.

    The AST equivalent of the comment's own ``git grep -c '^    @_tool($'``: every
    function whose decorators include a call to ``_tool``. Recomputed from the live
    source so the pin holds a derivation, not a transcription of the prose it checks
    (``pin-derivations-not-prose``).
    """
    tree = ast.parse(_TOOLS_MODULE.read_text(encoding="utf-8"), filename=_TOOLS_MODULE.name)
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name)
        and decorator.func.id == "_tool"
    )


def _tool_count_the_server_comment_states() -> int:
    """The tool count ``daemon/server.py``'s body-cap comment prints, read from source.

    Exactly one occurrence is expected: the comment states the count once, as the
    surface ``MAX_REQUEST_BODY_BYTES`` is sized for. Zero would mean the derivation
    stopped citing the count and this pin is holding nothing to nothing.
    """
    matches = _STATED_TOOL_COUNT.findall(_SERVER_MODULE.read_text(encoding="utf-8"))
    assert len(matches) == 1, (
        f"daemon/server.py states an `mcp/tools.py:<count>` in {len(matches)} places, "
        f"expected 1. The body-cap comment derives its sizing from that count, so zero means "
        f"the derivation no longer names it and there is nothing here to pin"
    )
    return int(matches[0])


def test_the_server_comment_states_the_live_at_tool_registration_count() -> None:
    """RED means ``daemon/server.py``'s body-cap comment miscounts the registered tools.

    The comment sizes ``MAX_REQUEST_BODY_BYTES`` for "the surface ADR-0032 designs
    and slice B4 registered", and prints that surface's size as the answer to
    ``git grep -c '^    @_tool($' -- packages/theurian-core/src`` --
    ``mcp/tools.py:N``. That narration went stale from 7 to 9 in silence when B4
    registered the two write-intent tools, because nothing recomputed ``N`` from the
    tree. This pins the derivation: ``N`` must equal the live count of ``@_tool(...)``
    decorators in ``mcp/tools.py``, so a tool added or removed without re-counting
    the comment reddens here rather than shipping a wrong count beside a
    security-relevant transport bound (``pin-derivations-not-prose``).
    """
    stated = _tool_count_the_server_comment_states()
    live = _live_at_tool_decorator_count()

    assert stated == live, (
        f"daemon/server.py's body-cap comment says mcp/tools.py registers {stated} tools, but "
        f"the live tree carries {live} `@_tool(...)` decorators. That comment cites the count "
        f"as the surface the transport body cap is sized for -- re-count it in the same change "
        f"that adds or removes a tool"
    )
