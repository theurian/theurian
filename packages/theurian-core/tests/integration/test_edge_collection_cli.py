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
import tempfile
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any, Final

import pytest
from git_isolation_fixtures import git, new_repo
from label_inheritance_support import BODY, ROOT_MIGRATION_ID, ROOT_REVISION_ID, root_migration
from typer.testing import CliRunner, Result

from theurian.cli import commands
from theurian.cli.main import app
from theurian.infrastructure.git import first_parent_walk as walking
from theurian.infrastructure.git.first_parent_walk import FirstParentWalk, GitWalkRefusedError
from theurian.infrastructure.sqlite.store import SqliteWriter

pytestmark = pytest.mark.integration

runner = CliRunner()

ITEM: Final = "architecture.auth-policy"
NEVER: Final = "architecture.never-held"
EDGES: Final = (
    "SELECT source_commit, source_type, source_id, target_type, target_id "
    "FROM traceability_edges ORDER BY 1, 3, 5"
)
_MARK: Final = "echo-marker-7f3"
#: C2-3's refused shapes (``test_refs_trailer.py``'s ``_HOSTILE``), as one commit's trailers.
_REFUSED: Final = (
    "ADR-0040",
    "#1033",
    f"{_MARK}, c.d",
    f"{_MARK}\xa0",
    f"{_MARK}x" + "a" * 190,
    _MARK.upper(),
    "z" * 201,
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


def _commit_raw(root: Path, path: str, message: bytes) -> str:
    """A commit holding ``message`` undecoded, which ``git commit`` would re-encode."""
    (root / path).write_bytes(message)
    git(root, "add", path)
    tree, parent = git(root, "write-tree").strip(), git(root, "rev-parse", "HEAD").strip()
    signature = "T <t@example.com> 1700000000 +0000"
    header = f"tree {tree}\nparent {parent}\nauthor {signature}\ncommitter {signature}\n\n"
    staged = root.parent / "commit-object"
    staged.write_bytes(header.encode() + message)
    sha = git(root, "hash-object", "-t", "commit", "-w", str(staged)).strip()
    git(root, "update-ref", "HEAD", sha)
    return sha


def _chain(error: BaseException | None) -> str:
    """Each exception's ``str`` and ``repr`` down ``__cause__`` and ``__context__``."""
    parts: list[str] = []
    while error is not None:
        parts += [str(error), repr(error)]
        error = error.__cause__ or error.__context__
    return "".join(parts)


def _fail_after_the_walk_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raise inside the apply's transaction once the edges and the walk record are written."""
    record = SqliteWriter.record_walk

    def failing(self: SqliteWriter, *args: Any) -> None:
        record(self, *args)
        raise sqlite3.OperationalError("planted")

    monkeypatch.setattr(SqliteWriter, "record_walk", failing)


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
    spawn, write = subprocess.Popen.__init__, SqliteWriter.replace_traceability_edges

    def spied_spawn(self: Any, args: Sequence[str], *rest: Any, **options: Any) -> None:
        spawns.append((_subcommand(args), _held(lock)))
        spawn(self, args, *rest, **options)

    def spied_replace(self: SqliteWriter, *args: Any) -> int:
        at_replace.append(_held(lock))
        return write(self, *args)

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


def test_no_refused_value_and_no_message_byte_leaves_the_walk(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C2-3's deferred no-echo claim on the wired path (D5) and ruling c2-2b's message rule (D5b):
    a refused value costs a count, and a message is read for its trailers and for nothing else.
    The third apply fails after the collector has read every message, so an exception does reach
    the caller and its chain is walked.
    """
    text = _commit(
        project,
        "a.py",
        f"hostile\n\nbody {_MARK}\n" + "".join(f"Refs: {v}\n" for v in (*_REFUSED, ITEM)),
    )
    raw = _commit_raw(project, "b.py", f"raw\n\nbody {_MARK}\nRefs: ".encode() + b"\x80\n")

    result, payload = _apply()
    plain = runner.invoke(app, ["migrate", "apply"])
    stored = b"".join(
        p.read_bytes() for p in (project / ".theurian/state").iterdir() if p.is_file()
    )
    _fail_after_the_walk_record(monkeypatch)
    failed, refusal = _apply()

    assert result.exit_code == plain.exit_code == 0, payload
    assert (failed.exit_code, refusal["error"]) == (4, "planted")
    assert "planted" in _chain(failed.exception), "positive control: the chain walk reaches it"
    flags = {"capped": False, "undated": False, "absentItems": 0}
    assert payload["traceability"]["commits"] == [
        {"sha": raw, "refused": 0, "undecodable": True, **flags},
        {"sha": text, "refused": len(_REFUSED), "undecodable": False, **flags},
    ]
    assert _rows(project, EDGES) == [(text, "code", "a.py", "knowledge", ITEM)]
    assert text.encode() in stored, "positive control: the byte search reads the edge rows"
    published = b"".join(
        [
            *(run.stdout_bytes + run.stderr_bytes for run in (result, plain, failed)),
            repr(_rows(project, "SELECT * FROM traceability_edges")).encode(),
            _chain(failed.exception).encode(),
        ]
    ).lower()
    for value in (b"adr-0040", b"#1033", _MARK.encode(), b"z" * 20, b"\x80"):
        assert value not in published, value
    assert _MARK.encode() not in stored.lower()
    assert b"z" * 20 not in stored


def _moving_shallow_list(patch: pytest.MonkeyPatch, _root: Path) -> None:
    digests = iter(("before", "after"))
    patch.setattr(walking, "_read_shallow", lambda _path, _length: (next(digests), frozenset()))


_READ_HEAD: Final = walking._read_head
#: One plant per remedy C2-2b's walk refuses with (``dor-c2-4.md`` section 8), set for the walk's
#: own call only, beside the remedy fragment that shows the plant reached that kind. The two
#: shallow plants are real triggers, and the unreadable one's ``__cause__`` names the file's path.
_REFUSALS: Final[dict[str, tuple[str, Callable[[pytest.MonkeyPatch, Path], object]]]] = {
    "install": ("Install git", lambda patch, root: patch.setenv("PATH", str(root / "no-git"))),
    "rooted-elsewhere": (
        "--show-toplevel",
        lambda patch, _: patch.setattr(
            walking, "_read_head", lambda g, r: _READ_HEAD(g, r / ".theurian")
        ),
    ),
    "report": ("report its output", lambda patch, _: patch.setattr(walking, "_HEX_LENGTHS", {})),
    "shallow-line": (
        "one lowercase object id",
        lambda _, r: (r / ".git/shallow").write_text("x\n"),
    ),
    "shallow-unreadable": (
        "one lowercase object id",
        lambda _, root: (root / ".git/shallow").mkdir(exist_ok=True),
    ),
    "shallow-moved": ("Wait for the 'git fetch'", _moving_shallow_list),
    "deadline": ("git gc", lambda patch, _: patch.setattr(walking, "GIT_TIMEOUT_SECONDS", 0)),
    "repair": (
        "git fsck",
        lambda patch, _: patch.setattr(walking, "_PREFIX", ("--no-such-option",)),
    ),
    "stage": ("TMPDIR", lambda patch, root: patch.setattr(tempfile, "tempdir", str(root / "gone"))),
}


@pytest.mark.parametrize("kind", sorted(_REFUSALS))
def test_a_refusing_walk_prints_its_own_remedy_and_writes_no_state(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """ADR-0040 decision 2 (D12): the walk refuses before the lock, so nothing is built, and the
    caller gets its text and remedy and never its cause, which can name a path (#1092 R1 LOW-1).
    """
    fragment, plant = _REFUSALS[kind]
    caught: list[GitWalkRefusedError] = []
    walk = walking.walk_first_parent

    def planted(root: Path | None) -> FirstParentWalk:
        with pytest.MonkeyPatch.context() as patch:
            plant(patch, project)
            try:
                return walk(root)
            except GitWalkRefusedError as exc:
                caught.append(exc)
                raise

    monkeypatch.setattr(commands, "walk_first_parent", planted)
    _commit(project, "a.py", f"change a\n\nRefs: {ITEM}\n")
    state = project / ".theurian/state"
    before = sorted(state.iterdir())

    result, payload = _apply()
    plain = runner.invoke(app, ["migrate", "apply"])

    first, second = caught
    assert fragment in first.remedy
    assert (result.exit_code, plain.exit_code) == (4, 4)
    assert payload == {"error": str(first), "remedy": first.remedy}
    assert plain.stderr == f"error: {second}\n{second.remedy}\n"
    assert sorted(state.iterdir()) == before
    causes = [str(exc.__cause__) for exc in caught if exc.__cause__ is not None]
    assert len(causes) == (2 if kind in {"shallow-unreadable", "stage"} else 0)
    assert all(str(project.name) in cause for cause in causes)
    for secret in (str(tmp_path), str(tmp_path.resolve()), *causes):
        assert secret not in result.stdout + result.stderr + plain.stdout + plain.stderr


def test_an_apply_records_each_ref_once_per_changed_path_at_the_walk_commit(project: Path) -> None:
    """ADR-0040 decisions 1 and 2 (D1)."""
    head = _commit(project, "a.py", f"change a\n\nRefs: {ITEM}\n")

    result, payload = _apply()

    assert result.exit_code == 0, payload
    assert (payload["traceability"]["walkCommit"], payload["traceability"]["edges"]) == (head, 1)
    assert _rows(project, EDGES) == [(head, "code", "a.py", "knowledge", ITEM)]


def test_every_apply_replaces_the_edges_and_the_walk_record_in_its_one_transaction(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0040 decision 2 (D2): an apply with nothing pending replaces the set after a rebase,
    and a failure after the walk record is written rolls back both."""
    first = _commit(project, "a.py", f"change a\n\nRefs: {ITEM}\n")
    assert _apply()[0].exit_code == 0
    git(project, "commit", "-q", "--amend", "-m", f"amended\n\nRefs: {ITEM}\n")
    amended = git(project, "rev-parse", "HEAD").strip()

    result, payload = _apply()
    _fail_after_the_walk_record(monkeypatch)
    _commit(project, "b.py", f"change b\n\nRefs: {ITEM}\n")
    failed, refusal = _apply()

    assert amended != first
    assert (result.exit_code, payload["applied"]) == (0, [])
    assert (failed.exit_code, refusal["error"]) == (4, "planted")
    assert _rows(project, EDGES) == [(amended, "code", "a.py", "knowledge", ITEM)]
    assert _rows(project, "SELECT walk_commit FROM schema_metadata") == [(amended,)]


def test_one_head_gives_one_edge_set_and_a_commit_outside_the_set_keeps_the_state_hash(
    project: Path,
) -> None:
    """ADR-0040 decision 2's identity (D3): edges are a function of the walk commit, and no edge
    field enters ``StateInputs`` (*Still owed* item 1's ``state_hash`` perturbation pin)."""
    every = "SELECT * FROM traceability_edges ORDER BY edge_id"
    _commit(project, "a.py", f"change a\n\nRefs: {ITEM}\n")
    first = _apply()[1]
    edges = _rows(project, every)
    second = _apply()[1]
    again = _rows(project, every)
    head = _commit(project, "b.py", f"change b\n\nRefs: {ITEM}\n")
    third = _apply()[1]

    assert (again, len(edges)) == (edges, 1)
    assert first["stateHash"] == second["stateHash"] == third["stateHash"]
    assert (third["traceability"]["walkCommit"], len(_rows(project, every))) == (head, 2)


def test_the_apply_report_publishes_exactly_these_keys(project: Path) -> None:
    """D5c: a key in ``migrate apply --json`` is a published field, decided here before it ships."""
    _commit(project, "a.py", f"change a\n\nRefs: {NEVER}\n")

    result, payload = _apply()

    assert result.exit_code == 0, payload
    assert sorted(payload) == [
        *("applied", "changed", "databaseCreated", "indexPurge", "operationsApplied"),
        *("permissiveMoves", "skipped", "stateHash", "traceability"),
    ]
    assert sorted(payload["traceability"]) == [
        *("budgetBounded", "cappedDigest", "commits", "depthBounded", "edges", "shallowDigest"),
        "walkCommit",
    ]
    assert [sorted(row) for row in payload["traceability"]["commits"]] == [
        ["absentItems", "capped", "refused", "sha", "undated", "undecodable"]
    ]


def test_a_path_listed_twice_in_one_commit_gives_one_edge(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D9: two equal edge ids in one commit would abort the user's apply on the primary key."""
    head = _commit(project, "a.py", f"change a\n\nRefs: {ITEM}\n")
    walked = walking.walk_first_parent(project)
    doubled = replace(walked.commits[0], paths=("a.py", "a.py"))
    monkeypatch.setattr(
        commands, "walk_first_parent", lambda _: replace(walked, commits=(doubled,))
    )

    result, payload = _apply()

    assert result.exit_code == 0, payload
    assert _rows(project, EDGES) == [(head, "code", "a.py", "knowledge", ITEM)]


def test_an_apply_after_a_project_id_change_replaces_the_whole_edge_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C-H1 (#1097 R1, HIGH): the state hash omits the project id, so the renamed project's apply
    reuses the database holding the old id's edges. A replace scoped to the applying project
    exited 4 on ``edge_id``; an ``INSERT OR IGNORE`` one exits 0 and keeps the old rows (#1099).
    """
    root = _project(tmp_path, monkeypatch, item=False)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init", extra_env=_DATED)
    _commit(root, "a.py", f"change a\n\nRefs: {ITEM}\n")
    first, before = _apply()
    renamed = runner.invoke(app, ["project", "register", "--project-id", "renamed"])

    second, after = _apply()

    assert (first.exit_code, renamed.exit_code, second.exit_code) == (0, 0, 0), after
    assert after["traceability"] == before["traceability"]
    assert _rows(root, "SELECT project_id, source_id FROM traceability_edges") == [
        ("renamed", "a.py")
    ]


def _listed(stdout: str) -> list[str]:
    """The text form's flagged commits: the last list, under ``traceability``."""
    return [line.removeprefix("    - ") for line in stdout.split("  commits:\n")[1].splitlines()]


def test_the_text_form_lists_twenty_flagged_commits_and_counts_the_rest(project: Path) -> None:
    """Adv L4 (#1097 R1, adopted): under GitHub's ``Refs: #N`` every commit is refused and
    flagged, and the text form printed a line for each, up to 10,000; ``--json`` keeps them all.
    """
    shas = [_commit(project, f"f{n}.py", f"fix {n}\n\nRefs: #{n}\n") for n in range(20)]
    at_cap = _listed(runner.invoke(app, ["migrate", "apply"]).stdout)
    shas += [_commit(project, f"f{n}.py", f"fix {n}\n\nRefs: #{n}\n") for n in range(20, 23)]

    over = _listed(runner.invoke(app, ["migrate", "apply"]).stdout)
    payload = _apply()[1]

    rows = [f"{sha} refused=1" for sha in reversed(shas)]
    assert at_cap == rows[3:]
    assert over == [*rows[:20], "\u2026 and 3 more; see --json"]
    assert [row["sha"] for row in payload["traceability"]["commits"]] == list(reversed(shas))
