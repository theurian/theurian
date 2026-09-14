"""What ``_rendered_width`` charges a leaf, per class ``repr`` escapes (#669).

:data:`~theurian.mcp.validation.MAX_PARAMS_RENDERED_CHARS` bounds the render
work ``jsonschema`` may be asked to do, and
:func:`~theurian.mcp.validation._rendered_width` is the only thing that holds
it. ``jsonschema`` builds its own message with ``{instance!r}``, and ``repr``
escapes -- so a leaf charged its own length is charged one character for
something that renders as up to ten, and the budget is enforced against a count
that is not the render.

**The premise that made this look safe was false in both directions.** The
constant was first written under *"a request's rendered width never exceeds the
bytes the caller sent"* -- a sentence the module now carries only to warn
against it -- which reads true and is not: one raw U+007F byte renders as four
characters, and one raw U+0600 -- two UTF-8 bytes -- renders as six. No
ordering of the transport byte cap and this character cap substitutes for the
charge; the charge is what makes the bound hold at *any* transport cap.

So every class ``repr`` renders differently is charged here against
``len(repr(value)) - 2`` exactly -- the two delimiting quotes ``repr`` always
carries -- rather than against a remembered number, and the two arms that exist
for soundness rather than for accuracy (``bool``, ``int``) are driven for the
one property they claim: never under-charging.

``mcp/validation.py``'s own table is the record this file makes falsifiable, and
``tests/integration/test_input_validation_dispatch.py`` drives the consequence
over the wire: a body smaller in bytes than the character budget still meets it.
"""

from __future__ import annotations

from typing import Final

import pytest
from escape_class_sweep import CODE_POINTS, expected_width, sweep

from theurian.mcp import validation

pytestmark = pytest.mark.unit

#: The widths ``repr`` can charge one code point, and how many code points each
#: holds. Measured 2026-09-15 over all 1,114,112 of them on CPython 3.13: four
#: two-character escapes (``\t`` ``\n`` ``\r`` ``\\``), the 64 ``\xHH`` code
#: points, 9,956 other non-printable BMP ones, 954,464 non-printable astral
#: ones, and the printable remainder. A count that moves is a Unicode data
#: change moving characters across the printable boundary -- a real event, and
#: one worth a failure that names it rather than a silently shifted table.
WIDTH_POPULATION: Final = {1: 149_624, 2: 4, 4: 64, 6: 9_956, 10: 954_464}

#: One representative per class ``repr`` renders differently, with the number of
#: characters it contributes to ``{instance!r}``. The classes come from
#: ``_rendered_width``'s own table, which was measured exhaustively over all
#: 1,114,112 code points; what is checked here is that the implementation agrees
#: with it, one class at a time, so a class that moves fails by name.
RENDER_CLASSES: Final = {
    # Rendered as themselves: `repr` escapes nothing printable but the quotes
    # and the backslash, in any plane.
    "printable_ascii": ("hello world", 11),
    "printable_cjk": ("日本語", 3),
    "printable_astral_emoji": ("\U0001f600", 1),
    "double_quote": ('say "hi"', 8),
    "apostrophe_without_a_quote": ("it's", 4),
    # Two characters: the short escapes, and the apostrophe when the string also
    # carries a `"` so `repr` cannot switch delimiters away from it.
    "backslash": ("\\", 2),
    "tab": ("\t", 2),
    "newline": ("\n", 2),
    "carriage_return": ("\r", 2),
    "apostrophe_with_a_quote": ("a'b\"c", 6),
    # Four characters: the 64 code points `repr` renders as `\xHH`.
    "hex_escape_c0": ("\x01", 4),
    "hex_escape_delete": ("\x7f", 4),
    "hex_escape_latin1": ("\xa0", 4),
    "hex_escape_soft_hyphen": ("\xad", 4),
    # Six: every other non-printable BMP code point, lone surrogates included.
    "unicode_escape_bmp": ("؀", 6),
    "unicode_escape_lone_surrogate": ("\ud800", 6),
    # Ten: non-printable astral.
    "unicode_escape_astral": ("\U000e0001", 10),
}

#: The classes whose render is wider than the string's own length -- the ones a
#: ``len(value)`` charge under-counts. Asserted by set equality below rather
#: than listed per test, so a class that stops escaping, or one that starts,
#: fails here.
ESCAPING_CLASSES: Final = frozenset(
    {
        "backslash",
        "tab",
        "newline",
        "carriage_return",
        "apostrophe_with_a_quote",
        "hex_escape_c0",
        "hex_escape_delete",
        "hex_escape_latin1",
        "hex_escape_soft_hyphen",
        "unicode_escape_bmp",
        "unicode_escape_lone_surrogate",
        "unicode_escape_astral",
    }
)


