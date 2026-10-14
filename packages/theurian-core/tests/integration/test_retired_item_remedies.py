"""A remedy never directs a retired item to a step the next command refuses (GHSA-v2qg-23fc-7fqp).

The draft-side refusal and the accept status floor made three older remedies
false for a retired item: each routes the reader to ``theurian propose`` or to
an ``expectedRevision`` edit, and the command that follows refuses a retired
item. For a retired item the only route back is a hand-authored ``restoreItem``
migration, so each remedy here must name it and must not name the step that is
refused.

The three faces, one test each. RED before the fix, GREEN after: the combined
refusal for a confidential and deprecated create-only item; the CLI's
missing-``--expected-revision`` refusal, which the retired refusal now wins over for a retired item;
and the replay's revision conflict, which a retired item reaches because the
MCP lookup answers it like an absent id. Each has a control showing that the
same remedy is unchanged for an item that is not retired.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from floor_remedy_support import retired_after_a_race
from git_harness import commit_migrations
from label_inheritance_support import (
    EVIDENCE,
    ITEM_ID,
    SORTS_BEFORE_A_DRAFT,
    UPDATED_BODY,
    LabelledProject,
    cli,
    cli_propose,
    create_only_project,
    labelled_project,
    land,
    land_deprecation,
    landing_zone,
    proposals_tree,
    reclassification,
)

from theurian.application import proposal_service
from theurian.daemon.runner import build_server

from mcp_wire_session import mcp_session  # isort: skip

pytestmark = pytest.mark.integration


def _mcp_draft(project: LabelledProject, tmp_path: Path) -> dict[str, Any]:
    """``knowledge.proposeChange`` with no ``expectedRevision`` and no sensitivity."""
    arguments: dict[str, Any] = {
        "projectId": "demo",
        "itemId": project.item_id,
        "title": "Authentication policy",
        "kind": "architecture",
        "owner": "platform-team",
        "author": "platform-team@example.com",
        "description": "Tighten the token lifetime.",
        "body": UPDATED_BODY,
        "contentType": "text/markdown",
        "evidence": EVIDENCE,
        "sourceAnchors": [{"provider": "git", "sourceUri": "git://demo/auth-policy.md"}],
    }
    with mcp_session(build_server(project.registry), tmp_path / "wire") as call:
        answer = call("knowledge.proposeChange", arguments)
    assert answer["result"]["isError"] is False, answer
    drafted: dict[str, Any] = answer["result"]["structuredContent"]
    return drafted


def _remedy(payload: dict[str, Any]) -> str:
    return str(payload.get("remedy", ""))


# -- (a) the combined refusal ------------------------------------------------------


def test_a_retired_create_only_item_is_not_sent_back_to_propose_by_the_combined_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``theurian propose`` for a retired item is refused, so "run it again" is a dead end."""
    project = create_only_project(tmp_path, monkeypatch, sensitivity="confidential")
    land_deprecation(project.root, SORTS_BEFORE_A_DRAFT, project.item_id)
    drafted = _mcp_draft(project, tmp_path)
    before = landing_zone(project.root)

    code, payload = cli("propose", "accept", drafted["proposalId"])

    assert code == 1, payload
    error = str(payload.get("error", ""))
    assert "lower the sensitivity" in error and "readmit" in error, payload
    assert "restoreItem" in _remedy(payload), payload
    assert "run `theurian propose`" not in _remedy(payload), payload
    assert landing_zone(project.root) == before, "a refused accept moved files"


