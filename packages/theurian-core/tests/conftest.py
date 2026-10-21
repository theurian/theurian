"""Test configuration.

Puts this directory on ``sys.path`` so ``fakes`` is importable as a top-level
package. The suite runs under ``--import-mode=importlib``, which does not add
test directories to the path automatically.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TESTS_ROOT = Path(__file__).parent
if str(_TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TESTS_ROOT))

from git_isolation_fixtures import (  # noqa: E402 - needs the path insert above
    BELOW_2_45,
    HONOURS_NO_LAZY_FETCH,
    without_lazy_fetch_guard,
)


@pytest.fixture(params=[HONOURS_NO_LAZY_FETCH, BELOW_2_45])
def lazy_fetch_mode(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run a partial-clone case as git 2.45+ behaves, then as an older git does (#981)."""
    if request.param == BELOW_2_45:
        without_lazy_fetch_guard(monkeypatch)
    return str(request.param)
