"""ADR-0039's records: the amendments it made to ADR-0005 and ADR-0038, and the roadmap.

``test_adr_0039_claims.py`` names the rest of the pin and states its reach.
"""

from __future__ import annotations

import re
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
    _table,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("path", [ADR_0005, ADR_0038], ids=["ADR-0005", "ADR-0038"])
def test_the_amended_record_carries_a_block_naming_adr_0039_and_lost_no_line(path: Path) -> None:
    """A standing append-only guard against live ``origin/main``, not a check of this PR.

    On a branch it goes RED when a line ``origin/main`` holds has gone. On ``main``
    the working file *is* ``origin/main``, so there it cannot fail.
    """
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
    [block] = [block for block in _blockquotes(ADR_0005) if collapsed(ADR_0039_LINK) in block]

    assert rules[2].startswith("An applied migration is frozen.")
    assert (
        rules[8] == "Applying all migrations to an empty store reproduces the full canonical state."
    )
    assert "an applied migration is frozen (rule 2)" in block
    assert "an empty store replays every committed document (rule 8)" in block


def _adr_0038_block() -> list[str]:
    """The bullets of ADR-0038's amendment block, above its *Context*, each collapsed."""
    head = ADR_0038.read_text(encoding="utf-8").split("\n## Context\n", 1)[0].splitlines()
    block = "\n".join(line.removeprefix(">") for line in head if line.startswith(">"))
    return [collapsed(bullet) for bullet in re.split(r"\n\s*- ", block)[1:]]


def _version_claim_sites() -> dict[str, str]:
    [adr_0005] = [block for block in _blockquotes(ADR_0005) if collapsed(ADR_0039_LINK) in block]
    return {
        "ADR-0039 decision 4": _numbered(_section("## Decision"))[4],
        "ADR-0005's amendment": adr_0005,
        "ADR-0038's first bullet": _adr_0038_block()[0],
    }


@pytest.mark.parametrize(
    "site", ["ADR-0039 decision 4", "ADR-0005's amendment", "ADR-0038's first bullet"]
)
def test_each_version_claim_names_both_versions_it_leaves_alone(site: str) -> None:
    """An unqualified "bumps nothing" read as leaving the engine version alone too."""
    text = _version_claim_sites()[site]

    assert "bumps neither apiVersion nor protocolVersion" in text
    assert not re.search(r"bumps (?:nothing|no version)", text)


def test_the_adr_0038_block_narrows_what_adr_0038_says() -> None:
    """Each bullet names ADR-0038's own decision, alternative or Still owed text."""
    bullets = _adr_0038_block()
    adr_0038 = collapsed(ADR_0038.read_text(encoding="utf-8"))
    decisions = _numbered(_section("## Decision", ADR_0038))
    rows = _table(_section("## Alternatives considered", ADR_0038))
    [reinterpret] = [row[1] for row in rows if row[0].startswith("**Retire the two operations")]
    still_owed = ADR_0038.read_text(encoding="utf-8").split("Still owed, with the issue", 1)[1]
    owed_2 = collapsed(_numbered(still_owed.splitlines()[2:])[2])
    [narrowing] = [
        bullet for bullet in bullets if bullet.startswith("Compliance, Still owed item 2")
    ]

    assert [bullet.split(".", 1)[0] for bullet in bullets] == [
        'Negative, first item, "Both halves of the fold are protocol changes"',
        "Decision 4's precondition is met: #274's policy is accepted, as ADR-0039",
        "Decision 5 and rejected alternative (b) are narrowed by ADR-0039 decision 6",
        "Compliance, Still owed item 1, is discharged by ADR-0039",
        "Compliance, Still owed item 2, is narrowed by ADR-0039 decisions 4 and 6, in five places",
    ]
    assert decisions[5].startswith("The two operations are not retired by reinterpreting them")
    assert "**(b)**" in reinterpret
    assert "does not tell a reader of the document which meaning it carries" in adr_0038
    assert (
        len(re.findall(r"Its .*? becomes:|its removal of the specifications table", narrowing)) == 5
    )
    for quoted in (
        "remove registerSpecification and supersedeSpecification from OperationKind, the "
        "migration schema, the loader and V1_OPERATION_KINDS",
        "SpecId, SpecificationStatus",
        "amend ADR-0005's operation list",
        "move every committed document and fixture that names either operation",
        "the sample project's migration among them",
        "remove the entity, the specifications table",
    ):
        assert quoted in owed_2, f"ADR-0038's Still owed item 2 no longer says {quoted!r}"


def test_the_roadmap_records_adr_0039_against_candidate_three_and_section_four() -> None:
    marker = "Recorded: [ADR-0039](adr/0039-closed-set-extension-compatibility.md)"
    candidates = _numbered(_section("## 9. ADR candidates", ROADMAP))
    compatibility = collapsed(" ".join(_section("### Migration compatibility", ROADMAP)))

    assert candidates[3].startswith("The compatibility policy for extending a closed enum")
    assert marker in candidates[3]
    assert f"{marker}, which takes the first two clauses and declines the third" in compatibility
    assert (ROADMAP.parent / "adr" / ADR.name).is_file()
