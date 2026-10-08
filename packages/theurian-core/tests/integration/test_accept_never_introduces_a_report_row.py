"""``propose accept`` never introduces a permissive-move report row (GHSA-v2qg-23fc-7fqp).

The accept-side invariant: the union replay's report may hold no row that the
landed-alone replay's report does not, and no row it holds may name another migration
as what it undoes. It is wider than refusing the incoming
migration's own upsert, which ``test_accept_refuses_a_reported_upsert.py`` holds: a
proposal minted early but accepted after a later-minted update has landed replays
BEFORE that update, so the landed update's upsert is the one reported, naming the
accepted migration as what it undoes. A pre-existing row is the baseline, so a
project whose history already holds one still accepts.

The incoming migration's own row stays refused through the widening, held there by
``test_accepting_a_proposal_that_replays_before_a_landed_restore_is_refused_and_moves_nothing``.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from label_inheritance_support import (
    EVIDENCE,
    ITEM_ID,
    ROOT_MIGRATION_ID,
    ROOT_REVISION_ID,
    SORTS_AFTER_A_DRAFT,
    SORTS_BEFORE_A_DRAFT,
    LabelledProject,
    cli,
    cli_ok,
    cli_propose,
    item_row,
    labelled_project,
    land_deprecation,
    land_reclassification,
    landing_zone,
    proposals_tree,
)

from theurian.daemon.runner import build_server

from mcp_wire_session import mcp_session  # isort: skip

pytestmark = pytest.mark.integration


def _mcp_migration_draft(
    project: LabelledProject,
    tmp_path: Path,
    *operations: dict[str, Any],
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    """``knowledge.generateMigrationDraft`` over the wire; its migration id is minted now."""
    time.sleep(0.05)
    document: dict[str, Any] = {
        "author": "sec@example.com",
        "description": "Staged by an agent.",
        "operations": list(operations),
    }
    if depends_on is not None:
        document["dependsOn"] = depends_on
    with mcp_session(build_server(project.registry), tmp_path / "wire") as call:
        answer = call(
            "knowledge.generateMigrationDraft",
            {
                "projectId": "demo",
                "document": document,
                "evidence": EVIDENCE,
            },
        )
    assert answer["result"]["isError"] is False, answer
    drafted: dict[str, Any] = answer["result"]["structuredContent"]
    return drafted


def _deprecate_draft(project: LabelledProject, tmp_path: Path) -> dict[str, Any]:
    return _mcp_migration_draft(
        project,
        tmp_path,
        {"op": "deprecateItem", "itemId": project.item_id, "reason": "Wrong."},
    )


def _landed_update(
    project: LabelledProject,
    *extra: str,
    land_between: Callable[[], None] | None = None,
    depends_on: list[str] | None = None,
    migration_id: str | None = None,
) -> dict[str, Any]:
    """A CLI content update drafted now, accepted and applied: its id sorts after earlier drafts.

    ``land_between`` runs after the accept and before the apply. The accept floors
    refuse an update over an already reclassified or withdrawn item, so a history
    where the update undoes such a write must land that write after the accept.

    ``depends_on`` hand-edits ``dependsOn`` into the staged migration before the
    accept, the shape ``docs/protocol/migrations.md`` shows: ``theurian propose``
    has no option that writes it. ``migration_id`` re-ids the staged migration the same
    way, as one that sorts after every draft to come.
    """
    time.sleep(0.05)
    code, drafted = cli_propose(
        project, project.item_id, "--expected-revision", ROOT_REVISION_ID, *extra
    )
    assert code == 0, drafted
    if depends_on is not None:
        staged = Path(project.root / drafted["proposalDirectory"] / drafted["migrationFile"])
        document = yaml.safe_load(staged.read_text(encoding="utf-8"))
        document["dependsOn"] = depends_on
        staged.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    if migration_id is not None:
        staged = Path(project.root / drafted["proposalDirectory"] / drafted["migrationFile"])
        document = yaml.safe_load(staged.read_text(encoding="utf-8"))
        document["id"] = migration_id
        staged.unlink()
        renamed = staged.name.replace(drafted["migrationId"], migration_id)
        (staged.parent / renamed).write_text(
            yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
        )
    cli_ok("propose", "accept", drafted["proposalId"])
    if land_between is not None:
        land_between()
    cli_ok("migrate", "apply")
    return drafted


def _report() -> list[dict[str, Any]]:
    rows = cli_ok("migrate", "validate")["permissiveMoves"]
    assert isinstance(rows, list), rows
    return rows


def _refusal_names(
    payload: dict[str, Any], landed_id: str, *names: str, moved: tuple[str, str] | None = None
) -> None:
    """The refusal names the landed migration and its row; the remedy never says "accept again".

    Accepting the same proposal again re-enters the refusal, so a remedy that
    says so must fail. ``moved`` is ``(before, after)`` of the landed row: the
    error states them in that order, and membership alone would pass a swap.
    The refused proposals here are migrations, never content, so ``theurian
    propose`` is no route for any of them.
    """
    error = str(payload.get("error", ""))
    assert "undoing what this proposal sets" in error, (
        payload
    )  # the report check's, not the end-state's
    for named in (landed_id, *names):
        assert named in error, (named, payload)
    if moved is not None:
        assert f"from {moved[0]} to {moved[1]}" in error, payload
    remedy = str(payload.get("remedy", ""))
    assert landed_id in remedy, payload
    assert "theurian propose" not in remedy, payload
    assert not re.search(r"accept[^.]*again", remedy, re.IGNORECASE), payload


# -- the face: a withdrawal accepted after a later-minted update landed --------------


def test_a_withdrawal_minted_before_a_landed_update_is_refused_and_moves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accepted, the deprecation exits 0 and is nullified by the update's ``approved``.

    ``migrate validate`` then reports ``deprecated -> approved`` for the *landed*
    update's migration, a row the incoming deprecation introduced and does not own.
    """
    project = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    deprecation = _deprecate_draft(project, tmp_path)
    update = _landed_update(project)
    assert update["migrationId"] > deprecation["migrationId"], "the update must sort after"
    before = landing_zone(project.root)
    proposals_before = proposals_tree(project.root)

    code, payload = cli("propose", "accept", deprecation["proposalId"])

    assert code == 1, payload
    _refusal_names(
        payload,
        update["migrationId"],
        project.item_id,
        "status",
        moved=("deprecated", "approved"),
    )
    assert landing_zone(project.root) == before, "a refused accept moved files"
    assert proposals_tree(project.root) == proposals_before
    cli_ok("migrate", "apply")
    assert item_row(project.root, project.item_id)["status"] == "approved"
    assert _report() == []


