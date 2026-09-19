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
refusals in text **and in duration**, so both questions are now one ``git log``
call and the verdict is read off its exit code and whether the stored path's UTF-8
bytes are among the raw path entries ``-z`` emits:

============================  =================================================
non-zero exit                 ``NO_SUCH_COMMIT`` -- measured 128 for an absent
                              object and for a name that is not a commit; also
                              every fail-closed reading, where git could not be
                              run at all
exit 0, path is an entry      ``VERIFIED``
exit 0, path is not an entry  ``TOUCHES_NOTHING_HERE``
============================  =================================================

**The verdict is byte-membership over git's machine output -- no rendering is
interpreted -- and that is the round-3 closure of the whole output-parsing
class.** Three faces of one root cause each read git's *human* rendering of a
path: round-1 tested empty-vs-non-empty output; round-2 (HIGH-2) tested exact
line membership, which a stored directory pathspec (``.``, ``docs``) defeated by
matching a foreign commit's files; round-3 (HIGH) found that even exact line
membership read a *rendered* line -- git quotes a CJK, ``"``-bearing, backslash
or control-char name under ``core.quotePath``, and ``.splitlines()`` breaks a name
with a newline in two -- so an honest fix for such a file was refused. The
closure reads the machine format instead: with ``-z`` git emits each touched path
as raw NUL-delimited bytes, and ``verify`` interprets that output *only* as an
exact set of NUL-delimited raw entries, empty-filtered (``!= b""``, never
``.strip()`` -- a file named ``" "`` is a valid path whose ``b" "`` entry
``.strip()`` would drop), compared byte-identically to
``file_path.encode("utf-8")``. No quoting, no line-splitting, no
whitespace-stripping, no pathspec breadth. ``--literal-pathspecs`` is now defence
in depth rather than a closure (below); the round-1/round-2 claims that
``--literal-pathspecs`` or line-membership "closed" the class are superseded
(measured git 2.47.1/2.54.0, 2026-09-19; ``high2_repro.py``,
``quotepath_repro.py``, ``nul_byte_compare.py``).