def test_a_lowering_only_refusal_still_names_theurian_propose_for_an_item_that_is_not_retired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control for the test above: the lowering remedy is right for an approved item."""
    project = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    code, drafted = cli_propose(
        project, ITEM_ID, "--expected-revision", project.revision_id, "--sensitivity", "internal"
    )
    assert code == 0, drafted
    (project.root / f".theurian/migrations/{SORTS_BEFORE_A_DRAFT}-reclassify.yaml").write_text(
        reclassification(SORTS_BEFORE_A_DRAFT, ITEM_ID, "confidential")
    )
    commit_migrations(project.root)

    code, payload = cli("propose", "accept", drafted["proposalId"])

    assert code == 1, payload
    assert "lower the sensitivity" in str(payload.get("error", "")), payload
    assert "readmit" not in str(payload.get("error", "")), payload
    assert "run `theurian propose`" in _remedy(payload), payload
    assert "restoreItem" not in _remedy(payload), payload


# -- (b) the CLI fast path ---------------------------------------------------------


def test_a_retired_item_drafted_without_expected_revision_gets_the_retired_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cheap revision check runs first, and its remedy is a step the retired check refuses."""
    project = labelled_project(tmp_path, monkeypatch)
    land_deprecation(project.root, SORTS_BEFORE_A_DRAFT, project.item_id)
    proposals_before = proposals_tree(project.root)

    code, payload = cli_propose(project, project.item_id)

    assert code == 2, payload
    assert payload.get("error") == proposal_service.RETIRED_ITEM_MESSAGE, payload
    assert "restoreItem" in _remedy(payload), payload
    assert "Pass --expected-revision" not in _remedy(payload), payload
    assert proposals_tree(project.root) == proposals_before, "a refused draft wrote a proposal"


def test_an_approved_item_drafted_without_expected_revision_is_still_told_to_pass_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control for the test above: the revision remedy is right for an item that is not retired."""
    project = labelled_project(tmp_path, monkeypatch)
    proposals_before = proposals_tree(project.root)

    code, payload = cli_propose(project, project.item_id)

    assert code == 2, payload
    assert "already exists at revision" in str(payload.get("error", "")), payload
    assert f"Pass --expected-revision {project.revision_id}" in _remedy(payload), payload
    assert proposals_tree(project.root) == proposals_before


# -- (c) the union refusal ---------------------------------------------------------


def test_a_retired_item_with_a_revision_is_not_sent_to_the_revision_conflict_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both options the conflict remedy offers are refused for a retired item."""
    project = labelled_project(tmp_path, monkeypatch)
    land_deprecation(project.root, SORTS_BEFORE_A_DRAFT, project.item_id)
    drafted = _mcp_draft(project, tmp_path)
    assert drafted["expectedRevision"] is None, "the lookup must have answered like an absent id"
    before = landing_zone(project.root)

    code, payload = cli("propose", "accept", drafted["proposalId"])

    assert code == 1, payload
    assert f"Revision conflict on {ITEM_ID}" in str(payload.get("error", "")), payload
    assert "restoreItem" in _remedy(payload), payload
    assert "give the proposal's own migration `expectedRevision:" not in _remedy(payload), payload
    assert landing_zone(project.root) == before, "a refused accept moved files"


def test_a_retired_item_whose_revision_writer_replays_after_the_proposal_is_not_sent_to_the_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reversed order: the proposal has the smaller id, so the conflict is raised on the
    landed revision writer, and a retirement landed after it."""
    project = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    refused = retired_after_a_race(project)
    before = landing_zone(project.root)

    code, payload = cli("propose", "accept", refused["proposalId"])

    assert code == 1, payload
    assert f"Revision conflict on {ITEM_ID}" in str(payload.get("error", "")), payload
    assert "restoreItem" in _remedy(payload), payload
    assert "expectedRevision" not in _remedy(payload), payload
    assert landing_zone(project.root) == before, "a refused accept moved files"


def test_a_revision_conflict_on_an_approved_item_still_names_the_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control for the test above: two drafts of one revision, the second accepted last."""
    project = labelled_project(tmp_path, monkeypatch)
    first_code, first = cli_propose(project, ITEM_ID, "--expected-revision", project.revision_id)
    second_code, second = cli_propose(project, ITEM_ID, "--expected-revision", project.revision_id)
    assert (first_code, second_code) == (0, 0), (first, second)
    land(first["proposalId"])
    before = landing_zone(project.root)

    code, payload = cli("propose", "accept", second["proposalId"])

    assert code == 1, payload
    assert f"Revision conflict on {ITEM_ID}" in str(payload.get("error", "")), payload
    assert "Another change to this item landed first" in _remedy(payload), payload
    assert "restoreItem" not in _remedy(payload), payload
    assert landing_zone(project.root) == before, "a refused accept moved files"