def test_the_withdrawal_drafted_again_after_the_update_is_accepted_and_takes_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A draft with no ``dependsOn`` whose id sorts after the update is accepted.

    The item ends deprecated. The remedy always names ``dependsOn``; this holds
    only the case where the id order alone already places the draft after the
    landed update.
    """
    project = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    stale = _deprecate_draft(project, tmp_path)
    update = _landed_update(project)
    code, refused = cli("propose", "accept", stale["proposalId"])
    assert code == 1, refused

    fresh = _deprecate_draft(project, tmp_path)
    assert fresh["migrationId"] > update["migrationId"], "the new draft must sort after"
    accepted_code, accepted = cli("propose", "accept", fresh["proposalId"])
    cli_ok("migrate", "apply")

    assert accepted_code == 0, accepted
    assert item_row(project.root, project.item_id)["status"] == "deprecated"
    assert cli_ok("migrate", "validate")["permissiveMoves"] == []


# -- the remedy names the route that can draft the refused operation ----------------


def test_a_withdrawals_remedy_routes_to_the_migration_draft_tool_not_the_content_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``theurian propose`` drafts content only, so a ``deprecateItem`` cannot be re-drafted there.

    A remedy naming it sends the reader to a command with no status option;
    ``knowledge.generateMigrationDraft`` is what stages the deprecation. The
    update here is a root migration, and the remedy still names ``dependsOn``
    for it: a later id does not place the redraft after it.
    """
    project = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    stale = _deprecate_draft(project, tmp_path)
    update = _landed_update(project)
    assert update["migrationId"] > stale["migrationId"]

    code, payload = cli("propose", "accept", stale["proposalId"])

    assert code == 1, payload
    remedy = str(payload.get("remedy", ""))
    assert "knowledge.generateMigrationDraft" in remedy, payload
    assert "theurian propose" not in remedy, payload
    assert "proposeChange" not in remedy, payload
    assert f"dependsOn: [{update['migrationId']}]" in remedy, payload
    assert "later migration id" not in remedy, payload


