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

**``fixCommit`` is a git revision *expression* unless the adapter stops it being
one.** The entry funnel is what stops it: a value that is not a full-length
lower-case object name is answered ``NO_SUCH_COMMIT`` before any process exists.
Without it ``HEAD^{/planted}``, ``:/planted|zzzz``, ``main``, ``HEAD~2..HEAD``,
``refs/heads/main`` and ``HEAD@{0}`` each answered ``VERIFIED`` against a
repository whose HEAD touched the thread's file (measured, git 2.47.1,
2026-09-19), so a caller who knew no sha could satisfy ``fix_commit_present`` by
describing the commit it wanted. ``fix_commit_grammar`` holds the corpus and the
reasoning; :func:`test_a_fix_commit_that_is_not_a_full_object_name_is_refused_without_spawning`
drives it here and
``tests/unit/test_candidate_input_schema.py`` drives the same corpus at the
wire.

**One process, for every input the grammar admits, and that is a disclosure
control rather than a saving.** The adapter used to ask two questions in two
spawns -- *does this object resolve to a commit* (``rev-parse``), then *did it
touch this path* -- and the first could answer no on its own. So a refusal about
an absent object cost one process and a refusal about a real commit cost two, and
the C4b battery measured the difference end to end at **+7.2 ms, P=1.000**: the
refusal's *duration* answered "does this object exist here", which is a fact
about the repository the caller was not granted. ADR-0033 decision 5 binds the
pair in text **and in duration**, so the two questions collapse into one call
whose outcome is read off the exit code and the output.
:func:`test_the_same_request_spawns_the_same_vector_whether_the_object_is_here_or_not`
is the pin, and it is the one that was RED when this shape was chosen.

**The one call is the git-2.30 ``log`` form, verdict-identical to the ``diff-tree``
form it replaced.** Round-1 reached merge commits with
``diff-tree --diff-merges=first-parent``; round-2 code review found that option
is a git 2.31 feature while the documented floor is 2.30
(``development.md``), so on a floor install it errors, the non-zero exit folds to
``NO_SUCH_COMMIT``, and every valid ``fixCommit`` refuses (HIGH-1). The command is
now ``git --literal-pathspecs log --no-walk --first-parent -m --name-only
--format= -z --root --end-of-options <sha>^{commit} -- <path>``, whose verdicts
are byte-identical (measured, git 2.47.1, 2026-09-19) and whose newest token is
``--end-of-options`` (2.24), at or below the floor -- ``-z`` predates it.
:func:`test_the_verify_command_uses_only_git_features_at_or_below_the_documented_floor`
holds the whole command to the floor as a class, not the one retired flag.

