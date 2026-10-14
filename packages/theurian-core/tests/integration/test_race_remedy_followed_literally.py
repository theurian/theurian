"""Each alternative the race remedy prints, followed as printed, lands the item (#931, #937).

The race is drafts of one revision. The edit and revision-name tests parametrize the refused
proposal's position before, between or after the landed revision writers; the redraft and
removal tests parametrize two orders. Routes: content, or non-content (a content draft plus a
hand-added ``changeSensitivity`` of a second item); landed applied or not.
Nothing here derives a value the remedy does not print.
"""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any, NamedTuple

import pytest
import yaml
from floor_remedy_support import follow_removal_cure, staged_migration, with_operations
from label_inheritance_support import (
    LabelledProject,
    cli,
    cli_ok,
    cli_propose,
    item_row,
    labelled_project,
    staged_upsert,
)
from replay_order_support import OTHER_ITEM, create_other, write

pytestmark = pytest.mark.integration

_ULID = "[0-9A-HJKMNP-TV-Z]{26}"
_EXPECT, _FLAG = (
    re.compile(f"`expectedRevision: ({_ULID})`"),
    re.compile(f"--expected-revision ({_ULID})"),
)
_DEPENDS, _DELETE = re.compile(r"`dependsOn: \[([^\]]*)\]`"), re.compile(r"and delete (\S+)/\.")
_REDRAFT = "draft it again afterwards with `theurian propose`"

ORDERS = pytest.mark.parametrize("land_first", ["larger", "smaller"])
#: The refused proposal's id before, after or between the landed revision writers.
POSITIONS = pytest.mark.parametrize("land_first", ["larger", "smaller", "between"])
APPLIED = pytest.mark.parametrize("applied", [True, False], ids=["applied", "unapplied"])
ROUTES = pytest.mark.parametrize("noncontent", [False, True], ids=["content", "non-content"])


class Raced(NamedTuple):
    project: LabelledProject
    landed: dict[str, Any]
    landed_revision: str
    refused: dict[str, Any]
    remedy: str


def _draft(p: LabelledProject) -> dict[str, Any]:
    code, drafted = cli_propose(
        p, p.item_id, "--expected-revision", p.revision_id, "--sensitivity", "internal"
    )
    assert code == 0, drafted
    return drafted


def _race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    land_first: str,
    noncontent: bool,
    applied: bool,
) -> Raced:
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    if noncontent:
        write(
            p.root,
            "01K1AAAAAB01234567890ABCDE",
            "o",
            create_other("01K1AAAAAB01234567890ABCDE", OTHER_ITEM),
        )
        cli_ok("migrate", "apply")
    drafts = []
    for _ in range(3 if land_first == "between" else 2):
        drafts.append(_draft(p))
        time.sleep(0.05)  # ULIDs sort by millisecond
    if land_first == "between":
        early, refused, landed = drafts
        steps = yaml.safe_load(staged_migration(p, landed).read_text(encoding="utf-8"))[
            "operations"
        ]
        for step in steps:
            if step["op"] == "upsertRevision":
                step["expectedRevision"] = staged_upsert(p.root, early)["revisionId"]
        with_operations(p, landed, steps)
        cli_ok("propose", "accept", early["proposalId"])
    else:
        landed, refused = (drafts[1], drafts[0]) if land_first == "larger" else drafts
    if noncontent:
        operations = yaml.safe_load(staged_migration(p, refused).read_text(encoding="utf-8"))[
            "operations"
        ]
        extra = {
            "op": "changeSensitivity",
            "itemId": OTHER_ITEM,
            "sensitivity": "confidential",
            "reason": "r",
        }
        with_operations(p, refused, [*operations, extra])
    landed_revision = staged_upsert(p.root, landed)["revisionId"]
    cli_ok("propose", "accept", landed["proposalId"])
    if applied:
        cli_ok("migrate", "apply")
    code, payload = cli("propose", "accept", refused["proposalId"])
    assert code == 1 and "Revision conflict" in payload["error"], payload
    return Raced(p, landed, landed_revision, refused, str(payload["remedy"]))


def _current(p: LabelledProject) -> str:
    return str(item_row(p.root, p.item_id)["current_revision_id"])


