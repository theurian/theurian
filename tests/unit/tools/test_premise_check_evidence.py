"""The evidence a `CitationResult` carries: `command` is what actually ran,
`captured_output` is what it actually printed with its exit code, and
`derivation` is this tool's own sentence and nothing else (round 1 HIGH-2,
MEDIUM-1, MEDIUM-2).

Before the fix, three of eight verification kinds stored a `command` that did
not produce the `output` beside it -- worst face: a `sha`'s own exit-1
ancestor check sat beside an `INTACT` verdict with no exit code visible to a
reader re-running the pasted command. This file pins that a reader can
re-run any line of `command` and get exactly the matching line of
`captured_output`, that the evidence is bounded and says so honestly when
truncated, and that a failing command's stderr is never dropped just because
its stdout was non-empty.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence

import premise_verify
import pytest

pytestmark = pytest.mark.unit


class _ScriptedRunner:
    """Maps an exact argv tuple to a canned `CommandResult`; anything unscripted fails loudly."""

    def __init__(self, script: dict[tuple[str, ...], premise_verify.CommandResult]) -> None:
        self._script = script

    def __call__(self, argv: Sequence[str]) -> premise_verify.CommandResult:
        key = tuple(argv)
        if key not in self._script:
            raise AssertionError(f"unscripted git invocation: {' '.join(key)}")
        return self._script[key]


def _result(returncode: int, stdout: str = "", stderr: str = "") -> premise_verify.CommandResult:
    return premise_verify.CommandResult(returncode=returncode, stdout=stdout, stderr=stderr)


# --------------------------------------------------------------------------
# Evidence honesty (HIGH-2): the worst face was a sha's own exit-1 ancestor
# check reading INTACT with no exit code beside it, and prose sat in the
# command column.
# --------------------------------------------------------------------------

_SHA = "9f2c1ab4d5e6f70819a2b3c4d5e6f7089a1b2c3d"
_COMMIT_ARGV = ("git", "cat-file", "-e", f"{_SHA}^{{commit}}")
_ANCESTOR_ARGV = ("git", "merge-base", "--is-ancestor", _SHA, "HEAD")


def test_a_shas_non_ancestor_evidence_is_runnable_and_the_prose_lives_only_in_derivation() -> None:
    """The non-ancestor case is exactly the HIGH-2 face: `merge-base
    --is-ancestor`'s own "no" answer is exit 1, and the verdict it produces
    is `INTACT`. `command` must carry both argvs so a reader can re-run
    either step; `captured_output` must carry each step's own exit code,
    including the 1; and the "expected under squash-merge" sentence must
    live only in `derivation`, never in the two fields a reader re-runs.
    """
    runner = _ScriptedRunner(
        {_COMMIT_ARGV: _result(0, stdout="commit\n"), _ANCESTOR_ARGV: _result(1)}
    )

    result = premise_verify._verify_sha(_SHA, runner)

    assert result.status == premise_verify.INTACT
    assert result.command == f"{shlex.join(_COMMIT_ARGV)}\n{shlex.join(_ANCESTOR_ARGV)}"
    lines = result.captured_output.splitlines()
    assert lines[0].startswith("exit 0:")
    assert lines[1].startswith("exit 1:")
    assert "not an ancestor of HEAD" in result.derivation
    assert "not an ancestor of HEAD" not in result.command
    assert "not an ancestor of HEAD" not in result.captured_output


def test_verify_issue_ref_carries_no_command_or_captured_output_only_derivation() -> None:
    """No git call backs an `issue_ref` lookup -- it is answered from the
    snapshot's own set of open issue numbers -- so `command` and
    `captured_output` stay empty; the whole explanation belongs in
    `derivation` alone (round 1 LOW-1: it used to sit in the command column).
    """
    result = premise_verify._verify_issue_ref("#42", frozenset({1, 2, 3}), {})

    assert result.command == ""
    assert result.captured_output == ""
    assert "not an open issue" in result.derivation
    assert result.status == premise_verify.UNKNOWN


# --------------------------------------------------------------------------
# Evidence bound (MEDIUM-1): unbounded, one `git grep` census stored ~116 KB
# of JSON for a single synthetic issue.
# --------------------------------------------------------------------------


def test_truncate_leaves_text_under_the_cap_untouched() -> None:
    text = "x" * (premise_verify._CAPTURED_OUTPUT_CAP - 1)

    assert premise_verify._truncate(text) == text


def test_truncate_marks_truncation_at_the_named_cap_with_the_true_original_size() -> None:
    """`N` is the cap itself, and `M` is the true original byte count -- ASCII
    content keeps the encode/decode round trip exact, so both numbers are
    checked against the real inputs rather than restated as literals.
    """
    cap = premise_verify._CAPTURED_OUTPUT_CAP
    original_size = cap + 500
    text = "a" * original_size

    truncated = premise_verify._truncate(text)

    kept, marker = truncated.rsplit("\n", 1)
    assert len(kept.encode("utf-8")) == cap
    assert marker == f"…[truncated: kept {cap} of {original_size} bytes]"


# --------------------------------------------------------------------------
# stderr kept (MEDIUM-2): `_captured` used to drop stderr whenever stdout was
# non-empty, on exactly the failing paths that need the diagnosis.
# --------------------------------------------------------------------------


def test_a_failing_commands_output_text_keeps_both_streams_labelled() -> None:
    result = premise_verify.CommandResult(returncode=1, stdout="from stdout", stderr="from stderr")

    text = premise_verify._output_text(result)

    assert "stdout: from stdout" in text
    assert "stderr: from stderr" in text


# --------------------------------------------------------------------------
# Paste-safe evidence (round 3 HIGH-1): `_argv_str` used to bare-space-join
# argv, so a `symbol` pattern like `def screen_landing_candidates` -- one
# argv element carrying an embedded space -- re-split on paste into an extra
# word `git grep` reads as a second pathspec, turning the shown `exit 0`
# into `git`'s own "no such path" exit 128.
# --------------------------------------------------------------------------


def test_a_symbols_command_round_trips_through_shlex_split_to_the_exact_argv() -> None:
    argv = ("git", "grep", "-n", "def screen_landing_candidates", "HEAD")
    runner = _ScriptedRunner(
        {
            argv: _result(
                0, stdout="packages/theurian-core/src/x.py:338:def screen_landing_candidates("
            )
        }
    )

    result = premise_verify._verify_symbol("screen_landing_candidates", runner)

    assert result.status == premise_verify.INTACT
    (line,) = result.command.splitlines()
    assert tuple(shlex.split(line)) == argv


def test_a_test_names_command_round_trips_through_shlex_split_to_the_exact_argv() -> None:
    pattern = r"(?:async )?def test_thing\("
    argv = ("git", "grep", "-nP", pattern, "HEAD", "--", *premise_verify.TEST_ROOTS)
    runner = _ScriptedRunner({argv: _result(0, stdout="tests/x.py:1:def test_thing():")})

    result = premise_verify._verify_test_name("test_thing", runner)

    assert result.status == premise_verify.INTACT
    (line,) = result.command.splitlines()
    assert tuple(shlex.split(line)) == argv


def test_a_path_lines_two_command_lines_each_round_trip_through_shlex_split() -> None:
    """A multi-invocation citation (`path_line`) carries one line per step
    (round 1 HIGH-2) -- the round-trip must hold for every line, not just
    the first.
    """
    cat_argv = ("git", "cat-file", "-e", "HEAD:tools/premise check.py")
    show_argv = ("git", "show", "HEAD:tools/premise check.py")
    runner = _ScriptedRunner(
        {cat_argv: _result(0), show_argv: _result(0, stdout="line one\nline two\n")}
    )

    result = premise_verify._verify_path_line("tools/premise check.py:2", runner)

    assert result.status == premise_verify.INTACT
    first, second = result.command.splitlines()
    assert tuple(shlex.split(first)) == cat_argv
    assert tuple(shlex.split(second)) == show_argv