@pytest.mark.parametrize("name", sorted(RENDER_CLASSES))
def test_every_render_class_is_charged_exactly_what_repr_renders_it_as(name: str) -> None:
    """The charge is the render, per class, to the character.

    Two assertions because they fail for different reasons. The first is against
    the table ``mcp/validation.py`` records, so a class that moves names itself;
    the second is against ``repr`` itself on this interpreter, so a CPython
    change to any escape rule is caught even where the table happens to agree.

    ``len(repr(value)) - 2`` is exact rather than an estimate: ``repr`` of a
    string always carries exactly two delimiting quotes, whichever quote it
    picked. Exactness in the *other* direction matters too -- an over-charging
    arm refuses a request the budget was sized to admit -- which is why this is
    equality and not ``>=``.
    """
    sample, rendered = RENDER_CLASSES[name]

    charge = validation._rendered_width(sample)

    assert charge == rendered, (
        f"{name} ({sample!r}) is charged {charge} characters, not the {rendered} "
        f"_rendered_width's own table records for its class. Under-charging lets a request "
        f"past MAX_PARAMS_RENDERED_CHARS build a jsonschema message this seam never "
        f"budgeted for; over-charging refuses a request the budget was sized to admit"
    )
    assert charge == len(repr(sample)) - 2, (
        f"{name} ({sample!r}) is charged {charge} but renders as "
        f"{len(repr(sample)) - 2} characters under repr on this interpreter, so the charge "
        f"is no longer the render it claims to be"
    )


def test_the_classes_charged_more_than_their_own_length_are_exactly_the_escaping_ones() -> None:
    """The direction of #669's charge defect, and the control that gives it teeth.

    A charge of ``len(value)`` -- what this function carried before -- is
    correct for every class ``repr`` renders as itself and wrong for every class
    it escapes, by factors up to ten to one. Set equality says both halves at
    once: each escaping class must cost *more* than its own length, and each
    printable one must cost exactly its length and no more.

    The second half is not decoration. The obvious over-shoot -- charging
    ``len(repr(value))`` with its two delimiting quotes, rather than
    ``- 2`` -- is sound in the direction that matters and still wrong: it puts
    every printable class into this set, and it prices a request two characters
    higher per leaf than the render it is standing in for. A one-sided
    ``>=``-shaped assertion would take it.

    What this does *not* see is the fast path being removed while the fallback
    stays: ``len(repr(value)) - 2`` is the same number for a clean string, so
    every assertion in this file survives it. That arm is a cost decision, not a
    correctness one, and the figures it rests on are recorded on
    ``_rendered_width`` itself rather than pinned here.
    """
    wider = {
        name
        for name, (sample, _) in RENDER_CLASSES.items()
        if validation._rendered_width(sample) > len(sample)
    }

    assert wider == ESCAPING_CLASSES, (
        f"the classes charged more than their own length are {sorted(wider)}, not the "
        f"{sorted(ESCAPING_CLASSES)} repr escapes. A class that left is one a len(value) "
        f"charge would now count correctly, which means it is no longer escaped; a class "
        f"that joined is one now charged for an escape repr does not render"
    )


def test_an_apostrophe_costs_two_characters_only_when_the_string_also_holds_a_quote() -> None:
    """The one character whose render depends on the rest of the string.

    ``repr`` picks its delimiter: a string carrying apostrophes and no double
    quote is rendered inside ``"``, so each apostrophe renders as itself, and a
    string carrying both is rendered inside ``'`` with every apostrophe escaped.
    A ``"`` is never escaped under either delimiter.

    This is why the fast path tests for ``"'" not in value`` and not for a
    double quote, and it is the case a per-character escape table gets wrong:
    the width is not a function of the code point alone. Both sides are asserted
    because only the pair distinguishes the rule from "apostrophes always cost
    two", which over-charges, or "apostrophes are free", which under-charges.
    """
    alone = validation._rendered_width("it's")
    beside_a_quote = validation._rendered_width("a'b\"c")

    assert alone == len("it's") == 4, (
        f"'it's' is charged {alone}; repr renders it as {'it' + chr(39) + 's'!r}, where the "
        f"apostrophe costs one character because repr switched to a double-quote delimiter"
    )
    assert beside_a_quote == 6, (
        f"'a\\'b\"c' is charged {beside_a_quote}, but repr renders it as "
        f"{'a' + chr(39) + 'b' + chr(34) + 'c'!r} -- six characters, because the string's own "
        f"double quote forces the apostrophe-delimited form and the apostrophe is escaped. "
        f"A charge that reads the apostrophe alone gets this one wrong by one character per "
        f"apostrophe, which a caller controls"
    )


