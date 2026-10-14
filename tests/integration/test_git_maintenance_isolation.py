"""The suite's git never spawns ``git maintenance run --auto`` (#938).

The detached child writes ``objects/maintenance.lock`` after the command returns,
racing any test that byte-compares a repository it just committed in (#932).
The repo-root ``conftest.py`` sets ``maintenance.auto=false`` through the
``GIT_CONFIG_COUNT`` env trio; these tests prove it reaches a child that also
pins ``GIT_CONFIG_GLOBAL``/``GIT_CONFIG_SYSTEM`` to ``os.devnull`` as the suite's
hermetic fixtures do.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_TRIO = frozenset(
    {"GIT_CONFIG_COUNT"} | {f"GIT_CONFIG_{part}_{i}" for i in range(8) for part in ("KEY", "VALUE")}
)
_IDENTITY = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]


def _git(cwd: Path, env: dict[str, str], *args: str) -> None:
    subprocess.run(  # noqa: S603 - fixed argv in a throwaway repository
        ["git", *args],  # noqa: S607 - git resolved via PATH
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
    )


def _maintenance_trace_lines(tmp_path: Path, env: dict[str, str]) -> list[str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    trace = tmp_path / "trace.txt"
    hermetic = {
        **env,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }
    _git(repo, hermetic, "init", "-q")
    _git(repo, hermetic, *_IDENTITY, "commit", "-q", "--allow-empty", "-m", "initial")
    _git(
        repo,
        {**hermetic, "GIT_TRACE": str(trace)},
        *_IDENTITY,
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "second",
    )
    return [line for line in trace.read_text().splitlines() if "maintenance run" in line]


def test_an_inherited_environment_keeps_git_from_spawning_auto_maintenance(
    tmp_path: Path,
) -> None:
    lines = _maintenance_trace_lines(tmp_path, dict(os.environ))

    assert lines == []


def test_without_the_conftest_trio_git_does_spawn_auto_maintenance(tmp_path: Path) -> None:
    """The positive control: the zero above is not vacuous on a git that never spawns."""
    env = {k: v for k, v in os.environ.items() if k not in _TRIO}

    lines = _maintenance_trace_lines(tmp_path, env)

    assert lines != []
