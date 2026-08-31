"""``--with-git`` lends a copy the source's objects, so git-needing rules run.

The other half of ``.mutate-population``'s contract
(``test_mutate_population_manifest.py``): a manifest is a path list, and it is
enough for every rule that reads a *path* or working-tree bytes. It is nothing
for a rule that reads a **blob** --
``test_dogfood_corpus_governance.py::test_every_pinned_body_is_byte_identical_to_
its_source_anchor_commit`` compares a committed body against ``git cat-file
blob <commitSha>:<filePath>``, and a manifest has no blob to hand over. That
rule -- and ``test_root_corpus_applies.py``, which needs ``git ls-files`` to
confirm the migrations directory holds nothing untracked -- both skip loudly in
every ordinary ``tools/mutate.py`` copy (#452's finding), so a mutation whose
only killer is either one comes back SURVIVED with no sign that the harness
never ran the rule that would have caught it.

``--with-git`` gives the copy its own ``.git``, borrowing the source's objects
through ``objects/info/alternates`` rather than copying them, with ``HEAD``,
``index``, ``packed-refs`` and ``refs/`` copied outright so the copy's
worktree answers for itself. This module pins the two directions of that
contract: with the flag, the copy's own ``git ls-files`` matches the
population manifest ``_record_population`` wrote for the very same tree; without
it, the copy carries no ``.git`` at all -- the deletion direction
``_lend_git_objects``'s docstring names (``_COPY_IGNORE`` drops ``.git``, and
nothing puts it back unless asked).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import mutate
import pytest
from mutate_edits import HarnessError

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cut every git in this module off from the developer's own configuration.

    The same isolation ``test_mutate_population_manifest.py``'s
    ``_isolated_git`` fixture documents, and the same reason: a global
    ``core.excludesFile`` or ``init.templateDir`` on one machine would
    otherwise decide what these fixtures commit and track, and CI would
    disagree without either being wrong.
    """
    home = tmp_path / "git-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "absent.gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(home / "absent.gitconfig"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_INDEX_FILE", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)


def _git(*arguments: str) -> subprocess.CompletedProcess[str]:
    """One git command, by absolute path, failing loudly and with git's reason."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("this module borrows real git objects, and this machine has no git")
    completed = subprocess.run(  # noqa: S603 - argv is written here, never user input
        [git, *arguments], check=False, capture_output=True, text=True
    )
    assert completed.returncode == 0, (
        f"the fixture's own `git {' '.join(arguments)}` failed, so the test below would be "
        f"asserting against a tree nobody built:\n{completed.stderr}"
    )
    return completed


def _checkout(root: Path) -> None:
    """A real, *committed* repository with one tracked file.

    Committed rather than merely staged, unlike
    ``test_mutate_population_manifest.py``'s fixture: that module only ever
    reads the index (``ls-files --cached``), but a rule this module exercises
    reads a **blob at a commit** (``git cat-file``/``git show <sha>:<path>``),
    which does not exist until something is committed.
    """
    _git("init", "-q", str(root))
    _git("-C", str(root), "config", "user.email", "mutate-tests@example.invalid")
    _git("-C", str(root), "config", "user.name", "mutate tests")
    (root / "docs").mkdir()
    (root / "docs" / "tracked.md").write_text("run `theurian init`\n", encoding="utf-8")
    (root / "docs" / "untracked.md").write_text("run `theurian init`\n", encoding="utf-8")
    _git("-C", str(root), "add", "docs/tracked.md")
    _git("-C", str(root), "commit", "-q", "-m", "seed")


def _stub_uv(tmp_path: Path) -> Path:
    """A fake ``uv`` that only makes the marker directory ``_build_tree`` checks for.

    Building a real virtualenv here would cost a network round trip and a
    minute for something this module never asserts on.
    """
    stub = tmp_path / "stub-uv"
    stub.write_text("#!/bin/sh\nmkdir -p .venv/lib\n", encoding="utf-8")
    stub.chmod(0o755)
    return stub


def test_a_with_git_copy_answers_its_own_ls_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The borrowed repository is not decorative: the copy can be asked what it tracks.

    Compared against the manifest ``_record_population`` wrote for the exact
    same destination, which is independently derived (``git ls-files --cached``
    against the *source*) -- the two answering the same thing is the contract
    this flag exists to restore for the rules that need blobs, not paths.
    """
    source = tmp_path / "source"
    source.mkdir()
    _checkout(source)
    destination = tmp_path / "copy"
    destination.mkdir()
    monkeypatch.setattr(mutate, "REPO_ROOT", source)
    mutate._record_population(destination)

    mutate._lend_git_objects(destination)

    listed = _git("-C", str(destination), "ls-files", "-z").stdout
    manifest = (destination / ".mutate-population").read_bytes().decode("utf-8")
    assert listed == manifest == "docs/tracked.md\x00"


def test_a_with_git_copy_can_read_a_blob_at_the_commit_it_borrowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The claim the byte-identity rule actually needs: a blob, not just a name.

    ``git ls-files`` only proves the index copied correctly. The rule this
    flag exists for runs ``git cat-file blob <commitSha>:<filePath>``, which
    needs the *object*, reachable only through
    ``objects/info/alternates`` -- nothing else this function writes carries
    object content.
    """
    source = tmp_path / "source"
    source.mkdir()
    _checkout(source)
    commit = _git("-C", str(source), "rev-parse", "HEAD").stdout.strip()
    destination = tmp_path / "copy"
    destination.mkdir()
    monkeypatch.setattr(mutate, "REPO_ROOT", source)

    mutate._lend_git_objects(destination)

    shown = _git("-C", str(destination), "cat-file", "blob", f"{commit}:docs/tracked.md")
    assert shown.stdout == "run `theurian init`\n"


def test_the_alternates_file_names_the_source_object_store_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The copy answers for itself: the lent directory is content-addressed objects.

    ``objects/info/alternates`` is the only thing borrowed rather than copied,
    and it names exactly one absolute path -- the source's own
    ``.git/objects`` -- which stores objects by hash and carries no reference
    to which paths the working tree holds. A leak here would mean a mutated
    copy's ``git`` reads could describe the *source* checkout, not the copy.
    """
    source = tmp_path / "source"
    source.mkdir()
    _checkout(source)
    destination = tmp_path / "copy"
    destination.mkdir()
    monkeypatch.setattr(mutate, "REPO_ROOT", source)

    mutate._lend_git_objects(destination)

    alternates = (
        (destination / ".git" / "objects" / "info" / "alternates")
        .read_text(encoding="utf-8")
        .strip()
    )
    assert alternates == str((source / ".git" / "objects").resolve())


def test_a_mutation_inside_a_with_git_copy_never_touches_the_source_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Objects are lent, not shared write access -- the copy's ``git`` never writes upstream.

    A caller reading only the alternates line could still wonder whether the
    borrowed store is somehow writable back into the source. It is not:
    editing a tracked file inside the copy is invisible to ``git status`` run
    against the source, which is the same guarantee the harness's own
    isolated-copy design (see ``tools/mutate.py``'s module docstring) already
    gives every rule -- this just confirms ``--with-git`` does not weaken it.
    """
    source = tmp_path / "source"
    source.mkdir()
    _checkout(source)
    destination = tmp_path / "copy"
    destination.mkdir()
    monkeypatch.setattr(mutate, "REPO_ROOT", source)
    mutate._record_population(destination)
    mutate._lend_git_objects(destination)
    (destination / "docs").mkdir()
    (destination / "docs" / "tracked.md").write_text("mutated in the copy\n", encoding="utf-8")

    source_status = _git("-C", str(source), "status", "--short", "--", "docs/tracked.md").stdout

    assert source_status == "", (
        f"the source's own tracked.md reports {source_status!r} after the copy's was "
        f"overwritten; the two are supposed to share objects, not a working tree"
    )


def test_a_linked_worktree_source_is_refused_rather_than_lent_from_wrong(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gitdir *file* -- a linked worktree -- does not carry the index this borrows.

    ``git worktree add`` leaves ``.git`` as a one-line pointer
    (``gitdir: <path>``) rather than a directory holding ``HEAD``/``index``/
    ``refs``, so copying it as if it were the real thing would either raise on
    a missing file or silently hand the copy an index belonging to a different
    checkout. Refusing here is the same choice ``_record_population`` makes
    for a source that cannot be asked what it tracks: a build that cannot be
    made honest is refused, not degraded.
    """
    source = tmp_path / "source"
    source.mkdir()
    (source / ".git").write_text(
        "gitdir: /elsewhere/.git/worktrees/some-branch\n", encoding="utf-8"
    )
    destination = tmp_path / "copy"
    destination.mkdir()
    monkeypatch.setattr(mutate, "REPO_ROOT", source)

    with pytest.raises(HarnessError, match="needs a plain repository"):
        mutate._lend_git_objects(destination)

    assert not (destination / ".git").exists()


def test_without_the_flag_a_built_tree_carries_no_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deletion direction: nothing puts ``.git`` back unless asked.

    ``_COPY_IGNORE`` drops ``.git`` from every ordinary copy on purpose (see
    ``tools/mutate.py``'s comment on it) -- this is the default this module
    exists to add an *opt-in* escape hatch from, not to change. Mutation
    would find this: dropping the ``with_git`` guard so ``_lend_git_objects``
    always ran would leave every test in this file green while breaking the
    documented default every other mutation batch relies on for its 3.8 MB
    cost claim.
    """
    source = tmp_path / "source"
    source.mkdir()
    _checkout(source)
    monkeypatch.setattr(mutate, "REPO_ROOT", source)
    monkeypatch.setattr(mutate, "_uv", lambda: str(_stub_uv(tmp_path)))

    tree = mutate._build_tree(tmp_path / "copy", tmp_path / "cache")

    assert not (tree / ".git").exists()


def test_with_the_flag_a_built_trees_git_ls_files_matches_its_own_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_build_tree(with_git=True)`` wires the lending in, not merely makes it available.

    Mirrors ``test_mutate_population_manifest.py``'s
    ``test_building_a_tree_records_the_population_into_it``: mutation found
    that module's population write was available but not called from
    ``_build_tree``, and the same gap is possible here -- ``_lend_git_objects``
    working in isolation (the tests above) says nothing about whether
    ``_build_tree`` actually reaches for it when asked.
    """
    source = tmp_path / "source"
    source.mkdir()
    _checkout(source)
    monkeypatch.setattr(mutate, "REPO_ROOT", source)
    monkeypatch.setattr(mutate, "_uv", lambda: str(_stub_uv(tmp_path)))

    tree = mutate._build_tree(tmp_path / "copy", tmp_path / "cache", with_git=True)

    assert (tree / ".git").is_dir()
    listed = _git("-C", str(tree), "ls-files", "-z").stdout
    manifest = (tree / ".mutate-population").read_bytes().decode("utf-8")
    assert listed == manifest == "docs/tracked.md\x00"
