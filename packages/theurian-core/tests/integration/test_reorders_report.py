"""``permissiveMoves`` reports a loosening replay order, not id order, produced (GHSA-wwq9).

R1: before each migration replays, a field is attributed to the largest-id migration that has
written it (any label write), at the level it left it; a smaller-id write never takes the
attribution. R2': a write is a ``"reorders"`` row only when its migration has a smaller id than the
attributed one, found the field at or above the attributed level, and left it below;
``undoes`` names the attributed migration, and ``reorders`` replaces ``undoes``/``lowers``
for the same (migration, item, field). Tightening inversions are not reported. Accepted
proposals' refusal is ``test_accept_refuses_a_replay_order_overwrite.py``'s.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from label_inheritance_support import (
    ITEM_ID,
    ROOT_MIGRATION_ID,
    ROOT_REVISION_ID,
    SORTS_AFTER_A_DRAFT,
    LabelledProject,
    cli_ok,
    cli_propose,
    deprecation,
    item_row,
    labelled_project,
    reclassification,
    root_migration,
    runner,
)
from replay_order_support import (
    ACCEPTED,
    D1,
    D2,
    FACES,
    declare_dependency,
    depending_on,
    draft,
    history,
    restoration,
    write,
)

from theurian.cli.main import app

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[4]
LATE = "01K1DDDDDD01234567890ABCDE"
T, L = "01K1BBBBBB01234567890ABCDE", "01K1CCCCCC01234567890ABCDE"
LO1, LO2 = "01K1BBBBBB01234567890ABC01", "01K1CCCCCC01234567890ABC02"
HI2 = "7ZZZZZZZZZ01234567890ABC04"
MOVE = {"sensitivity": ("confidential", "internal"), "status": ("deprecated", "approved")}


def _shape(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    keys = ("migrationId", "itemId", "field", "before", "after", "undoes", "kind")
    return [tuple(r[k] for k in keys) for r in rows]


def _rows() -> list[tuple[Any, ...]]:
    return _shape(cli_ok("migrate", "validate")["permissiveMoves"])


def land_late(
    p: LabelledProject, face: str, *, dependent: bool = True, chained: bool = False
) -> dict[str, Any]:
    """D1 then D2 (undoing it, replaying after it only if ``dependent``), then LATE raises again."""
    history(p, face, dependent=dependent, chained=chained)
    late = (
        reclassification(LATE, p.item_id, "confidential")
        if face == "sensitivity"
        else deprecation(LATE, p.item_id)
    )
    write(p.root, LATE, "late", late)
    return cli_ok("migrate", "apply")


def _accepted(p: LabelledProject) -> str:
    drafted = draft(p, "sensitivity")
    cli_ok("propose", "accept", drafted["proposalId"])
    cli_ok("migrate", "apply")
    return str(drafted["migrationId"])


@pytest.mark.parametrize(
    ("back_to", "row"),
    [
        pytest.param("internal", True, id="f1-chain"),
        pytest.param("confidential", False, id="back-to-level"),
    ],
)
def test_a_lowering_after_a_tightening_replaying_after_an_accepted_raise_reports_a_real_loosening(
    back_to: str, row: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T tightens past P's level and L lowers after it, both replaying after P (T < L < P).

    The attribution stays P's, not T's: L reaching only P's own level is no row.
    """
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    accepted = _accepted(p)
    assert accepted > L > T
    tighten = depending_on(reclassification(T, p.item_id, "restricted"), ROOT_MIGRATION_ID)
    write(p.root, T, "tighten", tighten)
    write(p.root, L, "lower", depending_on(reclassification(L, p.item_id, back_to), T))

    applied = _shape(cli_ok("migrate", "apply")["permissiveMoves"])

    expected = [(L, p.item_id, "sensitivity", "restricted", back_to, accepted, "reorders")]
    assert applied == (expected if row else [])
    assert _rows() == (expected if row else [])


