"""The copy is told what the checkout tracks, because it cannot ask.

``_build_tree`` copies the checkout without ``.git`` on purpose, so the suite
running inside the copy has no repository to ask which files ship. It reads
``.mutate-population`` instead, and this is the end that writes it -- the other
end is ``_manifest_listing`` in
``packages/theurian-core/tests/command_population.py``.

Without it the suite inside the copy falls back to a name-based guess that
refuses the repository-root ``.theurian/`` wholesale. On ``dogfood/dev7-corpus``
that is 81 tracked files -- 26 knowledge documents, 27 migrations, 27 proposals,
one specification -- 78 of them with a suffix the scan reads, out of a scanned
population of 321. Every verdict in a batch would be graded against 24% less
than the gate the batch stands in for, silently, because a smaller population
fails nothing.
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

    The same isolation ``test_command_population``'s ``sandbox`` fixture
    documents, and the same reason: a global ``core.excludesFile`` or a
    ``init.templateDir`` on one machine would otherwise decide what these
    fixtures track, and CI would disagree without either being wrong. ``HOME``
    moves too, because git reads ``$HOME/.gitconfig`` when
    ``GIT_CONFIG_GLOBAL`` is unset -- and because a test that writes into the
    real one has already failed.

    ``_record_population`` builds its own environment and drops ``GIT_CONFIG_*``
    from it, so what reaches the code under test here is ``HOME`` and the
    ceiling; the rest governs the fixtures' own ``git init`` and ``git add``.
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


def _git(*arguments: str) -> None:
    """One git command, by absolute path, failing loudly and with git's reason."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("the manifest is `git ls-files` output, and this machine has no git")
    completed = subprocess.run(  # noqa: S603 - argv is written here, never user input
        [git, *arguments], check=False, capture_output=True, text=True
    )
    assert completed.returncode == 0, (
        f"the fixture's own `git {' '.join(arguments)}` failed, so the test below would "
        f"be asserting against a tree nobody built:\n{completed.stderr}"
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


def test_an_inherited_git_index_file_cannot_answer_for_the_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The writer has the same hijack as the reader, and needed the same fix.

    ``GIT_INDEX_FILE`` binds the index while ``-C`` binds the working tree, so
    an inherited one answers for somebody else's index and ``ls-files`` comes
    back empty. Git exports it to hooks: a harness started from a ``pre-commit``
    would record a manifest for the index that hook was invoked with, and every
    copy in the batch would scan whatever that turned out to be.

    Found by mutation, not by review of the fix -- ``_git_output`` was given
    this protection and ``_record_population`` was not, and nothing in the suite
    noticed the asymmetry.
    """
    source = tmp_path / "source"
    source.mkdir()
    _checkout(source)
    destination = tmp_path / "copy"
    destination.mkdir()
    monkeypatch.setattr(mutate, "REPO_ROOT", source)
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "somebody-elses.index"))

    mutate._record_population(destination)

    assert (destination / ".mutate-population").read_bytes() == b"docs/tracked.md\x00"


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


def test_a_source_that_cannot_be_asked_what_it_tracks_stops_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refusing to build beats building something that measures the wrong suite.

    A copy with no manifest falls back to a name-based guess that drops the
    repository-root ``.theurian/`` -- 78 scanned files of the 321 the gate reads
    on the corpus branch, 24% of it. Every verdict in the batch would then
    be computed against a suite that does not exist, and each would still read
    as an ordinary KILLED or SURVIVED. Nothing downstream can tell, which is
    what makes a warning the wrong instrument and an exception the right one:
    ``_prepare_mode`` unwinds the work root on ``HarnessError``, and a batch
    stops before it produces numbers nobody should trust.
    """
    source = tmp_path / "not-a-checkout"
    source.mkdir()
    destination = tmp_path / "copy"
    destination.mkdir()
    monkeypatch.setattr(mutate, "REPO_ROOT", source)

    with pytest.raises(HarnessError, match="could not record the population"):
        mutate._record_population(destination)

    assert not (destination / ".mutate-population").exists()


def test_a_source_inside_another_checkout_does_not_leave_an_empty_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 0 and nothing on stdout is a failure wearing a success's clothes.

    A source tree that is not a checkout but sits *inside* one -- a copy
    unpacked under someone's repository, one ``TMPDIR`` away from real -- is
    answered for by the outer repository's index. Where that index holds none of
    the source's paths, ``ls-files`` exits 0 with no output, and a
    returncode-only check writes a 0-byte manifest that says this repository
    ships nothing. The reader would then have an authoritative-looking answer of
    *nothing*, which is the one answer that makes the documented-command suite
    pass by reading no files.
    """
    outer = tmp_path / "outer"
    source = outer / "copy-of-a-checkout"
    source.mkdir(parents=True)
    (source / "docs").mkdir()
    (source / "docs" / "a.md").write_text("run `theurian init`\n", encoding="utf-8")
    _git("init", "-q", str(outer))
    destination = tmp_path / "copy"
    destination.mkdir()
    monkeypatch.setattr(mutate, "REPO_ROOT", source)

    with pytest.raises(HarnessError, match="could not record the population"):
        mutate._record_population(destination)

    assert not (destination / ".mutate-population").exists()


def test_a_source_whose_files_the_outer_checkout_tracks_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same nesting, with the outer repository *holding* those paths.

    This is the half neither the exit code nor the emptiness check can see.
    When the outer repository tracks what is inside the copy -- somebody
    committed the unpacked tree -- ``git -C <source> ls-files --cached`` exits 0
    and returns a **non-empty** listing of the outer repository's index,
    expressed relative to the source. Measured on a scratch tree: it named
    ``.theurian/knowledge/local.md``, a file the source does not track and no
    clone of the real repository has, because the source is not a repository at
    all.

    Recording that as the population would hand every copy in the batch a
    manifest describing a different repository -- the #262 shape, arriving
    through the mechanism built to prevent it. Only comparing
    ``rev-parse --show-toplevel`` against the source catches it, which is the
    guard the reader has had since the toplevel check and the writer did not.
    """
    outer = tmp_path / "outer"
    source = outer / "copy-of-a-checkout"
    (source / ".theurian" / "knowledge").mkdir(parents=True)
    (source / ".theurian" / "knowledge" / "local.md").write_text(
        "quoting `theurian upgrade`\n", encoding="utf-8"
    )
    (source / "docs").mkdir()
    (source / "docs" / "a.md").write_text("run `theurian init`\n", encoding="utf-8")
    _git("init", "-q", str(outer))
    _git("-C", str(outer), "add", "-A", ".")
    destination = tmp_path / "copy"
    destination.mkdir()
    monkeypatch.setattr(mutate, "REPO_ROOT", source)

    with pytest.raises(HarnessError, match="could not record the population"):
        mutate._record_population(destination)

    assert not (destination / ".mutate-population").exists()


def test_a_checkout_with_an_empty_index_stops_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real checkout that tracks nothing yet still cannot say what ships.

    The toplevel guard closed the nested-source face and in doing so made the
    emptiness check look dead -- mutation says otherwise, and this is the state
    that keeps it alive: ``git init`` and nothing added. The toplevel *is* this
    tree, the exit code *is* 0, and the listing is empty because the index is.
    Writing that manifest tells the copy the repository ships nothing, which is
    the answer that makes the documented-command suite pass by reading no files.
    """
    source = tmp_path / "source"
    source.mkdir()
    _git("init", "-q", str(source))
    (source / "docs").mkdir()
    (source / "docs" / "written-but-not-added.md").write_text("x\n", encoding="utf-8")
    destination = tmp_path / "copy"
    destination.mkdir()
    monkeypatch.setattr(mutate, "REPO_ROOT", source)

    with pytest.raises(HarnessError, match="could not record the population"):
        mutate._record_population(destination)

    assert not (destination / ".mutate-population").exists()


def test_a_git_that_cannot_be_executed_stops_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found on ``PATH`` and unable to run is a different failure from absent.

    ``shutil.which`` answers "there is a git here", and executing it still
    raises: measured with an executable whose interpreter does not exist, which
    is what a half-installed toolchain or a stale ``PATH`` entry into a removed
    virtualenv looks like -- ``FileNotFoundError`` naming the *interpreter*.
    That is an ``OSError`` out of ``subprocess``, and uncaught it walks past
    ``_prepare_mode``'s ``except HarnessError`` and leaves the work root behind
    with a traceback that names ``subprocess`` rather than the tool.
    """
    tools = tmp_path / "broken-tools"
    tools.mkdir()
    broken_git = tools / "git"
    broken_git.write_text("#!/nonexistent/interpreter\n", encoding="utf-8")
    broken_git.chmod(0o755)
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "copy"
    destination.mkdir()
    monkeypatch.setattr(mutate, "REPO_ROOT", source)
    monkeypatch.setenv("PATH", str(tools))

    with pytest.raises(HarnessError, match="could not record the population"):
        mutate._record_population(destination)


def test_a_missing_git_stops_the_build_instead_of_crashing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure has to arrive as ``HarnessError`` or the work root leaks.

    ``_prepare_mode`` cleans up its ``mkdtemp`` root in ``except HarnessError``.
    A bare ``FileNotFoundError`` from a git that is not on ``PATH`` walks past
    that handler and leaves a full copy of the checkout, plus its virtualenv,
    in the temporary directory -- with a traceback that names ``subprocess``
    rather than the missing tool.
    """
    empty_path = tmp_path / "no-tools"
    empty_path.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "copy"
    destination.mkdir()
    monkeypatch.setattr(mutate, "REPO_ROOT", source)
    monkeypatch.setenv("PATH", str(empty_path))

    with pytest.raises(HarnessError, match="could not record the population"):
        mutate._record_population(destination)


def test_a_copy_of_a_prepared_tree_keeps_the_population_it_carried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one no-git case that is not a degrade, and where its note may go.

    A prepared tree has no ``.git`` and does carry a manifest, so copying *it*
    produces a copy whose manifest is already right -- the same files, recorded
    from the same index. Refusing there would break a working case to no
    purpose.

    The note goes to stderr, and that is the contract rather than a preference:
    ``--prepare-tree`` puts the tree's path on stdout and nothing else, so a
    caller's ``cd $(mutate.py --prepare-tree ...)`` captures whatever else is
    printed there. A commentary line on stdout makes that ``cd`` fail and the
    documented ``rm -rf`` clean up a path that does not exist.
    """
    source = tmp_path / "prepared-source"
    source.mkdir()
    destination = tmp_path / "copy"
    destination.mkdir()
    (destination / ".mutate-population").write_bytes(b"docs/carried.md\x00")
    monkeypatch.setattr(mutate, "REPO_ROOT", source)

    mutate._record_population(destination)

    assert (destination / ".mutate-population").read_bytes() == b"docs/carried.md\x00"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "kept the population its source recorded" in captured.err


def test_a_truncated_manifest_carried_by_a_copy_is_not_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A manifest that was cut short is worse than none: it is a shorter suite.

    ``ls-files -z`` terminates every entry, so a listing that does not end in a
    NUL stopped mid-write -- a killed harness, a full disk. Keeping it hands the
    copy a population missing however many files were still to be written, with
    nothing anywhere saying so. The build stops instead.
    """
    source = tmp_path / "prepared-source"
    source.mkdir()
    destination = tmp_path / "copy"
    destination.mkdir()
    (destination / ".mutate-population").write_bytes(b"docs/carried.md\x00docs/half-writ")
    monkeypatch.setattr(mutate, "REPO_ROOT", source)

    with pytest.raises(HarnessError, match="could not record the population"):
        mutate._record_population(destination)
