"""``FixCommitCheck`` against a real repository, and the vector it spawns (ADR-0033).

The sibling of ``test_committed_migration_check_adapter.py``, and the same split:
behavioural cases driven against a throwaway ``git init`` under ``tmp_path``, plus
one captured-vector pin for the foreclosures a behavioural case cannot reach.

**Both of this adapter's arguments are untrusted, and they are untrusted
differently.** The sha is caller wire input (ADR-0033 decision 3: the caller names
a commit and Theurian checks it). The path is *author-controlled stored data* --
``ReviewThread.file_path`` is whatever the evidence file says, and
``.theurian/review/`` is source rather than derived state, so a clone can deliver
one this installation never fetched (threat model T-24). The second is the one
that was actually open, and the measurement is below.

**What each foreclosure is worth, measured on git 2.47.1, 2026-09-18**, because
three of them read alike in the source and only two change an answer:

* ``--literal-pathspecs`` -- **load-bearing**. Against a commit touching only
  ``docs/notes.md``, the stored paths ``:(exclude)src/retrying.py``,
  ``:!src/retrying.py``, ``:(glob)**/*.md`` and ``:(top)`` each make ``diff-tree``
  print ``docs/notes.md``: a non-empty answer, which is this adapter's
  ``VERIFIED``. Under the flag each prints nothing.
* ``--root`` -- **load-bearing**. The repository's first commit reports no files
  without it (measured: empty output where the flag gives ``src/retrying.py``), so
  a fix that *is* the root commit would read as touching nothing.
* ``--end-of-options`` before the sha -- **defence in depth, with no reachable
  difference under the shipped vector**, and this file says so rather than
  implying a behavioural pin it does not have. Production appends ``^{commit}``,
  so an option-shaped value arrives as the token ``--all^{commit}``, which git
  fails to resolve with or without the flag; nineteen option spellings were
  measured identical in both. What the flag buys is measurable only once the
  suffix is gone: *without* both, ``--all``, ``--branches`` and
  ``--glob=refs/heads/*`` each print ``HEAD``'s object id, and
  ``--local-env-vars`` and ``--show-toplevel`` print environment names and an
  absolute path. So the class is closed four times over -- the suffix, the flag,
  the non-zero-exit check and the hex re-match -- and it is
  :func:`test_the_git_vectors_are_fixed_and_foreclose_an_option_a_path_and_magic`
  that holds each of them, not the behavioural case beside it.
* ``^{commit}`` on the revision -- **load-bearing, and for more than the module
  says**. ``rev-parse --verify`` accepts a full-width hex string as an object
  *name* without asking whether the object is present, so
  ``eeee…eeee`` prints itself at exit 0 and only the dereference fails. The suffix
  is therefore what refuses a fabricated forty hex digits, which is the class
  ADR-0033 decision 3 names first;
  :func:`test_a_commit_this_repository_does_not_have_is_not_verified` is where it
  goes RED.

Nothing here touches the developer's machine: every ``git`` call names a
``tmp_path`` repository with ``cwd``, and the identity and signing settings are
passed with ``-c`` so an ambient ``~/.gitconfig`` neither reaches nor breaks them.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from theurian.domain.review import FixCommitVerdict
from theurian.infrastructure.git.fix_commit_check import GIT_TIMEOUT_SECONDS, FixCommitCheck

pytestmark = pytest.mark.integration

#: The file a stored thread is anchored to, and the one the verification asks about.
FILE_PATH = "src/retrying.py"

#: A path in the same repository the thread is *not* anchored to. A commit that
#: touches this and nothing else is what every refusing case below is driven at.
OTHER_PATH = "docs/notes.md"

#: An identity and a signing setting on every call, so these commits never depend
#: on -- nor are broken by -- the developer's ambient ``~/.gitconfig``; a global
#: ``commit.gpgsign = true`` would otherwise fail the commit.
_GIT_IDENTITY = (
    "-c",
    "user.email=test@example.com",
    "-c",
    "user.name=Test",
    "-c",
    "commit.gpgsign=false",
)

#: Stored ``filePath`` values that are pathspec *expressions* rather than paths.
#:
#: Author-controlled and reachable through a clone (T-24). Each was measured to
#: verify a commit that touched only :data:`OTHER_PATH` when ``--literal-pathspecs``
#: is absent. ``:(top)`` is the worst member and is why the set is not just the two
#: exclusion spellings: it names **no path at all**, so under pathspec magic every
#: commit in the repository verifies every thread, and the ``fix_commit_present``
#: signal stops being about this thread's file in any sense.
MAGIC_STORED_PATHS: tuple[str, ...] = (
    f":(exclude){FILE_PATH}",
    f":!{FILE_PATH}",
    ":(glob)**/*.md",
    ":(top)",
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", *_GIT_IDENTITY, *args],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


def _commit(repo: Path, relative: str, body: str, message: str) -> str:
    """Write ``relative``, commit it, and answer the commit's own sha."""
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    _git(repo, "add", relative)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[Path]:
    """A throwaway repository whose **root** commit touches :data:`FILE_PATH`.

    The root commit carrying the anchored path is deliberate: it is the fixture
    :func:`test_a_repositorys_first_commit_can_be_the_fix_it_verifies` needs, and
    building it here rather than in that test keeps every other case driving
    against the same history.
    """
    repo = tmp_path / "work"
    repo.mkdir()
    _git(repo, "-c", "init.defaultBranch=main", "init", "-q")
    _commit(repo, FILE_PATH, "def retry():\n    pass\n", "root: add the retry helper")
    yield repo