def test_a_second_lowering_that_starts_already_below_is_not_a_second_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HI2 (a larger id than a draft) raises with no edge; LO1 < LO2 replay after it."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    write(p.root, HI2, "raise", reclassification(HI2, p.item_id, "confidential"))
    lower = depending_on(reclassification(LO1, p.item_id, "internal"), ROOT_MIGRATION_ID)
    write(p.root, LO1, "lower", lower)
    lower_more = depending_on(reclassification(LO2, p.item_id, "public"), LO1)
    write(p.root, LO2, "lower-more", lower_more)

    cli_ok("migrate", "apply")

    assert _rows() == [(LO1, p.item_id, "sensitivity", "confidential", "internal", HI2, "reorders")]


def test_a_smaller_id_lowering_landing_after_an_accepted_raise_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    accepted = _accepted(p)
    assert accepted > D2
    lower = depending_on(reclassification(D2, p.item_id, "internal"), ROOT_MIGRATION_ID)
    write(p.root, D2, "lower", lower)

    applied = _shape(cli_ok("migrate", "apply")["permissiveMoves"])

    expected = [(D2, p.item_id, "sensitivity", "confidential", "internal", accepted, "reorders")]
    assert applied == expected
    assert _rows() == expected


@pytest.mark.parametrize("face", FACES)
def test_validate_and_apply_report_the_dependson_migration_as_reordering_the_later_id(
    face: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")

    applied = land_late(p, face)

    expected = [(D2, p.item_id, face, *MOVE[face], LATE, "reorders")]
    assert _shape(applied["permissiveMoves"]) == expected
    assert _rows() == expected


@pytest.mark.parametrize("face", FACES)
def test_a_smaller_id_restatement_before_the_loosening_leaves_it_attributed_to_the_later_id(
    face: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D1 waits on the root, so it replays after LATE and restates its label with a smaller id."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")

    land_late(p, face, chained=True)

    assert _rows() == [(D2, p.item_id, face, *MOVE[face], LATE, "reorders")]


@pytest.mark.parametrize("face", FACES)
def test_without_dependson_nothing_is_reported(
    face: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")

    land_late(p, face, dependent=False)

    assert _rows() == []


def test_a_tightening_inversion_is_not_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``dependsOn`` deprecation replays after a later-id restore: inverted, but tightened."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    write(p.root, D1, "deprecate", deprecation(D1, p.item_id))
    write(p.root, D2, "deprecate-late", depending_on(deprecation(D2, p.item_id), D1))
    write(p.root, LATE, "restore", restoration(LATE, p.item_id))

    cli_ok("migrate", "apply")

    assert _rows() == []


def test_one_migration_writing_a_field_twice_is_not_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The writer and the attributed migration must be different migrations."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    twice = reclassification(D2, p.item_id, "confidential") + (
        f"  - op: changeSensitivity\n    itemId: {p.item_id}\n"
        "    sensitivity: internal\n    reason: restated\n"
    )
    write(p.root, D2, "twice", twice)

    cli_ok("migrate", "apply")

    assert _rows() == []


def test_a_field_the_attributed_migration_wrote_twice_is_attributed_at_its_last_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1's "the level that migration left it": a first write's level must not stay attributed.

    LATE restricts then returns to internal; D2 lowers to public after it. Attributed at
    restricted, D2 would have found the field below it and reported nothing, serving public
    where id order serves internal.
    """
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    twice = reclassification(LATE, p.item_id, "restricted") + (
        f"  - op: changeSensitivity\n    itemId: {p.item_id}\n"
        "    sensitivity: internal\n    reason: restated\n"
    )
    write(p.root, LATE, "twice", twice)
    lower = depending_on(reclassification(D2, p.item_id, "public"), ROOT_MIGRATION_ID)
    write(p.root, D2, "lower", lower)

    cli_ok("migrate", "apply")

    assert _rows() == [(D2, p.item_id, "sensitivity", "internal", "public", LATE, "reorders")]


def _validate_then_apply(
    p: LabelledProject, **migrations: tuple[str, str]
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Write each ``name=(id, text)``; the rows ``validate`` then ``apply`` report."""
    for name, (migration_id, text) in migrations.items():
        write(p.root, migration_id, name, text)
    validated = _rows()
    return validated, _shape(cli_ok("migrate", "apply")["permissiveMoves"])


def _twice(migration_id: str, item_id: str, first: str, then: str) -> str:
    """One migration writing the class ``first``, then ``then``."""
    return reclassification(migration_id, item_id, first) + (
        f"  - op: changeSensitivity\n    itemId: {item_id}\n"
        f"    sensitivity: {then}\n    reason: restated\n"
    )


def test_a_migration_is_judged_against_the_writer_before_it_not_one_replaying_after_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GHSA-wwq9: "the field's largest-id writer" is read before the migration replays.

    Z (the largest id) replays after M and raises the field past A's level. Read globally
    Z is the attribution and M, found below Z's level, is no row; the engine names A.
    """
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    raise_ = reclassification(D2, p.item_id, "confidential")
    lower = depending_on(reclassification(D1, p.item_id, "internal"), ROOT_MIGRATION_ID)
    raise_more = depending_on(reclassification(LATE, p.item_id, "restricted"), D1)

    validated, applied = _validate_then_apply(
        p, raise_=(D2, raise_), lower=(D1, lower), raise_more=(LATE, raise_more)
    )

    expected = [(D1, p.item_id, "sensitivity", "confidential", "internal", D2, "reorders")]
    assert validated == expected
    assert applied == expected


def test_a_lowering_a_smaller_id_migration_raises_back_within_itself_is_no_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows are decided at the migration's end: its intermediate lowering is not one."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    raise_ = reclassification(D2, p.item_id, "confidential")
    lower_and_back = depending_on(
        _twice(D1, p.item_id, "internal", "confidential"), ROOT_MIGRATION_ID
    )

    validated, applied = _validate_then_apply(p, raise_=(D2, raise_), lower=(D1, lower_and_back))

    assert validated == []
    assert applied == []


def test_the_attribution_is_the_largest_id_writer_not_the_last_to_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W (largest id) replays first, N (smaller, still above M) writes after it: M undoes W.

    A smaller-id write never takes the attribution (R1), so N's later replay does not
    move it.
    """
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    first = reclassification(LATE, p.item_id, "confidential")
    later = depending_on(reclassification(D2, p.item_id, "restricted"), ROOT_MIGRATION_ID)
    lower = depending_on(reclassification(D1, p.item_id, "internal"), D2)

    validated, applied = _validate_then_apply(p, w=(LATE, first), n=(D2, later), m=(D1, lower))

    expected = [(D1, p.item_id, "sensitivity", "restricted", "internal", LATE, "reorders")]
    assert validated == expected
    assert applied == expected


def test_a_reorders_rows_after_is_not_the_label_the_item_is_served_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A further lowering is no second row, so read the item's current label, not ``after``.

    T-28 residual 5 and ``docs/protocol/migrations.md``'s ``reorders`` section: the row
    records the migration's own end (``confidential``), and a later lowering that found
    the field below the attributed level leaves it ``internal`` unreported.
    """
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    raise_ = reclassification(LATE, p.item_id, "restricted")
    first = depending_on(reclassification(D1, p.item_id, "confidential"), ROOT_MIGRATION_ID)
    further = depending_on(reclassification(D2, p.item_id, "internal"), D1)

    validated, applied = _validate_then_apply(
        p, raise_=(LATE, raise_), first=(D1, first), further=(D2, further)
    )

    expected = [(D1, p.item_id, "sensitivity", "restricted", "confidential", LATE, "reorders")]
    assert validated == expected
    assert applied == expected
    assert item_row(p.root, p.item_id)["sensitivity"] == "internal"


def _update_then_deprecation(p: LabelledProject, writer: str, *, edge: bool) -> str:
    """An update accepted while the item is live, then ``writer`` deprecates it; the update's id."""
    code, drafted = cli_propose(p, p.item_id, "--expected-revision", p.revision_id)
    assert code == 0, drafted
    if edge:
        declare_dependency(p, drafted, ROOT_MIGRATION_ID)
    cli_ok("propose", "accept", drafted["proposalId"])
    write(p.root, writer, "deprecate", deprecation(writer, p.item_id))
    cli_ok("migrate", "apply")
    return str(drafted["migrationId"])


def test_an_upsert_replaying_after_a_smaller_id_withdrawal_keeps_its_undoes_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 0.5.1 shape is unchanged where ids and replay order agree."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")

    update = _update_then_deprecation(p, D1, edge=False)

    assert _rows() == [(update, p.item_id, "status", "deprecated", "approved", D1, "undoes")]


