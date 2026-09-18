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

**One process, for every input, and that is a disclosure control rather than a
saving.** The adapter used to ask two questions in two spawns -- *does this
object resolve to a commit* (``rev-parse``), then *did it touch this path*
(``diff-tree``) -- and the first could answer no on its own. So a refusal about
an absent object cost one process and a refusal about a real commit cost two,
and the C4b battery measured the difference end to end at **+7.2 ms, P=1.000**:
the refusal's *duration* answered "does this object exist here", which is a fact
about the repository the caller was not granted. ADR-0033 decision 5 binds the
pair in text **and in duration**, so the two questions collapse into one
``diff-tree`` call whose outcome is read off the exit code and the output.
:func:`test_the_same_request_spawns_the_same_vector_whether_the_object_is_here_or_not`
is the pin, and it is the one that was RED when this shape was chosen.

**What each foreclosure is worth, measured on git 2.47.1, 2026-09-19** -- all
four re-measured under the single-call shape, because two of them had been
justified by a ``rev-parse`` behaviour that no longer runs:

* ``--literal-pathspecs`` -- **load-bearing**. Against a commit touching only
  ``docs/notes.md``, the stored paths ``:(exclude)src/retrying.py``,
  ``:!src/retrying.py``, ``:(glob)**/*.md`` and ``:(top)`` each make ``diff-tree``
  print ``docs/notes.md``: a non-empty answer, which is this adapter's
  ``VERIFIED``. Under the flag each prints nothing.
* ``--root`` -- **load-bearing**. The repository's first commit reports no files
  without it (measured: empty output where the flag gives ``src/retrying.py``), so
  a fix that *is* the root commit would read as touching nothing.
* ``^{commit}`` on the revision -- **load-bearing, and for a different reason
  than it used to be**. Under the two-call shape it was what refused a fabricated
  forty hex digits, because ``rev-parse --verify`` accepts a full-width hex string
  as an object *name* without asking whether the object is present. ``diff-tree``
  does not: ``eeee…eeee`` exits 128 with or without the suffix, so that
  justification did not survive the collapse and is recorded here as history
  rather than repeated as a live claim. What the suffix holds *now* is the
  **commit-only** semantics -- a tree id and a blob id both exit 0 with empty
  output without it, which this adapter would read as ``TOUCHES_NOTHING_HERE``,
  and exit 128 with it.
  :func:`test_an_object_that_is_not_a_commit_names_no_commit` is where that goes
  RED.
* ``--end-of-options`` before the sha -- **defence in depth, with no reachable
  verdict difference**, and this file says so rather than implying a behavioural
  pin it does not have. An option-shaped sha exits 128 behind the flag and 129
  (git's usage error) without it; both are non-zero, so both are the same
  verdict, and no file was created in either. The flag is held by
  :func:`test_the_git_vector_is_one_process_fixed_and_forecloses_an_option_a_path_and_magic`
  and by nothing else.

Nothing here touches the developer's machine: every ``git`` call names a
``tmp_path`` repository with ``cwd``, and the identity and signing settings are
passed with ``-c`` so an ambient ``~/.gitconfig`` neither reaches nor breaks them.
"""

from __future__ import annotations

import ast
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from theurian.domain.review import FixCommitVerdict
from theurian.infrastructure.git import fix_commit_check
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

    **What refuses it is the single ``diff-tree`` call itself**, which exits 128
    on an object the repository does not have (measured, git 2.47.1). That is a
    change of mechanism worth recording rather than quietly inheriting: under the
    two-call shape the refusal came from the ``^{commit}`` dereference, because
    ``rev-parse --verify`` accepted a full-width hex string as a well-formed
    object *name* without asking whether the object existed and printed it back
    at exit 0. ``diff-tree`` asks. The suffix still earns its place on this
    vector, and :func:`test_an_object_that_is_not_a_commit_names_no_commit` is
    now where that is held.
    """
    verdict = FixCommitCheck(repository).verify("e" * 40, FILE_PATH)

    assert verdict is FixCommitVerdict.NO_SUCH_COMMIT, (
        "a fabricated forty hex digits verified as a commit, so the caller's word "
        "is being taken for the one signal ADR-0033 decision 3 says has to be "
        "found in the repository rather than asserted."
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


@pytest.mark.parametrize("kind", ["tree", "blob"])
def test_an_object_that_is_not_a_commit_names_no_commit(repository: Path, kind: str) -> None:
    """``^{commit}``'s job on this vector: an object that is not a commit is not one.

    A tree id and a blob id are real objects this repository has, and neither is
    a commit. Without the suffix ``diff-tree`` takes them and exits **0 with no
    output**, which this adapter reads as ``TOUCHES_NOTHING_HERE`` -- *the commit
    is here, it just did not touch your file*. That is the wrong answer about the
    wrong thing, and it is the only place the suffix now changes one: measured on
    git 2.47.1, both exit 128 with it and 0 without.

    This case exists because the suffix's old justification did not survive the
    collapse to one call. It used to be held by the fabricated-sha case, where
    ``rev-parse`` accepted any full-width hex string; ``diff-tree`` rejects an
    absent object itself, so that case would now stay green with the suffix gone.
    Deleting the suffix has to be RED somewhere or it is decoration, and this is
    where.
    """
    revision = "HEAD^{tree}" if kind == "tree" else f"HEAD:{FILE_PATH}"
    object_id = _git(repository, "rev-parse", revision).stdout.strip()

    verdict = FixCommitCheck(repository).verify(object_id, FILE_PATH)

    assert verdict is FixCommitVerdict.NO_SUCH_COMMIT, (
        f"the {kind} id {object_id[:12]}… answered {verdict!r}. A {kind} is not a "
        f"commit, so the honest answer is that the caller named no commit -- and "
        f"`TOUCHES_NOTHING_HERE` instead says a commit was found, which is the "
        f"reading `^{{commit}}` exists to prevent."
    )


# ---------------------------------------------------------------------------
# One process, and the vector it carries (ADR-0033 decisions 3 and 5).
# ---------------------------------------------------------------------------


def test_the_module_reaches_a_spawn_from_exactly_one_place() -> None:
    """The source half of the one-process property, for the inputs nobody drives.

    The runtime pins below count spawns for the inputs they send. This counts the
    places a spawn can be reached from **at all**, which is the half that covers
    a branch no case in this file happens to take -- a second question added for
    a shape somebody thought was special is exactly how the two-call version got
    here, and it was reachable only for inputs that resolved.

    ``test_network_call_sites.py`` records this module as a permitted spawn site
    and says in as many words that it "records the module, not the call count".
    This is that count.

    **What is counted is an *initiation*, not an occurrence of a name**, and the
    distinction is the whole design of the key. The module spawns through a
    ``_run`` wrapper, so the ``subprocess.run`` inside that wrapper is its single
    implementation and must not be tallied beside the calls *to* it -- counting
    both read three today and would have read two after the collapse, so the pin
    would have been red either way and told nobody anything. The helper's body is
    therefore excluded, and what remains is counted in both spellings: a call to
    ``_run``, and a direct ``subprocess.run`` for a factoring that has no wrapper
    at all. One, in every shape.

    Its bound, stated: a spawn reached through ``getattr`` or a callable handed in
    from elsewhere is invisible here, the same floor every call-site scan in this
    repository records.
    """
    tree = ast.parse(
        Path(fix_commit_check.__file__).read_text(encoding="utf-8"),
        filename="fix_commit_check.py",
    )
    helper = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == "_run"
        ),
        None,
    )
    implementation = set() if helper is None else {id(node) for node in ast.walk(helper)}
    spawns = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and id(node) not in implementation
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"_run", "run"}
        and isinstance(node.func.value, ast.Name)
    ]

    assert len(spawns) == 1, (
        f"the adapter reaches a git spawn from {len(spawns)} places, at lines "
        f"{[node.lineno for node in spawns]}. One verification is one process: a "
        f"second site is a question that can be asked or skipped, and whether it was "
        f"asked is then readable from how long the refusal took (ADR-0033 decision 5, "
        f"measured at +7.2 ms). If the spawn helper was renamed rather than doubled, "
        f"this pin's key is what needs moving -- say which in the change."
    )


def _recorded_spawns(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every ``subprocess.run`` this process makes, with git still really running.

    A recorder around the **real** call rather than a stand-in: the verdicts these
    pins compare have to be git's own, or a spawn-count assertion would be about
    a fake's control flow. :func:`subprocess.run` is captured before it is
    replaced, and ``monkeypatch`` puts it back.
    """
    calls: list[dict[str, Any]] = []
    real = subprocess.run

    def recording(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append({"args": list(args), "kwargs": kwargs})
        return real(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", recording)
    return calls


def test_the_git_vector_is_one_process_fixed_and_forecloses_an_option_a_path_and_magic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole spawned vector, captured, at the worst input on each axis at once.

    ``PROCESS_SPAWN_SITES`` records this module, and the checklist
    ``test_network_call_sites.py`` states binds it: the vector is fixed by the
    adapter rather than taken from a document, a config, a URL or a remote, and
    the call carries a timeout.

    **One fixture, worst member on both axes**: the sha is option-shaped and the
    stored path is a pathspec expression, so a single capture shows the caller's
    value landing behind ``--end-of-options`` and the record's value landing
    behind ``--`` under ``--literal-pathspecs``.

    **``--end-of-options`` is held here and nowhere else**, which is why an exact
    vector equality is worth its brittleness: an option-shaped sha exits non-zero
    with or without the flag, so no behavioural case can tell. The module
    docstring records that measurement rather than leaving this pin to imply a
    coverage it has.

    RED if the call count moves off one, if a timeout is dropped, if the call
    reaches a shell, if the binary stops being an absolute path, if
    ``--end-of-options`` / ``--`` / ``--literal-pathspecs`` / ``--root`` leaves
    the vector, or if the ``^{commit}`` suffix leaves the revision.
    """
    caller_sha = "--upload-pack=touch pwned"
    stored_path = f":(exclude){FILE_PATH}"
    calls = _recorded_spawns(monkeypatch)

    FixCommitCheck(tmp_path).verify(caller_sha, stored_path)

    assert len(calls) == 1, (
        f"the verification spawned {len(calls)} processes: "
        f"{[call['args'][1:] for call in calls]}. It asks git one question, because a "
        f"second question that can be skipped is a duration the caller can time "
        f"(ADR-0033 decision 5)."
    )
    call = calls[0]
    binary = call["args"][0]

    assert Path(binary).is_absolute(), f"the git binary is not an absolute path: {binary!r}"
    assert Path(binary).name in {"git", "git.exe"}, call["args"]
    assert call["kwargs"].get("timeout") == GIT_TIMEOUT_SECONDS, (
        f"the git call must carry a timeout of {GIT_TIMEOUT_SECONDS}s (SEC-19); "
        f"got {call['kwargs'].get('timeout')!r}"
    )
    assert call["kwargs"].get("shell", False) is False, "the git call may not reach a shell"

    vector = call["args"]
    assert vector[1:] == [
        "--literal-pathspecs",
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        "--root",
        "--end-of-options",
        f"{caller_sha}^{{commit}}",
        "--",
        stored_path,
    ], vector
    assert vector[1] == "--literal-pathspecs", (
        "`--literal-pathspecs` is a root-level option and must precede the `diff-tree` "
        "subcommand; after it git does not accept it at all, and the stored path is a "
        "pathspec expression again"
    )
    assert "--root" in vector, (
        "`--root` must stay in the vector, or a fix that is the repository's first "
        "commit reports no files and a true fix is refused"
    )
    assert vector[-4] == "--end-of-options" and vector[-3].endswith("^{commit}"), (
        "the caller's sha must be the token immediately after `--end-of-options` and "
        "must carry the `^{commit}` suffix. This is the one place the sha is spent now, "
        "so both foreclosures are on this token or they are nowhere"
    )
    assert vector[-2:] == ["--", stored_path], (
        "the stored path must be the last token and must sit behind `--`, so an "
        "option-shaped `filePath` out of an evidence file is a pathspec rather than a flag"
    )


def test_both_failure_verdicts_spawn_one_process_with_the_same_vector_shape(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The attacker's own move: vary the sha, time the answers, learn nothing.

    Two refusals about the same thread, driven the way a caller would drive them
    -- one sha the repository has and one it does not -- and the only thing that
    may differ between the two vectors is the token carrying the sha the caller
    chose. Same length, same positions, one process each.

    This is the same-repository half. Its sibling
    :func:`test_the_same_request_spawns_the_same_vector_whether_the_object_is_here_or_not`
    holds the stronger form, where even that token is equal. Both are needed: this
    one is the shape a caller can actually walk, and the other is the shape that
    makes the equality byte-exact.
    """
    unrelated = _commit(repository, OTHER_PATH, "notes\n", "add notes")
    check = FixCommitCheck(repository)
    calls = _recorded_spawns(monkeypatch)

    absent_verdict = check.verify("e" * 40, FILE_PATH)
    spawned_for_absent = len(calls)
    untouching_verdict = check.verify(unrelated, FILE_PATH)

    assert (absent_verdict, untouching_verdict) == (
        FixCommitVerdict.NO_SUCH_COMMIT,
        FixCommitVerdict.TOUCHES_NOTHING_HERE,
    ), (
        f"the two arms produced {absent_verdict!r} and {untouching_verdict!r}; this pin "
        f"is about two *different* refusals costing the same, so it says nothing until "
        f"both are reached"
    )
    assert (spawned_for_absent, len(calls) - spawned_for_absent) == (1, 1), (
        f"the absent-object refusal spawned {spawned_for_absent} process(es) and the "
        f"untouching-commit refusal {len(calls) - spawned_for_absent}. A refusal that "
        f"costs a different number of processes tells the caller which of the two it "
        f"was, whatever the message says -- measured at +7.2 ms, P=1.000, before the "
        f"two calls became one."
    )

    absent, untouching = calls[0]["args"], calls[1]["args"]
    # `strict=False`: a length mismatch is a failure this test reports itself, in
    # the assertion below with both vectors printed, rather than a ValueError out
    # of the comparison that says neither what differed nor by how much.
    differing = [
        index for index, (a, b) in enumerate(zip(absent, untouching, strict=False)) if a != b
    ]

    assert len(absent) == len(untouching) and differing == [len(absent) - 3], (
        f"the two vectors differ at positions {differing} (lengths "
        f"{len(absent)} and {len(untouching)}):\n  {absent[1:]}\n  {untouching[1:]}\n\n"
        f"Only the revision token may differ, because only the sha differs between the "
        f"two calls. Anything else is a branch the caller's input took."
    )


def test_the_same_request_spawns_the_same_vector_whether_the_object_is_here_or_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0033 decision 5's duration half, as the strongest form it has.

    The same caller input -- same sha, same path -- against two repositories that
    differ only in whether they hold the object. One answers
    ``TOUCHES_NOTHING_HERE`` and the other ``NO_SUCH_COMMIT``, and the work each
    does must be **byte-identical**, because the caller chose nothing that
    differs between them: what differs is a fact about the repository, which is
    exactly what decision 5 says a refusal may not disclose.

    Stronger than its same-repository sibling in the way that matters. There, the
    sha differs and the vectors can only be asserted equal *except* at that token;
    here nothing the caller sent differs, so the assertion is an equality over the
    whole argv, and the one-process count is what makes it an equality over the
    work rather than over a string.

    The verdicts are asserted different first. Without that this passes against a
    build that answers ``NO_SUCH_COMMIT`` for everything, which is the cheapest
    way to make two refusals indistinguishable and the least useful.
    """
    here, elsewhere = tmp_path / "here", tmp_path / "elsewhere"
    for repo in (here, elsewhere):
        repo.mkdir()
        _git(repo, "-c", "init.defaultBranch=main", "init", "-q")
        _commit(repo, FILE_PATH, "def retry():\n    pass\n", "root: add the retry helper")
    sha = _commit(here, OTHER_PATH, "notes\n", "add notes")

    calls = _recorded_spawns(monkeypatch)
    found = FixCommitCheck(here).verify(sha, FILE_PATH)
    spawned_when_found = len(calls)
    missing = FixCommitCheck(elsewhere).verify(sha, FILE_PATH)

    assert (found, missing) == (
        FixCommitVerdict.TOUCHES_NOTHING_HERE,
        FixCommitVerdict.NO_SUCH_COMMIT,
    ), (
        f"the same request answered {found!r} here and {missing!r} elsewhere; the two "
        f"repositories are supposed to disagree about this object, and until they do "
        f"the equality below is over one case twice"
    )
    assert (spawned_when_found, len(calls) - spawned_when_found) == (1, 1), (
        f"the same request spawned {spawned_when_found} process(es) against the "
        f"repository that has the object and {len(calls) - spawned_when_found} against "
        f"the one that does not. The count is the channel: a caller who cannot read the "
        f"refusal can still count the work, and ADR-0033 decision 5 binds the duration "
        f"as well as the text."
    )
    assert calls[0]["args"] == calls[1]["args"], (
        f"the same request produced two different argument vectors:\n"
        f"  has the object:  {calls[0]['args'][1:]}\n"
        f"  lacks it:        {calls[1]['args'][1:]}\n\n"
        f"Nothing the caller sent differs between these two calls, so a difference here "
        f"is the adapter branching on what the repository contains -- which is the fact "
        f"decision 5 refuses to leak through the shape of the work."
    )
