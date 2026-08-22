"""Which files the scan opens, and the invocations they turn out to hold.

Split from ``tests/unit/test_documented_commands.py``, which owns the recorded
exemptions and the assertions, and sitting beside ``command_extraction``, which
owns turning one text into command words. This module is the middle: the
population -- which files the repository ships -- and what it hands the readers.

**The population is what git tracks, not what is on disk.** ``git ls-files
--cached`` is the definition, and "ships" is the whole of it: a file the index
holds is a file every clone gets, and nothing else here is any clone's problem.
Asking the filesystem instead was #262. A machine that dogfoods Theurian keeps
its own knowledge under ``.theurian/``, excluded through ``.git/info/exclude``
and never committed; a directory-name rule descended into it and the suite
failed on a handoff note quoting ``theurian upgrade`` that no clone will ever
hold. The same class had already been patched once by name: the mutation
harness runs the suite with ``TMPDIR`` inside its copy of the tree, and a run
left 12,734 fixture files under ``.mutate-tmp/`` that the scan read, turning the
unmutated control RED and every verdict in that batch with it. Git answers both,
and answers the residual the name list recorded and could not close --
``plugins/claude-code/.claude-plugin/`` is a fourth shipped dot directory the
three-entry list did not name, and the population picked up its ``plugin.json``
the moment git was asked (measured at bd4fb25).

**Untracked files are deliberately out, ignored or not.** Adding ``--others
--exclude-standard`` to catch a dead command before it is committed looks like a
strictly better gate and is not: it makes the gate fail on the product's own
documented workflow. ``theurian propose`` writes
``.theurian/proposals/<proposal-id>/`` -- a migration named after the change, the
body, and ``evidence.json`` -- and those files stay untracked for the whole
review window ``propose accept`` exists to close. The committed ``.gitignore``
does not cover them, and a fresh clone has no ``.git/info/exclude`` to fence
them off, so ``--others`` puts a draft proposal into the scan on any machine
running the flow this repository documents. Reproduced on a clone: all three
files appear in that listing. An uncommitted draft that names a dead command is
caught the moment it is staged, by CI on the pull request that ships it, and
that is the right boundary -- a repository gate must not fail on files the
product itself writes.

Three sources answer that question, in order, and :func:`_population` holds the
reasoning for each: **git**, then the **manifest** ``tools/mutate.py`` records
in a copy it made without a ``.git``, then :func:`_walked`'s name rule as a last
resort. Only the first is a definition; the second carries the first into a tree
that cannot be asked, and the third is a guess whose error :func:`_population`
states.

Lives under ``tests/`` and so inside :data:`UNREAD`, which is load-bearing --
the docstrings below quote dead commands as examples, and
``test_no_file_that_names_a_command_escapes_the_scan`` reports this file by name
the moment that prefix leaves the list, taking ``command_extraction`` and the
integration tests with it (measured, not assumed).

The same list is applied a second time, inside :func:`_files`, and *there* it is
inert today: exactly one file under those prefixes has a scanned suffix --
``tests/e2e/README.md``, which names no command -- so deleting that call changes
no verdict in this repository. It is pinned by a synthetic fixture rather than
left to the next markdown file written under either prefix to discover.
"""

from __future__ import annotations

import functools
import os
import pathlib
import shutil
import subprocess
import warnings
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Final

from command_extraction import (
    Reader,
    json_command_lines,
    markdown_command_lines,
    plain_command_lines,
    python_command_lines,
    unregistered_in,
)

REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[3]


# -- the scan --------------------------------------------------------------


@dataclass(frozen=True)
class Surface:
    """One family of files, and the reader that turns it into command lines."""

    label: str
    root: pathlib.Path
    suffixes: frozenset[str]
    reader: Reader


