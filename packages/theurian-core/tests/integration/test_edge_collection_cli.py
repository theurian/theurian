"""`migrate apply` collects traceability edges on every run (ADR-0040 decisions 2-4, #1033).

The CLI runs in-process against a scratch repository under ``tmp_path``, with ``HOME``, the data
directory and ``GIT_CEILING_DIRECTORIES`` moved there; edges and the walk record are read back
with ``sqlite3``, never through the adapter that wrote them.
"""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import subprocess
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import Any, Final

import pytest
from git_isolation_fixtures import git, new_repo
from label_inheritance_support import BODY, ROOT_MIGRATION_ID, ROOT_REVISION_ID, root_migration
from typer.testing import CliRunner, Result

from theurian.cli.main import app
from theurian.infrastructure.sqlite.store import SqliteWriter

pytestmark = pytest.mark.integration

runner = CliRunner()

ITEM: Final = "architecture.auth-policy"
NEVER: Final = "architecture.never-held"
EDGES: Final = (
    "SELECT source_commit, source_type, source_id, target_type, target_id "
    "FROM traceability_edges ORDER BY 1, 3, 5"
)
#: Commits are dated so two repositories given the same history hold the same shas.
_DATED: Final = {"GIT_AUTHOR_DATE": "1700000000 +0000", "GIT_COMMITTER_DATE": "1700000000 +0000"}


def _project(
    base: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    sensitivity: str | None = None,
    item: bool = True,
) -> Path:
    """An initialised project; its migration, uncommitted, creates :data:`ITEM` if ``item``."""
    root = new_repo(base / "demo")
    monkeypatch.setenv("HOME", str(base))
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(base / "data"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(base))
    monkeypatch.chdir(root)
    assert runner.invoke(app, ["init", "--json"]).exit_code == 0
    if not item:
        return root
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(BODY)
    (root / f".theurian/migrations/{ROOT_MIGRATION_ID}-auth.yaml").write_text(
        root_migration(
            item_id=ITEM,
            revision_id=ROOT_REVISION_ID,
            namespace="backend",
            sensitivity=sensitivity,
            trust_level="reviewed",
            status="approved",
        )
    )
    return root


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _project(tmp_path, monkeypatch)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "add the migration", extra_env=_DATED)
    return root


def _commit(root: Path, path: str, message: str) -> str:
    (root / path).write_text(message)
    git(root, "add", path)
    git(root, "commit", "-q", "-m", message, extra_env=_DATED)
    return git(root, "rev-parse", "HEAD").strip()


def _apply(*flags: str) -> tuple[Result, dict[str, Any]]:
    result = runner.invoke(app, ["migrate", "apply", "--json", *flags])
    return result, json.loads(result.stdout if result.exit_code == 0 else result.stderr)


def _rows(root: Path, sql: str) -> list[tuple[Any, ...]]:
    [database] = (root / ".theurian/state").glob("theurian-state-*.sqlite")
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        return connection.execute(sql).fetchall()


def _held(lock: Path) -> bool:
    """Whether anything holds ``lock``: a non-blocking flock from a fresh descriptor."""
    try:
        descriptor = os.open(lock, os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False
    finally:
        os.close(descriptor)


def _subcommand(argv: Sequence[str]) -> str:
    words = iter(argv[1:])
    for word in words:
        if word == "-c":
            next(words)
        elif not word.startswith("-"):
            return word
    return ""


def test_no_git_process_starts_while_the_write_lock_is_held(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NFR-8 (D2b): the walk reads git before the lock; under it the apply is CPU and SQLite.

    The probe reads the lock file itself at each spawn, not the code path that takes it; its
    reading at the replace, which runs under the lock, is what makes a False reading mean free.
    """
    _commit(project, "a.py", f"change a\n\nRefs: {ITEM}\n")
    lock = project / ".theurian/runtime/write.lock"
    spawns: list[tuple[str, bool]] = []
    at_replace: list[bool] = []
    spawn, replace = subprocess.Popen.__init__, SqliteWriter.replace_traceability_edges

    def spied_spawn(self: Any, args: Sequence[str], *rest: Any, **options: Any) -> None:
        spawns.append((_subcommand(args), _held(lock)))
        spawn(self, args, *rest, **options)

    def spied_replace(self: SqliteWriter, *args: Any) -> int:
        at_replace.append(_held(lock))
        return replace(self, *args)

    monkeypatch.setattr(subprocess.Popen, "__init__", spied_spawn)
    monkeypatch.setattr(SqliteWriter, "replace_traceability_edges", spied_replace)

    result, payload = _apply()

    assert result.exit_code == 0, payload
    assert at_replace == [True]
    assert [name for name, held in spawns if held] == [], spawns
    assert {"rev-parse", "rev-list", "diff-tree", "log"} <= {n for n, held in spawns if not held}


def test_a_withheld_item_and_one_never_held_differ_only_in_the_absent_item_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0040 3(b) (D10): one walk over two corpora, :data:`ITEM` withheld in one and never held
    in the other, gives one edge set and one report but the absent-item count, which is a count.

    The migrations stay uncommitted, so the two repositories hold one commit byte for byte and
    the rows, edge ids included, compare whole.
    """
    seen = []
    for name, item in (("withheld", True), ("never-held", False)):
        root = _project(tmp_path / name, monkeypatch, sensitivity="confidential", item=item)
        head = _commit(root, "a.py", f"change a\n\nRefs: {ITEM}\nRefs: {NEVER}\n")
        result, payload = _apply("--allow-uncommitted")
        assert result.exit_code == 0, payload
        rows = _rows(root, "SELECT * FROM traceability_edges ORDER BY edge_id")
        seen.append((head, payload["traceability"], rows, result.stdout))
    (head, withheld, rows, out), (other_head, never, other_rows, other_out) = seen

    assert (head, len(rows)) == (other_head, 2)
    assert rows == other_rows
    assert [[row.pop("absentItems") for row in t["commits"]] for t in (withheld, never)] == [
        [1],
        [2],
    ]
    assert withheld == never
    assert withheld["commits"] == [
        {"sha": head, "refused": 0, "capped": False, "undecodable": False, "undated": False}
    ]
    assert ITEM not in out + other_out
    assert NEVER not in out + other_out
