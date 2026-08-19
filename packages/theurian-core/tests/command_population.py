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

:func:`_walked` is the fallback for a tree with no git in it, and
:func:`_population` says what it costs.

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
    Surface("json", REPO_ROOT, frozenset({".json"}), json_command_lines),
    Surface("plain", REPO_ROOT, frozenset({".sh", ".yml", ".yaml"}), plain_command_lines),
)

#: What git is asked, once per repository. ``--cached`` is the index and the
#: index is what ships; ``-z`` because git quotes non-ASCII paths in every other
#: output mode, and this repository holds CJK fixtures.
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
    """The subdirectories of one directory the *fallback* treats as shipped.

    ``.theurian/`` at the top of the tree is refused, and only there. That is
    where a project keeps its own knowledge, this repository tracks nothing
    under it, and a copy of a dogfooding checkout is precisely where the
    fallback runs. Deeper it is sample content -- the scan has always read
    ``examples/sample-project/.theurian/config.yaml``.
    """
    return sorted(
        name
        for name in names
        if name not in PRUNED_DIRECTORIES
        and (not name.startswith(".") or name in SHIPPED_DOT_DIRECTORIES)
        and not (at_repository_root and name == ".theurian")
    )


def _git_listing(repository: pathlib.Path) -> tuple[str, ...] | None:
    """Every path git reports for ``repository``, or ``None`` if it cannot be asked.

    ``None`` means one of three things, and the caller treats them alike: no
    git on this machine, the tree is not a working copy, or the tree sits
    *inside* somebody else's working copy -- a copy of the checkout unpacked
    below an unrelated repository, which is one ``TMPDIR`` away from real.

    The third is why the toplevel is checked rather than a zero exit trusted.
    Asked from inside such a copy, git answers for the *outer* repository's
    index, which holds none of these paths: measured on a scratch repository,
    the listing comes back empty and the exit code is 0. That is the failure
    this module cannot survive, because every assertion in it passes when no
    file is read -- and it is silent, which nothing else here is.
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
    return tuple(sorted(entry for entry in listing.split("\0") if entry))


def _git_output(git: str, repository: pathlib.Path, *arguments: str) -> str | None:
    """One read-only git command's stdout, or ``None`` if it failed.

    ``surrogateescape`` rather than a decode error: a path this suite must
    report is a path it first has to be able to name.
    """
    completed = subprocess.run(  # noqa: S603 - argv is module-owned, never user input
        [git, "-C", str(repository), *arguments],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="surrogateescape",
    )
    return None if completed.returncode != 0 else completed.stdout


@functools.cache
def _population(repository: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """Every file the repository ships, sorted, git's answer where there is one.

    Cached because several tests want it and it costs a subprocess; the walk it
    replaced was paid per call.

    The fallback is what runs when :func:`_git_listing` cannot answer, and the
    mutation harness is why it exists rather than an assertion: ``tools/mutate.py``
    copies the checkout with ``shutil.copytree`` and its ``_COPY_IGNORE`` drops
    ``.git`` deliberately, so the suite runs there with no repository at all --
    while the copy still carries every untracked file the developer's tree
    carried, local-only knowledge and half-written proposals alike. The fallback
    cannot tell those from tracked ones, so it reads *less*: :func:`_walked`
    refuses the repository-root ``.theurian/`` outright, which is where both of
    them live.

    The residual is stated rather than hidden -- an untracked file anywhere
    else, say a scratch note under ``docs/``, is still read there. That is a
    copy of one machine's tree, never CI and never a clone, and the gate that
    decides anything runs in a checkout where git answers.
    """
    listed = _git_listing(repository)
    if listed is not None:
        return tuple(path for entry in listed if (path := repository / entry).is_file())

    found: list[pathlib.Path] = []
    for base, directories, names in os.walk(repository):
        directories[:] = _walked(directories, at_repository_root=pathlib.Path(base) == repository)
        found.extend(path for name in names if (path := pathlib.Path(base) / name).is_file())
    return tuple(sorted(found))


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

    ``root`` selects a subtree of ``repository``: the Python surface reads only
    Core's ``src/`` while the rest read from the top. Both are filtered out of
    one population rather than walked separately, so a file cannot be part of
    one answer and not the other.

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

    Cached because four tests want the same answer and the walk reads every file
    in the repository. Deterministic for the same reason it is cacheable: the
    surfaces are ordered, :func:`_files` sorts, and each reader is a generator
    over one text.
    """
    return tuple(
        Invocation(relative, span.line, command, span.text)
        for surface in SCANNED_SURFACES
        for path in _files(surface.root, surface.suffixes)
        for relative in (path.relative_to(REPO_ROOT).as_posix(),)
        for span in surface.reader(_text(path))
        for command in unregistered_in(span.text, prose=span.prose)
    )
