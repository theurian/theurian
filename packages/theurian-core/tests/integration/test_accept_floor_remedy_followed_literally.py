"""A floor or conflict remedy, followed exactly as printed, ends where it says (#915).

``follow_removal_cure`` reads the ids and directory out of the remedy's own words and acts
on them, so a remedy that omits an id or names a wrong directory fails here. The non-content
proposals are cured by following the printed removal; the two content controls assert the
content remedy instead.

Own-item floor faces by route x field: non-content sensitivity is F1 and F4, status is F5
and F7. ``[restoreItem, deprecateItem]`` of the proposal's own item is unreachable for the
readmission floor (its last write retires the item); the ``named_by_no_floor`` test measured
it with and without a landed restore replaying after the proposal: accept exits 0 both
times. Content sensitivity and status are the two ``keeps_the_content_remedy`` tests.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from floor_remedy_support import (
    follow_removal_cure,
    mcp_draft,
    staged_migration,
    with_operations,
)
from label_inheritance_support import (
    BODY,
    ITEM_ID,
    ROOT_MIGRATION_ID,
    SORTS_AFTER_A_DRAFT,
    SORTS_BEFORE_A_DRAFT,
    LabelledProject,
    cli,
    cli_ok,
    cli_propose,
    item_row,
    labelled_project,
    land,
    land_deprecation,
    land_reclassification,
    root_migration,
)
from replay_order_support import (
    OTHER_ITEM,
    declare_dependency,
    draft,
    get_is_withheld,
    restoration,
    write,
)

from theurian.application.item_labels import ACCEPT_LOWERING_REMEDY, ACCEPT_READMISSION_REMEDY

pytestmark = pytest.mark.integration

LOWERS, READMITS = "would lower the sensitivity of", "would readmit"


def _op(kind: str, item: str, **fields: str) -> dict[str, str]:
    reason = {"reason": "r"} if kind in {"changeSensitivity", "deprecateItem"} else {}
    return {"op": kind, "itemId": item, **reason, **fields}


def _set(item: str, to: str) -> dict[str, str]:
    return _op("changeSensitivity", item, sensitivity=to)


def _non_content(p: LabelledProject, *operations: dict[str, str]) -> dict[str, Any]:
    return with_operations(p, draft(p, "status"), list(operations))


def _land_item(p: LabelledProject, item: str, number: int) -> None:
    """An approved ``internal`` item with a revision, which ``knowledge.get`` can withhold."""
    mid, revision = f"01K1AAAAA{number}01234567890ABCDE", f"01K1AAAAB{number}01234567890ABCDE"
    (p.root / f".theurian/knowledge/architecture/i{number}.md").write_text(BODY)
    text = root_migration(
        item_id=item, revision_id=revision, namespace="backend", sensitivity="internal",
        trust_level="reviewed", status="approved",
    )  # fmt: skip
    text = text.replace(ROOT_MIGRATION_ID, mid).replace("auth-policy.md", f"i{number}.md")
    write(p.root, mid, "item", text)
    cli_ok("migrate", "apply")


def _refused(drafted: dict[str, Any], *, says: str) -> dict[str, Any]:
    code, payload = cli("propose", "accept", drafted["proposalId"])
    assert code == 1 and says in str(payload["error"]), payload
    return payload


def _migrations(p: LabelledProject) -> list[str]:
    return sorted(f.name for f in (p.root / ".theurian/migrations").iterdir())


def _delete_cure(p: LabelledProject, drafted: dict[str, Any], refusal: dict[str, Any]) -> None:
    """Follow a cure that deletes the proposal: nothing lands and the directory goes."""
    before = _migrations(p)

    cure = follow_removal_cure(p, drafted, str(refusal["remedy"]))

    assert cure.deleted
    assert not (p.root / drafted["proposalDirectory"]).exists()
    assert _migrations(p) == before, "deleting the proposal changed what has landed"
    cli_ok("migrate", "apply")


def _kept_cure(p: LabelledProject, drafted: dict[str, Any], refusal: dict[str, Any]) -> None:
    cure = follow_removal_cure(p, drafted, str(refusal["remedy"]))
    assert not cure.deleted and cure.code == 0, cure
    cli_ok("migrate", "apply")


def test_a_tightener_that_is_the_only_operation_is_cured_by_deleting_the_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1: the floor names the proposal's own item, so the cure discards its whole change."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="public")
    drafted = _non_content(p, _set(p.item_id, "internal"))
    land_reclassification(p.root, SORTS_BEFORE_A_DRAFT, p.item_id, "confidential")
    refusal = _refused(drafted, says=LOWERS)

    _delete_cure(p, drafted, refusal)

    assert item_row(p.root, p.item_id)["sensitivity"] == "confidential"
    assert get_is_withheld(p)


def test_a_tightener_beside_a_kept_raise_is_cured_by_removing_only_its_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1 with a second item's raise kept."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="public")
    _land_item(p, OTHER_ITEM, 1)
    drafted = _non_content(p, _set(p.item_id, "internal"), _set(OTHER_ITEM, "confidential"))
    land_reclassification(p.root, SORTS_BEFORE_A_DRAFT, p.item_id, "confidential")
    refusal = _refused(drafted, says=LOWERS)

    _kept_cure(p, drafted, refusal)

    assert item_row(p.root, OTHER_ITEM)["sensitivity"] == "confidential"
    assert item_row(p.root, p.item_id)["sensitivity"] == "confidential"
    assert get_is_withheld(p) and get_is_withheld(p, itemId=OTHER_ITEM)


def test_a_co_carried_items_older_label_is_cured_by_removing_that_items_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F2: the cure names only the co-carried item, so the proposal's own raise lands."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    _land_item(p, OTHER_ITEM, 1)
    land_reclassification(p.root, SORTS_BEFORE_A_DRAFT, OTHER_ITEM, "confidential")
    drafted = _non_content(p, _set(p.item_id, "confidential"), _set(OTHER_ITEM, "internal"))
    refusal = _refused(drafted, says=LOWERS)

    _kept_cure(p, drafted, refusal)

    assert item_row(p.root, p.item_id)["sensitivity"] == "confidential"
    assert item_row(p.root, OTHER_ITEM)["sensitivity"] == "confidential"
    assert get_is_withheld(p) and get_is_withheld(p, itemId=OTHER_ITEM)


def test_a_co_carried_readmission_is_cured_by_removing_its_operation_and_the_item_stays_retired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F3."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    _land_item(p, OTHER_ITEM, 1)
    land_deprecation(p.root, SORTS_BEFORE_A_DRAFT, OTHER_ITEM)
    drafted = _non_content(p, _set(p.item_id, "confidential"), _op("restoreItem", OTHER_ITEM))
    declare_dependency(p, drafted, SORTS_BEFORE_A_DRAFT)
    refusal = _refused(drafted, says=READMITS)

    _kept_cure(p, drafted, refusal)

    assert item_row(p.root, OTHER_ITEM)["status"] == "deprecated"
    assert item_row(p.root, p.item_id)["sensitivity"] == "confidential"
    assert get_is_withheld(p, itemId=OTHER_ITEM) and get_is_withheld(p)


def test_a_deliberate_lowering_is_cured_by_deleting_the_proposal_and_nothing_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F4: a lowering nobody landed a tightener for is cured by deleting the proposal."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="confidential")
    drafted = _non_content(p, _set(p.item_id, "internal"))
    refusal = _refused(drafted, says=LOWERS)

    _delete_cure(p, drafted, refusal)

    assert item_row(p.root, p.item_id)["sensitivity"] == "confidential"
    assert get_is_withheld(p)


@pytest.mark.parametrize("lowers", [False, True], ids=["restore-only", "restore-and-lower"])
def test_a_deliberate_readmission_is_cured_by_deleting_the_proposal_and_the_item_stays_retired(
    lowers: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F5, and F7 when the same item is also lowered: deleting the proposal cures both."""
    label = "confidential" if lowers else "internal"
    p = labelled_project(tmp_path, monkeypatch, sensitivity=label)
    land_deprecation(p.root, SORTS_BEFORE_A_DRAFT, p.item_id)
    lowering = [_set(p.item_id, "internal")] * lowers
    drafted = _non_content(p, *lowering, _op("restoreItem", p.item_id))
    refusal = _refused(drafted, says=READMITS)

    _delete_cure(p, drafted, refusal)

    assert item_row(p.root, p.item_id)["status"] == "deprecated"
    assert item_row(p.root, p.item_id)["sensitivity"] == label
    assert get_is_withheld(p)