**``verify`` interprets git's ``--name-only`` output only as an exact set of
NUL-delimited raw path entries, empty-filtered, compared byte-identically -- no
quoting, no line-splitting, no whitespace-stripping, no pathspec breadth.** That
sentence is the round-3 closure, and it is exact: three consecutive findings were
one root cause -- ``verify`` was reading git's *human* rendering of paths.
Round-1 read empty-vs-nonempty; round-2 read line membership (reopened by a
literal directory, HIGH-2); round-3 read a rendering that ``core.quotePath``
quotes and newlines split (HIGH), so an honest fix for a CJK, quoted,
control-char or embedded-newline filename was falsely refused. ``-z`` emits the
machine format -- each entry is one full raw path, NUL-delimited -- and the
adapter splits on ``b"\\0"``, drops **empty entries only** (``!= b""``, never
``.strip()``: a file literally named ``" "`` emits ``b" "`` and ``.strip()``
would drop it, refusing an honest fix even under ``-z`` -- the fourth face), and
answers ``VERIFIED`` iff ``file_path.encode("utf-8")`` is one of those entries.
The comparison encoding is stated because it must be: git emits raw path *bytes*
and the stored ``file_path`` is a ``str``, so a non-UTF-8 disk path never verifies
a UTF-8 anchor -- a recorded fail-closed property
(:func:`test_a_non_utf8_disk_path_never_verifies_a_utf8_anchor`). What enforces it
is git's byte-based pathspec filter, which emits empty output for a UTF-8 pathspec
that does not match the raw bytes *before* the comparison runs; byte-membership
and a decode-membership are therefore provably equivalent on every reachable
input, so the property is closed on the git side, not by the comparison form.
Pathname byte-equality across Unicode normal forms (NFC vs NFD) is a *different*
root cause -- an equality residual, not an output-parsing one -- filed as
[#758](https://github.com/theurian/theurian/issues/758) and deliberately outside
this closure; ``-z`` does not touch it.

The directory sub-class round-2 closed stays closed under ``-z``: a raw entry is
one full path, never a directory and never a ``:(…)`` expression, so byte
membership refuses ``.`` and ``docs`` exactly as line membership did (measured).
This supersedes the round-1 "closed here rather than recorded" claim below --
``--literal-pathspecs`` stays as defence in depth.
:func:`test_a_stored_path_spelling_a_pathspec_expression_verifies_nothing` drives
the directory sub-class and
:func:`test_an_honest_anchor_of_any_path_shape_verifies` the rendering family.

**The funnel's own zero-spawn refusal does not reopen that channel, and the
reason is which fact the split is on.** Whether a value is forty lower-case hex
digits is a function of the caller's own bytes, computable by the caller without
asking Theurian anything; whether a well-formed sha is *present here* is a fact
about the repository. So the funnel splits on the first and the byte-identity
pins below hold the second: every input the grammar admits costs one process
carrying the same vector, whatever the repository turns out to hold.

**What each foreclosure is worth, measured on git 2.47.1, 2026-09-19** -- all
four re-measured under the single-call shape, because two of them had been
justified by a ``rev-parse`` behaviour that no longer runs:

* ``--literal-pathspecs`` -- **defence in depth over the ``:(…)`` magic
  sub-class**. Against a commit touching only ``docs/notes.md``, the stored paths
  ``:(exclude)src/retrying.py``, ``:!src/retrying.py``, ``:(glob)**/*.md`` and
  ``:(top)`` each make git print ``docs/notes.md`` without the flag, and print
  nothing under it. Round-1 recorded this as the closure; round-2 found it never
  closed a literal **directory** (``docs``, ``.``), and that the output-membership
  check above closes both -- so the flag is now belt to that check's braces.
* ``--root`` -- **defence in depth on the ``log`` form; it was load-bearing on
  ``diff-tree``**. ``diff-tree`` reported no files for a root commit without it,
  but ``git log`` shows a root commit's own diff by default -- measured git
  2.47.1, ``src/retrying.py`` with *and without* the flag on the log form -- so it
  changes no verdict here and stays only against a future move back to a diff
  form that needs it. Its teeth are the captured-vector pin, not a behavioural
  case.
* ``^{commit}`` on the revision -- **load-bearing, and for a different reason
  than it used to be**. Under the two-call shape it was what refused a fabricated
  forty hex digits, because ``rev-parse --verify`` accepts a full-width hex string
  as an object *name* without asking whether the object is present. The one call
  does not: ``eeee…eeee`` exits 128 with or without the suffix, so that
  justification did not survive the collapse and is recorded here as history
  rather than repeated as a live claim. What the suffix holds *now* is the
  **commit-only** semantics -- a tree id and a blob id both exit 0 with empty
  output without it, which this adapter would read as ``TOUCHES_NOTHING_HERE``,
  and exit 128 with it.
  :func:`test_an_object_that_is_not_a_commit_names_no_commit` is where that goes
  RED.
* ``--end-of-options`` before the sha -- **defence in depth over a value the
  funnel has already refused**, and this file says so rather than implying a
  behavioural pin it does not have. An option-shaped sha exits 128 behind the
  flag and 129 (git's usage error) without it; both are non-zero, so both were
  the same verdict even before the funnel, and no file was created in either
  reading. Since the funnel, no option-shaped value reaches the vector at all,
  so the flag guards the token position rather than any input a caller can send
  -- and what holds it is the captured vector in
  :func:`test_the_git_vector_is_one_process_fixed_and_forecloses_an_option_a_path_and_magic`,
  which is now captured at an *admitted* sha because a refused one spawns
  nothing to capture.
* ``--first-parent -m`` on ``log`` -- **load-bearing, and the only tokens here
  that change a verdict for an honest input**. ``log`` shows nothing for a merge
  commit without ``-m``, so a conflict-resolving merge that introduced the
  thread's fix would answer ``TOUCHES_NOTHING_HERE`` -- a true fix, correctly
  named, refused. ``--first-parent`` is what makes that diff the branch it landed
  on rather than every parent. Measured on git 2.47.1, 2026-09-19 over the three
  merge shapes
  :func:`test_a_merge_is_verified_when_it_changed_the_threads_file_against_its_first_parent`
  drives: the pair separates *this merge changed the file* from an all-parents
  diff's *some parent did*. This replaces round-1's
  ``diff-tree --diff-merges=first-parent``, whose option needs git 2.31, above the
  2.30 floor (HIGH-1).
* ``-z`` after ``--format=`` -- **load-bearing, and the only token that changes a
  verdict for an honest input with an unusual filename**. Without it git renders
  paths for humans: a CJK, quoted, backslash, control-char or embedded-newline
  name is quoted under ``core.quotePath`` and the output is newline-split, so the
  rendered line never equals the raw ``file_path`` and an honest fix is refused
  (round-3 HIGH). With it each entry is one raw path, NUL-delimited, compared
  byte-identically. Measured git 2.47.1, 2026-09-19 over the shape family
  :func:`test_an_honest_anchor_of_any_path_shape_verifies` drives, and the
  whitespace-only face :func:`test_an_honest_whitespace_only_anchor_verifies`
  holds separately because it also rules out ``.strip()``.

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
from fix_commit_grammar import ADMITTED, REFUSED, RefusedFixCommit

from theurian.domain.review import FixCommitVerdict
from theurian.infrastructure.git import fix_commit_check
from theurian.infrastructure.git.fix_commit_check import GIT_TIMEOUT_SECONDS, FixCommitCheck

pytestmark = pytest.mark.integration

#: The repository root, for reading the documented git floor out of
#: ``development.md`` (``test_the_floor_constant_matches_the_documented_git_requirement``).
REPO_ROOT = Path(__file__).resolve().parents[4]

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

#: Stored ``filePath`` values that make a **foreign** commit's diff non-empty, in
#: two sub-classes, so none of them may verify one.
#:
#: Author-controlled and reachable through a clone (T-24). Each was measured to
#: report :data:`OTHER_PATH` for a commit that touched only :data:`OTHER_PATH`:
#:
#: * **pathspec magic** -- ``:(exclude)``/``:!``/``:(glob)``/``:(top)``. Neutralised
#:   by ``--literal-pathspecs``, which makes git read them as literal paths that
#:   match nothing. ``:(top)`` is the worst of these: it names **no path at all**,
#:   so without the flag every commit verifies every thread.
#: * **literal directories** -- ``.``, ``./``, ``docs``, ``docs/``. This is the
#:   round-2 (HIGH-2) class ``--literal-pathspecs`` does **not** close: a directory
#:   is a literal path, and a directory pathspec matches every file *under* it, so
#:   a foreign commit that touched ``docs/notes.md`` makes ``docs`` (or ``.``)
#:   non-empty. ``docs`` is deliberately :data:`OTHER_PATH`'s own parent.
#:
#: What closes **both** is the output-membership check the round-2 fix adds: a
#: verdict is ``VERIFIED`` only when the *exact* stored ``file_path`` is one of the
#: named output lines. A ``--name-only`` line is a file path, never a directory and
#: never a ``:(…)`` expression, so neither sub-class can ever be a line -- which is
#: why membership subsumes what ``--literal-pathspecs`` did for magic and adds the
#: directory class ``--literal-pathspecs`` alone left open (measured, git 2.47.1,
#: 2026-09-19; ``high2_repro.py``).
MAGIC_STORED_PATHS: tuple[str, ...] = (
    f":(exclude){FILE_PATH}",
    f":!{FILE_PATH}",
    ":(glob)**/*.md",
    ":(top)",
    ".",
    "./",
    "docs",
    "docs/",
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

    **What refuses it is the single ``git`` call itself**, which exits 128 on an
    object the repository does not have (measured, git 2.47.1, on both the retired
    ``diff-tree`` form and the shipped ``log`` form). That is a change of mechanism
    worth recording rather than quietly inheriting: under the two-call shape the
    refusal came from the ``^{commit}`` dereference, because ``rev-parse --verify``
    accepted a full-width hex string as a well-formed object *name* without asking
    whether the object existed and printed it back at exit 0. The one call asks.
    The suffix still earns its place on this vector, and
    :func:`test_an_object_that_is_not_a_commit_names_no_commit` is now where that
    is held.
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
    ordinary anchored path over a foreign commit answers ``TOUCHES_NOTHING_HERE``
    because its exact name is not among the touched lines (the output-membership
    check), not because the lookup is broken -- so that test's refusals are the
    check working, and this arm is what shows the honest anchor still refuses a
    foreign commit rather than everything refusing.
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
    """A commit with no parent still verifies for a file it introduced.

    This is the honest behaviour a root-commit fix must have: a thread anchored to
    a file the first commit of a repository created is ordinary in a young
    project, and refusing it is the worst failure a verification has -- a true
    fix, correctly named, told to *go and find the right commit* it cannot.

    **``--root`` is what this looked like it pinned, and on the ``log`` form it no
    longer does -- said plainly rather than implied.** ``git log`` shows a root
    commit's own diff by default (measured, git 2.47.1: ``src/retrying.py`` with
    *and without* ``--root`` on the log form), so the flag is redundant here,
    unlike the retired ``diff-tree`` form where it was load-bearing (a root commit
    reported no files without it). This arm therefore verifies the *verdict*, not
    the flag; what holds ``--root`` in the vector is the captured-vector pin
    :func:`test_the_git_vector_is_one_process_fixed_and_forecloses_an_option_a_path_and_magic`,
    where it stays as defence in depth against a future move back to a diff form
    that needs it.
    """
    root = _git(repository, "rev-list", "--max-parents=0", "HEAD").stdout.strip()

    verdict = FixCommitCheck(repository).verify(root, FILE_PATH)

    assert verdict is FixCommitVerdict.VERIFIED, (
        f"the repository's root commit answered {verdict!r} for {FILE_PATH}, which it "
        f"introduced. A file a first commit created must verify, or every thread anchored "
        f"to one is unverifiable."
    )


# ---------------------------------------------------------------------------
# The stored path is author-controlled, and a pathspec is not a path (T-24).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stored_path", MAGIC_STORED_PATHS, ids=MAGIC_STORED_PATHS)
def test_a_stored_path_spelling_a_pathspec_expression_verifies_nothing(
    repository: Path, stored_path: str
) -> None:
    """A stored ``filePath`` that matches a **foreign** commit must not verify it.

    ``--`` stops an option-shaped path being read as a flag; it does **not** stop
    git parsing the path as a pathspec after the separator, and a pathspec matches
    more than the one file a thread is anchored to. Two sub-classes reach a
    non-empty answer for a commit that touched only :data:`OTHER_PATH`, and a
    non-empty answer used to be this adapter's ``VERIFIED``:

    * a **pathspec-magic** expression -- ``:(exclude)src/retrying.py`` asks for
      "everything the commit touched except the thread's file". ``--literal-pathspecs``
      neutralises this: git reads it as a literal path that matches nothing.
    * a **literal directory** -- ``docs`` or ``.``. ``--literal-pathspecs`` does
      **not** neutralise this (the round-2 HIGH-2 finding), because a directory is
      a literal path and a directory pathspec matches every file under it; ``docs``
      is :data:`OTHER_PATH`'s own parent, so it prints ``docs/notes.md``.

    **The closure is output-membership, not ``--literal-pathspecs``.** The round-2
    fix reads ``VERIFIED`` only when the *exact* stored ``file_path`` is one of the
    named output lines. A ``--name-only`` line is a file path -- never a directory,
    never a ``:(…)`` expression -- so neither sub-class can be one, and both refuse.
    This supersedes the round-1 claim that the magic class was "closed here rather
    than recorded" by ``--literal-pathspecs`` alone: that flag only ever closed
    ``:(…)`` magic, and the directory class was open until membership. The flag
    stays as defence in depth (``high2_repro.py`` verifies membership closes the
    magic class even with the flag removed).

    The reach is the whole point. ``file_path`` is not caller input on this call
    -- it is read out of the stored evidence record, and ``.theurian/review/`` is
    source rather than derived state, so a clone can deliver a record this
    installation never fetched (T-24). A fabricated ``filePath`` that turns the
    verification into "did this commit touch anything at all" is what this closes.
    """
    unrelated = _commit(repository, OTHER_PATH, "notes\n", "add notes")

    verdict = FixCommitCheck(repository).verify(unrelated, stored_path)

    assert verdict is FixCommitVerdict.TOUCHES_NOTHING_HERE, (
        f"the stored path {stored_path!r} verified a commit that touched only "
        f"{OTHER_PATH}, which is not the file the thread is anchored to. The verdict "
        f"must be `VERIFIED` only when the exact stored path is a named output line; a "
        f"directory or a `:(…)` pathspec is never one, so a review evidence file cannot "
        f"make `fix_commit_present` true for a commit with nothing to do with the thread."
    )


# ---------------------------------------------------------------------------
# The path-shape family: an honest anchor of any filename verifies (round-3 HIGH).
# ---------------------------------------------------------------------------

#: The filename shapes a stored ``file_path`` can take, one per way git's *human*
#: rendering of ``--name-only`` diverges from the raw path. This enumeration is
#: what was missing and hid three findings: every earlier fixture used an ASCII
#: path, which renders as itself. Each shape is driven against a commit that
#: really touches it (honest anchor → ``VERIFIED``) and a foreign commit
#: (→ ``TOUCHES_NOTHING_HERE``).
#:
#: ``cjk`` is the corpus's own measured input, the CJK phrase ADR-0023, the
#: CHANGELOG and the fixtures carry to demonstrate CJK tokenization -- git quotes
#: it under ``core.quotePath``. The quote/backslash/tab shapes are quoted too; the
#: embedded-newline shape is the one ``.splitlines()`` tore in two. The
#: whitespace-only shape and the non-UTF-8 path are their own tests below, because
#: each rules out a *different* naive port (``.strip()`` and a ``str`` compare).
#:
#: Pathname byte-equality across Unicode normal forms (NFC vs NFD) is a *separate*
#: class -- an equality residual, not an output-parsing one -- filed
#: [#758](https://github.com/theurian/theurian/issues/758) and out of this
#: closure; it is deliberately not a member here.
PATH_SHAPES: tuple[tuple[str, str], ...] = (
    ("ascii", "docs/ascii.md"),
    ("cjk", "docs/署名付きトークンを持つ.md"),
    ("double-quote", 'docs/a"b.md'),
    ("backslash", "docs/a\\b.md"),
    ("embedded-newline", "docs/a\nb.md"),
    ("tab", "docs/a\tb.md"),
)


def _seeded_repo(tmp_path: Path, name: str = "work") -> Path:
    """A fresh repository with one ordinary seed commit, for a per-shape fixture."""
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "-c", "init.defaultBranch=main", "init", "-q")
    _commit(repo, "seed.md", "seed\n", "seed")
    return repo


