"""One verification recipe per citation kind, plus the evidence plumbing they share.

Split out of ``premise_check.py`` to keep that module under this repository's
line cap (round 2, out-of-perspective: the HIGH-3 PR-provenance fix would
otherwise have pushed it past 800 lines). This module is self-contained --
:mod:`premise_check` imports its types and constants rather than the other
way around, so the two modules never form a cycle.

Every recipe returns a :class:`VerifyResult`: ``command`` is the argv(s)
actually run, one per line, each reproducing the matching line of
``captured_output``; ``derivation`` is this tool's own sentence and never raw
output (round 1 HIGH-2: a shared ``output`` field used to mix prose in, so a
sha's exit-1 output could sit beside an ``INTACT`` verdict). A frozen
dataclass rather than a positional tuple (round 2 LOW-2): a 4-tuple lets a
caller transpose two same-typed fields and mypy would not notice.
"""

from __future__ import annotations

import fnmatch
import re
import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

#: The two trees ``pytest`` itself walks (``pyproject.toml``'s
#: ``testpaths``). The brief's own illustrative command searched a bare
#: ``tests`` pathspec, which resolves to only the top-level tree: measured
#: 2026-09-19, that pathspec matches 52 of the repository's 317 test files
#: and misses every one of the 265 under ``packages/theurian-core/tests``.
TEST_ROOTS: Final = ("packages/theurian-core/tests", "tests")

#: ``git cat-file -e`` on a missing object or invalid ref. Measured against
#: this checkout 2026-09-19: exit 128, not 1 -- the return code most of
#: `git`'s own porcelain uses for "no such thing" is not this plumbing
#: command's. Getting this wrong maps every dangling path onto ERROR.
_CAT_FILE_NOT_FOUND: Final = 128

#: A few KB is plenty for evidence; unbounded, one `git grep` census stored
#: ~116 KB of JSON for a single synthetic issue (round 1 MEDIUM-1).
_CAPTURED_OUTPUT_CAP: Final = 4096

#: Lines of context either side of a `path_line` citation's own cited line
#: (round 2 MEDIUM-1): an INTACT `path_line` used to store the whole file's
#: first 4 KB rather than the neighbourhood the citation actually names, so
#: the cap fired on ordinary long files instead of on genuine outliers.
_PATH_LINE_CONTEXT: Final = 3

INTACT: Final = "INTACT"
DANGLING: Final = "DANGLING"
UNKNOWN: Final = "UNKNOWN"
ERROR: Final = "ERROR"

#: The only kinds whose "not found" is a reliable premise signal rather than
#: an artifact of this checkout's own state (round 1 HIGH-1c, decision 2).
#: `_verify` downgrades any DANGLING outside this set to UNKNOWN, so a kind
#: added later without its own not-found recipe still fails closed.
_DANGLING_ALLOWED_KINDS: Final = frozenset({"path", "path_line", "adr", "test_name"})


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


#: Runs one command with a list argv (no shell) and returns what it produced.
#: Required everywhere it is used -- ``runner=None`` meaning "use the real
#: subprocess" is the falsy-default shape issue #729 was filed over,
#: reproduced nowhere here. Only :func:`premise_check.main` constructs the
#: real one.
Runner = Callable[[Sequence[str]], CommandResult]


def git_repository_present(repo_root: Path) -> bool:
    """Whether ``repo_root`` itself carries a ``.git`` -- not some ancestor of it.

    ``mutate.py --prepare-tree`` without ``--with-git`` produces exactly a
    tree with no ``.git`` of its own (#788): every plumbing command below then
    fails with exit 128 ("not a git repository"), and a recipe that maps that
    code onto its kind's own not-found status reports a verdict this checkout
    never actually computed. A caller that cannot proceed without git checks
    this first and degrades, mirroring ``eval/report.py``'s
    ``commitSha == "unknown"``.

    Not ``git rev-parse --is-inside-work-tree`` (round 1 M1): that walks
    *upward* through every ancestor looking for a ``.git``, so a gitless copy
    placed underneath a real repository -- ``--prepare-tree --work-dir``
    pointed at a path inside a clone -- answers "inside" and this guard would
    never fire, reproducing #788's exact symptom under a different
    ``--work-dir``. ``.exists()`` rather than ``.is_dir()``: ``.git`` is a
    file, not a directory, inside a linked worktree.
    """
    return (repo_root / ".git").exists()


@dataclass(frozen=True, slots=True)
class VerifyResult:
    command: str
    captured_output: str
    derivation: str
    status: str


def _argv_str(argv: Sequence[str]) -> str:
    """Quoted so a pasted line re-splits to the exact argv, never a bare-space join
    (round 3 HIGH-1): an element carrying a space or shell metacharacter --
    `git grep -n def screen_landing_candidates HEAD`'s pattern is one such
    argument -- used to re-split into more words on paste than the argv it
    reproduced, so the shown `exit 0` line turned into `git`'s own exit 128.
    """
    return shlex.join(argv)


