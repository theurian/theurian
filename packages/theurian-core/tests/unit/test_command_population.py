"""What the scan is allowed to open, checked against a repository built for it.

``command_population`` decides which files exist as far as this suite is
concerned, and every assertion in ``test_documented_commands`` is downstream of
that decision: a population that answers "nothing" makes the whole module pass.
The tests here are the ones that need a *repository* rather than this one --
a sandbox with a tracked file, an ignored file, an unmerged path, a draft the
product wrote -- which is why they are not in the module they defend.

Split out at 1026 lines, over the 800-line ceiling, and the seam is the fixture:
everything here builds a git checkout and asks what the population says about
it.

Lives under ``tests/`` and so inside ``UNREAD``, which matters here for the same
reason it matters in ``command_population`` -- the fixtures below quote dead
commands on purpose.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest
from command_population import (
    _files,
    _walked,
)


def test_the_fallback_walk_enters_only_what_the_repository_could_ship() -> None:
    """The rule that stands in for git where there is none, pinned as a rule.

    A tree with no ``.git`` in it is not hypothetical: the mutation harness
    copies the checkout without one, and a run there left 12,734 fixture files
    under ``.mutate-tmp/`` -- entire ``.theurian`` project directories with
    their own markdown, JSON and YAML, some of it not UTF-8. The scan read them,
    the unmutated control went RED, and every verdict in that batch with it.

    In a checkout none of this decides anything, because :func:`_population`
    asks git. What the fallback still has to get right is the direction of its
    error: it reads less than the repository ships, never more than the
    repository tracks.

    Pinned as a rule and not as the list of names seen so far, because the names
    keep changing and the rule does not.
    """
    assert _walked(
        [".claude", ".claude-plugin", ".github", ".theurian", "docs"], at_repository_root=False
    ) == [".claude", ".claude-plugin", ".github", ".theurian", "docs"]

    assert _walked([".theurian", "docs"], at_repository_root=True) == ["docs"]

    tool_state = [".mutate-tmp", ".mutate-home", ".venv", ".git", ".pytest_cache"]
    build_output = ["worktrees", "node_modules", "site", "htmlcov", "__pycache__"]
    for at_root in (True, False):
        assert _walked(tool_state, at_repository_root=at_root) == []
        assert _walked(build_output, at_repository_root=at_root) == []


def _require_git() -> str:
    """The git the population is defined by, or a skip that says why."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("the population is defined by `git ls-files`, and this machine has no git")
    return git