def test_bytes_are_charged_for_their_escapes_rather_than_falling_through_uncounted() -> None:
    """The leaf type no parsed JSON request holds, charged soundly anyway.

    ``json.loads`` never produces ``bytes``, so nothing over the wire reaches
    this arm -- but it is the one leaf type whose escapes would otherwise be
    uncounted entirely: delete the arm and ``bytes`` falls past every branch to
    the ``return 0`` at the end, which charges a megabyte of NULs nothing at
    all. Driven so that the arm cannot be removed as dead code on the strength
    of its own unreachability.

    ``len(repr(value))`` over-charges by the ``b''`` delimiters, which is the
    safe direction, and is asserted as such rather than smoothed to an exact
    figure this function does not compute.
    """
    sample = b"\x00" * 16

    charge = validation._rendered_width(sample)

    assert charge == len(repr(sample)), (
        f"{sample!r} is charged {charge}, not the {len(repr(sample))} characters repr renders "
        f"it as. A charge of len(value) counts 16 for a leaf that renders as 64, and a charge "
        f"of 0 -- what removing this arm produces -- counts nothing at all"
    )
    assert charge > len(sample), charge


@pytest.mark.parametrize(("value", "rendered"), [(True, 4), (False, 5)])
def test_a_boolean_is_charged_the_width_of_its_own_literal(value: bool, rendered: int) -> None:
    """The arm that exists because ``bool`` is a subclass of ``int``.

    ``True.bit_length()`` is 1, so the integer arm below would charge a boolean
    one character for something that renders as four or five. The arm is matched
    *before* the integer one for that reason alone, and nothing else in the
    module would show it being reordered or deleted -- the numbers are small, so
    an under-charge here never moves a real request past the budget and the
    defect is silent by construction.

    Both values, because they render to different widths and a charge of a
    single constant would pass for one of them.
    """
    charge = validation._rendered_width(value)

    assert charge == rendered, (
        f"{value!r} is charged {charge}, not the {rendered} characters repr(value) renders. "
        f"A charge of 1 means bool is being matched by the int arm, whose bit_length "
        f"estimate does not describe a literal spelled out in letters"
    )


def _integer_sweep() -> list[int]:
    """Integers the charge must not under-count, chosen where the estimate is tightest.

    Three families. The small integers, because that is where
    ``(bit_length * 30103) // 100000`` floors to *zero* -- ``1`` has one
    bit_length and no digits by that product alone, so the ``+ 1`` is
    load-bearing there and nowhere a large-number sweep would look. Powers of
    ten, because each is the first integer of its digit count and so the tightest
    case for an over-estimate of ``log10(2)``. Powers of two, because they are
    the tightest case for the *bit* side of the same approximation.

    Every magnitude is kept under CPython's 4300-digit int-to-str limit, since
    ``len(str(n))`` past it raises the very cost this bound exists to refuse.
    """
    small = list(range(-2000, 2001))
    decimal = [10**exponent for exponent in range(0, 4001, 250)]
    binary = [2**exponent for exponent in range(0, 13001, 500)]
    magnitudes = decimal + binary
    return small + magnitudes + [-n for n in magnitudes]


def test_an_integer_is_never_charged_less_than_its_decimal_render() -> None:
    """The one property the integer estimate claims: it never under-charges.

    The charge is estimated from ``bit_length`` rather than measured with
    ``str(value)``, because ``str`` of a giant integer is quadratic and, past
    CPython's int-to-str limit, raises the very cost this bound exists to
    refuse. An estimate is only safe in one direction, so that direction is what
    is asserted -- ``>=``, not equality, since the estimate is deliberately
    loose.

    Swept in one test rather than parametrized: four thousand test ids for one
    property is noise in every run's report, and the sweep's job is to produce a
    counter-example, which a single assertion can name.
    """
    under_charged = [
        value for value in _integer_sweep() if validation._rendered_width(value) < len(str(value))
    ]

    assert under_charged == [], (
        f"the bit_length estimate charges less than the decimal render for "
        f"{len(under_charged)} of the swept integers, the first being {under_charged[:5]}. An "
        f"under-charging estimate lets a request past MAX_PARAMS_RENDERED_CHARS whose "
        f"jsonschema message is wider than the budget -- the estimate is allowed to be loose, "
        f"and allowed to be loose in one direction only"
    )


