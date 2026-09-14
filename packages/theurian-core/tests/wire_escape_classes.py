"""The wire-escape classes ``MAX_REQUEST_BODY_BYTES``'s derivation is measured over.

``daemon/server.py`` derives its transport body cap as
``3 * MAX_SOURCE_FILE_BYTES + 1 MiB``, and the ``3`` is a **measurement**: the
worst ratio of wire bytes to landed UTF-8 bytes any non-control text reaches
once ``json.dumps`` has escaped it. That measurement is taken over a population,
and the population is what this module holds -- one representative per class,
partitioned by what actually decides the ratio.

**Here rather than inside one test module because two now read it**, which is the
same reason ``threat_model_claims`` gives for its own contents. ``tests/unit/
test_transport_body_cap.py`` is the fact side: it measures each class through
``json.dumps``, checks each recorded factor against what the character's own
encoding derives, and holds :data:`RESIDUAL_CLASSES` *equal* to the set that
measures above the multiplier. ``tests/integration/test_sec12_shipped_claims.py``
is the record side: the threat model's T-11 residual states how many classes
exceed the multiplier and at what factors, and that pin reads the count and the
factors from here rather than transcribing them. A copy in the second module
would be a second thing to keep in step, and the one that drifted would be the
one nobody was reading.

**Why the earlier population was wrong**, recorded because the shape of the
mistake is the reason this module is a partition rather than a list. The cap was
first sized at ``2 *`` from a sample of encodings someone judged realistic --
quote, backslash, newline, tab, CJK -- all of which happen to measure 2.0x. The
ratio is a property of a character's UTF-8 length, not of how ordinary its
script is: ordinary Cyrillic, Greek, Hebrew and Arabic prose expands at 3.0x, so
a body of it landing at the file cap met a bare ``413`` that named no tool. A
table built per *class* cannot make that mistake, because a class nobody
remembered is a class with no row.

Pure data. Nothing here reads a file, a socket or the clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class WireClass:
    """One wire-escape class, its representative, and the factors recorded for it.

    ``escaped`` and ``raw`` are the two columns
    :data:`~theurian.daemon.server.MAX_REQUEST_BODY_BYTES`'s docstring states as
    wire bytes over landed UTF-8 bytes, under ``ensure_ascii=True`` (the stdlib
    default, and what the escaping clients emit) and under raw UTF-8 (what the
    official Python and JS clients emit).

    Whole numbers, because every class comes out at one: a factor that stopped
    being whole would mean the partition has stopped partitioning, and
    ``test_transport_body_cap.py`` compares against these as exact ratios rather
    than as approximations.
    """

    character: str
    escaped: int
    raw: int


#: Every class a character can fall into on this wire, one representative each.
#: Partitioned by what decides the ratio -- the character's UTF-8 length and
#: whether JSON or ``ensure_ascii`` must escape it -- so the population is
#: complete by construction rather than by whoever last remembered a script.
#: Which of these rows the derivation records as its residual is
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
#: control rows, and only those. Asserted as a set by
#: ``test_transport_body_cap.py``, so a third class rising past the multiplier
#: fails there rather than being absorbed.
RESIDUAL_CLASSES: Final = frozenset({"c0_other", "delete"})

#: The classes the ``3 *`` multiplier is derived over -- everything the residual
#: does not name. A knowledge store lands text, not control bytes, which is why
#: the cap is sized for this population and the residual is recorded rather than
#: paid for.
COVERED_CLASSES: Final = frozenset(WIRE_CLASSES) - RESIDUAL_CLASSES