# ---------------------------------------------------------------------------
# The three verdicts, as git produces them.
# ---------------------------------------------------------------------------


def test_a_commit_this_repository_does_not_have_is_not_verified(repository: Path) -> None:
    """A commit the caller names and the repository does not have satisfies nothing.

    The class the check closes, in ADR-0033's own words: "the whole class of
    values that touch *nothing* -- a random forty hex digits, a commit from
    another repository, the fabricated ``"e" * 40`` the suite's own fixtures
    use".

    **The ``^{commit}`` suffix is the whole of what closes it, and that is bigger
    than the reason the module states.** Measured on git 2.47.1, 2026-09-18, in a
    repository holding no such object::

        $ git rev-parse --verify --quiet --end-of-options eeee…eeee
        eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee        # rc=0
        $ git rev-parse --verify --quiet --end-of-options 'eeee…eeee^{commit}'
                                                        # rc=1, nothing printed

    ``--verify`` accepts a full-width hex string as a well-formed *object name*
    without asking whether the object exists; only dereferencing it to a commit
    fails. So a fabricated forty hex digits resolves, and the id it "resolves" to
    is itself -- which then reaches ``diff-tree`` as a revision naming nothing.
    Removing the suffix turns this test RED, which is what says the sentence
    above is held rather than believed.
    """
    verdict = FixCommitCheck(repository).verify("e" * 40, FILE_PATH)

    assert verdict is FixCommitVerdict.NO_SUCH_COMMIT, (
        "a fabricated forty hex digits verified as a commit. `rev-parse --verify` "
        "accepts a full-width hex string as an object name without checking that "
        "the object is there, so the `^{commit}` suffix is what refuses it -- "
        "restore it before trusting any other case in this file."
    )


def test_a_commit_that_touches_another_file_is_not_verified(repository: Path) -> None:
    """The arm that separates a verification from an existence check.

    ADR-0033's Compliance section names this case as the one without which "an
    implementation that only ran ``cat-file -e`` would pass". The commit is real,
    reachable and in this repository; it simply has nothing to do with the file
    the stored thread is anchored to.

    It is also the negative control for
    :func:`test_a_stored_path_spelling_a_pathspec_expression_verifies_nothing`: an
    ordinary path over this same commit answers nothing **with or without**
    ``--literal-pathspecs`` (measured), so that test's refusals are the flag's
    doing and not the flag breaking every lookup.
    """
    unrelated = _commit(repository, OTHER_PATH, "notes\n", "add notes")

    verdict = FixCommitCheck(repository).verify(unrelated, FILE_PATH)

    assert verdict is FixCommitVerdict.TOUCHES_NOTHING_HERE, (
        f"git answered {verdict!r} for a commit that touched {OTHER_PATH} while the "
        f"thread is anchored to {FILE_PATH}. A check that stops at existence makes "
        f"`fix_commit_present` satisfiable by any commit in the repository -- 285 of "
        f"them at ADR-0033's measurement -- which is very nearly no check at all."
    )


def test_a_commit_that_touches_the_threads_file_is_verified(repository: Path) -> None:
    """The positive control: the check can say yes, and says it for the right commit.

    Without it, every refusal here is satisfied by an adapter that verifies
    nothing. What passing establishes is narrower than "this is the thread's fix"
    -- the choice set is the file's whole history, measured at 15, 25 and 71
    commits on three plausible paths -- and ADR-0033 assigns the narrowing to
    FR-V4's human rather than to a machine.
    """
    touching = _commit(repository, FILE_PATH, "def retry():\n    return None\n", "fix the deadlock")

    verdict = FixCommitCheck(repository).verify(touching, FILE_PATH)

    assert verdict is FixCommitVerdict.VERIFIED


