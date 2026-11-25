r"""INV-6's refusal where the CLI replays a migration set (ADR-0040 decision 5).

The wiring sites are the six lines this key prints (nine without the ``':!'``
exclusion of ``cli/migration_pipeline.py``, the module that defines the four and
calls them from one another):

    git grep -n -P '\b(apply_migration_set|rehearse_migration_set|current_item_in|permissive_moves_in)\(' -- packages/theurian-core/src/theurian/cli ':!packages/theurian-core/src/theurian/cli/migration_pipeline.py' | grep -v -E ':[0-9]+:\s*(#|def )'

The census test below re-derives them from the AST, so a seventh goes RED.
"""  # noqa: E501

from __future__ import annotations

import ast
import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any, Final

import pytest
from adr_0039_support import SRC, _named, _scoped
from git_isolation_fixtures import git
from label_inheritance_support import (
    ITEM_ID,
    cli,
    cli_propose,
    labelled_project,
    landing_zone,
    proposals_tree,
)

from theurian.cli import migration_pipeline
from theurian.domain.specification import EdgeCollection

pytestmark = pytest.mark.integration

OTHER: Final = "domain.order-cancellation"
#: A stored ``superseded_by`` row is the inverse ``supersedes`` row, so these two close a loop.
CYCLE_ROWS: Final = ((ITEM_ID, "supersedes", OTHER), (ITEM_ID, "superseded_by", OTHER))
#: How ``RelationCycleError`` renders that loop: each item once, then back to the first.
CYCLE: Final = f"{ITEM_ID} supersedes {OTHER} supersedes {ITEM_ID}"
PIPELINE: Final = frozenset(
    {"apply_migration_set", "rehearse_migration_set", "current_item_in", "permissive_moves_in"}
)


def _write_migration(root: Path, migration_id: str, operations: str) -> None:
    (root / f".theurian/migrations/{migration_id}-cycle.yaml").write_text(
        f"apiVersion: theurian.dev/v1\nid: {migration_id}\n"
        "createdAt: 2026-11-04T10:00:00+09:00\nauthor: engineer@example.com\noperations:\n"
        + operations
    )


def _relations(op: str, rows: tuple[tuple[str, str, str], ...]) -> str:
    return "".join(
        f"- op: {op}\n  sourceItemId: {s}\n  relationType: {k}\n  targetItemId: {t}\n"
        for s, k, t in rows
    )


def _land_the_cycle(root: Path) -> None:
    _write_migration(root, "01K1GGGGGG01234567890ABCDE", _relations("addRelation", CYCLE_ROWS))


def _dump(database: Path) -> list[str]:
    with closing(sqlite3.connect(database)) as connection:
        return list(connection.iterdump())