SCANNED_SURFACES: Final = (
    Surface("markdown", REPO_ROOT, frozenset({".md"}), markdown_command_lines),
    Surface(
        "python",
        REPO_ROOT / "packages" / "theurian-core" / "src",
        frozenset({".py"}),
        python_command_lines,
    ),
    # examples/ ships runnable `.py` whose help strings and comments name real
    # commands -- `query.py` documents the token file as one "written by
    # `theurian daemon start`" -- so it is a user-facing instructional surface
    # and its commands are verified against the CLI, not exempted: a dead command
    # in a shipped example misleads the reader this module exists to protect, and
    # `examples/sample-project/smoke-test.sh` (which would fail on one) runs in no
    # CI job, so the static scan is the only guard here. It needs its own root
    # because the Python surface above is deliberately scoped to Core's `src/` to
    # keep the `.py` scan clear of the two test trees, which name dead commands on
    # purpose (see UNREAD); examples is neither of those.
    Surface(
        "python-examples",
        REPO_ROOT / "examples",
        frozenset({".py"}),
        python_command_lines,
    ),
    # tools/ for the same reason as examples/, one reader further along: a
    # development tool's console output is an instruction somebody follows.
    # `tools/corpus_drift.py` prints a remedy naming `theurian propose` into a CI
    # job log and a step summary, and a maintainer runs what it says. Added when
    # that file landed, on the report of
    # `test_no_file_that_names_a_command_escapes_the_scan` -- which is the guard
    # working: `tools/` held no `theurian <command>` before, so it was neither
    # scanned nor exempted, and the first one to arrive was reported rather than
    # absorbed. Scanned rather than added to UNREAD, because a dead command here
    # misleads exactly the reader this module protects; the test trees are in
    # UNREAD because they name dead commands *on purpose*, which is not true of
    # anything under `tools/`.
    Surface(
        "python-tools",
        REPO_ROOT / "tools",
        frozenset({".py"}),
        python_command_lines,
    ),
    Surface("json", REPO_ROOT, frozenset({".json"}), json_command_lines),
    Surface("plain", REPO_ROOT, frozenset({".sh", ".yml", ".yaml"}), plain_command_lines),
)

#: What git is asked, once per repository. ``--cached`` is the index and the
#: index is what ships.
#:
#: ``-z`` because every other output mode is ambiguous rather than merely
#: awkward: git separates paths with newlines and C-quotes the ones that would
#: break that, so ``docs/two\nlines.md`` comes back as ``"docs/two\\nlines.md"``
#: and this module's split -- which does not unquote -- yields one entry that is
#: no path at all. Measured on a scratch repository. This repository's CJK is
#: file *content*, which is a different problem and not this one.
#:
#: Paths come from the index, bytes from the working tree -- :func:`_text` opens
#: the file, not the blob. Identical in a fresh CI checkout, and where they
#: differ locally the working tree is the right answer: an uncommitted edit that
#: adds a dead command is the one this suite should report.
#:
#: What it leaves out is the fix, and it leaves out more than the ignore rules:
#: an ignored file is untracked by construction, so #262's corpus is excluded
#: for the same reason a draft proposal is -- nobody committed it. ``--others``
#: is what would put both back, which is why it is not here.
_LISTING: Final = ("ls-files", "--cached", "-z")

#: Belt and braces, not the definition. Since the population is git's answer,
#: these two lists decide nothing in a checkout; they are the fallback's
#: approximation of "shipped" for a tree with no git in it, and
#: :func:`_population` records what that approximation costs. Kept as names
#: because there is nothing better to key on once git is gone: ``.claude``,
#: ``.github``, ``.claude-plugin`` and a nested ``.theurian`` are the four dot
#: directories holding tracked files (``git ls-files``, bd4fb25).
SHIPPED_DOT_DIRECTORIES: Final = frozenset({".github", ".claude", ".claude-plugin", ".theurian"})