def test_an_upsert_that_is_both_undoes_and_reordered_yields_one_reorders_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row key is (migration, item, field): ``reorders`` replaces ``undoes``, never joins it."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    late = SORTS_AFTER_A_DRAFT

    update = _update_then_deprecation(p, late, edge=True)

    assert _rows() == [(update, p.item_id, "status", "deprecated", "approved", late, "reorders")]


def test_the_text_report_names_the_migration_a_reorders_row_undoes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An upsert 0.5.1 reports as ``undoes``: its line must say the larger id it replays after."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    update = _update_then_deprecation(p, SORTS_AFTER_A_DRAFT, edge=True)

    result = runner.invoke(app, ["migrate", "validate"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    [line] = [line for line in result.output.splitlines() if update in line and "moves" in line]
    assert line.endswith(f"replays after {SORTS_AFTER_A_DRAFT}, a larger id, and undoes it"), line


@pytest.mark.parametrize("corpus", ["dogfood", "sample"])
def test_the_dogfood_corpus_and_the_sample_project_report_no_rows(
    corpus: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero counts only beside a positive control, which is what a ``reorders`` row is.

    Both corpora reported ``[]`` at a6a8d03; the full list is pinned, so a new row of any
    kind moves it.
    """
    (tmp_path / "control").mkdir()
    land_late(labelled_project(tmp_path / "control", monkeypatch, sensitivity="internal"), "status")
    assert [row[-1] for row in _rows()] == ["reorders"], "the control must report one"
    source = REPO / (".theurian" if corpus == "dogfood" else "examples/sample-project/.theurian")
    project = tmp_path / "corpus"
    project.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project, check=True)  # noqa: S607
    shutil.copytree(
        source, project / ".theurian", ignore=shutil.ignore_patterns("state", "proposals-local")
    )
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "corpus-data"))
    monkeypatch.chdir(project)
    cli_ok("project", "register", "--project-id", "corpus")

    assert _rows() == []


def _label(face: str, migration_id: str, item_id: str, *, tight: bool) -> str:
    if face == "sensitivity":
        return reclassification(migration_id, item_id, ACCEPTED[face] if tight else "internal")
    return (deprecation if tight else restoration)(migration_id, item_id)


@pytest.mark.parametrize("face", FACES)
def test_a_tightening_inversion_ends_tightened_and_reports_nothing(
    face: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-29 residual 4: the larger id's declassification or readmission replays first."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    steps = {
        "first": (D1, depending_on(_label(face, D1, p.item_id, tight=True), ROOT_MIGRATION_ID)),
        "undone": (LATE, _label(face, LATE, p.item_id, tight=False)),
    }
    if face == "sensitivity":  # C=D1, H=D2 raise; D=LATE declassifies; replay D, C, H
        steps["higher"] = (D2, depending_on(reclassification(D2, p.item_id, "restricted"), D1))

    rows = _validate_then_apply(p, **steps)

    assert rows == ([], [])
    assert item_row(p.root, p.item_id)[face] == (
        "restricted" if steps.get("higher") else "deprecated"
    )


@pytest.mark.parametrize("variant", ["plain", "restated-and-depended-on", "restated-only"])
@pytest.mark.parametrize("face", FACES)
def test_a_loosening_with_a_larger_id_replays_last_with_no_row(
    face: str, variant: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-29 residual 3: P raises, the larger-id L lowers, no dependsOn: id order; X restates P."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    steps = {"raise": (D1, _label(face, D1, p.item_id, tight=True))}
    if variant != "plain":
        restate = depending_on(_label(face, D2, p.item_id, tight=True), ROOT_MIGRATION_ID)
        steps["restate"] = (D2, restate)
    lower = _label(face, LATE, p.item_id, tight=False)
    steps["lower"] = (LATE, depending_on(lower, D2) if variant.endswith("on") else lower)

    rows = _validate_then_apply(p, **steps)

    assert rows == ([], [])
    ends = (
        ACCEPTED[face]
        if variant == "restated-only"
        else {"sensitivity": "internal", "status": "approved"}[face]
    )
    assert item_row(p.root, p.item_id)[face] == ends


HI5 = "7ZZZZZZZZZ01234567890ABC05"
#: Migrations after M, each depending on the one before it and restating the lowered level.
CHAINS = {"alone": (), "same-round": (HI5,), "deep": (D2, HI5)}


def _repair_target(row_migration: str, writers: tuple[str, ...]) -> str:
    """The last migration in applicationOrder that writes the field: the row's, if none follows."""
    return (row_migration, *writers)[-1]


@pytest.mark.parametrize("chain", CHAINS)
@pytest.mark.parametrize(
    "repair_id", ["01K1AAAAAB01234567890ABCDE", HI2], ids=["smaller", "larger"]
)
@pytest.mark.parametrize("face", FACES)
def test_the_repair_a_reorders_row_asks_for_holds_the_level_and_the_row_stays(
    face: str, repair_id: str, chain: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-29 operator action 2: N sets A's level again, depending on the last writer after M.

    Depending on M alone leaves a migration that depends on M, and replays after N, serving
    the lowered level.
    """
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    lowering = depending_on(_label(face, D1, p.item_id, tight=False), ROOT_MIGRATION_ID)
    steps = {"a": (LATE, _label(face, LATE, p.item_id, tight=True)), "m": (D1, lowering)}
    for before, writer in zip((D1, *CHAINS[chain]), CHAINS[chain], strict=False):
        restated = depending_on(_label(face, writer, p.item_id, tight=False), before)
        steps[f"w{writer[-2:]}"] = (writer, restated)
    _validate_then_apply(p, **steps)
    row = [(D1, p.item_id, face, *MOVE[face], LATE, "reorders")]
    assert _rows() == row

    target = _repair_target(D1, CHAINS[chain])
    write(
        p.root, repair_id, "n", depending_on(_label(face, repair_id, p.item_id, tight=True), target)
    )
    cli_ok("migrate", "apply")

    assert _rows() == row
    assert item_row(p.root, p.item_id)[face] == ACCEPTED[face]


def _upsert(migration_id: str, sensitivity: str) -> str:
    """The root migration's upsert alone, re-id'd: restates the current revision's labels."""
    text = root_migration(
        item_id=ITEM_ID, revision_id=ROOT_REVISION_ID, namespace="backend",
        sensitivity=sensitivity, trust_level="reviewed", status="approved",
    ).replace(ROOT_MIGRATION_ID, migration_id)  # fmt: skip
    return text[: text.index("  - op: createItem")] + text[text.index("  - op: upsertRevision") :]


def test_a_larger_id_upsert_loosening_merged_against_id_order_is_reported_as_undoes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.5.1's upsert row: the larger-id L lowers what P raised, so L undoes P; no inversion."""
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    steps = {
        "raise": (D1, _label("sensitivity", D1, p.item_id, tight=True)),
        "l": (LATE, _upsert(LATE, "internal")),
    }

    validated, applied = _validate_then_apply(p, **steps)

    row = [(LATE, p.item_id, "sensitivity", "confidential", "internal", D1, "undoes")]
    assert (validated, applied) == (row, row)


def test_an_upsert_raise_undone_by_a_larger_id_sanctioned_lowering_has_no_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = labelled_project(tmp_path, monkeypatch, sensitivity="internal")
    steps = {
        "raise": (D1, _upsert(D1, "confidential")),
        "l": (LATE, _label("sensitivity", LATE, p.item_id, tight=False)),
    }

    rows = _validate_then_apply(p, **steps)

    assert rows == ([], [])
    assert item_row(p.root, p.item_id)["sensitivity"] == "internal"