@pytest.mark.parametrize(("shape", "anchor"), PATH_SHAPES, ids=[shape for shape, _ in PATH_SHAPES])
def test_an_honest_anchor_of_any_path_shape_verifies(
    tmp_path: Path, shape: str, anchor: str
) -> None:
    """RED means an honest fix for a non-ASCII / quoted / newline filename is refused.

    The round-3 HIGH: ``verify`` read git's ``--name-only`` output as human-rendered
    text, so a filename git quotes under ``core.quotePath`` (CJK, a ``"`` , a
    backslash, a control char) rendered to a *quoted* line that never equalled the
    raw ``file_path``, and an embedded newline was split into two lines by
    ``.splitlines()`` -- each falsely answering ``TOUCHES_NOTHING_HERE`` for a
    commit that genuinely touched the file. The ``-z`` byte comparison reads each
    entry as one raw NUL-delimited path and matches it against
    ``file_path.encode("utf-8")``.

    Both arms, and the foreign arm is the negative control: without it a build that
    verified *everything* would pass the honest arm. The honest anchor verifies
    (RED now for every shape but ``ascii``); the same anchor over a foreign commit
    does not (green throughout -- it is the fail-closed side).
    """
    repo = _seeded_repo(tmp_path)
    touching = _commit(repo, anchor, "body\n", f"touch the {shape} file")
    foreign = _commit(repo, OTHER_PATH, "notes\n", "foreign: notes")
    check = FixCommitCheck(repo)

    assert check.verify(touching, anchor) is FixCommitVerdict.VERIFIED, (
        f"the {shape} anchor {anchor!r} did not verify a commit that touched it. git "
        f"renders this path for humans -- quoting it under `core.quotePath`, or splitting "
        f"it on an embedded newline -- so the rendered output never equals the raw path. "
        f"`-z` emits raw NUL-delimited bytes; the fix compares `file_path.encode('utf-8')` "
        f"to those (round-3 HIGH)."
    )
    assert check.verify(foreign, anchor) is FixCommitVerdict.TOUCHES_NOTHING_HERE, (
        f"the {shape} anchor {anchor!r} verified a foreign commit that never touched it; "
        f"byte membership must stay exact, not match everything"
    )


