"""Floor and conflict remedies route by what the proposal carries (#915)."""

from __future__ import annotations

import itertools
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Final

import pytest
from floor_remedy_support import _BODIES, _NAMED_IDS
from floor_remedy_support import TRACKED_LOCATION as _WHERE

from theurian.application import item_labels
from theurian.application.item_labels import (
    ACCEPT_LOWERING_REMEDY,
    ACCEPT_READMISSION_REMEDY,
    DECLASSIFY_REMEDY,
    READMIT_REMEDY,
    CurrentItem,
    ItemLabels,
)
from theurian.application.proposal_service import (
    _MAX_NAMES_LISTED,
    ProposalService,
    _BodyMove,
    _floor_refusal,
    _ProposalLocation,
    _removal_cure,
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

_CONTENT_KINDS: Final = {
    "no-kinds": frozenset[str](),
    "upsert": frozenset({"upsertRevision"}),
    "create": frozenset({"createItem"}),
    "create-and-upsert": frozenset({"createItem", "upsertRevision"}),
}
_OTHER_KINDS: Final = {
    "change-sensitivity": frozenset({"changeSensitivity"}),
    "restore": frozenset({"restoreItem"}),
    "restore-and-deprecate": frozenset({"restoreItem", "deprecateItem"}),
    "deprecate": frozenset({"deprecateItem"}),
    "alias": frozenset({"addAlias"}),
    "content-and-deprecate": frozenset({"createItem", "upsertRevision", "deprecateItem"}),
}
_CONTENT_PARAMS: Final = [pytest.param(k, id=i) for i, k in _CONTENT_KINDS.items()]
_OTHER_PARAMS: Final = [pytest.param(k, id=i) for i, k in _OTHER_KINDS.items()]
_LOCATIONS: Final = [
    pytest.param(
        _WHERE,
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


def _remedy(
    lowered: _Lowered, readmitted: _Readmitted, kinds: frozenset[str], where: _ProposalLocation
) -> str:
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


@pytest.mark.parametrize("kinds", _CONTENT_PARAMS)
@pytest.mark.parametrize(("lowered", "readmitted", "_ids"), _COMBO_PARAMS)
def test_a_content_only_proposal_keeps_the_content_remedy(
    lowered: _Lowered, readmitted: _Readmitted, _ids: list[ItemId], kinds: frozenset[str]
) -> None:
    """A content draft can redo it whole, so removing operations would discard its change."""
    remedy = _remedy(lowered, readmitted, kinds, _WHERE)

    assert "every operation whose" not in remedy, remedy
    assert "delete .theurian/proposals/P1/ instead" not in remedy, remedy
    assert "`theurian propose`" in remedy, remedy


def test_the_content_route_still_says_what_the_non_content_route_must_not() -> None:
    """Positive control: the phrases the other tests forbid are the ones this route prints."""
    kinds = frozenset({"upsertRevision"})

    lowering = _remedy((_low(_A),), (), kinds, _WHERE)
    readmission = _remedy((), (_back(_A),), kinds, _WHERE)

    assert lowering == ACCEPT_LOWERING_REMEDY
    assert readmission == ACCEPT_READMISSION_REMEDY
    assert all(phrase in lowering for phrase in _CONTENT_PATH_PHRASES), lowering
    assert "draft the update again" in readmission and "with `theurian propose`" in readmission


@pytest.mark.parametrize("where", _LOCATIONS)
@pytest.mark.parametrize("kinds", _OTHER_PARAMS)
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
    assert remedy.startswith("Nothing has moved. To keep those items at their landed labels, "), (
        remedy
    )
    assert (DECLASSIFY_REMEDY in remedy) is bool(lowered), remedy
    assert (READMIT_REMEDY in remedy) is bool(readmitted), remedy
    assert not [p for p in _CONTENT_PATH_PHRASES if p in remedy], remedy


# -- the union refusal, both RevisionConflictError branches ---------------------------------

_REVISION: Final = RevisionId("01K1AAAREV01234567890ABCDE")


def _union_remedy(  # noqa: PLR0913 -- the shapes a conflict can take
    monkeypatch: pytest.MonkeyPatch,
    kinds: frozenset[str],
    *,
    retired: bool,
    item: ItemId = _A,
    revision: RevisionId | None = _REVISION,
    document: Mapping[str, object] | None = None,
    where: _ProposalLocation = _WHERE,
    moves: tuple[_BodyMove, ...] = (),
) -> str:
    status = KnowledgeStatus.DEPRECATED if retired else KnowledgeStatus.APPROVED
    held = {item: ItemLabels(Sensitivity.INTERNAL, TrustLevel.REVIEWED, "backend", status)}
    monkeypatch.setattr(ProposalService, "_landed_set_alone", lambda *_: (None, held))
    service = ProposalService.__new__(ProposalService)
    service._current_item = lambda _: CurrentItem(revision, held[item])
    service._current_revision = None
    service._landed_migrations = lambda: ()
    error = RevisionConflictError(item, None, _REVISION)

    refusal = service._union_refusal(
        where, Path("m.yaml"), ("L1",), error, kinds, document=document or {}, moves=moves
    )

    return refusal.remedy or ""


@pytest.mark.parametrize("kinds", [frozenset({"upsertRevision"}), frozenset({"changeSensitivity"})])
def test_a_race_over_one_upsert_with_no_landed_revision_names_no_revision(
    kinds: frozenset[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one-upsert no-revision fallback: no test drives it end to end. In production a
    hand-edited `expectedRevision` on an item with no landed revision reaches it; a revision
    printed here would be one the landed set does not hold."""
    one = {"operations": [{"op": "upsertRevision", "itemId": _A.value, "revisionId": "R1"}]}

    remedy = _union_remedy(monkeypatch, kinds, retired=False, revision=None, document=one)

    assert "the expectedRevision the message reports" in remedy, remedy
    assert not re.search(r"expectedRevision: [0-9A-HJKMNP-TV-Z]{26}", remedy), remedy


_SECRET: Final = ItemId("sk-" + "a1b2c3d4e5f6" * 4)


@pytest.mark.parametrize("route", ["lowered", "readmitted", "retired-conflict", "raced-conflict"])
@pytest.mark.parametrize("ids", [[_SECRET], [_SECRET, _A]], ids=["alone", "beside-a-clean-id"])
def test_a_remedy_never_prints_an_id_the_refusals_message_redacts(
    route: str, ids: list[ItemId], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under secretScan warn|off the cure once printed a secret-shaped id whole."""
    kinds = frozenset({"changeSensitivity"})
    if route in {"retired-conflict", "raced-conflict"}:
        retired = route == "retired-conflict"
        remedy = _union_remedy(monkeypatch, kinds, retired=retired, item=_SECRET)
    else:
        low = tuple(map(_low, ids)) if route == "lowered" else ()
        remedy = _remedy(low, tuple(map(_back, ids)) if not low else (), kinds, _WHERE)

    assert not [i.value for i in ids if i.value in remedy], remedy
    lead = "or " if route == "raced-conflict" else "landed labels, "
    assert f"{lead}delete {_WHERE.relative}/." in remedy, remedy


def _body_cure(tmp_path: Path, name: str, *, link_to: str | None = None) -> tuple[str, str]:
    """The removal cure for one body ``name`` under a real proposal directory.

    ``link_to`` makes ``name`` a symlink to that path. Returns the cure and the directory-only
    cure for the same location.
    """
    directory = tmp_path / "P1"
    directory.mkdir()
    location = _ProposalLocation(directory, ".theurian/proposals/P1", False)
    source = directory / name
    if link_to is not None:
        source.symlink_to(link_to)
    move = _BodyMove(source, source, b"", False, None, _A.value)

    return _removal_cure([_A], location, [move]), f"delete {location.relative}/."


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        pytest.param("body.md", "body.md", id="flat"),
        pytest.param("sub/dir/body.md", "sub/dir/body.md", id="nested"),
        pytest.param("a..b/.hidden-1_x.md", "a..b/.hidden-1_x.md", id="dots-inside-a-segment"),
    ],
)
def test_a_plain_body_path_inside_the_proposal_directory_is_named(
    tmp_path: Path, name: str, shown: str
) -> None:
    """Positive control: the rows below are refused for their shape, not because every path is."""
    cure, directory_only = _body_cure(tmp_path, name)

    assert cure != directory_only, cure
    assert f"delete the body files those operations drafted, `.theurian/proposals/P1/{shown}`" in (
        cure
    ), cure


def test_a_symlink_that_stays_inside_the_proposal_directory_is_named(tmp_path: Path) -> None:
    """Positive control for the containment row: only a link leaving the directory is refused."""
    cure, directory_only = _body_cure(tmp_path, "link.md", link_to="real.md")

    assert cure != directory_only, cure
    assert "`.theurian/proposals/P1/link.md`" in cure, cure


_ESCAPING: Final = {
    "backtick": "zzmark`x.md",
    "dotdot-leading": "../zzmark.md",
    "dotdot-mid": "sub/../zzmark.md",
    "space": "zzmark x.md",
    "newline": "zzmark\nx.md",
    "escape-char": "zzmark\x1b[2Kx.md",
    "secret-shaped": "sk-" + "a1b2c3d4e5f6" * 4 + ".md",
}


@pytest.mark.parametrize("name", list(_ESCAPING.values()), ids=list(_ESCAPING))
def test_a_body_path_outside_the_plain_grammar_leaves_the_directory_only_cure(
    tmp_path: Path, name: str
) -> None:
    """The path is the contributor's own contentFile: a backtick closes the code span the
    reader copies from, a `..` names a file outside the proposal, and a secret-shaped name
    is what the refusal's message redacts (#915 security HIGH, #360)."""
    cure, directory_only = _body_cure(tmp_path, name)

    assert cure == directory_only
    assert "zzmark" not in cure and "sk-" not in cure, cure


def test_an_absolute_body_path_outside_the_proposal_directory_is_not_named(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "P1"
    location = _ProposalLocation(directory, ".theurian/proposals/P1", False)
    source = tmp_path / "zzmark-elsewhere" / "body.md"
    move = _BodyMove(source, source, b"", False, None, _A.value)

    cure = _removal_cure([_A], location, [move])

    assert cure == f"delete {location.relative}/."
    assert "zzmark" not in cure


def test_a_symlink_inside_the_proposal_directory_pointing_outside_it_is_not_named(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "zzmark-outside.md"
    outside.write_text("x")

    cure, directory_only = _body_cure(tmp_path, "zzmark-link.md", link_to=str(outside))

    assert cure == directory_only
    assert "zzmark" not in cure


@pytest.mark.parametrize("kinds", _OTHER_PARAMS)
def test_a_retired_items_conflict_for_a_non_content_proposal_is_cured_by_removal_and_readmission(
    kinds: frozenset[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    remedy = _union_remedy(monkeypatch, kinds, retired=True)

    assert remedy == (
        "Nothing has moved. To keep those items at their landed labels, "
        + _cure([_A], _WHERE)
        + " "
        + READMIT_REMEDY
    )


def test_a_retired_item_with_a_body_is_not_offered_a_redraft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`theurian propose` refuses a retired item, so a redraft line would send the reader there."""
    body = tmp_path / "body.md"
    body.write_bytes(b"")
    where = _ProposalLocation(tmp_path, ".theurian/proposals/P1", False)
    move = _BodyMove(body, body, b"", False, None, _A.value)

    remedy = _union_remedy(
        monkeypatch, frozenset({"changeSensitivity"}), retired=True, where=where, moves=(move,)
    )

    assert "`.theurian/proposals/P1/body.md`" in remedy, remedy
    assert "draft it again afterwards with `theurian propose`" not in remedy, remedy


@pytest.mark.parametrize("kinds", _OTHER_PARAMS)
def test_a_raced_revision_for_a_non_content_proposal_offers_the_edit_or_the_removal(
    kinds: frozenset[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    remedy = _union_remedy(monkeypatch, kinds, retired=False)

    assert remedy == (
        "Another change to this item landed first. Nothing has moved, so either give the "
        f"proposal's own migration `expectedRevision: {_REVISION.value}`, then run "
        "`theurian propose accept` again, or " + _cure([_A], _WHERE)
    )
    assert "--expected-revision" not in remedy and "draft the change again" not in remedy


@pytest.mark.parametrize("kinds", _CONTENT_PARAMS)
def test_the_content_proposals_conflict_remedies_are_unchanged(
    kinds: frozenset[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    retired = _union_remedy(monkeypatch, kinds, retired=True)
    raced = _union_remedy(monkeypatch, kinds, retired=False)

    assert retired == ACCEPT_READMISSION_REMEDY
    assert f"draft the change again with --expected-revision {_REVISION.value} and delete" in raced
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
    "token",
    ["<its id>", "<id>", "X", "...", "", "01K1AAAAAA0123456789OABCDE"],  # invalid-ulid
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
    kinds_all = [*_CONTENT_KINDS.values(), *_OTHER_KINDS.values()]
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


# -- the removal cure's redraft line (#937), its lists (#940) and its shared bodies (#944) ----

_REDRAFT: Final = "draft it again afterwards with `theurian propose`"


def _body_of(directory: Path, name: str, item: ItemId) -> _BodyMove:
    directory.mkdir(exist_ok=True)
    source = directory / name
    source.write_text(name)
    return _BodyMove(source, source, b"", False, None, item.value)


def _located(tmp_path: Path) -> _ProposalLocation:
    return _ProposalLocation(tmp_path / "P1", ".theurian/proposals/P1", False)


@pytest.mark.parametrize(
    ("owners", "removed", "retired", "printed"),
    [
        pytest.param([_A], [_A], [], True, id="removed-upsert-of-a-live-item"),
        pytest.param([_A, _B], [_A, _B], [_B], True, id="a-live-item-beside-a-retired-one"),
        pytest.param([_A], [_A], [_A], False, id="the-only-removed-upsert-is-retired"),
        pytest.param([_A, _C], [_A], [_A], False, id="a-kept-upsert-does-not-count"),
        pytest.param([_C], [_A], [], False, id="the-removed-item-has-no-upsert"),
    ],
)
def test_the_redraft_line_is_printed_for_a_removed_upsert_of_an_item_that_is_not_withdrawn(
    owners: list[ItemId],
    removed: list[ItemId],
    retired: list[ItemId],
    printed: bool,
    tmp_path: Path,
) -> None:
    """`theurian propose` refuses a withdrawn item without naming a revision, so the line
    would send the follower into a dead end (#937)."""
    moves = [_body_of(tmp_path / "P1", f"{o.value}.md", o) for o in owners]

    cure = _removal_cure(removed, _located(tmp_path), moves, retired)

    assert (_REDRAFT in cure) is printed, cure


def test_no_redraft_line_is_printed_when_no_operation_removed_is_an_upsert(
    tmp_path: Path,
) -> None:
    assert _REDRAFT not in _removal_cure([_A], _located(tmp_path), [])


@pytest.mark.parametrize("kinds", [frozenset({"changeSensitivity"}), frozenset({"restoreItem"})])
def test_the_readmission_floor_passes_its_readmitted_items_as_retired(
    kinds: frozenset[str], tmp_path: Path
) -> None:
    move = _body_of(tmp_path / "P1", "a.md", _A)

    readmission = _floor_refusal((), (_back(_A),), kinds, _located(tmp_path), [move])
    lowering = _floor_refusal((_low(_A),), (), kinds, _located(tmp_path), [move])

    assert readmission is not None and lowering is not None
    assert _REDRAFT not in (readmission.remedy or "")
    assert _REDRAFT in (lowering.remedy or "")


def test_the_cure_lists_every_refused_id_and_body_past_the_names_listed_bound(
    tmp_path: Path,
) -> None:
    """#940: a follower who is shown a prefix leaves the rest in the proposal."""
    items = [ItemId(f"architecture.i{n}") for n in range(_MAX_NAMES_LISTED + 3)]
    directory = tmp_path / "P1"
    moves = [_body_of(directory, f"{i.value}.md", i) for i in items]

    cure = _removal_cure(items, _located(tmp_path), moves)

    listed = _BODIES.search(cure)
    assert listed is not None, cure
    assert _NAMED_IDS.findall(cure) == [i.value for i in items]
    assert re.findall(r"`([^`]+)`", listed[1]) == sorted(
        f".theurian/proposals/P1/{i.value}.md" for i in items
    )


def _case_folds(tmp_path: Path) -> bool:
    (tmp_path / "CaseProbe").write_text("x")
    return (tmp_path / "caseprobe").exists()


_REFUSED_BODY = ".theurian/proposals/P1/refused.md"


def test_a_body_a_kept_operation_also_reads_is_not_listed_for_deletion_beside_a_distinct_one(
    tmp_path: Path,
) -> None:
    """Positive control for the two tests below: a refused body nobody keeps is listed."""
    directory = tmp_path / "P1"
    moves = [_body_of(directory, "refused.md", _A), _body_of(directory, "kept.md", _B)]

    cure = _removal_cure([_A], _located(tmp_path), moves)

    assert f"`{_REFUSED_BODY}`" in cure, cure


def test_a_hard_linked_pair_is_one_body_and_the_shared_file_is_not_listed(tmp_path: Path) -> None:
    """#944: two spellings of one inode; deleting either is deleting the kept operation's body."""
    directory = tmp_path / "P1"
    kept = _body_of(directory, "kept.md", _B)
    os.link(kept.source, directory / "refused.md")
    refused = _BodyMove(
        directory / "refused.md", directory / "refused.md", b"", False, None, "architecture.a"
    )

    cure = _removal_cure([_A], _located(tmp_path), [refused, kept])

    assert _REFUSED_BODY not in cure and "delete the body file" not in cure, cure
    assert "every operation whose `itemId` is `architecture.a`" in cure, cure


def test_a_case_variant_spelling_of_a_kept_body_is_not_listed_for_deletion(
    tmp_path: Path,
) -> None:
    if not _case_folds(tmp_path):
        pytest.skip("the temporary filesystem is case-sensitive: no second spelling exists")
    directory = tmp_path / "P1"
    kept = _body_of(directory, "Kept.md", _B)
    variant = directory / "kept.md"
    refused = _BodyMove(variant, variant, b"", False, None, "architecture.a")

    cure = _removal_cure([_A], _located(tmp_path), [refused, kept])

    assert "kept.md" not in cure, cure


@pytest.mark.parametrize("unreadable", ["kept", "refused"])
def test_an_identity_that_cannot_be_read_gives_the_directory_only_cure(
    unreadable: str, tmp_path: Path
) -> None:
    """Failing closed: without both identities the cure cannot rule out a shared body."""
    directory = tmp_path / "P1"
    refused = _body_of(directory, "refused.md", _A)
    kept = _body_of(directory, "kept.md", _B)
    gone = {"kept": kept, "refused": refused}[unreadable]
    gone.source.unlink()

    cure = _removal_cure([_A], _located(tmp_path), [refused, kept])

    assert cure == f"delete {_located(tmp_path).relative}/."