def test_a_lowering_and_a_readmission_of_two_items_is_cured_by_removing_both_and_keeping_a_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F6: the cure names both items, and a third item's raise is not discarded with them."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="confidential")
    _land_item(p, OTHER_ITEM, 1)
    _land_item(p, "architecture.third", 2)
    land_deprecation(p.root, SORTS_BEFORE_A_DRAFT, OTHER_ITEM)
    drafted = _non_content(
        p,
        _set(p.item_id, "internal"),
        _op("restoreItem", OTHER_ITEM),
        _set("architecture.third", "confidential"),
    )
    refusal = _refused(drafted, says=READMITS)
    assert LOWERS in refusal["error"], refusal

    _kept_cure(p, drafted, refusal)

    assert item_row(p.root, p.item_id)["sensitivity"] == "confidential"
    assert item_row(p.root, OTHER_ITEM)["status"] == "deprecated"
    assert item_row(p.root, "architecture.third")["sensitivity"] == "confidential"
    assert get_is_withheld(p, itemId="architecture.third")


@pytest.mark.parametrize(
    "landed_restore_after", [False, True], ids=["plain", "restore-replays-after"]
)
def test_a_restore_then_deprecate_of_a_retired_item_is_named_by_no_floor(
    landed_restore_after: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured: with the item retired before and after, accept exits 0 and no floor refuses."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    drafted = _non_content(p, _op("restoreItem", p.item_id), _op("deprecateItem", p.item_id))
    land_deprecation(p.root, SORTS_BEFORE_A_DRAFT, p.item_id)
    if landed_restore_after:
        write(p.root, SORTS_AFTER_A_DRAFT, "restore", restoration(SORTS_AFTER_A_DRAFT, p.item_id))
        cli_ok("migrate", "apply")
        refused = _refused(drafted, says="loosen what it sets")
        named = re.search(r"`dependsOn: \[([^\]]*)\]`", refused["remedy"])
        assert named, refused
        declare_dependency(p, drafted, *named[1].split(", "))

    code, accepted = cli("propose", "accept", drafted["proposalId"])

    assert code == 0, accepted


def _mixed_update(p: LabelledProject, *, with_deprecation: bool) -> dict[str, Any]:
    """A ``theurian propose`` update that a smaller-id landed tightening makes a lowering."""
    _land_item(p, OTHER_ITEM, 1)
    code, drafted = cli_propose(p, p.item_id, "--expected-revision", p.revision_id)
    assert code == 0, drafted
    operations = yaml.safe_load(staged_migration(p, drafted).read_text(encoding="utf-8"))[
        "operations"
    ]
    assert {o["op"] for o in operations} == {"createItem", "upsertRevision"}, operations
    if with_deprecation:
        with_operations(p, drafted, [*operations, _op("deprecateItem", OTHER_ITEM)])
    land_reclassification(p.root, SORTS_BEFORE_A_DRAFT, p.item_id, "confidential")
    return drafted


def test_a_mixed_proposal_is_cured_by_removing_every_operation_on_the_refused_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F8: the drafted content operations go with the item, the hand-added retirement stays."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    drafted = _mixed_update(p, with_deprecation=True)
    refusal = _refused(drafted, says=LOWERS)

    _kept_cure(p, drafted, refusal)

    assert item_row(p.root, p.item_id)["sensitivity"] == "confidential"
    assert item_row(p.root, OTHER_ITEM)["status"] == "deprecated"
    assert get_is_withheld(p) and get_is_withheld(p, itemId=OTHER_ITEM)


def test_a_content_update_lowering_its_item_keeps_the_content_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control for F8: without the hand-added operation the content remedy is printed."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    drafted = _mixed_update(p, with_deprecation=False)

    refusal = _refused(drafted, says=LOWERS)

    assert refusal["remedy"] == ACCEPT_LOWERING_REMEDY


def test_a_content_update_readmitting_its_item_keeps_the_content_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    code, drafted = cli_propose(p, p.item_id, "--expected-revision", p.revision_id)
    assert code == 0, drafted
    land_deprecation(p.root, SORTS_BEFORE_A_DRAFT, p.item_id)

    refusal = _refused(drafted, says=READMITS)

    assert refusal["remedy"] == ACCEPT_READMISSION_REMEDY


def test_the_cure_helper_fails_loudly_on_a_remedy_that_prints_no_cure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A content remedy names no id and no directory: following it as a cure must not pass."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")

    with pytest.raises(AssertionError):
        follow_removal_cure(p, {"proposalId": "x"}, ACCEPT_LOWERING_REMEDY)


