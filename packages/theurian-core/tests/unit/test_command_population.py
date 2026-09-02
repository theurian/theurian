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

import command_population
import pytest
from command_population import (
    REPO_ROOT,
    _files,
    _git_listing,
    _git_output,
    _manifest_listing,
    _population,
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
    asks git, and in a copy the harness prepared it reads that copy's manifest.
    This is the last resort, and what it has to get right is bounded: it enters
    no directory that holds a project's own state or a tool's.

    It does **not** get "never more than the repository tracks" right, and the
    claim used to say so. Measured in a gitless copy: an untracked scratch note
    under ``docs/`` is read, and one naming a dead command turned the control
    RED (population 398 to 399). Directories are all this rule can refuse, so
    an untracked file outside them is indistinguishable from a tracked one --
    which is the whole reason the manifest exists.

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


def _git(git: str, *arguments: str, stdin: str = "") -> str:
    """Run one git command in a sandbox and fail loudly if it did not work."""
    completed = subprocess.run(  # noqa: S603 - argv is written here, never user input
        [git, *arguments], input=stdin, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, (
        f"the fixture's own `git {' '.join(arguments)}` failed, so the test below would "
        f"be asserting against a tree nobody built:\n{completed.stderr}"
    )
    return completed.stdout


@pytest.fixture
def sandbox(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A repository-shaped tree, cut off from the developer's own git configuration.

    ``GIT_CONFIG_GLOBAL`` and ``GIT_CONFIG_SYSTEM`` name files that do not
    exist, and ``HOME`` moves with them because git reads ``$HOME/.gitconfig``
    when the first is unset: a developer whose global config happens to mention
    ``.theurian`` would otherwise get a different verdict here than CI does.
    ``GIT_CEILING_DIRECTORIES`` stops a fallback test from finding a repository
    above ``TMPDIR`` and taking the git path by accident, which would make it
    pass without exercising the fallback at all.

    Only some of this reaches the code under test, and the split is deliberate.
    :func:`_git_output` runs git under an environment it builds itself, dropping
    every variable that would make git answer for another tree or index -- so
    the ``GIT_CONFIG_*`` set here governs the *fixture's* own ``git init`` and
    ``git add``, while ``HOME`` and ``GIT_CEILING_DIRECTORIES`` are inherited by
    both. That is why a test can hand the module a hostile ``GIT_INDEX_FILE``
    and expect it to be ignored, and in the same file set a ceiling and expect
    it to be obeyed.
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

    **What this pins is the reason, not a second mechanism.** The
    ``.git/info/exclude`` write is inert: an ignored file is untracked by
    construction, so ``--cached`` would leave it out with the exclude file
    empty, and the draft-proposal test next door covers the same code path.
    It stays because #262's face is worth keeping executable and because the
    inference runs the other way for a reader -- "the ignore chain is what
    excludes it" is the rule somebody would re-derive from a passing suite, and
    then reintroduce ``--others --exclude-standard`` believing it safe.

    Asserted as the whole list rather than as an absence, because an enumeration
    that returned nothing at all would satisfy ``ignored not in scanned`` and
    say nothing about #262 -- the vacuity is this assertion's, not the suite's.
    An empty population is caught in ``test_documented_commands`` -- by
    ``test_the_scan_reaches_every_arm_of_every_reader``, by
    ``test_no_recorded_exception_outlives_the_text_it_excuses``, and now
    directly by the floor ``test_no_file_that_names_a_command_escapes_the_scan``
    asserts on the population it is handed.
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


def test_a_path_git_would_have_to_quote_survives_the_listing(sandbox: pathlib.Path) -> None:
    """``-z`` is not a nicety: every other output mode is ambiguous.

    Git separates paths with newlines and C-quotes anything that would break
    that, so a filename containing a newline comes back as
    ``"docs/two\\nlines.md"`` -- quoted, escaped, and indistinguishable from two
    ordinary paths without an unquoting step this module does not have. Measured
    on a scratch repository: parsed the way this module parses, that listing
    yields one entry which is no file, and the population empties.

    A newline in a filename is not a thing anyone commits on purpose. It is a
    thing a repository can be handed -- and the cost of getting it wrong is not
    "that file is skipped" but "every file is skipped", which is the failure
    mode this module is least able to notice.
    """
    git = _require_git()
    _git(git, "init", "-q", str(sandbox))
    (sandbox / "docs").mkdir()
    (sandbox / "docs" / "two\nlines.md").write_text("run `theurian init`\n", encoding="utf-8")
    (sandbox / "docs" / "with spaces.md").write_text("run `theurian init`\n", encoding="utf-8")
    _git(git, "-C", str(sandbox), "add", "-A", "docs")

    scanned = _scanned_in(sandbox)

    assert scanned == ["docs/two\nlines.md", "docs/with spaces.md"]


def test_a_symlinked_repository_is_recognised_as_its_own_checkout(
    sandbox: pathlib.Path,
) -> None:
    """The toplevel comparison must survive being handed a path through a symlink.

    ``rev-parse --show-toplevel`` answers with the physical path, so comparing
    it against an unresolved argument would say "this tree is not its own
    checkout" and drop a perfectly good repository onto the fallback -- reading
    a different set of files, silently, because falling back is not an error.

    ``/tmp`` is a symlink to ``/private/tmp`` on this platform, which is how a
    reviewer met this; both sides are resolved before the comparison and this
    is what keeps them that way.
    """
    git = _require_git()
    _git(git, "init", "-q", str(sandbox))
    (sandbox / "docs").mkdir()
    (sandbox / "docs" / "tracked.md").write_text("run `theurian init`\n", encoding="utf-8")
    _git(git, "-C", str(sandbox), "add", "docs/tracked.md")
    through_a_link = sandbox.parent / "link-to-checkout"
    through_a_link.symlink_to(sandbox)

    assert _git_listing(through_a_link) == ("docs/tracked.md",)


def test_a_path_left_unmerged_by_a_conflict_is_listed_once(sandbox: pathlib.Path) -> None:
    """A merge conflict is a legitimate local state, and it must not fail this suite.

    The index records up to three entries for an unmerged path -- the merge
    base and the two sides -- and ``git ls-files --cached`` prints the path once
    per stage. Measured on a scratch repository: three lines for one file, with
    ``-z`` and without.

    Downstream that is not a duplicate path but a duplicate *finding*.
    :func:`_scan` yields one invocation per occurrence on purpose, so that an
    exemption's count means something; three copies of a file make three copies
    of every invocation in it, and the count-based check reports surplus
    occurrences for a file whose every mention is already excused. The suite
    goes RED on the wrong thing, in the middle of a conflict, and points at a
    document nobody has touched -- which is the same class as #262: a false
    failure on a local state git considers ordinary.

    The stages are built with ``update-index --index-info`` rather than by
    provoking a real merge, because the state wanted here is the index's, and
    an actual conflicting merge would also depend on the merge driver.
    """
    git = _require_git()
    _git(git, "init", "-q", str(sandbox))
    (sandbox / "docs").mkdir()
    conflicted = sandbox / "docs" / "conflicted.md"
    conflicted.write_text("both sides quote `theurian upgrade`\n", encoding="utf-8")
    blob = _git(git, "-C", str(sandbox), "hash-object", "-w", "docs/conflicted.md").strip()
    stages = "".join(f"100644 {blob} {stage}\tdocs/conflicted.md\n" for stage in (1, 2, 3))
    _git(git, "-C", str(sandbox), "update-index", "--index-info", stdin=stages)

    scanned = _scanned_in(sandbox)

    assert scanned == ["docs/conflicted.md"]


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


def test_an_inherited_git_index_file_cannot_answer_for_this_tree(
    sandbox: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GIT_INDEX_FILE`` binds the index, and the toplevel check does not see it.

    The check compares ``rev-parse --show-toplevel`` against the tree being
    asked about, which pins the *working tree* and says nothing about which
    index answers for it. Measured: with ``GIT_INDEX_FILE`` pointed at a file
    that is not this repository's, ``--show-toplevel`` still returns the
    repository -- the check passes -- and ``ls-files --cached`` reads the
    foreign index and returns nothing.

    Nobody sets that variable by hand. Git sets it for hooks, so a suite run
    from a ``pre-commit`` or ``post-merge`` hook inherits it, and the population
    would be whatever that hook's index held.

    Fixed by running git with an environment the module owns rather than the
    one it inherited, which is also why the sandbox's stripped ``GIT_*`` no
    longer has to be trusted to reach the subprocess.
    """
    git = _require_git()
    _git(git, "init", "-q", str(sandbox))
    (sandbox / "docs").mkdir()
    (sandbox / "docs" / "tracked.md").write_text("run `theurian init`\n", encoding="utf-8")
    _git(git, "-C", str(sandbox), "add", "docs/tracked.md")
    monkeypatch.setenv("GIT_INDEX_FILE", str(sandbox.parent / "somebody-elses.index"))

    scanned = _scanned_in(sandbox)

    assert scanned == ["docs/tracked.md"]


def test_a_git_that_fails_for_an_unexpected_reason_says_so(sandbox: pathlib.Path) -> None:
    """ "No repository here" and "git refused" must not arrive as the same silence.

    Both end at the fallback, and only one of them is a tree telling the truth
    about itself. A ``safe.directory`` refusal on a checkout owned by another
    user, a corrupt config, a git that cannot be executed -- each would have
    dropped the whole population onto the name-based walk with nothing said, on
    a machine where the git path was available and expected.

    So an exit that is not "not a git repository" carries git's own stderr into
    a warning, which this suite's ``filterwarnings = error`` turns into a
    failure. What matters is that **git's reason reaches the reader**, not that
    the module's own sentence does -- so the stub writes the message a
    ``safe.directory`` refusal writes, and the assertion matches on that rather
    than on this module's wording, which is free to change and did.
    """
    _require_git()
    refusing_git = sandbox / "refusing-git"
    refusing_git.write_text(
        "#!/bin/sh\necho 'fatal: detected dubious ownership in repository' >&2\nexit 128\n",
        encoding="utf-8",
    )
    refusing_git.chmod(0o755)

    with pytest.warns(RuntimeWarning, match="detected dubious ownership"):
        answer = _git_output(str(refusing_git), sandbox, "rev-parse", "--show-toplevel")

    assert answer is None


def test_a_tree_that_simply_has_no_repository_is_not_reported_as_a_failure(
    sandbox: pathlib.Path,
) -> None:
    """The quiet half of the same rule, and the one that would break every run.

    A copy the mutation harness made has no ``.git`` by design, so the absent
    repository is the *expected* answer there and on any tree the fallback is
    meant to serve. Warning about it would turn this suite's
    ``filterwarnings = error`` on the harness itself and fail every run inside a
    prepared tree -- the discrimination is what makes the noisy half affordable,
    and until now nothing asserted it directly.
    """
    _require_git()
    absent_git = sandbox / "absent-repository-git"
    absent_git.write_text(
        "#!/bin/sh\necho 'fatal: not a git repository (or any parent up to /)' >&2\nexit 128\n",
        encoding="utf-8",
    )
    absent_git.chmod(0o755)

    answer = _git_output(str(absent_git), sandbox, "rev-parse", "--show-toplevel")

    assert answer is None


def test_a_git_that_never_returns_does_not_hold_the_gate_open(
    sandbox: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A subprocess with no timeout is a gate with no upper bound on its runtime.

    ``ls-files`` takes 11 ms on this repository, so the bound is not about
    slowness -- it is about the cases where git does not come back at all: a
    contended ``index.lock``, a credential helper waiting on a prompt, a
    filesystem that has stopped answering. Without a timeout the suite waits
    with it, and CI reports a job that hung rather than a check that failed.

    Driven with a stand-in for git rather than a real hang, because
    :func:`_git_output` takes the executable as an argument -- which is the
    seam that makes this testable at all -- and the module's own bound is
    lowered for the length of the test so it costs a second rather than thirty.
    """
    _require_git()
    slow_git = sandbox / "slow-git"
    slow_git.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    slow_git.chmod(0o755)
    monkeypatch.setattr(command_population, "_GIT_TIMEOUT_SECONDS", 1)

    with pytest.warns(RuntimeWarning, match="did not finish"):
        answer = _git_output(str(slow_git), sandbox, "rev-parse", "--show-toplevel")

    assert answer is None


def test_this_tree_answers_from_an_index_and_not_from_a_guess() -> None:
    """Whichever tree this is, the population came from an index -- not from names.

    Every other test here builds the tree it asks about. None of them says what
    answered for *this* one, and the sources do not agree: the name guess drops
    the repository-root ``.theurian/`` wholesale, which on the dogfood corpus
    branch is 78 scanned files of the 321 the gate reads. A machine without git
    on ``PATH``, or a checkout git refuses for ``safe.directory``, would run the
    whole documented-command suite against a different population and report
    nothing about it.

    **Two trees are supported and this must pass in both**, which the first
    version of this test got wrong and the mutation harness proved: it asserted
    on ``_git_listing`` alone, and the harness's own copy has no ``.git`` -- a
    valid manifest, no git, a failing assert, a RED control, and every verdict
    in the batch void. It is the assertion that was wrong, not the tree. So the
    question is whether *an index* answered, by either route.

    The skip is what is left when neither could: no git binary and no manifest,
    where the guess is the only thing available and this test has nothing to
    say. Visible in ``-ra``, unlike the silence it replaces.
    """
    from_git = _git_listing(REPO_ROOT)
    recorded = from_git if from_git is not None else _manifest_listing(REPO_ROOT)
    if recorded is None:
        pytest.skip(
            "neither git nor a recorded manifest can answer for this tree, so the "
            "name-based guess is all there is and there is nothing here to check"
        )

    assert "pyproject.toml" in recorded
    # Which source answered is one question; whether the gate used it is
    # another. `_population` is cached and picks among three, so a fallback
    # taken and memoised before this test ran would not show up above.
    assert set(_population(REPO_ROOT)) == {
        path for entry in recorded if (path := REPO_ROOT / entry).is_file()
    }


def test_both_branches_of_the_population_hand_over_the_same_order(
    sandbox: pathlib.Path,
) -> None:
    """One order, whichever branch answered, because the order is what is cached.

    :func:`_scan` is ``functools.cache``d and calls its determinism the reason
    it may be: same surfaces, same file order, same generators. That holds only
    if both branches sort by the same key, and they did not. Git hands back
    repository-relative strings and the fallback built :class:`~pathlib.Path`
    objects, which sort component-wise -- so ``docs-x/`` and ``docs/`` come back
    in opposite orders depending on whether a tree has a ``.git`` in it, because
    ``-`` sorts before ``/`` as a byte and after it as a path boundary.

    Nothing downstream is known to depend on that order today. It is pinned
    because a cached answer whose order depends on the environment is the kind
    of difference that gets discovered as a mutation-harness verdict nobody can
    reproduce.
    """
    git = _require_git()
    copy = sandbox.parent / "copy"
    for tree in (sandbox, copy):
        (tree / "docs").mkdir(parents=True)
        (tree / "docs" / "b.md").write_text("run `theurian init`\n", encoding="utf-8")
        (tree / "docs-x").mkdir()
        (tree / "docs-x" / "a.md").write_text("run `theurian init`\n", encoding="utf-8")
    _git(git, "init", "-q", str(sandbox))
    _git(git, "-C", str(sandbox), "add", "docs", "docs-x")

    from_git = _scanned_in(sandbox)
    from_fallback = [
        path.relative_to(copy).as_posix()
        for path in _files(copy, frozenset({".md"}), repository=copy)
    ]

    assert from_git == from_fallback == ["docs-x/a.md", "docs/b.md"]


def test_a_copy_of_the_tree_inside_another_checkout_takes_the_fallback(
    sandbox: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tree nested in an unrelated repository is answered for by that repository.

    One ``TMPDIR`` away from real: ``tools/mutate.py`` builds its copies outside
    this checkout, and nothing says the directory it builds them in is outside
    every checkout. Asked from inside such a copy, git answers for the outer
    repository's index, which holds none of these paths -- measured on a scratch
    repository, an empty listing and exit 0.

    What that costs is a *false RED*, not a silent pass: an empty population is
    caught in ``test_documented_commands`` by
    ``test_the_scan_reaches_every_arm_of_every_reader``, by
    ``test_no_recorded_exception_outlives_the_text_it_excuses``, and by the
    floor ``test_no_file_that_names_a_command_escapes_the_scan`` asserts on its
    input. The tree here is a legitimate no-git tree -- the harness's own copy
    -- and the toplevel check is what routes it to the fallback instead of
    failing it for a reason that has nothing to do with the tree.

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


def test_a_gitless_copy_scans_the_population_the_harness_recorded(
    sandbox: pathlib.Path,
) -> None:
    """The mutation harness must grade against the population its gate has.

    ``tools/mutate.py`` copies the checkout without ``.git`` and the copy keeps
    every untracked file the developer's tree carried, so nothing in it can be
    asked what ships. Until the corpus landed, the name-based guess was close
    enough; on the corpus-seeding commit ``e165e4e`` (``dogfood/dev7-corpus``,
    which no longer resolves on the remote) the tree held 81 files under
    ``.theurian/`` (measured 2026-08-20) -- 26 knowledge documents, 26 migrations,
    26 proposal evidence files and 3 ``.gitkeep`` placeholders, 78 of them with a
    suffix the scan reads -- and the guess refuses that directory wholesale.
    Corrected here to match ``command_population.py``'s own note on
    :func:`_walked`: an earlier
    "27 migrations, 27 proposals and one specification" phrasing counted a
    ``.gitkeep`` placeholder as a member of each directory it holds open, which
    reaches the same 81-file total by the wrong partition. The gate's scanned
    population there is 321 files, so a harness running on the guess reports
    verdicts about a suite 24% smaller than the one it stands in for.

    So the harness records ``git ls-files --cached -z`` into the copy and this
    reads it. Asserted in both directions on purpose: the tracked knowledge
    document is scanned *because* the manifest names it, and the untracked
    scratch file beside a manifested one is skipped *because* it does not --
    which is the half a "read everything the walk allows" implementation would
    still pass.
    """
    knowledge = sandbox / ".theurian" / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "corpus.md").write_text("run `theurian init`\n", encoding="utf-8")
    (knowledge / "local-only.md").write_text("quoting `theurian upgrade`\n", encoding="utf-8")
    (sandbox / "docs").mkdir()
    (sandbox / "docs" / "shipped.md").write_text("run `theurian init`\n", encoding="utf-8")
    (sandbox / "docs" / "scratch.md").write_text("quoting `theurian upgrade`\n", encoding="utf-8")
    (sandbox / ".mutate-population").write_bytes(
        b".theurian/knowledge/corpus.md\x00docs/shipped.md\x00"
    )

    scanned = _scanned_in(sandbox)

    assert scanned == [".theurian/knowledge/corpus.md", "docs/shipped.md"]


@pytest.mark.parametrize(
    ("label", "manifest"),
    [
        ("empty", b""),
        ("a lone terminator", b"\x00"),
        ("cut off mid-path", b"docs/first.md\x00docs/sec"),
        ("cut off after a whole path", b"docs/first.md"),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_a_manifest_that_is_not_whole_is_not_an_answer(
    sandbox: pathlib.Path, label: str, manifest: bytes
) -> None:
    """The reader must say "I do not know" rather than "nothing", twice over.

    An empty manifest read as an answer is a population of nothing, which is
    the one state that makes the documented-command suite pass by opening no
    files -- ``_population``'s own docstring says it refuses exactly that, and
    ``_entries`` returning ``()`` handed it back regardless. A truncated one is
    quieter and worse: a population silently missing whatever was still being
    written, indistinguishable from a smaller repository.

    Both are caught by the terminator. ``ls-files -z`` ends every entry with a
    NUL including the last, so anything that does not is not a whole listing.
    The writer will not produce either now -- it refuses, and renames into place
    -- but the reader cannot assume it was written by this version of the
    harness, or that the disk did not fill between the two.

    Asserted against the fallback's answer rather than against emptiness,
    because "returned None" is only interesting if the caller then goes and
    finds the files itself. Two files on disk and a manifest that names at most
    one of them, for the same reason: a truncated manifest whose missing entry
    is also missing from the tree produces the fallback's answer by accident,
    and a test that cannot tell those apart passes with the check deleted --
    this one did, until the fixture grew its second file.
    """
    (sandbox / "docs").mkdir(parents=True)
    (sandbox / "docs" / "first.md").write_text("run `theurian init`\n", encoding="utf-8")
    (sandbox / "docs" / "second.md").write_text("run `theurian init`\n", encoding="utf-8")
    (sandbox / ".mutate-population").write_bytes(manifest)

    scanned = _scanned_in(sandbox)

    assert scanned == ["docs/first.md", "docs/second.md"], label


def test_a_tree_with_neither_git_nor_a_manifest_still_refuses_a_projects_own_state(
    sandbox: pathlib.Path,
) -> None:
    """The last resort, and the one thing it can still get right.

    ``tools/mutate.py`` copies the checkout with ``shutil.copytree`` and its
    ``_COPY_IGNORE`` drops ``.git`` on purpose ("the copy is not a repository,
    and the suite has been run without one"), while copying everything else the
    developer's tree carried -- local-only knowledge and draft proposals alike.
    A copy the harness prepared now carries a manifest; a tree that reaches here
    has neither, and no way to tell a tracked file from an untracked one.

    All it can refuse is a directory, so it refuses the one where a project
    keeps its own state: ``.theurian/`` at the top of the tree, where both the
    private knowledge and the drafts land. A nested one is sample content and is
    read -- ``examples/sample-project/.theurian/config.yaml``, which the scan has
    always covered.

    **This is not "only what ships", and the name no longer says it is.** Every
    file in this fixture is untracked -- there is no git here to track anything
    -- and two of the three are read. An untracked scratch note under ``docs/``
    is read too, and one naming a dead command turns the gate RED: measured in a
    gitless copy of this repository, population 398 to 399. That residual is
    what the manifest removes, and it is the reason the manifest is preferred
    over this.
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