def test_migrate_apply_renders_the_cycle_and_the_remove_relation_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frozen migration's cure is a new one's `removeRelation`, not `MigrationCycleError`'s
    "break the dependency cycle" or "fix the migration set", which both mean editing one."""
    project = labelled_project(tmp_path, monkeypatch)
    _land_the_cycle(project.root)

    code, payload = cli("migrate", "apply")

    assert code == 4, payload
    assert CYCLE in payload["error"]
    assert payload["remedy"] == (
        "Write a new migration with a `removeRelation` for one `supersedes` relation from an "
        "item of this cycle to the next (or for the `superseded_by` relation from the next item "
        "back to it, whichever the migrations recorded), then run `theurian migrate apply` again. "
        "If the cycle holds more than one relation between the same two items, removing one "
        "leaves the cycle: repeat until the apply succeeds."
    )


RENAMED: Final = "architecture.auth-policy-v2"


@pytest.mark.parametrize(
    "row",
    [(ITEM_ID, "depends_on", RENAMED), (RENAMED, "depends_on", ITEM_ID)],
    ids=["old depends_on new", "new depends_on old"],
)
def test_migrate_apply_refuses_a_rename_whose_old_and_new_ids_share_a_depends_on_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, row: tuple[str, str, str]
) -> None:
    """migrations.md's rename section: the alias makes the two ids one item, so the row is a
    cycle of one."""
    project = labelled_project(tmp_path, monkeypatch)
    _write_migration(
        project.root,
        "01K1GGGGGG01234567890ABCDE",
        f"- op: createItem\n  itemId: {RENAMED}\n  kind: architecture\n  namespace: backend\n"
        "  owner: platform-team\n"
        + _relations("addRelation", (row,))
        + f"- op: deprecateItem\n  itemId: {ITEM_ID}\n  reason: renamed\n"
        f"- op: addAlias\n  alias: {ITEM_ID}\n  itemId: {RENAMED}\n",
    )

    code, payload = cli("migrate", "apply")

    assert code == 4, payload
    assert "depends_on cycle (INV-6)" in payload["error"]


ITERATION: Final = (
    "If the cycle holds more than one relation between the same two items, removing one leaves "
    "the cycle: repeat until the apply succeeds."
)


@pytest.mark.parametrize(
    ("aliases", "rows", "named"),
    [
        pytest.param(
            ("backend.x", "backend.y"),
            (("backend.a", "supersedes", "backend.b"),),
            (("backend.b", "supersedes", "backend.x"), ("backend.b", "supersedes", "backend.y")),
            id="parallel rows through two aliases",
        ),
        pytest.param(
            ("backend.x",),
            (("backend.b", "supersedes", "backend.x"),),
            (("backend.a", "supersedes", "backend.b"), ("backend.b", "superseded_by", "backend.a")),
            id="one pair stored as supersedes and as superseded_by",
        ),
    ],
)
def test_each_apply_names_the_next_row_of_a_step_until_the_step_has_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    aliases: tuple[str, ...],
    rows: tuple[tuple[str, str, str], ...],
    named: tuple[tuple[str, str, str], ...],
) -> None:
    """The remedy's iteration (ADR-0040 decision 5, #1059 M-1): `named` is one step stored
    twice, in the order successive applies list its rows."""
    project = labelled_project(tmp_path, monkeypatch)
    _write_migration(
        project.root,
        "01K1GGGGGG01234567890ABCDE",
        "".join(f"- op: addAlias\n  alias: {x}\n  itemId: backend.a\n" for x in aliases)
        + _relations("addRelation", (*rows, *named)),
    )

    for i, row in enumerate(named):
        code, payload = cli("migrate", "apply")
        assert (code, " ".join(row) in payload["error"]) == (4, True), payload
        assert ITERATION in payload["remedy"]
        _write_migration(
            project.root, f"01K1HHHHH{i}01234567890ABCDE", _relations("removeRelation", (row,))
        )
    code, payload = cli("migrate", "apply")

    assert code == 0, payload


def test_an_alias_free_cycle_is_refused_word_for_word_until_its_doubled_step_has_no_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The alias-free message names items, not rows, so it is unchanged after one row of a step
    stored twice is removed: that unchanged refusal is what the remedy's "repeat" is about."""
    project = labelled_project(tmp_path, monkeypatch)
    doubled = (
        ("backend.a", "supersedes", "backend.b"),
        ("backend.b", "superseded_by", "backend.a"),
    )
    closing_rows = (
        ("backend.b", "supersedes", "backend.c"),
        ("backend.c", "supersedes", "backend.a"),
    )
    _write_migration(
        project.root,
        "01K1GGGGGG01234567890ABCDE",
        _relations("addRelation", (*doubled, *closing_rows)),
    )

    refusals = []
    for i, row in enumerate(doubled):
        refusals.append(cli("migrate", "apply"))
        _write_migration(
            project.root, f"01K1HHHHH{i}01234567890ABCDE", _relations("removeRelation", (row,))
        )
    cured = cli("migrate", "apply")

    (first, before), (second, after) = refusals
    assert (first, second, cured[0]) == (4, 4, 0), (before, after, cured)
    assert before["error"].endswith(
        "(INV-6): backend.a supersedes backend.b supersedes backend.c supersedes backend.a"
    )
    assert after["error"] == before["error"]
    assert ITERATION in before["remedy"]