def test_a_repositorys_first_commit_can_be_the_fix_it_verifies(repository: Path) -> None:
    """``--root``: a commit with no parent still has the files it introduced.

    ``diff-tree`` diffs a commit against its parents, and a root commit has none
    -- so without ``--root`` it reports no files at all and the adapter answers
    ``TOUCHES_NOTHING_HERE`` for a commit that plainly created the anchored file.
    Measured on git 2.47.1: empty output without the flag, ``src/retrying.py``
    with it.

    Not a corner case worth skipping. A thread anchored to a file introduced by
    the first commit of a repository is ordinary in a young project, and the
    failure it produces is the worst kind -- a true fix, correctly named, refused
    with the message that says *go and find the right commit*, which there is no
    way for the caller to satisfy.
    """
    root = _git(repository, "rev-list", "--max-parents=0", "HEAD").stdout.strip()

    verdict = FixCommitCheck(repository).verify(root, FILE_PATH)

    assert verdict is FixCommitVerdict.VERIFIED, (
        f"the repository's root commit answered {verdict!r} for {FILE_PATH}, which it "
        f"introduced. `--root` has been dropped from the diff-tree vector, and every "
        f"thread anchored to a file the first commit created is now unverifiable."
    )


# ---------------------------------------------------------------------------
# The stored path is author-controlled, and a pathspec is not a path (T-24).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stored_path", MAGIC_STORED_PATHS, ids=MAGIC_STORED_PATHS)
def test_a_stored_path_spelling_a_pathspec_expression_verifies_nothing(
    repository: Path, stored_path: str
) -> None:
    """A ``filePath`` that is a pathspec expression must not verify a foreign commit.

    ``--`` stops an option-shaped path being read as a flag; it does **not** stop
    a path being read as a pathspec *expression*, and that is a separate language
    git parses after the separator. So a stored ``filePath`` of
    ``:(exclude)src/retrying.py`` asks git for "everything the commit touched
    except the file this thread is about", which for a commit touching anything
    else is a non-empty answer -- and a non-empty answer is this adapter's
    ``VERIFIED``. Measured open on git 2.47.1 before ``--literal-pathspecs``
    landed: all four spellings printed ``docs/notes.md`` for a commit that touched
    only ``docs/notes.md``.

    The reach is the whole point. ``file_path`` is not caller input on this call
    -- it is read out of the stored evidence record, and ``.theurian/review/`` is
    source rather than derived state, so a clone can deliver a record this
    installation never fetched (T-24). The residual ADR-0033 records for that
    directory is a *fabricated* ``fix_commit``; a fabricated ``filePath`` that
    turns the verification into "did this commit touch anything at all" is a
    second and wider one, and it is closed here rather than recorded.

    ``:(top)`` is the worst member and is why this ranges over four spellings: it
    names no path, so under pathspec magic **every** commit in the repository
    verifies **every** thread.
    """
    unrelated = _commit(repository, OTHER_PATH, "notes\n", "add notes")

    verdict = FixCommitCheck(repository).verify(unrelated, stored_path)

    assert verdict is FixCommitVerdict.TOUCHES_NOTHING_HERE, (
        f"the stored path {stored_path!r} verified a commit that touched only "
        f"{OTHER_PATH}. `--literal-pathspecs` has been dropped from the diff-tree "
        f"vector, and a review evidence file can now name a pathspec that makes "
        f"`fix_commit_present` true for a commit with nothing to do with the thread."
    )


# ---------------------------------------------------------------------------
# The caller's sha is wire input (decision 3).
# ---------------------------------------------------------------------------