@POSITIONS
@APPLIED
@ROUTES
def test_giving_the_refused_migration_the_printed_revision_and_writer_lands_its_revision(
    land_first: str,
    noncontent: bool,
    applied: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Population: the proposal's position before (`larger`), after (`smaller`) or between
    (`between`) the landed writers of the named revision. `dependsOn` is printed exactly when
    that writer replays after the proposal; without it the edit conflicts on the writer."""
    r = _race(tmp_path, monkeypatch, land_first, noncontent, applied)
    revision, writers = _EXPECT.search(r.remedy), _DEPENDS.search(r.remedy)
    assert revision, r.remedy
    assert (writers is not None) == (land_first != "smaller"), r.remedy
    own = staged_upsert(r.project.root, r.refused)["revisionId"]
    staged = staged_migration(r.project, r.refused)
    document = yaml.safe_load(staged.read_text(encoding="utf-8"))
    for operation in document["operations"]:
        if operation["op"] == "upsertRevision" and operation["itemId"] == r.project.item_id:
            operation["expectedRevision"] = revision[1]
    if writers:
        document["dependsOn"] = writers[1].split(", ")
        assert document["dependsOn"] == [r.landed["migrationId"]]
    staged.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    cli_ok("propose", "accept", r.refused["proposalId"])
    cli_ok("migrate", "apply")

    assert _current(r.project) == own


@POSITIONS
@APPLIED
@ROUTES
def test_the_revision_the_remedy_names_is_the_one_the_landed_draft_wrote(
    land_first: str,
    noncontent: bool,
    applied: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The landed draft's own revisionId, read from its migration file before it landed; for
    `between` the second writer's."""
    r = _race(tmp_path, monkeypatch, land_first, noncontent, applied)

    named = set(_EXPECT.findall(r.remedy) + _FLAG.findall(r.remedy))

    assert named == {r.landed_revision}, r.remedy


@ORDERS
@APPLIED
def test_redrafting_with_the_printed_revision_after_deleting_the_proposal_lands_the_redraft(
    land_first: str, applied: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = _race(tmp_path, monkeypatch, land_first, False, applied)
    flag, directory = _FLAG.search(r.remedy), _DELETE.search(r.remedy)
    assert flag and directory, r.remedy
    shutil.rmtree(r.project.root / directory[1])

    code, redraft = cli_propose(r.project, r.project.item_id, "--expected-revision", flag[1])
    assert code == 0, redraft
    cli_ok("propose", "accept", redraft["proposalId"])
    cli_ok("migrate", "apply")

    assert _current(r.project) == redraft["revisionId"]


@ORDERS
@APPLIED
def test_the_printed_removal_cure_then_its_redraft_line_ends_at_the_new_revision(
    land_first: str, applied: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#937: the removal drops the proposal's content update; the printed redraft line is the
    only way back to it, and its first command is a bare `theurian propose`."""
    r = _race(tmp_path, monkeypatch, land_first, True, applied)
    assert _REDRAFT in r.remedy, r.remedy
    cure = follow_removal_cure(r.project, r.refused, r.remedy)
    assert not cure.deleted and cure.code == 0, cure
    cli_ok("migrate", "apply")
    assert _current(r.project) == r.landed_revision
    assert item_row(r.project.root, OTHER_ITEM)["sensitivity"] == "confidential"

    code, refusal = cli_propose(r.project, r.project.item_id)
    flag = _FLAG.search(str(refusal.get("remedy")))
    assert code == 2 and flag, refusal
    code, redraft = cli_propose(r.project, r.project.item_id, "--expected-revision", flag[1])
    assert code == 0, redraft
    cli_ok("propose", "accept", redraft["proposalId"])
    cli_ok("migrate", "apply")

    assert _current(r.project) == redraft["revisionId"]
    assert item_row(r.project.root, r.project.item_id)["sensitivity"] == "internal"


@pytest.mark.parametrize("created", [True, False], ids=["item-created", "item-absent"])
def test_a_proposal_that_conflicts_with_itself_is_cured_by_the_revision_its_message_reports(
    created: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two upserts of the item and no revision landed for it: this reaches the #950 two-upsert
    guard, not the no-revision fallback. The message's `store holds` value is the one to follow."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    if created:
        write(
            p.root,
            "01K1AAAAAB01234567890ABCDE",
            "o",
            create_other("01K1AAAAAB01234567890ABCDE", OTHER_ITEM),
        )
        cli_ok("migrate", "apply")
    code, drafted = cli_propose(p, OTHER_ITEM)
    assert code == 0, drafted
    operations = yaml.safe_load(staged_migration(p, drafted).read_text(encoding="utf-8"))[
        "operations"
    ]
    first = next(o for o in operations if o["op"] == "upsertRevision")
    body = p.root / drafted["bodyFile"]
    shutil.copy(body, body.with_name("second.md"))
    second = {
        **first,
        "revisionId": "01K1AAAREV99999999999ABCDE",
        "contentFile": "../knowledge/architecture/second.md",
    }
    with_operations(p, drafted, [*operations, second])
    code, payload = cli("propose", "accept", drafted["proposalId"])
    assert (
        code == 1
        and "give the proposal's own migration the expectedRevision the message reports"
        in payload["remedy"]
    ), payload
    held = re.search(r"store holds (\S+)", payload["error"])
    assert held, payload

    second["expectedRevision"] = held[1]
    with_operations(p, drafted, [first, second])
    cli_ok("propose", "accept", drafted["proposalId"])
    cli_ok("migrate", "apply")

    assert item_row(p.root, OTHER_ITEM)["current_revision_id"] == second["revisionId"]


def test_a_two_upsert_proposal_over_a_landed_revision_gets_the_message_reports_wording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#950: its own first upsert is the revision the store holds, so a landed-writer edit
    (`expectedRevision: <R0>` and `dependsOn`) is a cure that exits 1."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    drafted = _draft(p)
    operations = yaml.safe_load(staged_migration(p, drafted).read_text(encoding="utf-8"))[
        "operations"
    ]
    first = next(o for o in operations if o["op"] == "upsertRevision")
    body = p.root / drafted["bodyFile"]
    shutil.copy(body, body.with_name("second.md"))
    second = {
        **first,
        "revisionId": "01K1AAAREV99999999999ABCDE",
        "contentFile": "../knowledge/architecture/second.md",
    }
    with_operations(p, drafted, [*operations, second])

    code, payload = cli("propose", "accept", drafted["proposalId"])

    remedy = str(payload["remedy"])
    assert code == 1 and "the expectedRevision the message reports" in remedy, payload
    assert "dependsOn" not in remedy and not _EXPECT.search(remedy), remedy
    held = re.search(r"store holds (\S+)", payload["error"])
    assert held, payload
    second["expectedRevision"] = held[1]
    with_operations(p, drafted, [first, second])
    cli_ok("propose", "accept", drafted["proposalId"])
    cli_ok("migrate", "apply")
    assert _current(p) == second["revisionId"]
