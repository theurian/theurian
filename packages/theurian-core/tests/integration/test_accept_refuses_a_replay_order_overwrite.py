"""``propose accept`` refuses a proposal that a landed migration replaying after it
would leave looser.

The guard refuses, with exit 1 and nothing moved, when the union replay ends a
gate field the proposal P wrote looser than P left it, whatever the id of the landed
migration that loosened it. The predicate is 0.5.1's floors': the class for
sensitivity, ``may_surface(..., include_unapproved=True)`` for status. The remedy is
``_redraft_remedy``'s and names ``dependsOn`` every landed migration that writes the field and
replays after P.

Which check speaks depends on the rows (GHSA-wwq9). Where accepting P would add a report
row the landed migrations alone do not report, a ``reorders`` row included, or would make a
row the history already holds name a different migration in ``undoes``, the report check
refuses first, in its words ("undoing what this proposal sets"): for instance, a smaller-id
``dependsOn`` lowering replaying after P's raise, with no id larger than P's having written
the field. Otherwise the end-state check's words ("loosen what it sets") remain.

P3: for the migration set ``accept`` replays, every floor-read label an accepted proposal
writes (sensitivity class; status surfaceability via ``may_surface(...,
include_unapproved=True)``) is, by those predicates, no looser after that replay than the
proposal left it. P3 is scoped to accepted proposals.

Not tested: a proposal that loosens, then a landed migration that tightens. A proposal
cannot loosen past 0.5.1's floors, so accept never reaches it. The tests drive the CLI
only: the guard's helper has no contract to pin yet.
"""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from label_inheritance_support import (
    BODY,
    ROOT_MIGRATION_ID,
    SORTS_AFTER_A_DRAFT,
    LabelledProject,
    cli,
    cli_ok,
    cli_propose,
    deprecation,
    item_row,
    labelled_project,
    land_reclassification,
    landing_zone,
    reclassification,
    root_migration,
    staged_metadata,
)
from mcp_wire_session import mcp_session
from replay_order_support import (
    ABOVE_A_DRAFT,
    ACCEPTED,
    D1,
    D2,
    FACES,
    OTHER_ITEM,
    create_other,
    declare_dependency,
    depending_on,
    draft,
    get_is_withheld,
    history,
    restoration,
    write,
)

from theurian.daemon.runner import build_server
from theurian.infrastructure.determinism import UlidGenerator

pytestmark = pytest.mark.integration

REPORT_CHECK, END_STATE_CHECK = "undoing what this proposal sets", "loosen what it sets"

LARGER_IDS = (ABOVE_A_DRAFT, SORTS_AFTER_A_DRAFT)


