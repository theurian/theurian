"""A readmitted item is not sent back to a draft by the floor refusal (GHSA-v2qg-23fc-7fqp)."""

from __future__ import annotations

from pathlib import Path

import pytest

from theurian.application.item_labels import (
    ACCEPT_LOWERING_REMEDY,
    ACCEPT_READMISSION_REMEDY,
    DRAFT_AGAIN_CLAUSE,
)
from theurian.application.proposal_service import _floor_refusal, _ProposalLocation
from theurian.domain.enums import KnowledgeStatus, Sensitivity
from theurian.domain.identifiers import ItemId

_A = ItemId("architecture.a")
_B = ItemId("architecture.b")
_LOWERED_A = (_A, Sensitivity.CONFIDENTIAL, Sensitivity.INTERNAL)
_LOWERED_B = (_B, Sensitivity.CONFIDENTIAL, Sensitivity.INTERNAL)
_READMITTED_A = (_A, KnowledgeStatus.DEPRECATED, KnowledgeStatus.APPROVED)


_LOCATION = _ProposalLocation(Path("/p/.theurian/proposals/P1"), ".theurian/proposals/P1", False)


def _remedy(
    *,
    lowered: tuple[tuple[ItemId, Sensitivity, Sensitivity], ...],
    readmitted: tuple[tuple[ItemId, KnowledgeStatus, KnowledgeStatus], ...],
) -> str:
    refusal = _floor_refusal(lowered, readmitted, frozenset({"upsertRevision"}), _LOCATION)
    assert refusal is not None
    return refusal.remedy or ""


def test_an_item_both_lowered_and_readmitted_is_not_told_to_run_propose_now() -> None:
    remedy = _remedy(lowered=(_LOWERED_A,), readmitted=(_READMITTED_A,))
    assert "run `theurian propose`" not in remedy
    assert "changeSensitivity" in remedy and "restoreItem" in remedy


def test_a_lowered_item_that_is_not_readmitted_keeps_the_redraft_remedy() -> None:
    remedy = _remedy(lowered=(_LOWERED_A, _LOWERED_B), readmitted=(_READMITTED_A,))
    assert "run `theurian propose`" in remedy
    assert "restoreItem" in remedy


#: The accepted proposal's migration id predates the reclassification or restore the
#: remedy sends the reader to write, so accepting it again replays it before them.
_DRAFT_AGAIN_TOKENS = ("theurian propose", "rather than accepting", "this proposal again")


def _refusal_remedies() -> list[object]:
    return [
        pytest.param(_remedy(lowered=(_LOWERED_A,), readmitted=()), id="lowered-only"),
        pytest.param(
            _remedy(lowered=(_LOWERED_A,), readmitted=(_READMITTED_A,)),
            id="lowered-and-readmitted",
        ),
        pytest.param(
            _remedy(lowered=(_LOWERED_A, _LOWERED_B), readmitted=(_READMITTED_A,)),
            id="lowered-one-more-than-readmitted",
        ),
        pytest.param(ACCEPT_LOWERING_REMEDY, id="the-lowering-constant"),
        pytest.param(_remedy(lowered=(), readmitted=(_READMITTED_A,)), id="readmitted-only"),
    ]


@pytest.mark.parametrize("remedy", _refusal_remedies())
def test_every_remedy_that_sends_the_reader_to_a_migration_says_to_draft_again_not_accept_again(
    remedy: str,
) -> None:
    """Accepting the refused proposal again replays it before the migration the remedy asks for.

    ``readmitted-only`` is the control: the clause is already there, so a token
    check that reddens on it is reddening on the wrong thing.
    """
    for token in _DRAFT_AGAIN_TOKENS:
        assert token in remedy, (token, remedy)


#: A fresh draft carries no ``dependsOn`` and ``theurian propose`` has no option for
#: one, and a landed id can sort after the drafting clock, so the draft replays after
#: the migration only when ``dependsOn`` is edited in by hand, whatever that migration declares.
_DEPENDS_ON_CLAUSE = (
    "edit that migration's id into the `dependsOn` of the new draft's migration file."
)


@pytest.mark.parametrize("remedy", _refusal_remedies())
def test_every_remedy_that_says_to_draft_again_says_to_edit_dependson_in(remedy: str) -> None:
    """Without it the redraft can replay before the landed migration, whatever either id."""
    assert _DEPENDS_ON_CLAUSE in remedy, remedy
    assert "declares" not in remedy, remedy


@pytest.mark.parametrize("constant", [DRAFT_AGAIN_CLAUSE, ACCEPT_READMISSION_REMEDY])
def test_the_draft_again_constants_carry_the_dependson_clause(constant: str) -> None:
    assert constant.endswith(_DEPENDS_ON_CLAUSE), constant
    assert "declares" not in constant, constant
