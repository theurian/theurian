"""`check`'s two documented exit codes: 0 once the report is written, 1 when
it cannot even start (module docstring's own "Exit codes" section, AC9).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import premise_check
import premise_verify
import pytest

pytestmark = pytest.mark.unit


def test_check_exits_zero_once_the_report_is_written_even_if_every_issue_needs_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The module docstring: a report that is entirely NEEDS-AGENT is a
    successful `check` -- the verdicts are the point. Zero holding citations
    is not what exit 0 means here.
    """
    snapshot_path = tmp_path / "snapshot.json"
    report_path = tmp_path / "report.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "number": 1,
                        "title": "t",
                        "createdAt": "2026-01-01T00:00:00Z",
                        "labels": [],
                        "body": "no citation here at all",
                        "comments": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def fake_run_command(argv: Sequence[str]) -> premise_verify.CommandResult:
        if tuple(argv)[:2] == ("git", "rev-parse"):
            return premise_verify.CommandResult(0, "cafebabe1234\n", "")
        return premise_verify.CommandResult(0, "", "")

    monkeypatch.setattr(premise_check, "run_command", fake_run_command)

    exit_code = premise_check.main(
        ["check", "--snapshot", str(snapshot_path), "--json", str(report_path)]
    )

    assert exit_code == 0
    written = json.loads(report_path.read_text(encoding="utf-8"))
    assert written["issues"][0]["machineVerdict"] == premise_check.NEEDS_AGENT


def test_check_exits_one_when_the_snapshot_is_absent(tmp_path: Path) -> None:
    exit_code = premise_check.main(
        [
            "check",
            "--snapshot",
            str(tmp_path / "does-not-exist.json"),
            "--json",
            str(tmp_path / "report.json"),
        ]
    )

    assert exit_code == 1


def test_check_exits_one_when_the_snapshot_is_malformed_json(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "corrupt.json"
    snapshot_path.write_text("not json at all", encoding="utf-8")

    exit_code = premise_check.main(
        ["check", "--snapshot", str(snapshot_path), "--json", str(tmp_path / "report.json")]
    )

    assert exit_code == 1


def test_check_exits_one_when_the_snapshot_path_is_unreadable_as_a_file(tmp_path: Path) -> None:
    """A directory in place of the snapshot -- a true OS-level read failure,
    distinct from the malformed-JSON case above.
    """
    snapshot_dir = tmp_path / "snapshot.json"
    snapshot_dir.mkdir()

    exit_code = premise_check.main(
        ["check", "--snapshot", str(snapshot_dir), "--json", str(tmp_path / "report.json")]
    )

    assert exit_code == 1
