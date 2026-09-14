"""The wire-expansion ratios ``MAX_REQUEST_BODY_BYTES``'s derivation records (#669).

``daemon/server.py`` does not pick its transport body cap; it derives it, as
``2 * MAX_SOURCE_FILE_BYTES + 1 MiB``. The multiplier is a **measurement** -- how
many bytes a body that lands at :data:`~theurian.security.paths.MAX_SOURCE_FILE_BYTES`
takes on the wire once ``json.dumps`` has escaped it -- and the constant's
docstring states four of those measurements as fact: two-byte-escapable text at
2.0x, ``ensure_ascii``-escaped CJK at 2.0x, C0-control-dense text at 6.0x, and
``ensure_ascii``-escaped astral characters at 3.0x. It states a remedy for one of
the two residual cases and no remedy for the other.

Until this file existed those were prose. Nothing recomputed them, so a CPython
or ``json`` change that moved an escape ratio -- or a reader who transcribed one
wrongly -- would leave the recorded decision describing a world that no longer
holds, silently, while the cap it justifies stayed where it was. **This file is
what makes that decision falsifiable**: each ratio is recomputed from live
``json.dumps`` behaviour and compared against the recorded factor, and the
consequence the factors buy -- that a body landing at the file cap still fits
the transport cap at the worst *realistic* expansion, with the envelope headroom
the addend names -- is recomputed from the measurement rather than restated.

A ratio moving out from under the recorded decision goes RED here. That is the
signal to re-measure and re-record the residual on the constant, not to retune a
number: which encodings can still meet the bare ``413`` at a landed size the
store would accept is exactly what these factors decide.

The wire-side counterpart is
``tests/integration/test_input_validation_dispatch.py``, which pins the formula
itself and drives the ``413`` boundary at exact bytes.
"""

from __future__ import annotations

import json
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

#: The encodings the constant calls realistic, each with the escape that makes
#: it so. A quote, a backslash, a newline and a tab are the characters
#: ``json.dumps`` gives a two-character escape; CJK is three UTF-8 bytes that
#: ``ensure_ascii`` renders as a six-character ``\\uXXXX``.
REALISTIC: Final = {
    "quote": '"',
    "backslash": "\\",
    "newline": "\n",
    "tab": "\t",
    "cjk": "日",
}

