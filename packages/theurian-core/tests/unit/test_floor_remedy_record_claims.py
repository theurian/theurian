"""Holds the records of #915's kind-routed floor remedy to the code that prints it.

Reads `docs/protocol/migrations.md`, `plugins/claude-code/commands/propose.md` and T-28
residual 11, against `_REFUSED_TO_CONTENT_PATH`, `item_labels` and `_floor_refusal`.
"""

from __future__ import annotations

import ast
import re
from typing import Final

import pytest
from floor_remedy_support import TRACKED_LOCATION
from threat_model_claims import SPELLED_NUMBERS, entry_in
from write_lock_claims import REPO_ROOT

from theurian.application.item_labels import (
    ACCEPT_READMISSION_REMEDY,
    DECLASSIFY_REMEDY,
    DRAFT_AGAIN_CLAUSE,
    READMIT_REMEDY,
)
from theurian.application.proposal_service import _REFUSED_TO_CONTENT_PATH, _floor_refusal
from theurian.domain.enums import KnowledgeStatus, Sensitivity
from theurian.domain.identifiers import ItemId

pytestmark = pytest.mark.unit

_MIG, _PROPOSE = "docs/protocol/migrations.md", "plugins/claude-code/commands/propose.md"
_TM = "docs/security/threat-model.md"
_ITEM = ItemId("architecture.a")


def _flat(rel: str) -> str:
    return re.sub(r"\s+", " ", (REPO_ROOT / rel).read_text(encoding="utf-8"))


def _residual_eleven() -> str:
    block = entry_in((REPO_ROOT / _TM).read_text(encoding="utf-8"), "T-28")
    residuals = block.split("**Residuals.**", 1)[1].split("**A known cost", 1)[0]
    return re.sub(r"\s+", " ", re.split(r"(?m)^\d+\. ", residuals)[11])


_STALE_PLACEHOLDER: Final = re.compile(r"dependsOn:\s*\[<its id>\]")


def test_no_record_edits_a_placeholder_into_a_drafts_dependson() -> None:
    """A tool extracting ids from a remedy read `<its id>` as one on main 4d42fe6f (#915); the
    records now say "that migration's id". ADR-0032 keeps the old spelling as history, so it
    is not read, and `dependsOn: [<id>]` (a migration declaring it) stays legitimate."""
    sample = "edit `dependsOn: [<its id>]` into"

    assert _STALE_PLACEHOLDER.search(sample)
    assert not [rel for rel in (_MIG, _PROPOSE) if _STALE_PLACEHOLDER.search(_flat(rel))]


_PAIR: Final = re.compile(r"(?:of|besides) `(\w+)` and `(\w+)`")


def test_the_kinds_the_records_call_content_only_are_the_ones_the_code_routes_so() -> None:
    """Widening `_REFUSED_TO_CONTENT_PATH` changes who is told to redraft; a record naming two
    kinds would then misdirect. Keyed on `(of|besides) `A` and `B``: four sites in
    migrations.md and one in residual 11 at this commit."""
    code = {k.value for k in _REFUSED_TO_CONTENT_PATH}

    pairs = {
        _MIG: _PAIR.findall(_flat(_MIG)),
        _TM: _PAIR.findall(_residual_eleven()),
    }

    assert {rel: len(found) for rel, found in pairs.items()} == {_MIG: 4, _TM: 1}
    assert [set(p) for found in pairs.values() for p in found] == [code] * 5


def test_migrations_md_quotes_the_dependson_sentence_the_content_remedies_end_with() -> None:
    """The clause is the sentence a reader follows to place a redraft after a landed migration;
    a quote that drifted from it sends them to a different file edit."""
    tail = DRAFT_AGAIN_CLAUSE.rsplit(", so ", 1)[1]
    text = _flat(_MIG)

    assert ACCEPT_READMISSION_REMEDY.endswith(tail)
    assert tail.startswith("edit that migration's id into the `dependsOn` of")
    assert f'"{tail}"' in text
    assert f'"{tail.rstrip(".")}"' in text
    assert text.count(tail.rstrip(".")) == 3


@pytest.mark.parametrize(
    ("lowered", "readmitted", "tail"),
    [
        (((_ITEM, Sensitivity.CONFIDENTIAL, Sensitivity.INTERNAL),), (), DECLASSIFY_REMEDY),
        ((), ((_ITEM, KnowledgeStatus.DEPRECATED, KnowledgeStatus.APPROVED),), READMIT_REMEDY),
    ],
    ids=["lowering", "readmission"],
)
def test_migrations_md_quotes_the_removal_remedy_the_floor_prints(
    lowered: tuple[tuple[ItemId, Sensitivity, Sensitivity], ...],
    readmitted: tuple[tuple[ItemId, KnowledgeStatus, KnowledgeStatus], ...],
    tail: str,
) -> None:
    """The quote is the cure a reader performs on a file and a directory. The doc code-formats
    its directory placeholder (the remedy prints the path bare) and the id sits in the remedy's own
    code span; what follows the cure is the hand-written step."""
    where = TRACKED_LOCATION
    text = _flat(_MIG)
    quoted = re.search(r'It reads "(.*?)", with an "every operation whose', text)
    assert quoted, "the removal remedy is no longer quoted"
    quote = (
        quoted[1].replace("<id>", _ITEM.value).replace("`<proposal dir>/`", where.relative + "/")
    )
    refusal = _floor_refusal(lowered, readmitted, frozenset({"changeSensitivity"}), where)
    assert refusal is not None

    assert refusal.remedy == f"{quote} {tail}"


def test_migrations_md_names_the_hand_written_operation_each_remedy_tail_sends_to() -> None:
    """The record says which operation follows the cure; the tails are what name it."""
    lowering, readmission = (
        re.search(r"`(\w+)` operation", tail) for tail in (DECLASSIFY_REMEDY, READMIT_REMEDY)
    )
    assert lowering and readmission

    assert (
        f"the hand-written `{lowering[1]}` for a lowering and `{readmission[1]}` for a readmission"
        in _flat(_MIG)
    )


def _routing_kind_sets() -> int:
    """The entries of the two dicts `test_floor_remedy_routing.py` builds; not importable
    (`tests/unit` is not on `sys.path`), so read from its source."""
    source = (
        REPO_ROOT / "packages/theurian-core/tests/unit/test_floor_remedy_routing.py"
    ).read_text(encoding="utf-8")
    sizes = {
        target.id: len(node.value.keys)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.AnnAssign)
        and isinstance(target := node.target, ast.Name)
        and target.id in {"_CONTENT_KINDS", "_OTHER_KINDS"}
        and isinstance(node.value, ast.Dict)
    }
    assert sorted(sizes) == ["_CONTENT_KINDS", "_OTHER_KINDS"]
    return sum(sizes.values())


def test_migrations_md_counts_the_kind_sets_the_routing_test_builds() -> None:
    """ "ten kind sets" is the population `_every_remedy` drives; adding a set without
    recounting leaves the record claiming less coverage than the test has."""
    said = re.search(r"for (\w+) kind sets", _flat(_MIG))
    assert said, "the kind-set count is no longer stated"

    assert SPELLED_NUMBERS[said[1]] == _routing_kind_sets()