#: Directory names the fallback never enters even though they do not start with
#: a dot. Build and coverage output, vendored packages, and -- the one that is
#: not obvious -- ``worktrees``, because ``.claude/worktrees/`` is where this
#: machine keeps agent checkouts of the repository itself. Walking one would
#: scan a second copy of every file below, which both doubles the population and
#: makes the result depend on who else is working today. Git needs none of this:
#: an ignored directory is not listed, and a nested checkout comes back as one
#: entry that :func:`_population` drops because it is not a file.
PRUNED_DIRECTORIES: Final = frozenset(
    {"__pycache__", "node_modules", "htmlcov", "dist", "site", "worktrees"}
)


def _walked(names: Iterable[str], *, at_repository_root: bool) -> list[str]:
    """The subdirectories of one directory the *last-resort* walk treats as shipped.

    ``.theurian/`` at the top of the tree is refused, and only there. That is
    where a project keeps its own knowledge, and a copy of a dogfooding checkout
    is precisely where this runs. Deeper it is sample content -- the scan has
    always read ``examples/sample-project/.theurian/config.yaml``.

    **This rule is wrong the moment the repository tracks its own knowledge,
    and that is why it is the last resort rather than the fallback.** It rests
    on ``git ls-files .theurian`` being empty, measured at bd4fb25;
    ``dogfood/dev7-corpus`` puts 81 tracked files under ``.theurian/`` -- 26
    knowledge documents, 26 migrations, 26 proposal evidence files and 3
    ``.gitkeep`` placeholders (measured 2026-08-20; the earlier "27 migrations,
    27 proposals and one specification" counted a placeholder as a member of
    each directory it holds open) -- and the 26 sit beside the untracked
    local-only notes of #262, in the same
    directory. No name can separate those two, so a narrower rule is not
    available: the manifest :func:`_population` prefers is what carries the real
    answer into a tree with no git, and this is what is left when even that is
    absent.
    """
    return sorted(
        name
        for name in names
        if name not in PRUNED_DIRECTORIES
        and (not name.startswith(".") or name in SHIPPED_DOT_DIRECTORIES)
        and not (at_repository_root and name == ".theurian")
    )


#: Where ``tools/mutate.py`` records the source checkout's tracked paths when it
#: copies the tree without a ``.git`` -- see ``_POPULATION_NAME`` there, which is
#: the other half of this contract. The bytes are ``git ls-files --cached -z``
#: output verbatim, so :func:`_entries` parses both ends.
_POPULATION_MANIFEST: Final = ".mutate-population"


def _entries(listing: str) -> tuple[str, ...]:
    """The paths in one ``-z`` listing: deduplicated, sorted, empties dropped.

    Two jobs, and only one of them is load-bearing. ``dict.fromkeys`` is
    correctness: the index holds up to three entries for an unmerged path --
    base, ours, theirs -- and ``ls-files`` prints the path once per stage, so a
    merge conflict would otherwise put a file into the population three times
    and every invocation in it three times with it. ``sorted`` is determinism
    only, and cheap belt: git already emits index order, which is sorted by path
    bytes and so identical to this. Deleting it changes nothing today, which is
    why no test pins it -- deleting the dedupe fails
    ``test_a_path_left_unmerged_by_a_conflict_is_listed_once``.
    """
    return tuple(sorted(dict.fromkeys(entry for entry in listing.split("\0") if entry)))


def _manifest_listing(repository: pathlib.Path) -> tuple[str, ...] | None:
    """The population the mutation harness recorded for this copy, if it did.

    ``None`` when the file is absent, which is every ordinary run; when it
    cannot be read; and -- the two that are not obvious -- when it is **empty**
    or **truncated**.

    ``ls-files -z`` terminates every entry including the last, so a manifest
    that does not end in a NUL was cut short, and an empty one fails the same
    test. Both would otherwise be adopted as the answer: a population of
    nothing, or a population silently missing whatever was being written when
    the write stopped. This function's whole job is to say "I do not know",
    because the caller can fall back and cannot detect a short answer.
    """
    manifest = repository / _POPULATION_MANIFEST
    try:
        listing = manifest.read_text(encoding="utf-8", errors="surrogateescape")
    except OSError:
        return None
    if not listing.endswith("\0"):
        return None
    return _entries(listing) or None


