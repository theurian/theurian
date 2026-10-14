"""Floor and conflict remedies route by what the proposal carries (#915)."""

from __future__ import annotations

import itertools
import re
from pathlib import Path
from typing import Any, Final

import pytest

from theurian.application import item_labels
from theurian.application.item_labels import (
    ACCEPT_LOWERING_REMEDY,
    ACCEPT_READMISSION_REMEDY,
    DECLASSIFY_REMEDY,
    READMIT_REMEDY,
    ItemLabels,
)
from theurian.application.proposal_service import (
    ProposalService,
    _floor_refusal,
    _ProposalLocation,
)
from theurian.domain.enums import KnowledgeStatus, Sensitivity, TrustLevel
from theurian.domain.errors import RevisionConflictError
from theurian.domain.identifiers import ItemId, RevisionId

pytestmark = pytest.mark.unit

_A = ItemId("architecture.a")
_B = ItemId("architecture.b")
_C = ItemId("architecture.c")
_Lowered = tuple[tuple[ItemId, Sensitivity, Sensitivity], ...]
_Readmitted = tuple[tuple[ItemId, KnowledgeStatus, KnowledgeStatus], ...]


def _low(item: ItemId) -> tuple[ItemId, Sensitivity, Sensitivity]:
    return (item, Sensitivity.CONFIDENTIAL, Sensitivity.INTERNAL)


def _back(item: ItemId) -> tuple[ItemId, KnowledgeStatus, KnowledgeStatus]:
    return (item, KnowledgeStatus.DEPRECATED, KnowledgeStatus.APPROVED)


#: (lowered, readmitted, ids the cure must name, in order). The last two are the worst
#: instances of ordering: lowered ids come first even when they sort later, and an item
#: both lowered and readmitted is named once, at its first mention.
_COMBINATIONS: Final = {
    "lowered-only": ((_low(_A),), (), [_A]),
    "readmitted-only": ((), (_back(_A),), [_A]),
    "lowered-and-readmitted-same-item": ((_low(_A),), (_back(_A),), [_A]),
    "two-lowered-one-readmitted": ((_low(_A), _low(_B)), (_back(_A),), [_A, _B]),
    "lowered-b-readmitted-a": ((_low(_B),), (_back(_A),), [_B, _A]),
    "lowered-ab-readmitted-bc": ((_low(_A), _low(_B)), (_back(_B), _back(_C)), [_A, _B, _C]),
}
_COMBO_PARAMS: Final = [pytest.param(*value, id=key) for key, value in _COMBINATIONS.items()]

_CONTENT_KINDS: Final = [
    pytest.param(frozenset(), id="no-kinds"),
    pytest.param(frozenset({"upsertRevision"}), id="upsert"),
    pytest.param(frozenset({"createItem"}), id="create"),
    pytest.param(frozenset({"createItem", "upsertRevision"}), id="create-and-upsert"),
]
_OTHER_KINDS: Final = [
    pytest.param(frozenset({"changeSensitivity"}), id="change-sensitivity"),
    pytest.param(frozenset({"restoreItem"}), id="restore"),
    pytest.param(frozenset({"restoreItem", "deprecateItem"}), id="restore-and-deprecate"),
    pytest.param(frozenset({"deprecateItem"}), id="deprecate"),
    pytest.param(frozenset({"addAlias"}), id="alias"),
    pytest.param(
        frozenset({"createItem", "upsertRevision", "deprecateItem"}), id="content-and-deprecate"
    ),
]
_ALL_KINDS: Final = [*_CONTENT_KINDS, *_OTHER_KINDS]
_LOCATIONS: Final = [
    pytest.param(
        _ProposalLocation(Path("/p/.theurian/proposals/P1"), ".theurian/proposals/P1", False),
        id="tracked",
    ),
    pytest.param(
        _ProposalLocation(
            Path("/p/.theurian/proposals-local/P2"), ".theurian/proposals-local/P2", True
        ),
        id="local",
    ),
]

#: The content route's words.
_CONTENT_PATH_PHRASES: Final = (
    "run `theurian propose`",
    "draft the update again",
    "with `theurian propose`",
    "`theurian migrate apply`",
    "served state",
)


