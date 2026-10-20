"""ADR-0037's S3 residue must quote the rotted ``git grep -n accept`` re-check only as rotted.

At ``6c3c2827`` that command exits 0 on two content-type hits in
``application/okf_import.py``, so it cannot show that the module names ``accept``
nowhere (#965 item 9). The record quotes it, marked rotted.

Key: the ADR flattened by ``adr_0037_support.flowed``; every ``git grep -n [']accept``
occurrence must be followed, after its own closing backtick, by that command's
parenthetical ``(rotted:``, with no other backtick span between. Cannot see another
spelling of the re-check (``-nw``, ``rg``), nor another document.
"""

from __future__ import annotations

import re
from typing import Final

import pytest
from adr_0037_support import flowed

_RECHECK: Final = re.compile(r"git grep -n '?accept\b")
_MARK: Final = re.compile(r"[^`]*`[^`(]*\(rotted:")


def unmarked_rechecks(flat: str) -> list[str]:
    return [
        flat[m.start() : m.start() + 60]
        for m in _RECHECK.finditer(flat)
        if not _MARK.match(flat, m.end())
    ]


def test_adr_0037_names_the_accept_grep_only_as_rotted() -> None:
    text = flowed()

    assert _RECHECK.search(text), (
        "the key found no occurrence; if ADR-0037 no longer quotes the command, delete this pin"
    )
    assert not unmarked_rechecks(text)


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        ("re-check: `git grep -n accept -- a.py` exits 1.", True),
        ("re-check: `git grep -n 'accept' -- a.py` exits 1.", True),
        ("`git grep -n accept -- a.py` exits 1; the anchor table rotted once in #900.", True),
        ("`git grep -n accept -- a.py` exits 1 (unrotted at HEAD).", True),
        ("`git grep -n accept -- a.py` exits 1 (not rotted: re-run at abc1234).", True),
        ("`git grep -n accept -- a.py` exits 1; `git grep -n foo` exits 1 (rotted: x).", True),
        ("`git grep\n  -n accept -- a.py` exited 1 (rotted: it exits 0 now).", False),
        ("no mention of the command at all", False),
    ],
)
def test_the_comparison_flags_an_unmarked_recheck_and_passes_a_rotted_one(
    text: str, flagged: bool
) -> None:
    assert bool(unmarked_rechecks(" ".join(text.split()))) is flagged
