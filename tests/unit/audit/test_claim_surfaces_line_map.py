"""Every audit row names a place a person can open, so the line map has to be exact.

``claim_surfaces._collapse`` joins a block's source lines into one
whitespace-collapsed string and returns, beside it, the source line each
character came from. That map is the *only* thing turning a match in the joined
text back into a file and a line, and every one of the five audits in
``tools/audit/`` prints its rows from it. A map that drifts does not fail
loudly: the audit still exits 0 or 1 for the right reason, and the row it prints
points a reader at the wrong paragraph.

Round one's M-e was that the rebuild matched **characters** -- for each character
of the collapsed string it scanned forward in the joined string until it found
that same character. When a whitespace run collapsed to one space it went looking
for a literal ``" "``, and a run made of tabs has none, so it skipped past the
tabs to the next real space and every attribution after that point was off by a
line.

These tests are ``unit``: pure functions over strings, no filesystem and no
subprocess.
"""

from __future__ import annotations

import re

import pytest
from claim_surfaces import _collapse

pytestmark = pytest.mark.unit


def _word_lines(text: str, origin: list[int]) -> dict[str, int]:
    """Each word of the collapsed text mapped to the source line it is attributed to.

    Every case below uses distinct words, so a repeated key cannot silently
    overwrite an earlier attribution and turn a drifted map into a passing dict.
    """
    return {match.group(): origin[match.start()] for match in re.finditer(r"\w+", text)}


def test_a_tab_run_inside_one_source_line_does_not_shift_the_lines_after_it() -> None:
    """RED means every audit row after a tabbed line names the wrong paragraph.

    Two tabs collapse to one space, so the joined and collapsed strings differ in
    length and the rebuild runs. Under the character-matching rebuild this exact
    input reported ``beta`` -- typed on line 10 -- at line 11, and carried that
    off-by-one through the rest of the block.
    """
    lines = [(10, "alpha\t\tbeta gamma"), (11, "delta epsilon")]

    text, origin = _collapse(lines)

    assert _word_lines(text, origin) == {
        "alpha": 10,
        "beta": 10,
        "gamma": 10,
        "delta": 11,
        "epsilon": 11,
    }, f"the line map drifted across the tab run: {text!r} -> {origin}"


def test_the_line_map_has_exactly_one_entry_per_collapsed_character() -> None:
    """RED means an index into the collapsed text can fall off the end of the map.

    The map is consumed as ``origin[start]`` where ``start`` is an offset into
    the collapsed string, so the two have to be the same length for every input.
    A rebuild that emitted one entry per *joined* character would be longer and
    silently misattribute; one that emitted too few would raise ``IndexError``
    from inside a sweep.
    """
    lines = [(3, "one\t\t\ttwo   three"), (4, "four\tfive"), (5, "six")]

    text, origin = _collapse(lines)

    assert len(origin) == len(text), (
        f"the line map has {len(origin)} entries for {len(text)} collapsed characters: {text!r}"
    )


def test_a_single_tab_is_attributed_to_its_own_line() -> None:
    """RED means the fast path is back, and it reads length rather than structure.

    One tab collapses to one space, so the joined and collapsed strings are the
    same length. The old code returned the unrebuilt map on that test -- correct
    here by luck, since one character still maps to one character, and the reason
    the defect above needed *two* tabs to show itself. There is one path now, and
    this is the input that would notice a fast path reappearing beside a broken
    rebuild.
    """
    lines = [(20, "alpha\tbeta"), (21, "gamma")]

    text, origin = _collapse(lines)

    assert _word_lines(text, origin) == {"alpha": 20, "beta": 20, "gamma": 21}


def test_blank_lines_contribute_nothing_and_do_not_break_the_map() -> None:
    """RED means a paragraph containing an empty line misattributes everything after it.

    ``_collapse`` skips a line that strips to nothing rather than emitting a
    space for it, so the separator inserted between two kept lines is the only
    whitespace at that seam. A rebuild that assumed one entry per input line
    would be off by one for every block a reader's editor left a trailing blank
    inside.
    """
    lines = [(7, "opening sentence"), (8, "   "), (9, "closing remark")]

    text, origin = _collapse(lines)

    assert text == "opening sentence closing remark"
    assert _word_lines(text, origin) == {
        "opening": 7,
        "sentence": 7,
        "closing": 9,
        "remark": 9,
    }