**One face stays inside this closure, and one class stays outside it.** Inside:
the stored path is a ``str`` and git emits *bytes*, so the comparison
UTF-8-encodes the anchor. A ``str`` that has no UTF-8 encoding -- a lone surrogate
a ``\\udcXX`` escape in a landed evidence file decodes to, or the
``surrogateescape`` ``str`` a record stores for a non-UTF-8 disk byte (T-24) --
would make that encode raise ``UnicodeEncodeError`` outside ``_run``'s fail-closed
``except``. The candidate path refuses such a path *before* ``verify`` with a
designed ``CandidateGenerationError`` (``candidate_generation`` folds
``untransportable_reason`` in, the guard ``_refuse_untransportable`` runs at build
time); ``verify`` itself is total independently -- a top-of-call transportability
check returns ``NO_SUCH_COMMIT`` for any untransportable path that reaches it, the
fail-closed verdict every reading that cannot reach a git answer takes, never a
crash and never the misleading ``TOUCHES_NOTHING_HERE`` (which would assert a
commit was found and touched nothing, a claim the verification never made for a
path git cannot receive). Outside, and named so the closure is not overclaimed:
pathname byte-*equality* under Unicode normalization (an NFC-stored anchor versus
an NFD tree path) is a different root cause -- ``-z`` does not touch it, the two
byte strings simply differ and the honest fix reads ``TOUCHES_NOTHING_HERE`` --
and it is filed as [#758], not folded in here.

**The command is the git-2.30 ``log`` form, chosen so the documented floor holds
(round-2 HIGH-1).** The floor is git 2.30+ (``development.md``). The round-1 fix
reached merge commits with ``diff-tree --diff-merges=first-parent``, but
``--diff-merges=first-parent`` is a git 2.31 feature: on a 2.30 install the option
errors, the non-zero exit folds to ``NO_SUCH_COMMIT``, and *every* valid
``fixCommit`` refuses. That attempt is recorded here as history, not repeated. The
shipped form is ``git --literal-pathspecs log --no-walk --first-parent -m
--name-only --format= --root --end-of-options <sha>^{commit} -- <path>``, whose
verdicts are byte-identical to the diff-tree form across present+touches,
not-touching, absent, conflict-merge+touches, root+touches and non-commit, one
spawn each (``log_form_probe.py``).

**Both arguments are untrusted, and they are untrusted differently.** The sha is
caller wire input; the path is author-controlled stored data a clone can deliver
(T-3, T-24). What each token is worth was measured under this shape on git 2.47.1
and 2.54.0, 2026-09-19:

* ``-z`` -- **load-bearing (round-3).** It makes git emit each touched path as
  raw NUL-delimited bytes rather than a newline-separated human rendering, which
  is what lets the byte-membership check above see the true path of a CJK,
  quoted, control-char or newline-bearing file. Without it git quotes such a name
  under ``core.quotePath`` and separates entries by newline, so ``.splitlines()``
  over the rendering never equals the raw ``file_path`` and an honest fix is
  refused. Predates the 2.30 floor (git 1.5).
* ``--literal-pathspecs`` -- **defence in depth, subsumed by membership.** Against
  a commit touching only ``docs/notes.md``, the stored paths
  ``:(exclude)src/retrying.py``, ``:!src/retrying.py``, ``:(glob)**/*.md`` and
  ``:(top)`` each make ``log`` emit ``docs/notes.md`` without the flag and nothing
  with it. The membership check refuses all four either way -- ``docs/notes.md`` is
  not the stored path -- so the flag no longer closes anything on its own; it stays
  because a name it disarms is one less name to reason about.
* ``--root`` -- **defence in depth on this form.** ``log`` shows a root commit's
  diff by default, so the flag changes no ``log``-form verdict; it is load-bearing
  on a ``diff-tree`` form (a first commit reports no files without it), so it
  guards a move back to one. Held by the captured-vector pin, since no ``log``
  behavioural case can tell.
* ``--first-parent -m`` on ``log`` -- **load-bearing, and the only tokens here
  that change a verdict for an honest input.** ``log`` shows nothing for a merge
  commit without ``-m``, so a conflict-resolving merge that introduced the fix read
  ``TOUCHES_NOTHING_HERE`` -- a true fix, correctly named, refused. ``--first-parent``
  makes the shown diff *this merge against the branch it landed on* rather than
  against every parent; the integration battery enumerates the merge shapes that
  rule out dropping either, and plain-commit verdicts are unchanged.
* ``^{commit}`` on the revision -- **load-bearing, for a different reason than it
  used to be.** Under the two-call shape it was what refused a fabricated forty
  hex digits, because ``rev-parse --verify`` accepts a full-width hex string as an
  object *name* without asking whether the object is present. This form does not:
  ``eeee…eeee`` exits 128 with the suffix and without it, so that justification did
  not survive the collapse and is recorded here as history rather than repeated.
  What the suffix holds now is **commit-only** semantics -- a tree id and a blob id
  each exit 0 with empty output without it, which this module would read as
  ``TOUCHES_NOTHING_HERE``, i.e. as *a commit was found*, and exit 128 with it.
* ``--end-of-options`` before the sha -- **defence in depth over a value the
  funnel has already refused.** An option-shaped sha exits 128 behind the flag and
  129, git's usage error, without it; both are non-zero, so both were the same
  verdict even before the funnel, and no file was created in either reading. Since
  the funnel, no option-shaped value reaches the vector at all, so the flag guards
  the token position rather than any input a caller can send. ``--`` before the
  path is the same kind of token on the same vector, and the one that still guards
  a live input: the path is not funnelled, so an option-shaped ``filePath`` out of
  an evidence file stays a pathspec.

``tests/integration/test_fix_commit_check_adapter.py`` holds all of these, and
names which a behavioural case can reach and which only its captured-vector pin
can; ``test_the_verify_command_uses_only_git_features_at_or_below_the_documented_floor``
holds the 2.30 floor as an allowlist rather than as a single flag's absence.

**The binary is resolved to an absolute path**, the ``gh`` precedent tier
(ADR-0030 clause 5) its ``committed_check.py`` sibling takes, because this call
decides a promotion signal and letting an inherited ``PATH`` choose the
executable that answers it is the shape that clause refuses. It reaches no
network -- ``log`` reads local object storage, names no remote and takes no
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
from theurian.domain.review_search import untransportable_reason

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

        Two guards run before any process exists. The grammar funnel refuses a
        *commit* that is not a full-length lower-case object name, so no revision
        expression is ever spent as one (module docstring). The transportability
        guard refuses a *file_path* git cannot receive -- a NUL, or a lone
        surrogate a ``\\udcXX`` escape in a landed evidence file decodes to (T-24)
        -- with ``NO_SUCH_COMMIT``: this keeps ``verify`` total, since the
        membership check's ``file_path.encode("utf-8")`` would otherwise raise
        ``UnicodeEncodeError`` on a lone surrogate outside ``_run``'s ``except``.
        The candidate path refuses such a value earlier with a named
        ``CandidateGenerationError``; this guard is what makes the adapter total on
        its own, so no consumer can crash it with a stored path.

        Past the guards it is one question, whatever the answer turns out to be:
        the branch that used to skip the second spawn is what made an absent object
        cheaper to refuse than a real one. So the outcome is read off one call
        rather than chosen between two, and the fail-closed readings -- git absent,
        a spawn that raised, a timeout -- join the non-zero exits on the refusing
        side rather than adding a path of their own.

        The three branches are explicit: a non-zero exit is ``NO_SUCH_COMMIT``;
        exit zero with the stored ``file_path``'s UTF-8 bytes among the emitted raw
        path entries is ``VERIFIED``; exit zero without them is
        ``TOUCHES_NOTHING_HERE``. ``-z`` makes git emit its *machine* format -- each
        touched path as raw NUL-delimited bytes -- so nothing here interprets git's
        human rendering: no ``core.quotePath`` quoting to undo, no line-splitting a
        newline-bearing name would break, no whitespace-stripping a name of one
        space would lose (module docstring, round-3). The comparison is
        ``file_path.encode("utf-8")`` against those raw byte entries; the
        transportability guard above has already refused any path that encode
        could not make, so it runs only over paths that have a UTF-8 encoding.
        """
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit) is None:
            return FixCommitVerdict.NO_SUCH_COMMIT
        if untransportable_reason(file_path) is not None:
            return FixCommitVerdict.NO_SUCH_COMMIT
        completed = self._run(
            [
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
                f"{commit}^{{commit}}",
                "--",
                file_path,
            ]
        )
        if completed is None or completed.returncode != 0:
            return FixCommitVerdict.NO_SUCH_COMMIT
        # Filter EMPTY entries only, never `.strip()`: a file named " " emits a
        # `b" "` entry that `.strip()` would drop, falsely refusing an honest fix.
        touched = {entry for entry in completed.stdout.split(b"\0") if entry}
        if file_path.encode("utf-8") in touched:
            return FixCommitVerdict.VERIFIED
        return FixCommitVerdict.TOUCHES_NOTHING_HERE

    def _run(self, git_args: list[str]) -> subprocess.CompletedProcess[bytes] | None:
        """Spawn the ``git`` call, or ``None`` if the binary is absent or it fails.

        The single spawn site in this module, and since C4c that is true in the
        stronger sense as well: one site, reached once per verification, so the
        count ``PROCESS_SPAWN_SITES`` deliberately does not record is one anyway.
        ``test_the_module_reaches_a_spawn_from_exactly_one_place`` counts the
        initiations that reach here; the runtime pins beside it count the spawns a
        verification actually makes. Fixed vector, no shell.

        The ``except`` names ``ValueError`` as defence in depth: a NUL byte in the
        stored ``file_path`` (T-24) raises it out of ``subprocess`` -- neither
        ``OSError`` nor ``TimeoutExpired`` -- and though ``verify``'s
        transportability guard now refuses a NUL before any spawn, a stored value
        that reached here must still earn a verdict rather than a traceback across
        the tool seam.
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