def _remedy(lowered: _Lowered, readmitted: _Readmitted, kinds: frozenset[str], where: Any) -> str:
    refusal = _floor_refusal(lowered, readmitted, kinds, where)
    assert refusal is not None
    return refusal.remedy or ""


def _cure(ids: list[ItemId], where: _ProposalLocation) -> str:
    operations = " and ".join(f"every operation whose `itemId` is `{i.value}`" for i in ids)
    return (
        f"remove {operations} from this proposal's migration file in {where.relative}/, then "
        "run `theurian propose accept` again; if those are all of its operations, "
        f"delete {where.relative}/ instead."
    )


def _named_ids(remedy: str) -> list[str]:
    return re.findall(r"every operation whose `itemId` is `([^`]*)`", remedy)


@pytest.mark.parametrize("kinds", _CONTENT_KINDS)
@pytest.mark.parametrize(("lowered", "readmitted", "_ids"), _COMBO_PARAMS)
def test_a_content_only_proposal_keeps_the_content_remedy(
    lowered: _Lowered, readmitted: _Readmitted, _ids: list[ItemId], kinds: frozenset[str]
) -> None:
    """A content draft can redo it whole, so removing operations would discard its change."""
    where = _ProposalLocation(Path("/p/x"), ".theurian/proposals/P1", False)

    remedy = _remedy(lowered, readmitted, kinds, where)

    assert "every operation whose" not in remedy, remedy
    assert "delete .theurian/proposals/P1/ instead" not in remedy, remedy
    assert "`theurian propose`" in remedy, remedy


def test_the_content_route_still_says_what_the_non_content_route_must_not() -> None:
    """Positive control: the phrases the other tests forbid are the ones this route prints."""
    where = _ProposalLocation(Path("/p/x"), ".theurian/proposals/P1", False)
    kinds = frozenset({"upsertRevision"})

    lowering = _remedy((_low(_A),), (), kinds, where)
    readmission = _remedy((), (_back(_A),), kinds, where)

    assert lowering == ACCEPT_LOWERING_REMEDY
    assert readmission == ACCEPT_READMISSION_REMEDY
    assert all(phrase in lowering for phrase in _CONTENT_PATH_PHRASES), lowering
    assert "draft the update again" in readmission and "with `theurian propose`" in readmission


@pytest.mark.parametrize("where", _LOCATIONS)
@pytest.mark.parametrize("kinds", _OTHER_KINDS)
@pytest.mark.parametrize(("lowered", "readmitted", "ids"), _COMBO_PARAMS)
def test_a_proposal_no_content_draft_can_redo_is_told_to_remove_the_refused_items_operations(
    lowered: _Lowered,
    readmitted: _Readmitted,
    ids: list[ItemId],
    kinds: frozenset[str],
    where: _ProposalLocation,
) -> None:
    """The content route sends it to a draft that cannot carry its operations (#915).

    Every id, in order, and the directory are the claims a reader acts on: a missing id
    leaves the refusal standing, a wrong directory deletes a file that is not the proposal.
    """
    remedy = _remedy(lowered, readmitted, kinds, where)

    assert _named_ids(remedy) == [i.value for i in ids], remedy
    assert _cure(ids, where) in remedy, remedy
    assert remedy.startswith("Nothing has moved. To leave the landed state alone, "), remedy
    assert (DECLASSIFY_REMEDY in remedy) is bool(lowered), remedy
    assert (READMIT_REMEDY in remedy) is bool(readmitted), remedy
    assert not [p for p in _CONTENT_PATH_PHRASES if p in remedy], remedy


# -- the union refusal, both RevisionConflictError branches ---------------------------------

_WHERE: Final = _ProposalLocation(Path("/p/x"), ".theurian/proposals/P1", False)
_REVISION: Final = RevisionId("01K1AAAREV01234567890ABCDE")


def _union_remedy(monkeypatch: pytest.MonkeyPatch, kinds: frozenset[str], *, retired: bool) -> str:
    status = KnowledgeStatus.DEPRECATED if retired else KnowledgeStatus.APPROVED
    held = {_A: ItemLabels(Sensitivity.INTERNAL, TrustLevel.REVIEWED, "backend", status)}
    monkeypatch.setattr(ProposalService, "_landed_set_alone", lambda *_: (None, held))
    service = ProposalService.__new__(ProposalService)
    error = RevisionConflictError(_A, None, _REVISION)

    refusal = service._union_refusal(_WHERE, Path("m.yaml"), ("L1",), error, kinds)

    return refusal.remedy or ""


