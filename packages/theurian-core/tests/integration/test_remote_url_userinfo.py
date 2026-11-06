"""#1061: ``repository_url`` is the one value every recorded copy of the remote comes from
(``project register``/``list`` output, ``projects.json``, the state ``projects`` row and the
ingest anchor), so a credential embedded in origin must not survive it.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest
from git_isolation_fixtures import git, new_repo
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.ingestion_service import IngestionRequest, _anchor
from theurian.cli import commands
from theurian.cli.context import current_commit, repository_url
from theurian.cli.main import app

pytestmark = pytest.mark.integration

TOKEN = "TOK_1061"  # noqa: S105
HOST = "example.invalid"

#: (stored URL, expected). Mirrors the contract table measured for #1061.
CHANGES = [
    (f"https://alice:{TOKEN}@{HOST}/r.git", f"https://{HOST}/r.git"),
    (f"https://{TOKEN}@{HOST}/r.git", f"https://{HOST}/r.git"),
    (f"http://alice:{TOKEN}@{HOST}:8080/r.git", f"http://{HOST}:8080/r.git"),
    (f"HTTPS://alice:{TOKEN}@Example.INVALID/r.git", "HTTPS://Example.INVALID/r.git"),
    (f"https://alice:p@{TOKEN}@{HOST}/r.git", f"https://{HOST}/r.git"),
    (f"https://alice:{TOKEN}@[::1]:8080/r.git", "https://[::1]:8080/r.git"),
    (f"https://alice:{TOKEN}@{HOST}", f"https://{HOST}"),
    (f"https://alice:{TOKEN}@{HOST}?x=1", f"https://{HOST}?x=1"),
    (f"https://alice:{TOKEN}@{HOST}/x ", f"https://{HOST}/x "),
    (f"https://alice:{TOKEN}@{HOST}/a\tb", f"https://{HOST}/a\tb"),
    (f"ftps://alice:{TOKEN}@{HOST}/r.git", f"ftps://{HOST}/r.git"),
    (f"git://alice@{HOST}/r.git", f"git://{HOST}/r.git"),
    (
        f"persistent-https::https://alice:{TOKEN}@{HOST}/r",
        f"persistent-https::https://{HOST}/r",
    ),
    (f"ssh://alice:{TOKEN}@{HOST}/r.git", f"ssh://{HOST}/r.git"),
]

#: Forms without a secret in the userinfo slot: each must come back byte-identical.
UNCHANGED = [
    f"ssh://git@{HOST}/o/r.git",
    f"ssh://git@{HOST}:2222/o/r.git",
    f"git+ssh://git@{HOST}/o/r.git",
    f"SSH://git@{HOST}/o/r.git",
    f"git@{HOST}:o/r.git",
    f"https://{HOST}/r.git",
    f"https://{HOST}/a@b/r.git",
    f"https://{HOST}/x ",
    f"https://{HOST}/a\tb",
    "/srv/git/r.git",
    "../r.git",
    "file:///srv/git/r.git",
]

MIGRATION_ID = "01K1AAAAAA01234567890ABCDE"
BODY = "# Authentication policy\n\nEvery call carries a signed token.\n"
MIGRATION = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: 01K1AAAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/auth-policy.md
    contentSha256: {body_pin(BODY)}
    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth-policy.md
"""

runner = CliRunner()


@pytest.fixture(autouse=True)
def _hermetic_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_DATE", "2026-01-01T00:00:00+00:00")


def _repo(path: Path, url: str) -> Path:
    repo = new_repo(path)
    git(repo, "remote", "add", "origin", "https://placeholder.invalid/p.git")
    git(repo, "config", "remote.origin.url", url)
    return repo


@pytest.mark.parametrize(("stored", "expected"), CHANGES)
def test_a_remote_url_is_recorded_without_its_userinfo(
    tmp_path: Path, stored: str, expected: str
) -> None:
    repo = _repo(tmp_path / "r", stored)

    assert repository_url(repo) == expected


@pytest.mark.parametrize("stored", UNCHANGED)
def test_a_remote_url_without_a_secret_is_recorded_byte_for_byte(
    tmp_path: Path, stored: str
) -> None:
    repo = _repo(tmp_path / "r", stored)

    assert repository_url(repo) == stored