def test_a_sensitivity_raises_remedy_routes_to_a_hand_authored_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither ``generateMigrationDraft`` nor ``theurian propose`` can draft ``changeSensitivity``.

    The only route left is the one ``generateMigrationDraft``'s own refusal
    gives: author the operation as a migration, then ``theurian migrate apply``.
    """
    project = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    raise_ = _mcp_migration_draft(
        project, tmp_path, {"op": "changeOwner", "itemId": project.item_id, "owner": "sec-team"}
    )
    _become_a_raise(project, raise_)
    update = _landed_update(project, "--sensitivity", "internal")
    assert update["migrationId"] > raise_["migrationId"]

    code, payload = cli("propose", "accept", raise_["proposalId"])

    assert code == 1, payload
    remedy = str(payload.get("remedy", ""))
    assert re.search(r"author[^.]*changeSensitivity[^.]*migration", remedy, re.IGNORECASE), payload
    assert "theurian propose" not in remedy, payload
    assert "generateMigrationDraft" not in remedy, payload


# -- a landed update that declares dependsOn replays after every migration without one


def test_the_remedy_names_dependson_when_the_landed_update_declares_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh draft carries no ``dependsOn``, so it still replays before such an update.

    The layered sort replays every migration without ``dependsOn`` before every
    migration with one, whatever the ids: "a later id replays after" is false
    here, and a remedy saying only that loops the reader back into the refusal.
    """
    project = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    stale = _deprecate_draft(project, tmp_path)
    update = _landed_update(project, depends_on=[ROOT_MIGRATION_ID])
    assert update["migrationId"] > stale["migrationId"]

    code, payload = cli("propose", "accept", stale["proposalId"])

    assert code == 1, payload
    remedy = str(payload.get("remedy", ""))
    assert re.search(rf"dependsOn\W+{update['migrationId']}", remedy), payload
    assert "knowledge.generateMigrationDraft" in remedy, payload
    _refusal_names(
        payload,
        update["migrationId"],
        project.item_id,
        "status",
        moved=("deprecated", "approved"),
    )


def test_a_fresh_withdrawal_that_depends_on_the_dependent_update_is_accepted_and_takes_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Following the remedy works: ``dependsOn: [<update>]`` replays the deprecation after it."""
    project = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    stale = _deprecate_draft(project, tmp_path)
    update = _landed_update(project, depends_on=[ROOT_MIGRATION_ID])
    code, refused = cli("propose", "accept", stale["proposalId"])
    assert code == 1, refused

    fresh = _mcp_migration_draft(
        project,
        tmp_path,
        {"op": "deprecateItem", "itemId": project.item_id, "reason": "Wrong."},
        depends_on=[update["migrationId"]],
    )
    accepted_code, accepted = cli("propose", "accept", fresh["proposalId"])
    cli_ok("migrate", "apply")

    assert accepted_code == 0, accepted
    assert item_row(project.root, project.item_id)["status"] == "deprecated"
    assert _report() == []


def test_a_withdrawal_redrafted_behind_a_future_id_update_lands_with_dependson(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The remedy cannot rest on id order: an id past the drafting clock outlasts every redraft.

    An id edited before accept, a hand-chosen one and a collaborator's fast clock all
    produce it. Followed literally the remedy looped, exit 1 every time; ``dependsOn``
    is what places the fresh withdrawal after the update.
    """
    project = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    update = _landed_update(project, migration_id=SORTS_AFTER_A_DRAFT)
    assert update["migrationId"] < SORTS_AFTER_A_DRAFT, "the draft carried its own id"
    stale = _deprecate_draft(project, tmp_path)
    code, payload = cli("propose", "accept", stale["proposalId"])
    assert code == 1, payload
    remedy = str(payload.get("remedy", ""))
    assert "knowledge.generateMigrationDraft" in remedy, payload
    assert f"dependsOn: [{SORTS_AFTER_A_DRAFT}]" in remedy, payload

    fresh = _mcp_migration_draft(
        project,
        tmp_path,
        {"op": "deprecateItem", "itemId": project.item_id, "reason": "Wrong."},
        depends_on=[SORTS_AFTER_A_DRAFT],
    )
    assert fresh["migrationId"] < SORTS_AFTER_A_DRAFT
    accepted_code, accepted = cli("propose", "accept", fresh["proposalId"])
    cli_ok("migrate", "apply")

    assert accepted_code == 0, accepted
    assert item_row(project.root, project.item_id)["status"] == "deprecated"
    assert _report() == []