@dataclass(frozen=True, slots=True)
class _Invocation:
    argv: tuple[str, ...]
    result: CommandResult


def _output_text(result: CommandResult) -> str:
    """Both streams, labelled, on failure: a diagnosis often needs stderr too (MEDIUM-2)."""
    if result.returncode == 0:
        return result.stdout.strip()
    parts = [f"stdout: {result.stdout.strip()}"] if result.stdout.strip() else []
    if result.stderr.strip():
        parts.append(f"stderr: {result.stderr.strip()}")
    return "\n".join(parts)


def _truncate(text: str) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= _CAPTURED_OUTPUT_CAP:
        return text
    kept = encoded[:_CAPTURED_OUTPUT_CAP].decode("utf-8", errors="ignore")
    return f"{kept}\n…[truncated: kept {len(kept.encode('utf-8'))} of {len(encoded)} bytes]"


def _evidence(invocations: Sequence[_Invocation], derivation: str, status: str) -> VerifyResult:
    """One step per line, never a shell-joined command beside another step's exit code (HIGH-2)."""
    command = "\n".join(_argv_str(invocation.argv) for invocation in invocations)
    captured = "\n".join(
        f"exit {invocation.result.returncode}: {_output_text(invocation.result) or '<empty>'}"
        for invocation in invocations
    )
    return VerifyResult(command, _truncate(captured), derivation, status)


def _verify_path(path: str, runner: Runner) -> VerifyResult:
    argv = ("git", "cat-file", "-e", f"HEAD:{path}")
    result = runner(argv)
    invocation = _Invocation(argv, result)
    if result.returncode == 0:
        return _evidence([invocation], "", INTACT)
    if result.returncode == _CAT_FILE_NOT_FOUND:
        return _evidence([invocation], "", DANGLING)
    return _evidence([invocation], "", ERROR)


def _line_window(lines: Sequence[str], line_number: int, context: int) -> str:
    """``lines[line_number]`` (1-indexed) plus ``context`` lines either side."""
    start = max(0, line_number - 1 - context)
    end = min(len(lines), line_number + context)
    return "\n".join(lines[start:end])


def _verify_path_line(token: str, runner: Runner) -> VerifyResult:
    path, _, raw_line = token.rpartition(":")
    line_number = int(raw_line)
    path_result = _verify_path(path, runner)
    if path_result.status != INTACT:
        return path_result
    show_argv = ("git", "show", f"HEAD:{path}")
    show_result = runner(show_argv)
    command = f"{path_result.command}\n{_argv_str(show_argv)}"
    if show_result.returncode != 0:
        output = f"{path_result.captured_output}\nexit {show_result.returncode}: " + (
            _output_text(show_result) or "<empty>"
        )
        return VerifyResult(command, _truncate(output), "", ERROR)
    lines = show_result.stdout.splitlines()
    if 1 <= line_number <= len(lines):
        excerpt = _line_window(lines, line_number, _PATH_LINE_CONTEXT) or "<empty>"
        output = f"{path_result.captured_output}\nexit {show_result.returncode}: {excerpt}"
        derivation = f"line {line_number}: {lines[line_number - 1]}"
        return VerifyResult(command, _truncate(output), derivation, INTACT)
    output = f"{path_result.captured_output}\nexit {show_result.returncode}: " + (
        _output_text(show_result) or "<empty>"
    )
    derivation = f"{path} has {len(lines)} line(s); line {line_number} does not exist"
    return VerifyResult(command, _truncate(output), derivation, DANGLING)


def _from_grep(argv: tuple[str, ...], result: CommandResult, *, not_found: str) -> VerifyResult:
    invocation = _Invocation(argv, result)
    if result.returncode == 0:
        return _evidence([invocation], "", INTACT)
    if result.returncode == 1:
        return _evidence([invocation], "no match", not_found)
    return _evidence([invocation], "", ERROR)


def _verify_test_name(name: str, runner: Runner) -> VerifyResult:
    pattern = rf"(?:async )?def {re.escape(name)}\("
    argv = ("git", "grep", "-nP", pattern, "HEAD", "--", *TEST_ROOTS)
    return _from_grep(argv, runner(argv), not_found=DANGLING)


def _verify_constant(name: str, runner: Runner) -> VerifyResult:
    # A grep miss is UNKNOWN, not DANGLING (decision 2): unlike a `test_name`
    # definition site, a constant can live in text this grep never reaches.
    argv = ("git", "grep", "-wnF", name, "HEAD")
    return _from_grep(argv, runner(argv), not_found=UNKNOWN)


