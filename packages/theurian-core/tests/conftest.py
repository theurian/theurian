"""Test configuration.

Puts this directory on ``sys.path`` so ``fakes`` is importable as a top-level
package. The suite runs under ``--import-mode=importlib``, which does not add
test directories to the path automatically.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from theurian.infrastructure.git import environment

_TESTS_ROOT = Path(__file__).parent
if str(_TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TESTS_ROOT))


@pytest.fixture(params=["git_honours_no_lazy_fetch", "git_below_2_45"])
def lazy_fetch_mode(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run a partial-clone case as git 2.45+ behaves, then as an older git does (#981)."""
    from git_isolation_fixtures import git_version

    major, minor = git_version()
    if request.param == "git_honours_no_lazy_fetch" and (major, minor) < (2, 45):
        pytest.skip(
            f"git {major}.{minor} is below 2.45, where honouring GIT_NO_LAZY_FETCH is not assumed"
        )
    if request.param == "git_below_2_45":
        # child_environment reads the forced set at call time; simulates a git that ignores the key.
        forced = {k: v for k, v in environment._FORCED.items() if k != "GIT_NO_LAZY_FETCH"}
        monkeypatch.setattr(environment, "_FORCED", forced)
    return str(request.param)
