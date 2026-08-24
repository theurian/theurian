"""Puts the core test tree on ``sys.path`` so ``migration_fixtures`` imports here.

The same mechanism ``tests/unit/tools/conftest.py`` uses for ``tools/``. The
migration fixtures in this directory declare a ``contentSha256`` the schema
requires (ADR-0027), and the digest helper lives with the rest of the suite's
shared builders under ``packages/theurian-core/tests/``. That directory is
already put on the path by its own ``conftest.py``, but only once pytest has
collected it -- which a run of ``tests/e2e`` alone never does.
"""

from __future__ import annotations

import sys
from pathlib import Path

_CORE_TESTS = Path(__file__).resolve().parents[2] / "packages" / "theurian-core" / "tests"

if str(_CORE_TESTS) not in sys.path:
    sys.path.insert(0, str(_CORE_TESTS))
