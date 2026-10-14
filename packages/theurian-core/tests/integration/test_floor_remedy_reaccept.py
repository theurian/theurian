"""The re-accept route of #915: the floor's printed cure after a replay-order edit.

Every followed-literally test is a first accept. Here the first accept is refused by the
replay-order check, the proposal is edited in exactly the ``dependsOn`` that remedy names,
and only the re-accept reaches the lowering floor, whose printed removal is then followed.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
import yaml
from floor_remedy_support import follow_removal_cure, staged_migration
from label_inheritance_support import (
    BODY,
    ROOT_MIGRATION_ID,
    LabelledProject,
    cli,
    cli_ok,
    item_row,
    labelled_project,
    reclassification,
    root_migration,
)
from replay_order_support import OTHER_ITEM, depending_on, draft, get_is_withheld, write

from theurian.infrastructure.determinism import UlidGenerator

pytestmark = pytest.mark.integration

DEPENDS = re.compile(r"`dependsOn: \[([^\]]*)\]`")


def _mid() -> str:
    time.sleep(0.01)  # ULIDs sort by millisecond
    return UlidGenerator().new_ulid().value


def _land_item(p: LabelledProject, item: str, sens: str, *, expected: str | None = None) -> str:
    """A content migration for ``item``: a fresh item, or a revision of it with ``expected``."""
    mid, tag = _mid(), f"body-{time.monotonic_ns()}"
    (p.root / f".theurian/knowledge/architecture/{tag}.md").write_text(BODY)
    text = root_migration(
        item_id=item, revision_id=_mid(), namespace="backend", sensitivity=sens,
        trust_level="reviewed", status="approved",
    )  # fmt: skip
    text = text.replace(ROOT_MIGRATION_ID, mid).replace("auth-policy.md", f"{tag}.md")
    if expected is not None:
        text = re.sub(r"  - op: createItem\n(?:    .*\n)+", "", text)
        text = text.replace(
            "    contentFile:", f"    expectedRevision: {expected}\n    contentFile:"
        )
    write(p.root, mid, tag, text)
    return mid


def _raise(item: str) -> dict[str, str]:
    return {"op": "changeSensitivity", "itemId": item, "sensitivity": "internal", "reason": "r"}


@pytest.mark.parametrize("order", ["newer", "older"])
@pytest.mark.parametrize("kept", [False, True], ids=["own-item-only", "second-item-kept"])
def test_a_reaccept_the_floor_refuses_is_cured_by_the_printed_removal(
    order: str, kept: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Residual 11 / action 3: the re-accept's cure was only ever measured from a first accept."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="public")
    _land_item(p, OTHER_ITEM, "internal")
    cli_ok("migrate", "apply")
    older_tightener = _mid() if order == "older" else None
    drafted = draft(p, "status")
    staged = staged_migration(p, drafted)
    document = yaml.safe_load(staged.read_text(encoding="utf-8"))
    raises = [_raise(p.item_id)] + [{**_raise(OTHER_ITEM), "sensitivity": "confidential"}] * kept
    document["operations"] = raises
    staged.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    lowering = _land_item(p, p.item_id, "public", expected=p.revision_id)
    tightener = older_tightener or _mid()
    write(
        p.root,
        tightener,
        "set",
        depending_on(reclassification(tightener, p.item_id, "confidential"), lowering),
    )
    cli_ok("migrate", "apply")

    code, first = cli("propose", "accept", drafted["proposalId"])
    named = DEPENDS.search(str(first.get("remedy")))
    assert code == 1 and named, first
    document["dependsOn"] = named[1].split(", ")
    staged.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    code, again = cli("propose", "accept", drafted["proposalId"])
    assert code == 1 and "would lower the sensitivity of" in str(again["error"]), again
    cure = follow_removal_cure(p, drafted, str(again["remedy"]))
    cli_ok("migrate", "apply")

    assert cure.code == 0 and cure.deleted is not kept
    assert item_row(p.root, p.item_id)["sensitivity"] == "confidential"
    assert get_is_withheld(p)
    folder = p.root / drafted["proposalDirectory"]
    if kept:
        assert item_row(p.root, OTHER_ITEM)["sensitivity"] == "confidential"
        assert get_is_withheld(p, itemId=OTHER_ITEM)
        assert [f.name for f in folder.rglob("*") if f.is_file()] == ["evidence.json"]
    else:
        assert not folder.exists()
