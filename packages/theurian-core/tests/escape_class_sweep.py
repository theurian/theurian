"""The escape classes, measured over the code-point space rather than over a table.

``wire_escape_classes`` records what each class costs. This module measures what
the *space* does, one code point at a time, and it exists because the two are
different claims and only the second one is exhaustive.

**The gap this closes.** A table of representatives is checked by measuring its
representatives, and that check is green no matter how badly a representative
misrepresents its class: file a Cyrillic character under ``three_byte`` and both
the measurement and the derivation agree with each other, because both read the
same wrong character. Five such mutations survived a round of review on exactly
that seam. What cannot survive it is a partition defined over the space and
applied to every code point: the class a character belongs to is then computed
from the character, the table's factors are asserted against *every* member, and
a representative filed under the wrong name is a character whose measured class
is not the one it sits in.

So :func:`class_of` is the definition -- written from the JSON grammar and the
UTF-8 encoding, naming no representative -- and :func:`sweep` applies it to all
1,114,112 code points.

**What the sweep cannot see, and what covers it.** Two classes that record the
same factor pair are indistinguishable by measurement: move the boundary between
them and every member still measures its row's figures, because the figures are
the same on both sides. Two such pairs exist -- ``json_escapable`` and
``short_control_escape`` at ``(2, 2)``, and ``two_byte`` and ``astral`` at
``(3, 1)`` -- so those two lines rest on :func:`class_of` and on the byte-length
arms, not on anything the factors say. The second is the one a review mutation
walked through: a CJK character filed in the ``astral`` row leaves both rows
measuring correctly and removes four-byte text from the population.
``test_transport_body_cap.test_no_two_rows_record_the_same_pair_of_factors``
holds the set of collisions at the two that are known, so a *new* one cannot
appear without someone stating which boundary has stopped being observable and
what holds it instead.

**Cost, measured 2026-09-15 on CPython 3.13:** ~2 s for the whole sweep, paid
once per process through :func:`functools.cache` and shared by every module that
asks. That is slow for a unit test and cheap for an exhaustive one; the
alternative is a stratified sample, which is what the representatives already
were.

**The encoder entry points are ``json.dumps``'s own.** ``JSONEncoder`` dispatches
a bare string to :func:`json.encoder.encode_basestring_ascii` or
:func:`~json.encoder.encode_basestring` and does nothing else to it, so calling
them directly measures the same thing several times faster.
:func:`test_transport_body_cap.test_the_sweep_encodes_the_way_json_dumps_does`
holds that equality over the whole space rather than leaving it as a reading.

Pure: no file, socket, clock or temporary directory.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from json.encoder import encode_basestring, encode_basestring_ascii
from types import MappingProxyType
from typing import Final

from theurian.mcp.validation import _rendered_width

#: Every code point, surrogates included. The sweep reports the surrogates
#: separately rather than skipping them silently, because "UTF-8 cannot carry
#: them" is itself one of the facts the records rest on.
CODE_POINTS: Final = 0x110000

#: The surrogate range, which no UTF-8 encoder will emit. ``repr`` renders a
#: lone surrogate perfectly well -- that is why the charge has to cover it -- so
#: it is in the width sweep and out of the wire sweep.
SURROGATES: Final = range(0xD800, 0xE000)

#: The seven characters JSON renders as a two-character escape whatever the
#: encoder is set to: the quote, the backslash, and the five short control
#: escapes. The grammar's own list, not a class.
JSON_SHORT_ESCAPES: Final = frozenset('"\\\b\t\n\f\r')

#: The four characters ``repr`` renders as a two-character escape. Not the same
#: seven: ``repr`` spells ``\b`` and ``\f`` as ``\x08`` and ``\x0c``, and it
#: escapes a ``"`` never and an apostrophe only in a string that also holds a
#: ``"``. The two sets differing is the whole reason the wire table and the
#: width table are separate measurements.
REPR_SHORT_ESCAPES: Final = frozenset("\t\n\r\\")


#: Class name per UTF-8 byte length, for the characters JSON leaves alone.
_BY_LANDED_BYTES: Final = {
    1: "printable_ascii",
    2: "two_byte",
    3: "three_byte",
    4: "astral",
}


def landed_bytes(code_point: int) -> int:
    """How many UTF-8 bytes ``code_point`` lands as, by arithmetic.

    ``chr(cp).encode()`` answers the same question and allocates twice to do it;
    over a million code points that is most of this module's runtime.
    :func:`test_transport_body_cap.test_the_sweep_encodes_the_way_json_dumps_does`
    holds the two equal over the whole space, so the arithmetic is checked rather
    than assumed.
    """
    if code_point < 0x80:
        return 1
    if code_point < 0x800:
        return 2
    return 3 if code_point < 0x10000 else 4


def class_of(character: str) -> str:
    """Which wire-escape class ``character`` belongs to, from the character alone.

    The partition, written from the two rules that decide a wire ratio -- what
    JSON must escape, and how many UTF-8 bytes the character lands as -- and
    naming no representative. ``wire_escape_classes.WIRE_CLASSES`` is then a
    claim *about* this partition, which is what makes a misfiled representative
    detectable: its measured class is the one this function computes, not the
    one it is filed under.

    Raises on a surrogate rather than classifying one, because the wire classes
    are ratios over landed UTF-8 bytes and a surrogate has none.
    """
    code_point = ord(character)

    if code_point in SURROGATES:
        raise ValueError(
            f"U+{code_point:04X} is a surrogate; UTF-8 carries none of them, so it has no "
            f"landed byte count and belongs to no wire class. The width sweep covers it; "
            f"this one does not."
        )
    if character in JSON_SHORT_ESCAPES:
        return "json_escapable" if character in '"\\' else "short_control_escape"
    if code_point < 0x20:
        return "c0_other"
    if code_point == 0x7F:
        return "delete"
    return _BY_LANDED_BYTES[landed_bytes(code_point)]


def expected_width(character: str) -> int:
    """How many characters ``character`` contributes to a ``{instance!r}`` render.

    ``_rendered_width``'s recorded table, written as a rule over the space
    instead of as a list of classes: the four characters ``repr`` gives a
    two-character escape, then everything printable at one, then the
    non-printables by plane -- ``\\xHH`` below U+0100, ``\\uXXXX`` below
    U+10000, ``\\UXXXXXXXX`` above it.

    Independent of the implementation on purpose. Asserting the implementation
    against itself proves nothing; asserting it against a rule a reader can check
    against CPython's documentation is what makes the recorded table falsifiable.
    """
    if character in REPR_SHORT_ESCAPES:
        return 2
    if character.isprintable():
        return 1
    code_point = ord(character)
    if code_point < 0x100:
        return 4
    return 6 if code_point < 0x10000 else 10


def wire_widths(character: str, landed: int) -> tuple[int, int]:
    """``(escaped, raw)`` wire byte counts for one non-surrogate character.

    The escaped form is pure ASCII, so its character count is its byte count.
    The raw form is the character itself unless JSON forces an escape, and the
    escape is ASCII too -- so one comparison replaces an encode.
    """
    escaped = len(encode_basestring_ascii(character)) - 2
    emitted = encode_basestring(character)[1:-1]
    return escaped, landed if emitted == character else len(emitted)


@dataclass(frozen=True, slots=True)
class Sweep:
    """What one pass over the code-point space measured.

    Aggregates rather than per-code-point records: the width classes alone would
    be a million integers, and every question asked of this sweep is a question
    about a class.
    """

    #: Class name to the ``(escaped, raw)`` factor pairs its members measure at,
    #: as exact ``Fraction``-free integer ratios ``(wire, landed)``.
    factors: Mapping[str, frozenset[tuple[tuple[int, int], tuple[int, int]]]]
    #: Render width to how many code points render at it.
    width_counts: Mapping[int, int]
    #: Code points whose measured width disagrees with :func:`expected_width`,
    #: first few only -- a failure needs an example, not a million of them.
    width_disagreements: tuple[tuple[int, int, int], ...]
    #: The distinct ``(raw wire bytes, render width)`` pairs a legal-raw code
    #: point can present. What the worst pre-#669 render is maximised over.
    reach_pairs: frozenset[tuple[int, int]]


@cache
def sweep() -> Sweep:
    """One pass over every code point, cached for the process.

    Cached because three modules ask and the answer cannot change inside a run:
    it is a property of CPython's ``json`` encoder, of UTF-8, and of
    ``_rendered_width``, none of which this suite mutates.
    """
    factors: dict[str, set[tuple[tuple[int, int], tuple[int, int]]]] = {}
    width_counts: dict[int, int] = {}
    disagreements: list[tuple[int, int, int]] = []
    reach_pairs: set[tuple[int, int]] = set()

    for code_point in range(CODE_POINTS):
        character = chr(code_point)

        measured = _rendered_width(character)
        width_counts[measured] = width_counts.get(measured, 0) + 1
        if measured != expected_width(character) and len(disagreements) < 8:
            disagreements.append((code_point, measured, expected_width(character)))

        if code_point in SURROGATES:
            continue
        landed = landed_bytes(code_point)
        escaped, raw = wire_widths(character, landed)
        factors.setdefault(class_of(character), set()).add(((escaped, landed), (raw, landed)))
        reach_pairs.add((raw, measured))

    # Read-only views, because this result is cached for the process and shared
    # by four modules: a plain dict handed to several callers is one `.pop()` in
    # one arm away from changing what every later arm measures, and the failure
    # would land in whichever module ran next.
    return Sweep(
        factors=MappingProxyType({name: frozenset(pairs) for name, pairs in factors.items()}),
        width_counts=MappingProxyType(dict(width_counts)),
        width_disagreements=tuple(disagreements),
        reach_pairs=frozenset(reach_pairs),
    )
