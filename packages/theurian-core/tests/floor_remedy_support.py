"""Follow a printed removal cure exactly, and build a non-content proposal to refuse."""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any, NamedTuple

import yaml
from label_inheritance_support import (
    EVIDENCE,
    SORTS_AFTER_A_DRAFT,
    UPDATED_BODY,
    LabelledProject,
    cli,
    cli_ok,
    cli_propose,
    land_deprecation,
)

from theurian.application.proposal_service import _ProposalLocation
from theurian.daemon.runner import build_server

from mcp_wire_session import mcp_session  # isort: skip


class Followed(NamedTuple):
    deleted: bool
    code: int


#: A tracked proposal's location, for tests that need one and never touch the disk.
TRACKED_LOCATION = _ProposalLocation(Path("/p/x"), ".theurian/proposals/P1", False)

_NAMED_IDS = re.compile(r"every operation whose `itemId` is `([^`]+)`")
_FILE_IN = re.compile(r"migration file in (\S+)/,")
_BODIES = re.compile(r"delete the body files those operations drafted, ((?:`[^`]+`(?: and )?)+)")
_DELETE = re.compile(r"if those are all of its operations, delete (\S+)/ instead\.")


def follow_removal_cure(p: LabelledProject, drafted: dict[str, Any], remedy: str) -> Followed:
    """Perform the removal cure a remedy prints, reading nothing but the remedy.

    The ids and the directory are parsed out of its words; the operations removed are
    exactly those whose ``itemId`` is a parsed id and every body file it lists is deleted;
    with none left the parsed directory is deleted, else ``theurian propose accept`` runs
    again for ``drafted``.
    """
    ids = _NAMED_IDS.findall(remedy)
    in_file, delete = _FILE_IN.search(remedy), _DELETE.search(remedy)
    assert ids and in_file and delete and in_file[1] == delete[1], remedy
    folder = p.root / in_file[1]
    assert folder.is_dir(), remedy
    if "delete the body file" in remedy:
        listed = _BODIES.search(remedy)
        assert listed, remedy
        for body in re.findall(r"`([^`]+)`", listed[1]):
            (p.root / body).unlink()
    (staged,) = folder.glob("*.yaml")
    document = yaml.safe_load(staged.read_text(encoding="utf-8"))
    document["operations"] = [o for o in document["operations"] if o.get("itemId") not in ids]
    if not document["operations"]:
        shutil.rmtree(folder)
        return Followed(True, 0)
    staged.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return Followed(False, cli("propose", "accept", drafted["proposalId"])[0])


def staged_migration(p: LabelledProject, drafted: dict[str, Any]) -> Path:
    """The migration file a draft staged under its proposal directory."""
    staged: Path = p.root / drafted["proposalDirectory"] / drafted["migrationFile"]
    return staged


def with_operations(
    p: LabelledProject, drafted: dict[str, Any], operations: list[dict[str, Any]]
) -> dict[str, Any]:
    """Replace the operations of a staged proposal."""
    staged = staged_migration(p, drafted)
    document = yaml.safe_load(staged.read_text(encoding="utf-8"))
    document["operations"] = operations
    staged.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return drafted


def mcp_draft(p: LabelledProject) -> dict[str, Any]:
    """``knowledge.proposeChange`` with no ``expectedRevision``: how a retired item is drafted."""
    arguments = {
        "projectId": "demo",
        "itemId": p.item_id,
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
    with mcp_session(build_server(p.registry), p.root.parent / "wire") as call:
        answer = call("knowledge.proposeChange", arguments)
    assert answer["result"]["isError"] is False, answer
    drafted: dict[str, Any] = answer["result"]["structuredContent"]
    return drafted


def retired_after_a_race(p: LabelledProject) -> dict[str, Any]:
    """Draft P, then land a later draft Q and a retirement with a larger id; P is returned.

    P replays before Q, so the union's conflict is raised on the landed Q, not on P.
    """
    drafts = []
    for _ in range(2):
        code, drafted = cli_propose(
            p, p.item_id, "--expected-revision", p.revision_id, "--sensitivity", "internal"
        )
        assert code == 0, drafted
        drafts.append(drafted)
        time.sleep(0.05)  # ULIDs sort by millisecond
    cli_ok("propose", "accept", drafts[1]["proposalId"])
    cli_ok("migrate", "apply")
    land_deprecation(p.root, SORTS_AFTER_A_DRAFT, p.item_id)
    return drafts[0]