def test_a_fresh_withdrawal_without_dependson_is_refused_again_behind_a_dependent_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: the loop the remedy must warn about is real, whatever the fresh id.

    The fresh id sorts after the update's, yet the update's ``dependsOn`` puts it
    in a later layer, so the fresh draft's migration still replays first.
    """
    project = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    stale = _deprecate_draft(project, tmp_path)
    update = _landed_update(project, depends_on=[ROOT_MIGRATION_ID])
    code, refused = cli("propose", "accept", stale["proposalId"])
    assert code == 1, refused

    fresh = _deprecate_draft(project, tmp_path)
    assert fresh["migrationId"] > update["migrationId"]
    code, payload = cli("propose", "accept", fresh["proposalId"])

    assert code == 1, payload
    assert update["migrationId"] in str(payload.get("error", "")), payload


# -- the face: a raise accepted after a later-minted update landed -------------------


def _become_a_raise(
    project: LabelledProject, drafted: dict[str, Any], sensitivity: str = "confidential"
) -> None:
    """Hand-edit the staged ``changeOwner`` into a ``changeSensitivity`` raise to ``sensitivity``.

    ``generateMigrationDraft`` refuses ``changeSensitivity``, so the staged document
    is the only way a pending proposal carries one.
    """
    path = Path(project.root / drafted["proposalDirectory"] / drafted["migrationFile"])
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert [op["op"] for op in document["operations"]] == ["changeOwner"], document
    document["operations"] = [
        {
            "op": "changeSensitivity",
            "itemId": project.item_id,
            "sensitivity": sensitivity,
            "reason": "reclassified in the proposal",
        }
    ]
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def test_a_sensitivity_raise_minted_before_a_landed_update_is_refused_and_moves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The update restates ``internal`` after the raise replays, so the raise is silently undone."""
    project = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    raise_ = _mcp_migration_draft(
        project, tmp_path, {"op": "changeOwner", "itemId": project.item_id, "owner": "sec-team"}
    )
    _become_a_raise(project, raise_)
    update = _landed_update(project, "--sensitivity", "internal")
    assert update["migrationId"] > raise_["migrationId"], "the update must sort after"
    before = landing_zone(project.root)

    code, payload = cli("propose", "accept", raise_["proposalId"])

    assert code == 1, payload
    _refusal_names(
        payload,
        update["migrationId"],
        project.item_id,
        "sensitivity",
        moved=("confidential", "internal"),
    )
    assert landing_zone(project.root) == before, "a refused accept moved files"
    cli_ok("migrate", "apply")
    assert item_row(project.root, project.item_id)["sensitivity"] == "internal"
    assert _report() == []


# -- the face: a raise accepted over a row it would re-attribute ---------------------


