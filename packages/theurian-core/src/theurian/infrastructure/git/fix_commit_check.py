"""Ask git whether a caller's ``fixCommit`` is a commit here that touched a file (ADR-0033).

Decision 3's verification, and the whole of what makes ``fix_commit_present`` a
signal the caller has to *find something real* to satisfy rather than one it
asserts. The shipped GitHub adapter never fills ``ReviewResolution.fix_commit``,
so a gate that read the signal off the stored record would refuse every ingested
thread while a hand-authored evidence file passed it -- the inversion ADR-0033
measured and rejected.

**``fixCommit`` is a git revision expression unless the adapter stops it, and the
entry funnel is what stops it.** ``verify`` refuses anything that is not a
full-length lower-case object name -- forty hex digits or sixty-four -- with
``NO_SUCH_COMMIT`` before any process exists. Two reviewers independently
recovered a commit's message through the revision language: ``HEAD^{/<text>}``
searches history and ``:/<text>|zzzz`` searches every ref, and the alternation
puts the appended ``^{commit}`` inside the second branch so the first branch
still matches -- the suffix forecloses nothing, and a caller who knew no sha
could satisfy ``fix_commit_present`` (ADR-0033 decision 3) by describing the
commit it wanted. A grammar miss spawns nothing, so malformed input carries no
timing arm at all; the residual is one existence bit about a *valid* full-hex
sha, which the byte-identity pins hold. ``tests/fix_commit_grammar.py`` is the
shared corpus, asked at the wire and here.

**One process, for every input the grammar admits, and that is a disclosure
control rather than a saving.** This adapter asked two questions in two spawns
until C4c -- *does this name resolve to a commit* (``rev-parse``), then *did that
commit touch this path* (``diff-tree``) -- and the first could answer no on its
own, so a refusal about an absent object cost one process and a refusal about a
real commit cost two. The
C4b battery measured the pair end to end at **+7.2 ms, P=1.000**: the refusal's
*duration* answered "does this object exist here", which is a fact about the
repository the caller was not granted. ADR-0033 decision 5 binds the two
refusals in text **and in duration**, so both questions are now one ``diff-tree``
call and the verdict is read off its exit code and its output:

===================  ==========================================================
non-zero exit        ``NO_SUCH_COMMIT`` -- measured 128 for an absent object and
                     for a name that is not a commit; also every fail-closed
                     reading, where git could not be run at all
exit 0, no output    ``TOUCHES_NOTHING_HERE``
exit 0, output       ``VERIFIED``
===================  ==========================================================

**Both arguments are untrusted, and they are untrusted differently.** The sha is
caller wire input; the path is author-controlled stored data a clone can deliver
(T-3, T-24). Five tokens shape the single ``diff-tree`` call, and what each is
worth was re-measured under this shape on git 2.47.1, 2026-09-19 -- two because a
``rev-parse`` behaviour that once justified them no longer runs, and
``--diff-merges=first-parent`` because it is new:

* ``--literal-pathspecs`` -- **load-bearing.** Against a commit touching only
  ``docs/notes.md``, the stored paths ``:(exclude)src/retrying.py``,
  ``:!src/retrying.py``, ``:(glob)**/*.md`` and ``:(top)`` each make ``diff-tree``
  print ``docs/notes.md``: a non-empty answer, which is this module's
  ``VERIFIED``. Each prints nothing under the flag.
* ``--root`` -- **load-bearing.** A repository's first commit reports no files
  without it (measured: empty output where the flag gives ``src/retrying.py``),
  so a fix that *is* the root commit would read as touching nothing.
* ``--diff-merges=first-parent`` -- **load-bearing, and the only token here that
  changes a verdict for an honest input.** ``diff-tree`` prints nothing for a
  merge commit by default, so a conflict-resolving merge that introduced the fix
  read ``TOUCHES_NOTHING_HERE`` -- a true fix, correctly named, refused. The mode
  is *this merge changed the file relative to the branch it landed on*, which
  names the merge rather than some parent; the integration battery enumerates the
  three merge shapes that rule out ``separate`` and ``combined``, and plain-commit
  verdicts are unchanged.
* ``^{commit}`` on the revision -- **load-bearing, for a different reason than it
  used to be.** Under the two-call shape it was what refused a fabricated forty
  hex digits, because ``rev-parse --verify`` accepts a full-width hex string as an
  object *name* without asking whether the object is present. ``diff-tree`` does
  not: ``eeee…eeee`` exits 128 with the suffix and without it, so that
  justification did not survive the collapse and is recorded here as history
  rather than repeated. What the suffix holds now is **commit-only** semantics --
  a tree id and a blob id each exit 0 with empty output without it, which this
  module would read as ``TOUCHES_NOTHING_HERE``, i.e. as *a commit was found*,
  and exit 128 with it.
* ``--end-of-options`` before the sha -- **defence in depth over a value the
  funnel has already refused.** An option-shaped sha exits 128 behind the flag and
  129, git's usage error, without it; both are non-zero, so both were the same
  verdict even before the funnel, and no file was created in either reading. Since
  the funnel, no option-shaped value reaches the vector at all, so the flag guards
  the token position rather than any input a caller can send. ``--`` before the
  path is the same kind of token on the same vector, and the one that still guards
  a live input: the path is not funnelled, so an option-shaped ``filePath`` out of
  an evidence file stays a pathspec.

``tests/integration/test_fix_commit_check_adapter.py`` holds all five, and names
which of them a behavioural case can reach and which only its captured-vector pin
can.

**The binary is resolved to an absolute path**, the ``gh`` precedent tier
(ADR-0030 clause 5) its ``committed_check.py`` sibling takes, because this call
decides a promotion signal and letting an inherited ``PATH`` choose the
executable that answers it is the shape that clause refuses. It reaches no
network -- ``diff-tree`` reads local object storage, names no remote and takes no
URL -- and is on ``PROCESS_SPAWN_SITES`` only because it spawns a process.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Final, final

from theurian.domain.review import FixCommitVerdict

#: Timeout on the one ``git`` call this adapter spawns. A single local object read
#: is cheap, and an unbounded subprocess on a daemon-reachable path is a hang the
#: caller cannot explain (SEC-19). The same five seconds ``committed_check.py``
#: and the ``cli/context.py`` reads bound at; ``trailer_source.py``'s 30 is for a
#: full-history ``git log``, not one commit.
GIT_TIMEOUT_SECONDS: Final = 5.0

#: The widest object name the entry funnel admits -- a SHA-256 id, sixty-four
#: lower-case hex digits -- and so the ``maxLength`` the published input schema
#: carries for ``fixCommit`` (ADR-0031 decision 6, held by
#: ``test_input_schema_bounds.py``). A SHA-1 name is forty, the funnel's other
#: admitted width.
MAX_FIX_COMMIT_CHARS: Final = 64


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

        The grammar funnel runs first: a *commit* that is not a full-length
        lower-case object name is ``NO_SUCH_COMMIT`` before any process exists, so
        no revision expression is ever spent as one (module docstring). *file_path*
        is not funnelled -- it is author-controlled stored data (T-24) -- so a NUL
        byte it can carry is caught by ``_run``'s fail-closed ``except`` instead.

        Past the funnel it is one question, whatever the answer turns out to be:
        the branch that used to skip the second spawn is what made an absent object
        cheaper to refuse than a real one. So the outcome is read off one call
        rather than chosen between two, and the fail-closed readings -- git absent,
        a spawn that raised, a timeout -- join the non-zero exits on the refusing
        side rather than adding a path of their own.
        """
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit) is None:
            return FixCommitVerdict.NO_SUCH_COMMIT
        completed = self._run(
            [
                "--literal-pathspecs",
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                "--root",
                "--diff-merges=first-parent",
                "--end-of-options",
                f"{commit}^{{commit}}",
                "--",
                file_path,
            ]
        )
        if completed is None or completed.returncode != 0:
            return FixCommitVerdict.NO_SUCH_COMMIT
        if not completed.stdout.strip():
            return FixCommitVerdict.TOUCHES_NOTHING_HERE
        return FixCommitVerdict.VERIFIED

    def _run(self, git_args: list[str]) -> subprocess.CompletedProcess[bytes] | None:
        """Spawn the ``git`` call, or ``None`` if the binary is absent or it fails.

        The single spawn site in this module, and since C4c that is true in the
        stronger sense as well: one site, reached once per verification, so the
        count ``PROCESS_SPAWN_SITES`` deliberately does not record is one anyway.
        ``test_the_module_reaches_a_spawn_from_exactly_one_place`` counts the
        initiations that reach here; the runtime pins beside it count the spawns a
        verification actually makes. Fixed vector, no shell.

        The ``except`` names ``ValueError`` because a NUL byte in the stored
        ``file_path`` (T-24) raises it out of ``subprocess`` -- neither ``OSError``
        nor ``TimeoutExpired`` -- and a stored value must earn a verdict, never a
        traceback across the tool seam.
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
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None
