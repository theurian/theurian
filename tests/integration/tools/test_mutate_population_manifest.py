"""The copy is told what the checkout tracks, because it cannot ask.

``_build_tree`` copies the checkout without ``.git`` on purpose, so the suite
running inside the copy has no repository to ask which files ship. It reads
``.mutate-population`` instead, and this is the end that writes it -- the other
end is ``_manifest_listing`` in
``packages/theurian-core/tests/command_population.py``.

Without it the suite inside the copy falls back to a name-based guess that
refuses the repository-root ``.theurian/`` wholesale. That is 81 tracked
knowledge documents on ``dogfood/dev7-corpus``, so every verdict in a batch
would be graded against a smaller population than the gate the batch stands in
for -- silently, because a smaller population fails nothing.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import mutate
import pytest

pytestmark = pytest.mark.integration


def _git(*arguments: str) -> None:
    """One git command, by absolute path, failing loudly."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("the manifest is `git ls-files` output, and this machine has no git")
    subprocess.run(  # noqa: S603 - argv is written here, never user input
        [git, *arguments], check=True, capture_output=True
    )


def _checkout(root: Path) -> None:
    """A real repository with one tracked file and one untracked one."""
    _git("init", "-q", str(root))
    (root / "docs").mkdir()
    (root / "docs" / "tracked.md").write_text("run `theurian init`\n", encoding="utf-8")
    (root / "docs" / "untracked.md").write_text("run `theurian init`\n", encoding="utf-8")
    _git("-C", str(root), "add", "docs/tracked.md")


def test_the_copy_is_handed_the_paths_its_source_tracks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What lands in the copy is the index, not the directory listing.

    The untracked file is the assertion that matters: a manifest built from the
    filesystem would name it, and the copy would then scan a file no clone has
    -- which is #262 inside the harness rather than inside the gate.
    """
    source = tmp_path / "source"
    source.mkdir()
    _checkout(source)
    destination = tmp_path / "copy"
    destination.mkdir()
    monkeypatch.setattr(mutate, "REPO_ROOT", source)

    mutate._record_population(destination)

    manifest = destination / ".mutate-population"
    assert manifest.read_bytes() == b"docs/tracked.md\x00"


def test_building_a_tree_records_the_population_into_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recording has to be wired into the copy, not merely available.

    Mutation found this: deleting the call from ``_build_tree`` left every test
    above green, because they all call ``_record_population`` themselves. What
    the harness actually does with it was pinned nowhere, and a manifest nobody
    writes is a fallback nobody notices.

    ``uv sync`` is replaced by a stub that makes the marker directory
    ``_build_tree`` checks for -- the virtualenv is not what this test is about,
    and building one here would cost a network and a minute.
    """
    source = tmp_path / "source"
    source.mkdir()
    _checkout(source)
    stub_uv = tmp_path / "stub-uv"
    stub_uv.write_text("#!/bin/sh\nmkdir -p .venv/lib\n", encoding="utf-8")
    stub_uv.chmod(0o755)
    monkeypatch.setattr(mutate, "REPO_ROOT", source)
    monkeypatch.setattr(mutate, "_uv", lambda: str(stub_uv))

    tree = mutate._build_tree(tmp_path / "copy", tmp_path / "cache")

    assert (tree / ".mutate-population").read_bytes() == b"docs/tracked.md\x00"


def test_a_source_that_is_not_a_checkout_leaves_no_manifest_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A manifest nobody could build must be absent, not empty.

    An empty manifest is a population of nothing, and a population of nothing
    makes most of the documented-command suite pass by reading no files. Absent
    is the honest state: the copy then falls back to its own guess, which is
    what happened before this file existed.
    """
    source = tmp_path / "not-a-checkout"
    source.mkdir()
    destination = tmp_path / "copy"
    destination.mkdir()
    monkeypatch.setattr(mutate, "REPO_ROOT", source)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))

    mutate._record_population(destination)

    assert not (destination / ".mutate-population").exists()
    assert "could not record the population" in capsys.readouterr().out