def test_an_option_shaped_fix_commit_is_refused_and_starts_nothing(repository: Path) -> None:
    """An option-shaped ``fixCommit`` is a revision that resolves to nothing.

    **What this pins is the composed outcome, and not any one foreclosure.** The
    module docstring records the measurement: under the shipped vector this class
    is closed four times over -- the ``^{commit}`` suffix, ``--end-of-options``,
    the non-zero-exit check and the hex re-match -- and deleting ``--end-of-options``
    alone changes no answer, so a green result here is *not* evidence that the
    flag is present. :func:`test_the_git_vectors_are_fixed_and_foreclose_an_option_a_path_and_magic`
    is what holds it.

    What this case is still worth: it drives the shape a wire caller would
    actually send, asserts the refusal is the ordinary one rather than a crash or
    a traceback out of the adapter, and asserts the repository is unchanged --
    ``--upload-pack`` is the option git hands a command line to, and a rewrite that
    moved this sha to a subcommand honouring it would leave a file behind here.
    """
    before = sorted(path.name for path in repository.iterdir())

    verdict = FixCommitCheck(repository).verify("--upload-pack=touch pwned", FILE_PATH)

    assert verdict is FixCommitVerdict.NO_SUCH_COMMIT
    assert sorted(path.name for path in repository.iterdir()) == before, (
        "an option-shaped fixCommit changed the repository's contents, so something "
        "in the vector honoured it as an option rather than failing to resolve it "
        "as a revision"
    )


#: Answers ``rev-parse`` can print that are not a single object id.
#:
#: Each is the shape one foreclosure *upstream* of the hex re-match exists to
#: stop, driven here as if that foreclosure had failed -- which is what makes the
#: re-match a layer rather than a line nothing reaches. Membership is stated per
#: member, because a parametrisation whose rows share one killer is one case wearing
#: four names:
#:
#: * the **option-shaped** answer is the one that matters, and it is precisely what
#:   ``rev-parse`` prints once ``--end-of-options`` and the ``^{commit}`` suffix are
#:   both gone (measured: ``--local-env-vars`` prints environment names,
#:   ``--show-toplevel`` an absolute path, ``--all`` an id per ref). Forwarding it
#:   would put a caller-influenced option back into the ``diff-tree`` vector;
#: * a **ref name** is what an ``--abbrev-ref``-shaped answer looks like: harmless
#:   as a string, and not an object id;
#: * **empty** is the arm a truthiness shortcut still handles, and it is here to be
#:   the row that a ``return printed if … else None`` -> ``return printed`` mutation
#:   catches while the shortcut ``return printed or None`` does not -- the two
#:   weakenings are different, and only this row tells them apart;
#: * **two ids** is the ``--all`` answer's own shape, and the row that separates
#:   ``fullmatch`` from ``search``: a scan looking for a hex run anywhere in the
#:   output accepts it and spends the first id.
NON_OBJECT_ID_ANSWERS: tuple[str, ...] = (
    "--upload-pack=touch pwned",
    "refs/heads/main",
    "",
    f"{'a' * 40}\n{'b' * 40}",
)


@pytest.mark.parametrize(
    "printed", NON_OBJECT_ID_ANSWERS, ids=["option", "ref", "empty", "two ids"]
)
def test_a_rev_parse_answer_that_is_not_one_object_id_reaches_no_second_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, printed: str
) -> None:
    """The hex re-match is a layer, and this is what reaches it.

    ``_resolved_commit`` re-checks git's own output against ``[0-9a-f]{40,64}``
    before spending it, and that check is what lets the module state that
    ``diff-tree``'s revision "cannot be reached with an argument of any other
    shape". Nothing upstream reaches it today -- the suffix and the exit-status
    check answer first -- so without this test the re-match could be deleted and
    the whole suite would stay green (measured: it survived every other case in
    this file).

    Driven by cannning ``rev-parse``'s answer rather than by finding a git that
    prints one, because the point is what the *adapter* does with an answer it did
    not expect, not what git can be made to print. A verdict alone would be a weak
    assertion -- a build that refused everything passes it -- so the count of
    spawns is what is held: an unrecognised answer must end the call, not become
    the next vector's revision.
    """
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args, returncode=0, stdout=f"{printed}\n".encode(), stderr=b""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    verdict = FixCommitCheck(tmp_path).verify("a" * 40, FILE_PATH)

    assert verdict is FixCommitVerdict.NO_SUCH_COMMIT
    assert len(calls) == 1, (
        f"rev-parse answered {printed!r}, which is not one object id, and the adapter "
        f"spawned {len(calls)} calls: {calls!r}. The unrecognised answer became the "
        f"diff-tree vector's revision, which is the one thing that call's argument "
        f"is supposed to be unable to be."
    )


# ---------------------------------------------------------------------------
# The vector itself, which is where the foreclosures live.
# ---------------------------------------------------------------------------