#: The two the constant records as a residual it has not closed: a C0 control
#: byte and an astral character.
RESIDUAL: Final = {"control": "\x01", "astral": "\U0001f600"}


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
    ``float`` so an assertion of "exactly 2.0x" is not an assertion about
    binary floating point."""
    return Fraction(_wire_bytes(sample, ensure_ascii=ensure_ascii), _landed_bytes(sample))


@pytest.mark.parametrize("name", sorted(REALISTIC))
def test_every_realistic_encoding_expands_to_exactly_twice_its_landed_bytes(name: str) -> None:
    """The multiplier in ``2 * MAX_SOURCE_FILE_BYTES`` is this measurement.

    Both realistic worst cases the constant names come out at exactly 2.0x, and
    that is why the cap is twice the file bound rather than some rounder number:
    a body that lands at :data:`MAX_SOURCE_FILE_BYTES` in any of these encodings
    arrives whole. Measured per character class rather than asserted once over a
    mixed sample, so a change to one escape is not hidden by an average.
    """
    sample = REALISTIC[name] * REPEAT

    expansion = _ratio(sample)

    assert expansion == 2, (
        f"{name} ({REALISTIC[name]!r}) now expands {float(expansion)}x on the wire, not the "
        f"2.0x MAX_REQUEST_BODY_BYTES is derived with. Its escape is "
        f"{json.dumps(REALISTIC[name])}. The multiplier in "
        f"2 * MAX_SOURCE_FILE_BYTES no longer covers this encoding, so a body that lands "
        f"at the file cap can meet the bare 413 -- re-measure and re-record the decision "
        f"on the constant rather than editing this number"
    )


@pytest.mark.parametrize(("name", "factor"), [("control", 6), ("astral", 3)])
def test_the_two_encodings_the_residual_names_expand_past_the_multiplier(
    name: str, factor: int
) -> None:
    """The residual the constant records as open, measured rather than asserted.

    A C0 control byte becomes a six-character ``\\u0001`` and an astral
    character's four UTF-8 bytes become a twelve-character surrogate pair, so
    both exceed the 2.0x the cap is sized for: a body dense in either can still
    meet the bare ``413`` at a landed size the store would accept. Pinned so the
    recorded residual cannot quietly become wrong in either direction -- larger,
    and the reach of the gap is understated; smaller or gone, and the constant
    is warning about something that no longer happens.
    """
    sample = RESIDUAL[name] * REPEAT

    expansion = _ratio(sample)

    assert expansion == factor, (
        f"{name} ({RESIDUAL[name]!r}) now expands {float(expansion)}x, not the {factor}.0x "
        f"MAX_REQUEST_BODY_BYTES records as a residual. Whichever direction it moved, the "
        f"reach of the gap between a landed-size cap and this transport cap moved with it "
        f"and owes a re-measurement on the constant"
    )


def test_the_recorded_remedy_holds_for_the_astral_case_and_none_exists_for_control_bytes() -> None:
    """The asymmetry a reader would act on: one residual has a way out, one does not.

    The constant tells a caller meeting the ``413`` on astral text to send the
    body as raw UTF-8 instead of ``ensure_ascii``-escaped, and says the
    control-character case has no such remedy because JSON requires C0 escaping
    whatever the encoder is set to. Both halves are measured here, because a
    remedy that stopped working is worse than no remedy: it sends someone to
    retry a request that will be refused again.
    """
    astral = RESIDUAL["astral"] * REPEAT
    control = RESIDUAL["control"] * REPEAT

    with_remedy = _ratio(astral, ensure_ascii=False)
    without_one = _ratio(control, ensure_ascii=False)

    assert with_remedy == 1, (
        f"sending astral text as raw UTF-8 now expands {float(with_remedy)}x, so the remedy "
        f"MAX_REQUEST_BODY_BYTES records no longer removes the expansion it claims to"
    )
    assert without_one == 6, (
        f"a C0 control byte expands {float(without_one)}x even with ensure_ascii off, and the "
        f"constant says it stays 6.0x because JSON requires the escape whatever the encoder "
        f"is set to; a change here means a remedy now exists and is not recorded"
    )


def test_a_body_landing_at_the_file_cap_fits_the_transport_cap_with_the_envelope_headroom() -> None:
    """What the measured factors buy, recomputed from them rather than restated.

    This is the derivation's consequence and the reason #669's fix is sized the
    way it is: at the worst expansion any *realistic* encoding produces, a body
    that lands at :data:`MAX_SOURCE_FILE_BYTES` -- the cap ADR-0032 decision 3
    puts on the file a proposal writes -- still arrives, and what is left over
    is exactly the 1 MiB the constant sets aside for the JSON-RPC frame around
    it.

    The worst case is taken from the live measurement, not from the literal
    ``2``, so this fails both when an escape ratio moves and when the constant's
    own multiplier or addend does. Equality on the headroom rather than
    ``>=``: "there is some room left" passes for a cap ten times too large and
    would not notice the addend being changed.
    """
    worst_realistic = max(_ratio(character * REPEAT) for character in REALISTIC.values())

    on_the_wire = worst_realistic * MAX_SOURCE_FILE_BYTES

    assert on_the_wire <= MAX_REQUEST_BODY_BYTES, (
        f"a body landing at MAX_SOURCE_FILE_BYTES ({MAX_SOURCE_FILE_BYTES}) takes "
        f"{on_the_wire} bytes on the wire at the worst realistic expansion "
        f"({float(worst_realistic)}x), past the transport cap ({MAX_REQUEST_BODY_BYTES}), so "
        f"the write-intent body ADR-0032 sizes for meets a bare 413 that names no tool -- "
        f"#669's defect, re-opened"
    )
    assert MAX_REQUEST_BODY_BYTES - on_the_wire == 1024 * 1024, (
        f"the headroom left for the JSON-RPC envelope is "
        f"{MAX_REQUEST_BODY_BYTES - on_the_wire} bytes, not the 1 MiB "
        f"MAX_REQUEST_BODY_BYTES's addend records. Either the addend moved or the worst "
        f"realistic expansion did; the constant's docstring states both, so one of them is "
        f"now wrong there"
    )