def _verify_sha(sha: str, runner: Runner) -> VerifyResult:
    commit_argv = ("git", "cat-file", "-e", f"{sha}^{{commit}}")
    commit_result = runner(commit_argv)
    commit_invocation = _Invocation(commit_argv, commit_result)
    if commit_result.returncode == _CAT_FILE_NOT_FOUND:
        # UNKNOWN, not DANGLING (decision 2): under squash-merge plus GC, a
        # sha absent from the local object store is machine-local, not broken.
        return _evidence([commit_invocation], "", UNKNOWN)
    if commit_result.returncode != 0:
        return _evidence([commit_invocation], "", ERROR)
    ancestor_argv = ("git", "merge-base", "--is-ancestor", sha, "HEAD")
    ancestor_result = runner(ancestor_argv)
    invocations = [commit_invocation, _Invocation(ancestor_argv, ancestor_result)]
    if ancestor_result.returncode not in (0, 1):
        return _evidence(invocations, "", ERROR)
    if ancestor_result.returncode == 0:
        return _evidence(invocations, "", INTACT)
    derivation = f"{sha} exists but is not an ancestor of HEAD (expected under squash-merge)"
    return _evidence(invocations, derivation, INTACT)


def _verify_adr(token: str, runner: Runner) -> VerifyResult:
    number = token.removeprefix("ADR-")
    argv = ("git", "ls-tree", "-r", "--name-only", "HEAD", "--", "docs/adr")
    result = runner(argv)
    invocation = _Invocation(argv, result)
    if result.returncode != 0:
        return _evidence([invocation], "", ERROR)
    pattern = f"{number}-*.md"
    matches = [
        line
        for line in result.stdout.splitlines()
        if fnmatch.fnmatch(PurePosixPath(line).name, pattern)
    ]
    if matches:
        return _evidence([invocation], f"matched {matches[0]}", INTACT)
    return _evidence([invocation], f"no docs/adr/{pattern} in the tree", DANGLING)


def _verify_issue_ref(
    token: str, snapshot_numbers: frozenset[int], pr_states: Mapping[int, str]
) -> VerifyResult:
    number = int(token.removeprefix("#"))
    if number in snapshot_numbers:
        return VerifyResult("", "", f"#{number} is open in the snapshot", INTACT)
    state = pr_states.get(number)
    if state is not None:
        # A PR reference is provenance for the issue, not a premise the issue
        # itself made -- INTACT either way, and never `REFERENCE_NOT_OPEN`
        # (round 2 HIGH-3: 96 of 109 flagged issues carried a PR number, most
        # of them still-open PRs, flooding the section with non-signal).
        derivation = f"#{number} is PR #{number}, state {state} (provenance, not a premise anchor)"
        return VerifyResult("", "", derivation, INTACT)
    derivation = (
        f"#{number} is not an open issue and not a known PR: closed issue, or does not exist"
    )
    return VerifyResult("", "", derivation, UNKNOWN)


def _verify_symbol(token: str, runner: Runner) -> VerifyResult:
    name = token.removesuffix("()")
    last_segment = name.rsplit(".", 1)[-1]
    if not last_segment:
        # A token ending in "." (e.g. "foo.") rsplits to an empty last
        # segment, and an empty pattern's "def "/"class " grep matches
        # nearly every file -- a false INTACT, not a resolvable symbol.
        return VerifyResult("", "", f"{token!r} has no resolvable symbol segment", UNKNOWN)
    keyword = "class" if last_segment[:1].isupper() else "def"
    argv = ("git", "grep", "-n", f"{keyword} {last_segment}", "HEAD")
    result = runner(argv)
    invocation = _Invocation(argv, result)
    if result.returncode == 0:
        return _evidence([invocation], "", INTACT)
    if result.returncode == 1:
        # Never DANGLING: the pattern is a heuristic guess at a definition
        # site, and a miss says as much about the guess as about the symbol.
        # A false DANGLING here would poison the agent pass's triage with a
        # citation that never had a reliable check to begin with.
        return _evidence([invocation], "no match (best-effort)", UNKNOWN)
    return _evidence([invocation], "", ERROR)


def verify(
    citation_kind: str,
    token: str,
    runner: Runner,
    snapshot_numbers: frozenset[int],
    pr_states: Mapping[int, str],
) -> VerifyResult:
    match citation_kind:
        case "path":
            result = _verify_path(token, runner)
        case "path_line":
            result = _verify_path_line(token, runner)
        case "test_name":
            result = _verify_test_name(token, runner)
        case "constant":
            result = _verify_constant(token, runner)
        case "sha":
            result = _verify_sha(token, runner)
        case "adr":
            result = _verify_adr(token, runner)
        case "issue_ref":
            result = _verify_issue_ref(token, snapshot_numbers, pr_states)
        case _:
            result = _verify_symbol(token, runner)
    if result.status == DANGLING and citation_kind not in _DANGLING_ALLOWED_KINDS:
        return VerifyResult(result.command, result.captured_output, result.derivation, UNKNOWN)
    return result