@pytest.mark.parametrize("kinds", _OTHER_KINDS)
def test_a_retired_items_conflict_for_a_non_content_proposal_is_cured_by_removal_and_readmission(
    kinds: frozenset[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    remedy = _union_remedy(monkeypatch, kinds, retired=True)

    assert remedy == (
        "Nothing has moved. To leave the landed state alone, "
        + _cure([_A], _WHERE)
        + " "
        + READMIT_REMEDY
    )


@pytest.mark.parametrize("kinds", _OTHER_KINDS)
def test_a_raced_revision_for_a_non_content_proposal_offers_the_edit_or_the_removal(
    kinds: frozenset[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    remedy = _union_remedy(monkeypatch, kinds, retired=False)

    assert remedy == (
        "Another change to this item landed first. Nothing has moved, so either give the "
        "proposal's own migration the expectedRevision the message reports and run "
        "`theurian propose accept` again, or " + _cure([_A], _WHERE)
    )
    assert "--expected-revision" not in remedy and "draft the change again" not in remedy


@pytest.mark.parametrize("kinds", _CONTENT_KINDS)
def test_the_content_proposals_conflict_remedies_are_unchanged(
    kinds: frozenset[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    retired = _union_remedy(monkeypatch, kinds, retired=True)
    raced = _union_remedy(monkeypatch, kinds, retired=False)

    assert retired == ACCEPT_READMISSION_REMEDY
    assert "draft the change again with --expected-revision and delete" in raced
    assert "every operation whose" not in raced


# -- code spans are machine-read ------------------------------------------------------------

_MIGRATION_ID: Final = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}")
_SPAN: Final = re.compile(r"`dependsOn: \[([^\]]*)\]`")


def _bad_tokens(text: str) -> list[str]:
    """Every token, in the spans the integration helper ``_named`` reads, that is no MigrationId."""
    return [
        token
        for span in _SPAN.finditer(text)
        for token in span[1].split(", ")
        if not _MIGRATION_ID.fullmatch(token)
    ]


_REAL: Final = "01K1BBBBBB01234567890ABCDE, 7ZZZZZZZZZ01234567890ABCDE"


@pytest.mark.parametrize(
    "token", ["<its id>", "<id>", "X", "...", "", "01K1AAAAAA0123456789OABCDE"]
)
def test_the_span_check_rejects_every_non_id_token_and_accepts_real_ones(token: str) -> None:
    """Control: a check that passes these would pass main's `<its id>` too."""
    assert _bad_tokens(f"edit `dependsOn: [{token}]` in") == [token]
    assert _bad_tokens(f"edit `dependsOn: [{_REAL}, {token}]`") == [token]
    assert _bad_tokens(f"edit `dependsOn: [{_REAL}]` in") == []


def _every_remedy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    found = [
        (name, value)
        for name, value in vars(item_labels).items()
        if isinstance(value, str) and not name.startswith("__")
    ]
    kinds_all: list[frozenset[str]] = [p.values[0] for p in _ALL_KINDS]  # type: ignore[misc]
    for kinds, (key, (lowered, readmitted, _)) in itertools.product(
        kinds_all, _COMBINATIONS.items()
    ):
        found.append((f"floor {sorted(kinds)} {key}", _remedy(lowered, readmitted, kinds, _WHERE)))
    for kinds, retired in itertools.product(kinds_all, (True, False)):
        found.append(
            (f"union {sorted(kinds)} {retired}", _union_remedy(monkeypatch, kinds, retired=retired))
        )
    return found


def test_no_remedy_prints_a_dependson_token_outside_the_migration_id_grammar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool extracting ids from a remedy extracted `<its id>` on main 4d42fe6f (#915)."""
    remedies = _every_remedy(monkeypatch)

    names = {name for name, _ in remedies}
    assert {"DRAFT_AGAIN_CLAUSE", "ACCEPT_READMISSION_REMEDY"} <= names
    assert [(name, _bad_tokens(text)) for name, text in remedies if _bad_tokens(text)] == []
