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
from contextlib import closing
from pathlib import Path
from typing import Final

import pytest
from adr_0039_support import SRC, _named, _scoped
from label_inheritance_support import ITEM_ID, cli, labelled_project

pytestmark = pytest.mark.integration

OTHER: Final = "domain.order-cancellation"
#: A stored ``superseded_by`` row is the inverse ``supersedes`` row, so these two close a loop.
CYCLE_ROWS: Final = ((ITEM_ID, "supersedes", OTHER), (ITEM_ID, "superseded_by", OTHER))
#: How ``RelationCycleError`` renders that loop: each item once, then back to the first.
CYCLE: Final = f"{ITEM_ID} supersedes {OTHER} supersedes {ITEM_ID}"
PIPELINE: Final = frozenset(
    {"apply_migration_set", "rehearse_migration_set", "current_item_in", "permissive_moves_in"}
)


def _land_the_cycle(root: Path) -> None:
    operations = "".join(
        f"- op: addRelation\n  sourceItemId: {s}\n  relationType: {k}\n  targetItemId: {t}\n"
        for s, k, t in CYCLE_ROWS
    )
    (root / ".theurian/migrations/01K1GGGGGG01234567890ABCDE-cycle.yaml").write_text(
        "apiVersion: theurian.dev/v1\nid: 01K1GGGGGG01234567890ABCDE\n"
        "createdAt: 2026-11-04T10:00:00+09:00\nauthor: engineer@example.com\noperations:\n"
        + operations
    )


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
        "back to it, whichever the migrations recorded), then run `theurian migrate apply` again."
    )


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


#: The tests above render the first two; the other four are the proposal service's.
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