def _git_listing(repository: pathlib.Path) -> tuple[str, ...] | None:
    """Every path git reports for ``repository``, or ``None`` if it cannot be asked.

    ``None`` means one of three things, and the caller treats them alike: no
    git on this machine, the tree is not a working copy, or the tree sits
    *inside* somebody else's working copy -- a copy of the checkout unpacked
    below an unrelated repository, which is one ``TMPDIR`` away from real.

    The third is why the toplevel is checked rather than a zero exit trusted.
    Asked from inside such a copy, git answers for the *outer* repository's
    index, which holds none of these paths: measured on a scratch repository,
    the listing comes back empty and the exit code is 0.

    An empty population is *caught* -- ``test_the_scan_reaches_every_arm_of_every
    _reader``, ``test_no_recorded_exception_outlives_the_text_it_excuses`` and
    the floor in ``test_no_file_that_names_a_command_escapes_the_scan`` all go
    RED on one, measured by returning ``()`` here. So the check is not what
    stands between this module and a silent pass; it is what keeps a legitimate
    no-git tree, which is what the mutation harness runs in, from taking a
    *wrong* answer instead of the fallback and failing for a reason
    that has nothing to do with the tree.
    """
    git = shutil.which("git")
    if git is None:
        return None
    toplevel = _git_output(git, repository, "rev-parse", "--show-toplevel")
    if toplevel is None or pathlib.Path(toplevel.strip()).resolve() != repository.resolve():
        return None
    listing = _git_output(git, repository, *_LISTING)
    if listing is None:
        return None
    return _entries(listing)


#: Environment variables that make git answer for a *different* tree, index or
#: configuration than the one it was handed, dropped before it is asked. The
#: toplevel check cannot catch them: ``GIT_INDEX_FILE`` binds the index and not
#: the working tree, so ``--show-toplevel`` still names this repository while
#: ``ls-files --cached`` reads somebody else's index and returns nothing
#: (measured). Nobody exports these by hand -- git exports them to hooks, so a
#: suite run from ``pre-commit`` or ``post-merge`` inherits them.
#:
#: ``GIT_CEILING_DIRECTORIES`` is deliberately *not* here, and that is the whole
#: asymmetry with the sandbox fixture, which sets it: it can only make git look
#: less far, which ends at the fallback, never at a wrong answer. What this
#: module owns is which tree and which index answer; how far git may look to
#: find one it does not.
_INHERITED_GIT_OVERRIDES: Final = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_WORK_TREE",
    }
)

#: Long enough that no healthy ``ls-files`` reaches it -- this repository's is
#: 11 ms -- and short enough that a git waiting on a lock or on credentials
#: ends the run instead of hanging the gate.
_GIT_TIMEOUT_SECONDS: Final = 30

#: The one failure that means "there is nothing to ask here" rather than "the
#: question went wrong". Every other stderr is reported, because a
#: ``safe.directory`` refusal and a missing repository would otherwise both
#: arrive as the same silent fallback -- and only one of them is expected.
_NO_REPOSITORY: Final = "not a git repository"


