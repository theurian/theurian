"""Histories where ``D2`` undoes ``D1``, replaying after it only if ``dependent``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from label_inheritance_support import (
    EVIDENCE,
    ROOT_MIGRATION_ID,
    SORTS_BEFORE_A_DRAFT,
    LabelledProject,
    cli_ok,
    cli_propose,
    deprecation,
    reclassification,
)

from theurian.daemon.runner import build_server

from mcp_wire_session import mcp_session  # isort: skip

D1 = SORTS_BEFORE_A_DRAFT
D2 = "01K1CCCCCC01234567890ABCDE"
#: Sorts after a draft minted today, and below ``SORTS_AFTER_A_DRAFT``.
ABOVE_A_DRAFT = "7ZZZZZZZZZ01234567890ABCDD"
OTHER_ITEM = "architecture.rate-limits"
FACES = ("sensitivity", "status")
ACCEPTED = {"sensitivity": "confidential", "status": "deprecated"}


def depending_on(text: str, *ids: str) -> str:
    edges = "".join(f"  - {i}\n" for i in ids)
    return text.replace("operations:", f"dependsOn:\n{edges}operations:", 1)


def create_other(migration_id: str, item_id: str) -> str:
    """A second item, internal, for histories that move one item while another is raised."""
    text = reclassification(migration_id, item_id, "internal").replace(
        "changeSensitivity", "createItem"
    )
    return text.replace(
        "    reason: reclassified after review\n",
        "    kind: architecture\n    namespace: backend\n    owner: platform-team\n",
    )


def restoration(migration_id: str, item_id: str) -> str:
    return deprecation(migration_id, item_id).replace("deprecateItem", "restoreItem")


def write(root: Path, migration_id: str, name: str, text: str) -> None:
    (root / f".theurian/migrations/{migration_id}-{name}.yaml").write_text(text)


def history(  # noqa: PLR0913 -- the shapes a history can take
    p: LabelledProject,
    face: str,
    *,
    dependent: bool = True,
    relabel: str = "internal",
    chained: bool = False,
    ids: tuple[str, str] = (D1, D2),
) -> None:
    """The first of ``ids`` raises (or deprecates) the item; the second relabels (or restores) it.

    ``chained`` makes the first depend on the root, so it replays after a fresh draft.
    """
    first_id, second_id = ids
    if face == "sensitivity":
        first, second = (
            reclassification(first_id, p.item_id, "confidential"),
            reclassification(second_id, p.item_id, relabel),
        )
    else:
        first, second = deprecation(first_id, p.item_id), restoration(second_id, p.item_id)
    write(p.root, first_id, "first", depending_on(first, ROOT_MIGRATION_ID) if chained else first)
    write(p.root, second_id, "second", depending_on(second, first_id) if dependent else second)
    cli_ok("migrate", "apply")


def draft(p: LabelledProject, face: str) -> dict[str, Any]:
    if face == "sensitivity":
        code, proposal = cli_propose(
            p, p.item_id, "--expected-revision", p.revision_id, "--sensitivity", "confidential"
        )
        assert code == 0, proposal
        return proposal
    operation = {"op": "deprecateItem", "itemId": p.item_id, "reason": "Superseded"}
    document = {"author": "platform-team@example.com", "operations": [operation]}
    with mcp_session(build_server(p.registry), p.root.parent / "wire") as call:
        answer = call(
            "knowledge.generateMigrationDraft",
            {"projectId": "demo", "document": document, "evidence": EVIDENCE},
        )
    assert answer["result"]["isError"] is False, answer
    drafted: dict[str, Any] = answer["result"]["structuredContent"]
    return drafted


def declare_dependency(p: LabelledProject, drafted: dict[str, Any], *ids: str) -> None:
    """``theurian propose`` has no ``dependsOn`` option, so it is hand-edited in."""
    staged = p.root / drafted["proposalDirectory"] / drafted["migrationFile"]
    document = yaml.safe_load(staged.read_text(encoding="utf-8"))
    document["dependsOn"] = list(ids)
    staged.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def get_is_withheld(p: LabelledProject, **extra: object) -> bool:
    """Whether ``knowledge.get`` refuses the item; *extra* joins its arguments."""
    arguments = {"projectId": "demo", "itemId": p.item_id, **extra}
    with mcp_session(build_server(p.registry), p.root.parent / "wire") as call:
        answer = call("knowledge.get", arguments)
    return bool(answer["result"]["isError"])