# -- the union refusal's two RevisionConflictError branches ---------------------------------


def _conflict(
    p: LabelledProject, *, retired: bool, keep: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A non-content proposal over a conflicting revision; ``keep`` adds an ``addAlias`` elsewhere.

    ``retired``: the item is withdrawn by the landed set and drafted over MCP without an
    ``expectedRevision``. Otherwise two drafts claim one revision and the first lands.
    """
    _land_item(p, OTHER_ITEM, 1)
    if retired:
        land_deprecation(p.root, SORTS_BEFORE_A_DRAFT, p.item_id)
        drafted = mcp_draft(p)
    else:
        first = cli_propose(p, p.item_id, "--expected-revision", p.revision_id)[1]
        time.sleep(0.01)  # ULIDs sort by millisecond: the refused draft must replay after
        drafted = cli_propose(p, p.item_id, "--expected-revision", p.revision_id)[1]
        land(first["proposalId"])
    document = yaml.safe_load(staged_migration(p, drafted).read_text(encoding="utf-8"))
    aliases = [{"op": "addAlias", "alias": "probe.alias", "itemId": ITEM_ID}]
    aliases += [{"op": "addAlias", "alias": "probe.other", "itemId": OTHER_ITEM}] * keep
    with_operations(p, drafted, [*document["operations"], *aliases])
    return drafted, _refused(drafted, says="Revision conflict")


KEEP = pytest.mark.parametrize("keep", [False, True], ids=["only-operations", "another-item-kept"])


@KEEP
def test_a_retired_items_conflict_is_cured_by_removal_and_the_item_stays_retired(
    keep: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    drafted, refusal = _conflict(p, retired=True, keep=keep)
    assert "Nothing has moved. To leave the landed state alone, remove every" in refusal["remedy"]

    (_kept_cure if keep else _delete_cure)(p, drafted, refusal)

    assert item_row(p.root, p.item_id)["status"] == "deprecated"
    assert get_is_withheld(p)


@KEEP
def test_a_raced_revision_is_cured_by_removing_the_items_operations(
    keep: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    drafted, refusal = _conflict(p, retired=False, keep=keep)
    assert "Another change to this item landed first." in refusal["remedy"]

    (_kept_cure if keep else _delete_cure)(p, drafted, refusal)

    assert item_row(p.root, p.item_id)["status"] == "approved"


def test_a_raced_revision_is_cured_by_the_expected_revision_the_message_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    drafted, refusal = _conflict(p, retired=False, keep=False)
    reported = re.search(r"store holds (\S+)", str(refusal["error"]))
    assert (
        reported and "give the proposal's own migration the expectedRevision" in refusal["remedy"]
    )
    document = yaml.safe_load(staged_migration(p, drafted).read_text(encoding="utf-8"))
    for operation in document["operations"]:
        if operation["op"] == "upsertRevision":
            operation["expectedRevision"] = reported[1]
    with_operations(p, drafted, document["operations"])

    code, accepted = cli("propose", "accept", drafted["proposalId"])

    assert code == 0, accepted