def test_an_honest_whitespace_only_anchor_verifies(tmp_path: Path) -> None:
    """RED means a fix for a file literally named ``" "`` is refused (the fourth face).

    A whitespace-only filename is a valid git path, and its ``-z`` entry is
    ``b" "`` -- non-empty, but ``.strip()`` would reduce it to ``b""`` and drop it.
    So this is RED under **two** implementations: the current ``.splitlines()`` code
    (which filters ``if ln.strip()``), and any naive ``-z`` port that keeps
    ``.strip()``. Only filtering *empty entries only* (``!= b""``) keeps ``b" "``
    and verifies the honest fix. This is why the fix must never ``.strip()``.

    The foreign arm is the control: the same anchor over a commit that did not touch
    it stays ``TOUCHES_NOTHING_HERE``.
    """
    repo = _seeded_repo(tmp_path)
    touching = _commit(repo, " ", "body\n", "touch the space-named file")
    foreign = _commit(repo, OTHER_PATH, "notes\n", "foreign: notes")
    check = FixCommitCheck(repo)

    assert check.verify(touching, " ") is FixCommitVerdict.VERIFIED, (
        "a file literally named ' ' did not verify a commit that touched it. Its `-z` "
        "entry is `b' '`, which `.strip()` drops to `b''` -- so an empty-*content* filter "
        "(`!= b''`) is required; `.strip()` refuses this honest fix even under `-z`."
    )
    assert check.verify(foreign, " ") is FixCommitVerdict.TOUCHES_NOTHING_HERE, (
        "the ' ' anchor verified a foreign commit; byte membership must stay exact"
    )


def test_a_multi_file_commit_verifies_each_of_its_files(tmp_path: Path) -> None:
    """Membership is over the whole set of touched paths, not the first entry.

    A commit that touches several files must verify for *each* of them and for
    none it did not touch. A parser that read only the first ``-z`` entry (or the
    first line) would verify one file and refuse the rest of the same commit. Green
    now (these are ASCII paths) and after the fix -- it holds the set semantics the
    byte comparison must keep.
    """
    repo = _seeded_repo(tmp_path)
    (repo / "docs").mkdir()
    (repo / "docs" / "one.md").write_text("1\n", encoding="utf-8")
    (repo / "docs" / "two.md").write_text("2\n", encoding="utf-8")
    _git(repo, "add", "docs/one.md", "docs/two.md")
    _git(repo, "commit", "-q", "-m", "touch two files")
    multi = _git(repo, "rev-parse", "HEAD").stdout.strip()
    check = FixCommitCheck(repo)

    assert check.verify(multi, "docs/one.md") is FixCommitVerdict.VERIFIED
    assert check.verify(multi, "docs/two.md") is FixCommitVerdict.VERIFIED, (
        "the second file of a multi-file commit did not verify: membership must be over "
        "the whole set of touched paths, not the first entry"
    )
    assert check.verify(multi, "docs/three.md") is FixCommitVerdict.TOUCHES_NOTHING_HERE, (
        "a file the multi-file commit did not touch verified anyway"
    )


