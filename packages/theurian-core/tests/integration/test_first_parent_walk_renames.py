"""A rename in ``walk_first_parent``'s history (ADR-0040 decision 2; A-M5, #1097 R1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from git_isolation_fixtures import commit_file, git, new_repo

from theurian.infrastructure.git.first_parent_walk import walk_first_parent

pytestmark = pytest.mark.integration


def test_a_rename_yields_both_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Under ``--no-renames``: a detected rename's two-path record made diff-tree's read refuse."""
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = new_repo(tmp_path / "repo")
    commit_file(repo, "a.py", "x\n")
    git(repo, "mv", "a.py", "b.py")
    git(repo, "commit", "-q", "-m", "rename")

    walk = walk_first_parent(repo)

    assert [commit.paths for commit in walk.commits] == [("a.py", "b.py"), ("a.py",)]
