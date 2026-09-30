"""ADR-0039's records: the amendments it made to ADR-0005 and ADR-0038, and the roadmap.

``test_adr_0039_claims.py`` names the rest of the pin and states its reach.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from adr_0037_support import collapsed
from adr_0039_support import (
    ADR,
    ADR_0005,
    ADR_0038,
    ADR_0039_LINK,
    REPO_ROOT,
    ROADMAP,
    _blockquotes,
    _git,
    _numbered,
    _section,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("path", [ADR_0005, ADR_0038], ids=["ADR-0005", "ADR-0038"])
def test_the_amended_record_carries_a_block_naming_adr_0039_and_lost_no_line(path: Path) -> None:
    relative = path.relative_to(REPO_ROOT).as_posix()
    base = _git("show", f"origin/main:{relative}").splitlines()
    working = iter(path.read_text(encoding="utf-8").splitlines())

    assert base, f"origin/main has no {relative}; `git fetch origin main` first"
    assert [
        block
        for block in _blockquotes(path)
        if block.startswith("Amended") and collapsed(ADR_0039_LINK) in block
    ]
    assert all(line in working for line in base), "a line of origin/main's record is gone"


def test_adr_0005s_amendment_rests_on_the_rules_it_cites() -> None:
    lines = ADR_0005.read_text(encoding="utf-8").splitlines()
    rules = _numbered(lines[lines.index("Rules the engine enforces:") + 2 :])
    [block] = _blockquotes(ADR_0005)

    assert rules[2].startswith("An applied migration is frozen.")
    assert (
        rules[8] == "Applying all migrations to an empty store reproduces the full canonical state."
    )
    assert "an applied migration is frozen (rule 2)" in block
    assert "an empty store replays every committed document (rule 8)" in block


def test_the_roadmap_records_adr_0039_against_candidate_three_and_section_four() -> None:
    marker = "Recorded: [ADR-0039](adr/0039-closed-set-extension-compatibility.md)"
    candidates = _numbered(_section("## 9. ADR candidates", ROADMAP))
    compatibility = collapsed(" ".join(_section("### Migration compatibility", ROADMAP)))

    assert candidates[3].startswith("The compatibility policy for extending a closed enum")
    assert marker in candidates[3]
    assert f"{marker}, which takes the first two clauses and declines the third" in compatibility
    assert (ROADMAP.parent / "adr" / ADR.name).is_file()
