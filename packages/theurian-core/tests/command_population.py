"""Which files the scan opens, and the invocations they turn out to hold.

Split from ``tests/unit/test_documented_commands.py``, which owns the recorded
exemptions and the assertions, and sitting beside ``command_extraction``, which
owns turning one text into command words. This module is the middle: the walk
over the repository, and what it hands the readers.

A pure move; every reason recorded here was written where the code was.

Lives under ``tests/`` and so inside :data:`UNREAD`, which is load-bearing --
the docstrings below quote dead commands as examples, and a reader that opened
this file would report every one of them.
"""

from __future__ import annotations

import functools
import os
import pathlib
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

#: The dot directories this repository *ships*. Every other one is somebody's
#: tooling state and is not walked, which is a rule rather than a list because a
#: list of the ones seen so far is a list that keeps being wrong: the mutation
#: harness runs the suite with ``TMPDIR`` inside the copied tree, so a run there
#: put 12,734 fixture files -- whole ``.theurian`` project directories, some of
#: them not even UTF-8 -- under ``.mutate-tmp/``, and the scan read them. The
#: control run went RED and every verdict in the batch with it.
#:
#: The residual is stated rather than hidden: a *fourth* dot directory that
#: ships instructions would escape the scan, and nothing here would say so. The
#: list is three entries long and sits beside the rule for that reason.
SHIPPED_DOT_DIRECTORIES: Final = frozenset({".github", ".claude", ".theurian"})

#: Directory names the walk never enters even though they do not start with a
#: dot. Build and coverage output, vendored packages, and -- the one that is not
#: obvious -- ``worktrees``, because ``.claude/worktrees/`` is where this machine
#: keeps agent checkouts of the repository itself. Walking one would scan a
#: second copy of every file below, which both doubles the population and makes
#: the result depend on who else is working today.
PRUNED_DIRECTORIES: Final = frozenset(
    {"__pycache__", "node_modules", "htmlcov", "dist", "site", "worktrees"}
)


def _walked(names: Iterable[str]) -> list[str]:
    """The subdirectories of one directory that are part of the repository."""
    return sorted(
        name
        for name in names
        if name not in PRUNED_DIRECTORIES
        and (not name.startswith(".") or name in SHIPPED_DOT_DIRECTORIES)
    )


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


def _files(root: pathlib.Path, suffixes: frozenset[str]) -> Iterator[pathlib.Path]:
    """Every file of those suffixes under ``root``, in a fixed order.

    ``os.walk`` rather than :meth:`~pathlib.Path.rglob` because the directories
    have to be pruned *during* the walk. Filtering afterwards still descends
    into a 149 MB virtualenv, into every sibling worktree, and into the twelve
    thousand fixture files a suite run leaves under a redirected ``TMPDIR``.
    """
    for base, directories, names in os.walk(root):
        directories[:] = _walked(directories)
        for name in sorted(names):
            path = pathlib.Path(base) / name
            if path.suffix in suffixes and not _is_unread(path.relative_to(REPO_ROOT).as_posix()):
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
