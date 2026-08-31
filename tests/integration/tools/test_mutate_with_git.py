"""``--with-git`` lends a copy the source's objects, so git-needing rules run.

The other half of ``.mutate-population``'s contract
(``test_mutate_population_manifest.py``): a manifest is a path list, and it is
enough for every rule that reads a *path* or working-tree bytes. It is nothing
for a rule that reads a **blob** --
``test_dogfood_corpus_governance.py::test_every_pinned_body_is_byte_identical_to_
its_source_anchor_commit`` compares a committed body against ``git cat-file
blob <commitSha>:<filePath>``, and a manifest has no blob to hand over. That
rule is one of **four** measured to skip loudly in every ordinary
``tools/mutate.py`` copy (#452's finding; ``_lend_git_objects``'s docstring in
``tools/mutate.py`` carries the dated, named list -- the other three are
``test_root_corpus_applies.py`` and two rules in
``test_git_trailer_source.py``), so a mutation whose only killer is any one of
them comes back SURVIVED with no sign that the harness never ran the rule that
would have caught it.

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

import hashlib
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


def _hash_tree(root: Path) -> dict[str, str]:
    """Every regular file under ``root``, by relative path, to its sha256.

    A directory listing alone would miss a file rewritten in place with the
    same name -- exactly the shape of mistake this guards against (a write
    aimed at ``config`` or ``index`` that already exists in the source).
    """
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


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


def test_lending_never_writes_a_single_byte_into_the_sources_own_git_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The direct check: not one of the four files this function writes is aimed at the source.

    ``_lend_git_objects`` writes ``config`` and ``objects/info/alternates``
    into ``destination``, and reads (never writes) ``HEAD``/``index``/
    ``packed-refs``/``shallow``/``refs`` from ``source``. A copy-paste slip
    that aimed any one of those writes at ``git_dir`` instead of ``borrowed``
    -- swapped in a refactor, or a variable renamed halfway -- would corrupt
    the one ``.git`` every other test and every real mutation batch shares.
    Caught by hashing the source's own ``.git`` directory (every regular file
    inside it, not merely ``config``) before and after.
    """
    source = tmp_path / "source"
    source.mkdir()
    _checkout(source)
    before = _hash_tree(source / ".git")
    destination = tmp_path / "copy"
    destination.mkdir()
    monkeypatch.setattr(mutate, "REPO_ROOT", source)

    mutate._lend_git_objects(destination)

    after = _hash_tree(source / ".git")
    assert after == before, (
        "the source's own .git directory changed after lending its objects to a copy -- "
        "lending must only ever read from the source, never write to it"
    )


def test_a_mutation_inside_a_with_git_copy_never_touches_the_source_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Objects are lent, not shared write access -- the copy's ``git`` never writes upstream.

    Asserts what the code actually decides, not an incidental consequence of
    it: ``git -C <copy> rev-parse --show-toplevel`` must resolve to the copy
    itself, and the lent ``config`` must set no explicit ``core.worktree`` --
    if it named the source instead (a plausible future slip: someone "fixing"
    a git command that misbehaves inside the copy by pointing ``worktree`` at
    the real tree), every git *write* run against the copy would land in the
    source's actual files, not the copy's. A previous version of this test
    wrote the mutated file directly with Python rather than through git and
    checked the source's ``git status`` afterwards -- which passed even with
    ``_lend_git_objects`` entirely no-opped (mutation-tested while landing this
    fix), because a plain filesystem write was never going to reach the
    source either way. This version reads what ``_lend_git_objects`` itself
    decided, so a regression in the decision -- not merely in some unrelated
    write path -- is what turns it RED.
    """
    source = tmp_path / "source"
    source.mkdir()
    _checkout(source)
    destination = tmp_path / "copy"
    destination.mkdir()
    monkeypatch.setattr(mutate, "REPO_ROOT", source)

    mutate._lend_git_objects(destination)

    toplevel = _git("-C", str(destination), "rev-parse", "--show-toplevel").stdout.strip()
    assert Path(toplevel).resolve() == destination.resolve(), (
        f"the copy's own git resolved its toplevel to {toplevel!r}, not the copy "
        f"({destination}) -- a misconfigured core.worktree would redirect every git write "
        f"run against the copy into the source's real files"
    )
    config_text = (destination / ".git" / "config").read_text(encoding="utf-8")
    assert "worktree" not in config_text.lower(), (
        f"the lent config names an explicit core.worktree ({config_text!r}); left unset, "
        f"git infers the copy's own directory -- named explicitly, it could point anywhere, "
        f"including back at the source"
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


def _checkout_sha256_format(root: Path) -> bool:
    """A real repository using git's SHA-256 object format, not SHA-1.

    Returns whether it could be built at all -- older git binaries do not
    support ``--object-format``, and the test calling this skips rather than
    fails when that is what is on the machine running it.
    """
    git = shutil.which("git")
    if git is None:
        return False
    initialised = subprocess.run(  # noqa: S603 - argv is written here, never user input
        [git, "init", "-q", "--object-format=sha256", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    if initialised.returncode != 0:
        return False
    _git("-C", str(root), "config", "user.email", "mutate-tests@example.invalid")
    _git("-C", str(root), "config", "user.name", "mutate tests")
    (root / "docs").mkdir()
    (root / "docs" / "tracked.md").write_text("run `theurian init`\n", encoding="utf-8")
    _git("-C", str(root), "add", "docs/tracked.md")
    _git("-C", str(root), "commit", "-q", "-m", "seed")
    return True


def test_a_sha256_object_format_source_is_refused_not_silently_misdescribed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M3: a non-SHA-1 source must not be lent under a hardcoded repositoryformatversion=0.

    ``git init --object-format=sha256`` writes ``repositoryformatversion = 1``
    and ``[extensions] objectformat = sha256``. Lending such a source's
    objects while the borrowed ``config`` still hardcodes
    ``repositoryformatversion = 0`` would not fail loudly: every git read
    against the copy would exit 0 while misinterpreting each borrowed object's
    hash algorithm -- silent corruption, and the exact
    binary-garbage-with-exit-0 shape the population guards elsewhere in this
    module were built to avoid.
    """
    source = tmp_path / "source"
    source.mkdir()
    if not _checkout_sha256_format(source):
        pytest.skip("this git does not support --object-format=sha256")
    destination = tmp_path / "copy"
    destination.mkdir()
    monkeypatch.setattr(mutate, "REPO_ROOT", source)

    with pytest.raises(HarnessError, match="only lends a repositoryformatversion=0"):
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
    documented default every other mutation batch relies on for its working-
    tree-size cost claim (``_lend_git_objects``'s docstring carries the current
    dated figure).
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
