"""T-28 residual 8, a recorded gap: a withdrawal to ``draft`` or ``proposed`` is no tightening
by ``may_surface(..., include_unapproved=True)``, so a ``dependsOn`` restore replaying after it
is no ``reorders`` row and is not refused. RED, deliberately, when the gap is closed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from label_inheritance_support import (
    ITEM_ID,
    ROOT_MIGRATION_ID,
    ROOT_REVISION_ID,
    cli,
    cli_ok,
    cli_propose,
    item_row,
    labelled_project,
    root_migration,
)
from replay_order_support import D1, depending_on, get_is_withheld, restoration, write

pytestmark = pytest.mark.integration

LARGER = "01K1DDDDDD01234567890ABCDE"
STATUSES = pytest.mark.parametrize("status", ["draft", "proposed"])


def _in_place_withdrawal(status: str) -> str:
    """The root migration's upsert alone, re-id'd, restating the current revision as ``status``."""
    text = root_migration(
        item_id=ITEM_ID,
        revision_id=ROOT_REVISION_ID,
        namespace="backend",
        sensitivity="internal",
        trust_level="reviewed",
        status=status,
    ).replace(ROOT_MIGRATION_ID, LARGER)
    return text[: text.index("  - op: createItem")] + text[text.index("  - op: upsertRevision") :]


@STATUSES
def test_a_restore_after_a_larger_id_withdrawal_to_an_unapproved_status_is_no_row(
    status: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    write(p.root, D1, "restore", depending_on(restoration(D1, p.item_id), ROOT_MIGRATION_ID))
    write(p.root, LARGER, "withdraw", _in_place_withdrawal(status))

    applied = cli_ok("migrate", "apply")

    assert applied["permissiveMoves"] == []
    assert cli_ok("migrate", "validate")["permissiveMoves"] == []
    assert item_row(p.root, p.item_id)["status"] == "approved"
    assert not get_is_withheld(p)


@STATUSES
def test_accept_lands_a_withdrawal_to_an_unapproved_status_a_landed_restore_replays_after(
    status: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    write(p.root, D1, "restore", depending_on(restoration(D1, p.item_id), ROOT_MIGRATION_ID))
    cli_ok("migrate", "apply")
    code, drafted = cli_propose(p, p.item_id, "--expected-revision", p.revision_id)
    assert code == 0, drafted
    staged = p.root / drafted["proposalDirectory"] / drafted["migrationFile"]
    document = yaml.safe_load(staged.read_text(encoding="utf-8"))
    for operation in document["operations"]:
        if operation["op"] == "upsertRevision":
            operation["metadata"]["status"] = status
    staged.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    code, payload = cli("propose", "accept", drafted["proposalId"])

    assert code == 0, payload
