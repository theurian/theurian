"""Follow a printed removal cure exactly, and build a non-content proposal to refuse."""

from __future__ import annotations

import re
import shutil
from typing import Any, NamedTuple

import yaml
from label_inheritance_support import EVIDENCE, UPDATED_BODY, LabelledProject, cli

from theurian.daemon.runner import build_server

from mcp_wire_session import mcp_session  # isort: skip


class Followed(NamedTuple):
    deleted: bool
    code: int
    payload: Any


_NAMED_IDS = re.compile(r"every operation whose `itemId` is `([^`]+)`")
_FILE_IN = re.compile(r"migration file in (\S+)/,")
_DELETE = re.compile(r"if those are all of its operations, delete (\S+)/ instead\.")


def follow_removal_cure(p: LabelledProject, drafted: dict[str, Any], remedy: str) -> Followed:
    """Perform the removal cure a remedy prints, reading nothing but the remedy.

    The ids and the directory are parsed out of its words; the operations removed are
    exactly those whose ``itemId`` is a parsed id; with none left the parsed directory is
    deleted, else ``theurian propose accept`` runs again for ``drafted``.
    """
    ids = _NAMED_IDS.findall(remedy)
    in_file, delete = _FILE_IN.search(remedy), _DELETE.search(remedy)
    assert ids and in_file and delete and in_file[1] == delete[1], remedy
    folder = p.root / in_file[1]
    assert folder.is_dir(), remedy
    (staged,) = folder.glob("*.yaml")
    document = yaml.safe_load(staged.read_text(encoding="utf-8"))
    document["operations"] = [o for o in document["operations"] if o.get("itemId") not in ids]
    if not document["operations"]:
        shutil.rmtree(folder)
        return Followed(True, 0, {})
    staged.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return Followed(False, *cli("propose", "accept", drafted["proposalId"]))


def with_operations(
    p: LabelledProject,
    drafted: dict[str, Any],
    operations: list[dict[str, Any]],
    *depends_on: str,
) -> dict[str, Any]:
    """Replace the operations of a staged proposal, and declare ``dependsOn`` if given."""
    staged = p.root / drafted["proposalDirectory"] / drafted["migrationFile"]
    document = yaml.safe_load(staged.read_text(encoding="utf-8"))
    document["operations"] = operations
    if depends_on:
        document["dependsOn"] = list(depends_on)
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