def test_an_apply_with_nothing_pending_refuses_a_planted_cycle_and_publishes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decision 5 reads the stored rows on every apply, nothing pending included; the refusal
    rolls back the apply's own write, the project row's commit, and publishes nothing."""
    project = labelled_project(tmp_path, monkeypatch)
    state = project.root / ".theurian/state"
    pointer = (state / "active.json").read_bytes()
    database = state / json.loads(pointer)["databaseFilename"]
    with closing(sqlite3.connect(database)) as raw, raw:
        raw.executemany(
            "INSERT INTO knowledge_relations VALUES ('demo', ?, ?, ?, NULL, '2026-11-04T00:00:00')",
            CYCLE_ROWS,
        )
    planted = _dump(database)

    code, payload = cli("migrate", "apply")

    assert code == 4, payload
    assert CYCLE in payload["error"]
    assert (state / "active.json").read_bytes() == pointer
    assert _dump(database) == planted


def test_migrate_validate_marks_the_cycle_unavailable_while_apply_refuses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validate's verdict does not rest on its replay (ADR-0027 decision 2), so it stays
    valid and puts the refusal in `permissiveMovesUnavailable` (#1056 files the class)."""
    project = labelled_project(tmp_path, monkeypatch)
    _land_the_cycle(project.root)

    validated = cli("migrate", "validate")
    applied = cli("migrate", "apply")

    assert validated[0] == 0, validated
    assert validated[1]["valid"] is True
    assert CYCLE in validated[1]["permissiveMovesUnavailable"]
    assert applied[0] == 4, applied
    assert CYCLE in applied[1]["error"]


def _assert_names_migrate_apply_not_the_set_edits(payload: dict[str, str]) -> None:
    assert CYCLE in payload["error"]
    assert "migrate apply" in payload["remedy"]
    assert "break the dependency cycle" not in payload["remedy"]
    assert "fix the migration set" not in payload["remedy"]


def test_propose_over_a_landed_cycle_refuses_with_the_cycle_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The proposal service's `current_item_in` site: a refusal that says nothing was
    written must leave `.theurian/proposals` as it was, and name `migrate apply`."""
    project = labelled_project(tmp_path, monkeypatch)
    _land_the_cycle(project.root)
    before = proposals_tree(project.root)

    code, payload = cli_propose(project, ITEM_ID)

    assert code == 2, payload
    _assert_names_migrate_apply_not_the_set_edits(payload)
    assert proposals_tree(project.root) == before


def test_accept_over_a_landed_cycle_refuses_with_the_cycle_and_moves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The proposal service's `rehearse_migration_set` site: the cycle is the landed set's,
    not the proposal's, and a refused accept leaves its directory intact."""
    project = labelled_project(tmp_path, monkeypatch)
    code, drafted = cli_propose(project, "architecture.new-item")
    assert code == 0, drafted
    _land_the_cycle(project.root)
    before = landing_zone(project.root)

    code, payload = cli("propose", "accept", drafted["proposalId"])

    assert code == 4, payload
    _assert_names_migrate_apply_not_the_set_edits(payload)
    assert landing_zone(project.root) == before


#: The tests above render the first four sites in `SITES` (`migrate apply` and `validate`,
#: and `propose_commands.py`'s two); the okf pair is rendered by none.
SITES: Final = {
    ("commands.py", "migrate_apply", "apply_migration_set"),
    ("commands.py", "_permissive_move_fields", "permissive_moves_in"),
    ("propose_commands.py", "_service", "current_item_in"),
    ("propose_commands.py", "_service", "rehearse_migration_set"),
    ("okf_commands.py", "_service", "current_item_in"),
    ("okf_commands.py", "_service", "rehearse_migration_set"),
}


def test_the_cli_wiring_sites_are_the_six_this_module_accounts_for() -> None:
    cli_root = SRC / "cli"
    found = {
        (path.relative_to(cli_root).as_posix(), scope, name)
        for path in sorted(cli_root.rglob("*.py"))
        if path.name != "migration_pipeline.py"
        for node, scope in _scoped(ast.parse(path.read_bytes()))
        if isinstance(node, ast.Call) and (name := _named(node.func)) in PIPELINE
    }

    assert found == SITES


