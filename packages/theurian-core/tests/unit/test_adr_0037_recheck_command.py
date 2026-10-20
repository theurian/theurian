"""ADR-0037's S3 residue must not name the rotted ``git grep -n accept`` re-check as live.

At ``6c3c2827`` that command exits 0 on two content-type hits in
``application/okf_import.py``, so it cannot show that the module names ``accept``
nowhere (#965 item 9). The record may still quote it, as rotted.

Key: whitespace-normalised ADR text, every ``git grep -n [']accept`` occurrence;
each must be followed within ``_WINDOW`` characters by ``rotted``. Cannot see
another spelling of the re-check (``-nw``, ``rg``), nor another document.
"""

from __future__ import annotations

import pathlib
import re
from typing import Final

import pytest

_ADR: Final = (
    next(
        p
        for p in pathlib.Path(__file__).resolve().parents
        if (p / "docs/adr/0037-okf-is-the-knowledge-layer-interchange.md").exists()
    )
    / "docs/adr/0037-okf-is-the-knowledge-layer-interchange.md"
)
_RECHECK: Final = re.compile(r"git grep -n '?accept\b")
_WINDOW: Final = 200


def unmarked_rechecks(text: str) -> list[str]:
    flat = " ".join(text.split())
    return [
        flat[m.start() : m.start() + 60]
        for m in _RECHECK.finditer(flat)
        if "rotted" not in flat[m.end() : m.end() + _WINDOW]
    ]


def test_adr_0037_names_the_accept_grep_only_as_rotted() -> None:
    text = _ADR.read_text(encoding="utf-8")

    assert _RECHECK.search(" ".join(text.split())), "the key found no occurrence to check"
    assert not unmarked_rechecks(text)


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        ("re-check: `git grep -n accept -- a.py` exits 1.", True),
        ("re-check: `git grep -n 'accept' -- a.py` exits 1.", True),
        ("`git grep\n  -n accept -- a.py` exited 1 (rotted: it exits 0 now).", False),
        ("no mention of the command at all", False),
    ],
)
def test_the_comparison_flags_an_unmarked_recheck_and_passes_a_rotted_one(
    text: str, flagged: bool
) -> None:
    assert bool(unmarked_rechecks(text)) is flagged