def _git_output(git: str, repository: pathlib.Path, *arguments: str) -> str | None:
    """One read-only git command's stdout, or ``None`` if it could not be asked.

    ``surrogateescape`` rather than a decode error: a path this suite must
    report is a path it first has to be able to name.
    """
    try:
        completed = subprocess.run(  # noqa: S603 - argv is module-owned, never user input
            [git, "-C", str(repository), *arguments],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="surrogateescape",
            env={
                name: value
                for name, value in os.environ.items()
                if name not in _INHERITED_GIT_OVERRIDES
            },
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        warnings.warn(
            f"`git {' '.join(arguments)}` in {repository} did not finish in "
            f"{_GIT_TIMEOUT_SECONDS}s, so the population cannot be defined by the index. "
            "Under this suite's `filterwarnings = error` that ends the run here; outside "
            "it, the caller falls back to a manifest or a walk.",
            RuntimeWarning,
            stacklevel=2,
        )
        return None
    if completed.returncode == 0:
        return completed.stdout
    if _NO_REPOSITORY not in completed.stderr:
        warnings.warn(
            f"`git {' '.join(arguments)}` in {repository} exited "
            f"{completed.returncode}: {completed.stderr.strip()}. The population cannot be "
            "defined by the index. Under this suite's `filterwarnings = error` that ends "
            "the run here; outside it, the caller falls back and reads a different set of "
            "files.",
            RuntimeWarning,
            stacklevel=2,
        )
    return None


@functools.cache
def _population(repository: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """Every file the repository ships, sorted, from the best source available.

    Three sources, in order, and the order is the point: **git**, then a
    **manifest** the mutation harness recorded, then a **walk**.

    Cached because several tests want it and it costs a subprocess; the walk it
    replaced was paid per call.

    The second source exists because ``tools/mutate.py`` copies the checkout
    with ``shutil.copytree`` and its ``_COPY_IGNORE`` drops ``.git``
    deliberately, so the suite runs there with no repository to ask -- while the
    copy still carries every untracked file the developer's tree carried,
    local-only knowledge and half-written proposals alike. The harness now
    writes ``git ls-files --cached -z`` into the copy as
    :data:`_POPULATION_MANIFEST`, so the copy scans exactly what the gate scans.

    A name-based guess is what is left when there is neither, and it was the
    whole answer until ``dogfood/dev7-corpus`` made it untenable. Measured on
    that branch: :func:`_walked` refuses 81 tracked files under ``.theurian/``
    -- 26 knowledge documents, 26 migrations, 26 proposal evidence files and 3
    ``.gitkeep`` placeholders -- of which 78 carry a scanned suffix (the three
    placeholders do not). The gate's whole scanned population
    there is 321 files, so the guess drops **24% of it**. A harness grading
    mutations against that is answering for a suite that does not exist.

    The manifest carries the answer rather than a cleverer name rule because no
    name can distinguish the 26 tracked knowledge documents from the untracked
    local-only notes of #262: both live in ``.theurian/knowledge/``, and one set
    is in every clone while the other is in no clone.

    The residual is stated rather than hidden: in a tree with neither git nor a
    manifest, an untracked file outside the repository-root ``.theurian/`` --
    say a scratch note under ``docs/`` -- is still read. That is one machine's
    copy, never CI and never a clone, and the gate that decides anything runs
    in a checkout where git answers.
    """
    listed = _git_listing(repository)
    if listed is None:
        listed = _manifest_listing(repository)
    if listed is not None:
        return tuple(path for entry in listed if (path := repository / entry).is_file())

    found: list[pathlib.Path] = []
    for base, directories, names in os.walk(repository):
        directories[:] = _walked(directories, at_repository_root=pathlib.Path(base) == repository)
        found.extend(path for name in names if (path := pathlib.Path(base) / name).is_file())
    # Sorted on the repository-relative posix string, which is the key the git
    # branch gets for free from `ls-files`. Sorting the paths themselves is
    # component-wise, so `docs/b.md` would come back before `docs-x/a.md` here
    # and after it there, and `_scan` caches whichever order it was handed.
    return tuple(sorted(found, key=lambda path: path.relative_to(repository).as_posix()))


@dataclass(frozen=True)
class Unread:
    """A path prefix no reader looks at, and the reason that is safe."""

    prefix: str
    reason: str


#: The whole of the exclusion. Everything else in the repository is either read
#: by a :data:`SCANNED_SURFACES` entry or holds no ``theurian <command>`` at all,
#: and :func:`test_no_file_that_names_a_command_escapes_the_scan` is what turns
#: that second half from a claim into a check.
UNREAD: Final = (
    Unread(
        prefix="packages/theurian-core/tests/",
        reason="a test that names a dead command and runs it fails on its own, and the "
        "fixtures in this very file name dead commands on purpose",
    ),
    Unread(
        prefix="tests/",
        reason="the same, for the end-to-end tree",
    ),
)


def _is_unread(relative: str) -> bool:
    return any(relative.startswith(entry.prefix) for entry in UNREAD)


def _files(
    root: pathlib.Path, suffixes: frozenset[str], repository: pathlib.Path = REPO_ROOT
) -> Iterator[pathlib.Path]:
    """Every shipped file of those suffixes under ``root``, in path order.

    ``root`` selects a subtree of ``repository``: the Python reader runs over
    two roots -- Core's ``src/`` and ``examples/`` -- while the rest read from the
    top. All are filtered out of one population rather than walked separately, so
    a file cannot be part of one answer and not the other.

    ``repository`` is a parameter and not just :data:`REPO_ROOT` because that is
    what makes the population testable: a sandbox with one tracked and one
    ignored file is the only way to show that this reads git's answer rather
    than the disk's.
    """
    for path in _population(repository):
        if path.suffix not in suffixes or not path.is_relative_to(root):
            continue
        if _is_unread(path.relative_to(repository).as_posix()):
            continue
        yield path


def _text(path: pathlib.Path) -> str:
    """Read a file the scan is responsible for, naming it if it is not UTF-8.

    Bare :meth:`~pathlib.Path.read_text` raises a ``UnicodeDecodeError`` that
    names the byte and not the file, which is a long way from the file when the
    walk covers the whole repository.
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        message = f"{path.relative_to(REPO_ROOT).as_posix()} is not UTF-8 ({error})"
        raise AssertionError(message) from error


@dataclass(frozen=True)
class Invocation:
    """One ``theurian <command>`` a repository file tells somebody to run."""

    path: str
    line: int
    command: str
    span: str

    @property
    def literal(self) -> str:
        """The text an exemption is anchored to, and a reader can grep for."""
        return f"theurian {self.command}"

    @property
    def anchor(self) -> tuple[str, str]:
        return self.path, self.literal

    @property
    def excerpt(self) -> str:
        """The quoted text, whitespace-normalised, as an exemption records it.

        This is the key an :class:`Exemption` is matched on, so it must not
        depend on a reader arm's side effects. Every arm happens to hand back
        single-spaced text today -- :func:`_unwrap` collapses a wrap and
        :func:`_fence_lines` joins with one space -- which makes the
        normalisation here look unreachable and makes deleting it survive the
        whole suite. It is not unreachable: it is what stops a *future* arm, or
        a reader that stops collapsing, from silently invalidating every
        recorded permission at once.
        """
        return " ".join(self.span.split())


@functools.cache
def _scan() -> tuple[Invocation, ...]:
    """Every unregistered invocation in the repository, one entry per occurrence.

    Not deduplicated, and that is what makes an :class:`Exemption`'s occurrence
    count mean something: ``plugins/claude-code/CHANGELOG.md`` names two dead
    invocations on line 218 and the threat model names the same one twice on line
    1106, so collapsing by ``(path, line, command)`` would license a third. The
    readers are made not to overlap instead -- see :func:`_prose_of`.

    Cached because four tests want the same answer and it reads every file in
    the repository. Deterministic for the same reason it is cacheable: the
    surfaces are ordered, :func:`_files` yields :func:`_population`'s order --
    sorted on the repository-relative posix path, the same key whichever of its
    three sources answered -- and each reader is a generator over one text.
    """
    return tuple(
        Invocation(relative, span.line, command, span.text)
        for surface in SCANNED_SURFACES
        for path in _files(surface.root, surface.suffixes)
        for relative in (path.relative_to(REPO_ROOT).as_posix(),)
        for span in surface.reader(_text(path))
        for command in unregistered_in(span.text, prose=span.prose)
    )