def test_the_integer_sweep_reaches_the_values_the_rounding_terms_are_there_for() -> None:
    """The control without which the sweep above proves nothing.

    "No value under-charged" is the shape of an assertion that passes when the
    sweep never reached the branch under test, and the integer arm has two
    terms whose whole purpose is a branch a large-number sweep does not visit.
    ``30103/100000`` rounds ``log10(2)`` up, so the product alone floors to zero
    for every integer below eight: the ``+ 1`` is what stops ``1`` being charged
    nothing, and it is load-bearing there rather than at the magnitudes a sweep
    of powers would probe. The sign's own ``+ 1`` is the same story mirrored.

    So the sweep is asserted to *contain* values where each term is what carries
    the estimate over the decimal render -- checked by recomputing the estimate
    without that term, here, rather than by trusting that a wide enough range
    must have included one.
    """
    swept = _integer_sweep()

    without_the_floor_term = [
        value
        for value in swept
        if value >= 0 and (value.bit_length() * 30103) // 100_000 < len(str(value))
    ]
    without_the_sign_term = [
        value
        for value in swept
        if value < 0 and (value.bit_length() * 30103) // 100_000 + 1 < len(str(value))
    ]

    assert without_the_floor_term, (
        "the sweep holds no non-negative integer whose digit count the bit_length product "
        "alone fails to cover, so dropping the `+ 1` from the estimate would leave the sweep "
        "above green and the charge under-counting"
    )
    assert without_the_sign_term, (
        "the sweep holds no negative integer whose rendered minus sign the estimate would "
        "fail to cover, so dropping the sign's own `+ 1` would leave the sweep above green"
    )


# -- The partition, over the space rather than over the representatives --------


def test_the_render_width_of_every_code_point_is_the_one_the_rule_predicts() -> None:
    """The exhaustive form of the per-class arms above.

    ``RENDER_CLASSES`` holds one representative per width and asks whether the
    implementation agrees with the recorded number for *that character*. This
    asks the same question of all 1,114,112 code points, against a rule written
    from CPython's documented ``repr`` behaviour -- four two-character escapes,
    then printable at one, then non-printable by plane -- rather than against
    the implementation or against the table.

    Two independent things can go wrong and only this notices either: a
    representative that stops representing its class, and a class whose rule is
    right about the sampled character and wrong elsewhere in its range. The
    ``\\xHH`` class is the one where that is easy -- it runs to U+00A0 and picks
    up U+00AD, neither of them adjacent to the C0 block a reader thinks of.
    """
    disagreements = sweep().width_disagreements

    assert disagreements == (), (
        "these code points are charged a width the documented rule does not predict "
        + ", ".join(
            f"U+{code_point:04X}: charged {measured}, rule says {predicted}"
            for code_point, measured, predicted in disagreements
        )
        + ". Either `_rendered_width` changed, or CPython's `repr` did, or the rule "
        "`expected_width` states no longer describes it -- and the per-class arms above "
        "are green either way, because they read one character per class"
    )


def test_the_width_classes_are_exactly_the_five_recorded_with_their_populations() -> None:
    """RED means the set of widths ``repr`` can charge has changed.

    The rule the arm above checks each code point against is a function; this is
    its *image*, which is the part the records quote. ``_rendered_width``'s own
    table says a code point costs 1, 2, 4, 6 or 10 -- five values, no others --
    and the composed-ceiling and pre-fix-reach figures on ADR-0031 are
    maximisations over exactly that set. A sixth width would make those figures
    understatements without changing any of the five.

    Populations as well as widths, because a width whose class emptied would
    still appear in the set as long as one character held it: the counts are
    what say the classes are the sizes the table describes.
    """
    assert sweep().width_counts == WIDTH_POPULATION, (
        f"the render widths over the code-point space are "
        f"{dict(sorted(sweep().width_counts.items()))}, "
        f"not the {WIDTH_POPULATION} recorded. A width that appeared is a class nothing "
        f"prices; a population that moved is characters crossing the printable boundary, "
        f"which moves what a body of them costs to render"
    )


def test_the_rule_the_sweep_checks_against_is_not_the_implementation() -> None:
    """The premise under both arms above: the two derivations are independent.

    A sweep comparing ``_rendered_width`` to a rule that called
    ``_rendered_width`` would report zero disagreements for any implementation
    at all -- the shape of a check that cannot fail. So the rule is exercised
    here on its own, against widths a reader can verify against CPython's
    documentation by eye, and the arms above are worth what they say only
    because this passes.
    """
    assert expected_width("\t") == 2
    assert expected_width("\\") == 2
    assert expected_width("a") == 1
    assert expected_width("\U0001f600") == 1
    assert expected_width("\x01") == 4
    assert expected_width("\xad") == 4
    assert expected_width("؀") == 6
    assert expected_width("\ud800") == 6
    assert expected_width("\U000e0001") == 10


def test_the_sweep_reaches_every_code_point() -> None:
    """The other premise: nothing was skipped.

    "No disagreements" is the assertion that passes most convincingly when the
    loop never ran, and the sweep quietly skips surrogates on its wire side --
    so the width side is asserted to have covered the whole space, surrogates
    included, by counting what it charged.
    """
    assert sum(sweep().width_counts.values()) == CODE_POINTS, (
        f"the width sweep charged {sum(sweep().width_counts.values())} code points, not the "
        f"{CODE_POINTS} that exist. A sweep that stopped early reports a clean partition over "
        f"whatever it reached"
    )