def test_a_raise_minted_before_an_update_that_already_undoes_a_landed_raise_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The update's row exists before and after the accept, so only its ``undoes`` moves.

    Landed alone, the update reports undoing the landed ``confidential``. Accepted,
    the proposal's ``restricted`` replays between the two and the update silently
    overwrites it: the same ``(migration, item, field)`` row, now re-attributed.
    """
    project = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    raise_ = _mcp_migration_draft(
        project, tmp_path, {"op": "changeOwner", "itemId": project.item_id, "owner": "sec-team"}
    )
    _become_a_raise(project, raise_, "restricted")
    update = _landed_update(
        project,
        "--sensitivity",
        "internal",
        land_between=lambda: land_reclassification(
            project.root, SORTS_BEFORE_A_DRAFT, ITEM_ID, "confidential"
        ),
    )
    assert update["migrationId"] > raise_["migrationId"], "the update must sort after"
    rows_before = _report()
    assert [
        (r["migrationId"], r["itemId"], r["field"], r["before"], r["after"], r["undoes"])
        for r in rows_before
    ] == [
        (
            update["migrationId"],
            ITEM_ID,
            "sensitivity",
            "confidential",
            "internal",
            SORTS_BEFORE_A_DRAFT,
        )
    ], rows_before
    before = landing_zone(project.root)

    code, payload = cli("propose", "accept", raise_["proposalId"])

    assert code == 1, payload
    _refusal_names(
        payload,
        update["migrationId"],
        project.item_id,
        "sensitivity",
        moved=("restricted", "internal"),
    )
    assert landing_zone(project.root) == before, "a refused accept moved files"
    cli_ok("migrate", "apply")
    assert _report() == rows_before


def test_a_withdrawal_that_restores_first_is_refused_when_it_would_take_over_an_update_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The status twin: restore then deprecate in one migration is a withdrawal writer.

    The item is already deprecated, so the proposal looks like a no-op, but its
    ``restoreItem`` makes the item surfaceable and its ``deprecateItem`` withdraws it
    again. The update's status row then names the proposal as what it undoes.
    """
    project = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    staged = _deprecate_draft(project, tmp_path)
    path = Path(project.root / staged["proposalDirectory"] / staged["migrationFile"])
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["operations"] = [
        {"op": op, "itemId": project.item_id, "reason": "Restored, then withdrawn again."}
        for op in ("restoreItem", "deprecateItem")
    ]
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    update = _landed_update(
        project,
        land_between=lambda: land_deprecation(project.root, SORTS_BEFORE_A_DRAFT, ITEM_ID),
    )
    assert update["migrationId"] > staged["migrationId"], "the update must sort after"
    rows_before = _report()
    assert [(r["migrationId"], r["field"], r["undoes"]) for r in rows_before] == [
        (update["migrationId"], "status", SORTS_BEFORE_A_DRAFT)
    ], rows_before
    before = landing_zone(project.root)

    code, payload = cli("propose", "accept", staged["proposalId"])

    assert code == 1, payload
    _refusal_names(
        payload,
        update["migrationId"],
        project.item_id,
        "status",
        moved=("deprecated", "approved"),
    )
    remedy = str(payload.get("remedy", ""))
    assert "restoreItem" in remedy and "deprecateItem" in remedy, payload
    assert "Author the " in remedy and "`theurian migrate apply`" in remedy, payload
    assert landing_zone(project.root) == before, "a refused accept moved files"
    cli_ok("migrate", "apply")
    assert _report() == rows_before


# -- controls: the report check's baseline is the row the history holds; the end-state
# check still refuses a stale withdrawal ------------------------------------------


def test_a_proposal_on_the_item_of_an_existing_row_that_leaves_the_row_alone_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The baseline is the landed-alone report: comparing with an empty one refuses every accept.

    The history already reports the race on this item. A ``changeOwner`` writes no
    label, so the union reports the same row and the accept must land.
    ``test_accepting_a_proposal_while_another_migrations_row_exists_leaves_only_that_row``
    holds the other-item case.
    """
    project = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    raced = _landed_update(project)
    land_deprecation(project.root, SORTS_BEFORE_A_DRAFT, ITEM_ID)
    rows_before = _report()
    assert [(row["itemId"], row["migrationId"]) for row in rows_before] == [
        (ITEM_ID, raced["migrationId"])
    ], rows_before
    owner = _mcp_migration_draft(
        project, tmp_path, {"op": "changeOwner", "itemId": ITEM_ID, "owner": "sec-team"}
    )

    code, accepted = cli("propose", "accept", owner["proposalId"])
    cli_ok("migrate", "apply")

    assert code == 0, accepted
    assert _report() == rows_before


def test_a_stale_withdrawal_minted_before_a_landed_reapproval_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deprecation restates the landed one with a larger id, so the update re-approving
    after it undoes the proposal: refused, and the update's row stays as it was.

    The acceptance this test used to pin read the replay position, not the state the
    proposal was drafted against.
    """
    project = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    deprecation_draft = _deprecate_draft(project, tmp_path)
    update = _landed_update(
        project,
        land_between=lambda: land_deprecation(project.root, SORTS_BEFORE_A_DRAFT, ITEM_ID),
    )
    assert update["migrationId"] > deprecation_draft["migrationId"]
    rows_before = _report()
    assert [(r["migrationId"], r["field"], r["undoes"]) for r in rows_before] == [
        (update["migrationId"], "status", SORTS_BEFORE_A_DRAFT)
    ], rows_before
    before = landing_zone(project.root)

    code, payload = cli("propose", "accept", deprecation_draft["proposalId"])

    assert code == 1, payload
    assert landing_zone(project.root) == before, "a refused accept moved files"
    assert "loosen what it sets" in str(payload["error"]), payload
    assert f"dependsOn: [{update['migrationId']}]" in str(payload["remedy"]), payload
    cli_ok("migrate", "apply")
    assert _report() == rows_before