@pytest.mark.parametrize("face", FACES)
def test_accept_refuses_and_names_the_overwriting_migration_and_the_route(
    face: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D2 replays after the proposal with a smaller id: the report check's words, and the
    remedy never says "accept again"."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    history(p, face)
    drafted = draft(p, face)
    before = landing_zone(p.root)

    code, payload = cli("propose", "accept", drafted["proposalId"])

    assert code == 1, payload
    assert all(named in str(payload["error"]) for named in (D2, p.item_id, face)), payload
    assert REPORT_CHECK in str(payload["error"]), payload
    assert END_STATE_CHECK not in str(payload["error"]), payload
    assert f"dependsOn: [{D2}]" in str(payload["remedy"]), payload
    assert not re.search(r"accept[^.]*again", str(payload["remedy"]), re.IGNORECASE), payload
    assert landing_zone(p.root) == before, "a refused accept moved files"


@pytest.mark.parametrize("face", FACES)
def test_a_fresh_draft_declaring_the_named_dependson_is_accepted_and_its_label_survives(
    face: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal comes first: the follow-through alone passes without the refusal."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    history(p, face)
    code, refused = cli("propose", "accept", draft(p, face)["proposalId"])
    assert code == 1, refused

    fresh = draft(p, face)
    declare_dependency(p, fresh, D2)
    cli_ok("propose", "accept", fresh["proposalId"])
    cli_ok("migrate", "apply")

    assert item_row(p.root, p.item_id)[face] == ACCEPTED[face]
    assert get_is_withheld(p)


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(((D1, D2), REPORT_CHECK, END_STATE_CHECK), id="smaller-id-restatement"),
        pytest.param((LARGER_IDS, END_STATE_CHECK, REPORT_CHECK), id="larger-id-restatement"),
    ],
)
@pytest.mark.parametrize("face", FACES)
def test_a_restatement_after_the_proposal_hides_no_later_loosening(
    face: str,
    case: tuple[tuple[str, str], str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first waits on the root, so it replays after the proposal and restates its label.

    Whatever its id, the second's loosening leaves the field looser than the proposal did.
    ``case`` is the ids, the words that speak and the words that stay silent.
    """
    ids, speaks, silent = case
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    history(p, face, chained=True, ids=ids)
    drafted = draft(p, face)
    before = landing_zone(p.root)

    code, payload = cli("propose", "accept", drafted["proposalId"])

    assert code == 1, payload
    assert ids[1] in str(payload["error"]), payload
    assert speaks in str(payload["error"]), payload
    assert silent not in str(payload["error"]), payload
    assert landing_zone(p.root) == before, "a refused accept moved files"


def _lowered_then_raised_back(
    p: LabelledProject, face: str, ids: tuple[str, str]
) -> dict[str, Any]:
    """A draft, then a landed lowering that a second landed migration raises back."""
    drafted = draft(p, face)
    first_id, second_id = ids
    if face == "sensitivity":
        lower = reclassification(first_id, p.item_id, "internal")
        raise_back = reclassification(second_id, p.item_id, "confidential")
    else:
        lower, raise_back = restoration(first_id, p.item_id), deprecation(second_id, p.item_id)
    write(p.root, first_id, "lower", depending_on(lower, ROOT_MIGRATION_ID))
    write(p.root, second_id, "raise-back", depending_on(raise_back, first_id))
    cli_ok("migrate", "apply")
    return drafted


@pytest.mark.parametrize("face", FACES)
def test_a_larger_id_loosening_that_a_landed_migration_raises_back_is_accepted(
    face: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first lowers the field below the proposal's, the second raises it back, both ids
    larger: the union ends at the proposal's own label and no row is made.

    The status face pins that a status raise clears the loosening as a class raise does.
    """
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    drafted = _lowered_then_raised_back(p, face, LARGER_IDS)

    code, payload = cli("propose", "accept", drafted["proposalId"])

    assert code == 0, payload


@pytest.mark.parametrize("face", FACES)
def test_a_smaller_id_loosening_that_a_landed_migration_raises_back_is_refused_for_its_row(
    face: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both ids are smaller, so the lowering replays after the proposal and is a ``reorders``
    row the accept would add, permanently, even though the field ends at the proposal's own
    label: accept never introduces a report row."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    drafted = _lowered_then_raised_back(p, face, (D1, D2))
    before = landing_zone(p.root)

    code, payload = cli("propose", "accept", drafted["proposalId"])

    assert code == 1, payload
    assert REPORT_CHECK in str(payload["error"]), payload
    assert f"dependsOn: [{D1}, {D2}]" in str(payload["remedy"]), payload
    assert landing_zone(p.root) == before, "a refused accept moved files"


def test_a_raise_of_another_item_does_not_forget_this_items_loosening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loosening is keyed by item as well as field: a later migration raising a
    different item's class must not read as this item being raised back."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    drafted = draft(p, "sensitivity")
    created = "01K1AAAAAB01234567890ABCDE"
    write(p.root, created, "other", create_other(created, OTHER_ITEM))
    lower = reclassification(D1, p.item_id, "internal")
    write(p.root, D1, "lower", depending_on(lower, ROOT_MIGRATION_ID))
    raise_other = reclassification(D2, OTHER_ITEM, "confidential")
    write(p.root, D2, "raise-other", depending_on(raise_other, D1))
    cli_ok("migrate", "apply")
    before = landing_zone(p.root)

    code, payload = cli("propose", "accept", drafted["proposalId"])

    assert code == 1, payload
    assert REPORT_CHECK in str(payload["error"]), payload
    assert END_STATE_CHECK not in str(payload["error"]), payload
    assert landing_zone(p.root) == before, "a refused accept moved files"


def test_the_report_check_speaks_first_when_both_checks_would_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A withdrawal drafted before a landed update is undone by it: the report check's
    refusal, and its words, are the ones a caller sees; the end-state check would also refuse."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    drafted = draft(p, "status")
    code, update = cli_propose(p, p.item_id, "--expected-revision", p.revision_id)
    assert code == 0, update
    cli_ok("propose", "accept", update["proposalId"])
    cli_ok("migrate", "apply")
    before = landing_zone(p.root)

    code, payload = cli("propose", "accept", drafted["proposalId"])

    assert code == 1, payload
    assert REPORT_CHECK in str(payload["error"]), payload
    assert END_STATE_CHECK not in str(payload["error"]), payload
    assert landing_zone(p.root) == before, "a refused accept moved files"


def test_a_content_update_minted_before_a_landed_larger_id_declassification_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deliberate, safe-side behaviour change: the update asserts the label it inherited.

    A drafted update writes its labels, so one minted before a landed
    declassification with a larger id restates the old class just ahead of it. The
    remedy's redraft inherits the new label and is accepted.
    """
    p = labelled_project(tmp_path, monkeypatch, sensitivity="confidential")
    code, stale = cli_propose(p, p.item_id, "--expected-revision", p.revision_id)
    assert code == 0, stale
    land_reclassification(p.root, SORTS_AFTER_A_DRAFT, p.item_id, "internal")
    before = landing_zone(p.root)

    code, payload = cli("propose", "accept", stale["proposalId"])

    assert code == 1, payload
    assert "loosen what it sets" in str(payload["error"]), "no report row: the check's own"
    assert landing_zone(p.root) == before, "a refused accept moved files"
    assert f"dependsOn: [{SORTS_AFTER_A_DRAFT}]" in str(payload["remedy"]), payload
    code, fresh = cli_propose(p, p.item_id, "--expected-revision", p.revision_id)
    assert code == 0, fresh
    assert staged_metadata(p.root, fresh)["sensitivity"] == "internal"
    declare_dependency(p, fresh, SORTS_AFTER_A_DRAFT)
    cli_ok("propose", "accept", fresh["proposalId"])


@pytest.mark.parametrize(
    ("face", "relabel"),
    [("status", "internal"), ("sensitivity", "confidential")],
    ids=["a-different-field", "the-same-class"],
)
def test_a_landed_dependson_migration_that_leaves_the_proposals_predicate_alone_does_not_refuse(
    face: str, relabel: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The predicate is the class of the field the proposal wrote, not any later write."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    history(p, "sensitivity", relabel=relabel)

    code, payload = cli("propose", "accept", draft(p, face)["proposalId"])

    assert code == 0, payload


_SITES = (("end-state", "sensitivity"), ("end-state", "status"), ("landed-row", "sensitivity"))


def _hand_authored(p: LabelledProject, face: str) -> dict[str, Any]:
    drafted = draft(p, "status")
    staged = p.root / drafted["proposalDirectory"] / drafted["migrationFile"]
    document = yaml.safe_load(staged.read_text(encoding="utf-8"))
    on = {"itemId": p.item_id}
    document["operations"] = (
        [{"op": "changeSensitivity", "sensitivity": "confidential", "reason": "r", **on}]
        if face == "sensitivity"
        else [{"op": "restoreItem", **on}, {"op": "deprecateItem", **on}]
    )
    staged.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return drafted


def _land_chain(p: LabelledProject, face: str, count: int, after: str = ROOT_MIGRATION_ID) -> None:
    for number in range(count):
        time.sleep(0.01)
        mid = UlidGenerator().new_ulid().value
        text = (
            reclassification(mid, p.item_id, "internal")
            if face == "sensitivity"
            else restoration(mid, p.item_id)
        )
        write(p.root, mid, f"w{number}", depending_on(text, after))
        after = mid
    cli_ok("migrate", "apply")


def _refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str, int],
    *,
    hand: bool = True,
) -> tuple[LabelledProject, dict[str, Any], str]:
    site, face, extra = case
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    drafted = _hand_authored(p, face) if hand else draft(p, face)
    if site == "landed-row":
        time.sleep(0.05)
        code, update = cli_propose(
            p, p.item_id, "--expected-revision", p.revision_id, "--sensitivity", "internal"
        )
        assert code == 0, update
        cli_ok("propose", "accept", update["proposalId"])
        _land_chain(p, face, extra, update["migrationId"])
    else:
        _land_chain(p, face, extra + 1)
    code, refused = cli("propose", "accept", drafted["proposalId"])
    speaks = END_STATE_CHECK if site == "end-state" else REPORT_CHECK
    assert code == 1 and speaks in str(refused["error"]), refused
    return p, drafted, str(refused["remedy"])


def _named(remedy: str) -> list[str]:
    found = re.search(r"`dependsOn: \[([^\]]*)\]`", remedy)
    assert found, remedy
    return found[1].split(", ")


def _follow(p: LabelledProject, drafted: dict[str, Any], remedy: str) -> tuple[int, Any]:
    assert "`theurian propose accept`" in remedy and "migrate apply" not in remedy, remedy
    staged = p.root / drafted["proposalDirectory"] / drafted["migrationFile"]
    document = yaml.safe_load(staged.read_text(encoding="utf-8"))
    document["dependsOn"] = _named(remedy)
    staged.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return cli("propose", "accept", drafted["proposalId"])


@pytest.mark.parametrize("where", _SITES, ids="-".join)
@pytest.mark.parametrize("extra", [0, 1, 2], ids=["none", "single", "deep"])
def test_the_hand_authored_remedy_followed_literally_keeps_the_item_withheld(
    where: tuple[str, str], extra: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accept re-checks the edited proposal, so the first edit is accepted and the item stays
    withheld. ``landed-row`` is the report check's remedy, ``end-state`` the floor's."""
    p, drafted, remedy = _refused(tmp_path, monkeypatch, (*where, extra))

    code, accepted = _follow(p, drafted, remedy)
    cli_ok("migrate", "apply")

    assert code == 0, accepted
    assert get_is_withheld(p)


@pytest.mark.parametrize("face", FACES)
def test_the_accept_routed_remedy_converges_on_the_first_redraft_of_a_deep_chain(
    face: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An incomplete ``dependsOn`` is refused again, one writer further on (#898)."""
    p, _, remedy = _refused(tmp_path, monkeypatch, ("end-state", face, 2), hand=False)

    fresh = draft(p, face)
    declare_dependency(p, fresh, *_named(remedy))
    cli_ok("propose", "accept", fresh["proposalId"])
    cli_ok("migrate", "apply")

    assert get_is_withheld(p)


def _must_follow(p: LabelledProject, drafted: dict[str, Any], face: str) -> set[str]:
    """Landed writers of the face's field after the proposal in ``validate``'s union order."""
    staged = p.root / drafted["proposalDirectory"] / drafted["migrationFile"]
    shutil.copy(staged, placed := p.root / f".theurian/migrations/{staged.name}")
    try:
        order = cli_ok("migrate", "validate")["applicationOrder"]
    finally:
        placed.unlink()
    after = order[order.index(drafted["migrationId"]) + 1 :]
    return {
        mid
        for mid in after
        for path in p.root.glob(f".theurian/migrations/{mid}-*.yaml")
        for operation in yaml.safe_load(path.read_text(encoding="utf-8"))["operations"]
        if operation["op"] in _WRITERS[face] | {"createItem", "upsertRevision"}
        and operation["itemId"] == p.item_id
    }


_CASES = [(site, face, extra) for site, face in _SITES for extra in range(3)]
_WRITERS = {"sensitivity": {"changeSensitivity"}, "status": {"deprecateItem", "restoreItem"}}


def test_no_remedy_names_a_set_that_leaves_a_refused_field_writer_replaying_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The required set comes from ``migrate validate``, not the remedy's rule. Not site (b),
    held by ``test_a_redraft_after_two_dependent_restores_names_both_and_lands``."""
    for number, case in enumerate(_CASES):
        (tmp_path / str(number)).mkdir()
        p, drafted, remedy = _refused(tmp_path / str(number), monkeypatch, case)

        required = _must_follow(p, drafted, case[1])

        assert len(required) == case[2] + 1, (case, required)
        assert required <= set(_named(remedy)), (case, required, remedy)


def test_the_remedy_lists_the_writers_in_replay_order_not_id_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The smaller id depends on the larger, so it replays after it: sorting by id is wrong."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    drafted = _hand_authored(p, "sensitivity")
    low, high = sorted(UlidGenerator().new_ulid().value for _ in range(2))
    for mid, after in ((high, ROOT_MIGRATION_ID), (low, high)):
        write(p.root, mid, "w", depending_on(reclassification(mid, p.item_id, "internal"), after))
    cli_ok("migrate", "apply")

    code, refused = cli("propose", "accept", drafted["proposalId"])

    assert code == 1, refused
    assert _named(str(refused["remedy"])) == [high, low]


def _mid() -> str:
    time.sleep(0.01)
    return UlidGenerator().new_ulid().value


def _land(p: LabelledProject, item: str, sens: str, *, expected: str | None = None) -> str:
    mid, tag = _mid(), f"body-{time.monotonic_ns()}"
    (p.root / f".theurian/knowledge/architecture/{tag}.md").write_text(BODY)
    text = root_migration(
        item_id=item, revision_id=_mid(), namespace="backend", sensitivity=sens,
        trust_level="reviewed", status="approved",
    )  # fmt: skip
    text = text.replace(ROOT_MIGRATION_ID, mid).replace("auth-policy.md", f"{tag}.md")
    if expected is not None:  # an update over ``expected``: no createItem
        text = re.sub(r"  - op: createItem\n(?:    .*\n)+", "", text)
        text = text.replace(
            "    contentFile:", f"    expectedRevision: {expected}\n    contentFile:"
        )
    write(p.root, mid, tag, text)
    return mid


def _withheld(p: LabelledProject, item: str) -> bool:
    with mcp_session(build_server(p.registry), p.root.parent / "wire") as call:
        answer = call("knowledge.get", {"projectId": "demo", "itemId": item})
    return bool(answer["result"]["isError"])


def _set(p: LabelledProject, item: str, sens: str, *after: str) -> str:
    mid = _mid()
    text = reclassification(mid, item, sens)
    write(p.root, mid, "set", depending_on(text, *after) if after else text)
    return mid


def _raising(p: LabelledProject, *targets: tuple[str, str]) -> dict[str, Any]:
    _land(p, OTHER_ITEM, "internal")
    cli_ok("migrate", "apply")
    drafted = _hand_authored(p, "sensitivity")
    staged = p.root / drafted["proposalDirectory"] / drafted["migrationFile"]
    document = yaml.safe_load(staged.read_text(encoding="utf-8"))
    document["operations"] = [
        {"op": "changeSensitivity", "itemId": i, "sensitivity": s, "reason": "r"}
        for i, s in targets
    ]
    staged.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return drafted


def _reaccepted(p: LabelledProject, drafted: dict[str, Any], check: str) -> tuple[int, Any]:
    """Refused by ``check``, then the remedy as printed: its ``dependsOn`` in, accept again."""
    cli_ok("migrate", "apply")
    code, refused = cli("propose", "accept", drafted["proposalId"])
    assert code == 1 and check in str(refused["error"]), refused
    return _follow(p, drafted, str(refused["remedy"]))


def test_a_redraft_after_every_refused_items_writers_is_accepted_on_the_first_try(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The report check speaks for one item; a remedy naming only its writers served the other."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    drafted = _raising(p, (p.item_id, "confidential"), (OTHER_ITEM, "confidential"))
    update = _land(p, p.item_id, "internal", expected=p.revision_id)
    _set(p, OTHER_ITEM, "internal", _set(p, OTHER_ITEM, "internal", update))

    code, accepted = _reaccepted(p, drafted, REPORT_CHECK)
    cli_ok("migrate", "apply")

    assert code == 0, accepted
    assert _withheld(p, p.item_id) and _withheld(p, OTHER_ITEM)


def test_a_redraft_after_a_later_landed_tightener_is_refused_by_the_lowering_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = labelled_project(tmp_path, monkeypatch, sensitivity="public")
    drafted = _raising(p, (p.item_id, "internal"))
    _set(p, p.item_id, "confidential", _land(p, p.item_id, "public", expected=p.revision_id))

    code, again = _reaccepted(p, drafted, REPORT_CHECK)

    assert code == 1 and "would lower the sensitivity of" in str(again["error"]), again
    assert item_row(p.root, p.item_id)["sensitivity"] == "confidential" and _withheld(p, p.item_id)


def test_a_redraft_restating_a_co_carried_items_older_label_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    drafted = _raising(p, (p.item_id, "confidential"), (OTHER_ITEM, "internal"))
    _set(p, OTHER_ITEM, "confidential")
    _set(p, p.item_id, "internal", ROOT_MIGRATION_ID)

    code, again = _reaccepted(p, drafted, END_STATE_CHECK)

    assert code == 1 and "would lower the sensitivity of" in str(again["error"]), again
    assert item_row(p.root, OTHER_ITEM)["sensitivity"] == "confidential" and _withheld(
        p, OTHER_ITEM
    )


def test_a_lowering_revision_without_a_create_item_is_named_by_the_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    drafted = _raising(p, (p.item_id, "confidential"))
    lower = _land(p, p.item_id, "internal", expected=p.revision_id)
    cli_ok("migrate", "apply")

    code, refused = cli("propose", "accept", drafted["proposalId"])

    assert code == 1, refused
    assert _named(str(refused["remedy"])) == [lower]


def test_the_remedy_names_the_writer_of_every_field_a_landed_migration_loosens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no upsert the end-state check speaks; one field's writers alone loop the reader."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    drafted = _hand_authored(p, "sensitivity")
    staged = p.root / drafted["proposalDirectory"] / drafted["migrationFile"]
    document = yaml.safe_load(staged.read_text(encoding="utf-8"))
    document["operations"].append({"op": "deprecateItem", "itemId": p.item_id})
    staged.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    lower, restore = LARGER_IDS
    write(p.root, lower, "lower", reclassification(lower, p.item_id, "internal"))
    write(
        p.root, restore, "restore", depending_on(restoration(restore, p.item_id), ROOT_MIGRATION_ID)
    )
    cli_ok("migrate", "apply")

    code, refused = cli("propose", "accept", drafted["proposalId"])
    assert code == 1 and END_STATE_CHECK in str(refused["error"]), refused
    assert {lower, restore} <= set(_named(str(refused["remedy"]))), refused
    code, accepted = _follow(p, drafted, str(refused["remedy"]))
    cli_ok("migrate", "apply")

    assert code == 0, accepted
    assert get_is_withheld(p)


def test_accept_refuses_a_raise_that_would_re_attribute_a_held_reorders_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """core-v0.5.1 CHANGELOG: accept "refuses a proposal ... that would make a row the history
    already holds name a different migration in ``undoes``".

    M's ``reorders`` row undoes the landed raise; the proposal's larger id would make M undo
    the proposal instead. The row's key without ``undoes`` is unchanged, so only that half
    of the invariant refuses it.
    """
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    landed_raise, lower, raise_back = "01K1DDDDDD01234567890ABCDE", D1, D2
    write(p.root, landed_raise, "raise", reclassification(landed_raise, p.item_id, "confidential"))
    lowering = depending_on(reclassification(lower, p.item_id, "internal"), ROOT_MIGRATION_ID)
    write(p.root, lower, "lower", lowering)
    again = depending_on(reclassification(raise_back, p.item_id, "confidential"), lower)
    write(p.root, raise_back, "raise-back", again)
    cli_ok("migrate", "apply")
    code, drafted = cli_propose(
        p, p.item_id, "--expected-revision", p.revision_id, "--sensitivity", "confidential"
    )
    assert code == 0, drafted
    held = [
        (r["migrationId"], r["field"], r["kind"], r["undoes"])
        for r in cli_ok("migrate", "validate")["permissiveMoves"]
    ]
    assert held == [(lower, "sensitivity", "reorders", landed_raise)]
    before = landing_zone(p.root)

    code, payload = cli("propose", "accept", drafted["proposalId"])

    assert code == 1, payload
    assert all(named in str(payload["error"]) for named in (lower, p.item_id, "sensitivity"))
    assert REPORT_CHECK in str(payload["error"]), payload
    assert END_STATE_CHECK not in str(payload["error"]), payload
    assert landing_zone(p.root) == before, "a refused accept moved files"
    cli_ok("migrate", "apply")
    rows = cli_ok("migrate", "validate")["permissiveMoves"]
    assert [(r["migrationId"], r["undoes"]) for r in rows] == [(lower, landed_raise)]