def test_the_git_vectors_are_fixed_and_foreclose_an_option_a_path_and_magic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both spawned vectors, captured, at the worst input on each axis.

    ``PROCESS_SPAWN_SITES`` records this module, and the checklist
    ``test_network_call_sites.py`` states binds it: the vector is fixed by the
    adapter rather than taken from a document, a config, a URL or a remote, and
    each call carries a timeout. This is the pin the module's three foreclosures
    actually rest on -- a behavioural case reaches only two of them (the module
    docstring's measurement), so a refactor that dropped ``--end-of-options``
    would go RED here and nowhere else.

    One fixture, chosen to be the worst member on **both** axes at once: the sha
    is option-shaped and the stored path is a pathspec expression, so the same
    capture shows the caller's value landing behind ``--end-of-options`` and the
    record's value landing behind ``--`` under ``--literal-pathspecs``.

    The revision handed to ``diff-tree`` is asserted to be the id ``rev-parse``
    **printed**, not the caller's string -- the canned id differs from both, so an
    adapter that forwarded the caller's value would be visible here even though
    every behavioural case above would stay green.

    RED if a timeout is dropped, if either call reaches a shell, if the binary
    stops being an absolute path, if ``--end-of-options`` / ``--`` /
    ``--literal-pathspecs`` / ``--root`` leaves either vector, if the ``^{commit}``
    suffix leaves the revision, or if the caller's string reaches ``diff-tree``.
    """
    calls: list[dict[str, Any]] = []
    printed_id = "b" * 40
    caller_sha = "--upload-pack=touch pwned"
    stored_path = f":(exclude){FILE_PATH}"

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append({"args": args, "kwargs": kwargs})
        return subprocess.CompletedProcess(
            args, returncode=0, stdout=f"{printed_id}\n".encode(), stderr=b""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    FixCommitCheck(tmp_path).verify(caller_sha, stored_path)

    assert len(calls) == 2, f"expected a rev-parse then a diff-tree, got {calls!r}"
    for call in calls:
        binary = call["args"][0]
        assert Path(binary).is_absolute(), f"the git binary is not an absolute path: {binary!r}"
        assert Path(binary).name in {"git", "git.exe"}, call["args"]
        assert call["kwargs"].get("timeout") == GIT_TIMEOUT_SECONDS, (
            f"each git call must carry a timeout of {GIT_TIMEOUT_SECONDS}s (SEC-19); "
            f"got {call['kwargs'].get('timeout')!r}"
        )
        assert call["kwargs"].get("shell", False) is False, "no git call may reach a shell"

    rev_parse, diff_tree = calls[0]["args"], calls[1]["args"]

    assert rev_parse[1:] == [
        "rev-parse",
        "--verify",
        "--quiet",
        "--end-of-options",
        f"{caller_sha}^{{commit}}",
    ], rev_parse
    assert rev_parse[-2] == "--end-of-options", (
        "the caller's sha must be the token immediately after `--end-of-options`, or "
        "an option-shaped fixCommit is an option git honours rather than a revision "
        "it fails to resolve"
    )
    assert rev_parse[-1].endswith("^{commit}"), (
        "the revision must carry the `^{commit}` suffix. Measured: without it, "
        "`rev-parse --verify` accepts any full-width hex string as an object name "
        "and prints it back at exit 0, so a fabricated forty hex digits verifies -- "
        "which is the exact class ADR-0033 decision 3 says this check closes"
    )

    assert diff_tree[1:] == [
        "--literal-pathspecs",
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        "--root",
        "--end-of-options",
        printed_id,
        "--",
        stored_path,
    ], diff_tree
    assert diff_tree[1] == "--literal-pathspecs", (
        "`--literal-pathspecs` is a root-level option and must precede the `diff-tree` "
        "subcommand; after it, git does not accept it at all and the stored path is a "
        "pathspec expression again"
    )
    assert "--root" in diff_tree, (
        "`--root` must stay in the diff-tree vector, or a fix that is the repository's "
        "first commit reports no files and a true fix is refused"
    )
    assert diff_tree[-2:] == ["--", stored_path], (
        "the stored path must be the last token and must sit behind `--`, so an "
        "option-shaped `filePath` out of an evidence file is a pathspec rather than a flag"
    )
    assert diff_tree[-3] == printed_id and caller_sha not in diff_tree, (
        f"diff-tree's revision is {diff_tree[-3]!r}; it must be the object id rev-parse "
        f"printed ({printed_id!r}) and never the caller's string ({caller_sha!r}), which "
        f"is what lets this call state that its revision cannot be option-shaped"
    )