def _git(git: str, *arguments: str) -> None:
    """Run one git command in a sandbox and fail loudly if it did not work."""
    completed = subprocess.run(  # noqa: S603 - argv is written here, never user input
        [git, *arguments], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, (
        f"the fixture's own `git {' '.join(arguments)}` failed, so the test below would "
        f"be asserting against a tree nobody built:\n{completed.stderr}"
    )


@pytest.fixture
def sandbox(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A repository-shaped tree, cut off from the developer's own git configuration.

    ``GIT_CONFIG_GLOBAL`` and ``GIT_CONFIG_SYSTEM`` name files that do not
    exist, and ``HOME`` moves with them because git reads ``$HOME/.gitconfig``
    when the first is unset: a developer whose global ``core.excludesFile``
    happens to mention ``.theurian`` would otherwise get a different verdict
    here than CI does. ``GIT_CEILING_DIRECTORIES`` stops the *fallback* test
    from finding a repository above ``TMPDIR`` and taking the git path by
    accident, which would make it pass without exercising the fallback at all.

    The environment reaches the code under test because it runs git in a
    subprocess, which inherits it -- and it is also what keeps this test off the
    real ``~/.gitconfig``.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "absent.gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(home / "absent.gitconfig"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)

    root = tmp_path / "checkout"
    root.mkdir()
    return root


def _scanned_in(sandbox: pathlib.Path) -> list[str]:
    """The markdown the population hands the readers, relative to the sandbox."""
    return [
        path.relative_to(sandbox).as_posix()
        for path in _files(sandbox, frozenset({".md"}), repository=sandbox)
    ]


def test_a_git_ignored_document_is_no_part_of_the_population(sandbox: pathlib.Path) -> None:
    """A working tree ``git status`` calls clean must not fail this suite (#262).

    ``.theurian/`` is where a project keeps its own knowledge, so a machine that
    dogfoods Theurian keeps knowledge there that is deliberately never committed
    -- 56 bodies on the checkout that reported #262, excluded through
    ``.git/info/exclude``. One was a historical handoff note quoting
    ``theurian upgrade``, and because the population was defined by directory
    name, ``test_every_theurian_command_a_document_names_is_registered`` failed
    on a file no clone will ever hold. No exemption could have covered it: the
    path carries a ULID that exists on one machine.

    Ignored through ``.git/info/exclude`` rather than ``.gitignore`` deliberately
    -- that is the file #262's corpus used, and no clone can see it. What keeps
    it out of the population is simpler than the ignore chain, though, and worth
    saying so nobody re-derives the wrong rule from this fixture: it was never
    committed, and an ignored file is untracked by construction.

    Asserted as the whole list rather than as an absence, because an enumeration
    that returned nothing at all would satisfy ``ignored not in scanned`` while
    making every other assertion in this module pass by reading no files.
    """
    git = _require_git()
    _git(git, "init", "-q", str(sandbox))
    knowledge = sandbox / ".theurian" / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "committed.md").write_text("run `theurian upgrade`\n", encoding="utf-8")
    (knowledge / "local-only.md").write_text("quoting `theurian upgrade`\n", encoding="utf-8")
    (sandbox / ".git" / "info" / "exclude").write_text(
        ".theurian/knowledge/local-only.md\n", encoding="utf-8"
    )
    _git(git, "-C", str(sandbox), "add", ".theurian/knowledge/committed.md")

    scanned = _scanned_in(sandbox)

    assert scanned == [".theurian/knowledge/committed.md"]


def test_a_draft_the_product_itself_writes_is_no_part_of_the_population(
    sandbox: pathlib.Path,
) -> None:
    """A repository gate must not fail on the files the product tells you to create.

    ``--others --exclude-standard`` would add the files that exist and are not
    ignored, which reads as a strictly better gate and is not one: it fails on
    the workflow this repository documents. ``theurian propose`` writes
    ``.theurian/proposals/<proposal-id>/`` -- the migration, the body, and
    ``evidence.json`` -- and those three stay untracked for the whole review
    window ``propose accept`` exists to close. The committed ``.gitignore`` does
    not cover them and a fresh clone has no ``.git/info/exclude`` to fence them,
    so on a clone running the product's own flow the gate would go RED on a
    draft. Reproduced on one: all three files appear in that listing.

    Tracked is therefore the whole rule, and the boundary it draws is the right
    one -- a draft naming a dead command becomes a failure the moment it is
    staged, on the pull request that ships it.

    The tracked document is here so the assertion cannot pass by finding
    nothing, which is the one way this module goes quietly useless.
    """
    git = _require_git()
    _git(git, "init", "-q", str(sandbox))
    (sandbox / "docs").mkdir()
    (sandbox / "docs" / "committed.md").write_text("run `theurian init`\n", encoding="utf-8")
    _git(git, "-C", str(sandbox), "add", "docs/committed.md")
    proposal = sandbox / ".theurian" / "proposals" / "01K1ABCXYZ01234567890ABCDE"
    proposal.mkdir(parents=True)
    (proposal / "body.md").write_text("then run `theurian upgrade`\n", encoding="utf-8")
    (proposal / "evidence.json").write_text('{"note": "theurian upgrade"}\n', encoding="utf-8")

    scanned = _scanned_in(sandbox)

    assert scanned == ["docs/committed.md"]


def test_the_python_reader_is_handed_only_the_subtree_its_surface_names(
    sandbox: pathlib.Path,
) -> None:
    """``root`` decides which files owe an answer to a reader and which to the guard.

    One population feeds four surfaces, and only the Python one is narrowed --
    to Core's ``src/``, because that is the product's own source. A ``.py`` file
    outside it is not unwatched: it is in the population, no reader opens it,
    and :func:`test_no_file_that_names_a_command_escapes_the_scan` reports it if
    it names a command, which forces a decision instead of a silent read.

    Pinned because the narrowing used to be structural -- the walk started at
    ``root`` and could not yield above it -- and is now one condition that
    deletes cleanly. Deleting it hands ``tools/`` and ``plugins/`` Python to a
    tokenizing reader that was never scoped to them, and until this test
    existed, nothing in the suite noticed.
    """
    git = _require_git()
    _git(git, "init", "-q", str(sandbox))
    source = sandbox / "packages" / "theurian-core" / "src" / "theurian"
    source.mkdir(parents=True)
    (source / "compatibility.py").write_text('REMEDY = "theurian upgrade"\n', encoding="utf-8")
    (sandbox / "tools").mkdir()
    (sandbox / "tools" / "harness.py").write_text('LABEL = "theurian upgrade"\n', encoding="utf-8")
    _git(git, "-C", str(sandbox), "add", "packages", "tools")

    scanned = [
        path.relative_to(sandbox).as_posix()
        for path in _files(
            sandbox / "packages" / "theurian-core" / "src",
            frozenset({".py"}),
            repository=sandbox,
        )
    ]

    assert scanned == ["packages/theurian-core/src/theurian/compatibility.py"]


def test_a_tracked_document_under_a_test_tree_is_not_handed_to_a_reader(
    sandbox: pathlib.Path,
) -> None:
    """The exclusion applies to the readers, not only to the guard that reports gaps.

    :data:`UNREAD` exists because a test naming a dead command fails on its own
    if it runs one, and because the fixtures here quote dead commands on
    purpose. Both call sites apply it, and only one of them is exercised by this
    repository: deleting the guard's call reports ``command_population``,
    ``command_extraction`` and the integration tests, while deleting the one in
    :func:`_files` changes no verdict, because exactly one file under those
    prefixes has a scanned suffix -- ``tests/e2e/README.md`` -- and it names no
    command.

    So this is a synthetic fixture for a guard no real file reaches. Without it
    the filter deletes clean, and the first markdown fixture written under
    ``packages/theurian-core/tests/`` becomes a failure nobody asked for.
    """
    git = _require_git()
    _git(git, "init", "-q", str(sandbox))
    fixtures = sandbox / "packages" / "theurian-core" / "tests" / "fixtures"
    fixtures.mkdir(parents=True)
    (fixtures / "corpus.md").write_text("a fixture quoting `theurian upgrade`\n", encoding="utf-8")
    (sandbox / "docs").mkdir()
    (sandbox / "docs" / "shipped.md").write_text("run `theurian init`\n", encoding="utf-8")
    _git(git, "-C", str(sandbox), "add", "packages", "docs")

    scanned = _scanned_in(sandbox)

    assert scanned == ["docs/shipped.md"]


def test_a_tracked_document_deleted_from_the_working_tree_is_not_read(
    sandbox: pathlib.Path,
) -> None:
    """``--cached`` answers for the index, and the index outlives the file.

    Deleting a file without telling git is an ordinary state mid-edit, and the
    path git still reports for it points at nothing. Handing that to the readers
    is not a wrong answer but a crash: :func:`_text` would raise
    ``FileNotFoundError`` out of a module whose whole job is to *report* files,
    and the traceback would name the reader rather than the deletion.
    """
    git = _require_git()
    _git(git, "init", "-q", str(sandbox))
    (sandbox / "docs").mkdir()
    deleted = sandbox / "docs" / "gone.md"
    deleted.write_text("run `theurian upgrade`\n", encoding="utf-8")
    (sandbox / "docs" / "here.md").write_text("run `theurian init`\n", encoding="utf-8")
    _git(git, "-C", str(sandbox), "add", "docs/gone.md", "docs/here.md")
    deleted.unlink()

    scanned = _scanned_in(sandbox)

    assert scanned == ["docs/here.md"]


def test_a_copy_of_the_tree_inside_another_checkout_takes_the_fallback(
    sandbox: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tree nested in an unrelated repository is answered for by that repository.

    One ``TMPDIR`` away from real: ``tools/mutate.py`` builds its copies outside
    this checkout, and nothing says the directory it builds them in is outside
    every checkout. Asked from inside such a copy, git answers for the outer
    repository's index, which holds none of these paths -- measured on a scratch
    repository, an empty listing and exit 0. Every assertion in this module
    passes when no file is read, so that answer is worse than an error, and the
    toplevel git reports has to be this tree before its listing is used.

    The ceiling is raised for this test alone, and that is the whole fixture:
    :func:`sandbox` pins it at the directory holding the sandbox so no test
    finds a repository by accident, and under that ceiling git never discovers
    the outer repository either -- ``rev-parse`` exits 128, the listing is
    refused for the *first* reason rather than the toplevel, and deleting the
    toplevel check leaves this test green. It did, until the ceiling moved up
    one directory.

    Asserted through the fallback's own signature -- the repository-root
    ``.theurian/`` missing while ``docs/`` is read -- because that is what
    distinguishes "fell back" from "took the outer repository's word".
    """
    git = _require_git()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(sandbox.parent.parent))
    _git(git, "init", "-q", str(sandbox.parent))
    (sandbox / ".theurian" / "knowledge").mkdir(parents=True)
    (sandbox / ".theurian" / "knowledge" / "local-only.md").write_text(
        "quoting `theurian upgrade`\n", encoding="utf-8"
    )
    (sandbox / "docs").mkdir()
    (sandbox / "docs" / "shipped.md").write_text("run `theurian init`\n", encoding="utf-8")

    scanned = _scanned_in(sandbox)

    assert scanned == ["docs/shipped.md"]


def test_a_tree_that_is_not_a_checkout_reads_less_rather_than_more(
    sandbox: pathlib.Path,
) -> None:
    """The population has to answer where there is no git to ask, and under-read there.

    ``tools/mutate.py`` copies the checkout with ``shutil.copytree`` and its
    ``_COPY_IGNORE`` drops ``.git`` on purpose ("the copy is not a repository,
    and the suite has been run without one"), while copying everything else the
    developer's tree carried -- local-only knowledge and draft proposals alike.
    So the fallback runs in exactly the environment #262 is about, with no way
    to tell a tracked file from an untracked one.

    It resolves that by refusing the one directory where a project keeps its own
    state: ``.theurian/`` at the top of the tree, which this repository tracks
    nothing under (``git ls-files .theurian`` is empty, measured 2026-08-19 at
    bd4fb25) and which is where both the private knowledge and the drafts land.
    A nested one is sample content and is read -- that is
    ``examples/sample-project/.theurian/config.yaml``, which the scan has always
    covered.

    The cost is stated rather than hidden: without git the fallback under-reads,
    and every file it skips is one the real gate still reads, because the gate
    runs in a checkout.
    """
    (sandbox / ".theurian" / "knowledge").mkdir(parents=True)
    (sandbox / ".theurian" / "knowledge" / "local-only.md").write_text(
        "quoting `theurian upgrade`\n", encoding="utf-8"
    )
    (sandbox / "docs").mkdir()
    (sandbox / "docs" / "shipped.md").write_text("run `theurian init`\n", encoding="utf-8")
    nested = sandbox / "examples" / "sample-project" / ".theurian"
    nested.mkdir(parents=True)
    (nested / "notes.md").write_text("run `theurian init`\n", encoding="utf-8")

    scanned = _scanned_in(sandbox)

    assert scanned == ["docs/shipped.md", "examples/sample-project/.theurian/notes.md"]
