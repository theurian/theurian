"""Two `check` runs over one snapshot write byte-identical JSON (AC6, module docstring).

Determinism here is not "the two Python objects compare equal" -- a set's
iteration order or a dict's insertion order can still leak into an encoder
nobody told to be strict about it. This file compares the actual bytes
`_write_json` puts on disk (the real write seam `main` uses), over a runner
that is a pure function of its argv -- the only kind of runner two
independent calls to `check` are allowed to see the same answer from.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import premise_check
import premise_verify
import pytest

pytestmark = pytest.mark.unit


def _snapshot() -> premise_check.Snapshot:
    return premise_check.Snapshot(
        issues=(
            premise_check.IssueRecord(
                number=2,
                title="second",
                created_at="2026-01-01T00:00:00Z",
                labels=("bug",),
                body="See tools/premise_check.py and `FETCH_LIMIT`, filed against #1.",
                comments=("Also ADR-0033.",),
            ),
            premise_check.IssueRecord(
                number=1,
                title="first",
                created_at="2026-01-02T00:00:00Z",
                labels=(),
                body="`_verify_symbol` looks stale.",
                comments=(),
            ),
        )
    )


class _PureRunner:
    """A pure function of argv: the same script answers both calls identically."""

    def __init__(self, script: dict[tuple[str, ...], premise_verify.CommandResult]) -> None:
        self._script = script

    def __call__(self, argv: Sequence[str]) -> premise_verify.CommandResult:
        return self._script[tuple(argv)]


def _script() -> dict[tuple[str, ...], premise_verify.CommandResult]:
    ok = premise_verify.CommandResult(0, "", "")
    return {
        ("git", "rev-parse", "HEAD"): premise_verify.CommandResult(0, "cafebabe1234\n", ""),
        ("git", "ls-tree", "-r", "--name-only", "HEAD"): premise_verify.CommandResult(
            0, "tools/premise_check.py\n", ""
        ),
        ("git", "cat-file", "-e", "HEAD:tools/premise_check.py"): ok,
        ("git", "grep", "-wnF", "FETCH_LIMIT", "HEAD"): premise_verify.CommandResult(
            0, "tools/premise_check.py:85:FETCH_LIMIT", ""
        ),
        (
            "git",
            "ls-tree",
            "-r",
            "--name-only",
            "HEAD",
            "--",
            "docs/adr",
        ): premise_verify.CommandResult(0, "docs/adr/0033-x.md\n", ""),
        (
            "git",
            "log",
            "--format=%H%x09%s",
            "--since-as-filter=2026-01-01T00:00:00Z",
            "HEAD",
            "--",
            "tools/premise_check.py",
        ): premise_verify.CommandResult(0, "", ""),
        ("git", "grep", "-n", "def _verify_symbol", "HEAD"): premise_verify.CommandResult(
            1, "", ""
        ),
    }


def test_checking_the_same_snapshot_twice_writes_byte_identical_json(tmp_path: Path) -> None:
    snapshot = _snapshot()
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"

    premise_check._write_json(
        str(first_path),
        premise_check.report_to_json(
            premise_check.check(snapshot, _PureRunner(_script()), "snap.json")
        ),
    )
    premise_check._write_json(
        str(second_path),
        premise_check.report_to_json(
            premise_check.check(snapshot, _PureRunner(_script()), "snap.json")
        ),
    )

    assert first_path.read_bytes() == second_path.read_bytes()
    parsed = json.loads(first_path.read_text(encoding="utf-8"))
    assert [issue["number"] for issue in parsed["issues"]] == [1, 2]