def _git_raw(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    """Run ``git`` in ``repo`` and return raw stdout **bytes** (for non-UTF-8 paths)."""
    return subprocess.run(  # noqa: S603
        ["git", *_GIT_IDENTITY, *args],  # noqa: S607
        cwd=repo,
        capture_output=True,
        input=stdin,
        check=True,
    ).stdout


def _non_utf8_path_commit(repo: Path) -> tuple[str, bytes]:
    """A commit adding a file whose path is **non-UTF-8 bytes**, and that raw path.

    Built with ``hash-object`` + ``mktree -z`` + ``commit-tree`` (the #527-era
    technique) rather than ``git add``, because the shell/filesystem cannot carry
    an arbitrary byte path portably. ``mktree -z`` takes NUL-delimited records, so
    the raw ``0xff 0xfe`` bytes survive into the tree; the child commit has the
    seed as its parent, so the non-UTF-8 file is *added* and git emits its raw
    bytes in the ``-z`` diff (a parentless attempt diffs against nothing and emits
    an empty output -- the caveat this fixture is built to avoid).
    """
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    seed_blob = _git(repo, "rev-parse", "HEAD:seed.md").stdout.strip()
    blob = _git_raw(repo, "hash-object", "-w", "--stdin", stdin=b"nonutf\n").decode().strip()
    leaf = b"\xff\xfe.md"  # not valid UTF-8: 0xff can never begin a UTF-8 sequence
    inner = (
        _git_raw(repo, "mktree", "-z", stdin=b"100644 blob " + blob.encode() + b"\t" + leaf + b"\0")
        .decode()
        .strip()
    )
    outer_spec = (
        b"100644 blob " + seed_blob.encode() + b"\tseed.md\0"
        b"040000 tree " + inner.encode() + b"\tdocs\0"
    )
    outer = _git_raw(repo, "mktree", "-z", stdin=outer_spec).decode().strip()
    child = (
        _git_raw(repo, "commit-tree", outer, "-p", base, "-m", "add a non-utf-8 path under docs")
        .decode()
        .strip()
    )
    return child, b"docs/" + leaf


def test_a_non_utf8_disk_path_never_verifies_a_utf8_anchor(tmp_path: Path) -> None:
    """The encoding face: a non-UTF-8 disk path is fail-closed against a str anchor.

    With ``-z`` git emits raw path **bytes**; the stored ``file_path`` is a
    ``str``. A path git records as ``docs/\\xff\\xfe.md`` -- non-UTF-8, because
    ``0xff`` can never begin a UTF-8 sequence -- can never be named by any
    ``str``, so an honest fix for it is refused ``TOUCHES_NOTHING_HERE``. That is
    acceptable and fail-closed, and this pin holds the **property**, verified by a
    fixture rather than asserted from reasoning.

    **What enforces it is git's byte-based pathspec filter, not the comparison
    form -- said plainly, because the first draft claimed the wrong mechanism.**
    ``verify`` passes the stored ``file_path`` as ``-- <path>``, and a UTF-8 ``str``
    pathspec is byte-matched by git against the tree's raw path bytes; it never
    matches ``docs/\\xff\\xfe.md``, so git emits **empty output before any
    comparison runs** (measured: ``b""`` for the anchor pathspec against this
    commit, git 2.47.1). So byte-membership and a ``decode("utf-8",
    "surrogateescape")`` membership are **provably equivalent on every reachable
    input** -- both read the empty output as ``TOUCHES_NOTHING_HERE`` -- and this
    pin is therefore **not** a discriminator between comparison forms: a decode
    port survives it. The comparison's real teeth are elsewhere -- the ``.strip()``
    face (:func:`test_an_honest_whitespace_only_anchor_verifies`) and the
    rendering-shape family (:func:`test_an_honest_anchor_of_any_path_shape_verifies`).
    The encoding face is closed on the git side and this records that it stays
    fail-closed.

    The fixture is verified real before the property is asserted: git is asked for
    the commit's ``-z`` entries **without a pathspec** and the raw non-UTF-8 bytes
    must be among them -- otherwise the commit did not actually carry the path and
    the assertion below would pass over nothing (the caveat the #527-era build
    avoids). This is the **encoding** face and is in scope; normalization (NFC vs
    NFD) is #758 and is not tested here.
    """
    repo = _seeded_repo(tmp_path)
    child, raw_name = _non_utf8_path_commit(repo)

    emitted = [
        entry
        for entry in _git_raw(
            repo,
            "--literal-pathspecs",
            "log",
            "--no-walk",
            "--first-parent",
            "-m",
            "--name-only",
            "--format=",
            "-z",
            "--root",
            "--end-of-options",
            f"{child}^{{commit}}",
        ).split(b"\0")
        if entry
    ]
    assert raw_name in emitted, (
        f"the fixture did not make git emit the raw non-UTF-8 path: emitted {emitted!r}, "
        f"expected {raw_name!r} among them. Without git emitting the raw bytes this test "
        f"exercises nothing (the empty-output caveat)."
    )

    anchor = raw_name.decode("utf-8", "replace")  # a proper-UTF-8 str a record could store
    assert anchor.encode("utf-8") not in emitted, (
        f"the UTF-8-encoded anchor {anchor.encode('utf-8')!r} equalled a raw non-UTF-8 "
        f"entry, which cannot happen -- the premise of the fail-closed property is wrong"
    )
    assert FixCommitCheck(repo).verify(child, anchor) is FixCommitVerdict.TOUCHES_NOTHING_HERE, (
        "a non-UTF-8 disk path verified against a UTF-8-encoded str anchor. The comparison "
        "encodes the anchor to UTF-8 and compares to raw entries, so a non-UTF-8 path is "
        "fail-closed (TOUCHES_NOTHING_HERE) -- a recorded property (the encoding face; "
        "NFC/NFD normalization is #758, out of scope)."
    )


# ---------------------------------------------------------------------------
# The caller's sha is wire input (decision 3).
# ---------------------------------------------------------------------------


def test_an_option_shaped_fix_commit_is_refused_and_starts_nothing(repository: Path) -> None:
    """An option-shaped ``fixCommit`` is a revision that resolves to nothing.

    **What this pins is the composed outcome, and not any one foreclosure.** The
    module docstring records the measurements: this class is closed four times
    over -- the entry funnel, the ``^{commit}`` suffix, ``--end-of-options`` and
    the non-zero-exit check -- and deleting any one of the last three alone
    changes no answer, so a green result here is *not* evidence that the flag is
    present. The captured-vector pin below is what holds the flag.

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
    a commit. Without the suffix the command takes them and exits **0 with no
    output**, which this adapter reads as ``TOUCHES_NOTHING_HERE`` -- *the commit
    is here, it just did not touch your file*. That is the wrong answer about the
    wrong thing, and it is the only place the suffix now changes one: measured on
    git 2.47.1 on the shipped ``log`` form (and the retired ``diff-tree`` form),
    both exit 128 with the suffix and 0 without.

    This case exists because the suffix's old justification did not survive the
    collapse to one call. It used to be held by the fabricated-sha case, where
    ``rev-parse`` accepted any full-width hex string; the one call rejects an
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


# ---------------------------------------------------------------------------
# The entry funnel: `fixCommit` is an object name, never an expression.
# ---------------------------------------------------------------------------


@pytest.fixture
def forgeable(tmp_path: Path) -> Iterator[Path]:
    """A repository in which **every resolvable member of the corpus verifies**.

    The funnel battery's whole value depends on this fixture, because a battery
    driven at inputs git would have refused anyway measures nothing but a spawn
    count. So the history is built for the worst case on each axis a revision
    expression can reach:

    * ``HEAD`` **touches the anchored file**, so a name that resolves to it --
      ``main``, ``HEAD~0``, ``refs/heads/main``, ``HEAD@{0}`` -- answers
      ``VERIFIED`` rather than merely resolving;
    * ``HEAD``'s message carries ``planted``, so the two search forms find it;
    * there are **three** commits, so ``HEAD~2..HEAD`` is a range that resolves.

    :func:`test_the_forgeable_repository_verifies_the_commit_the_funnel_members_aim_at`
    is the control that says so, and it is what separates this battery's
    refusals from a fixture in which nothing could have been verified anyway.
    """
    repo = tmp_path / "forgeable"
    repo.mkdir()
    _git(repo, "-c", "init.defaultBranch=main", "init", "-q")
    _commit(repo, FILE_PATH, "def retry():\n    pass\n", "root: add the retry helper")
    _commit(repo, OTHER_PATH, "notes\n", "add notes")
    _commit(repo, FILE_PATH, "def retry():\n    return None\n", "planted: fix the deadlock")
    yield repo


def test_the_forgeable_repository_verifies_the_commit_the_funnel_members_aim_at(
    forgeable: Path,
) -> None:
    """The positive control the whole funnel battery rests on.

    Every arm below asserts a refusal, and a refusal is satisfied by an empty
    repository, by a fixture whose HEAD touches nothing, and by an adapter that
    refuses everything. What makes those refusals evidence is that the commit the
    expressions aim at is really here and really touched the anchored file, so
    the *only* thing separating ``HEAD^{/planted}`` from the sha it resolves to is
    how the caller spelled it.
    """
    head = _git(forgeable, "rev-parse", "HEAD").stdout.strip()

    assert FixCommitCheck(forgeable).verify(head, FILE_PATH) is FixCommitVerdict.VERIFIED, (
        f"the fixture's HEAD ({head[:12]}…) does not verify against {FILE_PATH} by its own "
        f"sha, so every refusal in this battery is about a repository in which nothing "
        f"could have been verified. Fix the fixture before reading any arm below."
    )


@pytest.mark.parametrize("member", REFUSED, ids=[member.label for member in REFUSED])
def test_a_fix_commit_that_is_not_a_full_object_name_is_refused_without_spawning(
    forgeable: Path, monkeypatch: pytest.MonkeyPatch, member: RefusedFixCommit
) -> None:
    """ADR-0033 decision 3: the caller names a commit, it does not describe one.

    Two reviewers recovered through this independently, and the recovery needs no
    secret: ``fixCommit`` was spent as a git **revision expression**, and git's
    revision language can search history by commit message, search every ref,
    name a branch, count backwards from ``HEAD``, read this machine's reflog, or
    name a whole range. Each of those satisfies ``fix_commit_present`` without
    the caller knowing a single object id -- which is the one thing decision 3
    says the caller has to go and find.

    So the value is funnelled at **entry**: a full-length lower-case object name
    or nothing, answered ``NO_SUCH_COMMIT`` before a process exists.

    **Both halves are asserted, and they fail for different reasons.** The
    verdict is the behavioural change, and it is what moved for the resolvable
    members (each measured ``VERIFIED`` before the funnel, against this
    fixture). The **zero** spawns is the half every member carries: a refusal
    that costs a process is one a caller can time, and it is also the half that
    keeps a caller's own bytes from reaching ``git``'s argv at all -- the
    universal the spawn-site ratchet in ``tests/unit/test_network_call_sites.py``
    holds one layer up.
    """
    calls = _recorded_spawns(monkeypatch)

    verdict = FixCommitCheck(forgeable).verify(member.value, FILE_PATH)

    assert verdict is FixCommitVerdict.NO_SUCH_COMMIT, (
        f"{member.label}: {member.value!r} answered {verdict!r}. It is not a full-length "
        f"object name, and {member.why}. A caller that can describe the commit it wants "
        f"does not have to find one, and `fix_commit_present` stops being a signal "
        f"anybody had to earn (ADR-0033 decision 3)."
    )
    assert calls == [], (
        f"{member.label}: {member.value!r} reached git as "
        f"{[call['args'][1:] for call in calls]}. The funnel refuses at entry, so no "
        f"value outside the grammar is ever spent as a revision -- and a refusal that "
        f"spawns is a refusal a caller can time."
    )


@pytest.mark.parametrize("spelling", ["abbreviated", "upper-cased"])
def test_a_real_commits_other_spellings_are_refused_without_spawning(
    forgeable: Path, monkeypatch: pytest.MonkeyPatch, spelling: str
) -> None:
    """The two members the corpus cannot hold as constants, at the worst instance.

    Both name a commit this repository really has, so both measured ``VERIFIED``
    before the funnel -- they are the corpus's ``abbreviation`` and
    ``upper-case`` members with a sha that actually resolves, which no constant
    can be. They are refused for different reasons and each is worth stating:

    * an **abbreviation** is a prefix, and a prefix that is unique today becomes
      ambiguous as a repository grows, so the verdict a caller gets for one value
      would change with history it cannot see;
    * an **upper-cased** sha is the same object under a second spelling, and a
      funnel that admitted both would leave two caller strings reaching one
      verification.
    """
    head = _git(forgeable, "rev-parse", "HEAD").stdout.strip()
    value = head[:7] if spelling == "abbreviated" else head.upper()
    calls = _recorded_spawns(monkeypatch)

    verdict = FixCommitCheck(forgeable).verify(value, FILE_PATH)

    assert verdict is FixCommitVerdict.NO_SUCH_COMMIT, (
        f"the {spelling} spelling {value!r} of a commit this repository has answered "
        f"{verdict!r}. The grammar is full-length lower-case hex, so one object has one "
        f"spelling a caller may send."
    )
    assert calls == [], f"the {spelling} spelling reached git: {[c['args'][1:] for c in calls]}"


@pytest.mark.parametrize("value", ADMITTED, ids=[f"{len(value)}-hex" for value in ADMITTED])
def test_a_value_the_grammar_admits_still_reaches_exactly_one_git_call(
    forgeable: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """The funnel's other boundary: what it lets through still costs one process.

    A funnel with no admitted side is a verification that verifies nothing, and
    every refusal arm above would be green against it. Both admitted widths are
    driven because both are object names a repository can carry: forty for
    SHA-1 and sixty-four for a ``--object-format=sha256`` repository, which a
    forty-only grammar would refuse every commit of.

    The verdict is deliberately **not** asserted: neither fabricated sha is an
    object this fixture holds, so the honest claim here is about the work rather
    than the answer -- exactly one git call, which is what
    :func:`test_the_same_request_spawns_the_same_vector_whether_the_object_is_here_or_not`
    then holds byte-identical across repositories.
    """
    calls = _recorded_spawns(monkeypatch)

    FixCommitCheck(forgeable).verify(value, FILE_PATH)

    assert len(calls) == 1, (
        f"a {len(value)}-hex object name reached {len(calls)} git call(s). The grammar "
        f"admits both object-name widths, and an admitted value is verified against the "
        f"repository rather than answered from its spelling."
    )


# ---------------------------------------------------------------------------
# A merge commit is a commit (code review HIGH-1).
# ---------------------------------------------------------------------------


def _merge_repository(tmp_path: Path, name: str, *, side_touches: bool, main_touches: bool) -> Path:
    """A repository whose HEAD is a merge, with each side's changes as asked.

    ``--no-ff`` always, so HEAD is a merge commit whatever the two branches did.
    When both sides touch :data:`FILE_PATH` the merge conflicts and is resolved
    here, which is the shape the finding was reported against: the resolution
    text belongs to no parent, so the merge commit itself is the only commit that
    introduced it.
    """
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "-c", "init.defaultBranch=main", "init", "-q")
    _commit(repo, FILE_PATH, "def retry():\n    pass\n", "root: add the retry helper")
    _commit(repo, OTHER_PATH, "notes\n", "add notes")

    _git(repo, "checkout", "-q", "-b", "side")
    if side_touches:
        _commit(repo, FILE_PATH, "def retry():\n    return 'side'\n", "side: take the lock later")
    else:
        _commit(repo, OTHER_PATH, "notes: side\n", "side: note the incident")

    _git(repo, "checkout", "-q", "main")
    if main_touches:
        _commit(repo, FILE_PATH, "def retry():\n    return 'main'\n", "main: take the lock later")
    else:
        _commit(repo, OTHER_PATH, "notes: main\n", "main: note the rollout")

    merge = subprocess.run(  # noqa: S603
        ["git", *_GIT_IDENTITY, "merge", "--no-ff", "-m", "merge side into main", "side"],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if merge.returncode != 0:
        (repo / FILE_PATH).write_text("def retry():\n    return 'resolved'\n", encoding="utf-8")
        _git(repo, "add", FILE_PATH)
        _git(repo, "commit", "-q", "--no-edit")
    return repo


#: The three merge shapes, with the verdict first-parent semantics give each.
#:
#: The semantics are the same whichever command carries them, and the round-2
#: HIGH-1 fix moved from ``diff-tree --diff-merges=first-parent`` (a git 2.31
#: feature, above the 2.30 floor) to ``log --first-parent -m``, which is
#: verdict-identical and 2.30-compatible. Three shapes rather than one, because
#: each rules out a reading of a merge diff that is *not* first-parent (measured,
#: git 2.47.1, 2026-09-19, where both the default ``diff-tree`` and a ``log``
#: without ``--first-parent -m`` answered ``TOUCHES_NOTHING_HERE`` for all three):
#:
#: * **conflict-resolved** is the reported finding: both sides changed the file,
#:   the merge resolved it, and the resolution is in no parent;
#: * **side-branch-only** is what a *combined* diff of the merge gets wrong -- it
#:   suppresses a hunk that came verbatim from one parent, so it answers
#:   ``TOUCHES_NOTHING_HERE`` for a merge that plainly changed the file relative
#:   to the branch it landed on;
#: * **mainline-only** is what an *all-parents* diff gets wrong -- it reports the
#:   file for *any* parent that differs, so a merge that changed nothing on the
#:   first-parent line verifies because the side branch was behind.
_MERGE_SHAPES: tuple[tuple[str, bool, bool, FixCommitVerdict], ...] = (
    ("conflict-resolved", True, True, FixCommitVerdict.VERIFIED),
    ("side-branch-only", True, False, FixCommitVerdict.VERIFIED),
    ("mainline-only", False, True, FixCommitVerdict.TOUCHES_NOTHING_HERE),
)


@pytest.mark.parametrize(
    ("name", "side_touches", "main_touches", "expected"),
    _MERGE_SHAPES,
    ids=[shape[0] for shape in _MERGE_SHAPES],
)
def test_a_merge_is_verified_when_it_changed_the_threads_file_against_its_first_parent(
    tmp_path: Path, name: str, side_touches: bool, main_touches: bool, expected: FixCommitVerdict
) -> None:
    """A merge is diffable only against a chosen parent, and it must be the first.

    The failure this closes is the worst kind a verification has: a true fix,
    correctly named, refused with a message that says *go and find the right
    commit*. A conflict-resolving merge is often the only commit that carries the
    fix -- the resolution text is in neither parent -- and a command that omits
    merges answers ``TOUCHES_NOTHING_HERE`` for every one of them.

    The round-2 fix reaches merges with ``log --first-parent -m`` (git 2.30
    compatible), having retired ``diff-tree --diff-merges=first-parent`` because
    that option is a git 2.31 feature above the documented floor (HIGH-1). The
    semantics the three shapes pin are the same either way: **this merge changed
    the file relative to the branch it landed on**, so a merge that only brought
    the mainline's own earlier change along is not the commit that introduced it
    -- the mainline commit is, and the caller can name that one.

    **The reading was enumerated before these three were chosen**, measured on git
    2.47.1, 2026-09-19, so the expected column matches first-parent and not a
    family of readings:

    ===============  ==========  =============  =============  ==============
    shape            merges off  first-parent   all-parents    combined
    ===============  ==========  =============  =============  ==============
    conflict         nothing     **verified**   verified       verified
    side-branch      nothing     **verified**   verified       nothing
    mainline         nothing     **nothing**    verified       nothing
    ===============  ==========  =============  =============  ==============

    *merges off* is what a plain diff of a merge shows (nothing); *all-parents*
    (``diff-tree``'s ``--diff-merges=separate``, or ``log -m`` without
    ``--first-parent``) verifies ``mainline-only`` because some parent differs;
    *combined* refuses ``side-branch-only`` because it hides a hunk that came
    verbatim from one parent. No proper subset of these three shapes picks the
    first-parent column.
    """
    repo = _merge_repository(tmp_path, name, side_touches=side_touches, main_touches=main_touches)
    parents = _git(repo, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
    merge = parents[0]

    verdict = FixCommitCheck(repo).verify(merge, FILE_PATH)

    assert len(parents) == 3, (
        f"the {name} fixture's HEAD has {len(parents) - 1} parent(s), so it is not a merge "
        f"and this arm is about an ordinary commit"
    )
    assert verdict is expected, (
        f"the {name} merge answered {verdict!r}, expected {expected!r}.\n\n"
        f"`--first-parent -m` is what makes a merge commit diffable against the branch it "
        f"landed on: dropping `-m` shows nothing, dropping `--first-parent` verifies "
        f"`mainline-only` (all parents), and a combined diff refuses `side-branch-only`. A "
        f"merge the command cannot see is a true fix refused, and the caller is told to go "
        f"and find a commit that does not exist."
    )


def test_a_merges_first_parent_is_still_verified_on_its_own(tmp_path: Path) -> None:
    """The control for the arm above: first-parent semantics change no plain commit.

    Without it, ``--first-parent -m`` could be doing something to every verdict
    rather than to the merges it was added for. The ``mainline-only`` fixture is
    the one that separates the two readings: its merge answers
    ``TOUCHES_NOTHING_HERE`` while the mainline commit *inside* it touched the
    file, so a change that had broken ordinary commits would show here as the
    parent going quiet too.
    """
    repo = _merge_repository(
        tmp_path, "mainline-only-control", side_touches=False, main_touches=True
    )
    first_parent = _git(repo, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()[1]

    verdict = FixCommitCheck(repo).verify(first_parent, FILE_PATH)

    assert verdict is FixCommitVerdict.VERIFIED, (
        f"the merge's first parent answered {verdict!r} for the file it changed. The "
        f"`--first-parent -m` pair governs merge commits; an ordinary commit's verdict "
        f"must not move with it."
    )


# ---------------------------------------------------------------------------
# A NUL byte reaches `subprocess`, which raises rather than returning.
# ---------------------------------------------------------------------------


def test_a_stored_path_carrying_a_nul_byte_is_a_verdict_and_not_a_crash(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fail-closed reading has to cover the exception ``subprocess`` actually raises.

    ``file_path`` is author-controlled stored data a clone can deliver (T-24),
    and a NUL byte cannot cross ``subprocess``: it raises ``ValueError: embedded
    null byte`` from inside the spawn, which is neither ``OSError`` nor
    ``TimeoutExpired``. So the one exception a *stored* value can provoke was the
    one the fail-closed ``except`` did not name, and it left the adapter raising
    through ``verify`` into a caller that may only catch ``TheurianError``.

    The caller-side twin of this is dead since the entry funnel: a ``fixCommit``
    carrying a NUL is not a full object name, so it never reaches the spawn --
    the corpus's ``embedded-nul`` member is where that half is held.

    **The sha is taken from the admitted grammar, and the spawn is asserted to
    have been attempted.** Both are the same guard: with any other sha the funnel
    answers first, no spawn happens, the verdict is ``NO_SUCH_COMMIT`` anyway,
    and this arm goes green having never reached the ``except`` it is about.
    """
    calls = _recorded_spawns(monkeypatch)

    verdict = FixCommitCheck(repository).verify(ADMITTED[0], "a\x00b")

    assert len(calls) == 1, (
        f"the verification made {len(calls)} spawn attempt(s), so the stored path never "
        f"reached `subprocess` and the refusal below came from somewhere else -- the "
        f"sha has to be a value the grammar admits for this arm to be about the path"
    )
    assert verdict is FixCommitVerdict.NO_SUCH_COMMIT, (
        f"a stored path carrying a NUL byte answered {verdict!r} rather than the "
        f"fail-closed refusal. Every reading that cannot reach git joins the refusing "
        f"side; a `ValueError` out of `verify` crosses the tool seam as a traceback "
        f"about a domain the caller has never heard of."
    )


def test_the_git_vector_is_one_process_fixed_and_forecloses_an_option_a_path_and_magic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole spawned vector, captured, at the worst input on each axis at once.

    ``PROCESS_SPAWN_SITES`` records this module, and the checklist
    ``test_network_call_sites.py`` states binds it: the vector is fixed by the
    adapter rather than taken from a document, a config, a URL or a remote, and
    the call carries a timeout.

    **One fixture, worst member on each axis that still reaches a spawn**: the
    sha is the widest value the grammar admits and the stored path is a pathspec
    expression, so a single capture shows the caller's value landing behind
    ``--end-of-options`` and the record's value landing behind ``--`` under
    ``--literal-pathspecs``. The sha used to be option-shaped, which was the
    worst member while any string could be spent as a revision; since the entry
    funnel such a value is refused before a process exists, so capturing at one
    would capture nothing and assert over an empty list.

    **``--end-of-options`` is held here and nowhere else**, which is why an exact
    vector equality is worth its brittleness: it forecloses a value the funnel
    already refuses, so no behavioural case can tell whether it is present. The
    module docstring records that measurement rather than leaving this pin to
    imply a coverage it has.

    **The command is the git-2.30 ``log`` form, not ``diff-tree``.** The round-1
    fix reached merges with ``--diff-merges=first-parent``, which is a git 2.31
    feature, while the documented floor is 2.30 (``development.md``); on 2.30 that
    option errors, ``verify`` folds the non-zero exit to ``NO_SUCH_COMMIT``, and
    every valid ``fixCommit`` refuses (round-2 HIGH-1). The log form
    ``git ... log --no-walk --first-parent -m --name-only --format= --root ...``
    is verdict-identical and 2.30-compatible; the floor-allowlist pin
    :func:`test_the_verify_command_uses_only_git_features_at_or_below_the_documented_floor`
    holds it to the floor.

    RED if the call count moves off one, if a timeout is dropped, if the call
    reaches a shell, if the binary stops being an absolute path, if
    ``--end-of-options`` / ``--`` / ``--literal-pathspecs`` / ``--root`` /
    ``--first-parent`` / ``-m`` / ``--no-walk`` leaves the vector, if
    ``--diff-merges=first-parent`` returns, or if the ``^{commit}`` suffix leaves
    the revision.
    """
    caller_sha = max(ADMITTED, key=len)
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
        "log",
        "--no-walk",
        "--first-parent",
        "-m",
        "--name-only",
        "--format=",
        "-z",
        "--root",
        "--end-of-options",
        f"{caller_sha}^{{commit}}",
        "--",
        stored_path,
    ], vector
    assert vector[1] == "--literal-pathspecs", (
        "`--literal-pathspecs` is a root-level option and must precede the `log` "
        "subcommand; after it git does not accept it at all, and the stored path is a "
        "pathspec expression again"
    )
    assert "-z" in vector, (
        "`-z` must stay in the vector: it makes `git` emit NUL-delimited *raw path "
        "bytes* rather than its human rendering, and the round-3 fix reads those bytes "
        "directly. Without it git quotes a CJK, quoted, control-char or newline path "
        "under `core.quotePath` and splits on newlines, so an honest fix for such a file "
        "is falsely refused (round-3 HIGH)"
    )
    assert "--root" in vector, (
        "`--root` must stay in the vector as defence in depth: it is redundant on the "
        "`log` form (log shows a root commit's diff by default) but load-bearing on a "
        "`diff-tree` form, so it guards a move back to one -- and this captured-vector "
        "pin is where it is held, since no `log`-form behavioural case can tell"
    )
    assert {"--first-parent", "-m"} <= set(vector), (
        "`--first-parent` and `-m` must stay in the vector, or `log` shows a merge "
        "commit against no parent (nothing) or against all of them, and every fix that "
        "landed as a conflict resolution is misjudged"
    )
    assert "--diff-merges=first-parent" not in vector, (
        "`--diff-merges=first-parent` is a git 2.31 feature and the documented floor is "
        "2.30 (round-2 HIGH-1); the log form reaches merges without it, so its return "
        "breaks the floor"
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


# ---------------------------------------------------------------------------
# The command uses only git features at or below the documented floor (HIGH-1).
# ---------------------------------------------------------------------------

#: The git version the project documents as its floor, read off the requirements
#: table in ``development.md``. A capability the fix relies on that is newer than
#: this errors on a supported install and folds to ``NO_SUCH_COMMIT`` -- refusing
#: every valid ``fixCommit``, which is round-2 HIGH-1 (the round-1
#: ``--diff-merges=first-parent`` was a git 2.31 feature above this floor).
GIT_FLOOR: tuple[int, int] = (2, 30)

#: Every option, subcommand and separator ``verify`` may hand ``git``, each with
#: the git version that introduced it. The load-bearing entries are the newest
#: genuine token and the one the finding retired:
#:
#: * ``--end-of-options`` is 2.24.0 -- the newest token the command actually
#:   carries, and it is at or below the 2.30 floor;
#: * ``--diff-merges=first-parent`` is 2.31.0 and is **not** here, which is why the
#:   captured-vector pin refuses it and the positive control below reddens when it
#:   is added to a floor comparison.
#:
#: The older entries are recorded at the version that introduced each; small
#: inaccuracies among the pre-2.0 flags cannot change the verdict, because the
#: check is "every token at or below (2, 30)" and they are all far below it. What
#: the pin actually closes is the *class* "a future token newer than the floor",
#: whatever flag it is -- a second HIGH-1. It is not a real 2.30 run: CI runs
#: modern git, so the allowlist recorded here is the check.
GIT_TOKEN_FLOOR: dict[str, tuple[int, int]] = {
    "log": (1, 0),
    "--no-walk": (1, 5),
    "--first-parent": (1, 5),
    "-m": (1, 5),
    "--name-only": (1, 0),
    "--format=": (1, 6),
    "-z": (1, 5),
    "--root": (1, 5),
    "--literal-pathspecs": (1, 9),
    "--end-of-options": (2, 24),
    "--": (1, 0),
}


def _above_floor(
    tokens: list[str], allowlist: dict[str, tuple[int, int]], floor: tuple[int, int]
) -> list[str]:
    """The tokens whose recorded introducing version is newer than *floor*.

    Split out so the positive control drives the same code the pin does: a token
    with a version above the floor is exactly what a second HIGH-1 looks like,
    whatever the flag.
    """
    return [token for token in tokens if allowlist[token] > floor]


def test_the_floor_constant_matches_the_documented_git_requirement() -> None:
    """The premise: :data:`GIT_FLOOR` is the version ``development.md`` publishes.

    A floor bump moves the constant and the doc together, or the pin below holds
    the command to a floor the project no longer promises. Read off the doc rather
    than trusted, because if the project ever raised its floor to 2.31 then
    ``--diff-merges=first-parent`` would be admissible and HIGH-1 would not be a
    finding -- so this constant may not drift from the requirements table.
    """
    requirements = (REPO_ROOT / "docs" / "contributing" / "development.md").read_text(
        encoding="utf-8"
    )
    major, minor = GIT_FLOOR

    assert f"| Git | {major}.{minor}+ |" in requirements, (
        f"`development.md` no longer states the git floor as {major}.{minor}+, so "
        f"`GIT_FLOOR` has drifted from the documented requirement. Move both together -- "
        f"and if the floor really rose, the `--diff-merges`-class tokens this pin excludes "
        f"may now be admissible."
    )


def test_the_verify_command_uses_only_git_features_at_or_below_the_documented_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED means ``verify`` spawns a git feature newer than the documented floor.

    The class-level pin round-2 HIGH-1 asks for: not "``--diff-merges`` is gone"
    but "the ``verify`` command uses only features available at the documented git
    floor". Its two halves both have teeth:

    * **subset** -- every option, subcommand and separator ``verify`` hands ``git``
      is a recorded token. A flag the allowlist does not name -- including
      ``--diff-merges=first-parent`` re-added -- fails here, because its version is
      unknown;
    * **floor** -- every recorded token was introduced at or below :data:`GIT_FLOOR`.

    Read off a captured call rather than the source, so it is the argv the process
    actually spawns. The two runtime values (the ``^{commit}`` revision and the
    stored path) are not tokens and are excluded by identity.

    The positive control is :func:`_above_floor` driven over a synthetic allowlist
    carrying a known-2.31 token, so a floor comparison that stopped catching a
    newer feature is caught here rather than shipping.
    """
    caller_sha = max(ADMITTED, key=len)
    path = FILE_PATH
    calls = _recorded_spawns(monkeypatch)

    FixCommitCheck(tmp_path).verify(caller_sha, path)

    assert len(calls) == 1, f"expected one spawn to read the command off; got {len(calls)}"
    runtime_values = {f"{caller_sha}^{{commit}}", path}
    tokens = [token for token in calls[0]["args"][1:] if token not in runtime_values]

    unlisted = [token for token in tokens if token not in GIT_TOKEN_FLOOR]
    assert not unlisted, (
        f"`verify` hands git {unlisted}, which the floor allowlist does not name. A token "
        f"with no recorded introducing version cannot be shown to predate the {GIT_FLOOR} "
        f"floor -- record its version in `GIT_TOKEN_FLOOR` if it is at or below the floor, "
        f"or drop it if it is a `--diff-merges`-class feature newer than the floor (HIGH-1)."
    )
    above = _above_floor(tokens, GIT_TOKEN_FLOOR, GIT_FLOOR)
    assert not above, (
        f"`verify` uses {[(t, GIT_TOKEN_FLOOR[t]) for t in above]}, newer than the "
        f"documented git floor {GIT_FLOOR}. On a floor install the option errors, the "
        f"non-zero exit folds to `NO_SUCH_COMMIT`, and every valid `fixCommit` refuses "
        f"(round-2 HIGH-1)."
    )

    planted = {**GIT_TOKEN_FLOOR, "--diff-merges=first-parent": (2, 31)}
    assert _above_floor(["--diff-merges=first-parent"], planted, GIT_FLOOR) == [
        "--diff-merges=first-parent"
    ], (
        "the floor comparison stopped catching a token newer than the floor; a second "
        "HIGH-1 would ship green"
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
