"""Ask git whether a caller's ``fixCommit`` is a commit here that touched a file (ADR-0033).

Decision 3's verification, and the whole of what makes ``fix_commit_present`` a
signal the caller has to *find something real* to satisfy rather than one it
asserts. The shipped GitHub adapter never fills ``ReviewResolution.fix_commit``,
so a gate that read the signal off the stored record would refuse every ingested
thread while a hand-authored evidence file passed it -- the inversion ADR-0033
measured and rejected.

**Two calls, because the second is what separates a verification from an
existence check.** ``rev-parse`` answers whether the name resolves to a commit
here; ``diff-tree`` answers whether that commit touched the thread's path. An
implementation that stopped at the first would make the signal satisfiable by any
commit in the repository -- 285 of them at ADR-0033's measurement.

**Both arguments are untrusted, and they are untrusted in different ways.** The
sha is caller-supplied wire input; the path is author-controlled stored data a
clone can deliver (T-3, T-24). Three shapes are foreclosed on every invocation:

* ``--end-of-options`` precedes the sha, so a value spelled ``--upload-pack=...``
  is a revision git fails to resolve rather than an option it honours;
* ``--`` precedes the path, so an option-shaped ``filePath`` is a pathspec;
* ``--literal-pathspecs`` disarms pathspec *magic*, which the two above do not
  reach. Measured with git 2.47.1 on 2026-09-18 in a throwaway repository: the
  pathspec ``:(exclude)src/retrying.py`` against a commit that touched only
  ``docs/notes.md`` prints ``docs/notes.md`` -- a non-empty answer, which is this
  module's ``VERIFIED`` -- and prints nothing under ``--literal-pathspecs``.
  ``:(glob)**/*.md`` behaves the same way. So a stored ``filePath`` carrying one
  prefix would have verified a commit that touched anything *but* the file the
  thread is anchored to.

``diff-tree``'s revision argument is not the caller's string at all: it is the
object id ``rev-parse`` printed, re-checked as hex here before it is spent, so
the second call cannot be reached with an argument of any other shape.

**The binary is resolved to an absolute path**, the ``gh`` precedent tier
(ADR-0030 clause 5) its ``committed_check.py`` sibling takes, because this call
decides a promotion signal and letting an inherited ``PATH`` choose the
executable that answers it is the shape that clause refuses. It reaches no
network -- ``rev-parse`` and ``diff-tree`` read local object storage, name no
remote and take no URL -- and is on ``PROCESS_SPAWN_SITES`` only because it
spawns a process.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Final, final

from theurian.domain.review import FixCommitVerdict

#: Timeout on each ``git`` call this adapter spawns. Two local object reads are
#: cheap, and an unbounded subprocess on a daemon-reachable path is a hang the
#: caller cannot explain (SEC-19). The same five seconds ``committed_check.py``
#: and the ``cli/context.py`` reads bound at; ``trailer_source.py``'s 30 is for a
#: full-history ``git log``, not one commit.
GIT_TIMEOUT_SECONDS: Final = 5.0

#: A git object id as ``rev-parse --verify`` prints one, in either object format
#: (40 hex for SHA-1, 64 for SHA-256). Applied to git's own output rather than to
#: the caller's input: it is what lets the ``diff-tree`` call below state that its
#: revision argument cannot be option-shaped.
_OBJECT_ID: Final = re.compile(r"[0-9a-f]{40,64}")


@final
class FixCommitCheck:
    """Answers whether a named commit exists here and touched a given path.

    Built with the working tree to ask; the git binary is resolved to an absolute
    path once, at construction (the module docstring says why this call resolves
    it rather than leaving it to the child's ``PATH``).
    """

    def __init__(self, repo_root: Path) -> None:
        found = shutil.which("git", path=os.environ.get("PATH"))
        self._git: Path | None = Path(found).resolve() if found is not None else None
        self._repo_root = repo_root

    def verify(self, commit: str, file_path: str) -> FixCommitVerdict:
        """Which of the three answers *commit* earns against *file_path*.

        Fail-closed in both directions: a git that cannot be run, times out, or
        answers in a shape this module does not recognise yields a refusing
        verdict, never :attr:`~theurian.domain.review.FixCommitVerdict.VERIFIED`.
        """
        object_id = self._resolved_commit(commit)
        if object_id is None:
            return FixCommitVerdict.NO_SUCH_COMMIT
        if not self._touches(object_id, file_path):
            return FixCommitVerdict.TOUCHES_NOTHING_HERE
        return FixCommitVerdict.VERIFIED

    def _resolved_commit(self, commit: str) -> str | None:
        """The object id *commit* names here, or ``None`` if it names no commit.

        ``--verify --quiet`` prints the id alone and exits non-zero when the
        argument resolves to nothing -- **except for a full-length hex id, where it
        does not check existence at all**: measured on git 2.47.1, that form printed
        ``"e" * 40`` and exited 0 in a repository holding no such object. So the
        ``^{commit}`` dereference is not tag-or-tree normalisation but the whole of
        what refuses a fabricated sha (ADR-0033 decision 3's first class); trimming
        it reopens that class, and
        ``tests/integration/test_fix_commit_check_adapter.py`` is what goes red.
        """
        completed = self._run(
            ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{commit}^{{commit}}"]
        )
        if completed is None or completed.returncode != 0:
            return None
        printed = completed.stdout.decode("ascii", "replace").strip()
        return printed if _OBJECT_ID.fullmatch(printed) else None

    def _touches(self, object_id: str, file_path: str) -> bool:
        """Whether the commit *object_id* changed anything at *file_path*.

        ``--root`` is load-bearing: without it a repository's first commit reports
        no files at all, so a fix that *is* the first commit would read as
        touching nothing. ``--literal-pathspecs`` and the ``--`` separator are the
        module docstring's foreclosures.
        """
        completed = self._run(
            [
                "--literal-pathspecs",
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                "--root",
                "--end-of-options",
                object_id,
                "--",
                file_path,
            ]
        )
        if completed is None or completed.returncode != 0:
            return False
        return bool(completed.stdout.strip())

    def _run(self, git_args: list[str]) -> subprocess.CompletedProcess[bytes] | None:
        """Spawn one ``git`` call, or ``None`` if the binary is absent or it fails.

        The single spawn site in this module (``PROCESS_SPAWN_SITES`` records the
        module, not the call count). Fixed vector, no shell.
        """
        if self._git is None:
            return None
        try:
            return subprocess.run(  # noqa: S603 - fixed adapter-controlled vector, no shell; the caller's sha sits behind `--end-of-options` and the stored path behind `--` under `--literal-pathspecs`, so neither is read as an option, a revision or a pathspec expression (SEC-9)
                [str(self._git), *git_args],
                cwd=self._repo_root,
                capture_output=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