def _cli(monkeypatch: pytest.MonkeyPatch, root: Path, data: Path, *words: str) -> tuple[str, int]:
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data))
    monkeypatch.chdir(root)
    result = runner.invoke(app, [*words, "--json"], catch_exceptions=False)
    return (result.stdout or result.stderr), result.exit_code


def _prepare(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, url: str
) -> tuple[Path, Path]:
    root = _repo(tmp_path / name / "demo", url)
    data = tmp_path / f"{name}-data"
    _cli(monkeypatch, root, data, "init")
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml").write_text(MIGRATION)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "migrations")
    return root, data


def _state_row(root: Path) -> dict[str, Any]:
    (db,) = (root / ".theurian/state").glob("theurian-state-*.sqlite")
    con = sqlite3.connect(db)
    try:
        con.row_factory = sqlite3.Row
        (row,) = con.execute("SELECT * FROM projects").fetchall()
        return dict(row)
    finally:
        con.close()


def _without(mapping: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in mapping.items() if k not in keys}


def _recorded(monkeypatch: pytest.MonkeyPatch, root: Path, data: Path) -> dict[str, object]:
    registered, code = _cli(monkeypatch, root, data, "project", "register")
    assert code == 0
    listed, code = _cli(monkeypatch, root, data, "project", "list")
    assert code == 0
    out, code = _cli(monkeypatch, root, data, "migrate", "apply")
    assert code == 0, out
    entry = json.loads((data / "projects.json").read_text())
    request = IngestionRequest(
        project_root=root,
        knowledge_dir=root / ".theurian/knowledge",
        known_hashes={},
        commit_sha=current_commit(root),
        repository=repository_url(root),
    )
    return {
        "register": _without(json.loads(registered), "root"),
        "list": json.dumps(_strip_list(json.loads(listed)), sort_keys=True),
        "projects.json": json.dumps(_strip_list(entry), sort_keys=True),
        "state": _without(_state_row(root), "root_path", "registered_at"),
        "anchor": dataclasses.asdict(_anchor("architecture/auth-policy.md", request)),
    }


def _strip_list(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strip_list(v) for k, v in node.items() if k not in {"registeredAt", "rootPath"}}
    if isinstance(node, list):
        return [_strip_list(v) for v in node]
    return node


def test_a_credential_in_origin_reaches_no_recorded_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One query, two corpora: a credentialed origin records what a clean one does."""
    tokened, tokened_data = _prepare(
        monkeypatch, tmp_path, "tokened", f"https://alice:{TOKEN}@{HOST}/r.git"
    )
    clean, clean_data = _prepare(monkeypatch, tmp_path, "clean", f"https://{HOST}/r.git")
    assert current_commit(tokened) == current_commit(clean)

    with_token = _recorded(monkeypatch, tokened, tokened_data)
    without_token = _recorded(monkeypatch, clean, clean_data)

    assert with_token == without_token
    assert with_token["register"]["repositoryUrl"] == f"https://{HOST}/r.git"  # type: ignore[index]
    assert TOKEN not in json.dumps(with_token, default=str)


def test_the_next_apply_and_register_heal_a_record_written_with_the_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project registered by the old code must not keep its token once the fix runs."""
    raw = f"https://alice:{TOKEN}@{HOST}/r.git"
    root, data = _prepare(monkeypatch, tmp_path, "old", raw)
    with monkeypatch.context() as old:
        old.setattr(commands, "repository_url", lambda _root: raw)
        _cli(monkeypatch, root, data, "project", "register")
        _cli(monkeypatch, root, data, "migrate", "apply")
    assert TOKEN in _state_row(root)["repository_url"]

    _, code = _cli(monkeypatch, root, data, "migrate", "apply")
    assert code == 0
    assert TOKEN not in json.dumps(_state_row(root))

    _, code = _cli(monkeypatch, root, data, "project", "register")
    assert code == 0
    assert TOKEN not in (data / "projects.json").read_text()


def test_git_stores_the_forms_this_file_drives(tmp_path: Path) -> None:
    """Guards the fixture: every row above reached git unchanged, not a normalised form."""
    for i, (stored, _) in enumerate(CHANGES):
        repo = _repo(tmp_path / f"c{i}", stored)
        out = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],  # noqa: S607
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert out == stored + "\n"