def test_no_mcp_service_is_wired_to_a_replay_that_raises_relation_cycle_error() -> None:
    """`RelationCycleError` names item ids and must reach no MCP response (decision 5).

    Key: every name, attribute and import under ``mcp/`` and ``daemon/`` (a docstring or
    comment naming the replay is outside it), and each ``ProposalService(...)``'s ``rehearse``.
    """
    spelled: set[str] = set()
    rehearsals: list[str] = []
    for path in sorted([*(SRC / "mcp").rglob("*.py"), *(SRC / "daemon").rglob("*.py")]):
        for node in ast.walk(ast.parse(path.read_bytes())):
            match node:
                case ast.Name(id=name) | ast.Attribute(attr=name) | ast.alias(name=name):
                    spelled.update(name.split("."))
                case ast.ImportFrom(module=str(module)):
                    spelled.update(module.split("."))
                case ast.Call(func=ast.Name(id="ProposalService"), keywords=keywords):
                    rehearsals += [ast.unparse(k.value) for k in keywords if k.arg == "rehearse"]

    assert rehearsals == ["_accept_is_unreachable"]
    assert spelled.isdisjoint({*PIPELINE, "migration_pipeline"})


def test_every_apply_migration_set_call_under_src_passes_its_walk() -> None:
    """D4: ``walk`` defaults to ``None``, so ``migrate apply`` dropping it would record a null
    walk and no edges with nothing refusing. Key: every call by that name under ``src``."""
    walks = sorted(
        (
            path.relative_to(SRC).as_posix(),
            scope,
            next((ast.unparse(k.value) for k in node.keywords if k.arg == "walk"), "<default>"),
        )
        for path in sorted(SRC.rglob("*.py"))
        for node, scope in _scoped(ast.parse(path.read_bytes()))
        if isinstance(node, ast.Call) and _named(node.func) == "apply_migration_set"
    )

    assert walks == [
        ("cli/commands.py", "migrate_apply", "walk"),
        ("cli/migration_pipeline.py", "current_item_in", "None"),
        ("cli/migration_pipeline.py", "permissive_moves_in", "None"),
        ("cli/migration_pipeline.py", "rehearse_migration_set", "None"),
    ]


def test_no_replay_walks_with_tmpdir_inside_a_clone_or_outside_any_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0040 decision 2 (D4): a replay's copy is no working tree, and git run in a TMPDIR
    inside a clone reads that clone. Key: the replays :data:`SITES` names."""
    project = labelled_project(tmp_path, monkeypatch)
    git(project.root, "commit", "-q", "--allow-empty", "-m", f"x\n\nRefs: {ITEM_ID}")
    scratches = (project.root / "tmp", tmp_path / "outside")
    seen: set[tuple[str, str, EdgeCollection | None]] = set()
    apply = migration_pipeline.apply_migration_set

    def spied(**arguments: Any) -> Any:
        report = apply(**arguments)
        seen.add((tempfile.gettempdir(), sys._getframe(1).f_code.co_name, report.traceability))
        return report

    monkeypatch.setattr(migration_pipeline, "apply_migration_set", spied)
    for n, scratch in enumerate(scratches):
        scratch.mkdir()
        monkeypatch.setattr(tempfile, "tempdir", str(scratch))
        assert cli("migrate", "validate")[0] == 0
        assert cli_propose(project, ITEM_ID, "--expected-revision", project.revision_id)[0] == 0
        drafted = cli_propose(project, f"architecture.new-item-{n}")[1]
        assert cli("propose", "accept", drafted["proposalId"])[0] == 0, drafted

    replays = {name for _, _, name in SITES} - {"apply_migration_set"}
    null = EdgeCollection(None, 0, None, None, None, None, ())
    assert seen == {(str(s), name, null) for s in scratches for name in replays}
